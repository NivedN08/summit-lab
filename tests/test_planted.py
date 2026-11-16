"""Checks on the planted-fault suite (lab/adapters/planted.py).

Everything is generated into a temporary folder; no test reads data/planted.
"""

import json
import pathlib
import re

import pytest

from lab.adapters import biodsa, planted
from lab.adapters.planted import Planted

LEAK_WORDS = ["fault", "twin", "duplicat", "planted", "confound", "mislead", "hidden", "mismatch", "invert",
              "revers", "swap", "sealed", "mapping.json", "generation.json", "heldout", "held-out",
              *planted.FAMILIES, *(f.replace("_", " ") for f in planted.FAMILIES)]


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    out = tmp_path_factory.mktemp("planted")
    planted.generate(planted.SEED, out)
    return out


@pytest.fixture(scope="module")
def mapping(root):
    return json.loads((root / "sealed" / "mapping.json").read_text())


@pytest.fixture(scope="module")
def tasks(root):
    return planted.load_tasks(root)


@pytest.fixture
def adapter(root):
    return Planted(root=root, mounted=False)


def files(folder: pathlib.Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def write_answer(folder: pathlib.Path, doc) -> pathlib.Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "answer.json"
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def answer(label, **extra):
    return {"answer": {"label": label, **extra}, "reasoning": "test"}


# ----------------------------------------------------------------------------- the generator

def test_generator_is_deterministic(root, tmp_path):
    planted.generate(planted.SEED, tmp_path / "again")
    assert files(tmp_path / "again") == files(root)
    planted.generate(planted.SEED + 1, tmp_path / "other")
    assert files(tmp_path / "other") != files(root)


def test_twenty_four_tasks_split_eight_and_sixteen(mapping):
    assert sorted(mapping) == [f"pf{i:02d}" for i in range(1, 25)]
    splits = [m["split"] for m in mapping.values()]
    assert splits.count("dev") == 8 and splits.count("heldout") == 16
    assert {m["family"] for m in mapping.values()} == set(planted.FAMILIES)


def test_each_faulted_task_has_a_clean_twin_in_the_same_split(mapping):
    faulted = [i for i, m in mapping.items() if m["faulted"]]
    assert len(faulted) == 12
    for i in faulted:
        twin = mapping[mapping[i]["twin"]]
        assert not twin["faulted"] and twin["twin"] == i
        assert (twin["family"], twin["split"], twin["label"]) == (mapping[i]["family"], mapping[i]["split"],
                                                                  mapping[i]["label"])


def test_dev_split_covers_four_families_and_both_labels(mapping):
    dev = [m for m in mapping.values() if m["split"] == "dev"]
    assert len({m["family"] for m in dev}) == 4
    assert sorted(m["label"] for m in dev) == ["False"] * 4 + ["True"] * 4


def test_generation_record_holds_seed_and_parameters(root):
    record = json.loads((root / "sealed" / "generation.json").read_text())
    assert record["seed"] == planted.SEED and record["parameters"]["n_tasks"] == 24


# ----------------------------------------------------------------------------- the public side

def test_public_tasks_have_the_same_files(root):
    for d in sorted((root / "public").iterdir()):
        assert sorted(p.name for p in d.iterdir()) == ["answer_schema.json", "data", "task.md"]
        assert sorted(p.name for p in (d / "data").iterdir()) == sorted(
            [planted.PATIENT_TABLE, planted.SAMPLE_TABLE, planted.SUPPLEMENT_TABLE])
        assert json.loads((d / "answer_schema.json").read_text()) == {"label": "string"}


def test_public_files_hold_no_leak_words(root):
    for p in (root / "public").rglob("*"):
        text = p.name.lower() + ("\n" + p.read_text().lower() if p.is_file() else "")
        found = [w for w in LEAK_WORDS if w.lower() in text]
        assert not found, f"{p.relative_to(root)} contains {found}"


def test_row_counts_do_not_give_away_the_family(mapping, tasks):
    for t in tasks:
        assert planted.ROWS[0] <= t["rows"] <= planted.ROWS[1]


def test_hypotheses_are_all_different(mapping):
    assert len({m["hypothesis"] for m in mapping.values()}) == 24


def test_label_definitions_match_biodsa(root):
    def block(text):
        return re.search(r"Choose exactly one label:\n(.*?)\n# The data", text, re.S).group(1)
    statement = (root / "public" / "pf01" / "task.md").read_text()
    assert block(statement) == block(biodsa.TASK_TEXT)
    assert "answer.json" in statement


# ----------------------------------------------------------------------------- the analyses

def test_reference_answer_matches_the_mapping_for_every_task(tasks):
    for t in tasks:
        assert planted.reference_answer(t) == t["label"], t["id"]


def test_reference_analysis_finds_each_planted_fault(tasks):
    for t in tasks:
        notes = planted.analyse(t)["notes"]
        faulted = t["faulted"]
        assert (notes["group_table"] == planted.SUPPLEMENT_TABLE) == (faulted and t["family"] == planted.HIDDEN)
        assert notes["duplicates_removed"] == (t["fault"]["duplicated_rows"] if faulted and
                                               t["family"] == planted.DUPLICATES else 0)
        assert notes["coding_reversed"] == (faulted and t["family"] == planted.CODING)
        assert notes["sites_converted"] == ([t["fault"]["site"]] if faulted and t["family"] == planted.UNITS else [])


def test_naive_analysis_is_wrong_on_faulted_and_right_on_clean_tasks(tasks):
    bites = {}
    for family in planted.FAMILIES:
        mine = [t for t in tasks if t["family"] == family]
        bites[family] = (all(planted.naive_answer(t) != t["label"] for t in mine if t["faulted"])
                         and all(planted.naive_answer(t) == t["label"] for t in mine if not t["faulted"]))
    assert sum(bites.values()) >= 4, bites


def test_self_check_counts(root):
    report = planted.self_check(root)
    assert report["reference_matches"] == report["tasks"] == 24
    assert sum(sum(c.values()) for c in report["labels_by_split"].values()) == 24


# ----------------------------------------------------------------------------- the adapter

def test_studies_by_split(root):
    assert len(Planted(root=root).studies()) == 24
    dev, held = Planted("dev", root=root).studies(), Planted("heldout", root=root).studies()
    assert len(dev) == 8 and len(held) == 16 and not {s.id for s in dev} & {s.id for s in held}
    with pytest.raises(ValueError):
        Planted("test", root=root)


def test_studies_point_at_the_mount_or_the_local_folder(root):
    mounted, local = Planted(root=root).studies()[0], Planted(root=root, mounted=False).studies()[0]
    assert str(mounted.data_dir) == planted.MOUNT and Planted(root=root).modal_only
    assert local.data_dir == root / "public" / local.id / "data" and not Planted(root=root, mounted=False).modal_only
    assert mounted.deliverables == ["answer.json"] and "# Hypothesis" in mounted.statement


def test_grade_kinds(adapter, mapping, tmp_path):
    studies = {s.id: s for s in adapter.studies()}
    true_id = next(i for i, m in mapping.items() if m["label"] == "True")
    false_id = next(i for i, m in mapping.items() if m["label"] == "False")
    cases = [(true_id, "True", True, "correct"), (false_id, "False", True, "correct"),
             (false_id, "True", False, "accepted a false claim"), (true_id, "False", False, "rejected a true claim"),
             (true_id, "Not verifiable", False, "judged a settled claim not verifiable")]
    for n, (sid, label, passed, kind) in enumerate(cases):
        folder = tmp_path / str(n)
        write_answer(folder, answer(label))
        g = adapter.grade(studies[sid], folder)
        assert (g["passed"], g["kind"], g["score"]) == (passed, kind, float(passed))
        assert (g["family"], g["faulted"], g["twin"]) == (mapping[sid]["family"], mapping[sid]["faulted"],
                                                          mapping[sid]["twin"])


def test_grade_fails_malformed_or_missing_answers(adapter, tmp_path):
    study = adapter.studies()[0]
    write_answer(tmp_path / "bad", answer("maybe"))
    g = adapter.grade(study, tmp_path / "bad")
    assert not g["passed"] and "error" in g and "kind" not in g and "family" in g
    g = adapter.grade(study, tmp_path / "nothing")
    assert not g["passed"] and g["error"].startswith("cannot read JSON")


def test_problems_catch_malformed_answers(adapter, tmp_path):
    study = adapter.studies()[0]
    check = lambda doc: adapter.problems(study, "answer.json", write_answer(tmp_path, doc))  # noqa: E731
    assert check(answer("True")) == [] and check(answer("Not verifiable")) == []
    assert check("not json")[0].startswith("cannot read JSON")
    assert check({"label": "True"}) == ['top level must be an object with an "answer" object inside']
    assert check({"answer": {}}) == ['missing answer field "label"']
    assert check(answer("true")) == ['"label" must be exactly one of "True", "False" or "Not verifiable"']
    assert check(answer("False", p=0.01)) == ['unexpected answer field "p"']


def test_agreement_is_equal_labels(adapter, tmp_path):
    study = adapter.studies()[0]
    a = write_answer(tmp_path / "a", answer("True"))
    b = write_answer(tmp_path / "b", answer("True"))
    c = write_answer(tmp_path / "c", answer("False"))
    d = write_answer(tmp_path / "d", answer("yes"))
    e = write_answer(tmp_path / "e", answer("yes"))
    assert adapter.field_agreement(study, a, b) == {"label": True}
    assert adapter.agreement(study, "answer.json", a, c) == 0.0
    assert adapter.agreement(study, "answer.json", d, e) == 0.0


def test_schema_and_blocked_terms(adapter):
    study = adapter.studies()[0]
    assert adapter.schema(study) == {"label": "string"}
    assert adapter.blocked_terms() == ["summit-planted"]


def test_modal_spec_mounts_the_task_folder(root):
    adapter = Planted(root=root)
    study = adapter.studies()[0]
    spec = adapter.modal_spec(study)
    assert spec["volumes"] == [{"name": "summit-planted-data", "sub_path": f"/{study.id}", "mount": "/planted-data"}]


def test_missing_data_on_the_volume_and_locally(root):
    adapter = Planted(root=root)
    studies = adapter.studies()[:2]
    full = adapter.tables(studies[0])
    listing = lambda sub: full if sub == f"/{studies[0].id}" else {}  # noqa: E731
    assert adapter.missing_data(studies, listing=listing) == [studies[1].id]
    assert "upload" in adapter.data_command([studies[1].id])
    local = Planted(root=root, mounted=False)
    assert local.missing_data(local.studies()) == [] and "stage" in local.data_command([])
