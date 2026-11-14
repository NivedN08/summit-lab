"""Offline tests for the Modal runner: no network, no Modal calls.

Covers the gateway's verified-header mode, PastMemory.attach, the settings ModalBackend gives a
sandbox, workspace file selection for copy-in and copy-back, and the gateway log copy-back."""

import io
import json
import pathlib
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request

import pytest

from lab import backend_modal as bm
from lab.contracts import HEAD, WORKER, Agent, Org
from lab.eventlog import JsonlEventLog
from lab.gateway import Gateway
from lab.memory_local import LocalMemory
from lab.memory_past import PastMemory, audience_slug
from lab.modal_gateway import ModalGateway
from lab.tools import exa

ROOT = pathlib.Path(__file__).resolve().parents[1]
HEADER = "X-Verified-User-Data"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(base, path, body, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with NO_PROXY.open(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def who(agent_id):
    return {HEADER: json.dumps({"agent": agent_id})}


ORG = Org(agents=[Agent("worker-a", WORKER, "team-a", "opus"), Agent("worker-b", WORKER, "team-b", "opus"),
                  Agent("head", HEAD, "team-a", "opus")])


# ----- the gateway's verified-header mode -----

@pytest.fixture
def header_gateway(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    memory = LocalMemory(log, tmp_path / "facts.jsonl")
    memory.setup(ORG)
    gw = Gateway(ORG, memory, log, identity_header=HEADER)
    base = gw.start()
    yield gw, base, log
    gw.stop()


def test_header_mode_takes_identity_from_the_header(header_gateway):
    gw, base, log = header_gateway
    status, body = post(base, "/memory/write", {"text": "Team A code is 41723.", "label": "code",
                                                 "agent": "worker-b", "department": "team-b"}, who("worker-a"))
    assert status == 200 and body["department"] == "team-a"
    assert log.events("memory.write")[-1]["actor"] == "worker-a"


def test_header_mode_ignores_a_body_claiming_another_agent(header_gateway):
    gw, base, _ = header_gateway
    post(base, "/memory/write", {"text": "Team A code is 41723.", "label": "code"}, who("worker-a"))
    spoof = {"query": "code", "agent": "head", "agent_id": "head", "identity": "head", "role": "head",
             "department": "team-a", "audience": "team-a"}
    status, body = post(base, "/memory/recall", spoof, who("worker-b"))
    assert status == 200 and "41723" not in json.dumps(body)
    assert "41723" in json.dumps(post(base, "/memory/recall", {"query": "code"}, who("head"))[1])


def test_header_mode_without_header_is_401(header_gateway):
    gw, base, _ = header_gateway
    assert post(base, "/memory/recall", {"query": "x"})[0] == 401
    for bad in ("not json", json.dumps({"agent": "nobody"}), json.dumps(["worker-a"]), json.dumps({"agent": 7})):
        assert post(base, "/memory/recall", {"query": "x"}, {HEADER: bad})[0] == 401


def test_header_mode_does_not_use_the_bearer_table(header_gateway):
    gw, base, _ = header_gateway
    token = gw.issue_token("head")
    assert post(base, "/memory/recall", {"query": "x"}, {"Authorization": "Bearer " + token})[0] == 401
    # with both, the header decides: a worker's header plus the head's token reads as the worker
    post(base, "/memory/write", {"text": "Team A code is 41723.", "label": "code"}, who("worker-a"))
    both = {"Authorization": "Bearer " + token, **who("worker-b")}
    assert "41723" not in json.dumps(post(base, "/memory/recall", {"query": "code"}, both)[1])


def test_bearer_mode_ignores_the_header(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    memory = LocalMemory(log, tmp_path / "facts.jsonl")
    memory.setup(ORG)
    gw = Gateway(ORG, memory, log)
    base = gw.start()
    try:
        assert post(base, "/memory/recall", {"query": "x"}, who("head"))[0] == 401
    finally:
        gw.stop()


# ----- PastMemory.attach -----

def test_attach_makes_no_call_and_writes_to_the_departments_audience(tmp_path, monkeypatch):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    memory = PastMemory(log, "sl-test", settle_seconds=0)
    sent = []

    def fake_send(method, path, body=None):
        sent.append((method, path, body))
        return 202, {"status": "queued", "ingestionId": "ing-1"}, {}, "req-1"

    monkeypatch.setattr(memory, "_send", fake_send)
    memory.attach(ORG)
    assert sent == []
    assert log.events("memory.attach")[-1]["audiences"] == {d: audience_slug("sl-test", d) for d in ORG.departments}
    fact_id = memory.write("worker-b", "Team B saw duplicates.", "duplicates")
    method, path, body = sent[-1]
    assert (method, path) == ("POST", "/ingest")
    assert body["audience"] == audience_slug("sl-test", "team-b") and body["identity"] == "sl-test-worker-b"
    assert memory._pending == {fact_id: "ing-1"}
    with pytest.raises(PermissionError):
        memory.write("stranger", "x", "y")
    assert not any(p in ("/audiences", "/identities") or "/audiences/" in p or "/identities/" in p for _, p, _ in sent)


# ----- the sandbox settings ModalBackend uses -----

def test_sandbox_settings():
    backend = bm.ModalBackend()
    opts = backend.sandbox_options(1800, {"LAB_GATEWAY_URL": "https://a-ta-xyz.w.modal.host", "LAB_GATEWAY_TOKEN": "t"})
    assert opts["cpu"] == 2.0 and opts["memory"] == 4096
    assert 1800 < opts["timeout"] <= 1800 + 600
    assert opts["secret_names"] == ["claude-token"]
    assert opts["outbound_domain_allowlist"] == ["api.anthropic.com", "pypi.org", "files.pythonhosted.org",
                                                 "a-ta-xyz.w.modal.host"]
    assert backend.sandbox_options(60)["outbound_domain_allowlist"] == bm.AGENT_HOSTS
    for host in opts["outbound_domain_allowlist"]:
        assert "past.dev" not in host and "exa.ai" not in host and "*" not in host


def test_command_model_mapping_and_no_web_tools(tmp_path):
    backend = bm.ModalBackend()
    for lab_name, cli_name in (("opus", "claude-opus-5-5"), ("fable", "claude-fable-5-2"), ("haiku", "haiku"),
                               ("claude-x", "claude-x")):
        cmd = backend.command(Agent("a", WORKER, "d", lab_name), tmp_path)
        assert cmd[cmd.index("--model") + 1] == cli_name
    cmd = backend.command(Agent("a", WORKER, "d", "opus"), tmp_path, [bm.PUBLIC_DATA], resume="sess-1")
    assert cmd[0] == "claude" and "-p" in cmd and "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--tools") + 1] == "Bash,Read,Write,Edit,Glob,Grep"
    assert "WebFetch" not in cmd[cmd.index("--tools") + 1]
    assert cmd[cmd.index("--resume") + 1] == "sess-1" and cmd[cmd.index("--add-dir") + 1] == str(bm.PUBLIC_DATA)
    assert bm.IMAGE_ENV["IS_SANDBOX"] == "1"          # the CLI refuses to skip permissions as root without it
    for var in ("DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        assert bm.IMAGE_ENV[var] == "1"


def test_environment_strips_keys():
    env = bm.ModalBackend().environment({"LAB_GATEWAY_URL": "u", "LAB_GATEWAY_TOKEN": "t", "ANTHROPIC_API_KEY": "x",
                                         "EXA_API_KEY": "y", "PAST_API_KEY": "z"})
    assert env["LAB_GATEWAY_URL"] == "u" and env["LAB_GATEWAY_TOKEN"] == "t" and env["HOME"] == "/root"
    assert not {"ANTHROPIC_API_KEY", "EXA_API_KEY", "PAST_API_KEY"} & set(env)


def test_no_sealed_path_or_key_file_anywhere(tmp_path):
    backend = bm.ModalBackend()
    inputs = bm.image_inputs()
    assert inputs and all("sealed" not in p.parts and p.name not in (".env", "exa.py") for p in inputs)
    assert all(p in bm.TOOL_FILES or bm.PUBLIC_DATA in p.parents for p in inputs)
    agent = Agent("a", WORKER, "d", "opus")
    everything = json.dumps([backend.sandbox_options(1800, {"LAB_GATEWAY_URL": "https://h.w.modal.host"}),
                             backend.command(agent, tmp_path, [bm.PUBLIC_DATA / "gb01" / "data"]),
                             backend.environment({}), bm.IMAGE_ENV, [str(p) for p in inputs]])
    assert "sealed" not in everything and ".env" not in everything and "exa.py" not in everything
    sealed = ROOT / "data" / "genebench" / "sealed"
    for bad in (sealed, sealed / "x", ROOT, ROOT / ".env", pathlib.Path.home()):
        with pytest.raises(ValueError):
            backend.check_readable([bad], tmp_path)
    ok = backend.check_readable([bm.PUBLIC_DATA / "gb01" / "data", ROOT / "lab" / "tools", tmp_path / "sub"], tmp_path)
    assert len(ok) == 3


# ----- workspace file selection, copy-in and copy-back -----

@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "answer.json").write_text('{"answer": {}}')
    (ws / "sub" / "fit.py").write_text("print(1)")
    (ws / "big.bin").write_bytes(b"x" * 2000)
    for skip in (".agent", ".claude", "__pycache__", "sub/.cache", "node_modules"):
        (ws / skip).mkdir(parents=True, exist_ok=True)
        (ws / skip / "f.txt").write_text("skip me")
    (ws / "link.txt").symlink_to(tmp_path / "outside.txt")
    (tmp_path / "outside.txt").write_text("outside")
    return ws


def test_select_files(workspace):
    kept, skipped = bm.select_files(str(workspace), bm.SKIP_DIRS, 1000, 10_000)
    assert sorted(kept) == ["answer.json", "sub/fit.py"]
    reasons = dict(skipped)
    assert "per-file limit" in reasons["big.bin"] and reasons["link.txt"] == "not a regular file"
    kept, skipped = bm.select_files(str(workspace), bm.SKIP_DIRS, 10_000, 2020)   # 14 + 2000 fit, then 8 more do not
    assert kept == ["answer.json", "big.bin"] and dict(skipped)["sub/fit.py"] == "over the total limit"


def test_pack_script_in_a_separate_python_matches_select_files(workspace, tmp_path):
    out = tmp_path / "out.tar.gz"
    lim = {"skip": list(bm.SKIP_DIRS), "max_file": 1000, "max_total": 10_000}
    proc = subprocess.run([sys.executable, "-I", "-c", bm.pack_script(), str(workspace), str(out), json.dumps(lim)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["kept"] == 2
    back = tmp_path / "back"
    assert sorted(bm.unpack(out.read_bytes(), back)) == ["answer.json", "sub/fit.py"]
    assert (back / "sub" / "fit.py").read_text() == "print(1)"


def test_pack_unpack_round_trip(workspace, tmp_path):
    kept, _ = bm.select_files(str(workspace), bm.SKIP_DIRS, 10_000, 100_000)
    dest = tmp_path / "dest"
    bm.unpack(bm.pack(workspace, kept), dest)
    assert sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()) == sorted(kept)


def test_unpack_drops_unsafe_members(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data=b"x", kind=tarfile.REGTYPE, target=""):
            info = tarfile.TarInfo(name)
            info.type, info.size, info.linkname = kind, (len(data) if kind == tarfile.REGTYPE else 0), target
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        add("ok.txt")
        add("../escape.txt")
        add("/abs.txt")
        add(".agent/attempt-1.jsonl")
        add(".claude/settings.json")
        add("evil", kind=tarfile.SYMTYPE, target="/etc/passwd")
        add("hard", kind=tarfile.LNKTYPE, target="ok.txt")
    dest = tmp_path / "dest"
    assert bm.unpack(buf.getvalue(), dest) == ["ok.txt"]
    assert not (tmp_path / "escape.txt").exists() and not (dest / "evil").exists()


# ----- the Modal gateway's log copy-back, against a fake sandbox -----

class FakeProc:
    def __init__(self, data: bytes, code=0):
        self.stdout = io.BytesIO(data)
        self.code = code

    def wait(self):
        return self.code


class FakeSandbox:
    """Answers `tail -c +N path` from in-memory files."""

    object_id = "sb-fake"

    def __init__(self):
        self.files: dict[str, bytes] = {}

    def exec(self, *args, **kwargs):
        assert args[0] == "tail"
        start, path = int(args[2][1:]) - 1, args[3]
        if path not in self.files:
            return FakeProc(b"", 1)
        return FakeProc(self.files[path][start:])

    def terminate(self):
        pass


def test_gateway_sync_copies_events_and_exa_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(exa, "LOG", tmp_path / "exa_calls.jsonl")
    log = JsonlEventLog(tmp_path / "events.jsonl")
    memory = PastMemory(log, "sl-test")
    memory.attach(ORG)
    gw = ModalGateway(ORG, memory, log, exa_departments=["team-a"])
    assert gw.memory_kind == "past" and gw.needs_keys()
    config = json.dumps(gw.config())
    assert "KEY" not in config and config.count("worker-a") == 1
    sb = gw.sandbox = FakeSandbox()
    first = {"seq": 1, "time": "2026-11-14T20:00:00.000Z", "kind": "memory.write", "actor": "worker-a",
             "fact_id": "f-1", "ingestion_id": "ing-1", "department": "team-a"}
    second = {"seq": 2, "time": "2026-11-14T20:00:01.000Z", "kind": "gateway.request", "actor": "worker-a",
              "path": "/memory/write", "status": 200}
    sb.files["/gateway/events.jsonl"] = (json.dumps(first) + "\n" + json.dumps(second)[:20]).encode()
    sb.files["/gateway/exa_calls.jsonl"] = (json.dumps({"agent": "worker-a", "cost_usd": 0.01}) + "\n").encode()
    assert gw.sync() == 2
    got = log.events("memory.write")[-1]
    assert got["actor"] == "worker-a" and got["remote_time"] == first["time"] and got["remote_seq"] == 1
    assert got["fact_id"] == "f-1" and memory._pending["f-1"] == "ing-1"
    assert exa.spent() == pytest.approx(0.01)
    sb.files["/gateway/events.jsonl"] = (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode()
    assert gw.sync() == 1 and log.events("gateway.request")[-1]["status"] == 200
    assert gw.sync() == 0
    gw.stop()
    assert gw.sandbox is None and log.events("gateway.modal.stop")


def test_gateway_without_keys_needs_no_secret(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    gw = ModalGateway(ORG, LocalMemory(log, tmp_path / "m.jsonl"), log)
    assert gw.memory_kind == "local" and not gw.needs_keys()
    assert "--memory local" in gw.command()[-1]
    with pytest.raises(ValueError):
        ModalGateway(ORG, LocalMemory(log, tmp_path / "m.jsonl"), log, exa_call=lambda p, b: None)


def test_lab_with_modal_backend_refuses_local_memory(tmp_path):
    from lab.run import main
    with pytest.raises(SystemExit) as err:
        main(["lab", "--benchmark", "genebench", "--studies", "gb01", "--run-dir", str(tmp_path / "r"),
              "--backend", "modal", "--memory", "local"])
    assert "--memory past" in str(err.value)
