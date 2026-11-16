"""Tests for the control arms (lab/arms.py): the self-challenge arm and the same-cost vote arm.

Offline, with the mock backend's script (lab.run.mock_script) and the fake BioDSA-style adapter of
tests/test_recheck.py. Also the golden briefs of the arms, and a check that `--preset v4` leaves the
lab's first analyst brief and the single agent's brief word for word as before.
"""

import ast
import dataclasses
import json
import pathlib

import pytest

from lab import arms, measures, protocol, replay, run
from lab.backend_mock import MockBackend
from lab.contracts import Study
from lab.eventlog import JsonlEventLog
from lab.memory_local import LocalMemory
from test_recheck import FAST, GOLDEN, STATEMENT, LabelAdapter, assert_replay_matches, no_exa
from test_scbench_wiring import REPRODUCE, lab_briefs


class Costed:
    """The mock backend, with every run costing `cost` dollars."""

    def __init__(self, adapter, cost=1.0):
        self.inner, self.cost = MockBackend(run.mock_script(adapter)), cost

    @property
    def calls(self):
        return self.inner.calls

    def run(self, *args, **kwargs):
        return dataclasses.replace(self.inner.run(*args, **kwargs), cost_usd=self.cost)


def finishes(run_dir):
    return JsonlEventLog(run_dir / "events.jsonl").events("agent.finish")


def self_arm(tmp_path, adapter=None, cost=1.0, **config):
    adapter = adapter or LabelAdapter(tmp_path / "bench")
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    backend = Costed(adapter, cost)
    out = arms.run_self_arm(adapter, backend, log, run_dir, adapter.studies(), "fable", 2,
                            protocol.Config(**config), timeout_s=600)
    return out, adapter, backend, log, run_dir


# ----- role names -----

def test_the_self_arm_runs_solo_then_challenge_then_revise_with_its_own_role_names(tmp_path):
    out, adapter, backend, log, run_dir = self_arm(tmp_path)
    assert out == {"s1": {"status": "changed", "final": {
        "round": "revise", "id": "self-revise", "path": str((run_dir / "arms/self/s1/final/answer.json").resolve())}}}
    runs = [(e["agent"], e["role"], e["phase"]) for e in finishes(run_dir)]
    assert runs == [("self-s1", "self-solo", "self-solo"), ("self-s1-challenger", "self-challenge", "self-challenge"),
                    ("self-s1", "self-revise", "self-revise")]
    briefs = [c.brief for c in backend.calls]
    assert "# Your job: director, challenging one analyst's answer" in briefs[1]
    assert "# Your job: analyst, answering the director's questions" in briefs[2]
    folder = run_dir / "arms" / "self" / "s1"
    questions = json.loads((folder / "solver" / arms.QUESTIONS_FILE).read_text())["questions"]
    assert len(questions) == 1 and questions[0].startswith("QUESTION FROM THE DIRECTOR:")
    assert not (folder / "challenger" / "solver" / "answer.json").exists()      # the challenger never sees the answer
    record = json.loads((folder / "arm.json").read_text())
    assert [c["round"] for c in record["candidates"]] == ["solo", "revise"]
    kinds = [e["kind"] for e in log.events() if e.get("arm") == "self"]
    assert kinds == ["arm.start", "arm.candidate", "arm.questions", "arm.candidate", "arm.final"]
    assert all(e.get("study") == "s1" for e in log.events() if e.get("arm") == "self")


def test_the_self_arm_with_v4_rechecks_and_guards(tmp_path):
    out, adapter, backend, log, run_dir = self_arm(tmp_path, recheck=True, guard=True)
    runs = [(e["agent"], e["role"]) for e in finishes(run_dir)]
    assert runs == [("self-s1", "self-solo"), ("self-s1", "self-recheck"), ("self-s1-challenger", "self-challenge"),
                    ("self-s1", "self-revise")]
    verdict = log.events("recheck.verdict")
    assert len(verdict) == 1 and verdict[0]["accepted"] is True and verdict[0]["arm"] == "self"
    assert [(e["team"], e["round"]) for e in log.events("guard.passed")] == [("self", "recheck")]
    # the mock's revision repeats "True": not a change, so it needs no evidence, and the revision is used
    assert out["s1"]["status"] == "kept" and out["s1"]["final"]["round"] == "revise"
    assert len(log.events("guard.passed")) == 1 and not log.events("guard.blocked")
    assert replay.study_events(replay.read_events(run_dir), "s1") == ([], {})     # no lab study here


def test_the_self_arm_resumes_and_skips_finished_studies(tmp_path):
    out, adapter, backend, log, run_dir = self_arm(tmp_path)
    again = arms.run_self_arm(adapter, backend, log, run_dir, adapter.studies(), "fable", 2, protocol.Config())
    assert again == {"s1": {"status": "changed", "skipped": True}} and len(backend.calls) == 3


def test_the_vote_arm_runs_k_attempts_with_rotating_hints(tmp_path):
    adapter = LabelAdapter(tmp_path / "bench")
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    backend = Costed(adapter)
    out = arms.run_vote_arm(adapter, backend, log, run_dir, adapter.studies(), 3, "fable", 2)
    assert out == {"s1": {1: True, 2: True, 3: True}}
    assert sorted((e["agent"], e["role"], e["phase"]) for e in finishes(run_dir)) == [
        (f"vote-s1-{k}", "vote-arm", f"vote-{k}") for k in (1, 2, 3)]
    hints = [e["hint"] for e in sorted(log.events("arm.attempt"), key=lambda e: e["attempt_number"])]
    assert hints == list(arms.VOTE_HINTS[:3]) and len(set(hints)) == 3
    assert arms.vote_brief(adapter.studies()[0], len(arms.VOTE_HINTS) + 1) == arms.vote_brief(adapter.studies()[0], 1)
    again = arms.run_vote_arm(adapter, backend, log, run_dir, adapter.studies(), 4, "fable", 2)
    assert again == {"s1": {4: True}} and len(backend.calls) == 4


def test_every_arm_role_is_outside_the_lab():
    assert {"self-solo", "self-challenge", "self-revise", "self-recheck"} == measures.SELF_ROLES
    assert measures.VOTE_ROLES == {"vote-arm"}
    assert not measures.ARM_ROLES & measures.LAB_ROLES
    assert {"baseline", "proposer"} <= measures.ARM_ROLES and "recheck" in measures.LAB_ROLES


def roles_in_code(source: str) -> tuple[set, list]:
    """Every role a module passes to an agent run, read from its syntax tree: each `role=` keyword
    given a string, and the positional role argument of each call to a `run_agent` or `run` the module
    defines with a `role` parameter (StudyRun.run_agent, SelfArm.run_agent). Returns (roles, places
    where a role is not a string literal, other than a function handing on its own `role` parameter)."""
    tree = ast.parse(source)
    takes_role = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("run_agent", "run"):
            names = [a.arg for a in node.args.posonlyargs + node.args.args if a.arg not in ("self", "cls")]
            if "role" in names:
                takes_role[node.name] = names.index("role")
    roles, unknown = set(), []

    def take(value, where):
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            roles.add(value.value)
        elif not (isinstance(value, ast.Name) and value.id == "role"):
            unknown.append(f"line {where.lineno}: {ast.unparse(value)}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "role":
                take(kw.value, node)
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if name in takes_role and not any(kw.arg == "role" for kw in node.keywords):
            if len(node.args) > takes_role[name]:
                take(node.args[takes_role[name]], node)
    return roles, unknown


def test_every_role_the_code_uses_is_a_lab_or_arm_role():
    found, unknown = set(), []
    for path in sorted((pathlib.Path(__file__).resolve().parents[1] / "lab").glob("*.py")):
        roles, odd = roles_in_code(path.read_text())
        found |= roles
        unknown += [f"{path.name} {where}" for where in odd]
    assert not unknown, f"roles the test cannot read: {unknown}"
    assert {"analyst", "recheck", "self-recheck", "vote-arm", "baseline"} <= found     # the scan sees them
    assert found - (measures.LAB_ROLES | measures.ARM_ROLES) == set()


def test_the_role_scan_reads_keywords_and_positional_arguments():
    src = ("class R:\n    def run_agent(self, agent, brief, folder, role):\n        pass\n"
           "    def go(self):\n        self.run_agent(1, 2, 3, 'odd-one')\n        x.run(role='kw-role')\n"
           "        self.run_agent(1, 2, 3, role=f'v-{k}')\n")
    roles, unknown = roles_in_code(src)
    assert roles == {"odd-one", "kw-role"} and len(unknown) == 1


# ----- what the self arm's challenger sees -----

def test_notes_without_answer_drops_what_tells_the_answer_or_a_change():
    doc = {"answer_file": "answer.json", "changed": True, "revised": False, "label": "True", "final_answer": 1,
           "method": "the test shows the claim is true", "variables_found": ["labs.csv: albumin", "Not verifiable before"],
           "nested": {"answer_label": "x", "kept": "ok"}, "confidence": 0.4}
    out = arms.notes_without_answer(doc, [{"label": "Not verifiable"}, {"label": "True"}])
    assert out == {"answer_file": "answer.json", "variables_found": ["labs.csv: albumin"], "nested": {"kept": "ok"},
                   "confidence": 0.4}


def test_the_challenger_after_an_accepted_recheck_does_not_learn_it_changed(tmp_path):
    out, adapter, backend, log, run_dir = self_arm(tmp_path, recheck=True, guard=True)
    folder = run_dir / "arms" / "self" / "s1"
    assert json.loads((folder / "candidates" / "self-recheck-result.json").read_text())["changed"] is True
    seen = json.loads((folder / "challenger" / "solver" / "result.json").read_text())
    assert "changed" not in seen and seen.get("answer_file") == "answer.json"
    text = json.dumps(seen).lower()
    assert "not verifiable" not in text and "true" not in text


def rejecting_backend(adapter, seen: dict):
    """The mock, except that the solver's re-check writes no test_result.json (so the lab rejects it),
    and the solo run leaves symbolic links among its scripts. Records the solver's folder as the
    challenger starts."""
    inner = run.mock_script(adapter)
    secret = adapter.root / "secret.py"
    secret.write_text("SECRET = 'True'\n")

    def script(agent, brief, ws):
        result = inner(agent, brief, ws)
        if "# Your job: re-check a Not verifiable answer" in brief:
            (ws / protocol.TEST_FILE).unlink()
        if "# Your job: director, challenging one analyst's answer" in brief:
            solver = ws.parent / "solver"
            seen["answer"] = json.loads((solver / "answer.json").read_text())["answer"]["label"]
            seen["result"] = json.loads((solver / "result.json").read_text())
        if agent.id == "self-s1" and not (ws / "peek.py").exists():
            (ws / "real.py").write_text("x = 1\n")
            (ws / "peek.py").symlink_to(secret)
            (ws / "inside.py").symlink_to(ws / "real.py")
        return result

    return MockBackend(script)


def test_a_rejected_recheck_in_the_self_arm_restores_the_kept_answer(tmp_path):
    adapter = LabelAdapter(tmp_path / "bench")
    seen = {}
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    arms.run_self_arm(adapter, rejecting_backend(adapter, seen), log, run_dir, adapter.studies(), "fable", 2,
                      protocol.Config(recheck=True, guard=True), timeout_s=600)
    folder = run_dir / "arms" / "self" / "s1"
    record = json.loads((folder / "arm.json").read_text())
    assert [(r["accepted"], r["in_use"]) for r in record["recheck"]] == [(False, "self-solo")]
    assert seen["answer"] == "Not verifiable"              # the solver's folder holds the kept answer again
    assert seen["result"] == json.loads((folder / "candidates" / "self-solo-result.json").read_text())
    assert not log.events("guard.blocked")
    notes = folder / "challenger" / "solver"
    assert (notes / "real.py").is_file()
    assert not (notes / "peek.py").exists() and not (notes / "inside.py").exists()   # symbolic links are skipped


# ----- grading and cost -----

def full_run(tmp_path, cost=1.0):
    """A v4 lab run, two single-agent attempts, the self arm and a 3-attempt vote arm in one run directory."""
    adapter = LabelAdapter(tmp_path / "bench")
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    backend = Costed(adapter, cost)
    cfg = protocol.Config(**{**FAST, "recheck": True, "guard": True})
    lab = protocol.Lab(adapter, backend, LocalMemory(log, run_dir / "memory.jsonl"), log, run_dir, cfg, exa_call=no_exa)
    lab.run(adapter.studies())
    lab_runs = len(finishes(run_dir))
    from lab.baseline import run_baselines
    run_baselines(adapter, backend, log, run_dir, adapter.studies(), 2, "fable", 2)
    arms.run_self_arm(adapter, backend, log, run_dir, adapter.studies(), "fable", 2, cfg)
    arms.run_vote_arm(adapter, backend, log, run_dir, adapter.studies(), 3, "fable", 2)
    return adapter, run_dir, lab_runs


def test_grade_run_scans_the_arms(tmp_path):
    adapter, run_dir, _ = full_run(tmp_path)
    results = run.grade_run(run_dir, adapter)
    entry = results["studies"]["s1"]
    rows = {r["id"]: r for r in entry["candidates"]}
    self_ids = {i for i, r in rows.items() if r["kind"] == "self"}
    assert self_ids == {"self-solo", "self-recheck", "self-revise", "self-final"}
    assert rows["self-solo"]["passed"] is False and rows["self-recheck"]["passed"] is True
    assert rows["self-final"]["same_as"] == "self-revise" and rows["self-final"]["passed"] is True
    vote_ids = {i for i, r in rows.items() if r["kind"] == "vote" and r["team"] == "same-cost vote arm"}
    assert vote_ids == {"vote-attempt-1", "vote-attempt-2", "vote-attempt-3", "vote-arm"}
    assert rows["vote-arm"]["passed"] is True and entry["vote_arm"]["vote"]["answer"]["answer"]["label"] == "True"
    assert entry["self"] == {"status": "kept", "final": entry["self"]["final"], "final_round": "revise"}
    t = results["totals"]
    assert (t["self_passed"], t["self_studies"], t["vote_arm_passed"], t["vote_arm_studies"]) == (1, 1, 1, 1)
    assert (t["baseline_attempts"], t["lab_studies"]) == (2, 1)
    assert_replay_matches(run_dir)        # the arms' recheck and guard events do not disturb the lab's replay
    lab_events, _ = replay.study_events(replay.read_events(run_dir), "s1")
    assert lab_events and not any(e.get("arm") for e in lab_events)


def test_without_arms_results_keep_their_old_shape(tmp_path):
    adapter = LabelAdapter(tmp_path / "bench")
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    lab = protocol.Lab(adapter, Costed(adapter), LocalMemory(log, run_dir / "memory.jsonl"), log, run_dir,
                       protocol.Config(**FAST), exa_call=no_exa)
    lab.run(adapter.studies())
    results = run.grade_run(run_dir, adapter)
    assert set(results["totals"]) == {"lab_passed", "lab_studies", "baseline_attempts_passed", "baseline_attempts",
                                      "vote_passed", "vote_studies"}
    assert not {"self", "vote_arm"} & set(results["studies"]["s1"])
    m = measures.measure(run_dir)
    assert "self_challenge" not in m["headline"] and "vote_arm" not in m["headline"]
    assert set(m["arms"]) == {"lab"}


def test_the_labs_cost_excludes_the_arms(tmp_path):
    adapter, run_dir, lab_runs = full_run(tmp_path, cost=0.5)
    run.grade_run(run_dir, adapter)
    m = measures.measure(run_dir)
    overall = m["cost"]["overall"]
    assert overall["lab"]["runs"] == lab_runs and overall["lab"]["dollars"] == pytest.approx(0.5 * lab_runs)
    assert overall["baseline"]["runs"] == 2
    assert overall["all"]["runs"] == lab_runs + 2 + 4 + 3        # self arm: solo, recheck, challenge, revise
    a = m["arms"]
    assert a["lab"]["runs"] == lab_runs and a["single"]["runs"] == 2
    assert a["self"]["runs"] == 4 and a["self"]["dollars"] == pytest.approx(2.0)
    assert a["vote"]["runs"] == 3 and a["vote"]["dollars_per_study"] == pytest.approx(1.5)
    assert (a["self"]["passed"]["n"], a["self"]["passed"]["of"], a["vote"]["passed"]["n"]) == (1, 1, 1)
    assert a["vote"]["attempts_passed"]["n"] == 3
    assert m["headline"]["self_challenge"]["n"] == 1 and m["headline"]["vote_arm"]["n"] == 1
    assert not [x for x in m["missing"] if "role" in x]
    text = "\n".join(measures.summary_lines(m))
    assert "self-challenge" in text.lower() and "vote" in text.lower()


# ----- --match-cost -----

def write_finishes(folder: pathlib.Path, rows) -> pathlib.Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"kind": "agent.finish", "role": role, "study": study, "phase": phase, "cost_usd": cost})
             for role, study, phase, cost in rows]
    (folder / "events.jsonl").write_text("\n".join(lines) + "\n")
    return folder


def test_match_cost_k_is_lab_dollars_per_study_over_dollars_per_attempt(tmp_path):
    rows = [("analyst", "s1", "analysis", 2.0), ("director", "s1", "relay", 1.0), ("analyst", "s2", "analysis", 3.0),
            ("referee", "s2", "referee", 0.5), ("recheck", "s2", "recheck", 0.5),
            ("baseline", "s1", "attempt-1", 0.6), ("baseline", "s1", "attempt-1", 0.4),     # a retry: one attempt
            ("baseline", "s2", "attempt-1", 1.0),
            ("vote-arm", "s1", "vote-1", 50.0), ("self-solo", "s1", "self-solo", 50.0)]   # arms never count
    k, detail = arms.match_cost_k(write_finishes(tmp_path / "a", rows))
    assert (k, detail["lab_per_study"], detail["per_attempt"], detail["ratio"]) == (4, 3.5, 1.0, 3.5)
    six = [("analyst", "s1", "analysis", 6.0), ("baseline", "s1", "attempt-1", 1.0)]
    assert arms.match_cost_k(write_finishes(tmp_path / "b", six))[0] == 6
    low = [("analyst", "s1", "analysis", 1.0), ("baseline", "s1", "attempt-1", 1.0)]
    assert arms.match_cost_k(write_finishes(tmp_path / "c", low))[0] == arms.MIN_VOTE_K == 3


@pytest.mark.parametrize("rows, message", [
    ([("analyst", "s1", "analysis", 1.0)], "needs both lab runs and single attempts"),
    ([("baseline", "s1", "attempt-1", 1.0)], "needs both lab runs and single attempts"),
    ([("analyst", "s1", "analysis", None), ("baseline", "s1", "attempt-1", 1.0)], "have no dollar figure"),
    ([("analyst", "s1", "analysis", 1.0), ("baseline", "s1", "attempt-1", 0.0)], "cost nothing"),
])
def test_match_cost_k_refuses_without_the_figures(tmp_path, rows, message):
    with pytest.raises(ValueError, match=message):
        arms.match_cost_k(write_finishes(tmp_path, rows))
    with pytest.raises(ValueError, match="no events.jsonl"):
        arms.match_cost_k(tmp_path / "missing")


# ----- the arm command -----

@pytest.fixture
def arm_cli(tmp_path, monkeypatch):
    adapter = LabelAdapter(tmp_path / "bench")
    monkeypatch.setitem(run.ADAPTERS, "fake-labels", lambda: adapter)
    return adapter


def test_the_arm_command_with_match_cost_notes_k(tmp_path, arm_cli, capsys):
    lab_run = write_finishes(tmp_path / "lab-run", [("analyst", "s1", "analysis", 5.0),
                                                    ("baseline", "s1", "attempt-1", 1.0)])
    run_dir = tmp_path / "arm-run"
    assert run.main(["arm", "--arm", "vote", "--benchmark", "fake-labels", "--backend", "mock",
                     "--match-cost", str(lab_run), "--run-dir", str(run_dir)]) == 0
    printed = capsys.readouterr().out
    assert "k = 5" in printed and "s1: attempt-1 ok" in printed
    note = json.loads((run_dir / "run.json").read_text())
    assert note["vote_arm_attempts"] == 5 and note["vote_arm_match_cost"]["k"] == 5
    assert note["vote_arm_match_cost"]["from"] == str(lab_run)
    assert len(list((run_dir / "arms" / "vote" / "s1").glob("attempt-*"))) == 5


def test_the_arm_command_checks_its_flags(tmp_path, arm_cli):
    base = ["arm", "--benchmark", "fake-labels", "--backend", "mock", "--run-dir", str(tmp_path / "r")]
    for extra in (["--arm", "vote"], ["--arm", "vote", "--attempts", "3", "--match-cost", "x"],
                  ["--arm", "self", "--attempts", "3"]):
        with pytest.raises(SystemExit):
            run.main(base + extra)
    with pytest.raises(SystemExit, match="needs --max-usd"):
        run.main(["arm", "--arm", "self", "--benchmark", "fake-labels", "--backend", "claude",
                  "--run-dir", str(tmp_path / "r")])


def test_the_self_arm_command_with_the_v4_preset(tmp_path, arm_cli, capsys):
    run_dir = tmp_path / "arm-run"
    assert run.main(["arm", "--arm", "self", "--benchmark", "fake-labels", "--backend", "mock", "--preset", "v4",
                     "--run-dir", str(run_dir)]) == 0
    assert "s1: kept, final answer from the revise round" in capsys.readouterr().out
    note = json.loads((run_dir / "run.json").read_text())
    assert note["self_arm_switches"] == {"recheck": True, "guard": True}


# ----- the golden briefs -----

FAKE = Study(id="s1", title="s1", statement=STATEMENT, data_dir=pathlib.Path("<DATA>"), deliverables=["answer.json"])


def golden(text: str) -> str:
    return text.replace(str(protocol.TOOLS), "<TOOLS>")


@pytest.mark.parametrize("name, render", [
    ("director_challenge_single.md", lambda: arms.self_challenge_brief(FAKE, pathlib.Path("<TEAMS>"))),
    ("self_challenge_brief.md", lambda: arms.self_revise_brief(FAKE, 1800)),
    ("vote_arm_brief.md", lambda: arms.vote_brief(FAKE, 1, 1800)),
])
def test_the_arm_briefs_match_their_golden_files(name, render):
    assert golden(render()) == (GOLDEN / name).read_text()


def test_the_vote_brief_is_the_single_agent_brief_plus_one_hint():
    from lab.baseline import baseline_brief
    base, voted = baseline_brief(FAKE, 1800), arms.vote_brief(FAKE, 2, 1800)
    hint = f"\nOne way to approach it: {arms.VOTE_HINTS[1]}\n"
    assert hint in voted and voted.replace(hint, "", 1) == base


def test_the_self_revise_brief_with_the_guard_adds_only_the_evidence_note():
    off, on = arms.self_revise_brief(FAKE, 1800), arms.self_revise_brief(FAKE, 1800, protocol.EVIDENCE_NOTE)
    assert on.replace(protocol.EVIDENCE_NOTE, "", 1) == off and "evidence.json" not in off


# ----- the v4 preset leaves the first briefs as they were -----

def test_the_v4_preset_leaves_the_first_analyst_brief_and_the_single_agent_brief_unchanged(tmp_path):
    brief, _ = lab_briefs(tmp_path / "old", REPRODUCE + ["--preset", "v4"])
    assert brief("gb01-team-a") == (GOLDEN / "genebench_analyst_brief.md").read_text()
    assert brief("baseline-gb01-1") == (GOLDEN / "genebench_baseline_brief.md").read_text()
    brief, _ = lab_briefs(tmp_path / "v3", ["--preset", "v4"])
    assert brief("gb01-team-a") == (GOLDEN / "genebench_analyst_brief_v3_team_a.md").read_text()
    assert brief("gb01-team-b") == (GOLDEN / "genebench_analyst_brief_v3_team_b.md").read_text()
    args = run.build_parser().parse_args(["lab", "--benchmark", "genebench", "--run-dir", "x", "--preset", "v4"])
    config = run.make_config(args)
    assert (config.recheck, config.guard) == (True, True)
    default = run.make_config(run.build_parser().parse_args(["lab", "--benchmark", "genebench", "--run-dir", "x"]))
    assert (default.recheck, default.guard) == (False, False)
