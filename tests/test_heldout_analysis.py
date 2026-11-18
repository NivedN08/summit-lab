"""The held-out analysis (lab.heldout) on synthetic graded runs: the sign test, the exact interval, missing
tasks counted as failed, pooling, and the planted groups. No model runs."""

import json
import math

import pytest

from lab import heldout


def results(path, rows):
    path.mkdir(parents=True, exist_ok=True)
    (path / "results.json").write_text(json.dumps({"studies": rows}))


def write_batch(root, outcomes, planted_meta=None):
    """outcomes: {benchmark: {arm: {task: passed}}}; a task left out of an arm is missing from its run."""
    planted_meta = planted_meta or {}
    for bench, arms in outcomes.items():
        for arm, by_task in arms.items():
            key = heldout.ARMS[arm][0]
            rows = {}
            for t, ok in by_task.items():
                grade = {"passed": ok, **planted_meta.get(t, {})}
                rows[t] = {key: {"grade": grade} if arm == "lab" else {"final": grade} if arm == "self"
                           else {"vote": grade}}
            results(root / bench / arm, rows)


def test_sign_test_and_known_values():
    assert heldout.sign_test(5, 0) == pytest.approx(1 / 32)
    assert heldout.sign_test(9, 1) == pytest.approx(11 / 1024)
    assert heldout.sign_test(0, 0) == 1.0
    assert heldout.sign_test(1, 1) == pytest.approx(0.75)


def test_clopper_pearson_matches_textbook_values():
    lo, hi = heldout.clopper_pearson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-6)     # 0.3085
    lo, hi = heldout.clopper_pearson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.025 ** (1 / 10), abs=1e-6)
    lo, hi = heldout.clopper_pearson(5, 10)
    assert lo == pytest.approx(0.1871, abs=1e-3) and hi == pytest.approx(0.8129, abs=1e-3)


def test_paired_refuses_different_tasks():
    with pytest.raises(ValueError):
        heldout.paired({"a": True}, {"b": True})


def test_a_task_missing_from_a_run_counts_as_failed(tmp_path):
    results(tmp_path / "vote", {"bh01": {"vote_arm": {"vote": {"passed": True}}}})
    assert heldout.passes(tmp_path / "vote", "vote", ["bh01", "bh02"]) == {"bh01": True, "bh02": False}


def test_the_primary_endpoint_pools_both_benchmarks(tmp_path):
    bd = [f"bh{i:02d}" for i in range(1, 7)]
    pf = ["pf01", "pf02"]
    lab_b = dict.fromkeys(bd, True)
    vote_b = {**dict.fromkeys(bd, True), "bh01": False, "bh02": False, "bh03": False}     # 3 lab wins
    rest_b = dict.fromkeys(bd, True)
    lab_p, vote_p = {"pf01": True, "pf02": True}, {"pf01": False, "pf02": True}            # 1 more win
    meta = {"pf01": {"faulted": True, "family": "unit_mismatch"}, "pf02": {"faulted": False, "family": "unit_mismatch"}}
    write_batch(tmp_path, {
        "biodsa": {"lab": lab_b, "vote": vote_b, "opus": rest_b, "self": rest_b, "fable": rest_b, "opus-plain": rest_b},
        "planted": {"lab": lab_p, "vote": vote_p, "opus": lab_p, "self": lab_p, "fable": vote_p}}, meta)
    r = heldout.analyse(tmp_path, {"biodsa": bd, "planted": pf})
    p = r["primary"]
    assert p["wins"] == ["biodsa/bh01", "biodsa/bh02", "biodsa/bh03", "planted/pf01"] and p["losses"] == []
    assert p["p_one_sided"] == pytest.approx(1 / 16) and p["significant"] is False      # 4 of 4 is not enough
    assert p["win_share"] == 1.0 and p["win_share_95"][0] == pytest.approx(0.025 ** 0.25, abs=1e-3)
    assert r["passed"] == {"biodsa": {"lab": 6, "vote": 3, "opus": 6, "self": 6, "fable": 6, "opus-plain": 6},
                           "planted": {"lab": 2, "vote": 1, "opus": 2, "self": 2, "fable": 1}}
    groups = r["secondary"]["planted groups"]
    assert groups["faulted"] == {"lab": 1, "vote": 0, "opus": 1, "self": 1, "fable": 0, "tasks": 1}
    assert groups["family:unit_mismatch"]["tasks"] == 2
    assert r["secondary"]["convention note: opus vs opus-plain, biodsa"]["wins"] == []


def test_five_clean_wins_are_significant():
    a = {f"t{i}": True for i in range(5)}
    b = {f"t{i}": False for i in range(5)}
    out = heldout.paired(a, b)
    assert out["p_one_sided"] == pytest.approx(1 / 32) and out["p_one_sided"] <= heldout.ALPHA
    assert math.isclose(out["win_share"], 1.0)
