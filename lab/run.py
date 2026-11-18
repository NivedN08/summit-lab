"""Command line for the lab.  Run as `python3 -m lab.run COMMAND ...`.

  lab       run studies through the lab protocol
  baseline  run single-agent attempts per study
  grade     grade every candidate answer: each team's in each round, the referee's, team C's,
            the final answer, every baseline attempt and the votes over attempts
  measures  compute the lab's measures (explanation.md, D-12) from results.json into measures.json
  report    write <run_dir>/report.md in plain words
  prereg    freeze a batch's pre-registration (prereg/<batch>.json) or check it (lab/prereg.py)
  replay    rebuild each study's decision from events.jsonl and check it against study.json
  arm       run a control arm: --arm self (self-challenge) or --arm vote (same-cost vote), lab/arms.py
  loop      the improvement loop (lab/autoloop.py): propose a move, run it on the dev panel against
            the incumbent, accept it only when code says it beat noise; up to --max-moves moves

Version 4: `lab --preset v4` turns on the "Not verifiable" re-check and the damage guard
(--recheck, --guard); `arm` takes the same two flags.

The improvement loop's settings (lab/autoloop.py), each off by default: `lab` and `arm` take
--prompt-dir (prompt overrides); `lab` also takes --config-file (Config fields from a variant's
config.json), --snapshot-first-round (keep each first team's workspace after the analysis round)
and --reuse-first-round RUN_DIR (copy that run's first round instead of running it).

Guardrails: `lab`, `baseline` and `arm` take --max-usd, a hard dollar cap for the run directory
(lab/budget.py), required with the claude and modal backends; --prereg <spec> checks the run
against a frozen pre-registration before anything starts; `grade --ledger` appends the grades
to the research ledger (lab/ledger.py).

Every command works on one run directory: `events.jsonl` (the event log), `memory.jsonl` (local
memory), `run.json` (what was run), one folder per study, `baseline/`, `arms/`, `results.json`,
`measures.json`, `report.md`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

from lab.eventlog import JsonlEventLog
from lab.protocol import Config, Lab, answer_file, unfinished_agents

NETWORK_HOSTS = ["pypi.org", "files.pythonhosted.org", "conda.anaconda.org", "repo.anaconda.com", "cran.r-project.org"]


def _genebench():
    from lab.adapters.genebench import GeneBench
    return GeneBench()


def _scbench():
    from lab.adapters.scbench import ScBench
    return ScBench()


def _biodsa(convention: bool = False, split: str = "dev"):
    def factory():
        from lab.adapters.biodsa import BioDSA
        return BioDSA(split=split, convention=convention)
    return factory


def _planted(split: str):
    """A factory for the planted-fault suite. It takes `mounted`: tables on the Modal Volume (the
    default) or in the local public folder (load_adapter passes mounted=False off Modal)."""
    def factory(mounted: bool = True):
        from lab.adapters.planted import Planted
        return Planted(split, mounted=mounted)
    factory.local_option = True
    return factory


# benchmark name -> factory, called with no argument (or, for one with local_option, `mounted`)
ADAPTERS = {"genebench": _genebench, "scbench": _scbench, "biodsa": _biodsa(),
            "biodsa-convention": _biodsa(convention=True),
            "biodsa-heldout": _biodsa(split="heldout"),
            "biodsa-heldout-convention": _biodsa(convention=True, split="heldout"),
            "planted": _planted("all"), "planted-dev": _planted("dev"), "planted-heldout": _planted("heldout")}


def load_adapter(name: str, backend: str | None = None):
    """The benchmark's adapter. With a backend, a factory that can point agents at local tables
    (local_option) mounts them only on Modal; the mock and claude backends read the local copy."""
    if name not in ADAPTERS:
        raise SystemExit(f"unknown benchmark {name!r}; known: {', '.join(sorted(ADAPTERS))}")
    factory = ADAPTERS[name]
    if backend is not None and getattr(factory, "local_option", False):
        return factory(mounted=backend == "modal")
    return factory()


MOCK_LABELS = {0.0: "Not verifiable", 1.0: "True", 2.0: "False"}   # mock numbers as BioDSA-style labels


def mock_evidence(workspace: pathlib.Path) -> None:
    """What a careful mock agent leaves for the damage guard: a script that computes its numbers, its
    output, and evidence.json quoting the output's numbers."""
    (workspace / "mock_check.py").write_text("print('p = %.4f' % (1 / 100))\nprint('n = %d' % (12 * 10))\n")
    (workspace / "mock_check_output.txt").write_text("p = 0.0100\nn = 120\n")
    (workspace / "evidence.json").write_text(json.dumps({"field": "label", "script": "mock_check.py",
                                                         "output_file": "mock_check_output.txt",
                                                         "result": "p = 0.01, n = 120"}, indent=2))


def mock_script(adapter):
    """A scripted stand-in for every agent, for a free end-to-end check of the loop.
    Team B first disagrees with team A, then agrees after the question round ("converged").
    Answers go in the study's answer file, wrapped in {"answer": ...} unless the benchmark's format
    check wants the bare object (scBench).

    For a benchmark with BioDSA's labels (lab.protocol.LABEL_ADAPTERS), team A and the self arm's
    solver first answer "Not verifiable", team B "False" and everyone else "True"; the re-check
    writes test_result.json and answers "True"; revising and re-checking agents leave evidence for
    the damage guard. The self-challenge arm's director always asks one question."""
    from lab.protocol import LABEL_ADAPTERS, TEST_FILE
    labels = getattr(adapter, "name", None) in LABEL_ADAPTERS or callable(getattr(adapter, "abstains", None))

    def write_answer(study, path: pathlib.Path, number: float) -> None:
        schema = adapter.schema(study) if hasattr(adapter, "schema") else {}
        mock = {"number": number, "string": MOCK_LABELS[number] if labels else "mock", "list": ["mock"],
                "mapping": {"mock": number}}
        values = {k: mock.get(kind) for k, kind in schema.items()}
        path.write_text(json.dumps({"answer": values, "reasoning": "mock agent"}, indent=2))
        if adapter.problems(study, path.name, path):
            path.write_text(json.dumps(values, indent=2))

    def script(agent, brief, workspace):
        if PROPOSER_HEADING in brief:
            return mock_proposer(brief, workspace)
        study_id = workspace.parent.name
        if "# Your job: director, challenging one analyst's answer" in brief:
            return {"untested_assumptions": [], "unused_evidence": [], "unresolved_weak_points": [],
                    "second_readings": [], "verdict": "mock: one question", "questions": [
                        {"question": "QUESTION FROM THE DIRECTOR: check for repeated sample ids. "
                                     "Does that change your method?"}]}
        if "# Your job: re-check a Not verifiable answer" in brief:
            study = next(s for s in adapter.studies() if s.id == study_id)
            mock_evidence(workspace)
            (workspace / TEST_FILE).write_text(json.dumps({"test": "mock t-test", "variables": ["mock: column"],
                                                           "statistic": 2.6, "p_value": 0.01, "n": 120,
                                                           "direction": "supports"}, indent=2))
            write_answer(study, workspace / answer_file(study), 1.0)
            return {"answer_file": answer_file(study), "variables_found": ["mock: column"], "test_file": TEST_FILE,
                    "changed": True, "method": "mock re-check", "confidence": 0.0}
        if "# Your job: director, briefing the teams" in brief:
            return {"pass_down": [{"from": [], "text": "PITFALL (mock): check for repeated sample ids.",
                                   "reason": "mock briefing"}], "left_out": []}
        if "# Your job: director, after the teams disagreed" in brief:
            return {"differences_found": ["mock"], "likely_cause": "mock", "questions": [
                {"to": f"{study_id}-team-b", "from": [],
                 "question": "QUESTION FROM THE DIRECTOR: the other team checked for repeated rows. Does that change your method?"}]}
        if "# Your job: director, challenging an answer both teams agree on" in brief:
            return {"shared_assumptions": [], "unused_evidence": [], "unresolved_weak_points": [],
                    "second_readings": [], "questions": [], "verdict": "mock: nothing to ask"}
        if "# Your job: the lab's library" in brief:
            return {"pitfalls": [], "searches_made": 0, "notes": "mock library"}
        if "# Your job: research on demand" in brief:
            return {"findings": []}
        if "# Your job: referee" in brief:
            return {"pick": "X", "reason": "mock referee", "test": "none", "own_answer": None}
        revising = "# Your job: analyst, answering the director's questions" in brief
        number = 2.0 if agent.id.endswith("team-b") and not revising else 1.0
        if labels and not revising and (agent.id.endswith("team-a") or agent.id == f"self-{study_id}"):
            number = 0.0
        study = next(s for s in adapter.studies() if s.id == study_id)
        write_answer(study, workspace / answer_file(study), number)
        if labels and revising:
            mock_evidence(workspace)
        return {"answer_file": answer_file(study), "method": "mock", "warning_signs": [], "confidence": 0.0}

    return script


PROPOSER_HEADING = "# Your job: propose one change to the lab"
# The mock proposer's moves, in turn by how many moves the loop has proposed. The second is not a
# switch a move may flip, so the loop falls back to its rules there: both paths run in a mock loop.
MOCK_MOVES = (
    {"kind": "threshold", "target": "recheck_p", "value": 0.1, "rationale": "mock: a looser p threshold"},
    {"kind": "toggle", "target": "max_agents", "value": True, "rationale": "mock: not a switch a move may flip"},
    {"kind": "add_check", "target": "guard_strict_decimals", "value": True, "rationale": "mock: stricter quotes"},
    {"kind": "edit_section", "target": "analyst_revise.md#Working limits", "rationale": "mock: one more limit",
     "value": "- You have {time_limit_minutes} minutes in total. When they run out you are stopped, and only what is "
              "in `{answer_file}` at that moment counts.\n- Check every number you report against the output that "
              "printed it."},
)


def mock_proposer(brief: str, workspace: pathlib.Path) -> dict:
    """The mock proposer: writes the next of MOCK_MOVES (by the brief's count of moves so far) as move.json."""
    found = re.search(r"Moves proposed so far: (\d+)", brief)
    move = MOCK_MOVES[int(found.group(1) if found else 0) % len(MOCK_MOVES)]
    (workspace / "move.json").write_text(json.dumps(move, indent=2))
    return {"move_file": "move.json", "lesson": "mock: no lesson"}


def check_can_run(adapter, backend: str, studies) -> None:
    """Stop before anything starts when the benchmark cannot run on this backend, or when a
    requested study's data is not where its agents will look for it."""
    if backend == "claude" and getattr(adapter, "modal_only", False):
        raise SystemExit(f"{adapter.name} runs only with --backend modal: its data is on a Modal Volume, "
                         "which the laptop runner cannot reach (--backend mock checks the loop for free)")
    if backend == "modal" and hasattr(adapter, "missing_data"):
        missing = adapter.missing_data(studies)
        if missing:
            raise SystemExit(f"the data for {', '.join(missing)} is not on the Modal Volume yet. "
                             f"Put it there first, from the repository root: {adapter.data_command(missing)}")


def make_backend(kind: str, adapter=None, studies=()):
    """Import the backend only when it is used, so the module may be missing until then.
    On Modal, an adapter with `modal_spec` sets the sandbox for each of its studies."""
    if kind == "claude":
        from lab.backend_claude import ClaudeBackend
        data = pathlib.Path(__file__).resolve().parents[1] / "data"
        return ClaudeBackend(allowed_hosts=list(NETWORK_HOSTS),
                             deny_read=[data / "genebench" / "sealed", data / "planted" / "sealed"])
    if kind == "mock":
        from lab.backend_mock import MockBackend
        return MockBackend(mock_script(adapter))
    if kind == "modal":
        from lab.backend_modal import ModalBackend
        specs = {s.id: adapter.modal_spec(s) for s in studies} if hasattr(adapter, "modal_spec") else {}
        return ModalBackend(specs=specs)   # its own host list: Anthropic's API and the Python package index
    raise SystemExit(f"unknown backend {kind!r}")


def stop_modal_on_interrupt(backend, lab=None) -> None:
    """With the Modal backend, Ctrl-C terminates every agent sandbox and the gateway sandbox, then exits.
    Agents cut off this way have agent.start without agent.finish, like any crashed run."""
    import os
    import signal

    def handler(signum, frame):
        print("\ninterrupted: terminating Modal sandboxes", file=sys.stderr)
        backend.shutdown()
        gateway = getattr(lab, "gateway", None)
        if gateway is not None:
            try:
                gateway.stop()
            except Exception as err:
                print(f"could not stop the gateway sandbox cleanly: {err}", file=sys.stderr)
        os._exit(130)

    signal.signal(signal.SIGINT, handler)


def brief_teams(text: str) -> tuple:
    """Parse --brief-teams: a comma-separated list drawn from team-a and team-b."""
    teams = tuple(dict.fromkeys(t.strip() for t in text.split(",") if t.strip()))
    wrong = [t for t in teams if t not in ("team-a", "team-b")]
    if wrong or not teams:
        raise SystemExit(f"--brief-teams takes team-a, team-b or both, comma-separated; got {text!r}")
    return teams


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

def make_config(args) -> Config:
    """The lab's Config from the `lab` command's flags. --no-search turns off every web search:
    the library (and with it the briefing), team A's search and research on demand."""
    no_search = bool(getattr(args, "no_search", False))
    config = Config(**v4_switches(args), max_agents=args.parallel, max_searches=args.max_searches,
                    library=not (args.no_library or no_search), questions=not args.no_questions,
                    referee=not args.no_referee, challenge=not args.no_challenge,
                    team_search=not (args.no_team_search or no_search), research=not (args.no_research or no_search),
                    brief_teams=brief_teams(args.brief_teams), director_model=args.director_model,
                    worker_model=args.model, team_b_model=args.team_b_model, max_usd=getattr(args, "max_usd", None),
                    prompt_dir=prompt_dir(args), snapshot_first_round=bool(getattr(args, "snapshot_first_round", False)),
                    reuse_first_round=reuse_dir(args))
    return with_config_file(config, getattr(args, "config_file", None))


# Config fields a --config-file may not set: they belong to one run and have their own flags.
RUN_ONLY_FIELDS = ("max_agents", "max_usd", "prompt_dir", "reuse_first_round", "snapshot_first_round")


def with_config_file(config: Config, path) -> Config:
    """Config with the fields of a JSON file (a variant's config.json, lab/autoloop.py) in place of
    the flags' values. Unknown fields and the run-only ones (RUN_ONLY_FIELDS) are refused."""
    if not path:
        return config
    import dataclasses
    try:
        doc = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as err:
        raise SystemExit(f"--config-file {path}: {err}")
    if not isinstance(doc, dict):
        raise SystemExit(f"--config-file {path} must hold a JSON object of Config fields")
    known = {f.name for f in dataclasses.fields(Config)}
    wrong = sorted(set(doc) - known)
    if wrong:
        raise SystemExit(f"--config-file {path} names fields that are not Config fields: {', '.join(wrong)}")
    run_only = sorted(set(doc) & set(RUN_ONLY_FIELDS))
    if run_only:
        raise SystemExit(f"--config-file {path} may not set {', '.join(run_only)}: use their flags")
    values = {**dataclasses.asdict(config), **doc}
    values["brief_teams"] = tuple(values["brief_teams"])
    values["checks"] = tuple(values.get("checks") or ())
    try:
        return Config(**values)
    except (TypeError, ValueError) as err:
        raise SystemExit(f"--config-file {path}: {err}")


def prompt_dir(args) -> str | None:
    """--prompt-dir as an absolute path, refused when it is not a folder."""
    value = getattr(args, "prompt_dir", None)
    if not value:
        return None
    path = pathlib.Path(value).resolve()
    if not path.is_dir():
        raise SystemExit(f"--prompt-dir {value} is not a folder")
    return str(path)


def reuse_dir(args) -> str | None:
    """--reuse-first-round as an absolute path, refused when it is not a run directory."""
    value = getattr(args, "reuse_first_round", None)
    if not value:
        return None
    path = pathlib.Path(value).resolve()
    if not (path / "events.jsonl").is_file():
        raise SystemExit(f"--reuse-first-round {value} is not a lab run directory (no events.jsonl)")
    return str(path)


PRESETS = {"v4": {"recheck": True, "guard": True}}   # --preset name -> the Config switches it turns on


def v4_switches(args) -> dict:
    """The re-check and guard switches from --recheck, --guard and --preset (each off by default)."""
    preset = PRESETS.get(getattr(args, "preset", None) or "", {})
    return {k: bool(getattr(args, k, False) or preset.get(k, False)) for k in ("recheck", "guard")}


PAID_BACKENDS = ("claude", "modal")


def require_max_usd(args) -> None:
    """A run that spends money must say how much it may spend: --max-usd is required with the
    claude and modal backends, and optional with mock."""
    if args.backend in PAID_BACKENDS and args.max_usd is None:
        raise SystemExit(f"--backend {args.backend} needs --max-usd: a hard dollar cap for this run directory, "
                         "counting what it has already spent. For example --max-usd 30")
    if args.max_usd is not None and args.max_usd <= 0:
        raise SystemExit("--max-usd must be more than 0")


def check_prereg(args, studies, config) -> str | None:
    """With --prereg, refuse to start unless the run matches the frozen spec. Returns the lock's hash."""
    if not getattr(args, "prereg", None):
        return None
    from lab import prereg
    try:
        return prereg.check(args.prereg, benchmark=args.benchmark, studies=[s.id for s in studies], config=config)
    except prereg.PreregError as err:
        raise SystemExit(str(err))


def note_prereg(log, run_dir: pathlib.Path, args, sha: str | None, command: str) -> None:
    """Log the passed pre-registration check and record its hash in run.json."""
    if sha is None:
        return
    log.append("prereg.checked", "lab", spec=str(args.prereg), sha256=sha, command=command)
    note_run(run_dir, prereg={"spec": str(args.prereg), "sha256": sha})


def print_halt(runner) -> None:
    """Say why the run stopped when a usage limit or the dollar cap halted it, and how to go on."""
    halted = getattr(runner, "halted", None)
    if halted:
        print(f"halted: {halted}\nUnfinished studies have no final answer; run the same command again "
              "(with a higher --max-usd if the cap stopped it) to resume them.", file=sys.stderr)


def cmd_lab(args) -> int:
    adapter = load_adapter(args.benchmark, args.backend)
    studies = pick_studies(adapter, args.studies)
    check_can_run(adapter, args.backend, studies)
    if args.backend == "modal" and args.memory != "past":    # checked before anything is written
        raise SystemExit("--backend modal needs --memory past: the gateway runs on Modal, so a local "
                         "memory file on this laptop would not see the agents' facts")
    require_max_usd(args)
    config = make_config(args)
    prereg_sha = check_prereg(args, studies, config)
    run_dir = pathlib.Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = JsonlEventLog(run_dir / "events.jsonl")
    note_run(run_dir, benchmark=args.benchmark, lab_studies=[s.id for s in studies], memory=args.memory,
             backend=args.backend)
    note_prereg(log, run_dir, args, prereg_sha, "lab")
    gateway_factory = None
    if args.backend == "modal":
        from lab.modal_gateway import ModalGateway
        gateway_factory = ModalGateway
    backend = make_backend(args.backend, adapter, studies)
    memory = make_memory(args.memory, log, run_dir)
    lab = Lab(adapter, backend, memory, log, run_dir, config, gateway_factory=gateway_factory)
    if args.backend == "modal":
        stop_modal_on_interrupt(backend, lab)
    try:
        out = lab.run(studies)
    finally:
        if hasattr(backend, "shutdown"):
            backend.shutdown()
    for sid, info in out.items():
        extra = " (already finished)" if info.get("skipped") else ""
        print(f"{sid}: {info['status']}{extra}")
    print_halt(getattr(lab, "runner", None))
    return 1 if any(info["status"] == "error" for info in out.values()) else 0


def cmd_baseline(args) -> int:
    from lab import baseline
    from lab.limits import UsageLimit
    adapter = load_adapter(args.benchmark, args.backend)
    studies = pick_studies(adapter, args.studies)
    check_can_run(adapter, args.backend, studies)
    require_max_usd(args)
    prereg_sha = check_prereg(args, studies, None)    # a baseline has no lab Config to compare
    run_dir = pathlib.Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = JsonlEventLog(run_dir / "events.jsonl")
    note_run(run_dir, benchmark=args.benchmark, baseline_studies=[s.id for s in studies],
             baseline_attempts=args.attempts, baseline_model=args.model)
    note_prereg(log, run_dir, args, prereg_sha, "baseline")
    backend = make_backend(args.backend, adapter, studies)
    if args.backend == "modal":
        stop_modal_on_interrupt(backend)
    try:
        out = baseline.run_baselines(adapter, backend, log, run_dir, studies, args.attempts, args.model,
                                     args.parallel, timeout_s=args.timeout, max_usd=args.max_usd)
    except UsageLimit as err:     # the account's usage limit or the dollar cap: stop, resumable
        print(f"halted: {err}\nAttempts not marked done run again when the same command is repeated "
              "(with a higher --max-usd if the cap stopped it).", file=sys.stderr)
        return 1
    finally:
        if hasattr(backend, "shutdown"):
            backend.shutdown()
    for sid, runs in out.items():
        print(f"{sid}: " + (", ".join(f"attempt-{k} {'ok' if ok else 'failed'}" for k, ok in sorted(runs.items()))
                            or "nothing to run"))
    return 0


def candidate_id(team: str, round_: str) -> str:
    """The name of one lab answer in results.json, matching its file in `<study>/candidates/`."""
    return "referee-own" if team == "referee" else f"{team}-{round_}"


ARM_DIRS = {"attempt": "baseline", "self": "arms/self", "vote": "arms/vote"}   # row kind -> folder of the arm


def grade_run(run_dir: pathlib.Path, adapter) -> dict:
    """Grade every candidate answer in a run directory: each team's answer in each round, the
    referee's own answer, team C's, the final answer, every baseline attempt, the vote over all
    attempts and the vote over the first j attempts for each j, and the control arms in ARM_DIRS:
    each answer of the self-challenge arm and its final answer (rows of kind "self"), and each
    vote-arm attempt and the vote over them (rows of kind "vote"; the vote's id is "vote-arm").
    Refuses while any agent has started and not finished. Writes results.json and returns it.

    Each study's `candidates` list in results.json has one row per graded answer, with who
    produced it, in which round, whether it passed, and the adapter's word for what sort of
    outcome it was ("outcome_kind", None when the adapter gives none)."""
    from lab.baseline import combine_attempts
    log = JsonlEventLog(run_dir / "events.jsonl")
    open_runs = unfinished_agents(log)
    if open_runs:
        names = ", ".join(sorted({f"{e.get('agent')} ({e.get('phase')})" for e in open_runs}))
        raise RuntimeError(f"refusing to grade: {len(open_runs)} agent run(s) started and not finished: {names}")

    def graded(study, folder: pathlib.Path) -> dict:
        if not (folder / answer_file(study)).is_file():
            return {"passed": False, "score": 0.0, "error": f"no {answer_file(study)}"}
        return adapter.grade(study, folder)

    def graded_file(study, source: pathlib.Path, folder: pathlib.Path) -> dict:
        """Grade one answer file by copying it to `folder/<answer file>`, the shape the grader reads.
        A grader that raises on a malformed answer counts as a fail, so one bad answer cannot stop grading."""
        if not source.is_file() or not source.read_text().strip():
            return {"passed": False, "score": 0.0, "error": "no answer file"}
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, folder / answer_file(study))
        try:
            return graded(study, folder)
        except Exception as err:
            return {"passed": False, "score": 0.0, "error": f"grader raised {type(err).__name__}: {err}"[:500]}

    def vote(study, paths: list, folder: pathlib.Path) -> dict:
        voted = combine_attempts(adapter, study, paths)
        if voted is None:
            return {"passed": False, "score": 0.0, "error": "no valid attempt to vote over"}
        folder.mkdir(parents=True, exist_ok=True)
        (folder / answer_file(study)).write_text(json.dumps(voted, indent=2) + "\n")
        return {"answer": voted, **graded(study, folder)}

    def row(base: dict, grade: dict) -> dict:
        """One results.json row: who and which round, then the grade. The grade's own "kind" (what sort
        of right or wrong, when the adapter says) goes in "outcome_kind", since "kind" names the row's kind."""
        return {**base, **{k: v for k, v in grade.items() if k != "kind"}, "outcome_kind": grade.get("kind")}

    results = {"benchmark": getattr(adapter, "name", None), "studies": {}}
    for study in adapter.studies():
        entry, rows = {}, []
        study_dir = run_dir / study.id
        record_path = study_dir / "study.json"
        if record_path.is_file():
            record = json.loads(record_path.read_text())
            for c in record.get("candidates") or []:
                cid = candidate_id(c.get("team"), c.get("round"))
                source = study_dir / "candidates" / pathlib.Path(c.get("path") or f"{cid}.json").name
                rows.append(row({"id": cid, "kind": "lab", "who": c.get("agent"), "team": c.get("team"),
                                 "round": c.get("round"), "valid": bool(c.get("valid")),
                                 **({"not_used": c["not_used"]} if c.get("not_used") else {})},
                                graded_file(study, source, study_dir / "graded" / cid)))
            final = record.get("final")
            entry["lab"] = {"status": record.get("status"), "final": final,
                            "grade": graded(study, study_dir / "final")}
            rows.append(row({"id": "final", "kind": "final", "who": final.get("agent") if final else None,
                             "team": final.get("team") if final else None, "round": "final",
                             "same_as": candidate_id(final.get("team"), final.get("round")) if final else None,
                             "status": record.get("status")}, entry["lab"]["grade"]))
        base = run_dir / ARM_DIRS["attempt"] / study.id
        attempts = sorted((p for p in base.glob("attempt-*") if p.is_dir()),
                          key=lambda p: int(p.name.split("-")[1])) if base.is_dir() else []
        if attempts:
            entry["baseline"] = {"attempts": {p.name: graded(study, p) for p in attempts}}
            paths = [p / answer_file(study) for p in attempts]
            entry["baseline"]["vote"] = vote(study, paths, base / "vote")
            entry["baseline"]["votes_over_first"] = {
                str(j): vote(study, paths[:j], base / f"vote-first-{j}") for j in range(1, len(paths) + 1)}
            for p in attempts:
                k = p.name.split("-")[1]
                rows.append(row({"id": p.name, "kind": "attempt", "who": f"baseline-{study.id}-{k}",
                                 "team": "single agent", "round": p.name}, entry["baseline"]["attempts"][p.name]))
            v = entry["baseline"]["vote"]
            rows.append(row({"id": "vote", "kind": "vote", "who": "vote", "team": "single agent", "round": "vote"},
                            {key: val for key, val in v.items() if key != "answer"}))
        arm_dir = run_dir / ARM_DIRS["self"] / study.id
        arm_record = load_json(arm_dir / "arm.json")
        if isinstance(arm_record, dict):
            for c in arm_record.get("candidates") or []:
                cid = f"self-{c.get('round')}"
                rows.append(row({"id": cid, "kind": "self", "who": c.get("agent"), "team": "self-challenge arm",
                                 "round": c.get("round"), "valid": bool(c.get("valid")),
                                 **({"not_used": c["not_used"]} if c.get("not_used") else {})},
                                graded_file(study, arm_dir / "candidates" / f"{cid}.json", arm_dir / "graded" / cid)))
            final = arm_record.get("final")
            entry["self"] = {"status": arm_record.get("status"), "final": graded(study, arm_dir / "final"),
                             "final_round": final.get("round") if final else None}
            rows.append(row({"id": "self-final", "kind": "self", "who": f"self-{study.id}", "team": "self-challenge arm",
                             "round": "final", "same_as": final.get("id") if final else None,
                             "status": arm_record.get("status")}, entry["self"]["final"]))
        vote_base = run_dir / ARM_DIRS["vote"] / study.id
        vote_attempts = sorted((p for p in vote_base.glob("attempt-*") if p.is_dir()),
                               key=lambda p: int(p.name.split("-")[1])) if vote_base.is_dir() else []
        if vote_attempts:
            entry["vote_arm"] = {"attempts": {p.name: graded(study, p) for p in vote_attempts}}
            entry["vote_arm"]["vote"] = vote(study, [p / answer_file(study) for p in vote_attempts], vote_base / "vote")
            for p in vote_attempts:
                rows.append(row({"id": f"vote-{p.name}", "kind": "vote", "who": f"vote-{study.id}-{p.name.split('-')[1]}",
                                 "team": "same-cost vote arm", "round": p.name}, entry["vote_arm"]["attempts"][p.name]))
            v = entry["vote_arm"]["vote"]
            rows.append(row({"id": "vote-arm", "kind": "vote", "who": "vote", "team": "same-cost vote arm",
                             "round": "vote"}, {key: val for key, val in v.items() if key != "answer"}))
        if entry:
            entry["candidates"] = rows
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
    self_rows = [e["self"]["final"] for e in s if "self" in e]
    arm_votes = [e["vote_arm"]["vote"] for e in s if "vote_arm" in e]
    if self_rows:       # the arms' totals only when the run has them, so older results.json keep their shape
        results["totals"].update(self_passed=sum(bool(g.get("passed")) for g in self_rows), self_studies=len(self_rows))
    if arm_votes:
        results["totals"].update(vote_arm_passed=sum(bool(g.get("passed")) for g in arm_votes),
                                 vote_arm_studies=len(arm_votes))
    (run_dir / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    log.append("grade.done", "lab", totals=results["totals"])
    return results


def load_json(path: pathlib.Path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


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
    if "self_studies" in t or "vote_arm_studies" in t:
        arms = []
        if "self_studies" in t:
            arms.append(f"self-challenge arm {t['self_passed']}/{t['self_studies']} studies")
        if "vote_arm_studies" in t:
            arms.append(f"same-cost vote arm {t['vote_arm_passed']}/{t['vote_arm_studies']} studies")
        print("arms: " + "; ".join(arms))


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
    if args.ledger:      # opt in: --ledger for ledger/ledger.jsonl ($LAB_LEDGER), --ledger PATH for another file
        from lab import ledger
        path = None if args.ledger is True else pathlib.Path(args.ledger)
        written = ledger.record_grading(run_dir, results, path)
        print(f"ledger: {len(written)} entries appended to {path or ledger.default_path()}")
    return 0


def cmd_measures(args) -> int:
    from lab import measures
    run_dir = pathlib.Path(args.run_dir)
    try:
        result = measures.measure(run_dir)
        other = measures.measure(pathlib.Path(args.compare)) if args.compare else None
    except measures.NotGraded as err:
        print(err, file=sys.stderr)
        return 2
    if other is not None:
        result["compared_with"] = {"run": other["run"], "headline": other["headline"]}
    (run_dir / "measures.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    print("\n".join(measures.summary_lines(result)))
    if other is not None:
        print()
        print("\n".join(measures.compare_lines(result, other)))
    return 0


def cmd_report(args) -> int:
    from lab.report import write_report
    path = write_report(pathlib.Path(args.run_dir))
    print(f"wrote {path}")
    return 0


def cmd_prereg(args) -> int:
    from lab import prereg
    try:
        if args.action == "freeze":
            sha = prereg.freeze(args.spec)
            spec = prereg.load_spec(args.spec)
            print(f"frozen {spec['batch']}: {sha}\nwrote {prereg.lock_path(args.spec, spec)}")
            if args.ledger:
                from lab import ledger
                path = None if args.ledger is True else pathlib.Path(args.ledger)
                ledger.append("batch.registered", {"batch": spec["batch"], "spec": str(args.spec), "sha256": sha,
                                                   "benchmark": spec["benchmark"], "studies": spec["studies"]}, path)
                print(f"ledger: batch.registered appended to {path or ledger.default_path()}")
        else:
            sha = prereg.check(args.spec)
            print(f"matches its lock: {sha}")
    except prereg.PreregError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


def cmd_replay(args) -> int:
    from lab.replay import replay
    run_dir = pathlib.Path(args.run_dir)
    if not (run_dir / "events.jsonl").is_file():
        print(f"no events.jsonl in {run_dir}", file=sys.stderr)
        return 2
    mismatches = replay(run_dir, args.study)
    print(f"\n{mismatches} mismatch(es)" if mismatches else "\nevery rebuilt decision matches study.json")
    return 1 if mismatches else 0


def cmd_arm(args) -> int:
    """Run a control arm (lab/arms.py) into <run_dir>/arms/."""
    from lab import arms
    from lab.limits import UsageLimit
    adapter = load_adapter(args.benchmark, args.backend)
    studies = pick_studies(adapter, args.studies)
    check_can_run(adapter, args.backend, studies)
    require_max_usd(args)
    if args.arm == "vote" and bool(args.attempts) == bool(args.match_cost):
        raise SystemExit("--arm vote takes either --attempts K or --match-cost LAB_RUN_DIR")
    if args.arm == "self" and (args.attempts or args.match_cost):
        raise SystemExit("--attempts and --match-cost are for --arm vote")
    config = Config(**v4_switches(args), max_agents=args.parallel, worker_model=args.model, max_usd=args.max_usd,
                    prompt_dir=prompt_dir(args))
    k, matched = args.attempts, None
    if args.match_cost:
        try:
            k, matched = arms.match_cost_k(pathlib.Path(args.match_cost))
        except ValueError as err:
            raise SystemExit(f"--match-cost: {err}")
        print(f"k = {k}: the lab cost ${matched['lab_per_study']:.2f} per study and one attempt "
              f"${matched['per_attempt']:.2f} (ratio {matched['ratio']}, at least {arms.MIN_VOTE_K})")
    prereg_sha = check_prereg(args, studies, config)
    run_dir = pathlib.Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = JsonlEventLog(run_dir / "events.jsonl")
    if args.arm == "vote":
        note_run(run_dir, benchmark=args.benchmark, vote_arm_studies=[s.id for s in studies], vote_arm_attempts=k,
                 vote_arm_model=args.model, **({"vote_arm_match_cost": {"from": str(args.match_cost), **matched}}
                                               if matched else {}))
    else:
        note_run(run_dir, benchmark=args.benchmark, self_arm_studies=[s.id for s in studies], self_arm_model=args.model,
                 self_arm_switches={"recheck": config.recheck, "guard": config.guard})
    note_prereg(log, run_dir, args, prereg_sha, f"arm {args.arm}")
    backend = make_backend(args.backend, adapter, studies)
    if args.backend == "modal":
        stop_modal_on_interrupt(backend)
    try:
        if args.arm == "vote":
            out = arms.run_vote_arm(adapter, backend, log, run_dir, studies, k, args.model, args.parallel,
                                    timeout_s=args.timeout, max_usd=args.max_usd, prompt_dir=config.prompt_dir)
        else:
            out = arms.run_self_arm(adapter, backend, log, run_dir, studies, args.model, args.parallel, config,
                                    timeout_s=args.timeout, max_usd=args.max_usd)
    except UsageLimit as err:     # the account's usage limit or the dollar cap: stop, resumable
        print(f"halted: {err}\nRun the same command again (with a higher --max-usd if the cap stopped it) to "
              "resume what is not done.", file=sys.stderr)
        return 1
    finally:
        if hasattr(backend, "shutdown"):
            backend.shutdown()
    for sid, info in sorted(out.items()):
        if args.arm == "vote":
            print(f"{sid}: " + (", ".join(f"attempt-{n} {'ok' if ok else 'failed'}" for n, ok in sorted(info.items()))
                                or "nothing to run"))
        else:
            final = (info.get("final") or {}).get("round")
            print(f"{sid}: {info['status']}" + (" (already finished)" if info.get("skipped") else "")
                  + (f", final answer from the {final} round" if final else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The command line: lab, baseline, grade, measures, report, prereg, replay and arm."""
    parser = argparse.ArgumentParser(prog="python3 -m lab.run", description="Run, grade and report the lab")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lab", help="run studies through the lab")
    p.add_argument("--benchmark", required=True)
    p.add_argument("--studies", help="comma-separated study ids (default: all)")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--memory", choices=["past", "local"], default="local")
    p.add_argument("--backend", choices=["claude", "mock", "modal"], default="claude")
    p.add_argument("--parallel", type=int, default=4, help="agents running at once, across all studies")
    p.add_argument("--model", default="fable", help="model for the workers: library, teams and referee")
    p.add_argument("--team-b-model", default=None, help="model for team B (default: the same as --model)")
    p.add_argument("--director-model", default="fable")
    p.add_argument("--max-searches", type=int, default=12, help="Exa calls the library may make in its first pass")
    p.add_argument("--no-library", action="store_true")
    p.add_argument("--no-team-search", action="store_true", help="team A's analyst may not search the web")
    p.add_argument("--no-research", action="store_true", help="no research on demand in the question rounds")
    p.add_argument("--no-search", action="store_true",
                   help="no web search at all: no library, no team search, no research on demand")
    p.add_argument("--no-questions", action="store_true")
    p.add_argument("--no-referee", action="store_true")
    p.add_argument("--no-challenge", action="store_true",
                   help="report agreeing first answers as replicated without the director's challenge")
    p.add_argument("--brief-teams", default="team-a",
                   help="teams that receive the director's briefing, comma-separated (default: team-a)")
    add_v4(p)
    add_prompt_dir(p)
    p.add_argument("--config-file", metavar="JSON", default=None,
                   help="Config fields from a JSON file (a variant's config.json), in place of the flags' values")
    p.add_argument("--snapshot-first-round", action="store_true",
                   help="keep each first team's workspace as it stands after the analysis round, in "
                        "<study>/first-round/, so a later run can reuse it (Config.snapshot_first_round)")
    p.add_argument("--reuse-first-round", metavar="RUN_DIR", default=None,
                   help="copy that run's first round (library, briefing, analysis) instead of running it; "
                        "every later step runs live (Config.reuse_first_round)")
    add_guardrails(p)
    p.set_defaults(fn=cmd_lab)

    p = sub.add_parser("baseline", help="run single-agent attempts")
    p.add_argument("--benchmark", required=True)
    p.add_argument("--studies")
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--model", default="fable")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--backend", choices=["claude", "mock", "modal"], default="claude")
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--timeout", type=int, default=1800, help="seconds per attempt (the same limit a lab analyst gets)")
    add_guardrails(p)
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("grade", help="grade a finished run")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--benchmark", help="default: the one recorded in run.json")
    p.add_argument("--ledger", nargs="?", const=True, default=False, metavar="PATH",
                   help="append batch.graded and one failure entry per failed study to the research ledger "
                        "(default ledger/ledger.jsonl)")
    p.set_defaults(fn=cmd_grade)

    p = sub.add_parser("measures", help="compute measures.json from a graded run")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--compare", help="another graded run directory, to print both headlines side by side")
    p.set_defaults(fn=cmd_measures)

    p = sub.add_parser("report", help="write report.md")
    p.add_argument("--run-dir", required=True)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("prereg", help="freeze or check a pre-registration spec (prereg/<batch>.json)")
    p.add_argument("action", choices=["freeze", "check"])
    p.add_argument("spec")
    p.add_argument("--ledger", nargs="?", const=True, default=False, metavar="PATH",
                   help="with freeze: append batch.registered to the research ledger")
    p.set_defaults(fn=cmd_prereg)

    p = sub.add_parser("replay", help="rebuild each study's decision from the event log and check study.json")
    p.add_argument("run_dir")
    p.add_argument("--study", help="one study id (default: every study in the log)")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("arm", help="run a control arm: the self-challenge arm or the same-cost vote arm")
    p.add_argument("--arm", required=True, choices=["self", "vote"])
    p.add_argument("--benchmark", required=True)
    p.add_argument("--studies", help="comma-separated study ids (default: all)")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--backend", choices=["claude", "mock", "modal"], default="claude")
    p.add_argument("--parallel", type=int, default=4, help="agents running at once, across all studies")
    p.add_argument("--model", default="fable", help="model for every agent of the arm")
    p.add_argument("--timeout", type=int, default=1800, help="seconds per agent run (the same limit a lab analyst gets)")
    p.add_argument("--attempts", type=int, default=None, help="--arm vote: attempts per study")
    p.add_argument("--match-cost", metavar="LAB_RUN_DIR", default=None,
                   help="--arm vote: k = that lab run's mean dollars per study / mean dollars per single attempt "
                        "(at least 3)")
    add_v4(p)
    add_prompt_dir(p)
    add_guardrails(p)
    p.set_defaults(fn=cmd_arm)

    p = sub.add_parser("loop", help="the improvement loop: propose, evaluate on the dev panel, accept by code")
    p.add_argument("--benchmark", required=True)
    p.add_argument("--studies", required=True, help="the dev panel: comma-separated study ids")
    p.add_argument("--incumbent-run", required=True, metavar="RUN_DIR",
                   help="a finished lab run of the incumbent on the panel, made with --snapshot-first-round")
    p.add_argument("--run-root", required=True, help="folder for the loop's runs, variants and (with mock) ledger")
    p.add_argument("--backend", choices=["claude", "mock", "modal"], default="mock")
    p.add_argument("--memory", choices=["past", "local"], default="local")
    p.add_argument("--parallel", type=int, default=4, help="agents running at once in each evaluation run")
    p.add_argument("--max-usd", type=float, default=None,
                   help="total dollar cap across every move; required with --backend claude or modal")
    p.add_argument("--move-max-usd", type=float, default=None, help="dollar cap per evaluation run")
    p.add_argument("--max-moves", type=int, default=8)
    p.add_argument("--noise-threshold", type=float, default=None,
                   help="T: the net rescues a move needs (default 2 until Phase 1 measures it)")
    p.add_argument("--noise-threshold-source", default=None, help="where T came from, for the ledger")
    p.add_argument("--cost-ratio-max", type=float, default=1.25)
    p.add_argument("--max-damage", type=int, default=1)
    p.add_argument("--max-rejections", type=int, default=3, help="stop after this many rejections in a row")
    p.add_argument("--seed-moves", default=None, metavar="FILE",
                   help="moves tried first, in order, before the proposer (see read_seed_moves)")
    p.add_argument("--proposer-evidence", action="store_true",
                   help="show the proposer the failed tasks' labels and stated reasons (numbered, not named)")
    p.add_argument("--variants-dir", default=None, help="default: <run-root>/variants")
    p.add_argument("--incumbent", default=None, metavar="VARIANT_DIR",
                   help="the incumbent variant (default: <variants-dir>/v4-base, made from --preset v4)")
    p.add_argument("--model", default="opus", help="v4-base's workers (when it is made here)")
    p.add_argument("--director-model", default="opus", help="v4-base's director (when it is made here)")
    p.add_argument("--proposer-model", default="opus")
    p.add_argument("--ledger", default=None,
                   help="the research ledger to append to (default: ledger/ledger.jsonl; with --backend mock, "
                        "<run-root>/ledger.jsonl)")
    p.add_argument("--lessons", choices=["local", "past"], default=None,
                   help="also keep the proposer's lessons in memory (optional; the ledger is the main memory)")
    p.add_argument("--resume-loop", default=None, metavar="LOOP_ID",
                   help="continue a loop that crashed or halted mid-evaluation, from its ledger entries "
                        "(the same --run-root and settings; spend so far counts against --max-usd)")
    p.set_defaults(fn=cmd_loop)
    return parser


def add_prompt_dir(p) -> None:
    p.add_argument("--prompt-dir", default=None, metavar="DIR",
                   help="a folder of prompt overrides: DIR/<name>.md replaces lab/prompts/<name>.md (Config.prompt_dir)")


def read_seed_moves(path: str | None) -> list:
    """The seed moves file: {"moves": [move, ...]} or a bare list of moves, each {"kind", "target",
    "value", "rationale"}; an edit's or a bundle part's value may be {"from_variant": DIR} to take the
    value of the edit that variant's move.json made to the same target."""
    if not path:
        return []
    doc = json.loads(pathlib.Path(path).read_text())
    moves = doc.get("moves") if isinstance(doc, dict) else doc
    if not isinstance(moves, list):
        raise SystemExit(f"{path}: seed moves are a list")
    base = pathlib.Path(path).resolve().parent

    def resolve(m: dict) -> dict:
        m = dict(m)
        if m.get("kind") == "bundle" and isinstance(m.get("value"), list):
            m["value"] = [resolve(x) for x in m["value"]]
        elif isinstance(m.get("value"), dict) and "from_variant" in m["value"]:
            folder = (base / m["value"]["from_variant"]).resolve()
            made = json.loads((folder / "move.json").read_text())
            if made.get("kind") != m.get("kind") or made.get("target") != m.get("target"):
                raise SystemExit(f"{path}: {folder}/move.json is {made.get('kind')} {made.get('target')}, "
                                 f"not {m.get('kind')} {m.get('target')}")
            m["value"] = made["value"]
        return m
    return [resolve(m) for m in moves]


def cmd_loop(args) -> int:
    """Run the improvement loop (lab/autoloop.py) and print its ledger summary."""
    from lab import autoloop
    from lab import ledger as ledger_mod
    require_max_usd(args)
    if args.backend == "modal" and args.memory != "past":
        raise SystemExit("--backend modal needs --memory past")
    adapter = load_adapter(args.benchmark, args.backend)
    studies = pick_studies(adapter, args.studies)
    root = pathlib.Path(args.run_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    variants = pathlib.Path(args.variants_dir or root / "variants").resolve()
    if args.incumbent:
        incumbent = pathlib.Path(args.incumbent).resolve()
    else:
        base_args = build_parser().parse_args(["lab", "--benchmark", args.benchmark, "--run-dir", str(root),
                                               "--preset", "v4", "--model", args.model,
                                               "--director-model", args.director_model])
        incumbent = autoloop.make_base_variant(variants, make_config(base_args))
    problems = autoloop.check_incumbent_run(incumbent, pathlib.Path(args.incumbent_run), [s.id for s in studies])
    if problems:
        raise SystemExit(f"--incumbent-run {args.incumbent_run} does not stand for the incumbent {incumbent.name}:\n"
                         + "\n".join(f"  - {x}" for x in problems)
                         + f"\nMake it with: python3 -m lab.run lab --benchmark {args.benchmark} --studies "
                         f"{args.studies} --config-file {incumbent / 'config.json'} --prompt-dir {incumbent / 'prompts'} "
                         "--snapshot-first-round --run-dir <dir> --backend ... --max-usd ...")
    if args.ledger:
        ledger_path = pathlib.Path(args.ledger)
    else:
        ledger_path = root / "ledger.jsonl" if args.backend == "mock" else ledger_mod.default_path()
    given = args.noise_threshold is not None
    settings = autoloop.LoopSettings(
        benchmark=args.benchmark, panel=[s.id for s in studies], incumbent_run=pathlib.Path(args.incumbent_run),
        root=root, backend=args.backend, memory=args.memory, parallel=args.parallel, max_usd=args.max_usd,
        move_max_usd=args.move_max_usd, max_moves=args.max_moves,
        noise_threshold=args.noise_threshold if given else 2,
        noise_source=args.noise_threshold_source or ("given with --noise-threshold" if given
                                                     else autoloop.DEFAULT_T_SOURCE),
        cost_ratio_max=args.cost_ratio_max, max_damage=args.max_damage, ledger_path=ledger_path,
        variants_dir=variants, incumbent=incumbent, proposer_model=args.proposer_model, lessons=args.lessons,
        max_rejections=args.max_rejections, seed_moves=read_seed_moves(args.seed_moves),
        proposer_evidence=args.proposer_evidence)
    loop = autoloop.Loop(settings, adapter=adapter, resume=args.resume_loop)
    summary = loop.run()
    entries = [e for e in ledger_mod.entries(ledger_path) if (e.get("data") or {}).get("loop") == loop.id]
    print(f"\nloop {loop.id}: stopped ({summary['reason']}); {summary['moves']} move(s); "
          f"incumbent {summary['incumbent']}; ledger {ledger_path}")
    print("\n".join(autoloop.ledger_summary(entries)) or "no moves")
    return 0


def add_v4(p) -> None:
    """The version 4 switches `lab` and `arm` share, each off by default."""
    p.add_argument("--recheck", action="store_true",
                   help="re-check an answer judged Not verifiable against an inventory of the data (Config.recheck)")
    p.add_argument("--guard", action="store_true",
                   help="the damage guard: a changed answer needs evidence that code checks (Config.guard)")
    p.add_argument("--preset", choices=sorted(PRESETS), default=None, help="v4: --recheck and --guard")


def add_guardrails(p) -> None:
    """The flags `lab` and `baseline` share: the dollar cap and the pre-registration check."""
    p.add_argument("--max-usd", type=float, default=None,
                   help="hard dollar cap for the run directory, counting what it already spent; "
                        "required with --backend claude or modal")
    p.add_argument("--prereg", metavar="SPEC", help="refuse to start unless the run matches this frozen spec")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
