"""Watchdog (Phase 6) — recovery when the screen can't be identified.

Counts consecutive UNKNOWN frames; after ``unknown_limit`` it triggers a recovery
attempt (e.g. press BACK) up to ``max_recovery_attempts`` times before giving up.
Any recognised screen resets the counters.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Watchdog:
    unknown_limit: int
    max_recovery_attempts: int
    _unknown_streak: int = 0
    _recovery_attempts: int = 0

    def on_known(self) -> None:
        """A screen was identified — reset the UNKNOWN streak and attempts."""
        self._unknown_streak = 0
        self._recovery_attempts = 0

    def on_unknown(self) -> str:
        """Register an UNKNOWN frame. Returns one of: 'wait', 'recover', 'giveup'."""
        self._unknown_streak += 1
        if self._unknown_streak < self.unknown_limit:
            return "wait"
        if self._recovery_attempts >= self.max_recovery_attempts:
            return "giveup"
        self._recovery_attempts += 1
        self._unknown_streak = 0  # give recovery a fresh window to take effect
        return "recover"
