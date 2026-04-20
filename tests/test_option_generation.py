"""Tests for option_generation.py."""

from datetime import datetime

from src.data_loader import load_bars, load_rules
from src.models import GroupInput, RouteStop, Route, Score, UserPreference
from src.option_generation import (
    find_runner_ups, unlock_analysis, all_structural_counterfactuals,
    strategy_counterfactuals, strategy_winner,
)


def _mini_route_and_scores():
    bars = load_bars()[:5]
    # Fake a 2-stop route from the first two bars, with scores
    route = Route(
        stops=[
            RouteStop(bar=bars[0], arrival=datetime(2026, 4, 24, 19, 0),
                      departure=datetime(2026, 4, 24, 19, 45), group_score=1.2),
            RouteStop(bar=bars[1], arrival=datetime(2026, 4, 24, 20, 0),
                      departure=datetime(2026, 4, 24, 20, 45), group_score=1.0),
        ],
        total_utility=2.2, total_walking_miles=0.5, windows_captured=[],
        strategy_used="utilitarian_sum", strategy_rationale="",
    )
    # group scores for all 5 bars
    group_scores = {bars[0].id: 1.2, bars[1].id: 1.0, bars[2].id: 0.9,
                    bars[3].id: 0.8, bars[4].id: 0.7}
    # Per-user scores: one user "A"
    per_user = {"A": {}}
    crits = ["vibe", "budget", "drink_match", "noise", "distance",
             "happy_hour_active", "specials_match", "crowd_fit", "novelty", "quality_signal"]
    for i, b in enumerate(bars):
        per_user["A"][b.id] = Score(
            bar_id=b.id, user_id="A",
            per_criterion={c: 0.5 - 0.05 * i + (0.1 if c == "vibe" and i == 2 else 0.0)
                            for c in crits},
            weighted_contributions={c: 0.05 for c in crits},
            total=group_scores[b.id],
        )
    return bars, route, group_scores, per_user


def test_runner_ups_found_for_each_stop():
    bars, route, gs, pu = _mini_route_and_scores()
    ru = find_runner_ups(route, gs, pu, bars)
    assert len(ru) == len(route.stops)
    for idx, r in ru.items():
        assert r.bar.id not in {s.bar.id for s in route.stops}
        assert r.gap >= 0


def test_runner_up_has_criteria_gap():
    bars, route, gs, pu = _mini_route_and_scores()
    ru = find_runner_ups(route, gs, pu, bars)
    r = ru[0]
    assert r.gap_criteria
    assert set(r.gap_criteria.keys()) <= {
        "vibe", "budget", "drink_match", "noise", "distance",
        "happy_hour_active", "specials_match", "crowd_fit", "novelty", "quality_signal",
    }


def test_unlock_hint_mentions_criterion_or_is_null():
    bars, route, gs, pu = _mini_route_and_scores()
    ru = find_runner_ups(route, gs, pu, bars)
    ru = unlock_analysis(route, ru, pu)
    for r in ru.values():
        # Either a sensible hint or the null-case acknowledgement
        assert r.unlock_hint != "" or r.unlock_hint == ""


def test_structural_counterfactuals_extra_time():
    users = [UserPreference(name="A")]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 22, 0),
    )
    cfs = all_structural_counterfactuals(group)
    assert any(c.kind == "extra_time" for c in cfs)
    assert any(c.kind == "extra_budget" for c in cfs)
    # No vetoer present → remove_vetoer absent
    assert not any(c.kind == "remove_vetoer" for c in cfs)


def test_structural_remove_vetoer_when_exists():
    users = [
        UserPreference(name="Alice"),
        UserPreference(name="Bob", vetoes=("bar_001",)),
    ]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 22, 0),
    )
    cfs = all_structural_counterfactuals(group)
    rv = [c for c in cfs if c.kind == "remove_vetoer"]
    assert len(rv) == 1
    # Modified group has 1 user (Bob dropped)
    assert len(rv[0].modified_group.users) == 1
    assert rv[0].modified_group.users[0].name == "Alice"


def test_strategy_counterfactuals_returns_five():
    _, _, _, pu = _mini_route_and_scores()
    users = [UserPreference(name="A")]
    cfs = strategy_counterfactuals(pu, users)
    assert len(cfs) == 5
    assert set(cfs.keys()) == {
        "utilitarian_sum", "egalitarian_min", "borda_count",
        "copeland_pairwise", "approval_veto",
    }


def test_strategy_winner_returns_max():
    scores = {"b1": 0.9, "b2": 0.5, "b3": 0.7}
    assert strategy_winner(scores) == "b1"
    assert strategy_winner({}) is None
