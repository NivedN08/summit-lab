"""An agent runner that calls no model, for testing the lab's loop.

    backend = MockBackend(lambda agent, brief, workspace: {"answer": 4})
    result = backend.run(agent, "add 2 and 2", workspace)
    backend.calls   # every call, in order, for assertions

The script may write files into the workspace. Whatever dict it returns is written as
result.json. Returning None means "the agent left no result.json"; raising means the agent
crashed. Both give ok=False.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Callable

from lab.contracts import Agent, AgentResult


@dataclass(frozen=True)
class Call:
    agent_id: str
    brief: str
    workspace: pathlib.Path


class MockBackend:
    """Runs `script(agent, brief, workspace)` in place of a model session."""

    def __init__(self, script: Callable[[Agent, str, pathlib.Path], dict | None]):
        self.script = script
        self.calls: list[Call] = []
        self._lock = threading.Lock()

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800) -> AgentResult:
        start = time.monotonic()
        workspace = pathlib.Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.calls.append(Call(agent.id, brief, workspace))
            n = len(self.calls)

        def failed(error: str) -> AgentResult:
            return AgentResult(ok=False, result={}, seconds=time.monotonic() - start, error=error)

        try:
            out = self.script(agent, brief, workspace)
        except Exception as e:
            return failed(f"agent failed: {type(e).__name__}: {e}")
        if out is None:
            return failed("the agent left no result.json")
        if not isinstance(out, dict):
            return failed(f"result.json must be a JSON object, got {type(out).__name__}")
        (workspace / "result.json").write_text(json.dumps(out, indent=2))
        return AgentResult(ok=True, result=out, seconds=time.monotonic() - start, session_id=f"mock-{n}")
