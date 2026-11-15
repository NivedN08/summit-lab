"""The one process that holds the lab's API keys. Agents talk to it over HTTP on 127.0.0.1.

The orchestrator gives each agent a secret token (issue_token). The gateway decides who is
calling from that token alone: any agent id, identity, role, department or audience in a
request body is ignored. Unknown or missing token: 401.

On Modal (lab/gateway_main.py) the gateway runs with identity_header="X-Verified-User-Data": the
agent is then taken only from that header, which Modal sets from the agent's connect token, and
the token table above is not consulted. No header, or an agent not in the org: 401.

Routes (all POST, JSON in and out):
  /memory/write      {text, label}                               -> {id, department}
  /memory/recall     {query, limit}                              -> {facts: [...]}
  /memory/pass_down  {to_department, text, came_from, reason}    -> {id, to_department}   (403 unless head/director)
  /exa/search        {query, objective, type?, num?}             -> {results: [...]}      (403 unless an Exa department,
                                                                                           429 over the agent's budget)
  /exa/contents      {urls, objective, max_chars?}               -> {results: [...]}      (the same)

Exa results whose URL or title contains a blocked term are dropped before the agent sees
them, and a contents request for a blocked URL is refused. Every request is logged as
"gateway.request"; tokens are never logged.

Search budgets: `exa_caps` maps an agent id to the most Exa calls it may make (search and
contents both count); agents not listed have no cap. `set_budget(agent, cap, label)` starts a
new count for that agent, under a label that names what the calls are for ("library",
"team-a", "research"). Every Exa call is logged as "exa.call" with its label; a call over the
cap is answered 429 and logged as "exa.refused". With `budget_file`, the gateway also reads new
budgets from that JSON file ({agent: {"cap", "label", "window"}}) before each Exa call: that is
how the lab sets budgets on the gateway running on Modal.

Usage from the orchestrator:
    gw = Gateway(org, memory, log, exa_departments=["library"], blocked_terms=["source paper title"])
    url = gw.start()
    env = {"LAB_GATEWAY_URL": url, "LAB_GATEWAY_TOKEN": gw.issue_token(agent.id)}
    ...
    gw.stop()
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hmac
import json
import os
import pathlib
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lab.contracts import Agent, EventLog, Memory, Org
from lab.tools import exa

MAX_BODY = 1_000_000
SEARCH_TYPES = {"auto", "lite", "deep-lite", "deep", "deep-reasoning"}


class Refused(Exception):
    """Stop handling a request and answer with this status and message."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def default_exa_call(path: str, body: dict):
    """Call Exa with the key from the environment or .env. Returns (status, parsed, seconds)."""
    try:
        exa.load_env()
    except SystemExit as exc:          # exa.load_env exits when the key is missing
        raise RuntimeError(str(exc)) from None
    return exa.post(path, body)


def _text(body: dict, name: str, required: bool = True) -> str:
    value = body.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"'{name}' must be a non-empty string")
    return value


def _int(body: dict, name: str, default: int, low: int, high: int) -> int:
    value = body.get(name, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"'{name}' must be a whole number from {low} to {high}")
    return value


class Gateway:
    """Threaded HTTP server that turns a token into an agent and calls memory and Exa for it."""

    def __init__(self, org: Org, memory: Memory, log: EventLog, exa_departments=(), blocked_terms=(),
                 *, exa_call=default_exa_call, host: str = "127.0.0.1", port: int = 0,
                 agents_may_pass_down: bool = True, identity_header: str | None = None,
                 exa_caps: dict | None = None, budget_file=None):
        self.org = org
        # When set (on Modal: "X-Verified-User-Data"), the caller's identity comes ONLY from this header,
        # which a trusted proxy fills in as JSON {"agent": "<agent id>"}. The bearer-token table is not used.
        self.identity_header = identity_header
        self.memory = memory
        self.log = log
        self.exa_departments = set(exa_departments)
        self.blocked_terms = [t.lower() for t in blocked_terms if t]
        self.exa_call = exa_call
        # When False, no token can pass facts down: the lab delivers every pass-down itself, after checking it.
        self.agents_may_pass_down = agents_may_pass_down
        self.host, self.port = host, port
        self._tokens: dict[str, str] = {}     # agent id -> token
        self._exa_log_lock = threading.Lock()
        # agent id -> {"cap", "label", "window"}; a new window starts a new count of calls
        self._budgets = {a: {"cap": int(n), "label": None, "window": 0} for a, n in (exa_caps or {}).items()}
        self._used: dict[str, tuple[int, int]] = {}      # agent id -> (window, calls made in it)
        self._budget_lock = threading.Lock()
        self.budget_file = budget_file
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.routes = {
            "/memory/write": self._memory_write,
            "/memory/recall": self._memory_recall,
            "/memory/pass_down": self._memory_pass_down,
            "/exa/search": self._exa_search,
            "/exa/contents": self._exa_contents,
        }

    # ----- lifecycle -----

    def issue_token(self, agent_id: str) -> str:
        """Make a new secret token for this agent, replacing any earlier one. Unknown agent: KeyError."""
        self.org.agent(agent_id)
        token = secrets.token_urlsafe(32)
        self._tokens[agent_id] = token
        return token

    def start(self) -> str:
        """Start serving in a background thread and return the base URL."""
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                gateway._handle(self)

            def do_GET(self):
                gateway._handle(self)

            def log_message(self, *args):   # keep stderr quiet; requests go to the event log
                pass

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="lab-gateway", daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.log.append("gateway.start", "gateway", host=host, port=port,
                        exa_departments=sorted(self.exa_departments), blocked_terms=len(self.blocked_terms),
                        exa_caps={a: b["cap"] for a, b in self._budgets.items()},
                        identity=self.identity_header or "bearer token")
        return f"http://{host}:{port}"

    def stop(self) -> None:
        """Stop serving and close the socket."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None
            self.log.append("gateway.stop", "gateway")

    # ----- request handling -----

    def _verified_agent(self, value: str | None) -> Agent | None:
        """The agent named in the proxy's verified-identity header, or None if absent or unknown."""
        try:
            data = json.loads(value) if value else None
        except ValueError:
            return None
        agent_id = data.get("agent") if isinstance(data, dict) else None
        if not isinstance(agent_id, str):
            return None
        try:
            return self.org.agent(agent_id)
        except KeyError:
            return None

    def _agent_for(self, header: str | None) -> Agent | None:
        if not header or not header.lower().startswith("bearer "):
            return None
        given = header[7:].strip()
        for agent_id, token in self._tokens.items():
            if hmac.compare_digest(given.encode(), token.encode()):
                return self.org.agent(agent_id)
        return None

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        start = time.time()
        agent, error = None, None
        path = handler.path.split("?", 1)[0]
        try:
            length = int(handler.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise Refused(413, "request body too large")
            raw = handler.rfile.read(length) if length else b"{}"
            if self.identity_header:
                agent = self._verified_agent(handler.headers.get(self.identity_header))
            else:
                agent = self._agent_for(handler.headers.get("Authorization"))
            if agent is None:
                raise Refused(401, "missing or unknown token")
            if handler.command != "POST":
                raise Refused(405, "use POST")
            route = self.routes.get(path)
            if route is None:
                raise Refused(404, f"no route {path}")
            try:
                body = json.loads(raw)
            except ValueError:
                raise Refused(400, "request body is not JSON") from None
            if not isinstance(body, dict):
                raise Refused(400, "request body must be a JSON object")
            status, payload = route(agent, body)
        except Refused as exc:
            status, payload = exc.status, {"error": str(exc)}
        except PermissionError as exc:
            status, payload = 403, {"error": str(exc)}
        except (ValueError, TypeError, KeyError) as exc:
            status, payload = 400, {"error": str(exc)}
        except Exception as exc:   # report, do not crash the server thread
            status, payload = 500, {"error": f"internal error ({type(exc).__name__})"}
            error = f"{type(exc).__name__}: {exc}"[:300]
        entry = {"agent": agent.id if agent else None, "path": path, "status": status,
                 "seconds": round(time.time() - start, 3)}
        if status >= 400:
            entry["error"] = error or payload.get("error")
        self.log.append("gateway.request", agent.id if agent else "unknown", **entry)   # before replying
        data = json.dumps(payload, default=str).encode()
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        except OSError:
            pass

    # ----- memory routes -----

    def _memory_write(self, agent: Agent, body: dict):
        text = _text(body, "text")
        label = body.get("label") or "note"
        if not isinstance(label, str):
            raise ValueError("'label' must be a string")
        fact_id = self.memory.write(agent.id, text, label)
        return 200, {"id": fact_id, "department": agent.department}

    def _memory_recall(self, agent: Agent, body: dict):
        query = _text(body, "query")
        limit = _int(body, "limit", 20, 1, 200)
        facts = self.memory.recall(agent.id, query, limit)
        return 200, {"facts": [dataclasses.asdict(f) for f in facts]}

    def _memory_pass_down(self, agent: Agent, body: dict):
        if not self.agents_may_pass_down:
            raise Refused(403, "pass-downs are delivered by the lab, not by agents")
        if not agent.reads_everything:
            raise PermissionError(f"{agent.id} is a {agent.role}; only heads and the director pass down")
        to_department = _text(body, "to_department")
        text = _text(body, "text")
        reason = _text(body, "reason")
        came_from = body.get("came_from") or []
        if isinstance(came_from, str):
            came_from = [x.strip() for x in came_from.split(",") if x.strip()]
        if not isinstance(came_from, list) or not all(isinstance(x, str) for x in came_from):
            raise ValueError("'came_from' must be a list of fact ids")
        fact_id = self.memory.pass_down(agent.id, to_department, text, came_from, reason)
        return 200, {"id": fact_id, "to_department": to_department}

    # ----- Exa routes -----

    def _check_exa(self, agent: Agent) -> None:
        if agent.department not in self.exa_departments:
            raise PermissionError(f"department {agent.department} may not use Exa")
        cap = float(os.environ.get("EXA_BUDGET_USD", "25"))
        if exa.spent() >= cap:
            raise Refused(402, f"Exa budget of ${cap:.2f} is used up")

    def set_budget(self, agent_id: str, cap: int, label: str | None = None) -> dict:
        """Start a new count of Exa calls for this agent: at most `cap` from now on, under `label`."""
        with self._budget_lock:
            window = self._budgets.get(agent_id, {}).get("window", 0) + 1
            self._budgets[agent_id] = {"cap": int(cap), "label": label, "window": window}
            return dict(self._budgets[agent_id])

    def _read_budget_file(self) -> None:
        """Take new budgets from budget_file, when there is one. A file that cannot be read changes nothing."""
        if not self.budget_file:
            return
        try:
            data = json.loads(pathlib.Path(self.budget_file).read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        with self._budget_lock:
            for agent_id, b in data.items():
                if isinstance(b, dict) and isinstance(b.get("cap"), int) and isinstance(b.get("window"), int):
                    self._budgets[agent_id] = {"cap": b["cap"], "label": b.get("label"), "window": b["window"]}

    def _take_call(self, agent: Agent, endpoint: str) -> None:
        """Count one Exa call against the agent's budget, or refuse it (429) when the budget is used up."""
        self._read_budget_file()
        with self._budget_lock:
            budget = self._budgets.get(agent.id)
            if budget is None:
                self.log.append("exa.call", agent.id, agent=agent.id, endpoint=endpoint, budget=None, used=None, cap=None)
                return
            window, used = self._used.get(agent.id, (budget["window"], 0))
            if window != budget["window"]:
                used = 0
            if used >= budget["cap"]:
                self.log.append("exa.refused", agent.id, agent=agent.id, endpoint=endpoint, budget=budget["label"],
                                used=used, cap=budget["cap"])
                raise Refused(429, f"web search limit reached: {agent.id} has used {used} of {budget['cap']} calls "
                                   "allowed for this run (search and contents both count). Carry on without searching.")
            self._used[agent.id] = (budget["window"], used + 1)
            self.log.append("exa.call", agent.id, agent=agent.id, endpoint=endpoint, budget=budget["label"],
                            used=used + 1, cap=budget["cap"])

    def _is_blocked(self, *values) -> bool:
        joined = " ".join(v for v in values if isinstance(v, str)).lower()
        return any(term in joined for term in self.blocked_terms)

    def _filter(self, agent: Agent, endpoint: str, results: list) -> list:
        kept = [r for r in results if not self._is_blocked(r.get("url"), r.get("title"))]
        if len(kept) < len(results):
            self.log.append("exa.blocked", agent.id, endpoint=endpoint, dropped=len(results) - len(kept))
        return kept

    def _log_exa(self, agent: Agent, endpoint: str, objective: str, asked: dict, status, seconds, cost, kept, dropped):
        with self._exa_log_lock:
            exa.log_call({
                "time": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "agent": agent.id, "endpoint": endpoint, "objective": objective, **asked,
                "status": status, "seconds": seconds, "cost_usd": cost, "via": "gateway", "blocked": dropped,
                "results": [{"title": r.get("title"), "url": r.get("url"), "published": (r.get("publishedDate") or "")[:10]} for r in kept],
            })

    @staticmethod
    def _shape(results: list) -> list:
        out = []
        for r in results:
            item = {"title": r.get("title"), "url": r.get("url"), "published": (r.get("publishedDate") or "")[:10]}
            if r.get("highlights"):
                item["highlights"] = r["highlights"]
            if r.get("text"):
                item["text"] = r["text"]
            out.append(item)
        return out

    def _exa_search(self, agent: Agent, body: dict):
        self._check_exa(agent)
        query = _text(body, "query")
        objective = _text(body, "objective")
        request = {"query": query, "objective": objective, "contents": {"highlights": True}}
        if body.get("type") is not None:
            if body["type"] not in SEARCH_TYPES:
                raise ValueError(f"'type' must be one of {sorted(SEARCH_TYPES)}")
            request["type"] = body["type"]
        if body.get("num") is not None:
            request["numResults"] = _int(body, "num", 10, 1, 100)
        self._take_call(agent, "search")
        status, parsed, seconds = self.exa_call("/search", request)
        results = parsed.get("results") or []
        kept = self._filter(agent, "search", results)
        cost = (parsed.get("costDollars") or {}).get("total")
        self._log_exa(agent, "search", objective, {"query": query, "type": request.get("type", "auto")},
                      status, seconds, cost, kept, len(results) - len(kept))
        if status >= 300:
            return 502, {"error": f"Exa returned HTTP {status}"}
        return 200, {"results": self._shape(kept), "cost_usd": cost, "seconds": seconds}

    def _exa_contents(self, agent: Agent, body: dict):
        self._check_exa(agent)
        urls = body.get("urls")
        if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and u.strip() for u in urls):
            raise ValueError("'urls' must be a non-empty list of links")
        objective = _text(body, "objective")
        max_chars = _int(body, "max_chars", 6000, 100, 100_000)
        blocked = [u for u in urls if self._is_blocked(u)]
        if blocked:
            self.log.append("exa.blocked", agent.id, endpoint="contents", dropped=len(blocked), refused=True)
            raise PermissionError(f"{len(blocked)} of the requested links are blocked for this lab")
        self._take_call(agent, "contents")
        status, parsed, seconds = self.exa_call("/contents", {"urls": urls, "text": {"maxCharacters": max_chars}})
        results = parsed.get("results") or []
        kept = self._filter(agent, "contents", results)
        cost = (parsed.get("costDollars") or {}).get("total")
        self._log_exa(agent, "contents", objective, {"urls": urls}, status, seconds, cost, kept, len(results) - len(kept))
        if status >= 300:
            return 502, {"error": f"Exa returned HTTP {status}"}
        return 200, {"results": self._shape(kept), "cost_usd": cost, "seconds": seconds}
