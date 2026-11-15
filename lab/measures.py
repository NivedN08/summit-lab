"""The lab's measures (explanation.md, D-12), computed from a graded run directory.

`measure(run_dir)` reads `results.json` (written by `grade`, which must have run), each
`<study>/study.json`, the lab's copies of the teams' result.json files in `<study>/candidates/`,
the library's `<study>/library/result.json` and, from `events.jsonl`, the agent.finish events,
the gateway's exa.call and exa.refused events and the memory.write events.
No agent is run. Every measure is a count first (`n` out of `of`) and a rate second, because
ten studies are too few for rates to the decimal (I-5).

How rescues and damage (d) are counted, step by step:
  question round  per team: its first answer against the answer it used after the round
                  (the revised one when that was valid, else the first).
  challenge       per challenged study: the first agreed answer (team A's first answer, which
                  the lab would have reported as replicated without the challenge) against the
                  final answer. When the teams agree again that is team A's answer after the
                  challenge; when they no longer agree it is what the referee or team C produced.
  referee         per team: the answer that team handed to the referee against the answer the
                  referee step produced (its pick, or its own answer when it picked neither).
                  A referee that picked neither and gave no usable answer changed nothing.
  third team      per study: what the lab would have reported without team C (the referee's own
                  valid answer, else no answer, which counts as failing) against the final answer.
  research        per team, in studies where research on demand delivered at least one finding:
                  the team's first answer against the answer it used after the revision round
                  that followed the findings (the revised one when valid, else the first).
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any

LABELS = ("replicated", "converged", "refereed", "third-team", "unresolved", "single-source", "no-answer")
FIRST_TEAMS = ("team-a", "team-b")
REVISING = ("revise", "challenge")     # rounds in which a team answers the director's questions
SEARCH_ROLES = ("library", "team-a", "research")   # who made an Exa call: library first pass, team A, research
REVISION_ROUND = {"questions": "revise", "challenge": "challenge"}   # director's round -> the teams' next round


class NotGraded(RuntimeError):
    """The run directory has no results.json yet."""


def count(n: int, of: int) -> dict:
    """A count and its rate; the rate is None when there is nothing to count."""
    return {"n": n, "of": of, "rate": round(n / of, 3) if of else None}


def _load(path: pathlib.Path) -> Any:
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


def _read_events(run_dir: pathlib.Path, kinds: tuple) -> dict[str, list[dict]]:
    """Events of the given kinds, by kind, read without creating or locking anything."""
    path = run_dir / "events.jsonl"
    out: dict[str, list[dict]] = {k: [] for k in kinds}
    if path.is_file():
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("kind") in out:
                out[event["kind"]].append(event)
    return out


def _cid(team: str, round_: str) -> str:
    return "referee-own" if team == "referee" else f"{team}-{round_}"


class Run:
    """What one graded run directory holds, loaded once."""

    def __init__(self, run_dir: pathlib.Path):
        self.dir = pathlib.Path(run_dir)
        results = _load(self.dir / "results.json")
        if not isinstance(results, dict) or not isinstance(results.get("studies"), dict):
            raise NotGraded(f"no results.json in {self.dir}: grade the run first with "
                            f"`python3 -m lab.run grade --run-dir {self.dir}`")
        self.missing: list[str] = []
        self.entries: dict = results["studies"]
        self.lab_ids = [sid for sid, e in self.entries.items() if "lab" in e]
        self.base_ids = [sid for sid, e in self.entries.items() if e.get("baseline", {}).get("attempts")]
        self.records = {}
        for sid in self.lab_ids:
            rec = _load(self.dir / sid / "study.json")
            self.records[sid] = rec if isinstance(rec, dict) else {}
            if not rec:
                self.missing.append(f"{sid}: no readable study.json")
        self.passed = {}
        for sid, e in self.entries.items():
            if "candidates" not in e:
                self.missing.append(f"{sid}: results.json has no per-candidate grades (run grade again)")
            self.passed[sid] = {c["id"]: bool(c.get("passed")) for c in e.get("candidates") or [] if "id" in c}
        logged = _read_events(self.dir, ("agent.finish", "exa.call", "exa.refused", "memory.write"))
        self.events = logged["agent.finish"]
        self.exa_calls, self.exa_refused, self.writes = logged["exa.call"], logged["exa.refused"], logged["memory.write"]

    def lab_passed(self, sid: str) -> bool:
        return bool((self.entries[sid]["lab"].get("grade") or {}).get("passed"))

    def candidates(self, sid: str) -> list[dict]:
        return self.records[sid].get("candidates") or []

    def cand(self, sid: str, cid: str) -> dict | None:
        return next((c for c in self.candidates(sid) if _cid(c.get("team"), c.get("round")) == cid), None)

    def team_results(self, sid: str) -> list[tuple[dict, dict | None]]:
        """(candidate, that run's result.json copy) for every team answer, in order."""
        out = []
        for c in self.candidates(sid):
            if c.get("team") in ("team-a", "team-b", "team-c"):
                doc = _load(self.dir / sid / "candidates" / f"{c['team']}-{c['round']}-result.json")
                out.append((c, doc if isinstance(doc, dict) else None))
        return out

    def latest_valid(self, sid: str, team: str) -> str | None:
        """The id of the team's answer the protocol used last (revised if valid, else first)."""
        ids = [_cid(c["team"], c["round"]) for c in self.candidates(sid) if c.get("team") == team and c.get("valid")]
        return ids[-1] if ids else None


# ----- a. headline -----

def _spend(events: list[dict], basis: str) -> float:
    if basis == "dollars":
        return sum(float(e.get("cost_usd") or 0) for e in events)
    if basis == "minutes":
        return sum(float(e.get("seconds") or 0) for e in events) / 60
    return float(len(events))


def same_budget_vote(run: Run) -> dict:
    """A plain vote over the first k single attempts, k = lab cost per study / cost of one attempt."""
    notes = []
    attempts = sum(len(run.entries[sid]["baseline"]["attempts"]) for sid in run.base_ids)
    out = {"k": None, "k_unrounded": None, "basis": None, "lab_per_study": None, "per_attempt": None,
           **count(0, 0), "notes": notes}
    if not attempts or not run.lab_ids:
        notes.append("no single-agent attempts to vote over" if not attempts else "no lab studies to compare with")
        return out
    lab_events = [e for e in run.events if e.get("role") != "baseline" and e.get("study") in run.lab_ids]
    base_events = [e for e in run.events if e.get("role") == "baseline" and e.get("study") in run.base_ids]
    both = lab_events + base_events
    no_cost = sum(1 for e in both if e.get("cost_usd") is None)
    if lab_events and base_events and not no_cost:
        basis = "dollars"
    else:
        notes.append(f"cost missing for {no_cost} of {len(both)} agent runs, so k is set by "
                     + ("time" if _spend(lab_events, "minutes") > 0 and _spend(base_events, "minutes") > 0
                        else "agent-run counts"))
        basis = "minutes" if _spend(lab_events, "minutes") > 0 and _spend(base_events, "minutes") > 0 else "agent runs"
    lab_per_study = _spend(lab_events, basis) / len(run.lab_ids)
    per_attempt = _spend(base_events, basis) / attempts
    out.update(basis=basis, lab_per_study=round(lab_per_study, 4), per_attempt=round(per_attempt, 4))
    if per_attempt <= 0:
        notes.append("the single attempts have no recorded runs, so k cannot be set")
        return out
    raw = lab_per_study / per_attempt
    most = max(len(run.entries[sid]["baseline"]["attempts"]) for sid in run.base_ids)
    k = min(max(1, math.floor(raw + 0.5)), most)
    if k < math.floor(raw + 0.5):
        notes.append(f"the lab cost {raw:.1f} attempts per study, but only {most} attempts exist")
    out.update(k=k, k_unrounded=round(raw, 3))
    passes, cut = 0, 0
    for sid in run.base_ids:
        b = run.entries[sid]["baseline"]
        used = min(k, len(b["attempts"]))
        cut += used < k
        vote = (b.get("votes_over_first") or {}).get(str(used))
        if vote is None:
            notes.append(f"{sid}: no graded vote over the first {used} attempts (run grade again)")
            continue
        passes += bool(vote.get("passed"))
    if cut:
        notes.append(f"k was cut to the attempts available in {cut} studies")
    out.update(count(passes, len(run.base_ids)))
    return out


def headline(run: Run) -> dict:
    attempt_rows = [g for sid in run.base_ids for g in run.entries[sid]["baseline"]["attempts"].values()]
    first = [run.entries[sid]["baseline"]["attempts"].get("attempt-1") for sid in run.base_ids]
    first = [g for g in first if g is not None]
    votes = [run.entries[sid]["baseline"].get("vote") or {} for sid in run.base_ids]
    return {
        "lab": count(sum(run.lab_passed(sid) for sid in run.lab_ids), len(run.lab_ids)),
        "single_attempts": count(sum(bool(g.get("passed")) for g in attempt_rows), len(attempt_rows)),
        "single_attempt_1": count(sum(bool(g.get("passed")) for g in first), len(first)),
        "same_budget_vote": same_budget_vote(run),
        "vote_all_attempts": count(sum(bool(v.get("passed")) for v in votes), len(votes)),
    }


# ----- b, c. labels and false agreement -----

def labels(run: Run) -> dict:
    out = {label: {"studies": 0, "passed": 0} for label in LABELS}
    for sid in run.lab_ids:
        row = out.setdefault(str(run.entries[sid]["lab"].get("status")), {"studies": 0, "passed": 0})
        row["studies"] += 1
        row["passed"] += run.lab_passed(sid)
    return out


def false_agreement(run: Run) -> dict:
    replicated = [sid for sid in run.lab_ids if run.entries[sid]["lab"].get("status") == "replicated"]
    failed = [sid for sid in replicated if not run.lab_passed(sid)]
    agreed = [sid for sid in run.lab_ids if any(c.get("round") == "analysis" and c.get("agree")
                                                for c in run.records[sid].get("comparisons") or [])]
    wrong = {sid: sum(not run.passed[sid].get(f"{t}-analysis", False) for t in FIRST_TEAMS) for sid in agreed}
    return {"replicated_failed": count(len(failed), len(replicated)), "replicated_failed_studies": failed,
            "first_answers_agreed": len(agreed),
            "first_agreed_both_wrong": sum(1 for n in wrong.values() if n == 2),
            "first_agreed_one_wrong": sum(1 for n in wrong.values() if n == 1),
            "first_agreed_both_wrong_studies": [sid for sid, n in wrong.items() if n == 2]}


# ----- d. rescues and damage -----

def _tally(changes: list, sid: str, who: str, before: bool, after: bool) -> None:
    if before != after:
        changes.append({"study": sid, "who": who, "change": "rescued" if after else "damaged"})


def _step(changes: list, compared: int, **extra) -> dict:
    return {"compared": compared, "rescued": sum(c["change"] == "rescued" for c in changes),
            "damaged": sum(c["change"] == "damaged" for c in changes), "changes": changes, **extra}


def steps(run: Run) -> dict:
    q_changes, q_compared = [], 0
    r_changes, r_compared, r_ran, r_nothing = [], 0, 0, 0
    c_changes, c_ran, c_without_answer = [], 0, 0
    ch_changes, ch_compared = [], 0
    for sid in run.lab_ids:
        grades, record = run.passed[sid], run.records[sid]
        if record.get("challenged"):
            ch_compared += 1
            _tally(ch_changes, sid, "study", grades.get("team-a-analysis", False), run.lab_passed(sid))
        for team in FIRST_TEAMS:
            revised = run.cand(sid, f"{team}-revise")
            if revised is None or f"{team}-analysis" not in grades:
                continue
            q_compared += 1
            first = grades[f"{team}-analysis"]
            after = grades.get(f"{team}-revise", False) if revised.get("valid") else first
            _tally(q_changes, sid, team, first, after)
        ref = record.get("referee")
        if isinstance(ref, dict):
            r_ran += 1
            given = {team: run.latest_valid(sid, team) for team in FIRST_TEAMS}
            if ref.get("pick") in ("X", "Y"):
                result = next((cid for cid in given.values() if cid and run.cand(sid, cid)
                               and run.cand(sid, cid).get("agent") == ref.get("picked_agent")), None)
            else:
                own = run.cand(sid, "referee-own")
                result = "referee-own" if own and own.get("valid") else None
            if result is None:
                r_nothing += 1
            else:
                for team, cid in given.items():
                    if cid:
                        r_compared += 1
                        _tally(r_changes, sid, team, grades.get(cid, False), grades.get(result, False))
        if any(r.get("phase") == "third-team" for r in record.get("agent_runs") or []):
            c_ran += 1
            own = run.cand(sid, "referee-own")
            before = grades.get("referee-own", False) if own and own.get("valid") else False
            c_without_answer += not (own and own.get("valid"))
            _tally(c_changes, sid, "study", before, run.lab_passed(sid))
    return {"questions": _step(q_changes, q_compared), "challenge": _step(ch_changes, ch_compared),
            "referee": _step(r_changes, r_compared, ran=r_ran, settled_nothing=r_nothing),
            "team_c": _step(c_changes, c_ran, ran=c_ran, no_answer_without_it=c_without_answer)}


# ----- e. selection -----

def selection(run: Run) -> dict:
    had = []
    for sid in run.lab_ids:
        rows = run.entries[sid].get("candidates") or []
        if any(r.get("kind") == "lab" and r.get("passed") for r in rows):
            had.append(sid)
    shipped_fail = [sid for sid in had if not run.lab_passed(sid)]
    return {"lab_passed": count(len(had) - len(shipped_fail), len(had)),
            "passing_candidate_but_failing_final": shipped_fail}


# ----- f, g. noticing to acting, library value -----

def noticing(run: Run) -> dict:
    signs, sign_files, no_signs = 0, 0, 0
    proposed = blocked = delivered = 0
    replies = replies_changed = 0
    changed, fixed = 0, 0
    for sid in run.lab_ids:
        for cand, doc in run.team_results(sid):
            if cand.get("round") in REVISING:
                for r in (doc or {}).get("replies") or []:
                    if isinstance(r, dict):
                        replies += 1
                        replies_changed += r.get("changes_method") is True
                continue
            if isinstance((doc or {}).get("warning_signs"), list):
                sign_files += 1
                signs += len(doc["warning_signs"])
            else:
                no_signs += 1
        for q in run.records[sid].get("questions") or []:
            proposed += 1
            delivered += bool(q.get("delivered"))
            blocked += not q.get("delivered")
        for team in FIRST_TEAMS:
            first, revised = run.cand(sid, f"{team}-analysis"), run.cand(sid, f"{team}-revise")
            if first and revised and revised.get("valid") and revised.get("answer") != first.get("answer"):
                changed += 1
                fixed += (not run.passed[sid].get(f"{team}-analysis", False)) and run.passed[sid].get(f"{team}-revise", False)
    if no_signs:
        run.missing.append(f"{no_signs} team answer(s) came with no warning_signs list")
    return {"warning_signs": signs, "team_answers_with_signs_list": sign_files, "questions_proposed": proposed,
            "questions_blocked": blocked, "questions_delivered": delivered, "replies": replies,
            "replies_changed_method": replies_changed, "teams_changed_answer": changed,
            "changed_failing_to_passing": fixed}


def library(run: Run) -> dict:
    found = with_sources = sources = 0
    no_library = []
    checks = in_data = changed = 0
    without_checks = 0
    for sid in run.lab_ids:
        doc = _load(run.dir / sid / "library" / "result.json")
        pitfalls = doc.get("pitfalls") if isinstance(doc, dict) else None
        if not isinstance(pitfalls, list):
            no_library.append(sid)
        else:
            for p in pitfalls:
                links = p.get("sources") if isinstance(p, dict) and isinstance(p.get("sources"), list) else []
                found += 1
                with_sources += bool(links)
                sources += len(links)
        for cand, doc in run.team_results(sid):
            if cand.get("round") in REVISING:
                continue
            items = (doc or {}).get("pitfalls_checked")
            if not isinstance(items, list):
                without_checks += 1
                continue
            for item in items:
                if isinstance(item, dict):
                    checks += 1
                    in_data += item.get("found_in_data") is True
                    changed += item.get("changed_method") is True
    if no_library:
        run.missing.append(f"no library result for {len(no_library)} studies: {', '.join(no_library)}")
    if without_checks:
        run.missing.append(f"{without_checks} team answer(s) came with no pitfalls_checked list")
    return {"pitfalls_found": found, "pitfalls_with_sources": with_sources, "sources": sources,
            "studies_without_library": no_library, "pitfall_checks": checks, "found_in_data": in_data,
            "changed_method": changed, "team_answers_without_checks": without_checks}


# ----- the challenge of agreed answers, and who was briefed -----

def challenge(run: Run) -> dict:
    """How many agreed first answers were challenged, drew questions, changed, or split the teams."""
    agreed = [sid for sid in run.lab_ids if any(c.get("round") == "analysis" and c.get("agree")
                                                for c in run.records[sid].get("comparisons") or [])]
    challenged = [sid for sid in run.lab_ids if run.records[sid].get("challenged")]
    questioned = [sid for sid in challenged if (run.records[sid].get("challenge_questions") or 0) > 0]
    changed = [sid for sid in questioned if run.records[sid].get("challenge_changed_answer") is True]
    split = [sid for sid in questioned if any(c.get("round") == "challenge" and not c.get("agree")
                                              for c in run.records[sid].get("comparisons") or [])]
    written = delivered = 0
    for sid in challenged:
        for q in (run.records[sid].get("challenge") or {}).get("questions") or []:
            written += 1
            delivered += bool(q.get("delivered"))
    return {"challenged": count(len(challenged), len(agreed)), "questioned": count(len(questioned), len(challenged)),
            "changed": count(len(changed), len(questioned)), "disagreed_after": count(len(split), len(questioned)),
            "questions_written": written, "questions_delivered": delivered,
            "changed_studies": changed, "disagreed_after_studies": split}


def briefing(run: Run) -> dict:
    """Which first teams received the director's briefing, per study and as a tally."""
    studies = {}
    for sid in run.lab_ids:
        teams = run.records[sid].get("briefed_teams")
        studies[sid] = list(teams) if isinstance(teams, list) else None
    tally: dict[str, int] = {}
    for teams in studies.values():
        key = "not recorded" if teams is None else ",".join(teams) or "none"
        tally[key] = tally.get(key, 0) + 1
    return {"studies": studies, "by_teams": tally}


# ----- web search, research on demand and outcome kinds (version 3) -----

def _search_role(event: dict) -> str:
    """Which search an Exa call belonged to: the gateway's budget label, else the agent's job."""
    if event.get("budget") in SEARCH_ROLES:
        return event["budget"]
    agent = str(event.get("agent") or event.get("actor") or "")
    return "library" if agent.endswith("-library") else "team-a" if agent.endswith("-team-a") else "other"


def search(run: Run) -> dict:
    """Exa calls made and refused by the budget, by role, and the LEARNED facts the teams wrote."""
    calls = {r: 0 for r in SEARCH_ROLES}
    refused = {r: 0 for r in SEARCH_ROLES}
    for events, tally in ((run.exa_calls, calls), (run.exa_refused, refused)):
        for e in events:
            role = _search_role(e)
            tally[role] = tally.get(role, 0) + 1
    teams = tuple(f"{sid}-team-" for sid in run.lab_ids)
    learned = [e for e in run.writes if str(e.get("actor") or "").startswith(teams)
               and (str(e.get("label") or "").strip().upper() == "LEARNED"
                    or str(e.get("text") or "").lstrip().upper().startswith("LEARNED"))]
    by_team: dict[str, int] = {}
    for e in learned:
        team = "team-" + str(e["actor"]).rsplit("team-", 1)[1]
        by_team[team] = by_team.get(team, 0) + 1
    return {"calls": calls, "refused": refused, "learned": len(learned), "learned_by_team": by_team}


def research(run: Run) -> dict:
    """Research questions asked, blocked and answered; findings delivered; and the rescues and
    damage of the revision round that followed delivered findings."""
    asked = blocked = answered = findings = delivered = compared = 0
    studies, changes = [], []
    for sid in run.lab_ids:
        reached = False
        for r in run.records[sid].get("research") or []:
            questions = r.get("questions") or []
            found = r.get("findings") or []
            asked += len(questions)
            blocked += sum(not q.get("passed") for q in questions)
            findings += len(found)
            answered += min(sum(bool(q.get("passed")) for q in questions), len(found))
            sent = sum(bool(d.get("delivered")) for f in found for d in (f.get("deliveries") or {}).values())
            delivered += sent
            round_ = REVISION_ROUND.get(r.get("round"))
            if not sent or round_ is None:
                continue
            reached = True
            for team in FIRST_TEAMS:
                cand = run.cand(sid, f"{team}-{round_}")
                if cand is None or f"{team}-analysis" not in run.passed[sid]:
                    continue
                compared += 1
                first = run.passed[sid][f"{team}-analysis"]
                after = run.passed[sid].get(f"{team}-{round_}", False) if cand.get("valid") else first
                _tally(changes, sid, team, first, after)
        if reached:
            studies.append(sid)
    return {"asked": asked, "blocked": blocked, "answered": answered, "findings": findings, "delivered": delivered,
            "studies": studies, "revision": _step(changes, compared)}


def kinds(run: Run) -> dict:
    """The adapter's outcome kinds, tallied for the lab's final answers and for single-agent attempts."""
    out = {"lab_final": {}, "single_attempts": {}}
    for entry in run.entries.values():
        for row in entry.get("candidates") or []:
            key = {"final": "lab_final", "attempt": "single_attempts"}.get(row.get("kind"))
            if key:
                kind = row.get("outcome_kind") or "not given"
                out[key][kind] = out[key].get(kind, 0) + 1
    return out


# ----- h. cost -----

def _cost_row(events: list[dict]) -> dict:
    known = [float(e["cost_usd"]) for e in events if e.get("cost_usd") is not None]
    return {"runs": len(events), "failed": sum(not e.get("ok") for e in events),
            "minutes": round(sum(float(e.get("seconds") or 0) for e in events) / 60, 2),
            "dollars": round(sum(known), 4) if known else None, "runs_without_cost": len(events) - len(known)}


def cost(run: Run, head: dict) -> dict:
    by_role = {}
    for role in sorted({str(e.get("role") or "unknown") for e in run.events}):
        by_role[role] = _cost_row([e for e in run.events if str(e.get("role") or "unknown") == role])
    lab_ev = [e for e in run.events if e.get("role") != "baseline"]
    base_ev = [e for e in run.events if e.get("role") == "baseline"]
    per_study = {}
    for sid in sorted({e.get("study") for e in run.events if e.get("study")}):
        per_study[sid] = {"lab": _cost_row([e for e in lab_ev if e.get("study") == sid]),
                          "baseline": _cost_row([e for e in base_ev if e.get("study") == sid])}
    overall = {"lab": _cost_row(lab_ev), "baseline": _cost_row(base_ev), "all": _cost_row(run.events)}

    def per_dollar(passes: int, row: dict, who: str):
        if not row["runs"]:
            return None
        if row["dollars"] is None or row["runs_without_cost"] or row["dollars"] <= 0:
            run.missing.append(f"cost not reported for {row['runs_without_cost']} of {row['runs']} {who} runs, "
                               "so passes per dollar is not given")
            return None
        return round(passes / row["dollars"], 4)

    return {"by_role": by_role, "per_study": per_study, "overall": overall,
            "passes_per_dollar": {"lab": per_dollar(head["lab"]["n"], overall["lab"], "lab"),
                                  "single_attempts": per_dollar(head["single_attempts"]["n"], overall["baseline"],
                                                                "single-agent")}}


# ----- all together -----

def measure(run_dir: pathlib.Path) -> dict:
    """Every measure in D-12 for one graded run. Raises NotGraded when results.json is missing."""
    run = Run(run_dir)
    head = headline(run)
    out = {"run": run.dir.name, "studies": len(run.lab_ids), "headline": head, "labels": labels(run),
           "false_agreement": false_agreement(run), "steps": steps(run), "challenge": challenge(run),
           "briefing": briefing(run), "selection": selection(run),
           "noticing": noticing(run), "library": library(run), "search": search(run), "research": research(run),
           "kinds": kinds(run), "cost": cost(run, head)}
    out["missing"] = run.missing
    return out


def frac(c: dict) -> str:
    """'n of m' for a count."""
    return f"{c['n']} of {c['of']}"


def headline_rows(m: dict) -> list[tuple[str, str]]:
    h = m["headline"]
    sb = h["same_budget_vote"]
    k = f"k = {sb['k']}, by {sb['basis']}" if sb["k"] else "k not set"
    return [("Lab final answers (studies)", frac(h["lab"])),
            ("Single-agent attempts (all attempts)", frac(h["single_attempts"])),
            ("Single-agent attempt 1 only (studies)", frac(h["single_attempt_1"])),
            (f"Same-budget vote ({k})", frac(sb)),
            ("Vote over all attempts (studies)", frac(h["vote_all_attempts"]))]


def _cost_text(row: dict) -> str:
    money = "no dollar figure" if row["dollars"] is None else f"${row['dollars']:.2f}"
    return f"{row['runs']} runs, {row['minutes']} min, {money}"


def summary_lines(m: dict) -> list[str]:
    """A short plain-text summary for the terminal."""
    lines = [f"Measures for {m['run']} ({m['studies']} lab studies)", ""]
    lines += [f"  {name:<44} {value} passed" for name, value in headline_rows(m)]
    lines += [f"  note: {n}" for n in m["headline"]["same_budget_vote"]["notes"]]
    lines += ["", "  By label (studies, passed):"]
    lines += [f"    {label:<14} {row['studies']}, {row['passed']}" for label, row in m["labels"].items()]
    fa, st, sel = m["false_agreement"], m["steps"], m["selection"]
    n, lib, c = m["noticing"], m["library"], m["cost"]
    ch = m["challenge"]
    lines += ["",
              f"  False agreement: {frac(fa['replicated_failed'])} replicated studies failed; first answers agreed in "
              f"{fa['first_answers_agreed']}, both wrong in {fa['first_agreed_both_wrong']}.",
              f"  Briefing went to: " + "; ".join(f"{k} in {v} studies" for k, v in m["briefing"]["by_teams"].items())
              + ".",
              f"  Challenge: {frac(ch['challenged'])} agreed first answers challenged; {frac(ch['questioned'])} "
              f"drew questions; {frac(ch['changed'])} changed the agreed answer; {frac(ch['disagreed_after'])} left the "
              f"teams disagreeing. {st['challenge']['rescued']} rescued, {st['challenge']['damaged']} damaged.",
              f"  Question round: {st['questions']['rescued']} rescued, {st['questions']['damaged']} damaged "
              f"({st['questions']['compared']} team answers). Referee: {st['referee']['rescued']} rescued, "
              f"{st['referee']['damaged']} damaged. Team C: {st['team_c']['rescued']} rescued, "
              f"{st['team_c']['damaged']} damaged.",
              f"  Selection: the lab passed {frac(sel['lab_passed'])} studies where some answer passed; shipped a "
              f"failing one in: {', '.join(sel['passing_candidate_but_failing_final']) or 'none'}.",
              f"  Noticing: {n['warning_signs']} warning signs; {n['questions_proposed']} questions, "
              f"{n['questions_blocked']} blocked, {n['questions_delivered']} delivered; {n['replies_changed_method']} of "
              f"{n['replies']} replies changed the method; {n['changed_failing_to_passing']} of "
              f"{n['teams_changed_answer']} changed answers went from failing to passing.",
              f"  Library: {lib['pitfalls_found']} pitfalls ({lib['pitfalls_with_sources']} with sources); "
              f"{lib['pitfall_checks']} checks, {lib['found_in_data']} found in the data, {lib['changed_method']} "
              "changed a method.",
              *search_lines(m),
              f"  Cost: lab {_cost_text(c['overall']['lab'])}; single agents {_cost_text(c['overall']['baseline'])}; "
              f"passes per dollar lab {c['passes_per_dollar']['lab']}, single {c['passes_per_dollar']['single_attempts']}."]
    if m["missing"]:
        lines += ["", "  Missing:"] + [f"    {x}" for x in m["missing"]]
    return lines


def _tally_text(tally: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def search_lines(m: dict) -> list[str]:
    """The summary's lines on web search, research on demand and outcome kinds (empty for an older measures.json)."""
    lines = []
    s, r, k = m.get("search"), m.get("research"), m.get("kinds")
    if s:
        lines.append(f"  Web search: {s['calls'].get('library', 0)} calls by the library's first pass, "
                     f"{s['calls'].get('team-a', 0)} by team A's analysts, {s['calls'].get('research', 0)} by research "
                     f"on demand; {sum(s['refused'].values())} refused by the budget. {s['learned']} LEARNED facts written.")
    if r:
        v = r["revision"]
        lines.append(f"  Research on demand: {r['asked']} questions asked, {r['blocked']} blocked, {r['answered']} answered; "
                     f"{r['delivered']} findings delivered. The revision round after them: {v['rescued']} rescued, "
                     f"{v['damaged']} damaged ({v['compared']} team answers in {len(r['studies'])} studies).")
    if k and (set(k["lab_final"]) | set(k["single_attempts"])) - {"not given"}:
        lines.append(f"  Outcome kinds: lab final answers {_tally_text(k['lab_final'])}; single attempts "
                     f"{_tally_text(k['single_attempts'])}.")
    return lines


def compare_lines(a: dict, b: dict) -> list[str]:
    """The headline of two runs side by side."""
    def values(m: dict) -> list[str]:
        rows = [value for _, value in headline_rows(m)]
        rows[3] += f" (k = {m['headline']['same_budget_vote']['k']})"
        return rows

    names = [name.split(" (")[0] for name, _ in headline_rows(a)]
    lines = [f"  {'headline (passed)':<32} {a['run']:<20} {b['run']}"]
    for name, va, vb in zip(names, values(a), values(b)):
        lines.append(f"  {name:<32} {va:<20} {vb}")
    return lines
