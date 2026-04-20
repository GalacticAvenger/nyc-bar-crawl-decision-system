"""Tests for group_aggregation.py."""

from src.data_loader import load_bars, load_rules
from src.group_aggregation import (
    aggregate, aggregate_utilitarian_sum, aggregate_egalitarian_min,
    aggregate_borda_count, aggregate_copeland_pairwise, aggregate_approval_veto,
    disagreement_profile, select_strategy,
)
from src.models import Score, UserPreference


def _make_per_user(data):
    """Helper: dict[user][bar] -> total → full Score."""
    out = {}
    for user, bars in data.items():
        out[user] = {}
        for bid, total in bars.items():
            out[user][bid] = Score(bar_id=bid, user_id=user,
                                    per_criterion={}, weighted_contributions={},
                                    total=total)
    return out


def test_utilitarian_sums_across_users():
    data = _make_per_user({
        "A": {"b1": 0.5, "b2": 0.8},
        "B": {"b1": 0.3, "b2": 0.1},
    })
    gs = aggregate_utilitarian_sum(data)
    assert abs(gs["b1"].total - 0.8) < 1e-9
    assert abs(gs["b2"].total - 0.9) < 1e-9


def test_egalitarian_picks_minimum():
    data = _make_per_user({
        "A": {"b1": 0.9, "b2": 0.8},
        "B": {"b1": 0.2, "b2": 0.5},
    })
    gs = aggregate_egalitarian_min(data)
    assert abs(gs["b1"].total - 0.2) < 1e-9
    assert abs(gs["b2"].total - 0.5) < 1e-9
    # b2 wins under egalitarian (min=0.5 > min=0.2)


def test_borda_assigns_ranks():
    data = _make_per_user({
        "A": {"b1": 0.9, "b2": 0.5, "b3": 0.1},  # ranks: b1>b2>b3
        "B": {"b1": 0.1, "b2": 0.9, "b3": 0.5},  # ranks: b2>b3>b1
    })
    gs = aggregate_borda_count(data)
    # With 3 bars: rank0→3pts, rank1→2pts, rank2→1pt
    # b1: A=3 + B=1 = 4
    # b2: A=2 + B=3 = 5
    # b3: A=1 + B=2 = 3
    assert gs["b2"].total > gs["b1"].total > gs["b3"].total


def test_copeland_finds_condorcet_winner():
    data = _make_per_user({
        "A": {"b1": 0.9, "b2": 0.5, "b3": 0.1},
        "B": {"b1": 0.8, "b2": 0.3, "b3": 0.2},
        "C": {"b1": 0.7, "b2": 0.6, "b3": 0.4},
    })
    # b1 beats both others with all 3 voters; it's a Condorcet winner
    gs = aggregate_copeland_pairwise(data)
    assert gs["b1"].total == 2  # beats b2 and b3
    assert gs["b2"].total == 1  # beats b3


def test_approval_veto_hard_excludes():
    data = _make_per_user({
        "A": {"b1": 0.9, "b2": 0.8},
        "B": {"b1": 0.9, "b2": 0.8},
    })
    users = [
        UserPreference(name="A", vetoes=("b1",)),
        UserPreference(name="B"),
    ]
    gs = aggregate_approval_veto(data, users)
    assert gs["b1"].total == float("-inf")
    assert gs["b2"].total == 2  # both approve


def test_identical_users_all_strategies_agree():
    """Three users with identical prefs → all strategies produce identical rankings."""
    same = _make_per_user({
        "A": {"b1": 0.9, "b2": 0.5, "b3": 0.3},
        "B": {"b1": 0.9, "b2": 0.5, "b3": 0.3},
        "C": {"b1": 0.9, "b2": 0.5, "b3": 0.3},
    })
    users = [UserPreference(name=n) for n in "ABC"]
    for strat in ("utilitarian_sum", "egalitarian_min", "borda_count", "copeland_pairwise"):
        gs = aggregate(strat, same, users)
        sorted_bars = sorted(gs.values(), key=lambda g: -g.total)
        assert sorted_bars[0].bar_id == "b1", \
            f"{strat} should rank b1 first for identical users, got {sorted_bars[0].bar_id}"


def test_meta_selector_veto_fires_on_high_dealbreakers():
    bars = load_bars()[:10]
    rules = load_rules()
    users = [UserPreference(name="A", vetoes=tuple(b.id for b in bars[:5]))]
    profile = disagreement_profile(users, bars)
    assert profile["dealbreaker_density"] > 0.2
    strat, rule_id, rationale = select_strategy(profile, rules)
    assert strat == "approval_veto"
    assert rule_id == "strategy_veto"


def test_meta_selector_egalitarian_fires_on_budget_gap():
    bars = load_bars()[:10]
    rules = load_rules()
    users = [
        UserPreference(name="Poor", max_per_drink=5.0),
        UserPreference(name="Rich", max_per_drink=30.0),
    ]
    profile = disagreement_profile(users, bars)
    assert profile["budget_spread_ratio"] > 3.0
    strat, rule_id, _ = select_strategy(profile, rules)
    assert strat == "egalitarian_min"


def test_meta_selector_utilitarian_is_default():
    bars = load_bars()[:10]
    rules = load_rules()
    # Two aligned users, similar budgets, small vibe variance
    users = [
        UserPreference(name="A", max_per_drink=15, vibe_weights={"chill": 0.5, "conversation": 0.5}),
        UserPreference(name="B", max_per_drink=16, vibe_weights={"chill": 0.5, "conversation": 0.5}),
    ]
    profile = disagreement_profile(users, bars)
    strat, rule_id, _ = select_strategy(profile, rules)
    assert strat == "utilitarian_sum"
