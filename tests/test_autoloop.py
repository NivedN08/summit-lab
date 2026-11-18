"""Tests for the improvement loop (lab/autoloop.py), reuse mode (Config.reuse_first_round), the prompt
overlay (Config.prompt_dir) and the check registry (lab/check_registry.py).

Offline: a fake adapter with BioDSA-style labels and three studies whose truths differ, the mock
backend (lab.run.mock_script, which also plays the proposer), LocalMemory and the real Gateway.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from lab import autoloop, check_registry, ledger, protocol, replay, run
from lab.autoloop import Move
from lab.backend_mock import MockBackend
from lab.contracts import Study
from lab.eventlog import JsonlEventLog
from lab.measures import NOT_VERIFIABLE_KIND
from lab.memory_local import LocalMemory

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "lab" / "prompts"
LABELS = ("True", "False", "Not verifiable")
STATEMENT = """# Hypothesis

Smokers have a lower FEV1 percent than never smokers.

# Your task

Decide whether the data support the hypothesis ("True"), contradict it ("False") or cannot settle it
("Not verifiable"). Write `answer.json`: {"answer": {"label": "<label>"}, "reasoning": "..."}
"""
TRUTHS = {"s1": "True", "s2": "False", "s3": "True"}


class Labels:
    """Three studies with BioDSA's answer format. `name` is "planted", so the mock answers with labels
    and the re-check's label rule applies. s2's truth is "False", so the mock lab fails it."""

    name = "planted"

    def __init__(self, root: pathlib.Path, truths: dict | None = None):
        self.root, self.truths = root, dict(truths or TRUTHS)
        for sid in self.truths:
            data = root / sid / "data"
            data.mkdir(parents=True, exist_ok=True)
            (data / "clinical.tsv").write_text("PATIENT_ID\tSMOKING\tFEV1_PERCENT\nP1\t1\t71.5\nP2\t0\t92.0\n")

    def studies(self):
        return [Study(id=sid, title=sid, statement=STATEMENT, data_dir=self.root / sid / "data",
                      deliverables=["answer.json"]) for sid in self.truths]

    def schema(self, study):
        return {"label": "string"}

    @staticmethod
    def _label(path):
        try:
            doc = json.loads(pathlib.Path(path).read_text())
        except (OSError, ValueError):
            return None
        return doc.get("answer", {}).get("label") if isinstance(doc, dict) and isinstance(doc.get("answer"), dict) else None

    def problems(self, study, deliverable, candidate):
        return [] if self._label(candidate) in LABELS else ['"label" must be one of the three labels']

    def field_agreement(self, study, a, b):
        la, lb = self._label(a), self._label(b)
        return {"label": la in LABELS and la == lb}

    def agreement(self, study, deliverable, a, b):
        return float(self.field_agreement(study, a, b)["label"])

    def grade(self, study, final_dir):
        label, truth = self._label(pathlib.Path(final_dir) / "answer.json"), self.truths[study.id]
        if label not in LABELS:
            return {"passed": False, "score": 0.0, "error": "no valid answer"}
        if label == truth:
            kind = "correct"
        elif label == "Not verifiable":
            kind = NOT_VERIFIABLE_KIND
        else:
            kind = "accepted a false claim" if label == "True" else "rejected a true claim"
        return {"passed": label == truth, "score": float(label == truth), "kind": kind}


def prompts_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(PROMPTS.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(PROMPTS).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def v4(**extra) -> protocol.Config:
    return protocol.Config(recheck=True, guard=True, director_model="opus", worker_model="opus", **extra)


def base_variant(tmp_path, **extra) -> pathlib.Path:
    return autoloop.make_base_variant(tmp_path / "variants", v4(**extra))


def mv(kind, target, value, rationale="test") -> Move:
    return Move("m01", kind, target, value, rationale)


# ----- 1. move validation -----

WORKING_LIMITS = autoloop.section_body((PROMPTS / "analyst_revise.md").read_text(), "Working limits")


@pytest.mark.parametrize("move", [
    mv("toggle", "challenge", False),
    mv("toggle", "brief_teams", True),
    mv("threshold", "recheck_p", 0.1),
    mv("threshold", "recheck_min_n", 40),
    mv("add_check", "guard_strict_decimals", True),
    mv("edit_section", "analyst_revise.md#Working limits", WORKING_LIMITS + "- One more limit.\n"),
])
def test_valid_moves_of_each_kind(tmp_path, move):
    inc = base_variant(tmp_path)
    assert autoloop.validate(move, autoloop.settings(inc), inc) == []


@pytest.mark.parametrize("move, words", [
    (mv("rename", "x", 1), "kind"),
    (mv("toggle", "max_agents", True), "not one of"),
    (mv("toggle", "recheck", True), "already true"),
    (mv("toggle", "guard", "yes"), "true or false"),
    (mv("threshold", "recheck_p", 0.5), "within"),
    (mv("threshold", "recheck_p", 0.0005), "within"),
    (mv("threshold", "recheck_min_n", 12.5), "whole number"),
    (mv("threshold", "recheck_min_n", True), "needs a number"),
    (mv("threshold", "recheck_p", 0.05), "already"),
    (mv("threshold", "timeouts", 3), "not one of"),
    (mv("add_check", "no_such_check", True), "no check"),
    (mv("add_check", "recheck_on_agreed_nv", True), "change nothing"),   # recheck is on: no effect
    (mv("add_check", "guard_strict_decimals", "on"), "value is true"),
    (mv("edit_section", "analyst_revise.md", "x"), "<prompt file>#<heading>"),
    (mv("edit_section", "baseline.md#Working limits", "x"), "not a prompt a move may edit"),
    (mv("edit_section", "proposer.md#What to do", "x"), "not a prompt a move may edit"),
    (mv("edit_section", "analyst_revise.md#No such heading", "x"), "no single section"),
    (mv("edit_section", "analyst_revise.md#Working limits", WORKING_LIMITS), "same as the old one"),
    (mv("edit_section", "analyst_revise.md#Working limits", "## A heading\nbody {time_limit_minutes} {answer_file}"),
     "heading of its own"),
    (mv("edit_section", "analyst_revise.md#Working limits", "a { brace"), "braces"),
])
def test_invalid_moves_are_refused(tmp_path, move, words):
    inc = base_variant(tmp_path)
    problems = autoloop.validate(move, autoloop.settings(inc), inc)
    assert problems and words in " ".join(problems), problems


def test_edit_section_refuses_placeholder_drift(tmp_path):
    inc = base_variant(tmp_path)
    current = autoloop.settings(inc)
    dropped = WORKING_LIMITS.replace("{time_limit_minutes}", "thirty")
    added = WORKING_LIMITS + "- Your study is {study_id}.\n"
    renamed = WORKING_LIMITS.replace("{answer_file}", "{answer}")
    escaped = WORKING_LIMITS + "- Write {{\"a\": 1}} when in doubt.\n"     # a literal brace is not a field
    assert "drops {time_limit_minutes}" in " ".join(
        autoloop.validate(mv("edit_section", "analyst_revise.md#Working limits", dropped), current, inc))
    assert "adds {study_id}" in " ".join(
        autoloop.validate(mv("edit_section", "analyst_revise.md#Working limits", added), current, inc))
    both = " ".join(autoloop.validate(mv("edit_section", "analyst_revise.md#Working limits", renamed), current, inc))
    assert "drops {answer_file}" in both and "adds {answer}" in both
    assert autoloop.validate(mv("edit_section", "analyst_revise.md#Working limits", escaped), current, inc) == []


def test_a_move_from_the_proposer_is_checked_for_shape():
    assert autoloop.move_from([], "m01")[0] is None
    move, problems = autoloop.move_from({"kind": "toggle", "target": "guard"}, "m01")
    assert move is None and 'lacks "value"' in problems[0]
    move, problems = autoloop.move_from({"kind": "toggle", "target": "guard", "value": False, "rationale": "x",
                                         "affects_first_round": True, "id": "mine"}, "m07")
    assert problems == [] and move.id == "m07" and move.affects_first_round is False


def test_the_check_registry_is_small_and_explicit():
    assert set(check_registry.CHECKS) == {"recheck_on_agreed_nv", "guard_strict_decimals"}
    for c in check_registry.CHECKS.values():
        assert c.id and c.description and c.config in ("recheck", "guard")
    assert protocol.Config(checks=["guard_strict_decimals", "guard_strict_decimals"]).checks == ("guard_strict_decimals",)
    with pytest.raises(ValueError):
        protocol.Config(checks=("no_such_check",))
    assert protocol.Config().checks == ()


# ----- 2. the overlay never touches lab/prompts -----

def test_variants_and_the_overlay_never_touch_lab_prompts(tmp_path):
    before = prompts_hash()
    inc = base_variant(tmp_path)
    value = WORKING_LIMITS.rstrip("\n") + "\n- Check every number against its output.\n"
    variant = autoloop.make_variant(mv("edit_section", "analyst_revise.md#Working limits", value), inc)
    assert prompts_hash() == before
    written = (variant / "prompts" / "analyst_revise.md").read_text()
    assert "Check every number against its output." in written
    assert written.replace("- Check every number against its output.\n", "") == (PROMPTS / "analyst_revise.md").read_text()
    assert autoloop.section_body(written, "Working limits").strip() == value.strip()
    # template() reads the overlay when the file is there, and lab/prompts otherwise
    assert protocol.template("analyst_revise", str(variant / "prompts")) == written
    assert protocol.template("analyst", str(variant / "prompts")) == (PROMPTS / "analyst.md").read_text()
    assert protocol.template("analyst_revise") == (PROMPTS / "analyst_revise.md").read_text()
    # a child variant keeps its parent's overrides
    move2 = Move("m02", "threshold", "recheck_p", 0.1)
    child = autoloop.make_variant(move2, variant)
    assert (child / "prompts" / "analyst_revise.md").read_text() == written
    assert json.loads((child / "move.json").read_text())["parent"] == variant.name
    assert autoloop.settings(child)["recheck_p"] == 0.1 and autoloop.settings(variant)["recheck_p"] == 0.05
    assert prompts_hash() == before


def test_lab_and_arm_take_prompt_dir_and_lab_takes_the_new_flags(tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    args = run.build_parser().parse_args(["lab", "--benchmark", "x", "--run-dir", str(tmp_path / "r"),
                                          "--prompt-dir", str(overlay), "--snapshot-first-round"])
    cfg = run.make_config(args)
    assert cfg.prompt_dir == str(overlay.resolve()) and cfg.snapshot_first_round and cfg.reuse_first_round is None
    args = run.build_parser().parse_args(["arm", "--arm", "self", "--benchmark", "x", "--run-dir", str(tmp_path / "a"),
                                          "--prompt-dir", str(overlay)])
    assert args.prompt_dir == str(overlay)
    default = run.make_config(run.build_parser().parse_args(["lab", "--benchmark", "x", "--run-dir", "r"]))
    assert (default.prompt_dir, default.reuse_first_round, default.snapshot_first_round, default.checks) == \
        (None, None, False, ())
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"recheck_p": 0.1, "checks": ["guard_strict_decimals"], "brief_teams": ["team-a", "team-b"]}))
    cfg = run.make_config(run.build_parser().parse_args(["lab", "--benchmark", "x", "--run-dir", "r",
                                                         "--config-file", str(conf)]))
    assert cfg.recheck_p == 0.1 and cfg.checks == ("guard_strict_decimals",) and cfg.brief_teams == ("team-a", "team-b")
    conf.write_text(json.dumps({"max_usd": 5}))
    with pytest.raises(SystemExit):
        run.make_config(run.build_parser().parse_args(["lab", "--benchmark", "x", "--run-dir", "r",
                                                       "--config-file", str(conf)]))


# ----- 3. affects_first_round is computed -----

@pytest.mark.parametrize("move, first", [
    (mv("edit_section", "analyst.md#Working limits", "x"), True),
    (mv("edit_section", "library.md#result.json", "x"), True),
    (mv("edit_section", "director_relay.md#The task", "x"), True),
    (mv("edit_section", "common.md#How this lab works (read once)", "x"), True),
    (mv("edit_section", "analyst_revise.md#Working limits", "x"), False),
    (mv("edit_section", "analyst_recheck.md#Working limits", "x"), False),
    (mv("toggle", "library", False), True),
    (mv("toggle", "team_search", False), True),
    (mv("toggle", "brief_teams", True), True),
    (mv("toggle", "guard", False), False),
    (mv("threshold", "max_searches", 4), True),
    (mv("threshold", "recheck_p", 0.1), False),
    (mv("add_check", "guard_strict_decimals", True), False),
])
def test_affects_first_round_is_computed(move, first):
    assert autoloop.first_round(move) is first


def test_make_variant_overrides_what_the_proposer_claims(tmp_path):
    inc = base_variant(tmp_path)
    move = Move("m01", "threshold", "recheck_p", 0.1, "x", affects_first_round=True)
    variant = autoloop.make_variant(move, inc)
    assert json.loads((variant / "move.json").read_text())["affects_first_round"] is False
    move = Move("m02", "toggle", "team_search", False, "x", affects_first_round=False)
    assert json.loads((autoloop.make_variant(move, inc) / "move.json").read_text())["affects_first_round"] is True


# ----- 4. the rules fallback -----

def test_rules_follow_the_top_failure_kind_and_skip_what_was_tried(tmp_path):
    v3 = autoloop.make_base_variant(tmp_path / "variants", protocol.Config(), name="v3")
    current = autoloop.settings(v3)
    failures = {NOT_VERIFIABLE_KIND: 5, autoloop.DAMAGE_KIND: 2}
    first = autoloop.rules_move([], failures, current, v3)
    assert (first.kind, first.target, first.value) == ("toggle", "recheck", True)
    tried = [{"kind": "move.proposed", "data": {"move": first.record()}}]
    second = autoloop.rules_move(tried, failures, current, v3)
    # the re-check is still off on v3, so its thresholds would change nothing and are skipped
    assert (second.kind, second.target, second.value) == ("add_check", "recheck_on_agreed_nv", True)
    # on the v4 base the re-check is on already, so the queue starts at the threshold sweep
    inc = base_variant(tmp_path)
    nxt = autoloop.rules_move([], failures, autoloop.settings(inc), inc)
    assert (nxt.target, nxt.value) == ("recheck_p", 0.1)
    # damages first: the guard is on in v4, so the stricter decimals come next
    damage = autoloop.rules_move([], {autoloop.DAMAGE_KIND: 3, NOT_VERIFIABLE_KIND: 1}, autoloop.settings(inc), inc)
    assert (damage.kind, damage.target) == ("add_check", "guard_strict_decimals")
    damage_v3 = autoloop.rules_move([], {autoloop.DAMAGE_KIND: 3}, current, v3)
    assert (damage_v3.target, damage_v3.value) == ("guard", True)


def test_rules_walk_the_whole_queue_then_give_none(tmp_path):
    inc = base_variant(tmp_path)
    current, entries, seen = autoloop.settings(inc), [], []
    while (m := autoloop.rules_move(entries, {NOT_VERIFIABLE_KIND: 1}, current, inc)) is not None:
        seen.append((m.kind, m.target, m.value))
        entries.append({"kind": "move.proposed", "data": {"move": m.record()}})
        assert len(seen) < 30
    assert seen[:4] == [("threshold", "recheck_p", 0.1), ("threshold", "recheck_p", 0.2),
                        ("threshold", "recheck_min_n", 10), ("threshold", "recheck_min_n", 5)]
    assert ("add_check", "recheck_on_agreed_nv", True) not in seen      # no effect with the re-check on
    assert seen[-1] == ("toggle", "brief_teams", True)
    assert len(seen) == len(set(seen))


# ----- 5. pairing and acceptance -----

def outcomes(passes: dict, usd=None, runs=10) -> dict:
    return {"tasks": {sid: {"passed": p, "usd": usd, "runs": runs} for sid, p in passes.items()}}


INC = {"t1": False, "t2": False, "t3": True, "t4": True, "t5": False}


def test_paired_outcomes_per_task():
    p = autoloop.paired(outcomes(INC), outcomes({"t1": True, "t2": False, "t3": False, "t4": True, "t5": True}))
    assert {sid: t["outcome"] for sid, t in p["tasks"].items()} == {
        "t1": "rescued", "t2": "same-fail", "t3": "damaged", "t4": "same-pass", "t5": "rescued"}
    assert (p["rescued"], p["damaged"], p["same_pass"], p["same_fail"], p["net"]) == (2, 1, 1, 1, 1)
    assert p["cost_basis"] == "agent runs" and p["cost_per_task"]["ratio"] == 1.0


def test_net_exactly_T_is_accepted_and_one_less_is_not():
    p = autoloop.paired(outcomes(INC), outcomes({"t1": True, "t2": True, "t3": True, "t4": True, "t5": False}))
    assert p["net"] == 2 and autoloop.accept(p, 2)["accepted"]
    assert not autoloop.accept(p, 3)["accepted"]


def test_too_many_damages_are_refused_even_with_enough_net():
    inc = {f"t{i}": i > 6 for i in range(1, 11)}            # t7..t10 pass
    var = {f"t{i}": (i <= 6) or i == 10 for i in range(1, 11)}   # rescues 6, damages t7, t8, t9
    p = autoloop.paired(outcomes(inc), outcomes(var))
    assert (p["rescued"], p["damaged"], p["net"]) == (6, 3, 3)
    verdict = autoloop.accept(p, 2)
    assert not verdict["accepted"] and "damaged 3 tasks" in verdict["reasons"][0]
    assert autoloop.accept(p, 2, max_damage=3)["accepted"]


def test_cost_per_task_is_capped_at_the_ratio():
    better = {"t1": True, "t2": True, "t3": True, "t4": True, "t5": True}
    p = autoloop.paired(outcomes(INC, usd=2.0), outcomes(better, usd=2.6))
    assert p["cost_basis"] == "dollars" and p["cost_per_task"]["ratio"] == 1.3
    verdict = autoloop.accept(p, 2)
    assert not verdict["accepted"] and "cost per task" in verdict["reasons"][0]
    edge = autoloop.paired(outcomes(INC, usd=2.0), outcomes(better, usd=2.5))
    assert edge["cost_per_task"]["ratio"] == 1.25 and autoloop.accept(edge, 2)["accepted"]
    # a dollar figure missing anywhere: both are costed in agent runs
    mixed = autoloop.paired(outcomes(INC, usd=2.0), outcomes(better, usd=None, runs=13))
    assert mixed["cost_basis"] == "agent runs" and mixed["cost_per_task"]["ratio"] == 1.3
    free = autoloop.paired(outcomes(INC, usd=0.0), outcomes(better, usd=0.5))
    assert free["cost_per_task"]["ratio"] is None and not autoloop.accept(free, 2)["accepted"]


def test_a_task_missing_from_the_variant_counts_as_failed():
    var = outcomes({"t1": True, "t2": False, "t3": True})
    p = autoloop.paired(outcomes(INC), var)
    assert p["missing"] == ["t4", "t5"] and p["tasks"]["t4"]["outcome"] == "damaged"


def test_the_second_replicate_needs_net_above_zero():
    win = autoloop.paired(outcomes(INC), outcomes({**INC, "t1": True}))
    tie = autoloop.paired(outcomes(INC), outcomes({**INC, "t1": True, "t3": False}))
    assert autoloop.confirm(win)["accepted"] and not autoloop.confirm(tie)["accepted"]


# ----- 6 and 8. the ledger and the stop rules, with a scripted proposer and evaluator -----

def fake_incumbent_run(path: pathlib.Path, passes: dict, kind=NOT_VERIFIABLE_KIND) -> pathlib.Path:
    path.mkdir(parents=True)
    finish = [{"kind": "agent.finish", "study": sid, "role": "analyst", "cost_usd": None}
              for sid in passes for _ in range(10)]          # ten agent runs a task, as outcomes() reports
    (path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in finish))
    studies = {sid: {"lab": {"grade": {"passed": p}},
                     "candidates": [{"id": "final", "kind": "final", "passed": p, "outcome_kind": None if p else kind}]}
               for sid, p in passes.items()}
    (path / "results.json").write_text(json.dumps({"studies": studies}))
    return path


def scripted(tmp_path, results: list, moves: list | None = None, **settings):
    """A Loop whose proposer returns `moves` in turn (None: no valid move) and whose evaluator
    returns `results` in turn: each a dict of passes, or (passes, spent_usd)."""
    inc = base_variant(tmp_path)
    inc_run = fake_incumbent_run(tmp_path / "inc-run", INC)
    queue = list(results)
    proposals = list(moves or [])
    calls = {"evaluate": [], "propose": 0}

    def evaluate_fn(variant, incumbent_run, panel, replicate, *, loop):
        item = queue.pop(0)
        passes, usd = item if isinstance(item, tuple) else (item, 0.0)
        calls["evaluate"].append((variant.name, replicate, pathlib.Path(incumbent_run).name))
        return {"run_dir": str(tmp_path / f"{variant.name}-r{replicate}"), "reused_from": None, "spent": None,
                "spent_usd": usd, "incomplete": None, "outcomes": outcomes(passes)}

    def propose_fn(loop, *, move_id, incumbent, current, failures, moves_so_far):
        calls["propose"] += 1
        spec = proposals.pop(0) if proposals else None
        if spec is None:
            return None, ["no proposal"], {}
        return Move(move_id, *spec, "scripted"), [], {"lesson": "scripted"}

    s = autoloop.LoopSettings(benchmark="fake", panel=sorted(INC), incumbent_run=inc_run, root=tmp_path / "loop",
                              variants_dir=tmp_path / "variants", incumbent=inc, **settings)
    loop = autoloop.Loop(s, evaluate_fn=evaluate_fn, propose_fn=propose_fn, adapter=object())
    return loop, calls


SAME = dict(INC)
BETTER = {**INC, "t1": True, "t2": True}             # net +2
SLIGHTLY = {**INC, "t1": True}                       # net +1


def kinds(loop) -> list[str]:
    return [e["kind"] for e in loop.entries()]


def test_an_accepted_move_needs_a_second_replicate_and_becomes_the_incumbent(tmp_path):
    loop, calls = scripted(tmp_path, [BETTER, SLIGHTLY, SAME, SAME, SAME],
                           moves=[("threshold", "recheck_p", 0.1), ("threshold", "recheck_min_n", 40),
                                  ("threshold", "recheck_min_n", 10), ("threshold", "recheck_p", 0.02)])
    summary = loop.run()
    assert summary["incumbent"] == "m01" and summary["reason"] == "3 rejections in a row"
    assert calls["evaluate"][:3] == [("m01", 1, "inc-run"), ("m01", 2, "inc-run"), ("m02", 1, "m01-r1")]
    assert kinds(loop) == ["note", "move.proposed", "move.evaluated", "move.evaluated", "move.accepted",
                           *["move.proposed", "move.evaluated", "move.rejected"] * 3, "note"]
    start, *_, stop = loop.entries()
    assert start["data"]["event"] == "loop.start" and start["data"]["noise_threshold"] == 2
    assert start["data"]["noise_threshold_source"] == autoloop.DEFAULT_T_SOURCE
    accepted = next(e for e in loop.entries() if e["kind"] == "move.accepted")["data"]
    assert accepted["new_incumbent"] == "m01" and [n["net"] for n in accepted["numbers"]] == [2, 1]
    ev = next(e for e in loop.entries() if e["kind"] == "move.evaluated")["data"]
    assert ev["paired"]["tasks"]["t1"]["outcome"] == "rescued" and ev["verdict"]["accepted"]
    m02 = json.loads((loop.s.variants_dir / "m02" / "move.json").read_text())
    assert m02["parent"] == "m01"                 # later moves build on the new incumbent
    pointer = json.loads((loop.s.variants_dir / "incumbent" / "pointer.json").read_text())
    assert pointer["variant"] == "m01" and pointer["incumbent_run"].endswith("m01-r1")
    assert stop["data"]["event"] == "loop.stop" and ledger.verify_chain(loop.s.ledger_path) == []


def test_a_second_replicate_that_fails_rejects_the_move(tmp_path):
    loop, calls = scripted(tmp_path, [BETTER, SAME], moves=[("threshold", "recheck_p", 0.1)], max_moves=1)
    summary = loop.run()
    assert summary["incumbent"] == "v4-base" and summary["reason"] == "1 moves made (--max-moves)"
    rejected = next(e for e in loop.entries() if e["kind"] == "move.rejected")["data"]
    assert rejected["replicate"] == 2 and "second replicate" in rejected["reasons"][0]
    assert [n["net"] for n in rejected["numbers"]] == [2, 0]


def test_stop_after_max_moves(tmp_path):
    loop, calls = scripted(tmp_path, [SAME, SAME], moves=[("threshold", "recheck_p", 0.1),
                                                          ("threshold", "recheck_p", 0.02)], max_moves=2)
    assert loop.run()["reason"] == "2 moves made (--max-moves)" and len(calls["evaluate"]) == 2


def test_stop_after_three_rejections_in_a_row(tmp_path):
    loop, calls = scripted(tmp_path, [SAME] * 5, moves=[("threshold", "recheck_p", v) for v in (0.1, 0.02, 0.01, 0.15)])
    assert loop.run()["reason"] == "3 rejections in a row" and len(calls["evaluate"]) == 3


def test_stop_when_the_total_cap_is_spent(tmp_path):
    loop, calls = scripted(tmp_path, [(SAME, 6.0)] * 4, moves=[("threshold", "recheck_p", v) for v in (0.1, 0.02, 0.01)],
                           max_usd=10.0)
    summary = loop.run()
    assert summary["reason"] == "the total cap of $10.0 is spent" and len(calls["evaluate"]) == 2
    assert summary["spent_usd"] == 12.0


def test_invalid_or_repeated_proposals_fall_back_to_the_rules(tmp_path):
    loop, calls = scripted(tmp_path, [SAME] * 3, moves=[("threshold", "recheck_p", 0.1), ("threshold", "recheck_p", 0.1),
                                                        ("toggle", "max_agents", True)])
    loop.run()
    proposed = [e["data"] for e in loop.entries() if e["kind"] == "move.proposed"]
    assert [p["path"] for p in proposed] == ["proposer", "rules", "rules"]
    assert "repeats a move proposed before" in proposed[1]["proposer_problems"]
    assert "not one of" in proposed[2]["proposer_problems"][0]
    # the incumbent fails with "judged a settled claim not verifiable": the rules sweep the re-check thresholds
    assert [(p["move"]["target"], p["move"]["value"]) for p in proposed[1:]] == [("recheck_p", 0.2), ("recheck_min_n", 10)]
    assert proposed[1]["failures_seen"] == {NOT_VERIFIABLE_KIND: 3}
    summary = autoloop.ledger_summary(loop.entries())
    assert len(summary) == 3 and "(rules)" in summary[1] and "rejected" in summary[1]


def test_the_proposer_brief_shows_summaries_only(tmp_path):
    inc = base_variant(tmp_path)
    entries = [{"kind": "move.proposed", "data": {"loop": "L", "move_id": "m01", "path": "rules",
                                                  "move": mv("threshold", "recheck_p", 0.1).record()}},
               {"kind": "move.evaluated", "data": {"loop": "L", "move_id": "m01", "replicate": 1,
                                                   "paired": {"rescued": 1, "damaged": 0, "net": 1,
                                                              "cost_per_task": {"ratio": 1.0},
                                                              "tasks": {"bd14": {"outcome": "rescued"}}}}},
               {"kind": "move.rejected", "data": {"loop": "L", "move_id": "m01", "reasons": ["net 1 is under T = 2"]}}]
    brief = autoloop.proposer_brief(entries=entries, failures={NOT_VERIFIABLE_KIND: 8}, current=autoloop.settings(inc),
                                    incumbent=inc, moves_so_far=1, T=2, cost_ratio_max=1.25, max_damage=1, panel_size=23)
    assert brief.startswith(run.PROPOSER_HEADING)
    assert "Moves proposed so far: 1" in brief and "net 1 is under T = 2" in brief
    assert f"- {NOT_VERIFIABLE_KIND}: 8" in brief and "recheck_p" in brief and "guard_strict_decimals" in brief
    assert "bd14" not in brief and "sealed" not in brief.lower()


# ----- strict decimals and the agreed Not verifiable check, where they act -----

def test_guard_strict_decimals_needs_three_significant_digits(tmp_path):
    import time
    started = time.time() - 1
    run.mock_evidence(tmp_path)               # quotes "p = 0.01, n = 120"
    assert protocol.check_evidence(tmp_path, started, "answer.json") == (True, "the evidence checks out")
    ok, why = protocol.check_evidence(tmp_path, started, "answer.json", strict_decimals=True)
    assert not ok and "0.01" in why and "guard_strict_decimals" in why
    (tmp_path / "evidence.json").write_text(json.dumps({"field": "label", "script": "mock_check.py",
                                                        "output_file": "mock_check_output.txt",
                                                        "result": "p = 0.0100, n = 120"}))
    assert protocol.check_evidence(tmp_path, started, "answer.json", strict_decimals=True)[0]
    cfg = protocol.Config(guard=True, checks=("guard_strict_decimals",))
    assert protocol.evidence_note(cfg).startswith(protocol.EVIDENCE_NOTE) and "3 significant digits" in protocol.evidence_note(cfg)
    assert protocol.evidence_note(protocol.Config(guard=True)) == protocol.EVIDENCE_NOTE
    assert protocol.evidence_note(protocol.Config()) == ""


def lab_run(tmp_path, name, cfg, adapter=None, backend=None, memory_note=False):
    adapter = adapter or Labels(tmp_path / "bench")
    run_dir = tmp_path / name
    log = JsonlEventLog(run_dir / "events.jsonl")
    backend = backend or (NotingBackend(run.mock_script(adapter)) if memory_note else MockBackend(run.mock_script(adapter)))
    lab = protocol.Lab(adapter, backend, LocalMemory(log, run_dir / "memory.jsonl"), log, run_dir, cfg,
                       exa_call=lambda path, body: (_ for _ in ()).throw(AssertionError("no Exa call")))
    out = lab.run(adapter.studies())
    return adapter, backend, log, run_dir, out


def test_recheck_on_agreed_nv_rechecks_only_agreed_abstentions_with_the_recheck_off(tmp_path):
    class BothAbstain(MockBackend):
        """Both teams answer "Not verifiable" first."""
        def __init__(self, adapter):
            inner = run.mock_script(adapter)

            def script(agent, brief, ws):
                out = inner(agent, brief, ws)
                if "# Your job: analyst on a blind team" in brief:
                    doc = json.loads((ws / "answer.json").read_text())
                    doc["answer"]["label"] = "Not verifiable"
                    (ws / "answer.json").write_text(json.dumps(doc))
                return out
            super().__init__(script)

    one = {"s1": "True"}
    adapter = Labels(tmp_path / "b1", one)
    cfg = protocol.Config(library=False, team_search=False, research=False, challenge=False,
                          checks=("recheck_on_agreed_nv",))
    _, backend, log, run_dir, out = lab_run(tmp_path, "agreed", cfg, adapter, BothAbstain(adapter))
    assert sorted(e["team"] for e in log.events("recheck.start")) == ["team-a", "team-b"]
    assert "recheck" in json.loads((run_dir / "s1" / "study.json").read_text())
    assert replay.replay(run_dir, out=lambda *_: None) == 0
    # one team abstaining is not enough without Config.recheck
    adapter2 = Labels(tmp_path / "b2", one)
    _, _, log2, _, _ = lab_run(tmp_path, "single", cfg, adapter2)
    assert log2.events("recheck.start") == []
    # and with the check off nothing changes
    _, _, log3, run3, _ = lab_run(tmp_path, "off", protocol.Config(library=False, team_search=False, research=False,
                                                                     challenge=False), Labels(tmp_path / "b3", one),
                                  BothAbstain(Labels(tmp_path / "b3", one)))
    assert log3.events("recheck.start") == [] and "recheck" not in json.loads((run3 / "s1" / "study.json").read_text())


# ----- 7. reuse mode -----

class NotingBackend(MockBackend):
    """The mock, plus: each first-round analyst writes one note to memory through the gateway, as a
    real agent would (lab/tools/memory.py), so a reused run must replay it."""

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800):
        if "# Your job: analyst on a blind team" in brief and env:
            out = subprocess.run([sys.executable, str(ROOT / "lab" / "tools" / "memory.py"), "write",
                                  f"NOTE from {agent.id}: the smoking column is coded 0/1", "--label", "note"],
                                 env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
            assert out.returncode == 0, out.stderr
        return super().run(agent, brief, workspace, readable=readable, env=env, timeout_s=timeout_s)


FIRST_ROUND_JOBS = ("# Your job: the lab's library", "# Your job: director, briefing the teams",
                    "# Your job: analyst on a blind team")


def first_round_calls(backend) -> int:
    return sum(any(j in c.brief for j in FIRST_ROUND_JOBS) for c in backend.calls)


def test_an_incumbent_run_with_snapshots_then_a_reused_run(tmp_path):
    adapter = Labels(tmp_path / "bench")
    _, inc_backend, inc_log, inc_dir, inc_out = lab_run(tmp_path, "inc", v4(snapshot_first_round=True), adapter,
                                                        memory_note=True)
    for sid in TRUTHS:
        for team in ("team-a", "team-b"):
            snap = inc_dir / sid / "first-round" / team
            assert (snap / "answer.json").is_file() and (snap / "result.json").is_file()
        # the snapshot is the answer before the re-check: team A said "Not verifiable"
        assert json.loads((inc_dir / sid / "first-round" / "team-a" / "answer.json").read_text())["answer"]["label"] \
            == "Not verifiable"
    assert first_round_calls(inc_backend) == 3 * 4          # library, briefing, two analysts per study

    _, backend, log, run_dir, out = lab_run(tmp_path, "reused", v4(reuse_first_round=str(inc_dir)), adapter)
    assert first_round_calls(backend) == 0
    assert {sid: o["status"] for sid, o in out.items()} == {sid: o["status"] for sid, o in inc_out.items()}
    assert len(backend.calls) == len(inc_backend.calls) - 12
    for sid in TRUTHS:
        for team in ("team-a", "team-b"):
            name = f"{team}-analysis.json"
            assert (run_dir / sid / "candidates" / name).read_bytes() == (inc_dir / sid / "candidates" / name).read_bytes()
        reused = [e for e in log.events("study.reused") if e["study"] == sid]
        assert len(reused) == 1 and reused[0]["source"] == str(inc_dir.resolve())
        assert [c["valid"] for c in reused[0]["candidates"]] == [True, True]
        assert reused[0]["memory_replayed"]["writes"] == 2 and reused[0]["memory_replayed"]["pass_downs"] == 1
        record = json.loads((run_dir / sid / "study.json").read_text())
        assert record["reused"]["from"] == str(inc_dir.resolve()) and len(record["briefing"]) == 1
        assert record["switches"]["reuse_first_round"] == str(inc_dir.resolve())
        assert not (run_dir / sid / "first-round").exists()     # snapshot_first_round is off in this run
    # later rounds can recall what the first round wrote: the notes and the briefing are in the new memory
    memory = [json.loads(line) for line in (run_dir / "memory.jsonl").read_text().splitlines()]
    notes = [m for m in memory if m["text"].startswith("NOTE from s1-team-a")]
    assert len(notes) == 1 and notes[0]["department"] == "s1-team-a"
    assert any(m["department"] == "s1-team-a" and m["text"].startswith("PITFALL (mock)") for m in memory)
    assert not any(m["department"] == "s1-team-b" and m["text"].startswith("PITFALL") for m in memory)
    assert replay.replay(run_dir, out=lambda *_: None) == 0
    assert replay.replay(inc_dir, out=lambda *_: None) == 0
    # grading reads the copied candidates; the reused run's lab grades match the incumbent's
    inc_results, results = run.grade_run(inc_dir, adapter), run.grade_run(run_dir, adapter)
    assert {s: e["lab"]["grade"]["passed"] for s, e in results["studies"].items()} == \
        {s: e["lab"]["grade"]["passed"] for s, e in inc_results["studies"].items()}
    # a reused run costs what its first round cost too
    cost = autoloop.run_outcomes(run_dir, sorted(TRUTHS))["tasks"]["s1"]["runs"]
    assert cost == autoloop.run_outcomes(inc_dir, sorted(TRUTHS))["tasks"]["s1"]["runs"]


def test_a_reused_run_can_be_reused_in_turn(tmp_path):
    adapter = Labels(tmp_path / "bench", {"s1": "True"})
    _, _, _, inc_dir, _ = lab_run(tmp_path, "inc", v4(snapshot_first_round=True), adapter, memory_note=True)
    _, _, _, second, _ = lab_run(tmp_path, "second", v4(reuse_first_round=str(inc_dir), snapshot_first_round=True), adapter)
    assert (second / "s1" / "first-round" / "team-a" / "answer.json").is_file()
    _, backend, log, third, _ = lab_run(tmp_path, "third", v4(reuse_first_round=str(second)), adapter)
    assert first_round_calls(backend) == 0
    reused = log.events("study.reused")[0]
    assert reused["memory_replayed"]["writes"] == 2 and reused["first_round_runs"] == 4
    assert replay.replay(third, out=lambda *_: None) == 0


def test_reuse_needs_the_snapshot(tmp_path):
    adapter = Labels(tmp_path / "bench", {"s1": "True"})
    _, _, _, inc_dir, _ = lab_run(tmp_path, "inc", v4(), adapter)          # no snapshot
    _, backend, log, run_dir, out = lab_run(tmp_path, "reused", v4(reuse_first_round=str(inc_dir)), adapter)
    assert out["s1"]["status"] == "error" and "snapshot" in out["s1"]["error"]
    assert backend.calls == []


def test_default_runs_write_no_snapshot_and_no_new_record_fields(tmp_path):
    adapter = Labels(tmp_path / "bench", {"s1": "True"})
    _, _, log, run_dir, _ = lab_run(tmp_path, "plain", v4(), adapter)
    record = json.loads((run_dir / "s1" / "study.json").read_text())
    assert not (run_dir / "s1" / "first-round").exists() and "reused" not in record
    assert not {"checks", "prompt_dir", "reuse_first_round"} & set(record["switches"])
    assert log.events("study.reused") == [] and log.events("first_round.snapshot") == []


# ----- 9. a full mock loop -----

def test_a_full_mock_loop_end_to_end(tmp_path, monkeypatch):
    before = prompts_hash()
    adapter = Labels(tmp_path / "bench")
    monkeypatch.setitem(run.ADAPTERS, "fakelab", lambda: adapter)
    inc = tmp_path / "incumbent-run"
    common = ["--benchmark", "fakelab", "--studies", "s1,s2,s3", "--backend", "mock", "--memory", "local"]
    assert run.main(["lab", *common, "--run-dir", str(inc), "--preset", "v4", "--model", "opus",
                     "--director-model", "opus", "--snapshot-first-round"]) == 0
    root = tmp_path / "loop"
    assert run.main(["loop", *common, "--incumbent-run", str(inc), "--run-root", str(root), "--max-moves", "3"]) == 0
    entries = ledger.entries(root / "ledger.jsonl")
    assert ledger.verify_chain(root / "ledger.jsonl") == []
    proposed = [e["data"] for e in entries if e["kind"] == "move.proposed"]
    # the mock proposer's first move, then its unusable second (rules fallback), then a repeat (rules again)
    assert [p["path"] for p in proposed] == ["proposer", "rules", "rules"]
    assert proposed[0]["move"]["target"] == "recheck_p" and proposed[0]["move"]["affects_first_round"] is False
    assert proposed[1]["move"]["target"] == "guard_strict_decimals"       # the damage kind's queue
    assert proposed[2]["move"]["target"] == "brief_teams" and proposed[2]["move"]["affects_first_round"] is True
    evaluated = [e["data"] for e in entries if e["kind"] == "move.evaluated"]
    assert [bool(e["reused_from"]) for e in evaluated] == [True, True, False]
    assert evaluated[0]["paired"]["net"] == 0                             # the same answers: no effect
    assert evaluated[1]["paired"]["damaged"] >= 2                         # strict quotes block the mock's evidence
    assert [e["kind"] for e in entries].count("move.rejected") == 3
    stop = entries[-1]["data"]
    assert stop["event"] == "loop.stop" and stop["incumbent"] == "v4-base" and stop["moves"] == 3
    assert json.loads((root / "variants" / "incumbent" / "pointer.json").read_text())["variant"] == "v4-base"
    for name in ("m01-r1", "m02-r1", "m03-r1"):
        assert replay.replay(root / name, out=lambda *_: None) == 0
    assert (root / "proposer" / "m01" / "move.json").is_file()
    assert prompts_hash() == before


def test_the_loop_refuses_an_incumbent_run_that_does_not_match(tmp_path, monkeypatch):
    adapter = Labels(tmp_path / "bench", {"s1": "True"})
    monkeypatch.setitem(run.ADAPTERS, "fakelab", lambda: adapter)
    inc = tmp_path / "inc"
    common = ["--benchmark", "fakelab", "--studies", "s1", "--backend", "mock", "--memory", "local"]
    assert run.main(["lab", *common, "--run-dir", str(inc), "--model", "opus", "--director-model", "opus"]) == 0
    with pytest.raises(SystemExit) as err:
        run.main(["loop", *common, "--incumbent-run", str(inc), "--run-root", str(tmp_path / "loop")])
    text = str(err.value)
    assert "recheck" in text and "guard" in text and "no first-round snapshot" in text


# ----- moves that cannot change anything are refused -----

def test_a_threshold_or_prompt_whose_switch_is_off_is_refused():
    from lab import autoloop
    off = {"recheck": False, "recheck_p": 0.05, "recheck_min_n": 20, "challenge": True}
    m = autoloop.Move(id="x", kind="threshold", target="recheck_p", value=0.1, rationale="r")
    assert any("change nothing" in p for p in autoloop.validate(m, off))
    assert not autoloop.validate(m, {**off, "recheck": True})
    e = autoloop.Move(id="y", kind="edit_section", target="analyst_recheck.md#Anything", value="new", rationale="r")
    assert any("change nothing" in p for p in autoloop.validate(e, off))


# ----- the proposer's prompt copies, and resuming a loop -----

def test_the_proposer_reads_prompt_copies_in_its_workspace(tmp_path):
    from lab.backend_modal import ModalBackend
    from lab.contracts import AgentResult
    inc = base_variant(tmp_path)
    (inc / "prompts").mkdir(exist_ok=True)
    (inc / "prompts" / "analyst_revise.md").write_text("# Override\n")
    seen = {}

    class Fake:
        def run(self, agent, brief, workspace, *, readable, env, timeout_s):
            seen["readable"] = ModalBackend.check_readable(readable, workspace)   # what Modal refuses
            seen["brief"] = brief
            seen["files"] = sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*.md"))
            (workspace / "move.json").write_text(json.dumps({"kind": "threshold", "target": "recheck_p",
                                                             "value": 0.1, "rationale": "r"}))
            return AgentResult(ok=True, result={"lesson": "l"}, seconds=1.0, cost_usd=0.5)

    loop, _ = scripted(tmp_path, [])
    loop._backend = Fake()
    move, problems, extra = autoloop.propose(loop, move_id="m01", incumbent=inc, current=autoloop.settings(inc),
                                             failures={NOT_VERIFIABLE_KIND: 3}, moves_so_far=0)
    assert problems == [] and move.target == "recheck_p" and extra["spent_usd"] == 0.5
    assert seen["readable"] == []
    assert "incumbent-prompts/analyst_revise.md" in seen["files"]
    assert {f"prompts/{n}" for n in autoloop.EDITABLE_PROMPTS} <= set(seen["files"])
    assert not any(f.startswith("prompts/") and f[8:] not in autoloop.EDITABLE_PROMPTS for f in seen["files"])
    assert "`prompts/`" in seen["brief"] and "`incumbent-prompts/`" in seen["brief"]
    assert str(autoloop.PROMPTS) not in seen["brief"]


class Crash(RuntimeError):
    pass


def test_a_crashed_loop_resumes_where_it_stopped(tmp_path):
    moves = [("threshold", "recheck_p", 0.1), ("threshold", "recheck_min_n", 40), ("threshold", "recheck_p", 0.02)]
    loop, calls = scripted(tmp_path, [SAME], moves=list(moves), max_usd=100.0)
    real = loop.evaluate_fn

    def crash_on_second(variant, *a, **k):
        if variant.name == "m02":
            raise Crash("past.dev network error")
        return real(variant, *a, **k)
    loop.evaluate_fn = crash_on_second
    with pytest.raises(Crash):
        loop.run()
    # what m01's evaluation and the proposer spent, as their event logs show it
    for folder, cost in (("m01-r1", 7.0), ("proposer", 1.0)):
        (loop.s.root / folder).mkdir(parents=True, exist_ok=True)
        (loop.s.root / folder / "events.jsonl").write_text(
            json.dumps({"kind": "agent.start", "run_id": "x", "role": "analyst", "model": "opus"}) + "\n"
            + json.dumps({"kind": "agent.finish", "run_id": "x", "cost_usd": cost}) + "\n")

    again, calls2 = scripted(tmp_path / "second", [(SAME, 3.0), (SAME, 3.0)], moves=[moves[2]], max_usd=100.0)
    again.s = loop.s
    again.id, again.resume = loop.id, loop.id
    summary = again.run()
    assert summary["reason"] == "3 rejections in a row"
    assert [c[0] for c in calls2["evaluate"]] == ["m02", "m03"]      # m02 judged again, then a new move
    assert calls2["propose"] == 1 and summary["moves"] == 3
    assert [h["path"] for h in summary["history"]] == ["proposer", "proposer", "proposer"]
    assert summary["spent_usd"] == 7.0 + 1.0 + 3.0 + 3.0
    mine = [e for e in again.entries() if e["data"].get("loop") == loop.id]
    resumed = next(e["data"] for e in mine if e["data"].get("event") == "loop.resume")
    assert resumed["pending_move"] == "m02" and resumed["moves"] == 2 and resumed["in_a_row"] == 1
    assert [e["kind"] for e in mine].count("move.proposed") == 3      # m02 is not proposed twice
    assert ledger.verify_chain(loop.s.ledger_path) == []


def test_a_loop_that_stopped_cannot_resume_but_one_halted_mid_move_can(tmp_path):
    loop, _ = scripted(tmp_path, [SAME], moves=[("threshold", "recheck_p", 0.1)], max_moves=1)
    loop.run()
    again, _ = scripted(tmp_path / "b", [], max_moves=1)
    again.s, again.id, again.resume = loop.s, loop.id, loop.id
    with pytest.raises(ValueError, match="stopped"):
        again.run()
    with pytest.raises(ValueError, match="no loop"):
        third, _ = scripted(tmp_path / "c", [])
        third.id = third.resume = "loop-none"
        third.run()

    halted, _ = scripted(tmp_path / "d", [], moves=[("threshold", "recheck_p", 0.1)], max_moves=1)
    halted.evaluate_fn = lambda variant, inc, panel, rep, *, loop: {
        "run_dir": "x", "reused_from": None, "spent": None, "spent_usd": 0.0, "outcomes": None,
        "incomplete": "the run halted: cap"}
    assert halted.run()["reason"].startswith("evaluation incomplete")
    resumed, calls = scripted(tmp_path / "e", [SAME], max_moves=1)
    resumed.s, resumed.id, resumed.resume = halted.s, halted.id, halted.id
    summary = resumed.run()
    assert [c[0] for c in calls["evaluate"]] == ["m01"] and calls["propose"] == 0
    assert summary["reason"] == "1 moves made (--max-moves)"
