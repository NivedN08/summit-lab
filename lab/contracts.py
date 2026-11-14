"""The shared shapes every part of the lab is built against.

Nothing here does any work. These are the names and signatures the event log, the memory
layer, the agent runner, the benchmark adapters and the main loop agree on, so each can be
built and tested on its own.

Words used the same way everywhere:
  agent       one model session with one job (a worker, a head or the director)
  department  a group of agents that share one memory (for example "library", "team-a")
  head        an agent that may read every department's memory and pass facts between them
  fact        one short statement written to memory
  pass-down   a new fact a head writes into another department, pointing at the facts it came from
  study       one task from a benchmark: data plus a list of deliverables to produce
  candidate   one team's version of one deliverable
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Protocol

DIRECTOR = "director"
HEAD = "head"
WORKER = "worker"


@dataclass(frozen=True)
class Agent:
    id: str            # unique, lowercase letters, digits and dashes, for example "team-a-analyst"
    role: str          # DIRECTOR, HEAD or WORKER
    department: str    # the department whose memory this agent writes to
    model: str         # model name passed to the agent runner, for example "opus" or "fable"

    @property
    def reads_everything(self) -> bool:
        return self.role in (DIRECTOR, HEAD)


@dataclass
class Org:
    """Who is in the lab. Built once per run."""
    agents: list[Agent]

    def agent(self, agent_id: str) -> Agent:
        for a in self.agents:
            if a.id == agent_id:
                return a
        raise KeyError(f"no agent called {agent_id}")

    @property
    def departments(self) -> list[str]:
        return sorted({a.department for a in self.agents})

    def can_read(self, agent_id: str, department: str) -> bool:
        a = self.agent(agent_id)
        return a.reads_everything or a.department == department


class EventLog(Protocol):
    """Append-only record of everything that happens. The lab's state is rebuilt from it alone."""

    def append(self, kind: str, actor: str, **data: Any) -> dict:
        """Add one event and return it. Adds `seq` (1, 2, 3 ...) and `time` (UTC, ISO 8601)."""

    def events(self, kind: str | None = None) -> list[dict]:
        """All events in order, or only those of one kind."""


@dataclass
class Fact:
    id: str
    text: str
    department: str | None      # the department it was written to, when known
    author: str | None          # the agent that wrote it, when known
    kind: str = "source"        # past.dev's kind for the result ("source", "claim", "rule" ...)
    came_from: list[str] = field(default_factory=list)   # ids of the facts a pass-down was made from
    occurred_at: str | None = None


class Memory(Protocol):
    """Shared memory with read limits. A worker reads its own department; a head reads all."""

    def setup(self, org: Org) -> None:
        """Create the identities and the per-department read rules for this org."""

    def write(self, agent_id: str, text: str, label: str) -> str:
        """Write one fact into the WRITER'S OWN department and return its id.
        The caller cannot choose the department. An unknown agent is refused."""

    def pass_down(self, head_id: str, to_department: str, text: str, came_from: list[str], reason: str) -> str:
        """A head writes a new fact into another department. Only heads may call this.
        `came_from` lists the facts it is based on; `reason` says why the department needs it."""

    def recall(self, agent_id: str, query: str, limit: int = 20) -> list[Fact]:
        """What this agent is allowed to see that matches the question."""


@dataclass
class AgentResult:
    ok: bool
    result: dict                 # the agent's result.json, already checked to be a JSON object
    seconds: float
    cost_usd: float | None = None
    session_id: str | None = None
    error: str | None = None


class Backend(Protocol):
    """Runs one agent to completion."""

    def run(
        self,
        agent: Agent,
        brief: str,
        workspace: pathlib.Path,
        *,
        readable: list[pathlib.Path] = (),
        env: dict[str, str] | None = None,
        timeout_s: int = 1800,
    ) -> AgentResult:
        """Start the agent in `workspace` with `brief` as its instructions.
        The agent may write only inside `workspace`, may read `workspace` and `readable`,
        and must leave a `result.json` in `workspace`."""


@dataclass
class Study:
    id: str
    title: str
    statement: str               # the task text exactly as the benchmark gives it
    data_dir: pathlib.Path       # read-only inputs
    deliverables: list[str]      # names of the things to produce, for example file names or question ids


class Adapter(Protocol):
    """Everything the lab needs to know about one benchmark."""

    name: str

    def studies(self) -> list[Study]: ...

    def problems(self, study: Study, deliverable: str, candidate: pathlib.Path) -> list[str]:
        """Check a candidate against the benchmark's stated output format, without the answers.
        Returns the problems found; an empty list means the format is right."""

    def agreement(self, study: Study, deliverable: str, a: pathlib.Path, b: pathlib.Path) -> float:
        """How closely two candidates agree, from 0 to 1, using the benchmark's own measure where it has one."""

    def grade(self, study: Study, final_dir: pathlib.Path) -> dict:
        """Score the final deliverables against the answers. Called only after every agent has exited."""
