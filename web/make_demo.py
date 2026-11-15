"""Choose the 5 minutes of lab activity the demo plays, and record the choice in public/runs/demo.json.

    python3 web/make_demo.py                         # the busiest 5 minutes across every baked run
    python3 web/make_demo.py --run test-v2           # the busiest 5 minutes of one run
    python3 web/make_demo.py --run results/<final> --last
                                                     # the last 5 minutes, ending on the grading:
                                                     # for when the final results are what we present

`--run` takes a baked run name (web/public/runs/<name>.json) or a run folder (results/<name> or
runs/<name>), which is baked first. Then rebuild the page: `npm run build` in web/.

"Busiest" scores each window by what a viewer sees move on the floor (web/src/world/engine.ts):
walks (arrivals, a librarian carrying findings to the director, the director or a head visiting a
desk), notes and answers in flight, the hatch comparing answers, referee verdicts and results. A
window with more kinds of interaction scores higher than one with many of the same kind.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from collections import Counter

WEB = pathlib.Path(__file__).resolve().parent
RUNS = WEB / "public" / "runs"
sys.path.insert(0, str(WEB))

# What one event is worth to a viewer: how much moves on the floor because of it.
WEIGHTS = {
    "arrival": 3,          # an agent's first session: it walks in from the elevator
    "briefing-visit": 4,   # the director's briefing starts: a librarian carries findings to the office
    "head-visit": 3,       # a head's review starts: it walks to the desk it reviews
    "relay.briefing": 3,
    "question": 4,         # a question passes the leak check: the director walks to a desk
    "held-back": 2,
    "memory.pass_down": 2,
    "candidate": 2,        # an answer flies to the hatch
    "compare": 5,          # the hatch lights up
    "referee.assign": 4,
    "referee.verdict": 5,
    "study.final": 4,
    "head.review": 3,
    "grade.done": 6,
    "memory.write": 1,
    "web.search": 1,
    "web.contents": 1,
    "session": 1,          # any later session starting
}
VARIETY = 3                # bonus per distinct kind of interaction in the window


def replay_end(run: dict) -> float:
    """Where the front end's replay ends (web/src/sim/replay.ts): 20 s after the last agent leaves
    for a finished run, with the grading pinned there; the whole log for a run still going."""
    last = 0.0
    for a in run["agents"]:
        for s in a["sessions"]:
            last = max(last, s["end"] if s["end"] is not None else s["start"])
    return min(run["duration"], last + 20) if run.get("finished") else run["duration"]


def interactions(run: dict) -> list[tuple[float, str]]:
    """(time, kind) for every event that makes something move on the floor."""
    end = replay_end(run)
    first_start = {}
    for a in run["agents"]:
        if a["sessions"]:
            first_start[a["id"]] = a["sessions"][0]["start"]
    out = []
    for e in run["events"]:
        k, t = e["k"], e["t"]
        if k == "grade.done":
            out.append((min(t, end), k))
        elif k == "agent.start":
            who = e.get("a", "")
            if first_start.get(who) == t and who != "director":
                out.append((t, "arrival"))
            elif who == "director" and e.get("phase") == "briefing":
                out.append((t, "briefing-visit"))
            elif who.startswith("head-") and e.get("phase") == "head-review":
                out.append((t, "head-visit"))
            else:
                out.append((t, "session"))
        elif k == "relay.leak_check":
            out.append((t, "question" if e.get("passed") else "held-back"))
        elif k in WEIGHTS:
            out.append((t, k))
    return sorted(out)


def score(events: list[tuple[float, str]], start: float, end: float) -> tuple[float, Counter]:
    kinds = Counter(k for t, k in events if start <= t <= end)
    return sum(WEIGHTS[k] * n for k, n in kinds.items()) + VARIETY * len(kinds), kinds


def busiest(run: dict, seconds: float, step: float = 5.0) -> tuple[float, float, float, Counter]:
    """The window of `seconds` with the highest score. A run shorter than that is shown whole."""
    end = replay_end(run)
    events = interactions(run)
    if end <= seconds:
        s, kinds = score(events, 0, end)
        return 0.0, end, s, kinds
    best = (-1.0, 0.0, Counter())
    t = 0.0
    while t + seconds <= end + 1e-6:
        s, kinds = score(events, t, t + seconds)
        if s > best[0]:
            best = (s, t, kinds)
        t += step
    s, start, kinds = best
    # Start a few seconds before the first event, so the clip does not open mid-action.
    first = next((et for et, _ in events if et >= start), start)
    start = max(0.0, min(start, first - 3))
    return start, min(end, start + seconds), s, kinds


def last(run: dict, seconds: float) -> tuple[float, float, float, Counter]:
    end = replay_end(run)
    start = max(0.0, end - seconds)
    s, kinds = score(interactions(run), start, end)
    return start, end, s, kinds


def load(name_or_dir: str) -> dict:
    """A baked run by name, or a run folder baked now (and added to the index)."""
    baked = RUNS / f"{name_or_dir}.json"
    if baked.is_file():
        return json.loads(baked.read_text())
    folder = pathlib.Path(name_or_dir)
    if not (folder / "events.jsonl").is_file():
        raise SystemExit(f"no baked run called {name_or_dir!r} and no events.jsonl in {folder}")
    import build_data
    run = build_data.build(folder)
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{run['run']}.json").write_text(json.dumps(run, separators=(",", ":")))
    index_path = RUNS / "index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else []
    index = [r for r in index if r["run"] != run["run"]]
    index.insert(0, {"run": run["run"], "label": run["label"], "started": run["started"], "studies": len(run["studies"]),
                     "agents": len(run["agents"]), "finished": run["finished"]})
    index_path.write_text(json.dumps(index, indent=1))
    print(f"baked {run['run']} from {folder}")
    return run


def clock(run: dict, t: float) -> str:
    start = dt.datetime.fromisoformat(run["started"].replace("Z", "+00:00"))
    when = start + dt.timedelta(seconds=t)
    pacific = when.astimezone(dt.timezone(dt.timedelta(hours=-8)))
    return pacific.strftime("%-I:%M:%S %p")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--run", help="a baked run name or a run folder (default: every baked run, busiest wins)")
    p.add_argument("--last", action="store_true", help="the last minutes of the run instead of the busiest")
    p.add_argument("--minutes", type=float, default=5.0)
    p.add_argument("--speed", type=int, default=4, help="replay speed the demo starts at (1, 4, 12, 30 or 60)")
    args = p.parse_args(argv)
    seconds = args.minutes * 60
    if args.run:
        candidates = [load(args.run)]
    else:
        index = json.loads((RUNS / "index.json").read_text())
        candidates = [json.loads((RUNS / f"{r['run']}.json").read_text()) for r in index]
    choices = []
    for run in candidates:
        start, end, s, kinds = (last if args.last else busiest)(run, seconds)
        choices.append((s, run, start, end, kinds))
        print(f"{run['run']:<16} {clock(run, start)} to {clock(run, end)}  score {s:.0f}")
    s, run, start, end, kinds = max(choices, key=lambda c: c[0])
    graded = any(e["k"] == "grade.done" for e in run["events"]) and end >= replay_end(run) - 1
    demo = {
        "run": run["run"], "label": run["label"], "start": round(start, 1), "end": round(end, 1),
        "minutes": args.minutes, "speed": args.speed, "mode": "last" if args.last else "busiest",
        "ends_with_results": graded, "score": round(s, 1), "interactions": dict(kinds.most_common()),
        "chosen": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    (RUNS / "demo.json").write_text(json.dumps(demo, indent=1))
    print(f"\ndemo: {run['run']}, {clock(run, start)} to {clock(run, end)} Pacific "
          f"({'the last' if args.last else 'the busiest'} {args.minutes:g} minutes"
          f"{', ending on the grading' if graded else ''})")
    print("  " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    print("Now rebuild the page: cd web && npm run build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
