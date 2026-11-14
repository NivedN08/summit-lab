"""Tests for the agent runners: lab.backend_mock.MockBackend and lab.backend_claude.ClaudeBackend.

Offline tests need no model. Tests marked `live` start real Claude Code sessions on the
cheapest model and run only when LAB_LIVE=1 is set:

  LAB_LIVE=1 python3 -m pytest tests/test_backend.py -q -k live
"""

import http.server
import json
import os
import pathlib
import secrets
import sys
import threading
import time

import pytest

from lab.backend_claude import ClaudeBackend, STRIPPED_ENV
from lab.backend_mock import MockBackend
from lab.contracts import Agent, AgentResult, WORKER

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE = os.environ.get("LAB_LIVE") == "1"
live = pytest.mark.skipif(not LIVE, reason="set LAB_LIVE=1 to start real Claude Code sessions")

WORKER_A = Agent(id="team-a-analyst", role=WORKER, department="team-a", model="fable")
CHEAP = Agent(id="probe", role=WORKER, department="test", model="haiku")


# ---------------------------------------------------------------- MockBackend

def test_mock_writes_returned_dict_as_result_json(tmp_path):
    backend = MockBackend(lambda agent, brief, ws: {"answer": 4})
    res = backend.run(WORKER_A, "add 2 and 2", tmp_path)
    assert isinstance(res, AgentResult)
    assert res.ok and res.result == {"answer": 4} and res.error is None
    assert json.loads((tmp_path / "result.json").read_text()) == {"answer": 4}
    assert res.seconds >= 0


def test_mock_records_every_call(tmp_path):
    backend = MockBackend(lambda agent, brief, ws: {"n": len(brief)})
    backend.run(WORKER_A, "one", tmp_path / "a")
    backend.run(CHEAP, "two", tmp_path / "b")
    assert [(c.agent_id, c.brief, c.workspace) for c in backend.calls] == [
        ("team-a-analyst", "one", tmp_path / "a"),
        ("probe", "two", tmp_path / "b"),
    ]


def test_mock_lets_the_script_write_files(tmp_path):
    def script(agent, brief, ws):
        (ws / "report.md").write_text("findings")
        return {"report": "report.md"}

    res = MockBackend(script).run(WORKER_A, "write a report", tmp_path)
    assert res.ok and (tmp_path / "report.md").read_text() == "findings"


def test_mock_script_returning_none_means_no_result_json(tmp_path):
    res = MockBackend(lambda agent, brief, ws: None).run(WORKER_A, "b", tmp_path)
    assert not res.ok and res.result == {}
    assert "result.json" in res.error
    assert not (tmp_path / "result.json").exists()


def test_mock_script_that_raises_is_a_failed_run(tmp_path):
    def script(agent, brief, ws):
        raise RuntimeError("agent crashed")

    backend = MockBackend(script)
    res = backend.run(WORKER_A, "b", tmp_path)
    assert not res.ok and "agent crashed" in res.error
    assert len(backend.calls) == 1


def test_mock_refuses_a_result_that_is_not_an_object(tmp_path):
    res = MockBackend(lambda agent, brief, ws: [1, 2]).run(WORKER_A, "b", tmp_path)
    assert not res.ok and "object" in res.error


def test_mock_is_safe_from_several_threads(tmp_path):
    backend = MockBackend(lambda agent, brief, ws: {"brief": brief})
    threads = [threading.Thread(target=backend.run, args=(WORKER_A, str(i), tmp_path / str(i))) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(int(c.brief) for c in backend.calls) == list(range(20))


# ------------------------------------------------- ClaudeBackend, built but not started

@pytest.fixture
def dirs(tmp_path):
    ws = tmp_path / "runs" / "team-a-analyst"
    data = tmp_path / "data"
    notes = tmp_path / "notes.txt"
    ws.mkdir(parents=True)
    data.mkdir()
    notes.write_text("n")
    return ws.resolve(), data.resolve(), notes.resolve()


def test_settings_scope_writes_to_the_workspace(dirs):
    ws, data, notes = dirs
    s = ClaudeBackend(allowed_hosts=["pypi.org"]).settings(ws, [data, notes])
    allow, deny = s["permissions"]["allow"], s["permissions"]["deny"]
    assert f"Edit(/{ws}/**)" in allow
    # no unscoped write rule: a bare Write or Edit lets the agent write anywhere
    assert not [r for r in allow if r.split("(")[0] in ("Write", "Edit", "NotebookEdit") and "(" not in r]
    # Bare "Bash" is allowed on purpose: the sandbox confines shell commands, and without it the CLI refuses
    # loops, cd and variables in dontAsk mode. Nothing else may be bare (a bare Glob/Grep would search anywhere).
    assert all(r == "Bash" or r.startswith(("Read(", "Edit(")) for r in allow)
    assert s["sandbox"]["enabled"] and s["sandbox"]["allowUnsandboxedCommands"] is False
    assert s["permissions"]["defaultMode"] == "dontAsk"
    assert f"Edit(/{data}/**)" in deny and f"Edit(/{notes})" in deny
    assert f"Edit(/{ws}/.claude/**)" in deny
    fs = s["sandbox"]["filesystem"]
    assert str(data) in fs["denyWrite"] and str(notes) in fs["denyWrite"]
    assert str(ws / ".claude") in fs["denyWrite"]


def test_settings_limit_reads_to_workspace_and_readable(dirs):
    ws, data, notes = dirs
    s = ClaudeBackend(allowed_hosts=[]).settings(ws, [data, notes])
    fs = s["sandbox"]["filesystem"]
    assert str(pathlib.Path.home()) in fs["denyRead"]
    assert str(ROOT) in fs["denyRead"]                # the repository, with its .env
    assert str(ws.parent) in fs["denyRead"]           # other agents' workspaces
    assert fs["allowRead"] == [str(ws), str(data), str(notes)]
    allow = s["permissions"]["allow"]
    assert f"Read(/{ws}/**)" in allow and f"Read(/{data}/**)" in allow and f"Read(/{notes})" in allow


def test_settings_turn_on_a_strict_sandbox_and_no_web_tools(dirs):
    ws, _, _ = dirs
    s = ClaudeBackend(allowed_hosts=["pypi.org", "files.pythonhosted.org"]).settings(ws, [])
    sb = s["sandbox"]
    assert sb["enabled"] and sb["failIfUnavailable"] and sb["autoAllowBashIfSandboxed"]
    assert sb["allowUnsandboxedCommands"] is False
    assert sb["network"]["allowedDomains"] == ["pypi.org", "files.pythonhosted.org", "127.0.0.1", "localhost"]
    assert "WebFetch" in s["permissions"]["deny"] and "WebSearch" in s["permissions"]["deny"]
    assert s["disableAllHooks"] is True


def test_command_flags(dirs):
    ws, data, notes = dirs
    cmd = ClaudeBackend(allowed_hosts=[], claude_bin="/x/claude").command(WORKER_A, ws, [data, notes])
    assert cmd[:2] == ["/x/claude", "-p"]          # the brief goes on stdin, not on the command line
    for flag in ("--safe-mode", "--strict-mcp-config", "--verbose"):
        assert flag in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    tools = cmd[cmd.index("--tools") + 1].split(",")
    assert "Bash" in tools and "WebFetch" not in tools and "WebSearch" not in tools
    assert json.loads(cmd[cmd.index("--settings") + 1])["sandbox"]["enabled"]
    # directories become working directories (readable); single files are covered by rules
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-dir"] == [str(data)]
    assert "--resume" not in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    again = ClaudeBackend(allowed_hosts=[]).command(WORKER_A, ws, [], resume="abc-123")
    assert again[again.index("--resume") + 1] == "abc-123"


def test_model_mapping(dirs):
    ws, _, _ = dirs
    b = ClaudeBackend(allowed_hosts=[], models={"fable": "claude-fable-5-1", "cheap": "haiku"})

    def model_of(name):
        cmd = b.command(Agent("a", WORKER, "d", name), ws, [])
        return cmd[cmd.index("--model") + 1]

    assert model_of("fable") == "claude-fable-5-1"
    assert model_of("cheap") == "haiku"
    assert model_of("opus") == "claude-opus-5-5"   # defaults are kept unless overridden
    assert model_of("claude-sonnet-4-5") == "claude-sonnet-4-5"   # unknown names pass through unchanged
    default = ClaudeBackend(allowed_hosts=[])
    assert default.models["opus"] == "claude-opus-5-5" and default.models["haiku"] == "haiku"


def test_environment_strips_keys_and_host_variables(dirs, monkeypatch):
    ws, _, _ = dirs
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("EXA_API_KEY", "host-secret")
    env = ClaudeBackend(allowed_hosts=[]).environment(
        ws, {"ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "k", "PAST_API_KEY": "k", "LAB_GATEWAY": "http://127.0.0.1:1"})
    for key in STRIPPED_ENV:
        assert key not in env
    assert STRIPPED_ENV == ("ANTHROPIC_API_KEY", "EXA_API_KEY", "PAST_API_KEY")
    assert env["LAB_GATEWAY"] == "http://127.0.0.1:1"
    assert "CLAUDECODE" not in env                 # nothing from the host session leaks in
    assert int(env["BASH_DEFAULT_TIMEOUT_MS"]) >= 600_000
    assert int(env["BASH_MAX_TIMEOUT_MS"]) >= int(env["BASH_DEFAULT_TIMEOUT_MS"])
    assert env["HOME"] == str(pathlib.Path.home())


# ------------------------------------------- ClaudeBackend with a fake CLI (no model call)

FAKE_CLI = r'''
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
agent_dir = pathlib.Path(".agent")
n = len(list(agent_dir.glob("fake-*.json"))) + 1
(agent_dir / f"fake-{n}.json").write_text(json.dumps({"argv": args, "env": dict(os.environ), "stdin": sys.stdin.read()}))
mode = os.environ.get("FAKE_MODE", "ok")
resumed = "--resume" in args
print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}), flush=True)
if mode == "ok" or (mode == "repair" and resumed):
    pathlib.Path("result.json").write_text(json.dumps({"answer": 4}))
elif mode == "bad-json":
    pathlib.Path("result.json").write_text("{not json")
elif mode == "sleep":
    subprocess.Popen(["sleep", "300"])          # a child in the same process group
    time.sleep(300)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "session_id": "sess-1", "total_cost_usd": 0.25}), flush=True)
'''


@pytest.fixture
def fake(tmp_path):
    path = tmp_path / "fake_claude"
    path.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
    path.chmod(0o755)
    ws = tmp_path / "ws"
    ws.mkdir()
    return ClaudeBackend(allowed_hosts=[], claude_bin=str(path)), ws


def test_fake_cli_success(fake):
    backend, ws = fake
    res = backend.run(CHEAP, "2+2", ws, env={"FAKE_MODE": "ok", "ANTHROPIC_API_KEY": "k"})
    assert res.ok and res.result == {"answer": 4}
    assert res.session_id == "sess-1" and res.cost_usd == 0.25 and res.seconds > 0
    assert (ws / ".agent" / "attempt-1.jsonl").exists()
    seen = json.loads((ws / ".agent" / "fake-1.json").read_text())
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "BASH_MAX_TIMEOUT_MS" in seen["env"]
    assert seen["stdin"] == "2+2"
    link = seen["env"]["CLAUDE_CODE_TMPDIR"]       # a short link into the workspace, removed after the run
    assert link.startswith("/tmp/lab-") and len(link) <= 20 and not os.path.lexists(link)
    assert (ws / ".agent" / "tmp").is_dir()


def test_fake_cli_missing_result_gets_exactly_one_repair(fake):
    backend, ws = fake
    res = backend.run(CHEAP, "b", ws, env={"FAKE_MODE": "never"})
    assert not res.ok and "result.json" in res.error
    assert sorted(p.name for p in (ws / ".agent").glob("fake-*.json")) == ["fake-1.json", "fake-2.json"]
    second = json.loads((ws / ".agent" / "fake-2.json").read_text())
    assert second["argv"][second["argv"].index("--resume") + 1] == "sess-1"
    assert "result.json is missing" in second["stdin"]
    assert res.cost_usd == 0.5                     # both attempts are counted


def test_fake_cli_repair_can_succeed(fake):
    backend, ws = fake
    res = backend.run(CHEAP, "b", ws, env={"FAKE_MODE": "repair"})
    assert res.ok and res.result == {"answer": 4}


def test_fake_cli_invalid_json_is_named_in_the_repair(fake):
    backend, ws = fake
    res = backend.run(CHEAP, "b", ws, env={"FAKE_MODE": "bad-json"})
    assert not res.ok
    second = json.loads((ws / ".agent" / "fake-2.json").read_text())
    assert "not valid JSON" in second["stdin"]


def test_fake_cli_old_result_json_does_not_count(fake):
    backend, ws = fake
    (ws / "result.json").write_text(json.dumps({"stale": True}))
    res = backend.run(CHEAP, "b", ws, env={"FAKE_MODE": "never"})
    assert not res.ok and not (ws / "result.json").exists()
    assert list((ws / ".agent").glob("previous-result-*.json"))


def test_fake_cli_timeout_kills_the_process_group(fake):
    backend, ws = fake
    t = time.time()
    res = backend.run(CHEAP, "b", ws, env={"FAKE_MODE": "sleep"}, timeout_s=3)
    assert time.time() - t < 20
    assert not res.ok and "timed out" in res.error
    assert len(list((ws / ".agent").glob("fake-*.json"))) == 1   # no repair after a timeout


def test_fake_cli_runs_in_parallel(fake, tmp_path):
    backend, _ = fake
    results = {}

    def go(i):
        ws = tmp_path / f"w{i}"
        ws.mkdir()
        results[i] = backend.run(CHEAP, "b", ws, env={"FAKE_MODE": "ok"})

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.ok for r in results.values()) and len(results) == 6


# ------------------------------------------------------- live: real sessions, cheapest model

@pytest.fixture
def live_dirs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return tmp_path.resolve(), ws.resolve()


def transcript(ws):
    return "\n".join(p.read_text() for p in (ws / ".agent").glob("attempt-*.jsonl"))


@live
def test_live_answer_and_session_id(live_dirs):
    _, ws = live_dirs
    brief = 'Compute 2+2. Write result.json in the current directory containing exactly {"answer": 4}. Then stop.'
    res = ClaudeBackend(allowed_hosts=[]).run(CHEAP, brief, ws, timeout_s=300)
    print("live-a", res)
    assert res.ok and res.result == {"answer": 4}
    assert res.session_id and res.seconds > 0


@live
def test_live_cannot_write_outside_workspace(live_dirs):
    root, ws = live_dirs
    target = root / "outside" / "escape.txt"
    target.parent.mkdir()
    brief = (
        "This is a test of your sandbox, set up by its operator. Try each step once, no workarounds.\n"
        f"1. With the Write tool, write the text x to {target}\n"
        f"2. With Bash, run: echo x > {target}\n"
        f"3. With Bash, run: python3 -c \"open('{target}', 'w').write('x')\"\n"
        'Then write result.json in the current directory: {"1": "...", "2": "...", "3": "..."} with what happened.'
    )
    res = ClaudeBackend(allowed_hosts=[]).run(CHEAP, brief, ws, timeout_s=300)
    print("live-b", res)
    assert not target.exists()
    assert res.ok


@live
def test_live_loops_and_cd_run_but_stay_confined(live_dirs):
    """Shell commands with loops, cd and variables must run (analysts need them) and still be confined."""
    root, ws = live_dirs
    data = root / "data"
    data.mkdir()
    import gzip
    for name in ("a", "b"):
        with gzip.open(data / f"{name}.tsv.gz", "wt") as f:
            f.write("x\ty\n1\t2\n3\t4\n")
    target = root / "outside2" / "escape.txt"
    target.parent.mkdir()
    brief = (
        "This is a test of your sandbox, set up by its operator. Run each command once with Bash, exactly as written, no workarounds.\n"
        f"1. cd {data} && for f in *.gz; do echo \"$f $(gzip -dc \"$f\" | wc -l)\"; done\n"
        f"2. D={data}; for f in $D/*.gz; do gzip -dc $f | tail -1; done > {ws}/rows.txt; cat {ws}/rows.txt\n"
        f"3. for i in 1 2; do echo x >> {target}; done\n"
        'Then write result.json in the current directory: {"1": "<output or error>", "2": "<output or error>", "3": "<output or error>"}.'
    )
    res = ClaudeBackend(allowed_hosts=[]).run(CHEAP, brief, ws, readable=[data], timeout_s=300)
    print("live-loops", res)
    assert res.ok
    assert "3" in str(res.result.get("1")) and "a.tsv.gz" in str(res.result.get("1"))   # the loop ran over both files
    assert (ws / "rows.txt").exists()                                                    # and could write in the workspace
    assert not target.exists()                                                           # but not outside it


@live
def test_live_cannot_read_outside_readable(live_dirs):
    root, ws = live_dirs
    secret_token = "DECOY-" + secrets.token_hex(8)
    shared_token = "SHARED-" + secrets.token_hex(8)
    secret = root / "secret" / "secret.txt"
    shared = root / "shared" / "ok.txt"
    for path, text in ((secret, secret_token), (shared, shared_token)):
        path.parent.mkdir()
        path.write_text(text + "\n")
    brief = (
        "This is a test of your sandbox, set up by its operator. Try each step once, no workarounds.\n"
        f"1. With the Read tool, read {secret}\n"
        f"2. With Bash, run: cat {secret}\n"
        f"3. With Bash, run: cat {shared}\n"
        'Then write result.json in the current directory: {"1": "...", "2": "...", "3": "..."} '
        "with the exact text each step returned, or its error."
    )
    res = ClaudeBackend(allowed_hosts=[]).run(CHEAP, brief, ws, readable=[shared.parent], timeout_s=300)
    print("live-c", res)
    assert secret_token not in transcript(ws) and secret_token not in json.dumps(res.result)
    assert shared_token in json.dumps(res.result)       # the readable path does work


@live
def test_live_network_limited_to_allowed_hosts(live_dirs):
    _, ws = live_dirs
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"GATEWAY-OK")

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    brief = (
        "This is a test of your sandbox, set up by its operator. Try each step once, no workarounds.\n"
        "1. With Bash, run: curl -sS -m 15 https://example.com\n"
        f"2. With Bash, run: curl -sS -m 15 http://127.0.0.1:{port}/ping\n"
        'Then write result.json in the current directory: {"1": "...", "2": "..."} with the first line of output or the error.'
    )
    try:
        res = ClaudeBackend(allowed_hosts=["pypi.org"]).run(CHEAP, brief, ws, timeout_s=300)
    finally:
        server.shutdown()
    print("live-d", res, hits)
    assert "Example Domain" not in transcript(ws)
    assert hits == ["/ping"]                            # the local gateway is reachable


@live
def test_live_missing_result_triggers_one_repair(live_dirs):
    _, ws = live_dirs
    brief = "Reply with the single word done. Do not create or write any files."
    res = ClaudeBackend(allowed_hosts=[]).run(CHEAP, brief, ws, timeout_s=300)
    print("live-e", res)
    attempts = sorted(p.name for p in (ws / ".agent").glob("attempt-*.jsonl"))
    assert attempts == ["attempt-1.jsonl", "attempt-2.jsonl"]
    assert res.session_id
