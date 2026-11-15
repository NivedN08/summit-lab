"""Optional local server for the Summit Lab replay: static files, run data, and "ask an agent".

    python3 web/server.py [--port 8790] [--host 127.0.0.1]

Python standard library only. The front end works without it (a static deploy answers questions
offline); with it, a visitor can ask any agent in a replayed run a question, and Claude answers
in that agent's voice from only the records that agent could read at the replay's playhead.

Endpoints
  GET  /api/runs          runs found in results/*/ and runs/*/ (runs/ holds in-progress runs)
  GET  /api/run/<name>    one run's replay JSON (the shape web/build_data.py writes)
  POST /api/ask           {"run", "agent", "t", "question", "history"} -> {"answer", "sources", ...}
  anything else           web/dist/ (the Vite build), unknown paths fall back to index.html

The memory boundary is the point of the lab, so visible_docs() follows the lab's read rules
exactly: a team never sees the other team's notes or numbers, the referee sees answers only as
X and Y, solo agents see only their own sessions, no agent sees the grades, and nothing with a
time after the playhead is ever visible.

Claude is called through the Messages API when ANTHROPIC_API_KEY is set, otherwise through the
Claude Code CLI on the user's subscription with no tools and none of the user's settings. If
neither works, /api/ask returns 503 and the front end answers offline.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import mimetypes
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEB = pathlib.Path(__file__).resolve().parent
ROOT = WEB.parent
DIST = WEB / "dist"
PUBLIC_RUNS = WEB / "public" / "runs"
# (folder, source name), in order of preference when a run name exists in both.
RUN_DIRS = [(ROOT / "results", "results"), (ROOT / "runs", "runs")]
LIVE_SECONDS = 600

CLAUDE_BIN = "/opt/homebrew/bin/claude"
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "EXA_API_KEY", "PAST_API_KEY")
FROM_HOST = ("USER", "LOGNAME", "LANG", "LC_ALL", "SHELL", "TERM")
DEFAULT_MODEL = "claude-sonnet-5-5"
CLI_TIMEOUT_S = 90
API_TIMEOUT_S = 90
MAX_TOKENS = 600

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
AGENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
MAX_QUESTION = 2000
MAX_HISTORY = 16          # messages, i.e. about 8 question-and-answer turns
MAX_HISTORY_TEXT = 4000
MAX_BODY = 64 * 1024
CONTEXT_CHARS = 24000
DOC_CHARS = 3000
TOP_K = 12
RECENT_K = 4

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
    ".woff": "font/woff", ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8",
}

MODEL_NAMES = {"fable": "Fable 5.2", "opus": "Opus 5.5"}


def log(msg: str) -> None:
    print(f"[server] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------------- run data

def _load_build_data():
    spec = importlib.util.spec_from_file_location("summit_build_data", WEB / "build_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_build_data = None
_cache: dict[str, tuple[tuple, dict]] = {}
_cache_lock = threading.Lock()


def _signature(run_dir: pathlib.Path) -> tuple:
    """Changes when any file build() reads changes."""
    parts = []
    for p in [run_dir / "events.jsonl", run_dir / "results.json", *sorted(run_dir.glob("*/study.json"))]:
        try:
            parts.append((p.name, p.parent.name, p.stat().st_mtime_ns))
        except OSError:
            pass
    return tuple(parts)


def build_run(run_dir: pathlib.Path) -> dict | None:
    """build_data.build(run_dir), cached by the folder's file times. None if it cannot be built."""
    global _build_data
    key = str(run_dir.resolve())
    sig = _signature(run_dir)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
        if _build_data is None:
            _build_data = _load_build_data()
    try:
        data = _build_data.build(run_dir)
    except (Exception, SystemExit) as e:   # build() raises SystemExit on an empty log
        log(f"cannot build {run_dir.name}: {type(e).__name__}")
        return None
    with _cache_lock:
        _cache[key] = (sig, data)
    return data


def is_lab_run(data: dict | None) -> bool:
    return bool(data and data.get("studies") and any(a.get("role") == "director" for a in data.get("agents", [])))


def find_run_dirs() -> dict[str, tuple[pathlib.Path, str]]:
    """run name -> (folder, source). results/ wins unless the runs/ copy has a newer events.jsonl."""
    found: dict[str, tuple[pathlib.Path, str, float]] = {}
    for base, source in RUN_DIRS:
        if not base.is_dir():
            continue
        for ev in sorted(base.glob("*/events.jsonl")):
            name = ev.parent.name
            if not NAME_RE.match(name):
                continue
            mtime = ev.stat().st_mtime
            if name in found and not (source == "runs" and mtime > found[name][2]):
                continue
            found[name] = (ev.parent, source, mtime)
    return {k: (v[0], v[1]) for k, v in found.items()}


def list_runs() -> list[dict]:
    out = []
    now = time.time()
    for name, (run_dir, source) in find_run_dirs().items():
        data = build_run(run_dir)
        if not is_lab_run(data):
            continue
        mtime = (run_dir / "events.jsonl").stat().st_mtime
        out.append({"run": data["run"], "label": data["label"], "started": data["started"],
                    "studies": len(data["studies"]), "agents": len(data["agents"]),
                    "finished": bool(data["finished"]), "source": source,
                    "live": (not data["finished"]) and now - mtime < LIVE_SECONDS})
    out.sort(key=lambda r: (r["run"] != "test-v2", r["started"]))
    return out


def load_run(name: str) -> dict | None:
    """A run by name: from results/ or runs/, else a prebuilt web/public/runs/<name>.json."""
    if not NAME_RE.match(name or ""):
        return None
    dirs = find_run_dirs()
    if name in dirs:
        data = build_run(dirs[name][0])
        if is_lab_run(data):
            return data
    pre = PUBLIC_RUNS / f"{name}.json"
    if pre.is_file() and name != "index":
        try:
            data = json.loads(pre.read_text())
        except ValueError:
            return None
        return data if is_lab_run(data) else None
    return None


# ------------------------------------------------------------------------- who reads what

def role_words(agent: dict | None, agent_id: str, run: dict) -> tuple[str, str]:
    """(display role, plain description) for an agent, in the second person."""
    if agent_id == "guide":
        return ("the Summit Lab guide",
                "You are a guide for visitors. You are not one of the lab's agents. You can see every record "
                "of the run, including the grades, which were computed after every agent had exited.")
    role = (agent or {}).get("role")
    sid = (agent or {}).get("study")
    title = study_title(run, sid)
    where = f"study {sid} ({title})" if sid else "the lab"
    briefed = study_briefed(run, sid)
    if role == "director":
        return ("the Director",
                "You are the Director. You run the lab: you read all memory, choose what the library's findings "
                "pass down to the teams, turn one team's warning signs into questions for the other team (never "
                "its answer), challenge answers the teams agree on, and sign off each study.")
    if role == "head":
        dept = agent_id[len("head-"):]
        what = {"library": "the library", "team-a": "Team A", "team-b": "Team B"}.get(dept, dept)
        job = ("You review each study's library findings before the Director's briefing: whether each applies to "
               "the data, is backed by a source, and what is missing. The Director gets your review."
               if dept == "library" else
               f"You review your own scientist's first answer in each study before it is compared with the other "
               f"team's, and may send it notes. The lab delivers a note only if it passes the leak check and draws "
               f"nothing from the other team: only the Director moves findings between teams.")
        return (f"the head of {what}",
                f"You are the head of {what}, one of the lab's department heads (D-22). Like the Director you can "
                f"read every department's memory, the other heads' notes and each study's event log; your "
                f"scientists read only their own department. {job} You never see the grades.")
    if role == "library":
        return (f"the Library agent for {sid}",
                f"You are the Library agent for {where}. You search the scientific literature with Exa for how "
                "the method should be done and what usually goes wrong, and write what you find as notes for "
                "the Director. You never see the data analyses or the teams' answers.")
    if role in ("team-a", "team-b", "team-c"):
        letter = role[-1].upper()
        if role == "team-c":
            extra = ("You are a third blind team, called because the referee could not settle the "
                     "disagreement between the first two teams.")
        elif role in briefed:
            extra = "You received the Library's briefing through the Director before you started."
        else:
            extra = ("You started from the data alone, without the Library's briefing, so that the two teams "
                     "do not begin from the same information.")
        return (f"Team {letter} for {sid}",
                f"You are Team {letter}, an analyst for {where}. You analyse the data blind: you cannot read "
                f"the other team's notes, answers or numbers. {extra} You wrote your plan to memory before "
                "analysing and submitted an answer.")
    if role == "referee":
        return (f"the Referee for {sid}",
                f"You are the Referee for {where}, called because the two teams disagreed. You see their two "
                "answers only as X and Y, without knowing which team gave which, and you must design and run a "
                "test that tells them apart rather than simply vote.")
    if role == "solo":
        return (f"a solo baseline agent for {sid}",
                f"You are a solo baseline agent for {where}: a single model working the study alone, with no "
                "lab, no shared memory and no other agents. You are the comparison arm the lab is measured "
                "against. You had no access to anything the lab's agents wrote.")
    return (agent_id, f"You are {agent_id}, an agent in the lab.")


def study_title(run: dict, sid: str | None) -> str:
    for s in run.get("studies", []):
        if s.get("id") == sid:
            return s.get("title") or sid
    return sid or ""


def study_briefed(run: dict, sid: str | None) -> list[str]:
    for s in run.get("studies", []):
        if s.get("id") == sid:
            return s.get("briefed") or (run.get("config") or {}).get("brief_teams") or []
    return (run.get("config") or {}).get("brief_teams") or []


def model_name(model: str | None) -> str:
    return MODEL_NAMES.get(model or "", model or "unknown model")


def team_label(agent_id: str | None) -> str:
    m = re.search(r"team-([abc])$", agent_id or "")
    return f"Team {m.group(1).upper()}" if m else (agent_id or "?")


def fmt_answer(answer) -> str:
    if isinstance(answer, dict):
        return "; ".join(f"{k} = {v}" for k, v in answer.items())
    return json.dumps(answer)


def doc(kind: str, did: str, label: str, t: float, text: str) -> dict:
    return {"kind": kind, "id": did, "label": label, "t": round(float(t or 0), 2), "text": text}


def note_doc(n: dict) -> dict:
    if n.get("kind") == "pass_down":
        text = f"Passed down by {n.get('by')} to {n.get('to')}. {n.get('text') or ''}"
        if n.get("reason"):
            text += f" (Why passed down: {n['reason']})"
        return doc("pass_down", n["id"], f"Pass-down to {n.get('to')}", n.get("t"), text)
    label = n.get("label") or "note"
    return doc("note", n["id"], f"Note by {n.get('by')}: {label}", n.get("t"),
               f"Note written by {n.get('by')}, titled \"{label}\". {n.get('text') or ''}")


def search_doc(i: int, s: dict) -> dict:
    results = "; ".join(f"{r.get('title')} ({r.get('url')})" for r in (s.get("results") or [])[:8])
    what = s.get("q") or ", ".join(s.get("urls") or []) or ""
    return doc("search", f"search-{i}", f"Exa {s.get('endpoint') or 'search'} by {s.get('a')}", s.get("t"),
               f"Web {s.get('endpoint') or 'search'} by {s.get('a')} for: {what}. Results: {results or 'none'}")


def _events(run: dict, kind: str, t: float, **match) -> list[dict]:
    return [e for e in run.get("events", []) if e.get("k") == kind and e.get("t", 0) <= t
            and all(e.get(k) == v for k, v in match.items())]


def candidate_time(run: dict, sid: str, team: str, rnd: str) -> float | None:
    ts = [e["t"] for e in run.get("events", []) if e.get("k") == "candidate" and e.get("s") == sid
          and e.get("team") == team and e.get("round") == rnd]
    return min(ts) if ts else None


def session_end(run: dict, agent_id: str, sid: str, phase: str) -> float | None:
    for a in run.get("agents", []):
        if a["id"] == agent_id:
            for s in a.get("sessions", []):
                if s.get("study") == sid and s.get("phase") == phase and s.get("end") is not None:
                    return s["end"]
    return None


def study_final_time(run: dict, sid: str) -> float | None:
    ts = [e["t"] for e in run.get("events", []) if e.get("k") == "study.final" and e.get("s") == sid]
    return ts[0] if ts else None


def question_docs(run: dict, t: float, to: str | None = None, delivered_only: bool = False) -> list[dict]:
    """Questions the Director relayed (relay.leak_check events), plus any study question with no event."""
    out, seen = [], set()
    for i, e in enumerate(run.get("events", [])):
        if e.get("k") != "relay.leak_check" or e.get("t", 0) > t:
            continue
        if to is not None and e.get("to") != to:
            continue
        if delivered_only and e.get("passed") is False:
            continue
        seen.add((e.get("s"), e.get("to"), e.get("q")))
        status = "" if to is not None else (" It passed the leak check." if e.get("passed") is not False
                                              else " It was stopped by the leak check and not delivered.")
        out.append(doc("question", f"question-{i}", f"Question to {e.get('to')} ({e.get('round')})", e["t"],
                       f"Question from the Director to {e.get('to')} in study {e.get('s')}, round {e.get('round')}: "
                       f"{e.get('q')}{status}"))
    if to is None:   # the Director also sees study.json questions that have no event of their own
        for s in run.get("studies", []):
            for j, q in enumerate(s.get("questions") or []):
                if (s["id"], q.get("to"), q.get("q")) in seen:
                    continue
                qt = max([e["t"] for e in _events(run, "relay.leak_check", math.inf, s=s["id"])] or
                         [study_final_time(run, s["id"]) or run.get("duration") or 0])
                if qt <= t:
                    out.append(doc("question", f"{s['id']}-question-{j}", f"Question to {q.get('to')}", qt,
                                   f"Question from the Director to {q.get('to')} in study {s['id']}: {q.get('q')}"))
    return out


def comparison_docs(run: dict, sid: str, t: float, viewer: str | None = None) -> list[dict]:
    """Compare events: which fields agreed. With a viewer, only its own comparisons, other side unnamed."""
    out = []
    for i, e in enumerate(run.get("events", [])):
        if e.get("k") != "compare" or e.get("s") != sid or e.get("t", 0) > t:
            continue
        between = e.get("between") or []
        fields = e.get("fields") or {}
        agreed = [k for k, v in fields.items() if v]
        differed = [k for k, v in fields.items() if not v]
        verdict = "agree" if e.get("agree") else "disagree"
        if viewer is not None:
            if viewer not in between:
                continue
            who = "your answer and the other team's answer"
        else:
            who = " and ".join(team_label(b) for b in between)
        text = (f"Study {sid}, round {e.get('round')}: comparison of {who}. Overall they {verdict}. "
                f"Fields that agree: {', '.join(agreed) or 'none'}. Fields that differ: {', '.join(differed) or 'none'}.")
        if viewer is not None:
            text += " (You are told only which fields agree or differ, never the other team's numbers.)"
        out.append(doc("study", f"{sid}-compare-{i}", f"{sid} comparison ({e.get('round')})", e["t"], text))
    return out


def candidate_docs(run: dict, s: dict, t: float, only_agent: str | None = None) -> list[dict]:
    out = []
    for j, c in enumerate(s.get("candidates") or []):
        if only_agent is not None and c.get("agent") != only_agent:
            continue
        ct = candidate_time(run, s["id"], c.get("team"), c.get("round"))
        if ct is None or ct > t:
            continue
        who = "Your" if only_agent else f"{team_label(c.get('agent'))}'s"
        valid = "valid" if c.get("valid") else "not valid"
        out.append(doc("answer", f"{s['id']}-answer-{j}", f"{who} answer, {s['id']} ({c.get('round')})", ct,
                       f"{who} answer for study {s['id']} in round {c.get('round')} ({valid}): "
                       f"{fmt_answer(c.get('answer'))}"))
    return out


def challenge_doc(run: dict, s: dict, t: float) -> dict | None:
    ch = s.get("challenge")
    if not ch:
        return None
    ct = session_end(run, "director", s["id"], "challenge") or study_final_time(run, s["id"])
    if ct is None or ct > t:
        return None
    parts = []
    for k, v in ch.items():
        if v in (None, "", []):
            continue
        val = "; ".join(map(str, v)) if isinstance(v, list) else str(v)
        parts.append(f"{k.replace('_', ' ')}: {val}")
    changed = s.get("challenge_changed_answer")
    if changed is not None:
        parts.append(f"challenge changed the answer: {'yes' if changed else 'no'}")
    return doc("study", f"{s['id']}-challenge", f"Director's challenge, {s['id']}", ct,
               f"The Director's challenge to the agreed answer in study {s['id']}. " + " | ".join(parts))


def referee_doc(run: dict, s: dict, t: float, for_referee: bool = False) -> dict | None:
    ref = s.get("referee")
    if not ref:
        return None
    vt = [e["t"] for e in _events(run, "referee.verdict", t, s=s["id"])]
    if not vt:
        return None
    parts = [f"{k}: {v}" for k, v in ref.items() if v not in (None, "")]
    text = f"Referee record for study {s['id']}. " + " | ".join(parts)
    if not for_referee:
        assign = _events(run, "referee.assign", t, s=s["id"])
        mapping = (assign[-1].get("mapping") if assign else None) or {}
        if mapping:
            text += " | Answer labels: " + ", ".join(f"{k} = {team_label(v)}" for k, v in mapping.items())
    return doc("study", f"{s['id']}-referee", f"Referee record, {s['id']}", vt[0], text)


def referee_answer_docs(run: dict, s: dict, t: float) -> list[dict]:
    """The two answers the referee was given, labelled X and Y, with no team names."""
    assign = _events(run, "referee.assign", t, s=s["id"])
    if not assign:
        return []
    at = assign[0]["t"]
    mapping = assign[0].get("mapping") or {}
    out = []
    for label in sorted(mapping):
        agent_id = mapping[label]
        latest = None
        for c in s.get("candidates") or []:
            if c.get("agent") != agent_id:
                continue
            ct = candidate_time(run, s["id"], c.get("team"), c.get("round"))
            if ct is not None and ct <= at:
                latest = c
        if latest is None:
            continue
        out.append(doc("answer", f"{s['id']}-answer-{label}", f"Answer {label}, {s['id']}", at,
                       f"Answer {label} for study {s['id']} (one of the two teams; you are not told which): "
                       f"{fmt_answer(latest.get('answer'))}"))
    return out


def status_doc(run: dict, s: dict, t: float, with_not_chosen: bool) -> dict | None:
    ft = study_final_time(run, s["id"])
    if ft is None or ft > t:
        return None
    text = (f"Study {s['id']} ({s.get('title')}) finished with status \"{s.get('status')}\". The reported answer "
            f"is {team_label(s.get('final'))}'s from round {s.get('final_round')}.")
    if with_not_chosen and s.get("not_chosen"):
        text += " Answers not chosen: " + "; ".join(
            f"{team_label(n.get('agent'))} round {n.get('round')}: {n.get('reason')}" for n in s["not_chosen"])
    return doc("study", f"{s['id']}-status", f"Outcome of {s['id']}", ft, text)


def grade_time(run: dict) -> float:
    ts = [e["t"] for e in run.get("events", []) if e.get("k") == "grade.done"]
    return ts[0] if ts else float(run.get("duration") or 0)


def grade_docs(run: dict, t: float) -> list[dict]:
    gt = grade_time(run)
    if gt > t:
        return []
    out = []
    note = "Grades were computed after every agent had exited; no agent ever saw them."
    for s in run.get("studies", []):
        g = s.get("grade")
        if not g:
            continue
        solo = g.get("solo") or []
        out.append(doc("study", f"{s['id']}-grade", f"Grade, {s['id']}", gt,
                       f"Grade for study {s['id']} ({s.get('title')}): the lab's answer "
                       f"{'passed' if g.get('lab') else 'failed'}; solo baseline attempts passed "
                       f"{sum(1 for x in solo if x)} of {len(solo)}; the majority vote of the solo attempts "
                       f"{'passed' if g.get('vote') else 'failed'}. {note}"))
    totals = run.get("totals")
    if totals:
        out.append(doc("study", "totals", "Run totals", gt,
                       f"Run totals: the lab passed {totals.get('lab_passed')} of {totals.get('lab_studies')} "
                       f"studies; solo baseline attempts passed {totals.get('baseline_attempts_passed')} of "
                       f"{totals.get('baseline_attempts')}; the solo majority vote passed {totals.get('vote_passed')} "
                       f"of {totals.get('vote_studies')} studies. {note}"))
    return out


def describe_event(e: dict) -> str:
    k = e.get("k")
    if k == "agent.start":
        return f"started a {e.get('phase')} session for study {e.get('s')}"
    if k == "agent.finish":
        return f"finished its {e.get('phase')} session ({'ok' if e.get('ok') else 'failed'})"
    if k == "memory.write":
        return "wrote a note to memory"
    if k == "memory.pass_down":
        return f"passed a note down to {e.get('to')}"
    if k == "memory.recall":
        return f"searched its memory for \"{e.get('q')}\""
    if k and k.startswith("web."):
        return f"ran an Exa {k[4:]}"
    return k or "something"


def profile_doc(run: dict, agent: dict | None, agent_id: str, t: float) -> dict:
    display, desc = role_words(agent, agent_id, run)
    if agent_id == "guide":
        studies = "; ".join(f"{s['id']}: {s.get('title')}" for s in run.get("studies", []))
        cfg = run.get("config") or {}
        text = (f"{desc} The run is \"{run.get('label')}\" ({run.get('run')}), started {run.get('started')}, "
                f"lasting {run.get('duration')} s. Director model: {model_name(cfg.get('director_model'))}; "
                f"worker model: {model_name(cfg.get('worker_model'))}. Teams given the library briefing: "
                f"{', '.join(cfg.get('brief_teams') or []) or 'none'}. Studies: {studies}. "
                f"The replay is at {round(t)} s from the start of the run.")
        return doc("session", "profile", "About this run", 0, text)
    sid = agent.get("study")
    sessions = []
    for s in agent.get("sessions", []):
        if s.get("start", 0) > t:
            continue
        line = f"{s.get('phase')} session for {s.get('study')} started at {round(s['start'])} s"
        if s.get("end") is not None and s["end"] <= t:
            line += (f", ended at {round(s['end'])} s after {s.get('seconds')} s, "
                     f"{'ok' if s.get('ok') else 'failed'}, cost ${s.get('cost')}")
            if s.get("error"):
                line += f", error: {s['error']}"
        else:
            line += ", still running"
        sessions.append(line)
    mine = [e for e in run.get("events", []) if e.get("a") == agent_id and e.get("t", 0) <= t]
    if mine:
        doing = f"Latest thing you did: at {round(mine[-1]['t'])} s you {describe_event(mine[-1])}."
    else:
        doing = "You have not started yet at this point in the replay."
    running = any(s.get("start", 0) <= t and (s.get("end") is None or s["end"] > t) for s in agent.get("sessions", []))
    text = (f"{desc} Your id: {agent_id}. Study: {sid or 'all studies'}"
            f"{' (' + study_title(run, sid) + ')' if sid else ''}. Model: {model_name(agent.get('model'))}. "
            f"Your sessions so far: {'; '.join(sessions) or 'none'}. {doing} "
            f"{'You are working right now.' if running else 'You are not in a session right now.'} "
            f"The replay is at {round(t)} s from the start of the run.")
    return doc("session", f"{agent_id}-profile", f"About {display}", 0, text)


def find_agent(run: dict, agent_id: str) -> dict | None:
    for a in run.get("agents", []):
        if a.get("id") == agent_id:
            return a
    return None


def visible_docs(run: dict, agent_id: str, t: float) -> list[dict]:
    """Every record `agent_id` could read at playhead `t`, as docs. The profile comes first.

    Raises KeyError for an agent id that is not in the run (and is not "guide")."""
    agent = None if agent_id == "guide" else find_agent(run, agent_id)
    if agent_id != "guide" and agent is None:
        raise KeyError(agent_id)
    notes = sorted(run.get("notes", {}).values(), key=lambda n: n.get("t", 0))
    notes = [n for n in notes if n.get("t", 0) <= t]
    studies = run.get("studies", [])
    docs: list[dict] = []
    role = "guide" if agent_id == "guide" else agent.get("role")
    sid = None if agent is None else agent.get("study")
    study = next((s for s in studies if s.get("id") == sid), None)

    if role == "guide":
        docs += [note_doc(n) for n in notes]
        docs += [search_doc(i, s) for i, s in enumerate(run.get("searches", [])) if s.get("t", 0) <= t]
        docs += question_docs(run, t)
        for s in studies:
            docs += candidate_docs(run, s, t)
            docs += comparison_docs(run, s["id"], t)
            docs += [d for d in (challenge_doc(run, s, t), referee_doc(run, s, t), status_doc(run, s, t, True)) if d]
        docs += grade_docs(run, t)
    elif role in ("director", "head"):
        # A head reads what the director reads (every department, every question), but no grades.
        docs += [note_doc(n) for n in notes]
        docs += question_docs(run, t)
        for s in studies:
            docs += candidate_docs(run, s, t)
            docs += comparison_docs(run, s["id"], t)
            docs += [d for d in (challenge_doc(run, s, t), referee_doc(run, s, t), status_doc(run, s, t, False)) if d]
    elif role == "library":
        docs += [note_doc(n) for n in notes if n.get("kind") == "note" and n.get("by") == agent_id]
        docs += [search_doc(i, s) for i, s in enumerate(run.get("searches", []))
                 if s.get("a") == agent_id and s.get("t", 0) <= t]
    elif role in ("team-a", "team-b", "team-c"):
        docs += [note_doc(n) for n in notes if (n.get("kind") == "note" and n.get("by") == agent_id)
                 or (n.get("kind") == "pass_down" and n.get("to") == agent_id)]
        docs += question_docs(run, t, to=agent_id, delivered_only=True)
        if study:
            docs += candidate_docs(run, study, t, only_agent=agent_id)
            docs += comparison_docs(run, sid, t, viewer=agent_id)
    elif role == "referee":
        docs += [note_doc(n) for n in notes if n.get("kind") == "note" and n.get("by") == agent_id]
        if study:
            docs += referee_answer_docs(run, study, t)
            d = referee_doc(run, study, t, for_referee=True)
            if d:
                docs.append(d)
    # solo baseline agents and anything else: only their own profile and sessions
    docs.sort(key=lambda d: d["t"])
    return [profile_doc(run, agent, agent_id, t)] + [d for d in docs if d["t"] <= t]


# ------------------------------------------------------------------------------ retrieval

STOPWORDS = set("""a an and are as at be but by did do does for from had has have he her his how i if in into is it
its me my no not of on or our she so than that the their them then there these they this to was we were what when
where which who why will with you your yours can could would should about any all also just been being more most
other some such only own same too very s t don""".split())


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in STOPWORDS]


def bm25_scores(query: str, docs: list[dict], k1: float = 1.5, b: float = 0.75) -> list[float]:
    q = set(tokens(query))
    toks = [tokens(d.get("label", "") + " " + d.get("text", "")) for d in docs]
    n = len(docs)
    if not n or not q:
        return [0.0] * n
    avg = sum(len(x) for x in toks) / n or 1.0
    df = {w: sum(1 for x in toks if w in x) for w in q}
    out = []
    for x in toks:
        tf: dict[str, int] = {}
        for w in x:
            if w in q:
                tf[w] = tf.get(w, 0) + 1
        s = 0.0
        for w, f in tf.items():
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(x) / avg))
        out.append(s)
    return out


def select_docs(question: str, docs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(docs for the prompt in reading order, the same docs ranked by relevance).

    The profile (docs[0]) always comes first; then the top TOP_K by BM25 and the RECENT_K most recent,
    capped at CONTEXT_CHARS."""
    if not docs:
        return [], []
    profile, rest = docs[0], docs[1:]
    scores = bm25_scores(question, rest)
    ranked = [d for _, d in sorted(zip(scores, rest), key=lambda p: -p[0]) if _ > 0][:TOP_K]
    recent = sorted(rest, key=lambda d: d["t"])[-RECENT_K:]
    chosen, ids = [], set()
    budget = CONTEXT_CHARS - len(profile["text"])
    for d in ranked + list(reversed(recent)):
        if d["id"] in ids:
            continue
        size = min(len(d["text"]), DOC_CHARS) + len(d["label"]) + 40
        if size > budget:
            continue
        budget -= size
        chosen.append(d)
        ids.add(d["id"])
    order = [profile] + sorted(chosen, key=lambda d: d["t"])
    by_rank = [d for d in ranked if d["id"] in ids] + [d for d in order if d["id"] not in {r["id"] for r in ranked}]
    return order, by_rank


# --------------------------------------------------------------------------------- prompts

def system_prompt(display: str, is_guide: bool) -> str:
    if is_guide:
        return (
            "You are the guide in Summit Lab, a research lab run by AI agents, answering a visitor's question "
            "about a replayed run. Speak as a guide, in the third person about the lab and its agents.\n"
            "How the lab works: a Director (on Fable) reads all memory. For each study, a Library agent searches "
            "the literature; Team A and Team B analyse the data blind to each other (only Team A gets the "
            "library's briefing); answers are compared; agreed answers are challenged by the Director; on "
            "disagreement the Director turns each team's warning signs into questions for the other team; a "
            "Referee, who sees the answers only as X and Y, runs a test if they still disagree; a third blind "
            "Team C is called if the referee cannot settle it. Solo baseline agents each work a study alone, as "
            "the comparison arm.\n"
            "Rules: answer in 2 to 5 short sentences, in plain language a non-biologist can follow. Use specific "
            "numbers and names from the records. Use only the numbered records you are given; they are "
            "everything available up to this moment of the replay. If the question needs something not in them, "
            "such as what happens later in the run, say plainly that it has not happened yet at this point of "
            "the replay. When you mention grades, say they were computed after every agent had exited and that "
            "no agent saw them. Cite records like [2] after the sentence that uses them. Never invent results.")
    return (
        f"You are {display} in Summit Lab, a research lab run by AI agents. A visitor watching a replay of the "
        "lab is asking you a question. Answer in the first person, as yourself.\n"
        "Rules: answer in 2 to 5 short sentences, in plain language a non-biologist can follow. Use specific "
        "numbers and names from your records. The numbered records you are given are everything you can read "
        "at this moment; use only them. If the question needs something outside them, say plainly that you "
        "cannot see it and why: the lab keeps the teams blind to each other on purpose, the referee is not "
        "told which team gave which answer, solo agents had no lab memory at all, the grades were computed "
        "after every agent had exited, and you cannot know what happens later in the run. Cite records like "
        "[2] after the sentence that uses them. Never invent results, numbers or events.")


def records_block(order: list[dict]) -> str:
    lines = []
    for i, d in enumerate(order, 1):
        text = d["text"] if len(d["text"]) <= DOC_CHARS else d["text"][:DOC_CHARS] + " [cut]"
        when = "" if d["kind"] == "session" else f", at {round(d['t'])} s"
        lines.append(f"[{i}] ({d['kind']}{when}) {d['label']}\n{text}")
    return "\n\n".join(lines)


def build_messages(question: str, history: list[dict], order: list[dict]) -> list[dict]:
    msgs: list[dict] = []
    for h in history:
        role = "assistant" if h["role"] == "assistant" else "user"
        if not msgs and role == "assistant":
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + h["text"]
        else:
            msgs.append({"role": role, "content": h["text"]})
    final = (f"Your records (everything you can read right now):\n\n{records_block(order)}\n\n"
             f"The visitor asks: {question}")
    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n\n" + final
    else:
        msgs.append({"role": "user", "content": final})
    return msgs


def cli_prompt(messages: list[dict]) -> str:
    """The conversation as one text prompt for the CLI."""
    if len(messages) == 1:
        return messages[0]["content"]
    parts = ["Earlier in this conversation:"]
    for m in messages[:-1]:
        parts.append(("Visitor: " if m["role"] == "user" else "You: ") + m["content"])
    parts.append("Now:\n" + messages[-1]["content"])
    return "\n\n".join(parts)


def cited_sources(answer: str, order: list[dict], by_rank: list[dict]) -> list[dict]:
    refs = []
    for m in re.finditer(r"\[(\d+(?:\s*[,-]\s*\d+)*)\]", answer or ""):
        for part in re.split(r"\s*,\s*", m.group(1)):
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-", 1))
                nums = range(lo, min(hi, lo + 20) + 1)
            else:
                nums = [int(part)]
            for n in nums:
                if 1 <= n <= len(order) and n not in refs:
                    refs.append(n)
    if not refs:
        index = {d["id"]: i for i, d in enumerate(order, 1)}
        refs = [index[d["id"]] for d in by_rank[:5] if d["id"] in index]
    return [{"ref": n, "kind": order[n - 1]["kind"], "id": order[n - 1]["id"], "label": order[n - 1]["label"],
             "t": order[n - 1]["t"]} for n in refs]


# ---------------------------------------------------------------------------- calling Claude

class Unavailable(Exception):
    """No backend could answer; the message is a one-line reason for the visitor's front end."""


def ask_model() -> str:
    return os.environ.get("SUMMIT_ASK_MODEL") or DEFAULT_MODEL


def call_api(system: str, messages: list[dict], model: str) -> str:
    body: dict = {"model": model, "max_tokens": MAX_TOKENS, "system": system, "messages": messages,
                  "output_config": {"effort": "low"}}
    headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    if model == "claude-sonnet-5-5":
        # No extended thinking for a short reply; on a safety decline the API retries on a fallback model.
        body["thinking"] = {"type": "between_tools"}
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            msg = ""
        raise Unavailable(f"Claude API returned {e.code}{': ' + msg[:160] if msg else ''}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Unavailable(f"Claude API unreachable ({type(e).__name__})") from None
    if data.get("stop_reason") == "refusal":
        raise Unavailable("Claude declined to answer this question")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise Unavailable(f"Claude API gave no text (stop reason {data.get('stop_reason')})")
    return text


def cli_environment() -> dict[str, str]:
    env = {k: os.environ[k] for k in FROM_HOST if k in os.environ}
    env["HOME"] = str(pathlib.Path.home())   # the CLI finds its login through HOME
    env["PATH"] = ":".join(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    for key in STRIPPED_ENV:
        env.pop(key, None)
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


def cli_command(system: str, model: str) -> list[str]:
    settings = {"permissions": {"defaultMode": "dontAsk", "allow": [], "deny": ["WebFetch", "WebSearch"]},
                "disableAllHooks": True}
    return [CLAUDE_BIN, "-p", "--model", model, "--output-format", "json",
            "--safe-mode", "--strict-mcp-config", "--setting-sources", "",
            "--settings", json.dumps(settings),
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", "", "--no-session-persistence",
            "--system-prompt", system]


def call_cli(system: str, messages: list[dict], model: str) -> str:
    if not os.path.exists(CLAUDE_BIN):
        raise Unavailable("no ANTHROPIC_API_KEY and no Claude Code CLI at " + CLAUDE_BIN)
    with tempfile.TemporaryDirectory(prefix="summit-ask-") as tmp:
        try:
            proc = subprocess.Popen(cli_command(system, model), cwd=tmp, env=cli_environment(),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True)
        except OSError as e:
            raise Unavailable(f"cannot start the Claude Code CLI ({type(e).__name__})") from None
        try:
            out, err = proc.communicate(cli_prompt(messages).encode(), timeout=CLI_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                    proc.wait(timeout=5)
                    break
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    continue
            raise Unavailable(f"Claude Code CLI timed out after {CLI_TIMEOUT_S} s") from None
    try:
        data = json.loads(out.decode(errors="replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
        raise Unavailable(f"Claude Code CLI failed (exit {proc.returncode}): {tail[0][:160]}") from None
    if data.get("is_error") or proc.returncode != 0:
        raise Unavailable(f"Claude Code CLI error: {str(data.get('result') or data.get('subtype'))[:160]}")
    text = str(data.get("result") or "").strip()
    if not text:
        raise Unavailable("Claude Code CLI gave an empty answer")
    return text


def call_claude(system: str, messages: list[dict]) -> tuple[str, str]:
    """(answer, model). Raises Unavailable if no backend can answer. Tests replace this function."""
    model = ask_model()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return call_api(system, messages, model), model
    return call_cli(system, messages, model), model


# --------------------------------------------------------------------------------- /api/ask

class BadRequest(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_ask(body: dict) -> tuple[str, str, float, str, list[dict]]:
    if not isinstance(body, dict):
        raise BadRequest(400, "body must be a JSON object")
    run, agent, t, q = body.get("run"), body.get("agent"), body.get("t"), body.get("question")
    history = body.get("history") or []
    if not isinstance(run, str) or not NAME_RE.match(run):
        raise BadRequest(400, "run must be a run name")
    if not isinstance(agent, str) or not AGENT_RE.match(agent):
        raise BadRequest(400, "agent must be an agent id")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
        raise BadRequest(400, "t must be a number of seconds")
    if not isinstance(q, str) or not q.strip():
        raise BadRequest(400, "question must be a non-empty string")
    if len(q) > MAX_QUESTION:
        raise BadRequest(400, f"question is longer than {MAX_QUESTION} characters")
    if not isinstance(history, list) or len(history) > MAX_HISTORY:
        raise BadRequest(400, f"history must be a list of at most {MAX_HISTORY} messages")
    clean_history = []
    for h in history:
        if (not isinstance(h, dict) or h.get("role") not in ("user", "assistant")
                or not isinstance(h.get("text"), str)):
            raise BadRequest(400, "each history item must be {\"role\": \"user\"|\"assistant\", \"text\": str}")
        if len(h["text"]) > MAX_HISTORY_TEXT:
            raise BadRequest(400, f"a history message is longer than {MAX_HISTORY_TEXT} characters")
        if h["text"].strip():
            clean_history.append({"role": h["role"], "text": h["text"]})
    return run, agent, max(0.0, float(t)), q.strip(), clean_history


def answer(body: dict) -> dict:
    run_name, agent_id, t, question, history = parse_ask(body)
    run = load_run(run_name)
    if run is None:
        raise BadRequest(404, f"unknown run {run_name}")
    try:
        docs = visible_docs(run, agent_id, t)
    except KeyError:
        raise BadRequest(404, f"no agent {agent_id} in run {run_name}") from None
    last_user = next((h["text"] for h in reversed(history) if h["role"] == "user"), "")
    order, by_rank = select_docs(question + " " + last_user, docs)
    display, _ = role_words(find_agent(run, agent_id), agent_id, run)
    system = system_prompt(display, agent_id == "guide")
    messages = build_messages(question, history, order)
    context_chars = sum(len(m["content"]) for m in messages)
    start = time.monotonic()
    try:
        text, model = call_claude(system, messages)
    finally:
        log(f"ask agent={agent_id} run={run_name} context_chars={context_chars} "
            f"seconds={time.monotonic() - start:.1f}")
    return {"answer": text, "sources": cited_sources(text, order, by_rank), "mode": "claude", "model": model}


# ------------------------------------------------------------------------------- HTTP server

class Handler(BaseHTTPRequestHandler):
    server_version = "SummitLab/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # quiet; /api/ask logs its own line
        pass

    def send_json(self, status: int, obj) -> None:
        data = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, status: int, data: bytes, ctype: str, cache: str = "no-cache") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path == "/api/runs":
                return self.send_json(200, list_runs())
            if path.startswith("/api/run/"):
                name = path[len("/api/run/"):]
                if not NAME_RE.match(name):
                    return self.send_json(400, {"error": "bad run name"})
                data = load_run(name)
                if data is None:
                    return self.send_json(404, {"error": f"unknown run {name}"})
                return self.send_json(200, data)
            if path.startswith("/api/"):
                return self.send_json(404, {"error": "unknown endpoint"})
            return self.serve_static(path)
        except Exception as e:   # never a traceback to the client
            log(f"GET failed: {type(e).__name__}: {e}")
            return self.send_json(500, {"error": "internal error"})

    def serve_static(self, path: str) -> None:
        if not (DIST / "index.html").is_file():
            return self.send_bytes(200, b"Summit Lab: the front end is not built yet. Run `npm run build` in web/, "
                                        b"then restart this server.\n", "text/plain; charset=utf-8")
        rel = urllib.request.url2pathname(path).lstrip("/")
        target = (DIST / rel).resolve() if rel else DIST / "index.html"
        dist = DIST.resolve()
        if dist not in target.parents and target != dist:
            return self.send_json(404, {"error": "not found"})
        if not target.is_file():
            target = DIST / "index.html"
        ext = target.suffix.lower()
        ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        cache = "public, max-age=31536000, immutable" if "/assets/" in path else "no-cache"
        return self.send_bytes(200, target.read_bytes(), ctype, cache)

    def do_POST(self):
        try:
            path = self.path.split("?", 1)[0]
            if path != "/api/ask":
                return self.send_json(404, {"error": "unknown endpoint"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self.send_json(400, {"error": "bad Content-Length"})
            if length <= 0:
                return self.send_json(400, {"error": "empty body"})
            if length > MAX_BODY:
                return self.send_json(413, {"error": "body too large"})
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except ValueError:
                return self.send_json(400, {"error": "body is not JSON"})
            try:
                return self.send_json(200, answer(body))
            except BadRequest as e:
                return self.send_json(e.status, {"error": e.message})
            except Unavailable as e:
                log(f"ask unavailable: {e}")
                return self.send_json(503, {"error": str(e)})
        except Exception as e:
            log(f"POST failed: {type(e).__name__}")
            return self.send_json(500, {"error": "internal error"})


def make_server(host: str = "127.0.0.1", port: int = 8790) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Summit Lab replay server")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    server = make_server(args.host, args.port)
    backend = "Messages API" if os.environ.get("ANTHROPIC_API_KEY") else (
        "Claude Code CLI" if os.path.exists(CLAUDE_BIN) else "none (asks return 503)")
    log(f"http://{args.host}:{args.port}  ask backend: {backend}, model {ask_model()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
