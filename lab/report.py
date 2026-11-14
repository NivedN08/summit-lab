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


def challenge_line(record: dict) -> str:
    """One sentence on the director's challenge of an agreed answer, or "" if there was none."""
    if not record.get("challenged"):
        return ""
    n = record.get("challenge_questions") or 0
    if not n:
        return "The director challenged the agreed answer and asked no questions, so it was reported."
    changed = record.get("challenge_changed_answer")
    after = ("the teams then agreed on a different answer" if changed is True else
             "the teams kept the answer they had agreed on" if changed is False else
             "the teams then disagreed")
    return f"The director challenged the agreed answer with {n} question(s); {after}."


def study_section(record: dict, grade: dict | None) -> list[str]:
    lines = [f"## {record['study']}: {record['status']}", "", HOW.get(record["status"], "")]
    if challenge_line(record):
        lines.append(challenge_line(record))
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
        teams = record.get("briefed_teams")
        to = " and ".join(teams) if teams else "the teams"
        lines.append(f"The director passed {len(record['briefing'])} item(s) from the library to {to}.")
        lines.append("")
    challenge = record.get("challenge") or {}
    if challenge:
        lines.append(f"The director's challenge: {challenge.get('verdict') or '-'}")
        for key, name in (("shared_assumptions", "Shared assumptions"), ("unused_evidence", "Unused evidence"),
                          ("unresolved_weak_points", "Unresolved weak points"), ("second_readings", "Second readings")):
            if challenge.get(key):
                lines.append(f"- {name}: " + "; ".join(str(x) for x in challenge[key]))
        for q in challenge.get("questions") or []:
            state = "delivered" if q["delivered"] else "blocked (" + "; ".join(q["reasons"]) + ")"
            lines.append(f"- question to {q['to']}, {state}: {q['question']}")
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


def _n(c: dict) -> str:
    return f"{c['n']} of {c['of']}"


def measures_section(m: dict) -> list[str]:
    """The Measures section: the headline table, the label table and one sentence per measure."""
    from lab.measures import headline_rows
    other = m.get("compared_with")
    lines = ["## Measures", "", "Counts come first because there are few studies; treat rates as rough.", ""]
    if other:
        theirs = [v for _, v in headline_rows({"headline": other["headline"]})]
        lines += [f"| Measure | Passed ({m['run']}) | Passed ({other['run']}) |", "|---|---|---|"]
        lines += [f"| {name} | {value} | {theirs[i]} |" for i, (name, value) in enumerate(headline_rows(m))]
    else:
        lines += ["| Measure | Passed |", "|---|---|"]
        lines += [f"| {name} | {value} |" for name, value in headline_rows(m)]
    sb = m["headline"]["same_budget_vote"]
    if sb["k"]:
        lines += ["", f"The same-budget vote uses the first {sb['k']} single attempts: the lab cost "
                      f"{sb['lab_per_study']} {sb['basis']} per study and one attempt cost {sb['per_attempt']}."]
    lines += [f"Note: {note}." for note in sb["notes"]]
    lines += ["", "| Label | Studies | Passed |", "|---|---|---|"]
    lines += [f"| {label} | {row['studies']} | {row['passed']} |" for label, row in m["labels"].items()]
    fa, st, sel = m["false_agreement"], m["steps"], m["selection"]
    n, lib, c = m["noticing"], m["library"], m["cost"]
    q, ref, tc = st["questions"], st["referee"], st["team_c"]
    ch, ch_step, brief = m.get("challenge"), st.get("challenge"), m.get("briefing")
    failing = ", ".join(sel["passing_candidate_but_failing_final"]) or "none"
    lab, base = c["overall"]["lab"], c["overall"]["baseline"]

    def dollars(row: dict) -> str:
        if row["dollars"] is None:
            return "no dollar figure"
        gap = f" ({row['runs_without_cost']} runs did not report a cost)" if row["runs_without_cost"] else ""
        return f"${row['dollars']:.2f}{gap}"

    def per_study(row: dict) -> str:
        if not m.get("studies"):
            return ""
        money = f", ${row['dollars'] / m['studies']:.2f}" if row["dollars"] is not None else ""
        return f" (per study: {row['runs'] / m['studies']:.1f} runs, {row['minutes'] / m['studies']:.1f} minutes{money})"

    def per_dollar(x) -> str:
        return "not known" if x is None else f"{x:.2f}"

    lines += ["",
              f"**False agreement.** {_n(fa['replicated_failed'])} replicated studies failed the grader. The two teams' "
              f"first answers agreed in {fa['first_answers_agreed']} studies; in {fa['first_agreed_both_wrong']} of "
              f"them both answers were wrong, and in {fa['first_agreed_one_wrong']} only one was.",
              "",
              f"**Rescues and damage.** The question round turned {q['rescued']} team answers from failing to passing "
              f"and {q['damaged']} from passing to failing, out of {q['compared']}. The referee ran in {ref['ran']} "
              f"studies: it turned {ref['rescued']} team answers right and {ref['damaged']} wrong, and settled nothing "
              f"in {ref['settled_nothing']}. Team C ran in {tc['ran']} studies: it turned {tc['rescued']} studies right "
              f"and {tc['damaged']} wrong.",
              "",
              *([f"**Challenge.** {_n(ch['challenged'])} studies whose first answers agreed were challenged by the "
                 f"director; {_n(ch['questioned'])} challenges produced questions ({ch['questions_delivered']} of "
                 f"{ch['questions_written']} questions delivered). After the questions the agreed answer changed in "
                 f"{_n(ch['changed'])} and the teams no longer agreed in {_n(ch['disagreed_after'])}. Against the first "
                 f"agreed answer, the challenge turned {ch_step['rescued']} studies right and {ch_step['damaged']} "
                 f"wrong, out of {ch_step['compared']}.", ""] if ch and ch_step else []),
              *([f"**Briefing.** The library's briefing went to: "
                 + "; ".join(f"{k} in {v} studies" for k, v in brief["by_teams"].items()) + ".", ""] if brief else []),
              f"**Selection.** In {sel['lab_passed']['of']} studies at least one answer in the lab passed, and the lab "
              f"shipped a passing answer in {sel['lab_passed']['n']} of them. Studies where it shipped a failing "
              f"answer although a passing one existed: {failing}.",
              "",
              f"**Noticing to acting.** Teams recorded {n['warning_signs']} warning signs. The director proposed "
              f"{n['questions_proposed']} questions; the leak check blocked {n['questions_blocked']} and "
              f"{n['questions_delivered']} were delivered. {n['replies_changed_method']} of {n['replies']} replies "
              f"said the question changed the method. {n['teams_changed_answer']} teams changed their answer, and "
              f"{n['changed_failing_to_passing']} of them went from failing to passing.",
              "",
              f"**Library.** The library found {lib['pitfalls_found']} pitfalls, {lib['pitfalls_with_sources']} of them "
              f"with at least one source ({lib['sources']} sources in all). Teams reported {lib['pitfall_checks']} "
              f"pitfall checks: {lib['found_in_data']} found the pitfall in the data and {lib['changed_method']} "
              f"changed a method.",
              "",
              f"**Cost.** The lab used {lab['runs']} agent runs, {lab['minutes']:.1f} minutes and {dollars(lab)}"
              f"{per_study(lab)}. The single agents used {base['runs']} runs, {base['minutes']:.1f} minutes and "
              f"{dollars(base)}. "
              f"Passes per dollar: lab {per_dollar(c['passes_per_dollar']['lab'])}, single attempts "
              f"{per_dollar(c['passes_per_dollar']['single_attempts'])}."]
    if m.get("missing"):
        lines += ["", "Missing from this run: " + "; ".join(m["missing"]) + "."]
    return lines + [""]


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
    measures_path = run_dir / "measures.json"
    if measures_path.is_file():
        try:
            measured = json.loads(measures_path.read_text())
        except ValueError:
            measured = None
        lines += [""] + (measures_section(measured) if isinstance(measured, dict) else
                         ["## Measures", "", "measures.json could not be read; run `python3 -m lab.run measures` again."])
    out = run_dir / "report.md"
    out.write_text("\n".join(lines).rstrip() + "\n")
    return out
