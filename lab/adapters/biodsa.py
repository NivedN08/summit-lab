"""BioDSA-1K: biomedical hypotheses checked against cancer-genomics data tables from cBioPortal.

Each task is one hypothesis taken from a published study and the data tables of one cBioPortal
study. The agent analyses the tables and decides whether the data support the hypothesis
("True"), contradict it ("False") or cannot settle it ("Not verifiable"). Most hypotheses come in
pairs built from one finding: the published claim and a reversed counter-claim, one True and one
False. The rest are "Not verifiable": a claim from another publication attached to an unrelated
dataset. Source: Hugging Face `zifeng-ai/BioDSA-1K` (ODbL), paper arXiv 2505.16100 (CC BY 4.0;
the label definitions in task.md follow its section 2.4). The tables are cBioPortal datahub
archives (ODbL), fetched from ARCHIVE_HOST: the dataset's own S3 links now answer 403, and the
archives there are today's versions, which may differ from the ones the benchmark was built on.

The download holds the labels, the published evidence, the source publication and a sample id
whose suffix names the label. So this adapter splits it:

  data/biodsa/sealed/   the Hugging Face files as downloaded (at HF_REVISION), the same rows as
                        JSON from the Hugging Face datasets server (the standard library cannot
                        read parquet), the cBioPortal archives of the sampled studies, the
                        neutral-id mapping and the sampling record. No agent may read it.
  data/biodsa/public/   bd01 ... bd40, each with task.md, answer_schema.json and data/ (the
                        study's `data_*` tables, unchanged). No labels, evidence, pair ids, real
                        ids or source names.

Sample: N_TASKS hypotheses drawn with SEED, at most one per true/false pair, per cBioPortal study,
per publication and per hypothesis text (so no agent and no director sees both halves of a pair
or two tasks from one source), skipping studies whose archive is missing, whose `data_*` tables
exceed MAX_TABLE_BYTES, whose tables contain the study's identifiers, or whose hypothesis text
contains its source's title. The label mix matches the full set's shares (largest remainder).

Two splits. "dev" is the sample above (bd01 ... bd40, in public/, summit_mapping.json,
summit_sample.json); it has been seen. "heldout" is a second sample drawn with its own seed under
the same rules, excluding every pair, study, publication and hypothesis text the dev set used:
bh01 ... bhNN in public-heldout/, with summit_mapping_heldout.json and summit_sample_heldout.json
in sealed/. Both splits share the Volume; the dev tasks sit at /bdNN and the held-out ones at /bhNN.
Staging refuses to overwrite a split that is already staged unless given --force, and staging the
held-out split never touches the dev side.

There is no grader code to run: `grade` is our own exact match on the label, with a "kind" that
says what sort of right or wrong the answer was ("accepted a false claim" and so on). The
paper's second score (evidence alignment) needs a judge model; we do not use it.

Agents run on Modal (`modal_spec`): each sandbox mounts its own task's folder of the Volume
`summit-biodsa-data` read-only at MOUNT.

    python3 -m lab.adapters.biodsa stage     # download, sample and build the public side
    python3 -m lab.adapters.biodsa upload    # put the public tables on the Modal Volume
    python3 -m lab.adapters.biodsa stage --split heldout --seed S [--n N] [--force]
    python3 -m lab.adapters.biodsa upload --split heldout
"""

from __future__ import annotations

import collections
import hashlib
import io
import json
import pathlib
import random
import re
import shutil
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request

from lab.contracts import Study

ROOT = pathlib.Path(__file__).resolve().parents[2]
SEALED = ROOT / "data" / "biodsa" / "sealed"
PUBLIC = ROOT / "data" / "biodsa" / "public"
HF_DIR = SEALED / "hf"
ARCHIVES = SEALED / "cbioportal"                    # <study id>.tar.gz, as downloaded
MAPPING = SEALED / "summit_mapping.json"           # neutral id -> label, real ids, source; sealed
SAMPLE_RECORD = SEALED / "summit_sample.json"      # seed, label counts, every candidate skipped and why
PUBLIC_HELDOUT = ROOT / "data" / "biodsa" / "public-heldout"
MAPPING_HELDOUT = SEALED / "summit_mapping_heldout.json"
SAMPLE_RECORD_HELDOUT = SEALED / "summit_sample_heldout.json"
SPLITS = ("dev", "heldout")
ID_PREFIX = {"dev": "bd", "heldout": "bh"}

HF_REPO = "zifeng-ai/BioDSA-1K"
HF_REVISION = "7acd6e60be5feec3f202daa8823db33aa75fd313"   # the dataset's commit on 29 July 2025
HF_FILES = ["biodsa_1k_hypothesis.parquet", "cbioportal_dataset_url.parquet", "README.md"]
ROWS_API = "https://datasets-server.huggingface.co/rows"
ARCHIVE_HOST = "https://datahub.assets.cbioportal.org"    # cBioPortal datahub archives, <study id>.tar.gz
ROW_SETS = {"hypotheses": "0_hypothesis", "datasets": "2_cbioportal_data"}   # file stem -> HF config

SEED = 20261114
N_TASKS = 40
MAX_TABLE_BYTES = 300_000_000
LABELS = {"true": "True", "false": "False", "non-verifiable": "Not verifiable"}   # sealed value -> answer
ANSWER_LABELS = tuple(LABELS.values())
DELIVERABLE = "answer.json"

VOLUME = "summit-biodsa-data"
MOUNT = "/biodsa-data"
AGENT_PACKAGES = ["lifelines==0.30.0"]      # survival analysis; the base image has numpy, pandas, scipy, statsmodels
AGENT_CPU, AGENT_MEMORY_MB = 2.0, 4096
LARGE_TABLES_BYTES, LARGE_MEMORY_MB = 100_000_000, 8192   # more memory when the tables are this big
BENCHMARK_TERMS = ["biodsa", "biodsa-1k", "biodsa 1k", "biodsa1k", "biodsa_1k", "2505.16100", "zifeng-ai",
                   "keiji-ai", "keiji.ai", "biodsa.github.io", "ryanwangzf.github.io", "biodsbench"]

TASK_TEXT = """\
# Hypothesis

{hypothesis}

# Your task

You are given the data tables of one cancer-genomics study (listed below). Analyse them, with code
you write and run, and decide whether the data support the hypothesis, contradict it, or cannot
settle it. The hypothesis is stated as a definite claim; do not assume it is true.

Choose exactly one label:

- "True": analysis of the data supports the hypothesis.
- "False": you can identify the relevant variables in the data, and analysing them gives evidence
  that contradicts the hypothesis.
- "Not verifiable": the data hold no relevant variables or tables with which to support or reject
  the hypothesis. For example, a claim that one group of tumours has a higher mutational burden
  than another is Not verifiable if the data have no mutational-burden variable.

# The data

Tab-separated text files. In the clinical tables, lines starting with `#` describe the columns;
the first line that does not start with `#` is the header.

{tables}

# Your answer

Write `answer.json` in your working folder, exactly in this form:

{{"answer": {{"label": "<label>"}}, "reasoning": "what you analysed and what it showed"}}

where `<label>` is exactly one of "True", "False" or "Not verifiable".
"""


# ----------------------------------------------------------------------------- download

def _get(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "summit-lab"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download() -> None:
    """The Hugging Face files at HF_REVISION and the same rows as JSON. Skips what is already there."""
    HF_DIR.mkdir(parents=True, exist_ok=True)
    info = json.loads(_get(f"https://huggingface.co/api/datasets/{HF_REPO}"))
    for name in HF_FILES:
        if not (HF_DIR / name).exists():
            (HF_DIR / name).write_bytes(_get(f"https://huggingface.co/datasets/{HF_REPO}/resolve/{HF_REVISION}/{name}"))
    for stem, config in ROW_SETS.items():
        target = HF_DIR / f"{stem}.rows.json"
        if target.exists():
            continue
        if info.get("sha") != HF_REVISION:   # the datasets server serves the current revision only
            raise RuntimeError(f"{HF_REPO} is now at {info.get('sha')}, not {HF_REVISION}; check before restaging")
        rows, total, offset = [], None, 0
        while total is None or offset < total:
            query = urllib.parse.urlencode({"dataset": HF_REPO, "config": config, "split": "test",
                                            "offset": offset, "length": 100})
            page = json.loads(_get(f"{ROWS_API}?{query}"))
            total = page["num_rows_total"]
            if page.get("partial") or any(r.get("truncated_cells") for r in page["rows"]):
                raise RuntimeError(f"the datasets server returned partial rows for {config}")
            rows += [r["row"] for r in page["rows"]]
            offset += len(page["rows"])
        if len(rows) != total:
            raise RuntimeError(f"{config}: got {len(rows)} rows of {total}")
        target.write_text(json.dumps({"revision": HF_REVISION, "config": config, "rows": rows}) + "\n")


def load_rows(stem: str) -> list[dict]:
    return json.loads((HF_DIR / f"{stem}.rows.json").read_text())["rows"]


def pair_id(sample_id: str) -> str:
    """The id shared by both halves of a pair: the sample id without its last part (the label)."""
    return sample_id.rsplit("_", 1)[0]


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def split_paths(split: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """(public folder, sealed mapping, sealed sample record) of one split. Read at call time, so
    the module constants can be swapped in tests."""
    if split == "dev":
        return PUBLIC, MAPPING, SAMPLE_RECORD
    if split == "heldout":
        return PUBLIC_HELDOUT, MAPPING_HELDOUT, SAMPLE_RECORD_HELDOUT
    raise ValueError(f"unknown split {split!r}; known: {', '.join(SPLITS)}")


# ----------------------------------------------------------------------------- the study archives

def _archive_size(url: str) -> int | None:
    """Size in bytes of a datahub archive, or None when it is not there."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "summit-lab"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.headers["Content-Length"])
    except (urllib.error.HTTPError, urllib.error.URLError, TypeError, ValueError):
        return None


def _fetch_archive(study: str, url: str, size: int) -> pathlib.Path:
    path = ARCHIVES / f"{study}.tar.gz"
    if path.is_file() and path.stat().st_size == size:
        return path
    ARCHIVES.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "summit-lab"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, 8 * 1024 * 1024)
    if partial.stat().st_size != size:
        raise RuntimeError(f"{study}: downloaded {partial.stat().st_size} bytes, expected {size}")
    partial.rename(path)
    return path


def _data_members(archive: pathlib.Path) -> list[tarfile.TarInfo]:
    """The archive's data tables: regular files whose name starts with `data_`."""
    with tarfile.open(archive, "r:gz") as tar:
        return [m for m in tar.getmembers() if m.isreg() and pathlib.PurePosixPath(m.name).name.startswith("data_")]


def _meta_study(archive: pathlib.Path) -> dict[str, str]:
    """name, citation and pmid from the archive's meta_study.txt (sealed; used only for blocking)."""
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            if m.isreg() and pathlib.PurePosixPath(m.name).name == "meta_study.txt":
                text = tar.extractfile(m).read().decode("utf-8", "replace")
                fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
                return {k.strip(): v.strip() for k, v in fields.items() if k.strip() in ("name", "citation", "pmid")}
    return {}


def _needles(source: dict) -> tuple[list[bytes], list[re.Pattern]]:
    """What identifies a task's source, as lowercase byte strings and PMID patterns."""
    texts = [source["study"], source.get("title"), source.get("dataset_title"),
             source.get("meta", {}).get("name"), source.get("meta", {}).get("citation")]
    strings = [t.lower().encode() for t in texts if t and len(t) >= 8]
    pmids = {p for p in [source.get("pmid"), source.get("dataset_pmid"), *re.findall(r"\d{6,9}",
             source.get("meta", {}).get("pmid", ""))] if p}
    return strings, [re.compile(rb"(?<!\d)" + p.encode() + rb"(?!\d)") for p in sorted(pmids)]


def _found_in(data, strings: list[bytes], patterns: list[re.Pattern], chunk: int = 8 * 1024 * 1024) -> bool:
    """Whether a binary stream contains any of the identifiers (case-insensitive)."""
    overlap = max([len(s) for s in strings] + [12])
    tail = b""
    while True:
        block = data.read(chunk)
        if not block:
            return False
        window = (tail + block).lower()
        if any(s in window for s in strings) or any(p.search(window) for p in patterns):
            return True
        tail = window[-overlap:]


# ----------------------------------------------------------------------------- sampling

def _targets(rows: list[dict], n: int) -> dict[str, int]:
    """Tasks per label, in the full set's proportions (largest remainder)."""
    counts = collections.Counter(r["hypothesis_label"] for r in rows)
    exact = {k: n * v / len(rows) for k, v in counts.items()}
    out = {k: int(v) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - out[k], reverse=True)[:n - sum(out.values())]:
        out[k] += 1
    return out


def archive_url(dataset_url: str) -> str:
    """The dataset's archive link on cBioPortal's current host (its S3 links now answer 403)."""
    return ARCHIVE_HOST + "/" + dataset_url.rsplit("/", 1)[-1]


def _source(row: dict, datasets: dict[str, dict]) -> dict:
    dataset = datasets[row["cBioPortal_ID"]]
    return {"study": row["cBioPortal_ID"], "url": archive_url(dataset["dataset_url"]),
            "study_page": row["cBioPortal_data_url"], "pmid": row["PMID"],
            "title": row["Title"], "dataset_pmid": dataset.get("PMID"), "dataset_title": dataset.get("Title"),
            "publication_url": dataset.get("publication_url")}


def _check_study(source: dict) -> tuple[str | None, dict]:
    """Download one study's archive and check its tables. Returns (reason to skip or None, facts)."""
    size = _archive_size(source["url"])
    if size is None:
        return "archive missing", {}
    if size > MAX_TABLE_BYTES:
        return "archive over the size limit", {"archive_bytes": size}
    archive = _fetch_archive(source["study"], source["url"], size)
    members = _data_members(archive)
    names = [pathlib.PurePosixPath(m.name).name for m in members]
    facts = {"archive_bytes": size, "table_bytes": sum(m.size for m in members), "tables": sorted(names),
             "meta": _meta_study(archive)}
    if not members:
        return "no data tables", facts
    if len(set(names)) != len(names):
        return "two tables with one name", facts
    if facts["table_bytes"] > MAX_TABLE_BYTES:
        return "tables over the size limit", facts
    strings, patterns = _needles({**source, "meta": facts["meta"]})
    if any(_found_in(io.BytesIO(n.encode()), strings, patterns) for n in names):
        return "a table name names the source", facts
    with tarfile.open(archive, "r:gz") as tar:
        for m in members:
            if _found_in(tar.extractfile(m), strings, patterns):
                return "the tables contain the source's identifiers", facts
    return None, facts


EXCLUDED_SKIP = "already used by the dev set"


def _groups(rows: list[dict]) -> dict[str, list[dict]]:
    """Rows by pair id (a true/false pair, or a single row)."""
    groups = collections.defaultdict(list)
    for r in rows:
        groups[pair_id(r["sample_id"])].append(r)
    return groups


def _is_excluded(pid: str, group: list[dict], source: dict, exclude) -> bool:
    """Whether a candidate shares a pair, study, publication (PubMed id or normalized title) or
    hypothesis text with the excluded set. PubMed ids are compared as strings."""
    if not exclude:
        return False
    keys = {pid, source["study"], *(str(p) for p in (source["pmid"], source["dataset_pmid"]) if p is not None),
            *(_norm_text(t) for t in (source["title"], source["dataset_title"]) if t),
            *(_norm_text(r["hypothesis"]) for r in group)}
    return not keys.isdisjoint(exclude)


def sample(rows: list[dict], *, seed: int, n: int, exclude=frozenset(), datasets: dict[str, dict],
           check, tally: dict | None = None) -> list[dict]:
    """Draw n tasks from rows with seed. Returns the chosen rows with their source facts.

    No I/O of its own: `check(source) -> (reason to skip or None, facts)` judges a study (the real
    one downloads and reads its archive) and is called at most once per study. `exclude` is a set
    of pair ids, cBioPortal study ids, PubMed ids (strings), normalized publication titles and
    normalized hypothesis texts; a candidate that shares any of them is skipped. With exclude
    empty this is the original draw, step for step. When given, `tally` receives the skip counts
    ("skipped"), each study's check result ("studies_checked") and the label targets ("targets")."""
    exclude = frozenset(exclude)
    targets = _targets(rows, n)
    groups = _groups(rows)
    order = sorted(groups)
    rng = random.Random(seed)
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
        source = _source(row, datasets)
        pmids = {source["pmid"], source["dataset_pmid"]} - {None}
        if _is_excluded(pid, groups[pid], source, exclude):
            skipped[EXCLUDED_SKIP] += 1
            continue
        if source["study"] in used_studies or pmids & used_pmids or any(
                _norm_text(r["hypothesis"]) in used_texts for r in groups[pid]):
            skipped["shares a study, publication or text with a chosen task"] += 1
            continue
        if any(t and _norm_text(t)[:40] in _norm_text(row["hypothesis"])
               for t in (source["title"], source["dataset_title"])):
            skipped["hypothesis text contains its source's title"] += 1
            continue
        if source["study"] not in studies_checked:
            studies_checked[source["study"]] = check(source)
        reason, facts = studies_checked[source["study"]]
        if reason:
            skipped[reason] += 1
            continue
        need[row["hypothesis_label"]] -= 1
        used_studies.add(source["study"])
        used_pmids |= pmids
        used_texts |= {_norm_text(r["hypothesis"]) for r in groups[pid]}
        chosen.append({"row": row, "source": {**source, **facts}})
    if tally is not None:
        tally.update(skipped=dict(skipped), studies_checked=dict(studies_checked), targets=targets)
    if any(need.values()):   # the count only: what is still needed per label would hint at the draw
        raise RuntimeError(f"could not fill the sample; {sum(need.values())} tasks short")
    return chosen


def _checked_study(source: dict) -> tuple[str | None, dict]:
    """_check_study, and drop the archive of a study that fails (keep only what the sample needs)."""
    reason, facts = _check_study(source)
    if reason:
        (ARCHIVES / f"{source['study']}.tar.gz").unlink(missing_ok=True)
    return reason, facts


def draw(seed: int = SEED, n: int = N_TASKS, exclude=frozenset()) -> tuple[list[dict], dict]:
    """Draw the tasks from the downloaded rows, checking each study's archive. Returns (chosen rows
    with their source facts, the sampling record)."""
    rows = load_rows("hypotheses")
    datasets = {d["dataset_id"]: d for d in load_rows("datasets")}
    tally: dict = {}
    chosen = sample(rows, seed=seed, n=n, exclude=exclude, datasets=datasets, check=_checked_study, tally=tally)
    groups = _groups(rows)
    full = collections.Counter(r["hypothesis_label"] for r in rows)
    record = {"seed": seed, "n_tasks": n, "hf_revision": HF_REVISION,
              "full_set": {"rows": len(rows), "pairs": sum(len(g) == 2 for g in groups.values()),
                           "single": sum(len(g) == 1 for g in groups.values()), "labels": dict(full)},
              "sample_labels": dict(collections.Counter(c["row"]["hypothesis_label"] for c in chosen)),
              "targets": tally["targets"], "skipped": tally["skipped"],
              "studies_skipped": {s: r for s, (r, _) in tally["studies_checked"].items() if r}}
    return chosen, record


def dev_exclusions(rows: list[dict]) -> dict[str, set[str]]:
    """What the dev set used, by kind, from the sealed dev mapping and sample record:
      pairs         every dev pair id (so neither half of a dev pair is drawn again)
      studies       every dev cBioPortal study id
      publications  the PubMed ids of each dev task's publication, its dataset's publication and
                    the study's meta_study.txt; and the normalized titles of both publications
      texts         the normalized hypothesis text of every row in a dev pair (both halves)
    The dev sample record holds no pair, study, publication or text of a chosen task (only counts,
    and studies skipped for archive problems, which no agent saw), so it adds nothing; it is
    checked to be the record of the same Hugging Face revision."""
    if not (MAPPING.exists() and SAMPLE_RECORD.exists()):
        raise RuntimeError("the dev set is not staged: its sealed mapping and sample record are needed to exclude it")
    record = json.loads(SAMPLE_RECORD.read_text())
    if record.get("hf_revision") not in (None, HF_REVISION):
        raise RuntimeError("the dev set was drawn from another revision of the dataset; check before drawing")
    mapping = json.loads(MAPPING.read_text())
    pairs = {m.get("pair_id") or pair_id(m["sample_id"]) for m in mapping.values()}
    pairs |= {pair_id(m["sample_id"]) for m in mapping.values() if m.get("sample_id")}
    studies = {m["study"] for m in mapping.values()}
    publications = set()
    for m in mapping.values():
        meta = m.get("meta") or {}
        publications |= {str(p) for p in (m.get("pmid"), m.get("dataset_pmid")) if p is not None}
        publications |= set(re.findall(r"\d{6,9}", str(meta.get("pmid") or "")))
        publications |= {_norm_text(t) for t in (m.get("title"), m.get("dataset_title")) if t}
    texts = {_norm_text(r["hypothesis"]) for r in rows if pair_id(r["sample_id"]) in pairs}
    return {"pairs": pairs, "studies": studies, "publications": publications, "texts": texts}


# ----------------------------------------------------------------------------- staging

def _size_text(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 100_000 else f"{n / 1e3:.1f} kB"


def task_text(hypothesis: str, tables: dict[str, int]) -> str:
    """task.md for one hypothesis and its tables {file name: bytes}."""
    listing = "\n".join(f"- `{name}` ({_size_text(size)})" for name, size in sorted(tables.items()))
    return TASK_TEXT.format(hypothesis=hypothesis.strip(), tables=listing)


def stage(split: str = "dev", seed: int | None = None, n: int | None = None, force: bool = False) -> list[str]:
    """Download, sample and build the public side of one split. Returns the neutral ids.

    dev: SEED and N_TASKS (no others), ids bd01 ... The held-out split needs its own seed, excludes
    everything the dev set used (dev_exclusions) and never writes to the dev side. Either split
    refuses to overwrite what is already staged unless force is given."""
    public, mapping_path, record_path = split_paths(split)
    if split == "dev":
        if seed not in (None, SEED) or n not in (None, N_TASKS):
            raise ValueError(f"the dev split is fixed: seed {SEED}, {N_TASKS} tasks")
        seed, n = SEED, N_TASKS
    else:
        if seed is None:
            raise ValueError("the held-out split needs --seed (pre-register it before the draw)")
        if seed == SEED:
            raise ValueError("the held-out split needs a seed other than the dev seed")
        n = N_TASKS if n is None else n
        dev_public, dev_mapping, dev_record = split_paths("dev")
        clash = {public.resolve(), mapping_path.resolve(), record_path.resolve()} & {
            dev_public.resolve(), dev_mapping.resolve(), dev_record.resolve()}
        if clash or public.resolve().is_relative_to(dev_public.resolve()):
            raise RuntimeError("the held-out paths overlap the dev paths; refusing")
        if not (dev_mapping.exists() and dev_record.exists()):
            raise RuntimeError("the dev set is not staged: its sealed mapping and sample record are needed to exclude it")
    if n < 1:
        raise ValueError("n must be at least 1")
    present = [p for p in (public, mapping_path, record_path) if p.exists()]
    if present and not force:
        raise FileExistsError(f"the {split} split is already staged ({', '.join(p.name for p in present)}); "
                              "pass --force to draw it again")
    download()
    if split == "dev":
        chosen, record = draw(seed, n)
    else:
        exclusions = dev_exclusions(load_rows("hypotheses"))
        chosen, record = draw(seed, n, set().union(*exclusions.values()))
        record["split"] = split
        record["excluded"] = {kind: len(items) for kind, items in exclusions.items()}
    chosen.sort(key=lambda c: hashlib.sha256(f"{seed}:{c['row']['sample_id']}".encode()).hexdigest())
    if public.exists():
        shutil.rmtree(public)
    prefix, width = ID_PREFIX[split], max(2, len(str(len(chosen))))
    mapping = {}
    for i, c in enumerate(chosen, 1):
        neutral, row, source = f"{prefix}{i:0{width}d}", c["row"], c["source"]
        data = public / neutral / "data"
        data.mkdir(parents=True)
        tables = {}
        with tarfile.open(ARCHIVES / f"{source['study']}.tar.gz", "r:gz") as tar:
            for m in _data_members(ARCHIVES / f"{source['study']}.tar.gz"):
                name = pathlib.PurePosixPath(m.name).name
                with tar.extractfile(m.name) as src, (data / name).open("wb") as out:
                    shutil.copyfileobj(src, out, 8 * 1024 * 1024)
                tables[name] = m.size
        (public / neutral / "task.md").write_text(task_text(row["hypothesis"], tables))
        (public / neutral / "answer_schema.json").write_text(json.dumps({"label": "string"}, indent=2) + "\n")
        mapping[neutral] = {"sample_id": row["sample_id"], "pair_id": pair_id(row["sample_id"]),
                            "label": row["hypothesis_label"], "publication_class": row["publication_class"],
                            **source}
    mapping_path.write_text(json.dumps(mapping, indent=2) + "\n")
    record["total_table_bytes"] = sum(m["table_bytes"] for m in mapping.values())
    if split == "dev":
        record["archives_bytes"] = sum(p.stat().st_size for p in ARCHIVES.glob("*.tar.gz"))
    else:   # the archives folder also holds the dev set's archives
        record["archives_bytes"] = sum((ARCHIVES / f"{m['study']}.tar.gz").stat().st_size for m in mapping.values())
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    return list(mapping)


# ----------------------------------------------------------------------------- the Modal Volume

def upload(study_ids: list[str] | None = None, split: str = "dev") -> list[str]:
    """Put each task's public tables in its own folder of the Volume (/bd01/... for dev, /bh01/...
    for the held-out split). Files already there with the right size are skipped. Returns the ids
    uploaded to."""
    public = split_paths(split)[0]
    if not public.exists():
        raise RuntimeError(f"the {split} split is not staged")
    import modal
    volume = modal.Volume.from_name(VOLUME, create_if_missing=True)
    done = []
    for folder in sorted(public.iterdir()):
        if not (folder / "data").is_dir() or (study_ids and folder.name not in study_ids):
            continue
        present = volume_listing(f"/{folder.name}")
        todo = [f for f in sorted((folder / "data").iterdir()) if present.get(f.name) != f.stat().st_size]
        if todo:
            with volume.batch_upload(force=True) as batch:
                for f in todo:
                    batch.put_file(f, f"/{folder.name}/{f.name}")
        done.append(folder.name)
    return done


def volume_listing(sub_path: str) -> dict[str, int]:
    """File name -> size in bytes for one folder of the Volume; {} if there is none."""
    import time
    import modal
    from modal.volume import FileEntryType
    entries = None
    for wait in (0, 5, 15, 30, 60):   # Modal limits how often a Volume may be listed; wait and retry
        time.sleep(wait)
        try:
            entries = modal.Volume.from_name(VOLUME).listdir(sub_path)
            break
        except (modal.exception.NotFoundError, FileNotFoundError):
            return {}
        except modal.exception.ResourceExhaustedError:
            continue
    if entries is None:
        raise RuntimeError(f"Modal kept refusing to list {VOLUME}:{sub_path} (rate limit)")
    return {pathlib.PurePosixPath(e.path).name: e.size for e in entries if e.type == FileEntryType.FILE}


# ----------------------------------------------------------------------------- answers

def _load_answer(path: pathlib.Path):
    """Return (answer dict, problem list). The form is {"answer": {"label": ...}, "reasoning": ...}."""
    try:
        doc = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as err:
        return None, [f"cannot read JSON: {err}"]
    if not isinstance(doc, dict) or not isinstance(doc.get("answer"), dict):
        return None, ['top level must be an object with an "answer" object inside']
    return doc["answer"], []


def outcome_kind(given: str, expected: str) -> str:
    """What sort of right or wrong an answer label is, against the expected label."""
    if given == expected:
        return "correct"
    if expected == "Not verifiable":
        return "judged an unverifiable claim settled"
    if given == "Not verifiable":
        return "judged a settled claim not verifiable"
    return "accepted a false claim" if given == "True" else "rejected a true claim"


# ----------------------------------------------------------------------------- the adapter

class BioDSA:
    name = "biodsa-1k"
    modal_only = True     # the tables are mounted from the Modal Volume at MOUNT

    def __init__(self, split: str = "dev"):
        split_paths(split)    # an unknown split fails here
        self.split = split
        if split == "heldout":
            self.name = "biodsa-heldout"

    @property
    def public(self) -> pathlib.Path:
        return split_paths(self.split)[0]

    @property
    def mapping_path(self) -> pathlib.Path:
        return split_paths(self.split)[1]

    def studies(self) -> list[Study]:
        """Each study's data_dir is where its agents see the tables inside their sandbox."""
        public = self.public
        if not public.exists():
            command = "stage" if self.split == "dev" else "stage --split heldout --seed S"
            raise RuntimeError(f"run `python3 -m lab.adapters.biodsa {command}` first")
        return [Study(id=d.name, title=d.name, statement=(d / "task.md").read_text(), data_dir=pathlib.Path(MOUNT),
                      deliverables=[DELIVERABLE])
                for d in sorted(public.iterdir()) if (d / "task.md").exists()]

    def local_data_dir(self, study: Study) -> pathlib.Path:
        """The study's public tables on this machine (agents see them at MOUNT instead)."""
        return self.public / study.id / "data"

    def tables(self, study: Study) -> dict[str, int]:
        """The study's public tables: file name -> bytes."""
        return {f.name: f.stat().st_size for f in sorted(self.local_data_dir(study).iterdir()) if f.is_file()}

    def modal_spec(self, study: Study) -> dict:
        """The sandbox for this study's agents (see lab/backend_modal.py). The package index stays
        reachable: no public package we found holds this benchmark's labels."""
        big = sum(self.tables(study).values()) > LARGE_TABLES_BYTES
        return {"packages": list(AGENT_PACKAGES), "cpu": AGENT_CPU,
                "memory_mb": LARGE_MEMORY_MB if big else AGENT_MEMORY_MB,
                "volumes": [{"name": VOLUME, "sub_path": f"/{study.id}", "mount": MOUNT}],
                "package_index": True}

    def missing_data(self, studies: list[Study], listing=None) -> list[str]:
        """Ids of the studies whose tables are not all on the Volume with the right size."""
        listing = listing or volume_listing
        missing = []
        for s in studies:
            present = listing(f"/{s.id}")     # one listing per task: Modal rate-limits listings
            if any(present.get(name) != size for name, size in self.tables(s).items()):
                missing.append(s.id)
        return missing

    def data_command(self, study_ids: list[str]) -> str:
        return "python3 -m lab.adapters.biodsa upload" + ("" if self.split == "dev" else f" --split {self.split}")

    def blocked_terms(self) -> list[str]:
        """The benchmark's names, and for each sampled task of either split (whichever mappings
        exist) what identifies its source: the cBioPortal study id, the publications' PubMed ids,
        titles and links, and the study's name and citation as cBioPortal gives them."""
        terms = list(BENCHMARK_TERMS)
        for path in dict.fromkeys(split_paths(s)[1] for s in SPLITS):
            if not path.exists():
                continue
            for m in json.loads(path.read_text()).values():
                meta = m.get("meta") or {}
                terms += [m["study"], m.get("url"), m.get("study_page"), m.get("pmid"), m.get("dataset_pmid"), m.get("title"),
                          m.get("dataset_title"), m.get("publication_url"), meta.get("name"), meta.get("citation"),
                          *re.findall(r"\d{6,9}", meta.get("pmid") or "")]
        return list(dict.fromkeys(str(t).lower() for t in terms if t))

    def schema(self, study: Study) -> dict:
        return json.loads((self.public / study.id / "answer_schema.json").read_text())

    @staticmethod
    def abstains(study: Study, values) -> bool:
        """Whether an answer abstains: its label is "Not verifiable". `values` is the answer's
        fields ({"label": ...}), the whole answer file ({"answer": {...}}) or the label itself."""
        if isinstance(values, dict):
            values = values["answer"] if isinstance(values.get("answer"), dict) else values
            values = values.get("label")
        return values == "Not verifiable"

    def problems(self, study: Study, deliverable: str, candidate: pathlib.Path) -> list[str]:
        answer, found = _load_answer(candidate)
        if answer is None:
            return found
        if "label" not in answer:
            found.append('missing answer field "label"')
        elif answer["label"] not in ANSWER_LABELS:
            found.append('"label" must be exactly one of "True", "False" or "Not verifiable"')
        found += [f'unexpected answer field "{k}"' for k in answer if k != "label"]
        return found

    def field_agreement(self, study: Study, a: pathlib.Path, b: pathlib.Path) -> dict[str, bool]:
        """Two candidates agree when their labels are equal (both valid)."""
        answer_a, _ = _load_answer(a)
        answer_b, _ = _load_answer(b)
        la, lb = (answer_a or {}).get("label"), (answer_b or {}).get("label")
        return {"label": la in ANSWER_LABELS and la == lb}

    def agreement(self, study: Study, deliverable: str, a: pathlib.Path, b: pathlib.Path) -> float:
        return float(self.field_agreement(study, a, b)["label"])

    def grade(self, study: Study, final_dir: pathlib.Path) -> dict:
        """Our own exact match against the sealed label. Call only after every agent has exited.
        A missing or malformed answer is a fail. "kind" says what sort of right or wrong it was
        (outcome_kind), in words that never name the label itself."""
        candidate = pathlib.Path(final_dir) / DELIVERABLE
        answer, found = _load_answer(candidate)
        if answer is None or self.problems(study, DELIVERABLE, candidate):
            return {"passed": False, "score": 0.0, "error": (found or self.problems(study, DELIVERABLE, candidate))[0]}
        expected = LABELS[json.loads(self.mapping_path.read_text())[study.id]["label"]]
        passed = answer["label"] == expected
        return {"passed": passed, "score": 1.0 if passed else 0.0, "kind": outcome_kind(answer["label"], expected)}


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(prog="python3 -m lab.adapters.biodsa")
    commands = parser.add_subparsers(dest="command", required=True)
    st = commands.add_parser("stage", help="download, sample and build the public side of one split")
    st.add_argument("--split", choices=SPLITS, default="dev")
    st.add_argument("--seed", type=int, help="the held-out split's pre-registered seed (required for it)")
    st.add_argument("--n", type=int, help=f"tasks to draw for the held-out split (default {N_TASKS})")
    st.add_argument("--force", action="store_true", help="draw again over a split that is already staged")
    up = commands.add_parser("upload", help="put one split's public tables on the Modal Volume")
    up.add_argument("--split", choices=SPLITS, default="dev")
    up.add_argument("ids", nargs="?", help="comma-separated task ids, for example bd01,bd02 (default all)")
    args = parser.parse_args(argv)
    if args.command == "stage":
        if args.split == "heldout" and args.seed is None:
            parser.error("--seed is required for --split heldout")
        if args.split == "dev" and (args.seed is not None or args.n is not None):
            parser.error(f"the dev split is fixed (seed {SEED}, {N_TASKS} tasks); --seed and --n are for --split heldout")
        try:
            ids = stage(args.split, seed=args.seed, n=args.n, force=args.force)
        except (FileExistsError, ValueError) as err:
            sys.exit(str(err))
        public, mapping_path, record_path = split_paths(args.split)
        record = json.loads(record_path.read_text())
        studies = {m["study"] for m in json.loads(mapping_path.read_text()).values()}
        text = (f"staged {len(ids)} tasks from {len(studies)} studies under {public.relative_to(ROOT)}; "
                f"tables {record['total_table_bytes'] / 1e6:.0f} MB, archives {record['archives_bytes'] / 1e6:.0f} MB")
        if args.split == "heldout":   # counts only, never the label mix
            excluded = record["excluded"]
            text += (f"; excluded from the dev set: {excluded['pairs']} pairs, {excluded['studies']} studies, "
                     f"{excluded['publications']} publication ids and titles, {excluded['texts']} hypothesis texts; "
                     f"{record['skipped'].get(EXCLUDED_SKIP, 0)} candidates skipped as dev")
        print(text + " (label counts are in the sealed sampling record)")
    else:
        ids = upload(args.ids.split(",") if args.ids else None, split=args.split)
        print(f"tables on the Volume {VOLUME} for {len(ids)} tasks")


if __name__ == "__main__":
    main()
