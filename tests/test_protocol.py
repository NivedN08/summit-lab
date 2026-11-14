"""Tests for lab.protocol (one study's flow), lab.baseline and the grade/report commands.

No network beyond 127.0.0.1 and no real agents: a small fake adapter, LocalMemory, the real
Gateway (with Exa stubbed out) and a scripted fake backend that writes the files a real agent
would write.
"""

import json
import math
import os
import pathlib
import subprocess
import sys

import pytest

from lab import baseline as baseline_mod
from lab import protocol, run
from lab.contracts import AgentResult, Study
from lab.eventlog import JsonlEventLog
from lab.memory_local import LocalMemory

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATEMENT = 'Estimate the effect and name its label. Return {"answer": {"effect": <float>, "label": "<text>"}}.'

# Distinctive values, so a leak is easy to spot in any text.
A = (2.718, "upzeta")
B = (9.137, "downomega")
TRUTH = A


# ----- fake adapter -----

class FakeAdapter:
    """Two studies, an answer with one number and one string, and its own grade."""

    name = "fake"

    def __init__(self, root: pathlib.Path):
        self.root = root
        for sid in ("s1", "s2"):
            (root / sid / "data").mkdir(parents=True, exist_ok=True)
            (root / sid / "data" / "table.csv").write_text("x,y\n1,2\n")
        self.graded = []

    def studies(self):
        return [Study(id=sid, title=sid, statement=STATEMENT, data_dir=self.root / sid / "data",
                      deliverables=["answer.json"]) for sid in ("s1", "s2")]

    def blocked_terms(self):
        return ["fakebench"]

    def schema(self, study):
        return {"effect": "number", "label": "string"}

    @staticmethod
    def _load(path):
        try:
            doc = json.loads(pathlib.Path(path).read_text())
        except (OSError, ValueError):
            return None
        return doc.get("answer") if isinstance(doc, dict) and isinstance(doc.get("answer"), dict) else None

    def problems(self, study, deliverable, candidate):
        ans = self._load(candidate)
        if ans is None:
            return ["not an answer object"]
        found = []
        if not isinstance(ans.get("effect"), (int, float)) or isinstance(ans.get("effect"), bool):
            found.append("effect must be a number")
        if not isinstance(ans.get("label"), str):
            found.append("label must be a string")
        return found

    def field_agreement(self, study, a, b):
        x, y = self._load(a) or {}, self._load(b) or {}
        out = {}
        try:
            out["effect"] = abs(x["effect"] - y["effect"]) <= 0.05 * max(abs(x["effect"]), abs(y["effect"]))
        except (KeyError, TypeError):
            out["effect"] = False
        out["label"] = str(x.get("label", "")).lower() == str(y.get("label", "?")).lower()
        return out

    def agreement(self, study, deliverable, a, b):
        f = self.field_agreement(study, a, b)
        return sum(f.values()) / len(f)

    def grade(self, study, final_dir):
        self.graded.append((study.id, str(final_dir)))
        ans = self._load(pathlib.Path(final_dir) / "answer.json") or {}
        ok = ans.get("effect") is not None and math.isclose(ans["effect"], TRUTH[0], rel_tol=0.01) and ans.get("label") == TRUTH[1]
        return {"passed": bool(ok), "score": 1.0 if ok else 0.0}


# ----- fake backend -----

JOBS = {
    "# Your job: the lab's library": "library",
    "# Your job: director, briefing the teams": "relay",
    "# Your job: director, after the teams disagreed": "crossexam",
    "# Your job: analyst on a blind team": "analyst",
    "# Your job: analyst, answering the director's questions": "revise",
    "# Your job: referee": "referee",
}


class FakeBackend:
    """Plays every agent from a script. `plan` keys:
    team-a / team-b / team-c: list of (effect, label) or None (invalid answer), one per run
    questions: list of {"to": "team-a"|"team-b", "question": ...}
    referee: {"pick_team": "team-b"} or {"pick": "neither", "own_answer": ...}
    fail: {"team-a": 1} makes that agent's first run(s) fail
    baseline: list of (effect, label) by attempt number
    """

    def __init__(self, plan):
        self.plan = plan
        self.calls = []
        self.fails_left = dict(plan.get("fail", {}))

    @staticmethod
    def job(brief):
        for heading, name in JOBS.items():
            if heading in brief:
                return name
        return "baseline"

    def _write_answer(self, ws, value, who):
        doc = {"answer": {"effect": "not a number"}} if value is None else \
            {"answer": {"effect": value[0], "label": value[1]}, "reasoning": "fitted a model"}
        (ws / "answer.json").write_text(json.dumps(doc))
        result = {"answer_file": "answer.json", "method": f"method used by {who}",
                  "warning_signs": [{"sign": "duplicates", "means": "drop them", "action": "dropped"}],
                  "weakest_point": "small sample", "confidence": 0.6}
        (ws / "result.json").write_text(json.dumps(result))
        return result

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800):
        workspace = pathlib.Path(workspace)
        job = self.job(brief)
        study = workspace.parent.name
        self.calls.append({"agent": agent.id, "job": job, "study": study, "brief": brief,
                           "workspace": workspace, "readable": [pathlib.Path(p) for p in readable], "env": env})
        suffix = agent.id[len(study) + 1:] if agent.id.startswith(study + "-") else agent.id
        if self.fails_left.get(suffix):
            self.fails_left[suffix] -= 1
            return AgentResult(ok=False, result={}, seconds=0.01, error="scripted failure")
        result = {}
        if job == "library":
            full_env = {**os.environ, **(env or {})}
            out = subprocess.run([sys.executable, str(ROOT / "lab" / "tools" / "memory.py"), "write",
                                  "PITFALL: repeated samples inflate the effect. CHECK: count duplicate ids.",
                                  "--label", "pitfall"], env=full_env, capture_output=True, text=True, timeout=30)
            assert out.returncode == 0, out.stderr
            result = {"pitfalls": [{"title": "duplicates"}], "searches_made": 0, "fact": out.stdout.split()[1]}
        elif job == "relay":
            result = {"pass_down": [{"from": [], "text": "PITFALL: check for repeated sample ids before fitting.",
                                     "reason": "duplicates change the estimate"}]}
        elif job in ("analyst", "revise"):
            done = sum(1 for c in self.calls[:-1] if c["agent"] == agent.id and c["job"] in ("analyst", "revise")
                       and c["study"] == study)
            answers = self.plan[suffix]
            result = self._write_answer(workspace, answers[min(done, len(answers) - 1)], agent.id)
        elif job == "crossexam":
            teams_dir = workspace / "teams"
            assert teams_dir.is_dir() and not list(teams_dir.rglob("answer*.json"))
            result = {"differences_found": ["rows kept"], "likely_cause": "duplicates",
                      "questions": [{"to": f"{study}-{q['to']}", "from": [], "question": q["question"]}
                                    for q in self.plan.get("questions", [])]}
        elif job == "referee":
            spec = self.plan["referee"]
            if "pick_team" in spec:
                wanted = self.plan[spec["pick_team"]][-1]
                pick = None
                for label in ("X", "Y"):
                    doc = json.loads((workspace / "candidates" / label / "answer.json").read_text())
                    if doc["answer"]["effect"] == wanted[0]:
                        pick = label
                result = {"difference": "rows kept", "test": "refit", "outcome": "supports one", "pick": pick,
                          "reason": "the refit matched", "own_answer": None}
            else:
                result = {"difference": "rows kept", "test": "refit", "outcome": "supports neither",
                          "pick": "neither", "reason": "both wrong", "own_answer": spec.get("own_answer")}
        else:   # baseline
            k = int(workspace.name.split("-")[1])
            result = self._write_answer(workspace, self.plan["baseline"][k - 1], "single agent")
        (workspace / "result.json").write_text(json.dumps(result))
        return AgentResult(ok=True, result=result, seconds=0.02, cost_usd=0.25)


def no_exa(path, body):
    raise AssertionError("no Exa call expected in these tests")


# ----- helpers -----

def make(tmp_path, plan, config=None, run_name="run"):
    adapter = FakeAdapter(tmp_path / "bench")
    run_dir = tmp_path / "runs" / run_name
    log = JsonlEventLog(run_dir / "events.jsonl")
    memory = LocalMemory(log, run_dir / "memory.jsonl")
    backend = FakeBackend(plan)
    lab = protocol.Lab(adapter, backend, memory, log, run_dir, config or protocol.Config(max_agents=3), exa_call=no_exa)
    return lab, adapter, backend, log, memory, run_dir


def final_answer(run_dir, sid="s1"):
    return json.loads((run_dir / sid / "final" / "answer.json").read_text())["answer"]


def study_record(run_dir, sid="s1"):
    return json.loads((run_dir / sid / "study.json").read_text())


def jobs(backend, study="s1"):
    return [c["job"] for c in backend.calls if c["study"] == study]


def one_study(adapter):
    return [s for s in adapter.studies() if s.id == "s1"]


# ----- the flow -----

def test_teams_agree_replicated_without_questions_or_referee(tmp_path):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, {"team-a": [A], "team-b": [(2.75, "UpZeta")]})
    out = lab.run(adapter.studies())
    for sid in ("s1", "s2"):
        assert out[sid]["status"] == "replicated"
        assert final_answer(run_dir, sid) == {"effect": A[0], "label": A[1]}   # team A's file
        assert "crossexam" not in jobs(backend, sid) and "referee" not in jobs(backend, sid)
        assert sorted(jobs(backend, sid)) == sorted(["library", "relay", "analyst", "analyst"])
    finals = log.events("study.final")
    assert {e["study"] for e in finals} == {"s1", "s2"} and all(e["status"] == "replicated" for e in finals)
    # the briefing reached both teams under the director's name
    to = {e["to_department"] for e in log.events("memory.pass_down") if e["actor"] == "director"}
    assert {"s1-team-a", "s1-team-b", "s2-team-a", "s2-team-b"} <= to
    rec = study_record(run_dir)
    assert rec["comparisons"][0]["agree"] is True and rec["comparisons"][0]["fields"] == {"effect": True, "label": True}
    assert any(n["agent"] == "s1-team-b" for n in rec["not_chosen"])
    starts, finishes = log.events("agent.start"), log.events("agent.finish")
    assert len(starts) == len(finishes) == len(backend.calls)
    assert all({"agent", "study", "phase", "ok", "seconds", "cost_usd"} <= set(e) for e in finishes)


def test_disagree_then_questions_then_team_b_revises_converged(tmp_path):
    plan = {"team-a": [A, A], "team-b": [B, A],
            "questions": [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: the other team found repeated "
                                                        "sample ids. Does that change your method?"}]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "converged"
    assert final_answer(run_dir) == {"effect": A[0], "label": A[1]}
    assert jobs(backend).count("revise") == 2 and "referee" not in jobs(backend)
    delivered = [e for e in log.events("memory.pass_down") if e["to_department"] == "s1-team-b" and e["text"].startswith("QUESTION")]
    assert len(delivered) == 1
    rec = study_record(run_dir)
    assert [c["round"] for c in rec["comparisons"]] == ["analysis", "revise"]
    assert rec["questions"][0]["delivered"] is True
    assert any(n["agent"] == "s1-team-b" and n["round"] == "analysis" for n in rec["not_chosen"])


def test_still_disagree_referee_picks_team_b(tmp_path):
    plan = {"team-a": [A, A], "team-b": [B, B], "referee": {"pick_team": "team-b"},
            "questions": [{"to": "team-a", "question": "The other team found an outlier batch. Does that change your method?"}]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "refereed"
    assert final_answer(run_dir) == {"effect": B[0], "label": B[1]}
    assign = log.events("referee.assign")[0]
    assert set(assign["mapping"].values()) == {"s1-team-a", "s1-team-b"}
    # the referee's folder never says which candidate is which team
    referee_ws = next(c["workspace"] for c in backend.calls if c["job"] == "referee")
    for f in referee_ws.rglob("*"):
        if f.is_file():
            text = f.read_text().lower()
            assert "team-a" not in text and "team-b" not in text and "team a" not in text
    method = json.loads((referee_ws / "candidates" / "X" / "method.json").read_text())
    assert "method" not in method and "warning_signs" in method   # the field naming the team was removed
    rec = study_record(run_dir)
    assert rec["referee"]["pick"] in ("X", "Y")
    assert any(n["agent"] == "s1-team-a" and "referee" in n["reason"] for n in rec["not_chosen"])
    assert "team-c" not in " ".join(c["agent"] for c in backend.calls)


def test_referee_neither_team_c_agrees_with_a_third_team(tmp_path):
    q = "QUESTION FROM THE DIRECTOR: the other team found that two files use different units. Does that change your method?"
    plan = {"team-a": [A, A], "team-b": [B, B], "team-c": [A], "referee": {"pick": "neither"},
            "questions": [{"to": "team-b", "question": q}]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "third-team"
    assert final_answer(run_dir) == {"effect": A[0], "label": A[1]}
    to_c = [e["text"] for e in log.events("memory.pass_down") if e["to_department"] == "s1-team-c"]
    assert any("different units" in t for t in to_c)
    c_call = next(c for c in backend.calls if c["agent"] == "s1-team-c")
    assert c_call["job"] == "analyst"
    rec = study_record(run_dir)
    assert rec["final"]["agent"] == "s1-team-c"
    assert any(n["agent"] == "s1-team-b" for n in rec["not_chosen"])


def test_referee_neither_and_no_agreement_unresolved_uses_own_answer(tmp_path):
    own = {"answer": {"effect": 5.5, "label": "middle"}}
    plan = {"team-a": [A, A], "team-b": [B, B], "team-c": [(7.0, "other")], "referee": {"pick": "neither", "own_answer": own}}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "unresolved"
    assert final_answer(run_dir) == own["answer"]


def test_one_invalid_answer_single_source(tmp_path):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, {"team-a": [None], "team-b": [B]})
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "single-source"
    assert final_answer(run_dir) == {"effect": B[0], "label": B[1]}
    rec = study_record(run_dir)
    bad = next(n for n in rec["not_chosen"] if n["agent"] == "s1-team-a")
    assert "format" in bad["reason"]
    assert "crossexam" not in jobs(backend)


def test_failed_run_is_retried_once(tmp_path):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, {"team-a": [A], "team-b": [A], "fail": {"team-a": 1}})
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "replicated"
    runs = [e for e in log.events("agent.finish") if e["agent"] == "s1-team-a"]
    assert [e["ok"] for e in runs] == [False, True] and [e["attempt"] for e in runs] == [1, 2]


def test_question_with_other_teams_value_is_blocked_logged_and_not_delivered(tmp_path):
    leaky_number = "QUESTION FROM THE DIRECTOR: the other team estimated about 2.72. Does that change your method?"
    leaky_string = "QUESTION FROM THE DIRECTOR: the other team labelled it DownOmega. Check your label."
    clean = "QUESTION FROM THE DIRECTOR: the other team found repeated rows. Does that change your method?"
    plan = {"team-a": [A, A], "team-b": [B, B], "referee": {"pick_team": "team-a"},
            "questions": [{"to": "team-b", "question": leaky_number}, {"to": "team-a", "question": leaky_string},
                          {"to": "team-b", "question": clean}]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    lab.run(one_study(adapter))
    checks = [e for e in log.events("relay.leak_check") if e["round"] == "questions"]
    assert [(e["to"], e["passed"]) for e in checks] == [("s1-team-b", False), ("s1-team-a", False), ("s1-team-b", True)]
    assert checks[0]["reasons"] and checks[1]["reasons"]
    texts = [e["text"] for e in log.events("memory.pass_down")]
    assert clean in texts and leaky_number not in texts and leaky_string not in texts
    seen_by_b = " ".join(f.text for f in memory.recall("s1-team-b", "the other team estimated about 2.72", 200))
    assert "2.72" not in seen_by_b
    rec = study_record(run_dir)
    assert [q["delivered"] for q in rec["questions"]] == [False, False, True]


def test_leak_check_rules():
    other, own = {"effect": 5.02, "label": "Down"}, {"effect": 5.0, "label": "down"}
    assert protocol.leak_check("Is it near 5.01?", {"effect": 1.0}, [other])            # within 1% of the other
    assert not protocol.leak_check("Is it near 5.01?", own, [other])                     # but the team's own too
    assert not protocol.leak_check("Is it near 5.3?", {"effect": 1.0}, [other])          # 5.6% away
    assert protocol.leak_check("Check the DOWN label", {"label": "up"}, [other])
    assert not protocol.leak_check("Check the DOWN label", own, [other])                  # same string as the team gave
    assert protocol.leak_check("A share of 25% was dropped", {}, [{"rate": 0.25}])        # percent form
    assert protocol.leak_check("about 1,234 rows", {}, [{"n": 1234}])
    assert not protocol.leak_check("Single letters like a are fine", {}, [{"code": "a"}])


def test_blindness_across_a_whole_run(tmp_path):
    plan = {"team-a": [A, A], "team-b": [B, B], "team-c": [A], "referee": {"pick": "neither"},
            "questions": [{"to": "team-b", "question": "The other team got 2.718; does that change your method?"},
                          {"to": "team-a", "question": "The other team saw a batch pattern. Does that change your method?"},
                          {"to": "team-b", "question": "The other team saw repeated ids. Does that change your method?"}]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    out = lab.run(adapter.studies())
    assert out["s1"]["status"] == out["s2"]["status"] == "third-team"
    values = {"team-a": ["2.718", "2.72", "upzeta"], "team-b": ["9.137", "9.14", "downomega"]}
    others = {"team-a": ["team-b"], "team-b": ["team-a"], "team-c": ["team-a", "team-b"]}
    team_calls = [c for c in backend.calls if c["job"] in ("analyst", "revise")]
    assert {c["agent"] for c in team_calls} == {f"{s}-team-{x}" for s in ("s1", "s2") for x in "abc"}
    for call in team_calls:
        sid, team = call["study"], call["agent"][len(call["study"]) + 1:]
        study_dir = run_dir / sid
        for other in others[team]:
            other_ws = study_dir / other
            assert str(other_ws) not in call["brief"]
            for text in values[other]:
                assert text.lower() not in call["brief"].lower()
            for p in call["readable"] + [call["workspace"]]:
                p = p.resolve()
                assert not p.is_relative_to(other_ws.resolve()) and not other_ws.resolve().is_relative_to(p)
        for p in call["readable"]:
            assert not p.resolve().is_relative_to(run_dir.resolve())   # nothing the lab wrote, besides its own folder
    # nothing delivered to a team's memory carries another team's values
    for e in log.events("memory.pass_down"):
        dept = e["to_department"]
        team = dept.split("-", 1)[1] if "-" in dept else dept
        for other in others.get(team, []):
            for text in values[other]:
                assert text.lower() not in e["text"].lower(), (dept, e["text"])
    # and no fact a team may read (its whole department) holds the other team's values
    facts = [json.loads(line) for line in (run_dir / "memory.jsonl").read_text().splitlines() if line.strip()]
    for sid in ("s1", "s2"):
        for team, other_list in others.items():
            seen = " ".join(f["text"] for f in facts if f["department"] == f"{sid}-{team}").lower()
            assert seen   # the department did receive the briefing and questions
            for other in other_list:
                for text in values[other]:
                    assert text.lower() not in seen


def test_switches_turn_off_library_questions_and_referee(tmp_path):
    plan = {"team-a": [A], "team-b": [B], "team-c": [B]}
    config = protocol.Config(max_agents=2, library=False, questions=False, referee=False)
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan, config)
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "third-team"
    assert jobs(backend) == ["analyst", "analyst", "analyst"]


def test_finished_study_is_not_rerun(tmp_path):
    plan = {"team-a": [A], "team-b": [A]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    lab.run(adapter.studies())
    first = len(backend.calls)
    assert first > 0
    lab2, _, backend2, log2, _, _ = make(tmp_path, plan)
    out = lab2.run(adapter.studies())
    assert backend2.calls == []
    assert out["s1"]["status"] == "replicated" and out["s1"].get("skipped")
    assert len(log2.events("study.final")) == 2


# ----- baselines, grading, report -----

def test_combine_attempts_votes(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench")
    study = adapter.studies()[0]
    paths = []
    for i, doc in enumerate([{"answer": {"effect": 1.0, "label": "up"}}, {"answer": {"effect": 2.0, "label": "Up "}},
                             {"answer": {"effect": 10.0, "label": "down"}}, {"answer": {"effect": "bad"}}]):
        p = tmp_path / f"a{i}.json"
        p.write_text(json.dumps(doc))
        paths.append(p)
    voted = baseline_mod.combine_attempts(adapter, study, paths)
    assert voted == {"answer": {"effect": 2.0, "label": "up"}}
    ints = []
    for i, v in enumerate([0, 1]):
        p = tmp_path / f"i{i}.json"
        p.write_text(json.dumps({"answer": {"effect": v, "label": "x"}}))
        ints.append(p)
    assert baseline_mod.combine_attempts(adapter, study, ints)["answer"]["effect"] in (0, 1)   # a code stays a code
    assert baseline_mod.combine_attempts(adapter, study, paths[3:]) is None


def test_grade_refuses_while_an_agent_is_unfinished_then_grades(tmp_path, monkeypatch, capsys):
    plan = {"team-a": [A], "team-b": [A], "baseline": [A, B]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    lab.run(adapter.studies())
    baseline_mod.run_baselines(adapter, backend, log, run_dir, adapter.studies(), attempts=2, model="opus",
                               max_agents=2)
    base_calls = [c for c in backend.calls if c["job"] == "baseline"]
    assert len(base_calls) == 4
    assert all("How this lab works" not in c["brief"] and not c["env"] for c in base_calls)
    assert (run_dir / "baseline" / "s1" / "attempt-2" / "answer.json").exists()

    monkeypatch.setitem(run.ADAPTERS, "fake", lambda: adapter)
    log.append("agent.start", "ghost", agent="ghost", study="s1", phase="analysis", run_id="r-ghost", attempt=1)
    assert run.main(["grade", "--run-dir", str(run_dir), "--benchmark", "fake"]) != 0
    assert not (run_dir / "results.json").exists() and adapter.graded == []

    log.append("agent.finish", "ghost", agent="ghost", study="s1", phase="analysis", run_id="r-ghost", attempt=1,
               ok=True, seconds=1.0, cost_usd=0.0)
    assert run.main(["grade", "--run-dir", str(run_dir), "--benchmark", "fake"]) == 0
    results = json.loads((run_dir / "results.json").read_text())
    s1 = results["studies"]["s1"]
    assert s1["lab"]["status"] == "replicated" and s1["lab"]["grade"]["passed"] is True
    assert [s1["baseline"]["attempts"][k]["passed"] for k in ("attempt-1", "attempt-2")] == [True, False]
    assert "passed" in s1["baseline"]["vote"]
    assert (run_dir / "baseline" / "s1" / "vote" / "answer.json").exists()
    assert "s1" in capsys.readouterr().out

    assert run.main(["report", "--run-dir", str(run_dir)]) == 0
    report = (run_dir / "report.md").read_text()
    assert "replicated" in report and "s2" in report
