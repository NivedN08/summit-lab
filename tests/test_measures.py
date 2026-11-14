"""Tests for grading every candidate answer (lab.run grade), lab.measures and the report's Measures section.

Runs are built with the loop itself: the fake adapter and scripted fake backend from
test_protocol.py, one plan per study, so each test controls which answers are right
(TRUTH is A; B and W are wrong) and what each agent run costs.
"""

import dataclasses
import json
import pathlib

import pytest

from lab import baseline as baseline_mod
from lab import measures, protocol, run
from lab.eventlog import JsonlEventLog
from lab.memory_local import LocalMemory
from test_protocol import A, B, CHALLENGE, FakeAdapter, FakeBackend, no_exa

W = (7.0, "other")      # another wrong answer
NO_CHALLENGE = protocol.Config(max_agents=3, challenge=False)


class StudyBackend:
    """Plays each study from its own FakeBackend plan, and sets each run's cost and time.

    `cost` and `seconds` are {"lab": x, "baseline": y}; a value of None means "not reported".
    Analysts also report one pitfall check; revising analysts report one reply, which says the
    method changed when the answer did."""

    def __init__(self, plans, cost=None, seconds=None):
        self.backends = {sid: FakeBackend(plan) for sid, plan in plans.items()}
        self.cost = cost
        self.seconds = seconds

    def run(self, agent, brief, workspace, **kwargs):
        workspace = pathlib.Path(workspace)
        fake = self.backends[workspace.parent.name]
        before = (workspace / "answer.json").read_text() if (workspace / "answer.json").is_file() else None
        result = fake.run(agent, brief, workspace, **kwargs)
        job = fake.calls[-1]["job"]
        if result.ok and job in ("analyst", "revise"):
            extra = {"pitfalls_checked": [{"pitfall": "duplicates", "found_in_data": True, "changed_method": False}]}
            if job == "revise":
                changed = (workspace / "answer.json").read_text() != before
                extra = {"replies": [{"question": "q", "check_run": "c", "finding": "f", "changes_method": changed}],
                         "revised": changed}
            data = {**result.result, **extra}
            (workspace / "result.json").write_text(json.dumps(data))
            result = dataclasses.replace(result, result=data)
        side = "baseline" if job == "baseline" else "lab"
        if self.cost is not None:
            result = dataclasses.replace(result, cost_usd=self.cost[side])
        if self.seconds is not None:
            result = dataclasses.replace(result, seconds=self.seconds[side])
        return result


def build(tmp_path, plans, attempts=None, cost=None, seconds=None, config=None, name="run"):
    """Run the lab (and the baselines, if any study's plan has them) and grade. Returns (run_dir, adapter)."""
    adapter = FakeAdapter(tmp_path / "bench")
    run_dir = tmp_path / "runs" / name
    log = JsonlEventLog(run_dir / "events.jsonl")
    memory = LocalMemory(log, run_dir / "memory.jsonl")
    backend = StudyBackend(plans, cost=cost, seconds=seconds)
    studies = [s for s in adapter.studies() if s.id in plans]
    protocol.Lab(adapter, backend, memory, log, run_dir, config or protocol.Config(max_agents=3),
                 exa_call=no_exa).run(studies)
    if attempts:
        baseline_mod.run_baselines(adapter, backend, log, run_dir, studies, attempts=attempts, model="opus",
                                   max_agents=2)
    run.grade_run(run_dir, adapter)
    return run_dir, adapter


def results(run_dir):
    return json.loads((run_dir / "results.json").read_text())


def by_id(run_dir, sid="s1"):
    return {c["id"]: c for c in results(run_dir)["studies"][sid]["candidates"]}


# ----- grade grades every candidate -----

def test_grade_grades_every_candidate(tmp_path):
    own = {"answer": {"effect": A[0], "label": A[1]}}
    plan = {"team-a": [A, A], "team-b": [B, B], "team-c": [W], "referee": {"pick": "neither", "own_answer": own},
            "baseline": [A, B, B]}
    run_dir, _ = build(tmp_path, {"s1": plan}, attempts=3)
    assert results(run_dir)["studies"]["s1"]["lab"]["status"] == "unresolved"
    cands = by_id(run_dir)
    expected = {
        "team-a-analysis": ("s1-team-a", "analysis", True),
        "team-b-analysis": ("s1-team-b", "analysis", False),
        "team-a-revise": ("s1-team-a", "revise", True),
        "team-b-revise": ("s1-team-b", "revise", False),
        "referee-own": ("s1-referee", "referee", True),
        "team-c-third-team": ("s1-team-c", "third-team", False),
        "final": ("s1-referee", "final", True),
        "attempt-1": ("baseline-s1-1", "attempt-1", True),
        "attempt-2": ("baseline-s1-2", "attempt-2", False),
        "attempt-3": ("baseline-s1-3", "attempt-3", False),
        "vote": ("vote", "vote", False),     # median of 2.718, 9.137, 9.137
    }
    assert set(cands) == set(expected)
    for cid, (who, round_, passed) in expected.items():
        assert (cands[cid]["who"], cands[cid]["round"], cands[cid]["passed"]) == (who, round_, passed), cid
    assert cands["final"]["same_as"] == "referee-own"
    # the existing keys are still there
    s1 = results(run_dir)["studies"]["s1"]
    assert s1["lab"]["grade"]["passed"] is True and s1["baseline"]["vote"]["passed"] is False
    assert [s1["baseline"]["votes_over_first"][k]["passed"] for k in ("1", "2", "3")] == [True, False, False]


def test_first_answer_is_kept_when_a_team_revises(tmp_path):
    run_dir, _ = build(tmp_path, {"s1": {"team-a": [A, A], "team-b": [B, A]}})
    first = json.loads((run_dir / "s1" / "candidates" / "team-b-analysis.json").read_text())
    revised = json.loads((run_dir / "s1" / "candidates" / "team-b-revise.json").read_text())
    assert first["answer"]["effect"] == B[0] and revised["answer"]["effect"] == A[0]
    cands = by_id(run_dir)
    assert cands["team-b-analysis"]["passed"] is False and cands["team-b-revise"]["passed"] is True


# ----- agreement, false agreement -----

def test_replicated_right_and_replicated_wrong(tmp_path):
    plans = {"s1": {"team-a": [A], "team-b": [A]}, "s2": {"team-a": [B], "team-b": [B]}}
    run_dir, _ = build(tmp_path, plans)
    m = measures.measure(run_dir)
    assert m["headline"]["lab"]["n"] == 1 and m["headline"]["lab"]["of"] == 2
    assert m["labels"]["replicated"] == {"studies": 2, "passed": 1}
    assert m["labels"]["converged"] == {"studies": 0, "passed": 0}
    fa = m["false_agreement"]
    assert (fa["replicated_failed"]["n"], fa["replicated_failed"]["of"]) == (1, 2)
    assert fa["replicated_failed_studies"] == ["s2"]
    assert fa["first_answers_agreed"] == 2 and fa["first_agreed_both_wrong"] == 1 and fa["first_agreed_one_wrong"] == 0


# ----- the challenge of agreed answers, and who was briefed -----

def test_challenge_rescue_and_damage_and_every_candidate_graded(tmp_path):
    plans = {"s1": {"team-a": [B, A], "team-b": [B, A], "challenge": CHALLENGE},   # agreed wrong, both turn right
             "s2": {"team-a": [A, B], "team-b": [A, B], "challenge": CHALLENGE}}   # agreed right, both turn wrong
    run_dir, _ = build(tmp_path, plans)
    cands = by_id(run_dir)
    passed = {cid: cands[cid]["passed"] for cid in ("team-a-analysis", "team-b-analysis", "team-a-challenge",
                                                    "team-b-challenge", "final")}
    assert passed == {"team-a-analysis": False, "team-b-analysis": False, "team-a-challenge": True,
                      "team-b-challenge": True, "final": True}
    assert cands["team-a-challenge"]["round"] == "challenge" and cands["final"]["same_as"] == "team-a-challenge"
    m = measures.measure(run_dir)
    ch = m["challenge"]
    assert (ch["challenged"]["n"], ch["challenged"]["of"]) == (2, 2)
    assert (ch["questioned"]["n"], ch["questioned"]["of"]) == (2, 2)
    assert (ch["changed"]["n"], ch["changed"]["of"]) == (2, 2)
    assert (ch["disagreed_after"]["n"], ch["disagreed_after"]["of"]) == (0, 2)
    step = m["steps"]["challenge"]
    assert (step["rescued"], step["damaged"], step["compared"]) == (1, 1, 2)
    assert {(x["study"], x["change"]) for x in step["changes"]} == {("s1", "rescued"), ("s2", "damaged")}
    assert m["labels"]["replicated"] == {"studies": 2, "passed": 1}
    assert m["briefing"]["by_teams"] == {"team-a": 2}
    assert m["briefing"]["studies"] == {"s1": ["team-a"], "s2": ["team-a"]}
    assert m["noticing"]["replies"] == 4    # the challenge round's replies count too
    assert not any("warning_signs" in x for x in m["missing"])


def test_challenge_without_questions_and_unchanged_answers(tmp_path):
    plans = {"s1": {"team-a": [A], "team-b": [A]},                                  # nothing to ask
             "s2": {"team-a": [B, B], "team-b": [B, B], "challenge": CHALLENGE}}   # asked, both keep B
    run_dir, _ = build(tmp_path, plans)
    m = measures.measure(run_dir)
    ch = m["challenge"]
    assert (ch["challenged"]["n"], ch["questioned"]["n"], ch["questioned"]["of"]) == (2, 1, 2)
    assert (ch["changed"]["n"], ch["changed"]["of"]) == (0, 1)
    step = m["steps"]["challenge"]
    assert (step["rescued"], step["damaged"], step["compared"]) == (0, 0, 2)
    assert "team-a-challenge" in by_id(run_dir, "s2") and "team-a-challenge" not in by_id(run_dir, "s1")

    old, _ = build(tmp_path, {"s1": {"team-a": [A], "team-b": [A]}}, name="old-way",
                   config=protocol.Config(max_agents=3, challenge=False, brief_teams=("team-a", "team-b")))
    m = measures.measure(old)
    assert (m["challenge"]["challenged"]["n"], m["challenge"]["challenged"]["of"]) == (0, 1)
    assert m["steps"]["challenge"]["compared"] == 0
    assert m["briefing"]["by_teams"] == {"team-a,team-b": 1}
    assert any("challenge" in line.lower() for line in measures.summary_lines(m))


# ----- rescues and damage -----

def test_question_round_rescue_and_damage(tmp_path):
    plans = {"s1": {"team-a": [A, A], "team-b": [B, A]},      # team B turned right: a rescue
             "s2": {"team-a": [A, B], "team-b": [B, B]}}      # team A turned wrong: damage
    run_dir, _ = build(tmp_path, plans)
    m = measures.measure(run_dir)
    assert m["labels"]["converged"] == {"studies": 2, "passed": 1}
    q = m["steps"]["questions"]
    assert (q["rescued"], q["damaged"], q["compared"]) == (1, 1, 4)
    assert {(x["study"], x["who"], x["change"]) for x in q["changes"]} == {
        ("s1", "team-b", "rescued"), ("s2", "team-a", "damaged")}
    n = m["noticing"]
    assert n["replies"] == 4 and n["replies_changed_method"] == 2
    assert n["teams_changed_answer"] == 2 and n["changed_failing_to_passing"] == 1
    # s2 had a passing answer (team A's first) and shipped a failing one
    sel = m["selection"]
    assert (sel["lab_passed"]["n"], sel["lab_passed"]["of"]) == (1, 2)
    assert sel["passing_candidate_but_failing_final"] == ["s2"]


def test_selection_referee_picks_the_wrong_team_and_team_c_rescues(tmp_path):
    plans = {"s1": {"team-a": [A, A], "team-b": [B, B], "referee": {"pick_team": "team-b"}},
             "s2": {"team-a": [B, B], "team-b": [W, W], "team-c": [A], "referee": {"pick": "neither"}}}
    run_dir, _ = build(tmp_path, plans)
    m = measures.measure(run_dir)
    assert m["labels"]["refereed"] == {"studies": 1, "passed": 0}
    assert m["labels"]["unresolved"] == {"studies": 1, "passed": 1}
    ref = m["steps"]["referee"]
    assert (ref["rescued"], ref["damaged"]) == (0, 1) and ref["ran"] == 2 and ref["settled_nothing"] == 1
    c = m["steps"]["team_c"]
    assert (c["rescued"], c["damaged"], c["ran"]) == (1, 0, 1)
    sel = m["selection"]
    assert (sel["lab_passed"]["n"], sel["lab_passed"]["of"]) == (1, 2)
    assert sel["passing_candidate_but_failing_final"] == ["s1"]


# ----- the same-budget vote and cost -----

def test_same_budget_vote_takes_k_from_costs(tmp_path):
    # Replicated study without the challenge: 4 lab runs at $0.25 = $1.00. One attempt costs $0.50, so k = 2.
    plan = {"team-a": [A], "team-b": [A], "baseline": [B, A, A]}
    run_dir, _ = build(tmp_path, {"s1": plan}, attempts=3, cost={"lab": 0.25, "baseline": 0.5}, config=NO_CHALLENGE)
    m = measures.measure(run_dir)
    sb = m["headline"]["same_budget_vote"]
    assert sb["basis"] == "dollars" and sb["k"] == 2
    assert (sb["n"], sb["of"]) == (0, 1)                       # vote over B, A fails
    assert m["headline"]["vote_all_attempts"]["n"] == 1        # vote over B, A, A passes
    assert (m["headline"]["single_attempts"]["n"], m["headline"]["single_attempts"]["of"]) == (2, 3)
    assert (m["headline"]["single_attempt_1"]["n"], m["headline"]["single_attempt_1"]["of"]) == (0, 1)
    cost = m["cost"]
    assert cost["overall"]["lab"]["dollars"] == pytest.approx(1.0)
    assert cost["overall"]["baseline"]["dollars"] == pytest.approx(1.5)
    assert cost["passes_per_dollar"]["lab"] == pytest.approx(1.0)
    assert cost["passes_per_dollar"]["single_attempts"] == pytest.approx(2 / 1.5, abs=1e-3)
    assert cost["by_role"]["analyst"]["runs"] == 2 and cost["per_study"]["s1"]["lab"]["runs"] == 4


def test_missing_cost_falls_back_to_time_then_to_runs(tmp_path):
    plan = {"team-a": [A], "team-b": [A], "baseline": [B, A, A]}
    run_dir, _ = build(tmp_path, {"s1": plan}, attempts=3, cost={"lab": None, "baseline": None},
                       seconds={"lab": 60.0, "baseline": 120.0}, name="timed", config=NO_CHALLENGE)
    m = measures.measure(run_dir)
    sb = m["headline"]["same_budget_vote"]
    assert sb["basis"] == "minutes" and sb["k"] == 2
    assert any("cost" in note for note in sb["notes"])
    assert m["cost"]["passes_per_dollar"]["lab"] is None
    assert any("cost" in note for note in m["missing"])

    run_dir, _ = build(tmp_path, {"s1": plan}, attempts=3, cost={"lab": None, "baseline": None},
                       seconds={"lab": 0.0, "baseline": 0.0}, name="counted", config=NO_CHALLENGE)
    sb = measures.measure(run_dir)["headline"]["same_budget_vote"]
    assert sb["basis"] == "agent runs" and sb["k_unrounded"] == pytest.approx(4.0)
    assert sb["k"] == 3                                        # at most the 3 attempts available


# ----- missing pieces, refusal, command, compare, report -----

def test_missing_pieces_do_not_crash(tmp_path):
    # no library, no baseline, no pitfall checks (the plain fake backend reports none)
    adapter = FakeAdapter(tmp_path / "bench")
    run_dir = tmp_path / "runs" / "bare"
    log = JsonlEventLog(run_dir / "events.jsonl")
    lab = protocol.Lab(adapter, FakeBackend({"team-a": [A], "team-b": [None]}), LocalMemory(log, run_dir / "m.jsonl"),
                       log, run_dir, protocol.Config(max_agents=2, library=False), exa_call=no_exa)
    lab.run(adapter.studies())
    run.grade_run(run_dir, adapter)
    m = measures.measure(run_dir)
    assert m["labels"]["single-source"] == {"studies": 2, "passed": 2}
    assert m["headline"]["single_attempts"]["of"] == 0 and m["headline"]["same_budget_vote"]["k"] is None
    assert m["library"]["pitfalls_found"] == 0 and m["library"]["pitfall_checks"] == 0
    assert m["steps"]["referee"]["ran"] == 0
    assert m["missing"]
    assert measures.summary_lines(m)


def test_measures_refuses_politely_without_results(tmp_path, capsys):
    run_dir = tmp_path / "runs" / "ungraded"
    run_dir.mkdir(parents=True)
    with pytest.raises(measures.NotGraded) as err:
        measures.measure(run_dir)
    assert "results.json" in str(err.value)
    assert run.main(["measures", "--run-dir", str(run_dir)]) == 2
    captured = capsys.readouterr()
    assert "grade" in captured.err and "Traceback" not in captured.err
    assert not (run_dir / "measures.json").exists()


def test_command_writes_measures_and_compares_two_runs(tmp_path, capsys):
    lab_run, _ = build(tmp_path, {"s1": {"team-a": [A], "team-b": [A]}}, name="with-library")
    other, _ = build(tmp_path, {"s1": {"team-a": [B], "team-b": [B]}}, name="no-library",
                     config=protocol.Config(max_agents=3, library=False))
    assert run.main(["measures", "--run-dir", str(lab_run), "--compare", str(other)]) == 0
    out = capsys.readouterr().out
    assert "with-library" in out and "no-library" in out
    saved = json.loads((lab_run / "measures.json").read_text())
    assert saved["headline"]["lab"]["n"] == 1
    assert saved["compared_with"]["run"] == "no-library" and saved["compared_with"]["headline"]["lab"]["n"] == 0


def test_report_has_a_measures_section(tmp_path):
    plans = {"s1": {"team-a": [A, A], "team-b": [B, A], "baseline": [A, B]},
             "s2": {"team-a": [B], "team-b": [B], "baseline": [B, B]}}
    run_dir, _ = build(tmp_path, plans, attempts=2, cost={"lab": 0.25, "baseline": 0.5})
    assert run.main(["report", "--run-dir", str(run_dir)]) == 0
    assert "## Measures" not in (run_dir / "report.md").read_text()
    assert run.main(["measures", "--run-dir", str(run_dir)]) == 0
    assert run.main(["report", "--run-dir", str(run_dir)]) == 0
    report = (run_dir / "report.md").read_text()
    section = report[report.index("## Measures"):]
    for words in ("Lab final answers", "Single-agent attempts", "Same-budget vote", "| replicated |",
                  "False agreement", "Rescues and damage", "Selection", "Noticing to acting", "Library",
                  "Cost", "Challenge", "Briefing"):
        assert words in section, words
