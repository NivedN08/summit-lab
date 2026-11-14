"""Runs one lab agent as a headless Claude Code session inside its own Modal Sandbox.

    backend = ModalBackend()
    result = backend.run(agent, brief, workspace, readable=[data_dir, tools], env={"LAB_GATEWAY_URL": url, ...})

It mirrors lab/backend_claude.py (brief on stdin, result.json, one repair attempt by resuming the
same session, AgentResult), with the sandbox as the boundary instead of the CLI's own sandbox:

  - image: one named Modal Image (IMAGE_NAME:<hash of its inputs>), built once and reused. Debian
    slim, Python 3.12 with the scientific packages, Node and the Claude Code CLI at CLAUDE_CODE_VERSION,
    the two agent clients lab/tools/memory.py and research.py, and the PUBLIC benchmark data only,
    all at the same absolute paths as on the laptop so briefs work unchanged. Nothing sealed, no
    .env and no lab/tools/exa.py.
  - sandbox: 2 CPUs, 4 GB, a timeout a little above the agent's, the Modal Secret `claude-token`
    (CLAUDE_CODE_OAUTH_TOKEN), and outbound TLS only to `allowed_hosts` plus the gateway's host.
  - files: the local workspace is copied in before the run and copied back after it, finished or
    cut off, without caches and huge files (select_files decides both ways).
  - the CLI runs as root with --dangerously-skip-permissions (IS_SANDBOX=1 allows that as root),
    tools Bash, Read, Write, Edit, Glob, Grep only, no MCP servers, no settings files, no hooks,
    telemetry and non-essential traffic off. Raw output streams to workspace/.agent/attempt-N.jsonl.

A benchmark can change the sandbox for its studies with an adapter method `modal_spec(study)`.
`ModalBackend(specs={study_id: spec})` uses the spec of the study a run belongs to (found from
the agent id, which names the study, or else from the workspace path). Every key is optional:

    {"packages": ["scanpy==1.10.4", ...],   # extra packages, installed over the base ones (so a pin
                                            # here wins). Gives its own image, IMAGE_NAME-<set hash>,
                                            # without the GeneBench public data
     "cpu": 4.0, "memory_mb": 16384,        # a hard limit (request = limit)
     "volumes": [{"name": "summit-scbench-data", "sub_path": "/sc01", "mount": "/scbench-data"}],
                                            # always mounted read-only
     "links": {"data": "/scbench-data"},    # name in the working folder -> path in a mount
     "workspace_alias": "/workspace",       # a path made to point at the working folder
     "package_index": False}                # False: no package-index hosts in the allowlist

`python3 -m lab.backend_modal build` builds and publishes the default image ahead of a run.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import pathlib
import shlex
import tarfile
import threading
import time

from lab.backend_claude import DEFAULT_MODELS, RESULT_NOTE, STRIPPED_ENV, TOOLS, read_result
from lab.contracts import Agent, AgentResult

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_NAME = "summit-lab"
IMAGE_NAME = "summit-lab-agent"
CLAUDE_CODE_VERSION = "2.1.311"
NODE_VERSION = "22.11.0"
PY_PACKAGES = ["numpy==1.26.4", "pandas==2.2.2", "scipy==1.13.1", "statsmodels==0.14.2",
               "scikit-learn==1.5.1", "matplotlib==3.8.4"]
PUBLIC_DATA = ROOT / "data" / "genebench" / "public"
TOOL_FILES = [ROOT / "lab" / "tools" / "memory.py", ROOT / "lab" / "tools" / "research.py"]
CLAUDE_SECRET = "claude-token"
# Claude Code needs only Anthropic's API; the Python package index lets an agent install a package.
AGENT_HOSTS = ["api.anthropic.com", "pypi.org", "files.pythonhosted.org"]
PACKAGE_INDEX_HOSTS = ("pypi.org", "files.pythonhosted.org")
CPU, MEMORY_MB = 2.0, 4096
SLACK_S = 300                 # sandbox lifetime beyond the agent's limit, for copy-in, copy-back and cleanup
# Set in the image: no telemetry, error reports, auto-updates or other non-essential traffic.
IMAGE_ENV = {
    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "IS_SANDBOX": "1", "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1",
}
# Never copied in or back: the runner's own logs, CLI settings, caches and environments.
SKIP_DIRS = (".agent", ".claude", "__pycache__", ".cache", ".ipynb_checkpoints", "node_modules", ".git",
             ".venv", "venv", ".mypy_cache", ".pytest_cache")
MAX_FILE_BYTES = 50_000_000
MAX_TOTAL_BYTES = 500_000_000
REMOTE_TMP = "/tmp/lab-agent"


# ----------------------------------------------------------------------------- the image

def image_inputs(packages=()) -> list[pathlib.Path]:
    """Every local file baked into the image. Refuses symlinks and anything outside the public side.
    The default image (no extra packages) also holds the GeneBench public data; a package-set image
    holds the agent tools only, since its benchmark brings its data on a Volume."""
    files = list(TOOL_FILES)
    for path in [] if packages else sorted(PUBLIC_DATA.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"{path} is a symlink; the image takes regular files only")
        if path.is_file():
            files.append(path)
    for path in files:
        if "sealed" in path.relative_to(ROOT).parts or path.name == ".env" or path.name == "exa.py":
            raise ValueError(f"{path} must never go into an agent image")
    return files


def image_name(packages=()) -> str:
    """IMAGE_NAME tagged with a hash of everything that goes in, so a change gives a new image.
    A package set gets its own name, IMAGE_NAME-<hash of the set>."""
    packages = tuple(packages)
    digest = hashlib.sha256(json.dumps([CLAUDE_CODE_VERSION, NODE_VERSION, PY_PACKAGES, IMAGE_ENV]).encode())
    name = IMAGE_NAME
    if packages:
        digest.update(json.dumps(list(packages)).encode())
        name += "-" + hashlib.sha256(json.dumps(sorted(packages)).encode()).hexdigest()[:8]
    for path in image_inputs(packages):
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return f"{name}:{digest.hexdigest()[:12]}"


def image_definition(packages=()):
    """The agent image as a Modal Image definition (not built). Extra packages are one more
    install layer on top of the base one, so the layers below it are shared with the default image."""
    import modal
    node = f"node-v{NODE_VERSION}-linux-x64"
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("ca-certificates", "curl", "xz-utils", "procps")
        .run_commands(
            f"curl -fsSL https://nodejs.org/dist/v{NODE_VERSION}/{node}.tar.xz | tar -xJ -C /usr/local --strip-components=1",
            f"npm install -g @anthropic-ai/claude-code@{CLAUDE_CODE_VERSION}",
            "claude --version",
        )
        .uv_pip_install(*PY_PACKAGES)
    )
    if packages:
        image = image.uv_pip_install(*packages)
    image = image.env(IMAGE_ENV)
    for path in TOOL_FILES:
        image = image.add_local_file(path, str(path), copy=True)
    image_inputs(packages)   # the checks above, before the public folder is added
    if packages:
        return image
    return image.add_local_dir(PUBLIC_DATA, str(PUBLIC_DATA), copy=True)


_lock = threading.Lock()
_cache: dict = {}


def modal_app():
    """The Modal App every lab sandbox belongs to (looked up once per process)."""
    import modal
    with _lock:
        if "app" not in _cache:
            _cache["app"] = modal.App.lookup(APP_NAME, create_if_missing=True)
        return _cache["app"]


def agent_image(build: bool = True, packages=()):
    """The published agent image for this package set (default: none extra); built and published
    first if it does not exist yet."""
    import modal
    packages = tuple(packages)
    name = image_name(packages)
    with _lock:
        if name in _cache:
            return _cache[name]
        app = _cache.get("app") or modal.App.lookup(APP_NAME, create_if_missing=True)
        try:
            image = modal.Image.from_name(name).build(app=app)   # only looks it up; raises if missing
        except modal.exception.NotFoundError:
            if not build:
                raise
            image = image_definition(packages)
            with modal.enable_output():
                image.build(app=app)
            image.publish(name)
        _cache[name] = image
        return image


# ------------------------------------------------------------------- workspace files

def select_files(root, skip_dirs, max_file_bytes, max_total_bytes):
    """Files to copy under `root`, as (kept relative paths, skipped [(path, reason)]).
    Regular files only (no symlinks), no folder named in `skip_dirs` at any depth, nothing above
    `max_file_bytes`, and nothing more once `max_total_bytes` is reached. This function is also
    sent into the sandbox for copy-back, so it uses only the standard library."""
    import os
    kept, skipped, total = [], [], 0
    for top, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not os.path.islink(os.path.join(top, d)))
        for name in sorted(files):
            path = os.path.join(top, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path) or not os.path.isfile(path):
                skipped.append((rel, "not a regular file"))
                continue
            size = os.path.getsize(path)
            if size > max_file_bytes:
                skipped.append((rel, f"{size} bytes is over the per-file limit"))
            elif total + size > max_total_bytes:
                skipped.append((rel, "over the total limit"))
            else:
                kept.append(rel)
                total += size
    return kept, skipped


PACK_MAIN = """
import json, sys, tarfile
root, out, limits = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
kept, skipped = select_files(root, tuple(limits["skip"]), limits["max_file"], limits["max_total"])
with tarfile.open(out, "w:gz") as tar:
    for rel in kept:
        tar.add(root + "/" + rel, arcname=rel, recursive=False)
print(json.dumps({"kept": len(kept), "skipped": skipped}))
"""

# Kills every process whose working directory is inside the workspace (the CLI and anything it left running).
STOP_SCRIPT = """
import os, signal, sys
ws, me, n = os.path.realpath(sys.argv[1]), os.getpid(), 0
for pid in os.listdir("/proc"):
    if not pid.isdigit() or int(pid) in (me, 1):
        continue
    try:
        cwd = os.path.realpath(os.readlink(f"/proc/{pid}/cwd"))
    except OSError:
        continue
    if cwd == ws or cwd.startswith(ws + "/"):
        try:
            os.kill(int(pid), signal.SIGKILL)
            n += 1
        except OSError:
            pass
print(n)
"""


def pack_script() -> str:
    return inspect.getsource(select_files) + PACK_MAIN


def limits() -> dict:
    return {"skip": list(SKIP_DIRS), "max_file": MAX_FILE_BYTES, "max_total": MAX_TOTAL_BYTES}


def pack(root: pathlib.Path, files: list[str]) -> bytes:
    """A gzipped tar of `files` (paths relative to `root`)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in files:
            tar.add(root / rel, arcname=rel, recursive=False)
    return buf.getvalue()


def unpack(data: bytes, root: pathlib.Path) -> list[str]:
    """Extract regular files from a tar made in the sandbox into `root`. Anything that is not a
    plain relative path inside `root`, any link, and anything under SKIP_DIRS is dropped."""
    root = pathlib.Path(root)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = []
        for m in tar.getmembers():
            parts = pathlib.PurePosixPath(m.name).parts
            if m.isreg() and parts and not m.name.startswith("/") and ".." not in parts \
                    and not any(p in SKIP_DIRS for p in parts):
                members.append(m)
        tar.extractall(root, members=members, filter="data")
    return [m.name for m in members]


# ----------------------------------------------------------------------------- the backend

def gateway_host(url: str | None) -> str | None:
    """The host name in a gateway URL, or None."""
    if not url:
        return None
    from urllib.parse import urlparse
    return urlparse(url).hostname


def link_script(ws: pathlib.Path, spec: dict) -> str | None:
    """Shell commands that make a spec's links in the working folder and its workspace alias.
    None when the spec has neither. Link names must be plain names inside the working folder."""
    lines = []
    for name, target in (spec.get("links") or {}).items():
        if not name or "/" in name or name in (".", ".."):
            raise ValueError(f"link name {name!r} must be a plain name in the working folder")
        lines.append(f"ln -sfn {shlex.quote(str(target))} {shlex.quote(str(ws / name))}")
    alias = spec.get("workspace_alias")
    if alias:
        a, w = shlex.quote(str(alias)), shlex.quote(str(ws))
        lines += [f"if [ -d {a} ] && [ ! -L {a} ]; then rmdir {a}; fi",
                  f"ln -sfn {w} {a}", f'test "$(readlink {a})" = {w}']
    return "set -e\n" + "\n".join(lines) if lines else None


class ModalBackend:
    """contracts.Backend on Modal Sandboxes. Safe to call from many threads at once.
    `specs` maps a study id to its sandbox spec (see the module docstring)."""

    def __init__(self, allowed_hosts: list[str] | None = None, models: dict[str, str] | None = None, *,
                 cpu: float = CPU, memory_mb: int = MEMORY_MB, secret_name: str = CLAUDE_SECRET,
                 specs: dict[str, dict] | None = None):
        self.allowed_hosts = list(AGENT_HOSTS if allowed_hosts is None else allowed_hosts)
        self.models = {**DEFAULT_MODELS, **(models or {})}
        self.cpu, self.memory_mb, self.secret_name = cpu, memory_mb, secret_name
        self.specs = dict(specs or {})
        self._live: dict[str, object] = {}     # sandbox id -> sandbox, while a run is in progress
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------ what the sandbox is given

    def spec_for(self, agent: Agent, workspace: pathlib.Path) -> dict:
        """The spec of the study this run belongs to, or {} (the defaults). Agent ids name their
        study ("sc01-team-a", "baseline-sc01-2"); the director's does not, so for it the workspace
        path (<run dir>/<study>/director-...) decides."""
        padded = f"-{agent.id}-"
        for study_id, spec in self.specs.items():
            if f"-{study_id}-" in padded:
                return spec
        parts = pathlib.Path(workspace).parts
        for study_id, spec in self.specs.items():
            if study_id in parts:
                return spec
        return {}

    def hosts(self, env: dict | None, spec: dict | None = None) -> list[str]:
        """Outbound TLS allowlist: the fixed hosts plus the gateway named in the agent's env,
        without the package index when the spec forbids it."""
        out = list(self.allowed_hosts)
        if (spec or {}).get("package_index") is False:
            out = [h for h in out if h not in PACKAGE_INDEX_HOSTS]
        host = gateway_host((env or {}).get("LAB_GATEWAY_URL"))
        if host and host not in out:
            out.append(host)
        return out

    def sandbox_options(self, timeout_s: int, env: dict | None = None, spec: dict | None = None) -> dict:
        """Sandbox.create settings other than app, image, volume and secret objects.
        A spec's CPUs and memory are a hard limit: Modal's request and limit are both set to them."""
        spec = spec or {}
        cpu = (float(spec["cpu"]),) * 2 if "cpu" in spec else self.cpu
        memory = (int(spec["memory_mb"]),) * 2 if "memory_mb" in spec else self.memory_mb
        return {"cpu": cpu, "memory": memory, "timeout": int(timeout_s) + SLACK_S,
                "secret_names": [self.secret_name] if self.secret_name else [],   # None only in tests
                "outbound_domain_allowlist": self.hosts(env, spec)}

    @staticmethod
    def volumes(spec: dict | None) -> dict:
        """The spec's Volumes as Sandbox.create's `volumes`, every one read-only."""
        import modal
        out = {}
        for v in (spec or {}).get("volumes") or []:
            out[v["mount"]] = modal.Volume.from_name(v["name"]).with_mount_options(
                read_only=True, sub_path=v.get("sub_path"))
        return out

    @staticmethod
    def spec_paths(spec: dict | None) -> list[pathlib.Path]:
        """Paths an agent may be told to read because the spec puts them in its sandbox."""
        spec = spec or {}
        paths = [pathlib.Path(v["mount"]) for v in spec.get("volumes") or []]
        if spec.get("workspace_alias"):
            paths.append(pathlib.Path(spec["workspace_alias"]))
        return paths

    def command(self, agent: Agent, workspace: pathlib.Path, readable=(), resume: str | None = None) -> list[str]:
        """The CLI command line inside the sandbox. The brief goes on stdin."""
        settings = {"permissions": {"deny": ["WebFetch", "WebSearch"]}, "disableAllHooks": True}
        cmd = ["claude", "-p", "--model", self.models.get(agent.model, agent.model),
               "--output-format", "stream-json", "--verbose",
               "--safe-mode", "--strict-mcp-config", "--setting-sources", "",
               "--settings", json.dumps(settings), "--dangerously-skip-permissions",
               "--tools", TOOLS, "--disallowedTools", "WebFetch,WebSearch",
               "--append-system-prompt", RESULT_NOTE]
        for p in readable:
            cmd += ["--add-dir", str(p)]
        if resume:
            cmd += ["--resume", resume]
        return cmd

    def environment(self, env: dict | None = None) -> dict[str, str]:
        """The CLI's environment inside the sandbox (the image adds IMAGE_ENV, the secret the login)."""
        out = {"HOME": "/root", "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8",
               "BASH_DEFAULT_TIMEOUT_MS": str(30 * 60 * 1000), "BASH_MAX_TIMEOUT_MS": str(3 * 60 * 60 * 1000)}
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            out[var] = "2"
        out.update(env or {})
        for key in STRIPPED_ENV:
            out.pop(key, None)
        return out

    @staticmethod
    def check_readable(readable, workspace: pathlib.Path, extra=()) -> list[pathlib.Path]:
        """Readable paths must already be in the image (public data, agent tools), in the workspace,
        or under `extra` (a spec's mounts and workspace alias). Paths are compared resolved and
        returned as given (made absolute), since the image holds them at their unresolved path."""
        out = []
        workspace = pathlib.Path(workspace).resolve()
        allowed = [pathlib.Path(a).resolve() for a in (PUBLIC_DATA, *TOOL_FILES, TOOL_FILES[0].parent, *extra)]
        for given in readable:
            p = pathlib.Path(given).resolve()
            inside = any(p == a or a in p.parents for a in allowed) or p == workspace or workspace in p.parents
            if not inside or "sealed" in p.parts:
                raise ValueError(f"readable path {p} is not in the agent image or the workspace")
            out.append(pathlib.Path(os.path.abspath(given)))
        return out

    # ------------------------------------------------------------------------- running

    def create_sandbox(self, timeout_s: int, env: dict | None, spec: dict | None = None):
        """Start an idle sandbox with the agent settings (and a spec's image, resources and
        read-only Volumes) and remember it until the run ends."""
        import modal
        with self._lock:
            if self._closed:
                raise RuntimeError("the Modal backend has been shut down")
        opts = self.sandbox_options(timeout_s, env, spec)
        sb = modal.Sandbox.create(
            "sleep", "infinity", app=modal_app(), image=agent_image(packages=(spec or {}).get("packages") or ()),
            secrets=[modal.Secret.from_name(n) for n in opts.pop("secret_names")],
            volumes=self.volumes(spec), tags={"summit-lab": "agent"}, **opts)
        with self._lock:
            self._live[sb.object_id] = sb
        return sb

    def make_links(self, sb, ws: pathlib.Path, spec: dict | None) -> None:
        """Make the spec's links and workspace alias inside the sandbox (after copy-in). Links are
        never copied back: copy-back takes regular files only."""
        script = link_script(ws, spec or {})
        if script:
            _check(sb.exec("bash", "-c", script, timeout=60), "link the data into the working folder")

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800) -> AgentResult:
        start = time.monotonic()
        deadline = start + timeout_s
        ws = pathlib.Path(workspace).resolve()
        agent_dir = ws / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        spec = self.spec_for(agent, ws)
        readable = self.check_readable(readable, ws, self.spec_paths(spec))
        old = ws / "result.json"
        if old.exists():   # a result from an earlier run must not count for this one
            old.rename(agent_dir / f"previous-result-{time.time_ns()}.json")
        environment = self.environment(env)
        costs: list = []
        sb = self.create_sandbox(timeout_s, env, spec)
        try:
            self.copy_in(sb, ws)
            self.make_links(sb, ws, spec)
            first = self._attempt(sb, self.command(agent, ws, readable), brief, ws, environment,
                                  agent_dir / "attempt-1", deadline)
            costs.append(first["cost"])
            session = first["session_id"]
            self.finish_files(sb, ws, stop=first["timed_out"])
            if first["timed_out"]:
                return self._done(False, {}, start, costs, session, f"timed out after {timeout_s} s")
            if first.get("limit"):
                return self._done(False, {}, start, costs, session, f"usage limit: {first['limit']}")
            result, problem = read_result(ws)
            if problem is None:
                return self._done(True, result, start, costs, session, None)
            if not session:
                return self._done(False, {}, start, costs, None, f"{problem}; the CLI gave no session to resume ({first['stderr']})")
            if time.monotonic() >= deadline:
                return self._done(False, {}, start, costs, session, f"{problem}; no time left to repair")
            message = (f"{problem}. Write result.json in your working directory ({ws}) containing a single "
                       "JSON object with your results, then stop.")
            second = self._attempt(sb, self.command(agent, ws, readable, resume=session), message, ws, environment,
                                   agent_dir / "attempt-2", deadline)
            costs.append(second["cost"])
            session = second["session_id"] or session
            self.finish_files(sb, ws, stop=True)
            if second["timed_out"]:
                return self._done(False, {}, start, costs, session, f"{problem}; repair timed out after {timeout_s} s in all")
            result, problem2 = read_result(ws)
            if problem2 is None:
                return self._done(True, result, start, costs, session, None)
            return self._done(False, {}, start, costs, session, f"{problem2} after one repair attempt")
        finally:
            self._terminate(sb)

    def copy_in(self, sb, ws: pathlib.Path) -> int:
        """Put the local workspace's files into the sandbox at the same absolute path."""
        files, skipped = select_files(str(ws), SKIP_DIRS, MAX_FILE_BYTES, MAX_TOTAL_BYTES)
        sb.filesystem.make_directory(str(ws))
        sb.filesystem.make_directory(REMOTE_TMP)
        if files:
            archive = f"{REMOTE_TMP}/in.tar.gz"
            sb.filesystem.write_bytes(pack(ws, files), archive)
            _check(sb.exec("tar", "-xzf", archive, "-C", str(ws)), "unpack the workspace")
            sb.filesystem.remove(archive)
        (ws / ".agent" / "copy-in.json").write_text(json.dumps({"files": len(files), "skipped": skipped}, indent=2))
        return len(files)

    def copy_back(self, sb, ws: pathlib.Path) -> list[str]:
        """Copy the sandbox's workspace files back into the local workspace."""
        archive = f"{REMOTE_TMP}/out.tar.gz"
        p = sb.exec("python3", "-c", pack_script(), str(ws), archive, json.dumps(limits()), timeout=600)
        summary = _check(p, "pack the workspace")
        written = unpack(sb.filesystem.read_bytes(archive), ws)
        try:
            info = json.loads(summary.strip().splitlines()[-1])
        except (ValueError, IndexError):
            info = {}
        (ws / ".agent" / "copy-back.json").write_text(
            json.dumps({"files": len(written), "skipped": info.get("skipped", [])}, indent=2))
        return written

    def finish_files(self, sb, ws: pathlib.Path, stop: bool) -> None:
        """Optionally stop everything still running in the workspace, then copy the files back."""
        if stop:
            sb.exec("python3", "-c", STOP_SCRIPT, str(ws), timeout=60).wait()
        self.copy_back(sb, ws)

    def _attempt(self, sb, cmd, prompt: str, ws, env, prefix: pathlib.Path, deadline: float) -> dict:
        """Run the CLI once with `prompt` on stdin, streaming its output to `prefix`.jsonl.
        `timeout` inside the sandbox stops it at the deadline; a local watchdog backs that up."""
        prompt_local = prefix.with_suffix(".prompt.txt")
        out_path, err_path = prefix.with_suffix(".jsonl"), prefix.with_suffix(".stderr.txt")
        prompt_local.write_text(prompt)
        prompt_remote = f"{REMOTE_TMP}/{prefix.name}.prompt.txt"
        sb.filesystem.write_text(prompt, prompt_remote)
        limit = max(1, int(deadline - time.monotonic()))
        script = f"exec timeout -k 15 -s TERM {limit} {shlex.join(cmd)} < {shlex.quote(prompt_remote)}"
        t0 = time.monotonic()
        proc = sb.exec("bash", "-c", script, workdir=str(ws), env=env, timeout=limit + 120)
        err_lines: list[str] = []

        def pump_out():
            with open(out_path, "w") as out:
                for line in proc.stdout:
                    out.write(line)
                    out.flush()

        def pump_err():
            for line in proc.stderr:
                err_lines.append(line)

        threads = [threading.Thread(target=pump_out, daemon=True), threading.Thread(target=pump_err, daemon=True)]
        for t in threads:
            t.start()
        threads[0].join(limit + 90)
        watchdog = threads[0].is_alive()
        if watchdog:       # the in-sandbox limit did not stop it: kill the CLI from outside
            sb.exec("python3", "-c", STOP_SCRIPT, str(ws), timeout=60).wait()
            threads[0].join(60)
        threads[1].join(10)
        try:
            returncode = proc.wait()
        except Exception:
            returncode = None
        err_path.write_text("".join(err_lines))
        elapsed = time.monotonic() - t0
        timed_out = watchdog or (returncode in (124, 137) and elapsed >= limit - 2)
        info = {"timed_out": timed_out, "session_id": None, "cost": None, "returncode": returncode,
                "stderr": "".join(err_lines)[-500:]}
        for line in out_path.read_text(errors="replace").splitlines() if out_path.exists() else []:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                info["session_id"] = msg.get("session_id") or info["session_id"]
                if msg.get("type") == "result" and msg.get("total_cost_usd") is not None:
                    info["cost"] = float(msg["total_cost_usd"])
                # The account's usage limit comes back as an error result, not as a crash.
                if msg.get("type") == "result" and msg.get("is_error") and "limit" in str(msg.get("result", "")).lower():
                    info["limit"] = str(msg.get("result"))[:200]
        return info

    @staticmethod
    def _done(ok, result, start, costs, session, error) -> AgentResult:
        known = [c for c in costs if c is not None]
        return AgentResult(ok=ok, result=result, seconds=time.monotonic() - start,
                           cost_usd=sum(known) if known else None, session_id=session, error=error)

    def _terminate(self, sb) -> None:
        with self._lock:
            self._live.pop(sb.object_id, None)
        try:
            sb.terminate()
        except Exception:
            pass

    def live(self) -> list[str]:
        """Ids of the sandboxes this backend is running right now."""
        with self._lock:
            return list(self._live)

    def shutdown(self) -> int:
        """Refuse new runs and terminate every sandbox still alive. Returns how many were terminated."""
        with self._lock:
            self._closed = True
            alive = list(self._live.values())
            self._live.clear()
        for sb in alive:
            try:
                sb.terminate()
            except Exception:
                pass
        return len(alive)


def _check(proc, what: str) -> str:
    """Wait for a sandbox command; return its stdout, or raise with its stderr."""
    out = proc.stdout.read()
    err = proc.stderr.read()
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"could not {what} in the sandbox (exit {code}): {err[-500:]}")
    return out


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["build"]:
        modal_app()
        print(f"image ready: {image_name()} ({agent_image().object_id})")
    else:
        sys.exit("usage: python3 -m lab.backend_modal build")
