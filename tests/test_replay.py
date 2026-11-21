"""Tests for lab.replay: every decision path StudyRun.run can take, rebuilt from the event log alone,
must equal what study.json records. The scenarios are the ones tests/test_protocol.py runs."""

import json

import pytest

from lab import protocol, replay, run
from test_protocol import A, B, CHALLENGE, make, one_study
from test_research import CLEAN, FINDING, exa_log  # noqa: F401  (exa_log: keep Exa records out of the repo)
from test_research import make as make_v3

Q_TO_B = [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: the other team found repeated sample ids. "
                                       "Does that change your method?"}]

SCENARIOS = {
    # name: (plan, Config overrides, expected status, expected final (team, round) or None)
    "replicated": ({"team-a": [A], "team-b": [A]}, {}, "replicated", ("team-a", "analysis")),
    "replicated-challenged-kept": ({"team-a": [A, A], "team-b": [A, A], "challenge": CHALLENGE}, {},
                                   "replicated", ("team-a", "challenge")),
    "replicated-challenged-changed": ({"team-a": [B, A], "team-b": [B, A], "challenge": CHALLENGE}, {},
                                      "replicated", ("team-a", "challenge")),
    "replicated-no-challenge": ({"team-a": [A], "team-b": [A], "challenge": CHALLENGE}, {"challenge": False},
                                "replicated", ("team-a", "analysis")),
    "replicated-after-a-retry": ({"team-a": [A], "team-b": [A], "fail": {"team-a": 1}}, {},
                                 "replicated", ("team-a", "analysis")),
    "challenged-then-refereed": ({"team-a": [A, A], "team-b": [A, B], "challenge": CHALLENGE,
                                  "referee": {"pick_team": "team-b"}}, {}, "refereed", ("team-b", "challenge")),
    "converged": ({"team-a": [A, A], "team-b": [B, A], "questions": Q_TO_B}, {}, "converged", ("team-a", "revise")),
    "refereed": ({"team-a": [A, A], "team-b": [B, B], "referee": {"pick_team": "team-b"}, "questions": Q_TO_B}, {},
                 "refereed", ("team-b", "revise")),
    "third-team": ({"team-a": [A, A], "team-b": [B, B], "team-c": [A], "referee": {"pick": "neither"},
                    "questions": Q_TO_B}, {}, "third-team", ("team-c", "third-team")),
    "unresolved-referee-own": ({"team-a": [A, A], "team-b": [B, B], "team-c": [(7.0, "other")],
                                "referee": {"pick": "neither", "own_answer": {"answer": {"effect": 5.5, "label": "m"}}}},
                               {}, "unresolved", ("referee", "referee")),
    "unresolved-team-c": ({"team-a": [A, A], "team-b": [B, B], "team-c": [(7.0, "other")],
                           "referee": {"pick": "neither"}}, {}, "unresolved", ("team-c", "third-team")),
    "unresolved-no-answer": ({"team-a": [A, A], "team-b": [B, B], "team-c": [None], "referee": {"pick": "neither"}},
                             {}, "unresolved", None),
    "single-source": ({"team-a": [None], "team-b": [B]}, {}, "single-source", ("team-b", "analysis")),
    "no-answer": ({"team-a": [None], "team-b": [None]}, {}, "no-answer", None),
    "switches-off-third-team": ({"team-a": [A], "team-b": [B], "team-c": [B]},
                                {"library": False, "questions": False, "referee": False},
                                "third-team", ("team-c", "third-team")),
    "no-questions-refereed": ({"team-a": [A], "team-b": [B], "referee": {"pick_team": "team-a"}},
                              {"questions": False}, "refereed", ("team-a", "analysis")),
}


def assert_rebuilt(run_dir, sid, status, final):
    events = replay.read_events(run_dir)
    rebuilt = replay.decide(events, sid)
    record = json.loads((run_dir / sid / "study.json").read_text())
    assert rebuilt == replay.recorded(run_dir, sid)
    assert rebuilt["status"] == record["status"] == status
    assert (rebuilt["final"]["team"], rebuilt["final"]["round"]) == final if final else rebuilt["final"] is None
    assert any(line.split(None, 1)[1].startswith("final: ") for line in replay.timeline(run_dir, sid))


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_decide_rebuilds_what_study_json_records(tmp_path, name):
    plan, overrides, status, final = SCENARIOS[name]
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan, protocol.Config(max_agents=3, **overrides))
    assert lab.run(one_study(adapter))["s1"]["status"] == status
    assert_rebuilt(run_dir, "s1", status, final)


def test_research_findings_in_the_challenge_round_lead_to_a_revision(tmp_path):
    plan = {"team-a": [A, A], "team-b": [A, A], "research_questions": [CLEAN], "findings": [FINDING]}
    t = make_v3(tmp_path, plan)
    assert t.lab.run([s for s in t.adapter.studies() if s.id == "s1"])["s1"]["status"] == "replicated"
    assert_rebuilt(t.run_dir, "s1", "replicated", ("team-a", "challenge"))


def test_decide_ignores_study_final_and_study_json(tmp_path):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, SCENARIOS["converged"][0])
    lab.run(one_study(adapter))
    events = [e for e in replay.read_events(run_dir) if e["kind"] != "study.final"]
    (run_dir / "s1" / "study.json").unlink()
    assert replay.decide(events, "s1") == {"status": "converged",
                                           "final": {"agent": "s1-team-a", "team": "team-a", "round": "revise"}}


def test_a_halted_study_is_incomplete_and_a_resumed_one_matches(tmp_path):
    plan = {"team-a": [A], "team-b": [A]}
    lab, adapter, *_ , run_dir = make(tmp_path, plan, protocol.Config(max_agents=3, max_usd=1.0))
    assert lab.run(one_study(adapter))["s1"]["status"] == "error"
    halted = replay.decide(replay.read_events(run_dir), "s1")
    assert halted["status"] is None and halted["incomplete"]
    assert replay.replay(run_dir, out=lambda line: None) == 0          # unfinished: reported, not a mismatch
    lab2, *_ = make(tmp_path, plan, protocol.Config(max_agents=3, max_usd=50.0))
    lab2.run(one_study(adapter))
    assert_rebuilt(run_dir, "s1", "replicated", ("team-a", "analysis"))   # only the last attempt's events count


def test_cli_replay_reports_match_and_exits_1_on_a_mismatch(tmp_path, capsys):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, SCENARIOS["refereed"][0])
    lab.run(adapter.studies())
    assert run.main(["replay", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert out.count("MATCH") == 2 and "MISMATCH" not in out and "== s1" in out and "== s2" in out
    assert "referee picked" in out and "compare revise" in out
    assert run.main(["replay", str(run_dir), "--study", "s2"]) == 0
    assert "== s1" not in capsys.readouterr().out
    path = run_dir / "s1" / "study.json"
    record = json.loads(path.read_text())
    record["status"] = "converged"
    path.write_text(json.dumps(record))
    assert run.main(["replay", str(run_dir)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
    assert run.main(["replay", str(tmp_path / "nothing")]) == 2
