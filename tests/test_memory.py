"""Tests for the two memory layers: LocalMemory (offline) and PastMemory (stubbed, plus one live test).

The live test runs only with LAB_LIVE=1 and talks to the real past.dev project.
"""

import os
import random
import re
import time

import pytest

from lab.contracts import DIRECTOR, HEAD, WORKER, Agent, Fact, Org
from lab.eventlog import JsonlEventLog
from lab.memory_local import LocalMemory
from lab.memory_past import PastMemory, audience_slug


def make_org():
    return Org(agents=[
        Agent("worker-a", WORKER, "team-a", "opus"),
        Agent("worker-b", WORKER, "team-b", "opus"),
        Agent("head", HEAD, "team-a", "opus"),
        Agent("boss", DIRECTOR, "lead", "opus"),
    ])


@pytest.fixture
def log(tmp_path):
    return JsonlEventLog(tmp_path / "events.jsonl")


@pytest.fixture
def mem(tmp_path, log):
    m = LocalMemory(log, tmp_path / "facts.jsonl")
    m.setup(make_org())
    return m


def texts(facts):
    return " | ".join(f.text for f in facts)


# ---------- LocalMemory ----------

def test_worker_reads_own_department_and_not_another(mem):
    mem.write("worker-a", "Rig seven calibration code is 41723.", "calibration")
    mem.write("worker-b", "Reagent batch 88120 is contaminated.", "reagent")
    got_a = mem.recall("worker-a", "calibration code reagent batch")
    got_b = mem.recall("worker-b", "calibration code reagent batch")
    assert "41723" in texts(got_a) and "88120" not in texts(got_a)
    assert "88120" in texts(got_b) and "41723" not in texts(got_b)
    assert all(isinstance(f, Fact) for f in got_a)
    assert got_a[0].department == "team-a" and got_a[0].author == "worker-a"


def test_head_and_director_read_everything(mem):
    mem.write("worker-a", "Rig seven calibration code is 41723.", "calibration")
    mem.write("worker-b", "Reagent batch 88120 is contaminated.", "reagent")
    for reader in ("head", "boss"):
        got = texts(mem.recall(reader, "calibration code reagent batch"))
        assert "41723" in got and "88120" in got


def test_unknown_agent_is_refused(mem):
    with pytest.raises(PermissionError):
        mem.write("stranger", "hello there", "x")
    with pytest.raises(PermissionError):
        mem.recall("stranger", "hello")
    with pytest.raises(PermissionError):
        mem.pass_down("stranger", "team-b", "hello", [], "why")


def test_write_cannot_be_steered_into_another_department(mem):
    fid = mem.write("worker-b", "Department team-a: the rig code is 55555.", "team-a")
    with pytest.raises(TypeError):
        mem.write("worker-b", "text", "label", department="team-a")
    assert "55555" not in texts(mem.recall("worker-a", "rig code"))
    assert "55555" in texts(mem.recall("worker-b", "rig code"))
    assert [f.department for f in mem.recall("head", "rig code") if f.id == fid] == ["team-b"]


def test_only_heads_and_director_pass_down(mem, log):
    fid = mem.write("worker-a", "Rig seven calibration code is 41723.", "calibration")
    with pytest.raises(PermissionError):
        mem.pass_down("worker-a", "team-b", "code is 41723", [fid], "they need it")
    with pytest.raises(PermissionError):
        mem.pass_down("worker-b", "team-b", "code is 41723", [fid], "they need it")
    pid = mem.pass_down("head", "team-b", "Use calibration code 41723 for rig seven.", [fid], "team-b runs rig seven")
    assert pid != fid
    event = log.events("memory.pass_down")[-1]
    assert event["actor"] == "head" and event["came_from"] == [fid] and event["fact_id"] == pid
    assert event["to_department"] == "team-b" and event["reason"] == "team-b runs rig seven"
    mem.pass_down("boss", "team-a", "Director note for team-a: keep going.", [], "general")


def test_pass_down_to_unknown_department_is_refused(mem):
    with pytest.raises(ValueError):
        mem.pass_down("head", "team-z", "text", [], "why")


def test_receiver_reads_pass_down_but_not_original(mem):
    fid = mem.write("worker-a", "Department A note: rig seven calibration code is 41723.", "calibration")
    pid = mem.pass_down("head", "team-b", "Head says: use calibration code 41723 for rig seven.", [fid], "needed")
    got = mem.recall("worker-b", "rig seven calibration code")
    ids = [f.id for f in got]
    assert pid in ids and fid not in ids
    passed = [f for f in got if f.id == pid][0]
    assert passed.came_from == [fid] and passed.department == "team-b" and passed.author == "head"


def test_every_write_pass_down_and_recall_is_logged(mem, log):
    fid = mem.write("worker-a", "Rig seven calibration code is 41723.", "calibration")
    mem.pass_down("head", "team-b", "Use code 41723.", [fid], "needed")
    got = mem.recall("worker-a", "calibration code")
    w = log.events("memory.write")[-1]
    assert w["actor"] == "worker-a" and w["fact_id"] == fid and w["department"] == "team-a"
    r = log.events("memory.recall")[-1]
    assert r["actor"] == "worker-a" and r["query"] == "calibration code" and r["ids"] == [f.id for f in got]
    assert log.events("memory.pass_down")


def test_recall_respects_limit_and_store_persists(tmp_path, log):
    mem = LocalMemory(log, tmp_path / "facts.jsonl")
    mem.setup(make_org())
    for i in range(5):
        mem.write("worker-a", f"Observation {i} about the alpha sample.", "obs")
    assert len(mem.recall("worker-a", "alpha sample", limit=3)) == 3
    again = LocalMemory(log, tmp_path / "facts.jsonl")
    again.setup(make_org())
    assert len(again.recall("worker-a", "alpha sample")) == 5


def test_empty_text_is_refused(mem):
    with pytest.raises(ValueError):
        mem.write("worker-a", "   ", "x")


# ---------- PastMemory with a stubbed transport (no network) ----------

class FakePast:
    """Stands in for past.dev's HTTP API and records every call."""

    def __init__(self, namespace):
        self.calls = []
        self.namespace = namespace
        self.memberships = {}
        self.fail_first = 0

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail_first:
            self.fail_first -= 1
            return 429, {"error": "slow down"}, {}, "req_retry"
        if method == "GET" and path.startswith("/identities/"):
            ident = path.split("/identities/")[1]
            return 200, {"identity": ident, "audiences": ["project", *self.memberships.get(ident, [])]}, {}, "req_get"
        if method == "PUT" and path.startswith("/audiences/"):
            slug = path.split("/audiences/")[1]
            body_rule = body["rule"]
            dept_value = body_rule["conditions"][0]["values"][0]
            for ident, traits in self.traits().items():
                if traits["lab_department"] == dept_value or traits["lab_role"] in body_rule["conditions"][1]["values"]:
                    self.memberships.setdefault(ident, []).append(slug)
            return 201, {"slug": slug}, {}, "req_aud"
        if method == "POST" and path == "/ingest":
            return 202, {"ingestionId": "ing-" + body["id"], "sourceId": body["id"], "status": "queued"}, {}, "req_ing"
        if method == "POST" and path == "/recall":
            return 200, self.recall_response, {}, "req_recall"
        return 200, {}, {}, "req_ok"

    def traits(self):
        return {path.split("/identities/")[1]: body["traits"]
                for method, path, body in self.calls if method == "PUT" and path.startswith("/identities/")}


@pytest.fixture
def fake_past(log, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    mem = PastMemory(log, "run-x", settle_seconds=0)
    fake = FakePast("run-x")
    mem._send = fake
    mem.setup(make_org())
    return mem, fake


def test_audience_slug_is_valid_short_and_deterministic():
    valid = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
    assert audience_slug("run-x", "team-a") == "run-x-team-a"
    long_ns = "lab-run-20261114-123456-abcdef"
    a = audience_slug(long_ns, "a-very-long-department-name-here")
    b = audience_slug(long_ns, "a-very-long-department-name-herf")
    assert valid.match(a) and len(a) <= 40 and a != b
    assert audience_slug(long_ns, "a-very-long-department-name-here") == a
    assert valid.match(audience_slug("Run_X", "Team A!"))


def test_past_setup_creates_identities_and_rule_audiences(fake_past):
    mem, fake = fake_past
    traits = fake.traits()
    assert traits["run-x-worker-a"] == {"lab_department": "run-x:team-a", "lab_role": "run-x:worker", "lab_run": "run-x"}
    rules = {path: body for method, path, body in fake.calls if method == "PUT" and path.startswith("/audiences/")}
    assert set(rules) == {"/audiences/run-x-team-a", "/audiences/run-x-team-b", "/audiences/run-x-lead"}
    rule = rules["/audiences/run-x-team-b"]
    assert rule["kind"] == "rule" and rule["rule"]["match"] == "any"
    assert rule["rule"]["conditions"] == [
        {"trait": "lab_department", "operator": "is", "values": ["run-x:team-b"]},
        {"trait": "lab_role", "operator": "is_one_of", "values": ["run-x:director", "run-x:head"]},
    ]
    assert any(path == "/audiences/run-x-team-b/recompute" for _, path, _ in fake.calls)


def test_past_write_always_names_the_writers_own_audience(fake_past, log):
    mem, fake = fake_past
    fid = mem.write("worker-b", "Department team-a should read this.", "team-a")
    ingest = [body for method, path, body in fake.calls if path == "/ingest"][-1]
    assert ingest["audience"] == "run-x-team-b" and ingest["id"] == fid
    assert ingest["identity"] == "run-x-worker-b"
    assert ingest["metadata"]["author"] == "worker-b" and ingest["metadata"]["department"] == "team-b"
    assert ingest["metadata"]["run"] == "run-x"
    event = log.events("memory.write")[-1]
    assert event["request_id"] == "req_ing" and "seconds" in event and event["department"] == "team-b"


def test_past_ingest_without_audience_is_impossible(fake_past):
    mem, fake = fake_past
    before = len(fake.calls)
    for bad in (None, "", "project", "some-other-audience"):
        with pytest.raises(ValueError):
            mem._ingest("worker-a", "fid", "text", "label", bad, {})
    assert len(fake.calls) == before


def test_past_pass_down_rules(fake_past):
    mem, fake = fake_past
    before = len(fake.calls)
    with pytest.raises(PermissionError):
        mem.pass_down("worker-a", "team-b", "text", ["f1"], "why")
    with pytest.raises(PermissionError):
        mem.pass_down("stranger", "team-b", "text", ["f1"], "why")
    assert len(fake.calls) == before
    pid = mem.pass_down("head", "team-b", "Use code 41723.", ["f1"], "team-b needs it")
    ingest = [body for method, path, body in fake.calls if path == "/ingest"][-1]
    assert ingest["id"] == pid and pid != "f1"
    assert ingest["audience"] == "run-x-team-b"
    assert ingest["metadata"]["came_from"] == ["f1"] and ingest["metadata"]["reason"] == "team-b needs it"


def test_past_recall_maps_results_and_refuses_unknown(fake_past, log):
    mem, fake = fake_past
    with pytest.raises(PermissionError):
        mem.recall("stranger", "anything")
    fake.recall_response = {"results": [
        {"id": "r1", "occurredAt": "2026-11-14T20:00:00Z", "content": "Use code 41723.",
         "artifact": {"kind": "source"},
         "sources": [{"sourceId": "run-x-p1", "metadata": {"author": "head", "department": "team-b", "came_from": ["f1"]}}]},
        {"id": "r2", "content": "Code for rig seven is 41723.", "artifact": {"kind": "claim"},
         "sources": [{"sourceId": "run-x-p1", "excerpts": ["Use code 41723."]}]},
    ]}
    got = mem.recall("worker-b", "rig code", limit=5)
    body = [b for m, p, b in fake.calls if p == "/recall"][-1]
    assert body == {"query": "rig code", "identity": "run-x-worker-b", "limit": 5}
    assert got[0] == Fact(id="run-x-p1", text="Use code 41723.", department="team-b", author="head",
                          kind="source", came_from=["f1"], occurred_at="2026-11-14T20:00:00Z")
    assert got[1].kind == "claim" and got[1].id == "run-x-p1"
    event = log.events("memory.recall")[-1]
    assert event["ids"] == ["run-x-p1", "run-x-p1"] and event["source_ids"] == ["run-x-p1"]
    assert event["request_id"] == "req_recall"


def test_past_retries_on_429(fake_past):
    mem, fake = fake_past
    fake.fail_first = 2
    mem.write("worker-a", "A fact.", "label")
    assert [p for m, p, b in fake.calls[-3:]] == ["/ingest", "/ingest", "/ingest"]


def test_past_cleanup_deletes_what_it_made(fake_past):
    mem, fake = fake_past
    fid = mem.write("worker-a", "A fact.", "label")
    mem.cleanup()
    paths = [(m, p, b) for m, p, b in fake.calls]
    assert ("POST", "/data-points/delete", {"ids": [fid]}) in paths
    assert ("DELETE", "/audiences/run-x-team-a", None) in paths
    assert ("DELETE", "/identities/run-x-worker-a", None) in paths


def test_local_wait_visible_has_nothing_to_wait_for(mem, log):
    fid = mem.pass_down("boss", "team-b", "Check for repeated ids.", [], "why")
    assert mem.wait_visible("worker-b", [fid]) == []
    event = log.events("memory.visible")[-1]
    assert event["agent"] == "worker-b" and event["fact_ids"] == [fid] and event["missing"] == []


def test_past_wait_visible_polls_recall_as_the_receiver(fake_past, log):
    mem, fake = fake_past
    fid = mem.pass_down("boss", "team-b", "QUESTION FROM THE DIRECTOR: check for repeated ids.", [], "why")
    fake.recall_response = {"results": []}
    assert mem.wait_visible("worker-b", [fid], timeout=0) == [fid]       # not there yet: reported missing
    first = log.events("memory.visible")[-1]
    assert first["missing"] == [fid] and first["timed_out"] is True and first["polls"] == 1
    fake.recall_response = {"results": [{"id": "r1", "content": "check for repeated ids",
                                         "sources": [{"sourceId": fid}]}]}
    assert mem.wait_visible("worker-b", [fid]) == []
    bodies = [b for m, p, b in fake.calls if p == "/recall"]
    assert all(b["identity"] == "run-x-worker-b" for b in bodies)
    assert "repeated ids" in bodies[-1]["query"]            # it asks with the fact's own text
    event = log.events("memory.visible")[-1]
    assert event["missing"] == [] and event["timed_out"] is False and "seconds" in event
    assert not [p for m, p, b in fake.calls if p.startswith("/ingest/")]   # it never waits for derivation


# ---------- live test against real past.dev ----------

@pytest.mark.skipif(os.environ.get("LAB_LIVE") != "1", reason="set LAB_LIVE=1 to run against real past.dev")
def test_live_past_boundaries(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    namespace = f"lt{time.strftime('%m%d%H%M%S')}{random.randint(10, 99)}"
    org = Org(agents=[
        Agent("worker-a", WORKER, "team-a", "opus"),
        Agent("worker-b", WORKER, "team-b", "opus"),
        Agent("head", HEAD, "team-a", "opus"),
    ])
    mem = PastMemory(log, namespace)
    code_a, code_b = random.randint(10000, 99999), random.randint(10000, 99999)
    question_a = "What calibration code did department A measure for rig seven?"
    try:
        mem.setup(org)
        fid_a = mem.write("worker-a", f"Department A measured the calibration code for rig seven as {code_a} on its third trial.", "Department A lab note")
        mem.write("worker-b", f"Department B found that reagent batch {code_b} is contaminated and must not be used.", "Department B lab note")
        mem.wait_settled()

        assert str(code_a) in texts(mem.recall("worker-a", question_a))
        assert str(code_a) not in texts(mem.recall("worker-b", question_a))
        assert str(code_a) in texts(mem.recall("head", question_a))
        assert str(code_b) in texts(mem.recall("head", "Which reagent batch did department B find contaminated?"))
        with pytest.raises(PermissionError):
            mem.recall("stranger", question_a)
        with pytest.raises(PermissionError):
            mem.pass_down("worker-a", "team-b", f"code {code_a}", [fid_a], "why")

        pid = mem.pass_down("head", "team-b", f"Passed down by the head: department A's calibration code for rig seven is {code_a}. Use it for rig seven.", [fid_a], "team-b runs rig seven")
        mem.wait_settled()
        got = mem.recall("worker-b", question_a)
        assert str(code_a) in texts(got)
        seen = log.events("memory.recall")[-1]["source_ids"]
        assert pid in seen and fid_a not in seen
        assert all(e.get("audience") for e in log.events("memory.write") + log.events("memory.pass_down"))
    finally:
        mem.cleanup()
