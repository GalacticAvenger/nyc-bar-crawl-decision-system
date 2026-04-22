"""Phase 2 tests: Argument / Premise dataclasses, build_stop_argument,
build_strategy_argument, and the render_argument linearizer.

Hard constraints (per the phase spec):
  (a) every Argument has a non-empty decisive_premise
  (b) every Argument's supporting premises sum to magnitude >= 0.5 of the
      total weight (supporting + opposing)
  (c) when runner-up relative_gap <= 0.10, rendered output contains a
      "But" clause (the counterfactual honesty move)
  (d) rendered English never contains literal template placeholders —
      any such leak must fail a test, not silently ship
"""

import re
from datetime import datetime

import pytest

from src.argument import Argument, Premise, render_argument, render_premise
from src.data_loader import load_all, load_bars, load_rules
from src.decision_system import plan_crawl
from src.explanation_engine import (
    build_stop_argument, build_strategy_argument, explain_stop,
)
from src.group_aggregation import disagreement_profile, select_strategy
from src.models import (
    GroupInput, Route, RouteStop, RunnerUp, Score, UserPreference,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def loaded():
    d = load_all()
    return {"bars": d["bars"], "cases": d["cases"], "rules": d["rules"]}


def _two_user_group():
    return GroupInput(
        users=[
            UserPreference(
                name="Alice", max_per_drink=18.0,
                vibe_weights={"intimate": 1.0, "conversation": 0.8},
                preferred_drinks=("cocktails",),
            ),
            UserPreference(
                name="Bob", max_per_drink=16.0,
                vibe_weights={"intimate": 0.6, "conversation": 1.0,
                              "cozy": 0.5},
                preferred_drinks=("cocktails", "wine"),
            ),
        ],
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        max_stops=3,
    )


# ---------------------------------------------------------------------------
# Constraint (a) — every Argument has a non-empty decisive_premise
# ---------------------------------------------------------------------------

def test_every_stop_argument_has_decisive_premise(loaded):
    """Every stop in a real plan produces an Argument whose decisive_premise
    is non-empty."""
    result = plan_crawl(_two_user_group(), **loaded)
    assert result.route.stops, "group should plan a route"
    for idx, stop in enumerate(result.route.stops):
        arg = build_stop_argument(
            idx, stop, result.route,
            result.traces["per_user_scores"],
            stop.runner_up, loaded["rules"],
            users=_two_user_group().users,
        )
        assert arg.decisive_premise is not None, (
            f"stop {idx}: decisive_premise missing"
        )
        # Decisive premise must carry evidence, not just a criterion name
        assert arg.decisive_premise.evidence, (
            f"stop {idx}: decisive_premise has no evidence"
        )


def test_strategy_argument_has_decisive_premise(loaded):
    group = _two_user_group()
    profile = disagreement_profile(group.users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    arg = build_strategy_argument(decision, profile, group.users, loaded["rules"])
    assert arg.decisive_premise is not None
    assert arg.decisive_premise.evidence
    # The decisive premise's evidence should echo the triggering signal
    assert decision.triggering_profile_signal in arg.decisive_premise.evidence


# ---------------------------------------------------------------------------
# Constraint (b) — supporting magnitude >= 0.5 of total magnitude
# ---------------------------------------------------------------------------

def test_stop_argument_supporting_magnitude_majority(loaded):
    """A plan we're presenting should lean toward its supporting premises;
    supporting magnitude must be at least half of total magnitude."""
    result = plan_crawl(_two_user_group(), **loaded)
    for idx, stop in enumerate(result.route.stops):
        arg = build_stop_argument(
            idx, stop, result.route,
            result.traces["per_user_scores"],
            stop.runner_up, loaded["rules"],
            users=_two_user_group().users,
        )
        total = arg.total_magnitude()
        if total > 0:
            assert arg.supporting_magnitude() >= 0.5 * total, (
                f"stop {idx}: supporting magnitude "
                f"{arg.supporting_magnitude():.2f} < 0.5 * total {total:.2f}"
            )


# ---------------------------------------------------------------------------
# Constraint (c) — close runner-up triggers a "But" clause
# ---------------------------------------------------------------------------

def test_close_runner_up_renders_but_clause():
    """A stop whose runner-up has relative_gap <= 0.10 must render a 'But'
    sentence — the counterfactual honesty move."""
    bars = load_bars()[:3]
    crits = ["vibe", "budget", "drink_match", "noise", "distance",
             "happy_hour_active", "specials_match", "crowd_fit",
             "novelty", "quality_signal"]
    per_user = {
        "Alice": {
            bars[0].id: Score(bar_id=bars[0].id, user_id="Alice",
                              per_criterion={c: 0.5 for c in crits},
                              weighted_contributions={c: 0.08 for c in crits},
                              total=0.8),
            bars[1].id: Score(bar_id=bars[1].id, user_id="Alice",
                              per_criterion={c: 0.5 for c in crits},
                              weighted_contributions={c: 0.07 for c in crits},
                              total=0.75),
        }
    }
    # Runner-up with tight relative_gap
    ru = RunnerUp(
        bar=bars[1], gap=0.05, gap_criteria={"vibe": 0.15, "noise": 0.02},
        unlock_hint="weighted vibes differently",
        relative_gap=0.06,  # <= 0.10 ⇒ close
    )
    stop = RouteStop(
        bar=bars[0],
        arrival=datetime(2026, 4, 24, 19, 0),
        departure=datetime(2026, 4, 24, 19, 45),
        group_score=0.8,
    )
    route = Route(
        stops=[stop], total_utility=0.8, total_walking_miles=0.0,
        windows_captured=[], strategy_used="utilitarian_sum",
        strategy_rationale="",
    )
    rules = load_rules()
    text = explain_stop(0, stop, route, per_user, ru, rules)
    assert " But " in f" {text} ", (
        f"close runner-up should produce a 'But' clause — got:\n{text}"
    )
    # And the runner-up's name should appear
    assert bars[1].name in text


def test_far_runner_up_does_not_force_but_clause():
    """When relative_gap is wide, no spurious 'But' clause."""
    bars = load_bars()[:3]
    crits = ["vibe", "budget", "drink_match", "noise", "distance",
             "happy_hour_active", "specials_match", "crowd_fit",
             "novelty", "quality_signal"]
    per_user = {
        "Alice": {
            bars[0].id: Score(bar_id=bars[0].id, user_id="Alice",
                              per_criterion={c: 0.5 for c in crits},
                              weighted_contributions={c: 0.08 for c in crits},
                              total=0.8),
            bars[1].id: Score(bar_id=bars[1].id, user_id="Alice",
                              per_criterion={c: 0.5 for c in crits},
                              weighted_contributions={c: 0.04 for c in crits},
                              total=0.4),
        }
    }
    ru = RunnerUp(
        bar=bars[1], gap=0.4, gap_criteria={"vibe": 0.02},
        unlock_hint="weighted vibes differently",
        relative_gap=0.4,  # wide
    )
    stop = RouteStop(
        bar=bars[0], arrival=datetime(2026, 4, 24, 19, 0),
        departure=datetime(2026, 4, 24, 19, 45), group_score=0.8,
    )
    route = Route(
        stops=[stop], total_utility=0.8, total_walking_miles=0.0,
        windows_captured=[], strategy_used="utilitarian_sum",
        strategy_rationale="",
    )
    rules = load_rules()
    text = explain_stop(0, stop, route, per_user, ru, rules)
    # Could still contain "but" in a different context (e.g. inside a
    # verb phrase); we check for the structural sentence prefix.
    assert ". But " not in text, (
        f"far runner-up should not add a 'But' clause — got:\n{text}"
    )


# ---------------------------------------------------------------------------
# Constraint (d) — no template-placeholder leakage
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def _contains_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(text))


def test_render_premise_never_leaks_placeholders(loaded):
    """Render every known criterion in every direction with a realistic
    subject+evidence. The output must never contain a literal '{...}'
    placeholder — that would mean a template slot wasn't filled."""
    from src.argument import _CRITERION_RENDERERS

    subjects = ["the group", "Alice", "alternative: utilitarian sum", "you"]
    evidences = [
        "tags: intimate, conversation", "~$14/drink", "library-quiet",
        "0.8★ over 1,234 reviews", "short walk from East Village",
    ]
    for criterion in _CRITERION_RENDERERS:
        for direction in ("supports", "opposes"):
            for subj in subjects:
                for ev in evidences:
                    p = Premise(subject=subj, criterion=criterion,
                                direction=direction, magnitude=0.3,
                                evidence=ev)
                    out = render_premise(p)
                    assert not _contains_placeholder(out), (
                        f"placeholder leak in render_premise for "
                        f"criterion={criterion}, direction={direction}: {out}"
                    )


def test_full_plan_explanations_never_leak_placeholders(loaded):
    """Run plan_crawl on a realistic group and scan every rendered string
    in the Explanation tree for placeholder leakage."""
    result = plan_crawl(_two_user_group(), **loaded)

    def _walk(node):
        yield node.summary
        for child in node.children:
            yield from _walk(child)

    strings = list(_walk(result.explanations))
    for s in strings:
        assert not _contains_placeholder(s), (
            f"placeholder leak in rendered explanation: {s!r}"
        )


# ---------------------------------------------------------------------------
# Shape checks for the rendered prose
# ---------------------------------------------------------------------------

def test_render_argument_shape_contains_decisive_marker():
    arg = Argument(
        conclusion="We chose Foo",
        supporting=[
            Premise(subject="the group", criterion="vibe",
                    direction="supports", magnitude=0.5, evidence="tags: x"),
            Premise(subject="the group", criterion="quality_signal",
                    direction="supports", magnitude=0.3, evidence="4.5★"),
        ],
        opposing=[],
    )
    arg.decisive_premise = arg.supporting[0]
    text = render_argument(arg)
    assert text.startswith("We chose Foo.")
    assert "The decisive factor:" in text


def test_render_argument_opposing_and_sacrifice():
    arg = Argument(
        conclusion="We chose Foo",
        supporting=[
            Premise(subject="the group", criterion="vibe",
                    direction="supports", magnitude=0.5, evidence="tags: x"),
        ],
        opposing=[
            Premise(subject="Alice", criterion="budget",
                    direction="opposes", magnitude=0.4,
                    evidence="$22/drink over Alice's $18 cap"),
        ],
        sacrifice="Alice is paying over her cap",
    )
    arg.decisive_premise = arg.supporting[0]
    text = render_argument(arg)
    # The "But" lead signals an opposing clause
    assert " But " in f" {text} "
    # Sacrifice appears after an em-dash
    assert "Alice is paying over her cap" in text


def test_render_argument_runner_up_line():
    arg = Argument(
        conclusion="We chose Foo",
        supporting=[
            Premise(subject="the group", criterion="vibe",
                    direction="supports", magnitude=0.5, evidence="tags: x"),
        ],
        runner_up="Bar Baz",
    )
    arg.decisive_premise = arg.supporting[0]
    text = render_argument(arg)
    assert "The closest alternative was Bar Baz." in text


def test_render_argument_raises_on_empty_conclusion():
    arg = Argument(conclusion="")
    with pytest.raises(ValueError):
        render_argument(arg)


# ---------------------------------------------------------------------------
# Strategy Argument + rendering
# ---------------------------------------------------------------------------

def test_strategy_argument_rendered_text_quotes_narrative_name(loaded):
    group = _two_user_group()
    profile = disagreement_profile(group.users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    arg = build_strategy_argument(decision, profile, group.users, loaded["rules"])
    text = render_argument(arg)
    assert decision.narrative_name in text
    # Supporting premise should mention the triggering signal or the
    # applies_when framing — both reference specific group facts.
    assert (decision.triggering_profile_signal in text
            or decision.applies_when in text)


def test_strategy_argument_mentions_losing_alternatives_by_name(loaded):
    """Opposing premises cite the considered_alternatives so the reader can
    see which strategies nearly fired."""
    group = _two_user_group()
    profile = disagreement_profile(group.users, loaded["bars"])
    decision = select_strategy(profile, loaded["rules"])
    arg = build_strategy_argument(decision, profile, group.users, loaded["rules"])
    # At least one opposing premise should name a losing strategy
    assert arg.opposing, "strategy Argument should have opposing premises"
    text = render_argument(arg).lower()
    alt_ids = [sid for sid, _r, _w in decision.considered_alternatives[:2]]
    assert any(
        sid.replace("_", " ") in text for sid in alt_ids
    ), f"rendered strategy text should name a losing alternative: {text}"
