"""Phase 1 noise measurement: pair two replicates of the same lab run task by task.

    python3 scripts/noise.py runs/dev-panel-noise-lab1 runs/dev-panel-noise-lab2 [--single runs/...]

Prints, from the graded results.json files only:
  - per task: the final answer's pass/fail in each replicate, and each team's first answer;
  - the flip rate (tasks whose final pass/fail differs between replicates);
  - net = (passes in rep 2 that failed in rep 1) - (the reverse), i.e. rescued - damaged if rep 2
    were a "move" scored against rep 1;
  - the noise threshold T: the 95th percentile of |net| over a bootstrap of tasks (10,000 draws,
    seed 0), and at least 2, as pre-registered in prereg/dev-panel-noise.json;
  - first-answer flips per team, and dollars per run.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random


def load(run_dir: pathlib.Path) -> dict:
    return json.loads((run_dir / "results.json").read_text())["studies"]


def first(study: dict, team: str):
    for c in study.get("candidates", []):
        if c.get("team") == team and c.get("round") == "analysis" and c.get("kind") == "lab":
            return bool(c.get("passed"))
    return None


def dollars(run_dir: pathlib.Path) -> float:
    total = 0.0
    for line in (run_dir / "events.jsonl").open():
        e = json.loads(line)
        if e["kind"] == "agent.finish":
            total += e.get("cost_usd") or 0
    return total


def threshold(diffs: list[int], draws: int = 10_000, seed: int = 0) -> tuple[int, list[int]]:
    rng = random.Random(seed)
    nets = sorted(abs(sum(rng.choice(diffs) for _ in diffs)) for _ in range(draws))
    p95 = nets[int(0.95 * draws) - 1]
    return max(2, p95), nets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rep1", type=pathlib.Path)
    ap.add_argument("rep2", type=pathlib.Path)
    ap.add_argument("--single", type=pathlib.Path, help="a graded single-agent run on the same tasks")
    args = ap.parse_args()
    a, b = load(args.rep1), load(args.rep2)
    single = load(args.single) if args.single else {}
    ids = sorted(set(a) & set(b))
    missing = sorted(set(a) ^ set(b))
    print(f"{'task':6} {'rep1':5} {'rep2':5} {'A1':3} {'A2':3} {'B1':3} {'B2':3} {'vote':5} att  status1 / status2")
    diffs, flips, first_flips = [], 0, {"team-a": 0, "team-b": 0}
    for s in ids:
        p1 = bool(a[s]["lab"]["grade"]["passed"]); p2 = bool(b[s]["lab"]["grade"]["passed"])
        d = int(p2) - int(p1); diffs.append(d); flips += d != 0
        cells = []
        for team in ("team-a", "team-b"):
            f1, f2 = first(a[s], team), first(b[s], team)
            first_flips[team] += f1 is not None and f2 is not None and f1 != f2
            cells += [f1, f2]
        base = single.get(s, {}).get("baseline", {})
        vote = base.get("vote", {}).get("passed") if single else None
        n_pass = sum(bool(v.get("passed")) for v in base.get("attempts", {}).values())
        mark = lambda x: "-" if x is None else ("pass" if x else "fail")
        print(f"{s:6} {mark(p1):5} {mark(p2):5} " + " ".join(f"{mark(x)[:3]:3}" for x in cells)
              + f" {mark(vote):5} {n_pass if single else '-'}/4 {a[s]['lab']['status']} / {b[s]['lab']['status']}")
    n = len(ids)
    t, nets = threshold(diffs)
    print()
    print(f"tasks paired: {n}" + (f" (unpaired: {', '.join(missing)})" if missing else ""))
    print(f"lab passes: rep1 {sum(bool(a[s]['lab']['grade']['passed']) for s in ids)}/{n}, "
          f"rep2 {sum(bool(b[s]['lab']['grade']['passed']) for s in ids)}/{n}")
    print(f"final-answer flips: {flips}/{n} = {flips / n:.0%} (pre-registered stop rule: more than 25%)")
    print(f"net (rep2 - rep1): rescued {diffs.count(1)}, damaged {diffs.count(-1)}, net {sum(diffs)}")
    print(f"first-answer flips: team A {first_flips['team-a']}/{n}, team B {first_flips['team-b']}/{n}")
    print(f"bootstrap |net|: median {nets[len(nets) // 2]}, 95th percentile {nets[int(0.95 * len(nets)) - 1]}; "
          f"noise threshold T = {t}")
    if single:
        print(f"single agent: vote {sum(bool(single[s]['baseline'].get('vote', {}).get('passed')) for s in ids)}/{n}, "
              f"attempts {sum(bool(v.get('passed')) for s in ids for v in single[s]['baseline']['attempts'].values())}/{4 * n}")
    print(f"dollars: rep1 {dollars(args.rep1):.2f}, rep2 {dollars(args.rep2):.2f}"
          + (f", single {dollars(args.single):.2f}" if args.single else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
