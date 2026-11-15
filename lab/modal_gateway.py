"""The lab gateway on Modal: lab.gateway_main in one long-lived Sandbox, reached through connect tokens.

    gw = ModalGateway(org, memory, log, exa_departments=["library"], blocked_terms=[...])
    gw.start()
    env = {"LAB_GATEWAY_URL": gw.url_for(agent.id), "LAB_GATEWAY_TOKEN": gw.issue_token(agent.id)}
    ...
    gw.stop()

It has the surface lab.protocol.Lab uses on the local Gateway (start, issue_token, set_budget,
stop) plus url_for and sync. Search caps go in the config like the other settings; set_budget
writes the agent's new budget to /gateway/budgets.json, which the gateway reads before each Exa
call. issue_token makes a Modal Sandbox Connect Token whose metadata is
{"agent": <agent id>}; Modal hands that to the gateway in the X-Verified-User-Data header, and the
gateway takes the caller's identity from that header only.

Keys: the sandbox gets the Modal Secret `summit-lab-keys` (PAST_API_KEY, EXA_API_KEY) only when it
needs one (past.dev memory or Exa departments). The orchestrator keeps doing memory.setup and the
pass-downs itself; the remote PastMemory only attaches to that setup.

Logs: every few seconds, and once more at stop, new lines of the gateway's events.jsonl are added
to the local event log (same kind, actor and data, plus remote_time and remote_seq) and new lines
of its exa_calls.jsonl to the local records/exa_calls.jsonl. Agent writes seen this way are also
registered with the local PastMemory: their ingestion ids, so its wait_settled() waits for them as
it does locally, and their text, so its wait_visible() can ask for them by what they say.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading


from lab import backend_modal
from lab.contracts import EventLog, Memory, Org
from lab.tools import exa

PORT = 8080
REMOTE_DIR = "/gateway"
REMOTE_CODE = "/opt/summit"
KEYS_SECRET = "summit-lab-keys"
BUDGET_FILE = "budgets.json"     # the same name lab.gateway_main reads


def gateway_image():
    """The agent image plus the lab's code (lab/ only: no data, no .env) at /opt/summit/lab."""
    root = backend_modal.ROOT
    return backend_modal.agent_image().add_local_dir(
        root / "lab", f"{REMOTE_CODE}/lab", ignore=lambda p: "__pycache__" in p.parts or p.suffix == ".pyc")


class ModalGateway:
    """lab.gateway.Gateway run in a Modal Sandbox, with its logs copied back while it runs."""

    def __init__(self, org: Org, memory: Memory, log: EventLog, exa_departments=(), blocked_terms=(), *,
                 agents_may_pass_down: bool = True, exa_call=None, exa_caps: dict | None = None,
                 memory_kind: str | None = None,
                 poll_s: float = 3.0, timeout_s: int = 12 * 3600, secret_name: str = KEYS_SECRET):
        if exa_call is not None:
            raise ValueError("the Modal gateway calls Exa from its sandbox; exa_call cannot be replaced")
        self.org, self.memory, self.log = org, memory, log
        self.exa_departments = list(exa_departments)
        self.blocked_terms = list(blocked_terms)
        self.agents_may_pass_down = agents_may_pass_down
        self.exa_caps = {a: int(n) for a, n in (exa_caps or {}).items()}
        self._budgets: dict[str, dict] = {}     # what budgets.json holds: agent id -> {cap, label, window}
        self._budget_lock = threading.Lock()
        self.memory_kind = memory_kind or ("past" if hasattr(memory, "namespace") else "local")
        self.poll_s, self.timeout_s, self.secret_name = poll_s, timeout_s, secret_name
        self.sandbox = None
        self._urls: dict[str, str] = {}
        self._offsets = {"events": 0, "exa": 0}
        self._sync_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ----- what the sandbox is given -----

    def config(self) -> dict:
        """The gateway_main config: who is in the org and what the gateway may do. No keys."""
        cap = float(os.environ.get("EXA_BUDGET_USD", "25"))
        return {"org": [dataclasses.asdict(a) for a in self.org.agents],
                "namespace": getattr(self.memory, "namespace", None),
                "exa_departments": self.exa_departments, "blocked_terms": self.blocked_terms,
                "agents_may_pass_down": self.agents_may_pass_down,
                "exa_budget_usd": round(cap - exa.spent(), 4), "exa_caps": self.exa_caps}

    def needs_keys(self) -> bool:
        return self.memory_kind == "past" or bool(self.exa_departments)

    def command(self) -> list[str]:
        """Wait for the config file, then run the gateway in the foreground (its exit ends the sandbox)."""
        run = (f"cd {REMOTE_CODE} && exec python3 -m lab.gateway_main --config {REMOTE_DIR}/config.json "
               f"--memory {self.memory_kind} --log-dir {REMOTE_DIR} --port {PORT}")
        return ["bash", "-c", f"while [ ! -f {REMOTE_DIR}/config.json ]; do sleep 0.2; done; {run}"]

    # ----- lifecycle -----

    def start(self) -> str:
        """Start the gateway sandbox and wait until it listens. Returns a name for it (not a URL)."""
        import modal
        secrets = [modal.Secret.from_name(self.secret_name)] if self.needs_keys() else []
        self.sandbox = modal.Sandbox.create(
            *self.command(), app=backend_modal.modal_app(), image=gateway_image(), secrets=secrets,
            cpu=1.0, memory=1024, timeout=self.timeout_s, env={"PYTHONPATH": REMOTE_CODE},
            readiness_probe=modal.Probe.with_tcp(PORT), tags={"summit-lab": "gateway"})
        try:
            self.sandbox.filesystem.make_directory(REMOTE_DIR)
            self.sandbox.filesystem.write_text(json.dumps(self.config()), f"{REMOTE_DIR}/config.json.tmp")
            self.sandbox.exec("mv", f"{REMOTE_DIR}/config.json.tmp", f"{REMOTE_DIR}/config.json").wait()
            self.sandbox.wait_until_ready()
        except BaseException:
            self.sandbox.terminate()
            raise
        self.log.append("gateway.modal.start", "lab", sandbox_id=self.sandbox.object_id, memory=self.memory_kind,
                        keys_secret=self.secret_name if self.needs_keys() else None)
        self._thread = threading.Thread(target=self._poll, name="modal-gateway-logs", daemon=True)
        self._thread.start()
        self.sync()
        # Each connect token comes with its own URL, so agents get theirs from url_for(), not from this.
        return f"modal-sandbox:{self.sandbox.object_id}"

    def issue_token(self, agent_id: str) -> str:
        """A connect token whose verified metadata names this agent. Unknown agent: KeyError."""
        self.org.agent(agent_id)
        creds = self.sandbox.create_connect_token(user_metadata={"agent": agent_id}, port=PORT)
        self._urls[agent_id] = creds.url
        return creds.token

    def set_budget(self, agent_id: str, cap: int, label: str | None = None) -> dict:
        """Start a new count of Exa calls for this agent on the remote gateway, through budgets.json.
        A failed write is logged and the agent keeps its earlier budget."""
        with self._budget_lock:
            window = self._budgets.get(agent_id, {}).get("window", 0) + 1
            self._budgets[agent_id] = {"cap": int(cap), "label": label, "window": window}
            path = f"{REMOTE_DIR}/{BUDGET_FILE}"
            try:
                self.sandbox.filesystem.write_text(json.dumps(self._budgets), path + ".tmp")
                self.sandbox.exec("mv", path + ".tmp", path).wait()
            except Exception as err:
                self.log.append("gateway.modal.budget_error", "lab", agent=agent_id, error=f"{type(err).__name__}: {err}"[:300])
            return dict(self._budgets[agent_id])

    def url_for(self, agent_id: str) -> str:
        """The connect URL that goes with this agent's token."""
        return self._urls[agent_id]

    def stop(self) -> None:
        """Copy the last log lines back and terminate the sandbox."""
        if self.sandbox is None:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)
        try:
            self.sync()
        finally:
            sandbox, self.sandbox = self.sandbox, None
            try:
                sandbox.terminate()
            finally:
                self.log.append("gateway.modal.stop", "lab", sandbox_id=sandbox.object_id)

    # ----- logs -----

    def _poll(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                self.sync()
            except Exception as err:    # a missed poll is caught up by the next one
                self.log.append("gateway.modal.sync_error", "lab", error=f"{type(err).__name__}: {err}"[:300])

    def _new_lines(self, which: str, path: str) -> list[str]:
        """Complete lines added to a remote file since the last read."""
        start = self._offsets[which]
        proc = self.sandbox.exec("tail", "-c", f"+{start + 1}", path, text=False, timeout=60)
        data = proc.stdout.read()
        if proc.wait() != 0 or not data:
            return []
        end = data.rfind(b"\n") + 1      # keep a half-written last line for the next read
        self._offsets[which] = start + end
        return [line for line in data[:end].decode(errors="replace").splitlines() if line.strip()]

    def sync(self) -> int:
        """Copy new gateway events and Exa log lines to the local logs. Returns how many lines."""
        if self.sandbox is None:
            return 0
        with self._sync_lock:
            n = 0
            for line in self._new_lines("events", f"{REMOTE_DIR}/events.jsonl"):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                data = {k: v for k, v in event.items() if k not in ("seq", "time", "kind", "actor")}
                self.log.append(event.get("kind", "gateway.unknown"), event.get("actor", "gateway"), **data,
                                remote_time=event.get("time"), remote_seq=event.get("seq"))
                self._track(event)
                n += 1
            for line in self._new_lines("exa", f"{REMOTE_DIR}/exa_calls.jsonl"):
                try:
                    exa.log_call(json.loads(line))
                except ValueError:
                    continue
                n += 1
            return n

    def _track(self, event: dict) -> None:
        """Let the local PastMemory wait for facts agents wrote through the remote gateway."""
        if event.get("kind") not in ("memory.write", "memory.pass_down") or not event.get("fact_id"):
            return
        pending = getattr(self.memory, "_pending", None)
        if isinstance(pending, dict) and event.get("ingestion_id"):
            pending.setdefault(event["fact_id"], event["ingestion_id"])
        texts = getattr(self.memory, "_texts", None)
        if isinstance(texts, dict) and isinstance(event.get("text"), str):
            texts.setdefault(event["fact_id"], event["text"])
