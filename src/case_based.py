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

from typing import Optional

from .models import Bar, Case, GroupInput, UserPreference


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def _budget_tier_of(user_avg_cap: float) -> str:
    if user_avg_cap < 8: return "cheap"
    if user_avg_cap < 14: return "moderate"
    if user_avg_cap < 20: return "premium"
    return "splurge"


def _case_size_match(case: Case, group_size: int) -> float:
    size_spec = case.group_profile.get("size")
    if isinstance(size_spec, list) and len(size_spec) == 2:
        lo, hi = size_spec
        if lo <= group_size <= hi:
            return 1.0
        closest = min(abs(group_size - lo), abs(group_size - hi))
        return max(0.0, 1.0 - closest * 0.3)
    return 0.5


def _case_budget_match(case: Case, group_budget_tier: str) -> float:
    expected = case.group_profile.get("budget_tier", "")
    if group_budget_tier in expected:  # e.g., "moderate_to_premium" contains "moderate"
        return 1.0
    return 0.3


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
    """How well does the aggregate group vibe-weight map to the case's summary?"""
    summary = case.group_profile.get("vibe_summary", "").lower()
    if not summary:
        return 0.5
    words = {w.strip(" +") for w in summary.replace("+", " ").split() if w}
    # Count how many of the top-weighted user vibes are mentioned in the summary
    ranked = sorted(combined_vibes.items(), key=lambda kv: -kv[1])[:4]
    hits = sum(1 for vibe, _ in ranked if vibe in words or any(vibe in w for w in words))
    return min(1.0, hits / 4)


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
    # Weighted average
    weights = {"size": 0.20, "budget": 0.20, "neighborhood": 0.25, "vibe": 0.35}
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
