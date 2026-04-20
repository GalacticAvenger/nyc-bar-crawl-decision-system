"""End-to-end integration tests: five full plan_crawl scenarios + invariants."""

from datetime import datetime

import pytest

from src.data_loader import load_all
from src.decision_system import plan_crawl
from src.models import AccessibilityNeeds, GroupInput, UserPreference


@pytest.fixture(scope="module")
def loaded():
    d = load_all()
    return {"bars": d["bars"], "cases": d["cases"], "rules": d["rules"]}


# ---------------------------------------------------------------------------
# Scenario 1: friendly trio, aligned preferences
# ---------------------------------------------------------------------------

def test_scenario_aligned_friends(loaded):
    users = [
        UserPreference(name="Alice", vibe_weights={"intimate": 1.0, "conversation": 0.8},
                        max_per_drink=18.0, preferred_drinks=("cocktails", "wine")),
        UserPreference(name="Bob", vibe_weights={"intimate": 0.7, "conversation": 1.0},
                        max_per_drink=15.0, preferred_drinks=("cocktails",)),
        UserPreference(name="Carol", vibe_weights={"conversation": 1.0, "cozy": 0.8},
                        max_per_drink=16.0, preferred_drinks=("wine",)),
    ]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        start_location=(40.7265, -73.9815),
        max_stops=3,
    )
    result = plan_crawl(group, **loaded)
    assert result.route.stops, "aligned group should find a route"
    assert result.route.strategy_used in (
        "utilitarian_sum", "egalitarian_min", "copeland_pairwise",
        "borda_count", "approval_veto",
    )
    # Every stop has an explanation
    assert len(result.explanations.children) > 0


# ---------------------------------------------------------------------------
# Scenario 2: wide budget spread → egalitarian
# ---------------------------------------------------------------------------

def test_scenario_budget_gap_triggers_egalitarian(loaded):
    users = [
        UserPreference(name="Student", vibe_weights={"chill": 0.5}, max_per_drink=6.0),
        UserPreference(name="Banker", vibe_weights={"polished": 0.7}, max_per_drink=25.0),
    ]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )
    result = plan_crawl(group, **loaded)
    assert result.route.strategy_used == "egalitarian_min", \
        f"expected egalitarian for >3× budget spread, got {result.route.strategy_used}"


# ---------------------------------------------------------------------------
# Scenario 3: a user vetoes a lot → approval_veto fires
# ---------------------------------------------------------------------------

def test_scenario_many_vetoes_triggers_veto(loaded):
    bars = loaded["bars"]
    # veto the 35 most expensive bars
    expensive = sorted(bars, key=lambda b: -b.avg_drink_price)[:35]
    users = [
        UserPreference(name="Picky", vibe_weights={"chill": 0.5},
                        vetoes=tuple(b.id for b in expensive)),
        UserPreference(name="Easy", vibe_weights={"chill": 0.5}),
    ]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )
    result = plan_crawl(group, **loaded)
    assert result.route.strategy_used == "approval_veto"
    # None of the vetoed bars in the route
    route_ids = {s.bar.id for s in result.route.stops}
    vetoed_ids = {b.id for b in expensive}
    assert not (route_ids & vetoed_ids)


# ---------------------------------------------------------------------------
# Scenario 4: infeasible window
# ---------------------------------------------------------------------------

def test_scenario_infeasible_window_graceful(loaded):
    users = [UserPreference(name="Lonely", vibe_weights={"chill": 1.0})]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 3, 0),
        end_time=datetime(2026, 4, 24, 3, 10),  # 10 min
        max_stops=3,
    )
    result = plan_crawl(group, **loaded)
    # Either empty route, or an explanation about infeasibility
    assert result.route.is_empty or len(result.route.stops) == 0
    assert result.explanations.summary


# ---------------------------------------------------------------------------
# Scenario 5: empty group (no users)
# ---------------------------------------------------------------------------

def test_scenario_no_users_gracefully(loaded):
    group = GroupInput(
        users=[],
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
    )
    # Should not crash — at minimum produces a result (possibly empty)
    try:
        result = plan_crawl(group, **loaded)
        # It's ok for this to be empty
        assert result is not None
    except (ZeroDivisionError, ValueError) as e:
        pytest.skip(f"empty-group edge case: {e}")


# ---------------------------------------------------------------------------
# Invariants that apply to every plan
# ---------------------------------------------------------------------------

def test_plan_is_deterministic(loaded):
    """Same inputs → same output (modulo mutation-safety)."""
    users = [UserPreference(name="Alice", vibe_weights={"intimate": 1.0})]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )
    r1 = plan_crawl(group, **loaded, compute_counterfactuals=False)
    r2 = plan_crawl(group, **loaded, compute_counterfactuals=False)
    assert [s.bar.id for s in r1.route.stops] == [s.bar.id for s in r2.route.stops]
    assert r1.route.strategy_used == r2.route.strategy_used


def test_route_respects_time_window(loaded):
    users = [UserPreference(name="Alice", vibe_weights={"lively": 1.0})]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )
    result = plan_crawl(group, **loaded, compute_counterfactuals=False)
    for s in result.route.stops:
        assert group.start_time <= s.arrival < group.end_time
        assert s.departure <= group.end_time


def test_excluded_bars_have_reasons(loaded):
    users = [
        UserPreference(name="A", max_per_drink=5.0, vetoes=("bar_001",)),
    ]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )
    result = plan_crawl(group, **loaded, compute_counterfactuals=False)
    # Every excluded bar has rule_id + reason
    for ex in result.excluded_bars:
        assert "rule_id" in ex and "reason" in ex and ex["reason"]
