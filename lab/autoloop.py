"""The autonomous improvement loop: propose one change to the lab, test it on the dev panel against
the current version, and keep it only when code says it beat noise. Run as
`python3 -m lab.run loop ...` (lab/run.py).

Words used here:
  move       one typed change: Move(id, kind, target, value, rationale, affects_first_round)
  variant    a folder `variants/<id>/` holding config.json (the lab's Config fields), prompts/*.md
             (prompt overrides, read through Config.prompt_dir; lab/prompts is never written) and
             move.json (the move that made it, and its parent)
  incumbent  the variant in use; the first is `variants/v4-base`, made from `--preset v4`
  panel      the dev studies every variant is run on

Move kinds (validate):
  edit_section  target "<prompt file>#<heading>", a heading that exists once in that file (the
                incumbent's version). value replaces the section's body: the text between that
                heading and the next. It must keep the body's {fields} exactly (no field dropped or
                added) and hold no heading of its own.
  toggle        target a Config switch in TOGGLES; value true or false, unlike the incumbent's.
                "brief_teams" true briefs both first teams, false team A only.
  threshold     target a knob in THRESHOLDS; value a number within its bounds, unlike the incumbent's.
  add_check     target a check id in lab/check_registry.py that the incumbent lacks and that would
                act with the incumbent's switches (check_registry.has_effect); value true.

affects_first_round is computed (first_round), never taken from the proposer: true for edits to
the prompts the library, briefing or analysis round reads (FIRST_ROUND_PROMPTS), and for the
switches and knobs those rounds use. A move that leaves the first round alone is evaluated in
reuse mode (Config.reuse_first_round): both runs start from the same first answers, which costs
less and removes most of the noise.

The loop, per move:
  1. propose  the proposer agent (role "proposer", lab/prompts/proposer.md) sees the ledger summary
              (past moves, their paired results, why each was rejected), the dev outcome kinds with a
              failure count per kind, the move schema and the incumbent's settings. Never answers,
              labels per task or sealed data. It writes move.json. A move that is invalid or repeats a
              past one is replaced by rules_move(), which maps the top failure kind to the next untried
              move in a fixed queue. The ledger says which path made the move.
  2. evaluate the variant runs on the panel through the lab command, in-process (evaluate), and is
              graded (grade_run refuses while any agent is unfinished).
  3. pair     per task: rescued, damaged, same-pass or same-fail, and cost per task (paired).
  4. accept   by code (accept): net = rescued - damaged >= T, damaged <= max_damage, and cost per
              task <= cost_ratio_max x the incumbent's. An accepted move is run once more (a second
              replicate) and must show net > 0 again before it becomes the incumbent.
Every proposal, evaluation, acceptance and rejection goes to the research ledger (lab/ledger.py)
with its numbers. The loop stops after max_moves moves, after MAX_REJECTIONS_IN_A_ROW rejections in
a row, when the total dollar cap is spent, or when no move is left; it then writes
`variants/incumbent/` (a copy of the final incumbent, with pointer.json) and a final ledger note.
"""

from __future__ import annotations

import dataclasses
import json
import math
import pathlib
import re
import shutil
import string
import time
import uuid
from typing import Any, Callable

from lab import check_registry
from lab import ledger as ledger_mod
from lab.measures import NOT_VERIFIABLE_KIND, SETTLED_WRONGLY_KIND, LAB_ROLES
from lab.protocol import PROMPTS, Config

KINDS = ("edit_section", "toggle", "threshold", "add_check")
# Config switches a toggle may flip. brief_teams is a tuple in Config: true means both first teams.
TOGGLES = ("recheck", "guard", "challenge", "research", "questions", "referee", "team_search", "library",
           "brief_teams")
# Knobs a threshold may set: (lowest, highest, type).
THRESHOLDS = {"recheck_p": (0.001, 0.2, float), "recheck_min_n": (5, 200, int), "max_searches": (0, 30, int),
              "team_searches": (0, 20, int), "research_searches": (0, 20, int)}
# The switch each threshold (and prompt) acts through: a move on it while that switch is off changes nothing.
THRESHOLD_NEEDS = {"recheck_p": "recheck", "recheck_min_n": "recheck", "max_searches": "library",
                   "team_searches": "team_search", "research_searches": "research"}
PROMPT_NEEDS = {"analyst_recheck.md": "recheck", "team_search.md": "team_search", "library.md": "library",
                "library_research.md": "research", "director_research.md": "research",
                "director_challenge.md": "challenge", "director_crossexam.md": "questions", "referee.md": "referee"}
# The lab's prompts a move may edit (not the control arms' prompts, nor the proposer's own).
EDITABLE_PROMPTS = ("analyst.md", "analyst_recheck.md", "analyst_revise.md", "common.md", "director_challenge.md",
                    "director_crossexam.md", "director_relay.md", "director_research.md", "library.md",
                    "library_research.md", "referee.md", "team_search.md")
# What the first round (library, briefing, analysis) reads: a move touching any of it cannot reuse a first round.
FIRST_ROUND_PROMPTS = frozenset({"analyst.md", "library.md", "director_relay.md", "common.md", "team_search.md"})
FIRST_ROUND_SWITCHES = frozenset({"library", "team_search", "brief_teams", "max_searches", "team_searches"})
# Config fields that belong to one run, not to a variant: never in config.json.
RUN_FIELDS = ("max_agents", "max_usd", "prompt_dir", "reuse_first_round", "snapshot_first_round", "seed")
OUTCOMES = ("rescued", "damaged", "same-pass", "same-fail")
DAMAGE_KIND = "a later step damaged a correct first answer"   # from measures.steps on the incumbent's run
MAX_SECTION_CHARS = 6000
MAX_RATIONALE_CHARS = 2000
MAX_REJECTIONS_IN_A_ROW = 3
BASE_VARIANT = "v4-base"
INCUMBENT_DIR = "incumbent"
DEFAULT_T_SOURCE = "default 2: Phase 1 (the A/A noise run) has not run yet"
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
FENCE = re.compile(r"^\s*(```|~~~)")


# ----- moves -----

@dataclasses.dataclass
class Move:
    id: str
    kind: str
    target: str
    value: Any
    rationale: str = ""
    affects_first_round: bool = False

    def key(self) -> str:
        """What makes two moves the same move: kind, target and value."""
        return json.dumps([self.kind, self.target, self.value], sort_keys=True)

    def record(self) -> dict:
        return dataclasses.asdict(self)

    def describe(self) -> str:
        value = json.dumps(self.value) if self.kind != "edit_section" else f"<{len(str(self.value))} characters>"
        return f"{self.kind} {self.target} = {value}"


def move_from(doc: Any, move_id: str) -> tuple[Move | None, list[str]]:
    """A Move from a proposer's move.json; (None, problems) when its shape is wrong. affects_first_round
    is computed later, whatever the document says."""
    if not isinstance(doc, dict):
        return None, ["move.json is not a JSON object"]
    problems = [f'move.json lacks "{k}"' for k in ("kind", "target", "value") if k not in doc]
    rationale = doc.get("rationale", "")
    if not isinstance(rationale, str):
        problems.append("rationale must be text")
    if problems:
        return None, problems
    return Move(move_id, str(doc["kind"]), str(doc["target"]), doc["value"], rationale[:MAX_RATIONALE_CHARS]), []


def first_round(move: Move) -> bool:
    """Whether a move changes what the first round (library, briefing, analysis) does."""
    if move.kind == "edit_section":
        return move.target.split("#", 1)[0] in FIRST_ROUND_PROMPTS
    if move.kind in ("toggle", "threshold"):
        return move.target in FIRST_ROUND_SWITCHES
    return False      # no check acts in the first round


# ----- prompt sections -----

def sections(text: str) -> list[tuple[str, int, int]]:
    """(heading text, first body line, end line) for every markdown heading outside code fences.
    A section's body runs from the line after its heading to the next heading of any level."""
    lines = text.splitlines(keepends=True)
    heads, fenced = [], False
    for i, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
            continue
        m = HEADING.match(line.rstrip("\n"))
        if m and not fenced:
            heads.append((m.group(2), i))
    out = []
    for n, (title, i) in enumerate(heads):
        end = heads[n + 1][1] if n + 1 < len(heads) else len(lines)
        out.append((title, i + 1, end))
    return out


def section_body(text: str, heading: str) -> str | None:
    """The body of the one section with this heading, or None when there is none or more than one."""
    found = [s for s in sections(text) if s[0] == heading]
    if len(found) != 1:
        return None
    lines = text.splitlines(keepends=True)
    return "".join(lines[found[0][1]:found[0][2]])


def replace_section(text: str, heading: str, value: str) -> str:
    """The text with that section's body replaced: one blank line after the heading, the value, and
    one blank line before the next heading (a single newline at the end of the file)."""
    found = [s for s in sections(text) if s[0] == heading]
    if len(found) != 1:
        raise ValueError(f"no single section headed {heading!r}")
    _, start, end = found[0]
    lines = text.splitlines(keepends=True)
    last = end >= len(lines)
    body = "\n" + value.strip("\n") + ("\n" if last else "\n\n")
    return "".join(lines[:start]) + body + "".join(lines[end:])


def placeholders(text: str) -> set[str]:
    """The template's {field} names (escaped {{ }} are not fields). Raises ValueError on bad braces."""
    names = set()
    for _, field, _, _ in string.Formatter().parse(text):
        if field is not None:
            names.add(re.split(r"[.\[]", field, 1)[0])
    return names


# ----- variants -----

def variant_config(cfg: Config) -> dict:
    """The Config fields a variant stores (everything but one run's own settings), as JSON values."""
    data = json.loads(json.dumps(dataclasses.asdict(cfg), default=str))
    return {k: v for k, v in data.items() if k not in RUN_FIELDS}


def read_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


def settings(variant: pathlib.Path) -> dict:
    """A variant's config.json."""
    doc = read_json(pathlib.Path(variant) / "config.json")
    if not isinstance(doc, dict):
        raise ValueError(f"{variant} has no readable config.json")
    return doc


def prompt_text(variant: pathlib.Path | None, name: str) -> str:
    """A prompt as the variant sees it: its own override, else lab/prompts."""
    own = pathlib.Path(variant) / "prompts" / name if variant else None
    return own.read_text() if own is not None and own.is_file() else (PROMPTS / name).read_text()


def make_base_variant(variants_dir: pathlib.Path, cfg: Config, name: str = BASE_VARIANT,
                      made_from: str = "--preset v4") -> pathlib.Path:
    """Write the first incumbent, `variants/<name>/`, from a Config (no prompt overrides). An existing
    folder is kept as it is."""
    path = pathlib.Path(variants_dir) / name
    if (path / "config.json").is_file():
        return path
    (path / "prompts").mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(json.dumps(variant_config(cfg), indent=2) + "\n")
    (path / "move.json").write_text(json.dumps({"id": name, "parent": None, "made_from": made_from}, indent=2) + "\n")
    return path


def make_variant(move: Move, incumbent: pathlib.Path, variants_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Write `variants/<move.id>/`: the incumbent's config.json and prompt overrides with the move
    applied, and move.json (the move and its parent). Validates the move first; lab/prompts is only read."""
    incumbent = pathlib.Path(incumbent)
    variants_dir = pathlib.Path(variants_dir or incumbent.parent)
    current = settings(incumbent)
    problems = validate(move, current, incumbent)
    if problems:
        raise ValueError(f"move {move.id} is not valid: " + "; ".join(problems))
    move.affects_first_round = first_round(move)
    path = variants_dir / move.id
    if path.exists():
        raise FileExistsError(f"{path} exists already")
    shutil.copytree(incumbent / "prompts", path / "prompts") if (incumbent / "prompts").is_dir() \
        else (path / "prompts").mkdir(parents=True)
    config = dict(current)
    if move.kind == "edit_section":
        name, heading = move.target.split("#", 1)
        (path / "prompts" / name).write_text(replace_section(prompt_text(incumbent, name), heading, move.value))
    elif move.kind == "toggle" and move.target == "brief_teams":
        config["brief_teams"] = ["team-a", "team-b"] if move.value else ["team-a"]
    elif move.kind in ("toggle", "threshold"):
        config[move.target] = THRESHOLDS[move.target][2](move.value) if move.kind == "threshold" else move.value
    elif move.kind == "add_check":
        config["checks"] = sorted(set(config.get("checks") or []) | {move.target})
    (path / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (path / "move.json").write_text(json.dumps({**move.record(), "parent": incumbent.name}, indent=2) + "\n")
    return path


# ----- validation -----

def brief_both(current: dict) -> bool:
    return set(current.get("brief_teams") or []) == {"team-a", "team-b"}


def validate(move: Move, current: dict, incumbent: pathlib.Path | None = None) -> list[str]:
    """Why a move cannot be made on an incumbent with Config values `current` (and prompt overrides
    in the folder `incumbent`); empty when it can."""
    if move.kind not in KINDS:
        return [f"kind {move.kind!r} is not one of {', '.join(KINDS)}"]
    if not isinstance(move.rationale, str) or len(move.rationale) > MAX_RATIONALE_CHARS:
        return [f"rationale must be text of at most {MAX_RATIONALE_CHARS} characters"]
    if move.kind == "toggle":
        if move.target not in TOGGLES:
            return [f"toggle target {move.target!r} is not one of {', '.join(TOGGLES)}"]
        if not isinstance(move.value, bool):
            return ["a toggle's value is true or false"]
        now = brief_both(current) if move.target == "brief_teams" else bool(current.get(move.target))
        if now == move.value:
            return [f"{move.target} is already {str(now).lower()}"]
        if move.target == "research" and move.value and not current.get("library"):
            return ["research on demand needs the library, which is off"]
        return []
    if move.kind == "threshold":
        if move.target not in THRESHOLDS:
            return [f"threshold target {move.target!r} is not one of {', '.join(THRESHOLDS)}"]
        low, high, kind = THRESHOLDS[move.target]
        value = move.value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return [f"{move.target} needs a number"]
        if kind is int and value != int(value):
            return [f"{move.target} needs a whole number"]
        if not low <= value <= high:
            return [f"{move.target} must be within [{low}, {high}]"]
        if current.get(move.target) is not None and float(current[move.target]) == float(value):
            return [f"{move.target} is already {current[move.target]}"]
        needs = THRESHOLD_NEEDS.get(move.target)
        if needs and not current.get(needs):
            return [f"{move.target} would change nothing: it acts only with {needs} on, and {needs} is off"]
        return []
    if move.kind == "add_check":
        if move.target not in check_registry.CHECKS:
            return [f"no check {move.target!r} in the registry; known: {', '.join(sorted(check_registry.CHECKS))}"]
        if move.value is not True:
            return ["an add_check move's value is true"]
        if move.target in (current.get("checks") or []):
            return [f"the check {move.target} is on already"]
        if not check_registry.has_effect(move.target, current):
            check = check_registry.CHECKS[move.target]
            return [f"the check {move.target} would change nothing: it acts only with {check.config} "
                    f"{'on' if check.needs_config_on else 'off'}"]
        return []
    needs = PROMPT_NEEDS.get(str(move.target).split("#", 1)[0])
    if needs and not current.get(needs):
        return [f"editing {str(move.target).split('#', 1)[0]} would change nothing: it is read only with {needs} on, "
                f"and {needs} is off"]
    return validate_edit(move, incumbent)


def validate_edit(move: Move, incumbent: pathlib.Path | None) -> list[str]:
    if "#" not in move.target:
        return ['an edit_section target is "<prompt file>#<heading>"']
    name, heading = move.target.split("#", 1)
    if name not in EDITABLE_PROMPTS:
        return [f"{name!r} is not a prompt a move may edit; editable: {', '.join(EDITABLE_PROMPTS)}"]
    if not isinstance(move.value, str) or not move.value.strip():
        return ["an edit_section value is the section's new text"]
    if len(move.value) > MAX_SECTION_CHARS:
        return [f"the new section is longer than {MAX_SECTION_CHARS} characters"]
    text = prompt_text(incumbent, name)
    body = section_body(text, heading)
    if body is None:
        return [f"{name} has no single section headed {heading!r}"]
    if any(HEADING.match(line) for line in move.value.splitlines()):
        return ["the new section holds a heading of its own; edit one section at a time"]
    try:
        before, after = placeholders(body), placeholders(move.value)
        placeholders(replace_section(text, heading, move.value))
    except ValueError as err:
        return [f"the new section's braces do not parse ({err}); write a literal brace as {{{{ or }}}}"]
    problems = []
    if before - after:
        problems.append(f"the new section drops {', '.join('{' + f + '}' for f in sorted(before - after))}")
    if after - before:
        problems.append(f"the new section adds {', '.join('{' + f + '}' for f in sorted(after - before))}")
    if not problems and body.strip("\n") == move.value.strip("\n"):
        problems.append("the new section is the same as the old one")
    return problems


# ----- pairing and acceptance -----

def outcome(before: bool, after: bool) -> str:
    if before == after:
        return "same-pass" if after else "same-fail"
    return "rescued" if after else "damaged"


def paired(incumbent_results: dict, variant_results: dict) -> dict:
    """Pair two runs task by task. Each argument is {"tasks": {study: {"passed", "usd", "runs"}}}
    (run_outcomes). A task the variant lacks counts as failed and is listed under "missing".
    Cost per task is in dollars when every task of both runs has a dollar figure, else in agent runs."""
    inc, var = incumbent_results.get("tasks") or {}, variant_results.get("tasks") or {}
    ids = sorted(inc)
    dollars = all(inc[s].get("usd") is not None for s in ids) and \
        all((var.get(s) or {}).get("usd") is not None for s in ids)
    basis = "dollars" if dollars else "agent runs"

    def cost(row: dict | None) -> float:
        row = row or {}
        return float(row.get("usd") or 0) if dollars else float(row.get("runs") or 0)

    tasks, counts = {}, {k: 0 for k in OUTCOMES}
    for sid in ids:
        before, after = bool(inc[sid].get("passed")), bool((var.get(sid) or {}).get("passed"))
        kind = outcome(before, after)
        counts[kind] += 1
        tasks[sid] = {"outcome": kind, "cost_incumbent": cost(inc[sid]), "cost_variant": cost(var.get(sid))}
    n = len(ids)
    per_inc = sum(t["cost_incumbent"] for t in tasks.values()) / n if n else 0.0
    per_var = sum(t["cost_variant"] for t in tasks.values()) / n if n else 0.0
    ratio = (per_var / per_inc) if per_inc > 0 else (1.0 if per_var == 0 else math.inf)
    return {"n": n, "rescued": counts["rescued"], "damaged": counts["damaged"], "same_pass": counts["same-pass"],
            "same_fail": counts["same-fail"], "net": counts["rescued"] - counts["damaged"],
            "cost_basis": basis, "cost_per_task": {"incumbent": round(per_inc, 6), "variant": round(per_var, 6),
                                                   "ratio": round(ratio, 6) if math.isfinite(ratio) else None},
            "missing": sorted(s for s in ids if s not in var or var[s].get("missing")), "tasks": tasks}


def accept(pair: dict, T: float, cost_ratio_max: float = 1.25, max_damage: int = 1) -> dict:
    """The code's decision on one paired evaluation: accepted only when net >= T, damaged <= max_damage
    and cost per task <= cost_ratio_max x the incumbent's. Returns {"accepted", "reasons"}."""
    reasons = []
    if pair["net"] < T:
        reasons.append(f"net {pair['net']} (rescued {pair['rescued']} - damaged {pair['damaged']}) is under T = {T}")
    if pair["damaged"] > max_damage:
        reasons.append(f"damaged {pair['damaged']} tasks, more than {max_damage}")
    ratio = pair["cost_per_task"]["ratio"]
    if ratio is None or ratio > cost_ratio_max:
        if ratio is None:
            reasons.append(f"cost per task has no finite ratio: the incumbent cost nothing ({pair['cost_basis']})")
        else:
            reasons.append(f"cost per task is {ratio:.3f}x the incumbent's, over {cost_ratio_max}x "
                           f"({pair['cost_basis']})")
    return {"accepted": not reasons, "reasons": reasons, "T": T, "cost_ratio_max": cost_ratio_max,
            "max_damage": max_damage}


def confirm(pair: dict) -> dict:
    """The second replicate's rule: net > 0."""
    ok = pair["net"] > 0
    return {"accepted": ok, "reasons": [] if ok else [f"second replicate: net {pair['net']} is not above 0"]}


# ----- what a graded run shows -----

def _events(run_dir: pathlib.Path) -> list[dict]:
    path = pathlib.Path(run_dir) / "events.jsonl"
    out = []
    for line in (path.read_text().splitlines() if path.is_file() else []):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def run_outcomes(run_dir: pathlib.Path, panel: list[str]) -> dict:
    """Per panel task of a graded run: whether the lab's final answer passed, and its cost (dollars,
    None when any run reported none; and agent runs). A reused first round's cost (from study.reused)
    is counted in, so a reused run costs what a full run would."""
    results = read_json(pathlib.Path(run_dir) / "results.json") or {}
    studies = results.get("studies") or {}
    events = _events(run_dir)
    tasks = {}
    for sid in panel:
        entry = studies.get(sid) or {}
        runs = [e for e in events if e.get("kind") == "agent.finish" and e.get("study") == sid
                and e.get("role") in LAB_ROLES]
        reused = [e for e in events if e.get("kind") == "study.reused" and e.get("study") == sid][-1:]
        costs = [e.get("cost_usd") for e in runs] + [e.get("first_round_cost_usd") for e in reused]
        tasks[sid] = {"passed": bool(((entry.get("lab") or {}).get("grade") or {}).get("passed")),
                      "missing": "lab" not in entry,
                      "usd": None if any(c is None for c in costs) else round(sum(float(c) for c in costs), 6),
                      "runs": len(runs) + sum(int(e.get("first_round_runs") or 0) for e in reused)}
    return {"run_dir": str(run_dir), "tasks": tasks}


def failure_counts(run_dir: pathlib.Path, panel: list[str] | None = None) -> dict[str, int]:
    """The dev outcome kinds the proposer and the rules see: for each failing final answer, the
    adapter's outcome kind (or "not given"), counted; plus DAMAGE_KIND, the damages later steps did
    to correct first answers (measures.steps). Counts only, never which task."""
    from lab import measures
    results = read_json(pathlib.Path(run_dir) / "results.json") or {}
    counts: dict[str, int] = {}
    for sid, entry in (results.get("studies") or {}).items():
        if panel is not None and sid not in panel:
            continue
        for row in entry.get("candidates") or []:
            if row.get("kind") == "final" and not row.get("passed"):
                kind = row.get("outcome_kind") or "not given"
                counts[kind] = counts.get(kind, 0) + 1
    try:
        steps = measures.steps(measures.Run(pathlib.Path(run_dir)))
        damaged = sum(int(step.get("damaged") or 0) for step in steps.values())
    except Exception:       # an incomplete run: no damage count rather than no loop
        damaged = 0
    if damaged:
        counts[DAMAGE_KIND] = damaged
    return counts


def spent(run_dir: pathlib.Path) -> dict:
    """What a run directory spent: reported dollars, and the runs that reported none."""
    finishes = [e for e in _events(run_dir) if e.get("kind") == "agent.finish"]
    return {"usd": round(sum(float(e.get("cost_usd") or 0) for e in finishes), 6),
            "runs": len(finishes), "runs_without_cost": sum(e.get("cost_usd") is None for e in finishes)}


# ----- the rules fallback -----

def _spec(kind: str, target: str, value: Any, why: str) -> dict:
    return {"kind": kind, "target": target, "value": value, "rationale": f"rules: {why}"}


RULE_QUEUES: dict[str, list[dict]] = {
    NOT_VERIFIABLE_KIND: [
        _spec("toggle", "recheck", True, "settled claims judged Not verifiable; re-check abstentions"),
        _spec("threshold", "recheck_p", 0.1, "a looser p threshold for the re-check"),
        _spec("threshold", "recheck_p", 0.2, "the loosest p threshold for the re-check"),
        _spec("threshold", "recheck_min_n", 10, "a smaller n for the re-check"),
        _spec("threshold", "recheck_min_n", 5, "the smallest n for the re-check"),
        _spec("add_check", check_registry.RECHECK_ON_AGREED_NV, True, "re-check agreed Not verifiable answers"),
    ],
    DAMAGE_KIND: [
        _spec("toggle", "guard", True, "later steps damaged correct answers; require evidence for a change"),
        _spec("add_check", check_registry.GUARD_STRICT_DECIMALS, True, "stricter evidence quotes"),
    ],
    SETTLED_WRONGLY_KIND: [
        _spec("threshold", "recheck_p", 0.01, "unverifiable claims judged settled; a stricter p threshold"),
        _spec("threshold", "recheck_min_n", 40, "a larger n for the re-check"),
        _spec("toggle", "guard", True, "require evidence for a changed answer"),
        _spec("add_check", check_registry.GUARD_STRICT_DECIMALS, True, "stricter evidence quotes"),
    ],
}
GENERAL_QUEUE = [
    _spec("toggle", "challenge", True, "challenge agreed answers"),
    _spec("toggle", "questions", True, "question the teams when they disagree"),
    _spec("toggle", "referee", True, "referee a remaining disagreement"),
    _spec("toggle", "research", True, "research on demand in the question rounds"),
    _spec("toggle", "guard", True, "require evidence for a changed answer"),
    _spec("toggle", "recheck", True, "re-check abstentions"),
    _spec("toggle", "brief_teams", True, "brief both first teams"),
]


def tried_keys(entries: list[dict]) -> set[str]:
    """The keys of every move ever proposed in the ledger: a move with one of these is a repeat."""
    out = set()
    for e in entries:
        if e.get("kind") == "move.proposed":
            m = (e.get("data") or {}).get("move") or {}
            out.add(json.dumps([m.get("kind"), m.get("target"), m.get("value")], sort_keys=True))
    return out


def rules_move(ledger_entries: list[dict], failures: dict[str, int], current: dict,
               incumbent: pathlib.Path | None = None, move_id: str = "rules") -> Move | None:
    """The next untried move from the fixed queues: the top failure kind's queue first (most
    failures first, then by name), then the other kinds' queues, then GENERAL_QUEUE. A move is
    skipped when it is invalid on the incumbent or was proposed before. None when nothing is left."""
    tried = tried_keys(ledger_entries)
    order = [k for k, n in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0])) if n > 0 and k in RULE_QUEUES]
    queues = [RULE_QUEUES[k] for k in order] + [GENERAL_QUEUE]
    for queue in queues:
        for spec in queue:
            move = Move(move_id, spec["kind"], spec["target"], spec["value"], spec["rationale"])
            if move.key() in tried or validate(move, current, incumbent):
                continue
            move.affects_first_round = first_round(move)
            return move
    return None


# ----- the ledger summary the proposer reads -----

def ledger_summary(entries: list[dict], limit: int = 30) -> list[str]:
    """One line per past move: what it was, which path made it, its paired numbers and the decision."""
    moves: dict[tuple, dict] = {}
    for e in entries:
        d = e.get("data") or {}
        key = (d.get("loop"), d.get("move_id") or (d.get("move") or {}).get("id"))
        if e.get("kind") == "move.proposed":
            moves[key] = {"move": d.get("move") or {}, "path": d.get("path"), "evals": [], "decision": None}
        elif key in moves and e.get("kind") == "move.evaluated":
            moves[key]["evals"].append(d)
        elif key in moves and e.get("kind") in ("move.accepted", "move.rejected"):
            moves[key]["decision"] = (e["kind"].split(".")[1], d.get("reasons") or [])
    lines = []
    for (loop, mid), m in list(moves.items())[-limit:]:
        mv = m["move"]
        value = json.dumps(mv.get("value")) if mv.get("kind") != "edit_section" \
            else f"<new text, {len(str(mv.get('value')))} characters>"
        text = f"- {mid} ({m['path']}): {mv.get('kind')} {mv.get('target')} = {value}"
        for ev in m["evals"]:
            p = ev.get("paired") or {}
            ratio = (p.get("cost_per_task") or {}).get("ratio")
            text += (f"; replicate {ev.get('replicate')}: rescued {p.get('rescued')}, damaged {p.get('damaged')}, "
                     f"net {p.get('net')}, cost ratio {ratio if ratio is not None else 'n/a'}")
        if m["decision"]:
            verdict, reasons = m["decision"]
            text += f"; {verdict}" + (f" ({'; '.join(reasons)})" if reasons else "")
        else:
            text += "; no decision recorded"
        lines.append(text)
    return lines


def schema_text() -> str:
    """The move schema, as the proposer brief gives it."""
    toggles = ", ".join(TOGGLES)
    knobs = "; ".join(f"{k} in [{lo}, {hi}]" + (" (whole number)" if kind is int else "")
                      for k, (lo, hi, kind) in THRESHOLDS.items())
    checks = "\n".join(f"  - {c.id}: {c.description} (acts only with {c.config} "
                       f"{'on' if c.needs_config_on else 'off'})" for c in check_registry.CHECKS.values())
    return (f'- edit_section: target "<prompt file>#<heading>", value the new text of that section (the text '
            f"between the heading and the next heading). Keep every {{field}} of the old text, add none, and add "
            f"no heading. Files: {', '.join(EDITABLE_PROMPTS)}.\n"
            f"- toggle: target one of {toggles}; value true or false (brief_teams true briefs both first teams).\n"
            f"- threshold: {knobs}.\n"
            f"- add_check: target a check id, value true. Checks:\n{checks}")


def settings_text(current: dict, incumbent: pathlib.Path) -> str:
    keep = [*TOGGLES, *THRESHOLDS, "checks", "director_model", "worker_model", "team_b_model"]
    lines = []
    for k in keep:
        if k == "brief_teams":
            lines.append(f"- brief_teams: {', '.join(current.get('brief_teams') or ['team-a'])}")
        elif k in current:
            lines.append(f"- {k}: {json.dumps(current[k])}")
    edited = sorted(p.name for p in (pathlib.Path(incumbent) / "prompts").glob("*.md"))
    lines.append(f"- prompts edited by earlier moves: {', '.join(edited) or 'none'}")
    return "\n".join(lines)


def prompt_listing(incumbent: pathlib.Path) -> str:
    out = []
    for name in EDITABLE_PROMPTS:
        heads = [h for h, _, _ in sections(prompt_text(incumbent, name))]
        out.append(f"- {name}: " + "; ".join(f"#{h}" for h in heads))
    return "\n".join(out)


def proposer_brief(*, entries: list[dict], failures: dict[str, int], current: dict, incumbent: pathlib.Path,
                   moves_so_far: int, T: float, cost_ratio_max: float, max_damage: int, panel_size: int,
                   lessons: list[str] | None = None, prompt_dir: str | None = None,
                   prompts_path: str | None = None, overlay_path: str | None = None) -> str:
    from lab.protocol import make_brief
    summary = ledger_summary(entries)
    fails = "\n".join(f"- {k}: {n}" for k, n in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0])))
    return make_brief(
        "proposer", None, prompt_dir=prompt_dir,
        panel_size=panel_size, noise_threshold=T, cost_ratio_max=cost_ratio_max, max_damage=max_damage,
        incumbent_settings=settings_text(current, incumbent),
        failures=fails or "- none: every final answer on the panel passed",
        moves_so_far=moves_so_far, ledger_summary="\n".join(summary) or "- none yet",
        lessons="\n".join(f"- {x}" for x in lessons or []) or "- none yet",
        move_schema=schema_text(), prompt_sections=prompt_listing(incumbent),
        prompts_dir=prompts_path or str(PROMPTS),
        overlay_dir=overlay_path or str(pathlib.Path(incumbent) / "prompts"))


# ----- the loop -----

@dataclasses.dataclass
class LoopSettings:
    benchmark: str
    panel: list[str]
    incumbent_run: pathlib.Path
    root: pathlib.Path
    backend: str = "mock"
    memory: str = "local"
    parallel: int = 4
    max_usd: float | None = None          # total across every move (proposer and lab runs)
    move_max_usd: float | None = None     # per move (its lab runs); None: what is left of max_usd
    max_moves: int = 8
    noise_threshold: float = 2
    noise_source: str = DEFAULT_T_SOURCE
    cost_ratio_max: float = 1.25
    max_damage: int = 1
    ledger_path: pathlib.Path | None = None
    variants_dir: pathlib.Path | None = None
    incumbent: pathlib.Path | None = None  # the incumbent variant folder (default variants/v4-base)
    proposer_model: str = "opus"
    proposer_timeout: int = 900
    lessons: str | None = None            # None, "local" or "past": where the proposer's lessons are kept

    def __post_init__(self):
        self.root = pathlib.Path(self.root).resolve()
        self.incumbent_run = pathlib.Path(self.incumbent_run).resolve()
        self.variants_dir = pathlib.Path(self.variants_dir or self.root / "variants").resolve()
        self.ledger_path = pathlib.Path(self.ledger_path or self.root / "ledger.jsonl")


class Loop:
    """One run of the improvement loop. evaluate_fn and propose_fn may be replaced in tests."""

    def __init__(self, s: LoopSettings, *, evaluate_fn: Callable | None = None, propose_fn: Callable | None = None,
                 backend=None, adapter=None, resume: str | None = None):
        self.s = s
        self.resume = resume
        self.id = resume or f"loop-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
        self.evaluate_fn = evaluate_fn or evaluate
        self.propose_fn = propose_fn or propose
        self._backend, self._adapter = backend, adapter
        self.spent_usd = 0.0
        self.lesson_store = None

    # ----- ledger -----

    def entries(self) -> list[dict]:
        return ledger_mod.entries(self.s.ledger_path)

    def note(self, kind: str, data: dict) -> dict:
        return ledger_mod.append(kind, {"loop": self.id, **data}, self.s.ledger_path)

    # ----- helpers -----

    def adapter(self):
        if self._adapter is None:
            from lab.run import load_adapter
            self._adapter = load_adapter(self.s.benchmark)
        return self._adapter

    def remaining(self) -> float | None:
        return None if self.s.max_usd is None else self.s.max_usd - self.spent_usd

    def move_cap(self) -> float | None:
        caps = [c for c in (self.s.move_max_usd, self.remaining()) if c is not None]
        return min(caps) if caps else None

    def next_id(self) -> str:
        taken = [int(m.group(1)) for p in self.s.variants_dir.glob("m*") if (m := re.fullmatch(r"m(\d+)", p.name))]
        return f"m{max(taken, default=0) + 1:02d}"

    def lessons(self) -> list[str]:
        return self.lesson_store.recall() if self.lesson_store else []

    # ----- the run -----

    def run(self) -> dict:
        s = self.s
        s.variants_dir.mkdir(parents=True, exist_ok=True)
        incumbent = pathlib.Path(s.incumbent or s.variants_dir / BASE_VARIANT).resolve()
        inc_run = s.incumbent_run
        if s.lessons:
            self.lesson_store = Lessons(s.lessons, s.root, s.proposer_model)
        moves, in_a_row, history, pending, pending_path = 0, 0, [], None, None
        if self.resume:
            state = self.restore()
            incumbent, inc_run = state["incumbent"] or incumbent, state["incumbent_run"] or inc_run
            moves, in_a_row, history, pending = state["moves"], state["in_a_row"], state["history"], state["pending"]
            pending_path = state["pending_path"]
            self.spent_usd = state["spent_usd"]
        ensure_graded(inc_run, self.adapter())
        inc_out = run_outcomes(inc_run, s.panel)
        if self.resume:
            self.note("note", {"event": "loop.resume", "moves": moves, "in_a_row": in_a_row,
                               "incumbent": incumbent.name, "incumbent_run": str(inc_run),
                               "pending_move": pending.id if pending else None,
                               "spent_usd": round(self.spent_usd, 4), "max_usd": s.max_usd})
        else:
            self.note("note", {"event": "loop.start", "benchmark": s.benchmark, "panel": s.panel,
                               "incumbent": incumbent.name, "incumbent_run": str(inc_run), "backend": s.backend,
                               "noise_threshold": s.noise_threshold, "noise_threshold_source": s.noise_source,
                               "cost_ratio_max": s.cost_ratio_max, "max_damage": s.max_damage,
                               "max_moves": s.max_moves, "max_usd": s.max_usd, "move_max_usd": s.move_max_usd,
                               "incumbent_passed": sum(t["passed"] for t in inc_out["tasks"].values())})
        reason = None
        while reason is None:
            if pending is not None:                 # a resumed loop first finishes the move it was judging
                proposed, variant, extra, path = pending, s.variants_dir / pending.id, {}, pending_path or "resumed"
                pending = None
                reason = self.decide(proposed, variant, inc_run, inc_out, path, extra, history)
                if reason == "accepted":
                    incumbent, inc_run, in_a_row, reason = variant, self._accepted_run, 0, None
                    inc_out = self._accepted_out
                elif reason == "rejected":
                    in_a_row, reason = in_a_row + 1, None
                continue
            if moves >= s.max_moves:
                reason = f"{s.max_moves} moves made (--max-moves)"
                break
            if in_a_row >= MAX_REJECTIONS_IN_A_ROW:
                reason = f"{MAX_REJECTIONS_IN_A_ROW} rejections in a row"
                break
            if s.max_usd is not None and self.remaining() <= 0:
                reason = f"the total cap of ${s.max_usd} is spent"
                break
            current = settings(incumbent)
            failures = failure_counts(inc_run, s.panel)
            move_id = self.next_id()
            proposed, problems, extra = self.propose_fn(self, move_id=move_id, incumbent=incumbent, current=current,
                                                        failures=failures, moves_so_far=moves)
            self.spent_usd += float(extra.get("spent_usd") or 0)
            path = "proposer"
            if proposed is not None:
                problems = problems + validate(proposed, current, incumbent)
                if not problems and proposed.key() in tried_keys(self.entries()):
                    problems = ["repeats a move proposed before"]
            if proposed is None or problems:
                proposed, path = rules_move(self.entries(), failures, current, incumbent, move_id), "rules"
            if proposed is None:
                reason = "no move left: the proposer gave none that could be made and the rules' queues are used up"
                self.note("note", {"event": "loop.no_move", "proposer_problems": problems})
                break
            variant = make_variant(proposed, incumbent, s.variants_dir)
            moves += 1
            self.note("move.proposed", {"move_id": proposed.id, "move": proposed.record(), "path": path,
                                        "proposer_problems": problems if path == "rules" else [],
                                        "proposer_lesson": extra.get("lesson"), "incumbent": incumbent.name,
                                        "variant": str(variant), "failures_seen": failures})
            reason = self.decide(proposed, variant, inc_run, inc_out, path, extra, history)
            if reason == "accepted":
                incumbent, inc_run, in_a_row, reason = variant, self._accepted_run, 0, None
                inc_out = self._accepted_out
            elif reason == "rejected":
                in_a_row, reason = in_a_row + 1, None
        final = write_incumbent(s.variants_dir, incumbent, inc_run, self.id)
        summary = {"event": "loop.stop", "reason": reason, "moves": moves, "history": history,
                   "incumbent": incumbent.name, "incumbent_run": str(inc_run), "incumbent_copy": str(final),
                   "spent_usd": round(self.spent_usd, 4)}
        self.note("note", summary)
        return summary

    def decide(self, proposed: Move, variant: pathlib.Path, inc_run: pathlib.Path, inc_out: dict, path: str,
               extra: dict, history: list) -> str:
        """Judge one move and record it. Returns "accepted" (the new incumbent's run and outcomes are
        left in _accepted_run / _accepted_out), "rejected", or the reason an incomplete evaluation stops the loop."""
        decision = self.judge(proposed, variant, inc_run, inc_out)
        history.append({"move": proposed.id, "path": path, "decision": decision["decision"]})
        if self.lesson_store:
            self.lesson_store.write(f"{proposed.describe()}: {decision['decision']}"
                                    + (f" ({'; '.join(decision['reasons'])})" if decision["reasons"] else "")
                                    + (f". Proposer's lesson: {extra['lesson']}" if extra.get("lesson") else ""))
        if decision["decision"] == "accepted":
            self._accepted_run, self._accepted_out = decision["run_dir"], decision["outcomes"]
            return "accepted"
        if decision["decision"] == "rejected":
            return "rejected"
        return decision["reasons"][0]        # incomplete: the cap or a usage limit stopped a run

    def restore(self) -> dict:
        """Rebuild a stopped or crashed loop's state from its ledger entries: moves made, rejections in
        a row, the incumbent, the move it was judging when it stopped (judged again, its run folders
        resumed by the lab command), and what it spent (read back from the event logs of the proposer
        and of every evaluation run, so the total cap still holds). A loop that stopped for any reason
        but an incomplete evaluation cannot be resumed. Assumes the run root belongs to this loop only."""
        from lab.budget import prior_spend
        from lab.eventlog import JsonlEventLog
        mine = [e for e in self.entries() if (e.get("data") or {}).get("loop") == self.id]
        if not any(e["kind"] == "note" and e["data"].get("event") == "loop.start" for e in mine):
            raise ValueError(f"no loop {self.id} in {self.s.ledger_path}")
        moves, in_a_row, history = 0, 0, []
        incumbent = inc_run = pending = pending_path = None
        stopped = None
        for e in mine:
            kind, d = e["kind"], e["data"]
            if kind == "move.proposed":
                moves += 1
                rec = d["move"]
                pending = Move(rec["id"], rec["kind"], rec["target"], rec["value"], rec.get("rationale", ""),
                               bool(rec.get("affects_first_round")))
                pending_path = d.get("path")
            elif kind == "move.accepted":
                incumbent = self.s.variants_dir / d["new_incumbent"]
                inc_run = pathlib.Path(d["incumbent_run"])
                in_a_row = 0
                history.append({"move": d["move_id"], "path": pending_path, "decision": "accepted"})
                pending = None
            elif kind == "move.rejected":
                if any(str(r).startswith("evaluation incomplete") for r in d.get("reasons") or []):
                    continue                  # judged again on resume
                in_a_row += 1
                history.append({"move": d["move_id"], "path": pending_path, "decision": "rejected"})
                pending = None
            elif kind == "note" and d.get("event") == "loop.stop":
                stopped = d.get("reason")
            elif kind == "note" and d.get("event") == "loop.resume":
                stopped = None
        if stopped is not None and pending is None:
            raise ValueError(f"loop {self.id} stopped ({stopped}); only a loop stopped mid-evaluation can resume")
        pending_dirs = {str(self.s.root / f"{pending.id}-r{r}") for r in (1, 2)} if pending else set()
        runs = {str(self.s.root / f"{e['data']['move_id']}-r{e['data']['replicate']}")
                for e in mine if e["kind"] == "move.evaluated"} - pending_dirs
        spent_usd = prior_spend(JsonlEventLog(self.s.root / "proposer" / "events.jsonl")) \
            if (self.s.root / "proposer" / "events.jsonl").is_file() else 0.0
        for run_dir in sorted(runs):
            if (pathlib.Path(run_dir) / "events.jsonl").is_file():
                spent_usd += prior_spend(JsonlEventLog(pathlib.Path(run_dir) / "events.jsonl"))
        return {"moves": moves, "in_a_row": in_a_row, "history": history, "incumbent": incumbent,
                "incumbent_run": inc_run, "pending": pending, "pending_path": pending_path if pending else None,
                "spent_usd": spent_usd}

    def judge(self, move: Move, variant: pathlib.Path, inc_run: pathlib.Path, inc_out: dict) -> dict:
        """Evaluate a variant (and a second replicate if the first is accepted) and log every step."""
        s = self.s
        numbers = []
        for replicate in (1, 2):
            ev = self.evaluate_fn(variant, inc_run, s.panel, replicate, loop=self)
            self.spent_usd += float(ev.get("spent_usd") or 0)
            pair = paired(inc_out, ev["outcomes"]) if not ev.get("incomplete") else None
            verdict = (accept(pair, s.noise_threshold, s.cost_ratio_max, s.max_damage) if replicate == 1
                       else confirm(pair)) if pair else None
            self.note("move.evaluated", {"move_id": move.id, "replicate": replicate, "run_dir": ev.get("run_dir"),
                                         "reused_from": ev.get("reused_from"), "incomplete": ev.get("incomplete"),
                                         "spent": ev.get("spent"), "paired": pair, "verdict": verdict})
            if pair is None:
                why = f"evaluation incomplete: {ev.get('incomplete')}"
                self.note("move.rejected", {"move_id": move.id, "reasons": [why], "replicate": replicate})
                return {"decision": "incomplete", "reasons": [why]}
            numbers.append({k: pair[k] for k in ("rescued", "damaged", "same_pass", "same_fail", "net")}
                           | {"cost_per_task": pair["cost_per_task"], "cost_basis": pair["cost_basis"]})
            if not verdict["accepted"]:
                self.note("move.rejected", {"move_id": move.id, "replicate": replicate, "reasons": verdict["reasons"],
                                            "numbers": numbers})
                return {"decision": "rejected", "reasons": verdict["reasons"]}
            if replicate == 1:
                first = ev
        self.note("move.accepted", {"move_id": move.id, "numbers": numbers, "new_incumbent": variant.name,
                                    "incumbent_run": first["run_dir"]})
        return {"decision": "accepted", "reasons": [], "run_dir": pathlib.Path(first["run_dir"]),
                "outcomes": first["outcomes"]}


COMPARED_FIELDS = (*TOGGLES, *THRESHOLDS, "checks", "director_model", "worker_model", "team_b_model")


def check_incumbent_run(incumbent: pathlib.Path, run_dir: pathlib.Path, panel: list[str]) -> list[str]:
    """Why a run directory cannot stand for an incumbent variant on the panel; empty when it can.
    The run's last lab.start must have used the variant's settings (COMPARED_FIELDS, and its prompt
    overrides when it has any), every panel study must have finished, and each must hold a
    first-round snapshot of both first teams, so moves can reuse it."""
    run_dir = pathlib.Path(run_dir)
    if not (run_dir / "events.jsonl").is_file():
        return [f"{run_dir} has no events.jsonl: it is not a lab run directory"]
    events = _events(run_dir)
    starts = [e for e in events if e.get("kind") == "lab.start"]
    if not starts:
        return [f"{run_dir} has no lab.start event"]
    used = dict(starts[-1].get("config") or {})
    used["brief_teams"] = list(used.get("brief_teams") or ["team-a"])
    used["checks"] = list(used.get("checks") or [])
    want = settings(incumbent)
    want = {**want, "brief_teams": list(want.get("brief_teams") or ["team-a"]), "checks": list(want.get("checks") or [])}
    problems = [f"{k}: the variant has {json.dumps(want.get(k))}, the run used {json.dumps(used.get(k))}"
                for k in COMPARED_FIELDS if k in want and json.dumps(want.get(k)) != json.dumps(used.get(k))]
    overrides = sorted((pathlib.Path(incumbent) / "prompts").glob("*.md"))
    if overrides and str(used.get("prompt_dir") or "") != str((pathlib.Path(incumbent) / "prompts").resolve()):
        problems.append(f"the variant overrides {len(overrides)} prompt(s), but the run did not read them "
                        f"(its prompt_dir was {used.get('prompt_dir')!r})")
    finished = {e.get("study") for e in events if e.get("kind") == "study.final"}
    for sid in panel:
        if sid not in finished:
            problems.append(f"{sid}: no final answer in {run_dir}")
        elif not all((run_dir / sid / "first-round" / t).is_dir() for t in ("team-a", "team-b")):
            problems.append(f"{sid}: no first-round snapshot (run the incumbent with --snapshot-first-round)")
    return problems


def ensure_graded(run_dir: pathlib.Path, adapter) -> None:
    """Grade a run that has no results.json yet (grade_run refuses while any agent is unfinished)."""
    if not (pathlib.Path(run_dir) / "results.json").is_file():
        from lab.run import grade_run
        grade_run(pathlib.Path(run_dir), adapter)


def write_incumbent(variants_dir: pathlib.Path, incumbent: pathlib.Path, run_dir: pathlib.Path, loop_id: str) -> pathlib.Path:
    """`variants/incumbent/`: a copy of the final incumbent variant, plus pointer.json naming it."""
    target = pathlib.Path(variants_dir) / INCUMBENT_DIR
    if pathlib.Path(incumbent).resolve() != target.resolve():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(incumbent, target, ignore=shutil.ignore_patterns("pointer.json"))
    (target / "pointer.json").write_text(json.dumps({"variant": incumbent.name, "path": str(incumbent),
                                                     "incumbent_run": str(run_dir), "loop": loop_id}, indent=2) + "\n")
    return target


# ----- the proposer -----

def propose(loop: Loop, *, move_id: str, incumbent: pathlib.Path, current: dict, failures: dict[str, int],
            moves_so_far: int) -> tuple[Move | None, list[str], dict]:
    """Run the proposer agent once. Returns (its move or None, why it is unusable, extras: its lesson
    and what it spent). The agent reads only the brief and the prompt files; it writes move.json."""
    from lab.budget import Budget, prior_spend
    from lab.contracts import WORKER, Agent
    from lab.eventlog import JsonlEventLog
    from lab.limits import UsageLimit
    from lab.protocol import AgentRunner, agent_file
    s = loop.s
    folder = s.root / "proposer"
    ws = folder / move_id
    log = JsonlEventLog(folder / "events.jsonl")
    before = prior_spend(log)
    stage_prompt_copies(ws, incumbent)
    brief = proposer_brief(entries=loop.entries(), failures=failures, current=current, incumbent=incumbent,
                           moves_so_far=moves_so_far, T=s.noise_threshold, cost_ratio_max=s.cost_ratio_max,
                           max_damage=s.max_damage, panel_size=len(s.panel), lessons=loop.lessons(),
                           prompts_path=PROPOSER_PROMPTS + "/", overlay_path=PROPOSER_OVERLAY + "/")
    cap = loop.remaining()
    runner = AgentRunner(proposer_backend(loop), log, 1,
                         budget=Budget(before + cap, before) if cap is not None else None)
    agent = Agent("proposer", WORKER, "proposer", s.proposer_model)
    try:
        result = runner.run(agent, brief, ws, readable=[], env=None,
                            timeout_s=s.proposer_timeout, study="loop", phase=f"propose-{move_id}", role="proposer",
                            brief_file=folder / "briefs" / f"{move_id}.md")
    except UsageLimit as err:
        return None, [f"the proposer could not run: {err}"], {"spent_usd": prior_spend(log) - before}
    extra = {"spent_usd": prior_spend(log) - before,
             "lesson": result.result.get("lesson") if result.ok and isinstance(result.result.get("lesson"), str) else None}
    if not result.ok:
        return None, [f"the proposer run failed: {result.error}"], extra
    path = agent_file(ws, "move.json")
    if path is None:
        return None, ["the proposer wrote no move.json"], extra
    move, problems = move_from(read_json(path), move_id)
    return move, problems, extra


# Where the proposer finds the prompt files: copies in its own workspace, since a remote sandbox
# (Modal) holds only the workspace and the agent image, not this checkout's lab/prompts.
PROPOSER_PROMPTS = "prompts"
PROPOSER_OVERLAY = "incumbent-prompts"


def stage_prompt_copies(ws: pathlib.Path, incumbent: pathlib.Path) -> None:
    """Copy the editable prompts (lab/prompts) and the incumbent's overrides into the proposer's
    workspace, replacing any copies left by an earlier attempt."""
    ws.mkdir(parents=True, exist_ok=True)
    for name, src in ((PROPOSER_PROMPTS, PROMPTS), (PROPOSER_OVERLAY, pathlib.Path(incumbent) / "prompts")):
        dest = ws / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir()
        for f in sorted(src.glob("*.md")) if src.is_dir() else []:
            if src == PROMPTS and f.name not in EDITABLE_PROMPTS:
                continue
            shutil.copyfile(f, dest / f.name)


def proposer_backend(loop: Loop):
    if loop._backend is None:
        from lab.run import make_backend, pick_studies
        adapter = loop.adapter()
        loop._backend = make_backend(loop.s.backend, adapter, pick_studies(adapter, ",".join(loop.s.panel)))
    return loop._backend


# ----- evaluation -----

def evaluate(variant: pathlib.Path, incumbent_run: pathlib.Path, panel: list[str], replicate: int, *,
             loop: Loop) -> dict:
    """Run a variant on the panel with the lab command, in-process: its config.json (--config-file),
    its prompt overrides (--prompt-dir), a first-round snapshot of its own, --reuse-first-round
    when its move leaves the first round alone, and the move's dollar cap. Then grade the run.
    Returns {"run_dir", "reused_from", "outcomes", "spent", "spent_usd", "incomplete"}."""
    from lab.run import build_parser, grade_run, load_adapter
    s = loop.s
    variant = pathlib.Path(variant)
    move = read_json(variant / "move.json") or {}
    run_dir = s.root / f"{variant.name}-r{replicate}"
    reuse = None if move.get("affects_first_round", True) else pathlib.Path(incumbent_run)
    argv = ["lab", "--benchmark", s.benchmark, "--studies", ",".join(panel), "--run-dir", str(run_dir),
            "--backend", s.backend, "--memory", s.memory, "--parallel", str(s.parallel),
            "--config-file", str(variant / "config.json"), "--prompt-dir", str(variant / "prompts"),
            "--snapshot-first-round"]
    if reuse is not None:
        argv += ["--reuse-first-round", str(reuse)]
    cap = loop.move_cap()
    if cap is not None:
        if cap <= 0:
            return {"run_dir": str(run_dir), "reused_from": str(reuse) if reuse else None, "outcomes": None,
                    "spent": None, "spent_usd": 0.0, "incomplete": "no money left under the cap"}
        argv += ["--max-usd", f"{cap:.4f}"]
    args = build_parser().parse_args(argv)
    code = args.fn(args)
    events = _events(run_dir)
    used = spent(run_dir)
    out = {"run_dir": str(run_dir), "reused_from": str(reuse) if reuse else None, "exit_code": code,
           "spent": used, "spent_usd": used["usd"], "incomplete": None, "outcomes": None}
    halted = [e for e in events if e.get("kind") == "lab.halted"]
    finals = {e.get("study") for e in events if e.get("kind") == "study.final"}
    if halted:
        out["incomplete"] = f"the run halted: {halted[-1].get('reason')}"
        return out
    if not set(panel) <= finals:
        out["incomplete"] = f"no final answer for {', '.join(sorted(set(panel) - finals))}"
        return out
    grade_run(run_dir, load_adapter(s.benchmark))
    out["outcomes"] = run_outcomes(run_dir, panel)
    return out


# ----- lessons (optional; the ledger summary is the main memory) -----

class Lessons:
    """The proposer's lessons in memory: "local" (a JSON-lines file in the loop's folder) or "past"
    (past.dev, which needs PAST_API_KEY). The loop writes one lesson per decision and puts the
    latest in the next proposer brief."""

    def __init__(self, kind: str, root: pathlib.Path, model: str):
        from lab.contracts import WORKER, Agent, Org
        from lab.eventlog import JsonlEventLog
        folder = pathlib.Path(root) / "lessons"
        folder.mkdir(parents=True, exist_ok=True)
        log = JsonlEventLog(folder / "events.jsonl")
        if kind == "local":
            from lab.memory_local import LocalMemory
            self.memory = LocalMemory(log, folder / "memory.jsonl")
        elif kind == "past":
            from lab.memory_past import PastMemory
            self.memory = PastMemory(log, "sl-loop-lessons")
        else:
            raise ValueError(f"lessons are kept in local or past memory, not {kind!r}")
        self.memory.setup(Org(agents=[Agent("proposer", WORKER, "proposer", model)]))

    def write(self, text: str) -> None:
        self.memory.write("proposer", text, "LESSON")

    def recall(self, limit: int = 10) -> list[str]:
        return [f.text for f in self.memory.recall("proposer", "lesson move accepted rejected", limit)]
