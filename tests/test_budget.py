"""Tests for lab.budget: the hard dollar cap, its wiring into AgentRunner, Lab and run_baselines, and --max-usd.

No model runs: FakeBackend from test_protocol.py reports $0.25 a run.
"""

import json
import threading

import pytest

from lab import baseline as baseline_mod
from lab import protocol, run
from lab.budget import BENCHMARK_ESTIMATE_USD, Budget, BudgetExceeded, ESTIMATE_USD, estimate_usd, estimates_for, prior_spend
from lab.contracts import WORKER, Agent, AgentResult
from lab.eventlog import JsonlEventLog
from lab.limits import UsageLimit
from test_protocol import A, FakeAdapter, make, one_study

FLAT = {("analyst", "opus"): 0.5}     # one estimate for every test run, so the arithmetic is plain


class CostBackend:
    """Every run succeeds and reports `cost` dollars (None: no cost reported)."""

    def __init__(self, cost):
        self.cost = cost
        self.calls = 0

    def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800):
        self.calls += 1
        return AgentResult(ok=True, result={}, seconds=0.01, cost_usd=self.cost)


def runner_for(tmp_path, backend, budget):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    return protocol.AgentRunner(backend, log, max_agents=2, budget=budget), log


def run_once(runner, tmp_path, name="a"):
    agent = Agent("s1-team-a", WORKER, "s1-team-a", "opus")
    return runner.run(agent, "brief", tmp_path / name, readable=[], env=None, timeout_s=10, study="s1",
                      phase="analysis", role="analyst")


# ----- the Budget itself -----

def test_reserve_refuses_at_the_cap_and_counts_reservations():
    budget = Budget(1.0, estimates=FLAT)
    first = budget.reserve("analyst", "opus")
    budget.reserve("analyst", "opus")               # 0.5 + 0.5 = 1.0: exactly at the cap is allowed
    with pytest.raises(BudgetExceeded) as err:
        budget.reserve("analyst", "opus")
    assert isinstance(err.value, UsageLimit) and "cap of $1.00" in str(err.value)
    budget.settle(first, 0.0)                       # a free run frees its whole reservation
    budget.reserve("analyst", "opus")
    assert budget.spent() == 0 and budget.reserved() == pytest.approx(1.0)


def test_settle_reconciles_estimates_with_actual_costs():
    budget = Budget(10.0, estimates=FLAT)
    tokens = [budget.reserve("analyst", "opus") for _ in range(3)]
    for token, cost in zip(tokens, (0.2, 0.9, 0.05)):
        budget.settle(token, cost)
    assert budget.spent() == pytest.approx(1.15) and budget.reserved() == 0
    assert not budget.estimated
    budget.settle(tokens[0], 5.0)                   # settling twice does nothing
    assert budget.spent() == pytest.approx(1.15)


def test_a_missing_cost_keeps_its_estimate():
    budget = Budget(10.0, estimates=FLAT)
    budget.settle(budget.reserve("analyst", "opus"), None)
    budget.settle(budget.reserve("analyst", "opus"), 0.3)
    assert budget.spent() == pytest.approx(0.8) and budget.estimated and budget.estimated_runs == 1


def test_no_cap_tracks_but_never_refuses():
    budget = Budget(None, estimates=FLAT)
    for _ in range(50):
        budget.reserve("analyst", "opus")
    assert budget.reserved() == pytest.approx(25.0)


def test_reservations_are_thread_safe():
    budget = Budget(10.0, estimates=FLAT)        # room for exactly 20 reservations
    got, refused = [], []

    def worker():
        for _ in range(10):
            try:
                got.append(budget.reserve("analyst", "opus"))
            except BudgetExceeded:
                refused.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 20 and len(refused) == 60 and len(set(got)) == 20


def test_estimates_fall_back_by_role_then_default():
    assert estimate_usd("analyst", "opus") == ESTIMATE_USD[("analyst", "opus")]
    assert estimate_usd("director", "opus") == ESTIMATE_USD[("director", "fable")]   # only role measured
    assert estimate_usd("new-role", "haiku") == 1.50
    assert all(0 < v <= 1.0 for v in ESTIMATE_USD.values())


# ----- AgentRunner -----

def test_a_run_refuses_at_the_cap_and_halts_like_a_usage_limit(tmp_path):
    backend = CostBackend(0.25)
    runner, log = runner_for(tmp_path, backend, Budget(1.0, estimates=FLAT))
    run_once(runner, tmp_path, "a")          # reserve 0.5, settle 0.25
    run_once(runner, tmp_path, "b")          # 0.25 + 0.5 <= 1.0; settle: 0.5 spent
    run_once(runner, tmp_path, "c")          # 0.5 + 0.5 <= 1.0; settle: 0.75 spent
    with pytest.raises(BudgetExceeded):
        run_once(runner, tmp_path, "d")      # 0.75 + 0.5 > 1.0
    with pytest.raises(BudgetExceeded):
        run_once(runner, tmp_path, "e")      # halted: refused without asking the budget again
    assert backend.calls == 3
    assert len(log.events("agent.start")) == len(log.events("agent.finish")) == 3
    halted = log.events("lab.halted")
    assert len(halted) == 1 and halted[0]["reason"] == "budget" and halted[0]["max_usd"] == 1.0
    assert halted[0]["spent_usd"] == pytest.approx(0.75)
    assert runner.budget.spent() == pytest.approx(0.75)


def test_runner_settles_a_missing_cost_at_its_estimate(tmp_path):
    runner, log = runner_for(tmp_path, CostBackend(None), Budget(10.0, estimates=FLAT))
    run_once(runner, tmp_path)
    assert log.events("agent.finish")[0]["cost_usd"] is None
    assert runner.budget.spent() == pytest.approx(0.5) and runner.budget.estimated


def test_prior_spend_reads_costs_back_from_the_event_log(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    log.append("agent.start", "x", run_id="r1", role="analyst", model="opus")
    log.append("agent.finish", "x", run_id="r1", role="analyst", cost_usd=0.9)
    log.append("agent.start", "x", run_id="r2", role="analyst", model="opus")
    log.append("agent.finish", "x", run_id="r2", role="analyst", cost_usd=None)     # counted at its estimate
    log.append("agent.start", "x", run_id="r3", role="analyst", model="opus")       # never finished: estimate
    assert prior_spend(log, FLAT) == pytest.approx(0.9 + 0.5 + 0.5)
    budget = Budget(2.0, prior_spend(log, FLAT), estimates=FLAT)
    with pytest.raises(BudgetExceeded):
        budget.reserve("analyst", "opus")       # 1.9 already spent


# ----- the lab: halt, then resume with a higher cap -----

def test_a_budget_halt_leaves_no_final_and_a_higher_cap_resumes(tmp_path):
    plan = {"team-a": [A], "team-b": [A]}
    lab, adapter, backend, log, memory, run_dir = make(tmp_path, plan, protocol.Config(max_agents=3, max_usd=1.0))
    out = lab.run(one_study(adapter))
    assert out["s1"]["status"] == "error" and "budget" in out["s1"]["error"]
    assert not log.events("study.final") and not (run_dir / "s1" / "study.json").exists()
    halted = log.events("lab.halted")
    assert [e["reason"] for e in halted] == ["budget"]
    assert len(log.events("agent.start")) == len(log.events("agent.finish"))   # nothing left running
    spent_before = sum(e["cost_usd"] for e in log.events("agent.finish"))
    assert 0 < spent_before <= 1.0

    lab2, _, backend2, log2, _, _ = make(tmp_path, plan, protocol.Config(max_agents=3, max_usd=50.0))
    assert lab2.runner.budget.spent() == pytest.approx(spent_before)    # what the first run spent counts
    out2 = lab2.run(one_study(adapter))
    assert out2["s1"]["status"] == "replicated"
    assert [e["study"] for e in log2.events("study.final")] == ["s1"]
    assert lab2.runner.budget.spent() == pytest.approx(sum(e["cost_usd"] for e in log2.events("agent.finish")))


def test_no_max_usd_means_no_budget(tmp_path):
    lab, *_ = make(tmp_path, {"team-a": [A], "team-b": [A]})
    assert lab.runner.budget is None


def test_baselines_stop_at_the_cap_and_resume(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench")
    log = JsonlEventLog(tmp_path / "run" / "events.jsonl")
    backend = CostBackend(0.25)
    # Fable baseline estimate 0.35: 0 -> 0.25 -> 0.5 -> 0.75; the fourth needs 0.75 + 0.35 > 1.0
    with pytest.raises(BudgetExceeded):
        baseline_mod.run_baselines(adapter, backend, log, tmp_path / "run", adapter.studies(), attempts=2,
                                   model="fable", max_agents=1, max_usd=1.0)
    assert backend.calls == 3 and len(log.events("baseline.done")) == 3
    out = baseline_mod.run_baselines(adapter, backend, log, tmp_path / "run", adapter.studies(), attempts=2,
                                     model="fable", max_agents=1, max_usd=5.0)
    assert sum(len(v) for v in out.values()) == 1 and len(log.events("baseline.done")) == 4


# ----- per-benchmark estimates -----

@pytest.mark.parametrize("name", ["biodsa-1k", "biodsa-1k-convention", "biodsa-heldout", "biodsa-heldout-convention"])
def test_every_biodsa_split_uses_the_biodsa_estimates(name):
    table = estimates_for(name)
    assert estimate_usd("baseline", "opus", table) == BENCHMARK_ESTIMATE_USD["biodsa"][("baseline", "opus")]
    assert estimate_usd("vote-arm", "opus", table) < estimate_usd("vote-arm", "opus")   # no $1.50 fallback
    assert estimate_usd("library", "opus", table) == ESTIMATE_USD[("library", "opus")]   # unmeasured on BioDSA: global


@pytest.mark.parametrize("name", [None, "genebench-pro", "scbench", "planted", "fake"])
def test_other_benchmarks_keep_the_global_table(name):
    assert estimates_for(name) == ESTIMATE_USD


def test_the_biodsa_table_lets_a_loop2_sized_cap_run_eight_at_once():
    # loop 2's Opus-alone run: $7.17 spent, cap $14, 8 attempts in flight. The global estimate ($0.95)
    # refused the eighth; the BioDSA estimate fits all eight.
    plain, biodsa = Budget(14.0, 7.17), Budget(14.0, 7.17, estimates=estimates_for("biodsa-1k-convention"))
    for _ in range(7):
        plain.reserve("baseline", "opus")
    with pytest.raises(BudgetExceeded):
        plain.reserve("baseline", "opus")
    for _ in range(8):
        biodsa.reserve("baseline", "opus")


def test_baselines_lab_and_arms_pass_the_benchmark_to_the_budget(tmp_path):
    from lab import arms
    adapter = FakeAdapter(tmp_path / "bench")
    adapter.name = "biodsa-heldout-convention"
    log = JsonlEventLog(tmp_path / "run" / "events.jsonl")
    backend = CostBackend(0.15)
    # BioDSA Opus baseline estimate 0.20: 0 -> 0.15 -> 0.30 -> 0.45 fit a $0.65 cap; the global 0.95 fits none
    out = baseline_mod.run_baselines(adapter, backend, log, tmp_path / "run", adapter.studies(), attempts=2,
                                     model="opus", max_agents=1, max_usd=0.65)
    assert backend.calls >= 3 and sum(len(v) for v in out.values()) == backend.calls
    runner = arms.make_runner(backend, log, 1, 5.0, adapter.name)
    assert runner.budget.estimates == estimates_for("biodsa-heldout-convention")
    lab, lab_adapter, *_ = make(tmp_path / "lab", {"team-a": [A], "team-b": [A]}, protocol.Config(max_usd=5.0))
    assert lab.runner.budget.estimates == estimates_for(getattr(lab_adapter, "name", None))


# ----- the command line -----

@pytest.mark.parametrize("command", ["lab", "baseline"])
@pytest.mark.parametrize("backend", ["claude", "modal"])
def test_max_usd_is_required_for_paid_backends(tmp_path, monkeypatch, command, backend):
    adapter = FakeAdapter(tmp_path / "bench")
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda: adapter)
    run_dir = tmp_path / "r"
    args = [command, "--benchmark", "fake", "--run-dir", str(run_dir), "--backend", backend]
    if command == "lab":
        args += ["--memory", "past"]
    with pytest.raises(SystemExit) as err:
        run.main(args)
    assert "--max-usd" in str(err.value)
    assert not run_dir.exists()          # stopped before anything was written


def test_max_usd_reaches_the_lab_config_and_the_baselines(tmp_path, monkeypatch):
    adapter = FakeAdapter(tmp_path / "bench")
    seen = {}

    class StubLab:
        def __init__(self, adapter, backend, memory, log, run_dir, config, **kwargs):
            seen["config"] = config
            self.runner = protocol.AgentRunner(backend, log, 1)

        def run(self, studies):
            return {}

    def stub_baselines(*args, **kwargs):
        seen["baseline"] = kwargs
        return {}

    monkeypatch.setitem(run.ADAPTERS, "fake", lambda: adapter)
    monkeypatch.setattr(run, "Lab", StubLab)
    monkeypatch.setattr(baseline_mod, "run_baselines", stub_baselines)
    base = ["--benchmark", "fake", "--backend", "mock"]
    assert run.main(["lab", *base, "--run-dir", str(tmp_path / "r1"), "--max-usd", "12.5"]) == 0
    assert seen["config"].max_usd == 12.5
    assert run.main(["lab", *base, "--run-dir", str(tmp_path / "r2")]) == 0     # optional for mock
    assert seen["config"].max_usd is None
    assert run.main(["baseline", *base, "--run-dir", str(tmp_path / "r3"), "--max-usd", "3"]) == 0
    assert seen["baseline"]["max_usd"] == 3.0
    with pytest.raises(SystemExit):
        run.main(["lab", *base, "--run-dir", str(tmp_path / "r4"), "--max-usd", "0"])


def test_cli_lab_halted_by_the_cap_exits_1_and_resumes(tmp_path, monkeypatch, capsys):
    adapter = FakeAdapter(tmp_path / "bench")
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda: adapter)
    run_dir = tmp_path / "r"
    args = ["lab", "--benchmark", "fake", "--studies", "s1", "--backend", "mock", "--run-dir", str(run_dir)]
    assert run.main(args + ["--max-usd", "1.0"]) == 1           # mock runs report no cost: estimates count
    assert "halted: budget" in capsys.readouterr().err
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert not [e for e in events if e["kind"] == "study.final"]
    assert run.main(args + ["--max-usd", "100"]) == 0
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert [e["status"] for e in events if e["kind"] == "study.final"] == ["converged"]


def test_default_flags_give_the_default_config():
    """The golden-brief tests (test_scbench_wiring.py) build their Config from the default flags; this ties
    them to Config() itself, so adding max_usd (default None) cannot change a default brief."""
    args = run.build_parser().parse_args(["lab", "--benchmark", "genebench", "--run-dir", "unused"])
    assert run.make_config(args) == protocol.Config()
    assert protocol.Config().max_usd is None
