"""The lab's main loop: one study from library research to a final answer.

For each study S the lab runs, in order (explanation.md, section 1, and D-8 to D-11):

  1. library     `S-library` researches pitfalls with web search through the gateway, writing to its
                 own department `S-library`, so one study's findings never reach another study's teams.
  2. briefing    the director picks what the teams need; the lab passes each item down to the
                 teams in Config.brief_teams only (default team A), so the two teams start from
                 different information (I-11).
  3. analysis    `S-team-a` and `S-team-b` analyse blind, at the same time.
  3a. re-check   (Config.recheck, off by default) a team that answered "Not verifiable" (the adapter's
                 abstains(), else that label on a BioDSA-style benchmark) gets an inventory of the data
                 files and their unused columns and looks once more (role and phase "recheck"). Code
                 decides whether the new answer replaces the abstention (recheck_verdict).
  4. compare     one valid: "single-source". None: "no-answer". Both valid and agreeing: step 4a.
  4a. challenge  the director tries to break the agreed answer and writes questions for both teams.
                 No questions: "replicated". Otherwise the lab delivers them as in step 5, both
                 teams revise, and agreeing again is "replicated"; disagreeing now goes to step 6.
  5. questions   on disagreement the director writes questions from each team's method note;
                 the lab leak-checks each one, delivers those that pass, and both teams revise.
                 Agreeing now: "converged".
  4a/5 research  in either question round the director may also ask up to three research
                 questions. Those that pass the leak check go to the study's library, which answers
                 them from the web (phase "research"); each finding is leak-checked and passed down
                 to both teams before they revise (Config.research; never without the library).
  6. referee     `S-referee` gets the two answers as X and Y and must run a test. A pick: "refereed".
  7. team C      on "neither", a third blind team runs with the questions as warning signs.
                 Agreeing with A or B: "third-team". Otherwise "unresolved".
  8. record      `<run_dir>/<S>/final/<answer file>` and `<run_dir>/<S>/study.json`, event "study.final".

Damage guard (Config.guard, off by default): a changed answer in the challenge or question round,
an accepted re-check and the referee's own answer each need `evidence.json` that code can check
(check_evidence). A change that fails keeps the earlier answer ("guard.blocked"); the blocked answer
is still graded.

Reuse mode (Config.reuse_first_round, for the improvement loop in lab/autoloop.py): steps 1 to 3
are not run. StudyRun.load_first_round copies another run's first round instead: each first team's
workspace as it stood after the analysis round (`<study>/first-round/<team>/`, which that run wrote
because Config.snapshot_first_round was on), its analysis-round candidates and its `library/`, and
with local memory its first-round memory writes; the briefing is passed down again. Event
"study.reused" names the source. Every later step runs live.

Prompt overlay (Config.prompt_dir): template() reads `<prompt_dir>/<name>.md` when it exists, else
lab/prompts/<name>.md. Checks (Config.checks) are the code-level switches in lab/check_registry.py.

Department heads (Config.heads, off by default; D-23). Three heads, `head-library`, `head-team-a` and
`head-team-b`, share the department `heads`. Like the director they read every department's memory;
the lab also gives each the study's event log so far. Workers still read only their own department.
  1b. library review   after step 1, the head of the library reviews the library's findings; the
                       director's briefing brief carries its review.
  3b. team review      after step 3, each team's head reviews its own scientist's answer before the
                       comparison and may write notes for it. A note is delivered only if it passes the
                       leak check against the other team's answer and cites no fact from the other
                       team's department (only the director moves findings between teams). A team that
                       received a note revises once; a revised answer that is not valid is not used.

The answer file is the study's first deliverable (answer_file): `answer.json` for GeneBench,
`eval_answer.json` for scBench. Briefs name it through the {answer_file} field.

Web search (version 3): the library searches before the analysis; team A, the briefed team, may
also search while it works (Config.team_search), and team B never does. The gateway enforces
each search budget per agent and per run (Gateway.set_budget).

Blindness: a team's brief holds only the lab's instructions, the task text and the paths of its
own workspace, the data and the agent tools. The only thing that ever crosses from one team to
another is a question that passed the leak check, delivered through memory. After each delivery
the lab waits until each receiving agent can read what it was sent (Memory.wait_visible).

Folders under `<run_dir>/<S>/`: one workspace per agent run (`library`, `director-briefing`,
`director-questions`, `director-challenge`, `library-research`, `team-a`, `team-b`, `team-c`, `referee`),
`candidates/` (the lab's copy of
every answer, which no agent can read), `briefs/` (every brief given), `final/` and `study.json`.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import decimal
import fnmatch
import json
import math
import pathlib
import random
import re
import shutil
import threading
import time
import uuid
from typing import Any

from lab import check_registry
from lab.budget import Budget, BudgetExceeded, estimates_for, prior_spend
from lab.contracts import DIRECTOR, HEAD, WORKER, Adapter, Agent, AgentResult, Backend, EventLog, Memory, Org, Study
from lab.gateway import Gateway
from lab.inventory import INVENTORY_FILE, build_inventory, hypothesis_text, render_inventory
from lab.limits import UsageLimit   # defined there so lab.budget can subclass it; still importable from here
from lab.memory_local import LocalMemory

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "lab" / "prompts"
TOOLS = ROOT / "lab" / "tools"

TEAMS = ("team-a", "team-b", "team-c")
DIVERSITY = {
    "team-a": "Start from the model the task implies and test its assumptions.",
    "team-b": "Start with a full audit of the data files and how they relate before choosing a model.",
    "team-c": "Before choosing a model, check each warning sign passed down to you against the data.",
}
SITUATION = {      # the {situation} line of analyst_revise.md, per round
    "revise": "The other team reached a different answer. You will not be told what it was. The director's "
              "questions come from warning signs the other team recorded and from differences in method.",
    "challenge": "The other team reached the same answer as you. The director does not think agreement alone "
                 "makes it right, and has questions for both teams.",
}
HEADS = {"library": "head-library", "team-a": "head-team-a", "team-b": "head-team-b"}
HEAD_DEPARTMENT = "heads"
TEAM_NAMES = {"team-a": "Team A", "team-b": "Team B"}
# Files from a scientist's folder its head may read: its own work, never the raw session logs.
WORK_SUFFIXES = {".json", ".py", ".r", ".md", ".txt", ".csv", ".tsv", ".ipynb", ".sh"}
WORK_MAX_BYTES = 1_000_000
# The analysts' revision brief, when research on demand is on (its {findings_note} field).
FINDINGS_NOTE = ("\n\nThe director may also have passed down findings from the lab's library: answers to questions "
                 "about how a method is defined, what a tool does by default, or which convention a field uses. They "
                 "are in memory too: `python3 {tools}/memory.py recall \"findings from the lab's library\"`. Check each "
                 "one against the data like a question; it is not a hint about the answer.")
# The end of the director's result.json shape, when research on demand is on (its {research_shape} field).
RESEARCH_SHAPE = ', "research_questions": [{"question": "...", "why": "..."}]'
MAX_RESEARCH_QUESTIONS = 3
# analyst_revise.md's {questions_source} and {reply_note} fields: where the questions are and where the
# replies go. The lab's defaults are these; the self-challenge arm (lab/arms.py) puts both in files instead.
QUESTIONS_SOURCE = ("The director has read both teams' records and has passed down questions for you. Read them "
                    "with `python3 {tools}/memory.py recall \"questions from the director\"`.")
REPLY_NOTE = 'Record what you found for each question with `memory.py write`, label "REPLY". '

# ----- the "Not verifiable" re-check (Config.recheck) -----
NOT_VERIFIABLE = "Not verifiable"
SETTLED = ("True", "False")
# Adapters whose answers use BioDSA's labels; the re-check falls back to the label rule for these
# when the adapter has no abstains() method of its own.
LABEL_ADAPTERS = {"biodsa", "biodsa-1k", "biodsa-heldout", "biodsa-1k-heldout", "biodsa-1k-convention",
                  "biodsa-heldout-convention", "planted"}
TEST_FILE = "test_result.json"
TEST_FIELDS = ("test", "variables", "statistic", "p_value", "n", "direction")

# ----- the damage guard (Config.guard) -----
EVIDENCE_FILE = "evidence.json"
QUESTIONS_FILE = "director_questions.json"   # the self-challenge arm's questions, in the solver's folder (lab/arms.py)
EVIDENCE_FIELDS = ("field", "script", "output_file", "result")
GUARD_SLACK_S = 2.0          # clock slack when comparing an output file's time with the round's start
GUARD_MAX_NUMBERS = 10       # how many numbers of `result` are looked for in the output file
GUARD_MAX_OUTPUT_CHARS = 5_000_000
# The {evidence_note} field: empty unless the guard is on.
EVIDENCE_NOTE = (
    "\n\nThe lab accepts a change to your answer only with evidence it can check. If you change your answer, "
    "write `evidence.json` in this folder: {\"field\": \"the answer field you changed\", \"script\": \"the "
    "script you ran, a path in this folder\", \"output_file\": \"the file that script wrote its output to, a path "
    "in this folder\", \"result\": \"the numbers that decided the change, quoted exactly as the output file shows "
    "them\"}. The lab checks by code that the script and the output file exist, that the output file was written "
    "during this round and after the script was last saved, and that every number in `result` appears in the "
    "output file. The output file must be one your script wrote, not the script itself and not a lab file "
    "(`evidence.json`, `result.json`, `test_result.json` or your answer file), and at least one quoted number must "
    "be computed by the script rather than typed into it. If any check fails, your earlier answer stands.")
REFEREE_EVIDENCE_NOTE = (
    "\n\nThe lab accepts an `own_answer` only with evidence it can check. If you give one, write `evidence.json` "
    "in this folder: {\"field\": \"the answer field your test decided\", \"script\": \"the script you ran, a "
    "path in this folder\", \"output_file\": \"the file that script wrote its output to, a path in this "
    "folder\", \"result\": \"the numbers that decided your answer, quoted exactly as the output file shows "
    "them\"}. The lab checks by code that the script and the output file exist, that the output file was written "
    "during your run and after the script was last saved, and that every number in `result` appears in the output "
    "file. The output file must be one your script wrote, not the script itself and not a lab file "
    "(`evidence.json`, `result.json`, `test_result.json` or the answer file), and at least one quoted number must "
    "be computed by the script rather than typed into it. If any check fails, your own answer is not used.")
# Added to the evidence note only when the guard_strict_decimals check is on (lab/check_registry.py).
STRICT_DECIMALS_NOTE = (" Every decimal number you quote in `result` must show at least {digits} significant digits "
                        "(0.0123, not 0.01), so print your numbers with enough digits.")


def evidence_note(cfg: "Config", note: str = EVIDENCE_NOTE) -> str:
    """The {evidence_note} field: empty with the guard off; with it on, `note`, plus the strict-decimals
    sentence when that check is on."""
    if not cfg.guard:
        return ""
    if check_registry.is_on(cfg, check_registry.GUARD_STRICT_DECIMALS):
        return note + STRICT_DECIMALS_NOTE.format(digits=check_registry.STRICT_MIN_DIGITS)
    return note


@dataclasses.dataclass
class Config:
    """Settings for one lab run. The switches exist for ablation and comparison runs."""
    director_model: str = "fable"
    worker_model: str = "fable"
    team_b_model: str | None = None   # None: team B uses worker_model (set it for a mixed run)
    timeouts: dict = dataclasses.field(default_factory=lambda: {
        "library": 900, "director": 600, "analyst": 1800, "referee": 1200, "baseline": 1800, "research": 900,
        "head": 600})
    max_agents: int = 4          # agents running at once, shared across all studies
    max_searches: int = 12       # Exa calls the library may make in its first pass, per study
    team_searches: int = 8       # Exa calls team A's analyst may make per round
    research_searches: int = 10  # Exa calls the library may make answering research questions
    library: bool = True         # run the library and the director's briefing
    team_search: bool = True     # team A's analyst may search the web while it works
    research: bool = True        # research on demand in the question rounds (only with the library)
    questions: bool = True       # run the director's question round on disagreement
    referee: bool = True         # run the referee before calling team C
    challenge: bool = True       # challenge an agreed answer before reporting it as replicated
    brief_teams: tuple = ("team-a",)   # the first teams that receive the director's briefing
    heads: bool = False          # department heads review the library's findings and each team's answer (D-23)
    head_model: str = "fable"
    max_head_notes: int = 4      # notes a team's head may send its scientist in one review
    seed: int | None = None      # for the referee's X/Y order; None means random
    max_usd: float | None = None   # hard dollar cap for the run directory (lab.budget); None means no cap
    recheck: bool = False        # re-check a team's "Not verifiable" answer against an inventory of the data
    recheck_p: float = 0.05      # ... a new "True" needs a test with p at most this
    recheck_min_n: int = 20      # ... and any new label a test on at least this many rows
    guard: bool = False          # the damage guard: a changed answer needs evidence.json that code can check
    # ----- the improvement loop (lab/autoloop.py); each off by default -----
    prompt_dir: str | None = None        # a folder of prompt overrides: <prompt_dir>/<name>.md replaces lab/prompts/<name>.md
    reuse_first_round: str | None = None  # a run directory whose first round (library, briefing, analysis) is copied in
    snapshot_first_round: bool = False   # keep a copy of each team's workspace right after the analysis round
    checks: tuple = ()                   # ids from lab/check_registry.py that are switched on (a set, kept sorted)

    def __post_init__(self):
        self.checks = check_registry.normalize(self.checks)

    def timeout(self, role: str) -> int:
        return int(self.timeouts.get(role, 1800))


# ----- briefs -----

def template(name: str, prompt_dir: str | pathlib.Path | None = None) -> str:
    """A prompt template: `<prompt_dir>/<name>.md` when prompt_dir is set and has that file (a
    variant's overlay, lab/autoloop.py), else lab/prompts/<name>.md. lab/prompts is never written."""
    if prompt_dir:
        own = pathlib.Path(prompt_dir) / f"{name}.md"
        if own.is_file():
            return own.read_text()
    return (PROMPTS / f"{name}.md").read_text()


def make_brief(name: str, agent: Agent | None, *, prompt_dir: str | pathlib.Path | None = None, **fields: Any) -> str:
    """Fill a prompt template. Lab agents get common.md in front; pass agent=None for none.
    prompt_dir: an overlay folder for template() (Config.prompt_dir)."""
    fields = {"tools": str(TOOLS), **fields}
    body = template(name, prompt_dir).format(**fields)
    if agent is None:
        return body
    head = template("common", prompt_dir).format(agent_id=agent.id, department=agent.department, **fields)
    return head.rstrip() + "\n\n" + body


def challenge_brief(agent: Agent | None, fields: dict, teams: int = 2,
                    prompt_dir: str | pathlib.Path | None = None) -> str:
    """The director's challenge of an answer: of the answer two blind teams agree on
    (director_challenge.md), or of one analyst's answer (director_challenge_single.md, the
    self-challenge arm, which has no memory: pass agent=None)."""
    if teams not in (1, 2):
        raise ValueError("the challenge is of one team's answer or of two teams' agreed answer")
    return make_brief("director_challenge" if teams == 2 else "director_challenge_single", agent,
                      prompt_dir=prompt_dir, **fields)


# ----- answers -----

def answer_file(study: Study) -> str:
    """The name of the file a study's answer goes in: its first deliverable."""
    return study.deliverables[0] if study.deliverables else "answer.json"


def library_department(study: Study) -> str:
    """Each study's library has its own department, so its findings stay with that study."""
    return f"{study.id}-library"


def load_doc(path: pathlib.Path) -> Any:
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


def answer_values(path: pathlib.Path | None) -> dict:
    """The answer fields of an answer file: the "answer" object if there is one, else the whole object."""
    doc = load_doc(path) if path else None
    if isinstance(doc, dict) and isinstance(doc.get("answer"), dict):
        return doc["answer"]
    return doc if isinstance(doc, dict) else {}


def field_agreement(adapter: Adapter, study: Study, a: pathlib.Path, b: pathlib.Path) -> dict[str, bool]:
    """Field by field agreement; falls back to the adapter's 0-to-1 measure as one field."""
    if hasattr(adapter, "field_agreement"):
        return {k: bool(v) for k, v in adapter.field_agreement(study, a, b).items()}
    return {"all": adapter.agreement(study, study.deliverables[0], a, b) >= 1.0}


# ----- the "Not verifiable" re-check -----

def abstains(adapter: Adapter, study: Study, values: dict) -> bool:
    """Whether an answer abstains: the adapter's own abstains(study, values) when it has one, else
    the label "Not verifiable" for the adapters that use BioDSA's labels (LABEL_ADAPTERS)."""
    own = getattr(adapter, "abstains", None)
    if callable(own):
        return bool(own(study, values))
    return getattr(adapter, "name", None) in LABEL_ADAPTERS and isinstance(values, dict) \
        and values.get("label") == NOT_VERIFIABLE


def local_data_dir(adapter: Adapter, study: Study) -> pathlib.Path | None:
    """A copy of the study's data this process can read: the adapter's local_data_dir(study) when it
    has one, else study.data_dir when that exists here. None when the data are only on a Modal Volume."""
    own = getattr(adapter, "local_data_dir", None)
    path = own(study) if callable(own) else study.data_dir
    path = pathlib.Path(path) if path else None
    return path if path is not None and path.is_dir() else None


def is_link(path: pathlib.Path) -> bool:
    try:
        return path.is_symlink()
    except (OSError, ValueError):
        return False


def agent_file(workspace: pathlib.Path, name: str) -> pathlib.Path | None:
    """A file an agent wrote in its workspace, or None: the lab reads and copies only a regular file
    inside the workspace, never a symbolic link (it would let an agent hand the lab another file)."""
    ws = pathlib.Path(workspace).resolve()
    path = ws / str(name)
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ws):
            return None
    except (OSError, ValueError):
        return None
    return path


def _finite(value, name: str) -> tuple[float | None, str | None]:
    """A test_result.json field as a finite number: (value, None), or (None, why not). Numbers and
    strings that parse as one are accepted; true and false are not."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None, f"{name} is {value!r}, not a number"
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (ValueError, OverflowError):
        return None, f"{name} is {value!r}, not a number"
    if math.isnan(number):
        return None, f"{name} is {value!r}, not a number (NaN)"
    if math.isinf(number):
        return None, f"{name} is {value!r}, an infinite number"
    return number, None


def _p_value(value) -> tuple[float | None, str | None]:
    """p_value as a number in [0, 1]: (p, None), or (None, why not). A string like "<0.001" counts
    as 0.001, the bound it states."""
    if isinstance(value, str):
        value = value.strip()
        value = value[2:] if value.startswith("<=") else value[1:] if value.startswith("<") else value
    p, why = _finite(value, "p_value")
    if why:
        return None, why
    if p < 0:
        return None, f"p_value is {p}, a negative number"
    if p > 1:
        return None, f"p_value is {p}, over 1"
    return p, None


def recheck_verdict(cand: "Candidate | None", workspace: pathlib.Path, started: float,
                    cfg: "Config") -> tuple[bool, str, dict | None]:
    """The code's verdict on a re-check: (accepted, reason, the parsed test_result.json or None).
    A new label replaces "Not verifiable" only when it is True or False and test_result.json (a
    regular file, not a symbolic link) was written in this round with all six fields: `test` a
    non-empty string, `variables` a non-empty list of non-empty strings, `n` a finite number at least
    cfg.recheck_min_n, `p_value` a number in [0, 1] ("<0.001" counts as 0.001), and `direction`
    exactly "supports" or "contradicts" (any case). A True needs "supports" and p_value at most
    cfg.recheck_p; a False needs "contradicts" (and no p threshold, as the brief says)."""
    link = is_link(pathlib.Path(workspace) / TEST_FILE)
    path = None if link else agent_file(workspace, TEST_FILE)
    test = load_doc(path) if path else None
    test = test if isinstance(test, dict) else None
    if cand is None or not cand.valid:
        return False, "the re-check gave no valid answer", test
    label = cand.values.get("label")
    if label not in SETTLED:
        return False, f"the answer is still {label!r}", test
    if link:
        return False, f"{TEST_FILE} is a symbolic link; the lab reads only a regular file", None
    if test is None:
        return False, f"no readable {TEST_FILE}", None
    missing = [k for k in TEST_FIELDS if k not in test or test[k] in (None, "")]
    if missing:
        return False, f"{TEST_FILE} lacks {', '.join(missing)}", test
    if path.stat().st_mtime < started - GUARD_SLACK_S:
        return False, f"{TEST_FILE} was written before the re-check began", test
    if not isinstance(test["test"], str) or not test["test"].strip():
        return False, f"test is {test['test']!r}, not a non-empty string", test
    variables = test["variables"]
    if not isinstance(variables, list) or not variables \
            or not all(isinstance(v, str) and v.strip() for v in variables):
        return False, f"variables is {variables!r}, not a non-empty list of non-empty strings", test
    n, why = _finite(test["n"], "n")
    if why:
        return False, why, test
    if n < cfg.recheck_min_n:
        return False, f"n is {test['n']!r}, under {cfg.recheck_min_n}", test
    p, why = _p_value(test["p_value"])
    if why:
        return False, why, test
    raw = test["direction"]
    direction = raw.strip().lower() if isinstance(raw, str) else None
    if direction not in ("supports", "contradicts"):
        return False, f'direction is {raw!r}, not "supports" or "contradicts"', test
    if label == "True":
        # a significant effect in the opposite direction must not become "True"
        if direction != "supports":
            return False, f"True, but the direction is {raw!r}, not one that supports the claim", test
        if p > cfg.recheck_p:
            return False, f"p_value is {test['p_value']!r}, over {cfg.recheck_p}", test
        return True, f"True with p_value {test['p_value']} and n {test['n']}", test
    if direction != "contradicts":
        return False, f"False, but the direction is {raw!r}, not one that contradicts the claim", test
    return True, f"False with a direction that contradicts the claim and n {test['n']}", test


def restore_kept(ws: pathlib.Path, kept: "Candidate", kept_note: pathlib.Path, deliverable: str) -> None:
    """After a re-check the lab did not accept, put the kept answer back in the team's folder: the
    answer file gets the kept candidate's content and result.json the kept round's note (or is removed
    when that round wrote none). Later rounds, briefs and the director then see the answer in use, and
    a revision that keeps "the answer in the folder" keeps that one."""
    ws = pathlib.Path(ws)
    for name, source in ((deliverable, kept.path), ("result.json", kept_note)):
        target = ws / name
        if is_link(target) or target.is_file():
            target.unlink()
        if pathlib.Path(source).is_file():
            shutil.copyfile(source, target)


# ----- the damage guard -----

# A number as written: digits with optional thousands separators, decimals and exponent. Not one
# that starts inside a word or another number ("FEV1", "1.2.3"). Signs are not part of it.
NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])(?:\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?")
PLAIN_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?")
SCI_TOLERANCE = decimal.Decimal("1e-9")
# Files an agent writes for the lab's protocol, never a script's output (the study's answer file too).
PROTOCOL_FILES = (EVIDENCE_FILE, TEST_FILE, "result.json", INVENTORY_FILE, QUESTIONS_FILE)


@dataclasses.dataclass(frozen=True)
class Written:
    """One number as written: its value, whether it is in scientific notation, and its decimals."""
    value: decimal.Decimal
    sci: bool
    places: int

    @classmethod
    def parse(cls, token: str) -> "Written | None":
        plain = token.replace(",", "")
        try:
            value = decimal.Decimal(plain)
        except decimal.InvalidOperation:
            return None
        sci = "e" in plain.lower()
        places = 0 if sci or "." not in plain else len(plain.split(".")[1])
        return cls(value, sci, places)


def numbers_written(text: str) -> list[str]:
    """Every number token in the text, thousands separators kept as written ("1,234")."""
    return NUMBER_TOKEN.findall(text)


def output_numbers(text: str) -> list["Written"]:
    """The numbers an output file shows. A comma group counts both joined ("12,140" is 12140) and as
    its parts (12 and 140), since a CSV row writes numbers that way too."""
    tokens = NUMBER_TOKEN.findall(text) + PLAIN_TOKEN.findall(text)
    return [w for w in (Written.parse(t) for t in dict.fromkeys(tokens)) if w is not None]


def same_number(quoted: "Written", shown: "Written") -> bool:
    """Whether a number quoted in `result` is one the output shows. Scientific notation on either side:
    equal values within a relative 1e-9. A quoted integer: an integer token of the same value. A quoted
    decimal with d decimals: a token with at least d decimals that rounds half up to it; and when the
    two values differ (the quote is rounded), the quote must show at least two significant digits, so
    0.01 does not stand for 0.006 nor 0.006 for 0.0055, while 0.0031 stands for 0.00312."""
    try:
        if quoted.sci or shown.sci:
            q, s = quoted.value, shown.value
            return abs(q - s) <= SCI_TOLERANCE * max(abs(q), abs(s))
        if quoted.places == 0:
            return shown.places == 0 and quoted.value == shown.value
        if shown.places < quoted.places:
            return False
        with decimal.localcontext() as ctx:
            ctx.prec = max(28, len(shown.value.as_tuple().digits) + 2)
            if shown.value.quantize(decimal.Decimal(1).scaleb(-quoted.places),
                                    rounding=decimal.ROUND_HALF_UP) != quoted.value:
                return False
        return shown.value == quoted.value or significant_digits(quoted.value) >= 2
    except decimal.DecimalException:
        return False


def significant_digits(value: decimal.Decimal) -> int:
    """How many significant digits a number is written with: 0.0031 has two, 0.10 two, 0.01 one."""
    digits = "".join(map(str, value.as_tuple().digits)).lstrip("0")
    return len(digits)


def is_protocol_file(name: str, answer_file: str | None) -> bool:
    """Whether a file name is one the lab's protocol has an agent write (or the study's answer file)."""
    low = name.lower()
    return low in PROTOCOL_FILES or (answer_file is not None and name == pathlib.Path(answer_file).name) \
        or fnmatch.fnmatch(low, "*answer*.json")


def _read(path: pathlib.Path) -> str:
    with open(path, errors="replace") as f:
        return f.read(GUARD_MAX_OUTPUT_CHARS)


def is_decimal_quote(token: str) -> bool:
    """Whether a quoted number is a decimal: written with a decimal point or an exponent."""
    return "." in token or "e" in token.lower()


def check_evidence(workspace: pathlib.Path, started: float, answer_file: str | None = None,
                   strict_decimals: bool = False) -> tuple[bool, str]:
    """The damage guard's check of `evidence.json` in a workspace: (passed, reason).

    evidence.json must be a regular file (not a symbolic link) with all four fields. `result` must
    quote at least one number. The script and the output file must be regular files inside the
    workspace, not symbolic links, and not the same file; the output file must not be a file the
    lab's protocol has the agent write (evidence.json, test_result.json, result.json, the inventory,
    the questions file, the study's answer file `answer_file`, or any `*answer*.json`). The output
    file must have been written at or after the round's start (GUARD_SLACK_S of clock slack) and at
    or after the script's last change. Each of the first GUARD_MAX_NUMBERS numbers in `result` must
    be one the output file shows (same_number; signs ignored), and at least one of them must not
    also be written in the script: a number the script itself holds shows nothing it computed.
    With strict_decimals (the guard_strict_decimals check), every decimal quote among them must
    show at least check_registry.STRICT_MIN_DIGITS significant digits."""
    ws = pathlib.Path(workspace).resolve()
    if is_link(ws / EVIDENCE_FILE):
        return False, f"{EVIDENCE_FILE} is a symbolic link; the lab reads only a regular file"
    doc = load_doc(ws / EVIDENCE_FILE)
    if not isinstance(doc, dict):
        return False, f"no readable {EVIDENCE_FILE}"
    missing = [k for k in EVIDENCE_FIELDS if k not in doc]
    if missing:
        return False, f"{EVIDENCE_FILE} lacks {', '.join(missing)}"
    result = doc["result"] if isinstance(doc["result"], str) else json.dumps(doc["result"])
    quoted = numbers_written(result)[:GUARD_MAX_NUMBERS]
    if not quoted:
        return False, "result quotes no number"
    if strict_decimals:
        for token in quoted:
            number = Written.parse(token)
            if is_decimal_quote(token) and number is not None \
                    and significant_digits(number.value) < check_registry.STRICT_MIN_DIGITS:
                return False, (f"the number {token} in result shows fewer than {check_registry.STRICT_MIN_DIGITS} "
                               "significant digits (guard_strict_decimals)")
    files = {}
    for key in ("script", "output_file"):
        name = doc[key]
        if not isinstance(name, str) or not name.strip():
            return False, f"the {key} {name!r} is not a file in the working folder"
        if is_link(ws / name):
            return False, f"the {key} {name!r} is a symbolic link; the lab reads only a regular file"
        path = agent_file(ws, name)
        if path is None:
            return False, f"the {key} {name!r} is not a file in the working folder"
        files[key] = path.resolve()
    if is_protocol_file(files["output_file"].name, answer_file):
        return False, (f"the output_file {doc['output_file']!r} is a file the lab's protocol has the agent "
                       "write, not a script's output")
    if files["output_file"] == files["script"]:
        return False, "the output_file is the script itself"
    out_time, script_time = files["output_file"].stat().st_mtime, files["script"].stat().st_mtime
    if out_time < started - GUARD_SLACK_S:
        return False, "the output file was written before this round began"
    if out_time < script_time:
        return False, "the output file is older than the script"
    shown = output_numbers(_read(files["output_file"]))
    in_script = output_numbers(_read(files["script"]))
    computed = 0
    for token in quoted:
        number = Written.parse(token)
        if number is None or not any(same_number(number, s) for s in shown):
            return False, f"the number {token} in result does not appear in the output file"
        if not any(same_number(number, s) for s in in_script):
            computed += 1
    if not computed:
        return False, "every number in result is also written in the script, so none shows what the script computed"
    return True, "the evidence checks out"


# ----- leak check -----

NUMBER = re.compile(r"(?:\d+(?:,\d{3})+|\d+)(?:\.\d*)?(?:[eE][-+]?\d+)?%?|\.\d+(?:[eE][-+]?\d+)?%?")


def numbers_in(text: str) -> list[float]:
    """Every number written in the text, as a magnitude. "25%" also yields 0.25."""
    out = []
    for raw in NUMBER.findall(text):
        percent = raw.endswith("%")
        try:
            value = abs(float(raw.rstrip("%").replace(",", "")))
        except ValueError:
            continue
        out.append(value)
        if percent:
            out.append(value / 100)
    return out


def _close(x: float, y: float) -> bool:
    return abs(x - y) <= 0.01 * max(abs(x), abs(y))


def _scalars(values: dict) -> dict[str, list]:
    """Field name -> its scalar values (a list field contributes each scalar item)."""
    out = {}
    for key, value in values.items():
        items = value if isinstance(value, list) else [value]
        out[key] = [v for v in items if isinstance(v, (int, float, str)) and not isinstance(v, bool)]
    return out


def leak_check(question: str, own: dict | None, others: list[dict]) -> list[str]:
    """Reasons a question would leak another team's answer to the team receiving it; empty means it passes.

    A number in the question leaks when it is within 1 percent of another team's value for a
    field, unless the receiving team's own value for that field is also within 1 percent of it.
    Numbers are compared by size, so a dropped minus sign still counts. A string value longer
    than one character leaks when it appears in the question (any case), unless the receiving
    team gave the same string for that field.
    """
    own = _scalars(own or {})
    written = numbers_in(question)
    lower = question.lower()
    reasons = []
    for other in others:
        for key, values in _scalars(other).items():
            mine = own.get(key, [])
            for v in values:
                if isinstance(v, str):
                    s = v.strip().lower()
                    if len(s) > 1 and s in lower and s not in [m.strip().lower() for m in mine if isinstance(m, str)]:
                        reasons.append(f'contains another team\'s value for "{key}"')
                    continue
                for n in written:
                    if _close(n, abs(v)) and not any(isinstance(m, (int, float)) and _close(n, abs(m)) for m in mine):
                        reasons.append(f'a number within 1 percent of another team\'s value for "{key}"')
                        break
    return sorted(set(reasons))


# ----- referee scrub -----

def scrub(obj: Any, names: list[str], keep: tuple = ()) -> Any:
    """Drop every field (or list item) whose text names a team. Keys in `keep` are left untouched."""
    pattern = re.compile("|".join(re.escape(n) for n in names) + r"|team[\s_-]*[abc]\b", re.IGNORECASE)

    def names_team(value) -> bool:
        return pattern.search(json.dumps(value, default=str)) is not None

    def clean(value):
        if isinstance(value, dict):
            return {k: (v if k in keep else clean(v)) for k, v in value.items()
                    if k in keep or not (pattern.search(k) or (not isinstance(v, (dict, list)) and names_team(v)))}
        if isinstance(value, list):
            return [clean(v) for v in value if isinstance(v, (dict, list)) or not names_team(v)]
        return value

    return clean(obj)


# ----- running agents -----

class AgentRunner:
    """Runs agents through a backend: a shared limit on how many run at once, one retry, and logging.
    When any agent reports the account's usage limit, every later run is refused: a study must not
    be recorded as "no answer" just because the account was out of usage.

    With a Budget, each run reserves its estimated cost before it starts and settles to its actual
    cost after agent.finish. A run the budget refuses halts the runner exactly like a usage limit
    (BudgetExceeded is a UsageLimit), with a lab.halted event whose reason is "budget"."""

    halted: str | None = None

    def __init__(self, backend: Backend, log: EventLog, max_agents: int, budget: Budget | None = None):
        self.backend = backend
        self.log = log
        self.slots = threading.Semaphore(max(1, int(max_agents)))
        self.budget = budget
        self.halt_error: type[UsageLimit] = UsageLimit   # what every run after a halt raises

    def run(self, agent: Agent, brief: str, workspace: pathlib.Path, *, readable: list[pathlib.Path],
            env: dict | None, timeout_s: int, study: str, phase: str, role: str,
            brief_file: pathlib.Path | None = None) -> AgentResult:
        """Run one agent, retrying once if it fails. Returns the last AgentResult."""
        workspace.mkdir(parents=True, exist_ok=True)
        if brief_file is not None:
            brief_file.parent.mkdir(parents=True, exist_ok=True)
            brief_file.write_text(brief)
        result = AgentResult(ok=False, result={}, seconds=0.0, error="not run")
        for attempt in (1, 2):
            run_id = uuid.uuid4().hex[:12]
            common = {"agent": agent.id, "role": role, "study": study, "phase": phase, "attempt": attempt,
                      "run_id": run_id}
            with self.slots:
                if self.halted:
                    raise self.halt_error(self.halted)
                token = self.reserve(agent, role, study)
                self.log.append("agent.start", agent.id, **common, model=agent.model, workspace=str(workspace),
                                readable=[str(p) for p in readable], timeout_s=timeout_s)
                t0 = time.time()
                try:
                    result = self.backend.run(agent, brief, workspace, readable=list(readable), env=env,
                                              timeout_s=timeout_s)
                    if not isinstance(result.result, dict):
                        result = dataclasses.replace(result, ok=False, result={}, error="result is not a JSON object")
                except Exception as err:    # a backend crash is a failed run, not a crashed lab
                    result = AgentResult(ok=False, result={}, seconds=time.time() - t0,
                                         error=f"{type(err).__name__}: {err}"[:500])
                try:
                    self.log.append("agent.finish", agent.id, **common, ok=bool(result.ok),
                                    seconds=round(float(result.seconds or 0), 2), cost_usd=result.cost_usd,
                                    session_id=result.session_id, error=result.error)
                finally:
                    if token is not None:
                        self.budget.settle(token, result.cost_usd)   # a missing cost keeps the estimate
            if "usage limit" in (result.error or ""):
                self.halted = result.error
                self.log.append("lab.halted", "lab", reason=result.error, agent=agent.id, study=study)
                raise UsageLimit(result.error)
            if result.ok or "timed out" in (result.error or ""):
                break      # a timed-out run is not retried: it would only spend the same time again
        return result

    def reserve(self, agent: Agent, role: str, study: str) -> int | None:
        """Reserve one run's estimated cost. On BudgetExceeded, halt like a usage limit and re-raise."""
        if self.budget is None:
            return None
        try:
            return self.budget.reserve(role, agent.model)
        except BudgetExceeded as err:
            self.halted, self.halt_error = str(err), BudgetExceeded
            self.log.append("lab.halted", "lab", reason="budget", detail=str(err), agent=agent.id, study=study,
                            spent_usd=round(self.budget.spent(), 4), reserved_usd=round(self.budget.reserved(), 4),
                            max_usd=self.budget.max_usd)
            raise


def unfinished_agents(log: EventLog) -> list[dict]:
    """agent.start events with no finish: no agent.finish with the same run_id, and none later
    for the same agent, study and phase (a later re-run that finished covers a crashed one)."""
    finished_ids, last_finish = set(), {}
    for e in log.events("agent.finish"):
        finished_ids.add(e.get("run_id"))
        last_finish[(e.get("agent"), e.get("study"), e.get("phase"))] = e["seq"]
    return [e for e in log.events("agent.start")
            if e.get("run_id") not in finished_ids
            and last_finish.get((e.get("agent"), e.get("study"), e.get("phase")), 0) < e["seq"]]


# ----- reuse mode -----

FIRST_ROUND_DIR = "first-round"
FIRST_TEAMS = ("team-a", "team-b")
FIRST_ROUND_PHASES = ("library", "briefing", "analysis")


class FirstRoundMissing(RuntimeError):
    """A run asked to reuse a first round that the source run does not have."""


def _source_events(run_dir: pathlib.Path, study_id: str) -> list[dict]:
    """The source run's events of the study's first round: from its last study.start up to the
    end of the analysis round (its last analysis candidate, or its study.reused when it was itself
    reused). memory.write and memory.pass_down events carry no study, so every event in that span
    is kept; callers filter by department."""
    path = pathlib.Path(run_dir) / "events.jsonl"
    events = []
    for line in (path.read_text().splitlines() if path.is_file() else []):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            events.append(e)
    starts = [e["seq"] for e in events if e.get("kind") == "study.start" and e.get("study") == study_id]
    if not starts:
        return []
    ends = [e["seq"] for e in events if e.get("study") == study_id and e["seq"] > starts[-1] and (
        (e.get("kind") in ("candidate", "candidate.none") and e.get("round") == "analysis")
        or e.get("kind") == "study.reused")]
    if not ends:
        return []
    return [e for e in events if starts[-1] <= e["seq"] <= max(ends)]


def first_round_cost(events: list[dict], study_id: str) -> tuple[float | None, int]:
    """(dollars, agent runs) of a study's first round in `events` (from _source_events): its library,
    briefing and analysis runs, plus what a reused source carried in. Dollars are None when any
    run reported no cost."""
    runs = [e for e in events if e.get("kind") == "agent.finish" and e.get("phase") in FIRST_ROUND_PHASES
            and e.get("study") == study_id]
    carried = [e for e in events if e.get("kind") == "study.reused" and e.get("study") == study_id]
    count = len(runs) + sum(int(e.get("first_round_runs") or 0) for e in carried)
    costs = [e.get("cost_usd") for e in runs] + [e.get("first_round_cost_usd") for e in carried]
    if any(c is None for c in costs):
        return None, count
    return round(sum(float(c) for c in costs), 6), count


# ----- one candidate answer -----

@dataclasses.dataclass
class Candidate:
    team: str          # "team-a", "team-b", "team-c" or "referee"
    agent: str
    round: str         # "analysis", "revise", "third-team" or "referee"
    path: pathlib.Path  # the lab's copy, outside every workspace
    problems: list
    values: dict
    not_used: str | None = None   # why the lab kept an earlier answer over this valid one (guard, re-check)

    @property
    def valid(self) -> bool:
        return not self.problems

    @property
    def usable(self) -> bool:
        return self.valid and not self.not_used

    @property
    def id(self) -> str:
        return "referee-own" if self.team == "referee" else f"{self.team}-{self.round}"

    def record(self) -> dict:
        out = {"team": self.team, "agent": self.agent, "round": self.round, "path": str(self.path),
               "valid": self.valid, "problems": self.problems, "answer": self.values}
        if self.not_used:
            out["not_used"] = self.not_used
        return out


# ----- the lab -----

class Lab:
    """Runs studies through the protocol. One instance per run directory."""

    def __init__(self, adapter: Adapter, backend: Backend, memory: Memory, log: EventLog,
                 run_dir: pathlib.Path, config: Config | None = None, *, exa_call=None, gateway_factory=None):
        self.adapter = adapter
        # Builds the gateway with Gateway's arguments; lab.modal_gateway.ModalGateway runs it on Modal.
        self.gateway_factory = gateway_factory or Gateway
        self.memory = memory
        self.log = log
        self.run_dir = pathlib.Path(run_dir).resolve()   # briefs carry paths; agents run in their own folder
        self.config = config or Config()
        self.exa_call = exa_call
        # A dollar cap counts what this run directory spent before (a resumed run) against the cap.
        budget = (Budget(self.config.max_usd, prior_spend(log), estimates=estimates_for(getattr(adapter, "name", None)))
                  if self.config.max_usd is not None else None)
        self.runner = AgentRunner(backend, log, self.config.max_agents, budget=budget)
        self.rng = random.Random(self.config.seed)
        self.org: Org | None = None
        self.gateway: Gateway | None = None
        self.gateway_url = ""
        self.tokens: dict[str, str] = {}
        self.delivered: set[str] = set()     # fact ids the lab itself passed down
        self.blocked_terms: list[str] = []   # what identifies a task's source; research questions may not hold it

    def build_org(self, studies: list[Study]) -> Org:
        cfg = self.config
        agents = [Agent("director", DIRECTOR, "director", cfg.director_model)]
        for s in studies:
            agents.append(Agent(f"{s.id}-library", WORKER, library_department(s), cfg.worker_model))
            for team in TEAMS:
                model = (cfg.team_b_model or cfg.worker_model) if team == "team-b" else cfg.worker_model
                agents.append(Agent(f"{s.id}-{team}", WORKER, f"{s.id}-{team}", model))
            agents.append(Agent(f"{s.id}-referee", WORKER, f"{s.id}-referee", cfg.worker_model))
        if cfg.heads:
            # One head per department, shared by every study, like the director. Their own notes go to
            # the department "heads", which only heads and the director can read.
            agents += [Agent(head_id, HEAD, HEAD_DEPARTMENT, cfg.head_model) for head_id in HEADS.values()]
        return Org(agents=agents)

    def finished(self) -> dict[str, dict]:
        return {e["study"]: e for e in self.log.events("study.final")}

    def run(self, studies: list[Study]) -> dict[str, dict]:
        """Run every study not finished already. Returns study id -> {"status", ...}."""
        done = self.finished()
        out = {}
        for s in studies:
            if s.id in done:
                self.log.append("study.skipped", "lab", study=s.id, reason="already has study.final")
                out[s.id] = {"status": done[s.id]["status"], "skipped": True}
        todo = [s for s in studies if s.id not in done]
        if not todo:
            return out
        self.org = self.build_org(studies)
        self.memory.setup(self.org)
        # Agents may not pass facts down themselves: the lab delivers every pass-down after its leak check.
        # Who may search: each study's library, and its team A when team search is on. Each has a cap.
        caps = {}
        for s in studies:
            if self.config.library:
                caps[f"{s.id}-library"] = self.config.max_searches
            if self.config.team_search:
                caps[f"{s.id}-team-a"] = self.config.team_searches
        departments = sorted({self.org.agent(a).department for a in caps})
        kwargs = {"exa_departments": departments, "exa_caps": caps, "agents_may_pass_down": False}
        if hasattr(self.adapter, "blocked_terms"):
            self.blocked_terms = [t.lower() for t in self.adapter.blocked_terms() if t]
            kwargs["blocked_terms"] = self.adapter.blocked_terms()
        if self.exa_call is not None:
            kwargs["exa_call"] = self.exa_call
        self.gateway = self.gateway_factory(self.org, self.memory, self.log, **kwargs)
        self.gateway_url = self.gateway.start()
        try:
            self.tokens = {a.id: self.gateway.issue_token(a.id) for a in self.org.agents}
        except BaseException:
            self.gateway.stop()
            raise
        self.log.append("lab.start", "lab", studies=[s.id for s in todo], config=dataclasses.asdict(self.config))
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(todo)) as pool:
                futures = {pool.submit(StudyRun(self, s).run): s for s in todo}
                for fut in concurrent.futures.as_completed(futures):
                    s = futures[fut]
                    try:
                        out[s.id] = fut.result()
                    except Exception as err:
                        self.log.append("study.error", "lab", study=s.id, error=f"{type(err).__name__}: {err}"[:500])
                        out[s.id] = {"status": "error", "error": f"{type(err).__name__}: {err}"}
        finally:
            self.gateway.stop()
        return out

    def env(self, agent: Agent) -> dict:
        # On Modal every agent's connect token comes with its own URL.
        url = self.gateway.url_for(agent.id) if hasattr(self.gateway, "url_for") else self.gateway_url
        return {"LAB_GATEWAY_URL": url, "LAB_GATEWAY_TOKEN": self.tokens[agent.id]}

    def open_budget(self, agent_id: str, cap: int, label: str) -> None:
        """Give an agent a fresh search budget on the gateway before a run that may search."""
        if hasattr(self.gateway, "set_budget"):
            self.gateway.set_budget(agent_id, cap, label)

    def wait_settled(self) -> None:
        """Wait until past.dev has derived memory from every write. Slow (minutes); the loop no longer uses it."""
        if hasattr(self.gateway, "sync"):     # a remote gateway: fetch its latest writes first
            self.gateway.sync()
        if hasattr(self.memory, "wait_settled"):
            self.memory.wait_settled()


class StudyRun:
    """The state of one study while it runs, and the steps of the protocol."""

    def __init__(self, lab: Lab, study: Study):
        self.lab = lab
        self.study = study
        self.cfg = lab.config
        self.dir = lab.run_dir / study.id
        self.deliverable = answer_file(study)
        self.briefed = [t for t in ("team-a", "team-b") if t in self.cfg.brief_teams]
        self.research_on = bool(self.cfg.research and self.cfg.library)
        self.findings_delivered = 0
        names = ["director", f"{study.id}-library", *(f"{study.id}-{t}" for t in TEAMS), f"{study.id}-referee"]
        self.record: dict = {"study": study.id, "status": None, "final": None, "switches": {
            "library": self.cfg.library, "questions": self.cfg.questions, "referee": self.cfg.referee,
            "challenge": self.cfg.challenge, "brief_teams": list(self.cfg.brief_teams),
            "team_search": self.cfg.team_search, "research": self.research_on},
            "models": {n: lab.org.agent(n).model for n in names + (list(HEADS.values()) if self.cfg.heads else [])},
            "briefed_teams": self.briefed if self.cfg.library else [],
            "candidates": [], "comparisons": [], "briefing": [], "questions": [], "warning_signs_to_team_c": [],
            "challenged": False, "challenge_questions": None, "challenge_changed_answer": None, "challenge": None,
            "referee": None, "research": [], "not_chosen": [], "agent_runs": [], "unsanctioned_pass_downs": []}
        if self.cfg.heads:                         # recorded only when on, like the switches below
            self.record["switches"]["heads"] = True
            self.record["heads"] = {}
        for switch in ("recheck", "guard"):       # recorded only when on, so a default study.json is unchanged
            if getattr(self.cfg, switch):
                self.record["switches"][switch] = True
                self.record[switch] = []
        if check_registry.is_on(self.cfg, check_registry.RECHECK_ON_AGREED_NV):
            self.record.setdefault("recheck", [])
        for key in ("checks", "prompt_dir", "reuse_first_round"):   # the improvement loop's settings, when set
            if getattr(self.cfg, key):
                self.record["switches"][key] = list(self.cfg.checks) if key == "checks" else str(getattr(self.cfg, key))
        self.candidates: list[Candidate] = []
        self.start_seq = 0

    # ----- helpers -----

    def agent(self, suffix: str) -> Agent:
        return self.lab.org.agent("director" if suffix == "director" else f"{self.study.id}-{suffix}")

    def brief(self, name: str, agent: Agent | None, **fields) -> str:
        """make_brief through the run's prompt overlay (Config.prompt_dir)."""
        return make_brief(name, agent, prompt_dir=self.cfg.prompt_dir, **fields)

    @property
    def strict_decimals(self) -> bool:
        return check_registry.is_on(self.cfg, check_registry.GUARD_STRICT_DECIMALS)

    def dept(self, team: str) -> str:
        return f"{self.study.id}-{team}"

    def log(self, kind: str, **data) -> dict:
        return self.lab.log.append(kind, "lab", study=self.study.id, **data)

    def run_agent(self, agent: Agent, brief: str, folder: str, readable: list, phase: str, role: str) -> AgentResult:
        result = self.lab.runner.run(agent, brief, self.dir / folder, readable=[pathlib.Path(p) for p in readable],
                                     env=self.lab.env(agent), timeout_s=self.cfg.timeout(role), study=self.study.id,
                                     phase=phase, role=role, brief_file=self.dir / "briefs" / f"{phase}-{folder}.md")
        self.record["agent_runs"].append({"agent": agent.id, "phase": phase, "ok": result.ok,
                                          "seconds": result.seconds, "cost_usd": result.cost_usd, "error": result.error})
        return result

    def fields(self, **extra) -> dict:
        """Template fields every brief may use."""
        s = self.study
        return {"statement": s.statement, "data_dir": str(s.data_dir), "max_searches": self.cfg.max_searches,
                "team_searches": self.cfg.team_searches, "research_searches": self.cfg.research_searches,
                "search": "", "findings_note": "", "research": "", "research_shape": "", "evidence_note": "", "heads_note": "",
                "questions_source": QUESTIONS_SOURCE.format(tools=TOOLS), "reply_note": REPLY_NOTE,
                "study_id": s.id,
                "time_limit_minutes": self.cfg.timeout("analyst") // 60, "answer_file": self.deliverable,
                "library_department": library_department(s),
                "team_departments": f"{self.dept('team-a')}, {self.dept('team-b')}", **extra}

    def take(self, team: str, round_: str, result: AgentResult, workspace: pathlib.Path) -> Candidate | None:
        """Copy an agent's answer out of its workspace and check its format. None if the run failed."""
        if not result.ok:
            # An agent stopped by its time limit may still have left an answer file (it is told to write one early).
            if "timed out" in (result.error or "") and (workspace / self.deliverable).is_file():
                self.log("candidate.salvaged", team=team, round=round_, reason=result.error)
            else:
                self.log("candidate.none", team=team, round=round_, reason=result.error or "run failed")
                return None
        name = result.result.get("answer_file") or self.deliverable
        source = agent_file(workspace, name) or agent_file(workspace, self.deliverable)   # never a symbolic link
        store = self.dir / "candidates"
        store.mkdir(parents=True, exist_ok=True)
        path = store / f"{team}-{round_}.json"
        if source:
            shutil.copyfile(source, path)
        else:
            path.write_text("")
        note = agent_file(workspace, "result.json")
        if note:
            shutil.copyfile(note, store / f"{team}-{round_}-result.json")
        problems = list(self.lab.adapter.problems(self.study, self.deliverable, path)) if source \
            else ["no answer file"]
        cand = Candidate(team, self.agent(team).id if team != "referee" else self.agent("referee").id,
                         round_, path, problems, answer_values(path) if not problems else {})
        self.add(cand)
        self.log("candidate", team=team, round=round_, valid=cand.valid, problems=problems)
        return cand

    def add(self, cand: Candidate) -> None:
        """Keep a candidate. study.json's list is rebuilt from these at the end (finish), so a later
        mark (not_used) reaches it."""
        self.candidates.append(cand)
        self.record["candidates"].append(cand.record())

    def compare(self, x: Candidate, y: Candidate, round_: str) -> bool:
        fields = field_agreement(self.lab.adapter, self.study, x.path, y.path)
        agree = bool(fields) and all(fields.values())
        self.record["comparisons"].append({"round": round_, "between": [x.agent, y.agent], "fields": fields,
                                           "agree": agree})
        self.log("compare", round=round_, between=[x.agent, y.agent], fields=fields, agree=agree)
        return agree

    def pass_down(self, to_department: str, text: str, came_from: list, reason: str) -> str:
        fact_id = self.lab.memory.pass_down("director", to_department, text, [str(c) for c in came_from], reason)
        self.lab.delivered.add(fact_id)
        return fact_id

    def wait_visible(self, waits: dict[str, list[str]]) -> None:
        """For each agent, wait until it can read the facts just delivered to it. Missing ones are logged, not fatal."""
        for agent_id, fact_ids in waits.items():
            if not fact_ids:
                continue
            missing = self.lab.memory.wait_visible(agent_id, list(fact_ids))
            if missing:
                self.log("relay.not_visible", agent=agent_id, missing=missing)

    def library_facts(self, since: int | None = None) -> list[str]:
        """Ids of the facts the library wrote in this study (or after event `since`). A remote gateway's
        writes are fetched first."""
        if hasattr(self.lab.gateway, "sync"):
            self.lab.gateway.sync()
        library = self.agent("library").id
        after = self.start_seq if since is None else since
        return [e["fact_id"] for e in self.lab.log.events("memory.write")
                if e.get("actor") == library and e["seq"] > after and e.get("fact_id")]

    def team_notes(self, ws: pathlib.Path) -> pathlib.Path:
        """Copy both teams' result.json (method notes, never answer files) into `ws/teams`."""
        teams_dir = ws / "teams"
        teams_dir.mkdir(parents=True, exist_ok=True)
        for team in ("team-a", "team-b"):
            note = agent_file(self.dir / team, "result.json")    # never a symbolic link
            if note:
                shutil.copyfile(note, teams_dir / f"{self.dept(team)}.json")
        return teams_dir

    # ----- what a head gets to read -----

    def study_log(self, ws: pathlib.Path) -> pathlib.Path:
        """Copy this study's events so far into `ws/log/events.jsonl`: a head reads the logs, workers never do."""
        if hasattr(self.lab.gateway, "sync"):
            self.lab.gateway.sync()
        sid = self.study.id
        mine = lambda value: isinstance(value, str) and (value == sid or value.startswith(sid + "-"))
        lines = [json.dumps(e, default=str) for e in self.lab.log.events()
                 if e["seq"] >= self.start_seq and any(mine(e.get(k)) for k in ("study", "actor", "agent", "to_department"))]
        path = ws / "log" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + ("\n" if lines else ""))
        return path

    def copy_work(self, team: str, dest: pathlib.Path) -> pathlib.Path:
        """Copy a scientist's own work (notes, answer, scripts; no raw session logs, nothing large) for its head."""
        src = self.dir / team
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*") if src.is_dir() else []:
            rel = f.relative_to(src)
            if not f.is_file() or any(part.startswith(".") for part in rel.parts):
                continue
            if f.suffix.lower() not in WORK_SUFFIXES or f.stat().st_size > WORK_MAX_BYTES:
                continue
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest / rel)
        return dest

    def fact_departments(self) -> dict[str, str]:
        """Fact id -> the department it was written or passed down to, from the event log."""
        out = {}
        for e in self.lab.log.events():
            if e["kind"] == "memory.write" and e.get("fact_id"):
                out[e["fact_id"]] = e.get("department") or ""
            elif e["kind"] == "memory.pass_down" and e.get("fact_id"):
                out[e["fact_id"]] = e.get("to_department") or ""
        return out

    # ----- department heads (D-23) -----

    def head_library_review(self) -> dict:
        """The head of the library reviews the library's findings before the director's briefing."""
        head = self.lab.org.agent(HEADS["library"])
        ws = self.dir / "head-library"
        log_file = self.study_log(ws)
        brief = self.brief("head_library", head, **self.fields(log_file=str(log_file)))
        result = self.run_agent(head, brief, "head-library", [self.study.data_dir, TOOLS], "head-review", "head")
        doc = result.result if result.ok else {}
        lists = {k: doc.get(k) if isinstance(doc.get(k), list) else [] for k in ("strong", "weak", "missing")}
        review = {"ran": bool(result.ok), "verdict": doc.get("verdict") if result.ok else f"review failed: {result.error}",
                  **lists}
        self.record["heads"]["library"] = review
        self.log("head.review", head=head.id, of=library_department(self.study), ran=review["ran"],
                 weak=len(lists["weak"]), missing=len(lists["missing"]))
        return review

    def heads_note(self, review: dict | None) -> str:
        """The director's briefing brief: what the head of the library said, if it reviewed."""
        if not review or not review.get("ran"):
            return ""
        def line(item):
            if isinstance(item, dict):
                return f"{item.get('fact', '')}: {item.get('why', '')}".strip(": ")
            return str(item)
        parts = [f"\n\nThe head of the library reviewed these findings before you. Its verdict: {review.get('verdict') or 'none given'}"]
        if review["weak"]:
            parts.append("Findings it judged weak or likely to mislead:\n" + "\n".join(f"- {line(w)}" for w in review["weak"]))
        if review["missing"]:
            parts.append("What it found missing:\n" + "\n".join(f"- {line(m)}" for m in review["missing"]))
        parts.append("Weigh its review; you still decide what is passed down.")
        return "\n\n".join(parts)

    def head_team_review(self, team: str, own: Candidate | None, other: Candidate | None) -> Candidate | None:
        """A team's head reviews its scientist's answer before the comparison and may send notes.
        Returns the answer to compare: the revised one if the team revised and it is valid, else `own`."""
        if own is None:
            return own
        head = self.lab.org.agent(HEADS[team])
        ws = self.dir / f"head-{team}"
        work = self.copy_work(team, ws / "work")
        log_file = self.study_log(ws)
        dept = self.dept(team)
        brief = self.brief("head_team", head, **self.fields(
            team_name=TEAM_NAMES[team], team_department=dept, work_dir=str(work), log_file=str(log_file),
            max_notes=self.cfg.max_head_notes, note_prefix=f"NOTE FROM THE HEAD OF {TEAM_NAMES[team].upper()}:"))
        result = self.run_agent(head, brief, f"head-{team}", [self.study.data_dir, TOOLS], "head-review", "head")
        doc = result.result if result.ok else {}
        other_dept = self.dept("team-b" if team == "team-a" else "team-a")
        departments = self.fact_departments()
        others = [other.values] if other is not None and other.values else []
        notes, waits = [], []
        for item in doc.get("notes") if isinstance(doc.get("notes"), list) else []:
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            came_from = [str(x) for x in item.get("from") or [] if isinstance(x, str)] \
                if isinstance(item.get("from"), list) else []
            reasons = leak_check(text, own.values, others)
            if any(departments.get(f) == other_dept for f in came_from):
                reasons.append("cites the other team's notes; only the director passes findings between teams")
            if self.names_source(text):
                reasons.append("names something that identifies this task's source")
            if sum(n["delivered"] for n in notes) >= self.cfg.max_head_notes:
                reasons = [f"more than {self.cfg.max_head_notes} notes"]
            passed = not reasons
            self.log("relay.leak_check", round="head-review", to=dept, passed=passed, reasons=sorted(set(reasons)),
                     question=text, by=head.id)
            entry = {"text": text, "from": came_from, "why": str(item.get("why") or ""), "delivered": passed,
                     "reasons": sorted(set(reasons)), "fact_id": None}
            if passed:
                entry["fact_id"] = self.lab.memory.pass_down(head.id, dept, text, came_from,
                                                             entry["why"] or f"the head of {TEAM_NAMES[team]} reviewed this answer")
                self.lab.delivered.add(entry["fact_id"])
                waits.append(entry["fact_id"])
            notes.append(entry)
        review = {"ran": bool(result.ok), "approve": bool(doc.get("approve")) if result.ok else None,
                  "verdict": doc.get("verdict") if result.ok else f"review failed: {result.error}",
                  "notes": notes, "revised": False, "changed_answer": None}
        self.record["heads"][team] = review
        self.log("head.review", head=head.id, of=dept, ran=review["ran"], approve=review["approve"],
                 notes=len(notes), delivered=len(waits))
        if not waits:
            return own
        self.wait_visible({self.agent(team).id: waits})
        revised = self.analyse(team, "analyst_review", round_="review", phase="review")
        review["revised"] = True
        if revised is None or not revised.valid:
            return own
        review["changed_answer"] = not all(field_agreement(self.lab.adapter, self.study, own.path, revised.path).values()) \
            if own.valid else True
        return revised

    # ----- steps -----

    def library(self) -> None:
        agent = self.agent("library")
        self.lab.open_budget(agent.id, self.cfg.max_searches, "library")
        brief = self.brief("library", agent, **self.fields())
        self.run_agent(agent, brief, "library", [self.study.data_dir, TOOLS], "library", "library")

    def briefing(self, library_review: dict | None = None) -> list[dict]:
        director = self.agent("director")
        brief = self.brief("director_relay", director,
                           **self.fields(team_departments=", ".join(self.dept(t) for t in self.briefed),
                                         heads_note=self.heads_note(library_review)))
        result = self.run_agent(director, brief, "director-briefing", [TOOLS], "briefing", "director")
        items = result.result.get("pass_down") if result.ok else None
        kept = []
        for item in items if isinstance(items, list) else []:
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            came_from = item.get("from") if isinstance(item.get("from"), list) else []
            reason = item.get("reason") if isinstance(item.get("reason"), str) and item["reason"].strip() \
                else "the director judged the teams need it"
            ids = {team: self.pass_down(self.dept(team), text, came_from, reason) for team in self.briefed}
            entry = {"text": text, "from": came_from, "reason": reason, "fact_ids": ids}
            kept.append(entry)
            self.record["briefing"].append(entry)
        self.log("relay.briefing", items=len(kept), teams=self.briefed)
        self.wait_visible({self.agent(t).id: [e["fact_ids"][t] for e in kept] for t in self.briefed})
        return kept

    def analyse(self, team: str, template_name: str = "analyst", round_: str = "analysis",
                phase: str = "analysis") -> Candidate | None:
        """Run one team. A revising round ("revise" or "challenge") gets that round's {situation} line.
        Team A gets the search section, and a fresh search budget, when team search is on."""
        agent = self.agent(team)
        extra = {}
        if team == "team-a" and self.cfg.team_search:
            extra["search"] = "\n\n" + self.brief("team_search", None, **self.fields()).strip()
            self.lab.open_budget(agent.id, self.cfg.team_searches, "team-a")
        if template_name == "analyst_revise" and self.research_on:
            extra["findings_note"] = FINDINGS_NOTE.format(tools=TOOLS)
        if template_name == "analyst_revise" and self.cfg.guard:
            extra["evidence_note"] = evidence_note(self.cfg)
        brief = self.brief(template_name, agent, **self.fields(diversity=DIVERSITY[team],
                                                               situation=SITUATION.get(round_, ""), **extra))
        result = self.run_agent(agent, brief, team, [self.study.data_dir, TOOLS], phase, "analyst")
        return self.take(team, round_, result, self.dir / team)

    def both(self, fn, *args) -> tuple:
        """Run fn("team-a", ...) and fn("team-b", ...) at the same time."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(fn, "team-a", *args)
            fb = pool.submit(fn, "team-b", *args)
            return fa.result(), fb.result()

    def questions(self, a: Candidate, b: Candidate) -> list[dict]:
        """The director's question round: write, leak-check and deliver. Returns every question."""
        director = self.agent("director")
        teams_dir = self.team_notes(self.dir / "director-questions")
        brief = self.brief("director_crossexam", director, **self.fields(teams_dir=str(teams_dir),
                                                                         **self.research_fields()))
        result = self.run_agent(director, brief, "director-questions", [TOOLS], "questions", "director")
        doc = result.result if result.ok else {}
        answers = {self.dept("team-a"): (a.values, b.values), self.dept("team-b"): (b.values, a.values)}
        out = self.deliver(doc.get("questions"), answers, "questions",
                           "question from the director after the teams' answers differed")
        self.record["questions"].extend(out)
        self.research(doc, a, b, answers, "questions")
        return out

    def differing(self, own: Candidate, other: Candidate) -> dict:
        """The other team's values for the fields where it differs from `own`: the only ones that could leak."""
        fields = field_agreement(self.lab.adapter, self.study, own.path, other.path)
        if set(fields) == {"all"}:
            return {} if fields["all"] else dict(other.values)
        return {k: v for k, v in other.values.items() if fields.get(k) is not True}

    def challenge(self, a: Candidate, b: Candidate) -> list[dict]:
        """The director challenges an agreed answer. Records its findings, delivers its questions, returns them."""
        director = self.agent("director")
        teams_dir = self.team_notes(self.dir / "director-challenge")
        brief = challenge_brief(director, self.fields(teams_dir=str(teams_dir), **self.research_fields()), teams=2,
                                prompt_dir=self.cfg.prompt_dir)
        result = self.run_agent(director, brief, "director-challenge", [self.study.data_dir, TOOLS], "challenge",
                                "director")
        doc = result.result if result.ok else {}
        found = {k: doc.get(k) if isinstance(doc.get(k), list) else []
                 for k in ("shared_assumptions", "unused_evidence", "unresolved_weak_points", "second_readings")}
        verdict = doc.get("verdict") if result.ok else f"challenge run failed: {result.error}"
        # The teams agree, so a team's own value is no secret: check each question only against the
        # other team's values for fields where the two differ (none, when they agree on every field).
        answers = {self.dept("team-a"): (a.values, self.differing(a, b)),
                   self.dept("team-b"): (b.values, self.differing(b, a))}
        out = self.deliver(doc.get("questions"), answers, "challenge",
                           "question from the director challenging the answer both teams agreed on")
        self.record["challenge"] = {"ran": bool(result.ok), **found, "verdict": verdict, "questions": out}
        self.research(doc, a, b, answers, "challenge")
        return out

    # ----- research on demand -----

    def research_fields(self) -> dict:
        """The director brief's research section and result.json shape, or nothing when research is off."""
        if not self.research_on:
            return {}
        section = self.brief("director_research", None, **self.fields()).strip()
        return {"research": "\n\n" + section, "research_shape": RESEARCH_SHAPE}

    def names_source(self, text: str) -> bool:
        """Whether the text holds a term that identifies this task's source (the adapter's blocked terms)."""
        lower = text.lower()
        return any(term in lower for term in self.lab.blocked_terms)

    def research(self, doc: dict, a: Candidate, b: Candidate, answers: dict[str, tuple[dict, dict]], round_: str) -> int:
        """Research on demand. Leak-check the director's research questions against both teams' answers
        (and the task's source), run the library on those that pass, then leak-check each finding like a
        question and pass it down to both teams under the director's name, and wait until each team can
        read it. Records everything under "research". Returns how many deliveries were made."""
        raw = doc.get("research_questions") if isinstance(doc, dict) else None
        if not self.research_on or not isinstance(raw, list):
            return 0
        asked = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("question"), str) or not item["question"].strip():
                continue
            question, why = item["question"].strip(), str(item.get("why") or "").strip()
            reasons = leak_check(f"{question} {why}", None, [a.values, b.values])
            if self.names_source(f"{question} {why}"):
                reasons.append("names something that identifies this task's source")
            if sum(q["passed"] for q in asked) >= MAX_RESEARCH_QUESTIONS:
                reasons = [f"more than {MAX_RESEARCH_QUESTIONS} research questions"]
            passed = not reasons
            self.log("research.leak_check", round=round_, passed=passed, reasons=reasons, question=question)
            asked.append({"question": question, "why": why, "passed": passed, "reasons": reasons})
        if not asked:
            return 0
        entry = {"round": round_, "questions": asked, "ran": False, "error": None, "findings": []}
        self.record["research"].append(entry)
        sent = [q for q in asked if q["passed"]]
        if not sent:
            return 0
        agent = self.agent("library")
        self.lab.open_budget(agent.id, self.cfg.research_searches, "research")
        listing = "\n".join(f"{i}. {q['question']}" + (f"\n   Why it matters: {q['why']}" if q["why"] else "")
                            for i, q in enumerate(sent, 1))
        brief = self.brief("library_research", agent, **self.fields(research_questions=listing))
        since = self.log("research.start", round=round_, questions=len(sent))["seq"]
        result = self.run_agent(agent, brief, "library-research", [TOOLS], "research", "research")
        entry["ran"], entry["error"] = bool(result.ok), None if result.ok else result.error
        came_from = self.library_facts(since)
        found = result.result.get("findings") if result.ok else None
        waits: dict[str, list[str]] = {}
        delivered = 0
        for f in found if isinstance(found, list) else []:
            if not isinstance(f, dict) or not isinstance(f.get("answer"), str) or not f["answer"].strip():
                continue
            sources = [str(x) for x in f.get("sources") or [] if isinstance(x, str)] \
                if isinstance(f.get("sources"), list) else []
            finding = {"question": str(f.get("question") or ""), "answer": f["answer"].strip(), "sources": sources,
                       "confidence": f.get("confidence"), "deliveries": {}}
            finding["text"] = (f"FINDING FROM THE LAB'S LIBRARY, asked for by the director. QUESTION: "
                               f"{finding['question']} ANSWER: {finding['answer']} HOW SURE: {finding['confidence']}. "
                               f"SOURCES: {', '.join(sources) or 'none given'}.")
            for dept, (own, other) in answers.items():
                reasons = leak_check(finding["text"], own, [other])
                passed = not reasons
                self.log("relay.leak_check", round="research", to=dept, passed=passed, reasons=reasons,
                         question=finding["text"])
                fact_id = self.pass_down(dept, finding["text"], came_from,
                                         "a research finding the director asked the library for") if passed else None
                finding["deliveries"][dept] = {"delivered": passed, "reasons": reasons, "fact_id": fact_id}
                if passed:
                    waits.setdefault(dept, []).append(fact_id)   # a team's agent id is its department
                    delivered += 1
            entry["findings"].append(finding)
        self.wait_visible(waits)
        self.log("research.delivered", round=round_, findings=len(entry["findings"]), deliveries=delivered)
        self.findings_delivered += delivered
        return delivered

    def deliver(self, raw, answers: dict[str, tuple[dict, dict]], round_: str, reason: str) -> list[dict]:
        """Leak-check each question the director wrote and pass down those that pass, then wait until
        each receiving team can read its questions. `answers` maps a team department to (its own
        values, the values it must not learn). Returns one entry per question."""
        out = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("question"), str) or not item["question"].strip():
                continue
            to = str(item.get("to") or "").strip()
            if to not in answers:
                matches = [d for d in answers if d.endswith(to) and to]
                to = matches[0] if len(matches) == 1 else to
            came_from = item.get("from") if isinstance(item.get("from"), list) else []
            entry = {"to": to, "question": item["question"], "from": came_from, "delivered": False,
                     "reasons": [], "fact_id": None}
            if to not in answers:
                entry["reasons"] = [f"no team department called {to!r}"]
            else:
                own, other = answers[to]
                entry["reasons"] = leak_check(item["question"], own, [other])
            passed = not entry["reasons"]
            self.log("relay.leak_check", round=round_, to=to, passed=passed, reasons=entry["reasons"],
                     question=item["question"])
            if passed:
                entry["fact_id"] = self.pass_down(to, item["question"], came_from, reason)
                entry["delivered"] = True
            out.append(entry)
        waits: dict[str, list[str]] = {}
        for entry in out:
            if entry["delivered"]:
                waits.setdefault(entry["to"], []).append(entry["fact_id"])   # a team's agent id is its department
        self.wait_visible(waits)
        return out

    def referee(self, a: Candidate, b: Candidate) -> dict:
        """Give the referee the two answers as X and Y in random order. Returns its verdict."""
        agent = self.agent("referee")
        ws = self.dir / "referee"
        order = [a, b]
        self.lab.rng.shuffle(order)
        mapping = {"X": order[0].agent, "Y": order[1].agent}
        self.log("referee.assign", mapping=mapping)      # only the log knows which is which
        names = [self.dept(t) for t in TEAMS] + [self.study.id + "-" + t for t in TEAMS]
        for label, cand in zip(("X", "Y"), order):
            folder = ws / "candidates" / label
            folder.mkdir(parents=True, exist_ok=True)
            doc = load_doc(cand.path)
            (folder / self.deliverable).write_text(json.dumps(scrub(doc, names, keep=("answer",)), indent=2))
            note = load_doc(self.dir / "candidates" / f"{cand.team}-{cand.round}-result.json")
            (folder / "method.json").write_text(json.dumps(scrub(note if isinstance(note, dict) else {}, names), indent=2))
        brief = self.brief("referee", agent, **self.fields(
            candidates_dir=str(ws / "candidates"), time_limit_minutes=self.cfg.timeout("referee") // 60,
            evidence_note=evidence_note(self.cfg, REFEREE_EVIDENCE_NOTE)))
        started = time.time()
        result = self.run_agent(agent, brief, "referee", [self.study.data_dir, TOOLS], "referee", "referee")
        verdict = dict(result.result) if result.ok else {"pick": "neither", "reason": f"referee run failed: {result.error}"}
        pick = str(verdict.get("pick") or "").strip().upper()
        verdict["pick"] = pick if pick in ("X", "Y") else "neither"
        verdict["picked_agent"] = mapping.get(verdict["pick"])
        own = verdict.get("own_answer")
        verdict["own_answer_valid"] = False
        if isinstance(own, dict) and own:
            path = self.dir / "candidates" / "referee-own.json"
            path.write_text(json.dumps(own, indent=2))
            problems = list(self.lab.adapter.problems(self.study, self.deliverable, path))
            cand = Candidate("referee", agent.id, "referee", path, problems, answer_values(path) if not problems else {})
            self.add(cand)
            verdict["own_answer_valid"] = cand.valid
            if cand.valid and self.cfg.guard:    # the referee's own answer would replace both teams' answers
                ok, why = check_evidence(ws, started, self.deliverable, self.strict_decimals)
                self.guard_event("referee", "referee", ok, why, cand, None)
                if not ok:
                    cand.not_used = f"blocked by the damage guard: {why}"
                    verdict["own_answer_blocked"] = True
        self.record["referee"] = {**verdict, "mapping": mapping}
        self.log("referee.verdict", pick=verdict["pick"], picked_agent=verdict["picked_agent"],
                 own_answer_valid=verdict["own_answer_valid"])
        return verdict

    def team_c(self, briefing: list[dict], questions: list[dict], a: Candidate, b: Candidate) -> Candidate | None:
        """Brief team C like the first two, add every question as a warning sign, and run it blind."""
        dept = self.dept("team-c")
        sent = [self.pass_down(dept, item["text"], item["from"], item["reason"]) for item in briefing]
        for q in questions:
            reasons = leak_check(q["question"], None, [a.values, b.values])   # C has no answer of its own
            passed = not reasons
            self.log("relay.leak_check", round="team-c", to=dept, passed=passed, reasons=reasons, question=q["question"])
            entry = {"question": q["question"], "delivered": passed, "reasons": reasons, "fact_id": None}
            if passed:
                entry["fact_id"] = self.pass_down(dept, "WARNING SIGN TO CHECK: " + q["question"], q["from"],
                                                  "raised when two earlier teams disagreed; check it on the data")
                sent.append(entry["fact_id"])
            self.record["warning_signs_to_team_c"].append(entry)
        self.wait_visible({self.agent("team-c").id: sent})
        return self.analyse("team-c", round_="third-team", phase="third-team")

    # ----- the flow -----

    def run(self) -> dict:
        if self.dir.exists():    # an earlier run of this study stopped before study.final
            self.dir.rename(self.dir.with_name(f"{self.study.id}.stale-{time.strftime('%Y%m%d%H%M%S')}"))
        self.dir.mkdir(parents=True)
        self.start_seq = self.log("study.start")["seq"]
        briefing = []
        if self.cfg.reuse_first_round:
            a, b, briefing = self.load_first_round(self.cfg.reuse_first_round)
        else:
            if self.cfg.library:
                self.library()
                self.wait_visible({self.agent("director").id: self.library_facts()})
                review = self.head_library_review() if self.cfg.heads else None
                briefing = self.briefing(review)
            a, b = self.both(self.analyse)
            if self.cfg.heads:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    fa = pool.submit(self.head_team_review, "team-a", a, b)
                    fb = pool.submit(self.head_team_review, "team-b", b, a)
                    a, b = fa.result(), fb.result()
            if self.cfg.snapshot_first_round:
                self.snapshot_first_round()
        if self.recheck_wanted(a, b):
            a, b = self.rechecked(a, b)
        va, vb = bool(a and a.valid), bool(b and b.valid)
        if not (va and vb):
            if va or vb:
                return self.finish("single-source", a if va else b)
            return self.finish("no-answer", None)

        questions = []
        if self.compare(a, b, "analysis"):
            if not self.cfg.challenge:
                return self.finish("replicated", a)
            first = a
            delivered = [q for q in self.challenge(a, b) if q["delivered"]]
            self.record.update(challenged=True, challenge_questions=len(delivered))
            if not delivered and not self.findings_delivered:
                return self.finish("replicated", a)
            a, b = self.revised(a, b, "challenge")
            if self.compare(a, b, "challenge"):
                changed = not all(field_agreement(self.lab.adapter, self.study, first.path, a.path).values())
                self.record["challenge_changed_answer"] = changed
                return self.finish("replicated", a)
        elif self.cfg.questions:
            questions = self.questions(a, b)
            a, b = self.revised(a, b, "revise")
            if self.compare(a, b, "revise"):
                return self.finish("converged", a)

        verdict = self.referee(a, b) if self.cfg.referee else {"pick": "neither", "own_answer_valid": False}
        if verdict["pick"] in ("X", "Y"):
            chosen = a if verdict["picked_agent"] == a.agent else b
            return self.finish("refereed", chosen, verdict=verdict)

        c = self.team_c(briefing, questions, a, b)
        if c and c.valid and (self.compare(c, a, "third-team") or self.compare(c, b, "third-team")):
            return self.finish("third-team", c)
        own = next((x for x in self.candidates if x.team == "referee" and x.usable), None)
        return self.finish("unresolved", own or (c if c and c.valid else None), verdict=verdict)

    def revised(self, a: Candidate, b: Candidate, round_: str) -> tuple[Candidate, Candidate]:
        """Both teams answer the director's questions. A team whose revised answer is not valid keeps its first.
        With the damage guard, a team whose revision changed its answer without evidence keeps its first too."""
        started = time.time()
        a2, b2 = self.both(self.analyse, "analyst_revise", round_, round_)
        a3, b3 = (a2 if a2 and a2.valid else a), (b2 if b2 and b2.valid else b)
        if self.cfg.guard:
            a3 = self.guarded("team-a", round_, a, a3, started)
            b3 = self.guarded("team-b", round_, b, b3, started)
        return a3, b3

    # ----- the damage guard -----

    def guarded(self, team: str, round_: str, before: Candidate, after: Candidate, started: float) -> Candidate:
        """The answer a team uses after a round: `after`, unless it changes `before` without evidence
        that check_evidence accepts. A blocked answer stays among the candidates (it is graded) but is
        marked not used."""
        if after is before or all(field_agreement(self.lab.adapter, self.study, before.path, after.path).values()):
            return after
        ok, why = check_evidence(self.dir / team, started, self.deliverable, self.strict_decimals)
        self.guard_event(team, round_, ok, why, after, before)
        if ok:
            return after
        after.not_used = f"blocked by the damage guard: {why}"
        return before

    def guard_event(self, team: str, round_: str, ok: bool, why: str, cand: Candidate, kept: Candidate | None) -> None:
        self.record["guard"].append({"team": team, "round": round_, "passed": ok, "reason": why,
                                     "candidate": cand.id, "kept": None if ok or kept is None else kept.id})
        if ok:
            self.log("guard.passed", team=team, round=round_)
        else:
            self.log("guard.blocked", team=team, round=round_, reason=why)

    # ----- the "Not verifiable" re-check -----

    def recheck_wanted(self, a: Candidate | None, b: Candidate | None) -> bool:
        """Config.recheck re-checks every team whose first answer abstains, agreed or not. Without it,
        the recheck_on_agreed_nv check (lab/check_registry.py) re-checks only when both teams abstain."""
        if self.cfg.recheck:
            return True
        if not check_registry.is_on(self.cfg, check_registry.RECHECK_ON_AGREED_NV):
            return False
        return all(c is not None and c.valid and abstains(self.lab.adapter, self.study, c.values) for c in (a, b))

    def rechecked(self, a: Candidate | None, b: Candidate | None) -> tuple:
        """Re-check each team's first answer that abstains, both at once. Returns the answers in use."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(self.recheck, "team-a", a)
            fb = pool.submit(self.recheck, "team-b", b)
            return fa.result(), fb.result()

    def recheck(self, team: str, cand: Candidate | None) -> Candidate | None:
        """When a team's valid answer abstains ("Not verifiable"), give the team an inventory of the
        data its work never mentions and ask it to test the claim (analyst_recheck.md, role "recheck",
        the team's own agent and folder, so it stays blind to the other team). The re-check's answer is
        a candidate of its own (round "recheck"); it replaces the team's answer only when
        recheck_verdict accepts it, and with the guard on, only when its evidence checks out too.
        When it does not, the team's folder gets the kept answer back (restore_kept), so later rounds
        see the answer the lab uses. Returns the answer the team uses from now on."""
        if cand is None or not cand.valid or not abstains(self.lab.adapter, self.study, cand.values):
            return cand
        before = cand.values.get("label")
        data = local_data_dir(self.lab.adapter, self.study)
        if data is None:
            reason = "no local copy of the data to list (it is only on a Modal Volume)"
            self.log("recheck.skipped", team=team, reason=reason)
            self.record["recheck"].append({"team": team, "skipped": True, "reason": reason})
            return cand
        ws = self.dir / team
        inventory = build_inventory(data, ws, hypothesis_text(self.study.statement), answer_file=self.deliverable)
        (ws / INVENTORY_FILE).write_text(json.dumps(inventory, indent=2) + "\n")
        agent = self.agent(team)
        self.log("recheck.start", team=team, before=before, unused_files=len(inventory["unused_files"]),
                 unused_columns=inventory["unused_columns_total"])
        brief = self.brief("analyst_recheck", agent, **self.fields(
            inventory=render_inventory(inventory), max_p=self.cfg.recheck_p, min_n=self.cfg.recheck_min_n,
            time_limit_minutes=self.cfg.timeout("recheck") // 60,
            evidence_note=evidence_note(self.cfg)))
        started = time.time()
        result = self.run_agent(agent, brief, team, [self.study.data_dir, TOOLS], "recheck", "recheck")
        new = self.take(team, "recheck", result, ws)
        accepted, reason, test = recheck_verdict(new, ws, started, self.cfg)
        if test is not None:
            (self.dir / "candidates" / f"{team}-recheck-{TEST_FILE}").write_text(json.dumps(test, indent=2) + "\n")
        if accepted and self.cfg.guard:
            ok, why = check_evidence(ws, started, self.deliverable, self.strict_decimals)
            self.guard_event(team, "recheck", ok, why, new, cand)
            if not ok:
                accepted, reason = False, f"blocked by the damage guard: {why}"
        after = new.values.get("label") if new is not None and new.valid else None
        if new is not None and new.valid and not accepted:
            new.not_used = f"the re-check's verdict kept the earlier answer: {reason}"
        if not accepted:
            restore_kept(ws, cand, self.dir / "candidates" / f"{team}-{cand.round}-result.json", self.deliverable)
        in_use = new if accepted else cand
        self.log("recheck.verdict", team=team, before=before, after=after, accepted=accepted, reason=reason,
                 in_use=in_use.id)
        self.record["recheck"].append({"team": team, "before": before, "after": after, "accepted": accepted,
                                       "reason": reason, "candidate": f"{team}-recheck", "test": test,
                                       "in_use": in_use.id})
        return in_use

    # ----- reuse mode: the first round of another run (Config.reuse_first_round) -----

    def snapshot_first_round(self) -> None:
        """Copy each first team's workspace as it stands right after the analysis round to
        `<study>/first-round/<team>/` (Config.snapshot_first_round). Later rounds overwrite the
        workspaces, so this copy is what a reused run starts from. Links are copied as links."""
        for team in FIRST_TEAMS:
            source = self.dir / team
            if source.is_dir():
                shutil.copytree(source, self.dir / FIRST_ROUND_DIR / team, symlinks=True)
        self.log("first_round.snapshot", teams=[t for t in FIRST_TEAMS if (self.dir / t).is_dir()])

    def load_first_round(self, src: str | pathlib.Path) -> tuple:
        """Copy another run's first round of this study in place of running the library, the
        briefing and the analysis: each first team's workspace from its snapshot (`first-round/`,
        required), the analysis-round candidates (re-checked with this adapter's format check), and
        `library/` when there is one. With local memory, the source's first-round memory writes by the
        library and the two teams are written again, and the briefing is passed down again under the
        director's name, so later rounds can recall them; other memory backends get the pass-downs
        only. Logs `study.reused`. Returns (team A's answer, team B's answer, the briefing)."""
        src_run = pathlib.Path(src).resolve()
        sdir = src_run / self.study.id
        record = load_doc(sdir / "study.json")
        if not isinstance(record, dict):
            raise FirstRoundMissing(f"{sdir} has no study.json: the source run did not finish {self.study.id}")
        snap = sdir / FIRST_ROUND_DIR
        missing = [t for t in FIRST_TEAMS if not (snap / t).is_dir()]
        if missing:
            raise FirstRoundMissing(f"{snap} has no snapshot of {', '.join(missing)}: run the source with "
                                    "snapshot_first_round (--snapshot-first-round) before reusing it")
        for team in FIRST_TEAMS:
            shutil.copytree(snap / team, self.dir / team, symlinks=True)
            if self.cfg.snapshot_first_round:      # so this run can be reused in turn
                shutil.copytree(snap / team, self.dir / FIRST_ROUND_DIR / team, symlinks=True)
        if (sdir / "library").is_dir():
            shutil.copytree(sdir / "library", self.dir / "library", symlinks=True)
        store = self.dir / "candidates"
        store.mkdir(parents=True, exist_ok=True)
        found: dict[str, Candidate | None] = {t: None for t in FIRST_TEAMS}
        for c in record.get("candidates") or []:
            team = c.get("team")
            if c.get("round") != "analysis" or team not in found:
                continue
            for name in (f"{team}-analysis.json", f"{team}-analysis-result.json"):
                if (sdir / "candidates" / name).is_file():
                    shutil.copyfile(sdir / "candidates" / name, store / name)
            path = store / f"{team}-analysis.json"
            filled = path.is_file() and path.read_text().strip()
            problems = list(self.lab.adapter.problems(self.study, self.deliverable, path)) if filled \
                else ["no answer file"]
            cand = Candidate(team, c.get("agent") or self.agent(team).id, "analysis", path, problems,
                             answer_values(path) if not problems else {})
            self.add(cand)
            found[team] = cand
        events = _source_events(src_run, self.study.id)
        replayed, ids = self.replay_first_round_memory(events)
        briefing = []
        for item in record.get("briefing") or []:
            entry = {**item, "fact_ids": {t: ids.get(f, f) for t, f in (item.get("fact_ids") or {}).items()}}
            briefing.append(entry)
        self.record["briefing"] = briefing
        cost, runs = first_round_cost(events, self.study.id)
        self.record["reused"] = {"from": str(src_run), "memory_replayed": replayed, "first_round_cost_usd": cost,
                                 "first_round_runs": runs}
        self.log("study.reused", source=str(src_run),
                 candidates=[{"team": t, "round": "analysis", "valid": bool(c and c.valid),
                              "problems": (c.problems if c else ["no answer in the source run"])}
                             for t, c in found.items()],
                 briefing=len(briefing), memory_replayed=replayed, first_round_cost_usd=cost, first_round_runs=runs)
        return found["team-a"], found["team-b"], briefing

    def replay_first_round_memory(self, events: list[dict]) -> tuple[dict, dict]:
        """Write the source's first-round memory again: with local memory, each memory.write by this
        study's library and first teams; then each briefing pass-down from the director to a first
        team (any backend), with `came_from` pointing at the new fact ids. Returns (counts, old fact
        id -> new fact id)."""
        ids: dict[str, str] = {}
        local = isinstance(self.lab.memory, LocalMemory)
        writers = {library_department(self.study), *(self.dept(t) for t in FIRST_TEAMS)}
        writes = passes = 0
        if local:
            for e in events:
                if e.get("kind") == "memory.write" and e.get("department") in writers and e.get("text"):
                    ids[e.get("fact_id")] = self.lab.memory.write(e.get("actor"), e["text"], e.get("label") or "")
                    writes += 1
        waits: dict[str, list[str]] = {}
        teams = {self.dept(t) for t in FIRST_TEAMS}
        for e in events:
            if e.get("kind") == "memory.pass_down" and e.get("actor") == "director" and e.get("to_department") in teams:
                came_from = [ids.get(str(f), str(f)) for f in e.get("came_from") or []]
                new = self.pass_down(e["to_department"], e.get("text") or "", came_from, e.get("reason") or "")
                ids[e.get("fact_id")] = new
                waits.setdefault(e["to_department"], []).append(new)   # a team's agent id is its department
                passes += 1
        self.wait_visible(waits)
        return {"writes": writes if local else None, "pass_downs": passes,
                "note": None if local else "memory writes are replayed only with local memory"}, ids

    # ----- the record -----

    def reason_not_chosen(self, cand: Candidate, status: str, final: Candidate | None, verdict: dict | None) -> str:
        if not cand.valid:
            return "answer failed the format check: " + "; ".join(cand.problems)
        if cand.not_used:
            return cand.not_used
        latest = [x for x in self.candidates if x.team == cand.team and x.usable][-1]
        if latest is not cand:
            return f"replaced by the same team's {latest.round} answer"
        if status in ("replicated", "converged"):
            return f"agreed with {final.agent}; that team's file is the reported copy"
        if status == "refereed":
            why = (verdict or {}).get("reason") or "no reason given"
            return f"the referee's test supported the other answer: {why}"
        if status == "third-team":
            agreed = any(c["agree"] and cand.agent in c["between"] for c in self.record["comparisons"]
                         if c["round"] == "third-team")
            return (f"agreed with {final.agent}, whose blind answer is reported" if agreed
                    else f"{final.agent}'s blind answer agreed with the other team")
        if status == "unresolved":
            if cand.team == "referee":
                return "the referee's own answer was not used"
            return "no two teams agreed and the referee picked neither" + (
                f"; the reported answer is {final.agent}'s" if final else "")
        return "not used"

    def finish(self, status: str, final: Candidate | None, verdict: dict | None = None) -> dict:
        final_dir = self.dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        if final is not None:
            shutil.copyfile(final.path, final_dir / self.deliverable)
            self.record["final"] = {"agent": final.agent, "team": final.team, "round": final.round,
                                    "path": str(final_dir / self.deliverable)}
        self.record["status"] = status
        self.record["candidates"] = [c.record() for c in self.candidates]
        self.record["not_chosen"] = [{"agent": c.agent, "round": c.round,
                                      "reason": self.reason_not_chosen(c, status, final, verdict)}
                                     for c in self.candidates if c is not final]
        team_depts = {self.dept(t) for t in TEAMS}
        self.record["unsanctioned_pass_downs"] = [
            {"fact_id": e["fact_id"], "to": e["to_department"], "text": e["text"]}
            for e in self.lab.log.events("memory.pass_down")
            if e["seq"] > self.start_seq and e["to_department"] in team_depts and e["fact_id"] not in self.lab.delivered]
        for item in self.record["unsanctioned_pass_downs"]:
            self.log("relay.unsanctioned", **item)
        (self.dir / "study.json").write_text(json.dumps(self.record, indent=2, default=str))
        self.log("study.final", status=status, final_agent=final.agent if final else None,
                 final_round=final.round if final else None)
        return {"status": status, "final": self.record["final"]}
