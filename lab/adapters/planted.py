"""Planted-fault suite: synthetic cohort tables with answer keys we control.

Each task is one hypothesis about a small synthetic cohort ("<group> have a higher <outcome> than
<other group>") with three tab-separated tables, in the same answer format as BioDSA-1K: "True",
"False" or "Not verifiable". Half the tasks carry one planted data fault that changes the correct
answer, or makes the obvious analysis give the wrong one. Each faulted task has a clean twin: the
same family, the same intended label and the same design, without the fault. Twins differ in their
random draw and in their variable names, so nothing in the public files pairs them.

The five fault families, and how a careful analyst would catch each one from the data alone:

  hidden_variable        the variable the hypothesis names is not in the patient or sample table;
                         listing every table shows it in `data_clinical_supplement.txt`.
  duplicated_samples     some patients appear twice under new ids; rows that repeat every measured
                         value exactly are easy to find and keep once.
  batch_confounder       the outcome shifts strongly by processing batch and group membership
                         differs by batch; comparing groups within batch removes the effect.
  unit_mismatch          one site's outcome values sit a standard conversion factor away from the
                         other sites' (and from the unit the column description states).
  misleading_dictionary  the column description gives the 0/1 coding the wrong way round; the
                         companion column (pack-years, dose, allele fraction ...) is 0 exactly for
                         the absent state and shows the true coding.

The truth is not asserted: `reference_answer` runs one careful analysis, the same for every task
and blind to the family (read every table, drop exact repeat rows, check the coding against the
companion column, convert a site whose values sit a known unit factor away, compare groups within
processing batch), and the generator keeps a draw only when that analysis gives the intended label
clearly and the fault's footprint is what was planted. `naive_answer` is the careless analysis
(patient and sample tables only, trust the description, pool everything); the generator also
requires it to be clearly wrong on each faulted task and clearly right on each clean twin.

Layout (git-ignored):

  data/planted/public/pfNN/   task.md, answer_schema.json, data/*.txt. No family, fault, twin,
                              split or label.
  data/planted/sealed/        mapping.json (per task: family, faulted, twin, label, split, the
                              analysis spec and the planted fault) and generation.json (seed and
                              parameters).

Agents run on Modal by default (`modal_spec`): each sandbox mounts its own task's folder of the
Volume `summit-planted-data` read-only at MOUNT. `Planted(mounted=False)` points agents at the
local public folder instead.

    python3 -m lab.adapters.planted stage [--seed N]   # generate the suite
    python3 -m lab.adapters.planted self-check         # reference analysis against the mapping
    python3 -m lab.adapters.planted upload             # put the public tables on the Modal Volume
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import re
import shutil
import sys

import numpy as np
from scipy import stats

from lab.adapters.biodsa import ANSWER_LABELS, _load_answer, outcome_kind
from lab.contracts import Study

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data" / "planted"
SEED = 20261116

DELIVERABLE = "answer.json"
VOLUME = "summit-planted-data"
MOUNT = "/planted-data"
AGENT_CPU, AGENT_MEMORY_MB = 2.0, 4096
SPLITS = ("dev", "heldout", "all")
TRUE, FALSE, NOT_VERIFIABLE = ANSWER_LABELS

HIDDEN, DUPLICATES, BATCH, UNITS, CODING = FAMILIES = (
    "hidden_variable", "duplicated_samples", "batch_confounder", "unit_mismatch", "misleading_dictionary")

PATIENT_TABLE = "data_clinical_patient.txt"
SAMPLE_TABLE = "data_clinical_sample.txt"
SUPPLEMENT_TABLE = "data_clinical_supplement.txt"
MAIN_TABLES = (PATIENT_TABLE, SAMPLE_TABLE)

# ----------------------------------------------------------------------------- parameters

N_TASKS, N_DEV = 24, 8
PAIRS_PER_FAMILY = (2, 3)          # two families, drawn with the seed, get the third pair
ROWS = (220, 380)                  # rows (patients) per task, duplicates included
EFFECT = 1.0                       # a real effect, in standard deviations of the outcome's noise
BATCH_OFFSETS = (-2.0, -2 / 3, 2 / 3, 2.0)   # processing-batch shifts of every assay score, lowest first
BATCH_SCALE = (0.3, 1.0)           # ... multiplied by this, drawn per task
BATCH_SCALE_CONFOUNDED = (0.85, 1.0)
BATCH_GROUP_SHARE = (0.85, 0.65, 0.35, 0.15)   # share of the higher group per batch, lowest offset first
SITE_GROUP_SHARE = 0.85            # share of one group at the site that records other units
DUPLICATE_SHARE = (0.12, 0.15)     # duplicated rows per group side, as a share of all rows
UNIT_FACTORS = (10.0, 18.016, 88.4, 38.67, 4.008)   # standard clinical conversions the analysis knows
UNIT_RATIO_LIMIT = 3.0             # a site whose median is this far from the others' is in other units
ALPHA = 0.05                       # the decision rule: True when p < ALPHA in the claimed direction
REFERENCE_P = 1e-4                 # a kept draw: the careful analysis is this clear when the claim holds
NAIVE_P = 1e-3                     # ... and the naive analysis this clear when it says True
NULL_P = 0.2                       # ... and either one at least this far from significance when it says False
MAX_ATTEMPTS = 300

SITES = ("C1", "C2", "C3")
BATCHES = ("B1", "B2", "B3", "B4")

GROUPS = {
    "smoking": {"column": "SMOKING_HISTORY", "display": "Smoking History", "what": "Smoking history",
                "present": "current or former smoker", "absent": "never smoker",
                "present_people": "current or former smokers", "absent_people": "never smokers",
                "companion": "PACK_YEARS", "companion_display": "Pack Years",
                "companion_desc": "Pack-years of cigarette smoking (0 for never smokers).",
                "companion_range": (2, 60), "companion_decimals": 1},
    "radiotherapy": {"column": "PRIOR_RADIOTHERAPY", "display": "Prior Radiotherapy", "what": "Prior radiotherapy",
                     "present": "received", "absent": "not received",
                     "present_people": "patients who received prior radiotherapy",
                     "absent_people": "patients without prior radiotherapy",
                     "companion": "RT_DOSE_GY", "companion_display": "Radiotherapy Dose (Gy)",
                     "companion_desc": "Cumulative radiotherapy dose in Gy (0 if none).",
                     "companion_range": (20, 70), "companion_decimals": 1},
    "kras": {"column": "KRAS_STATUS", "display": "KRAS Status", "what": "KRAS status",
             "present": "mutant", "absent": "wild type",
             "present_people": "patients with KRAS-mutant tumours",
             "absent_people": "patients with KRAS wild-type tumours",
             "companion": "KRAS_VAF", "companion_display": "KRAS VAF",
             "companion_desc": "Variant allele fraction of the KRAS mutation (0 if wild type).",
             "companion_range": (0.05, 0.6), "companion_decimals": 3},
    "tp53": {"column": "TP53_STATUS", "display": "TP53 Status", "what": "TP53 status",
             "present": "mutant", "absent": "wild type",
             "present_people": "patients with TP53-mutant tumours",
             "absent_people": "patients with TP53 wild-type tumours",
             "companion": "TP53_VAF", "companion_display": "TP53 VAF",
             "companion_desc": "Variant allele fraction of the TP53 mutation (0 if wild type).",
             "companion_range": (0.05, 0.8), "companion_decimals": 3},
    "er": {"column": "ER_STATUS", "display": "ER Status", "what": "Oestrogen receptor status",
           "present": "positive", "absent": "negative",
           "present_people": "patients with ER-positive tumours",
           "absent_people": "patients with ER-negative tumours",
           "companion": "ER_PERCENT_POSITIVE", "companion_display": "ER Percent Positive",
           "companion_desc": "Percentage of tumour cells staining for oestrogen receptor (0 if negative).",
           "companion_range": (5, 100), "companion_decimals": 0},
    "statin": {"column": "STATIN_USE", "display": "Statin Use", "what": "Statin use",
               "present": "taking statins", "absent": "not taking statins",
               "present_people": "patients taking statins", "absent_people": "patients not taking statins",
               "companion": "STATIN_DOSE_MG", "companion_display": "Statin Dose (mg)",
               "companion_desc": "Daily statin dose in mg (0 if none).",
               "companion_range": (10, 80), "companion_decimals": 0},
    "chemo": {"column": "PRIOR_CHEMOTHERAPY", "display": "Prior Chemotherapy", "what": "Prior chemotherapy",
              "present": "received", "absent": "not received",
              "present_people": "patients who received prior chemotherapy",
              "absent_people": "patients without prior chemotherapy",
              "companion": "PRIOR_CHEMO_CYCLES", "companion_display": "Prior Chemotherapy Cycles",
              "companion_desc": "Number of prior chemotherapy cycles (0 if none).",
              "companion_range": (1, 12), "companion_decimals": 0},
}

# Blood tests: patient level, measured at each site; `factor` converts the stated unit to the
# usual alternative (glucose mg/dL x (1/18.016) = mmol/L and so on).
LABS = {
    "glucose": {"column": "FASTING_GLUCOSE", "display": "Fasting Glucose", "desc": "Fasting plasma glucose (mg/dL).",
                "phrase": "fasting glucose", "mean": 100.0, "sd": 15.0, "decimals": 1, "factor": 1 / 18.016,
                "alt_decimals": 2},
    "creatinine": {"column": "SERUM_CREATININE", "display": "Serum Creatinine", "desc": "Serum creatinine (mg/dL).",
                   "phrase": "serum creatinine", "mean": 1.0, "sd": 0.2, "decimals": 2, "factor": 88.4,
                   "alt_decimals": 1},
    "hemoglobin": {"column": "HEMOGLOBIN", "display": "Haemoglobin", "desc": "Haemoglobin (g/dL).",
                   "phrase": "haemoglobin", "mean": 13.0, "sd": 1.5, "decimals": 1, "factor": 10.0,
                   "alt_decimals": 0},
    "albumin": {"column": "SERUM_ALBUMIN", "display": "Serum Albumin", "desc": "Serum albumin (g/dL).",
                "phrase": "serum albumin", "mean": 4.0, "sd": 0.4, "decimals": 2, "factor": 10.0,
                "alt_decimals": 1},
    "cholesterol": {"column": "TOTAL_CHOLESTEROL", "display": "Total Cholesterol",
                    "desc": "Total serum cholesterol (mg/dL).", "phrase": "total cholesterol",
                    "mean": 190.0, "sd": 35.0, "decimals": 1, "factor": 1 / 38.67, "alt_decimals": 2},
    "calcium": {"column": "SERUM_CALCIUM", "display": "Serum Calcium", "desc": "Serum calcium (mg/dL).",
                "phrase": "serum calcium", "mean": 9.5, "sd": 0.5, "decimals": 2, "factor": 1 / 4.008,
                "alt_decimals": 3},
}

# Expression-assay scores: sample level, run in processing batches.
SCORES = {
    "proliferation": {"column": "PROLIFERATION_SCORE", "display": "Proliferation Score",
                      "desc": "Proliferation signature score from the expression assay.",
                      "phrase": "proliferation score"},
    "immune": {"column": "IMMUNE_INFILTRATION_SCORE", "display": "Immune Infiltration Score",
               "desc": "Immune infiltration signature score from the expression assay.",
               "phrase": "immune infiltration score"},
    "hypoxia": {"column": "HYPOXIA_SCORE", "display": "Hypoxia Score",
                "desc": "Hypoxia signature score from the expression assay.", "phrase": "hypoxia score"},
    "stromal": {"column": "STROMAL_SCORE", "display": "Stromal Score",
                "desc": "Stromal signature score from the expression assay.", "phrase": "stromal score"},
    "ifng": {"column": "IFNG_SIGNATURE_SCORE", "display": "IFN-gamma Signature Score",
             "desc": "Interferon-gamma signature score from the expression assay.",
             "phrase": "interferon-gamma signature score"},
}
SCORE_MEAN, SCORE_SD = 100.0, 12.0

# The label definitions are BioDSA's, word for word (lab/adapters/biodsa.py TASK_TEXT).
TASK_TEXT = """\
# Hypothesis

{hypothesis}

# Your task

You are given the data tables of one clinical cohort study (listed below). Analyse them, with code
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


def parameters() -> dict:
    """Every generation setting, as recorded in sealed/generation.json."""
    return {"n_tasks": N_TASKS, "n_dev": N_DEV, "families": list(FAMILIES), "pairs_per_family": list(PAIRS_PER_FAMILY),
            "rows": list(ROWS), "effect_sd": EFFECT, "batch_offsets_sd": list(BATCH_OFFSETS),
            "batch_scale": list(BATCH_SCALE), "batch_scale_confounded": list(BATCH_SCALE_CONFOUNDED),
            "batch_group_share": list(BATCH_GROUP_SHARE), "site_group_share": SITE_GROUP_SHARE,
            "duplicate_share": list(DUPLICATE_SHARE), "unit_factors": list(UNIT_FACTORS),
            "unit_ratio_limit": UNIT_RATIO_LIMIT, "alpha": ALPHA, "reference_p": REFERENCE_P, "naive_p": NAIVE_P,
            "null_p": NULL_P, "max_attempts": MAX_ATTEMPTS, "score_mean": SCORE_MEAN, "score_sd": SCORE_SD,
            "numpy": np.__version__}


# ----------------------------------------------------------------------------- reading the tables

def read_table(path: pathlib.Path) -> dict:
    """{"header": [...], "desc": {column: description}, "rows": [{column: text}]} of one table."""
    lines = pathlib.Path(path).read_text().splitlines()
    comments = [line[1:].split("\t") for line in lines if line.startswith("#")]
    body = [line for line in lines if line and not line.startswith("#")]
    header = body[0].split("\t")
    return {"header": header, "desc": dict(zip(header, comments[1])) if len(comments) > 1 else {},
            "rows": [dict(zip(header, line.split("\t"))) for line in body[1:]]}


def _merge(tables: dict[str, dict]) -> list[dict]:
    """One row per sample, with its patient's columns from every patient-level table."""
    patients: dict[str, dict] = {}
    samples: list[dict] = []
    for table in tables.values():
        if "SAMPLE_ID" in table["header"]:
            samples += table["rows"]
        else:
            for r in table["rows"]:
                patients.setdefault(r["PATIENT_ID"], {}).update(r)
    return [{**patients.get(s["PATIENT_ID"], {}), **s} for s in samples]


def _drop_repeats(rows: list[dict]) -> tuple[list[dict], int]:
    """Keep the first of rows that repeat every value except their identifiers."""
    seen, kept = set(), []
    for r in rows:
        key = tuple(sorted((k, v) for k, v in r.items() if not k.endswith("_ID")))
        if key not in seen:
            seen.add(key)
            kept.append(r)
    return kept, len(rows) - len(kept)


def _dictionary_code(description: str, state: str) -> str | None:
    """The code ("0" or "1") the column description gives for a state, e.g. "1 = never smoker"."""
    for code, text in re.findall(r"\b([01])\s*=\s*([^,;.]+)", description):
        if text.strip().lower() == state.lower():
            return code
    return None


def _fix_units(y: np.ndarray, sites: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Convert any site whose median sits a known unit factor away from the median of the sites."""
    y = y.copy()
    medians = {s: float(np.median(y[sites == s])) for s in sorted(set(sites))}
    ref = float(np.median(list(medians.values())))
    candidates = [f for f in UNIT_FACTORS] + [1 / f for f in UNIT_FACTORS]
    converted = {}
    for s, m in medians.items():
        if ref > 0 and m > 0 and abs(math.log(m / ref)) > math.log(UNIT_RATIO_LIMIT):
            factor = min(candidates, key=lambda f: abs(math.log(m / ref / f)))
            y[sites == s] /= factor
            converted[s] = round(factor, 6)
    return y, converted


def _ols(y: np.ndarray, x: np.ndarray, batches: np.ndarray | None) -> tuple[float, float]:
    """Coefficient of x and its two-sided p in y ~ x + batch (fixed effects)."""
    columns = [np.ones(len(y)), x.astype(float)]
    if batches is not None:
        columns += [(batches == b).astype(float) for b in sorted(set(batches))[1:]]
    design = np.column_stack(columns)
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    df = len(y) - design.shape[1]
    cov = (resid @ resid / df) * np.linalg.inv(design.T @ design)
    t = coef[1] / math.sqrt(cov[1, 1])
    return float(coef[1]), float(2 * stats.t.sf(abs(t), df))


def analyse(task: dict, careful: bool = True) -> dict:
    """Test the task's hypothesis on its public tables. Returns {"label", "estimate", "p", "n", "notes"}.

    careful=True is the reference analysis: every table, exact repeat rows dropped, the coding
    checked against the companion column, sites in other units converted, groups compared within
    processing batch (least squares with batch fixed effects). careful=False is the naive one:
    patient and sample tables only, the description trusted, everything pooled (Welch t-test)."""
    spec = task["analysis"]
    data = pathlib.Path(task["dir"]) / "data"
    names = sorted(p.name for p in data.glob("*.txt"))
    if not careful:
        names = [n for n in names if n in MAIN_TABLES]
    tables = {n: read_table(data / n) for n in names}
    columns = {c for t in tables.values() for c in t["header"]}
    if spec["group"] not in columns or spec["outcome"] not in columns:
        return {"label": NOT_VERIFIABLE, "estimate": None, "p": None, "n": 0, "notes": {}}
    group_table = next(n for n, t in tables.items() if spec["group"] in t["header"])
    rows = _merge(tables)
    notes = {"group_table": group_table}
    if careful:
        rows, notes["duplicates_removed"] = _drop_repeats(rows)
    present_code = _dictionary_code(tables[group_table]["desc"][spec["group"]], spec["present_text"])
    if careful:
        companion = np.array([float(r[spec["companion"]]) > 0 for r in rows])
        coded = np.array([r[spec["group"]] == present_code for r in rows])
        notes["coding_reversed"] = bool(coded.any() and companion[coded].mean() < 0.5)
        if notes["coding_reversed"]:
            present_code = "1" if present_code == "0" else "0"
    present = np.array([r[spec["group"]] == present_code for r in rows])
    x = present if spec["higher"] == "present" else ~present
    y = np.array([float(r[spec["outcome"]]) for r in rows])
    if careful:
        notes["sites_converted"] = []
        if "SITE" in columns:
            y, converted = _fix_units(y, np.array([r["SITE"] for r in rows]))
            notes["sites_converted"] = sorted(converted)
        batches = np.array([r["PROCESSING_BATCH"] for r in rows]) if "PROCESSING_BATCH" in columns else None
        estimate, p = _ols(y, x, batches)
    else:
        estimate = float(y[x].mean() - y[~x].mean())
        p = float(stats.ttest_ind(y[x], y[~x], equal_var=False).pvalue)
    label = TRUE if estimate > 0 and p < ALPHA else FALSE
    return {"label": label, "estimate": estimate, "p": p, "n": len(rows), "notes": notes}


def reference_answer(task: dict) -> str:
    """The task's correct label, from the careful analysis of its public tables."""
    return analyse(task, careful=True)["label"]


def naive_answer(task: dict) -> str:
    """The label a careless analysis gives (see analyse)."""
    return analyse(task, careful=False)["label"]


def _clear(result: dict, label: str, strict_p: float) -> bool:
    """Whether an analysis result gives `label` with room to spare."""
    if label == NOT_VERIFIABLE or result["label"] == NOT_VERIFIABLE:
        return result["label"] == label
    if label == TRUE:
        return result["estimate"] > 0 and result["p"] < strict_p
    return result["estimate"] <= 0 or result["p"] > NULL_P


# ----------------------------------------------------------------------------- generating

def _fmt(values, decimals: int) -> list[str]:
    return [f"{v:.{decimals}f}" for v in values]


def _draw(family: str, faulted: bool, truth: str, theme: dict, rng: np.random.Generator) -> dict:
    """One task's tables, hypothesis, analysis spec and planted fault, from one random draw."""
    g = GROUPS[theme["group"]]
    out_key = theme["outcome"]
    outcome_is_lab = out_key in LABS
    total = int(rng.integers(ROWS[0], ROWS[1] + 1))
    k = int(round(total * rng.uniform(*DUPLICATE_SHARE))) if family == DUPLICATES and faulted else 0
    n = total - 2 * k
    site = rng.integers(0, len(SITES), n)
    batch_rank = rng.integers(0, len(BATCHES), n)          # rank of the batch's shift, lowest first
    batch_names = np.array(BATCHES)[rng.permutation(len(BATCHES))]
    scale = rng.uniform(*(BATCH_SCALE_CONFOUNDED if family == BATCH else BATCH_SCALE))

    share_high = np.full(n, 0.5)
    fault: dict = {}
    if family == BATCH and faulted:
        shares = np.array(BATCH_GROUP_SHARE if truth == TRUE else BATCH_GROUP_SHARE[::-1])
        share_high = shares[batch_rank]
        fault = {"higher_group_share_by_batch": {str(batch_names[r]): float(shares[r]) for r in range(len(BATCHES))},
                 "batch_shift_sd": {str(batch_names[r]): round(BATCH_OFFSETS[r] * scale, 3) for r in range(len(BATCHES))}}
    wrong_site = None
    if family == UNITS:
        wrong_site = int(rng.integers(0, len(SITES)))
        enrich_high = (truth == FALSE) == (LABS[out_key]["factor"] > 1)
        share_high = np.where(site == wrong_site, SITE_GROUP_SHARE if enrich_high else 1 - SITE_GROUP_SHARE, 0.5)
    x = rng.random(n) < share_high

    if truth == TRUE:
        effect = EFFECT
    elif family == CODING:
        effect = -EFFECT
    elif family == HIDDEN:
        effect = float(rng.choice([0.0, -EFFECT]))
    else:
        effect = 0.0

    # Blood tests (three, one of them the outcome when it is a lab) and assay scores (two).
    lab_pool = [str(v) for v in rng.permutation(sorted(set(LABS) - {out_key}))]
    lab_keys = [out_key] + lab_pool[:2] if outcome_is_lab else lab_pool[:3]
    lab_keys = [lab_keys[i] for i in rng.permutation(3)]
    score_pool = [str(v) for v in rng.permutation(sorted(set(SCORES) - {out_key}))]
    score_keys = score_pool[:2] if outcome_is_lab else [out_key, score_pool[0]]
    score_keys = [score_keys[i] for i in rng.permutation(2)]

    values: dict[str, np.ndarray] = {}
    for key in lab_keys:
        lab = LABS[key]
        z = rng.standard_normal(n) + (effect * x if key == out_key else 0.0)
        values[key] = np.maximum(lab["mean"] + lab["sd"] * z, lab["mean"] * 0.2)
    for key in score_keys:
        z = rng.standard_normal(n) + scale * np.array(BATCH_OFFSETS)[batch_rank] + (effect * x if key == out_key else 0.0)
        values[key] = SCORE_MEAN + SCORE_SD * z

    higher = str(rng.choice(["present", "absent"]))
    present = x if higher == "present" else ~x
    present_code = int(rng.integers(0, 2))
    code = np.where(present, present_code, 1 - present_code)
    if family == CODING and faulted:
        code = 1 - code
        fault = {"stated_present_code": present_code, "actual_present_code": 1 - present_code}
    lo, hi = g["companion_range"]
    companion = np.where(present, rng.uniform(lo, hi, n), 0.0)

    age = np.clip(np.round(rng.normal(62, 10, n)), 25, 90)
    sex = rng.choice(["Male", "Female"], n)
    ecog = rng.choice([0, 1, 2], n, p=[0.5, 0.35, 0.15])
    bmi = rng.normal(27, 4, n)
    follow_up = rng.uniform(1, 80, n)
    purity = rng.uniform(0.2, 0.95, n)
    sample_type = rng.choice(["Primary", "Metastasis"], n, p=[0.7, 0.3])

    # Rows: every patient once, then the duplicated ones again (the top of the higher group and
    # the bottom of the other, on the outcome), all shuffled and given fresh ids.
    source = np.arange(n)
    if k:
        y_out = values[out_key]
        high_idx, low_idx = np.flatnonzero(x), np.flatnonzero(~x)
        top = high_idx[np.argsort(-y_out[high_idx])[:k]]
        bottom = low_idx[np.argsort(y_out[low_idx])[:k]]
        source = np.concatenate([source, top, bottom])
        fault = {"duplicated_rows": 2 * k, "unique_rows": n}
    prefix = "".join(rng.choice(list("ABCDEFGHJKMNPQRSUVXYZ"), 2))
    numbers = rng.choice(np.arange(1000, 10000), size=len(source), replace=False)
    order = np.argsort(numbers)
    source, numbers = source[order], numbers[order]
    pid = [f"{prefix}{num}" for num in numbers]

    lab_values = {}
    for key in lab_keys:
        lab = LABS[key]
        v = values[key][source]
        texts = _fmt(v, lab["decimals"])
        if family == UNITS and faulted:
            at_site = site[source] == wrong_site
            alt = _fmt(v * lab["factor"], lab["alt_decimals"])
            texts = [a if s else t for t, a, s in zip(texts, alt, at_site)]
        lab_values[key] = texts
    if family == UNITS and faulted:
        fault = {"site": SITES[wrong_site], "factor": round(LABS[out_key]["factor"], 6),
                 "stated_unit": LABS[out_key]["desc"]}
    elif family == UNITS:
        fault = {"site_associated_with_group": SITES[wrong_site]}

    def col(name, display, desc, dtype, texts):
        return {"name": name, "display": display, "desc": desc, "type": dtype, "values": list(texts)}

    coding_desc = (f"{g['what']}: 1 = {g['present'] if present_code == 1 else g['absent']}, "
                   f"0 = {g['absent'] if present_code == 1 else g['present']}.")
    group_cols = [col(g["column"], g["display"], coding_desc, "NUMBER", [str(int(c)) for c in code[source]]),
                  col(g["companion"], g["companion_display"], g["companion_desc"], "NUMBER",
                      _fmt(companion[source], g["companion_decimals"]))]
    patient = [col("PATIENT_ID", "Patient Identifier", "Identifier to uniquely specify a patient.", "STRING", pid),
               col("AGE", "Diagnosis Age", "Age at diagnosis in years.", "NUMBER", _fmt(age[source], 0)),
               col("SEX", "Sex", "Sex.", "STRING", sex[source]),
               col("SITE", "Site", "Hospital site that enrolled the patient and ran the blood tests.", "STRING",
                   [SITES[s] for s in site[source]])]
    hidden = family == HIDDEN and faulted
    if not hidden:
        patient += group_cols
    patient += [col(LABS[k_]["column"], LABS[k_]["display"], LABS[k_]["desc"], "NUMBER", lab_values[k_])
                for k_ in lab_keys]
    sample = [col("SAMPLE_ID", "Sample Identifier", "A unique sample identifier.", "STRING", [f"{p}-T1" for p in pid]),
              col("PATIENT_ID", "Patient Identifier", "Identifier to uniquely specify a patient.", "STRING", pid),
              col("SAMPLE_TYPE", "Sample Type", "Primary tumour or metastasis.", "STRING", sample_type[source]),
              col("PROCESSING_BATCH", "Processing Batch", "Laboratory batch in which the expression assay was run.",
                  "STRING", batch_names[batch_rank[source]]),
              col("TUMOR_PURITY", "Tumor Purity", "Estimated tumour cell fraction of the sample.", "NUMBER",
                  _fmt(purity[source], 2))]
    sample += [col(SCORES[k_]["column"], SCORES[k_]["display"], SCORES[k_]["desc"], "NUMBER",
                   _fmt(values[k_][source], 2)) for k_ in score_keys]
    supplement = [col("PATIENT_ID", "Patient Identifier", "Identifier to uniquely specify a patient.", "STRING", pid),
                  col("ECOG_PS", "ECOG Performance Status", "ECOG performance status at enrolment.", "NUMBER",
                      [str(int(e)) for e in ecog[source]]),
                  col("BMI", "Body Mass Index", "Body mass index at enrolment (kg/m2).", "NUMBER", _fmt(bmi[source], 1)),
                  col("FOLLOW_UP_MONTHS", "Follow-up Months", "Months from enrolment to last contact.", "NUMBER",
                      _fmt(follow_up[source], 1))]
    if hidden:
        supplement += group_cols
        fault = {"group_table": SUPPLEMENT_TABLE}

    out = LABS[out_key] if outcome_is_lab else SCORES[out_key]
    high_people = g["present_people"] if higher == "present" else g["absent_people"]
    low_people = g["absent_people"] if higher == "present" else g["present_people"]
    if rng.random() < 0.5:
        hypothesis = f"{high_people} have a higher {out['phrase']} than {low_people}."
    else:
        hypothesis = f"{low_people} have a lower {out['phrase']} than {high_people}."
    hypothesis = hypothesis[0].upper() + hypothesis[1:]

    expected_notes = {"group_table": SUPPLEMENT_TABLE if hidden else PATIENT_TABLE, "duplicates_removed": 2 * k,
                      "coding_reversed": family == CODING and faulted,
                      "sites_converted": [SITES[wrong_site]] if family == UNITS and faulted else []}
    return {"tables": {PATIENT_TABLE: patient, SAMPLE_TABLE: sample, SUPPLEMENT_TABLE: supplement},
            "hypothesis": hypothesis, "rows": len(source), "effect_sd": effect, "batch_scale": round(scale, 3),
            "analysis": {"group": g["column"], "companion": g["companion"], "present_text": g["present"],
                         "higher": higher, "outcome": out["column"]},
            "fault": fault, "expected_notes": expected_notes}


def _write_table(path: pathlib.Path, columns: list[dict]) -> None:
    lines = ["#" + "\t".join(c[key] for c in columns) for key in ("display", "desc", "type")]
    lines.append("#" + "\t".join("1" for _ in columns))
    lines.append("\t".join(c["name"] for c in columns))
    lines += ["\t".join(row) for row in zip(*(c["values"] for c in columns))]
    path.write_text("\n".join(lines) + "\n")


def _size_text(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 100_000 else f"{n / 1e3:.1f} kB"


def _write_task(folder: pathlib.Path, draft: dict) -> None:
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "data").mkdir(parents=True)
    for name, columns in draft["tables"].items():
        _write_table(folder / "data" / name, columns)
    sizes = {p.name: p.stat().st_size for p in sorted((folder / "data").iterdir())}
    listing = "\n".join(f"- `{name}` ({_size_text(size)})" for name, size in sorted(sizes.items()))
    (folder / "task.md").write_text(TASK_TEXT.format(hypothesis=draft["hypothesis"], tables=listing))
    (folder / "answer_schema.json").write_text(json.dumps({"label": "string"}, indent=2) + "\n")


def _plan(rng: np.random.Generator) -> list[dict]:
    """The pairs: family, intended label and the two tasks' themes."""
    extra = set(int(i) for i in rng.choice(len(FAMILIES), size=N_TASKS // 2 - 2 * len(FAMILIES), replace=False))
    combos = {"lab": [(gk, ok) for gk in GROUPS for ok in LABS], "score": [(gk, ok) for gk in GROUPS for ok in SCORES]}
    for kind in combos:
        combos[kind] = [combos[kind][i] for i in rng.permutation(len(combos[kind]))]
    pairs = []
    for i, family in enumerate(FAMILIES):
        count = PAIRS_PER_FAMILY[1] if i in extra else PAIRS_PER_FAMILY[0]
        if family == DUPLICATES:
            labels = [FALSE] * count
        else:
            labels = [TRUE, FALSE] + [str(rng.choice([TRUE, FALSE])) for _ in range(count - 2)]
        for label in labels:
            themes = []
            for _ in range(2):
                kind = {BATCH: "score", UNITS: "lab"}.get(family) or str(rng.choice(["lab", "score"]))
                group, outcome = combos[kind].pop()
                themes.append({"group": group, "outcome": outcome})
            pairs.append({"family": family, "label": label, "themes": themes})
    return pairs


def generate(seed: int, out_root: pathlib.Path) -> dict:
    """Build the whole suite under out_root/public and out_root/sealed. Returns the mapping."""
    out_root = pathlib.Path(out_root)
    public, sealed = out_root / "public", out_root / "sealed"
    for d in (public, sealed):
        if d.exists():
            shutil.rmtree(d)
    sealed.mkdir(parents=True)
    rng = np.random.default_rng(seed)
    pairs = _plan(rng)
    # Split: shuffled pairs; dev takes the first ones from distinct families, half of them True.
    dev, seen, per_label = set(), set(), collections.Counter()
    for p in (int(i) for i in rng.permutation(len(pairs))):
        family, label = pairs[p]["family"], pairs[p]["label"]
        if len(dev) < N_DEV // 2 and family not in seen and per_label[label] < N_DEV // 4:
            dev.add(p)
            seen.add(family)
            per_label[label] += 1
    if len(dev) != N_DEV // 2:
        raise RuntimeError("could not fill the dev split")
    ids = [f"pf{int(i) + 1:02d}" for i in rng.permutation(N_TASKS)]
    mapping = {}
    for p, pair in enumerate(pairs):
        twin_ids = ids[2 * p], ids[2 * p + 1]
        for role, faulted in enumerate((True, False)):
            index, task_id = 2 * p + role, twin_ids[role]
            folder = public / task_id
            for attempt in range(MAX_ATTEMPTS):
                task_rng = np.random.default_rng([seed, index, attempt])
                draft = _draw(pair["family"], faulted, pair["label"], pair["themes"][role], task_rng)
                _write_task(folder, draft)
                task = {"dir": folder, "analysis": draft["analysis"]}
                ref, naive = analyse(task, careful=True), analyse(task, careful=False)
                if not (_clear(ref, pair["label"], REFERENCE_P) and ref["notes"] == draft["expected_notes"]):
                    continue
                if faulted and pair["family"] == HIDDEN:
                    bites = naive["label"] == NOT_VERIFIABLE
                elif faulted:
                    bites = _clear(naive, FALSE if pair["label"] == TRUE else TRUE, NAIVE_P)
                else:
                    bites = _clear(naive, pair["label"], NAIVE_P)
                if bites:
                    break
            else:
                raise RuntimeError(f"no acceptable draw for {task_id} ({pair['family']}) in {MAX_ATTEMPTS} attempts")

            def short(r):
                return {"label": r["label"], "estimate": None if r["estimate"] is None else round(r["estimate"], 4),
                        "p": None if r["p"] is None else float(f"{r['p']:.4g}"), "n": r["n"]}

            mapping[task_id] = {"family": pair["family"], "faulted": faulted, "twin": twin_ids[1 - role],
                                "label": pair["label"], "split": "dev" if p in dev else "heldout",
                                "pair": f"pair-{p + 1:02d}", "hypothesis": draft["hypothesis"],
                                "analysis": draft["analysis"], "fault": draft["fault"], "rows": draft["rows"],
                                "effect_sd": draft["effect_sd"], "batch_scale": draft["batch_scale"],
                                "attempts": attempt + 1, "reference": short(ref), "naive": short(naive)}
    mapping = dict(sorted(mapping.items()))
    (sealed / "mapping.json").write_text(json.dumps(mapping, indent=2) + "\n")
    (sealed / "generation.json").write_text(json.dumps({"seed": seed, "parameters": parameters(),
                                                        "pairs": pairs}, indent=2) + "\n")
    return mapping


def load_tasks(root: pathlib.Path = DATA_ROOT) -> list[dict]:
    """Every task as its sealed mapping entry plus "id" and "dir" (its public folder)."""
    root = pathlib.Path(root)
    mapping = json.loads((root / "sealed" / "mapping.json").read_text())
    return [{"id": i, "dir": root / "public" / i, **m} for i, m in mapping.items()]


def self_check(root: pathlib.Path = DATA_ROOT) -> dict:
    """Run the reference and naive analyses on every task. Returns counts (no labels per task)."""
    tasks = load_tasks(root)
    matches = sum(reference_answer(t) == t["label"] for t in tasks)
    by_family = {f: {"faulted_naive_wrong": 0, "faulted": 0, "clean_naive_right": 0, "clean": 0} for f in FAMILIES}
    for t in tasks:
        right = naive_answer(t) == t["label"]
        row = by_family[t["family"]]
        if t["faulted"]:
            row["faulted"] += 1
            row["faulted_naive_wrong"] += not right
        else:
            row["clean"] += 1
            row["clean_naive_right"] += right
    labels = {s: dict(sorted(collections.Counter(t["label"] for t in tasks if t["split"] == s).items()))
              for s in ("dev", "heldout")}
    return {"tasks": len(tasks), "reference_matches": matches, "naive": by_family, "labels_by_split": labels}


# ----------------------------------------------------------------------------- the Modal Volume

def upload(root: pathlib.Path = DATA_ROOT, study_ids: list[str] | None = None) -> list[str]:
    """Put each task's public tables in its own folder of the Volume (/pf01/...). Files already
    there with the right size are skipped. Returns the ids uploaded to."""
    import modal
    volume = modal.Volume.from_name(VOLUME, create_if_missing=True)
    done = []
    for folder in sorted((pathlib.Path(root) / "public").iterdir()):
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


# ----------------------------------------------------------------------------- the adapter

class Planted:
    """The planted-fault suite. split: "dev", "heldout" or "all". mounted=True (the default): agents
    see the tables at MOUNT on Modal; mounted=False: at the local public folder."""

    name = "planted"

    def __init__(self, split: str = "all", root: pathlib.Path | None = None, mounted: bool = True):
        if split not in SPLITS:
            raise ValueError(f"split must be one of {', '.join(SPLITS)}")
        self.split = split
        self.root = pathlib.Path(root) if root else DATA_ROOT
        self.mounted = mounted
        self.modal_only = mounted     # with mounted tables the laptop runner cannot reach them

    @property
    def public(self) -> pathlib.Path:
        return self.root / "public"

    def _mapping(self) -> dict:
        return json.loads((self.root / "sealed" / "mapping.json").read_text())

    def studies(self) -> list[Study]:
        if not self.public.exists():
            raise RuntimeError("run `python3 -m lab.adapters.planted stage` first")
        ids = sorted(d.name for d in self.public.iterdir() if (d / "task.md").exists())
        if self.split != "all":
            mapping = self._mapping()
            ids = [i for i in ids if mapping[i]["split"] == self.split]
        return [Study(id=i, title=i, statement=(self.public / i / "task.md").read_text(),
                      data_dir=pathlib.Path(MOUNT) if self.mounted else self.public / i / "data",
                      deliverables=[DELIVERABLE]) for i in ids]

    def tables(self, study: Study) -> dict[str, int]:
        """The study's public tables: file name -> bytes."""
        return {f.name: f.stat().st_size for f in sorted((self.public / study.id / "data").iterdir()) if f.is_file()}

    def modal_spec(self, study: Study) -> dict:
        """The sandbox for this study's agents (see lab/backend_modal.py): the base image's numpy,
        pandas, scipy and statsmodels are enough for these tables."""
        return {"packages": [], "cpu": AGENT_CPU, "memory_mb": AGENT_MEMORY_MB,
                "volumes": [{"name": VOLUME, "sub_path": f"/{study.id}", "mount": MOUNT}],
                "package_index": True}

    def missing_data(self, studies: list[Study], listing=None) -> list[str]:
        """Ids of the studies whose tables are not where their agents will look: on the Volume with
        the right size (mounted), or in the local public folder."""
        if not self.mounted:
            return [s.id for s in studies if not (self.public / s.id / "data").is_dir() or not self.tables(s)]
        listing = listing or volume_listing
        missing = []
        for s in studies:
            present = listing(f"/{s.id}")
            if any(present.get(name) != size for name, size in self.tables(s).items()):
                missing.append(s.id)
        return missing

    def data_command(self, study_ids: list[str]) -> str:
        return "python3 -m lab.adapters.planted " + ("upload" if self.mounted else "stage")

    def blocked_terms(self) -> list[str]:
        """Only the suite's own name: the tasks are synthetic, so there is no source to look up.
        ("planted" alone would also block searches that contain "transplanted".)"""
        return ["summit-planted"]

    def schema(self, study: Study) -> dict:
        return json.loads((self.public / study.id / "answer_schema.json").read_text())

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
        """Exact match against the sealed label. Call only after every agent has exited. A missing or
        malformed answer is a fail. "kind" uses BioDSA's words (outcome_kind); "family", "faulted"
        and "twin" let measures split results by fault family and pair faulted tasks with twins."""
        entry = self._mapping()[study.id]
        extra = {"family": entry["family"], "faulted": entry["faulted"], "twin": entry["twin"]}
        candidate = pathlib.Path(final_dir) / DELIVERABLE
        answer, found = _load_answer(candidate)
        found = found if answer is None else self.problems(study, DELIVERABLE, candidate)
        if found:
            return {"passed": False, "score": 0.0, "error": found[0], **extra}
        passed = answer["label"] == entry["label"]
        return {"passed": passed, "score": 1.0 if passed else 0.0,
                "kind": outcome_kind(answer["label"], entry["label"]), **extra}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m lab.adapters.planted")
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="generate the suite under data/planted")
    stage.add_argument("--seed", type=int, default=SEED)
    sub.add_parser("self-check", help="run the reference analysis on every task and compare with the mapping")
    up = sub.add_parser("upload", help="put the public tables on the Modal Volume")
    up.add_argument("ids", nargs="?", help="comma-separated task ids (default: all)")
    args = parser.parse_args(argv)
    if args.command == "stage":
        mapping = generate(args.seed, DATA_ROOT)
        print(f"staged {len(mapping)} tasks under {(DATA_ROOT / 'public').relative_to(ROOT)} with seed {args.seed} "
              f"(labels, families and the split are in {(DATA_ROOT / 'sealed').relative_to(ROOT)})")
        return 0
    if args.command == "self-check":
        report = self_check(DATA_ROOT)
        print(f"reference analysis matches the sealed label on {report['reference_matches']}/{report['tasks']} tasks")
        for family, r in report["naive"].items():
            print(f"  {family:22s} naive analysis wrong on {r['faulted_naive_wrong']}/{r['faulted']} faulted, "
                  f"right on {r['clean_naive_right']}/{r['clean']} clean")
        for split, counts in report["labels_by_split"].items():
            print(f"  {split}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        return 0 if report["reference_matches"] == report["tasks"] else 1
    ids = upload(DATA_ROOT, args.ids.split(",") if args.ids else None)
    print(f"tables on the Volume {VOLUME} for {len(ids)} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
