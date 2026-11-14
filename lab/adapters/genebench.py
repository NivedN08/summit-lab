"""GeneBench-Pro public case studies (OpenAI, June 2026): 10 genomics problems on small tables.

Each problem gives messy simulated data and asks for one JSON answer. The published grader
passes a problem only when every answer field is right.

The public package ships the answers, the grader tolerances and a worked report next to the
data, and the problem folder names hint at the intended method. So this adapter splits it:

  data/genebench/sealed/   the package exactly as downloaded. No agent may read it.
  data/genebench/public/   what agents get: gb01 ... gb10, each with task.md, answer_schema.json
                           and the data files. Neutral names, no titles, no answers.

`grade` is the only function that opens the sealed side, and the lab calls it only after
every agent has exited.

Stage the public side once with:  python3 -m lab.adapters.genebench stage
"""

from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import shutil
import sys

from lab.contracts import Study

ROOT = pathlib.Path(__file__).resolve().parents[2]
SEALED = ROOT / "data" / "genebench" / "sealed"
PUBLIC = ROOT / "data" / "genebench" / "public"
MAPPING = SEALED / "summit_mapping.json"   # neutral id -> real problem folder; sealed
RELATIVE_TOLERANCE = 0.05   # two teams "agree" on a number when they are within 5 percent


def stage() -> list[str]:
    """Build the public side from the sealed package. Returns the neutral ids."""
    problems = sorted(p for p in (SEALED / "problems").iterdir() if (p / "eval_config.json").exists())
    configs = {p.name: json.loads((p / "eval_config.json").read_text()) for p in problems}
    # Order by the package's random uuid so the neutral numbering carries no meaning.
    ordered = sorted(configs, key=lambda name: configs[name]["eval_uuid"])
    mapping = {}
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    for i, name in enumerate(ordered, 1):
        neutral = f"gb{i:02d}"
        mapping[neutral] = name
        out = PUBLIC / neutral
        (out / "data").mkdir(parents=True)
        config = configs[name]
        (out / "task.md").write_text(config["task"].strip() + "\n")
        schema = {key: _type_name(value) for key, value in config["ground_truth"].items()}
        (out / "answer_schema.json").write_text(json.dumps(schema, indent=2) + "\n")
        for f in sorted((SEALED / "problems" / name / "data_files").iterdir()):
            if f.is_file():
                shutil.copy2(f, out / "data" / f.name)
    MAPPING.write_text(json.dumps(mapping, indent=2) + "\n")
    return list(mapping)


def _type_name(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    return "object"


def _load_answer(path: pathlib.Path):
    """Return (answer dict, problem list). Accepts the benchmark's {"answer": {...}} form."""
    try:
        doc = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as err:
        return None, [f"cannot read JSON: {err}"]
    if not isinstance(doc, dict) or not isinstance(doc.get("answer"), dict):
        return None, ['top level must be an object with an "answer" object inside']
    return doc["answer"], []


class GeneBench:
    name = "genebench-pro-public"

    def studies(self) -> list[Study]:
        if not PUBLIC.exists():
            raise RuntimeError("run `python3 -m lab.adapters.genebench stage` first")
        out = []
        for d in sorted(PUBLIC.iterdir()):
            if (d / "task.md").exists():
                out.append(Study(id=d.name, title=d.name, statement=(d / "task.md").read_text(),
                                 data_dir=d / "data", deliverables=["answer.json"]))
        return out

    def schema(self, study: Study) -> dict:
        return json.loads((PUBLIC / study.id / "answer_schema.json").read_text())

    def problems(self, study: Study, deliverable: str, candidate: pathlib.Path) -> list[str]:
        answer, found = _load_answer(candidate)
        if answer is None:
            return found
        schema = self.schema(study)
        for key, kind in schema.items():
            if key not in answer:
                found.append(f'missing answer field "{key}"')
            elif kind == "number" and (isinstance(answer[key], bool) or not isinstance(answer[key], (int, float))
                                       or not math.isfinite(answer[key])):
                found.append(f'"{key}" must be a finite number')
            elif kind == "string" and not isinstance(answer[key], str):
                found.append(f'"{key}" must be a string')
        for key in answer:
            if key not in schema:
                found.append(f'unexpected answer field "{key}"')
        return found

    def field_agreement(self, study: Study, a: pathlib.Path, b: pathlib.Path) -> dict[str, bool]:
        """Field by field: do two candidates give the same answer?
        Uses our own 5 percent rule, not the grader's tolerances, which no agent may see."""
        answer_a, _ = _load_answer(a)
        answer_b, _ = _load_answer(b)
        result = {}
        for key, kind in self.schema(study).items():
            va = (answer_a or {}).get(key)
            vb = (answer_b or {}).get(key)
            if va is None or vb is None:
                result[key] = False
            elif kind == "number" and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (va, vb)):
                scale = max(abs(va), abs(vb))
                result[key] = scale == 0 or abs(va - vb) <= RELATIVE_TOLERANCE * scale
            elif kind == "string":
                result[key] = str(va).strip().lower() == str(vb).strip().lower()
            else:
                result[key] = va == vb
        return result

    def agreement(self, study: Study, deliverable: str, a: pathlib.Path, b: pathlib.Path) -> float:
        fields = self.field_agreement(study, a, b)
        return sum(fields.values()) / len(fields) if fields else 0.0

    def grade(self, study: Study, final_dir: pathlib.Path) -> dict:
        """Score with the package's own reference grader. Call only after every agent has exited."""
        real = json.loads(MAPPING.read_text())[study.id]
        config = json.loads((SEALED / "problems" / real / "eval_config.json").read_text())
        spec = importlib.util.spec_from_file_location("genebench_reference_grader", SEALED / "reference_grader.py")
        grader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(grader)
        candidate = pathlib.Path(final_dir) / "answer.json"
        try:
            submission = json.loads(candidate.read_text())
        except (OSError, ValueError) as err:
            return {"passed": False, "score": 0.0, "error": f"no usable answer.json: {err}"}
        try:
            verdict = grader.evaluate(config, submission)
        except Exception as err:   # a malformed answer must count as a fail, not crash the run
            return {"passed": False, "score": 0.0, "error": f"grader raised {type(err).__name__}: {err}"}
        return {"passed": bool(verdict.get("passed")), "score": float(verdict.get("score", 0.0))}


if __name__ == "__main__":
    if sys.argv[1:] == ["stage"]:
        ids = stage()
        print(f"staged {len(ids)} problems under {PUBLIC.relative_to(ROOT)}: {', '.join(ids)}")
    else:
        sys.exit("usage: python3 -m lab.adapters.genebench stage")
