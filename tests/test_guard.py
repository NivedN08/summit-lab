"""Tests for the damage guard (Config.guard): a changed answer needs evidence.json that code can check.

Offline, with the fake BioDSA-style adapter and scripted backend of tests/test_recheck.py. Also the
flags-off check: the revise and referee briefs are word for word what the code before the guard
produced (tests/golden/revise_brief_flags_off.md and referee_brief_flags_off.md were rendered from
that code).
"""

import json
import os
import time

import pytest

from lab import measures, protocol, run
from test_protocol import A, B
from test_protocol import make as make_fake
from test_protocol import one_study
from test_recheck import ACCEPTED, GOLDEN, NV, LabelAdapter, assert_replay_matches, make, record, write_evidence

Q_TO_B = [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: check the repeated rows. Does that change your method?"}]
CHALLENGE_A = [{"to": "team-a", "question": "QUESTION FROM THE DIRECTOR: check the batch column. Does that change your method?"}]


# ----- the check itself -----

def check(tmp_path, kind, started=None):
    write_evidence(tmp_path, kind)
    return protocol.check_evidence(tmp_path, time.time() - 1 if started is None else started)


def test_no_evidence_blocks(tmp_path):
    assert check(tmp_path, "none") == (False, "no readable evidence.json")


def test_a_quoted_number_missing_from_the_output_blocks(tmp_path):
    ok, why = check(tmp_path, "wrong-number")
    assert not ok and "0.0042" in why and "does not appear" in why


def test_a_stale_output_blocks(tmp_path):
    ok, why = check(tmp_path, "stale")
    assert not ok and "before this round began" in why


def test_valid_evidence_passes(tmp_path):
    assert check(tmp_path, "good") == (True, "the evidence checks out")


def test_an_output_older_than_its_script_blocks(tmp_path):
    write_evidence(tmp_path, "good")
    now = time.time()
    os.utime(tmp_path / "check_output.txt", (now - 1, now - 1))
    os.utime(tmp_path / "check.py", (now, now))
    ok, why = protocol.check_evidence(tmp_path, now - 10)
    assert not ok and "older than the script" in why


@pytest.mark.parametrize("doc, reason", [
    ({"field": "label", "script": "check.py", "output_file": "check_output.txt"}, "lacks result"),
    ({"field": "label", "script": "../outside.py", "output_file": "check_output.txt", "result": "1"}, "not a file in"),
    ({"field": "label", "script": "check.py", "output_file": "missing.txt", "result": "1"}, "not a file in"),
])
def test_incomplete_or_outside_evidence_blocks(tmp_path, doc, reason):
    ws = tmp_path / "ws"
    ws.mkdir()
    write_evidence(ws, "good")
    (tmp_path / "outside.py").write_text("print(1)\n")
    (ws / "evidence.json").write_text(json.dumps(doc))
    ok, why = protocol.check_evidence(ws, time.time() - 1)
    assert not ok and reason in why


@pytest.mark.parametrize("quoted, output, ok", [
    ("p = 0.0031", "p-value 0.00312", True),        # rounded to the decimals shown
    ("p = 0.004", "p-value 0.0031", False),
    ("p = 1.2e-05", "p-value 0.0000123", False),    # scientific notation: by value, within 1e-9
    ("p = 1.23e-05", "p-value 0.0000123", True),
    ("n = 140, 71.5%", "n=140\nmean 71.50", True),   # signs, units and trailing zeros do not matter
    ("t = -3.1", "statistic 3.1", True),
    ("the counts are 12 and 30", "12 rows", False),
])
def test_numbers_are_compared_at_the_decimals_shown(tmp_path, quoted, output, ok):
    (tmp_path / "s.py").write_text("print()\n")
    (tmp_path / "o.txt").write_text(output)
    (tmp_path / "evidence.json").write_text(json.dumps({"field": "label", "script": "s.py", "output_file": "o.txt",
                                                        "result": quoted}))
    assert protocol.check_evidence(tmp_path, time.time() - 1)[0] is ok


def evidence_in(ws, output_file="o.txt", script="s.py", result="p = 0.0031", files=None, answer_file=None):
    """Write the files (name -> text), then evidence.json, and check it."""
    ws.mkdir(parents=True, exist_ok=True)
    for name, text in (files if files is not None else {"s.py": "print(31 / 10000)\n", "o.txt": "p 0.0031\n"}).items():
        (ws / name).write_text(text)
    (ws / "evidence.json").write_text(json.dumps({"field": "label", "script": script, "output_file": output_file,
                                                  "result": result}))
    return protocol.check_evidence(ws, time.time() - 1, answer_file)


def test_the_evidence_helper_passes_when_everything_is_right(tmp_path):
    assert evidence_in(tmp_path) == (True, "the evidence checks out")


@pytest.mark.parametrize("result", ["", None, "the claim is false", {"verdict": "false"}, True])
def test_a_result_without_a_number_blocks(tmp_path, result):
    assert evidence_in(tmp_path, result=result) == (False, "result quotes no number")


@pytest.mark.parametrize("output_file, answer_file", [
    ("evidence.json", None), ("test_result.json", None), ("result.json", None), ("answer.json", None),
    ("answer_before_recheck.json", None), ("My_Answer_v2.json", None), ("eval_answer.json", "eval_answer.json"),
    ("deliverable.json", "deliverable.json"), ("recheck_inventory.json", None), ("director_questions.json", None),
])
def test_an_output_file_the_protocol_has_the_agent_write_blocks(tmp_path, output_file, answer_file):
    files = {"s.py": "print(31 / 10000)\n"}
    if output_file != "evidence.json":
        files[output_file] = '{"p": 0.0031}'
    ok, why = evidence_in(tmp_path, output_file=output_file, files=files, answer_file=answer_file)
    assert not ok and "a file the lab's protocol has the agent write" in why


def test_the_output_file_must_not_be_the_script(tmp_path):
    ok, why = evidence_in(tmp_path, output_file="s.py", files={"s.py": "# p = 0.0031\n"})
    assert (ok, why) == (False, "the output_file is the script itself")
    ok, why = evidence_in(tmp_path / "dot", output_file="./s.py", files={"s.py": "# p = 0.0031\n"})
    assert (ok, why) == (False, "the output_file is the script itself")


def test_numbers_written_in_the_script_do_not_count(tmp_path):
    files = {"s.py": "print('p 0.0031')\n", "o.txt": "p 0.0031\n"}
    ok, why = evidence_in(tmp_path, files=files)
    assert not ok and "also written in the script" in why
    # one number the script computed is enough; a threshold the script also holds may be quoted with it
    files = {"s.py": "alpha = 0.05\nprint('p', 31 / 10000, 'alpha', alpha)\n", "o.txt": "p 0.0031 alpha 0.05\n"}
    assert evidence_in(tmp_path / "two", files=files, result="p = 0.0031 < 0.05")[0] is True


@pytest.mark.parametrize("which", ["output_file", "script"])
def test_a_symlinked_script_or_output_blocks(tmp_path, which):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "real.txt").write_text("p 0.0031\n" if which == "output_file" else "print(31 / 10000)\n")
    (ws / "link.txt").symlink_to(ws / "real.txt")      # even a link inside the folder
    kwargs = {"output_file": "link.txt"} if which == "output_file" else {"script": "link.txt"}
    ok, why = evidence_in(ws, **kwargs)
    assert not ok and "symbolic link" in why and which in why


def test_a_symlink_to_outside_and_absolute_paths_block(tmp_path):
    (tmp_path / "outside.txt").write_text("p 0.0031\n")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "link.txt").symlink_to(tmp_path / "outside.txt")
    assert "symbolic link" in evidence_in(ws, output_file="link.txt")[1]
    ok, why = evidence_in(tmp_path / "abs", output_file=str(tmp_path / "outside.txt"))
    assert not ok and "not a file in the working folder" in why


def test_a_symlinked_evidence_file_is_refused(tmp_path):
    other = tmp_path / "other"
    evidence_in(other)
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in ("s.py", "o.txt"):
        (ws / name).write_text((other / name).read_text())
    (ws / "evidence.json").symlink_to(other / "evidence.json")
    ok, why = protocol.check_evidence(ws, time.time() - 1)
    assert not ok and "evidence.json is a symbolic link" in why


@pytest.mark.parametrize("quoted, output, ok", [
    ("n = 1,234", "n 1234", True),                  # thousands separators are stripped
    ("n = 1234", "n 1,234", True),
    ("r = 1", "r 0.7", False),                      # an integer matches only an integer
    ("n = 140", "n 140.0", False),
    ("n = 140", "a,b\n12,140,15", True),            # a CSV row's numbers count one by one too
    ("p = 0.01", "p 0.006", False),                 # a rounded quote needs two significant digits
    ("p = 0.006", "p 0.0055", False),
    ("p = 0.0032", "p 0.00316", True),              # rounded half up
    ("p = 0.0031", "p 0.0031", True),
    ("p = 0.006", "p 0.0060", True),                # the same value with a trailing zero
    ("p = 0.0031", "p 0.003", False),               # the output shows fewer decimals than the quote
    ("p = 1e-5", "p 1.0e-05", True),                # scientific notation: by value
    ("p = 1e-5", "p 0.00001", True),
    ("p = 0.00001", "p 1e-05", True),
    ("p = 3.2e-12", "p 0.0", False),
    ("p = 1e-300", "p 1e-300", True),
    ("FEV1 differs, p = 0.0031", "FEV1 p 0.0031", True),   # digits inside a word are not numbers
])
def test_the_matching_rules(tmp_path, quoted, output, ok):
    files = {"s.py": "print()\n", "o.txt": output}
    assert evidence_in(tmp_path, files=files, result=quoted)[0] is ok


# ----- in the lab -----

def guarded_run(tmp_path, revision, truth="True", **config):
    """Team A says True twice; team B says False, is asked a question and revises to `revision`;
    the referee picks team A when they still differ."""
    plan = {"team-a": ["True", "True"], "team-b": ["False", revision], "questions": Q_TO_B,
            "referee": {"pick_team": "team-a"}}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan, adapter=LabelAdapter(tmp_path / "bench", truth=truth),
                                               recheck=False, guard=True, **config)
    out = lab.run(adapter.studies())
    run.grade_run(run_dir, adapter)
    return out, adapter, backend, log, run_dir


def test_a_change_without_evidence_is_blocked_and_the_first_answer_kept(tmp_path):
    out, adapter, backend, log, run_dir = guarded_run(tmp_path, {"label": "True"})
    assert out["s1"]["status"] == "refereed"
    blocked = log.events("guard.blocked")
    assert [(e["team"], e["round"]) for e in blocked] == [("team-b", "revise")]
    assert blocked[0]["reason"] == "no readable evidence.json"
    rec = record(run_dir)
    cand = next(c for c in rec["candidates"] if c["team"] == "team-b" and c["round"] == "revise")
    assert cand["valid"] and cand["not_used"].startswith("blocked by the damage guard")
    assert rec["guard"] == [{"team": "team-b", "round": "revise", "passed": False, "reason": "no readable evidence.json",
                             "candidate": "team-b-revise", "kept": "team-b-analysis"}]
    revise = [c for c in rec["comparisons"] if c["round"] == "revise"][0]
    assert revise["agree"] is False          # team B still answered False
    assert_replay_matches(run_dir)


def test_the_blocked_candidate_is_still_graded_and_counted(tmp_path):
    out, adapter, backend, log, run_dir = guarded_run(tmp_path, {"label": "True"})
    rows = {r["id"]: r for r in json.loads((run_dir / "results.json").read_text())["studies"]["s1"]["candidates"]}
    assert rows["team-b-revise"]["passed"] is True and rows["team-b-analysis"]["passed"] is False
    assert rows["team-b-revise"]["not_used"].startswith("blocked by the damage guard")   # as on self-arm rows
    assert "not_used" not in rows["team-b-analysis"]
    g = measures.measure(run_dir)["guard"]
    assert (g["checked"], g["passed"], g["blocked"]) == (1, 0, 1)
    assert (g["would_have_rescued"], g["would_have_damaged"], g["no_effect"]) == (1, 0, 0)
    steps = measures.measure(run_dir)["steps"]["questions"]
    assert steps["rescued"] == 0 and steps["damaged"] == 0     # the blocked revision was not the answer used


def test_a_blocked_change_that_would_have_damaged_is_counted(tmp_path):
    out, adapter, backend, log, run_dir = guarded_run(tmp_path, {"label": "True"}, truth="False")
    g = measures.measure(run_dir)["guard"]
    assert (g["blocked"], g["would_have_rescued"], g["would_have_damaged"]) == (1, 0, 1)
    assert g["changes"][0]["effect"] == "would have damaged"


def test_valid_evidence_lets_the_change_through(tmp_path):
    out, adapter, backend, log, run_dir = guarded_run(tmp_path, {"label": "True", "evidence": "good"})
    assert out["s1"]["status"] == "converged"
    assert [(e["team"], e["round"]) for e in log.events("guard.passed")] == [("team-b", "revise")]
    assert not log.events("guard.blocked")
    g = measures.measure(run_dir)["guard"]
    assert (g["checked"], g["passed"], g["blocked"]) == (1, 1, 0)
    assert_replay_matches(run_dir)


@pytest.mark.parametrize("kind, reason", [("wrong-number", "does not appear"), ("stale", "before this round began")])
def test_bad_evidence_in_the_lab_blocks(tmp_path, kind, reason):
    out, adapter, backend, log, run_dir = guarded_run(tmp_path, {"label": "True", "evidence": kind})
    assert out["s1"]["status"] == "refereed" and reason in log.events("guard.blocked")[0]["reason"]
    assert_replay_matches(run_dir)


def test_an_unchanged_answer_needs_no_evidence(tmp_path):
    out, adapter, backend, log, run_dir = guarded_run(tmp_path, "False")
    assert not log.events("guard.blocked") and not log.events("guard.passed")
    assert record(run_dir)["guard"] == []


def test_the_guard_keeps_an_agreed_answer_through_the_challenge(tmp_path):
    plan = {"team-a": ["True", "False"], "team-b": ["True", "True"], "challenge": CHALLENGE_A}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan, recheck=False, guard=True, challenge=True)
    out = lab.run(adapter.studies())
    assert out["s1"]["status"] == "replicated"
    assert [(e["team"], e["round"]) for e in log.events("guard.blocked")] == [("team-a", "challenge")]
    rec = record(run_dir)
    assert rec["final"]["round"] == "analysis" and rec["challenge_changed_answer"] is False
    assert_replay_matches(run_dir)


def test_the_referees_own_answer_needs_evidence(tmp_path):
    own = {"answer": {"label": NV}, "reasoning": "neither"}
    base = {"team-a": ["True", "True"], "team-b": ["False", "False"], "team-c": ["Not verifiable"], "questions": Q_TO_B}
    for name, evidence, final in (("blocked", "none", ("team-c", "third-team")), ("passed", "good", ("referee", "referee"))):
        plan = {**base, "referee": {"pick": "neither", "own_answer": own, "evidence": evidence}}
        lab, adapter, backend, log, run_dir = make(tmp_path / name, plan, recheck=False, guard=True)
        out = lab.run(adapter.studies())
        rec = record(run_dir)
        assert out["s1"]["status"] == "unresolved"
        assert (rec["final"]["team"], rec["final"]["round"]) == final
        assert [(e["team"], e["round"]) for e in log.events(f"guard.{name}")] == [("referee", "referee")]
        assert rec["referee"].get("own_answer_blocked", False) is (name == "blocked")
        assert_replay_matches(run_dir)
    referee_brief = next(c["brief"] for c in backend.calls if c["job"] == "referee")
    assert "evidence.json" in referee_brief and protocol.REFEREE_EVIDENCE_NOTE.strip() in referee_brief


def test_the_guard_also_covers_an_accepted_recheck(tmp_path):
    plan = {"team-a": [NV, ACCEPTED], "team-b": ["True"], "referee": {"pick_team": "team-b"}}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan, guard=True)
    out = lab.run(adapter.studies())
    verdict = log.events("recheck.verdict")[0]
    assert verdict["accepted"] is False and verdict["reason"].startswith("blocked by the damage guard")
    # still Not verifiable, team A then revises to the same unevidenced True, which is blocked again
    assert [(e["team"], e["round"]) for e in log.events("guard.blocked")] == [("team-a", "recheck"), ("team-a", "revise")]
    assert out["s1"]["status"] == "refereed" and record(run_dir)["final"]["team"] == "team-b"
    assert_replay_matches(run_dir)
    recheck_brief = next(c["brief"] for c in backend.calls if c["job"] == "recheck")
    assert protocol.EVIDENCE_NOTE.strip() in recheck_brief


def test_the_revise_brief_gets_the_evidence_note_only_with_the_guard(tmp_path):
    out, adapter, backend, log, run_dir = guarded_run(tmp_path / "on", {"label": "True", "evidence": "good"})
    on = [c["brief"] for c in backend.calls if c["job"] == "revise"]
    assert len(on) == 2 and all(protocol.EVIDENCE_NOTE.strip() in b for b in on)
    plan = {"team-a": ["True", "True"], "team-b": ["False", "True"], "questions": Q_TO_B}
    lab, adapter, backend, log, run_dir = make(tmp_path / "off", plan, recheck=False)
    lab.run(adapter.studies())
    off = [c["brief"] for c in backend.calls if c["job"] == "revise"]
    assert len(off) == 2 and not any("evidence.json" in b for b in off)
    assert not log.events("guard.blocked") and "guard" not in record(run_dir)


# ----- with the flags off, the briefs are word for word as before -----

def test_revise_and_referee_briefs_with_flags_off_equal_the_golden_files(tmp_path):
    plan = {"team-a": [A, A], "team-b": [B, B], "referee": {"pick_team": "team-a"},
            "questions": [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: the other team found repeated "
                                                      "sample ids. Does that change your method?"}]}
    lab, adapter, backend, *_ = make_fake(tmp_path, plan, protocol.Config(max_agents=3, seed=1))
    lab.run(one_study(adapter))
    root = str(tmp_path.resolve())

    def brief(job, agent):
        text = next(c["brief"] for c in backend.calls if c["job"] == job and c["agent"] == agent)
        return text.replace(str(protocol.TOOLS), "<TOOLS>").replace(root, "<TMP>").replace(str(tmp_path), "<TMP>")

    assert brief("revise", "s1-team-a") == (GOLDEN / "revise_brief_flags_off.md").read_text()
    assert brief("referee", "s1-referee") == (GOLDEN / "referee_brief_flags_off.md").read_text()
    assert protocol.Config().recheck is False and protocol.Config().guard is False
