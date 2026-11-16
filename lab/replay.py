"""Rebuild each study's decision from the event log alone, and check it against study.json.

`decide(events, study_id)` reapplies the decision rules of StudyRun.run (lab/protocol.py) to the
events of the study's last attempt (from its last study.start): candidate, candidate.none,
compare, relay.leak_check, research.delivered and referee.verdict, plus the switches in the
lab.start event that started it. It never reads study.json, and it ignores study.final, so the
two are independent records of the same decision.

The rules, in the order StudyRun.run applies them:

  analysis    teams A and B answer. Only one valid: single-source (that answer). None: no-answer.
  agree       challenge off: replicated (team A's first answer). Challenge on and no question and
              no research finding delivered: replicated (team A's first answer). Otherwise both
              teams revise (an invalid revision keeps the team's earlier answer), and agreeing
              again is replicated (team A's current answer); disagreeing goes to the referee.
  disagree    questions on: both teams revise; agreeing now is converged (team A's current answer).
  referee     on, and it picks X or Y: refereed (the picked team's current answer).
  team C      agrees with A or B: third-team (team C's answer). Otherwise unresolved: the referee's
              own answer when valid, else team C's when valid, else no answer.

`timeline(run_dir, study_id)` lists what happened, one plain line per step.
"""

from __future__ import annotations

import json
import pathlib

STATUSES = ("replicated", "converged", "refereed", "third-team", "unresolved", "single-source", "no-answer")
# Switches a lab.start config may lack in a run older than the step: a missing step did not run.
SWITCH_DEFAULTS = {"questions": True, "referee": True, "challenge": False}


def read_events(run_dir: pathlib.Path | str) -> list[dict]:
    """Every event of a run directory, without creating or locking anything."""
    path = pathlib.Path(run_dir) / "events.jsonl"
    out = []
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def study_events(events: list[dict], study_id: str) -> tuple[list[dict], dict]:
    """The study's events from its last study.start on, and the config of the lab.start before it."""
    starts = [e for e in events if e.get("kind") == "study.start" and e.get("study") == study_id]
    if not starts:
        return [], {}
    begin = starts[-1]["seq"]
    labs = [e for e in events if e.get("kind") == "lab.start" and e["seq"] < begin
            and study_id in (e.get("studies") or [])]
    config = (labs[-1].get("config") if labs else None) or {}
    return [e for e in events if e["seq"] >= begin and e.get("study") == study_id], config


def _final(study_id: str, team: str, round_: str) -> dict:
    return {"agent": f"{study_id}-{team}", "team": team, "round": round_}


def decide(events: list[dict], study_id: str) -> dict:
    """The study's status and final answer, rebuilt from events. Returns
    {"status": ..., "final": {"agent", "team", "round"} or None}; when the events stop before a
    decision (an unfinished study), status is None and "incomplete" says where they stop."""
    evs, config = study_events(events, study_id)
    if not evs:
        return {"status": None, "final": None, "incomplete": "no study.start"}
    switch = {k: bool(config.get(k, default)) for k, default in SWITCH_DEFAULTS.items()}
    valid: dict[tuple[str, str], bool] = {}           # (team, round) -> the answer passed the format check
    for e in evs:
        if e["kind"] == "candidate":
            valid[(e.get("team"), e.get("round"))] = bool(e.get("valid"))
        elif e["kind"] == "candidate.none":
            valid[(e.get("team"), e.get("round"))] = False
    compares = {}
    for e in evs:
        if e["kind"] == "compare":
            compares.setdefault(e.get("round"), []).append(bool(e.get("agree")))

    def incomplete(where: str) -> dict:
        return {"status": None, "final": None, "incomplete": where}

    def done(status: str, final: tuple[str, str] | None) -> dict:
        return {"status": status, "final": _final(study_id, *final) if final else None}

    def agreed(round_: str) -> bool | None:
        found = compares.get(round_)
        return None if not found else any(found)

    if ("team-a", "analysis") not in valid or ("team-b", "analysis") not in valid:
        return incomplete("the first answers")
    va, vb = valid[("team-a", "analysis")], valid[("team-b", "analysis")]
    if not (va and vb):
        if va or vb:
            return done("single-source", ("team-a" if va else "team-b", "analysis"))
        return done("no-answer", None)
    current = {"team-a": "analysis", "team-b": "analysis"}   # the round of each team's answer in use

    def revise(round_: str) -> bool:
        """Both teams revised in this round: keep each valid revision. False if the round never ran."""
        if ("team-a", round_) not in valid and ("team-b", round_) not in valid:
            return False
        for team in ("team-a", "team-b"):
            if valid.get((team, round_)):
                current[team] = round_
        return True

    first = agreed("analysis")
    if first is None:
        return incomplete("the first comparison")
    if first:
        if not switch["challenge"]:
            return done("replicated", ("team-a", "analysis"))
        if not any(e["kind"] == "agent.start" and e.get("phase") == "challenge" for e in evs):
            return incomplete("the challenge")
        asked = sum(1 for e in evs if e["kind"] == "relay.leak_check" and e.get("round") == "challenge"
                    and e.get("passed"))
        findings = sum(int(e.get("deliveries") or 0) for e in evs
                       if e["kind"] == "research.delivered" and e.get("round") == "challenge")
        if not asked and not findings:
            return done("replicated", ("team-a", "analysis"))
        if not revise("challenge") or agreed("challenge") is None:
            return incomplete("the revision after the challenge")
        if agreed("challenge"):
            return done("replicated", ("team-a", current["team-a"]))
    elif switch["questions"]:
        if not revise("revise") or agreed("revise") is None:
            return incomplete("the revision after the question round")
        if agreed("revise"):
            return done("converged", ("team-a", current["team-a"]))

    verdicts = [e for e in evs if e["kind"] == "referee.verdict"]
    own_valid = False
    if switch["referee"]:
        if not verdicts:
            return incomplete("the referee")
        verdict = verdicts[-1]
        own_valid = bool(verdict.get("own_answer_valid"))
        if verdict.get("pick") in ("X", "Y"):
            team = "team-a" if verdict.get("picked_agent") == f"{study_id}-team-a" else "team-b"
            return done("refereed", (team, current[team]))
    if ("team-c", "third-team") not in valid:
        return incomplete("team C")
    c_valid = valid[("team-c", "third-team")]
    if c_valid and agreed("third-team"):
        return done("third-team", ("team-c", "third-team"))
    if own_valid:
        return done("unresolved", ("referee", "referee"))
    return done("unresolved", ("team-c", "third-team") if c_valid else None)


def recorded(run_dir: pathlib.Path | str, study_id: str) -> dict | None:
    """The decision study.json records, in decide()'s shape; None when there is no study.json."""
    path = pathlib.Path(run_dir) / study_id / "study.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    final = record.get("final")
    return {"status": record.get("status"),
            "final": {k: final.get(k) for k in ("agent", "team", "round")} if final else None}


def _money(value) -> str:
    return "no cost" if value is None else f"${float(value):.2f}"


def describe(e: dict) -> str | None:
    """One readable line for an event that marks a step; None for events that do not."""
    kind = e.get("kind")
    if kind == "study.start":
        return "study started"
    if kind == "agent.finish" and e.get("role") != "baseline":   # single-agent attempts are another arm
        state = "ok" if e.get("ok") else f"failed ({e.get('error')})"
        return (f"{e.get('phase')}: {e.get('agent')} ran, attempt {e.get('attempt')}, {state}, "
                f"{float(e.get('seconds') or 0):.0f} s, {_money(e.get('cost_usd'))}")
    if kind == "relay.briefing":
        return f"briefing: {e.get('items')} item(s) passed down to {', '.join(e.get('teams') or [])}"
    if kind == "candidate":
        problems = "; ".join(e.get("problems") or [])
        return f"answer {e.get('team')} {e.get('round')}: " + ("valid" if e.get("valid") else f"invalid ({problems})")
    if kind == "candidate.none":
        return f"answer {e.get('team')} {e.get('round')}: none ({e.get('reason')})"
    if kind == "candidate.salvaged":
        return f"answer {e.get('team')} {e.get('round')}: salvaged after a time-out"
    if kind == "compare":
        fields = ", ".join(f"{k} {'yes' if v else 'no'}" for k, v in (e.get("fields") or {}).items())
        return (f"compare {e.get('round')}: {' and '.join(e.get('between') or [])} "
                f"{'agree' if e.get('agree') else 'disagree'} ({fields})")
    if kind == "relay.leak_check":
        outcome = "delivered" if e.get("passed") else "blocked: " + "; ".join(e.get("reasons") or [])
        return f"{e.get('round')} question to {e.get('to')}: {outcome}"
    if kind == "research.leak_check":
        outcome = "sent to the library" if e.get("passed") else "blocked: " + "; ".join(e.get("reasons") or [])
        return f"research question ({e.get('round')}): {outcome}"
    if kind == "research.delivered":
        return f"research ({e.get('round')}): {e.get('findings')} finding(s), {e.get('deliveries')} delivery(ies)"
    if kind == "referee.assign":
        return "referee: " + ", ".join(f"{k} = {v}" for k, v in (e.get("mapping") or {}).items())
    if kind == "referee.verdict":
        own = ", its own answer valid" if e.get("own_answer_valid") else ""
        return f"referee picked {e.get('pick')}" + (f" ({e.get('picked_agent')})" if e.get("picked_agent") else "") + own
    if kind == "lab.halted":
        return f"lab halted: {e.get('reason')}"
    if kind == "study.error":
        return f"study stopped with an error: {e.get('error')}"
    if kind == "study.final":
        final = f"{e.get('final_agent')} ({e.get('final_round')})" if e.get("final_agent") else "no answer"
        return f"final: {e.get('status')}, {final}"
    return None


def timeline(run_dir: pathlib.Path | str, study_id: str) -> list[str]:
    """What happened in the study's last attempt, one readable line per step."""
    evs, _ = study_events(read_events(run_dir), study_id)
    lines = []
    for e in evs:
        text = describe(e)
        if text:
            lines.append(f"{e['seq']:>6}  {text}")
    return lines


def studies_in(events: list[dict]) -> list[str]:
    """Study ids that have a study.start, in order of first start."""
    return list(dict.fromkeys(e["study"] for e in events if e.get("kind") == "study.start" and e.get("study")))


def replay(run_dir: pathlib.Path | str, study: str | None = None, out=print) -> int:
    """Print each study's timeline and whether the rebuilt decision matches study.json.
    Returns the number of mismatches. A study with no study.json (it never finished) is reported, not counted."""
    run_dir = pathlib.Path(run_dir)
    events = read_events(run_dir)
    ids = [study] if study else studies_in(events)
    if not ids:
        out("no lab study in this event log (single-agent attempts only?)")
    mismatches = 0
    for sid in ids:
        out(f"== {sid}")
        for line in timeline(run_dir, sid):
            out("  " + line)
        rebuilt, record = decide(events, sid), recorded(run_dir, sid)
        if record is None:
            out(f"  UNFINISHED: no study.json; rebuilt from events: {rebuilt}")
            continue
        same = rebuilt.get("status") == record["status"] and rebuilt.get("final") == record["final"]
        mismatches += not same
        out(f"  {'MATCH' if same else 'MISMATCH'}: rebuilt {rebuilt['status']} {rebuilt['final']}; "
            f"study.json {record['status']} {record['final']}")
    return mismatches
