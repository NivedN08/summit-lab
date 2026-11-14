"""Write `<run_dir>/report.md`: what happened in each study, in plain words, then the totals.

Built only from files the lab wrote: study.json per study, events.jsonl, and results.json if
`grade` has run.
"""

from __future__ import annotations

import collections
import json
import pathlib

from lab.eventlog import JsonlEventLog

HOW = {
    "replicated": "Teams A and B answered blind and agreed.",
    "single-source": "Only one team gave an answer in the right format, so it is reported unchecked.",
    "no-answer": "Neither team gave an answer in the right format.",
    "converged": "Teams A and B disagreed, answered the director's questions, and then agreed.",
    "refereed": "Teams A and B still disagreed after the questions; the referee's test picked one answer.",
    "third-team": "The referee's test settled nothing; a third blind team agreed with one of the first two.",
    "unresolved": "No two teams agreed and the referee picked neither.",
}


def _fmt_answer(values: dict) -> str:
    return ", ".join(f"{k} = {v}" for k, v in values.items()) or "(none)"


def study_section(record: dict, grade: dict | None) -> list[str]:
    lines = [f"## {record['study']}: {record['status']}", "", HOW.get(record["status"], "")]
    final = record.get("final")
    lines.append(f"Reported answer: from {final['agent']} ({final['round']} round)." if final else "No answer reported.")
    if grade is not None:
        lines.append(f"Grade: {'passed' if grade.get('passed') else 'failed'} (score {grade.get('score')}).")
    lines.append("")
    if record.get("candidates"):
        lines.append("Answers given:")
        for c in record["candidates"]:
            state = _fmt_answer(c["answer"]) if c["valid"] else "invalid: " + "; ".join(c["problems"])
            lines.append(f"- {c['agent']}, {c['round']} round: {state}")
        lines.append("")
    if record.get("comparisons"):
        lines.append("Comparisons:")
        for c in record["comparisons"]:
            differ = [k for k, ok in c["fields"].items() if not ok]
            verdict = "agree" if c["agree"] else "differ on " + ", ".join(differ)
            lines.append(f"- {c['round']}: {' and '.join(c['between'])} {verdict}")
        lines.append("")
    if record.get("briefing"):
        lines.append(f"The director passed {len(record['briefing'])} item(s) from the library to the teams.")
        lines.append("")
    if record.get("questions"):
        lines.append("Questions the director wrote:")
        for q in record["questions"]:
            state = "delivered" if q["delivered"] else "blocked (" + "; ".join(q["reasons"]) + ")"
            lines.append(f"- to {q['to']}, {state}: {q['question']}")
        lines.append("")
    if record.get("warning_signs_to_team_c"):
        sent = sum(q["delivered"] for q in record["warning_signs_to_team_c"])
        lines.append(f"Team C received {sent} of {len(record['warning_signs_to_team_c'])} questions as warning signs.")
        lines.append("")
    ref = record.get("referee")
    if ref:
        picked = ref.get("picked_agent") or "neither"
        lines.append(f"Referee: picked {picked}. Test: {ref.get('test') or '-'}. Outcome: {ref.get('outcome') or '-'}. "
                     f"Reason: {ref.get('reason') or '-'}")
        lines.append("")
    if record.get("not_chosen"):
        lines.append("Not chosen:")
        for n in record["not_chosen"]:
            lines.append(f"- {n['agent']} ({n['round']} round): {n['reason']}")
        lines.append("")
    if record.get("unsanctioned_pass_downs"):
        lines.append(f"Warning: {len(record['unsanctioned_pass_downs'])} pass-down(s) to a team did not go through the "
                     "lab's leak check (the director wrote them itself).")
        lines.append("")
    return lines


def write_report(run_dir: pathlib.Path) -> pathlib.Path:
    run_dir = pathlib.Path(run_dir)
    results_path = run_dir / "results.json"
    results = json.loads(results_path.read_text()) if results_path.is_file() else {}
    grades = results.get("studies", {})
    lines = [f"# Report for {run_dir.name}", ""]
    if not results:
        lines += ["Not graded yet (run `python3 -m lab.run grade`).", ""]
    records = sorted(run_dir.glob("*/study.json"))
    statuses = collections.Counter()
    for path in records:
        record = json.loads(path.read_text())
        statuses[record["status"]] += 1
        lines += study_section(record, (grades.get(record["study"], {}).get("lab") or {}).get("grade"))

    lines += ["## Totals", ""]
    if statuses:
        lines.append("Studies by status: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())) + ".")
    events = JsonlEventLog(run_dir / "events.jsonl").events("agent.finish") if (run_dir / "events.jsonl").exists() else []
    by_role = collections.defaultdict(lambda: {"runs": 0, "failed": 0, "seconds": 0.0, "cost": 0.0})
    for e in events:
        row = by_role[e.get("role") or "unknown"]
        row["runs"] += 1
        row["failed"] += 0 if e.get("ok") else 1
        row["seconds"] += float(e.get("seconds") or 0)
        row["cost"] += float(e.get("cost_usd") or 0)
    if by_role:
        lines += ["", "| Role | Agent runs | Failed | Minutes | Cost (USD) |", "|---|---|---|---|---|"]
        for role, r in sorted(by_role.items()):
            lines.append(f"| {role} | {r['runs']} | {r['failed']} | {r['seconds'] / 60:.1f} | {r['cost']:.2f} |")
        total = {k: sum(r[k] for r in by_role.values()) for k in ("runs", "failed", "seconds", "cost")}
        lines.append(f"| all | {total['runs']} | {total['failed']} | {total['seconds'] / 60:.1f} | {total['cost']:.2f} |")
    t = results.get("totals")
    if t:
        lines += ["", f"Graded: the lab passed {t['lab_passed']} of {t['lab_studies']} studies; single agents passed "
                      f"{t['baseline_attempts_passed']} of {t['baseline_attempts']} attempts; the vote over attempts "
                      f"passed {t['vote_passed']} of {t['vote_studies']} studies."]
    out = run_dir / "report.md"
    out.write_text("\n".join(lines).rstrip() + "\n")
    return out
