"""The lab's main loop: one study from library research to a final answer.

For each study S the lab runs, in order (explanation.md, section 1, and D-8 to D-11):

  1. library     `S-library` researches pitfalls with web search through the gateway, writing to its
                 own department `S-library`, so one study's findings never reach another study's teams.
  2. briefing    the director picks what the teams need; the lab passes each item down to the
                 teams in Config.brief_teams only (default team A), so the two teams start from
                 different information (I-11).
  3. analysis    `S-team-a` and `S-team-b` analyse blind, at the same time.
  4. compare     one valid: "single-source". None: "no-answer". Both valid and agreeing: step 4a.
  4a. challenge  the director tries to break the agreed answer and writes questions for both teams.
                 No questions: "replicated". Otherwise the lab delivers them as in step 5, both
                 teams revise, and agreeing again is "replicated"; disagreeing now goes to step 6.
  5. questions   on disagreement the director writes questions from each team's method note;
                 the lab leak-checks each one, delivers those that pass, and both teams revise.
                 Agreeing now: "converged".
  4a/5 research  in either question round the director may also ask up to three research
                 questions. Those that pass the leak check go to the study's library, which answers
                 them from the web (phase "research"); each finding is leak-checked and passed down
                 to both teams before they revise (Config.research; never without the library).
  6. referee     `S-referee` gets the two answers as X and Y and must run a test. A pick: "refereed".
  7. team C      on "neither", a third blind team runs with the questions as warning signs.
                 Agreeing with A or B: "third-team". Otherwise "unresolved".
  8. record      `<run_dir>/<S>/final/<answer file>` and `<run_dir>/<S>/study.json`, event "study.final".

The answer file is the study's first deliverable (answer_file): `answer.json` for GeneBench,
`eval_answer.json` for scBench. Briefs name it through the {answer_file} field.

Web search (version 3): the library searches before the analysis; team A, the briefed team, may
also search while it works (Config.team_search), and team B never does. The gateway enforces
each search budget per agent and per run (Gateway.set_budget).

Blindness: a team's brief holds only the lab's instructions, the task text and the paths of its
own workspace, the data and the agent tools. The only thing that ever crosses from one team to
another is a question that passed the leak check, delivered through memory. After each delivery
the lab waits until each receiving agent can read what it was sent (Memory.wait_visible).

Folders under `<run_dir>/<S>/`: one workspace per agent run (`library`, `director-briefing`,
`director-questions`, `director-challenge`, `library-research`, `team-a`, `team-b`, `team-c`, `referee`),
`candidates/` (the lab's copy of
every answer, which no agent can read), `briefs/` (every brief given), `final/` and `study.json`.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import pathlib
import random
import re
import shutil
import threading
import time
import uuid
from typing import Any

from lab.budget import Budget, BudgetExceeded, prior_spend
from lab.contracts import DIRECTOR, WORKER, Adapter, Agent, AgentResult, Backend, EventLog, Memory, Org, Study
from lab.gateway import Gateway
from lab.limits import UsageLimit   # defined there so lab.budget can subclass it; still importable from here

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "lab" / "prompts"
TOOLS = ROOT / "lab" / "tools"

TEAMS = ("team-a", "team-b", "team-c")
DIVERSITY = {
    "team-a": "Start from the model the task implies and test its assumptions.",
    "team-b": "Start with a full audit of the data files and how they relate before choosing a model.",
    "team-c": "Before choosing a model, check each warning sign passed down to you against the data.",
}
SITUATION = {      # the {situation} line of analyst_revise.md, per round
    "revise": "The other team reached a different answer. You will not be told what it was. The director's "
              "questions come from warning signs the other team recorded and from differences in method.",
    "challenge": "The other team reached the same answer as you. The director does not think agreement alone "
                 "makes it right, and has questions for both teams.",
}
# The analysts' revision brief, when research on demand is on (its {findings_note} field).
FINDINGS_NOTE = ("\n\nThe director may also have passed down findings from the lab's library: answers to questions "
                 "about how a method is defined, what a tool does by default, or which convention a field uses. They "
                 "are in memory too: `python3 {tools}/memory.py recall \"findings from the lab's library\"`. Check each "
                 "one against the data like a question; it is not a hint about the answer.")
# The end of the director's result.json shape, when research on demand is on (its {research_shape} field).
RESEARCH_SHAPE = ', "research_questions": [{"question": "...", "why": "..."}]'
MAX_RESEARCH_QUESTIONS = 3


@dataclasses.dataclass
class Config:
    """Settings for one lab run. The switches exist for ablation and comparison runs."""
    director_model: str = "fable"
    worker_model: str = "fable"
    team_b_model: str | None = None   # None: team B uses worker_model (set it for a mixed run)
    timeouts: dict = dataclasses.field(default_factory=lambda: {
        "library": 900, "director": 600, "analyst": 1800, "referee": 1200, "baseline": 1800, "research": 900})
    max_agents: int = 4          # agents running at once, shared across all studies
    max_searches: int = 12       # Exa calls the library may make in its first pass, per study
    team_searches: int = 8       # Exa calls team A's analyst may make per round
    research_searches: int = 10  # Exa calls the library may make answering research questions
    library: bool = True         # run the library and the director's briefing
    team_search: bool = True     # team A's analyst may search the web while it works
    research: bool = True        # research on demand in the question rounds (only with the library)
    questions: bool = True       # run the director's question round on disagreement
    referee: bool = True         # run the referee before calling team C
    challenge: bool = True       # challenge an agreed answer before reporting it as replicated
    brief_teams: tuple = ("team-a",)   # the first teams that receive the director's briefing
    seed: int | None = None      # for the referee's X/Y order; None means random
    max_usd: float | None = None   # hard dollar cap for the run directory (lab.budget); None means no cap

    def timeout(self, role: str) -> int:
        return int(self.timeouts.get(role, 1800))


# ----- briefs -----

def template(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


def make_brief(name: str, agent: Agent | None, **fields: Any) -> str:
    """Fill a prompt template. Lab agents get common.md in front; pass agent=None for none."""
    fields = {"tools": str(TOOLS), **fields}
    body = template(name).format(**fields)
    if agent is None:
        return body
    head = template("common").format(agent_id=agent.id, department=agent.department, **fields)
    return head.rstrip() + "\n\n" + body


# ----- answers -----

def answer_file(study: Study) -> str:
    """The name of the file a study's answer goes in: its first deliverable."""
    return study.deliverables[0] if study.deliverables else "answer.json"


def library_department(study: Study) -> str:
    """Each study's library has its own department, so its findings stay with that study."""
    return f"{study.id}-library"


def load_doc(path: pathlib.Path) -> Any:
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


def answer_values(path: pathlib.Path | None) -> dict:
    """The answer fields of an answer file: the "answer" object if there is one, else the whole object."""
    doc = load_doc(path) if path else None
    if isinstance(doc, dict) and isinstance(doc.get("answer"), dict):
        return doc["answer"]
    return doc if isinstance(doc, dict) else {}


def field_agreement(adapter: Adapter, study: Study, a: pathlib.Path, b: pathlib.Path) -> dict[str, bool]:
    """Field by field agreement; falls back to the adapter's 0-to-1 measure as one field."""
    if hasattr(adapter, "field_agreement"):
        return {k: bool(v) for k, v in adapter.field_agreement(study, a, b).items()}
    return {"all": adapter.agreement(study, study.deliverables[0], a, b) >= 1.0}


# ----- leak check -----

NUMBER = re.compile(r"(?:\d+(?:,\d{3})+|\d+)(?:\.\d*)?(?:[eE][-+]?\d+)?%?|\.\d+(?:[eE][-+]?\d+)?%?")


def numbers_in(text: str) -> list[float]:
    """Every number written in the text, as a magnitude. "25%" also yields 0.25."""
    out = []
    for raw in NUMBER.findall(text):
        percent = raw.endswith("%")
        try:
            value = abs(float(raw.rstrip("%").replace(",", "")))
        except ValueError:
            continue
        out.append(value)
        if percent:
            out.append(value / 100)
    return out


def _close(x: float, y: float) -> bool:
    return abs(x - y) <= 0.01 * max(abs(x), abs(y))


def _scalars(values: dict) -> dict[str, list]:
    """Field name -> its scalar values (a list field contributes each scalar item)."""
    out = {}
    for key, value in values.items():
        items = value if isinstance(value, list) else [value]
        out[key] = [v for v in items if isinstance(v, (int, float, str)) and not isinstance(v, bool)]
    return out


def leak_check(question: str, own: dict | None, others: list[dict]) -> list[str]:
    """Reasons a question would leak another team's answer to the team receiving it; empty means it passes.

    A number in the question leaks when it is within 1 percent of another team's value for a
    field, unless the receiving team's own value for that field is also within 1 percent of it.
    Numbers are compared by size, so a dropped minus sign still counts. A string value longer
    than one character leaks when it appears in the question (any case), unless the receiving
    team gave the same string for that field.
    """
    own = _scalars(own or {})
    written = numbers_in(question)
    lower = question.lower()
    reasons = []
    for other in others:
        for key, values in _scalars(other).items():
            mine = own.get(key, [])
            for v in values:
                if isinstance(v, str):
                    s = v.strip().lower()
                    if len(s) > 1 and s in lower and s not in [m.strip().lower() for m in mine if isinstance(m, str)]:
                        reasons.append(f'contains another team\'s value for "{key}"')
                    continue
                for n in written:
                    if _close(n, abs(v)) and not any(isinstance(m, (int, float)) and _close(n, abs(m)) for m in mine):
                        reasons.append(f'a number within 1 percent of another team\'s value for "{key}"')
                        break
    return sorted(set(reasons))


# ----- referee scrub -----

def scrub(obj: Any, names: list[str], keep: tuple = ()) -> Any:
    """Drop every field (or list item) whose text names a team. Keys in `keep` are left untouched."""
    pattern = re.compile("|".join(re.escape(n) for n in names) + r"|team[\s_-]*[abc]\b", re.IGNORECASE)

    def names_team(value) -> bool:
        return pattern.search(json.dumps(value, default=str)) is not None

    def clean(value):
        if isinstance(value, dict):
            return {k: (v if k in keep else clean(v)) for k, v in value.items()
                    if k in keep or not (pattern.search(k) or (not isinstance(v, (dict, list)) and names_team(v)))}
        if isinstance(value, list):
            return [clean(v) for v in value if isinstance(v, (dict, list)) or not names_team(v)]
        return value

    return clean(obj)


# ----- running agents -----

class AgentRunner:
    """Runs agents through a backend: a shared limit on how many run at once, one retry, and logging.
    When any agent reports the account's usage limit, every later run is refused: a study must not
    be recorded as "no answer" just because the account was out of usage.

    With a Budget, each run reserves its estimated cost before it starts and settles to its actual
    cost after agent.finish. A run the budget refuses halts the runner exactly like a usage limit
    (BudgetExceeded is a UsageLimit), with a lab.halted event whose reason is "budget"."""

    halted: str | None = None

    def __init__(self, backend: Backend, log: EventLog, max_agents: int, budget: Budget | None = None):
        self.backend = backend
        self.log = log
        self.slots = threading.Semaphore(max(1, int(max_agents)))
        self.budget = budget
        self.halt_error: type[UsageLimit] = UsageLimit   # what every run after a halt raises

    def run(self, agent: Agent, brief: str, workspace: pathlib.Path, *, readable: list[pathlib.Path],
            env: dict | None, timeout_s: int, study: str, phase: str, role: str,
            brief_file: pathlib.Path | None = None) -> AgentResult:
        """Run one agent, retrying once if it fails. Returns the last AgentResult."""
        workspace.mkdir(parents=True, exist_ok=True)
        if brief_file is not None:
            brief_file.parent.mkdir(parents=True, exist_ok=True)
            brief_file.write_text(brief)
        result = AgentResult(ok=False, result={}, seconds=0.0, error="not run")
        for attempt in (1, 2):
            run_id = uuid.uuid4().hex[:12]
            common = {"agent": agent.id, "role": role, "study": study, "phase": phase, "attempt": attempt,
                      "run_id": run_id}
            with self.slots:
                if self.halted:
                    raise self.halt_error(self.halted)
                token = self.reserve(agent, role, study)
                self.log.append("agent.start", agent.id, **common, model=agent.model, workspace=str(workspace),
                                readable=[str(p) for p in readable], timeout_s=timeout_s)
                t0 = time.time()
                try:
                    result = self.backend.run(agent, brief, workspace, readable=list(readable), env=env,
                                              timeout_s=timeout_s)
                    if not isinstance(result.result, dict):
                        result = dataclasses.replace(result, ok=False, result={}, error="result is not a JSON object")
                except Exception as err:    # a backend crash is a failed run, not a crashed lab
                    result = AgentResult(ok=False, result={}, seconds=time.time() - t0,
                                         error=f"{type(err).__name__}: {err}"[:500])
                try:
                    self.log.append("agent.finish", agent.id, **common, ok=bool(result.ok),
                                    seconds=round(float(result.seconds or 0), 2), cost_usd=result.cost_usd,
                                    session_id=result.session_id, error=result.error)
                finally:
                    if token is not None:
                        self.budget.settle(token, result.cost_usd)   # a missing cost keeps the estimate
            if "usage limit" in (result.error or ""):
                self.halted = result.error
                self.log.append("lab.halted", "lab", reason=result.error, agent=agent.id, study=study)
                raise UsageLimit(result.error)
            if result.ok or "timed out" in (result.error or ""):
                break      # a timed-out run is not retried: it would only spend the same time again
        return result

    def reserve(self, agent: Agent, role: str, study: str) -> int | None:
        """Reserve one run's estimated cost. On BudgetExceeded, halt like a usage limit and re-raise."""
        if self.budget is None:
            return None
        try:
            return self.budget.reserve(role, agent.model)
        except BudgetExceeded as err:
            self.halted, self.halt_error = str(err), BudgetExceeded
            self.log.append("lab.halted", "lab", reason="budget", detail=str(err), agent=agent.id, study=study,
                            spent_usd=round(self.budget.spent(), 4), reserved_usd=round(self.budget.reserved(), 4),
                            max_usd=self.budget.max_usd)
            raise


def unfinished_agents(log: EventLog) -> list[dict]:
    """agent.start events with no finish: no agent.finish with the same run_id, and none later
    for the same agent, study and phase (a later re-run that finished covers a crashed one)."""
    finished_ids, last_finish = set(), {}
    for e in log.events("agent.finish"):
        finished_ids.add(e.get("run_id"))
        last_finish[(e.get("agent"), e.get("study"), e.get("phase"))] = e["seq"]
    return [e for e in log.events("agent.start")
            if e.get("run_id") not in finished_ids
            and last_finish.get((e.get("agent"), e.get("study"), e.get("phase")), 0) < e["seq"]]


# ----- one candidate answer -----

@dataclasses.dataclass
class Candidate:
    team: str          # "team-a", "team-b", "team-c" or "referee"
    agent: str
    round: str         # "analysis", "revise", "third-team" or "referee"
    path: pathlib.Path  # the lab's copy, outside every workspace
    problems: list
    values: dict

    @property
    def valid(self) -> bool:
        return not self.problems

    def record(self) -> dict:
        return {"team": self.team, "agent": self.agent, "round": self.round, "path": str(self.path),
                "valid": self.valid, "problems": self.problems, "answer": self.values}


# ----- the lab -----

class Lab:
    """Runs studies through the protocol. One instance per run directory."""

    def __init__(self, adapter: Adapter, backend: Backend, memory: Memory, log: EventLog,
                 run_dir: pathlib.Path, config: Config | None = None, *, exa_call=None, gateway_factory=None):
        self.adapter = adapter
        # Builds the gateway with Gateway's arguments; lab.modal_gateway.ModalGateway runs it on Modal.
        self.gateway_factory = gateway_factory or Gateway
        self.memory = memory
        self.log = log
        self.run_dir = pathlib.Path(run_dir).resolve()   # briefs carry paths; agents run in their own folder
        self.config = config or Config()
        self.exa_call = exa_call
        # A dollar cap counts what this run directory spent before (a resumed run) against the cap.
        budget = Budget(self.config.max_usd, prior_spend(log)) if self.config.max_usd is not None else None
        self.runner = AgentRunner(backend, log, self.config.max_agents, budget=budget)
        self.rng = random.Random(self.config.seed)
        self.org: Org | None = None
        self.gateway: Gateway | None = None
        self.gateway_url = ""
        self.tokens: dict[str, str] = {}
        self.delivered: set[str] = set()     # fact ids the lab itself passed down
        self.blocked_terms: list[str] = []   # what identifies a task's source; research questions may not hold it

    def build_org(self, studies: list[Study]) -> Org:
        cfg = self.config
        agents = [Agent("director", DIRECTOR, "director", cfg.director_model)]
        for s in studies:
            agents.append(Agent(f"{s.id}-library", WORKER, library_department(s), cfg.worker_model))
            for team in TEAMS:
                model = (cfg.team_b_model or cfg.worker_model) if team == "team-b" else cfg.worker_model
                agents.append(Agent(f"{s.id}-{team}", WORKER, f"{s.id}-{team}", model))
            agents.append(Agent(f"{s.id}-referee", WORKER, f"{s.id}-referee", cfg.worker_model))
        return Org(agents=agents)

    def finished(self) -> dict[str, dict]:
        return {e["study"]: e for e in self.log.events("study.final")}

    def run(self, studies: list[Study]) -> dict[str, dict]:
        """Run every study not finished already. Returns study id -> {"status", ...}."""
        done = self.finished()
        out = {}
        for s in studies:
            if s.id in done:
                self.log.append("study.skipped", "lab", study=s.id, reason="already has study.final")
                out[s.id] = {"status": done[s.id]["status"], "skipped": True}
        todo = [s for s in studies if s.id not in done]
        if not todo:
            return out
        self.org = self.build_org(studies)
        self.memory.setup(self.org)
        # Agents may not pass facts down themselves: the lab delivers every pass-down after its leak check.
        # Who may search: each study's library, and its team A when team search is on. Each has a cap.
        caps = {}
        for s in studies:
            if self.config.library:
                caps[f"{s.id}-library"] = self.config.max_searches
            if self.config.team_search:
                caps[f"{s.id}-team-a"] = self.config.team_searches
        departments = sorted({self.org.agent(a).department for a in caps})
        kwargs = {"exa_departments": departments, "exa_caps": caps, "agents_may_pass_down": False}
        if hasattr(self.adapter, "blocked_terms"):
            self.blocked_terms = [t.lower() for t in self.adapter.blocked_terms() if t]
            kwargs["blocked_terms"] = self.adapter.blocked_terms()
        if self.exa_call is not None:
            kwargs["exa_call"] = self.exa_call
        self.gateway = self.gateway_factory(self.org, self.memory, self.log, **kwargs)
        self.gateway_url = self.gateway.start()
        try:
            self.tokens = {a.id: self.gateway.issue_token(a.id) for a in self.org.agents}
        except BaseException:
            self.gateway.stop()
            raise
        self.log.append("lab.start", "lab", studies=[s.id for s in todo], config=dataclasses.asdict(self.config))
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(todo)) as pool:
                futures = {pool.submit(StudyRun(self, s).run): s for s in todo}
                for fut in concurrent.futures.as_completed(futures):
                    s = futures[fut]
                    try:
                        out[s.id] = fut.result()
                    except Exception as err:
                        self.log.append("study.error", "lab", study=s.id, error=f"{type(err).__name__}: {err}"[:500])
                        out[s.id] = {"status": "error", "error": f"{type(err).__name__}: {err}"}
        finally:
            self.gateway.stop()
        return out

    def env(self, agent: Agent) -> dict:
        # On Modal every agent's connect token comes with its own URL.
        url = self.gateway.url_for(agent.id) if hasattr(self.gateway, "url_for") else self.gateway_url
        return {"LAB_GATEWAY_URL": url, "LAB_GATEWAY_TOKEN": self.tokens[agent.id]}

    def open_budget(self, agent_id: str, cap: int, label: str) -> None:
        """Give an agent a fresh search budget on the gateway before a run that may search."""
        if hasattr(self.gateway, "set_budget"):
            self.gateway.set_budget(agent_id, cap, label)

    def wait_settled(self) -> None:
        """Wait until past.dev has derived memory from every write. Slow (minutes); the loop no longer uses it."""
        if hasattr(self.gateway, "sync"):     # a remote gateway: fetch its latest writes first
            self.gateway.sync()
        if hasattr(self.memory, "wait_settled"):
            self.memory.wait_settled()


class StudyRun:
    """The state of one study while it runs, and the steps of the protocol."""

    def __init__(self, lab: Lab, study: Study):
        self.lab = lab
        self.study = study
        self.cfg = lab.config
        self.dir = lab.run_dir / study.id
        self.deliverable = answer_file(study)
        self.briefed = [t for t in ("team-a", "team-b") if t in self.cfg.brief_teams]
        self.research_on = bool(self.cfg.research and self.cfg.library)
        self.findings_delivered = 0
        names = ["director", f"{study.id}-library", *(f"{study.id}-{t}" for t in TEAMS), f"{study.id}-referee"]
        self.record: dict = {"study": study.id, "status": None, "final": None, "switches": {
            "library": self.cfg.library, "questions": self.cfg.questions, "referee": self.cfg.referee,
            "challenge": self.cfg.challenge, "brief_teams": list(self.cfg.brief_teams),
            "team_search": self.cfg.team_search, "research": self.research_on},
            "models": {n: lab.org.agent(n).model for n in names},
            "briefed_teams": self.briefed if self.cfg.library else [],
            "candidates": [], "comparisons": [], "briefing": [], "questions": [], "warning_signs_to_team_c": [],
            "challenged": False, "challenge_questions": None, "challenge_changed_answer": None, "challenge": None,
            "referee": None, "research": [], "not_chosen": [], "agent_runs": [], "unsanctioned_pass_downs": []}
        self.candidates: list[Candidate] = []
        self.start_seq = 0

    # ----- helpers -----

    def agent(self, suffix: str) -> Agent:
        return self.lab.org.agent("director" if suffix == "director" else f"{self.study.id}-{suffix}")

    def dept(self, team: str) -> str:
        return f"{self.study.id}-{team}"

    def log(self, kind: str, **data) -> dict:
        return self.lab.log.append(kind, "lab", study=self.study.id, **data)

    def run_agent(self, agent: Agent, brief: str, folder: str, readable: list, phase: str, role: str) -> AgentResult:
        result = self.lab.runner.run(agent, brief, self.dir / folder, readable=[pathlib.Path(p) for p in readable],
                                     env=self.lab.env(agent), timeout_s=self.cfg.timeout(role), study=self.study.id,
                                     phase=phase, role=role, brief_file=self.dir / "briefs" / f"{phase}-{folder}.md")
        self.record["agent_runs"].append({"agent": agent.id, "phase": phase, "ok": result.ok,
                                          "seconds": result.seconds, "cost_usd": result.cost_usd, "error": result.error})
        return result

    def fields(self, **extra) -> dict:
        """Template fields every brief may use."""
        s = self.study
        return {"statement": s.statement, "data_dir": str(s.data_dir), "max_searches": self.cfg.max_searches,
                "team_searches": self.cfg.team_searches, "research_searches": self.cfg.research_searches,
                "search": "", "findings_note": "", "research": "", "research_shape": "",
                "study_id": s.id,
                "time_limit_minutes": self.cfg.timeout("analyst") // 60, "answer_file": self.deliverable,
                "library_department": library_department(s),
                "team_departments": f"{self.dept('team-a')}, {self.dept('team-b')}", **extra}

    def take(self, team: str, round_: str, result: AgentResult, workspace: pathlib.Path) -> Candidate | None:
        """Copy an agent's answer out of its workspace and check its format. None if the run failed."""
        if not result.ok:
            # An agent stopped by its time limit may still have left an answer file (it is told to write one early).
            if "timed out" in (result.error or "") and (workspace / self.deliverable).is_file():
                self.log("candidate.salvaged", team=team, round=round_, reason=result.error)
            else:
                self.log("candidate.none", team=team, round=round_, reason=result.error or "run failed")
                return None
        name = result.result.get("answer_file") or self.deliverable
        source = (workspace / str(name)).resolve()
        if not source.is_relative_to(workspace.resolve()) or not source.is_file():
            source = workspace / self.deliverable
        store = self.dir / "candidates"
        store.mkdir(parents=True, exist_ok=True)
        path = store / f"{team}-{round_}.json"
        if source.is_file():
            shutil.copyfile(source, path)
        else:
            path.write_text("")
        if (workspace / "result.json").is_file():
            shutil.copyfile(workspace / "result.json", store / f"{team}-{round_}-result.json")
        problems = list(self.lab.adapter.problems(self.study, self.deliverable, path)) if source.is_file() \
            else ["no answer file"]
        cand = Candidate(team, self.agent(team).id if team != "referee" else self.agent("referee").id,
                         round_, path, problems, answer_values(path) if not problems else {})
        self.candidates.append(cand)
        self.record["candidates"].append(cand.record())
        self.log("candidate", team=team, round=round_, valid=cand.valid, problems=problems)
        return cand

    def compare(self, x: Candidate, y: Candidate, round_: str) -> bool:
        fields = field_agreement(self.lab.adapter, self.study, x.path, y.path)
        agree = bool(fields) and all(fields.values())
        self.record["comparisons"].append({"round": round_, "between": [x.agent, y.agent], "fields": fields,
                                           "agree": agree})
        self.log("compare", round=round_, between=[x.agent, y.agent], fields=fields, agree=agree)
        return agree

    def pass_down(self, to_department: str, text: str, came_from: list, reason: str) -> str:
        fact_id = self.lab.memory.pass_down("director", to_department, text, [str(c) for c in came_from], reason)
        self.lab.delivered.add(fact_id)
        return fact_id

    def wait_visible(self, waits: dict[str, list[str]]) -> None:
        """For each agent, wait until it can read the facts just delivered to it. Missing ones are logged, not fatal."""
        for agent_id, fact_ids in waits.items():
            if not fact_ids:
                continue
            missing = self.lab.memory.wait_visible(agent_id, list(fact_ids))
            if missing:
                self.log("relay.not_visible", agent=agent_id, missing=missing)

    def library_facts(self, since: int | None = None) -> list[str]:
        """Ids of the facts the library wrote in this study (or after event `since`). A remote gateway's
        writes are fetched first."""
        if hasattr(self.lab.gateway, "sync"):
            self.lab.gateway.sync()
        library = self.agent("library").id
        after = self.start_seq if since is None else since
        return [e["fact_id"] for e in self.lab.log.events("memory.write")
                if e.get("actor") == library and e["seq"] > after and e.get("fact_id")]

    def team_notes(self, ws: pathlib.Path) -> pathlib.Path:
        """Copy both teams' result.json (method notes, never answer files) into `ws/teams`."""
        teams_dir = ws / "teams"
        teams_dir.mkdir(parents=True, exist_ok=True)
        for team in ("team-a", "team-b"):
            note = self.dir / team / "result.json"
            if note.is_file():
                shutil.copyfile(note, teams_dir / f"{self.dept(team)}.json")
        return teams_dir

    # ----- steps -----

    def library(self) -> None:
        agent = self.agent("library")
        self.lab.open_budget(agent.id, self.cfg.max_searches, "library")
        brief = make_brief("library", agent, **self.fields())
        self.run_agent(agent, brief, "library", [self.study.data_dir, TOOLS], "library", "library")

    def briefing(self) -> list[dict]:
        director = self.agent("director")
        brief = make_brief("director_relay", director,
                           **self.fields(team_departments=", ".join(self.dept(t) for t in self.briefed)))
        result = self.run_agent(director, brief, "director-briefing", [TOOLS], "briefing", "director")
        items = result.result.get("pass_down") if result.ok else None
        kept = []
        for item in items if isinstance(items, list) else []:
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            came_from = item.get("from") if isinstance(item.get("from"), list) else []
            reason = item.get("reason") if isinstance(item.get("reason"), str) and item["reason"].strip() \
                else "the director judged the teams need it"
            ids = {team: self.pass_down(self.dept(team), text, came_from, reason) for team in self.briefed}
            entry = {"text": text, "from": came_from, "reason": reason, "fact_ids": ids}
            kept.append(entry)
            self.record["briefing"].append(entry)
        self.log("relay.briefing", items=len(kept), teams=self.briefed)
        self.wait_visible({self.agent(t).id: [e["fact_ids"][t] for e in kept] for t in self.briefed})
        return kept

    def analyse(self, team: str, template_name: str = "analyst", round_: str = "analysis",
                phase: str = "analysis") -> Candidate | None:
        """Run one team. A revising round ("revise" or "challenge") gets that round's {situation} line.
        Team A gets the search section, and a fresh search budget, when team search is on."""
        agent = self.agent(team)
        extra = {}
        if team == "team-a" and self.cfg.team_search:
            extra["search"] = "\n\n" + make_brief("team_search", None, **self.fields()).strip()
            self.lab.open_budget(agent.id, self.cfg.team_searches, "team-a")
        if template_name == "analyst_revise" and self.research_on:
            extra["findings_note"] = FINDINGS_NOTE.format(tools=TOOLS)
        brief = make_brief(template_name, agent, **self.fields(diversity=DIVERSITY[team],
                                                               situation=SITUATION.get(round_, ""), **extra))
        result = self.run_agent(agent, brief, team, [self.study.data_dir, TOOLS], phase, "analyst")
        return self.take(team, round_, result, self.dir / team)

    def both(self, fn, *args) -> tuple:
        """Run fn("team-a", ...) and fn("team-b", ...) at the same time."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(fn, "team-a", *args)
            fb = pool.submit(fn, "team-b", *args)
            return fa.result(), fb.result()

    def questions(self, a: Candidate, b: Candidate) -> list[dict]:
        """The director's question round: write, leak-check and deliver. Returns every question."""
        director = self.agent("director")
        teams_dir = self.team_notes(self.dir / "director-questions")
        brief = make_brief("director_crossexam", director, **self.fields(teams_dir=str(teams_dir),
                                                                         **self.research_fields()))
        result = self.run_agent(director, brief, "director-questions", [TOOLS], "questions", "director")
        doc = result.result if result.ok else {}
        answers = {self.dept("team-a"): (a.values, b.values), self.dept("team-b"): (b.values, a.values)}
        out = self.deliver(doc.get("questions"), answers, "questions",
                           "question from the director after the teams' answers differed")
        self.record["questions"].extend(out)
        self.research(doc, a, b, answers, "questions")
        return out

    def differing(self, own: Candidate, other: Candidate) -> dict:
        """The other team's values for the fields where it differs from `own`: the only ones that could leak."""
        fields = field_agreement(self.lab.adapter, self.study, own.path, other.path)
        if set(fields) == {"all"}:
            return {} if fields["all"] else dict(other.values)
        return {k: v for k, v in other.values.items() if fields.get(k) is not True}

    def challenge(self, a: Candidate, b: Candidate) -> list[dict]:
        """The director challenges an agreed answer. Records its findings, delivers its questions, returns them."""
        director = self.agent("director")
        teams_dir = self.team_notes(self.dir / "director-challenge")
        brief = make_brief("director_challenge", director, **self.fields(teams_dir=str(teams_dir),
                                                                         **self.research_fields()))
        result = self.run_agent(director, brief, "director-challenge", [self.study.data_dir, TOOLS], "challenge",
                                "director")
        doc = result.result if result.ok else {}
        found = {k: doc.get(k) if isinstance(doc.get(k), list) else []
                 for k in ("shared_assumptions", "unused_evidence", "unresolved_weak_points", "second_readings")}
        verdict = doc.get("verdict") if result.ok else f"challenge run failed: {result.error}"
        # The teams agree, so a team's own value is no secret: check each question only against the
        # other team's values for fields where the two differ (none, when they agree on every field).
        answers = {self.dept("team-a"): (a.values, self.differing(a, b)),
                   self.dept("team-b"): (b.values, self.differing(b, a))}
        out = self.deliver(doc.get("questions"), answers, "challenge",
                           "question from the director challenging the answer both teams agreed on")
        self.record["challenge"] = {"ran": bool(result.ok), **found, "verdict": verdict, "questions": out}
        self.research(doc, a, b, answers, "challenge")
        return out

    # ----- research on demand -----

    def research_fields(self) -> dict:
        """The director brief's research section and result.json shape, or nothing when research is off."""
        if not self.research_on:
            return {}
        section = make_brief("director_research", None, **self.fields()).strip()
        return {"research": "\n\n" + section, "research_shape": RESEARCH_SHAPE}

    def names_source(self, text: str) -> bool:
        """Whether the text holds a term that identifies this task's source (the adapter's blocked terms)."""
        lower = text.lower()
        return any(term in lower for term in self.lab.blocked_terms)

    def research(self, doc: dict, a: Candidate, b: Candidate, answers: dict[str, tuple[dict, dict]], round_: str) -> int:
        """Research on demand. Leak-check the director's research questions against both teams' answers
        (and the task's source), run the library on those that pass, then leak-check each finding like a
        question and pass it down to both teams under the director's name, and wait until each team can
        read it. Records everything under "research". Returns how many deliveries were made."""
        raw = doc.get("research_questions") if isinstance(doc, dict) else None
        if not self.research_on or not isinstance(raw, list):
            return 0
        asked = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("question"), str) or not item["question"].strip():
                continue
            question, why = item["question"].strip(), str(item.get("why") or "").strip()
            reasons = leak_check(f"{question} {why}", None, [a.values, b.values])
            if self.names_source(f"{question} {why}"):
                reasons.append("names something that identifies this task's source")
            if sum(q["passed"] for q in asked) >= MAX_RESEARCH_QUESTIONS:
                reasons = [f"more than {MAX_RESEARCH_QUESTIONS} research questions"]
            passed = not reasons
            self.log("research.leak_check", round=round_, passed=passed, reasons=reasons, question=question)
            asked.append({"question": question, "why": why, "passed": passed, "reasons": reasons})
        if not asked:
            return 0
        entry = {"round": round_, "questions": asked, "ran": False, "error": None, "findings": []}
        self.record["research"].append(entry)
        sent = [q for q in asked if q["passed"]]
        if not sent:
            return 0
        agent = self.agent("library")
        self.lab.open_budget(agent.id, self.cfg.research_searches, "research")
        listing = "\n".join(f"{i}. {q['question']}" + (f"\n   Why it matters: {q['why']}" if q["why"] else "")
                            for i, q in enumerate(sent, 1))
        brief = make_brief("library_research", agent, **self.fields(research_questions=listing))
        since = self.log("research.start", round=round_, questions=len(sent))["seq"]
        result = self.run_agent(agent, brief, "library-research", [TOOLS], "research", "research")
        entry["ran"], entry["error"] = bool(result.ok), None if result.ok else result.error
        came_from = self.library_facts(since)
        found = result.result.get("findings") if result.ok else None
        waits: dict[str, list[str]] = {}
        delivered = 0
        for f in found if isinstance(found, list) else []:
            if not isinstance(f, dict) or not isinstance(f.get("answer"), str) or not f["answer"].strip():
                continue
            sources = [str(x) for x in f.get("sources") or [] if isinstance(x, str)] \
                if isinstance(f.get("sources"), list) else []
            finding = {"question": str(f.get("question") or ""), "answer": f["answer"].strip(), "sources": sources,
                       "confidence": f.get("confidence"), "deliveries": {}}
            finding["text"] = (f"FINDING FROM THE LAB'S LIBRARY, asked for by the director. QUESTION: "
                               f"{finding['question']} ANSWER: {finding['answer']} HOW SURE: {finding['confidence']}. "
                               f"SOURCES: {', '.join(sources) or 'none given'}.")
            for dept, (own, other) in answers.items():
                reasons = leak_check(finding["text"], own, [other])
                passed = not reasons
                self.log("relay.leak_check", round="research", to=dept, passed=passed, reasons=reasons,
                         question=finding["text"])
                fact_id = self.pass_down(dept, finding["text"], came_from,
                                         "a research finding the director asked the library for") if passed else None
                finding["deliveries"][dept] = {"delivered": passed, "reasons": reasons, "fact_id": fact_id}
                if passed:
                    waits.setdefault(dept, []).append(fact_id)   # a team's agent id is its department
                    delivered += 1
            entry["findings"].append(finding)
        self.wait_visible(waits)
        self.log("research.delivered", round=round_, findings=len(entry["findings"]), deliveries=delivered)
        self.findings_delivered += delivered
        return delivered

    def deliver(self, raw, answers: dict[str, tuple[dict, dict]], round_: str, reason: str) -> list[dict]:
        """Leak-check each question the director wrote and pass down those that pass, then wait until
        each receiving team can read its questions. `answers` maps a team department to (its own
        values, the values it must not learn). Returns one entry per question."""
        out = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("question"), str) or not item["question"].strip():
                continue
            to = str(item.get("to") or "").strip()
            if to not in answers:
                matches = [d for d in answers if d.endswith(to) and to]
                to = matches[0] if len(matches) == 1 else to
            came_from = item.get("from") if isinstance(item.get("from"), list) else []
            entry = {"to": to, "question": item["question"], "from": came_from, "delivered": False,
                     "reasons": [], "fact_id": None}
            if to not in answers:
                entry["reasons"] = [f"no team department called {to!r}"]
            else:
                own, other = answers[to]
                entry["reasons"] = leak_check(item["question"], own, [other])
            passed = not entry["reasons"]
            self.log("relay.leak_check", round=round_, to=to, passed=passed, reasons=entry["reasons"],
                     question=item["question"])
            if passed:
                entry["fact_id"] = self.pass_down(to, item["question"], came_from, reason)
                entry["delivered"] = True
            out.append(entry)
        waits: dict[str, list[str]] = {}
        for entry in out:
            if entry["delivered"]:
                waits.setdefault(entry["to"], []).append(entry["fact_id"])   # a team's agent id is its department
        self.wait_visible(waits)
        return out

    def referee(self, a: Candidate, b: Candidate) -> dict:
        """Give the referee the two answers as X and Y in random order. Returns its verdict."""
        agent = self.agent("referee")
        ws = self.dir / "referee"
        order = [a, b]
        self.lab.rng.shuffle(order)
        mapping = {"X": order[0].agent, "Y": order[1].agent}
        self.log("referee.assign", mapping=mapping)      # only the log knows which is which
        names = [self.dept(t) for t in TEAMS] + [self.study.id + "-" + t for t in TEAMS]
        for label, cand in zip(("X", "Y"), order):
            folder = ws / "candidates" / label
            folder.mkdir(parents=True, exist_ok=True)
            doc = load_doc(cand.path)
            (folder / self.deliverable).write_text(json.dumps(scrub(doc, names, keep=("answer",)), indent=2))
            note = load_doc(self.dir / "candidates" / f"{cand.team}-{cand.round}-result.json")
            (folder / "method.json").write_text(json.dumps(scrub(note if isinstance(note, dict) else {}, names), indent=2))
        brief = make_brief("referee", agent, **self.fields(candidates_dir=str(ws / "candidates"),
                                                           time_limit_minutes=self.cfg.timeout("referee") // 60))
        result = self.run_agent(agent, brief, "referee", [self.study.data_dir, TOOLS], "referee", "referee")
        verdict = dict(result.result) if result.ok else {"pick": "neither", "reason": f"referee run failed: {result.error}"}
        pick = str(verdict.get("pick") or "").strip().upper()
        verdict["pick"] = pick if pick in ("X", "Y") else "neither"
        verdict["picked_agent"] = mapping.get(verdict["pick"])
        own = verdict.get("own_answer")
        verdict["own_answer_valid"] = False
        if isinstance(own, dict) and own:
            path = self.dir / "candidates" / "referee-own.json"
            path.write_text(json.dumps(own, indent=2))
            problems = list(self.lab.adapter.problems(self.study, self.deliverable, path))
            cand = Candidate("referee", agent.id, "referee", path, problems, answer_values(path) if not problems else {})
            self.candidates.append(cand)
            self.record["candidates"].append(cand.record())
            verdict["own_answer_valid"] = cand.valid
        self.record["referee"] = {**verdict, "mapping": mapping}
        self.log("referee.verdict", pick=verdict["pick"], picked_agent=verdict["picked_agent"],
                 own_answer_valid=verdict["own_answer_valid"])
        return verdict

    def team_c(self, briefing: list[dict], questions: list[dict], a: Candidate, b: Candidate) -> Candidate | None:
        """Brief team C like the first two, add every question as a warning sign, and run it blind."""
        dept = self.dept("team-c")
        sent = [self.pass_down(dept, item["text"], item["from"], item["reason"]) for item in briefing]
        for q in questions:
            reasons = leak_check(q["question"], None, [a.values, b.values])   # C has no answer of its own
            passed = not reasons
            self.log("relay.leak_check", round="team-c", to=dept, passed=passed, reasons=reasons, question=q["question"])
            entry = {"question": q["question"], "delivered": passed, "reasons": reasons, "fact_id": None}
            if passed:
                entry["fact_id"] = self.pass_down(dept, "WARNING SIGN TO CHECK: " + q["question"], q["from"],
                                                  "raised when two earlier teams disagreed; check it on the data")
                sent.append(entry["fact_id"])
            self.record["warning_signs_to_team_c"].append(entry)
        self.wait_visible({self.agent("team-c").id: sent})
        return self.analyse("team-c", round_="third-team", phase="third-team")

    # ----- the flow -----

    def run(self) -> dict:
        if self.dir.exists():    # an earlier run of this study stopped before study.final
            self.dir.rename(self.dir.with_name(f"{self.study.id}.stale-{time.strftime('%Y%m%d%H%M%S')}"))
        self.dir.mkdir(parents=True)
        self.start_seq = self.log("study.start")["seq"]
        briefing = []
        if self.cfg.library:
            self.library()
            self.wait_visible({self.agent("director").id: self.library_facts()})
            briefing = self.briefing()
        a, b = self.both(self.analyse)
        va, vb = bool(a and a.valid), bool(b and b.valid)
        if not (va and vb):
            if va or vb:
                return self.finish("single-source", a if va else b)
            return self.finish("no-answer", None)

        questions = []
        if self.compare(a, b, "analysis"):
            if not self.cfg.challenge:
                return self.finish("replicated", a)
            first = a
            delivered = [q for q in self.challenge(a, b) if q["delivered"]]
            self.record.update(challenged=True, challenge_questions=len(delivered))
            if not delivered and not self.findings_delivered:
                return self.finish("replicated", a)
            a, b = self.revised(a, b, "challenge")
            if self.compare(a, b, "challenge"):
                changed = not all(field_agreement(self.lab.adapter, self.study, first.path, a.path).values())
                self.record["challenge_changed_answer"] = changed
                return self.finish("replicated", a)
        elif self.cfg.questions:
            questions = self.questions(a, b)
            a, b = self.revised(a, b, "revise")
            if self.compare(a, b, "revise"):
                return self.finish("converged", a)

        verdict = self.referee(a, b) if self.cfg.referee else {"pick": "neither", "own_answer_valid": False}
        if verdict["pick"] in ("X", "Y"):
            chosen = a if verdict["picked_agent"] == a.agent else b
            return self.finish("refereed", chosen, verdict=verdict)

        c = self.team_c(briefing, questions, a, b)
        if c and c.valid and (self.compare(c, a, "third-team") or self.compare(c, b, "third-team")):
            return self.finish("third-team", c)
        own = next((x for x in self.candidates if x.team == "referee" and x.valid), None)
        return self.finish("unresolved", own or (c if c and c.valid else None), verdict=verdict)

    def revised(self, a: Candidate, b: Candidate, round_: str) -> tuple[Candidate, Candidate]:
        """Both teams answer the director's questions. A team whose revised answer is not valid keeps its first."""
        a2, b2 = self.both(self.analyse, "analyst_revise", round_, round_)
        return (a2 if a2 and a2.valid else a), (b2 if b2 and b2.valid else b)

    # ----- the record -----

    def reason_not_chosen(self, cand: Candidate, status: str, final: Candidate | None, verdict: dict | None) -> str:
        if not cand.valid:
            return "answer failed the format check: " + "; ".join(cand.problems)
        latest = [x for x in self.candidates if x.team == cand.team and x.valid][-1]
        if latest is not cand:
            return f"replaced by the same team's {latest.round} answer"
        if status in ("replicated", "converged"):
            return f"agreed with {final.agent}; that team's file is the reported copy"
        if status == "refereed":
            why = (verdict or {}).get("reason") or "no reason given"
            return f"the referee's test supported the other answer: {why}"
        if status == "third-team":
            agreed = any(c["agree"] and cand.agent in c["between"] for c in self.record["comparisons"]
                         if c["round"] == "third-team")
            return (f"agreed with {final.agent}, whose blind answer is reported" if agreed
                    else f"{final.agent}'s blind answer agreed with the other team")
        if status == "unresolved":
            if cand.team == "referee":
                return "the referee's own answer was not used"
            return "no two teams agreed and the referee picked neither" + (
                f"; the reported answer is {final.agent}'s" if final else "")
        return "not used"

    def finish(self, status: str, final: Candidate | None, verdict: dict | None = None) -> dict:
        final_dir = self.dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        if final is not None:
            shutil.copyfile(final.path, final_dir / self.deliverable)
            self.record["final"] = {"agent": final.agent, "team": final.team, "round": final.round,
                                    "path": str(final_dir / self.deliverable)}
        self.record["status"] = status
        self.record["not_chosen"] = [{"agent": c.agent, "round": c.round,
                                      "reason": self.reason_not_chosen(c, status, final, verdict)}
                                     for c in self.candidates if c is not final]
        team_depts = {self.dept(t) for t in TEAMS}
        self.record["unsanctioned_pass_downs"] = [
            {"fact_id": e["fact_id"], "to": e["to_department"], "text": e["text"]}
            for e in self.lab.log.events("memory.pass_down")
            if e["seq"] > self.start_seq and e["to_department"] in team_depts and e["fact_id"] not in self.lab.delivered]
        for item in self.record["unsanctioned_pass_downs"]:
            self.log("relay.unsanctioned", **item)
        (self.dir / "study.json").write_text(json.dumps(self.record, indent=2, default=str))
        self.log("study.final", status=status, final_agent=final.agent if final else None,
                 final_round=final.round if final else None)
        return {"status": status, "final": self.record["final"]}
