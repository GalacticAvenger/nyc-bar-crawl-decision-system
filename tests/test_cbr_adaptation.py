"""Phase 3 tests: the CBR Revise step — adapt_case, router seed bonus,
and the CBR Argument generator.

Constraints from the phase spec:
  * Adaptation preserves the archetype's vibe arc — stage 1's profile
    still reads as "warm-up" after adaptation even if bars differ.
  * Adaptation must never produce a sequence that violates a hard
    dealbreaker (neighborhood, budget gross mismatch, accessibility).
  * Graceful fallback: stages with no feasible bars are marked
    unadapted; the planner does not crash.
  * Seed sequence nudges routing (soft prior, not hard constraint).
"""

from datetime import datetime

import pytest

from src.case_based import adapt_case, retrieve
from src.data_loader import load_all
from src.decision_system import plan_crawl
from src.models import (
    AdaptedCase, Adaptation, AccessibilityNeeds, GroupInput, UserPreference,
)


@pytest.fixture(scope="module")
def loaded():
    d = load_all()
    return {"bars": d["bars"], "cases": d["cases"], "rules": d["rules"]}


def _dive_group(max_stops=4, neighborhoods=()):
    users = [
        UserPreference(
            name="A", max_per_drink=9,
            vibe_weights={"divey": 1.0, "unpretentious": 0.8},
            preferred_drinks=("beer",),
        ),
        UserPreference(
            name="B", max_per_drink=10,
            vibe_weights={"divey": 0.9, "local-institution": 0.7},
            preferred_drinks=("beer",),
        ),
    ]
    return GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 20, 0),
        end_time=datetime(2026, 4, 25, 1, 0),
        max_stops=max_stops,
        neighborhoods=neighborhoods,
    )


# ---------------------------------------------------------------------------
# Constraint 1: adaptation preserves the vibe arc
# ---------------------------------------------------------------------------

def test_adaptation_preserves_warmup_stage_vibe_signature(loaded):
    """Stage-0's dominant vibe should still be the archetype's warm-up
    vibe after adaptation — we add/inject without overwriting the arc."""
    group = _dive_group(max_stops=4)
    cases = retrieve(group, loaded["cases"], top_k=3)
    dive_case = next(c for c, _s, _b in cases
                     if "dive" in c.name.lower() or "dive" in c.id.lower())
    adapted = adapt_case(dive_case, group, loaded["bars"], loaded["rules"],
                          similarity_value=0.7)
    original_stage_0 = dive_case.solution_sequence[0].get("vibe_profile", {})
    adapted_stage_0 = adapted.adapted_sequence[0].get("vibe_profile", {})
    # Every vibe that had a significant weight in the original should
    # still be present at >= its original weight in the adapted.
    for vibe, weight in original_stage_0.items():
        if weight >= 0.5:
            assert adapted_stage_0.get(vibe, 0.0) >= weight - 1e-9, (
                f"adaptation erased '{vibe}' (was {weight}) from stage 0"
            )


def test_adaptation_length_matches_group_max_stops(loaded):
    group = _dive_group(max_stops=2)  # force trim
    cases = retrieve(group, loaded["cases"], top_k=3)
    dive_case = next(c for c, _s, _b in cases
                     if "dive" in c.name.lower() or "dive" in c.id.lower())
    adapted = adapt_case(dive_case, group, loaded["bars"], loaded["rules"])
    assert len(adapted.adapted_sequence) == 2
    # Adaptations log should record the trim
    assert any(a.field_changed == "solution_sequence.length"
               for a in adapted.adaptations)


def test_adaptation_length_extends_when_group_max_stops_exceeds_case(loaded):
    # Date Night archetype has 2 stops; ask for 4 → pad
    date_case = next(c for c in loaded["cases"] if "date" in c.id.lower())
    group = GroupInput(
        users=[
            UserPreference(name="A", max_per_drink=18,
                             vibe_weights={"date": 1.0, "intimate": 0.8}),
            UserPreference(name="B", max_per_drink=16,
                             vibe_weights={"date": 1.0}),
        ],
        start_time=datetime(2026, 4, 24, 20, 0),
        end_time=datetime(2026, 4, 25, 0, 0),
        max_stops=4,
    )
    adapted = adapt_case(date_case, group, loaded["bars"], loaded["rules"])
    assert len(adapted.adapted_sequence) >= len(date_case.solution_sequence)


# ---------------------------------------------------------------------------
# Constraint 2: adapted sequence never violates hard dealbreakers
# ---------------------------------------------------------------------------

def test_adaptation_respects_neighborhood_constraint(loaded):
    """Archetype start_neighborhoods the group excluded must be
    retargeted, not kept as a silent violation."""
    group = _dive_group(max_stops=4, neighborhoods=("Astoria",))
    cases = retrieve(group, loaded["cases"], top_k=3)
    # Pick a case explicitly targeting a different neighborhood
    lenient_case = next(c for c in loaded["cases"]
                         if c.context.get("start_neighborhoods") and
                            "Astoria" not in c.context["start_neighborhoods"])
    adapted = adapt_case(lenient_case, group, loaded["bars"], loaded["rules"])
    # Either retargeting happened (adaptation logged) or no neighborhoods
    # matched and it's flagged unadapted. Either way, there is NO silent
    # pretense that the original neighborhood still applies.
    neighborhood_events = [a for a in adapted.adaptations
                            if a.field_changed.startswith("context.start_neighborhoods")]
    assert neighborhood_events, (
        "adaptation should explicitly handle the neighborhood mismatch"
    )


def test_adaptation_never_produces_budget_dealbreaker_violation(loaded):
    """A plan built with an adapted CBR seed must still respect the 2×
    budget gross-mismatch rule — the seed is a prior, the dealbreaker
    filter is absolute. End-to-end check."""
    group = _dive_group(max_stops=3)
    result = plan_crawl(group, **loaded)
    poorest = min(group.users, key=lambda u: u.max_per_drink).max_per_drink
    for stop in result.route.stops:
        assert stop.bar.avg_drink_price <= 2 * poorest + 1e-6, (
            f"budget dealbreaker violated at {stop.bar.name}: "
            f"${stop.bar.avg_drink_price} > 2× ${poorest}"
        )


def test_adaptation_never_produces_accessibility_violation(loaded):
    """When the group requires step-free access, no adapted plan can
    include a bar whose step_free is not explicitly True — even if the
    archetype suggested otherwise."""
    users = [UserPreference(
        name="A", max_per_drink=15,
        vibe_weights={"intimate": 0.8, "conversation": 0.9},
        preferred_drinks=("cocktails",),
    )]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
        accessibility_needs=AccessibilityNeeds(step_free=True),
    )
    result = plan_crawl(group, **loaded)
    for stop in result.route.stops:
        assert stop.bar.accessibility.get("step_free") is True, (
            f"step-free required but chose {stop.bar.name} with "
            f"step_free={stop.bar.accessibility.get('step_free')}"
        )


# ---------------------------------------------------------------------------
# Constraint 3: graceful fallback
# ---------------------------------------------------------------------------

def test_adaptation_marks_stages_unadapted_when_no_feasible_bars(loaded):
    """Archetype stage targeting beer_garden + restricted to Lower East
    Side (no beer gardens there in this dataset) — stage should be
    flagged unadapted, not silently kept as if candidates exist."""
    beer_case = next(
        c for c in loaded["cases"]
        if any("beer_garden" in (step.get("bar_type") or [])
                 for step in c.solution_sequence)
    )
    # Force neighborhood where that bar_type isn't available in the dataset.
    group = GroupInput(
        users=[UserPreference(name="A", max_per_drink=15)],
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
        neighborhoods=("Tribeca",),  # unlikely to host beer gardens
    )
    adapted = adapt_case(beer_case, group, loaded["bars"], loaded["rules"])
    # Whatever happens, the call must not crash and must return a valid
    # AdaptedCase — stages with no candidates are flagged.
    assert isinstance(adapted, AdaptedCase)
    if adapted.unadapted_stages:
        # The corresponding adaptations should explain why
        assert any(
            ad.field_changed.endswith(".feasibility")
            for ad in adapted.adaptations
        )


def test_plan_crawl_still_produces_plan_when_adaptation_partial(loaded):
    """The planner must not crash if the adapted case has unadapted
    stages — it treats the seed as a soft prior and plans normally."""
    group = _dive_group(max_stops=3)
    result = plan_crawl(group, **loaded)
    # Either a plan or a reasoned empty; never a traceback
    assert result.route is not None
    if result.route.stops:
        assert all(s.bar for s in result.route.stops)


# ---------------------------------------------------------------------------
# Seed sequence changes routing (soft nudge, not hard constraint)
# ---------------------------------------------------------------------------

def test_cbr_seed_bonus_threshold_is_yaml_configurable(loaded):
    assert "cbr_seed_bonus" in loaded["rules"]["routing_config"]
    # Default magnitude is 0.15 per the YAML; non-zero ensures the
    # nudge actually does something.
    assert loaded["rules"]["routing_config"]["cbr_seed_bonus"] > 0


def test_cbr_seeding_is_not_a_hard_constraint(loaded):
    """Proof by existence: some real plan chooses at least one bar whose
    vibe_tags don't match the top archetype's stage vibes. Verifies the
    seed is a prior, not a constraint."""
    group = _dive_group(max_stops=3)
    result = plan_crawl(group, **loaded)
    if not result.route.stops:
        pytest.skip("no plan produced for this group")
    adapted = result.traces.get("adapted_case")
    assert adapted is not None
    # Construct the union of all seed-stage vibes; some chosen bar should
    # include a tag OUTSIDE that union to prove the seed doesn't pin.
    seed_vibes = set()
    for stage in adapted.adapted_sequence:
        seed_vibes.update((stage.get("vibe_profile") or {}).keys())
    # Find any chosen bar with a tag outside the seed vibes.
    off_seed = [
        s for s in result.route.stops
        if any(t not in seed_vibes for t in s.bar.vibe_tags)
    ]
    assert off_seed, (
        "every chosen bar's tags are inside the seed union — seed looks "
        "like a constraint rather than a prior"
    )


# ---------------------------------------------------------------------------
# CBR Argument rendering
# ---------------------------------------------------------------------------

def test_cbr_argument_in_explanation_tree(loaded):
    """The CBR step should surface as an Explanation child with the
    archetype name and at least one adaptation cited."""
    group = _dive_group(max_stops=4)
    result = plan_crawl(group, **loaded)
    case_children = [c for c in result.explanations.children
                      if c.evidence.get("kind") == "case_match"]
    assert case_children, "CBR child missing from explanation tree"
    text = case_children[0].summary
    adapted = result.traces["adapted_case"]
    assert adapted.source_case_name in text
    # At least one adaptation's reason (or the success_narrative) should
    # be quoted — specific evidence, not a generic "we used CBR" note.
    if adapted.adaptations:
        assert any(ad.reason in text or ad.reason.split(";")[0] in text
                   for ad in adapted.adaptations)


def test_cbr_argument_flags_weak_match_when_similarity_low(loaded):
    """Similarity below the configured threshold should produce a soft
    opposing premise — the honesty move."""
    from src.explanation_engine import build_cbr_argument

    weak = AdaptedCase(
        source_case_id="fake", source_case_name="Fake Archetype",
        adapted_sequence=[{"vibe_profile": {"divey": 0.5}, "role": "warm_up"}],
        adaptations=[Adaptation("solution_sequence.length", 3, 1,
                                  "trimmed")],
        similarity=0.3,  # below default 0.55
        similarity_breakdown={"vibe": 0.3, "budget": 0.3},
    )
    arg = build_cbr_argument(weak, loaded["rules"])
    assert arg.opposing, "weak-match should surface an opposing premise"
    assert "loosely" in " ".join(p.evidence for p in arg.opposing)
