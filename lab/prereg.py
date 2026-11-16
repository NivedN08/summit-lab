"""Pre-registration: freeze what a batch of runs will be before it runs, then check each run against it.

A spec is JSON in `prereg/<batch>.json` with these fields:

  batch             the batch's name; the file must be called <batch>.json
  benchmark         the benchmark name, as given to --benchmark
  studies           the study ids every run of the batch uses
  arms              what is compared (free form, for example ["lab", "fable alone"])
  config            lab Config fields and the values the lab run must use, for example {"worker_model": "opus"}
  seeds             free form
  max_usd           the batch's total dollar budget (each run sets its own --max-usd)
  primary_endpoint  the one number the batch is judged on
  stop_rules        when to stop
  notes             anything else
  status            optional: "draft" while the design may still change (freeze refuses a draft),
                    or "final"

`freeze(spec)` writes `prereg/<batch>.lock`: one sha256 over the spec's canonical JSON, the git
HEAD and the sha256 of every `lab/prompts/*.md` and `lab/**/*.py`, plus each of those parts.

`check(spec, benchmark=..., studies=..., config=...)` recomputes everything and refuses, with one
line per difference, when the spec changed, a prompt or lab file changed, the benchmark or
studies differ from the spec, or a Config field the spec names has another value.

Git HEAD: committing the lock itself moves HEAD, so HEAD may move after freezing as long as the
frozen commit is an ancestor of HEAD and no commit since touched anything under `lab/`. Uncommitted
edits are caught by the file hashes either way.

`verify_lock(spec)` checks only that a lock still belongs to its spec (the spec is unchanged and
the lock's hash matches its own parts). verify_results.py uses it on finished batches, whose code
has moved on since.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIELDS = ("batch", "benchmark", "studies", "arms", "config", "seeds", "max_usd", "primary_endpoint",
          "stop_rules", "notes")
OPTIONAL_FIELDS = ("status",)
STATUSES = ("draft", "final")
BATCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
NOT_A_CHECKOUT = None   # what git_head() returns when the root is not the top of a git checkout


class PreregError(Exception):
    """A spec, its lock or a run does not match. `problems` holds one plain line per difference."""

    def __init__(self, spec_path: pathlib.Path | str, problems: list[str]):
        self.spec_path = pathlib.Path(spec_path)
        self.problems = list(problems)
        super().__init__(f"pre-registration check failed for {self.spec_path}:\n"
                         + "\n".join(f"  - {p}" for p in self.problems))


# ----- hashing -----

def canonical(obj: Any) -> str:
    """The canonical JSON of an object: sorted keys, no spaces."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_files(root: pathlib.Path | None = None) -> dict[str, str]:
    """Path (relative to root) -> sha256, for every lab/prompts/*.md and lab/**/*.py."""
    root = pathlib.Path(root or ROOT)
    paths = set((root / "lab" / "prompts").glob("*.md")) | set((root / "lab").rglob("*.py"))
    return {p.relative_to(root).as_posix(): sha256_bytes(p.read_bytes()) for p in sorted(paths) if p.is_file()}


def git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)


def git_head(root: pathlib.Path | None = None) -> str | None:
    """The commit checked out at root, or None when root is not the top of a git checkout."""
    root = pathlib.Path(root or ROOT).resolve()
    try:
        top = git(root, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or pathlib.Path(top.stdout.strip()).resolve() != root:
            return NOT_A_CHECKOUT
        head = git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return NOT_A_CHECKOUT
    return head.stdout.strip() if head.returncode == 0 else NOT_A_CHECKOUT


def combined_hash(spec_sha: str, head: str | None, files: dict[str, str]) -> str:
    """The one hash a lock stands for: the spec, the commit and every file hash together."""
    return sha256_bytes(canonical({"spec_sha256": spec_sha, "git_head": head, "files": files}).encode())


# ----- the spec and its lock -----

def config_fields() -> set[str]:
    from lab.protocol import Config     # imported here: lab.protocol is large and the spec check needs only names
    return {f.name for f in dataclasses.fields(Config)}


def load_spec(spec_path: pathlib.Path | str) -> dict:
    """Read and check a spec. Raises PreregError listing every problem."""
    spec_path = pathlib.Path(spec_path)
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, ValueError) as err:
        raise PreregError(spec_path, [f"cannot read the spec: {err}"])
    if not isinstance(spec, dict):
        raise PreregError(spec_path, ["the spec must be a JSON object"])
    problems = [f'missing field "{f}"' for f in FIELDS if f not in spec]
    problems += [f'unknown field "{f}"' for f in spec if f not in FIELDS + OPTIONAL_FIELDS]
    if "status" in spec and spec["status"] not in STATUSES:
        problems.append(f'"status" must be one of {", ".join(STATUSES)}')
    batch = spec.get("batch")
    if not isinstance(batch, str) or not BATCH_NAME.match(batch):
        problems.append('"batch" must be a name of letters, digits, dots, dashes and underscores')
    elif spec_path.stem != batch:
        problems.append(f'the spec file must be called {batch}.json (prereg/<batch>.json)')
    if not isinstance(spec.get("benchmark"), str) or not spec.get("benchmark"):
        problems.append('"benchmark" must be a benchmark name')
    studies = spec.get("studies")
    if not isinstance(studies, list) or not studies or not all(isinstance(s, str) and s for s in studies):
        problems.append('"studies" must be a non-empty list of study ids')
    config = spec.get("config")
    if not isinstance(config, dict):
        problems.append('"config" must be an object of Config fields')
    else:
        known = config_fields()
        problems += [f'"config" names "{k}", which is not a Config field' for k in config if k not in known]
    if spec.get("max_usd") is not None and not isinstance(spec.get("max_usd"), (int, float)):
        problems.append('"max_usd" must be a number or null')
    if problems:
        raise PreregError(spec_path, problems)
    return spec


def lock_path(spec_path: pathlib.Path | str, spec: dict | None = None) -> pathlib.Path:
    spec_path = pathlib.Path(spec_path)
    batch = (spec or {}).get("batch") or spec_path.stem
    return spec_path.with_name(f"{batch}.lock")


def spec_hash(spec: dict) -> str:
    return sha256_bytes(canonical(spec).encode())


def freeze(spec_path: pathlib.Path | str, root: pathlib.Path | None = None) -> str:
    """Write prereg/<batch>.lock for a spec and return its sha256. Freezing again with nothing
    changed returns the same hash; a lock that no longer matches is never overwritten."""
    spec_path = pathlib.Path(spec_path)
    spec = load_spec(spec_path)
    if spec.get("status") == "draft":
        raise PreregError(spec_path, ['the spec is a draft ("status": "draft"); set it to "final" when the design '
                                      "and the code are settled, then freeze"])
    root = pathlib.Path(root or ROOT)
    files, head, s_hash = code_files(root), git_head(root), spec_hash(spec)
    sha = combined_hash(s_hash, head, files)
    path = lock_path(spec_path, spec)
    if path.exists():
        try:
            old = json.loads(path.read_text())
        except ValueError:
            old = {}
        if old.get("sha256") == sha:
            return sha
        raise PreregError(spec_path, [f"{path.name} already exists and does not match the spec, the commit or "
                                      "the lab's files as they are now; a registration is never overwritten. "
                                      "Register a new batch, or remove the lock by hand if it was never used"])
    lock = {"batch": spec["batch"], "sha256": sha, "spec_sha256": s_hash, "git_head": head,
            "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "files": files}
    path.write_text(json.dumps(lock, indent=2) + "\n")
    return sha


def read_lock(spec_path: pathlib.Path, spec: dict) -> tuple[dict | None, list[str]]:
    path = lock_path(spec_path, spec)
    if not path.is_file():
        return None, [f"no lock {path.name}: freeze the spec first (python3 -m lab.run prereg freeze {spec_path})"]
    try:
        lock = json.loads(path.read_text())
    except ValueError as err:
        return None, [f"{path.name} is not valid JSON: {err}"]
    if not isinstance(lock, dict) or not isinstance(lock.get("files"), dict):
        return None, [f"{path.name} is not a lock written by freeze"]
    return lock, []


def verify_lock(spec_path: pathlib.Path | str) -> list[str]:
    """Problems with a lock as a record of its spec: the spec changed since freezing, or the lock's
    hash does not match its own parts. Says nothing about the code as it is now."""
    spec_path = pathlib.Path(spec_path)
    try:
        spec = load_spec(spec_path)
    except PreregError as err:
        return err.problems
    lock, problems = read_lock(spec_path, spec)
    if lock is None:
        return problems
    if lock.get("batch") != spec["batch"]:
        problems.append(f'the lock is for batch {lock.get("batch")!r}, the spec for {spec["batch"]!r}')
    if lock.get("spec_sha256") != spec_hash(spec):
        problems.append("the spec changed since it was frozen")
    if lock.get("sha256") != combined_hash(lock.get("spec_sha256"), lock.get("git_head"), lock["files"]):
        problems.append("the lock's sha256 does not match its own parts: the lock was edited")
    return problems


def head_problems(root: pathlib.Path, frozen: str | None, now: str | None) -> list[str]:
    """Problems with the commit: HEAD may only have moved by commits that touch nothing under lab/."""
    if frozen == now:
        return []
    if frozen is None or now is None:
        return [f"git HEAD was {frozen or 'not a git checkout'} when frozen and is {now or 'not a git checkout'} now"]
    if git(root, "merge-base", "--is-ancestor", frozen, now).returncode != 0:
        return [f"git HEAD {now[:12]} does not descend from the frozen commit {frozen[:12]}"]
    changed = git(root, "diff", "--name-only", frozen, now, "--", "lab").stdout.split()
    return [f"{path}: changed in a commit since freezing ({frozen[:12]}..{now[:12]})" for path in changed]


def _plain(value: Any) -> Any:
    """A value as it reads back from JSON (tuples become lists), for comparing a Config with a spec."""
    return json.loads(json.dumps(value, default=str))


def check(spec_path: pathlib.Path | str, *, benchmark: str | None = None, studies: list | None = None,
          config: Any = None, root: pathlib.Path | None = None) -> str:
    """Refuse (PreregError) unless the spec, its lock, the lab's files and the run all match.
    benchmark, studies and config are the run's; None skips that comparison. config is a Config
    or a dict of its fields. Returns the lock's sha256."""
    spec_path = pathlib.Path(spec_path)
    root = pathlib.Path(root or ROOT)
    spec = load_spec(spec_path)
    lock, problems = read_lock(spec_path, spec)
    if lock is None:
        raise PreregError(spec_path, problems)
    problems = verify_lock(spec_path)
    files = code_files(root)
    frozen = lock["files"]
    for path in sorted(set(frozen) | set(files)):
        if path not in files:
            problems.append(f"{path}: removed since freezing")
        elif path not in frozen:
            problems.append(f"{path}: added since freezing")
        elif files[path] != frozen[path]:
            problems.append(f"{path}: changed since freezing (sha256 {frozen[path][:12]} -> {files[path][:12]})")
    problems += head_problems(root, lock.get("git_head"), git_head(root))
    if benchmark is not None and benchmark != spec["benchmark"]:
        problems.append(f"benchmark: registered {spec['benchmark']!r}, this run {benchmark!r}")
    if studies is not None:
        run_ids, registered = [str(s) for s in studies], [str(s) for s in spec["studies"]]
        missing = [s for s in registered if s not in run_ids]
        extra = [s for s in run_ids if s not in registered]
        if missing or extra:
            problems.append("studies differ from the registration: "
                            + "; ".join(x for x in (f"not in this run: {', '.join(missing)}" if missing else "",
                                                    f"not registered: {', '.join(extra)}" if extra else "") if x))
    if config is not None:
        values = dataclasses.asdict(config) if dataclasses.is_dataclass(config) else dict(config)
        for key, wanted in sorted(spec["config"].items()):
            got = values.get(key, "<not set>")
            if _plain(got) != _plain(wanted):
                problems.append(f"config {key}: registered {_plain(wanted)!r}, this run {_plain(got)!r}")
    if problems:
        raise PreregError(spec_path, problems)
    return lock["sha256"]
