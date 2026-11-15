"""Checks on the BioDSA-1K adapter.

Tests of the staged public side are skipped when the benchmark has not been staged. The format,
agreement and grading tests need no download: grading uses a temporary fake sealed mapping, so
no test reads or prints a real label. Leak checks compare against the sealed side without
showing it.
"""

import json
import pathlib

import pytest

from lab import run
from lab.adapters import biodsa
from lab.adapters.biodsa import BioDSA
from lab.contracts import Study

STAGED = biodsa.PUBLIC.exists() and biodsa.MAPPING.exists() and biodsa.SAMPLE_RECORD.exists()
staged = pytest.mark.skipif(not STAGED, reason="benchmark not staged")


@pytest.fixture
def adapter():
    return BioDSA()


@pytest.fixture
def mapping():
    return json.loads(biodsa.MAPPING.read_text())


@pytest.fixture
def rows():
    return biodsa.load_rows("hypotheses")


def write(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def answer(label, **extra):
    return {"answer": {"label": label, **extra}, "reasoning": "test"}


def public_files():
    return [p for p in biodsa.PUBLIC.rglob("*") if p.is_file()]


# ----------------------------------------------------------------------------- the public side

@staged
def test_forty_studies_each_with_task_text_schema_and_tables(adapter):
    studies = adapter.studies()
    assert [s.id for s in studies] == [f"bd{i:02d}" for i in range(1, 41)]
    for s in studies:
        assert s.statement.strip() and "answer.json" in s.statement
        assert s.deliverables == ["answer.json"] and str(s.data_dir) == biodsa.MOUNT
        assert adapter.schema(s) == {"label": "string"}
        tables = adapter.tables(s)
        assert tables and all(name.startswith("data_") and size > 0 for name, size in tables.items())
        assert sum(tables.values()) <= biodsa.MAX_TABLE_BYTES


@staged
def test_public_side_holds_only_task_text_schema_and_data_tables():
    for path in public_files():
        rel = path.relative_to(biodsa.PUBLIC).parts
        assert len(rel) in (2, 3), path
        if len(rel) == 2:
            assert rel[1] in ("task.md", "answer_schema.json"), path
        else:
            assert rel[1] == "data" and rel[2].startswith("data_"), path


@staged
def test_task_text_is_the_hypothesis_and_the_fixed_instructions_only(adapter, mapping, rows):
    """task.md is rebuilt from the sealed hypothesis and the table list: nothing else is in it,
    so no label, evidence or source can be."""
    by_sample = {r["sample_id"]: r for r in rows}
    for s in adapter.studies():
        row = by_sample[mapping[s.id]["sample_id"]]
        assert s.statement == biodsa.task_text(row["hypothesis"], adapter.tables(s))


@staged
def test_public_side_names_no_label_evidence_pair_real_id_or_source(mapping, rows):
    by_sample = {r["sample_id"]: r for r in rows}
    small = [p for p in public_files() if p.name in ("task.md", "answer_schema.json")]
    texts = {p: p.read_text().lower() for p in small}
    names = " ".join(str(p.relative_to(biodsa.PUBLIC)).lower() for p in public_files())
    for m in mapping.values():
        row = by_sample[m["sample_id"]]
        evidence = biodsa._norm_text(row["supporting_evidences"])
        forbidden = [m["sample_id"], m["pair_id"], m["study"], m["pmid"], m.get("dataset_pmid"), m.get("title"),
                     m.get("dataset_title"), (m.get("meta") or {}).get("name"), (m.get("meta") or {}).get("citation"),
                     m["url"], m["study_page"], m["label"]]
        for term in (str(t).lower() for t in forbidden if t):
            if term in ("true", "false"):    # the label words are in every task's instructions
                continue
            assert term not in names
            assert not any(term in text for text in texts.values()), "a source identifier is in a task file"
        assert not any(evidence in biodsa._norm_text(t) for t in texts.values()), "evidence text is public"
    assert not any(t in names or any(t in x for x in texts.values()) for t in ("non-verifiable", "_nv", "_true", "_false"))


@staged
def test_tables_hold_no_source_identifiers(mapping):
    for neutral, m in mapping.items():
        strings, patterns = biodsa._needles(m)
        for table in sorted((biodsa.PUBLIC / neutral / "data").iterdir()):
            with table.open("rb") as f:
                assert not biodsa._found_in(f, strings, patterns), f"{neutral}/{table.name} names its source"


@staged
def test_no_two_tasks_share_a_pair_study_publication_or_text(mapping, rows):
    pairs = [m["pair_id"] for m in mapping.values()]
    studies = [m["study"] for m in mapping.values()]
    assert len(set(pairs)) == len(pairs) == 40
    assert len(set(studies)) == len(studies)
    pmids = [p for m in mapping.values() for p in {m["pmid"], m.get("dataset_pmid")} if p]
    assert len(set(pmids)) == len(pmids)
    # the other half of a chosen pair, and any repeat of a chosen text, is in no public task
    chosen = {m["sample_id"] for m in mapping.values()}
    others = {biodsa._norm_text(r["hypothesis"]) for r in rows
              if biodsa.pair_id(r["sample_id"]) in set(pairs) and r["sample_id"] not in chosen}
    statements = [biodsa._norm_text(p.read_text()) for p in biodsa.PUBLIC.glob("*/task.md")]
    assert not any(o in s for o in others for s in statements)


@staged
def test_mapping_and_label_counts_are_sealed_and_match_the_full_mix(mapping, rows):
    assert biodsa.SEALED in biodsa.MAPPING.parents and biodsa.SEALED in biodsa.SAMPLE_RECORD.parents
    assert not biodsa.PUBLIC.is_relative_to(biodsa.SEALED) and not biodsa.SEALED.is_relative_to(biodsa.PUBLIC)
    record = json.loads(biodsa.SAMPLE_RECORD.read_text())
    assert record["seed"] == biodsa.SEED and record["targets"] == biodsa._targets(rows, 40)
    counts = {}
    for m in mapping.values():
        counts[m["label"]] = counts.get(m["label"], 0) + 1
    assert counts == record["sample_labels"] == record["targets"]
    assert set(counts) <= set(biodsa.LABELS)


@staged
def test_neutral_order_carries_no_meaning(mapping):
    """Ids follow a hash of the sealed sample id, not the label, the study or the draw order."""
    import hashlib
    keys = [hashlib.sha256(f"{biodsa.SEED}:{m['sample_id']}".encode()).hexdigest() for m in mapping.values()]
    assert keys == sorted(keys)


@staged
def test_blocked_terms_name_the_benchmark_and_every_tasks_source(adapter, mapping):
    terms = adapter.blocked_terms()
    assert {"biodsa", "biodsa-1k", "2505.16100", "zifeng-ai"} <= set(terms)
    for m in mapping.values():
        assert m["study"].lower() in terms and m["pmid"] in terms
        if m.get("title"):
            assert m["title"].lower() in terms


@staged
def test_modal_spec_mounts_this_tasks_tables_read_only_with_survival_tools(adapter):
    for s in adapter.studies():
        spec = adapter.modal_spec(s)
        assert spec["volumes"] == [{"name": "summit-biodsa-data", "sub_path": f"/{s.id}", "mount": "/biodsa-data"}]
        assert spec["cpu"] == 2.0 and spec["memory_mb"] in (4096, 8192)
        assert any(p.startswith("lifelines==") for p in spec["packages"]) and spec["package_index"] is True


@staged
def test_missing_data_compares_the_volume_with_the_public_tables(adapter):
    studies = adapter.studies()[:2]
    full = {f"/{s.id}": adapter.tables(s) for s in studies}
    assert adapter.missing_data(studies, listing=lambda sub: full[sub]) == []
    short = dict(full)
    short[f"/{studies[1].id}"] = {}
    assert adapter.missing_data(studies, listing=lambda sub: short[sub]) == [studies[1].id]


# ----------------------------------------------------------------------------- format, agreement, grading

STUDY = Study("bd01", "bd01", "statement", pathlib.Path("/biodsa-data"), ["answer.json"])


def test_registered_as_biodsa():
    assert isinstance(run.load_adapter("biodsa"), BioDSA)


@pytest.mark.parametrize("label", ["True", "False", "Not verifiable"])
def test_format_check_accepts_each_of_the_three_labels(tmp_path, label):
    assert BioDSA().problems(STUDY, "answer.json", write(tmp_path, "a.json", answer(label))) == []


@pytest.mark.parametrize("doc", [
    answer("true"), answer("Not Verifiable"), answer("Non-verifiable"), answer("Unknown"), answer(True),
    answer("True", confidence=0.9), {"answer": {}}, {"label": "True"}, ["True"], "not json"])
def test_format_check_rejects_anything_else(tmp_path, doc):
    assert BioDSA().problems(STUDY, "answer.json", write(tmp_path, "a.json", doc))


def test_agreement_is_equal_valid_labels(tmp_path):
    a = BioDSA()
    t1, t2 = write(tmp_path, "t1.json", answer("True")), write(tmp_path, "t2.json", answer("True"))
    f = write(tmp_path, "f.json", answer("False"))
    bad1, bad2 = write(tmp_path, "b1.json", answer("maybe")), write(tmp_path, "b2.json", answer("maybe"))
    assert a.field_agreement(STUDY, t1, t2) == {"label": True} and a.agreement(STUDY, "answer.json", t1, t2) == 1.0
    assert a.field_agreement(STUDY, t1, f) == {"label": False} and a.agreement(STUDY, "answer.json", t1, f) == 0.0
    assert a.field_agreement(STUDY, bad1, bad2) == {"label": False}
    assert a.field_agreement(STUDY, t1, tmp_path / "missing.json") == {"label": False}


@pytest.fixture
def fake_sealed(tmp_path, monkeypatch):
    """A temporary sealed mapping with made-up labels, in place of the real one."""
    path = tmp_path / "sealed" / "summit_mapping.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"bd01": {"label": "true"}, "bd02": {"label": "false"},
                                "bd03": {"label": "non-verifiable"}}))
    monkeypatch.setattr(biodsa, "MAPPING", path)
    return path


def grade(tmp_path, study_id, doc):
    final = tmp_path / study_id / "final"
    final.mkdir(parents=True)
    if doc is not None:
        write(final, "answer.json", doc)
    return BioDSA().grade(Study(study_id, study_id, "s", pathlib.Path("/biodsa-data"), ["answer.json"]), final)


def test_grade_passes_the_right_label(tmp_path, fake_sealed):
    right = {"passed": True, "score": 1.0, "kind": "correct"}
    assert grade(tmp_path / "a", "bd01", answer("True")) == right
    assert grade(tmp_path / "b", "bd02", answer("False")) == right
    assert grade(tmp_path / "c", "bd03", answer("Not verifiable")) == right


def test_grade_fails_a_wrong_label(tmp_path, fake_sealed):
    result = grade(tmp_path, "bd01", answer("False"))
    assert result["passed"] is False and result["score"] == 0.0


# In the fake sealed mapping bd01 is True, bd02 False and bd03 Not verifiable (made-up labels).
@pytest.mark.parametrize("study_id, given, kind", [
    ("bd01", "True", "correct"), ("bd02", "False", "correct"), ("bd03", "Not verifiable", "correct"),
    ("bd02", "True", "accepted a false claim"),
    ("bd01", "False", "rejected a true claim"),
    ("bd01", "Not verifiable", "judged a settled claim not verifiable"),
    ("bd02", "Not verifiable", "judged a settled claim not verifiable"),
    ("bd03", "True", "judged an unverifiable claim settled"),
    ("bd03", "False", "judged an unverifiable claim settled")])
def test_grade_says_what_sort_of_right_or_wrong(tmp_path, fake_sealed, study_id, given, kind):
    result = grade(tmp_path, study_id, answer(given))
    assert result["kind"] == kind and result["passed"] is (kind == "correct")


@pytest.mark.parametrize("doc", [answer("true"), answer("Non-verifiable"), {"label": "True"}, "{", None])
def test_grade_fails_a_malformed_or_missing_answer(tmp_path, fake_sealed, doc):
    result = grade(tmp_path, "bd03" if doc != answer("true") else "bd01", doc)
    assert result["passed"] is False and result["score"] == 0.0 and result["error"]
