"""Phase 1 tests: the VOTE-shaped StrategyDecision returned by the
meta-selector, the rank/narrative_name/quote/applies_when fields on each
strategy in rules.yaml, and the deeper_analysis() callable that surfaces the
winner vs runner-up side-by-side when the plan's margin is tight.
"""

from datetime import datetime

import pytest

from src.data_loader import load_all, load_bars, load_rules
from src.decision_system import deeper_analysis, plan_crawl
from src.group_aggregation import (
    _build_considered_alternatives, _RULE_TO_STRATEGY,
    disagreement_profile, select_strategy,
)
from src.models import GroupInput, StrategyDecision, UserPreference


@pytest.fixture(scope="module")
def loaded():
    d = load_all()
    return {"bars": d["bars"], "cases": d["cases"], "rules": d["rules"]}


# ---------------------------------------------------------------------------
# rules.yaml: every strategy has rank / narrative_name / quote / applies_when
# ---------------------------------------------------------------------------

def test_every_strategy_has_vote_metadata(loaded):
    rules = loaded["rules"]
    strategies = rules["group_strategy_rules"]["strategies"]
    required = ("rank", "narrative_name", "quote", "applies_when")
    for name, meta in strategies.items():
        for field in required:
            assert field in meta, f"{name} missing '{field}'"
            assert meta[field], f"{name}.{field} is empty"


def test_rank_assignments_match_spec(loaded):
    """A = strong moral claim (approval_veto + egalitarian), B = positional/
    pairwise (borda + copeland), C = shallow fallback (utilitarian)."""
    strategies = loaded["rules"]["group_strategy_rules"]["strategies"]
    assert strategies["approval_veto"]["rank"] == "A"
    assert strategies["egalitarian_min"]["rank"] == "A"
    assert strategies["borda_count"]["rank"] == "B"
    assert strategies["copeland_pairwise"]["rank"] == "B"
    assert strategies["utilitarian_sum"]["rank"] == "C"


# ---------------------------------------------------------------------------
# select_strategy returns a StrategyDecision
# ---------------------------------------------------------------------------

def test_select_strategy_returns_decision_object(loaded):
    users = [
        UserPreference(name="A", max_per_drink=15),
        UserPreference(name="B", max_per_drink=16),
    ]
    profile = disagreement_profile(users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    assert isinstance(decision, StrategyDecision)
    # Backward-compat — strategy_id holds the old string return value
    assert decision.strategy_id == "utilitarian_sum"
    assert decision.triggering_rule_id == "strategy_utilitarian"
    assert decision.rank == "C"
    assert decision.narrative_name == "Maximize aggregate satisfaction"


def test_decision_carries_triggering_signal_string(loaded):
    users = [
        UserPreference(name="Poor", max_per_drink=5),
        UserPreference(name="Rich", max_per_drink=30),
    ]
    profile = disagreement_profile(users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    assert decision.strategy_id == "egalitarian_min"
    # Signal should name the triggering metric and expose the comparison
    signal = decision.triggering_profile_signal
    assert "budget_spread_ratio" in signal
    assert "threshold" in signal


def test_decision_considered_alternatives_covers_other_four(loaded):
    users = [
        UserPreference(name="A", max_per_drink=15),
        UserPreference(name="B", max_per_drink=16),
    ]
    profile = disagreement_profile(users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    alt_ids = {sid for sid, _rank, _why in decision.considered_alternatives}
    # All five strategies are present; the chosen one is NOT in alternatives
    assert decision.strategy_id not in alt_ids
    assert alt_ids == set(_RULE_TO_STRATEGY.values()) - {decision.strategy_id}
    # Every non-chosen alt must have a non-empty reason
    for _sid, _rank, why in decision.considered_alternatives:
        assert isinstance(why, str) and why.strip()


def test_decision_alternatives_explain_higher_rank_loss_vs_threshold_miss(loaded):
    """A utilitarian (priority 5 / rank C) decision means: the four higher-
    priority strategies all failed their thresholds. Each alternative's
    why_not_chosen should reflect that — not a "higher-rank applied" phrase."""
    users = [
        UserPreference(name="A", max_per_drink=15),
        UserPreference(name="B", max_per_drink=16),
    ]
    profile = disagreement_profile(users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    assert decision.strategy_id == "utilitarian_sum"
    for sid, _rank, why in decision.considered_alternatives:
        # None of the four losers should claim "higher-rank strategy applied"
        # — utilitarian is the lowest-priority branch, so nothing outranks it.
        assert "higher-rank" not in why, (
            f"{sid}: unexpected 'higher-rank' phrasing — "
            f"utilitarian is the fallback"
        )


def test_decision_alternatives_cite_higher_rank_when_A_fires(loaded):
    """When an A-rank strategy fires, the C-rank utilitarian alternative
    must cite 'higher-rank' as its reason — the utilitarian threshold never
    runs because a priority-2 rule already matched."""
    users = [UserPreference(name="A", vetoes=tuple(
        b.id for b in loaded["bars"][:40]
    ))]
    profile = disagreement_profile(users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    assert decision.strategy_id == "approval_veto"
    lookup = {sid: why for sid, _r, why in decision.considered_alternatives}
    assert "higher-rank" in lookup["utilitarian_sum"]


def test_build_considered_alternatives_is_stable_shape(loaded):
    """Output is a list of (str, str, str) tuples for every non-chosen id."""
    profile = {
        "dealbreaker_density": 0.0,
        "budget_spread_ratio": 1.0,
        "vibe_variance": 0.0,
        "max_preference_intensity": 0.0,
        "group_size": 1,
    }
    alts = _build_considered_alternatives("strategy_utilitarian", profile,
                                           loaded["rules"])
    assert len(alts) == 4
    for sid, rank, why in alts:
        assert isinstance(sid, str)
        assert rank in {"A", "B", "C", "D", "E"}
        assert isinstance(why, str) and why.strip()


# ---------------------------------------------------------------------------
# deeper_analysis: margin trigger + side-by-side diff
# ---------------------------------------------------------------------------

def _aligned_group():
    users = [
        UserPreference(
            name="A", max_per_drink=18.0,
            vibe_weights={"conversation": 1.0, "intimate": 0.8},
            preferred_drinks=("cocktails", "wine"),
        ),
        UserPreference(
            name="B", max_per_drink=17.0,
            vibe_weights={"conversation": 1.0, "cozy": 0.7},
            preferred_drinks=("cocktails",),
        ),
    ]
    return GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )


def test_plan_surfaces_strategy_decision_on_traces(loaded):
    result = plan_crawl(_aligned_group(), **loaded)
    decision = result.traces["strategy_decision"]
    assert isinstance(decision, StrategyDecision)
    assert decision.strategy_id == result.route.strategy_used


def test_plan_populates_runner_up_on_each_stop(loaded):
    """Phase 1 wires runner-ups onto RouteStop.runner_up so deeper_analysis
    doesn't have to dig through traces."""
    result = plan_crawl(_aligned_group(), **loaded)
    assert result.route.stops, "aligned group should produce a route"
    # At least one stop has a runner-up (the last stop may not if no
    # unvisited bars remain; first stops should)
    assert any(s.runner_up is not None for s in result.route.stops)


def test_deeper_analysis_returns_per_stop_diff(loaded):
    result = plan_crawl(_aligned_group(), **loaded)
    diff = deeper_analysis(result, rules=loaded["rules"])
    assert "margin" in diff
    assert "margin_threshold" in diff
    assert diff["margin_threshold"] == 0.05
    assert len(diff["stop_diffs"]) == len(result.route.stops)
    first = diff["stop_diffs"][0]
    assert first["stop_index"] == 0
    assert first["winner"]["bar_name"] == result.route.stops[0].bar.name


def test_rank_flips_to_E_when_margin_below_threshold(loaded):
    """If the mean relative_gap across stops is below the configured
    threshold, the decision rank must be reset to E and requires_deeper_analysis
    must be True. Confirm the mechanism by forcing a low threshold."""
    # Clone the rules with a very permissive threshold so E-tier fires
    # deterministically on any plan.
    rules = dict(loaded["rules"])
    rules["group_strategy_rules"] = {
        **rules["group_strategy_rules"],
        "deeper_analysis": {"margin_threshold": 1.0},  # any gap below 1.0
    }
    result = plan_crawl(_aligned_group(), bars=loaded["bars"],
                        cases=loaded["cases"], rules=rules)
    decision = result.traces["strategy_decision"]
    if result.traces.get("plan_margin") is not None:
        # With threshold=1.0, every real plan trips the trigger
        assert decision.rank == "E"
        assert decision.requires_deeper_analysis is True


def test_rank_unchanged_when_margin_above_threshold(loaded):
    """When the mean gap is comfortably above the threshold, rank stays at
    the strategy's natural rank (C for utilitarian, etc.)."""
    rules = dict(loaded["rules"])
    rules["group_strategy_rules"] = {
        **rules["group_strategy_rules"],
        "deeper_analysis": {"margin_threshold": 0.0},  # nothing trips
    }
    result = plan_crawl(_aligned_group(), bars=loaded["bars"],
                        cases=loaded["cases"], rules=rules)
    decision = result.traces["strategy_decision"]
    assert decision.rank in {"A", "B", "C"}
    assert decision.requires_deeper_analysis is False


def test_route_strategy_used_still_exposes_strategy_id(loaded):
    """Backward-compat: Route.strategy_used is still the strategy_id string
    so downstream code and tests that read the string don't break."""
    result = plan_crawl(_aligned_group(), **loaded)
    assert isinstance(result.route.strategy_used, str)
    assert result.route.strategy_used == result.traces["strategy_decision"].strategy_id
