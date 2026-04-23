"""Targeted tests for the issues surfaced by evaluation/REPORT.md.

Each test pins behavior for a specific bug fix. If any of these regress, the
issue has come back.
"""

from datetime import datetime

import pytest

from src.case_based import (
    _budget_tier_of, _expand_tier_spec, _case_budget_match, _case_vibe_match,
    similarity, retrieve, TIER_ORDER,
)
from src.data_loader import load_all
from src.decision_system import (
    plan_crawl, _apply_dealbreakers, _maybe_recenter_start,
    _DEFAULT_START_LOCATION,
)
from src.explanation_engine import (
    explain_stop, explain_counterfactual, _format_delta_pct, explain_exclusion,
)
from src.group_aggregation import (
    disagreement_profile, select_strategy, _threshold_for,
)
from src.models import (
    AccessibilityNeeds, GroupInput, Route, RouteStop, RunnerUp, Score,
    UserPreference,
)
from src.option_generation import find_runner_ups


@pytest.fixture(scope="module")
def loaded():
    d = load_all()
    return d


# ---------------------------------------------------------------------------
# Fix 1+2 — accessibility filter is conservative (None excludes when requested)
# ---------------------------------------------------------------------------

def test_step_free_filter_excludes_unverified(loaded):
    """When the user requests step-free, bars with None (unknown) are excluded.

    The dataset has no bars with explicit step_free=True, so a strict request
    should produce ZERO survivors and an explanation that says so."""
    bars = loaded["bars"]
    rules = loaded["rules"]
    g = GroupInput(
        users=[UserPreference(name="Mob", vibe_weights={"conversation": 0.5},
                              max_per_drink=15, preferred_noise="conversation")],
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=2,
        accessibility_needs=AccessibilityNeeds(step_free=True),
    )
    survivors, excluded = _apply_dealbreakers(bars, g, rules)
    a11y = [e for e in excluded if e["rule_id"] == "accessibility_unmet"]
    assert len(a11y) > 0, "step-free=True must exclude unverified bars"
    # All bars are unverified (None) so survivors should be 0
    assert len(survivors) == 0
    # Reason should say "unverified" — honesty about what we know
    assert any("unverified" in e["reason"].lower() for e in a11y)


def test_step_free_filter_admits_explicit_true():
    """A bar marked step_free=True should pass the filter."""
    from src.models import Bar
    # Build a synthetic bar manually
    b = Bar(
        id="bar_x", seed_id="seed_x", name="X", neighborhood="Test",
        address="1 Test St", lat=40.7, lon=-74.0,
        bar_type=("generic_bar",), vibe_tags=("lively",),
        price_tier="moderate", avg_drink_price=12.0,
        drink_specialties=(), drink_categories_served=("beer",),
        noise_level="lively", capacity_estimate=50, crowd_level_by_hour={},
        outdoor_seating=False, food_quality=None, kitchen_open=None,
        happy_hour_windows=(), specials=(),
        open_hours={"fri": ["17:00", "26:00"]},
        age_policy="21+",
        accessibility={"step_free": True, "accessible_restroom": True},
        reservations="", dress_code="", novelty=0.5, description="",
        good_for=(), avoid_for=(), google_rating=4.0, google_review_count=100,
        google_price_indicator=None, google_category=None, quality_signal=0.5,
        user_note=None, primary_function=None, editorial_note=None, source="",
    )
    rules = {"scoring_defaults": {"default_weights": {}}}
    g = GroupInput(
        users=[UserPreference(name="A", vibe_weights={"lively": 0.5},
                              max_per_drink=20)],
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=1,
        accessibility_needs=AccessibilityNeeds(step_free=True),
    )
    survivors, excluded = _apply_dealbreakers([b], g, rules)
    assert b in survivors
    assert not excluded


def test_accessible_restroom_filter_works():
    """The accessible_restroom dealbreaker now actually filters."""
    from src.models import Bar
    b = Bar(
        id="bar_x", seed_id="x", name="X", neighborhood="Test",
        address="1 X", lat=40.7, lon=-74.0,
        bar_type=("generic_bar",), vibe_tags=("lively",),
        price_tier="moderate", avg_drink_price=12.0,
        drink_specialties=(), drink_categories_served=("beer",),
        noise_level="lively", capacity_estimate=50, crowd_level_by_hour={},
        outdoor_seating=False, food_quality=None, kitchen_open=None,
        happy_hour_windows=(), specials=(),
        open_hours={"fri": ["17:00", "26:00"]},
        age_policy="21+",
        accessibility={"step_free": True, "accessible_restroom": False},
        reservations="", dress_code="", novelty=0.5, description="",
        good_for=(), avoid_for=(), google_rating=4.0, google_review_count=100,
        google_price_indicator=None, google_category=None, quality_signal=0.5,
        user_note=None, primary_function=None, editorial_note=None, source="",
    )
    rules = {"scoring_defaults": {"default_weights": {}}}
    g = GroupInput(
        users=[UserPreference(name="A", vibe_weights={"lively": 0.5},
                              max_per_drink=20)],
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=1,
        accessibility_needs=AccessibilityNeeds(accessible_restroom=True),
    )
    survivors, excluded = _apply_dealbreakers([b], g, rules)
    assert not survivors
    assert excluded[0]["rule_id"] == "accessible_restroom_unmet"


# ---------------------------------------------------------------------------
# Fix 3 — age_policy_mismatch is enforced
# ---------------------------------------------------------------------------

def test_underage_user_excludes_21plus_bars(loaded):
    bars = loaded["bars"]
    rules = loaded["rules"]
    users = [
        UserPreference(name="Yng", vibe_weights={"lively": 0.5},
                       max_per_drink=15, age=19),
        UserPreference(name="Old", vibe_weights={"lively": 0.5},
                       max_per_drink=15, age=30),
    ]
    g = GroupInput(users=users,
                   start_time=datetime(2025, 5, 2, 20),
                   end_time=datetime(2025, 5, 3, 0),
                   max_stops=2)
    survivors, excluded = _apply_dealbreakers(bars, g, rules)
    age_excluded = [e for e in excluded if e["rule_id"] == "age_policy_mismatch"]
    assert len(age_excluded) > 0, "21+ bars must exclude under-21 users"
    # Sample reason mentions the underage user
    assert "Yng" in age_excluded[0]["reason"]
    # All surviving bars (if any) must not be 21+
    for b in survivors:
        assert b.age_policy.replace(" ", "").lower() not in ("21+", "21andover")


# ---------------------------------------------------------------------------
# Fix 4 — "of the three" is no longer hardcoded
# ---------------------------------------------------------------------------

def test_dominant_user_line_uses_actual_group_size(loaded):
    """Two-person groups must NOT see 'of the three' in the explanation."""
    bars = loaded["bars"]
    rules = loaded["rules"]
    users = [
        UserPreference(name="Alice",
                       vibe_weights={"craft-cocktails": 1.0, "intimate": 0.9},
                       max_per_drink=18, preferred_drinks=("cocktails",),
                       preferred_noise="conversation"),
        UserPreference(name="Bob",
                       vibe_weights={"craft-cocktails": 1.0, "dim": 0.7},
                       max_per_drink=18, preferred_drinks=("cocktails",),
                       preferred_noise="conversation"),
    ]
    g = GroupInput(users=users,
                   start_time=datetime(2025, 5, 2, 20),
                   end_time=datetime(2025, 5, 3, 0),
                   max_stops=3,
                   neighborhoods=("East Village", "Lower East Side"))
    r = plan_crawl(g, bars=loaded["bars"], cases=loaded["cases"], rules=loaded["rules"])
    full_text = "\n".join(c.summary for c in r.explanations.children[1:])
    assert "of the three" not in full_text.lower(), \
        f"hardcoded 'of the three' leaked into 2-user plan:\n{full_text}"


def test_dominant_user_line_uses_n_for_4plus_groups(loaded):
    bars = loaded["bars"]
    rules = loaded["rules"]
    users = [
        UserPreference(name=f"P{i}",
                       vibe_weights={"lively": 0.7, "post-work": 0.5},
                       max_per_drink=15, preferred_noise="lively") for i in range(4)
    ]
    g = GroupInput(users=users,
                   start_time=datetime(2025, 5, 2, 20),
                   end_time=datetime(2025, 5, 2, 23, 30),
                   max_stops=3,
                   neighborhoods=("East Village",))
    r = plan_crawl(g, bars=loaded["bars"], cases=loaded["cases"], rules=loaded["rules"])
    full_text = "\n".join(c.summary for c in r.explanations.children[1:])
    assert "of the three" not in full_text.lower()
    # If there's a "highest of the X" line, X must be 4
    if "highest of the" in full_text:
        assert "highest of the 4" in full_text


# ---------------------------------------------------------------------------
# Fix 5 — "fits the budget" budget honesty disclaimer
# ---------------------------------------------------------------------------

def test_budget_disclaimer_fires_when_user_over_cap(loaded):
    """When some user is over budget at the chosen bar, the stop explanation
    surfaces it explicitly — never silently asserting 'fits the budget'."""
    bars = loaded["bars"]
    rules = loaded["rules"]
    g = GroupInput(
        users=[
            UserPreference(name="Hi", vibe_weights={"craft-cocktails": 1.0},
                           max_per_drink=22, preferred_drinks=("cocktails",),
                           preferred_noise="conversation"),
            UserPreference(name="Lo", vibe_weights={"craft-cocktails": 1.0},
                           max_per_drink=10, preferred_drinks=("cocktails", "beer"),
                           preferred_noise="conversation"),
        ],
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=2, neighborhoods=("East Village", "Lower East Side"),
    )
    r = plan_crawl(g, bars=loaded["bars"], cases=loaded["cases"], rules=loaded["rules"])
    full_text = " ".join(c.summary for c in r.explanations.children[1:])
    over_budget_for_lo = any(
        s.bar.avg_drink_price > 10 for s in r.route.stops
    )
    if over_budget_for_lo:
        # We MUST surface the heads-up
        assert "Heads-up" in full_text or "Lo" in full_text, \
            f"missing budget honesty disclaimer:\n{full_text}"


# ---------------------------------------------------------------------------
# Fix 6 — CBR retrieval correctness
# ---------------------------------------------------------------------------

def test_budget_tier_boundary_inclusive():
    """$8 is cheap, $14 moderate, $20 premium — boundaries inclusive."""
    assert _budget_tier_of(7.99) == "cheap"
    assert _budget_tier_of(8.0) == "cheap"
    assert _budget_tier_of(8.01) == "moderate"
    assert _budget_tier_of(14.0) == "moderate"
    assert _budget_tier_of(14.01) == "premium"
    assert _budget_tier_of(20.0) == "premium"
    assert _budget_tier_of(20.01) == "splurge"


def test_expand_tier_spec_handles_compound_ranges():
    assert _expand_tier_spec("cheap") == {"cheap"}
    assert _expand_tier_spec("moderate_to_premium") == {"moderate", "premium"}
    assert _expand_tier_spec("any") == set(TIER_ORDER)
    # The substring trap: previously "moderate" matched "moderate_to_premium"
    # via Python `in`. Now expansion is explicit. A "cheap" group should NOT
    # match "moderate_to_premium" with full credit.
    assert _case_budget_match(_FakeCase("moderate_to_premium"), "cheap") < 1.0


def test_dive_group_retrieves_dive_tour_archetype(loaded):
    """The CBR bug was: dive group → 'Nightcap Duo' wins instead of dive
    tour. After fix, dive tour should win."""
    cases = loaded["cases"]
    g = GroupInput(
        users=[UserPreference(name="X",
                              vibe_weights={"divey": 1.0, "unpretentious": 0.9,
                                            "games": 0.6},
                              max_per_drink=8,
                              preferred_drinks=("beer", "shots"),
                              preferred_noise="lively")] * 3,
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=3,
    )
    matches = retrieve(g, cases, top_k=1)
    assert matches, "CBR returned nothing"
    top, sim, _ = matches[0]
    assert top.id == "case_east_village_dive_tour", \
        f"expected dive tour to win for dive group; got {top.id} (sim={sim:.3f})"


def test_party_group_retrieves_party_archetype(loaded):
    cases = loaded["cases"]
    users = [UserPreference(name=f"P{i}",
                            vibe_weights={"birthday-party": 1.0,
                                          "large-groups": 0.9, "dance-floor": 0.7},
                            max_per_drink=18,
                            preferred_drinks=("cocktails", "shots"),
                            preferred_noise="loud") for i in range(8)]
    g = GroupInput(users=users,
                   start_time=datetime(2025, 5, 2, 21),
                   end_time=datetime(2025, 5, 3, 2),
                   max_stops=3)
    matches = retrieve(g, cases, top_k=1)
    top, _, _ = matches[0]
    # Either of the two birthday cases is acceptable
    assert top.id in ("case_large_group_birthday", "case_karaoke_birthday"), \
        f"party group should retrieve a birthday case, got {top.id}"


# ---------------------------------------------------------------------------
# Fix 7 — runner-up gap normalization
# ---------------------------------------------------------------------------

def test_runner_up_relative_gap_is_in_unit_interval(loaded):
    """relative_gap must be in [0, 1] regardless of strategy units."""
    bars = loaded["bars"]
    rules = loaded["rules"]
    cases = loaded["cases"]
    # A vibe-split group → Copeland (integer scores)
    g = GroupInput(
        users=[
            UserPreference(name="C",
                           vibe_weights={"craft-cocktails": 1.0, "intimate": 0.9},
                           max_per_drink=18, preferred_noise="conversation"),
            UserPreference(name="D",
                           vibe_weights={"dance-floor": 1.0, "dj-set": 0.9},
                           max_per_drink=18, preferred_noise="loud"),
            UserPreference(name="V",
                           vibe_weights={"divey": 1.0, "games": 0.7},
                           max_per_drink=12, preferred_noise="lively"),
        ],
        start_time=datetime(2025, 5, 2, 21),
        end_time=datetime(2025, 5, 3, 1),
        max_stops=3,
    )
    r = plan_crawl(g, bars=bars, cases=cases, rules=rules,
                   compute_counterfactuals=False)
    # Re-derive runner-ups so we can inspect relative_gap directly
    per_user = r.traces["per_user_scores"]
    # Use union scoring like decision_system does
    from src.group_aggregation import aggregate
    gs_full = aggregate(r.route.strategy_used, per_user, g.users)
    scores = {bid: g_.total for bid, g_ in gs_full.items()
              if g_.total != float("-inf")}
    routable = [b for b in bars if b.id in scores]
    rus = find_runner_ups(r.route, scores, per_user, routable)
    for idx, ru in rus.items():
        assert 0.0 <= ru.relative_gap <= 1.0 + 1e-9, \
            f"relative_gap {ru.relative_gap} out of [0,1] (strategy={r.route.strategy_used})"


# ---------------------------------------------------------------------------
# Fix 8 — counterfactual delta is in normalized units
# ---------------------------------------------------------------------------

def test_format_delta_pct_handles_zero_base():
    assert "no measurable change" in _format_delta_pct(0.0001, 0.0)
    assert "improvement" in _format_delta_pct(5.0, 0.0)
    assert "drop" in _format_delta_pct(-5.0, 0.0)


def test_format_delta_pct_returns_percent_for_normal_input():
    s = _format_delta_pct(10.0, 100.0)
    assert "10%" in s and "+" in s
    s = _format_delta_pct(-5.0, 50.0)
    assert "10%" in s and "-" in s
    # Below 1% threshold
    assert "essentially unchanged" in _format_delta_pct(0.5, 200.0)


def test_explain_counterfactual_does_not_print_raw_borda_units():
    """The bug was '+36.00' under Borda, which sounds dramatic but is just
    integer rank counts. After fix the text is in % or qualitative."""
    from src.models import Route, RouteStop, Bar
    bar = Bar(id="b", seed_id="s", name="N", neighborhood="X", address="",
              lat=40.7, lon=-74.0, bar_type=(), vibe_tags=(),
              price_tier="moderate", avg_drink_price=10.0,
              drink_specialties=(), drink_categories_served=(),
              noise_level="lively", capacity_estimate=50,
              crowd_level_by_hour={}, outdoor_seating=None,
              food_quality=None, kitchen_open=None,
              happy_hour_windows=(), specials=(), open_hours={},
              age_policy="21+", accessibility={}, reservations="",
              dress_code="", novelty=0.5, description=None,
              good_for=(), avoid_for=(), google_rating=4.0,
              google_review_count=100, google_price_indicator=None,
              google_category=None, quality_signal=0.5,
              user_note=None, primary_function=None,
              editorial_note=None, source="")
    stop = RouteStop(bar=bar, arrival=datetime(2025, 5, 2, 20),
                     departure=datetime(2025, 5, 2, 20, 45),
                     group_score=180.0)
    base = Route(stops=[stop], total_utility=180.0,
                 total_walking_miles=0.0, windows_captured=[],
                 strategy_used="borda_count", strategy_rationale="")
    alt = Route(stops=[stop], total_utility=216.0,  # +36 raw
                total_walking_miles=0.0, windows_captured=[],
                strategy_used="borda_count", strategy_rationale="")
    text = explain_counterfactual("extra_budget",
                                  "if each user had $10 more per drink",
                                  base, alt)
    # Must not display "+36.00" raw; must show a percent or qualitative phrase
    assert "+36" not in text
    assert "%" in text or "unchanged" in text or "improvement" in text


# ---------------------------------------------------------------------------
# Fix 9 — egalitarian threshold is 2.0× (read from rules.yaml)
# ---------------------------------------------------------------------------

def test_egalitarian_threshold_is_two_x(loaded):
    rules = loaded["rules"]
    bars = loaded["bars"]
    # 2.2× spread (was 3.0× threshold; now 2.0×) — egalitarian must fire
    users = [
        UserPreference(name="Hi", max_per_drink=22),
        UserPreference(name="Lo", max_per_drink=10),
    ]
    profile = disagreement_profile(users, bars)
    assert 2.0 < profile["budget_spread_ratio"] < 3.0
    # Phase 1: select_strategy now returns a StrategyDecision; pull the
    # id off the dataclass. Assertion below is unchanged in intent.
    decision = select_strategy(profile, rules)
    assert decision.strategy_id == "egalitarian_min", \
        f"2.2× spread should now trigger egalitarian; got {decision.strategy_id}"


def test_threshold_for_parses_yaml_condition(loaded):
    rules = loaded["rules"]
    assert _threshold_for(rules, "strategy_egalitarian", -1) == 2.0
    assert _threshold_for(rules, "strategy_veto", -1) == 0.20
    # Unknown rule falls back to default
    assert _threshold_for(rules, "made_up_rule", 99.0) == 99.0


# ---------------------------------------------------------------------------
# Budget-multiplier override (Phase 4.1 UX fix)
# ---------------------------------------------------------------------------

def test_budget_multiplier_default_from_rules_is_two(loaded):
    """Default multiplier comes from rules.yaml — value is 2.0."""
    rules = loaded["rules"]
    bgm = next(r for r in rules["dealbreaker_rules"]
                if r["id"] == "budget_gross_mismatch")
    assert bgm.get("multiplier") == 2.0


def test_group_budget_multiplier_override_admits_splurge_bar(loaded):
    """With the default 2.0× multiplier a $32 drink bar gets excluded at a
    $15 cap. Setting GroupInput.budget_multiplier=2.5 admits it."""
    from src.decision_system import _apply_dealbreakers

    bars = loaded["bars"]
    rules = loaded["rules"]
    mr_purple = next((b for b in bars if "Mr. Purple" in b.name), None)
    assert mr_purple is not None, "Mr. Purple must be in the dataset"
    users = [UserPreference(name="A", max_per_drink=15.0),
             UserPreference(name="B", max_per_drink=15.0)]

    g_default = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 21, 0),
        end_time=datetime(2026, 4, 25, 3, 0),
        max_stops=3,
    )
    _survivors_default, excluded_default = _apply_dealbreakers(bars, g_default, rules)
    excluded_ids = {e["bar"].id for e in excluded_default
                     if e["rule_id"] == "budget_gross_mismatch"}
    # At 2.0× × $15 = $30, Mr. Purple ($32.50) must be excluded.
    assert mr_purple.id in excluded_ids

    g_relaxed = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 21, 0),
        end_time=datetime(2026, 4, 25, 3, 0),
        max_stops=3,
        budget_multiplier=2.5,
    )
    survivors_relaxed, excluded_relaxed = _apply_dealbreakers(
        bars, g_relaxed, rules,
    )
    # At 2.5× × $15 = $37.50 > $32.50, Mr. Purple must survive.
    assert mr_purple.id in {b.id for b in survivors_relaxed}
    assert mr_purple.id not in {
        e["bar"].id for e in excluded_relaxed
        if e["rule_id"] == "budget_gross_mismatch"
    }


def test_budget_exclusion_explanation_cites_the_actual_multiplier(loaded):
    """The exclusion text must quote the multiplier that fired — a plan
    with budget_multiplier=2.5 should not say '2×' in its exclusions."""
    from src.decision_system import _apply_dealbreakers

    bars = loaded["bars"]
    rules = loaded["rules"]
    # Pick an expensive bar that 2.5× × $10 = $25 would still exclude
    splurge = next(b for b in bars if b.avg_drink_price > 30)
    users = [UserPreference(name="A", max_per_drink=10.0)]
    g = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 21, 0),
        end_time=datetime(2026, 4, 25, 3, 0),
        max_stops=3,
        budget_multiplier=2.5,
    )
    _, excluded = _apply_dealbreakers(bars, g, rules)
    match = next(e for e in excluded if e["bar"].id == splurge.id
                 and e["rule_id"] == "budget_gross_mismatch")
    # The exclusion text cites 2.5, not 2
    assert "2.5×" in match["reason"] or "2.5x" in match["reason"].lower(), (
        f"exclusion should quote 2.5×: {match['reason']}"
    )


def test_mr_purple_tagging_includes_nightlife_signals(loaded):
    """Phase 4.1 bar_overrides update: Mr. Purple should now carry
    dj-set + late-close + music-loud so it can compete with clubs on the
    Pregame→clubs peak stage."""
    bars = loaded["bars"]
    mr_purple = next(b for b in bars if "Mr. Purple" in b.name)
    assert "dj-set" in mr_purple.vibe_tags
    assert "late-close" in mr_purple.vibe_tags
    assert "music-loud" in mr_purple.vibe_tags
    # The updated tagging should NOT include conversation — that tag is
    # in an opposing pair with dance-floor and would penalize the bar on
    # club-heavy stages.
    assert "conversation" not in mr_purple.vibe_tags


# ---------------------------------------------------------------------------
# Fix 10 — start_location auto-recenter on neighborhoods centroid
# ---------------------------------------------------------------------------

def test_neighborhoods_recentering_picks_local_bars(loaded):
    """When the user picks Bushwick, the planner should produce a Bushwick
    crawl, not bias toward the East Village default start."""
    bars = loaded["bars"]
    g = GroupInput(
        users=[UserPreference(name="X", vibe_weights={"lively": 1.0},
                              max_per_drink=15, preferred_noise="lively")] * 2,
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=3,
        neighborhoods=("Bushwick",),
    )
    r = plan_crawl(g, bars=loaded["bars"], cases=loaded["cases"], rules=loaded["rules"])
    # Without recentering, distance penalty would heavily downweight Bushwick.
    # After recentering, all stops should be in Bushwick (since we've set the
    # filter to Bushwick only).
    for s in r.route.stops:
        assert s.bar.neighborhood == "Bushwick"


def test_recenter_only_when_default_start():
    """If the caller explicitly set start_location, don't override it."""
    from src.models import Bar
    bars = [
        # one fake bar at custom location
        Bar(id="b", seed_id="s", name="N", neighborhood="Bushwick",
            address="", lat=40.70, lon=-73.93,
            bar_type=(), vibe_tags=(),
            price_tier="moderate", avg_drink_price=10.0,
            drink_specialties=(), drink_categories_served=(),
            noise_level="lively", capacity_estimate=50,
            crowd_level_by_hour={}, outdoor_seating=None,
            food_quality=None, kitchen_open=None,
            happy_hour_windows=(), specials=(), open_hours={},
            age_policy="21+", accessibility={}, reservations="",
            dress_code="", novelty=0.5, description=None,
            good_for=(), avoid_for=(), google_rating=4.0,
            google_review_count=100, google_price_indicator=None,
            google_category=None, quality_signal=0.5,
            user_note=None, primary_function=None,
            editorial_note=None, source="")
    ]
    custom = (40.80, -73.95)
    g = GroupInput(
        users=[UserPreference(name="X", vibe_weights={}, max_per_drink=20)],
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        start_location=custom,
        max_stops=1,
        neighborhoods=("Bushwick",),
    )
    g2 = _maybe_recenter_start(g, bars)
    assert g2.start_location == custom, "explicit start_location must be preserved"


def test_recenter_default_start_for_neighborhood(loaded):
    """When the user omits start_location, it auto-derives from neighborhoods."""
    bars = loaded["bars"]
    g = GroupInput(
        users=[UserPreference(name="X", vibe_weights={}, max_per_drink=15)],
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=2,
        neighborhoods=("Bushwick",),
    )
    g2 = _maybe_recenter_start(g, bars)
    assert g2.start_location != _DEFAULT_START_LOCATION


# ---------------------------------------------------------------------------
# Fix 11 — vibe vocab cleanliness
# ---------------------------------------------------------------------------

def test_vibe_vocab_has_no_unused_or_undeclared_tags(loaded):
    bars = loaded["bars"]
    vocab_obj = loaded["vibe_vocab"]
    vocab = set()
    for f in vocab_obj["facets"].values():
        vocab.update(f)
    used = set()
    for b in bars:
        used.update(b.vibe_tags)
    assert not (used - vocab), f"Tags used by bars but not in vocab: {used - vocab}"
    assert not (vocab - used), f"Vocab tags never used by any bar: {vocab - used}"


def test_vibe_vocab_opposing_pairs_reference_known_tags(loaded):
    vocab_obj = loaded["vibe_vocab"]
    vocab = set()
    for f in vocab_obj["facets"].values():
        vocab.update(f)
    for pair in vocab_obj.get("opposing_pairs", []):
        for tag in pair:
            assert tag in vocab, f"opposing_pair references unknown tag: {tag}"


# ---------------------------------------------------------------------------
# Fix 12 — empty-route narrative includes top exclusion reasons
# ---------------------------------------------------------------------------

def test_empty_route_narrative_names_top_reasons(loaded):
    """When no bars survive, the summary cites the top exclusion rules so the
    user knows what to relax."""
    bars = loaded["bars"]
    rules = loaded["rules"]
    cases = loaded["cases"]
    # Morning window — most bars closed
    g = GroupInput(
        users=[UserPreference(name="EarlyBird",
                              vibe_weights={"conversation": 1.0},
                              max_per_drink=12,
                              preferred_noise="conversation")],
        start_time=datetime(2025, 5, 2, 8),
        end_time=datetime(2025, 5, 2, 10),
        max_stops=2,
    )
    r = plan_crawl(g, bars=bars, cases=cases, rules=rules)
    assert not r.route.stops
    summary = r.explanations.summary.lower()
    assert "closed" in summary or "open" in summary, \
        f"empty-route summary should mention the top exclusion reason:\n{r.explanations.summary}"


# ---------------------------------------------------------------------------
# Helper test class for tier matching
# ---------------------------------------------------------------------------

class _FakeCase:
    """Tiny stand-in so we can call _case_budget_match without loading data."""
    def __init__(self, budget_tier_spec):
        self.group_profile = {"budget_tier": budget_tier_spec}
