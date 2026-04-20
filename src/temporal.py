"""Temporal reasoning — open hours, active windows, bonuses.

Handles the NYC quirk where bars stay open past midnight. Hours are
expressed as [open, close] where `close` > 24:00 means "next calendar
day" (e.g., "26:00" = 2am next day).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .models import Bar, TemporalWindow


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def day_name(dt: datetime) -> str:
    """Python weekday(): Mon=0..Sun=6."""
    return DAYS[dt.weekday()]


def _hour_of(s: str) -> float:
    """Parse 'HH:MM' to float hours (supports HH > 24 for past-midnight)."""
    h, m = s.split(":")
    return int(h) + int(m) / 60.0


def _prev_day(day: str) -> str:
    i = DAYS.index(day)
    return DAYS[(i - 1) % 7]


def is_open(bar: Bar, dt: datetime) -> bool:
    """True if `bar` is open at datetime `dt`."""
    hour = dt.hour + dt.minute / 60.0
    day = day_name(dt)
    hours = bar.open_hours.get(day)
    if hours:
        open_h, close_h = _hour_of(hours[0]), _hour_of(hours[1])
        if open_h <= hour < min(close_h, 24.0):
            return True
    # Also handle: previous day's hours extending past midnight
    prev = bar.open_hours.get(_prev_day(day))
    if prev:
        open_h, close_h = _hour_of(prev[0]), _hour_of(prev[1])
        if close_h > 24.0 and hour < (close_h - 24.0):
            return True
    return False


def _window_active(window_start: str, window_end: str, window_days: tuple[str, ...],
                   dt: datetime) -> bool:
    day = day_name(dt)
    hour = dt.hour + dt.minute / 60.0
    s, e = _hour_of(window_start), _hour_of(window_end)

    # Same-day check
    if day in window_days and s <= hour < min(e, 24.0):
        return True
    # Past-midnight carryover from previous day
    prev = _prev_day(day)
    if prev in window_days and e > 24.0 and hour < (e - 24.0) and hour >= 0:
        return True
    return False


def active_windows(bar: Bar, dt: datetime) -> list[TemporalWindow]:
    """All happy_hour + special windows active at `dt`."""
    active: list[TemporalWindow] = []
    for w in bar.happy_hour_windows:
        if _window_active(w.start, w.end, w.days, dt):
            active.append(w)
    for w in bar.specials:
        if _window_active(w.start, w.end, w.days, dt):
            active.append(w)
    return active


def temporal_bonus(bar: Bar, arrival_dt: datetime, rules: dict,
                   user_budget_weight: float = 0.0,
                   user_wants_food: bool = False
                   ) -> tuple[float, list[TemporalWindow]]:
    """Additive bonus for active windows at arrival.

    Happy-hour bonuses scale with the user's budget weight (BUILD_PLAN §4.4):
    a budget-sensitive group values cheap-drink windows more.
    """
    active = active_windows(bar, arrival_dt)
    if not active:
        return 0.0, []

    cfg = rules.get("temporal_bonuses", {})
    hh_cfg = cfg.get("happy_hour", {})
    sp_cfg = cfg.get("specials", {})
    bonus = 0.0

    for w in active:
        if w.kind == "happy_hour":
            base = hh_cfg.get("base_bonus", w.bonus or 0.2)
            mult = 1.0 + hh_cfg.get("budget_weight_multiplier", 1.5) * user_budget_weight
            bonus += base * mult
        else:
            base = sp_cfg.get("base_bonus", w.bonus or 0.12)
            kind_mult = sp_cfg.get("kind_multipliers", {}).get(w.kind, 1.0)
            bonus += base * kind_mult

    if user_wants_food and bar.kitchen_open:
        # If kitchen is still open at arrival, apply the food bonus
        kitchen_bonus = cfg.get("kitchen_open", {}).get("base_bonus", 0.05)
        bonus += kitchen_bonus

    return bonus, active


def earliest_arrival_to_catch(window: TemporalWindow, at_bar: Bar,
                              after: datetime) -> Optional[datetime]:
    """Smallest datetime >= `after` that falls inside `window` on any of its days.
    Returns None if no such date in the next 7 days.
    """
    for offset in range(8):
        candidate = after + timedelta(days=offset)
        target_day = day_name(candidate)
        if target_day not in window.days:
            continue
        s_hour = _hour_of(window.start)
        # Construct datetime on `candidate.date()` at s_hour
        start_dt = candidate.replace(hour=int(s_hour), minute=int((s_hour % 1) * 60),
                                     second=0, microsecond=0)
        if offset == 0 and start_dt < after:
            # window has already started today; is it still active?
            if _window_active(window.start, window.end, window.days, after):
                return after
            continue
        return start_dt
    return None
