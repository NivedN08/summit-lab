"""Department heads (D-23): who they are, what they can read, and what may reach a scientist.

Same fakes as test_protocol: a fake adapter, LocalMemory, the real Gateway and a scripted backend,
extended here with the heads' jobs.
"""

import json
import os
import subprocess
import sys

from lab import protocol, run
from lab.contracts import HEAD, WORKER, AgentResult
from lab.eventlog import JsonlEventLog
from lab.memory_local import LocalMemory

import test_protocol as tp
from test_protocol import A, B

HEAD_JOBS = {
    "# Your job: head of the library": "head-library",
    "# Your job: head of Team": "head-team",
    "# Your job: analyst, answering your department head's notes": "revise",   # answered like a revision
}


class HeadsBackend(tp.FakeBackend):
    """FakeBackend plus the heads. Extra plan keys:
    head_notes: {"team-a": [note, ...], "team-b": [...]}; a note is {"text": ...} and may set
                "cite_other": True to cite a fact the other team wrote.
    notes_by: teams whose analyst writes one fact to its own department during its first analysis.
    raw_logs: analysts leave a raw session log in `.agent/`, as the real runner does.
    """

    def __init__(self, plan):
        super().__init__(plan)
        self.facts = {}

    @staticmethod
    def job(brief):
        for heading, name in HEAD_JOBS.items():
            if heading in brief:
                return name
        return tp.FakeBackend.job(brief)

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800):
        job = self.job(brief)
        study = workspace.parent.name
        if job == "analyst":
            out = super().run(agent, brief, workspace, readable=readable, env=env, timeout_s=timeout_s)
            if self.plan.get("raw_logs"):
                (workspace / ".agent").mkdir(exist_ok=True)
                (workspace / ".agent" / "stream.jsonl").write_text("{}\n")
            team = agent.id[len(study) + 1:]
            if team in self.plan.get("notes_by", ()) and agent.id not in self.facts:
                done = subprocess.run([sys.executable, str(tp.ROOT / "lab" / "tools" / "memory.py"), "write",
                                       f"WARNING SIGN from {team}: the batch column is unbalanced.", "--label", "warning"],
                                      env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=30)
                assert done.returncode == 0, done.stderr
                self.facts[agent.id] = done.stdout.split()[1]
            return out
        if job not in ("head-library", "head-team"):
            return super().run(agent, brief, workspace, readable=readable, env=env, timeout_s=timeout_s)
        self.calls.append({"agent": agent.id, "job": job, "study": study, "brief": brief, "workspace": workspace,
                           "readable": list(readable), "env": env})
        if job == "head-library":
            result = {"verdict": "the findings apply to this data", "strong": [],
                      "weak": [{"fact": "f1", "why": "from a different assay"}], "missing": ["a check for batch effects"]}
        else:
            team = "team-a" if "head of Team A" in brief else "team-b"
            other = "team-b" if team == "team-a" else "team-a"
            notes = []
            for n in self.plan.get("head_notes", {}).get(team, []):
                came_from = [self.facts[f"{study}-{other}"]] if n.get("cite_other") else []
                notes.append({"from": came_from, "text": n["text"], "why": "head review"})
            result = {"verdict": "reviewed", "approve": not notes, "notes": notes}
        (workspace / "result.json").write_text(json.dumps(result))
        return AgentResult(ok=True, result=result, seconds=0.02, cost_usd=0.1)


def make(tmp_path, plan, **config):
    adapter = tp.FakeAdapter(tmp_path / "bench")
    run_dir = tmp_path / "runs" / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    memory = LocalMemory(log, run_dir / "memory.jsonl")
    backend = HeadsBackend(plan)
    cfg = protocol.Config(max_agents=3, heads=True, **config)
    lab = protocol.Lab(adapter, backend, memory, log, run_dir, cfg, exa_call=tp.no_exa)
    return lab, adapter, backend, log, memory, run_dir


def note(team, text):
    return f"NOTE FROM THE HEAD OF {team}: {text}"


# ----- who the heads are -----

def test_heads_are_off_by_default():
    assert protocol.Config().heads is False
    lab = protocol.Lab(None, None, None, None, "/tmp/unused", protocol.Config())
    org = lab.build_org([type("S", (), {"id": "s1"})()])
    assert not [a for a in org.agents if a.role == HEAD]


def test_three_heads_share_a_department_only_heads_and_the_director_read(tmp_path):
    lab, adapter, *_ = make(tmp_path, {})
    org = lab.build_org(adapter.studies())
    heads = [a for a in org.agents if a.role == HEAD]
    assert sorted(a.id for a in heads) == ["head-library", "head-team-a", "head-team-b"]
    assert {a.department for a in heads} == {"heads"} and {a.model for a in heads} == {"fable"}
    for head in heads:
        assert all(org.can_read(head.id, d) for d in org.departments)
    workers = [a for a in org.agents if a.role == WORKER]
    assert workers and not any(org.can_read(w.id, "heads") for w in workers)
    assert not org.can_read("s1-team-a", "s1-team-b") and org.can_read("director", "heads")


def test_a_head_recalls_every_department_and_a_worker_only_its_own(tmp_path):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, {})
    memory.setup(lab.build_org(adapter.studies()))
    memory.write("s1-team-a", "Team A dropped twelve repeated samples.", "a")
    memory.write("s1-team-b", "Team B fitted a mixed model.", "b")
    memory.write("head-team-a", "Review of s1-team-a: the duplicate check holds.", "review")
    seen = lambda who, q: " ".join(f.text for f in memory.recall(who, q))
    assert "Team B fitted" in seen("head-team-a", "fitted mixed model samples")
    assert "Team A dropped" in seen("head-team-b", "repeated samples dropped")
    assert "duplicate check holds" in seen("head-team-b", "review duplicate check")
    assert "Team B fitted" not in seen("s1-team-a", "fitted mixed model samples")
    assert "duplicate check holds" not in seen("s1-team-a", "review duplicate check")


# ----- the reviews in the flow -----

def test_every_study_gets_a_library_review_and_two_team_reviews(tmp_path):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, {"team-a": [A], "team-b": [A]})
    out = lab.run(tp.one_study(adapter))
    assert out["s1"]["status"] == "replicated"
    jobs = tp.jobs(backend)
    assert jobs.count("head-library") == 1 and jobs.count("head-team") == 2
    assert jobs.index("head-library") < jobs.index("relay") < jobs.index("analyst") < jobs.index("head-team")
    assert "revise" not in jobs                      # no notes, so nobody revises
    relay = next(c for c in backend.calls if c["job"] == "relay")
    assert "The head of the library reviewed these findings" in relay["brief"]
    assert "a check for batch effects" in relay["brief"] and "from a different assay" in relay["brief"]
    rec = tp.study_record(run_dir)
    assert rec["switches"]["heads"] is True
    assert rec["heads"]["library"]["ran"] and rec["heads"]["library"]["missing"] == ["a check for batch effects"]
    for team in ("team-a", "team-b"):
        assert rec["heads"][team]["ran"] and rec["heads"][team]["notes"] == [] and rec["heads"][team]["revised"] is False
    reviews = [e for e in log.events("head.review")]
    assert sorted(e["head"] for e in reviews) == ["head-library", "head-team-a", "head-team-b"]


def test_a_delivered_note_comes_from_the_head_and_the_team_revises(tmp_path):
    text = note("TEAM B", "check whether any sample id repeats. Does it change your method?")
    plan = {"team-a": [A], "team-b": [B, A], "head_notes": {"team-b": [{"text": text}]}}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    out = lab.run(tp.one_study(adapter))
    assert out["s1"]["status"] == "replicated"            # B fixed its answer before the hatch
    sent = [e for e in log.events("memory.pass_down") if e["actor"] == "head-team-b"]
    assert [(e["to_department"], e["text"]) for e in sent] == [("s1-team-b", text)]
    review_briefs = [c["brief"] for c in backend.calls if "answering your department head's notes" in c["brief"]]
    assert len(review_briefs) == 1 and [c["agent"] for c in backend.calls if c["brief"] == review_briefs[0]] == ["s1-team-b"]
    rec = tp.study_record(run_dir)
    assert rec["heads"]["team-b"]["revised"] is True and rec["heads"]["team-b"]["changed_answer"] is True
    assert rec["heads"]["team-a"]["revised"] is False
    assert [c["round"] for c in rec["comparisons"]] == ["analysis"]     # the reviewed answer goes to the hatch
    assert {(c["team"], c["round"]) for c in rec["candidates"]} >= {("team-b", "analysis"), ("team-b", "review")}
    assert rec["unsanctioned_pass_downs"] == []
    # team B can now read its head's note; team A cannot
    assert text in " ".join(f.text for f in memory.recall("s1-team-b", "notes from the head of my department"))
    assert text not in " ".join(f.text for f in memory.recall("s1-team-a", "notes from the head of my department"))


def test_a_note_holding_the_other_teams_value_is_not_delivered(tmp_path):
    leaky = note("TEAM A", f"your effect is far from {B[0]}; check the duplicates. Does it change your method?")
    plan = {"team-a": [A], "team-b": [B], "head_notes": {"team-a": [{"text": leaky}]},
            "questions": [], "referee": {"pick_team": "team-a"}}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    lab.run(tp.one_study(adapter))
    checks = [e for e in log.events("relay.leak_check") if e["round"] == "head-review"]
    assert len(checks) == 1 and checks[0]["passed"] is False and checks[0]["by"] == "head-team-a"
    assert any("another team's value" in r for r in checks[0]["reasons"])
    assert not [e for e in log.events("memory.pass_down") if e["actor"] == "head-team-a"]
    rec = tp.study_record(run_dir)
    assert rec["heads"]["team-a"]["notes"][0]["delivered"] is False and rec["heads"]["team-a"]["revised"] is False


def test_a_note_built_on_the_other_teams_notes_is_not_delivered(tmp_path):
    cited = note("TEAM A", "check whether the batch column is balanced. Does it change your method?")
    plan = {"team-a": [A], "team-b": [A], "notes_by": ["team-b"],
            "head_notes": {"team-a": [{"text": cited, "cite_other": True}]}}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    lab.run(tp.one_study(adapter))
    check = next(e for e in log.events("relay.leak_check") if e["round"] == "head-review")
    assert check["passed"] is False and any("other team's notes" in r for r in check["reasons"])
    assert not [e for e in log.events("memory.pass_down") if e["actor"] == "head-team-a"]


def test_a_head_gets_its_studys_log_and_its_own_scientists_work_only(tmp_path):
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, {"team-a": [A], "team-b": [B], "questions": [],
                                                                   "referee": {"pick_team": "team-a"}, "raw_logs": True})
    lab.run(adapter.studies())
    assert (run_dir / "s1" / "team-a" / ".agent" / "stream.jsonl").is_file()
    ws = run_dir / "s1" / "head-team-a"
    events = [json.loads(line) for line in (ws / "log" / "events.jsonl").read_text().splitlines()]
    assert events and not [e for e in events if e.get("study") == "s2" or str(e.get("actor", "")).startswith("s2-")]
    assert any(e["kind"] == "candidate" and e.get("team") == "team-b" for e in events)
    copied = {p.relative_to(ws / "work").as_posix() for p in (ws / "work").rglob("*") if p.is_file()}
    assert {"answer.json", "result.json"} <= copied and not any(c.startswith(".agent") for c in copied)
    assert json.loads((ws / "work" / "answer.json").read_text())["answer"]["effect"] == A[0]


def test_the_lab_command_switches_heads_on():
    args = run.build_parser().parse_args(["lab", "--benchmark", "genebench", "--run-dir", "x", "--heads"])
    cfg = run.make_config(args)
    assert cfg.heads is True and cfg.head_model == "fable"
    args = run.build_parser().parse_args(["lab", "--benchmark", "genebench", "--run-dir", "x"])
    assert run.make_config(args).heads is False


# ----- measures -----

def test_a_rescue_at_the_head_review_is_credited_to_the_heads_not_the_questions(tmp_path):
    from lab import measures
    text = note("TEAM B", "check whether any sample id repeats. Does it change your method?")
    plan = {"team-a": [A], "team-b": [B, A], "head_notes": {"team-b": [{"text": text}]}}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan)
    lab.run(tp.one_study(adapter))
    run.grade_run(run_dir, adapter)
    graded = {c["id"]: c["passed"] for c in json.loads((run_dir / "results.json").read_text())["studies"]["s1"]["candidates"]}
    assert graded["team-b-analysis"] is False and graded["team-b-review"] is True
    m = measures.measure(run_dir)
    heads = m["steps"]["heads"]
    assert heads["reviews"] == 2 and heads["notes_delivered"] == 1
    assert heads["compared"] == 1 and heads["rescued"] == 1 and heads["damaged"] == 0
    assert m["steps"]["questions"]["compared"] == 0
    assert m["false_agreement"]["first_agreed_both_wrong"] == 0      # the handed answers agreed and were right
    assert any("Department heads: 2 team reviews, 1 notes delivered" in line for line in measures.summary_lines(m))
