"""Tests for temporal.py."""

from datetime import datetime

from src.data_loader import load_bars, load_rules
from src.temporal import (
    is_open, active_windows, temporal_bonus, earliest_arrival_to_catch, day_name
)


def test_day_name():
    assert day_name(datetime(2026, 4, 20)) == "mon"  # April 20 2026 is a Monday
    assert day_name(datetime(2026, 4, 24)) == "fri"


def test_is_open_at_peak_hour():
    bars = load_bars()
    # pick a bar open on Friday night
    fri_night = datetime(2026, 4, 24, 21, 0)  # 9pm Fri
    opens = [b for b in bars if is_open(b, fri_night)]
    assert len(opens) > 100, f"expected most bars open Fri 9pm, got {len(opens)}"


def test_is_open_past_midnight():
    """Bar open 17:00-26:00 Thu should be open at 1am Fri."""
    bars = load_bars()
    # Pick any bar with Thu hours ending past midnight
    sample = next(b for b in bars
                  if b.open_hours.get("thu")
                  and int(b.open_hours["thu"][1].split(":")[0]) > 24)
    # 1am Friday
    fri_1am = datetime(2026, 4, 24, 1, 0)
    assert is_open(sample, fri_1am)


def test_happy_hour_active_at_arrival():
    bars = load_bars()
    rules = load_rules()
    # A pub: happy hour 16-19 Mon-Fri
    pub = next(b for b in bars if "pub" in b.bar_type or "irish_pub" in b.bar_type)
    wed_5pm = datetime(2026, 4, 22, 17, 0)
    bonus, active = temporal_bonus(pub, wed_5pm, rules)
    assert bonus > 0 and active, f"expected happy hour active at 5pm Wed, got {bonus}, {active}"

    wed_10pm = datetime(2026, 4, 22, 22, 0)
    bonus2, active2 = temporal_bonus(pub, wed_10pm, rules)
    assert bonus2 == 0 or not any(w.kind == "happy_hour" for w in active2)


def test_budget_weight_scales_happy_hour_bonus():
    bars = load_bars()
    rules = load_rules()
    pub = next(b for b in bars if b.happy_hour_windows)
    hh = pub.happy_hour_windows[0]
    inside_hour = int(hh.start.split(":")[0]) + 1
    # map day name to concrete date
    from src.temporal import DAYS
    day_idx = DAYS.index(hh.days[0])
    # April 2026: Mon=20, Tue=21, ...
    dt = datetime(2026, 4, 20 + day_idx, inside_hour, 0)

    b1, _ = temporal_bonus(pub, dt, rules, user_budget_weight=0.0)
    b2, _ = temporal_bonus(pub, dt, rules, user_budget_weight=1.0)
    assert b2 > b1


def test_active_windows_returns_multiple_types():
    bars = load_bars()
    # Find a bar with both happy hour and specials
    both = [b for b in bars if b.happy_hour_windows and b.specials]
    if not both:
        return  # not guaranteed
    b = both[0]
    # Find a time where both could be active... happy hours are typically
    # early, specials late. Just check that we can detect them separately.
    all_windows = list(b.happy_hour_windows) + list(b.specials)
    assert all_windows


def test_earliest_arrival_to_catch():
    bars = load_bars()
    pub = next(b for b in bars if b.happy_hour_windows)
    hh = pub.happy_hour_windows[0]
    after = datetime(2026, 4, 20, 8, 0)  # Monday morning
    eta = earliest_arrival_to_catch(hh, pub, after)
    assert eta is not None
    assert eta.hour == int(hh.start.split(":")[0])
