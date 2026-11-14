"""Offline memory for tests and for running without network. Implements contracts.Memory.

Facts live in one JSON-lines file. Recall is simple keyword matching. The read rule is the
same as contracts.Org.can_read: a worker reads its own department, heads and the director
read everything. Every write, pass-down and recall is appended to the event log.

Refusals raise PermissionError (unknown agent, a worker trying to pass down) or
ValueError (bad input, unknown department).
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import re
import threading
import uuid

from lab.contracts import Agent, EventLog, Fact, Org
from lab.eventlog import utc_now

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "has", "have",
    "how", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "what",
    "when", "where", "which", "who", "why", "with",
}


def words(text: str) -> set[str]:
    """Lowercase word set used for matching, without common short words."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS}


class LocalMemory:
    """contracts.Memory backed by a local JSON-lines file."""

    def __init__(self, log: EventLog, path: str | os.PathLike):
        self.log = log
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.org: Org | None = None
        self._lock = threading.Lock()

    # ----- helpers -----

    def _agent(self, agent_id: str, action: str) -> Agent:
        if self.org is None:
            raise RuntimeError("call setup(org) first")
        try:
            return self.org.agent(agent_id)
        except KeyError:
            self.log.append("memory.refused", agent_id, action=action, reason="unknown agent")
            raise PermissionError(f"unknown agent {agent_id!r}") from None

    def _append(self, record: dict) -> None:
        with self._lock, open(self.path, "ab") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.write((json.dumps(record) + "\n").encode())
                handle.flush()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _all(self) -> list[dict]:
        with open(self.path, "rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            try:
                raw = handle.read().decode()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    # ----- contracts.Memory -----

    def setup(self, org: Org) -> None:
        """Remember the org. Local memory needs no other setup."""
        self.org = org
        self.log.append("memory.setup", "memory", backend="local", departments=org.departments,
                        agents=[a.id for a in org.agents])

    def write(self, agent_id: str, text: str, label: str) -> str:
        """Write one fact into the writer's own department and return its id."""
        agent = self._agent(agent_id, "write")
        if not text or not text.strip():
            raise ValueError("text is empty")
        fact_id = "fact-" + uuid.uuid4().hex[:12]
        self._append({"id": fact_id, "text": text, "label": label, "department": agent.department,
                      "author": agent.id, "kind": "source", "came_from": [], "reason": None,
                      "occurred_at": utc_now()})
        self.log.append("memory.write", agent.id, fact_id=fact_id, department=agent.department,
                        audience=agent.department, label=label, text=text)
        return fact_id

    def pass_down(self, head_id: str, to_department: str, text: str, came_from: list[str], reason: str) -> str:
        """A head or the director writes a new fact into another department."""
        agent = self._agent(head_id, "pass_down")
        if not agent.reads_everything:
            self.log.append("memory.refused", agent.id, action="pass_down", reason="not a head or director",
                            to_department=to_department)
            raise PermissionError(f"{agent.id} is a {agent.role}; only heads and the director pass down")
        if to_department not in self.org.departments:
            raise ValueError(f"no department called {to_department!r}")
        if not text or not text.strip():
            raise ValueError("text is empty")
        came_from = [str(x) for x in came_from]
        fact_id = "fact-" + uuid.uuid4().hex[:12]
        self._append({"id": fact_id, "text": text, "label": f"Pass-down from {agent.id}",
                      "department": to_department, "author": agent.id, "kind": "source",
                      "came_from": came_from, "reason": reason, "occurred_at": utc_now()})
        self.log.append("memory.pass_down", agent.id, fact_id=fact_id, to_department=to_department,
                        audience=to_department, came_from=came_from, reason=reason, text=text)
        return fact_id

    def recall(self, agent_id: str, query: str, limit: int = 20) -> list[Fact]:
        """Facts this agent may read that share words with the question, best match first."""
        agent = self._agent(agent_id, "recall")
        wanted = words(query)
        scored = []
        for order, rec in enumerate(self._all()):
            if not self.org.can_read(agent.id, rec["department"]):
                continue
            score = len(wanted & words(rec["text"] + " " + (rec.get("label") or ""))) if wanted else 1
            if score:
                scored.append((-score, -order, rec))
        scored.sort(key=lambda item: (item[0], item[1]))
        facts = [Fact(id=r["id"], text=r["text"], department=r["department"], author=r["author"],
                      kind=r.get("kind", "source"), came_from=r.get("came_from") or [],
                      occurred_at=r.get("occurred_at"))
                 for _, _, r in scored[:max(1, int(limit))]]
        self.log.append("memory.recall", agent.id, query=query, limit=limit, ids=[f.id for f in facts])
        return facts
