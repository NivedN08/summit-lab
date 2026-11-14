"""The single-agent baselines the lab is compared with (D-11).

A baseline agent gets baseline.md only: the task text and the data, no lab instructions, no
memory, no gateway and no web. Attempt k of study S runs in `<run_dir>/baseline/<S>/attempt-<k>/`.

`combine_attempts` is the "same budget, no organisation" baseline: a plain vote over several
attempts (median for numbers, most common value otherwise).
"""

from __future__ import annotations

import collections
import concurrent.futures
import json
import pathlib
import statistics

from lab.contracts import WORKER, Adapter, Agent, Backend, EventLog, Study
from lab.protocol import AgentRunner, load_doc, make_brief


def attempt_dir(run_dir: pathlib.Path, study_id: str, k: int) -> pathlib.Path:
    return pathlib.Path(run_dir) / "baseline" / study_id / f"attempt-{k}"


def run_baselines(adapter: Adapter, backend: Backend, log: EventLog, run_dir: pathlib.Path, studies: list[Study],
                  attempts: int, model: str, max_agents: int, timeout_s: int = 3600) -> dict:
    """Run `attempts` single agents per study, skipping attempts already done. Returns {study: {k: ok}}."""
    runner = AgentRunner(backend, log, max_agents)
    run_dir = pathlib.Path(run_dir).resolve()
    done = {(e["study"], e["attempt_number"]) for e in log.events("baseline.done")}
    jobs = [(s, k) for s in studies for k in range(1, attempts + 1) if (s.id, k) not in done]
    out: dict = {s.id: {} for s in studies}

    def one(study: Study, k: int) -> bool:
        agent = Agent(f"baseline-{study.id}-{k}", WORKER, "baseline", model)
        brief = make_brief("baseline", None, statement=study.statement, data_dir=str(study.data_dir))
        ws = attempt_dir(run_dir, study.id, k)
        result = runner.run(agent, brief, ws, readable=[study.data_dir], env=None, timeout_s=timeout_s,
                            study=study.id, phase=f"baseline-{k}", role="baseline",
                            brief_file=ws.parent / "briefs" / f"attempt-{k}.md")
        log.append("baseline.done", "lab", study=study.id, attempt_number=k, ok=result.ok)
        return result.ok

    if jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(len(jobs), max_agents))) as pool:
            futures = {pool.submit(one, s, k): (s.id, k) for s, k in jobs}
            for fut in concurrent.futures.as_completed(futures):
                sid, k = futures[fut]
                out[sid][k] = fut.result()
    return out


def combine_attempts(adapter: Adapter, study: Study, answer_paths: list[pathlib.Path]) -> dict | None:
    """A plain vote over valid attempts: median for numbers (a real attempt's value when all are
    whole numbers, so a code stays a code), most common value for strings and anything else.
    Returns an answer document in the attempts' own shape, or None if no attempt is valid."""
    deliverable = study.deliverables[0] if study.deliverables else "answer.json"
    docs = []
    for p in answer_paths:
        p = pathlib.Path(p)
        if p.is_file() and not adapter.problems(study, deliverable, p):
            docs.append(load_doc(p))
    if not docs:
        return None
    wrapped = isinstance(docs[0], dict) and isinstance(docs[0].get("answer"), dict)
    answers = [d["answer"] if wrapped else d for d in docs]
    keys = list(adapter.schema(study)) if hasattr(adapter, "schema") else list(dict.fromkeys(k for a in answers for k in a))
    voted = {}
    for key in keys:
        values = [a[key] for a in answers if key in a]
        if not values:
            continue
        numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(numbers) == len(values):
            voted[key] = statistics.median_low(numbers) if all(isinstance(v, int) for v in numbers) \
                else statistics.median(numbers)
        else:
            def norm(v):
                return v.strip().lower() if isinstance(v, str) else json.dumps(v, sort_keys=True)
            counts = collections.Counter(norm(v) for v in values)
            best = max(counts.values())
            voted[key] = next(v for v in values if counts[norm(v)] == best)   # ties go to the earliest attempt
    return {"answer": voted} if wrapped else voted
