"""Checks on the GeneBench-Pro adapter. Skipped when the benchmark has not been downloaded and staged."""

import json

import pytest

from lab.adapters import genebench
from lab.adapters.genebench import GeneBench

pytestmark = pytest.mark.skipif(not genebench.PUBLIC.exists(), reason="benchmark not staged")


@pytest.fixture
def adapter():
    return GeneBench()


def write_answer(tmp_path, name, answer):
    path = tmp_path / name
    path.write_text(json.dumps({"answer": answer, "reasoning": "test"}))
    return path


def test_ten_studies_each_with_task_schema_and_data(adapter):
    studies = adapter.studies()
    assert [s.id for s in studies] == [f"gb{i:02d}" for i in range(1, 11)]
    for s in studies:
        assert s.statement.strip()
        assert adapter.schema(s)
        assert any(s.data_dir.iterdir())


def test_agent_side_holds_no_answers_reports_or_real_problem_names():
    real_names = set(json.loads(genebench.MAPPING.read_text()).values())
    for path in genebench.PUBLIC.rglob("*"):
        assert path.name not in ("eval_config.json", "report_public.pdf", "reference_grader.py")
        assert not any(name in str(path) for name in real_names)
    for task in genebench.PUBLIC.rglob("task.md"):
        text = task.read_text()
        assert not any(name in text for name in real_names)


def test_format_check_accepts_a_well_formed_answer_and_names_what_is_wrong(adapter, tmp_path):
    study = adapter.studies()[0]
    schema = adapter.schema(study)
    good = {k: (1.5 if kind == "number" else "x") for k, kind in schema.items()}
    assert adapter.problems(study, "answer.json", write_answer(tmp_path, "good.json", good)) == []

    missing = dict(good)
    dropped = next(iter(missing))
    del missing[dropped]
    found = adapter.problems(study, "answer.json", write_answer(tmp_path, "missing.json", missing))
    assert any(dropped in p for p in found)

    extra = dict(good, surprise=1)
    assert any("surprise" in p for p in adapter.problems(study, "answer.json", write_answer(tmp_path, "extra.json", extra)))

    not_json = tmp_path / "bad.json"
    not_json.write_text("this is not json")
    assert adapter.problems(study, "answer.json", not_json)


def test_agreement_is_field_by_field_with_a_five_percent_rule_for_numbers(adapter, tmp_path):
    study = next(s for s in adapter.studies() if "number" in adapter.schema(s).values())
    schema = adapter.schema(study)
    base = {k: (100.0 if kind == "number" else "A") for k, kind in schema.items()}
    close = {k: (103.0 if kind == "number" else " a ") for k, kind in schema.items()}
    far = {k: (120.0 if kind == "number" else "A") for k, kind in schema.items()}
    a = write_answer(tmp_path, "a.json", base)
    assert adapter.agreement(study, "answer.json", a, a) == 1.0
    assert adapter.agreement(study, "answer.json", a, write_answer(tmp_path, "close.json", close)) == 1.0
    assert adapter.agreement(study, "answer.json", a, write_answer(tmp_path, "far.json", far)) < 1.0


def test_grader_fails_a_placeholder_answer_and_a_missing_file(adapter, tmp_path):
    study = adapter.studies()[0]
    placeholder = {k: (0.123456 if kind == "number" else "zzz") for k, kind in adapter.schema(study).items()}
    write_answer(tmp_path, "answer.json", placeholder)
    verdict = adapter.grade(study, tmp_path)
    assert verdict["passed"] is False

    empty = tmp_path / "empty"
    empty.mkdir()
    assert adapter.grade(study, empty)["passed"] is False
