"""The lab's main loop: one study from library research to a final answer.

For each study S the lab runs, in order (explanation.md, section 1, and D-8 to D-11):

  1. library     `S-library` researches pitfalls with web search through the gateway.
  2. briefing    the director picks what the teams need; the lab passes each item down.
  3. analysis    `S-team-a` and `S-team-b` analyse blind, at the same time.
  4. compare     both valid and agreeing: "replicated". One valid: "single-source". None: "no-answer".
  5. questions   on disagreement the director writes questions from each team's method note;
                 the lab leak-checks each one, delivers those that pass, and both teams revise.
                 Agreeing now: "converged".
  6. referee     `S-referee` gets the two answers as X and Y and must run a test. A pick: "refereed".
  7. team C      on "neither", a third blind team runs with the questions as warning signs.
                 Agreeing with A or B: "third-team". Otherwise "unresolved".
  8. record      `<run_dir>/<S>/final/answer.json` and `<run_dir>/<S>/study.json`, event "study.final".

Blindness: a team's brief holds only the lab's instructions, the task text and the paths of its
own workspace, the data and the agent tools. The only thing that ever crosses from one team to
another is a question that passed the leak check, delivered through memory.

Folders under `<run_dir>/<S>/`: one workspace per agent run (`library`, `director-briefing`,
`director-questions`, `team-a`, `team-b`, `team-c`, `referee`), `candidates/` (the lab's copy of
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

from lab.contracts import DIRECTOR, WORKER, Adapter, Agent, AgentResult, Backend, EventLog, Memory, Org, Study
from lab.gateway import Gateway

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "lab" / "prompts"
TOOLS = ROOT / "lab" / "tools"

TEAMS = ("team-a", "team-b", "team-c")
DIVERSITY = {
    "team-a": "Start from the model the task implies and test its assumptions.",
    "team-b": "Start with a full audit of the data files and how they relate before choosing a model.",
    "team-c": "Before choosing a model, check each warning sign passed down to you against the data.",
}


@dataclasses.dataclass
class Config:
    """Settings for one lab run. The three switches exist for ablation runs."""
    director_model: str = "fable"
    worker_model: str = "opus"
    timeouts: dict = dataclasses.field(default_factory=lambda: {
        "library": 900, "director": 600, "analyst": 1800, "referee": 1200, "baseline": 1800})
    max_agents: int = 4          # agents running at once, shared across all studies
    max_searches: int = 15       # web search calls the library may make per study
    library: bool = True         # run the library and the director's briefing
    questions: bool = True       # run the director's question round on disagreement
    referee: bool = True         # run the referee before calling team C
    seed: int | None = None      # for the referee's X/Y order; None means random

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
    """Runs agents through a backend: a shared limit on how many run at once, one retry, and logging."""

    def __init__(self, backend: Backend, log: EventLog, max_agents: int):
        self.backend = backend
        self.log = log
        self.slots = threading.Semaphore(max(1, int(max_agents)))

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
                self.log.append("agent.finish", agent.id, **common, ok=bool(result.ok),
                                seconds=round(float(result.seconds or 0), 2), cost_usd=result.cost_usd,
                                session_id=result.session_id, error=result.error)
            if result.ok or "timed out" in (result.error or ""):
                break      # a timed-out run is not retried: it would only spend the same time again
        return result


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
                 run_dir: pathlib.Path, config: Config | None = None, *, exa_call=None):
        self.adapter = adapter
        self.memory = memory
        self.log = log
        self.run_dir = pathlib.Path(run_dir).resolve()   # briefs carry paths; agents run in their own folder
        self.config = config or Config()
        self.exa_call = exa_call
        self.runner = AgentRunner(backend, log, self.config.max_agents)
        self.rng = random.Random(self.config.seed)
        self.org: Org | None = None
        self.gateway: Gateway | None = None
        self.gateway_url = ""
        self.tokens: dict[str, str] = {}
        self.delivered: set[str] = set()     # fact ids the lab itself passed down

    def build_org(self, studies: list[Study]) -> Org:
        cfg = self.config
        agents = [Agent("director", DIRECTOR, "director", cfg.director_model)]
        for s in studies:
            agents.append(Agent(f"{s.id}-library", WORKER, "library", cfg.worker_model))
            for team in TEAMS:
                agents.append(Agent(f"{s.id}-{team}", WORKER, f"{s.id}-{team}", cfg.worker_model))
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
        kwargs = {"exa_departments": ["library"] if self.config.library else [], "agents_may_pass_down": False}
        if hasattr(self.adapter, "blocked_terms"):
            kwargs["blocked_terms"] = self.adapter.blocked_terms()
        if self.exa_call is not None:
            kwargs["exa_call"] = self.exa_call
        self.gateway = Gateway(self.org, self.memory, self.log, **kwargs)
        self.gateway_url = self.gateway.start()
        self.tokens = {a.id: self.gateway.issue_token(a.id) for a in self.org.agents}
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
        return {"LAB_GATEWAY_URL": self.gateway_url, "LAB_GATEWAY_TOKEN": self.tokens[agent.id]}

    def wait_settled(self) -> None:
        if hasattr(self.memory, "wait_settled"):
            self.memory.wait_settled()


class StudyRun:
    """The state of one study while it runs, and the steps of the protocol."""

    def __init__(self, lab: Lab, study: Study):
        self.lab = lab
        self.study = study
        self.cfg = lab.config
        self.dir = lab.run_dir / study.id
        self.deliverable = study.deliverables[0] if study.deliverables else "answer.json"
        self.record: dict = {"study": study.id, "status": None, "final": None, "switches": {
            "library": self.cfg.library, "questions": self.cfg.questions, "referee": self.cfg.referee},
            "candidates": [], "comparisons": [], "briefing": [], "questions": [], "warning_signs_to_team_c": [],
            "referee": None, "not_chosen": [], "agent_runs": [], "unsanctioned_pass_downs": []}
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
                "time_limit_minutes": self.cfg.timeout("analyst") // 60,
                "team_departments": f"{self.dept('team-a')}, {self.dept('team-b')}", **extra}

    def take(self, team: str, round_: str, result: AgentResult, workspace: pathlib.Path) -> Candidate | None:
        """Copy an agent's answer out of its workspace and check its format. None if the run failed."""
        if not result.ok:
            # An agent stopped by its time limit may still have left an answer.json (it is told to write one early).
            if "timed out" in (result.error or "") and (workspace / "answer.json").is_file():
                self.log("candidate.salvaged", team=team, round=round_, reason=result.error)
            else:
                self.log("candidate.none", team=team, round=round_, reason=result.error or "run failed")
                return None
        name = result.result.get("answer_file") or "answer.json"
        source = (workspace / str(name)).resolve()
        if not source.is_relative_to(workspace.resolve()) or not source.is_file():
            source = workspace / "answer.json"
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

    # ----- steps -----

    def library(self) -> None:
        agent = self.agent("library")
        brief = make_brief("library", agent, **self.fields())
        self.run_agent(agent, brief, "library", [self.study.data_dir, TOOLS], "library", "library")

    def briefing(self) -> list[dict]:
        director = self.agent("director")
        brief = make_brief("director_relay", director, **self.fields())
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
            ids = {team: self.pass_down(self.dept(team), text, came_from, reason) for team in ("team-a", "team-b")}
            entry = {"text": text, "from": came_from, "reason": reason, "fact_ids": ids}
            kept.append(entry)
            self.record["briefing"].append(entry)
        self.log("relay.briefing", items=len(kept))
        return kept

    def analyse(self, team: str, template_name: str = "analyst", round_: str = "analysis",
                phase: str = "analysis") -> Candidate | None:
        agent = self.agent(team)
        brief = make_brief(template_name, agent, **self.fields(diversity=DIVERSITY[team]))
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
        ws = self.dir / "director-questions"
        teams_dir = ws / "teams"
        teams_dir.mkdir(parents=True, exist_ok=True)
        for team in ("team-a", "team-b"):
            note = self.dir / team / "result.json"
            if note.is_file():
                shutil.copyfile(note, teams_dir / f"{self.dept(team)}.json")
        brief = make_brief("director_crossexam", director, **self.fields(teams_dir=str(teams_dir)))
        result = self.run_agent(director, brief, "director-questions", [TOOLS], "questions", "director")
        raw = result.result.get("questions") if result.ok else None
        answers = {self.dept("team-a"): (a, b), self.dept("team-b"): (b, a)}
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
                entry["reasons"] = leak_check(item["question"], own.values, [other.values])
            passed = not entry["reasons"]
            self.log("relay.leak_check", round="questions", to=to, passed=passed, reasons=entry["reasons"],
                     question=item["question"])
            if passed:
                entry["fact_id"] = self.pass_down(to, item["question"], came_from,
                                                  "question from the director after the teams' answers differed")
                entry["delivered"] = True
            out.append(entry)
            self.record["questions"].append(entry)
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
            (folder / "answer.json").write_text(json.dumps(scrub(doc, names, keep=("answer",)), indent=2))
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
        for item in briefing:
            self.pass_down(dept, item["text"], item["from"], item["reason"])
        for q in questions:
            reasons = leak_check(q["question"], None, [a.values, b.values])   # C has no answer of its own
            passed = not reasons
            self.log("relay.leak_check", round="team-c", to=dept, passed=passed, reasons=reasons, question=q["question"])
            entry = {"question": q["question"], "delivered": passed, "reasons": reasons, "fact_id": None}
            if passed:
                entry["fact_id"] = self.pass_down(dept, "WARNING SIGN TO CHECK: " + q["question"], q["from"],
                                                  "raised when two earlier teams disagreed; check it on the data")
            self.record["warning_signs_to_team_c"].append(entry)
        self.lab.wait_settled()
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
            self.lab.wait_settled()
            briefing = self.briefing()
        self.lab.wait_settled()
        a, b = self.both(self.analyse)
        va, vb = bool(a and a.valid), bool(b and b.valid)
        if not (va and vb):
            if va or vb:
                return self.finish("single-source", a if va else b)
            return self.finish("no-answer", None)
        if self.compare(a, b, "analysis"):
            return self.finish("replicated", a)

        questions = []
        if self.cfg.questions:
            questions = self.questions(a, b)
            self.lab.wait_settled()
            a2, b2 = self.both(self.analyse, "analyst_revise", "revise", "revise")
            a = a2 if a2 and a2.valid else a
            b = b2 if b2 and b2.valid else b
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
            shutil.copyfile(final.path, final_dir / "answer.json")
            self.record["final"] = {"agent": final.agent, "team": final.team, "round": final.round,
                                    "path": str(final_dir / "answer.json")}
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
