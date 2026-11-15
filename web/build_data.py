"""Turn one lab run's event log into the compact JSON the web front end replays.

    python web/build_data.py                      # every run in results/ -> web/public/runs/
    python web/build_data.py results/test-v2      # one run

Reads only a run's events.jsonl, its study.json files and results.json (grades, written after
every agent had exited), plus records/exa_calls.jsonl for the librarians' searches. Never reads
data/ or a run's workspaces. Local paths, session ids and workspace fields are dropped.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "runs"
EXA_LOG = ROOT / "records" / "exa_calls.jsonl"

# Plain-language names for the GeneBench-Pro test problems, written from the librarians' own
# first searches (the problem folders have neutral ids on purpose).
TITLES = {
    "gb01": "Development problem 1",
    "gb02": "Development problem 2",
    "gb03": "Chromatin loop strength from Hi-C",
    "gb04": "Natural selection from allele frequencies over time",
    "gb05": "Structural variant dosage and disease risk",
    "gb06": "Single-cell eQTL for CXCL10",
    "gb07": "Residual risk after carrier screening",
    "gb08": "CRISPRi screen of long non-coding RNAs",
    "gb09": "Multivariable Mendelian randomization",
    "gb10": "Multi-parent QTL mapping",
    # Version 3 development tasks (BioDSA-1K and scBench), named from the librarians' first searches.
    "bd01": "Splicing factor mutations in cancer",
    "bd02": "Mutations in a pancreatic cyst study",
    "bd03": "TP53 subtype, aneuploidy and survival",
    "bd04": "A poor-outcome subgroup in neuroblastoma",
    "sc01": "Annotating single-nucleus clusters by markers",
    "sc07": "What drives the first principal component",
    "sc08": "Cell type markers in single-nucleus data",
}

LABELS = {
    "test-v2": "GeneBench-Pro test, 8 studies",
    "pilot1": "Pilot, 2 development studies",
    "dev-v2": "Design version 2, development studies",
    "dev-v2-local": "Design version 2, development studies",
    "biodsa-pilot": "Version 3 pilot, BioDSA-1K, 4 studies",
    "sc-pilot": "Version 3 pilot, scBench, 3 studies",
}

HOME = re.compile(r"/Users/[^/\s\"']+")
PATHLIKE = re.compile(r"(~|/Users/[^/\s\"']+)/Downloads/summit-lab/")


def clean(text):
    """Drop local paths from any text an agent wrote."""
    if not isinstance(text, str):
        return text
    text = PATHLIKE.sub("", text)
    return HOME.sub("~", text)


def parse_time(stamp: str) -> float:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def role_of(agent: str) -> str:
    if agent == "director":
        return "director"
    if agent.startswith("baseline-"):
        return "solo"
    if agent.startswith("head-"):
        return "head"
    for role in ("library", "team-a", "team-b", "team-c", "referee"):
        if agent.endswith(role):
            return role
    return "other"


def study_of(agent: str) -> str | None:
    match = re.search(r"(gb\d\d|sc\d\d|bd\d\d)", agent)
    return match.group(1) if match else None


def clean_heads(heads: dict) -> dict:
    """The department heads' reviews from study.json (D-22), with agent text cleaned."""
    out = {}
    for key, rv in heads.items():
        if not isinstance(rv, dict):
            continue
        item = {"ran": rv.get("ran"), "verdict": clean(rv.get("verdict"))}
        if key == "library":
            item.update({k: [{kk: clean(vv) for kk, vv in x.items()} if isinstance(x, dict) else clean(x)
                             for x in rv.get(k) or []] for k in ("strong", "weak", "missing")})
        else:
            item.update(approve=rv.get("approve"), revised=rv.get("revised"), changed_answer=rv.get("changed_answer"),
                        notes=[{"text": clean(n.get("text")), "why": clean(n.get("why")), "delivered": n.get("delivered"),
                                "reasons": n.get("reasons") or []} for n in rv.get("notes") or []])
        out[key] = item
    return out


def build(run_dir: pathlib.Path) -> dict:
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line.strip()]
    if not events:
        raise SystemExit(f"{run_dir}: no events")
    t0 = parse_time(events[0]["time"])
    t_end = parse_time(events[-1]["time"])

    def rel(stamp: str) -> float:
        return round(parse_time(stamp) - t0, 2)

    agents: dict[str, dict] = {}
    notes: dict[str, dict] = {}
    out: list[dict] = []
    config: dict = {}
    study_ids: list[str] = []
    totals = None
    lab_end = None
    exa_events = []   # the gateway's record of each search, used when records/exa_calls.jsonl lacks this run

    def agent(agent_id: str) -> dict:
        if agent_id not in agents:
            agents[agent_id] = {"id": agent_id, "role": role_of(agent_id), "study": study_of(agent_id),
                                "model": None, "sessions": []}
        return agents[agent_id]

    for e in events:
        kind, actor, t = e["kind"], e["actor"], rel(e["time"])
        if kind == "lab.start":
            config = e.get("config", {})
            study_ids = e.get("studies", [])
            out.append({"t": t, "k": kind})
        elif kind == "agent.start":
            a = agent(e["agent"])
            a["model"] = e.get("model") or a["model"]
            a["sessions"].append({"phase": e.get("phase"), "study": e.get("study"), "start": t, "end": None})
            out.append({"t": t, "k": kind, "a": e["agent"], "s": e.get("study"), "phase": e.get("phase")})
        elif kind == "agent.finish":
            a = agent(e["agent"])
            for session in reversed(a["sessions"]):
                if session["end"] is None and session["phase"] == e.get("phase"):
                    session.update(end=t, ok=e.get("ok"), seconds=round(e.get("seconds") or 0, 1),
                                   cost=round(e.get("cost_usd") or 0, 3), error=clean(e.get("error")))
                    break
            out.append({"t": t, "k": kind, "a": e["agent"], "s": e.get("study"), "phase": e.get("phase"),
                        "ok": e.get("ok")})
        elif kind == "memory.write":
            fid = e["fact_id"]
            notes[fid] = {"id": fid, "t": t, "kind": "note", "by": actor, "dept": e.get("department"),
                          "label": clean(e.get("label")), "text": clean(e.get("text"))}
            out.append({"t": t, "k": kind, "a": actor, "s": study_of(actor), "f": fid})
        elif kind == "memory.pass_down":
            fid = e["fact_id"]
            notes[fid] = {"id": fid, "t": t, "kind": "pass_down", "by": actor, "dept": e.get("to_department"),
                          "to": e.get("to_department"), "reason": clean(e.get("reason")),
                          "text": clean(e.get("text")), "came_from": e.get("came_from", [])}
            out.append({"t": t, "k": kind, "a": actor, "s": study_of(e.get("to_department", "")),
                        "to": e.get("to_department"), "f": fid})
        elif kind == "memory.recall":
            out.append({"t": t, "k": kind, "a": actor, "s": study_of(actor), "q": clean(e.get("query"))})
        elif kind == "relay.briefing":
            out.append({"t": t, "k": kind, "s": e.get("study"), "items": e.get("items"), "teams": e.get("teams")})
        elif kind == "relay.leak_check":
            item = {"t": t, "k": kind, "s": e.get("study"), "round": e.get("round"), "to": e.get("to"),
                    "passed": e.get("passed"), "q": clean(e.get("question"))}
            if e.get("by"):
                item["by"] = e["by"]
            if not e.get("passed") and e.get("reasons"):
                item["why"] = "; ".join(e["reasons"])
            out.append(item)
        elif kind == "head.review":
            out.append({"t": t, "k": kind, "a": e.get("head"), "s": e.get("study"), "of": e.get("of"),
                        "ok": e.get("ran"), "approve": e.get("approve"), "notes": e.get("notes"),
                        "delivered": e.get("delivered"), "weak": e.get("weak"), "missing": e.get("missing")})
        elif kind == "candidate":
            out.append({"t": t, "k": kind, "s": e.get("study"), "team": e.get("team"), "round": e.get("round"),
                        "valid": e.get("valid")})
        elif kind == "compare":
            out.append({"t": t, "k": kind, "s": e.get("study"), "round": e.get("round"),
                        "between": e.get("between"), "fields": e.get("fields"), "agree": e.get("agree")})
        elif kind == "referee.assign":
            out.append({"t": t, "k": kind, "s": e.get("study"), "mapping": e.get("mapping")})
        elif kind == "referee.verdict":
            out.append({"t": t, "k": kind, "s": e.get("study"), "pick": e.get("pick"),
                        "picked": e.get("picked_agent")})
        elif kind in ("study.start", "study.final"):
            item = {"t": t, "k": kind, "s": e.get("study")}
            if kind == "study.final":
                item.update(status=e.get("status"), final=e.get("final_agent"), round=e.get("final_round"))
            out.append(item)
        elif kind == "baseline.done":
            out.append({"t": t, "k": kind, "s": e.get("study"), "attempt": e.get("attempt_number"), "ok": e.get("ok")})
        elif kind == "lab.halted":
            out.append({"t": t, "k": kind, "why": clean(e.get("reason"))})
        elif kind == "grade.done":
            totals = e.get("totals")
            out.append({"t": t, "k": kind, "totals": totals})
        elif kind == "gateway.modal.stop":
            lab_end = t
        elif kind == "exa.call":
            exa_events.append({"t": t, "a": e.get("agent") or actor, "endpoint": e.get("endpoint") or "search"})

    # The librarians' web searches, from the Exa call record, inside this run's time window.
    searches = []
    if EXA_LOG.exists():
        for line in EXA_LOG.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("agent") not in agents:
                continue
            t = parse_time(r["time"]) - t0
            if t < -5 or t > t_end - t0 + 5:
                continue
            searches.append({
                "t": round(max(t, 0), 1), "a": r["agent"], "s": study_of(r["agent"]), "endpoint": r.get("endpoint"),
                "q": clean(r.get("query") or r.get("objective")), "urls": r.get("urls"),
                "results": [{"title": clean((x.get("title") or "").strip()), "url": x.get("url"),
                             "published": x.get("published") or ""} for x in (r.get("results") or [])][:8],
            })
            out.append({"t": round(max(t, 0), 1), "k": "web." + (r.get("endpoint") or "search"), "a": r["agent"],
                        "s": study_of(r["agent"]), "n": len(searches) - 1})
    recorded = {x["a"] for x in searches}
    for x in exa_events:
        if x["a"] not in recorded:   # no words for this search, only that it happened
            out.append({"t": x["t"], "k": "web." + x["endpoint"], "a": x["a"], "s": study_of(x["a"])})
    out.sort(key=lambda x: x["t"])

    # Per study: the outcome, the director's challenge and the referee's test, from study.json.
    grades = {}
    if (run_dir / "results.json").exists():
        grades = json.loads((run_dir / "results.json").read_text()).get("studies", {})
    studies = []
    for sid in study_ids or sorted({a["study"] for a in agents.values() if a["study"]}):
        sfile = run_dir / sid / "study.json"
        s = json.loads(sfile.read_text()) if sfile.exists() else {}
        g = grades.get(sid, {})
        challenge = s.get("challenge") or None
        referee = s.get("referee") or None
        studies.append({
            "id": sid,
            "title": TITLES.get(sid, sid),
            "status": s.get("status"),
            "final": (s.get("final") or {}).get("agent"),
            "final_round": (s.get("final") or {}).get("round"),
            "briefed": s.get("briefed_teams"),
            "candidates": [{"agent": c.get("agent"), "team": c.get("team"), "round": c.get("round"),
                            "valid": c.get("valid"), "answer": c.get("answer")} for c in s.get("candidates", [])],
            "comparisons": s.get("comparisons", []),
            "questions": [{"to": q.get("to"), "q": clean(q.get("question"))} for q in s.get("questions", [])],
            "challenge": None if not challenge else {
                k: clean(v) if isinstance(v, str) else [clean(x) for x in v] if isinstance(v, list) else v
                for k, v in challenge.items() if k in ("shared_assumptions", "unused_evidence", "unresolved_weak_points",
                                                       "second_readings", "verdict", "questions")},
            "challenge_changed_answer": s.get("challenge_changed_answer"),
            "referee": None if not referee else {k: clean(v) for k, v in referee.items()
                                                 if k in ("difference", "test", "result", "verdict", "pick", "reason")},
            "not_chosen": [{"agent": n.get("agent"), "round": n.get("round"), "reason": clean(n.get("reason"))}
                           for n in s.get("not_chosen", [])],
            "heads": clean_heads(s.get("heads") or {}),
            "grade": None if not g else {
                "lab": (g.get("lab", {}).get("grade") or {}).get("passed"),
                "solo": [v.get("passed") for v in (g.get("baseline", {}).get("attempts") or {}).values()],
                "vote": (g.get("baseline", {}).get("vote") or {}).get("passed"),
            },
        })

    for a in agents.values():
        a["sessions"].sort(key=lambda x: x["start"])
        a["cost"] = round(sum(x.get("cost") or 0 for x in a["sessions"]), 3)

    name = run_dir.name
    return {
        "run": name,
        "label": LABELS.get(name, name),
        "started": events[0]["time"],
        "duration": round(t_end - t0, 1),
        "lab_end": lab_end,
        "finished": totals is not None,
        "config": {k: config.get(k) for k in ("director_model", "worker_model", "timeouts", "max_searches",
                                               "brief_teams", "challenge", "referee", "library")},
        "studies": studies,
        "agents": sorted(agents.values(), key=lambda a: (a["study"] or "", a["id"])),
        "events": out,
        "notes": notes,
        "searches": searches,
        "totals": totals,
    }


def main(argv: list[str]) -> None:
    runs = [pathlib.Path(p) for p in argv] or sorted(p.parent for p in (ROOT / "results").glob("*/events.jsonl"))
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for run_dir in runs:
        data = build(run_dir)
        if not data["studies"] or not any(a["role"] == "director" for a in data["agents"]):
            continue  # single-model comparison runs have no lab to show
        (OUT / f"{data['run']}.json").write_text(json.dumps(data, separators=(",", ":")))
        index.append({"run": data["run"], "label": data["label"], "started": data["started"],
                      "studies": len(data["studies"]), "agents": len(data["agents"]), "finished": data["finished"]})
        print(f"{data['run']}: {len(data['events'])} events, {len(data['agents'])} agents, "
              f"{len(data['notes'])} notes, {len(data['searches'])} searches")
    order = {"test-v2": 0}
    index.sort(key=lambda r: (order.get(r["run"], 1), r["started"]), reverse=False)
    (OUT / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
