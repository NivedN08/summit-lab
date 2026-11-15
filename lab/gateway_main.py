"""Entry point that runs lab.gateway.Gateway inside a Modal Sandbox (see lab/modal_gateway.py).

    python3 -m lab.gateway_main --config /gateway/config.json --memory past --log-dir /gateway --port 8080

The config file holds {"org": [agents as dicts], "namespace": ..., "exa_departments": [...],
"blocked_terms": [...], "agents_may_pass_down": false, "exa_budget_usd": 25,
"exa_caps": {agent id: most Exa calls}}. Keys come from the environment (the Modal Secret
`summit-lab-keys`: PAST_API_KEY, EXA_API_KEY), never from the config. Later budgets (a new count
for an agent, for example for its next round) arrive in <log-dir>/budgets.json, which the
orchestrator writes and the gateway reads before each Exa call.

The gateway listens on 0.0.0.0 and takes each caller's identity ONLY from Modal's
X-Verified-User-Data header (set from the agent's connect token). With `--memory past` it attaches
to the identities and audiences the orchestrator already set up; with `--memory local` it keeps a
LocalMemory file in the log folder (for tests without past.dev). Events go to
<log-dir>/events.jsonl and Exa calls to <log-dir>/exa_calls.jsonl, which the orchestrator copies back.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import threading

from lab.contracts import Agent, Org
from lab.eventlog import JsonlEventLog
from lab.gateway import Gateway
from lab.tools import exa

VERIFIED_HEADER = "X-Verified-User-Data"
BUDGET_FILE = "budgets.json"


def build(config: dict, memory_kind: str, log_dir: pathlib.Path, port: int) -> Gateway:
    """Make the gateway described by `config`, with its logs in `log_dir`. Not started."""
    log_dir.mkdir(parents=True, exist_ok=True)
    org = Org(agents=[Agent(**a) for a in config["org"]])
    log = JsonlEventLog(log_dir / "events.jsonl")
    exa.LOG = log_dir / "exa_calls.jsonl"       # the gateway's Exa log and budget count live here
    if config.get("exa_budget_usd") is not None:
        os.environ["EXA_BUDGET_USD"] = str(max(0.0, float(config["exa_budget_usd"])))
    if memory_kind == "past":
        from lab.memory_past import PastMemory
        memory = PastMemory(log, config["namespace"])
        memory.attach(org)
    elif memory_kind == "local":
        from lab.memory_local import LocalMemory
        memory = LocalMemory(log, log_dir / "memory.jsonl")
        memory.setup(org)
    else:
        raise SystemExit(f"unknown memory {memory_kind!r}")
    return Gateway(org, memory, log, config.get("exa_departments") or [], config.get("blocked_terms") or [],
                   host="0.0.0.0", port=port, agents_may_pass_down=bool(config.get("agents_may_pass_down", False)),
                   identity_header=VERIFIED_HEADER, exa_caps=config.get("exa_caps") or {},
                   budget_file=log_dir / BUDGET_FILE)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the lab gateway inside a Modal Sandbox")
    parser.add_argument("--config", required=True)
    parser.add_argument("--memory", choices=["past", "local"], default="past")
    parser.add_argument("--log-dir", default="/gateway")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    config = json.loads(pathlib.Path(args.config).read_text())
    gateway = build(config, args.memory, pathlib.Path(args.log_dir), args.port)
    gateway.start()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    stopped.wait()
    gateway.stop()


if __name__ == "__main__":
    main()
