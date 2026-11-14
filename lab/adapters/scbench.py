"""scBench public tasks (LatchBio): single-cell RNA-seq analysis steps with automatic graders.

Each task gives one .h5ad data snapshot and a plain-language question about one analysis step,
and expects a JSON object in `eval_answer.json`. Two public sets can be run:

  current    the 6 tasks in https://github.com/latchbio/scbench (evals/), graded by the
             `latch-eval-tools==0.3.10` graders the repository pins. Data is on Latch storage.
  canonical  the 30 older tasks packaged in UK AISI's Inspect Evals (src/inspect_evals/scbench),
             graded by Inspect's port of the same graders. Data is mirrored on Hugging Face.

The public eval files hold the answers and the tolerances inside `grader.config`, plus notes on
the intended method, and the task ids name the method. So this adapter splits the download:

  data/scbench/sealed/   everything as downloaded, plus the neutral-id mapping. No agent may read it.
  data/scbench/public/   sc01 ... sc36, each with task.md (the prompt exactly as the benchmark's
                         harness builds it), answer_schema.json (field names and kinds, no values)
                         and data_manifest.json (file name, size, URL). The .h5ad files themselves
                         live on the Modal Volume `summit-scbench-data` (see lab/adapters/scbench_data.py).

Neutral ids: sc01-sc06 are the current set and sc07-sc36 the canonical set; inside each set the
order is by a hash of the real id, so it carries no meaning.

`grade` is the only function that runs the benchmark's grader, and the lab calls it only after
every agent has exited. It runs the grader in a separate `python -I` process from its own virtual
environment on the sealed side (GRADER_ENV, holding only pydantic, the graders' one dependency).

Agents run only on Modal (`modal_spec`): each sandbox mounts its own task's folder of the Volume
read-only at MOUNT, links the data into the working folder where the task text expects it, and
cannot reach any package index (an index package, inspect-evals, holds the older set's answers).

Stage the public side once with:  python3 -m lab.adapters.scbench stage
(needs network for the current set's file sizes; the result is cached on the sealed side.)
Make the grader's environment once with:  python3 -m lab.adapters.scbench grader-env
"""

from __future__ import annotations

import ast
import collections
import hashlib
import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request

from lab.contracts import Study

ROOT = pathlib.Path(__file__).resolve().parents[2]
SEALED = ROOT / "data" / "scbench" / "sealed"
PUBLIC = ROOT / "data" / "scbench" / "public"
MAPPING = SEALED / "summit_mapping.json"          # neutral id -> set and real task id; sealed
CURRENT_DATA = SEALED / "current_data.json"       # current set: data file name and size per task; sealed

CURRENT = "current"
CANONICAL = "canonical"
CURRENT_REPO = SEALED / "scbench"                                         # git clone of latchbio/scbench
CURRENT_EVALS = CURRENT_REPO / "evals"
LATCH_TOOLS = SEALED / "latch_eval_tools_wheel" / "latch_eval_tools"      # the pinned grader package, unpacked
INSPECT_TOP = SEALED / "inspect_evals" / "src" / "inspect_evals"          # sparse clone of inspect_evals
INSPECT_PKG = INSPECT_TOP / "scbench"
CANONICAL_EVALS = INSPECT_PKG / "data" / "evals_canonical"

VOLUME = "summit-scbench-data"
LATCH_SIGNED_URL = "https://nucleus.latch.bio/ldata/get-signed-url"      # anonymous POST {"path": "latch://..."}
DELIVERABLE = "eval_answer.json"                  # the file name both harnesses read
RELATIVE_TOLERANCE = 0.05   # two teams agree on a number when they are within 5 percent
JACCARD_AGREEMENT = 0.8     # ... and on a list of genes or labels when the sets overlap this much
GRADER_TIMEOUT_S = 120
GRADER_ENV = SEALED / "grader-venv"               # made with uv; only the graders' dependency in it
GRADER_PYTHON = GRADER_ENV / "bin" / "python"
GRADER_PACKAGES = ["pydantic==2.13.3"]            # the version Inspect Evals' uv.lock pins

# The sandbox each scBench agent runs in (lab.backend_modal reads this through modal_spec).
MOUNT = "/scbench-data"           # the task's Volume folder, mounted read-only
WORKSPACE_ALIAS = "/workspace"    # the current set's task text names /workspace/data and /workspace/eval_answer.json
CURRENT_DATA_DIR = "/workspace/data"
AGENT_PACKAGES = ["scanpy==1.10.4", "anndata==0.11.1", "h5py==3.12.1", "numpy==1.26.4", "pandas==2.2.3",
                  "scipy==1.14.1", "scikit-learn==1.5.2", "leidenalg==0.10.2", "igraph==0.11.8",
                  "umap-learn==0.5.7", "matplotlib==3.8.4"]
AGENT_CPU, AGENT_MEMORY_MB = 4.0, 16384           # what the benchmark's own harness gives
DATA_COMMAND = "modal run lab/adapters/scbench_data.py --sets {sets}"


# ----------------------------------------------------------------------------- reading the sealed side

def _eval_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in directory.glob("*/*.json") if p.name != "manifest.json")


def _load_tasks() -> list[dict]:
    """Every task of both sets as {"set", "id", "file", "record"}, in download order."""
    tasks = []
    for set_name, directory in ((CURRENT, CURRENT_EVALS), (CANONICAL, CANONICAL_EVALS)):
        for path in _eval_files(directory):
            record = json.loads(path.read_text())
            tasks.append({"set": set_name, "id": record["id"], "file": str(path.relative_to(SEALED)),
                          "record": record})
    return tasks


def _data_nodes(record: dict) -> list[str]:
    node = record.get("data_node")
    return node if isinstance(node, list) else ([node] if node else [])


def _literal(module_path: pathlib.Path, name: str):
    """Read one top-level constant from a downloaded module without running it."""
    for node in ast.parse(module_path.read_text()).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(t, "id", None) == name for t in targets):
                return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found in {module_path}")


def _inspect_data_manifest() -> dict[str, dict]:
    """Inspect's DATA_MANIFEST (latch URI -> filename, sha256, size), read with ast, not imported."""
    tree = ast.parse((INSPECT_PKG / "data_manifest.py").read_text())
    manifest = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "DataFile":
            fields = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
            manifest[fields["latch_uri"]] = fields
    return manifest


def _hf_base_url() -> str:
    path = INSPECT_PKG / "data_manifest.py"
    return f"https://huggingface.co/datasets/{_literal(path, 'REPO_ID')}/resolve/{_literal(path, 'REPO_REVISION')}"


# ----------------------------------------------------------------------------- the prompt, as each harness builds it

def _current_prompt(record: dict) -> str:
    """latch-eval-tools' CLI harnesses (Claude Code, Codex): task text, a newline, data_instructions.md."""
    instructions = (LATCH_TOOLS / "prompts" / "data_instructions.md").read_text(encoding="utf-8")
    return f"{record['task']}\n{instructions}"


def _canonical_prompt(record: dict, manifest: dict) -> str:
    """Inspect's record_to_sample: task text, the local file list, the node context, CRITICAL_INSTRUCTIONS."""
    task_text = str(record["task"]).strip()
    contextual, names = [], []
    for uri in _data_nodes(record):
        if uri in manifest:
            filename = pathlib.Path(uri).name
            names.append(filename)
            node_id = re.sub(r"^latch:///?|\.(?:node|h5ad|csv)$", "", uri)
            contextual.append({"type": "File", "path": uri, "local_path": filename, "id": node_id})
    if contextual:
        task_text += ("\n\nHere is the context of the selected nodes the user would like to use: "
                      f"<ContextualNodeData>{json.dumps(contextual)}</ContextualNodeData>")
        if names:
            listing = "\n".join(f"- {f}" for f in names)
            section = (f"\n\nThe following data files are available in your current working directory:\n"
                       f"{listing}\n\nUse these local filenames to access the data.\n")
            task_text = task_text.replace("<ContextualNodeData>", f"{section}<ContextualNodeData>")
    return task_text + _literal(INSPECT_PKG / "prompts.py", "CRITICAL_INSTRUCTIONS")


# ----------------------------------------------------------------------------- the answer shape

def _template_keys(task_text: str) -> list[str]:
    match = re.search(r"<EVAL_ANSWER>(.*?)</EVAL_ANSWER>", task_text, re.S)
    return re.findall(r'"([A-Za-z0-9_]+)"\s*:', match.group(1)) if match else []


def _answer_schema(record: dict) -> dict[str, str]:
    """Field name -> kind ("number", "string", "list", "mapping"), from the grader type and the
    field names it reads. Names only: no values, tolerances or other settings. Ordered as the
    task text's own answer template lists them."""
    grader = record.get("grader") or {}
    kind, config = grader.get("type"), grader.get("config") or {}
    if kind in ("numeric_tolerance", "numeric_range"):
        fields = {name: "number" for name in config.get("ground_truth", {})}
    elif kind == "multiple_choice":
        fields = {"answer": "string"}
    elif kind in ("label_set_jaccard", "jaccard_label_set"):
        fields = {str(config.get("answer_field", "cell_types_predicted")): "list"}
    elif kind == "marker_gene_precision_recall":
        fields = {str(config.get("answer_field", "top_marker_genes")): "list"}
    elif kind == "distribution_comparison":
        fields = {"cell_type_distribution": "mapping"}
    else:
        raise ValueError(f"{record['id']}: grader type {kind!r} is not handled")
    order = _template_keys(record["task"])
    missing = [name for name in fields if name not in order]
    if missing:
        raise ValueError(f"{record['id']}: the task text does not name answer fields {missing}")
    return {name: fields[name] for name in sorted(fields, key=order.index)}


# ----------------------------------------------------------------------------- data files

def _latch_signed_url(uri: str) -> str:
    """A 7-day download link for a public Latch data node. Needs no account."""
    body = json.dumps({"path": uri, "egress_event_data": {"purpose": json.dumps({"method": "latch-cli"})}}).encode()
    request = urllib.request.Request(LATCH_SIGNED_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["data"]["url"]


def _remote_size(url: str) -> int:
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(response.headers["Content-Range"].rsplit("/", 1)[1])


def _name_from_trajectories(task_id: str) -> str:
    """The file name the harness gave the data, as seen under /workspace/data/ in the published runs.
    Reads file names only; the trajectories themselves stay sealed."""
    counts = collections.Counter()
    for path in (CURRENT_REPO / "trajectories" / task_id).rglob("trajectory.json"):
        counts.update(name.rstrip(".") for name in re.findall(r"/workspace/data/([A-Za-z0-9_.\-]+)", path.read_text()))
    if not counts:
        raise RuntimeError(f"no data file name found in the trajectories of {task_id}")
    return counts.most_common(1)[0][0]


def _current_data(tasks: list[dict], probe: bool = True) -> dict[str, list[dict]]:
    """Current set: real id -> [{"uri", "name", "size_bytes"}]. Cached on the sealed side."""
    if CURRENT_DATA.exists():
        return json.loads(CURRENT_DATA.read_text())
    if not probe:
        raise RuntimeError(f"{CURRENT_DATA} missing; stage with network access first")
    out = {}
    for task in (t for t in tasks if t["set"] == CURRENT):
        nodes = _data_nodes(task["record"])
        if len(nodes) != 1:
            raise RuntimeError(f"{task['id']}: expected one data node, found {len(nodes)}")
        out[task["id"]] = [{"uri": nodes[0], "name": _name_from_trajectories(task["id"]),
                            "size_bytes": _remote_size(_latch_signed_url(nodes[0]))}]
    CURRENT_DATA.write_text(json.dumps(out, indent=2) + "\n")
    return out


def _data_manifest(neutral: str, task: dict, current: dict, hf: dict) -> dict:
    """Where the task's data comes from and where an agent must find it. Pointers only."""
    files = []
    if task["set"] == CURRENT:
        for item in current[task["id"]]:
            files.append({"name": item["name"], "size_bytes": item["size_bytes"], "source": "latch",
                          "url": item["uri"], "resolve": f"POST {LATCH_SIGNED_URL} with {{\"path\": url}}",
                          "volume_path": f"/{neutral}/{item['name']}"})
        place = {"data_dir": "/workspace/data", "answer_path": "/workspace/eval_answer.json"}
    else:
        base = _hf_base_url()
        for uri in _data_nodes(task["record"]):
            entry = hf[uri]
            name = pathlib.Path(uri).name     # the name the task text gives; the Inspect harness saves entry["filename"]
            files.append({"name": name, "size_bytes": entry["size_bytes"], "source": "huggingface",
                          "url": f"{base}/{entry['filename']}", "sha256": entry["sha256"],
                          "harness_name": entry["filename"], "volume_path": f"/{neutral}/{name}"})
        place = {"data_dir": "the working directory", "answer_path": "eval_answer.json in the working directory"}
    return {"volume": VOLUME, "volume_sub_path": f"/{neutral}", **place, "files": files,
            "total_bytes": sum(f["size_bytes"] for f in files)}


# ----------------------------------------------------------------------------- staging

def _order_key(task: dict) -> tuple:
    return (0 if task["set"] == CURRENT else 1, hashlib.sha256(task["id"].encode()).hexdigest())


def stage(probe: bool = True) -> list[str]:
    """Build the public side from the sealed download. Returns the neutral ids."""
    tasks = sorted(_load_tasks(), key=_order_key)
    current = _current_data(tasks, probe=probe)
    hf = _inspect_data_manifest()
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    mapping = {}
    for i, task in enumerate(tasks, 1):
        neutral = f"sc{i:02d}"
        record = task["record"]
        mapping[neutral] = {"set": task["set"], "id": task["id"], "file": task["file"]}
        prompt = _current_prompt(record) if task["set"] == CURRENT else _canonical_prompt(record, hf)
        out = PUBLIC / neutral
        out.mkdir(parents=True)
        (out / "task.md").write_text(prompt)
        (out / "answer_schema.json").write_text(json.dumps(_answer_schema(record), indent=2) + "\n")
        (out / "data_manifest.json").write_text(
            json.dumps(_data_manifest(neutral, task, current, hf), indent=2) + "\n")
    MAPPING.write_text(json.dumps(mapping, indent=2) + "\n")
    return list(mapping)


# ----------------------------------------------------------------------------- answers

def _load_answer(path: pathlib.Path):
    """Return (answer object, problem list). The benchmark reads a bare JSON object."""
    try:
        doc = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as err:
        return None, [f"cannot read JSON: {err}"]
    if not isinstance(doc, dict):
        return None, ["top level must be a JSON object with the answer fields"]
    return doc, []


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _close(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b))
    return scale == 0 or abs(a - b) <= RELATIVE_TOLERANCE * scale


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


def _jaccard(a: list, b: list) -> float:
    sa, sb = {_norm(x) for x in a}, {_norm(x) for x in b}
    return 1.0 if not sa and not sb else len(sa & sb) / len(sa | sb)


def grader_env_ready() -> bool:
    """True when the sealed grading environment exists (see make_grader_env)."""
    return GRADER_PYTHON.is_file()


def make_grader_env() -> pathlib.Path:
    """Create GRADER_ENV with uv and install GRADER_PACKAGES only. The grader code itself is the
    sealed copy of each set's pinned package, loaded by path. Returns the environment's python."""
    if not SEALED.is_dir():
        raise RuntimeError(f"{SEALED} is missing; download the benchmark first")
    subprocess.run(["uv", "venv", "--python", "3.12", str(GRADER_ENV)], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(GRADER_PYTHON), *GRADER_PACKAGES], check=True)
    return GRADER_PYTHON


def _benchmark_grader(set_name: str, grader_type: str, config: dict, answer: dict) -> dict:
    """Run the benchmark's own grader on one answer in a separate `python -I` process, with the
    sealed grading environment's python. The grader packages are mapped onto the sealed folders
    without running their package __init__ files (latch_eval_tools' imports the harness and the
    Latch SDK). Returns {"passed": bool, "score": float}. Raises on any failure; `grade` turns that
    into a fail."""
    if not grader_env_ready():
        raise RuntimeError("the grading environment is missing; run `python3 -m lab.adapters.scbench grader-env`")
    request = {"set": set_name, "type": grader_type, "config": config, "answer": answer,
               "latch_dir": str(LATCH_TOOLS), "inspect_top": str(INSPECT_TOP), "inspect_pkg": str(INSPECT_PKG)}
    done = subprocess.run([str(GRADER_PYTHON), "-I", "-c", _GRADER_RUNNER], input=json.dumps(request),
                          capture_output=True, text=True, timeout=GRADER_TIMEOUT_S, cwd=str(SEALED))
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        raise RuntimeError(f"grader process failed ({done.returncode}): {done.stderr.strip()[-400:]}")
    return json.loads(lines[-1])


_GRADER_RUNNER = r"""
import importlib, json, sys, types
req = json.load(sys.stdin)
if req["set"] == "current":
    pkg = types.ModuleType("latch_eval_tools"); pkg.__path__ = [req["latch_dir"]]
    sys.modules["latch_eval_tools"] = pkg
    graders = importlib.import_module("latch_eval_tools.graders")
else:
    top = types.ModuleType("inspect_evals"); top.__path__ = [req["inspect_top"]]
    sub = types.ModuleType("inspect_evals.scbench"); sub.__path__ = [req["inspect_pkg"]]
    sys.modules["inspect_evals"] = top; sys.modules["inspect_evals.scbench"] = sub
    graders = importlib.import_module("inspect_evals.scbench.graders")
result = graders.GRADER_REGISTRY[req["type"]]().evaluate_answer(req["answer"], req["config"])
score = getattr(result, "score", None)
passed = bool(result.passed)
print(json.dumps({"passed": passed, "score": float(score) if score is not None else float(passed)}))
"""


# ----------------------------------------------------------------------------- the adapter

def volume_listing(sub_path: str) -> dict[str, int]:
    """File name -> size in bytes for the files in one folder of the Volume; {} if there is none."""
    import modal
    from modal.volume import FileEntryType
    try:
        entries = modal.Volume.from_name(VOLUME).listdir(sub_path)
    except (modal.exception.NotFoundError, FileNotFoundError):
        return {}
    return {pathlib.PurePosixPath(e.path).name: e.size for e in entries if e.type == FileEntryType.FILE}


def _is_current(study_id: str) -> bool:
    """sc01-sc06 are the current set (lab/adapters/scbench_data.py uses the same split)."""
    return study_id in [f"sc{i:02d}" for i in range(1, 7)]


class ScBench:
    name = "scbench-public"
    modal_only = True     # the data is only on the Modal Volume; the laptop runner cannot reach it

    def studies(self) -> list[Study]:
        """Each study's data_dir is where its agents see the data inside their sandbox."""
        if not PUBLIC.exists():
            raise RuntimeError("run `python3 -m lab.adapters.scbench stage` first")
        out = []
        for d in sorted(PUBLIC.iterdir()):
            if (d / "task.md").exists():
                manifest = json.loads((d / "data_manifest.json").read_text())
                data_dir = CURRENT_DATA_DIR if manifest["data_dir"] == CURRENT_DATA_DIR else MOUNT
                out.append(Study(id=d.name, title=d.name, statement=(d / "task.md").read_text(),
                                 data_dir=pathlib.Path(data_dir), deliverables=[DELIVERABLE]))
        return out

    def modal_spec(self, study: Study) -> dict:
        """The sandbox for this study's agents (see lab/backend_modal.py, ModalBackend).
        The current set's task text names /workspace/data and /workspace/eval_answer.json, so
        /workspace is made to point at the working folder and `data` in it at the mount. The
        older set's text says the files are in the working directory, so each is linked there."""
        manifest = self.data_manifest(study)
        if manifest["data_dir"] == CURRENT_DATA_DIR:
            links, alias = {"data": MOUNT}, WORKSPACE_ALIAS
        else:
            links, alias = {f["name"]: f"{MOUNT}/{f['name']}" for f in manifest["files"]}, None
        return {"packages": list(AGENT_PACKAGES), "cpu": AGENT_CPU, "memory_mb": AGENT_MEMORY_MB,
                "volumes": [{"name": VOLUME, "sub_path": manifest["volume_sub_path"], "mount": MOUNT}],
                "links": links, "workspace_alias": alias, "package_index": False}

    def missing_data(self, studies: list[Study], listing=None) -> list[str]:
        """Ids of the studies whose data files are not all on the Volume with the right size.
        `listing(sub_path)` gives {file name: size}; default: volume_listing, which asks Modal."""
        listing = listing or volume_listing
        missing = []
        for s in studies:
            manifest = self.data_manifest(s)
            present = listing(manifest["volume_sub_path"])
            if any(present.get(f["name"]) != f["size_bytes"] for f in manifest["files"]):
                missing.append(s.id)
        return missing

    @staticmethod
    def data_command(study_ids: list[str]) -> str:
        """The command that puts these studies' data on the Volume."""
        current = [_is_current(i) for i in study_ids]
        sets = "current" if all(current) else "canonical" if not any(current) else "all"
        return DATA_COMMAND.format(sets=sets)

    def blocked_terms(self) -> list[str]:
        """Words that mark a web result as being about this benchmark. The gateway drops such results."""
        terms = ["scbench", "sc-bench", "sc bench", "latch-eval-tools", "latch_eval_tools", "benchmarks.bio",
                 "latch-bench", "blog.latch.bio", "2602.09063"]
        if MAPPING.exists():
            terms += [entry["id"] for entry in json.loads(MAPPING.read_text()).values()]
        if SEALED.exists():
            for task in _load_tasks():
                terms += re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                                    str(task["record"].get("canary", "")))
        if PUBLIC.exists():   # source dataset accessions in data file names, for example GSE276492
            for manifest in PUBLIC.glob("*/data_manifest.json"):
                for f in json.loads(manifest.read_text())["files"]:
                    terms += re.findall(r"GSE\d+", f["name"] + " " + f.get("harness_name", ""))
        return list(dict.fromkeys(t.lower() for t in terms))

    def schema(self, study: Study) -> dict:
        return json.loads((PUBLIC / study.id / "answer_schema.json").read_text())

    def data_manifest(self, study: Study) -> dict:
        return json.loads((PUBLIC / study.id / "data_manifest.json").read_text())

    def problems(self, study: Study, deliverable: str, candidate: pathlib.Path) -> list[str]:
        answer, found = _load_answer(candidate)
        if answer is None:
            return found
        schema = self.schema(study)
        for key, kind in schema.items():
            value = answer.get(key)
            if key not in answer:
                found.append(f'missing answer field "{key}"')
            elif kind == "number" and not _is_number(value):
                found.append(f'"{key}" must be a finite number')
            elif kind == "string" and not (isinstance(value, str) and value.strip()):
                found.append(f'"{key}" must be a non-empty string')
            elif kind == "list" and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                found.append(f'"{key}" must be a list of strings')
            elif kind == "mapping" and not (isinstance(value, dict) and value
                                            and all(_is_number(v) for v in value.values())):
                found.append(f'"{key}" must be a non-empty object of names to finite numbers')
        for key in answer:
            if key not in schema:
                found.append(f'unexpected answer field "{key}"')
        return found

    def field_agreement(self, study: Study, a: pathlib.Path, b: pathlib.Path) -> dict[str, bool]:
        """Field by field: do two candidates give the same answer? Our own rule, never the grader's
        tolerances: numbers within 5 percent; strings equal ignoring case and spaces; lists by
        Jaccard overlap of at least 0.8; mappings by the same keys and each value within 5 percent."""
        answer_a, _ = _load_answer(a)
        answer_b, _ = _load_answer(b)
        result = {}
        for key, kind in self.schema(study).items():
            va, vb = (answer_a or {}).get(key), (answer_b or {}).get(key)
            if va is None or vb is None:
                result[key] = False
            elif kind == "number":
                result[key] = _is_number(va) and _is_number(vb) and _close(va, vb)
            elif kind == "string":
                result[key] = isinstance(va, str) and isinstance(vb, str) and _norm(va) == _norm(vb)
            elif kind == "list":
                result[key] = isinstance(va, list) and isinstance(vb, list) and _jaccard(va, vb) >= JACCARD_AGREEMENT
            elif kind == "mapping":
                if not (isinstance(va, dict) and isinstance(vb, dict)):
                    result[key] = False
                    continue
                na, nb = {_norm(k): v for k, v in va.items()}, {_norm(k): v for k, v in vb.items()}
                result[key] = na.keys() == nb.keys() and all(
                    _is_number(na[k]) and _is_number(nb[k]) and _close(na[k], nb[k]) for k in na)
            else:
                result[key] = va == vb
        return result

    def agreement(self, study: Study, deliverable: str, a: pathlib.Path, b: pathlib.Path) -> float:
        fields = self.field_agreement(study, a, b)
        return sum(fields.values()) / len(fields) if fields else 0.0

    def grade(self, study: Study, final_dir: pathlib.Path) -> dict:
        """Score with the benchmark's own grader. Call only after every agent has exited.
        Reads eval_answer.json, or answer.json (the name the lab's grading copies candidates to).
        A missing or malformed answer, or a grader error, is a fail, never a crash."""
        final_dir = pathlib.Path(final_dir)
        candidate = next((final_dir / n for n in (DELIVERABLE, "answer.json") if (final_dir / n).is_file()), None)
        if candidate is None:
            return {"passed": False, "score": 0.0, "error": "no eval_answer.json"}
        answer, found = _load_answer(candidate)
        if answer is None:
            return {"passed": False, "score": 0.0, "error": found[0]}
        entry = json.loads(MAPPING.read_text())[study.id]
        grader = json.loads((SEALED / entry["file"]).read_text()).get("grader") or {}
        try:
            verdict = _benchmark_grader(entry["set"], grader.get("type"), grader.get("config") or {}, answer)
            return {"passed": bool(verdict["passed"]), "score": float(verdict["score"])}
        except Exception as err:   # a malformed answer must count as a fail, not crash the run
            return {"passed": False, "score": 0.0, "error": f"grader failed: {type(err).__name__}: {err}"[:500]}


if __name__ == "__main__":
    if sys.argv[1:] == ["stage"]:
        ids = stage()
        print(f"staged {len(ids)} tasks under {PUBLIC.relative_to(ROOT)}: {', '.join(ids)}")
    elif sys.argv[1:] == ["grader-env"]:
        print(f"grading environment ready: {make_grader_env()}")
    else:
        sys.exit("usage: python3 -m lab.adapters.scbench stage | grader-env")
