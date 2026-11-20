"""Tests for lab.ledger: the append-only, hash-chained research ledger, and `grade --ledger`."""

import json
import threading

import pytest

from lab import baseline as baseline_mod
from lab import ledger, run
from test_protocol import A, B, make


def fill(path):
    ledger.append("note", {"text": "first"}, path)
    ledger.append("move.proposed", {"move": "teams on different models", "why": "shared blind spots"}, path)
    ledger.append("move.accepted", {"move": "teams on different models"}, path)
    ledger.append("batch.registered", {"batch": "b1", "sha256": "ab" * 32}, path)
    return path


def lines(path):
    return path.read_text().splitlines()


def test_append_and_verify(tmp_path):
    path = fill(tmp_path / "ledger" / "ledger.jsonl")
    got = ledger.entries(path)
    assert [e["seq"] for e in got] == [1, 2, 3, 4]
    assert got[0]["prev_sha"] == "0" * 64
    assert all(b["prev_sha"] == a["sha"] for a, b in zip(got, got[1:]))
    assert all(set(e) == {"seq", "time", "kind", "data", "prev_sha", "sha"} for e in got)
    assert ledger.verify_chain(path) == []
    assert [e["kind"] for e in ledger.entries(path, kind="move.accepted")] == ["move.accepted"]
    with pytest.raises(ValueError):
        ledger.append("not-a-kind", {}, path)
    assert ledger.entries(tmp_path / "none.jsonl") == [] and ledger.verify_chain(tmp_path / "none.jsonl") == []


def test_the_default_path_can_be_moved_for_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_LEDGER", str(tmp_path / "elsewhere.jsonl"))
    ledger.append("note", {"text": "x"})
    assert ledger.default_path() == tmp_path / "elsewhere.jsonl" and len(ledger.entries()) == 1


@pytest.mark.parametrize("line", [0, 1, 2, 3])
def test_editing_the_data_of_any_line_is_detected(tmp_path, line):
    path = fill(tmp_path / "ledger.jsonl")
    rows = lines(path)
    entry = json.loads(rows[line])
    entry["data"]["tampered"] = True
    rows[line] = json.dumps(entry)
    path.write_text("\n".join(rows) + "\n")
    problems = ledger.verify_chain(path)
    assert problems and problems[0].startswith(f"line {line + 1}: sha does not match")


def test_rewriting_a_line_and_its_sha_breaks_the_next_line(tmp_path):
    path = fill(tmp_path / "ledger.jsonl")
    rows = lines(path)
    entry = json.loads(rows[1])
    entry["data"]["move"] = "something else"
    entry["sha"] = ledger.entry_sha(entry["prev_sha"], entry["seq"], entry["time"], entry["kind"], entry["data"])
    rows[1] = json.dumps(entry)
    path.write_text("\n".join(rows) + "\n")
    assert ledger.verify_chain(path) == ["line 3: prev_sha does not match the sha of the line before"]


def test_removing_or_reordering_lines_is_detected(tmp_path):
    path = fill(tmp_path / "ledger.jsonl")
    rows = lines(path)
    path.write_text("\n".join(rows[:1] + rows[2:]) + "\n")
    assert any("seq 3, expected 2" in p for p in ledger.verify_chain(path))
    path.write_text("\n".join([rows[1], rows[0]] + rows[2:]) + "\n")
    assert ledger.verify_chain(path)
    path.write_text("\n".join(rows) + "\nnot json\n")
    assert ledger.verify_chain(path) == ["line 5: not JSON"]


def test_appends_from_many_threads_keep_one_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    threads = [threading.Thread(target=lambda i=i: [ledger.append("note", {"i": i, "k": k}, path) for k in range(5)])
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [e["seq"] for e in ledger.entries(path)] == list(range(1, 31))
    assert ledger.verify_chain(path) == []


def test_grade_with_ledger_appends_the_batch_and_its_failures(tmp_path, monkeypatch):
    # s1: both teams give the right answer A (passes); s2: both agree on the wrong answer B (fails).
    plan = {"team-a": [A], "team-b": [A], "baseline": [A, B]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    lab.run([s for s in adapter.studies() if s.id == "s1"])
    backend.plan = {"team-a": [B], "team-b": [B], "baseline": [A, B]}
    lab.run([s for s in adapter.studies() if s.id == "s2"])
    baseline_mod.run_baselines(adapter, backend, log, run_dir, adapter.studies(), attempts=2, model="opus",
                               max_agents=2)
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda: adapter)
    book = tmp_path / "ledger.jsonl"
    assert run.main(["grade", "--run-dir", str(run_dir), "--benchmark", "fake"]) == 0
    assert not book.exists()                                       # opt in only
    assert run.main(["grade", "--run-dir", str(run_dir), "--benchmark", "fake", "--ledger", str(book)]) == 0
    got = ledger.entries(book)
    assert [e["kind"] for e in got] == ["batch.graded", "failure"]
    assert got[0]["data"]["totals"]["lab_passed"] == 1 and got[0]["data"]["run_dir"] == str(run_dir)
    assert got[1]["data"] == {"run_dir": str(run_dir), "study": "s2", "arm": "lab", "outcome_kind": None,
                              "status": "replicated"}
    assert ledger.verify_chain(book) == []


def test_record_grading_uses_the_vote_without_a_lab_answer_and_keeps_outcome_kinds(tmp_path):
    results = {"benchmark": "x", "totals": {"vote_passed": 0},
               "studies": {"t1": {"baseline": {"vote": {"passed": False, "kind": "wrong label"}},
                                  "candidates": [{"id": "vote", "kind": "vote", "outcome_kind": "wrong label"}]},
                           "t2": {"lab": {"status": "refereed", "grade": {"passed": False, "kind": "too low"}},
                                  "candidates": [{"id": "final", "kind": "final", "outcome_kind": "too low"}]},
                           "t3": {"lab": {"status": "replicated", "grade": {"passed": True}}, "candidates": []}}}
    written = ledger.record_grading(tmp_path / "r", results, tmp_path / "l.jsonl")
    failures = [e["data"] for e in written if e["kind"] == "failure"]
    assert [(f["study"], f["arm"], f["outcome_kind"]) for f in failures] == [("t1", "vote", "wrong label"),
                                                                           ("t2", "lab", "too low")]


def test_record_grading_records_failures_of_the_vote_and_self_challenge_arms(tmp_path):
    """The held-out planted vote and self-challenge arms failed 3 tasks each and the ledger had none of them."""
    results = {"benchmark": "x", "totals": {},
               "studies": {"t1": {"vote_arm": {"attempts": {}, "vote": {"passed": False, "kind": "wrong label"}},
                                  "candidates": [{"id": "vote-arm", "kind": "vote", "outcome_kind": "wrong label"}]},
                           "t2": {"vote_arm": {"attempts": {}, "vote": {"passed": True}}, "candidates": []},
                           "t3": {"self": {"status": "kept", "final": {"passed": False, "kind": "too low"}},
                                  "candidates": [{"id": "self-final", "kind": "self", "outcome_kind": "too low"}]},
                           "t4": {"self": {"status": "stopped", "final": {}}, "candidates": []},
                           "t5": {"self": {"status": "kept", "final": {"passed": True}}, "candidates": []}}}
    written = ledger.record_grading(tmp_path / "r", results, tmp_path / "l.jsonl")
    failures = [e["data"] for e in written if e["kind"] == "failure"]
    assert [(f["study"], f["arm"], f["outcome_kind"]) for f in failures] == [
        ("t1", "vote-arm", "wrong label"), ("t3", "self", "too low"), ("t4", "self", "no answer")]
    assert [f.get("status") for f in failures] == [None, "kept", "stopped"]
