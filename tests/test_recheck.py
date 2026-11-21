"""Tests for the "Not verifiable" re-check (Config.recheck) and lab.inventory.

Offline: a fake adapter with BioDSA-style labels ("True", "False", "Not verifiable") and local
cBioPortal-style tables, LocalMemory, the real Gateway with Exa stubbed out, and a scripted
backend that writes the files a real agent would (answers, code, test_result.json, evidence).
The helpers here are shared with tests/test_guard.py and tests/test_arms.py.
"""

import json
import os
import pathlib
import time

import pytest

from lab import inventory, protocol, replay, run
from lab.contracts import AgentResult, Study
from lab.eventlog import JsonlEventLog
from lab.memory_local import LocalMemory

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
LABELS = ("True", "False", "Not verifiable")
NV = "Not verifiable"
HYPOTHESIS = "Current or former smokers have a lower FEV1 percent than never smokers."
STATEMENT = f"""# Hypothesis

{HYPOTHESIS}

# Your task

Decide whether the data support the hypothesis ("True"), contradict it ("False") or cannot settle it
("Not verifiable"). Write `answer.json`: {{"answer": {{"label": "<label>"}}, "reasoning": "..."}}
"""
PATIENT_TABLE = (
    "#Patient Identifier\tDiagnosis Age\tSmoking History\n"
    "#Identifier to uniquely specify a patient.\tAge at diagnosis in years.\t"
    "Smoking history: 1 = current or former smoker, 0 = never smoker.\n"
    "#STRING\tNUMBER\tNUMBER\n"
    "#1\t1\t1\n"
    "PATIENT_ID\tAGE\tSMOKING_HISTORY\n"
    "P1\t61\t1\n"
    "P2\t55\t0\n")
SUPPLEMENT_TABLE = "PATIENT_ID\tPACK_YEARS\tFEV1_PERCENT\nP1\t30\t71.5\nP2\t0\t92.0\n"
LABS_CSV = "patient_id,albumin\nP1,4.0\n"
META = "cancer_study_identifier: fake\nname: Fake study\n"
GOOD_TEST = {"test": "Welch t-test", "variables": ["data_clinical_supplement.txt: FEV1_PERCENT"],
             "statistic": -3.1, "p_value": 0.003, "n": 140, "direction": "supports"}
FAST = dict(max_agents=3, library=False, team_search=False, research=False, challenge=False, seed=1)


def outcome(given: str, expected: str) -> str:
    """BioDSA's outcome kinds (lab/adapters/biodsa.py outcome_kind), restated so these tests stand alone."""
    if given == expected:
        return "correct"
    if expected == NV:
        return "judged an unverifiable claim settled"
    if given == NV:
        return "judged a settled claim not verifiable"
    return "accepted a false claim" if given == "True" else "rejected a true claim"


def write_tables(data: pathlib.Path) -> pathlib.Path:
    data.mkdir(parents=True, exist_ok=True)
    (data / "data_clinical_patient.txt").write_text(PATIENT_TABLE)
    (data / "data_clinical_supplement.txt").write_text(SUPPLEMENT_TABLE)
    (data / "labs.csv").write_text(LABS_CSV)
    (data / "meta_study.txt").write_text(META)
    return data


class LabelAdapter:
    """One study with BioDSA's answer format. `name` is "planted", so the re-check's label rule applies
    without an abstains() method. `local=False` points the study at a mount that does not exist here."""

    name = "planted"

    def __init__(self, root: pathlib.Path, truth: str = "True", local: bool = True):
        self.root, self.truth, self.local = root, truth, local
        write_tables(root / "s1" / "data")

    def studies(self):
        data = self.root / "s1" / "data" if self.local else pathlib.Path("/no-such-mount/s1")
        return [Study(id="s1", title="s1", statement=STATEMENT, data_dir=data, deliverables=["answer.json"])]

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
        label = self._label(pathlib.Path(final_dir) / "answer.json")
        if label not in LABELS:
            return {"passed": False, "score": 0.0, "error": "no valid answer"}
        return {"passed": label == self.truth, "score": float(label == self.truth), "kind": outcome(label, self.truth)}


class HookAdapter(LabelAdapter):
    """An adapter with its own abstains(): here "False" counts as abstaining and "Not verifiable" does not."""

    name = "hooked"

    def abstains(self, study, values):
        return values.get("label") == "False"


# ----- evidence for the damage guard -----

def write_evidence(ws: pathlib.Path, kind: str = "good") -> None:
    """good: a script that computes its numbers, its later output and evidence quoting it. wrong-number:
    evidence quotes a number the output does not hold. stale: the output was written an hour ago.
    none: nothing."""
    if kind == "none":
        return
    (ws / "check.py").write_text("print('statistic %.2f' % (-31 / 10)); print('p %.4f' % (31 / 10000)); "
                                 "print('n %d' % (14 * 10))\n")
    (ws / "check_output.txt").write_text("statistic -3.10\np 0.0031\nn 140\n")
    quoted = "p = 0.0042, n = 140" if kind == "wrong-number" else "t = -3.1, p = 0.0031 (n = 140)"
    (ws / "evidence.json").write_text(json.dumps({"field": "label", "script": "check.py",
                                                  "output_file": "check_output.txt", "result": quoted}))
    if kind == "stale":
        old = time.time() - 3600
        os.utime(ws / "check.py", (old - 60, old - 60))
        os.utime(ws / "check_output.txt", (old, old))


# ----- scripted backend -----

JOBS = {
    "# Your job: the lab's library": "library",
    "# Your job: director, briefing the teams": "relay",
    "# Your job: director, after the teams disagreed": "crossexam",
    "# Your job: director, challenging an answer both teams agree on": "challenge",
    "# Your job: director, challenging one analyst's answer": "self-challenge",
    "# Your job: re-check a Not verifiable answer": "recheck",
    "# Your job: analyst on a blind team": "analyst",
    "# Your job: analyst, answering the director's questions": "revise",
    "# Your job: referee": "referee",
}


def step_of(step) -> dict:
    return step if isinstance(step, dict) else {"label": step}


def act(ws: pathlib.Path, step, who: str) -> dict:
    """Do what one scripted answer says: write answer.json (no label: an invalid answer; {"keep": True}:
    leave whatever answer.json the folder holds), and any code, test_result.json and evidence it
    names. Returns the run's result.json."""
    step = step_of(step)
    label = step.get("label")
    doc = {"answer": {"label": label}, "reasoning": step.get("reasoning", "looked at the tables")} if label \
        else {"answer": {"verdict": "unsure"}}
    if not step.get("keep"):
        (ws / "answer.json").write_text(json.dumps(doc))
    if step.get("code"):
        (ws / "analysis.py").write_text(step["code"])
    if step.get("test") is not None:
        (ws / "test_result.json").write_text(json.dumps(step["test"]))
    write_evidence(ws, step.get("evidence", "none"))
    return {"answer_file": "answer.json", "method": step.get("method", f"method used by {who}"),
            "warning_signs": [], "confidence": 0.5}


class LabelBackend:
    """Plays every agent from a plan. Keys:
      team-a / team-b / team-c   answers in order, one per analyst, re-check or revise run: a label,
                                 None (an invalid answer) or {"label", "code", "test", "evidence", "method"}
      questions / challenge      [{"to": "team-a" | "team-b", "question": ...}]
      referee                    {"pick_team": team} or {"pick": "neither", "own_answer": {...}, "evidence": kind}
    `cost` is every run's cost_usd."""

    def __init__(self, plan, cost=None):
        self.plan, self.cost = plan, cost
        self.calls = []

    @staticmethod
    def job(brief):
        return next((name for heading, name in JOBS.items() if heading in brief), "baseline")

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800):
        ws = pathlib.Path(workspace)
        job, study = self.job(brief), ws.parent.name
        self.calls.append({"agent": agent.id, "job": job, "brief": brief, "workspace": ws})
        suffix = agent.id[len(study) + 1:] if agent.id.startswith(study + "-") else agent.id
        if job in ("analyst", "revise", "recheck"):
            done = sum(1 for c in self.calls[:-1] if c["agent"] == agent.id and c["job"] in ("analyst", "revise", "recheck"))
            steps = self.plan[suffix]
            result = act(ws, steps[min(done, len(steps) - 1)], agent.id)
        elif job == "relay":
            result = {"pass_down": []}
        elif job == "library":
            result = {"pitfalls": [], "searches_made": 0}
        elif job in ("crossexam", "challenge"):
            result = {"questions": [{"to": f"{study}-{q['to']}", "from": [], "question": q["question"]}
                                    for q in self.plan.get("questions" if job == "crossexam" else "challenge", [])],
                      "verdict": "scripted"}
        elif job == "referee":
            spec = self.plan["referee"]
            if "pick_team" in spec:
                wanted = step_of(self.plan[spec["pick_team"]][-1])["label"]
                pick = next(x for x in ("X", "Y") if json.loads(
                    (ws / "candidates" / x / "answer.json").read_text())["answer"]["label"] == wanted)
                result = {"pick": pick, "reason": "the test matched", "own_answer": None}
            else:
                write_evidence(ws, spec.get("evidence", "none"))
                result = {"pick": "neither", "reason": "both wrong", "own_answer": spec.get("own_answer")}
        else:
            raise AssertionError(f"unexpected job for {agent.id}")
        (ws / "result.json").write_text(json.dumps(result))
        return AgentResult(ok=True, result=result, seconds=0.01, cost_usd=self.cost)


def no_exa(path, body):
    raise AssertionError("no Exa call expected in these tests")


def make(tmp_path, plan, adapter=None, cost=None, **config):
    adapter = adapter or LabelAdapter(tmp_path / "bench")
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    backend = LabelBackend(plan, cost=cost)
    cfg = protocol.Config(**{**FAST, "recheck": True, **config})
    lab = protocol.Lab(adapter, backend, LocalMemory(log, run_dir / "memory.jsonl"), log, run_dir, cfg, exa_call=no_exa)
    return lab, adapter, backend, log, run_dir


def record(run_dir, sid="s1"):
    return json.loads((run_dir / sid / "study.json").read_text())


def jobs(backend):
    return [(c["agent"], c["job"]) for c in backend.calls]


def assert_replay_matches(run_dir, sid="s1"):
    rebuilt = replay.decide(replay.read_events(run_dir), sid)
    assert rebuilt == replay.recorded(run_dir, sid), rebuilt
    return rebuilt


ACCEPTED = {"label": "True", "test": GOOD_TEST, "code": "fev = df['FEV1_PERCENT']\n"}


# ----- the inventory -----

def test_hypothesis_text_is_the_text_under_its_heading():
    assert inventory.hypothesis_text(STATEMENT) == HYPOTHESIS
    assert inventory.hypothesis_text("no heading here") == "no heading here"


def test_inventory_finds_unused_columns_including_in_a_comment_header_table(tmp_path):
    data = write_tables(tmp_path / "data")
    ws = tmp_path / "team-a"
    ws.mkdir()
    (ws / "analysis.py").write_text("df = pd.read_csv('data_clinical_patient.txt', sep='\\t', comment='#')\n"
                                    "df.groupby('SMOKING_HISTORY')\n")
    (ws / "result.json").write_text(json.dumps({"method": "compared groups after checking age"}))
    (ws / "answer.json").write_text(json.dumps({"answer": {"label": NV}, "reasoning": "no lung function column"}))
    inv = inventory.build_inventory(data, ws, HYPOTHESIS)
    files = {f["file"]: f for f in inv["files"]}
    # the header is the first line that is not a "#" comment
    assert files["data_clinical_patient.txt"]["columns"] == ["PATIENT_ID", "AGE", "SMOKING_HISTORY"]
    assert files["labs.csv"]["columns"] == ["patient_id", "albumin"]
    assert files["meta_study.txt"]["columns"] == []
    # used: named in the code (SMOKING_HISTORY) or in the method text, any case (AGE in "age")
    assert set(files["data_clinical_patient.txt"]["used_columns"]) == {"AGE", "SMOKING_HISTORY"}
    assert inv["unused_files"] == ["data_clinical_supplement.txt", "labs.csv", "meta_study.txt"]
    unused = [(c["file"], c["column"]) for c in inv["unused_columns"]]
    assert unused[0] == ("data_clinical_supplement.txt", "FEV1_PERCENT")      # the most words shared with the claim
    assert ("data_clinical_patient.txt", "SMOKING_HISTORY") not in unused
    assert inv["unused_columns_total"] == len(unused) == 6
    # the display name and description come from the "#" lines
    patient_id = next(c for c in inv["unused_columns"] if c["column"] == "PATIENT_ID" and c["file"].startswith("data_clinical_p"))
    assert patient_id["display"] == "Patient Identifier"
    text = inventory.render_inventory(inv)
    assert "`data_clinical_supplement.txt` `FEV1_PERCENT`" in text and "never names this file" in text


def test_inventory_records_no_absolute_local_path(tmp_path):
    data = write_tables(tmp_path / "s1" / "data")
    inv = inventory.build_inventory(data, tmp_path / "ws", HYPOTHESIS)
    assert inv["data_dir"] == "data" and str(tmp_path) not in json.dumps(inv)
    under_root = inventory.DATA_ROOT / "planted" / "public" / "p1" / "data"
    assert inventory.data_label(under_root) == "planted/public/p1/data"


def test_inventory_keeps_the_top_25_unused_columns(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "wide.txt").write_text("\t".join(f"C{i}" for i in range(40)) + "\tFEV1_PERCENT\n")
    inv = inventory.build_inventory(data, tmp_path / "nothing", HYPOTHESIS)
    assert inv["unused_columns_total"] == 41 and len(inv["unused_columns"]) == inventory.TOP_COLUMNS
    assert inv["unused_columns"][0]["column"] == "FEV1_PERCENT"


# ----- when the re-check runs -----

def test_the_recheck_runs_only_for_a_team_that_abstains(tmp_path):
    lab, adapter, backend, log, run_dir = make(tmp_path, {"team-a": [NV, ACCEPTED], "team-b": ["True"]})
    lab.run(adapter.studies())
    assert [(a, j) for a, j in jobs(backend) if j == "recheck"] == [("s1-team-a", "recheck")]
    assert [e["team"] for e in log.events("recheck.start")] == ["team-a"]


def test_no_recheck_when_the_flag_is_off_or_no_team_abstains(tmp_path):
    lab, adapter, backend, log, _ = make(tmp_path / "off", {"team-a": [NV], "team-b": [NV]}, recheck=False)
    lab.run(adapter.studies())
    assert "recheck" not in [j for _, j in jobs(backend)] and not log.events("recheck.start")
    lab, adapter, backend, log, _ = make(tmp_path / "settled", {"team-a": ["True"], "team-b": ["False"],
                                                                "referee": {"pick_team": "team-a"}}, questions=False)
    lab.run(adapter.studies())
    assert "recheck" not in [j for _, j in jobs(backend)]


def test_the_adapters_own_abstains_decides_and_other_adapters_never_recheck(tmp_path):
    hooked = HookAdapter(tmp_path / "hooked")
    lab, adapter, backend, log, _ = make(tmp_path / "h", {"team-a": [NV], "team-b": ["False", ACCEPTED],
                                                          "referee": {"pick_team": "team-a"}}, adapter=hooked,
                                         questions=False)
    lab.run(adapter.studies())
    assert [(a, j) for a, j in jobs(backend) if j == "recheck"] == [("s1-team-b", "recheck")]

    class Other(LabelAdapter):
        name = "genebench"
    lab, adapter, backend, log, _ = make(tmp_path / "o", {"team-a": [NV], "team-b": [NV]}, adapter=Other(tmp_path / "ob"))
    lab.run(adapter.studies())
    assert "recheck" not in [j for _, j in jobs(backend)]


def test_without_a_local_copy_of_the_data_the_recheck_is_skipped(tmp_path):
    lab, adapter, backend, log, run_dir = make(tmp_path, {"team-a": [NV], "team-b": [NV]},
                                               adapter=LabelAdapter(tmp_path / "bench", local=False))
    lab.run(adapter.studies())
    assert "recheck" not in [j for _, j in jobs(backend)]
    assert sorted(e["team"] for e in log.events("recheck.skipped")) == ["team-a", "team-b"]   # two threads
    assert all(r["skipped"] for r in record(run_dir)["recheck"])
    assert_replay_matches(run_dir)


def test_local_data_dir_prefers_the_adapters_method(tmp_path):
    adapter = LabelAdapter(tmp_path / "bench", local=False)
    study = adapter.studies()[0]
    assert protocol.local_data_dir(adapter, study) is None
    adapter.local_data_dir = lambda s: tmp_path / "bench" / "s1" / "data"
    assert protocol.local_data_dir(adapter, study) == tmp_path / "bench" / "s1" / "data"


# ----- the code's verdict -----

@pytest.mark.parametrize("step, accepted, reason", [
    ({"label": "True"}, False, "no readable test_result.json"),
    ({"label": "True", "test": {**GOOD_TEST, "p_value": None}}, False, "lacks p_value"),
    ({"label": "True", "test": {k: v for k, v in GOOD_TEST.items() if k != "direction"}}, False, "lacks direction"),
    ({"label": "True", "test": {**GOOD_TEST, "p_value": 0.2}}, False, "over 0.05"),
    ({"label": "True", "test": {**GOOD_TEST, "n": 12}}, False, "under 20"),
    ({"label": "True", "test": {**GOOD_TEST, "direction": "contradicts"}}, False, "not one that supports"),
    ({"label": "False", "test": GOOD_TEST}, False, "not one that contradicts"),
    ({"label": NV, "test": GOOD_TEST}, False, "still 'Not verifiable'"),
    ({"label": "True", "test": GOOD_TEST}, True, "True with p_value"),
    ({"label": "False", "test": {**GOOD_TEST, "p_value": 0.6, "direction": "contradicts"}}, True, "contradicts"),
    # p_value: a finite number in [0, 1]; a string parses, and "<x" counts as x
    ({"label": "True", "test": {**GOOD_TEST, "p_value": "<0.001"}}, True, "True with p_value <0.001"),
    ({"label": "True", "test": {**GOOD_TEST, "p_value": "1e-5"}}, True, "True with p_value 1e-5"),
    ({"label": "True", "test": {**GOOD_TEST, "p_value": "<0.2"}}, False, "over 0.05"),
    ({"label": "True", "test": {**GOOD_TEST, "p_value": -3}}, False, "negative"),
    ({"label": "True", "test": {**GOOD_TEST, "p_value": "nan"}}, False, "NaN"),
    ({"label": "True", "test": {**GOOD_TEST, "p_value": "-inf"}}, False, "infinite"),
    ({"label": "False", "test": {**GOOD_TEST, "p_value": 1.5, "direction": "contradicts"}}, False, "over 1"),
    ({"label": "False", "test": {**GOOD_TEST, "p_value": "n/a", "direction": "contradicts"}}, False, "not a number"),
    # n: a finite number at least the minimum; true is not a number
    ({"label": "True", "test": {**GOOD_TEST, "n": "nan"}}, False, "NaN"),
    ({"label": "True", "test": {**GOOD_TEST, "n": "inf"}}, False, "infinite"),
    ({"label": "True", "test": {**GOOD_TEST, "n": True}}, False, "not a number"),
    ({"label": "True", "test": {**GOOD_TEST, "n": "120"}}, True, "True with p_value"),
    # test and variables
    ({"label": "True", "test": {**GOOD_TEST, "variables": []}}, False, "variables is [], not a non-empty list"),
    ({"label": "True", "test": {**GOOD_TEST, "variables": ["  "]}}, False, "non-empty list of non-empty strings"),
    ({"label": "True", "test": {**GOOD_TEST, "variables": "FEV1_PERCENT"}}, False, "non-empty list"),
    ({"label": "True", "test": {**GOOD_TEST, "test": {}}}, False, "not a non-empty string"),
    ({"label": "True", "test": {**GOOD_TEST, "test": "   "}}, False, "not a non-empty string"),
    # direction: exactly "supports" or "contradicts", any case, no prefixes
    ({"label": "True", "test": {**GOOD_TEST, "direction": "support is lacking"}}, False, 'not "supports" or'),
    ({"label": "True", "test": {**GOOD_TEST, "direction": "supportive"}}, False, 'not "supports" or'),
    ({"label": "True", "test": {**GOOD_TEST, "direction": " Supports "}}, True, "True with p_value"),
    ({"label": "False", "test": {**GOOD_TEST, "p_value": 0.9, "direction": "CONTRADICTS"}}, True, "contradicts"),
])
def test_the_verdict_rules(tmp_path, step, accepted, reason):
    act(tmp_path, step, "s1-team-a")
    adapter = LabelAdapter(tmp_path / "bench")
    path = tmp_path / "answer.json"
    cand = protocol.Candidate("team-a", "s1-team-a", "recheck", path, adapter.problems(None, "answer.json", path),
                              protocol.answer_values(path))
    ok, why, _ = protocol.recheck_verdict(cand, tmp_path, time.time() - 1, protocol.Config())
    assert ok is accepted and reason in why


def test_a_symlinked_test_result_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test_result.json").write_text(json.dumps(GOOD_TEST))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "test_result.json").symlink_to(outside / "test_result.json")
    cand = protocol.Candidate("team-a", "s1-team-a", "recheck", ws / "answer.json", [], {"label": "True"})
    ok, why, test = protocol.recheck_verdict(cand, ws, time.time() - 1, protocol.Config())
    assert not ok and "symbolic link" in why and test is None


def test_a_test_result_from_before_the_round_does_not_count(tmp_path):
    act(tmp_path, {"label": "True", "test": GOOD_TEST}, "s1-team-a")
    old = time.time() - 3600
    os.utime(tmp_path / "test_result.json", (old, old))
    cand = protocol.Candidate("team-a", "s1-team-a", "recheck", tmp_path / "answer.json", [], {"label": "True"})
    ok, why, _ = protocol.recheck_verdict(cand, tmp_path, time.time(), protocol.Config())
    assert not ok and "before the re-check began" in why


def test_without_a_valid_test_result_the_abstention_stands(tmp_path):
    plan = {"team-a": [NV, {"label": "True"}], "team-b": [NV]}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan)
    out = lab.run(adapter.studies())
    assert out["s1"]["status"] == "replicated"
    rec = record(run_dir)
    assert rec["final"]["round"] == "analysis"
    verdicts = {e["team"]: e for e in log.events("recheck.verdict")}
    assert verdicts["team-a"]["accepted"] is False and verdicts["team-a"]["after"] == "True"
    assert "no readable test_result.json" in verdicts["team-a"]["reason"]
    cand = next(c for c in rec["candidates"] if c["round"] == "recheck" and c["team"] == "team-a")
    assert cand["valid"] and "kept the earlier answer" in cand["not_used"]      # kept, and graded later
    assert any(n["round"] == "recheck" and "kept the earlier answer" in n["reason"] for n in rec["not_chosen"])
    assert_replay_matches(run_dir)


# A re-check the lab rejects; then team A revises by keeping whatever answer its folder holds.
KEEP_PLAN = {"team-a": [NV, {"label": "True"}, {"keep": True}], "team-b": ["False", "False"],
             "questions": [{"to": "team-a", "question": "QUESTION FROM THE DIRECTOR: check the rows?"}],
             "referee": {"pick_team": "team-b"}}


@pytest.mark.parametrize("guard", [False, True])
def test_a_rejected_recheck_puts_the_kept_answer_back_in_the_folder(tmp_path, guard):
    lab, adapter, backend, log, run_dir = make(tmp_path, KEEP_PLAN, guard=guard)
    out = lab.run(adapter.studies())
    rec = record(run_dir)
    assert [r["accepted"] for r in rec["recheck"]] == [False] and rec["recheck"][0]["in_use"] == "team-a-analysis"
    revise = next(c for c in rec["candidates"] if c["team"] == "team-a" and c["round"] == "revise")
    assert revise["answer"] == {"label": NV} and not revise.get("not_used")     # the kept label, not the rejected one
    assert not log.events("guard.blocked")      # keeping the kept answer is no change
    # the revise brief's folder held the analysis-round answer and note, not the re-check's
    ws = run_dir / "s1" / "team-a"
    assert json.loads((run_dir / "s1" / "candidates" / "team-a-revise.json").read_text())["answer"]["label"] == NV
    revise_call = next(c for c in backend.calls if c["job"] == "revise" and c["agent"] == "s1-team-a")
    assert revise_call["workspace"] == ws
    assert out["s1"]["status"] == "refereed" and rec["final"]["team"] == "team-b"
    assert_replay_matches(run_dir)


def test_the_kept_note_replaces_the_rechecks_result_json(tmp_path):
    plan = {"team-a": [{"label": NV, "method": "FIRST-NOTE"}, {"label": "True", "method": "RECHECK-NOTE"}],
            "team-b": ["False"], "referee": {"pick_team": "team-b"}}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan, questions=False)
    lab.run(adapter.studies())
    ws = run_dir / "s1" / "team-a"
    assert json.loads((ws / "answer.json").read_text())["answer"]["label"] == NV
    assert json.loads((ws / "result.json").read_text())["method"] == "FIRST-NOTE"
    assert json.loads((run_dir / "s1" / "candidates" / "team-a-recheck-result.json").read_text())["method"] == \
        "RECHECK-NOTE"      # the re-check's own note is still kept by the lab


def test_an_accepted_recheck_updates_the_answer_and_emits_events(tmp_path):
    plan = {"team-a": [NV, ACCEPTED], "team-b": ["True"]}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan, cost=0.5)
    out = lab.run(adapter.studies())
    assert out["s1"]["status"] == "replicated"
    rec = record(run_dir)
    assert rec["final"] == {**rec["final"], "team": "team-a", "round": "recheck"}
    start = log.events("recheck.start")[0]
    assert start["team"] == "team-a" and start["before"] == NV and start["unused_files"] >= 1
    verdict = log.events("recheck.verdict")[0]
    assert {k: verdict[k] for k in ("team", "before", "after", "accepted")} == \
        {"team": "team-a", "before": NV, "after": "True", "accepted": True}
    assert rec["recheck"][0]["test"] == GOOD_TEST and rec["switches"]["recheck"] is True
    assert (run_dir / "s1" / "candidates" / "team-a-recheck-test_result.json").is_file()
    assert (run_dir / "s1" / "team-a" / inventory.INVENTORY_FILE).is_file()
    assert rec["recheck"][0]["in_use"] == "team-a-recheck"
    finishes = [e for e in log.events("agent.finish") if e["role"] == "recheck"]
    assert [(e["agent"], e["phase"]) for e in finishes] == [("s1-team-a", "recheck")]
    assert_replay_matches(run_dir)
    results = run.grade_run(run_dir, adapter)
    ids = {r["id"]: r for r in results["studies"]["s1"]["candidates"]}
    assert ids["team-a-analysis"]["outcome_kind"] == "judged a settled claim not verifiable"
    assert ids["team-a-recheck"]["passed"] is True and ids["final"]["passed"] is True
    from lab import measures
    m = measures.measure(run_dir)
    assert m["steps"]["recheck"]["rescued"] == 1 and m["steps"]["recheck"]["damaged"] == 0
    assert m["recheck"]["before"] == 1 and m["recheck"]["after"] == 0 and m["recheck"]["rechecks"]["accepted"] == 1
    assert m["cost"]["by_role"]["recheck"]["dollars"] == 0.5
    assert m["cost"]["overall"]["lab"]["runs"] == 3    # two analysts and the re-check: all lab cost


def test_the_recheck_is_blind_to_the_other_team(tmp_path):
    plan = {"team-a": [{"label": NV, "code": "df.groupby('SMOKING_HISTORY')\n", "method": "SECRET-A-METHOD"},
                       ACCEPTED],
            "team-b": [{"label": "True", "code": "fev = sup['FEV1_PERCENT']; py = sup['PACK_YEARS']\n",
                        "method": "SECRET-B-METHOD"}]}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan)
    lab.run(adapter.studies())
    brief = next(c["brief"] for c in backend.calls if c["job"] == "recheck")
    assert "s1-team-b" not in brief and "SECRET-B-METHOD" not in brief and "team-b" not in brief
    inv = json.loads((run_dir / "s1" / "team-a" / inventory.INVENTORY_FILE).read_text())
    unused = {c["column"] for c in inv["unused_columns"]}
    assert {"FEV1_PERCENT", "PACK_YEARS"} <= unused          # team B's code does not count for team A
    assert "SMOKING_HISTORY" not in unused
    ws = next(c["workspace"] for c in backend.calls if c["job"] == "recheck")
    assert ws == run_dir / "s1" / "team-a"                   # the team's own folder


REPLAY = {
    # name: (plan, Config overrides, status, final (team, round))
    "accepted-replicated": ({"team-a": [NV, ACCEPTED], "team-b": ["True"]}, {}, "replicated", ("team-a", "recheck")),
    # team A's revision keeps whatever its folder holds: the kept "Not verifiable", not the rejected "True"
    "rejected-refereed": ({"team-a": [NV, {"label": "True"}, {"keep": True}], "team-b": ["True", "True"],
                           "questions": [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: check the rows?"}],
                           "referee": {"pick_team": "team-b"}}, {}, "refereed", ("team-b", "revise")),
    "accepted-single-source": ({"team-a": [NV, ACCEPTED], "team-b": [None]}, {}, "single-source", ("team-a", "recheck")),
    "accepted-converged": ({"team-a": [NV, {"label": "False", "test": {**GOOD_TEST, "direction": "contradicts"}},
                                       "True"], "team-b": ["True", "True"],
                            "questions": [{"to": "team-a", "question": "QUESTION FROM THE DIRECTOR: check the rows?"}]},
                           {}, "converged", ("team-a", "revise")),
    "accepted-challenged": ({"team-a": [NV, ACCEPTED, "True"], "team-b": ["True", "True"],
                             "challenge": [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: check the rows?"}]},
                            {"challenge": True}, "replicated", ("team-a", "challenge")),
}


@pytest.mark.parametrize("name", sorted(REPLAY))
def test_replay_rebuilds_every_recheck_path(tmp_path, name):
    plan, overrides, status, final = REPLAY[name]
    lab, adapter, backend, log, run_dir = make(tmp_path, plan, **overrides)
    lab.run(adapter.studies())
    rebuilt = assert_replay_matches(run_dir)
    assert rebuilt["status"] == status and (rebuilt["final"]["team"], rebuilt["final"]["round"]) == final
    assert replay.replay(run_dir, out=lambda line: None) == 0


# ----- symbolic links in a team's folder -----

class LinkingBackend(LabelBackend):
    """Team A's analysis leaves `names` as symbolic links to files outside its folder."""

    def __init__(self, plan, names):
        super().__init__(plan)
        self.names = names

    def run(self, agent, brief, workspace, **kw):
        out = super().run(agent, brief, workspace, **kw)
        ws = pathlib.Path(workspace)
        if agent.id == "s1-team-a" and self.job(brief) == "analyst":
            outside = ws.parents[2] / "outside"
            outside.mkdir(exist_ok=True)
            for name in self.names:
                (outside / name).write_text((ws / name).read_text().replace("method used by", "LEAKED"))
                (ws / name).unlink()
                (ws / name).symlink_to(outside / name)
        return out


def linked_run(tmp_path, names, plan):
    adapter = LabelAdapter(tmp_path / "bench")
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    backend = LinkingBackend(plan, names)
    cfg = protocol.Config(**{**FAST, "challenge": True})
    lab = protocol.Lab(adapter, backend, LocalMemory(log, run_dir / "memory.jsonl"), log, run_dir, cfg, exa_call=no_exa)
    lab.run(adapter.studies())
    return run_dir, record(run_dir)


def test_the_lab_does_not_follow_a_symlinked_answer_file(tmp_path):
    run_dir, rec = linked_run(tmp_path, ["answer.json"], {"team-a": ["True"], "team-b": ["True"]})
    cand = next(c for c in rec["candidates"] if c["team"] == "team-a")
    assert cand["problems"] == ["no answer file"] and rec["status"] == "single-source"


def test_the_lab_does_not_follow_a_symlinked_result_json(tmp_path):
    plan = {"team-a": ["True", "True"], "team-b": ["True", "True"],
            "challenge": [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: check the rows?"}]}
    run_dir, rec = linked_run(tmp_path, ["result.json"], plan)
    assert rec["status"] == "replicated"
    teams = run_dir / "s1" / "director-challenge" / "teams"
    assert (teams / "s1-team-b.json").is_file() and not (teams / "s1-team-a.json").exists()
    assert not (run_dir / "s1" / "candidates" / "team-a-analysis-result.json").exists()
    assert "LEAKED" not in "".join(p.read_text() for p in (run_dir / "s1").rglob("*.json") if not p.is_symlink())


# ----- the brief -----

def recheck_brief(tmp_path) -> str:
    plan = {"team-a": [{"label": NV, "code": "df.groupby('SMOKING_HISTORY')\n"}, ACCEPTED], "team-b": ["True"]}
    lab, adapter, backend, log, run_dir = make(tmp_path, plan)
    lab.run(adapter.studies())
    brief = next(c["brief"] for c in backend.calls if c["job"] == "recheck")
    return brief.replace(str(protocol.TOOLS), "<TOOLS>").replace(str(adapter.root / "s1" / "data"), "<DATA>")


def test_the_recheck_brief_matches_its_golden_file(tmp_path):
    """A fake study in BioDSA's answer format, with the default thresholds and the guard off."""
    assert recheck_brief(tmp_path) == (GOLDEN / "biodsa_recheck_brief.md").read_text()
