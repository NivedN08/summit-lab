"""Append-only event log stored as JSON lines. Implements contracts.EventLog.

Each event is one line: {"seq": 1, "time": "...Z", "kind": ..., "actor": ..., **data}.
Appends take an exclusive file lock, so several threads and several processes can share
one file and `seq` stays unique and increasing.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import pathlib
import threading
from typing import Any

RESERVED = ("seq", "time", "kind", "actor")


def utc_now() -> str:
    """Current UTC time in ISO 8601 with milliseconds and a trailing Z."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _last_seq(handle) -> int:
    """Read the seq of the last line of an open binary file without reading the whole file."""
    handle.seek(0, os.SEEK_END)
    pos = handle.tell()
    buf = b""
    while pos > 0:
        step = min(4096, pos)
        pos -= step
        handle.seek(pos)
        buf = handle.read(step) + buf
        lines = buf.rstrip(b"\n").split(b"\n")
        if len(lines) > 1 or pos == 0:
            last = lines[-1].strip()
            return json.loads(last)["seq"] if last else 0
    return 0


class JsonlEventLog:
    """Append-only record of everything that happens, in one JSON-lines file."""

    def __init__(self, path: str | os.PathLike):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.Lock()

    def append(self, kind: str, actor: str, **data: Any) -> dict:
        """Add one event and return it, with `seq` and `time` filled in."""
        clash = [k for k in RESERVED if k in data]
        if clash:
            raise ValueError(f"event data may not set {clash}")
        with self._lock, open(self.path, "a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                event = {"seq": _last_seq(handle) + 1, "time": utc_now(), "kind": kind, "actor": actor, **data}
                handle.write((json.dumps(event, default=str) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        return event

    def events(self, kind: str | None = None) -> list[dict]:
        """All events in order, or only those of one kind. Reads the file fresh each time."""
        with open(self.path, "rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            try:
                raw = handle.read()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        out = []
        for line in raw.decode().splitlines():
            if line.strip():
                event = json.loads(line)
                if kind is None or event["kind"] == kind:
                    out.append(event)
        return out
