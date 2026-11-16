"""Control arms: the cheapest explanations of the lab's gain, run on the same studies.

    python3 -m lab.run arm --arm self --benchmark B --run-dir R [--studies ...] [--recheck] [--guard]
    python3 -m lab.run arm --arm vote --benchmark B --run-dir R (--attempts K | --match-cost LAB_RUN_DIR)

Self-challenge arm, in `<run_dir>/arms/self/<S>/` (roles "self-solo", "self-challenge", "self-revise"):

  1. solo       agent `self-<S>` solves the task with the single agent's brief (baseline.md), in
                `solver/`. With Config.recheck, an answer that abstains is re-checked as in the lab
                (analyst_recheck.md, role "self-recheck"), and the code's verdict decides.
  2. challenge  agent `self-<S>-challenger` gets the solver's code and notes (never its answer, and
                notes_without_answer drops what would tell it or that it changed) and challenges them with the director's single-team wording (director_challenge_single.md).
  3. revise     the solver answers the questions, in its own folder, with the lab's revise brief
                (analyst_revise.md); the questions are in `director_questions.json` there. With
                Config.guard, a changed answer needs evidence that check_evidence accepts.

  The arm has no memory, gateway or web, like the single agent. `arm.json` records each step and
  `final/<answer file>` the answer the arm reports: the revision when it is valid (and, with the
  guard, not blocked), else the answer the solver had.

Same-cost vote arm, in `<run_dir>/arms/vote/<S>/attempt-<k>/` (role "vote-arm"): k single agents,
each with vote_arm.md, the single agent's brief plus one approach hint, rotating through VOTE_HINTS.
`grade` combines them with lab.baseline.combine_attempts. k comes from --attempts, or from
--match-cost: the lab run's mean dollars per study over its mean dollars per single attempt,
rounded, and at least MIN_VOTE_K.

Every event an arm writes has an "arm" field, so lab.replay never mistakes it for the lab's.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import pathlib
import re
import shutil
import time

from lab.baseline import baseline_brief
from lab.budget import Budget, prior_spend
from lab.contracts import WORKER, Adapter, Agent, AgentResult, Backend, EventLog, Study
from lab.inventory import CODE_SUFFIXES, INVENTORY_FILE, build_inventory, hypothesis_text, render_inventory
from lab.measures import LAB_ROLES
from lab.protocol import (DIVERSITY, EVIDENCE_NOTE, QUESTIONS_FILE, AgentRunner, Candidate, Config, abstains,
                          agent_file, answer_file, answer_values, challenge_brief, check_evidence, field_agreement,
                          load_doc, local_data_dir, make_brief, recheck_verdict, restore_kept)

MIN_VOTE_K = 3
# The vote arm's approach hints: the two analysts' DIVERSITY lines (team C's needs warning signs a single
# agent does not have), then four more, short and neutral about the answer.
VOTE_HINTS = (
    DIVERSITY["team-a"],
    DIVERSITY["team-b"],
    "Write down the simplest analysis that answers the task, then test whether its result holds under a reasonable alternative.",
    "Before analysing, read the task again and write down exactly which quantity, groups and scale it asks about.",
    "Check the data's quality first: missing values, repeated records, units and coding.",
    "Look for something in the data that could explain the result other than what the task names, and adjust for it.",
)
SELF_SITUATION = ("A director has read your code and notes and does not think one analysis alone makes your answer "
                  "right. It has questions for you.")
SELF_QUESTIONS_SOURCE = "Its questions are in `director_questions.json` in this folder."
SELF_REPLY_NOTE = "Record what you found for each question in `result.json` (below). "


def self_dir(run_dir: pathlib.Path, study_id: str) -> pathlib.Path:
    return pathlib.Path(run_dir) / "arms" / "self" / study_id


def vote_dir(run_dir: pathlib.Path, study_id: str, k: int) -> pathlib.Path:
    return pathlib.Path(run_dir) / "arms" / "vote" / study_id / f"attempt-{k}"


def vote_brief(study: Study, k: int, timeout_s: int = 1800) -> str:
    """Attempt k's brief: the single agent's brief with the k-th approach hint (they rotate)."""
    return make_brief("vote_arm", None, statement=study.statement, data_dir=str(study.data_dir),
                      time_limit_minutes=timeout_s // 60, answer_file=answer_file(study),
                      hint=VOTE_HINTS[(k - 1) % len(VOTE_HINTS)])


def self_challenge_brief(study: Study, teams_dir: pathlib.Path) -> str:
    """The challenger's brief: the director's challenge of one analyst's answer, with no memory."""
    return challenge_brief(None, {"statement": study.statement, "data_dir": str(study.data_dir),
                                  "teams_dir": str(teams_dir), "study_id": study.id}, teams=1)


def self_revise_brief(study: Study, timeout_s: int = 1800, evidence_note: str = "") -> str:
    """The solver's revision brief: the lab's analyst_revise.md, with the questions and replies in files."""
    return make_brief("analyst_revise", None, statement=study.statement, data_dir=str(study.data_dir),
                      time_limit_minutes=timeout_s // 60, answer_file=answer_file(study), situation=SELF_SITUATION,
                      questions_source=SELF_QUESTIONS_SOURCE, reply_note=SELF_REPLY_NOTE, findings_note="",
                      evidence_note=evidence_note, search="")


def recheck_brief(study: Study, inventory: dict, cfg: Config, timeout_s: int = 1800) -> str:
    """The re-check brief without the lab's common section (the arm has no memory)."""
    return make_brief("analyst_recheck", None, statement=study.statement, data_dir=str(study.data_dir),
                      time_limit_minutes=timeout_s // 60, answer_file=answer_file(study),
                      inventory=render_inventory(inventory), max_p=cfg.recheck_p, min_n=cfg.recheck_min_n,
                      evidence_note=EVIDENCE_NOTE if cfg.guard else "")


# Notes fields that tell the answer or that it changed: dropped from what the self arm's challenger reads.
REVEALING_KEYS = ("changed", "revised")


def notes_without_answer(doc, answers: list[dict]):
    """The solver's result.json as its challenger may read it: without `changed`, `revised` or any
    field named like a label or an answer (except `answer_file`, a file name), and without any text
    that names a string value of any of `answers` (the solver's answers so far; any case, as a whole
    word)."""
    words = set()
    for answer in answers:
        for v in answer.values():
            for item in v if isinstance(v, list) else [v]:
                if isinstance(item, str) and len(item.strip()) > 1:
                    words.add(item.strip().lower())

    def names_answer(text: str) -> bool:
        low = text.lower()
        return any(re.search(r"(?<![a-z0-9_])" + re.escape(w) + r"(?![a-z0-9_])", low) for w in words)

    def revealing(key: str) -> bool:
        low = key.lower()
        return low in REVEALING_KEYS or (low != "answer_file" and ("label" in low or "answer" in low))

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()
                    if not revealing(str(k)) and not (isinstance(v, str) and names_answer(v))}
        if isinstance(value, list):
            return [clean(v) for v in value if not (isinstance(v, str) and names_answer(v))]
        return value

    return clean(doc)


def make_runner(backend: Backend, log: EventLog, max_agents: int, max_usd: float | None) -> AgentRunner:
    budget = Budget(max_usd, prior_spend(log)) if max_usd is not None else None
    return AgentRunner(backend, log, max_agents, budget=budget)


# ----- the self-challenge arm -----

class SelfArm:
    """One study through the self-challenge arm."""

    def __init__(self, adapter: Adapter, runner: AgentRunner, log: EventLog, run_dir: pathlib.Path, study: Study,
                 model: str, cfg: Config, timeout_s: int = 1800):
        self.adapter, self.runner, self.log_, self.study, self.cfg = adapter, runner, log, study, cfg
        self.timeout_s = timeout_s
        self.dir = self_dir(pathlib.Path(run_dir).resolve(), study.id)
        self.ws = self.dir / "solver"
        self.deliverable = answer_file(study)
        self.solver = Agent(f"self-{study.id}", WORKER, f"self-{study.id}", model)
        self.challenger = Agent(f"self-{study.id}-challenger", WORKER, f"self-{study.id}-challenger", model)
        self.candidates: list[Candidate] = []
        self.record: dict = {"arm": "self", "study": study.id, "status": None, "final": None, "model": model,
                             "switches": {"recheck": cfg.recheck, "guard": cfg.guard}, "candidates": [],
                             "challenge": None, "questions": [], "recheck": [], "guard": [], "agent_runs": []}

    def log(self, kind: str, **data) -> dict:
        return self.log_.append(kind, "lab", arm="self", study=self.study.id, **data)

    def run_agent(self, agent: Agent, brief: str, folder: pathlib.Path, role: str) -> AgentResult:
        result = self.runner.run(agent, brief, folder, readable=[pathlib.Path(self.study.data_dir)], env=None,
                                 timeout_s=self.timeout_s, study=self.study.id, phase=role, role=role,
                                 brief_file=self.dir / "briefs" / f"{role}.md")
        self.record["agent_runs"].append({"agent": agent.id, "phase": role, "ok": result.ok,
                                          "seconds": result.seconds, "cost_usd": result.cost_usd, "error": result.error})
        return result

    def take(self, round_: str, result: AgentResult) -> Candidate | None:
        """Copy the solver's answer out of its folder and check its format, as the lab does."""
        if not result.ok and not ("timed out" in (result.error or "") and (self.ws / self.deliverable).is_file()):
            self.log("arm.candidate", round=round_, valid=False, problems=[result.error or "run failed"])
            return None
        name = result.result.get("answer_file") or self.deliverable
        source = agent_file(self.ws, name) or agent_file(self.ws, self.deliverable)   # never a symbolic link
        store = self.dir / "candidates"
        store.mkdir(parents=True, exist_ok=True)
        path = store / f"self-{round_}.json"
        if source:
            shutil.copyfile(source, path)
        else:
            path.write_text("")
        note = agent_file(self.ws, "result.json")
        if note:
            shutil.copyfile(note, store / f"self-{round_}-result.json")
        problems = list(self.adapter.problems(self.study, self.deliverable, path)) if source \
            else ["no answer file"]
        cand = Candidate("self", self.solver.id, round_, path, problems, answer_values(path) if not problems else {})
        self.candidates.append(cand)
        self.log("arm.candidate", round=round_, valid=cand.valid, problems=problems)
        return cand

    def guard_event(self, round_: str, ok: bool, why: str, cand: Candidate, kept: Candidate) -> None:
        self.record["guard"].append({"team": "self", "round": round_, "passed": ok, "reason": why,
                                     "candidate": cand.id, "kept": None if ok else kept.id})
        if ok:
            self.log("guard.passed", team="self", round=round_)
        else:
            self.log("guard.blocked", team="self", round=round_, reason=why)

    def recheck(self, cand: Candidate) -> Candidate:
        """The lab's re-check, for the solver: the same inventory, verdict and guard."""
        before = cand.values.get("label")
        data = local_data_dir(self.adapter, self.study)
        if data is None:
            reason = "no local copy of the data to list (it is only on a Modal Volume)"
            self.log("recheck.skipped", team="self", reason=reason)
            self.record["recheck"].append({"team": "self", "skipped": True, "reason": reason})
            return cand
        inventory = build_inventory(data, self.ws, hypothesis_text(self.study.statement), answer_file=self.deliverable)
        (self.ws / INVENTORY_FILE).write_text(json.dumps(inventory, indent=2) + "\n")
        self.log("recheck.start", team="self", before=before, unused_files=len(inventory["unused_files"]),
                 unused_columns=inventory["unused_columns_total"])
        started = time.time()
        result = self.run_agent(self.solver, recheck_brief(self.study, inventory, self.cfg, self.timeout_s), self.ws,
                                "self-recheck")
        new = self.take("recheck", result)
        accepted, reason, test = recheck_verdict(new, self.ws, started, self.cfg)
        if accepted and self.cfg.guard:
            ok, why = check_evidence(self.ws, started, self.deliverable)
            self.guard_event("recheck", ok, why, new, cand)
            if not ok:
                accepted, reason = False, f"blocked by the damage guard: {why}"
        after = new.values.get("label") if new is not None and new.valid else None
        if new is not None and new.valid and not accepted:
            new.not_used = f"the re-check's verdict kept the earlier answer: {reason}"
        if not accepted:     # the solver's folder gets the kept answer back, as in the lab
            restore_kept(self.ws, cand, self.dir / "candidates" / f"self-{cand.round}-result.json", self.deliverable)
        in_use = new if accepted else cand
        self.log("recheck.verdict", team="self", before=before, after=after, accepted=accepted, reason=reason,
                 in_use=in_use.id)
        self.record["recheck"].append({"team": "self", "before": before, "after": after, "accepted": accepted,
                                       "reason": reason, "candidate": "self-recheck", "test": test,
                                       "in_use": in_use.id})
        return in_use

    def challenge(self) -> list[str]:
        """The challenger reads the solver's code and notes (never its answer) and writes questions.
        Symbolic links are skipped, and the notes lose every field that would tell the answer or that
        it changed (notes_without_answer), as the single-team brief promises."""
        folder = self.dir / "challenger"
        notes = folder / "solver"
        notes.mkdir(parents=True, exist_ok=True)
        ws = self.ws.resolve()
        for path in sorted(ws.rglob("*")):
            rel = path.relative_to(ws)
            if ".agent" in rel.parts or path.suffix.lower() not in CODE_SUFFIXES or agent_file(ws, rel) is None:
                continue
            (notes / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, notes / rel)
        note = agent_file(ws, "result.json")
        doc = load_doc(note) if note else None
        if isinstance(doc, dict):
            (notes / "result.json").write_text(json.dumps(notes_without_answer(doc, [c.values for c in self.candidates if c.valid]), indent=2) + "\n")
        result = self.run_agent(self.challenger, self_challenge_brief(self.study, notes), folder, "self-challenge")
        doc = result.result if result.ok else {}
        raw = doc.get("questions") if isinstance(doc.get("questions"), list) else []
        questions = [q["question"].strip() for q in raw
                     if isinstance(q, dict) and isinstance(q.get("question"), str) and q["question"].strip()]
        self.record["challenge"] = {"ran": bool(result.ok), "verdict": doc.get("verdict") if result.ok
                                    else f"challenge run failed: {result.error}",
                                    **{k: doc.get(k) if isinstance(doc.get(k), list) else []
                                       for k in ("untested_assumptions", "unused_evidence", "unresolved_weak_points",
                                                 "second_readings")}}
        self.record["questions"] = questions
        self.log("arm.questions", questions=len(questions))
        return questions

    def revise(self, current: Candidate, questions: list[str]) -> tuple[Candidate, str]:
        """The solver answers the questions. Returns the answer it uses after, and the arm's status."""
        (self.ws / QUESTIONS_FILE).write_text(json.dumps({"questions": questions}, indent=2) + "\n")
        brief = self_revise_brief(self.study, self.timeout_s, EVIDENCE_NOTE if self.cfg.guard else "")
        started = time.time()
        rev = self.take("revise", self.run_agent(self.solver, brief, self.ws, "self-revise"))
        if rev is None or not rev.valid:
            return current, "kept"
        if all(field_agreement(self.adapter, self.study, current.path, rev.path).values()):
            return rev, "kept"
        if self.cfg.guard:
            ok, why = check_evidence(self.ws, started, self.deliverable)
            self.guard_event("revise", ok, why, rev, current)
            if not ok:
                rev.not_used = f"blocked by the damage guard: {why}"
                return current, "blocked"
        return rev, "changed"

    def run(self) -> dict:
        if self.dir.exists():     # an earlier run of this arm on this study stopped before arm.final
            self.dir.rename(self.dir.with_name(f"{self.study.id}.stale-{time.strftime('%Y%m%d%H%M%S')}"))
        self.ws.mkdir(parents=True)
        self.log("arm.start")
        current = self.take("solo", self.run_agent(self.solver, baseline_brief(self.study, self.timeout_s), self.ws,
                                                   "self-solo"))
        if current is not None and current.valid and self.cfg.recheck \
                and abstains(self.adapter, self.study, current.values):
            current = self.recheck(current)
        if current is None or not current.valid:
            return self.finish("no-answer", None)
        questions = self.challenge()
        if not questions:
            return self.finish("unchallenged", current)
        final, status = self.revise(current, questions)
        return self.finish(status, final)

    def finish(self, status: str, final: Candidate | None) -> dict:
        final_dir = self.dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        if final is not None:
            shutil.copyfile(final.path, final_dir / self.deliverable)
            self.record["final"] = {"round": final.round, "id": final.id, "path": str(final_dir / self.deliverable)}
        self.record["status"] = status
        self.record["candidates"] = [c.record() for c in self.candidates]
        (self.dir / "arm.json").write_text(json.dumps(self.record, indent=2, default=str) + "\n")
        self.log("arm.final", status=status, final_round=final.round if final else None)
        return {"status": status, "final": self.record["final"]}


def run_self_arm(adapter: Adapter, backend: Backend, log: EventLog, run_dir: pathlib.Path, studies: list[Study],
                 model: str, max_agents: int, cfg: Config | None = None, timeout_s: int = 1800,
                 max_usd: float | None = None) -> dict:
    """Run the self-challenge arm on every study it has not finished. Returns {study: {"status", "final"}}.
    The agents' runs share one dollar cap, counting what the run directory spent before."""
    cfg = cfg or Config()
    runner = make_runner(backend, log, max_agents, max_usd)
    done = {e["study"]: e for e in log.events("arm.final") if e.get("arm") == "self"}
    out = {sid: {"status": e.get("status"), "skipped": True} for sid, e in done.items()
           if sid in {s.id for s in studies}}
    todo = [s for s in studies if s.id not in done]
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(todo)) as pool:
            futures = {pool.submit(SelfArm(adapter, runner, log, run_dir, s, model, cfg, timeout_s).run): s
                       for s in todo}
            for fut in concurrent.futures.as_completed(futures):
                out[futures[fut].id] = fut.result()
    return out


# ----- the same-cost vote arm -----

def run_vote_arm(adapter: Adapter, backend: Backend, log: EventLog, run_dir: pathlib.Path, studies: list[Study],
                 attempts: int, model: str, max_agents: int, timeout_s: int = 1800,
                 max_usd: float | None = None) -> dict:
    """Run `attempts` vote-arm agents per study, skipping attempts already done. Returns {study: {k: ok}}."""
    runner = make_runner(backend, log, max_agents, max_usd)
    run_dir = pathlib.Path(run_dir).resolve()
    done = {(e["study"], e["attempt_number"]) for e in log.events("arm.attempt") if e.get("arm") == "vote"}
    jobs = [(s, k) for s in studies for k in range(1, attempts + 1) if (s.id, k) not in done]
    out: dict = {s.id: {} for s in studies}

    def one(study: Study, k: int) -> bool:
        agent = Agent(f"vote-{study.id}-{k}", WORKER, "vote-arm", model)
        ws = vote_dir(run_dir, study.id, k)
        result = runner.run(agent, vote_brief(study, k, timeout_s), ws, readable=[pathlib.Path(study.data_dir)],
                            env=None, timeout_s=timeout_s, study=study.id, phase=f"vote-{k}", role="vote-arm",
                            brief_file=ws.parent / "briefs" / f"attempt-{k}.md")
        log.append("arm.attempt", "lab", arm="vote", study=study.id, attempt_number=k, ok=result.ok,
                   hint=VOTE_HINTS[(k - 1) % len(VOTE_HINTS)])
        return result.ok

    if jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(len(jobs), max_agents))) as pool:
            futures = {pool.submit(one, s, k): (s.id, k) for s, k in jobs}
            for fut in concurrent.futures.as_completed(futures):
                sid, k = futures[fut]
                out[sid][k] = fut.result()
    return out


def match_cost_k(lab_run_dir: pathlib.Path) -> tuple[int, dict]:
    """k for the same-cost vote arm, from a lab run directory's events: the lab's mean dollars per
    study (agent runs with a lab role) over the mean dollars per single attempt (role "baseline";
    an attempt is one study and phase, retries included), rounded half up, and at least MIN_VOTE_K.
    Raises ValueError when the run has no lab runs, no single attempts, or runs without a dollar figure."""
    path = pathlib.Path(lab_run_dir) / "events.jsonl"
    if not path.is_file():
        raise ValueError(f"no events.jsonl in {lab_run_dir}")
    finishes = []
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("kind") == "agent.finish":
            finishes.append(e)
    lab = [e for e in finishes if e.get("role") in LAB_ROLES]
    single = [e for e in finishes if e.get("role") == "baseline"]
    if not lab or not single:
        raise ValueError(f"{lab_run_dir} needs both lab runs and single attempts (baseline) to match cost; "
                         f"it has {len(lab)} lab runs and {len(single)} single-attempt runs")
    missing = sum(1 for e in lab + single if e.get("cost_usd") is None)
    if missing:
        raise ValueError(f"{missing} of {len(lab) + len(single)} agent runs in {lab_run_dir} have no dollar figure")
    studies = {e.get("study") for e in lab}
    attempts = {(e.get("study"), e.get("phase")) for e in single}
    lab_per_study = sum(float(e["cost_usd"]) for e in lab) / len(studies)
    per_attempt = sum(float(e["cost_usd"]) for e in single) / len(attempts)
    if per_attempt <= 0:
        raise ValueError("the single attempts cost nothing, so k cannot be set")
    ratio = lab_per_study / per_attempt
    k = max(MIN_VOTE_K, math.floor(ratio + 0.5))
    return k, {"lab_per_study": round(lab_per_study, 4), "per_attempt": round(per_attempt, 4),
               "ratio": round(ratio, 3), "lab_studies": len(studies), "attempts": len(attempts), "k": k}
