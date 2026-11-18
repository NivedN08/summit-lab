"""Loop-2 additions to the improvement loop: bundled moves, seed moves, a settable rejection limit, and
the failure evidence the proposer may see. Offline; reuses the loop-1 test helpers."""

import json
import pathlib

import pytest

from lab import autoloop, run
from lab.autoloop import Move
from lab.measures import NOT_VERIFIABLE_KIND
from test_autoloop import INC, PROMPTS, SAME, WORKING_LIMITS, base_variant, kinds, scripted

NEW_LIMITS = WORKING_LIMITS.rstrip("\n") + "\nKeep every script under two minutes.\n"


def bundle(*parts, mid="m01") -> Move:
    return Move(mid, "bundle", "bundle", [{"kind": k, "target": t, "value": v} for k, t, v in parts], "test")


# ----- bundles -----

def test_a_bundle_whose_second_part_needs_the_first_is_valid(tmp_path):
    inc = autoloop.make_base_variant(tmp_path / "variants", autoloop.Config(), name="plain")
    current = autoloop.settings(inc)
    alone = Move("m01", "threshold", "recheck_p", 0.1)
    assert autoloop.validate(alone, current, inc)            # the re-check is off: a threshold alone changes nothing
    move = bundle(("toggle", "recheck", True), ("threshold", "recheck_p", 0.1))
    assert autoloop.validate(move, current, inc) == []
    path = autoloop.make_variant(move, inc)
    config = autoloop.settings(path)
    assert config["recheck"] is True and config["recheck_p"] == 0.1


def test_a_bundle_applies_an_edit_and_a_toggle_and_reads_as_first_round_when_any_part_does(tmp_path):
    inc = autoloop.make_base_variant(tmp_path / "variants", autoloop.Config(), name="plain")
    move = bundle(("edit_section", "analyst_revise.md#Working limits", NEW_LIMITS), ("toggle", "guard", True))
    path = autoloop.make_variant(move, inc)
    assert autoloop.settings(path)["guard"] is True
    assert autoloop.section_body((path / "prompts" / "analyst_revise.md").read_text(), "Working limits").strip() \
        == NEW_LIMITS.strip()
    assert move.affects_first_round is False
    first = bundle(("edit_section", "analyst.md#" + autoloop.sections((PROMPTS / "analyst.md").read_text())[0][0],
                    "x"), ("toggle", "guard", True))
    assert autoloop.first_round(first) is True
    assert json.loads((path / "move.json").read_text())["kind"] == "bundle"
    assert not (PROMPTS / "analyst_revise.md").read_text().endswith("two minutes.\n")   # lab/prompts untouched


@pytest.mark.parametrize("move, why", [
    (bundle(("toggle", "guard", True)), "2 to 2 parts"),
    (bundle(("toggle", "guard", True), ("toggle", "challenge", False), ("toggle", "referee", False)), "2 to 2 parts"),
    (bundle(("toggle", "guard", True), ("toggle", "guard", True)), "different targets"),
    (Move("m01", "bundle", "bundle", [{"kind": "bundle", "target": "bundle", "value": []},
                                      {"kind": "toggle", "target": "guard", "value": True}]), "kinds"),
    (Move("m01", "bundle", "bundle", "not a list"), "list of moves"),
    (Move("m01", "bundle", "guard", [{"kind": "toggle", "target": "guard", "value": True}] * 2), 'target is "bundle"'),
    (bundle(("toggle", "guard", True), ("threshold", "recheck_p", 0.1)), "part 2"),
])
def test_bad_bundles_are_refused(tmp_path, move, why):
    inc = autoloop.make_base_variant(tmp_path / "variants", autoloop.Config(), name="plain")
    problems = autoloop.validate(move, autoloop.settings(inc), inc)
    assert problems and why in " ".join(problems)


def test_a_bundle_is_described_in_the_ledger_summary_without_its_long_text(tmp_path):
    move = bundle(("edit_section", "analyst_revise.md#Working limits", NEW_LIMITS), ("toggle", "guard", True))
    entries = [{"kind": "move.proposed", "data": {"loop": "L", "move_id": "m01", "path": "seed", "move": move.record()}}]
    line = autoloop.ledger_summary(entries)[0]
    assert "bundle: edit_section analyst_revise.md#Working limits = <" in line and "toggle guard = true" in line
    assert "two minutes" not in line


# ----- seeds and the rejection limit -----

def test_seed_moves_come_first_and_an_unmakeable_seed_is_skipped_once(tmp_path):
    # the base variant has the guard on already: the first seed cannot be made
    seeds = [{"kind": "toggle", "target": "guard", "value": True, "rationale": "seed 1"},
             {"kind": "threshold", "target": "recheck_p", "value": 0.1, "rationale": "seed 2"}]
    loop, calls = scripted(tmp_path, [SAME] * 3, moves=[("threshold", "recheck_min_n", 40)], max_moves=2,
                           seed_moves=seeds)
    loop.run()
    proposed = [e["data"] for e in loop.entries() if e["kind"] == "move.proposed"]
    assert [(p["path"], p["move"]["target"]) for p in proposed] == [("seed", "recheck_p"), ("proposer", "recheck_min_n")]
    skipped = [e["data"] for e in loop.entries() if e["kind"] == "note" and e["data"].get("event") == "seed.skipped"]
    assert len(skipped) == 1 and skipped[0]["seed"] == 1 and "already" in skipped[0]["problems"][0]
    assert calls["propose"] == 1                 # the proposer ran only once the seeds were used up


def test_a_malformed_seed_stops_the_loop_before_anything_runs(tmp_path):
    loop, calls = scripted(tmp_path, [SAME], seed_moves=[{"kind": "toggle"}])
    with pytest.raises(ValueError, match="seed move 1"):
        loop.run()
    assert calls["evaluate"] == []


def test_the_rejection_limit_can_be_set(tmp_path):
    loop, calls = scripted(tmp_path, [SAME] * 5, moves=[("threshold", "recheck_p", v) for v in (0.1, 0.02, 0.01, 0.15, 0.2)],
                           max_rejections=4)
    assert loop.run()["reason"] == "4 rejections in a row" and len(calls["evaluate"]) == 4


def test_read_seed_moves_takes_an_edit_from_a_variant(tmp_path):
    var = tmp_path / "variants" / "m03"
    var.mkdir(parents=True)
    (var / "move.json").write_text(json.dumps({"kind": "edit_section", "target": "analyst.md#X", "value": "new text"}))
    spec = tmp_path / "seeds.json"
    spec.write_text(json.dumps({"moves": [
        {"kind": "toggle", "target": "guard", "value": True, "rationale": "r"},
        {"kind": "bundle", "target": "bundle", "rationale": "r", "value": [
            {"kind": "edit_section", "target": "analyst.md#X", "value": {"from_variant": "variants/m03"}},
            {"kind": "toggle", "target": "guard", "value": True}]}]}))
    moves = run.read_seed_moves(str(spec))
    assert moves[1]["value"][0]["value"] == "new text" and moves[0]["value"] is True
    spec.write_text(json.dumps([{"kind": "edit_section", "target": "analyst.md#Y", "value": {"from_variant": "variants/m03"}}]))
    with pytest.raises(SystemExit, match="not edit_section analyst.md#Y"):
        run.read_seed_moves(str(spec))
    assert run.read_seed_moves(None) == []


def test_the_loop_command_takes_the_new_flags():
    args = run.build_parser().parse_args(["loop", "--benchmark", "biodsa-convention", "--studies", "bd01",
                                          "--incumbent-run", "x", "--run-root", "y", "--max-rejections", "4",
                                          "--seed-moves", "s.json", "--proposer-evidence"])
    assert args.max_rejections == 4 and args.seed_moves == "s.json" and args.proposer_evidence
    plain = run.build_parser().parse_args(["loop", "--benchmark", "biodsa", "--studies", "bd01",
                                           "--incumbent-run", "x", "--run-root", "y"])
    assert plain.max_rejections == autoloop.MAX_REJECTIONS_IN_A_ROW and not plain.proposer_evidence
    assert plain.seed_moves is None


# ----- the proposer's failure evidence -----

def evidence_run(path: pathlib.Path) -> pathlib.Path:
    """A graded run of three tasks: s1 passed; s2 failed by abstaining after team A said True; s3 failed
    by a wrong True."""
    def answer(folder, name, label, why):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps({"answer": {"label": label}, "reasoning": why}))
    studies = {}
    for sid, passed, kind, steps, final in (
            ("s1", True, "correct", [("team-a", "analysis", "True")], "True"),
            ("s2", False, NOT_VERIFIABLE_KIND, [("team-b", "challenge", "Not verifiable"), ("team-a", "analysis", "True"),
                                                ("team-b", "analysis", "Not verifiable")], "Not verifiable"),
            ("s3", False, "accepted a false claim", [("team-a", "analysis", "True")], "True")):
        cands = []
        for team, rnd, label in steps:
            cid = f"{team}-{rnd}"
            answer(path / sid / "candidates", f"{cid}.json", label, f"{cid} reasons " + "x" * 2000)
            cands.append({"id": cid, "kind": "lab", "team": team, "round": rnd, "passed": passed})
        answer(path / sid / "final", "answer.json", final, "final reasons")
        cands.append({"id": "final", "kind": "final", "passed": passed, "outcome_kind": kind})
        studies[sid] = {"lab": {"grade": {"passed": passed}}, "candidates": cands}
    (path / "results.json").write_text(json.dumps({"studies": studies}))
    return path


def test_failure_evidence_counts_labels_and_numbers_failed_tasks_without_names(tmp_path):
    ev = autoloop.failure_evidence(evidence_run(tmp_path / "run"), ["s1", "s2", "s3"])
    assert ev["labels"] == {"True": {"right": 1, "wrong": 1}, "Not verifiable": {"right": 0, "wrong": 1}}
    assert [f["task"] for f in ev["failed"]] == ["failed task 1", "failed task 2"]
    s2 = ev["failed"][0]
    assert s2["outcome_kind"] == NOT_VERIFIABLE_KIND and s2["final_label"] == "Not verifiable"
    assert [(st["team"], st["round"]) for st in s2["steps"]] == [("team-a", "analysis"), ("team-b", "analysis"),
                                                                 ("team-b", "challenge")]
    assert all(len(st["reasoning"]) <= autoloop.MAX_REASON_CHARS for st in s2["steps"])
    text = autoloop.evidence_text(ev)
    assert "s1" not in text.replace("reasons", "") and "s2" not in text and "s3" not in text
    assert "- Not verifiable: right 0, wrong 1" in text and "### failed task 1" in text


def test_the_proposer_brief_shows_evidence_only_when_given(tmp_path):
    inc = base_variant(tmp_path)
    common = dict(entries=[], failures={NOT_VERIFIABLE_KIND: 1}, current=autoloop.settings(inc), incumbent=inc,
                  moves_so_far=0, T=3, cost_ratio_max=1.25, max_damage=1, panel_size=3)
    plain = autoloop.proposer_brief(**common)
    assert "Not shown in this loop" in plain and "failed task 1" not in plain
    ev = autoloop.failure_evidence(evidence_run(tmp_path / "run"), ["s1", "s2", "s3"])
    shown = autoloop.proposer_brief(**common, evidence=ev)
    assert "### failed task 1" in shown and "team-b, challenge: Not verifiable" in shown
    assert "bundle" in shown                    # the schema lists bundles
