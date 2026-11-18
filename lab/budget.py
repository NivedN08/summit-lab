"""A hard dollar cap for one run directory.

Every agent run reserves its estimated cost before it starts and settles to its actual cost
after it finishes. A run whose reservation would take spent + reserved past the cap is refused
with BudgetExceeded, a kind of UsageLimit: the lab halts the same way it does when the account
runs out of usage, so the study gets no study.final and a later run can resume it.

    budget = Budget(max_usd=20.0, prior_spend=prior_spend(log))
    token = budget.reserve("analyst", "opus")      # raises BudgetExceeded at the cap
    ...run the agent...
    budget.settle(token, result.cost_usd)          # None keeps the estimate

What the cap does not do: it cannot stop an agent already running, so the cap can be passed by
as much as the runs in flight cost beyond their estimates.

ESTIMATE_USD holds one estimate per (role, model). They are the 90th percentile of cost_usd,
rounded up to the next 5 cents, over the agent.finish events in results/*/events.jsonl (the
graded runs of Nov 14-15, 2026; 1,156 runs with a cost). The 90th percentile, not the mean,
because a reservation that is too low lets agents in flight run past the cap. The means are in
the comments, for reference. A pair not measured falls back to the highest estimate for its
role, then to DEFAULT_ESTIMATE_USD.
"""

from __future__ import annotations

import itertools
import threading

from lab.limits import UsageLimit

ESTIMATE_USD: dict[tuple[str, str], float] = {
    # (role, model): estimate        measured: runs, mean, 90th percentile, max
    ("analyst", "fable"): 0.60,      # 166 runs, mean 0.375, p90 0.555, max 0.755
    ("analyst", "opus"): 0.70,       # 179 runs, mean 0.424, p90 0.674, max 1.382
    ("baseline", "fable"): 0.35,     # 256 runs, mean 0.142, p90 0.305, max 0.875
    ("baseline", "opus"): 0.95,      # 106 runs, mean 0.350, p90 0.928, max 2.069
    ("director", "fable"): 0.40,     # 206 runs, mean 0.221, p90 0.377, max 0.597
    ("library", "fable"): 0.50,      # 111 runs, mean 0.352, p90 0.489, max 0.602
    ("library", "opus"): 0.55,       # 45 runs, mean 0.299, p90 0.533, max 1.120
    ("referee", "fable"): 0.40,      # 3 runs only, mean 0.246; set like referee/opus
    ("referee", "opus"): 0.40,       # 17 runs, mean 0.293, p90 0.369, max 0.586
    ("research", "fable"): 0.80,     # 38 runs, mean 0.616, p90 0.765, max 1.716
    ("research", "opus"): 0.85,      # 29 runs, mean 0.642, p90 0.802, max 1.194
}
DEFAULT_ESTIMATE_USD = 1.50          # a role or model never measured (for example a new role, or haiku)

# BioDSA-1K runs cost far less than the GeneBench runs that set several entries above (an Opus single
# attempt averages $0.11 on BioDSA and $0.72 on GeneBench), so the global table makes a BioDSA run's cap
# refuse runs it could afford. These entries replace the global ones for a BioDSA benchmark (dev or
# held-out, with or without the convention note). Measured on the BioDSA runs to 2026-11-18; each is
# about the 90th percentile, rounded up.
BENCHMARK_ESTIMATE_USD: dict[str, dict[tuple[str, str], float]] = {
    "biodsa": {
        # (role, model): estimate        measured on BioDSA: runs, mean, 90th percentile
        ("analyst", "fable"): 0.55,      # 180 runs, mean 0.343, p90 0.546
        ("analyst", "opus"): 0.45,       # 363 runs, mean 0.257, p90 0.437
        ("baseline", "fable"): 0.20,     # 152 runs, mean 0.109, p90 0.170
        ("baseline", "opus"): 0.20,      # 160 runs, mean 0.110, p90 0.196
        ("director", "fable"): 0.40,     # 111 runs, mean 0.219, p90 0.394
        ("director", "opus"): 0.35,      # 199 runs, mean 0.225, p90 0.336
        ("recheck", "opus"): 0.20,       # 40 runs, mean 0.126, p90 0.176
        # the control arms' roles, not yet run live: a single attempt's brief or less, set above baseline
        ("self-solo", "opus"): 0.25,
        ("self-challenge", "opus"): 0.25,
        ("self-revise", "opus"): 0.25,
        ("vote-arm", "opus"): 0.25,
    },
}


def estimates_for(benchmark: str | None) -> dict[tuple[str, str], float]:
    """The estimate table for a benchmark's runs: the global table, with the benchmark's own entries
    (BENCHMARK_ESTIMATE_USD, matched by the adapter name's first word) in place of the global ones."""
    family = (benchmark or "").split("-")[0]
    return {**ESTIMATE_USD, **BENCHMARK_ESTIMATE_USD.get(family, {})}


def estimate_usd(role: str, model: str, table: dict | None = None) -> float:
    """The estimated cost of one run: the (role, model) entry, else the highest for the role, else the default."""
    table = ESTIMATE_USD if table is None else table
    if (role, model) in table:
        return float(table[(role, model)])
    same_role = [v for (r, _), v in table.items() if r == role]
    return float(max(same_role)) if same_role else DEFAULT_ESTIMATE_USD


class BudgetExceeded(UsageLimit):
    """The next agent run would take the run past its dollar cap. The run must stop, not carry on."""


class Budget:
    """Spent and reserved dollars against an optional cap. Safe to share between threads.

    max_usd None means no cap: reservations are still tracked but never refused."""

    def __init__(self, max_usd: float | None, prior_spend: float = 0.0, *, estimates: dict | None = None):
        self.max_usd = None if max_usd is None else float(max_usd)
        self.prior = float(prior_spend or 0.0)
        self.estimates = estimates
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._outstanding: dict[int, float] = {}   # token -> reserved estimate
        self._settled = 0.0                         # actual costs, and estimates kept for runs with no cost
        self.estimated_runs = 0                     # settled runs that reported no cost

    def reserve(self, role: str, model: str) -> int:
        """Reserve one run's estimate. Raises BudgetExceeded if it would pass the cap. Returns a token."""
        estimate = estimate_usd(role, model, self.estimates)
        with self._lock:
            spent, reserved = self.prior + self._settled, sum(self._outstanding.values())
            if self.max_usd is not None and spent + reserved + estimate > self.max_usd + 1e-9:
                raise BudgetExceeded(
                    f"budget: ${spent:.2f} spent and ${reserved:.2f} reserved; the next {role} run on {model} is "
                    f"estimated at ${estimate:.2f}, which would pass the cap of ${self.max_usd:.2f}")
            token = next(self._ids)
            self._outstanding[token] = estimate
            return token

    def settle(self, token: int, cost_usd: float | None) -> None:
        """Replace a reservation with the run's actual cost. None keeps the estimate and marks the
        run as estimated. Settling a token twice, or an unknown one, does nothing."""
        with self._lock:
            estimate = self._outstanding.pop(token, None)
            if estimate is None:
                return
            if cost_usd is None:
                self._settled += estimate
                self.estimated_runs += 1
            else:
                self._settled += float(cost_usd)

    def spent(self) -> float:
        """Dollars spent: prior spend plus every settled run (estimates for runs with no cost)."""
        with self._lock:
            return self.prior + self._settled

    def reserved(self) -> float:
        """Dollars reserved by runs that have not settled yet."""
        with self._lock:
            return sum(self._outstanding.values())

    @property
    def estimated(self) -> bool:
        """True when some settled run reported no cost, so spent() includes estimates."""
        return self.estimated_runs > 0


def prior_spend(log, estimates: dict | None = None) -> float:
    """What a run directory has already spent, read back from its event log for a resumed run:
    the cost_usd of every agent.finish (its estimate when the cost is missing), plus the estimate
    of every agent.start that never finished (a crashed or interrupted run may still have cost money)."""
    starts = {e.get("run_id"): e for e in log.events("agent.start")}
    total, finished = 0.0, set()
    for e in log.events("agent.finish"):
        finished.add(e.get("run_id"))
        cost = e.get("cost_usd")
        if cost is None:
            model = (starts.get(e.get("run_id")) or {}).get("model") or ""
            cost = estimate_usd(str(e.get("role") or ""), str(model), estimates)
        total += float(cost)
    for run_id, e in starts.items():
        if run_id not in finished:
            total += estimate_usd(str(e.get("role") or ""), str(e.get("model") or ""), estimates)
    return total
