"""Case-Based Reasoning: retrieve similar crawl archetypes and adapt them.

Retrieval scores each case against (group_input, implicit goals) on
multiple dimensions (size, budget tier, neighborhood fit, vibe summary).

Adaptation maps a case's abstract `solution_sequence` (bar-types + vibe
profiles) to concrete candidate bars from the current dataset. The router
downstream picks the final route; CBR supplies a warm start and a
narrative anchor for the explanation engine ("this plan resembles our
'LES Speakeasy Ladder' archetype").
"""

from __future__ import annotations

import math
from typing import Optional

from .models import Bar, Case, GroupInput, UserPreference


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

# Tier boundaries are INCLUSIVE on the upper end of cheap/moderate/premium so
# a $8 cap = cheap, $14 = moderate, $20 = premium. This matches the
# qualitative tiers in rules.yaml §qualitative_thresholds.price_tier (which
# also uses min/max with min as the lower bound).
TIER_ORDER = ("cheap", "moderate", "premium", "splurge")


def _budget_tier_of(user_avg_cap: float) -> str:
    if user_avg_cap <= 8: return "cheap"
    if user_avg_cap <= 14: return "moderate"
    if user_avg_cap <= 20: return "premium"
    return "splurge"


def _expand_tier_spec(spec: str) -> set[str]:
    """Convert a case's budget_tier string ("cheap", "moderate_to_premium",
    "moderate to premium", "any") into the set of allowed tier names.

    Substring matching was the old hack; range expansion is what was meant."""
    if not spec:
        return set()
    s = spec.strip().lower().replace(" ", "_")
    if s == "any":
        return set(TIER_ORDER)
    if "_to_" in s:
        lo_s, hi_s = s.split("_to_", 1)
        if lo_s in TIER_ORDER and hi_s in TIER_ORDER:
            lo, hi = TIER_ORDER.index(lo_s), TIER_ORDER.index(hi_s)
            return set(TIER_ORDER[lo:hi + 1])
    if s in TIER_ORDER:
        return {s}
    # Fallback: try comma-separated list, otherwise nothing
    parts = {p.strip() for p in s.replace(",", " ").split() if p.strip() in TIER_ORDER}
    return parts


def _case_size_match(case: Case, group_size: int) -> float:
    size_spec = case.group_profile.get("size")
    if isinstance(size_spec, list) and len(size_spec) == 2:
        lo, hi = size_spec
        if lo <= group_size <= hi:
            return 1.0
        closest = min(abs(group_size - lo), abs(group_size - hi))
        return max(0.0, 1.0 - closest * 0.3)
    if isinstance(size_spec, list) and len(size_spec) == 1:
        # exact size requirement
        target = size_spec[0]
        if group_size == target:
            return 1.0
        return max(0.0, 1.0 - abs(group_size - target) * 0.3)
    return 0.5


def _case_budget_match(case: Case, group_budget_tier: str) -> float:
    expected = _expand_tier_spec(case.group_profile.get("budget_tier", ""))
    if not expected:
        return 0.5  # case is agnostic / unspecified
    if group_budget_tier in expected:
        return 1.0
    # Adjacent tier — partial credit (e.g. group=moderate, case=premium)
    try:
        gi = TIER_ORDER.index(group_budget_tier)
    except ValueError:
        return 0.3
    nearest = min(abs(gi - TIER_ORDER.index(t)) for t in expected
                  if t in TIER_ORDER)
    return max(0.0, 1.0 - 0.35 * nearest)


def _case_neighborhood_match(case: Case, neighborhoods: tuple[str, ...]) -> float:
    case_hoods = case.context.get("start_neighborhoods", [])
    if "any" in case_hoods:
        return 0.7
    if not neighborhoods:
        return 0.5  # agnostic
    if any(nh in case_hoods for nh in neighborhoods):
        return 1.0
    return 0.1


def _case_vibe_match(case: Case, combined_vibes: dict[str, float]) -> float:
    """Cosine-style overlap between the group's normalized vibe weights and
    the union of vibe_profiles in the case's solution_sequence.

    The old version matched user vibes against substrings of a free-form
    summary string ("intimate + sophisticated"), which under-rewarded a
    perfect match (e.g. dive_tour summary = "divey + unpretentious" missed
    the user's "divey" vibe even though it was identical, because the search
    was substring-on-summary, not weight-vector-on-vibe-profile).
    """
    if not combined_vibes:
        return 0.5
    case_profile: dict[str, float] = {}
    for step in case.group_profile.get("_indexed_profile") or _index_solution_profile(case):
        for v, w in step.items():
            case_profile[v] = max(case_profile.get(v, 0.0), w)

    if not case_profile:
        # Fall back to summary-word matching for cases with no solution_sequence
        summary = case.group_profile.get("vibe_summary", "").lower()
        if not summary:
            return 0.5
        words = {w.strip(" +") for w in summary.replace("+", " ").split() if w}
        ranked = sorted(combined_vibes.items(), key=lambda kv: -kv[1])[:4]
        hits = sum(1 for vibe, _ in ranked if vibe in words)
        return min(1.0, hits / 4)

    # Cosine similarity between the two weight vectors
    keys = set(combined_vibes) | set(case_profile)
    dot = sum(combined_vibes.get(k, 0.0) * case_profile.get(k, 0.0) for k in keys)
    n_user = math.sqrt(sum(v * v for v in combined_vibes.values()))
    n_case = math.sqrt(sum(v * v for v in case_profile.values()))
    if n_user == 0 or n_case == 0:
        return 0.5
    cos = dot / (n_user * n_case)
    return max(0.0, min(1.0, cos))


def _index_solution_profile(case: Case) -> list[dict[str, float]]:
    return [step.get("vibe_profile", {}) for step in case.solution_sequence]


def similarity(case: Case, group: GroupInput) -> tuple[float, dict[str, float]]:
    """Return (similarity in [0,1], breakdown dict)."""
    group_size = len(group.users)
    # Average budget
    caps = [u.max_per_drink for u in group.users if u.max_per_drink]
    avg_cap = sum(caps) / len(caps) if caps else 12.0
    budget_tier = _budget_tier_of(avg_cap)

    # Combined vibe weights (simple average)
    combined: dict[str, float] = {}
    for u in group.users:
        for v, w in u.vibe_weights.items():
            combined[v] = combined.get(v, 0.0) + w
    if combined:
        maxv = max(combined.values())
        combined = {v: w / maxv for v, w in combined.items()}

    components = {
        "size": _case_size_match(case, group_size),
        "budget": _case_budget_match(case, budget_tier),
        "neighborhood": _case_neighborhood_match(case, group.neighborhoods),
        "vibe": _case_vibe_match(case, combined),
    }
    # Weighted average. Vibe weight increased — it is the strongest signal
    # for "is this the right archetype for this group".
    weights = {"size": 0.15, "budget": 0.20, "neighborhood": 0.20, "vibe": 0.45}
    total = sum(weights[k] * components[k] for k in components)
    return total, components


def retrieve(group: GroupInput, case_lib: list[Case], top_k: int = 3
             ) -> list[tuple[Case, float, dict]]:
    """Return top_k cases by similarity. Stable order — ties broken by case id."""
    scored = []
    for case in case_lib:
        sim, breakdown = similarity(case, group)
        scored.append((case, sim, breakdown))
    scored.sort(key=lambda t: (-t[1], t[0].id))
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Adaptation
# ---------------------------------------------------------------------------

def _matches_bar_type(bar: Bar, wanted_types: list[str]) -> bool:
    return any(t in bar.bar_type for t in wanted_types)


def _vibe_score_for_profile(bar: Bar, vibe_profile: dict[str, float]) -> float:
    """How well do the bar's vibe_tags match the case's target vibe_profile?"""
    if not vibe_profile:
        return 0.5
    matched = sum(w for v, w in vibe_profile.items() if v in bar.vibe_tags)
    total_w = sum(vibe_profile.values()) or 1.0
    return matched / total_w


def adapt(case: Case, bars: list[Bar], max_per_step: int = 5
          ) -> list[list[Bar]]:
    """For each step in `case.solution_sequence`, return the top-N concrete
    bars that match the step's bar_type AND have the strongest vibe match."""
    adapted: list[list[Bar]] = []
    for step in case.solution_sequence:
        wanted_types = step["bar_type"]
        vibe_profile = step.get("vibe_profile", {})
        scored = [(b, _vibe_score_for_profile(b, vibe_profile))
                  for b in bars if _matches_bar_type(b, wanted_types)]
        # Sort descending by vibe match, then quality_signal for ties
        scored.sort(key=lambda t: (-t[1], -t[0].quality_signal, t[0].name))
        adapted.append([b for b, _ in scored[:max_per_step]])
    return adapted


def warm_start_from_case(
    case: Case,
    bars: list[Bar],
    group: GroupInput,
) -> Optional[list[Bar]]:
    """Pick one bar per step (the top-scored concrete match) as a starting point
    for the router to refine. Returns None if any step has no candidates."""
    adapted = adapt(case, bars, max_per_step=1)
    chosen: list[Bar] = []
    used_ids: set[str] = set()
    for candidates in adapted:
        picked = None
        for b in candidates:
            if b.id not in used_ids:
                picked = b
                break
        if picked is None:
            return None
        used_ids.add(picked.id)
        chosen.append(picked)
    return chosen
