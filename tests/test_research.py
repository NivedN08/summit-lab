"""Tests for version 3 of the lab: worker models, team A's web search while it works, search
budgets enforced by the gateway, research on demand, the search-off and reproduction flags,
the new measures and graded outcome kinds.

Offline: the fake adapter and scripted backend from test_protocol.py, LocalMemory, the real
Gateway with Exa stubbed out (FakeExa from test_gateway.py), and Exa's call log moved to a
temporary file. Agents that search or write memory run the real lab/tools scripts.
"""

import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

from lab import baseline as baseline_mod
from lab import gateway_main, measures, protocol, run
from lab.contracts import WORKER, Agent, AgentResult, Org
from lab.eventlog import JsonlEventLog
from lab.gateway import Gateway
from lab.memory_local import LocalMemory
from lab.modal_gateway import ModalGateway
from lab.tools import exa
from test_gateway import FakeExa, post
from test_protocol import A, B, FakeAdapter, FakeBackend

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "lab" / "tools"
HEADER = "X-Verified-User-Data"

CLEAN = {"question": "In the standard method, is this kind of effect reported on the log scale or the raw scale?",
         "why": "the two teams may be using different scales"}
LEAKY = {"question": "Is an effect near 9.14 usual for this kind of study?", "why": "one team found it"}
NAMES_SOURCE = {"question": "What does the fakebench package do by default?", "why": "it may matter"}
FINDING = {"question": CLEAN["question"], "answer": "On the log scale, by convention in the methods literature.",
           "sources": ["https://example.org/methods"], "confidence": "high"}
TEAM_Q = [{"to": "team-b", "question": "QUESTION FROM THE DIRECTOR: the other team found repeated sample ids. "
                                      "Does that change your method?"}]


@pytest.fixture(autouse=True)
def exa_log(tmp_path, monkeypatch):
    """Keep every Exa call record of these tests out of the repository's records/ folder."""
    monkeypatch.setattr(exa, "LOG", tmp_path / "exa_calls.jsonl")


def config(*flags, **overrides):
    """The Config that `python3 -m lab.run lab` builds from these flags, with 3 agents at once."""
    args = run.build_parser().parse_args(["lab", "--benchmark", "fake", "--run-dir", "unused", *flags])
    return dataclasses.replace(run.make_config(args), max_agents=3, **overrides)


class V3Backend(FakeBackend):
    """FakeBackend plus the research job, research questions from the director, and real calls
    to research.py and memory.py through the gateway. Extra plan keys:
      research_questions  returned by the director with its questions, in both rounds
      findings            returned by the library in the research phase
      searches            {"team-a" | "team-b" | "library" | "research": n} search calls per run
      learned             LEARNED facts team A writes per run
    `searched` records (agent, job, return code, stderr) for every search call."""

    def __init__(self, plan):
        super().__init__(plan)
        self.searched = []

    def job(self, brief):
        return "research" if "# Your job: research on demand" in brief else FakeBackend.job(brief)

    @staticmethod
    def tool(env, *args):
        full = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        full.update(env or {})
        return subprocess.run([sys.executable, *map(str, args)], env=full, capture_output=True, text=True, timeout=30)

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800):
        workspace = pathlib.Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        job = self.job(brief)
        study = workspace.parent.name
        suffix = agent.id[len(study) + 1:] if agent.id.startswith(study + "-") else agent.id
        key = "research" if job == "research" else suffix
        for _ in range(self.plan.get("searches", {}).get(key, 0)):
            out = self.tool(env, TOOLS / "research.py", "search", "how is this method defined", "--objective", "check")
            self.searched.append((agent.id, job, out.returncode, out.stderr))
        if suffix == "team-a" and job in ("analyst", "revise"):
            for _ in range(self.plan.get("learned", 0)):
                out = self.tool(env, TOOLS / "memory.py", "write", "LEARNED: the method reports log effects. USED FOR: "
                                "step 3. SOURCE: https://example.org/methods", "--label", "LEARNED")
                assert out.returncode == 0, out.stderr
        if job == "research":
            self.calls.append({"agent": agent.id, "job": job, "study": study, "brief": brief, "workspace": workspace,
                               "readable": [pathlib.Path(p) for p in readable], "env": env})
            out = self.tool(env, TOOLS / "memory.py", "write", "FINDING: on the log scale. QUESTION: which scale. "
                            "SOURCE: https://example.org/methods", "--label", "FINDING")
            assert out.returncode == 0, out.stderr
            result = {"findings": self.plan.get("findings", [])}
            (workspace / "result.json").write_text(json.dumps(result))
            return AgentResult(ok=True, result=result, seconds=0.02, cost_usd=0.25)
        result = super().run(agent, brief, workspace, readable=readable, env=env, timeout_s=timeout_s)
        if job in ("crossexam", "challenge") and "research_questions" in self.plan:
            data = {**result.result, "research_questions": self.plan["research_questions"]}
            (workspace / "result.json").write_text(json.dumps(data))
            result = dataclasses.replace(result, result=data)
        return result


def make(tmp_path, plan, cfg=None, adapter=None, name="run"):
    adapter = adapter or FakeAdapter(tmp_path / "bench")
    run_dir = tmp_path / "runs" / name
    log = JsonlEventLog(run_dir / "events.jsonl")
    memory = LocalMemory(log, run_dir / "memory.jsonl")
    backend = V3Backend(plan)
    fake = FakeExa()
    lab = protocol.Lab(adapter, backend, memory, log, run_dir, cfg or config(), exa_call=fake)
    return types.SimpleNamespace(lab=lab, adapter=adapter, backend=backend, log=log, memory=memory,
                                 run_dir=run_dir, exa=fake)


def one(adapter):
    return [s for s in adapter.studies() if s.id == "s1"]


def record(run_dir, sid="s1"):
    return json.loads((run_dir / sid / "study.json").read_text())


def briefs(backend, agent_id, *jobs):
    return [c["brief"] for c in backend.calls if c["agent"] == agent_id and c["job"] in jobs]


def jobs(backend, study="s1"):
    return [c["job"] for c in backend.calls if c["study"] == study]


# ----- change 1: models -----

def test_default_models_and_the_mixed_team_flag(tmp_path):
    cfg = config()
    assert (cfg.worker_model, cfg.director_model, cfg.team_b_model) == ("fable", "fable", None)
    assert config("--model", "opus").worker_model == "opus"
    baseline_args = run.build_parser().parse_args(["baseline", "--benchmark", "fake", "--run-dir", "unused"])
    assert baseline_args.model == "fable"

    t = make(tmp_path, {"team-a": [A], "team-b": [A]}, config("--team-b-model", "opus"))
    t.lab.run(one(t.adapter))
    assert record(t.run_dir)["models"] == {"director": "fable", "s1-library": "fable", "s1-team-a": "fable",
                                           "s1-team-b": "opus", "s1-team-c": "fable", "s1-referee": "fable"}
    started = {e["agent"]: e["model"] for e in t.log.events("agent.start")}
    assert started["s1-team-b"] == "opus" and started["s1-team-a"] == "fable" and started["director"] == "fable"

    t = make(tmp_path, {"team-a": [A], "team-b": [A]}, config("--model", "opus"), name="all-opus")
    t.lab.run(one(t.adapter))
    models = record(t.run_dir)["models"]
    assert models["director"] == "fable" and {m for a, m in models.items() if a != "director"} == {"opus"}


# ----- change 2: team A searches while it works; team B does not -----

def test_team_a_briefs_have_the_search_section_and_team_b_briefs_do_not(tmp_path):
    t = make(tmp_path, {"team-a": [A, A], "team-b": [B, A], "questions": TEAM_Q})
    assert t.lab.run(one(t.adapter))["s1"]["status"] == "converged"
    team_a = briefs(t.backend, "s1-team-a", "analyst", "revise")
    team_b = briefs(t.backend, "s1-team-b", "analyst", "revise")
    assert len(team_a) == len(team_b) == 2
    for brief in team_a:
        assert f'python3 {TOOLS}/research.py search "QUERY"' in brief
        assert f"python3 {TOOLS}/research.py contents URL" in brief
        assert "how a method is defined, what a tool does by default, or which convention applies" in brief
        assert "read the page" in brief and "snippet" in brief
        assert "Never search for this task, its dataset, a benchmark or a worked solution." in brief
        assert 'label "LEARNED"' in brief and '"LEARNED: <what>. USED FOR: <which step>. SOURCE: <link>."' in brief
        assert "8 calls" in brief
    for brief in team_b:
        assert "research.py" not in brief and "LEARNED" not in brief and "## Web search" not in brief
        assert "search the web" not in brief.lower() and "web search" not in brief.lower()


def test_team_a_may_search_through_the_gateway_and_team_b_is_refused(tmp_path):
    t = make(tmp_path, {"team-a": [A], "team-b": [A], "searches": {"team-a": 1, "team-b": 1}})
    t.lab.run(one(t.adapter))
    results = {agent: (code, err) for agent, job, code, err in t.backend.searched}
    assert results["s1-team-a"][0] == 0
    assert results["s1-team-b"][0] != 0 and "403" in results["s1-team-b"][1]
    assert len(t.exa.calls) == 1
    refused = [e for e in t.log.events("gateway.request") if e["agent"] == "s1-team-b" and e["path"] == "/exa/search"]
    assert [e["status"] for e in refused] == [403]
    start = t.log.events("gateway.start")[0]
    assert start["exa_departments"] == ["s1-library", "s1-team-a"]


# ----- change 3: budgets enforced by the gateway -----

@pytest.fixture
def capped(tmp_path):
    org = Org(agents=[Agent("librarian", WORKER, "library", "opus"), Agent("free", WORKER, "library", "opus")])
    log = JsonlEventLog(tmp_path / "events.jsonl")
    memory = LocalMemory(log, tmp_path / "facts.jsonl")
    memory.setup(org)
    fake = FakeExa()
    gw = Gateway(org, memory, log, exa_departments=["library"], exa_caps={"librarian": 2}, exa_call=fake)
    base = gw.start()
    tokens = {a.id: gw.issue_token(a.id) for a in org.agents}
    yield types.SimpleNamespace(gw=gw, base=base, tokens=tokens, log=log, exa=fake)
    gw.stop()


def test_the_budget_refuses_calls_over_the_limit_and_logs_them(capped):
    token = capped.tokens["librarian"]
    assert post(capped.base, "/exa/search", {"query": "q", "objective": "o"}, token)[0] == 200
    assert post(capped.base, "/exa/contents", {"urls": ["https://example.org/a"], "objective": "o"}, token)[0] == 200
    status, body = post(capped.base, "/exa/search", {"query": "q", "objective": "o"}, token)
    assert status == 429 and "2 of 2" in body["error"] and "search and contents both count" in body["error"]
    assert len(capped.exa.calls) == 2
    refused = capped.log.events("exa.refused")
    assert len(refused) == 1 and refused[0]["agent"] == "librarian" and (refused[0]["used"], refused[0]["cap"]) == (2, 2)
    assert capped.log.events("gateway.request")[-1]["status"] == 429
    calls = capped.log.events("exa.call")
    assert [(e["agent"], e["endpoint"], e["used"], e["cap"]) for e in calls] == [
        ("librarian", "search", 1, 2), ("librarian", "contents", 2, 2)]
    # a malformed request is not counted, and an agent without a cap is not limited
    assert post(capped.base, "/exa/search", {"query": "", "objective": "o"}, token)[0] == 400
    for _ in range(3):
        assert post(capped.base, "/exa/search", {"query": "q", "objective": "o"}, capped.tokens["free"])[0] == 200


def test_a_new_budget_starts_a_new_count_under_its_label(capped):
    token = capped.tokens["librarian"]
    for _ in range(2):
        post(capped.base, "/exa/search", {"query": "q", "objective": "o"}, token)
    assert post(capped.base, "/exa/search", {"query": "q", "objective": "o"}, token)[0] == 429
    capped.gw.set_budget("librarian", 1, "research")
    assert post(capped.base, "/exa/search", {"query": "q", "objective": "o"}, token)[0] == 200
    assert post(capped.base, "/exa/search", {"query": "q", "objective": "o"}, token)[0] == 429
    assert capped.log.events("exa.call")[-1]["budget"] == "research"
    assert capped.log.events("exa.refused")[-1]["budget"] == "research"


def test_the_lab_gives_each_search_its_budget(tmp_path):
    plan = {"team-a": [A, A], "team-b": [B, A], "questions": TEAM_Q, "searches": {"team-a": 3, "library": 2}}
    t = make(tmp_path, plan, config("--max-searches", "1", team_searches=2))
    assert t.lab.run(one(t.adapter))["s1"]["status"] == "converged"
    codes = [(job, code) for agent, job, code, _ in t.backend.searched if agent == "s1-team-a"]
    assert [c == 0 for _, c in codes] == [True, True, False, True, True, False]     # 2 of 3 per round
    lib = [code for agent, job, code, _ in t.backend.searched if agent == "s1-library"]
    assert [c == 0 for c in lib] == [True, False]
    calls = [(e["agent"], e["budget"]) for e in t.log.events("exa.call")]
    assert calls.count(("s1-team-a", "team-a")) == 4 and calls.count(("s1-library", "library")) == 1
    assert [(e["agent"], e["budget"]) for e in t.log.events("exa.refused")] == [
        ("s1-library", "library"), ("s1-team-a", "team-a"), ("s1-team-a", "team-a")]


def test_gateway_main_passes_the_caps_and_reads_new_budgets(tmp_path):
    org = [dataclasses.asdict(Agent("librarian", WORKER, "library", "opus"))]
    log_dir = tmp_path / "gw"
    gw = gateway_main.build({"org": org, "exa_departments": ["library"], "exa_caps": {"librarian": 1},
                             "blocked_terms": [], "agents_may_pass_down": False}, "local", log_dir, 0)
    fake = FakeExa()
    gw.exa_call = fake
    gw.start()
    base = f"http://127.0.0.1:{gw._server.server_address[1]}"
    try:
        me = {HEADER: json.dumps({"agent": "librarian"})}
        search = {"query": "q", "objective": "o"}
        assert post_with(base, "/exa/search", search, me)[0] == 200
        assert post_with(base, "/exa/search", search, me)[0] == 429
        (log_dir / "budgets.json").write_text(json.dumps({"librarian": {"cap": 1, "label": "research", "window": 1}}))
        assert post_with(base, "/exa/search", search, me)[0] == 200
        assert post_with(base, "/exa/search", search, me)[0] == 429
        events = [json.loads(line) for line in (log_dir / "events.jsonl").read_text().splitlines()]
        assert [e["budget"] for e in events if e["kind"] == "exa.call"] == [None, "research"]
    finally:
        gw.stop()


def post_with(base, path, body, headers):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


class FilesSandbox:
    """Records the Modal gateway's file writes and moves."""

    object_id = "sb-files"

    def __init__(self):
        self.files, self.moves = {}, []
        sandbox = self

        class FS:
            def write_text(self, text, path):
                sandbox.files[path] = text

        self.filesystem = FS()

    def exec(self, *args, **kwargs):
        assert args[0] == "mv"
        self.moves.append(args[1:])
        self.files[args[2]] = self.files.pop(args[1])
        return types.SimpleNamespace(wait=lambda: 0)


def test_the_modal_gateway_gives_the_caps_in_its_config_and_writes_new_budgets(tmp_path):
    org = Org(agents=[Agent("s1-team-a", WORKER, "s1-team-a", "fable")])
    log = JsonlEventLog(tmp_path / "events.jsonl")
    gw = ModalGateway(org, LocalMemory(log, tmp_path / "m.jsonl"), log, exa_departments=["s1-team-a"],
                      exa_caps={"s1-team-a": 8})
    assert gw.config()["exa_caps"] == {"s1-team-a": 8}
    gw.sandbox = FilesSandbox()
    gw.set_budget("s1-team-a", 8, "team-a")
    gw.set_budget("s1-team-a", 8, "team-a")
    written = json.loads(gw.sandbox.files["/gateway/budgets.json"])
    assert written == {"s1-team-a": {"cap": 8, "label": "team-a", "window": 2}}
    assert gw.sandbox.moves[-1] == ("/gateway/budgets.json.tmp", "/gateway/budgets.json")
    gw.sandbox = None


# ----- change 4: research on demand -----

def test_research_questions_are_checked_researched_and_reach_both_teams_before_they_revise(tmp_path):
    plan = {"team-a": [A, A], "team-b": [B, A], "questions": TEAM_Q, "research_questions": [CLEAN, LEAKY, NAMES_SOURCE],
            "findings": [FINDING], "searches": {"research": 1}}
    t = make(tmp_path, plan)
    assert t.lab.run(one(t.adapter))["s1"]["status"] == "converged"
    crossexam = briefs(t.backend, "director", "crossexam")[0]
    assert "research_questions" in crossexam and "must never contain either team's answer values" in crossexam

    research = [c for c in t.backend.calls if c["job"] == "research"]
    assert len(research) == 1 and research[0]["agent"] == "s1-library"
    assert CLEAN["question"] in research[0]["brief"]
    assert "9.14" not in research[0]["brief"] and "fakebench" not in research[0]["brief"]
    assert research[0]["workspace"].name == "library-research"
    start = next(e for e in t.log.events("agent.start") if e["phase"] == "research")
    assert start["agent"] == "s1-library" and start["role"] == "research"
    assert [e["budget"] for e in t.log.events("exa.call") if e["agent"] == "s1-library"] == ["research"]

    rec = record(t.run_dir)
    entry = rec["research"][0]
    assert entry["round"] == "questions" and entry["ran"] is True
    assert [q["passed"] for q in entry["questions"]] == [True, False, False]
    assert entry["questions"][1]["reasons"] and entry["questions"][2]["reasons"]
    finding = entry["findings"][0]
    assert finding["answer"] == FINDING["answer"] and finding["sources"] == FINDING["sources"]
    assert {d: v["delivered"] for d, v in finding["deliveries"].items()} == {"s1-team-a": True, "s1-team-b": True}

    sent = [e for e in t.log.events("memory.pass_down") if FINDING["answer"] in e["text"]]
    assert {e["to_department"] for e in sent} == {"s1-team-a", "s1-team-b"} and all(e["actor"] == "director" for e in sent)
    assert all(FINDING["sources"][0] in e["text"] and CLEAN["question"] in e["text"] for e in sent)
    library_finding = next(e["fact_id"] for e in t.log.events("memory.write") if e["label"] == "FINDING")
    assert all(e["came_from"] == [library_finding] for e in sent)
    first_revise = min(e["seq"] for e in t.log.events("agent.start") if e["phase"] == "revise")
    seen = {e["agent"]: e["seq"] for e in t.log.events("memory.visible")
            if any(x in e["fact_ids"] for x in [s["fact_id"] for s in sent])}
    assert set(seen) == {"s1-team-a", "s1-team-b"} and max(seen.values()) < first_revise
    assert max(e["seq"] for e in sent) < first_revise
    for brief in briefs(t.backend, "s1-team-a", "revise") + briefs(t.backend, "s1-team-b", "revise"):
        assert "findings from the lab's library" in brief


def test_a_finding_with_one_teams_value_reaches_only_that_team(tmp_path):
    leaky = dict(FINDING, answer="Reported effects are often near 9.137 here.")
    plan = {"team-a": [A, A], "team-b": [B, A], "questions": TEAM_Q, "research_questions": [CLEAN], "findings": [leaky]}
    t = make(tmp_path, plan)
    t.lab.run(one(t.adapter))
    deliveries = record(t.run_dir)["research"][0]["findings"][0]["deliveries"]
    assert deliveries["s1-team-a"]["delivered"] is False and deliveries["s1-team-a"]["reasons"]
    assert deliveries["s1-team-b"]["delivered"] is True
    to_a = " ".join(e["text"] for e in t.log.events("memory.pass_down") if e["to_department"] == "s1-team-a")
    assert "9.137" not in to_a


def test_research_in_the_challenge_round_leads_to_a_revision_without_team_questions(tmp_path):
    plan = {"team-a": [A, A], "team-b": [A, A], "research_questions": [CLEAN], "findings": [FINDING]}
    t = make(tmp_path, plan)
    assert t.lab.run(one(t.adapter))["s1"]["status"] == "replicated"
    challenge = briefs(t.backend, "director", "challenge")[0]
    assert "research_questions" in challenge
    assert jobs(t.backend).count("research") == 1 and jobs(t.backend).count("revise") == 2
    rec = record(t.run_dir)
    assert rec["challenge_questions"] == 0 and rec["research"][0]["round"] == "challenge"
    assert all(d["delivered"] for d in rec["research"][0]["findings"][0]["deliveries"].values())
    assert rec["final"]["round"] == "challenge"


@pytest.mark.parametrize("flags", [["--no-research"], ["--no-library"]])
def test_no_research_or_no_library_means_no_research_on_demand(tmp_path, flags):
    plan = {"team-a": [A, A], "team-b": [B, A], "questions": TEAM_Q, "research_questions": [CLEAN], "findings": [FINDING]}
    t = make(tmp_path, plan, config(*flags))
    assert t.lab.run(one(t.adapter))["s1"]["status"] == "converged"
    assert "research" not in jobs(t.backend)
    assert ("library" in jobs(t.backend)) == (flags == ["--no-research"])
    crossexam = briefs(t.backend, "director", "crossexam")[0]
    assert "research_questions" not in crossexam and "Research questions" not in crossexam
    for brief in briefs(t.backend, "s1-team-a", "revise") + briefs(t.backend, "s1-team-b", "revise"):
        assert "findings from the lab's library" not in brief
    rec = record(t.run_dir)
    assert rec["research"] == [] and rec["switches"]["research"] is False
    assert not any(FINDING["answer"] in e["text"] for e in t.log.events("memory.pass_down"))


# ----- change 5: one flag for no search at all, and the reproduction flags -----

def test_no_search_turns_off_the_library_team_search_and_research(tmp_path):
    cfg = config("--no-search")
    assert (cfg.library, cfg.team_search, cfg.research) == (False, False, False)
    plan = {"team-a": [A, A], "team-b": [B, A], "questions": TEAM_Q, "research_questions": [CLEAN],
            "findings": [FINDING], "searches": {"team-a": 1}}
    t = make(tmp_path, plan, cfg)
    t.lab.run(one(t.adapter))
    assert "library" not in jobs(t.backend) and "research" not in jobs(t.backend)
    assert t.log.events("gateway.start")[0]["exa_departments"] == []
    assert all(code != 0 and "403" in err for _, _, code, err in t.backend.searched) and t.exa.calls == []
    for brief in briefs(t.backend, "s1-team-a", "analyst", "revise"):
        assert "research.py" not in brief and "LEARNED" not in brief
    assert "research_questions" not in briefs(t.backend, "director", "crossexam")[0]
    switches = record(t.run_dir)["switches"]
    assert (switches["library"], switches["team_search"], switches["research"]) == (False, False, False)


def test_the_reproduction_flags_give_the_earlier_design():
    cfg = config("--model", "opus", "--no-team-search", "--no-research")
    assert (cfg.worker_model, cfg.director_model, cfg.team_b_model) == ("opus", "fable", None)
    assert (cfg.library, cfg.team_search, cfg.research, cfg.challenge, cfg.brief_teams) == (
        True, False, False, True, ("team-a",))


# ----- change 6: measures, report and outcome kinds -----

def test_measures_count_searches_learned_facts_and_research(tmp_path):
    plan = {"team-a": [A, A], "team-b": [B, A], "questions": TEAM_Q, "research_questions": [CLEAN, LEAKY],
            "findings": [FINDING], "searches": {"team-a": 2, "library": 1, "research": 1}, "learned": 1}
    t = make(tmp_path, plan, config(team_searches=1))
    t.lab.run(one(t.adapter))
    run.grade_run(t.run_dir, t.adapter)
    m = measures.measure(t.run_dir)
    s = m["search"]
    assert s["calls"] == {"library": 1, "team-a": 2, "research": 1}
    assert s["refused"] == {"library": 0, "team-a": 2, "research": 0}
    assert s["learned"] == 2
    r = m["research"]
    assert (r["asked"], r["blocked"], r["answered"], r["findings"], r["delivered"]) == (2, 1, 1, 1, 2)
    assert r["studies"] == ["s1"]
    assert (r["revision"]["rescued"], r["revision"]["damaged"], r["revision"]["compared"]) == (1, 0, 2)
    text = "\n".join(measures.summary_lines(m))
    assert "Web search" in text and "Research on demand" in text
    (t.run_dir / "measures.json").write_text(json.dumps(m))
    assert run.main(["report", "--run-dir", str(t.run_dir)]) == 0
    report = (t.run_dir / "report.md").read_text()
    section = report[report.index("## Measures"):]
    assert "**Web search.**" in section and "**Research on demand.**" in section and "LEARNED" in section
    assert "research question" in report[:report.index("## Measures")].lower()


class KindAdapter(FakeAdapter):
    """FakeAdapter whose grade also says what sort of right or wrong the answer was."""

    def grade(self, study, final_dir):
        out = super().grade(study, final_dir)
        return {**out, "kind": "correct" if out["passed"] else "wrong effect"}


def test_outcome_kinds_are_carried_into_results_and_tallied(tmp_path):
    adapter = KindAdapter(tmp_path / "bench")
    plan = {"team-a": [A], "team-b": [A], "baseline": [A, B, B]}
    t = make(tmp_path, plan, adapter=adapter)
    t.lab.run(one(adapter))
    baseline_mod.run_baselines(adapter, t.backend, t.log, t.run_dir, one(adapter), attempts=3, model="fable",
                               max_agents=2)
    results = run.grade_run(t.run_dir, adapter)
    s1 = results["studies"]["s1"]
    rows = {r["id"]: r for r in s1["candidates"]}
    assert rows["final"]["kind"] == "final" and rows["final"]["outcome_kind"] == "correct"
    assert rows["team-a-analysis"]["kind"] == "lab" and rows["team-a-analysis"]["outcome_kind"] == "correct"
    assert [rows[f"attempt-{k}"]["outcome_kind"] for k in (1, 2, 3)] == ["correct", "wrong effect", "wrong effect"]
    assert s1["lab"]["grade"]["kind"] == "correct" and s1["baseline"]["attempts"]["attempt-2"]["kind"] == "wrong effect"
    m = measures.measure(t.run_dir)
    assert m["kinds"]["lab_final"] == {"correct": 1}
    assert m["kinds"]["single_attempts"] == {"correct": 1, "wrong effect": 2}
    assert any("correct" in line and "wrong effect" in line for line in measures.summary_lines(m))
