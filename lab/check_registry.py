"""Named code-level checks that the improvement loop's `add_check` move can switch on.

A check is on when its id is in `Config.checks`. Each one is wired where it acts, in lab/protocol.py
(and lab/arms.py for the guard), and does nothing when its id is absent, so a Config without checks
behaves exactly as before. The registry stays small and explicit: each entry names its id, what it
does, and the Config switch it works with.

  recheck_on_agreed_nv    Re-check an answer of "Not verifiable" when BOTH teams gave it, even with
                          Config.recheck off. With Config.recheck on, the lab already re-checks every
                          team that abstains, agreed or not (StudyRun.rechecked), so the check then
                          adds nothing; `has_effect` says so and the loop refuses it as a move.
  guard_strict_decimals   In the damage guard (check_evidence), every decimal number quoted in
                          evidence.json's `result` must show at least 3 significant digits
                          (0.0123, not 0.01). Acts only with Config.guard on.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Check:
    id: str
    description: str
    config: str               # the Config switch the check works with
    needs_config_on: bool     # True: the check acts only when that switch is on; False: only when it is off


RECHECK_ON_AGREED_NV = "recheck_on_agreed_nv"
GUARD_STRICT_DECIMALS = "guard_strict_decimals"
STRICT_MIN_DIGITS = 3

CHECKS: dict[str, Check] = {c.id: c for c in (
    Check(RECHECK_ON_AGREED_NV,
          'Re-check "Not verifiable" when both teams agree on it, even with the re-check switch off.',
          config="recheck", needs_config_on=False),
    Check(GUARD_STRICT_DECIMALS,
          f"The damage guard requires at least {STRICT_MIN_DIGITS} significant digits on every decimal "
          "number quoted as evidence.",
          config="guard", needs_config_on=True),
)}


def unknown(ids) -> list[str]:
    """The ids that are not in the registry."""
    return sorted(str(i) for i in ids if i not in CHECKS)


def normalize(ids) -> tuple:
    """Config.checks as stored: a sorted tuple of unique registry ids. Raises ValueError on an unknown id."""
    ids = tuple(sorted(set(ids or ())))
    bad = unknown(ids)
    if bad:
        raise ValueError(f"unknown check(s) {', '.join(bad)}; known: {', '.join(sorted(CHECKS))}")
    return ids


def is_on(cfg, check_id: str) -> bool:
    """Whether a Config (or anything with a `checks` attribute) has the check switched on."""
    return check_id in (getattr(cfg, "checks", None) or ())


def has_effect(check_id: str, settings: dict) -> bool:
    """Whether switching the check on would change anything, given the Config values in `settings`."""
    check = CHECKS[check_id]
    return bool(settings.get(check.config)) == check.needs_config_on
