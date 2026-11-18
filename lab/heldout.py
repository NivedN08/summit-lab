"""The held-out test's analysis (research/heldout-plan.md), written and frozen before any held-out run.

Inputs: the graded run folders of one batch, laid out as

    <root>/biodsa/{lab,vote,opus,self,fable,opus-plain}/results.json
    <root>/planted/{lab,vote,opus,self,fable}/results.json

A task counts as passed by an arm only when that arm's graded answer for it passed; a task with no
graded answer (no final, a crash, a halt never resumed) counts as failed for that arm.

Primary endpoint: the lab against the same-cost vote, on every task of both benchmarks pooled. An exact
one-sided sign test on the discordant tasks (the lab right and the vote wrong are the lab's wins; the
reverse its losses), at alpha 0.05. Reported with it: the lab's share of discordant wins and its exact
(Clopper-Pearson) 95% interval, so a null result still says how large an advantage it rules out.

Secondary, as description only: the lab against every other arm, each benchmark on its own, the planted
tasks split into faulted and clean twins and by fault family, and the convention note's effect (Opus
alone with the note against without it, on the same BioDSA tasks).

    python3 -m lab.heldout runs/heldout [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ALPHA = 0.05
BENCHMARKS = ("biodsa", "planted")
ARMS = {  # arm folder -> (results.json key, how to read its graded answer)
    "lab": ("lab", lambda r: (r.get("grade") or {})),
    "vote": ("vote_arm", lambda r: (r.get("vote") or {})),
    "opus": ("baseline", lambda r: (r.get("vote") or {})),
    "self": ("self", lambda r: (r.get("final") or {})),
    "fable": ("baseline", lambda r: (r.get("vote") or {})),
    "opus-plain": ("baseline", lambda r: (r.get("vote") or {})),
}
ARMS_BY_BENCHMARK = {"biodsa": ("lab", "vote", "opus", "self", "fable", "opus-plain"),
                     "planted": ("lab", "vote", "opus", "self", "fable")}
PRIMARY = ("lab", "vote")


def graded(run_dir: pathlib.Path, arm: str) -> dict[str, dict]:
    """Task id -> that arm's graded answer ({} when it has none), for every task in the run's results."""
    key, read = ARMS[arm]
    studies = json.loads((pathlib.Path(run_dir) / "results.json").read_text())["studies"]
    return {sid: read(row.get(key) or {}) for sid, row in studies.items()}


def passes(run_dir: pathlib.Path, arm: str, tasks: list[str]) -> dict[str, bool]:
    """Task id -> passed, for exactly `tasks`; a task missing from the run counts as failed."""
    g = graded(run_dir, arm)
    return {t: bool((g.get(t) or {}).get("passed")) for t in tasks}


def binom_tail(k: int, n: int, p: float = 0.5) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def sign_test(wins: int, losses: int) -> float:
    """Exact one-sided p-value that wins exceed losses (ties already dropped). 1.0 with no discordant task."""
    n = wins + losses
    return 1.0 if n == 0 else binom_tail(wins, n)


def clopper_pearson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """The exact two-sided interval for a binomial share k/n, found by bisection (no SciPy needed)."""
    if n == 0:
        return (0.0, 1.0)
    a = (1 - level) / 2

    def solve(f, lo=0.0, hi=1.0):
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2

    lower = 0.0 if k == 0 else solve(lambda p: binom_tail(k, n, p) < a)
    upper = 1.0 if k == n else solve(lambda p: 1 - binom_tail(k + 1, n, p) >= a)
    return (lower, upper)


def paired(a: dict[str, bool], b: dict[str, bool]) -> dict:
    """a against b on the same tasks: a's wins and losses by task id, the sign test and the interval."""
    tasks = sorted(a)
    if sorted(b) != tasks:
        raise ValueError("the two arms were not scored on the same tasks")
    wins = [t for t in tasks if a[t] and not b[t]]
    losses = [t for t in tasks if b[t] and not a[t]]
    lo, hi = clopper_pearson(len(wins), len(wins) + len(losses))
    return {"tasks": len(tasks), "a_passed": sum(a.values()), "b_passed": sum(b.values()),
            "wins": wins, "losses": losses, "p_one_sided": sign_test(len(wins), len(losses)),
            "win_share": (len(wins) / (len(wins) + len(losses))) if wins or losses else None,
            "win_share_95": [round(lo, 4), round(hi, 4)]}


def planted_groups(run_dir: pathlib.Path, tasks: list[str]) -> dict[str, list[str]]:
    """The planted tasks by group ("faulted", "clean", and "family:<name>"), read from the grades."""
    groups: dict[str, list[str]] = {}
    for t, g in graded(run_dir, "lab").items():
        if t not in tasks or "faulted" not in g:
            continue
        groups.setdefault("faulted" if g["faulted"] else "clean", []).append(t)
        groups.setdefault(f"family:{g.get('family')}", []).append(t)
    return {k: sorted(v) for k, v in sorted(groups.items())}


def analyse(root: pathlib.Path, tasks: dict[str, list[str]]) -> dict:
    """The whole report. `tasks` is the registered task ids per benchmark (from the frozen specs)."""
    root = pathlib.Path(root)
    by = {b: {arm: passes(root / b / arm, arm, tasks[b]) for arm in ARMS_BY_BENCHMARK[b]} for b in BENCHMARKS}
    pooled = {arm: {f"{b}/{t}": ok for b in BENCHMARKS for t, ok in by[b][arm].items()}
              for arm in ARMS_BY_BENCHMARK["planted"]}
    primary = paired(pooled[PRIMARY[0]], pooled[PRIMARY[1]])
    primary["significant"] = primary["p_one_sided"] <= ALPHA
    report = {"primary": {"comparison": "lab vs same-cost vote, both benchmarks pooled", "alpha": ALPHA, **primary},
              "passed": {b: {arm: sum(v.values()) for arm, v in by[b].items()} for b in BENCHMARKS},
              "tasks": {b: len(tasks[b]) for b in BENCHMARKS},
              "secondary": {}}
    sec = report["secondary"]
    for arm in ARMS_BY_BENCHMARK["planted"][1:]:
        sec[f"lab vs {arm}, pooled"] = paired(pooled["lab"], pooled[arm])
    for b in BENCHMARKS:
        for arm in ARMS_BY_BENCHMARK[b][1:]:
            if arm != "opus-plain":
                sec[f"lab vs {arm}, {b}"] = paired(by[b]["lab"], by[b][arm])
    sec["convention note: opus vs opus-plain, biodsa"] = paired(by["biodsa"]["opus"], by["biodsa"]["opus-plain"])
    groups = planted_groups(root / "planted" / "lab", tasks["planted"])
    sec["planted groups"] = {g: {arm: sum(by["planted"][arm][t] for t in ids) for arm in ARMS_BY_BENCHMARK["planted"]}
                             | {"tasks": len(ids)} for g, ids in groups.items()}
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("root", help="the batch's run root, holding biodsa/ and planted/")
    p.add_argument("--specs", nargs=2, default=["prereg/heldout-biodsa.json", "prereg/heldout-planted.json"],
                   metavar=("BIODSA_SPEC", "PLANTED_SPEC"), help="the frozen specs that list the registered tasks")
    p.add_argument("--json", help="also write the report here")
    args = p.parse_args(argv)
    tasks = {b: json.loads(pathlib.Path(s).read_text())["studies"] for b, s in zip(BENCHMARKS, args.specs)}
    report = analyse(pathlib.Path(args.root), tasks)
    text = json.dumps(report, indent=2)
    if args.json:
        pathlib.Path(args.json).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
