"""Explanation engine — template-based, trace-driven natural language.

Every piece of text this module emits comes from a template with slots
filled by upstream reasoning traces. We never generate free-form text,
never narrate a decision we didn't compute, and never start an explanation
with "Based on your preferences...".

The Quality Bar (BUILD_PLAN §10) requires every explanation to:
  - be specific (names actual bars, users, prices, times)
  - be causal (connects to a rule or score)
  - be honest about trade-offs
  - be counterfactually aware
  - be strategy-aware
  - surface user_notes when applicable
  - cite quality_signal when it was a top contributor
  - be compact (top-level ≤200 words; stop-level ≤80)

Each generator below is careful to hit these.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .models import (
    Bar, Explanation, GroupInput, Route, RouteStop, RunnerUp, Score, UserPreference,
)
from .qualitative import phrase_for, quality_bucket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _top_contributors(score: Score, k: int = 3) -> list[tuple[str, float]]:
    """Return top-k criteria by weighted_contribution."""
    if not score.weighted_contributions:
        return []
    return sorted(score.weighted_contributions.items(), key=lambda kv: -kv[1])[:k]


def _format_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower().replace(":00", "").lstrip("0")


def _average_per_user_score(route: Route, per_user_scores: dict[str, dict[str, Score]],
                            bar_id: str) -> dict[str, Score]:
    """Return per_user scores for a single bar."""
    return {u: scores[bar_id] for u, scores in per_user_scores.items() if bar_id in scores}


def _dominant_user_for_bar(bar_id: str,
                           per_user_scores: dict[str, dict[str, Score]]
                           ) -> Optional[str]:
    """Return the user whose score for this bar is highest — useful for
    "Alice scored this highest" framing."""
    best, best_score = None, -1.0
    for u, scores in per_user_scores.items():
        if bar_id in scores and scores[bar_id].total > best_score:
            best = u
            best_score = scores[bar_id].total
    return best


CRITERION_PHRASES = {
    "vibe":              "matches the vibe you're after",
    "budget":            "fits the budget",
    "drink_match":       "carries the drinks you wanted",
    "noise":             "hits the right noise level",
    "distance":          "is a short walk away",
    "happy_hour_active": "lands in a happy-hour window",
    "specials_match":    "coincides with an event",
    "crowd_fit":         "has the right crowd energy",
    "novelty":           "is distinctive",
    "quality_signal":    "is a widely-loved pick",
}

# Varied lead-ins so multi-stop routes don't read like carbon copies.
STOP_LEAD_VERBS = (
    "We open at",           # 1st stop
    "From there, over to",  # 2nd
    "Then",                 # 3rd
    "Closing at",           # 4th / nightcap
    "After that,",          # 5th
    "Next,",                # 6th
    "Then,",                # 7th
)


def _lead_verb(idx: int, total: int) -> str:
    """Return a lead verb keyed to the stop's position."""
    if idx == 0:
        return "We open at"
    if idx == total - 1 and total > 1:
        return "Closing at"
    return STOP_LEAD_VERBS[min(idx, len(STOP_LEAD_VERBS) - 1)]


# ---------------------------------------------------------------------------
# Strategy explanation
# ---------------------------------------------------------------------------

def explain_strategy(strategy_name: str, rule_fired: str,
                     profile: dict, users: list[UserPreference],
                     rules: dict) -> str:
    """One paragraph: which aggregation strategy was used and why.
    Always names the strategy, the rule that fired, and one profile signal."""
    # Friendly names
    names = {
        "utilitarian_sum":    "utilitarian (sum of individual scores)",
        "egalitarian_min":    "egalitarian — Rawlsian min",
        "borda_count":        "Borda-count ranking",
        "copeland_pairwise":  "Copeland pairwise-majority",
        "approval_veto":      "approval/veto",
    }
    long = names.get(strategy_name, strategy_name)
    g = len(users)

    if rule_fired == "strategy_veto":
        pct = round(profile["dealbreaker_density"] * 100)
        return (
            f"We used {long} because {pct}% of the bars you considered have been "
            f"vetoed by at least one of the {g} of you. Approval/veto makes sure no "
            f"one's dealbreaker gets overridden — the vetoed bars are hard-excluded, "
            f"the rest ranked by approval count."
        )
    if rule_fired == "strategy_egalitarian":
        ratio = round(profile["budget_spread_ratio"], 1)
        poorest = min(users, key=lambda u: u.max_per_drink)
        return (
            f"We used {long} because the group's budget spread is {ratio}× between "
            f"the highest and lowest cap — {poorest.name} has the tightest at "
            f"${int(poorest.max_per_drink)} per drink. Egalitarian aggregation picks "
            f"the bar that makes the least-served member happiest rather than the bar "
            f"that maximizes the sum."
        )
    if rule_fired == "strategy_copeland":
        return (
            f"We used {long} because vibe preferences diverge across the {g} of you "
            f"(variance {profile['vibe_variance']:.2f}). Copeland runs every pair of "
            f"bars through a majority vote and picks whichever wins the most pairwise "
            f"contests — it's Condorcet-robust when tastes split."
        )
    if rule_fired == "strategy_borda":
        intense_user = max(users, key=lambda u: u.intensity())
        return (
            f"We used {long} because {intense_user.name} has a peaked preference "
            f"profile (intensity {profile['max_preference_intensity']:.2f}). Borda "
            f"counts positional rank rather than raw score, so one user's unusually "
            f"strong pull doesn't steamroll the others."
        )
    # default / utilitarian
    return (
        f"We used {long} because the {g} of you are aligned enough to simply maximize "
        f"total group happiness. Preferences cluster together (vibe variance "
        f"{profile['vibe_variance']:.2f}, budget spread {profile['budget_spread_ratio']:.1f}×), "
        f"so the straight sum is the right call — no one gets left behind, no one "
        f"dominates."
    )


# ---------------------------------------------------------------------------
# Per-stop explanation
# ---------------------------------------------------------------------------

def explain_stop(
    idx: int,
    stop: RouteStop,
    route: Route,
    per_user_scores: dict[str, dict[str, Score]],
    runner_up: Optional[RunnerUp],
    rules: dict,
) -> str:
    """≤80 words (soft). Names the bar, one user, concrete attribute(s),
    the top reason it beat its slot competitors, and — if genuinely
    informative — the runner-up with an unlock hint."""
    bar = stop.bar
    total_stops = len(route.stops)
    user_scores = _average_per_user_score(route, per_user_scores, bar.id)
    # Aggregate weighted contributions → pick top 2 distinct criteria.
    agg: dict[str, float] = {}
    for s in user_scores.values():
        for c, v in s.weighted_contributions.items():
            agg[c] = agg.get(c, 0.0) + v
    top_crits = sorted(agg.items(), key=lambda kv: -kv[1])[:3]

    dom_user = _dominant_user_for_bar(bar.id, per_user_scores)
    at_time = _format_time(stop.arrival)
    lead_verb = _lead_verb(idx, total_stops)

    # Lead sentence — vary by stop index + top criterion
    top_c = top_crits[0][0] if top_crits else "vibe"
    reason1 = CRITERION_PHRASES.get(top_c, "scores well")
    price_phrase = phrase_for(bar.price_tier, "price")
    noise_phrase = phrase_for(bar.noise_level, "noise")

    # First-stop framing differs from later stops
    if idx == 0:
        lead = (
            f"{lead_verb} **{bar.name}** ({at_time}) — it {reason1}, "
            f"and it's a {price_phrase}, {noise_phrase} room in {bar.neighborhood}."
        )
    else:
        lead = (
            f"{lead_verb} **{bar.name}** at {at_time}, a {price_phrase} "
            f"{noise_phrase} spot in {bar.neighborhood}. It {reason1}."
        )

    # Secondary — temporal window caught OR second criterion OR quality signal OR user_note
    extras = []
    if stop.temporal_bonuses_captured:
        w = stop.temporal_bonuses_captured[0]
        details = w.details or w.kind.replace("_", " ")
        extras.append(f"Arrival is inside its {w.kind.replace('_', ' ')} ({details}).")

    # Quality signal — surface when it's a real consensus bar
    if bar.quality_signal > 0.75:
        extras.append(
            f"Strong consensus pick: {bar.google_rating}★ over "
            f"{bar.google_review_count:,} reviews."
        )
    elif bar.quality_signal < 0.35 and top_c == "vibe":
        extras.append(
            f"Thin review base ({bar.google_review_count} reviews), "
            f"but it dominated on vibe."
        )

    # User note — always if present (this is the personal signal)
    if bar.user_note:
        extras.append(f"You'd noted: _{bar.user_note}_")

    # Second criterion mention — only if not already covered and different from top_c
    if len(top_crits) > 1 and not extras:
        c2 = top_crits[1][0]
        extras.append(CRITERION_PHRASES.get(c2, "also scores well").capitalize() + ".")

    sentences = [lead] + extras

    # Runner-up with unlock — only include if the gap is small enough to be
    # a genuine alternative (avoid stating runner-ups that are way behind).
    if runner_up and runner_up.bar.id != bar.id and runner_up.gap < 1.5:
        ru_name = runner_up.bar.name
        hint = runner_up.unlock_hint
        if hint:
            sentences.append(f"Close second: {ru_name} — it would have edged ahead if you'd {hint}.")
        else:
            sentences.append(f"Close second: {ru_name} (gap: {runner_up.gap:.2f}).")

    # Dominant user framing if 2+ users AND a clear winner — rotate by stop idx
    if dom_user and len(per_user_scores) > 1 and idx % 2 == 0:
        sentences.append(f"{dom_user} scored this highest of the three.")

    return " ".join(s.rstrip(" ") if not s.endswith(".") else s for s in sentences)


# ---------------------------------------------------------------------------
# Route-level narrative
# ---------------------------------------------------------------------------

def explain_route(route: Route, group: GroupInput, rules: dict) -> str:
    """≤200 words. Summarizes the plan, the strategy used, walking, windows."""
    if not route.stops:
        return ("No route found — none of the candidate bars were feasible for your "
                "window. See the exclusion trace for the hard constraints that blocked "
                "the planner.")

    n = len(route.stops)
    user_names = ", ".join(u.name for u in group.users)
    names = " → ".join(s.bar.name for s in route.stops)
    walking = f"{route.total_walking_miles:.1f} miles of walking"
    windows = len(route.windows_captured)

    lead = (
        f"For {user_names}, here is a {n}-stop crawl: {names}. "
        f"The plan captures {windows} happy-hour/special window{'s' if windows != 1 else ''} "
        f"and involves {walking}."
    )

    # Dominant narrative by neighborhoods + vibes
    hoods = {s.bar.neighborhood for s in route.stops}
    if len(hoods) == 1:
        shape = f"The whole crawl stays in {next(iter(hoods))}."
    else:
        shape = f"The crawl threads through {', '.join(sorted(hoods))}."

    strategy_line = (
        f"Aggregated under the **{route.strategy_used.replace('_', ' ')}** strategy: "
        f"{route.strategy_rationale}"
    )

    return " ".join([lead, shape, strategy_line])


# ---------------------------------------------------------------------------
# Exclusion explanation
# ---------------------------------------------------------------------------

def explain_exclusion(bar: Bar, reason: str, rule_id: str,
                      extra: Optional[dict] = None) -> str:
    """A single-sentence trace of *why* `bar` was excluded, citing the rule."""
    extra = extra or {}
    if rule_id == "user_veto":
        vetoer = extra.get("vetoer", "a member of the group")
        return f"{bar.name} was excluded: {vetoer} vetoed it."
    if rule_id == "budget_gross_mismatch":
        poorest = extra.get("poorest_user", "someone in the group")
        return (
            f"{bar.name} was excluded: its average drink price (${bar.avg_drink_price:.0f}) "
            f"is more than 2× {poorest}'s cap."
        )
    if rule_id == "closed_at_arrival":
        t = extra.get("arrival_time", "the planned arrival")
        return f"{bar.name} was excluded: it isn't open at {t}."
    if rule_id == "neighborhood_excluded":
        hoods = extra.get("allowed", "the requested neighborhoods")
        return f"{bar.name} was excluded: it's in {bar.neighborhood}, outside {hoods}."
    if rule_id == "pareto_dominated":
        dominator = extra.get("dominator_name", "another option")
        return f"{bar.name} was dropped: {dominator} beat it on every criterion."
    return f"{bar.name} was excluded: {reason}"


# ---------------------------------------------------------------------------
# Counterfactual explanation
# ---------------------------------------------------------------------------

def explain_counterfactual(cf_kind: str, cf_description: str,
                           original_route: Route,
                           alt_route: Route) -> str:
    """Describe how an alternative scenario's route differs from the original."""
    if alt_route.is_empty:
        return f"{cf_description.capitalize()}, no feasible crawl would have materialized."

    orig_ids = [s.bar.id for s in original_route.stops]
    alt_ids = [s.bar.id for s in alt_route.stops]

    # Identify new / removed bars
    new = [s.bar.name for s in alt_route.stops if s.bar.id not in orig_ids]
    removed = [s.bar.name for s in original_route.stops if s.bar.id not in alt_ids]

    delta_util = alt_route.total_utility - original_route.total_utility

    pieces = [f"{cf_description.capitalize()},"]
    if not new and not removed and len(alt_ids) == len(orig_ids) and alt_ids == orig_ids:
        pieces.append("the crawl would have been identical —")
        if abs(delta_util) < 0.1:
            pieces.append("the same stops, same order.")
        else:
            pieces.append(f"same stops but total utility would shift by {delta_util:+.2f}.")
    elif new and removed:
        pieces.append(
            f"the plan would swap {', '.join(removed)} for {', '.join(new)} "
            f"(total utility {delta_util:+.2f})."
        )
    elif new:
        pieces.append(f"{', '.join(new)} would be added (+{delta_util:.2f}).")
    elif removed:
        pieces.append(f"{', '.join(removed)} would be dropped ({delta_util:+.2f}).")
    else:
        # Same bars, different order
        pieces.append(
            f"the bars stay the same but the order changes ({delta_util:+.2f} in utility)."
        )
    return " ".join(pieces)


# ---------------------------------------------------------------------------
# Stakeholder table
# ---------------------------------------------------------------------------

def per_user_served_report(route: Route,
                           per_user_scores: dict[str, dict[str, Score]],
                           users: list[UserPreference]
                           ) -> dict[str, dict]:
    """Per-user "how were you served" table. Returns dict[user_name] -> dict."""
    report: dict[str, dict] = {}
    for u in users:
        scores_for_u = per_user_scores.get(u.name, {})
        chosen_scores = [scores_for_u[s.bar.id].total for s in route.stops
                         if s.bar.id in scores_for_u]
        mean = sum(chosen_scores) / len(chosen_scores) if chosen_scores else 0.0
        # How many of u's top-5 bars in the whole candidate set made the route?
        top_5 = sorted(scores_for_u.values(), key=lambda s: -s.total)[:5]
        top_5_in_route = sum(1 for s in top_5 if s.bar_id in {rs.bar.id for rs in route.stops})
        vetoes_respected = all(v not in {rs.bar.id for rs in route.stops} for v in u.vetoes)
        # Budget respect: fraction of stops within user's cap
        in_budget = sum(1 for s in route.stops if s.bar.avg_drink_price <= u.max_per_drink)
        report[u.name] = {
            "mean_score_on_route": round(mean, 3),
            "top5_in_route": f"{top_5_in_route}/5",
            "vetoes_respected": vetoes_respected,
            "in_budget_stops": f"{in_budget}/{len(route.stops)}" if route.stops else "0/0",
        }
    return report


def render_served_table(report: dict[str, dict]) -> str:
    """Plain-text table. Used in notebooks and the writeup."""
    if not report:
        return "(no users)"
    users = list(report.keys())
    fields = ["mean_score_on_route", "top5_in_route", "vetoes_respected", "in_budget_stops"]
    header = "| User | " + " | ".join(f.replace("_", " ") for f in fields) + " |"
    sep = "|" + "---|" * (len(fields) + 1)
    rows = [header, sep]
    for u in users:
        vals = [str(report[u][f]) for f in fields]
        rows.append(f"| {u} | " + " | ".join(vals) + " |")
    return "\n".join(rows)
