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

import copy

from .models import AdaptedCase, Adaptation, Bar, Case, GroupInput, UserPreference


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


# ---------------------------------------------------------------------------
# Adaptation — the Revise step of CBR's R-loop
# ---------------------------------------------------------------------------

def _stage_priority(stage: dict) -> float:
    """Heuristic: a stage's "priority" is the L1 sum of its vibe_profile
    weights. Stages with richer profiles are harder to drop; anaemic stages
    get dropped first when the group's time window forces us to shorten."""
    vp = stage.get("vibe_profile", {}) or {}
    return sum(vp.values())


def _neighborhood_centroid(neighborhood: str, bars: list[Bar]
                           ) -> Optional[tuple[float, float]]:
    """Return (lat, lon) mean over bars in this neighborhood, or None."""
    pts = [(b.lat, b.lon) for b in bars if b.neighborhood == neighborhood]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def _nearest_allowed_neighborhood(
    target_hoods: list[str],
    allowed_hoods: tuple[str, ...],
    bars: list[Bar],
) -> Optional[str]:
    """Given a case's preferred start_neighborhoods and the group's allowed
    list, pick the allowed neighborhood closest to the case's centroid.
    Uses centroids of bars in each neighborhood — no external geodata."""
    import math

    target_centroids = [
        c for c in (_neighborhood_centroid(h, bars) for h in target_hoods)
        if c is not None
    ]
    if not target_centroids:
        return None
    tgt = (sum(c[0] for c in target_centroids) / len(target_centroids),
           sum(c[1] for c in target_centroids) / len(target_centroids))

    best: Optional[tuple[float, str]] = None
    for hood in allowed_hoods:
        c = _neighborhood_centroid(hood, bars)
        if c is None:
            continue
        dist = math.hypot(c[0] - tgt[0], c[1] - tgt[1])
        if best is None or dist < best[0]:
            best = (dist, hood)
    return best[1] if best else None


def _user_must_have_vibes(users: list[UserPreference], min_weight: float = 0.8
                          ) -> list[str]:
    """Vibes any user weights strongly. Used to detect 'must-have' vibes
    the archetype should adapt toward. Threshold is a floor — anything
    above it is treated as load-bearing for that user."""
    out: set[str] = set()
    for u in users:
        for v, w in (u.vibe_weights or {}).items():
            if w >= min_weight:
                out.add(v)
    return sorted(out)


def _stage_best_fit_index(seq: list[dict], candidate_vibes: list[str]
                          ) -> int:
    """Return the index of the stage whose existing vibe_profile has the
    most overlap with a strong correlate of the candidate vibes. Used to
    decide where to inject a group must-have that's absent from the case."""
    if not seq:
        return 0
    best_i, best_score = 0, -1.0
    for i, stage in enumerate(seq):
        vp = stage.get("vibe_profile", {}) or {}
        # Score: how many distinct vibes the stage already carries. A
        # richer stage tolerates a new addition better than a sparse one.
        score = sum(vp.values())
        if score > best_score:
            best_score = score
            best_i = i
    return best_i


def adapt_case(
    case: Case,
    group: GroupInput,
    bars: list[Bar],
    rules: dict,
    similarity_value: float = 0.0,
    similarity_breakdown: Optional[dict[str, float]] = None,
) -> AdaptedCase:
    """Adapt `case` to fit the current group.

    Applies three adaptation kinds in order:

      1. LENGTH     — pad or trim the solution_sequence so its length
                      matches `group.max_stops`. Trimming drops the
                      lowest-priority stage (smallest vibe_profile
                      magnitude); padding repeats the final stage's
                      vibe_profile with role="extended".
      2. VIBE       — if any user has a strong must-have vibe (>= 0.8)
                      that doesn't appear in ANY stage, inject it into
                      the richest stage's vibe_profile.
      3. CONSTRAINT — if the case targets a neighborhood the group
                      excluded, re-target to the nearest allowed
                      neighborhood (centroid distance), recorded on the
                      first stage that referenced it.

    Every change is logged as an Adaptation record so the explanation
    engine can cite it.
    """
    adapted_sequence: list[dict] = copy.deepcopy(case.solution_sequence)
    adaptations: list[Adaptation] = []

    # 1. LENGTH --------------------------------------------------------------
    target_len = max(1, group.max_stops)
    current_len = len(adapted_sequence)
    if current_len > target_len:
        # Drop lowest-priority stages until length matches.
        while len(adapted_sequence) > target_len:
            drop_idx = min(
                range(len(adapted_sequence)),
                key=lambda i: _stage_priority(adapted_sequence[i]),
            )
            removed = adapted_sequence.pop(drop_idx)
            adaptations.append(Adaptation(
                field_changed="solution_sequence.length",
                from_value=current_len,
                to_value=len(adapted_sequence),
                reason=(f"group's max_stops={target_len} is below "
                        f"archetype's {current_len}; dropped stage "
                        f"'{removed.get('role', '?')}' "
                        f"(lowest-priority)"),
            ))
    elif current_len < target_len and adapted_sequence:
        last = adapted_sequence[-1]
        while len(adapted_sequence) < target_len:
            extra = copy.deepcopy(last)
            extra["role"] = "extended"
            adapted_sequence.append(extra)
            adaptations.append(Adaptation(
                field_changed="solution_sequence.length",
                from_value=current_len,
                to_value=len(adapted_sequence),
                reason=(f"group's max_stops={target_len} exceeds "
                        f"archetype's {current_len}; extended by "
                        f"repeating the final stage's vibe_profile"),
            ))

    # 2. VIBE ----------------------------------------------------------------
    must_haves = _user_must_have_vibes(group.users)
    archetype_vibes: set[str] = set()
    for stage in adapted_sequence:
        archetype_vibes.update((stage.get("vibe_profile") or {}).keys())
    for vibe in must_haves:
        if vibe in archetype_vibes:
            continue
        target_i = _stage_best_fit_index(adapted_sequence, [vibe])
        stage = adapted_sequence[target_i]
        vp = dict(stage.get("vibe_profile") or {})
        old_vp = dict(vp)
        vp[vibe] = max(vp.get(vibe, 0.0), 0.7)  # strong but not dominant
        stage["vibe_profile"] = vp
        adaptations.append(Adaptation(
            field_changed=f"solution_sequence[{target_i}].vibe_profile",
            from_value=old_vp,
            to_value=vp,
            reason=(f"group has a must-have vibe '{vibe}' that the "
                    f"archetype didn't include; injected into the "
                    f"'{stage.get('role', f'stage {target_i}')}' stage"),
        ))

    # 3. CONSTRAINT ----------------------------------------------------------
    case_hoods = list(case.context.get("start_neighborhoods") or [])
    if case_hoods and group.neighborhoods:
        allowed = set(group.neighborhoods)
        if not any(h in allowed for h in case_hoods):
            target = _nearest_allowed_neighborhood(
                case_hoods, group.neighborhoods, bars,
            )
            if target is not None:
                adaptations.append(Adaptation(
                    field_changed="context.start_neighborhoods",
                    from_value=case_hoods,
                    to_value=[target],
                    reason=(f"archetype targets {case_hoods} but group "
                            f"restricted to {list(allowed)}; "
                            f"retargeted to nearest allowed "
                            f"neighborhood '{target}'"),
                ))

    # 4. FEASIBILITY CHECK ---------------------------------------------------
    # For each stage, see whether at least one bar in the dataset matches
    # both the bar_type AND doesn't violate a hard dealbreaker already
    # encoded on the group. Stages with no candidates get marked
    # unadapted so the router treats them as a soft prior rather than a
    # required stop.
    unadapted_stages: list[int] = []
    allowed_set = set(group.neighborhoods) if group.neighborhoods else None
    # Use a permissive cap — the router still re-checks every hard rule.
    for i, stage in enumerate(adapted_sequence):
        wanted_types = stage.get("bar_type") or []
        candidates = [b for b in bars
                      if _matches_bar_type(b, wanted_types)
                      and (allowed_set is None or b.neighborhood in allowed_set)]
        if not candidates:
            unadapted_stages.append(i)
            adaptations.append(Adaptation(
                field_changed=f"solution_sequence[{i}].feasibility",
                from_value="feasible",
                to_value="unadapted",
                reason=(f"no bars in the current dataset match stage "
                        f"{i}'s bar_type {wanted_types} under the "
                        f"group's neighborhood constraint; router will "
                        f"treat this stage as a soft prior only"),
            ))

    return AdaptedCase(
        source_case_id=case.id,
        source_case_name=case.name,
        adapted_sequence=adapted_sequence,
        adaptations=adaptations,
        similarity=similarity_value,
        similarity_breakdown=dict(similarity_breakdown or {}),
        unadapted_stages=unadapted_stages,
    )


# ---------------------------------------------------------------------------
# Warm-start (legacy — kept for callers that want concrete bars)
# ---------------------------------------------------------------------------

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
