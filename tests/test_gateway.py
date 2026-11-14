"""Tests for lab.gateway and the agents' command-line clients. No network beyond 127.0.0.1:
the Exa call is stubbed and memory is LocalMemory."""

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from lab.contracts import HEAD, WORKER, Agent, Org
from lab.eventlog import JsonlEventLog
from lab.gateway import Gateway
from lab.memory_local import LocalMemory
from lab.tools import exa

ROOT = pathlib.Path(__file__).resolve().parents[1]
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(base, path, body, token=None, raw=None):
    """POST to the gateway. Returns (status, parsed body)."""
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with NO_PROXY.open(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


class FakeExa:
    """Stands in for lab.tools.exa.post and records what it was asked."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, body))
        results = [
            {"title": "A useful methods paper", "url": "https://example.org/methods", "publishedDate": "2025-01-02", "highlights": ["good passage"]},
            {"title": "The SecretBench source paper", "url": "https://example.org/source", "highlights": ["the answers"]},
            {"title": "Mirror of the source", "url": "https://arxiv.org/abs/2401.99999", "text": "the answers again"},
        ]
        if path == "/contents":
            results = [{"title": "Page", "url": u, "text": "page text"} for u in body["urls"]]
        return 200, {"results": results, "costDollars": {"total": 0.007}}, 0.5


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(exa, "LOG", tmp_path / "exa_calls.jsonl")
    org = Org(agents=[
        Agent("librarian", WORKER, "library", "opus"),
        Agent("worker-a", WORKER, "team-a", "opus"),
        Agent("worker-b", WORKER, "team-b", "opus"),
        Agent("head", HEAD, "team-a", "opus"),
    ])
    log = JsonlEventLog(tmp_path / "events.jsonl")
    memory = LocalMemory(log, tmp_path / "facts.jsonl")
    memory.setup(org)
    fake = FakeExa()
    gw = Gateway(org, memory, log, exa_departments=["library"], blocked_terms=["secretbench", "ARXIV.ORG/ABS/2401.99999"], exa_call=fake)
    base = gw.start()
    tokens = {a.id: gw.issue_token(a.id) for a in org.agents}
    yield {"gw": gw, "base": base, "tokens": tokens, "log": log, "exa": fake, "tmp": tmp_path}
    gw.stop()


def test_missing_or_wrong_token_gets_401(lab):
    base = lab["base"]
    assert post(base, "/memory/recall", {"query": "x"})[0] == 401
    assert post(base, "/memory/recall", {"query": "x"}, token="not-a-real-token")[0] == 401
    req = urllib.request.Request(base + "/memory/recall", data=b"{}", method="POST")
    req.add_header("Authorization", lab["tokens"]["worker-a"])  # no "Bearer "
    with pytest.raises(urllib.error.HTTPError) as err:
        NO_PROXY.open(req, timeout=10)
    assert err.value.code == 401


def test_worker_token_cannot_read_another_department_even_when_body_claims_head(lab):
    base, tok = lab["base"], lab["tokens"]
    assert post(base, "/memory/write", {"text": "Team A rig code is 41723.", "label": "rig"}, tok["worker-a"])[0] == 200
    spoof = {"query": "rig code", "limit": 20, "agent_id": "head", "agent": "head", "identity": "head",
             "role": "head", "department": "team-a", "audience": "team-a"}
    status, body = post(base, "/memory/recall", spoof, tok["worker-b"])
    assert status == 200
    assert "41723" not in json.dumps(body)
    status, body = post(base, "/memory/recall", {"query": "rig code"}, tok["head"])
    assert "41723" in json.dumps(body)


def test_write_through_gateway_lands_in_the_tokens_department(lab):
    base, tok, log = lab["base"], lab["tokens"], lab["log"]
    status, body = post(base, "/memory/write", {"text": "Batch 88120 is bad.", "label": "reagent",
                                                 "department": "team-a", "agent_id": "worker-a", "audience": "team-a"}, tok["worker-b"])
    assert status == 200 and body["department"] == "team-b"
    event = log.events("memory.write")[-1]
    assert event["actor"] == "worker-b" and event["department"] == "team-b"
    assert "88120" not in json.dumps(post(base, "/memory/recall", {"query": "batch"}, tok["worker-a"])[1])


def test_pass_down_needs_a_head_and_records_came_from(lab):
    base, tok, log = lab["base"], lab["tokens"], lab["log"]
    _, written = post(base, "/memory/write", {"text": "Team A rig code is 41723.", "label": "rig"}, tok["worker-a"])
    fid = written["id"]
    req = {"to_department": "team-b", "text": "Use rig code 41723.", "came_from": [fid], "reason": "team-b runs the rig"}
    assert post(base, "/memory/pass_down", {**req, "role": "head"}, tok["worker-a"])[0] == 403
    status, body = post(base, "/memory/pass_down", req, tok["head"])
    assert status == 200
    event = log.events("memory.pass_down")[-1]
    assert event["came_from"] == [fid] and event["actor"] == "head"
    _, got = post(base, "/memory/recall", {"query": "rig code"}, tok["worker-b"])
    ids = [f["id"] for f in got["facts"]]
    assert body["id"] in ids and fid not in ids
    assert post(base, "/memory/pass_down", {**req, "to_department": "nowhere"}, tok["head"])[0] == 400


def test_exa_only_for_library_departments(lab):
    base, tok = lab["base"], lab["tokens"]
    assert post(base, "/exa/search", {"query": "q", "objective": "o"}, tok["worker-a"])[0] == 403
    assert post(base, "/exa/contents", {"urls": ["https://example.org/x"], "objective": "o"}, tok["head"])[0] == 403
    assert lab["exa"].calls == []


def test_blocked_terms_are_filtered_and_counted(lab):
    base, tok, log = lab["base"], lab["tokens"], lab["log"]
    status, body = post(base, "/exa/search", {"query": "methods", "objective": "find methods", "num": 5, "type": "deep"}, tok["librarian"])
    assert status == 200
    assert [r["url"] for r in body["results"]] == ["https://example.org/methods"]
    path, sent = lab["exa"].calls[-1]
    assert path == "/search" and sent["numResults"] == 5 and sent["type"] == "deep"
    blocked = log.events("exa.blocked")[-1]
    assert blocked["actor"] == "librarian" and blocked["dropped"] == 2
    logged = [json.loads(line) for line in (lab["tmp"] / "exa_calls.jsonl").read_text().splitlines()]
    assert logged[-1]["agent"] == "librarian" and "secretbench" not in json.dumps(logged[-1]).lower()

    calls_before = len(lab["exa"].calls)
    status, _ = post(base, "/exa/contents", {"urls": ["https://arxiv.org/abs/2401.99999"], "objective": "read"}, tok["librarian"])
    assert status == 403 and len(lab["exa"].calls) == calls_before
    status, body = post(base, "/exa/contents", {"urls": ["https://example.org/methods"], "objective": "read", "max_chars": 500}, tok["librarian"])
    assert status == 200 and body["results"][0]["text"] == "page text"
    assert lab["exa"].calls[-1][1]["text"] == {"maxCharacters": 500}


def test_every_request_is_logged_without_the_token(lab):
    base, tok, log = lab["base"], lab["tokens"], lab["log"]
    post(base, "/memory/recall", {"query": "x"}, tok["worker-a"])
    post(base, "/memory/recall", {"query": "x"}, "bad-token")
    events = log.events("gateway.request")
    assert events[-2]["agent"] == "worker-a" and events[-2]["status"] == 200 and events[-2]["path"] == "/memory/recall"
    assert events[-1]["agent"] is None and events[-1]["status"] == 401
    assert all("seconds" in e for e in events)
    text = (lab["tmp"] / "events.jsonl").read_text()
    assert not any(t in text for t in tok.values())


def test_bad_requests(lab):
    base, tok = lab["base"], lab["tokens"]
    assert post(base, "/nowhere", {}, tok["worker-a"])[0] == 404
    assert post(base, "/memory/write", None, tok["worker-a"], raw=b"{not json")[0] == 400
    assert post(base, "/memory/write", {"text": "", "label": "x"}, tok["worker-a"])[0] == 400


def test_issue_token_for_unknown_agent_is_refused(lab):
    with pytest.raises(KeyError):
        lab["gw"].issue_token("stranger")


def run_tool(lab, agent, *args):
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(LAB_GATEWAY_URL=lab["base"], LAB_GATEWAY_TOKEN=lab["tokens"][agent])
    return subprocess.run([sys.executable, *args], cwd=lab["tmp"], env=env, capture_output=True, text=True, timeout=30)


def test_memory_cli_round_trip(lab):
    out = run_tool(lab, "worker-a", str(ROOT / "lab/tools/memory.py"), "write", "Team A rig code is 41723.", "--label", "rig")
    assert out.returncode == 0, out.stderr
    fid = out.stdout.split()[1]
    out = run_tool(lab, "head", str(ROOT / "lab/tools/memory.py"), "pass-down", "--to", "team-b", "--from", fid,
                   "--reason", "team-b runs the rig", "Use rig code 41723.")
    assert out.returncode == 0, out.stderr
    out = run_tool(lab, "worker-b", str(ROOT / "lab/tools/memory.py"), "recall", "rig code")
    assert out.returncode == 0 and "41723" in out.stdout and fid in out.stdout  # fid appears as came_from
    out = run_tool(lab, "worker-b", str(ROOT / "lab/tools/memory.py"), "pass-down", "--to", "team-a", "--from", fid,
                   "--reason", "r", "text")
    assert out.returncode != 0 and "403" in out.stderr


def test_research_cli(lab):
    out = run_tool(lab, "librarian", str(ROOT / "lab/tools/research.py"), "search", "methods", "--objective", "find methods", "--num", "3")
    assert out.returncode == 0, out.stderr
    assert "https://example.org/methods" in out.stdout and "example.org/source" not in out.stdout
    out = run_tool(lab, "worker-a", str(ROOT / "lab/tools/research.py"), "search", "methods", "--objective", "o")
    assert out.returncode != 0 and "403" in out.stderr
    out = run_tool(lab, "librarian", str(ROOT / "lab/tools/research.py"), "contents", "https://example.org/methods", "--objective", "read")
    assert out.returncode == 0 and "page text" in out.stdout
