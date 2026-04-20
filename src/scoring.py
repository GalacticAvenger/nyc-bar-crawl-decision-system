"""Multi-Criteria Decision Analysis scoring engine.

Every decision's "why" is stored in the Score object — per-criterion raw score,
per-criterion weighted contribution, and total. The upstream explanation engine
reads these traces directly; it never re-derives them.

Criteria produced (raw score in [0, 1]):
  vibe             cosine similarity between user vibe weights and bar vibe tags
  budget           exponential penalty above user.max_per_drink
  drink_match      jaccard similarity between user preferred_drinks and bar categories
  noise            ordinal distance from user preferred_noise
  distance         distance penalty from prev stop (1.0 if no prev; decreases with miles)
  happy_hour_active 1 if happy hour active at arrival_hour (else 0)
  specials_match   1 if any special active at arrival_hour
  crowd_fit        closeness between crowd level at arrival_hour and user's implicit preference
  novelty          bar.novelty scaled by user's novelty weight
  quality_signal   bar.quality_signal directly

Unknown/missing context (no arrival hour, no prev location) => neutral score of 0.5
for that criterion, and that fact is recorded in evidence.
"""

from __future__ import annotations

import math
from typing import Optional

from .models import Bar, Score, UserPreference


# ---------------------------------------------------------------------------
# Per-criterion scorers (all return [0, 1])
# ---------------------------------------------------------------------------

def score_vibe(bar: Bar, user: UserPreference, rules: dict) -> float:
    """Cosine similarity between user.vibe_weights and bar.vibe_tags (binary)."""
    if not user.vibe_weights:
        return 0.5
    # Apply opposing-pair penalty to weights
    vocab = rules.get("scoring_defaults", {}).get("vibe_scoring", {})
    # Weights: user's raw weights
    weights = dict(user.vibe_weights)
    # Bar vector: 1 per tag, 0 elsewhere
    dot = sum(weights.get(tag, 0.0) for tag in bar.vibe_tags)
    w_norm = math.sqrt(sum(w * w for w in weights.values()))
    b_norm = math.sqrt(len(bar.vibe_tags))
    if w_norm == 0 or b_norm == 0:
        return 0.5
    cos = dot / (w_norm * b_norm)
    return max(0.0, min(1.0, cos))


def score_budget(bar: Bar, user: UserPreference) -> float:
    """Exponential decay penalty above user.max_per_drink."""
    cap = max(1.0, user.max_per_drink)
    over = max(0.0, bar.avg_drink_price - cap)
    return math.exp(-over / cap)


def score_drink_match(bar: Bar, user: UserPreference) -> float:
    """Jaccard similarity between user's preferred drinks and bar's categories."""
    if not user.preferred_drinks:
        return 0.5
    user_set = set(user.preferred_drinks)
    bar_set = set(bar.drink_categories_served)
    if not bar_set:
        return 0.0
    inter = user_set & bar_set
    union = user_set | bar_set
    return len(inter) / len(union) if union else 0.0


NOISE_ORDER = ("library", "conversation", "lively", "loud", "deafening")


def score_noise(bar: Bar, user: UserPreference) -> float:
    """Ordinal closeness to preferred noise."""
    try:
        u_idx = NOISE_ORDER.index(user.preferred_noise)
        b_idx = NOISE_ORDER.index(bar.noise_level)
    except ValueError:
        return 0.5
    diff = abs(u_idx - b_idx)
    max_diff = len(NOISE_ORDER) - 1
    return 1.0 - (diff / max_diff)


def score_distance(bar: Bar, prev_location: Optional[tuple[float, float]],
                   rules: dict) -> float:
    """Distance penalty: 1.0 if no prev; decreases with miles from prev."""
    if prev_location is None:
        return 1.0
    from .routing import walking_miles  # local import to avoid cycle
    miles = walking_miles(prev_location, (bar.lat, bar.lon))
    cfg = rules.get("walking_and_distance", {})
    per_mile = cfg.get("per_mile_penalty", 0.08)
    penalty = per_mile * miles
    if miles > cfg.get("comfortable_max_miles", 0.6):
        penalty += cfg.get("amplified_per_mile_penalty_over_threshold", 0.20) * (
            miles - cfg["comfortable_max_miles"]
        )
    return max(0.0, 1.0 - penalty)


def _in_window(hour: int, start: str, end: str, day: str, window_days: tuple) -> bool:
    if day not in window_days:
        return False
    s = int(start.split(":")[0])
    e = int(end.split(":")[0])
    # handle past-midnight (e >= 24)
    if e >= 24:
        return s <= hour <= 23 or 0 <= hour < (e - 24)
    return s <= hour < e


def score_happy_hour(bar: Bar, arrival_hour: Optional[int], day: Optional[str]) -> float:
    if arrival_hour is None or day is None:
        return 0.0
    for w in bar.happy_hour_windows:
        if _in_window(arrival_hour, w.start, w.end, day, w.days):
            return 1.0
    return 0.0


def score_specials_match(bar: Bar, arrival_hour: Optional[int], day: Optional[str]) -> float:
    if arrival_hour is None or day is None:
        return 0.0
    for w in bar.specials:
        if _in_window(arrival_hour, w.start, w.end, day, w.days):
            return 1.0
    return 0.0


def score_crowd_fit(bar: Bar, arrival_hour: Optional[int],
                    preferred_crowd: str = "lively") -> float:
    if arrival_hour is None:
        return 0.5
    crowd = bar.crowd_level_by_hour.get(str(arrival_hour), "mellow")
    order = ("dead", "mellow", "lively", "packed", "overflowing")
    try:
        diff = abs(order.index(crowd) - order.index(preferred_crowd))
    except ValueError:
        return 0.5
    return 1.0 - (diff / (len(order) - 1))


def score_novelty(bar: Bar) -> float:
    return bar.novelty


def score_quality_signal(bar: Bar) -> float:
    return bar.quality_signal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

CRITERIA = (
    "vibe", "budget", "drink_match", "noise", "distance",
    "happy_hour_active", "specials_match", "crowd_fit", "novelty", "quality_signal",
)


def normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    """Clip negatives to 0, renormalize to sum=1. If all zero, return uniform."""
    clipped = {c: max(0.0, float(raw.get(c, 0.0))) for c in CRITERIA}
    total = sum(clipped.values())
    if total <= 0:
        return {c: 1.0 / len(CRITERIA) for c in CRITERIA}
    return {c: v / total for c, v in clipped.items()}


def score_bar_for_user(
    bar: Bar,
    user: UserPreference,
    rules: dict,
    arrival_hour: Optional[int] = None,
    day: Optional[str] = None,
    prev_location: Optional[tuple[float, float]] = None,
) -> Score:
    """Score `bar` for `user` given optional temporal/spatial context.

    Missing context yields neutral scores for the affected criteria (noted in
    the Score's evidence via `weighted_contributions`).
    """
    weights = user.criterion_weights or rules["scoring_defaults"]["default_weights"]
    weights = normalize_weights(weights)

    raw = {
        "vibe": score_vibe(bar, user, rules),
        "budget": score_budget(bar, user),
        "drink_match": score_drink_match(bar, user),
        "noise": score_noise(bar, user),
        "distance": score_distance(bar, prev_location, rules),
        "happy_hour_active": score_happy_hour(bar, arrival_hour, day),
        "specials_match": score_specials_match(bar, arrival_hour, day),
        "crowd_fit": score_crowd_fit(bar, arrival_hour),
        "novelty": score_novelty(bar),
        "quality_signal": score_quality_signal(bar),
    }
    weighted = {c: weights[c] * raw[c] for c in CRITERIA}
    total = sum(weighted.values())
    return Score(
        bar_id=bar.id,
        user_id=user.name,
        per_criterion=raw,
        weighted_contributions=weighted,
        total=total,
        temporal_bonus=0.0,
        total_with_bonus=total,
    )


# ---------------------------------------------------------------------------
# Pareto filtering
# ---------------------------------------------------------------------------

def pareto_filter(scores: list[Score]) -> tuple[list[Score], list[tuple[Score, Score]]]:
    """Remove scores strictly dominated on every criterion by some other score.

    Returns (kept, dominated_pairs) where dominated_pairs[i] = (loser, dominator).
    This is used so the explanation engine can say "Bar Y was dropped because
    Bar X beats it on every axis."
    """
    if not scores:
        return [], []
    kept: list[Score] = []
    dominated_pairs: list[tuple[Score, Score]] = []
    for s in scores:
        dominator = None
        for other in scores:
            if other is s:
                continue
            dominates = all(other.per_criterion[c] >= s.per_criterion[c] for c in CRITERIA)
            strictly = any(other.per_criterion[c] > s.per_criterion[c] for c in CRITERIA)
            if dominates and strictly:
                dominator = other
                break
        if dominator is None:
            kept.append(s)
        else:
            dominated_pairs.append((s, dominator))
    return kept, dominated_pairs
