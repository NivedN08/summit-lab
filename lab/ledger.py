"""The research ledger: an append-only, hash-chained record of batches, failures and moves.

One JSON line per entry in `ledger/ledger.jsonl` at the repository root (LAB_LEDGER overrides the
path; tests pass their own):

    {"seq": 1, "time": "...Z", "kind": "batch.graded", "data": {...}, "prev_sha": "000...", "sha": "..."}

    sha = sha256(prev_sha + canonical JSON of {"seq", "time", "kind", "data"})

The first entry's prev_sha is 64 zeros. Changing, removing or reordering any line breaks the chain
at that line or the next, and verify_chain() names where. (Rewriting the last line and its sha
together cannot be caught by the chain alone; a committed copy of the ledger catches that.)

Appends take the same locks as lab.eventlog.JsonlEventLog: a thread lock, then an exclusive fcntl
lock on the file, so several threads and processes can share one ledger.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pathlib
import threading
from typing import Any

from lab.eventlog import utc_now

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "ledger" / "ledger.jsonl"
KINDS = ("batch.registered", "batch.graded", "failure", "move.proposed", "move.evaluated", "move.accepted",
         "move.rejected", "note")
ZERO_SHA = "0" * 64
FIELDS = ("seq", "time", "kind", "data", "prev_sha", "sha")
_lock = threading.Lock()


def default_path() -> pathlib.Path:
    """The ledger file: $LAB_LEDGER when set, else ledger/ledger.jsonl at the repository root."""
    return pathlib.Path(os.environ.get("LAB_LEDGER") or DEFAULT_PATH)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def entry_sha(prev_sha: str, seq: int, time: str, kind: str, data: Any) -> str:
    body = canonical({"seq": seq, "time": time, "kind": kind, "data": data})
    return hashlib.sha256((prev_sha + body).encode()).hexdigest()


def _last_line(handle) -> bytes:
    """The last non-empty line of an open binary file, read from the end."""
    handle.seek(0, os.SEEK_END)
    pos, buf = handle.tell(), b""
    while pos > 0:
        step = min(4096, pos)
        pos -= step
        handle.seek(pos)
        buf = handle.read(step) + buf
        lines = buf.rstrip(b"\n").split(b"\n")
        if len(lines) > 1 or pos == 0:
            return lines[-1].strip()
    return b""


def append(kind: str, data: dict, path: pathlib.Path | str | None = None) -> dict:
    """Add one entry and return it. `kind` must be one of KINDS; `data` must be JSON-able."""
    if kind not in KINDS:
        raise ValueError(f"unknown ledger kind {kind!r}; known: {', '.join(KINDS)}")
    path = pathlib.Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(canonical(data))      # store exactly what the hash covers
    with _lock, open(path, "a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            last = _last_line(handle)
            prev = json.loads(last) if last else None
            seq = (prev["seq"] + 1) if prev else 1
            prev_sha = prev["sha"] if prev else ZERO_SHA
            time = utc_now()
            entry = {"seq": seq, "time": time, "kind": kind, "data": data, "prev_sha": prev_sha,
                     "sha": entry_sha(prev_sha, seq, time, kind, data)}
            handle.seek(0, os.SEEK_END)
            handle.write((json.dumps(entry, ensure_ascii=False) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return entry


def _read(path: pathlib.Path) -> list[str]:
    if not path.is_file():
        return []
    with open(path, "rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        try:
            raw = handle.read()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return raw.decode().splitlines()


def entries(path: pathlib.Path | str | None = None, kind: str | None = None) -> list[dict]:
    """Every entry in order, or only those of one kind. An empty list when there is no ledger."""
    out = [json.loads(line) for line in _read(pathlib.Path(path or default_path())) if line.strip()]
    return [e for e in out if kind is None or e.get("kind") == kind]


def verify_chain(path: pathlib.Path | str | None = None) -> list[str]:
    """Every break in the chain, one plain line each; empty means the ledger is intact."""
    problems, prev_sha, expected_seq = [], ZERO_SHA, 1
    for number, line in enumerate(_read(pathlib.Path(path or default_path())), 1):
        if not line.strip():
            problems.append(f"line {number}: empty line")
            continue
        try:
            e = json.loads(line)
        except ValueError:
            problems.append(f"line {number}: not JSON")
            continue
        if not isinstance(e, dict) or set(e) != set(FIELDS):
            problems.append(f"line {number}: fields should be {', '.join(FIELDS)}")
            continue
        if e["seq"] != expected_seq:
            problems.append(f"line {number}: seq {e['seq']}, expected {expected_seq}")
        if e["kind"] not in KINDS:
            problems.append(f"line {number}: unknown kind {e['kind']!r}")
        if e["prev_sha"] != prev_sha:
            problems.append(f"line {number}: prev_sha does not match the sha of the line before")
        if e["sha"] != entry_sha(e["prev_sha"], e["seq"], e["time"], e["kind"], e["data"]):
            problems.append(f"line {number}: sha does not match the entry (seq {e['seq']}, {e['kind']})")
        prev_sha = e["sha"]
        expected_seq = (e["seq"] + 1) if isinstance(e["seq"], int) else expected_seq + 1
    return problems


def record_grading(run_dir: pathlib.Path, results: dict, path: pathlib.Path | str | None = None) -> list[dict]:
    """After `grade`: one batch.graded entry with the pass counts, then one failure entry per study
    whose final answer failed (the lab's final answer; the vote over attempts for a study with no lab
    answer), then one per failed answer of a control arm (the same-cost vote, the self-challenge arm's
    final answer, or "no answer" when it has none), with the adapter's outcome kind when it gives one.
    A study missing from `results` entirely is not recorded. Returns the entries written."""
    run_dir = pathlib.Path(run_dir)
    written = [append("batch.graded", {"run_dir": str(run_dir), "benchmark": results.get("benchmark"),
                                       "totals": results.get("totals")}, path)]
    for sid, entry in sorted((results.get("studies") or {}).items()):
        rows = entry.get("candidates") or []
        if "lab" in entry:
            arm, grade = "lab", (entry["lab"].get("grade") or {})
            row = next((r for r in rows if r.get("kind") == "final"), {})
        elif "baseline" in entry:
            arm, grade = "vote", (entry["baseline"].get("vote") or {})
            row = next((r for r in rows if r.get("kind") == "vote"), {})
        else:
            continue
        if not grade.get("passed"):
            data = {"run_dir": str(run_dir), "study": sid, "arm": arm,
                    "outcome_kind": row.get("outcome_kind", grade.get("kind"))}
            if arm == "lab":
                data["status"] = entry["lab"].get("status")
            written.append(append("failure", data, path))
    for sid, entry in sorted((results.get("studies") or {}).items()):
        rows = entry.get("candidates") or []
        for arm, grade, row_id in arm_answers(entry):
            if grade.get("passed"):
                continue
            row = next((r for r in rows if r.get("id") == row_id), {})
            data = {"run_dir": str(run_dir), "study": sid, "arm": arm,
                    "outcome_kind": row.get("outcome_kind", grade.get("kind") or ("no answer" if not grade else None))}
            if arm == "self":
                data["status"] = entry["self"].get("status")
            written.append(append("failure", data, path))
    return written


def arm_answers(entry: dict) -> list[tuple[str, dict, str]]:
    """The control arms' graded answers in one study's results entry: (arm, grade, its candidate row id).
    An arm with no final answer has an empty grade, which counts as a failure."""
    out = []
    if "vote_arm" in entry:
        out.append(("vote-arm", entry["vote_arm"].get("vote") or {}, "vote-arm"))
    if "self" in entry:
        out.append(("self", entry["self"].get("final") or {}, "self-final"))
    return out
