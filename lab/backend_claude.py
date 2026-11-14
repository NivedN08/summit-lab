"""Runs one lab agent as a headless Claude Code session (`claude -p`) on the user's subscription.

    backend = ClaudeBackend(allowed_hosts=["pypi.org", "files.pythonhosted.org"])
    result = backend.run(agent, brief, workspace, readable=[data_dir], env={"LAB_GATEWAY": url})

What the agent gets:
  - tools: Bash, Read, Write, Edit, Glob, Grep. No web tools, no MCP servers, and none of the
    user's own settings, CLAUDE.md, memory, hooks or plugins (--safe-mode, --setting-sources "",
    --strict-mcp-config, disableAllHooks).
  - file tools: writes allowed only inside `workspace` (scoped Edit rule; dontAsk mode refuses
    everything not allowed, and --permission-prompts none refuses anything that would ask).
    Reads allowed in `workspace` and `readable`.
  - Bash: always sandboxed (no unsandboxed fallback). It can write only the workspace (and its
    own temp dir, which is pointed inside the workspace), cannot read the home directory, this
    repository or the workspace's siblings except `workspace` and `readable`, and reaches the
    network only for `allowed_hosts` plus 127.0.0.1.
  - environment: a short base (HOME, USER, PATH, ...) plus `env`, without the API keys in
    STRIPPED_ENV, with long Bash timeouts.

The agent must leave result.json (a JSON object) in `workspace`. If it does not, the same
session is resumed once with a message saying what is wrong. Raw CLI output (stream-json) and
stderr for each attempt are saved in `workspace/.agent/`.
"""

from __future__ import annotations

import json
import os
import sys
import pathlib
import secrets
import signal
import subprocess
import time

from lab.contracts import Agent, AgentResult

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLAUDE_BIN = "/opt/homebrew/bin/claude"
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "EXA_API_KEY", "PAST_API_KEY")
FROM_HOST = ("USER", "LOGNAME", "LANG", "LC_ALL", "SHELL", "TERM")
# Agents get the same Python the lab itself runs on (it has the scientific packages), then the usual system paths.
DEFAULT_PATH = os.environ.get("LAB_AGENT_PATH") or ":".join(
    [str(pathlib.Path(sys.executable).parent), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
# Lab model name -> CLI model name. Names not listed are passed to the CLI unchanged.
# Fable 5.2 needs CLI 2.1.290 or newer (the bare alias "fable" gave 5.1 on 2.1.285).
DEFAULT_MODELS = {"fable": "claude-fable-5-2", "opus": "claude-opus-5-5", "haiku": "haiku"}
RESULT_NOTE = (
    "When you finish, write result.json in your working directory. It must contain one JSON object. "
    "You may write files only inside your working directory."
)


class ClaudeBackend:
    """contracts.Backend on the Claude Code CLI. Safe to call from several threads at once."""

    def __init__(self, allowed_hosts: list[str], models: dict[str, str] | None = None, *,
                 claude_bin: str = CLAUDE_BIN, deny_read: list[pathlib.Path] = ()):
        self.allowed_hosts = list(allowed_hosts)
        self.models = {**DEFAULT_MODELS, **(models or {})}
        self.claude_bin = claude_bin
        self.deny_read = [pathlib.Path(p) for p in deny_read]

    # ------------------------------------------------------------ what the session is given

    def settings(self, workspace: pathlib.Path, readable=()) -> dict:
        """The --settings JSON: permission rules and sandbox limits for this workspace."""
        ws = pathlib.Path(workspace).resolve()
        rd = [pathlib.Path(p).resolve() for p in readable]
        env_file = ROOT / ".env"
        for p in rd:
            if p == env_file or p in env_file.parents:
                raise ValueError(f"readable path {p} would expose {env_file}")

        def rule(tool: str, p: pathlib.Path) -> str:
            # "//" starts an absolute path in a permission rule
            return f"{tool}(/{p}/**)" if p == ws or p.is_dir() else f"{tool}(/{p})"

        deny_read = []
        for p in [pathlib.Path.home(), ROOT, ws.parent, *self.deny_read]:
            if str(p) != "/" and str(p) not in deny_read:
                deny_read.append(str(p))
        return {
            "permissions": {
                "defaultMode": "dontAsk",
                # Every shell command is allowed to run, because the sandbox below confines what it can touch.
                # Without this, commands the CLI cannot analyse (loops, cd, variables) are refused in dontAsk mode.
                "allow": ["Bash", rule("Read", ws), rule("Edit", ws)] + [rule("Read", p) for p in rd],
                "deny": ["WebFetch", "WebSearch", f"Edit(/{ws}/.claude/**)", f"Read(/{env_file})"]
                        + [rule("Edit", p) for p in rd],
            },
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False,
                "network": {
                    "allowedDomains": self.allowed_hosts + ["127.0.0.1", "localhost"],
                    "allowLocalBinding": True,
                },
                "filesystem": {
                    "denyRead": deny_read,
                    "allowRead": [str(ws)] + [str(p) for p in rd],
                    "denyWrite": [str(ws / ".claude")] + [str(p) for p in rd],
                },
            },
            "disableAllHooks": True,
        }

    def command(self, agent: Agent, workspace: pathlib.Path, readable=(), resume: str | None = None) -> list[str]:
        """The CLI command line. The brief is not on it: run() passes it on stdin, so any text is safe."""
        cmd = [
            self.claude_bin, "-p",
            "--model", self.models.get(agent.model, agent.model),
            "--output-format", "stream-json", "--verbose",
            "--safe-mode", "--strict-mcp-config", "--setting-sources", "",
            "--settings", json.dumps(self.settings(workspace, readable)),
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", TOOLS,
            "--append-system-prompt", RESULT_NOTE,
        ]
        for p in readable:
            if pathlib.Path(p).is_dir():
                cmd += ["--add-dir", str(pathlib.Path(p).resolve())]
        if resume:
            cmd += ["--resume", resume]
        return cmd

    def environment(self, workspace: pathlib.Path, env: dict[str, str] | None = None) -> dict[str, str]:
        """The agent's environment: a short base from the host, then `env`, minus the API keys."""
        out = {k: os.environ[k] for k in FROM_HOST if k in os.environ}
        out["HOME"] = str(pathlib.Path.home())   # the CLI finds its login through HOME
        out["PATH"] = DEFAULT_PATH
        out.update(env or {})
        for key in STRIPPED_ENV:
            out.pop(key, None)
        out.setdefault("BASH_DEFAULT_TIMEOUT_MS", str(30 * 60 * 1000))
        out.setdefault("BASH_MAX_TIMEOUT_MS", str(3 * 60 * 60 * 1000))
        out["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        return out

    # ------------------------------------------------------------------------- running

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800) -> AgentResult:
        start = time.monotonic()
        deadline = start + timeout_s
        ws = pathlib.Path(workspace).resolve()
        agent_dir = ws / ".agent"
        tmp = agent_dir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        old = ws / "result.json"
        if old.exists():   # a result from an earlier run must not count for this one
            old.rename(agent_dir / f"previous-result-{time.time_ns()}.json")

        environment = self.environment(ws, env)
        # Bash's temp dir is made writable by the sandbox. The CLI needs a short path here
        # (Unix sockets), so a short link points it inside the workspace.
        link = pathlib.Path(f"/tmp/lab-{secrets.token_hex(4)}")
        os.symlink(tmp, link)
        environment["CLAUDE_CODE_TMPDIR"] = str(link)
        costs = []
        try:
            first = self._attempt(self.command(agent, ws, readable), brief, ws, environment, agent_dir / "attempt-1", deadline)
            costs.append(first["cost"])
            session = first["session_id"]
            if first["timed_out"]:
                return self._done(False, {}, start, costs, session, f"timed out after {timeout_s} s")
            result, problem = read_result(ws)
            if problem is None:
                return self._done(True, result, start, costs, session, None)
            if not session:
                return self._done(False, {}, start, costs, None, f"{problem}; the CLI gave no session to resume ({first['stderr']})")
            if time.monotonic() >= deadline:
                return self._done(False, {}, start, costs, session, f"{problem}; no time left to repair")
            message = (f"{problem}. Write result.json in your working directory ({ws}) containing a single "
                       "JSON object with your results, then stop.")
            second = self._attempt(self.command(agent, ws, readable, resume=session), message, ws, environment,
                                   agent_dir / "attempt-2", deadline)
            costs.append(second["cost"])
            session = second["session_id"] or session
            if second["timed_out"]:
                return self._done(False, {}, start, costs, session, f"{problem}; repair timed out after {timeout_s} s in all")
            result, problem2 = read_result(ws)
            if problem2 is None:
                return self._done(True, result, start, costs, session, None)
            return self._done(False, {}, start, costs, session, f"{problem2} after one repair attempt")
        finally:
            link.unlink(missing_ok=True)

    def _attempt(self, cmd, prompt: str, ws, env, prefix: pathlib.Path, deadline: float) -> dict:
        """Run the CLI once with `prompt` on stdin, killing its whole process group at the deadline."""
        in_path = prefix.with_suffix(".prompt.txt")
        out_path, err_path = prefix.with_suffix(".jsonl"), prefix.with_suffix(".stderr.txt")
        in_path.write_text(prompt)
        with open(in_path) as inp, open(out_path, "w") as out, open(err_path, "w") as err:
            proc = subprocess.Popen(cmd, cwd=ws, env=env, stdin=inp, stdout=out, stderr=err,
                                    start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=max(1.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_group(proc)
        info = {"timed_out": timed_out, "session_id": None, "cost": None, "returncode": proc.returncode,
                "stderr": err_path.read_text(errors="replace")[-500:]}
        for line in out_path.read_text(errors="replace").splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                info["session_id"] = msg.get("session_id") or info["session_id"]
                if msg.get("type") == "result" and msg.get("total_cost_usd") is not None:
                    info["cost"] = float(msg["total_cost_usd"])
        return info

    @staticmethod
    def _done(ok, result, start, costs, session, error) -> AgentResult:
        known = [c for c in costs if c is not None]
        return AgentResult(ok=ok, result=result, seconds=time.monotonic() - start,
                           cost_usd=sum(known) if known else None, session_id=session, error=error)


def read_result(ws: pathlib.Path) -> tuple[dict, str | None]:
    """Return (result, None) if ws/result.json holds a JSON object, else ({}, what is wrong)."""
    path = ws / "result.json"
    if not path.is_file():
        return {}, "result.json is missing"
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeDecodeError) as e:
        return {}, f"result.json is not valid JSON ({e})"
    if not isinstance(data, dict):
        return {}, f"result.json must contain a JSON object, not {type(data).__name__}"
    return data, None


def kill_group(proc: subprocess.Popen) -> None:
    """Stop a process and everything in its process group: TERM, then KILL after 5 s."""
    for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue
