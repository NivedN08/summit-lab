"""Checks on the scBench adapter. Skipped when the benchmark has not been downloaded and staged.

No test runs the benchmark's downloaded grader code: `grade` is tested with a stub in its place.
No test prints an answer value; leak checks compare against the sealed side without showing it.
"""

import json

import pytest

from lab.adapters import scbench
from lab.adapters.scbench import ScBench

pytestmark = pytest.mark.skipif(not (scbench.PUBLIC.exists() and scbench.MAPPING.exists()),
                                reason="benchmark not staged")

SAMPLE = {"number": 100.0, "string": "B", "list": ["CD14", "LYZ", "S100A8", "S100A9", "FCN1"],
          "mapping": {"T cells": 50.0, "B cells": 30.0, "Monocytes": 20.0}}


@pytest.fixture
def adapter():
    return ScBench()


@pytest.fixture
def sealed_tasks():
    return {t["id"]: t for t in scbench._load_tasks()}


def write(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(json.dumps(doc))
    return path


def good_answer(schema):
    return {k: SAMPLE[kind] for k, kind in schema.items()}


def study_with(adapter, kind):
    return next(s for s in adapter.studies() if kind in adapter.schema(s).values())


# ----------------------------------------------------------------------------- the public side

def test_every_study_has_task_text_answer_shape_and_data_pointer(adapter):
    studies = adapter.studies()
    assert [s.id for s in studies] == [f"sc{i:02d}" for i in range(1, len(studies) + 1)]
    assert len(studies) == 36
    for s in studies:
        assert "<EVAL_ANSWER>" in s.statement and "eval_answer.json" in s.statement
        assert s.deliverables == ["eval_answer.json"]
        schema = adapter.schema(s)
        assert schema and set(schema.values()) <= {"number", "string", "list", "mapping"}
        manifest = adapter.data_manifest(s)
        assert manifest["files"] and manifest["volume_sub_path"] == f"/{s.id}"
        for f in manifest["files"]:
            assert f["name"] and f["size_bytes"] > 0 and f["url"].startswith(("https://", "latch://"))
            assert f["volume_path"] == f"/{s.id}/{f['name']}"


def test_public_side_holds_only_the_three_files_per_task(adapter):
    for path in scbench.PUBLIC.rglob("*"):
        if path.is_file():
            assert path.name in ("task.md", "answer_schema.json", "data_manifest.json"), path
            assert path.parent.parent == scbench.PUBLIC


def test_public_side_holds_no_answers_tolerances_grader_files_or_real_ids(sealed_tasks):
    real_ids = set(sealed_tasks)
    canaries = {str(t["record"].get("canary")) for t in sealed_tasks.values()}
    grader_words = ("ground_truth", "tolerance", "correct_answer", "canonical_markers", "ground_truth_labels",
                    "grader", "canary", "notes", "scoring")
    for path in scbench.PUBLIC.rglob("*"):
        assert not any(i in str(path) for i in real_ids)
        if not path.is_file():
            continue
        text = path.read_text()
        assert not any(i in text for i in real_ids), path
        assert not any(c in text for c in canaries), path
        if path.suffix == ".json":
            assert not any(w in text for w in grader_words), path


def test_answer_schema_has_names_and_kinds_only_and_task_text_is_the_harness_prompt(adapter, sealed_tasks):
    mapping = json.loads(scbench.MAPPING.read_text())
    hf = scbench._inspect_data_manifest()
    for s in adapter.studies():
        record = sealed_tasks[mapping[s.id]["id"]]["record"]
        expected = scbench._current_prompt(record) if mapping[s.id]["set"] == "current" \
            else scbench._canonical_prompt(record, hf)
        assert s.statement == expected          # the prompt and nothing else
        assert record["task"].strip() in s.statement
        for value in adapter.schema(s).values():
            assert isinstance(value, str)       # a kind name, never a value


def test_answer_values_from_the_sealed_side_do_not_appear_in_public_json(adapter, sealed_tasks):
    """Every distinctive answer value (numbers with a fractional part, gene and label lists) stays sealed."""
    mapping = json.loads(scbench.MAPPING.read_text())
    for s in adapter.studies():
        config = (sealed_tasks[mapping[s.id]["id"]]["record"]["grader"].get("config") or {})
        public_json = "".join((scbench.PUBLIC / s.id / n).read_text() for n in ("answer_schema.json", "data_manifest.json"))
        values = []
        for v in (config.get("ground_truth") or {}).values():
            if isinstance(v, float) and v != int(v):
                values.append(repr(v))
        for key in ("ground_truth_labels", "canonical_markers"):
            if isinstance(config.get(key), list):
                values += [json.dumps(config[key])]
        assert not any(v in public_json for v in values), s.id


def test_neutral_mapping_is_sealed_and_covers_both_sets():
    mapping = json.loads(scbench.MAPPING.read_text())
    assert [m["set"] for m in mapping.values()].count("current") == 6
    assert [m["set"] for m in mapping.values()].count("canonical") == 30
    assert scbench.SEALED in scbench.MAPPING.parents


def test_blocked_terms_name_the_benchmark_and_its_task_ids(adapter, sealed_tasks):
    terms = adapter.blocked_terms()
    assert "scbench" in terms
    assert all(i.lower() in terms for i in sealed_tasks)
    assert all(t == t.lower() for t in terms)


# ----------------------------------------------------------------------------- the format check

def test_format_check_accepts_a_well_formed_answer_and_names_what_is_wrong(adapter, tmp_path):
    for kind in ("number", "string", "list", "mapping"):
        study = study_with(adapter, kind)
        schema = adapter.schema(study)
        good = good_answer(schema)
        assert adapter.problems(study, "eval_answer.json", write(tmp_path, "good.json", good)) == []

        dropped = next(iter(schema))
        missing = {k: v for k, v in good.items() if k != dropped}
        assert any(dropped in p for p in adapter.problems(study, "eval_answer.json", write(tmp_path, "m.json", missing)))

        field = next(k for k, v in schema.items() if v == kind)
        wrong = dict(good, **{field: {"number": "many", "string": 3, "list": "CD14", "mapping": [1, 2]}[kind]})
        assert any(field in p for p in adapter.problems(study, "eval_answer.json", write(tmp_path, "w.json", wrong)))

        extra = dict(good, surprise=1)
        assert any("surprise" in p for p in adapter.problems(study, "eval_answer.json", write(tmp_path, "x.json", extra)))

    study = adapter.studies()[0]
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json")
    assert adapter.problems(study, "eval_answer.json", bad)
    assert adapter.problems(study, "eval_answer.json", write(tmp_path, "list.json", [1, 2]))
    nan = tmp_path / "nan.json"
    schema = adapter.schema(study_with(adapter, "number"))
    nan.write_text("{" + ", ".join(f'"{k}": NaN' for k in schema) + "}")
    assert adapter.problems(study_with(adapter, "number"), "eval_answer.json", nan)


# ----------------------------------------------------------------------------- our agreement rule

def test_numbers_agree_within_five_percent(adapter, tmp_path):
    study = study_with(adapter, "number")
    schema = adapter.schema(study)
    field = next(k for k, v in schema.items() if v == "number")
    a = write(tmp_path, "a.json", dict(good_answer(schema), **{field: 100.0}))
    near = write(tmp_path, "b.json", dict(good_answer(schema), **{field: 104.0}))
    far = write(tmp_path, "c.json", dict(good_answer(schema), **{field: 110.0}))
    assert adapter.field_agreement(study, a, near)[field] is True
    assert adapter.field_agreement(study, a, far)[field] is False
    zero_a = write(tmp_path, "z1.json", dict(good_answer(schema), **{field: 0}))
    zero_b = write(tmp_path, "z2.json", dict(good_answer(schema), **{field: 0.0}))
    assert adapter.field_agreement(study, zero_a, zero_b)[field] is True
    assert adapter.agreement(study, "eval_answer.json", a, a) == 1.0


def test_strings_agree_ignoring_case_and_spaces(adapter, tmp_path):
    study = study_with(adapter, "string")
    schema = adapter.schema(study)
    field = next(k for k, v in schema.items() if v == "string")
    a = write(tmp_path, "a.json", dict(good_answer(schema), **{field: "B"}))
    same = write(tmp_path, "b.json", dict(good_answer(schema), **{field: " b "}))
    other = write(tmp_path, "c.json", dict(good_answer(schema), **{field: "C"}))
    assert adapter.field_agreement(study, a, same)[field] is True
    assert adapter.field_agreement(study, a, other)[field] is False


def test_lists_agree_by_jaccard_of_at_least_point_eight(adapter, tmp_path):
    study = study_with(adapter, "list")
    schema = adapter.schema(study)
    field = next(k for k, v in schema.items() if v == "list")
    genes = [f"G{i}" for i in range(10)]
    a = write(tmp_path, "a.json", dict(good_answer(schema), **{field: genes}))
    # 9 shared of 10 and 10 -> 9/11 = 0.82 agrees; 8 shared -> 8/12 = 0.67 does not
    close = write(tmp_path, "b.json", dict(good_answer(schema), **{field: [g.lower() for g in genes[:9]] + ["X1"]}))
    apart = write(tmp_path, "c.json", dict(good_answer(schema), **{field: genes[:8] + ["X1", "X2"]}))
    assert adapter.field_agreement(study, a, close)[field] is True
    assert adapter.field_agreement(study, a, apart)[field] is False


def test_mappings_agree_on_same_keys_with_each_value_within_five_percent(adapter, tmp_path):
    study = study_with(adapter, "mapping")
    schema = adapter.schema(study)
    field = next(k for k, v in schema.items() if v == "mapping")
    base = {"T cells": 50.0, "B cells": 30.0, "Monocytes": 20.0}
    a = write(tmp_path, "a.json", dict(good_answer(schema), **{field: base}))
    close = write(tmp_path, "b.json", dict(good_answer(schema), **{field: {"t cells": 51.0, "B cells": 29.0, "Monocytes": 20.5}}))
    off = write(tmp_path, "c.json", dict(good_answer(schema), **{field: {"T cells": 50.0, "B cells": 30.0, "Monocytes": 25.0}}))
    fewer = write(tmp_path, "d.json", dict(good_answer(schema), **{field: {"T cells": 50.0, "B cells": 30.0}}))
    assert adapter.field_agreement(study, a, close)[field] is True
    assert adapter.field_agreement(study, a, off)[field] is False
    assert adapter.field_agreement(study, a, fewer)[field] is False


def test_missing_or_unreadable_candidate_agrees_on_nothing(adapter, tmp_path):
    study = adapter.studies()[0]
    a = write(tmp_path, "a.json", good_answer(adapter.schema(study)))
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert not any(adapter.field_agreement(study, a, bad).values())
    assert adapter.agreement(study, "eval_answer.json", a, tmp_path / "absent.json") == 0.0


# ----------------------------------------------------------------------------- grading, with a stub grader

@pytest.fixture
def stub_grader(monkeypatch):
    """Replaces the call into the benchmark's grader. Passes only an answer holding PASS_TOKEN."""
    calls = []

    def stub(set_name, grader_type, config, answer):
        calls.append({"set": set_name, "type": grader_type, "config_is_dict": isinstance(config, dict)})
        if answer.get("boom"):
            raise KeyError("cell_type_distribution")      # what a real grader does on a missing field
        passed = "PASS_TOKEN" in json.dumps(answer)
        return {"passed": passed, "score": 1.0 if passed else 0.25}

    monkeypatch.setattr(scbench, "_benchmark_grader", stub)
    return calls


def test_grade_passes_and_fails_by_the_grader_verdict(adapter, tmp_path, stub_grader):
    study = adapter.studies()[0]
    write(tmp_path, "eval_answer.json", {"x": "PASS_TOKEN"})
    assert adapter.grade(study, tmp_path) == {"passed": True, "score": 1.0}
    write(tmp_path, "eval_answer.json", {"x": "nope"})
    assert adapter.grade(study, tmp_path) == {"passed": False, "score": 0.25}
    assert stub_grader[-1]["set"] == "current" and stub_grader[-1]["config_is_dict"]
    assert stub_grader[-1]["type"] in ("numeric_tolerance", "multiple_choice", "label_set_jaccard",
                                       "marker_gene_precision_recall", "distribution_comparison")


def test_grade_reads_answer_json_when_the_lab_copied_the_candidate_there(adapter, tmp_path, stub_grader):
    study = adapter.studies()[-1]
    write(tmp_path, "answer.json", {"x": "PASS_TOKEN"})
    assert adapter.grade(study, tmp_path)["passed"] is True
    assert stub_grader[-1]["set"] == "canonical"


def test_grade_survives_malformed_answers_and_grader_errors(adapter, tmp_path, stub_grader):
    study = adapter.studies()[0]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert adapter.grade(study, empty)["passed"] is False

    (tmp_path / "eval_answer.json").write_text("not json at all")
    verdict = adapter.grade(study, tmp_path)
    assert verdict["passed"] is False and verdict["score"] == 0.0

    write(tmp_path, "eval_answer.json", ["a", "list"])
    assert adapter.grade(study, tmp_path)["passed"] is False

    write(tmp_path, "eval_answer.json", {"boom": True})
    verdict = adapter.grade(study, tmp_path)
    assert verdict["passed"] is False and "KeyError" in verdict["error"]
    assert len(stub_grader) == 1        # only the object answer reached the grader
