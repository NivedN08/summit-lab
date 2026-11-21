"""Recompute every published number from the committed results/ files.

    python3 verify_results.py                    # every claim in claims.json, the pre-registration locks, the ledger
    python3 verify_results.py --claims other.json

claims.json lists each number published in README.md's headline and results tables, and the M-11,
M-13 and M-14 counts the README cites from explanation.md. Each claim has:

  id          a short name
  text        the number as published, and where
  value       the published value: a count "3 of 8" is [3, 8], a dollar figure is a number
  sources     alias -> a run directory under results/
  expr        a Python expression over the aliases and the helpers below that recomputes the value
  round       (optional) decimal places to round the recomputed number to before comparing
  unverifiable  (instead of expr) why the committed files cannot reproduce the number

Each alias is a Source: `.m` is lab.measures.measure() of the run (from results.json, the
study.json files and events.jsonl; no grader runs), `.results` its results.json, `.count(kind, **match)`
counts events. Helpers: nof(count) -> [n, of], pct(n, of) -> a whole percent, and the functions below.

Also checked: every prereg/*.lock still belongs to its spec (lab/prereg.verify_lock; the code may have
moved on since a batch ran), and the research ledger's hash chain when ledger/ledger.jsonl exists.
Prints one row per check and exits 1 if any check fails.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
CLAIMS = ROOT / "claims.json"
sys.path.insert(0, str(ROOT))

from lab import heldout, ledger, measures, prereg  # noqa: E402  (needs ROOT on the path when run as a script)


class Source:
    """One committed run directory, each part loaded once and only when a claim asks for it."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        if not (path / "results.json").is_file():
            raise FileNotFoundError(f"{path.relative_to(ROOT)} has no results.json")
        self._m = self._results = self._events = None

    @property
    def m(self) -> dict:
        if self._m is None:
            self._m = measures.measure(self.path)
        return self._m

    @property
    def results(self) -> dict:
        if self._results is None:
            self._results = json.loads((self.path / "results.json").read_text())
        return self._results

    @property
    def events(self) -> list[dict]:
        if self._events is None:
            self._events = [json.loads(line) for line in (self.path / "events.jsonl").read_text().splitlines()
                            if line.strip()]
        return self._events

    @property
    def studies(self) -> dict:
        return self.results["studies"]

    def count(self, kind: str, **match: Any) -> int:
        """Events of one kind whose fields equal every `match` value."""
        return sum(1 for e in self.events if e.get("kind") == kind and all(e.get(k) == v for k, v in match.items()))

    def attempts(self, sid: str) -> list[bool]:
        """Pass or fail of each single-agent attempt of a study, in attempt order."""
        rows = (self.studies[sid].get("baseline") or {}).get("attempts") or {}
        return [bool(rows[k].get("passed")) for k in sorted(rows, key=lambda k: int(k.split("-")[1]))]

    def lab_passed(self, sid: str) -> bool:
        return bool(((self.studies[sid].get("lab") or {}).get("grade") or {}).get("passed"))

    def candidate_passed(self, sid: str, cid: str) -> bool:
        return any(c.get("id") == cid and c.get("passed") for c in self.studies[sid].get("candidates") or [])


# ----- helpers a claim's expression may use -----

def nof(c: dict) -> list:
    """A measures count {"n", "of"} as [n, of]."""
    return [c["n"], c["of"]]


def pct(n: float, of: float) -> int:
    """A whole percent, as published."""
    return round(100 * n / of)


def studies_with_a_passing_attempt(run: Source) -> list:
    """[studies where at least one single-agent attempt passed, studies with attempts]."""
    with_attempts = [sid for sid in run.studies if run.attempts(sid)]
    return [sum(any(run.attempts(sid)) for sid in with_attempts), len(with_attempts)]


def first_answers_passed(run: Source, team: str) -> list:
    """[lab studies where the team's first answer passed, lab studies]."""
    labs = [sid for sid, e in run.studies.items() if "lab" in e]
    return [sum(run.candidate_passed(sid, f"{team}-analysis") for sid in labs), len(labs)]


def failed_every_arm(lab: Source, *singles: Source) -> list:
    """Studies the lab's final answer failed and every single-agent attempt failed, in every given run."""
    return sorted(sid for sid, e in lab.studies.items() if "lab" in e and not lab.lab_passed(sid)
                  and not any(lab.attempts(sid)) and not any(any(s.attempts(sid)) for s in singles))


def single_passes_at_least_half(run: Source) -> list:
    """Studies where at least half the single-agent attempts passed."""
    return sorted(sid for sid in run.studies if run.attempts(sid) and 2 * sum(run.attempts(sid)) >= len(run.attempts(sid)))


def all_or_nothing(run: Source) -> bool:
    """True when every study's attempts all passed or all failed."""
    return all(len(set(run.attempts(sid))) <= 1 for sid in run.studies)


def lab_only(run: Source) -> list:
    """Studies the lab passed and every single-agent attempt failed."""
    return sorted(sid for sid, e in run.studies.items() if "lab" in e and run.lab_passed(sid)
                  and run.attempts(sid) and not any(run.attempts(sid)))


def other_labels_passed(run: Source, but: str = "replicated") -> list:
    """[studies passed, studies] over every label except one."""
    rows = [row for label, row in run.m["labels"].items() if label != but]
    return [sum(r["passed"] for r in rows), sum(r["studies"] for r in rows)]


def rescues_and_damage(run: Source, steps=("questions", "referee", "team_c")) -> list:
    """[rescued, damaged] summed over the given steps."""
    s = run.m["steps"]
    return [sum(s[k]["rescued"] for k in steps), sum(s[k]["damaged"] for k in steps)]


def wall_minutes(run: Source) -> float:
    """Minutes from the first lab.start to the last study.final."""
    import datetime as dt

    def at(e):
        return dt.datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
    starts = [at(e) for e in run.events if e.get("kind") == "lab.start"]
    finals = [at(e) for e in run.events if e.get("kind") == "study.final"]
    return (max(finals) - min(starts)).total_seconds() / 60


HELDOUT_SPECS = ("prereg/heldout-biodsa.json", "prereg/heldout-planted.json")


def heldout_tasks() -> dict:
    """The registered held-out task ids per benchmark, from the frozen specs."""
    return {b: json.loads((ROOT / s).read_text())["studies"] for b, s in zip(heldout.BENCHMARKS, HELDOUT_SPECS)}


def heldout_report(run: Source) -> dict:
    """The frozen held-out analysis (lab.heldout) on the committed batch that holds this run folder."""
    return heldout.analyse(run.path.parents[1], heldout_tasks())


def heldout_failed(run: Source) -> list:
    """Registered tasks this held-out arm did not pass (a task with no graded answer counts as failed)."""
    bench, arm = run.path.parent.name, run.path.name
    return sorted(t for t, ok in heldout.passes(run.path, arm, heldout_tasks()[bench]).items() if not ok)


def heldout_unanswered(run: Source) -> list:
    """Registered tasks this held-out arm has no graded answer for."""
    bench, arm = run.path.parent.name, run.path.name
    graded = heldout.graded(run.path, arm)
    return sorted(t for t in heldout_tasks()[bench] if not graded.get(t))


HELPERS = {f.__name__: f for f in (nof, pct, studies_with_a_passing_attempt, first_answers_passed, failed_every_arm,
                                   single_passes_at_least_half, all_or_nothing, lab_only, other_labels_passed,
                                   rescues_and_damage, wall_minutes, heldout_report,
                                   heldout_failed, heldout_unanswered)}
BUILTINS = {f.__name__: f for f in (len, sum, round, min, max, abs, any, all, sorted, set, list, dict)}


# ----- checking -----

def rounded(value: Any, places: int | None) -> Any:
    if places is None:
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        out = round(float(value), places)
        return int(out) if places == 0 else out
    if isinstance(value, (list, tuple)):
        return [rounded(v, places) for v in value]
    return value


def same(published: Any, recomputed: Any) -> bool:
    if isinstance(published, (int, float)) and isinstance(recomputed, (int, float)) \
            and not isinstance(published, bool) and not isinstance(recomputed, bool):
        return abs(float(published) - float(recomputed)) < 1e-9
    return json.dumps(published, sort_keys=True) == json.dumps(recomputed, sort_keys=True)


def check_claim(claim: dict, sources: dict[str, Source]) -> tuple[str, Any, str]:
    """(status, recomputed value, note) for one claim. Status is OK, FAIL or UNVERIFIABLE."""
    if "unverifiable" in claim:
        return "UNVERIFIABLE", None, claim["unverifiable"]
    try:
        names = {}
        for alias, rel in claim["sources"].items():
            path = (ROOT / rel).resolve()
            if path not in sources:
                sources[path] = Source(path)
            names[alias] = sources[path]
        # The expression comes from the repository's own claims.json, never from outside input. It sees
        # only the run aliases, the helpers and a few builtins, and may not name any dunder attribute.
        if "__" in claim["expr"]:
            raise ValueError("an expression may not contain '__'")
        value = eval(claim["expr"], {"__builtins__": BUILTINS, **HELPERS, **names})   # globals: comprehensions see them
        value = rounded(value, claim.get("round"))
    except Exception as err:
        return "FAIL", None, f"could not recompute: {type(err).__name__}: {err}"
    return ("OK" if same(claim["value"], value) else "FAIL"), value, ""


def check_locks(root: pathlib.Path) -> list[tuple[str, str, str]]:
    rows = []
    for lock in sorted((root / "prereg").glob("*.lock")):
        spec = lock.with_suffix(".json")
        problems = prereg.verify_lock(spec) if spec.is_file() else [f"no spec {spec.name} for this lock"]
        rows.append((f"prereg/{lock.name}", "FAIL" if problems else "OK", "; ".join(problems) or "matches its spec"))
    return rows


def check_ledger() -> list[tuple[str, str, str]]:
    path = ledger.default_path()
    if not path.is_file():
        return []
    problems = ledger.verify_chain(path)
    n = len(ledger.entries(path))
    return [("ledger chain", "FAIL" if problems else "OK",
             "; ".join(problems) or f"{n} entries, chain intact")]


def show(value: Any, text: str = "") -> str:
    """A value for the table: a pair reads "n of m" when the published text says it that way."""
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in value) \
            and re.search(rf"\b{value[0]} of {value[1]}\b", text):
        return f"{value[0]} of {value[1]}"
    if value is None:
        return "-"
    return json.dumps(value) if not isinstance(value, str) else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute every published number from results/")
    parser.add_argument("--claims", default=str(CLAIMS))
    args = parser.parse_args(argv)
    claims = json.loads(pathlib.Path(args.claims).read_text())["claims"]
    ids = collections.Counter(c["id"] for c in claims)
    sources: dict[pathlib.Path, Source] = {}
    rows = []
    for claim in claims:
        status, value, note = check_claim(claim, sources)
        if ids[claim["id"]] > 1:
            status, note = "FAIL", f"claim id used {ids[claim['id']]} times"
        rows.append((claim["id"], show(claim.get("value"), claim["text"]), show(value, claim["text"]), status,
                     note or claim["text"]))
    width = max([len(r[0]) for r in rows] + [len("ledger chain"), 24])
    print(f"{'claim':<{width}}  {'published':<22} {'recomputed':<22} {'result':<12} text / note")
    for cid, published, recomputed, status, note in rows:
        print(f"{cid:<{width}}  {published:<22} {recomputed:<22} {status:<12} {note}")
    extra = check_locks(ROOT) + check_ledger()
    for name, status, note in extra:
        print(f"{name:<{width}}  {'':<22} {'':<22} {status:<12} {note}")
    statuses = [r[3] for r in rows] + [r[1] for r in extra]
    tally = collections.Counter(statuses)
    print(f"\n{tally['OK']} OK, {tally['FAIL']} FAIL, {tally['UNVERIFIABLE']} unverifiable "
          f"({len(rows)} claims, {len(extra)} other checks)")
    for cid, published, recomputed, status, note in rows:
        if status == "FAIL":
            print(f"FAIL {cid}: published {published}, recomputed {recomputed}. {note}")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
