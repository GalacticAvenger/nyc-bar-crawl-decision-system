"""Phase 4 tests: dialogic replan + lightweight preference learning.

Tests (from the phase spec):
  * determinism: same reactions → same replan
  * locked stops are preserved even when re-scoring would move them
  * preference updates are bounded (no weight > 2× original)
  * revert semantics work
  * delta argument attributes every change to either a reaction or a
    ripple — unattributable changes are flagged (not hidden)
"""

from datetime import datetime

import pytest

from src.data_loader import load_all
from src.decision_system import plan_crawl
from src.dialogic import (
    build_delta_argument, format_pref_updates, render_delta_argument,
    replan_with_reactions, revert_user_updates, update_preferences,
)
from src.models import (
    DeltaArgument, GroupInput, Reaction, StopChange, UserPreference,
)


@pytest.fixture(scope="module")
def loaded():
    d = load_all()
    return {"bars": d["bars"], "cases": d["cases"], "rules": d["rules"]}


def _base_group():
    users = [
        UserPreference(
            name="Alex", max_per_drink=15.0,
            vibe_weights={"lively": 0.8, "unpretentious": 0.6},
            criterion_weights={
                "vibe": 0.30, "budget": 0.20, "drink_match": 0.10,
                "noise": 0.05, "distance": 0.05, "happy_hour_active": 0.05,
                "specials_match": 0.05, "crowd_fit": 0.05, "novelty": 0.05,
                "quality_signal": 0.10,
            },
            preferred_drinks=("cocktails", "beer"),
        ),
        UserPreference(
            name="Sarah", max_per_drink=18.0,
            vibe_weights={"conversation": 1.0, "intimate": 0.8},
            criterion_weights={
                "vibe": 0.30, "budget": 0.20, "drink_match": 0.10,
                "noise": 0.05, "distance": 0.05, "happy_hour_active": 0.05,
                "specials_match": 0.05, "crowd_fit": 0.05, "novelty": 0.05,
                "quality_signal": 0.10,
            },
            preferred_drinks=("cocktails", "wine"),
        ),
    ]
    return GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )


# ---------------------------------------------------------------------------
# Preference updates: bounded, attributed
# ---------------------------------------------------------------------------

def test_reject_bumps_weights_on_bad_criteria(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("base plan had no stops; can't test rejection")
    reaction = Reaction(user_id="Alex", stop_index=0, verdict="reject")
    updated, updates = update_preferences(group.users, plan, [reaction])
    alex_new = next(u for u in updated if u.name == "Alex")
    alex_old = next(u for u in group.users if u.name == "Alex")
    # At least one weight should have changed
    changed = [c for c in alex_new.criterion_weights
                if alex_new.criterion_weights[c] !=
                   alex_old.criterion_weights.get(c, 0)]
    assert len(updates) == len(changed)
    for c in changed:
        assert alex_new.criterion_weights[c] > alex_old.criterion_weights[c]


def test_no_weight_exceeds_two_times_original(loaded):
    """Apply the same reject reaction twice in a row — even compounding,
    no weight should exceed 2× the original (cap enforced)."""
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("base plan had no stops")
    r1 = Reaction(user_id="Alex", stop_index=0, verdict="reject")
    updated1, _ = update_preferences(group.users, plan, [r1])
    plan2 = plan_crawl(GroupInput(
        users=updated1, start_time=group.start_time, end_time=group.end_time,
        max_stops=group.max_stops,
    ), **loaded)
    updated2, _ = update_preferences(updated1, plan2, [r1])
    alex_orig = next(u for u in group.users if u.name == "Alex")
    alex_final = next(u for u in updated2 if u.name == "Alex")
    for c, w in alex_final.criterion_weights.items():
        assert w <= 2.0 * alex_orig.criterion_weights.get(c, 0.1) + 1e-6, (
            f"{c} weight {w:.3f} exceeds 2× original "
            f"{alex_orig.criterion_weights.get(c, 0.1):.3f}"
        )


def test_accept_widens_budget_when_over_cap():
    """An accept on a stop that was over the user's cap widens the cap
    by half the overshoot."""
    from src.models import Bar, Route, RouteStop, Score

    # Build a minimal PlanResult with one bar at $24, user cap $20
    bar = Bar(
        id="x", seed_id="s", name="Pricey", neighborhood="N", address="A",
        lat=0.0, lon=0.0, bar_type=(), vibe_tags=(), price_tier="premium",
        avg_drink_price=24.0, drink_specialties=(), drink_categories_served=(),
        noise_level="lively", capacity_estimate=50, crowd_level_by_hour={},
        outdoor_seating=None, food_quality=None, kitchen_open=None,
        happy_hour_windows=(), specials=(), open_hours={"fri": ["17:00", "26:00"]},
        age_policy="", accessibility={}, reservations="", dress_code="",
        novelty=0.5, description=None, good_for=(), avoid_for=(),
        google_rating=4.0, google_review_count=100, google_price_indicator=None,
        google_category=None, quality_signal=0.5, user_note=None,
        primary_function=None, editorial_note=None, source="",
    )
    stop = RouteStop(bar=bar, arrival=datetime(2026, 4, 24, 20, 0),
                     departure=datetime(2026, 4, 24, 20, 45), group_score=0.5)
    from src.models import PlanResult, Explanation
    plan = PlanResult(
        route=Route(stops=[stop], total_utility=0.5, total_walking_miles=0.0,
                     windows_captured=[], strategy_used="utilitarian_sum",
                     strategy_rationale=""),
        explanations=Explanation(summary=""),
        traces={"per_user_scores": {"Alice": {"x": Score(
            bar_id="x", user_id="Alice",
            per_criterion={}, weighted_contributions={}, total=0.5,
        )}}},
    )
    user = UserPreference(name="Alice", max_per_drink=20.0)
    updated, updates = update_preferences(
        [user], plan, [Reaction(user_id="Alice", stop_index=0, verdict="accept")],
    )
    # Cap should widen by (24 - 20) * 0.5 = 2 → 22
    assert updated[0].max_per_drink == pytest.approx(22.0)
    assert len(updates) == 1
    assert updates[0].field == "max_per_drink"


# ---------------------------------------------------------------------------
# Locked stops — preserved across replan
# ---------------------------------------------------------------------------

def test_locked_stops_are_preserved(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if len(plan.route.stops) < 2:
        pytest.skip("need 2+ stops to test locking")
    locked_idx = 0
    locked_bar_id = plan.route.stops[locked_idx].bar.id
    reactions = [
        Reaction(user_id="Alex", stop_index=locked_idx,
                 verdict="accept", lock=True),
        Reaction(user_id="Alex", stop_index=1, verdict="reject"),
    ]
    result = replan_with_reactions(plan, reactions, group, **loaded)
    if not result.route.stops:
        pytest.skip("replan produced an empty route (lock infeasible?)")
    assert result.route.stops[locked_idx].bar.id == locked_bar_id, (
        f"locked stop was moved: {locked_bar_id} -> "
        f"{result.route.stops[locked_idx].bar.id}"
    )


# ---------------------------------------------------------------------------
# Determinism: same reactions → same replan
# ---------------------------------------------------------------------------

def test_replan_is_deterministic(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("no plan")
    reactions = [Reaction(user_id="Alex", stop_index=0, verdict="reject")]
    r1 = replan_with_reactions(plan, reactions, group, **loaded)
    r2 = replan_with_reactions(plan, reactions, group, **loaded)
    sig1 = tuple(s.bar.id for s in r1.route.stops)
    sig2 = tuple(s.bar.id for s in r2.route.stops)
    assert sig1 == sig2


# ---------------------------------------------------------------------------
# Delta attribution: every change attributed; none unattributed
# ---------------------------------------------------------------------------

def test_delta_every_change_is_attributed(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("no plan")
    reactions = [Reaction(user_id="Alex", stop_index=0, verdict="reject")]
    result = replan_with_reactions(plan, reactions, group, **loaded)
    delta: DeltaArgument = result.traces["delta_argument"]
    assert isinstance(delta, DeltaArgument)
    # Every change (non-unchanged) has a non-empty attribution
    for change in delta.per_stop_changes:
        if change.change_type != "unchanged":
            assert change.attributed_to and change.attributed_to != "no change"
    # unattributed list must be empty — an unattributable change is a bug
    assert not delta.unattributed, (
        f"unattributed changes detected: {delta.unattributed}"
    )


def test_delta_text_mentions_number_of_changed_stops(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("no plan")
    reactions = [Reaction(user_id="Alex", stop_index=0, verdict="reject")]
    result = replan_with_reactions(plan, reactions, group, **loaded)
    delta = result.traces["delta_argument"]
    text = render_delta_argument(delta)
    assert "stop" in text.lower()
    assert "changed" in text.lower() or "unchanged" in text.lower() \
        or "replan" in text.lower()


# ---------------------------------------------------------------------------
# Revert semantics
# ---------------------------------------------------------------------------

def test_revert_user_restores_original_preferences(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("no plan")
    reactions = [Reaction(user_id="Alex", stop_index=0, verdict="reject")]
    updated, updates = update_preferences(group.users, plan, reactions)
    reverted, remaining = revert_user_updates(
        group.users, updated, updates, "Alex",
    )
    alex_reverted = next(u for u in reverted if u.name == "Alex")
    alex_orig = next(u for u in group.users if u.name == "Alex")
    assert alex_reverted.criterion_weights == alex_orig.criterion_weights
    assert alex_reverted.max_per_drink == alex_orig.max_per_drink
    # Remaining updates should not mention Alex
    assert all(u.user_id != "Alex" for u in remaining)


def test_revert_preserves_other_users_updates(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("no plan")
    # Both users react — reject on stop 0
    reactions = [
        Reaction(user_id="Alex", stop_index=0, verdict="reject"),
        Reaction(user_id="Sarah", stop_index=0, verdict="reject"),
    ]
    updated, updates = update_preferences(group.users, plan, reactions)
    reverted, remaining = revert_user_updates(
        group.users, updated, updates, "Alex",
    )
    # Sarah's updates still in remaining
    assert any(u.user_id == "Sarah" for u in remaining)
    # Sarah's updated weights preserved
    sarah_reverted = next(u for u in reverted if u.name == "Sarah")
    sarah_updated = next(u for u in updated if u.name == "Sarah")
    assert sarah_reverted.criterion_weights == sarah_updated.criterion_weights


# ---------------------------------------------------------------------------
# Pref-update narrative + revert hint
# ---------------------------------------------------------------------------

def test_pref_update_narrative_includes_revert_instruction(loaded):
    group = _base_group()
    plan = plan_crawl(group, **loaded)
    if not plan.route.stops:
        pytest.skip("no plan")
    reactions = [Reaction(user_id="Alex", stop_index=0, verdict="reject")]
    _, updates = update_preferences(group.users, plan, reactions)
    if not updates:
        pytest.skip("no pref-update triggered for this group")
    text = format_pref_updates(updates)
    assert "revert Alex" in text
    assert "replan" in text.lower()
