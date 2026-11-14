"""Command line for the lab.  Run as `python3 -m lab.run COMMAND ...`.

  lab       run studies through the lab protocol
  baseline  run single-agent attempts per study
  grade     grade the lab's final answers, every baseline attempt and the vote over attempts
  report    write <run_dir>/report.md in plain words

Every command works on one run directory: `events.jsonl` (the event log), `memory.jsonl` (local
memory), `run.json` (what was run), one folder per study, `baseline/`, `results.json`, `report.md`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

from lab.eventlog import JsonlEventLog
from lab.protocol import Config, Lab, unfinished_agents

NETWORK_HOSTS = ["pypi.org", "files.pythonhosted.org", "conda.anaconda.org", "repo.anaconda.com", "cran.r-project.org"]


def _genebench():
    from lab.adapters.genebench import GeneBench
    return GeneBench()


ADAPTERS = {"genebench": _genebench}     # benchmark name -> zero-argument factory


def load_adapter(name: str):
    if name not in ADAPTERS:
        raise SystemExit(f"unknown benchmark {name!r}; known: {', '.join(sorted(ADAPTERS))}")
    return ADAPTERS[name]()


def mock_script(adapter):
    """A scripted stand-in for every agent, for a free end-to-end check of the loop.
    Team B first disagrees with team A, then agrees after the question round ("converged")."""

    def answer(study_id: str, number: float) -> dict:
        schema = adapter.schema(next(s for s in adapter.studies() if s.id == study_id)) \
            if hasattr(adapter, "schema") else {}
        values = {k: (number if kind == "number" else "mock" if kind == "string" else None) for k, kind in schema.items()}
        return {"answer": values, "reasoning": "mock agent"}

    def script(agent, brief, workspace):
        study_id = workspace.parent.name
        if "# Your job: director, briefing the teams" in brief:
            return {"pass_down": [{"from": [], "text": "PITFALL (mock): check for repeated sample ids.",
                                   "reason": "mock briefing"}], "left_out": []}
        if "# Your job: director, after the teams disagreed" in brief:
            return {"differences_found": ["mock"], "likely_cause": "mock", "questions": [
                {"to": f"{study_id}-team-b", "from": [],
                 "question": "QUESTION FROM THE DIRECTOR: the other team checked for repeated rows. Does that change your method?"}]}
        if "# Your job: the lab's library" in brief:
            return {"pitfalls": [], "searches_made": 0, "notes": "mock library"}
        if "# Your job: referee" in brief:
            return {"pick": "X", "reason": "mock referee", "test": "none", "own_answer": None}
        revising = "# Your job: analyst, answering the director's questions" in brief
        number = 2.0 if agent.id.endswith("team-b") and not revising else 1.0
        (workspace / "answer.json").write_text(json.dumps(answer(study_id, number), indent=2))
        return {"answer_file": "answer.json", "method": "mock", "warning_signs": [], "confidence": 0.0}

    return script


def make_backend(kind: str, adapter=None):
    """Import the backend only when it is used, so the module may be missing until then."""
    if kind == "claude":
        from lab.backend_claude import ClaudeBackend
        sealed = pathlib.Path(__file__).resolve().parents[1] / "data" / "genebench" / "sealed"
        return ClaudeBackend(allowed_hosts=list(NETWORK_HOSTS), deny_read=[sealed])
    if kind == "mock":
        from lab.backend_mock import MockBackend
        return MockBackend(mock_script(adapter))
    raise SystemExit(f"unknown backend {kind!r}")


def make_memory(kind: str, log, run_dir: pathlib.Path):
    if kind == "local":
        from lab.memory_local import LocalMemory
        return LocalMemory(log, run_dir / "memory.jsonl")
    if kind == "past":
        from lab.memory_past import PastMemory
        slug = re.sub(r"[^a-z0-9]+", "-", run_dir.name.lower()).strip("-")
        return PastMemory(log, ("sl-" + slug)[:31].rstrip("-"))
    raise SystemExit(f"unknown memory {kind!r}")


def pick_studies(adapter, ids: str | None):
    studies = adapter.studies()
    if not ids:
        return studies
    wanted = [x.strip() for x in ids.split(",") if x.strip()]
    known = {s.id: s for s in studies}
    missing = [x for x in wanted if x not in known]
    if missing:
        raise SystemExit(f"unknown studies: {', '.join(missing)}")
    return [known[x] for x in wanted]


def note_run(run_dir: pathlib.Path, **info) -> None:
    """Merge what was run into run.json, so `grade` knows the benchmark."""
    path = run_dir / "run.json"
    current = json.loads(path.read_text()) if path.exists() else {}
    for key, value in info.items():
        if isinstance(value, list) and isinstance(current.get(key), list):
            value = list(dict.fromkeys(current[key] + value))
        current[key] = value
    path.write_text(json.dumps(current, indent=2) + "\n")


# ----- commands -----

def cmd_lab(args) -> int:
    run_dir = pathlib.Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = JsonlEventLog(run_dir / "events.jsonl")
    adapter = load_adapter(args.benchmark)
    studies = pick_studies(adapter, args.studies)
    config = Config(max_agents=args.parallel, max_searches=args.max_searches, library=not args.no_library,
                    questions=not args.no_questions, referee=not args.no_referee,
                    director_model=args.director_model, worker_model=args.model)
    note_run(run_dir, benchmark=args.benchmark, lab_studies=[s.id for s in studies], memory=args.memory,
             backend=args.backend)
    backend = make_backend(args.backend, adapter)
    memory = make_memory(args.memory, log, run_dir)
    out = Lab(adapter, backend, memory, log, run_dir, config).run(studies)
    for sid, info in out.items():
        extra = " (already finished)" if info.get("skipped") else ""
        print(f"{sid}: {info['status']}{extra}")
    return 1 if any(info["status"] == "error" for info in out.values()) else 0


def cmd_baseline(args) -> int:
    from lab.baseline import run_baselines
    run_dir = pathlib.Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = JsonlEventLog(run_dir / "events.jsonl")
    adapter = load_adapter(args.benchmark)
    studies = pick_studies(adapter, args.studies)
    note_run(run_dir, benchmark=args.benchmark, baseline_studies=[s.id for s in studies],
             baseline_attempts=args.attempts, baseline_model=args.model)
    backend = make_backend(args.backend, adapter)
    out = run_baselines(adapter, backend, log, run_dir, studies, args.attempts, args.model, args.parallel,
                        timeout_s=args.timeout)
    for sid, runs in out.items():
        print(f"{sid}: " + (", ".join(f"attempt-{k} {'ok' if ok else 'failed'}" for k, ok in sorted(runs.items()))
                            or "nothing to run"))
    return 0


def grade_run(run_dir: pathlib.Path, adapter) -> dict:
    """Grade every final answer, baseline attempt and vote in a run directory. Refuses while any
    agent has started and not finished. Writes results.json and returns it."""
    from lab.baseline import combine_attempts
    log = JsonlEventLog(run_dir / "events.jsonl")
    open_runs = unfinished_agents(log)
    if open_runs:
        names = ", ".join(sorted({f"{e.get('agent')} ({e.get('phase')})" for e in open_runs}))
        raise RuntimeError(f"refusing to grade: {len(open_runs)} agent run(s) started and not finished: {names}")

    def graded(study, folder: pathlib.Path) -> dict:
        if not (folder / "answer.json").is_file():
            return {"passed": False, "score": 0.0, "error": "no answer.json"}
        return adapter.grade(study, folder)

    results = {"benchmark": getattr(adapter, "name", None), "studies": {}}
    for study in adapter.studies():
        entry = {}
        record_path = run_dir / study.id / "study.json"
        if record_path.is_file():
            record = json.loads(record_path.read_text())
            entry["lab"] = {"status": record.get("status"), "final": record.get("final"),
                            "grade": graded(study, run_dir / study.id / "final")}
        base = run_dir / "baseline" / study.id
        attempts = sorted((p for p in base.glob("attempt-*") if p.is_dir()),
                          key=lambda p: int(p.name.split("-")[1])) if base.is_dir() else []
        if attempts:
            entry["baseline"] = {"attempts": {p.name: graded(study, p) for p in attempts}}
            voted = combine_attempts(adapter, study, [p / "answer.json" for p in attempts])
            vote_dir = base / "vote"
            if voted is not None:
                vote_dir.mkdir(parents=True, exist_ok=True)
                (vote_dir / "answer.json").write_text(json.dumps(voted, indent=2) + "\n")
                entry["baseline"]["vote"] = {"answer": voted, **graded(study, vote_dir)}
            else:
                entry["baseline"]["vote"] = {"passed": False, "score": 0.0, "error": "no valid attempt to vote over"}
        if entry:
            results["studies"][study.id] = entry
    s = results["studies"].values()
    lab_rows = [e["lab"] for e in s if "lab" in e]
    attempt_rows = [g for e in s if "baseline" in e for g in e["baseline"]["attempts"].values()]
    vote_rows = [e["baseline"]["vote"] for e in s if "baseline" in e]
    results["totals"] = {
        "lab_passed": sum(bool(r["grade"].get("passed")) for r in lab_rows), "lab_studies": len(lab_rows),
        "baseline_attempts_passed": sum(bool(g.get("passed")) for g in attempt_rows),
        "baseline_attempts": len(attempt_rows),
        "vote_passed": sum(bool(v.get("passed")) for v in vote_rows), "vote_studies": len(vote_rows)}
    (run_dir / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    log.append("grade.done", "lab", totals=results["totals"])
    return results


def print_table(results: dict) -> None:
    def mark(g):
        return "-" if g is None else ("pass" if g.get("passed") else "fail")

    print(f"{'study':<8} {'lab status':<14} {'lab':<5} {'attempts passed':<16} vote")
    for sid, e in results["studies"].items():
        lab = e.get("lab") or {}
        b = e.get("baseline")
        attempts = f"{sum(bool(g.get('passed')) for g in b['attempts'].values())}/{len(b['attempts'])}" if b else "-"
        print(f"{sid:<8} {str(lab.get('status') or '-'):<14} {mark(lab.get('grade')):<5} {attempts:<16} "
              f"{mark(b['vote']) if b else '-'}")
    t = results["totals"]
    print(f"\nlab {t['lab_passed']}/{t['lab_studies']} studies; single agents {t['baseline_attempts_passed']}/"
          f"{t['baseline_attempts']} attempts; vote {t['vote_passed']}/{t['vote_studies']} studies")


def cmd_grade(args) -> int:
    run_dir = pathlib.Path(args.run_dir)
    benchmark = args.benchmark
    if not benchmark:
        info = run_dir / "run.json"
        if not info.is_file():
            print("no run.json in the run directory; pass --benchmark", file=sys.stderr)
            return 2
        benchmark = json.loads(info.read_text())["benchmark"]
    try:
        results = grade_run(run_dir, load_adapter(benchmark))
    except RuntimeError as err:
        print(err, file=sys.stderr)
        return 2
    print_table(results)
    return 0


def cmd_report(args) -> int:
    from lab.report import write_report
    path = write_report(pathlib.Path(args.run_dir))
    print(f"wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m lab.run", description="Run, grade and report the lab")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lab", help="run studies through the lab")
    p.add_argument("--benchmark", required=True)
    p.add_argument("--studies", help="comma-separated study ids (default: all)")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--memory", choices=["past", "local"], default="local")
    p.add_argument("--backend", choices=["claude", "mock"], default="claude")
    p.add_argument("--parallel", type=int, default=4, help="agents running at once, across all studies")
    p.add_argument("--model", default="opus", help="model for the workers")
    p.add_argument("--director-model", default="fable")
    p.add_argument("--max-searches", type=int, default=15)
    p.add_argument("--no-library", action="store_true")
    p.add_argument("--no-questions", action="store_true")
    p.add_argument("--no-referee", action="store_true")
    p.set_defaults(fn=cmd_lab)

    p = sub.add_parser("baseline", help="run single-agent attempts")
    p.add_argument("--benchmark", required=True)
    p.add_argument("--studies")
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--model", default="opus")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--backend", choices=["claude", "mock"], default="claude")
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--timeout", type=int, default=3600, help="seconds per attempt")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("grade", help="grade a finished run")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--benchmark", help="default: the one recorded in run.json")
    p.set_defaults(fn=cmd_grade)

    p = sub.add_parser("report", help="write report.md")
    p.add_argument("--run-dir", required=True)
    p.set_defaults(fn=cmd_report)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
