"""The "convention" variant of the BioDSA adapter: every arm reads the same task text, with the
benchmark's label convention (CONVENTION_NOTE) inserted before "# The data". Uses a temporary
public folder, so no staged data and no label is read."""

import json

import pytest

from lab import run
from lab.adapters import biodsa
from lab.adapters.biodsa import BioDSA
from lab.baseline import baseline_brief
from lab.protocol import LABEL_ADAPTERS, abstains


@pytest.fixture
def public(tmp_path, monkeypatch):
    root = tmp_path / "public"
    for sid, hypothesis in (("bd01", "Gene X mutations are more common in older patients."),
                            ("bd02", "Tumour Y responds to drug Z.")):
        (root / sid / "data").mkdir(parents=True)
        (root / sid / "data" / "data_clinical_patient.txt").write_text("#a\nPATIENT_ID\tAGE\nP1\t60\n")
        (root / sid / "task.md").write_text(biodsa.task_text(hypothesis, {"data_clinical_patient.txt": 30}))
        (root / sid / "answer_schema.json").write_text(json.dumps({"label": "string"}))
    monkeypatch.setattr(biodsa, "PUBLIC", root)
    return root


def test_the_plain_adapter_reads_task_md_unchanged(public):
    for s in BioDSA().studies():
        assert s.statement == (public / s.id / "task.md").read_text()
        assert "How the labels were assigned" not in s.statement


def test_the_convention_adapter_inserts_the_note_before_the_data_section(public):
    plain = {s.id: s.statement for s in BioDSA().studies()}
    for s in BioDSA(convention=True).studies():
        assert s.statement == biodsa.with_convention(plain[s.id])
        assert s.statement.replace(biodsa.CONVENTION_NOTE, "") == plain[s.id]
        assert s.statement.index("# How the labels were assigned") < s.statement.index("# The data")
        assert s.statement.index("# Your task") < s.statement.index("# How the labels were assigned")


def test_task_md_on_disk_is_never_changed(public):
    before = {p: p.read_bytes() for p in public.rglob("*") if p.is_file()}
    BioDSA(convention=True).studies()
    assert before == {p: p.read_bytes() for p in public.rglob("*") if p.is_file()}


def test_with_convention_refuses_a_text_without_a_data_section():
    with pytest.raises(ValueError):
        biodsa.with_convention("# Hypothesis\n\nx\n")


def test_registered_under_its_own_name_and_keeps_biodsa_labels(public):
    adapter = run.load_adapter("biodsa-convention")
    assert isinstance(adapter, BioDSA) and adapter.convention and adapter.name == "biodsa-1k-convention"
    assert adapter.name in LABEL_ADAPTERS and run.load_adapter("biodsa").name == "biodsa-1k"
    study = adapter.studies()[0]
    assert abstains(adapter, study, {"label": "Not verifiable"}) and not abstains(adapter, study, {"label": "True"})


def test_grading_and_schema_are_the_same_as_the_plain_adapter(public):
    plain, conv = BioDSA(), BioDSA(convention=True)
    assert plain.schema(plain.studies()[0]) == conv.schema(conv.studies()[0])
    assert plain.mapping_path == conv.mapping_path and plain.tables(plain.studies()[0]) == conv.tables(conv.studies()[0])


def test_the_single_model_brief_carries_the_note(public):
    study = BioDSA(convention=True).studies()[0]
    brief = baseline_brief(study)
    assert biodsa.CONVENTION_NOTE.strip() in brief


def test_the_note_states_the_convention_not_a_task():
    note = biodsa.CONVENTION_NOTE
    for phrase in ('"True" or "False"', '"Not verifiable"', "different publication", "established biology",
                   "counts as wrong"):
        assert phrase in note
    assert "{" not in note and "}" not in note      # it goes into templates through {statement}
