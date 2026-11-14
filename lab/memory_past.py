"""Shared memory on past.dev with per-department read limits. Implements contracts.Memory.

How the read limits map onto past.dev:
  - every agent is one past.dev identity, "<namespace>-<agent id>";
  - every department is one rule audience, "<namespace>-<department>" (shortened if needed),
    reached by identities whose lab_department is that department or whose lab_role is
    head or director;
  - every write names the writer's own department's audience. There is one code path that
    sends facts (_ingest) and it refuses anything but one of this run's audiences, because a
    fact with no audience is readable by every identity in the project (issue I-2).

Trait values carry the namespace ("<namespace>:team-a", "<namespace>:head"). past.dev rules
cannot be nested, so this is what keeps one run's audiences from matching another run's
identities.

The key is read from PAST_API_KEY or the repository's .env, kept on the instance only, and
never logged. Every HTTP call is logged as "past.call" with past.dev's request id and seconds.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from lab.contracts import DIRECTOR, HEAD, Agent, EventLog, Fact, Org
from lab.eventlog import utc_now

BASE = "https://api.past.dev/api/v1"
ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_TRIES = 5
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


class PastError(RuntimeError):
    """past.dev answered with an error that retrying did not fix."""


def audience_slug(namespace: str, department: str) -> str:
    """Audience slug for one department: 2 to 40 lowercase letters, digits and dashes.

    Returns "<namespace>-<department>" when that is already valid. Otherwise it cleans and
    shortens it and adds 8 characters of a hash of the original, so the result is
    deterministic and two different departments do not end up with the same slug.
    """
    raw = f"{namespace}-{department}"
    clean = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if clean == raw and SLUG.match(clean):
        return clean
    digest = hashlib.sha1(raw.encode()).hexdigest()[:8]
    prefix = clean[:31].strip("-")
    return f"{prefix}-{digest}" if prefix else digest


def _read_key() -> str:
    key = os.environ.get("PAST_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("PAST_API_KEY=") or line.startswith("PAST_API_KEY ="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        raise RuntimeError("PAST_API_KEY is not set (put it in .env)")
    return key


class PastMemory:
    """contracts.Memory on past.dev. One instance per run; `namespace` keeps runs apart."""

    def __init__(self, log: EventLog, namespace: str, *, settle_seconds: float = 5, base_url: str = BASE):
        if not NAMESPACE.match(namespace):
            raise ValueError("namespace must be 1 to 31 lowercase letters, digits and dashes")
        self.log = log
        self.namespace = namespace
        self.base_url = base_url
        self.settle_seconds = settle_seconds
        self.org: Org | None = None
        self._audiences: dict[str, str] = {}    # department -> audience slug
        self._written: list[str] = []           # fact ids this instance sent
        self._pending: dict[str, str] = {}      # fact id -> ingestion id not yet seen completed
        self._key: str | None = None

    # ----- HTTP -----

    def _send(self, method: str, path: str, body: dict | None = None):
        """One HTTP call. Returns (status, parsed body, headers, request id). Raises OSError on network failure."""
        if self._key is None:
            self._key = _read_key()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self._key)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "summit-lab/0.1")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw, status, headers = resp.read(), resp.status, resp.headers
        except urllib.error.HTTPError as err:
            raw, status, headers = err.read(), err.code, err.headers
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            parsed = {"raw": raw.decode(errors="replace")[:300]}
        return status, parsed, headers, headers.get("X-Request-Id")

    def _call(self, actor: str, method: str, path: str, body: dict | None = None):
        """Call past.dev, retrying on 429, 5xx and network errors. Returns (status, parsed, request id, seconds)."""
        start = time.time()
        status, parsed, request_id = None, {}, None
        for attempt in range(1, MAX_TRIES + 1):
            t0 = time.time()
            headers, error = {}, None
            try:
                status, parsed, headers, request_id = self._send(method, path, body)
            except OSError as exc:
                status, parsed, request_id, error = None, {}, None, type(exc).__name__
            entry = {"method": method, "path": path, "status": status, "request_id": request_id,
                     "seconds": round(time.time() - t0, 2), "attempt": attempt}
            if error:
                entry["error"] = error
            elif status >= 300:
                entry["error"] = json.dumps(parsed)[:300]
            self.log.append("past.call", actor, **entry)
            if status is not None and status < 500 and status != 429:
                break
            if attempt < MAX_TRIES:
                retry_after = (headers or {}).get("Retry-After")
                try:
                    delay = min(float(retry_after), 30) if retry_after else min(2 ** (attempt - 1), 16)
                except ValueError:
                    delay = min(2 ** (attempt - 1), 16)
                time.sleep(delay)
        if status is None:
            raise PastError(f"past.dev {method} {path}: network error after {MAX_TRIES} tries")
        return status, parsed, request_id, round(time.time() - start, 2)

    def _must(self, result, what: str):
        status, parsed, request_id, seconds = result
        if status >= 300:
            raise PastError(f"past.dev refused to {what}: HTTP {status} {json.dumps(parsed)[:300]} (request {request_id})")
        return parsed, request_id, seconds

    # ----- names -----

    def _identity(self, agent_id: str) -> str:
        return f"{self.namespace}-{agent_id}"

    def _agent(self, agent_id: str, action: str) -> Agent:
        if self.org is None:
            raise RuntimeError("call setup(org) first")
        try:
            return self.org.agent(agent_id)
        except KeyError:
            self.log.append("memory.refused", agent_id, action=action, reason="unknown agent")
            raise PermissionError(f"unknown agent {agent_id!r}") from None

    def _traits(self, agent: Agent) -> dict:
        ns = self.namespace
        return {"lab_department": f"{ns}:{agent.department}", "lab_role": f"{ns}:{agent.role}", "lab_run": ns}

    # ----- setup -----

    def setup(self, org: Org) -> None:
        """Create one identity per agent and one rule audience per department, then check memberships."""
        self.org = org
        ns = self.namespace
        self._audiences = {d: audience_slug(ns, d) for d in org.departments}
        for agent in org.agents:
            self._must(self._call("memory", "PUT", f"/identities/{urllib.parse.quote(self._identity(agent.id), safe='')}",
                                  {"traits": self._traits(agent)}), f"create identity for {agent.id}")
        for dept, slug in self._audiences.items():
            rule = {"match": "any", "conditions": [
                {"trait": "lab_department", "operator": "is", "values": [f"{ns}:{dept}"]},
                {"trait": "lab_role", "operator": "is_one_of", "values": [f"{ns}:{DIRECTOR}", f"{ns}:{HEAD}"]},
            ]}
            self._must(self._call("memory", "PUT", f"/audiences/{slug}",
                                  {"kind": "rule", "name": f"{ns} {dept}"[:200], "rule": rule}), f"create audience {slug}")
            self._must(self._call("memory", "POST", f"/audiences/{slug}/recompute"), f"recompute audience {slug}")
        time.sleep(self.settle_seconds)
        memberships = self._check_memberships()
        self.log.append("memory.setup", "memory", backend="past", namespace=ns, audiences=self._audiences,
                        identities={a.id: self._identity(a.id) for a in org.agents}, memberships=memberships)

    def attach(self, org: Org) -> None:
        """Use identities and audiences another process already set up for this namespace.
        Makes no call to past.dev: it only learns the org and the audience names setup() made."""
        self.org = org
        self._audiences = {d: audience_slug(self.namespace, d) for d in org.departments}
        self.log.append("memory.attach", "memory", backend="past", namespace=self.namespace,
                        audiences=self._audiences)

    def _check_memberships(self, tries: int = 6) -> dict:
        """Ask past.dev which audiences each identity reaches; fail if any differs from the org's rule."""
        all_slugs = set(self._audiences.values())
        wrong: dict = {}
        for attempt in range(tries):
            seen, wrong = {}, {}
            for agent in self.org.agents:
                parsed, _, _ = self._must(self._call("memory", "GET", f"/identities/{urllib.parse.quote(self._identity(agent.id), safe='')}"),
                                          f"look up identity {agent.id}")
                reached = set(parsed.get("audiences") or []) - {"project"}
                expected = all_slugs if agent.reads_everything else {self._audiences[agent.department]}
                seen[agent.id] = sorted(reached)
                if reached != expected:
                    wrong[agent.id] = {"expected": sorted(expected), "got": sorted(reached)}
            if not wrong:
                return seen
            time.sleep(3)
        raise PastError(f"audience memberships do not match the org: {json.dumps(wrong)}")

    # ----- writing -----

    def _ingest(self, actor: str, fact_id: str, text: str, label: str, audience: str | None, metadata: dict):
        """The only code path that sends a fact. Refuses anything but one of this run's audiences."""
        if not audience or audience not in self._audiences.values():
            raise ValueError(f"refusing to write {fact_id}: audience {audience!r} is not one of this run's departments")
        body = {"id": fact_id, "content": text, "label": label, "timestamp": utc_now(), "metadata": metadata,
                "audience": audience, "identity": self._identity(actor), "idempotencyKey": fact_id}
        parsed, request_id, seconds = self._must(self._call(actor, "POST", "/ingest", body), f"write {fact_id}")
        self._written.append(fact_id)
        if parsed.get("status") != "completed":
            self._pending[fact_id] = parsed.get("ingestionId")
        return parsed.get("ingestionId"), request_id, seconds

    def write(self, agent_id: str, text: str, label: str, *, wait: bool = False) -> str:
        """Write one fact into the writer's own department. With wait=True, block until past.dev has processed it."""
        agent = self._agent(agent_id, "write")
        if not text or not text.strip():
            raise ValueError("text is empty")
        fact_id = f"{self.namespace}-{uuid.uuid4().hex[:12]}"
        audience = self._audiences[agent.department]
        meta = {"author": agent.id, "department": agent.department, "run": self.namespace, "label": label}
        ingestion_id, request_id, seconds = self._ingest(agent.id, fact_id, text, label, audience, meta)
        self.log.append("memory.write", agent.id, fact_id=fact_id, department=agent.department, audience=audience,
                        label=label, text=text, ingestion_id=ingestion_id, request_id=request_id, seconds=seconds)
        if wait:
            self._wait(fact_id)
        return fact_id

    def pass_down(self, head_id: str, to_department: str, text: str, came_from: list[str], reason: str,
                  *, wait: bool = False) -> str:
        """A head or the director writes a NEW fact into another department's audience."""
        agent = self._agent(head_id, "pass_down")
        if not agent.reads_everything:
            self.log.append("memory.refused", agent.id, action="pass_down", reason="not a head or director",
                            to_department=to_department)
            raise PermissionError(f"{agent.id} is a {agent.role}; only heads and the director pass down")
        if to_department not in self._audiences:
            raise ValueError(f"no department called {to_department!r}")
        if not text or not text.strip():
            raise ValueError("text is empty")
        came_from = [str(x) for x in came_from]
        fact_id = f"{self.namespace}-{uuid.uuid4().hex[:12]}"
        audience = self._audiences[to_department]
        label = f"Pass-down from {agent.id}"
        meta = {"author": agent.id, "department": to_department, "run": self.namespace, "label": label,
                "pass_down": True, "came_from": came_from, "reason": reason}
        ingestion_id, request_id, seconds = self._ingest(agent.id, fact_id, text, label, audience, meta)
        self.log.append("memory.pass_down", agent.id, fact_id=fact_id, to_department=to_department, audience=audience,
                        came_from=came_from, reason=reason, text=text, ingestion_id=ingestion_id,
                        request_id=request_id, seconds=seconds)
        if wait:
            self._wait(fact_id)
        return fact_id

    def _wait(self, fact_id: str, timeout: float = 180) -> None:
        ingestion_id = self._pending.get(fact_id)
        deadline = time.time() + timeout
        while ingestion_id:
            parsed, _, _ = self._must(self._call("memory", "GET", f"/ingest/{ingestion_id}"), f"poll {fact_id}")
            if parsed.get("status") == "completed":
                self._pending.pop(fact_id, None)
                return
            if parsed.get("status") == "failed":
                self._pending.pop(fact_id, None)
                raise PastError(f"past.dev failed to process {fact_id}: {parsed.get('failure')}")
            if time.time() > deadline:
                raise PastError(f"{fact_id} was not processed within {timeout} seconds")
            time.sleep(3)

    def wait_settled(self, timeout: float = 180) -> None:
        """Block until every fact this instance wrote has been processed by past.dev."""
        for fact_id in list(self._pending):
            self._wait(fact_id, timeout)

    # ----- reading -----

    def recall(self, agent_id: str, query: str, limit: int = 20) -> list[Fact]:
        """Read as the agent's own identity; past.dev returns only what its audiences allow."""
        agent = self._agent(agent_id, "recall")
        limit = max(1, min(int(limit), 200))
        body = {"query": query, "identity": self._identity(agent.id), "limit": limit}
        parsed, request_id, seconds = self._must(self._call(agent.id, "POST", "/recall", body), "recall")
        facts, source_ids = [], set()
        for result in parsed.get("results") or []:
            sources = result.get("sources") or []
            source_ids.update(s.get("sourceId") for s in sources if s.get("sourceId"))
            meta = next((s.get("metadata") for s in sources if isinstance(s.get("metadata"), dict)), None) or {}
            text = result.get("content") or " ".join(e for s in sources for e in (s.get("excerpts") or []))
            facts.append(Fact(
                id=(sources[0].get("sourceId") if sources else None) or result.get("id"),
                text=text,
                department=meta.get("department"),
                author=meta.get("author"),
                kind=(result.get("artifact") or {}).get("kind") or "source",
                came_from=list(meta.get("came_from") or []),
                occurred_at=result.get("occurredAt"),
            ))
        self.log.append("memory.recall", agent.id, query=query, limit=limit, ids=[f.id for f in facts],
                        source_ids=sorted(source_ids), kinds=[f.kind for f in facts],
                        pending=parsed.get("pending"), request_id=request_id, seconds=seconds)
        return facts

    # ----- cleanup -----

    def cleanup(self) -> dict:
        """Delete the facts this instance wrote, then its audiences and identities. Best effort; never raises."""
        problems = []

        def delete(what, method, path, body=None):
            try:
                status = self._call("memory", method, path, body)[0]
            except PastError:
                status = None
            if status is None or (status >= 300 and status != 404):
                problems.append(f"{what}: HTTP {status}")
            return status

        if self._written:
            if delete("facts", "POST", "/data-points/delete", {"ids": list(self._written)}) == 404:
                # the batch is all-or-nothing and one id was already gone: delete one at a time
                for fact_id in self._written:
                    delete(f"fact {fact_id}", "DELETE", f"/data-points/{urllib.parse.quote(fact_id, safe='')}")
        for slug in self._audiences.values():
            delete(f"audience {slug}", "DELETE", f"/audiences/{slug}")
        for agent in (self.org.agents if self.org else []):
            delete(f"identity {agent.id}", "DELETE", f"/identities/{urllib.parse.quote(self._identity(agent.id), safe='')}")
        summary = {"facts": len(self._written), "audiences": len(self._audiences), "problems": problems}
        self.log.append("memory.cleanup", "memory", namespace=self.namespace, **summary)
        self._written, self._pending = [], {}
        return summary
