"""Checks on the BioDSA-1K held-out split and the refactored sampler.

Everything here runs on made-up Hugging Face-like rows and fake cBioPortal archives in tmp_path,
with the adapter's path constants pointed there. No test reads the real sealed files, downloads
anything or runs an agent.
"""

import collections
import hashlib
import io
import json
import pathlib
import random
import sys
import tarfile
import types

import pytest

from lab.adapters import biodsa
from lab.adapters.biodsa import BioDSA
from lab.contracts import Study

N_STUDIES, N_PAIRS, N_SINGLE = 160, 150, 70
HELDOUT_SEED, HELDOUT_N = 777, 24


# ----------------------------------------------------------------------------- synthetic rows

def study_id(k: int) -> str:
    return f"synthstudy_{k:04d}"


def make_rows():
    """Rows shaped like the BioDSA-1K files: true/false pairs and single "non-verifiable" rows,
    with shared studies, shared publications, repeated texts and texts that hold their title, so
    every sampling rule has something to skip."""
    datasets = [{"dataset_id": study_id(k), "dataset_url": f"https://s3.example.org/bucket/{study_id(k)}.tar.gz",
                 "PMID": str(31000000 + k), "Title": f"Dataset paper {k} on tumour genomics",
                 "publication_url": f"https://pub.example.org/d{k}"} for k in range(N_STUDIES)]
    rows = []
    for j in range(N_PAIRS):
        study, pmid, title = study_id((j * 7) % N_STUDIES), str(32000000 + j // 2), f"Paper {j // 2} about pathway findings"
        claim = f"mutation in gene G{j} raises risk of event R{j}"
        if j % 13 == 12:
            claim = f"mutation in gene G{j - 1} raises risk of event R{j - 1}"     # a text repeated from another pair
        if j % 17 == 16:
            claim = f"as {title} shows, {claim}"                                  # the text holds its title
        for label, text in (("true", claim), ("false", f"it is not so that {claim}")):
            rows.append({"sample_id": f"{pmid}_{j}_{label}", "hypothesis": text, "hypothesis_label": label,
                         "cBioPortal_ID": study, "cBioPortal_data_url": f"https://www.cbioportal.org/study?id={study}",
                         "PMID": pmid, "Title": title, "publication_class": "synthetic",
                         "supporting_evidences": f"evidence {j}"})
    for j in range(N_SINGLE):
        study = study_id((j * 3 + 1) % N_STUDIES)
        pmid = None if j % 10 == 9 else str(33000000 + j)      # some missing
        rows.append({"sample_id": f"nv{j}_x_nv", "hypothesis": f"loss of gene H{j} shortens survival S{j}",
                     "hypothesis_label": "non-verifiable", "cBioPortal_ID": study,
                     "cBioPortal_data_url": f"https://www.cbioportal.org/study?id={study}", "PMID": pmid,
                     "Title": f"Unrelated paper {j} on cohorts", "publication_class": "synthetic",
                     "supporting_evidences": f"evidence nv {j}"})
    return rows, datasets


def fake_check(source):
    """A deterministic stand-in for _check_study: some studies fail, the rest pass."""
    k = int(source["study"][-4:])
    if k % 9 == 0:
        return "archive missing", {}
    if k % 11 == 5:
        return "the tables contain the source's identifiers", {"table_bytes": 5}
    return None, {"archive_bytes": 100 + k, "table_bytes": 50 + k, "tables": ["data_clinical.txt"], "meta": {}}


def old_sample():
    """The draw exactly as it was before the refactor (lab/adapters/biodsa.py `sample()` at
    commit 91dfa70), with only the module prefix added."""
    rows = biodsa.load_rows("hypotheses")
    datasets = {d["dataset_id"]: d for d in biodsa.load_rows("datasets")}
    targets = biodsa._targets(rows, biodsa.N_TASKS)
    groups = collections.defaultdict(list)
    for r in rows:
        groups[biodsa.pair_id(r["sample_id"])].append(r)
    order = sorted(groups)
    rng = random.Random(biodsa.SEED)
    rng.shuffle(order)
    need = dict(targets)
    used_studies, used_pmids, used_texts = set(), set(), set()
    studies_checked: dict[str, tuple] = {}
    chosen, skipped = [], collections.Counter()
    for pid in order:
        if not any(need.values()):
            break
        options = [r for r in groups[pid] if need.get(r["hypothesis_label"], 0) > 0]
        if not options:
            continue
        row = rng.choice(sorted(options, key=lambda r: r["sample_id"]))
        source = biodsa._source(row, datasets)
        pmids = {source["pmid"], source["dataset_pmid"]} - {None}
        if source["study"] in used_studies or pmids & used_pmids or any(
                biodsa._norm_text(r["hypothesis"]) in used_texts for r in groups[pid]):
            skipped["shares a study, publication or text with a chosen task"] += 1
            continue
        if any(t and biodsa._norm_text(t)[:40] in biodsa._norm_text(row["hypothesis"])
               for t in (source["title"], source["dataset_title"])):
            skipped["hypothesis text contains its source's title"] += 1
            continue
        if source["study"] not in studies_checked:
            studies_checked[source["study"]] = biodsa._check_study(source)
        reason, facts = studies_checked[source["study"]]
        if reason:
            skipped[reason] += 1
            (biodsa.ARCHIVES / f"{source['study']}.tar.gz").unlink(missing_ok=True)
            continue
        need[row["hypothesis_label"]] -= 1
        used_studies.add(source["study"])
        used_pmids |= pmids
        used_texts |= {biodsa._norm_text(r["hypothesis"]) for r in groups[pid]}
        chosen.append({"row": row, "source": {**source, **facts}})
    if any(need.values()):
        raise RuntimeError(f"could not fill the sample; still needed: {need}")
    full = collections.Counter(r["hypothesis_label"] for r in rows)
    record = {"seed": biodsa.SEED, "n_tasks": biodsa.N_TASKS, "hf_revision": biodsa.HF_REVISION,
              "full_set": {"rows": len(rows), "pairs": sum(len(g) == 2 for g in groups.values()),
                           "single": sum(len(g) == 1 for g in groups.values()), "labels": dict(full)},
              "sample_labels": dict(collections.Counter(c["row"]["hypothesis_label"] for c in chosen)),
              "targets": targets, "skipped": dict(skipped),
              "studies_skipped": {s: r for s, (r, _) in studies_checked.items() if r}}
    return chosen, record


# ----------------------------------------------------------------------------- a fake data folder

def add_archive(archives: pathlib.Path, k: int) -> None:
    """A small datahub-like archive: meta_study.txt and two data tables free of identifiers,
    except that some studies leak their id into a table and some have no archive at all."""
    if k % 13 == 6:
        return
    files = {
        f"{study_id(k)}/meta_study.txt": (f"type_of_cancer: brca\ncancer_study_identifier: {study_id(k)}\n"
                                          f"name: Synthetic cohort number {k}\ncitation: Someone et al. 2020\n"
                                          f"pmid: {31000000 + k}\n"),
        f"{study_id(k)}/data_clinical_patient.txt": "#Patient Identifier\tAge\nPATIENT_ID\tAGE\nP-1\t61\nP-2\t47\n",
        f"{study_id(k)}/data_mutations.txt": "Hugo_Symbol\tTumor_Sample_Barcode\nTP53\tS-1\nKRAS\tS-2\n"
                                             + (f"note\t{study_id(k)}\n" if k % 11 == 5 else ""),
    }
    with tarfile.open(archives / f"{study_id(k)}.tar.gz", "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.fixture
def lab_data(tmp_path, monkeypatch):
    """The adapter's paths pointed at tmp_path, with synthetic rows and archives, no network."""
    sealed = tmp_path / "data" / "biodsa" / "sealed"
    paths = {"SEALED": sealed, "HF_DIR": sealed / "hf", "ARCHIVES": sealed / "cbioportal",
             "PUBLIC": tmp_path / "data" / "biodsa" / "public",
             "PUBLIC_HELDOUT": tmp_path / "data" / "biodsa" / "public-heldout",
             "MAPPING": sealed / "summit_mapping.json", "SAMPLE_RECORD": sealed / "summit_sample.json",
             "MAPPING_HELDOUT": sealed / "summit_mapping_heldout.json",
             "SAMPLE_RECORD_HELDOUT": sealed / "summit_sample_heldout.json", "ROOT": tmp_path}
    for name, value in paths.items():
        monkeypatch.setattr(biodsa, name, value)
    paths["HF_DIR"].mkdir(parents=True)
    paths["ARCHIVES"].mkdir()
    rows, datasets = make_rows()
    for stem, items in (("hypotheses", rows), ("datasets", datasets)):
        (paths["HF_DIR"] / f"{stem}.rows.json").write_text(json.dumps({"revision": biodsa.HF_REVISION, "rows": items}))
    for k in range(N_STUDIES):
        add_archive(paths["ARCHIVES"], k)

    def archive_size(url):
        path = paths["ARCHIVES"] / url.rsplit("/", 1)[-1]
        return path.stat().st_size if path.exists() else None

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(biodsa, "download", lambda: None)
    monkeypatch.setattr(biodsa, "_archive_size", archive_size)
    monkeypatch.setattr(biodsa, "_get", no_network)
    monkeypatch.setattr(biodsa.urllib.request, "urlopen", no_network)
    return types.SimpleNamespace(rows=rows, datasets=datasets, **{k.lower(): v for k, v in paths.items()})


@pytest.fixture
def staged_dev(lab_data):
    biodsa.stage()
    return lab_data


@pytest.fixture
def staged_both(staged_dev):
    biodsa.stage("heldout", seed=HELDOUT_SEED, n=HELDOUT_N)
    return staged_dev


def tree_hashes(*roots: pathlib.Path) -> dict[str, str]:
    out = {}
    for root in roots:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for p in files:
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def load(path):
    return json.loads(path.read_text())


# ----------------------------------------------------------------------------- the sampler

def test_refactored_sampler_reproduces_the_old_sample(lab_data, monkeypatch):
    monkeypatch.setattr(biodsa, "_check_study", fake_check)
    old_chosen, old_record = old_sample()
    new_chosen, new_record = biodsa.draw()
    pure = biodsa.sample(lab_data.rows, seed=biodsa.SEED, n=biodsa.N_TASKS, exclude=set(),
                         datasets={d["dataset_id"]: d for d in lab_data.datasets}, check=fake_check)
    assert len(old_chosen) == biodsa.N_TASKS == 40
    assert new_chosen == old_chosen and pure == old_chosen
    assert new_record == old_record
    # the synthetic rows make every rule bite, so the comparison covers each branch
    assert {"shares a study, publication or text with a chosen task", "hypothesis text contains its source's title",
            "archive missing", "the tables contain the source's identifiers"} <= set(old_record["skipped"])


def test_sampler_is_deterministic_and_seed_dependent(lab_data):
    datasets = {d["dataset_id"]: d for d in lab_data.datasets}
    ids = lambda seed: [c["row"]["sample_id"] for c in biodsa.sample(
        lab_data.rows, seed=seed, n=20, datasets=datasets, check=fake_check)]
    assert ids(5) == ids(5) and ids(5) != ids(6)


def test_sampler_skips_everything_in_the_exclude_set(lab_data):
    datasets = {d["dataset_id"]: d for d in lab_data.datasets}
    first = biodsa.sample(lab_data.rows, seed=1, n=20, datasets=datasets, check=fake_check)
    for kind in ("pair", "study", "publication", "text"):
        c = first[0]
        key = {"pair": biodsa.pair_id(c["row"]["sample_id"]), "study": c["source"]["study"],
               "publication": str(c["source"]["pmid"]), "text": biodsa._norm_text(c["row"]["hypothesis"])}[kind]
        tally = {}
        again = biodsa.sample(lab_data.rows, seed=1, n=20, exclude={key}, datasets=datasets, check=fake_check,
                              tally=tally)
        assert c["row"]["sample_id"] not in {x["row"]["sample_id"] for x in again}, kind
        assert tally["skipped"][biodsa.EXCLUDED_SKIP] >= 1


# ----------------------------------------------------------------------------- the held-out draw

def test_heldout_draw_shares_no_pair_study_publication_or_text_with_dev(staged_both):
    rows = staged_both.rows
    dev, held = load(biodsa.MAPPING), load(biodsa.MAPPING_HELDOUT)
    assert len(dev) == 40 and len(held) == HELDOUT_N

    def keys(mapping):
        pairs = {m["pair_id"] for m in mapping.values()}
        return {"pairs": pairs,
                "studies": {m["study"] for m in mapping.values()},
                "publications": {str(p) for m in mapping.values() for p in (m["pmid"], m["dataset_pmid"]) if p}
                | {biodsa._norm_text(t) for m in mapping.values() for t in (m["title"], m["dataset_title"]) if t},
                "texts": {biodsa._norm_text(r["hypothesis"]) for r in rows if biodsa.pair_id(r["sample_id"]) in pairs}}

    dev_keys, held_keys = keys(dev), keys(held)
    for kind in dev_keys:
        assert dev_keys[kind] and held_keys[kind]
        assert not dev_keys[kind] & held_keys[kind], kind
    # no held-out task text holds a dev hypothesis, and vice versa
    dev_tasks = [biodsa._norm_text(p.read_text()) for p in biodsa.PUBLIC.glob("*/task.md")]
    held_tasks = [biodsa._norm_text(p.read_text()) for p in biodsa.PUBLIC_HELDOUT.glob("*/task.md")]
    assert not any(t in s for t in dev_keys["texts"] for s in held_tasks)
    assert not any(t in s for t in held_keys["texts"] for s in dev_tasks)


def test_heldout_record_has_seed_n_revision_and_exclude_counts(staged_both):
    record = load(biodsa.SAMPLE_RECORD_HELDOUT)
    exclusions = biodsa.dev_exclusions(staged_both.rows)
    assert record["seed"] == HELDOUT_SEED and record["n_tasks"] == HELDOUT_N
    assert record["hf_revision"] == biodsa.HF_REVISION and record["split"] == "heldout"
    assert record["excluded"] == {kind: len(v) for kind, v in exclusions.items()}
    assert record["excluded"]["pairs"] == 40 and record["excluded"]["studies"] == 40
    assert record["skipped"][biodsa.EXCLUDED_SKIP] > 0
    assert sum(record["sample_labels"].values()) == HELDOUT_N
    assert record["targets"] == biodsa._targets(staged_both.rows, HELDOUT_N) == record["sample_labels"]


def test_heldout_ids_follow_a_hash_of_the_heldout_seed(staged_both):
    held = load(biodsa.MAPPING_HELDOUT)
    keys = [hashlib.sha256(f"{HELDOUT_SEED}:{m['sample_id']}".encode()).hexdigest() for m in held.values()]
    assert keys == sorted(keys)


def test_staging_heldout_leaves_the_dev_side_byte_identical(staged_dev):
    dev_paths = (biodsa.PUBLIC, biodsa.MAPPING, biodsa.SAMPLE_RECORD)
    dev_archives = [biodsa.ARCHIVES / f"{m['study']}.tar.gz" for m in load(biodsa.MAPPING).values()]
    before = tree_hashes(*dev_paths, *dev_archives)
    assert len(before) > 40 * 3
    biodsa.stage("heldout", seed=HELDOUT_SEED, n=HELDOUT_N)
    assert tree_hashes(*dev_paths, *dev_archives) == before
    biodsa.stage("heldout", seed=HELDOUT_SEED + 1, n=HELDOUT_N, force=True)    # a forced redraw too
    assert tree_hashes(*dev_paths, *dev_archives) == before
    assert biodsa.PUBLIC_HELDOUT.is_dir() and biodsa.MAPPING_HELDOUT.is_file()


def test_seed_is_required_for_heldout(staged_dev):
    with pytest.raises(ValueError, match="seed"):
        biodsa.stage("heldout")
    with pytest.raises(SystemExit) as err:
        biodsa.main(["stage", "--split", "heldout"])
    assert err.value.code == 2
    with pytest.raises(ValueError, match="seed other than"):
        biodsa.stage("heldout", seed=biodsa.SEED)
    with pytest.raises(SystemExit):
        biodsa.main(["stage", "--seed", "5"])     # the dev split takes no seed
    assert not biodsa.PUBLIC_HELDOUT.exists() and not biodsa.MAPPING_HELDOUT.exists()


def test_heldout_needs_the_dev_set_to_exclude(lab_data):
    with pytest.raises(RuntimeError, match="dev set is not staged"):
        biodsa.stage("heldout", seed=HELDOUT_SEED)
    assert not biodsa.PUBLIC_HELDOUT.exists()


def test_staging_refuses_existing_output_without_force(staged_both, capsys):
    before = tree_hashes(biodsa.PUBLIC_HELDOUT, biodsa.MAPPING_HELDOUT, biodsa.SAMPLE_RECORD_HELDOUT)
    with pytest.raises(FileExistsError):
        biodsa.stage("heldout", seed=HELDOUT_SEED + 5, n=HELDOUT_N)
    with pytest.raises(SystemExit) as err:
        biodsa.main(["stage", "--split", "heldout", "--seed", str(HELDOUT_SEED + 5)])
    assert "--force" in str(err.value.code)
    assert tree_hashes(biodsa.PUBLIC_HELDOUT, biodsa.MAPPING_HELDOUT, biodsa.SAMPLE_RECORD_HELDOUT) == before
    # one leftover file is enough to refuse
    biodsa.MAPPING_HELDOUT.unlink()
    with pytest.raises(FileExistsError):
        biodsa.stage("heldout", seed=HELDOUT_SEED, n=HELDOUT_N)
    # the dev split refuses too, so the dev set is never wiped by accident
    with pytest.raises(FileExistsError):
        biodsa.stage()
    biodsa.stage("heldout", seed=HELDOUT_SEED + 5, n=HELDOUT_N, force=True)
    assert load(biodsa.SAMPLE_RECORD_HELDOUT)["seed"] == HELDOUT_SEED + 5


def test_command_line_prints_counts_but_no_label_mix(staged_dev, capsys):
    biodsa.main(["stage", "--split", "heldout", "--seed", str(HELDOUT_SEED), "--n", str(HELDOUT_N)])
    out = capsys.readouterr().out
    assert f"staged {HELDOUT_N} tasks from {HELDOUT_N} studies" in out and "public-heldout" in out
    assert "40 pairs, 40 studies" in out
    lower = out.lower()
    assert not any(word in lower for word in ("true", "false", "verifiable", "sample_labels", "targets"))


# ----------------------------------------------------------------------------- the adapter

def test_dev_adapter_is_unchanged(staged_both):
    a = BioDSA()
    assert a.name == "biodsa-1k" and BioDSA.name == "biodsa-1k" and a.split == "dev"
    studies = a.studies()
    assert [s.id for s in studies] == [f"bd{i:02d}" for i in range(1, 41)]
    spec = a.modal_spec(studies[0])
    assert spec["volumes"] == [{"name": "summit-biodsa-data", "sub_path": "/bd01", "mount": "/biodsa-data"}]
    assert a.data_command(["bd01"]) == "python3 -m lab.adapters.biodsa upload"
    assert a.local_data_dir(studies[0]) == biodsa.PUBLIC / "bd01" / "data"


def test_heldout_adapter_names_ids_and_paths(staged_both):
    a = BioDSA(split="heldout")
    assert a.name == "biodsa-heldout" and a.modal_only
    studies = a.studies()
    assert [s.id for s in studies] == [f"bh{i:02d}" for i in range(1, HELDOUT_N + 1)]
    for s in studies:
        assert str(s.data_dir) == biodsa.MOUNT and s.deliverables == ["answer.json"]
        assert s.statement == (biodsa.PUBLIC_HELDOUT / s.id / "task.md").read_text()
        assert a.local_data_dir(s) == biodsa.PUBLIC_HELDOUT / s.id / "data"
        assert a.tables(s) and all(n.startswith("data_") for n in a.tables(s))
        assert a.schema(s) == {"label": "string"}
        spec = a.modal_spec(s)
        assert spec["volumes"] == [{"name": "summit-biodsa-data", "sub_path": f"/{s.id}", "mount": "/biodsa-data"}]
    assert a.data_command(["bh01"]) == "python3 -m lab.adapters.biodsa upload --split heldout"
    # grading reads the held-out mapping (synthetic labels)
    held = load(biodsa.MAPPING_HELDOUT)
    final = staged_both.root / "final"
    final.mkdir()
    label = biodsa.LABELS[held["bh01"]["label"]]
    (final / "answer.json").write_text(json.dumps({"answer": {"label": label}, "reasoning": "x"}))
    assert a.grade(studies[0], final)["passed"] is True
    with pytest.raises(ValueError):
        BioDSA(split="test")


def test_blocked_terms_merge_both_mappings(staged_dev):
    only_dev = BioDSA().blocked_terms()
    dev = load(biodsa.MAPPING)
    assert all(m["study"].lower() in only_dev for m in dev.values())
    biodsa.stage("heldout", seed=HELDOUT_SEED, n=HELDOUT_N)
    held = load(biodsa.MAPPING_HELDOUT)
    for split in ("dev", "heldout"):
        terms = BioDSA(split).blocked_terms()
        assert {"biodsa", "biodsa-1k", "2505.16100", "zifeng-ai"} <= set(terms)
        for m in [*dev.values(), *held.values()]:
            assert m["study"].lower() in terms and str(m["pmid"]).lower() in terms if m["pmid"] else True
            assert m["title"].lower() in terms
    assert BioDSA("dev").blocked_terms() == BioDSA("heldout").blocked_terms()
    assert set(only_dev) < set(BioDSA().blocked_terms())


def test_blocked_terms_with_only_the_heldout_mapping(staged_both):
    biodsa.MAPPING.unlink()
    terms = BioDSA("heldout").blocked_terms()
    assert all(m["study"].lower() in terms for m in load(biodsa.MAPPING_HELDOUT).values())


@pytest.mark.parametrize("values, expected", [
    ({"label": "Not verifiable"}, True), ({"label": "True"}, False), ({"label": "False"}, False),
    ({"answer": {"label": "Not verifiable"}, "reasoning": "r"}, True), ({"answer": {"label": "True"}}, False),
    ("Not verifiable", True), ("not verifiable", False), ({}, False), (None, False)])
def test_abstains_when_the_label_is_not_verifiable(values, expected):
    study = Study("bh01", "bh01", "s", pathlib.Path("/biodsa-data"), ["answer.json"])
    assert BioDSA("heldout").abstains(study, values) is expected
    assert BioDSA().abstains(study, values) is expected


def test_local_data_dir_points_at_each_splits_public_tables(lab_data):
    s = Study("bh07", "bh07", "s", pathlib.Path("/biodsa-data"), ["answer.json"])
    assert BioDSA("heldout").local_data_dir(s) == lab_data.public_heldout / "bh07" / "data"
    d = Study("bd07", "bd07", "s", pathlib.Path("/biodsa-data"), ["answer.json"])
    assert BioDSA().local_data_dir(d) == lab_data.public / "bd07" / "data"


def test_upload_heldout_puts_tables_under_bh_folders(staged_both, monkeypatch):
    put = []

    class Batch:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put_file(self, local, remote):
            put.append((pathlib.Path(local), remote))

    class Volume:
        @staticmethod
        def from_name(name, create_if_missing=False):
            assert name == "summit-biodsa-data"
            return Volume()

        def batch_upload(self, force=False):
            return Batch()

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(Volume=Volume))
    monkeypatch.setattr(biodsa, "volume_listing", lambda sub: {})
    ids = biodsa.upload(split="heldout")
    assert ids == [f"bh{i:02d}" for i in range(1, HELDOUT_N + 1)]
    assert put and all(remote.startswith("/bh") and local.is_relative_to(biodsa.PUBLIC_HELDOUT) for local, remote in put)
    put.clear()
    biodsa.main(["upload", "--split", "heldout", "bh01,bh02"])
    assert {remote.split("/")[1] for _, remote in put} == {"bh01", "bh02"}
