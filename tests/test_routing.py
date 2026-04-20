"""Tests for routing.py."""

from datetime import datetime

import pytest

from src.data_loader import load_bars, load_rules
from src.models import AccessibilityNeeds, GroupInput, UserPreference
from src.routing import (
    walking_miles, greedy_route, two_opt_improve, enumerate_exact, best_route,
    _recompute_schedule,
)


NYC_TIMES_SQUARE = (40.7580, -73.9855)
NYC_UNION_SQUARE = (40.7359, -73.9911)


def test_walking_miles_known_distance():
    """Times Square to Union Square is ~1.6 miles by road, ~1.55 by crow."""
    miles = walking_miles(NYC_TIMES_SQUARE, NYC_UNION_SQUARE)
    assert 1.3 <= miles <= 1.9, f"expected ~1.5 miles, got {miles}"


def test_walking_miles_symmetric():
    a = (40.7, -73.99)
    b = (40.72, -73.97)
    assert abs(walking_miles(a, b) - walking_miles(b, a)) < 1e-9


def test_walking_miles_zero_for_identical_point():
    assert walking_miles((40.7, -73.99), (40.7, -73.99)) < 1e-6


def _simple_group() -> tuple[GroupInput, list]:
    bars = load_bars()
    users = [UserPreference(name="Alice",
                             vibe_weights={"intimate": 1.0, "conversation": 0.8},
                             max_per_drink=15.0)]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 18, 0),  # Fri 6pm
        end_time=datetime(2026, 4, 24, 23, 30),
        start_location=(40.7265, -73.9815),  # East Village
        max_stops=3,
    )
    return group, bars


def test_greedy_produces_feasible_route():
    group, bars = _simple_group()
    rules = load_rules()
    # Pick 8 highest quality bars as candidates to keep tests fast
    candidates = sorted(bars, key=lambda b: -b.quality_signal)[:20]
    scores = {b.id: b.quality_signal for b in candidates}
    steps, log = greedy_route(candidates, scores, group, rules)
    assert steps, "greedy should find at least one stop"
    # All arrivals are monotonic
    for i in range(1, len(steps)):
        assert steps[i].arrival > steps[i - 1].arrival
    # All arrivals inside the window
    for s in steps:
        assert group.start_time <= s.arrival < group.end_time


def test_two_opt_does_not_worsen_greedy():
    group, bars = _simple_group()
    rules = load_rules()
    candidates = sorted(bars, key=lambda b: -b.quality_signal)[:12]
    scores = {b.id: b.quality_signal for b in candidates}
    greedy_steps, _ = greedy_route(candidates, scores, group, rules)
    if len(greedy_steps) < 2:
        pytest.skip("need 2+ stops")
    greedy_total = sum(s.utility + s.bonus for s in greedy_steps)
    improved, _ = two_opt_improve(greedy_steps, scores, group, rules)
    improved_total = sum(s.utility + s.bonus for s in improved)
    assert improved_total >= greedy_total - 1e-6, \
        f"2-opt made things worse: {greedy_total} → {improved_total}"


def test_exact_enumeration_matches_or_beats_greedy():
    group, bars = _simple_group()
    rules = load_rules()
    # Constrain to a small candidate set so exact enumeration is cheap
    candidates = sorted(bars, key=lambda b: -b.quality_signal)[:5]
    scores = {b.id: b.quality_signal for b in candidates}
    greedy_steps, _ = greedy_route(candidates, scores, group, rules)
    if not greedy_steps:
        pytest.skip("no feasible route")
    # Compute greedy's true objective (with walking penalty) using the same helper
    _, greedy_total = _recompute_schedule([s.bar for s in greedy_steps],
                                          scores, group, rules)
    exact, exact_total, perms = enumerate_exact(candidates, scores, group, rules)
    assert exact_total >= greedy_total - 1e-6, \
        f"exact ({exact_total}) should match/beat greedy ({greedy_total})"
    assert perms > 0


def test_best_route_end_to_end():
    group, bars = _simple_group()
    rules = load_rules()
    candidates = sorted(bars, key=lambda b: -b.quality_signal)[:15]
    scores = {b.id: b.quality_signal for b in candidates}
    route = best_route(candidates, scores, group, rules,
                       strategy_used="utilitarian_sum",
                       strategy_rationale="Aligned group; maximize total utility.")
    assert route.stops
    assert route.total_utility > 0
    assert route.search_log  # non-empty log


def test_best_route_infeasibly_tight_window_returns_empty():
    users = [UserPreference(name="Alice")]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 3, 0),  # 3am — most bars closed
        end_time=datetime(2026, 4, 24, 3, 15),   # 15 min only
        start_location=(40.7265, -73.9815),
        max_stops=3,
    )
    bars = load_bars()
    rules = load_rules()
    candidates = bars[:20]
    scores = {b.id: b.quality_signal for b in candidates}
    route = best_route(candidates, scores, group, rules)
    assert route.is_empty or len(route.stops) == 0
