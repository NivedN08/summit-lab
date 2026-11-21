"""Tests for web/server.py: the memory boundary, retrieval and the HTTP API. No network, no real Claude.

    python3 -m pytest web/test_server.py -q
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import threading
import urllib.error
import urllib.request

import pytest

WEB = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(WEB))
import server as S  # noqa: E402

RUN = json.loads((WEB / "public" / "runs" / "test-v2.json").read_text())
END = 1e9
STUDIES = [s["id"] for s in RUN["studies"]]
AGENT_IDS = [a["id"] for a in RUN["agents"]]


def ids_of(docs, kind=None):
    return {d["id"] for d in docs if kind is None or d["kind"] == kind}


def note_ids(by=None, to=None, kind=None):
    return {n["id"] for n in RUN["notes"].values()
            if (by is None or n.get("by") == by) and (to is None or n.get("to") == to)
            and (kind is None or n.get("kind") == kind)}


# ------------------------------------------------------------------------ memory boundary

@pytest.mark.parametrize("sid", STUDIES)
def test_team_a_never_sees_team_b(sid):
    a, b = f"{sid}-team-a", f"{sid}-team-b"
    for t in (300, 600, 1000, END):
        docs = S.visible_docs(RUN, a, t)
        seen = ids_of(docs)
        assert not seen & note_ids(by=b), "team A saw a note written by team B"
        assert not seen & note_ids(to=b), "team A saw a pass-down addressed to team B"
        for d in docs:
            if d["kind"] == "question":
                assert f"to {a}" in d["label"]
            if d["kind"] == "answer":
                assert d["label"].startswith("Your answer")
            assert b not in d["text"], "team B's id appeared in a document team A can read"
    # and no answer of team B's that differs from every answer of team A's shows up as numbers
    study = next(s for s in RUN["studies"] if s["id"] == sid)
    a_vals = {str(v) for c in study["candidates"] if c["agent"] == a for v in (c["answer"] or {}).values()}
    b_vals = {str(v) for c in study["candidates"] if c["agent"] == b for v in (c["answer"] or {}).values()}
    text = " ".join(d["text"] for d in S.visible_docs(RUN, a, END))
    for v in b_vals - a_vals:
        if len(v) >= 4:   # skip short values like 0 or 1 that can occur by chance
            assert not re.search(rf"= {re.escape(v)}(?![0-9])", text), f"team B's number {v} reached team A"


def test_team_b_sees_its_own_pass_downs():
    for sid in STUDIES:
        b = f"{sid}-team-b"
        docs = S.visible_docs(RUN, b, END)
        expected = note_ids(to=b, kind="pass_down")
        assert expected, f"test data should have pass-downs to {b}"
        assert ids_of(docs, "pass_down") == expected
        assert ids_of(docs, "note") == note_ids(by=b, kind="note")


def test_nothing_after_the_playhead_is_visible():
    for agent in ["guide", "director", "gb05-team-a", "gb05-team-b", "gb05-library", "gb03-referee",
                  "gb03-team-c", "baseline-gb05-1"]:
        for t in (0, 150, 300, 450, 600, 800, 1000, 1200, 1700):
            docs = S.visible_docs(RUN, agent, t)
            assert docs[0]["kind"] == "session"   # the profile always comes first
            for d in docs:
                assert d["t"] <= t, f"{agent} at {t} saw {d['id']} from {d['t']}"
            # every visible note really was written by the playhead
            for d in docs:
                if d["id"] in RUN["notes"]:
                    assert RUN["notes"][d["id"]]["t"] <= t


def test_a_later_note_is_hidden_early_and_shown_late():
    note = max((n for n in RUN["notes"].values() if n["by"] == "gb05-team-b"), key=lambda n: n["t"])
    assert note["id"] not in ids_of(S.visible_docs(RUN, "gb05-team-b", note["t"] - 1))
    assert note["id"] in ids_of(S.visible_docs(RUN, "gb05-team-b", note["t"]))


def test_answers_appear_only_after_their_candidate_event():
    sid, a = "gb03", "gb03-team-a"
    first = min(e["t"] for e in RUN["events"] if e["k"] == "candidate" and e["s"] == sid and e["team"] == "team-a")
    assert not ids_of(S.visible_docs(RUN, a, first - 0.5), "answer")
    assert ids_of(S.visible_docs(RUN, a, first), "answer")


def test_referee_sees_x_and_y_and_no_team_names():
    for s in RUN["studies"]:
        if not s.get("referee"):
            continue
        ref = f"{s['id']}-referee"
        docs = S.visible_docs(RUN, ref, END)
        labels = {d["label"] for d in docs if d["kind"] == "answer"}
        assert labels == {f"Answer X, {s['id']}", f"Answer Y, {s['id']}"}
        assert any(d["id"] == f"{s['id']}-referee" for d in docs)
        for d in docs:
            blob = d["label"] + " " + d["text"]
            assert not re.search(r"team[- ]?[abc]\b", blob, re.I), f"team name reached the referee: {d['id']}"
            assert "team-" not in blob


def test_referee_record_waits_for_the_verdict():
    verdict = next(e["t"] for e in RUN["events"] if e["k"] == "referee.verdict" and e["s"] == "gb03")
    assign = next(e["t"] for e in RUN["events"] if e["k"] == "referee.assign" and e["s"] == "gb03")
    mid = (assign + verdict) / 2
    docs = S.visible_docs(RUN, "gb03-referee", mid)
    assert ids_of(docs, "answer") and "gb03-referee" not in ids_of(docs)
    assert "gb03-referee" in ids_of(S.visible_docs(RUN, "gb03-referee", verdict))
    assert not ids_of(S.visible_docs(RUN, "gb03-referee", assign - 1), "answer")


def test_library_sees_only_its_own_searches_and_notes():
    for sid in STUDIES:
        lib = f"{sid}-library"
        docs = S.visible_docs(RUN, lib, END)
        searches = [d for d in docs if d["kind"] == "search"]
        assert len(searches) == sum(1 for s in RUN["searches"] if s["a"] == lib)
        assert all(f"by {lib}" in d["label"] for d in searches)
        assert ids_of(docs, "note") == note_ids(by=lib, kind="note")
        assert {d["kind"] for d in docs} <= {"session", "search", "note"}


def test_guide_sees_grades_and_director_does_not():
    guide = S.visible_docs(RUN, "guide", END)
    director = S.visible_docs(RUN, "director", END)
    assert "totals" in ids_of(guide)
    assert any(i.endswith("-grade") for i in ids_of(guide))
    assert any("no agent ever saw them" in d["text"] for d in guide)
    assert not any(i.endswith("-grade") or i == "totals" for i in ids_of(director))
    assert not any("solo baseline attempts passed" in d["text"] for d in director)
    # grades exist only after grading, even for the guide
    early = S.visible_docs(RUN, "guide", S.grade_time(RUN) - 1)
    assert not any(i.endswith("-grade") or i == "totals" for i in ids_of(early))


def test_director_reads_all_notes_and_questions():
    docs = S.visible_docs(RUN, "director", END)
    assert ids_of(docs, "note") | ids_of(docs, "pass_down") == set(RUN["notes"])
    n_questions = sum(1 for e in RUN["events"] if e["k"] == "relay.leak_check")
    assert len(ids_of(docs, "question")) >= n_questions
    assert not ids_of(docs, "search")


def test_solo_agent_sees_only_its_profile():
    for agent in [a for a in AGENT_IDS if a.startswith("baseline-")]:
        docs = S.visible_docs(RUN, agent, END)
        assert len(docs) == 1 and docs[0]["kind"] == "session"
        assert "no lab" in docs[0]["text"]


def test_profile_says_role_model_and_activity():
    prof = S.visible_docs(RUN, "director", 500)[0]["text"]
    assert "Fable 5.2" in prof and "Director" in prof and "Latest thing you did" in prof
    prof_b = S.visible_docs(RUN, "gb05-team-b", 1000)[0]["text"]
    assert "Opus 5.5" in prof_b and "without the Library's briefing" in prof_b
    prof_a = S.visible_docs(RUN, "gb05-team-a", 1000)[0]["text"]
    assert "received the Library's briefing" in prof_a


def test_unknown_agent_raises():
    with pytest.raises(KeyError):
        S.visible_docs(RUN, "gb99-team-a", END)


# ------------------------------------------------------------------------------ retrieval

def test_bm25_ranks_the_matching_note_first():
    docs = [S.doc("session", "p", "profile", 0, "You are a team."),
            S.doc("note", "n1", "Note", 1, "We checked the sequencing depth of every replicate."),
            S.doc("note", "n2", "Note", 2, "Outgroup polarisation flips the derived allele and the sign of s."),
            S.doc("note", "n3", "Note", 3, "The plan is written before the analysis starts.")]
    scores = S.bm25_scores("which allele is derived from the outgroup?", docs[1:])
    assert scores.index(max(scores)) == 1
    order, ranked = S.select_docs("which allele is derived from the outgroup?", docs)
    assert order[0]["id"] == "p" and ranked[0]["id"] == "n2"


def test_select_docs_caps_context():
    docs = S.visible_docs(RUN, "guide", END)
    order, _ = S.select_docs("what did the referee find for gb03", docs)
    assert len(order) <= 1 + S.TOP_K + S.RECENT_K
    assert sum(min(len(d["text"]), S.DOC_CHARS) for d in order) <= S.CONTEXT_CHARS + S.DOC_CHARS
    assert any(d["id"] == "gb03-referee" for d in order)


def test_cited_sources_parses_refs():
    order = [S.doc("session", "p", "profile", 0, "x"), S.doc("note", "a", "A", 1, "y"), S.doc("note", "b", "B", 2, "z")]
    src = S.cited_sources("We found it [2] and again [2, 3].", order, order)
    assert [s["ref"] for s in src] == [2, 3] and src[0]["id"] == "a"
    assert len(S.cited_sources("no refs here", order, order)) == 3


# ------------------------------------------------------------------------------ HTTP server

@pytest.fixture(scope="module")
def base_url():
    server = S.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def fake_claude(monkeypatch):
    calls = []

    def fake(system, messages):
        calls.append({"system": system, "messages": messages})
        return "I cannot see the other team's work [1].", "fake-model"
    monkeypatch.setattr(S, "call_claude", fake)
    return calls


def request(base, path, body=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def ask(**over):
    body = {"run": "test-v2", "agent": "gb05-team-b", "t": 1000, "question": "What did team A find?", "history": []}
    body.update(over)
    return body


def test_runs_lists_test_v2(base_url):
    status, runs = request(base_url, "/api/runs")
    assert status == 200
    row = next(r for r in runs if r["run"] == "test-v2")
    assert row["studies"] == 8 and row["finished"] is True and row["source"] == "results" and row["live"] is False
    assert set(row) == {"run", "label", "started", "studies", "agents", "finished", "source", "live"}


def test_run_endpoint(base_url):
    status, data = request(base_url, "/api/run/test-v2")
    assert status == 200 and {"agents", "events", "notes", "searches", "studies", "config", "totals"} <= set(data)
    assert request(base_url, "/api/run/no-such-run")[0] == 404
    assert request(base_url, "/api/run/..%2Fetc")[0] in (400, 404)


def test_ask_ok(base_url, fake_claude):
    status, out = request(base_url, "/api/ask", ask(history=[{"role": "user", "text": "hi"},
                                                            {"role": "assistant", "text": "hello"}]))
    assert status == 200, out
    assert out["mode"] == "claude" and out["model"] == "fake-model"
    assert out["sources"] and out["sources"][0]["ref"] == 1
    prompt = fake_claude[-1]["messages"][-1]["content"]
    assert "What did team A find?" in prompt and "gb05-team-a" not in prompt
    assert "Team B for gb05" in fake_claude[-1]["system"]
    assert [m["role"] for m in fake_claude[-1]["messages"]] == ["user", "assistant", "user"]


@pytest.mark.parametrize("body,code", [
    (ask(agent="gb99-team-z"), 404),
    (ask(agent="../etc"), 400),
    (ask(agent=5), 400),
    (ask(run="no-such-run"), 404),
    (ask(run="../results"), 400),
    (ask(question="x" * (S.MAX_QUESTION + 1)), 400),
    (ask(question="   "), 400),
    (ask(history=[{"role": "user", "text": "q"}] * (S.MAX_HISTORY + 1)), 400),
    (ask(history=[{"role": "system", "text": "q"}]), 400),
    (ask(t="soon"), 400),
    (ask(t=True), 400),
    (b"not json", 400),
    (b"[1, 2]", 400),
])
def test_ask_validation(base_url, fake_claude, body, code):
    status, out = request(base_url, "/api/ask", body)
    assert status == code and "error" in out
    assert not fake_claude


def test_ask_oversize_body(base_url, fake_claude):
    status, out = request(base_url, "/api/ask", ask(question="y" * (S.MAX_BODY + 10)))
    assert status in (400, 413) and "error" in out


def test_ask_unavailable_is_503(base_url, monkeypatch):
    def down(system, messages):
        raise S.Unavailable("no backend")
    monkeypatch.setattr(S, "call_claude", down)
    status, out = request(base_url, "/api/ask", ask())
    assert status == 503 and out == {"error": "no backend"}


def test_ask_internal_error_has_no_traceback(base_url, monkeypatch):
    def boom(system, messages):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(S, "call_claude", boom)
    status, out = request(base_url, "/api/ask", ask())
    assert status == 500 and out == {"error": "internal error"}


def test_static_without_dist(base_url, monkeypatch, tmp_path):
    monkeypatch.setattr(S, "DIST", tmp_path / "dist")
    with urllib.request.urlopen(base_url + "/", timeout=10) as r:
        assert r.status == 200 and b"npm run build" in r.read()


def test_static_with_dist(base_url, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(S, "DIST", dist)
    with urllib.request.urlopen(base_url + "/assets/app.js", timeout=10) as r:
        assert r.headers["Content-Type"].startswith("text/javascript")
    with urllib.request.urlopen(base_url + "/some/deep/route", timeout=10) as r:
        assert r.read() == b"<html>app</html>" and r.headers["Content-Type"].startswith("text/html")
    status, out = request(base_url, "/../../etc/passwd")
    assert status == 404   # a path that leaves dist is refused
    status, out = request(base_url, "/api/nothing")
    assert status == 404


# ------------------------------------------------------------------------ department heads (D-23)

def heads_run():
    """test-v2 with a head of Team A added: it read everything, like a run made with --heads."""
    run = json.loads(json.dumps(RUN))
    run["agents"].append({"id": "head-team-a", "role": "head", "study": None, "model": "fable",
                          "sessions": [{"phase": "head-review", "study": "gb05", "start": 100, "end": 200}], "cost": 0})
    return run


def test_a_head_reads_both_teams_and_the_questions_but_not_the_grades():
    run = heads_run()
    docs = S.visible_docs(run, "head-team-a", END)
    seen = ids_of(docs)
    assert note_ids(by="gb05-team-b", kind="note") <= seen and note_ids(by="gb05-team-a", kind="note") <= seen
    assert note_ids(to="gb05-team-b", kind="pass_down") <= seen
    assert not [d for d in docs if d["kind"] == "grade" or "grade" in d["id"]]
    # a scientist still reads only its own team
    team = ids_of(S.visible_docs(run, "gb05-team-a", END))
    assert not (note_ids(by="gb05-team-b", kind="note") & team)


def test_a_head_is_introduced_as_the_head_of_its_department():
    display, about = S.role_words(heads_run()["agents"][-1], "head-team-a", heads_run())
    assert display == "the head of Team A" and "every department's memory" in about and "never see the grades" in about
