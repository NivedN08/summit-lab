"""Live tests on Modal. Run only with LAB_MODAL=1:

    LAB_MODAL=1 python3 -m pytest tests/test_modal_live.py -v

Each test is short and terminates every sandbox it starts. (e) needs the Modal Secret
`claude-token`; (f) needs `summit-lab-keys` on Modal and PAST_API_KEY on the laptop (the
orchestrator side sets past.dev up)."""

import json
import os
import pathlib
import secrets
import shlex
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LAB_MODAL") != "1", reason="live Modal test; set LAB_MODAL=1")

from lab import backend_modal as bm  # noqa: E402
from lab.contracts import WORKER, Agent, Org  # noqa: E402
from lab.eventlog import JsonlEventLog  # noqa: E402

TOOLS = bm.ROOT / "lab" / "tools"
ORG = Org(agents=[Agent("t-a", WORKER, "dept-a", "haiku"), Agent("t-b", WORKER, "dept-b", "haiku")])


def secret_exists(name: str) -> bool:
    import modal
    try:
        modal.Secret.from_name(name).hydrate()
        return True
    except modal.exception.NotFoundError:
        return False


def client_sandbox(env: dict):
    """A sandbox with the agent image and the agent network policy for this env, but no secret."""
    import modal
    opts = bm.ModalBackend(secret_name=None).sandbox_options(300, env)
    opts.pop("secret_names")
    return modal.Sandbox.create("sleep", "infinity", app=bm.modal_app(), image=bm.agent_image(), **opts)


def sh(sb, script: str, env: dict | None = None, timeout: int = 120) -> tuple[int, str]:
    p = sb.exec("bash", "-c", script, env=env or {}, timeout=timeout)
    out = p.stdout.read() + p.stderr.read()
    return p.wait(), out


SPOOF = """
import json, os, sys, urllib.request, urllib.error
req = urllib.request.Request(os.environ["LAB_GATEWAY_URL"] + "/memory/recall", method="POST",
    data=json.dumps({"query": sys.argv[1], "agent": sys.argv[2], "agent_id": sys.argv[2], "identity": sys.argv[2],
                     "department": "dept-a", "role": "head"}).encode())
req.add_header("Authorization", "Bearer " + os.environ["LAB_GATEWAY_TOKEN"])
req.add_header("Content-Type", "application/json")
req.add_header("X-Verified-User-Data", json.dumps({"agent": sys.argv[2]}))
try:
    print(urllib.request.urlopen(req, timeout=60).read().decode())
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
"""


def memory_cmd(*args) -> str:
    return "python3 " + shlex.quote(str(TOOLS / "memory.py")) + " " + " ".join(shlex.quote(a) for a in args)


# ----- (a) the image -----

def test_a_image_runs_numpy_and_claude():
    import modal
    sb = modal.Sandbox.create("sleep", "infinity", app=bm.modal_app(), image=bm.agent_image(), timeout=300)
    try:
        code, out = sh(sb, "python3 -c 'import numpy, pandas, scipy, statsmodels, sklearn, matplotlib; print(numpy.__version__)'"
                           " && claude --version && ls " + shlex.quote(str(bm.ROOT / "data" / "genebench"))
                           + " && ls " + shlex.quote(str(TOOLS)) + " && test ! -e " + shlex.quote(str(bm.ROOT / ".env")))
        assert code == 0, out
        assert "1.26.4" in out and bm.CLAUDE_CODE_VERSION in out
        assert "sealed" not in out and "exa.py" not in out and "public" in out and "memory.py" in out
    finally:
        sb.terminate()


# ----- (b) and (c): the gateway in a sandbox, clients in two others -----

@pytest.fixture(scope="module")
def local_gateway(tmp_path_factory):
    from lab.memory_local import LocalMemory
    from lab.modal_gateway import ModalGateway
    tmp = tmp_path_factory.mktemp("gw")
    log = JsonlEventLog(tmp / "events.jsonl")
    gw = ModalGateway(ORG, LocalMemory(log, tmp / "unused.jsonl"), log, memory_kind="local", poll_s=2)
    gw.start()
    envs = {a.id: {"LAB_GATEWAY_TOKEN": gw.issue_token(a.id)} for a in ORG.agents}
    for agent_id, env in envs.items():
        env["LAB_GATEWAY_URL"] = gw.url_for(agent_id)
    clients = {agent_id: client_sandbox(env) for agent_id, env in envs.items()}
    try:
        yield {"gw": gw, "log": log, "envs": envs, "clients": clients}
    finally:
        for sb in clients.values():
            sb.terminate()
        gw.stop()


def test_b_each_agent_reads_only_its_own_department(local_gateway):
    envs, clients = local_gateway["envs"], local_gateway["clients"]
    code, out = sh(clients["t-a"], memory_cmd("write", "Department A rig code is 41723.", "--label", "rig"), envs["t-a"])
    assert code == 0 and "department dept-a" in out, out
    code, out = sh(clients["t-b"], memory_cmd("write", "Department B batch 88120 is bad.", "--label", "batch"), envs["t-b"])
    assert code == 0 and "department dept-b" in out, out
    _, a_reads = sh(clients["t-a"], memory_cmd("recall", "rig code batch"), envs["t-a"])
    _, b_reads = sh(clients["t-b"], memory_cmd("recall", "rig code batch"), envs["t-b"])
    assert "41723" in a_reads and "88120" not in a_reads, a_reads
    assert "88120" in b_reads and "41723" not in b_reads, b_reads


def test_b_body_and_forged_header_claiming_another_agent_are_ignored(local_gateway):
    envs, clients = local_gateway["envs"], local_gateway["clients"]
    sh(clients["t-a"], memory_cmd("write", "Department A rig code is 41723.", "--label", "rig"), envs["t-a"])
    code, out = sh(clients["t-b"], "python3 -c " + shlex.quote(SPOOF) + " 'rig code' t-a", envs["t-b"])
    assert code == 0 and "41723" not in out and '"facts"' in out, out


def test_b_request_without_token_is_refused(local_gateway):
    envs, clients = local_gateway["envs"], local_gateway["clients"]
    url = envs["t-a"]["LAB_GATEWAY_URL"]
    code, out = sh(clients["t-a"], f"curl -s -o /dev/null -w '%{{http_code}}' -X POST -d '{{}}' {shlex.quote(url)}/memory/recall")
    assert out.strip() == "401", out
    # a token for another agent's URL does not open this one either
    code, out = sh(clients["t-a"], f"curl -s -o /dev/null -w '%{{http_code}}' -X POST -d '{{}}' "
                                   f"-H 'Authorization: Bearer {envs['t-b']['LAB_GATEWAY_TOKEN']}' {shlex.quote(url)}/memory/recall")
    assert out.strip() in ("401", "403"), out


def test_b_gateway_events_are_copied_back(local_gateway):
    gw, log = local_gateway["gw"], local_gateway["log"]
    gw.sync()
    requests = log.events("gateway.request")
    assert requests and all(e.get("remote_time") for e in requests)
    assert {e["actor"] for e in requests} <= {"t-a", "t-b", "unknown"}


def test_c_agent_network_reaches_only_the_gateway_and_allowed_hosts(local_gateway):
    envs, clients = local_gateway["envs"], local_gateway["clients"]
    sb, env = clients["t-a"], envs["t-a"]

    def status(url):
        _, out = sh(sb, f"curl -s -o /dev/null -m 15 -w '%{{http_code}}' {shlex.quote(url)}; echo \" rc=$?\"")
        return out.strip()

    assert status("https://example.com").startswith("000"), "example.com was reachable"
    assert status("https://api.past.dev/api/v1/identities").startswith("000"), "past.dev was reachable"
    assert status("https://api.exa.ai/search").startswith("000"), "Exa was reachable"
    assert status("http://example.com").startswith("000"), "plain HTTP was reachable"
    assert status(env["LAB_GATEWAY_URL"] + "/memory/recall").startswith("401")   # reached Modal's proxy (no token)
    assert not status("https://pypi.org/simple/numpy/").startswith("000")
    assert not status("https://api.anthropic.com/v1/messages").startswith("000")


# ----- (d) workspace copy-in and copy-back, with a scripted stand-in for the CLI -----

class ScriptedBackend(bm.ModalBackend):
    """ModalBackend with a bash script in place of the Claude CLI, and no secret."""

    def __init__(self, script: str):
        super().__init__(secret_name=None)
        self.script = script

    def command(self, agent, workspace, readable=(), resume=None):
        return ["bash", "-c", self.script]


def test_d_copy_in_and_back_round_trip(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "earlier.txt").write_text("from an earlier round")
    (ws / "sub" / "kept.csv").write_text("a,b\n1,2\n")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "x.pyc").write_text("cache")
    backend = ScriptedBackend(
        "cat earlier.txt > copy.txt; ls -a > listing.txt; mkdir -p out .cache; echo made > out/new.txt; "
        "echo junk > .cache/junk; head -c 60000000 /dev/zero > huge.bin; echo '{\"ok\": 1}' > result.json")
    result = backend.run(Agent("t-a", WORKER, "dept-a", "haiku"), "brief", ws, readable=[TOOLS], timeout_s=120)
    assert result.ok and result.result == {"ok": 1}, result.error
    assert (ws / "copy.txt").read_text().strip() == "from an earlier round"
    assert (ws / "out" / "new.txt").read_text().strip() == "made"
    assert "__pycache__" not in (ws / "listing.txt").read_text()     # caches are not copied in
    assert not (ws / ".cache").exists() and not (ws / "huge.bin").exists()   # nor back
    skipped = dict(map(tuple, json.loads((ws / ".agent" / "copy-back.json").read_text())["skipped"]))
    assert "per-file limit" in skipped["huge.bin"]
    assert (ws / ".agent" / "attempt-1.jsonl").exists() and backend.live() == []


def test_d_forced_timeout_keeps_partial_answer(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    backend = ScriptedBackend(
        "echo '{\"answer\": {\"x\": 1}}' > answer.json; (while true; do date >> background.log; sleep 1; done) & "
        "echo '{\"type\": \"system\", \"session_id\": \"s-1\"}'; sleep 600")
    t0 = time.monotonic()
    result = backend.run(Agent("t-a", WORKER, "dept-a", "haiku"), "brief", ws, timeout_s=20)
    took = time.monotonic() - t0
    assert not result.ok and "timed out" in result.error and result.session_id == "s-1"
    assert took < 150, took
    assert json.loads((ws / "answer.json").read_text()) == {"answer": {"x": 1}}
    assert (ws / "background.log").exists()
    assert backend.live() == []


# ----- (e) a real agent -----

def test_e_real_agent_writes_result_json(tmp_path):
    if not secret_exists(bm.CLAUDE_SECRET):
        pytest.skip(f"Modal Secret {bm.CLAUDE_SECRET} is missing")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "numbers.txt").write_text("3\n4\n5\n")
    brief = ("Read numbers.txt in your working directory, add the numbers with a short Python script run through "
             "Bash, and write result.json containing {\"sum\": <the sum>}. Then stop.")
    backend = bm.ModalBackend()
    result = backend.run(Agent("t-a", WORKER, "dept-a", "haiku"), brief, ws, readable=[TOOLS], timeout_s=300)
    stderr = (ws / ".agent" / "attempt-1.stderr.txt").read_text() if (ws / ".agent" / "attempt-1.stderr.txt").exists() else ""
    last = (ws / ".agent" / "attempt-1.jsonl").read_text().strip().splitlines()[-1:] or [""]
    assert result.ok, f"{result.error}\nstderr: {stderr[-800:]}\nlast output: {last[0][:800]}"
    assert result.result.get("sum") == 12 and result.session_id
    print(f"seconds={result.seconds:.1f} cost_usd={result.cost_usd}")
    assert backend.live() == []


# ----- (f) the gateway with past.dev memory -----

def test_f_gateway_with_past_memory(tmp_path):
    from lab.memory_past import PastMemory
    from lab.modal_gateway import ModalGateway
    if not secret_exists("summit-lab-keys"):
        pytest.skip("Modal Secret summit-lab-keys is missing")
    log = JsonlEventLog(tmp_path / "events.jsonl")
    memory = PastMemory(log, "sl-modal-" + secrets.token_hex(3))
    memory.setup(ORG)       # laptop side, as the orchestrator does
    gw = ModalGateway(ORG, memory, log, poll_s=2)
    clients = []
    try:
        gw.start()
        envs = {}
        for a in ORG.agents:
            envs[a.id] = {"LAB_GATEWAY_TOKEN": gw.issue_token(a.id), "LAB_GATEWAY_URL": gw.url_for(a.id)}
        sa, sb_ = client_sandbox(envs["t-a"]), client_sandbox(envs["t-b"])
        clients += [sa, sb_]
        code, out = sh(sa, memory_cmd("write", "Department A calibration constant is 61937.", "--label", "calibration"), envs["t-a"])
        assert code == 0 and "department dept-a" in out, out
        gw.sync()
        assert any(e.get("remote_time") for e in log.events("memory.write")), "the write was not copied back"
        memory.wait_settled()       # waits for the fact the agent wrote through the remote gateway
        _, a_reads = sh(sa, memory_cmd("recall", "calibration constant"), envs["t-a"])
        _, b_reads = sh(sb_, memory_cmd("recall", "calibration constant"), envs["t-b"])
        _, b_spoof = sh(sb_, "python3 -c " + shlex.quote(SPOOF) + " 'calibration constant' t-a", envs["t-b"])
        assert "61937" in a_reads, a_reads
        assert "61937" not in b_reads and "61937" not in b_spoof, (b_reads, b_spoof)
        gw.sync()
        attach = [e for e in log.events("memory.attach") if e.get("remote_time")]
        assert attach and not [e for e in log.events("past.call") if e.get("remote_time") and "/audiences" in e["path"]]
    finally:
        for c in clients:
            c.terminate()
        gw.stop()
        memory._written += [e["fact_id"] for e in log.events("memory.write") if e.get("remote_time")]
        memory.cleanup()
