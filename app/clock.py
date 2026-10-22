"""Simulated clock module for TickTock Meals.

Centralizes all datetime operations to allow fast-forwarding, time travel in demos,
and deterministic testing without touching real system time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


class SimClock:
    """Simulated timekeeper supporting offsets, manual jumps, and frozen timestamps."""

    def __init__(self, initial_time: Optional[datetime] = None) -> None:
        self._initial_time: datetime = initial_time or datetime.now()
        self._current_time: datetime = self._initial_time
        self._is_fixed: bool = initial_time is not None

    def now(self) -> datetime:
        """Return the current simulated datetime."""
        return self._current_time

    def advance(self, minutes: float) -> datetime:
        """Advance the simulated clock forward by the given number of minutes."""
        self._current_time = self._current_time + timedelta(minutes=minutes)
        return self._current_time

    def set_fixed(self, dt: datetime) -> None:
        """Set the clock explicitly to a specific datetime (useful for tests)."""
        self._current_time = dt
        self._is_fixed = True

    def reset(self) -> datetime:
        """Reset the simulated clock to real system datetime."""
        self._current_time = datetime.now()
        self._initial_time = self._current_time
        self._is_fixed = False
        return self._current_time


# Default application-wide singleton instance
_GLOBAL_CLOCK = SimClock()


def now() -> datetime:
    """Return the current simulated time from the global clock."""
    return _GLOBAL_CLOCK.now()


def advance(minutes: float) -> datetime:
    """Advance the global clock forward by minutes."""
    return _GLOBAL_CLOCK.advance(minutes)


def set_fixed(dt: datetime) -> None:
    """Set the global clock to a fixed datetime."""
    _GLOBAL_CLOCK.set_fixed(dt)


def reset() -> datetime:
    """Reset the global clock to system time."""
    return _GLOBAL_CLOCK.reset()
