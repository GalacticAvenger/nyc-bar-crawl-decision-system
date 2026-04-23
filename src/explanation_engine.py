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

from .argument import Argument, Premise, render_argument
from .models import (
    AdaptedCase, Adaptation, Bar, Explanation, GroupInput, Route, RouteStop,
    RunnerUp, Score, StrategyDecision, UserPreference,
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
                     rules: dict,
                     decision: Optional[StrategyDecision] = None) -> str:
    """One paragraph: which aggregation strategy was used and why.
    Always names the strategy, the rule that fired, and one profile signal.

    Phase 2: when a `decision` is supplied, build + render a structured
    Argument (same shape as per-stop Arguments). The old branch-per-rule
    templates remain as a fallback for callers that don't have the
    decision object handy (older tests, etc.)."""
    if decision is not None:
        return render_argument(
            build_strategy_argument(decision, profile, users, rules)
        )
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
# Structured Argument builders (Phase 2) — assemble the reasoning produced
# upstream into an Argument dataclass; render_argument turns it into prose.
# These are the preferred entry points; explain_stop_legacy (below) stays
# as a fallback until the Argument renderer has been eyeballed in enough
# scenarios.
# ---------------------------------------------------------------------------

def _averaged_weighted_contributions(
    bar_id: str,
    per_user_scores: dict[str, dict[str, "Score"]],
) -> dict[str, float]:
    """Average the per-criterion weighted contribution across users who
    scored this bar. Returns an empty dict when no user scored it."""
    agg: dict[str, float] = {}
    n = 0
    for u_scores in per_user_scores.values():
        if bar_id in u_scores:
            n += 1
            for c, v in u_scores[bar_id].weighted_contributions.items():
                agg[c] = agg.get(c, 0.0) + v
    if n == 0:
        return {}
    return {c: v / n for c, v in agg.items()}


def _bar_evidence(bar, criterion: str) -> str:
    """Concrete evidence string for each criterion — the data point the
    premise is citing, not just the score."""
    if criterion == "budget":
        return f"~${bar.avg_drink_price:.0f}/drink, {bar.price_tier} tier"
    if criterion == "vibe":
        tags = ", ".join(bar.vibe_tags[:3]) if bar.vibe_tags else "no tags"
        return f"tags: {tags}"
    if criterion == "noise":
        return f"{phrase_for(bar.noise_level, 'noise')}"
    if criterion == "distance":
        return "close walk from the previous stop"
    if criterion == "drink_match":
        drinks = ", ".join(bar.drink_categories_served[:3])
        return f"serves {drinks}"
    if criterion == "happy_hour_active":
        if bar.happy_hour_windows:
            return f"HH: {bar.happy_hour_windows[0].details or 'active at arrival'}"
        return "happy hour active"
    if criterion == "specials_match":
        if bar.specials:
            return f"special: {bar.specials[0].kind.replace('_', ' ')}"
        return "event active at arrival"
    if criterion == "crowd_fit":
        return "the right crowd energy at arrival"
    if criterion == "novelty":
        return "a distinctive, less-obvious pick"
    if criterion == "quality_signal":
        return (f"{bar.google_rating}★ over "
                f"{bar.google_review_count:,} reviews")
    return criterion.replace("_", " ")


def build_stop_argument(
    idx: int,
    stop: RouteStop,
    route: Route,
    per_user_scores: dict[str, dict[str, Score]],
    runner_up: Optional[RunnerUp],
    rules: dict,
    users: Optional[list[UserPreference]] = None,
) -> Argument:
    """Assemble a structured Argument for why `stop.bar` was chosen.

    Pulls directly from the already-computed traces (per_user_scores,
    runner_up, weighted_contributions) — nothing is re-derived. The
    returned Argument has:
      * conclusion — "We chose <bar> for stop N."
      * supporting — top-3 averaged weighted contributions as premises
      * decisive_premise — the highest-magnitude supporting premise
      * opposing — budget-dishonesty premises (one per over-budget user)
        plus a premise when the runner-up would beat the winner on a
        single criterion
      * sacrifice — named when an over-budget user exists or a runner-up
        would have served one user better
      * runner_up — name only when relative_gap ≤ 0.10
    """
    bar = stop.bar

    # --- supporting premises from averaged weighted contributions ------------
    avg_contribs = _averaged_weighted_contributions(bar.id, per_user_scores)
    # Skip criteria with zero or trivial contribution.
    ranked = sorted(
        ((c, v) for c, v in avg_contribs.items() if v > 1e-6),
        key=lambda kv: -kv[1],
    )
    top_contribs = ranked[:3]

    # Over-budget users surfaced as opposing premises — kept honest
    # regardless of the weighted-contribution ranking.
    over_budget: list[UserPreference] = []
    if users:
        for u in users:
            if u.max_per_drink and bar.avg_drink_price > u.max_per_drink + 1e-6:
                over_budget.append(u)

    # Build supporting premises.
    supporting: list[Premise] = []
    for c, v in top_contribs:
        # If 'budget' headlines but someone's over cap, demote — it's not
        # actually supportive for the group (that premise lives in
        # `opposing` below).
        if c == "budget" and over_budget:
            continue
        supporting.append(Premise(
            subject="the group",
            criterion=c,
            direction="supports",
            magnitude=min(1.0, max(0.0, v)),
            evidence=_bar_evidence(bar, c),
        ))

    # Build opposing premises.
    opposing: list[Premise] = []
    sacrifice: Optional[str] = None
    for u in over_budget:
        opposing.append(Premise(
            subject=u.name,
            criterion="budget",
            direction="opposes",
            magnitude=0.5,  # prominent but not dominating
            evidence=(f"~${bar.avg_drink_price:.0f}/drink over "
                      f"{u.name}'s ${u.max_per_drink:.0f} cap"),
        ))
    if over_budget:
        if len(over_budget) == 1:
            sacrifice = (f"{over_budget[0].name} is paying over their cap at "
                         f"this stop")
        else:
            named = ", ".join(u.name for u in over_budget[:-1])
            named += f" and {over_budget[-1].name}"
            sacrifice = f"{named} are paying over their caps at this stop"

    # If the runner-up is close, surface a single-criterion
    # where-it-would-have-won as a soft opposing premise (the
    # counterfactual honesty move).
    runner_up_name: Optional[str] = None
    if runner_up is not None and runner_up.bar.id != bar.id:
        rel_gap = getattr(runner_up, "relative_gap", None)
        is_close = (rel_gap is not None and rel_gap <= 0.10)
        if rel_gap is None and 0 <= runner_up.gap < 0.20:
            is_close = True
        if is_close:
            runner_up_name = runner_up.bar.name
            # Cite the single criterion where the runner-up beats the winner
            # most (unlock_hint is already computed; here we add the premise
            # for the Argument structure).
            if runner_up.gap_criteria:
                best_c = max(runner_up.gap_criteria,
                             key=lambda c: runner_up.gap_criteria[c])
                if runner_up.gap_criteria[best_c] > 0:
                    opposing.append(Premise(
                        subject=runner_up.bar.name,
                        criterion=best_c,
                        direction="opposes",
                        magnitude=min(0.4, float(rel_gap or 0.0) + 0.2),
                        evidence=(f"runner-up wins on {best_c.replace('_', ' ')}"
                                  f" by {runner_up.gap_criteria[best_c]:.2f}"),
                    ))
                    if sacrifice is None and runner_up.unlock_hint:
                        hint = runner_up.unlock_hint
                        if hint.startswith("("):
                            sacrifice = f"{runner_up.bar.name} {hint.strip('()')}"
                        else:
                            sacrifice = (f"would have edged ahead if you'd "
                                         f"{hint}")

    # Decisive premise = the top-scored criterion premise, computed BEFORE
    # we inject editorial/personal premises. Upstream criteria reflect the
    # actual MCDA decision; the additions below are auxiliary colour that
    # should not overwrite the causal reason.
    decisive: Optional[Premise] = supporting[0] if supporting else None

    # Bar-level editorial signals that the legacy explanation surfaced —
    # ported into the Argument shape as auxiliary supporting premises with
    # magnitudes high enough to reliably land in render_argument's top-N,
    # but never so high they unseat `decisive`.
    if bar.user_note:
        supporting.insert(
            min(1, len(supporting)),
            Premise(
                subject="you",
                criterion="user_note",
                direction="supports",
                magnitude=0.35,
                evidence=bar.user_note,
            ),
        )
    if stop.temporal_bonuses_captured:
        w = stop.temporal_bonuses_captured[0]
        details = w.details or w.kind.replace("_", " ")
        supporting.append(Premise(
            subject="the group",
            criterion="temporal_window",
            direction="supports",
            magnitude=0.25,
            evidence=f"{w.kind.replace('_', ' ')} ({details})",
        ))
    if bar.quality_signal > 0.75:
        supporting.append(Premise(
            subject="the group",
            criterion="quality_consensus",
            direction="supports",
            magnitude=0.2,
            evidence=(f"{bar.google_rating}★ over "
                      f"{bar.google_review_count:,} reviews"),
        ))

    # Dominant-user framing (legacy parity). We want the specific user
    # named in prose whenever 2+ users are present — this is substantive
    # evidence a real person was pulled here. Placed late in the list so
    # it appears after the main reason + user_note but still within the
    # renderer's top-N.
    dom_user = _dominant_user_for_bar(bar.id, per_user_scores)
    n_users = len(per_user_scores)
    if dom_user and n_users > 1:
        if n_users == 2:
            other = next((u for u in per_user_scores if u != dom_user), None)
            phrasing = f"higher than {other}" if other else "highest here"
        else:
            phrasing = f"highest of the {n_users}"
        supporting.append(Premise(
            subject=dom_user,
            criterion="dominant_user",
            direction="supports",
            magnitude=0.3,
            evidence=phrasing,
        ))

    # Stop-opener rewrites the conclusion for natural reading order.
    at_time = _format_time(stop.arrival)
    price = phrase_for(bar.price_tier, "price")
    noise = phrase_for(bar.noise_level, "noise")
    if idx == 0:
        conclusion = (f"We open at **{bar.name}** at {at_time}, a {price}, "
                      f"{noise} spot in {bar.neighborhood}")
    elif idx == len(route.stops) - 1 and len(route.stops) > 1:
        conclusion = (f"Closing at **{bar.name}** at {at_time}, a {price}, "
                      f"{noise} spot in {bar.neighborhood}")
    else:
        conclusion = (f"Then **{bar.name}** at {at_time}, a {price}, "
                      f"{noise} spot in {bar.neighborhood}")

    return Argument(
        conclusion=conclusion,
        supporting=supporting,
        opposing=opposing,
        decisive_premise=decisive,
        sacrifice=sacrifice,
        runner_up=runner_up_name,
    )


def build_strategy_argument(
    decision: StrategyDecision,
    profile: dict,
    users: list[UserPreference],
    rules: dict,
) -> Argument:
    """Assemble an Argument for the meta-selector's choice.

    Pulls directly from the StrategyDecision's `triggering_profile_signal`
    and `considered_alternatives` (populated in Phase 1). The decisive
    premise is the triggering signal; opposing premises are the nearest
    losing alternatives.
    """
    narrative = decision.narrative_name
    conclusion = f"We used **{narrative}** to aggregate {len(users)} preferences"

    supporting: list[Premise] = [
        Premise(
            subject="the group",
            criterion=_metric_for_rule(decision.triggering_rule_id),
            direction="supports",
            magnitude=0.7,  # triggering signal is load-bearing
            evidence=decision.triggering_profile_signal,
        ),
    ]
    # applies_when is a broader English framing — include as a second
    # supporting premise so the rendered prose can echo it when the
    # decisive signal alone reads too numeric.
    if decision.applies_when:
        supporting.append(Premise(
            subject="the group",
            criterion="aligned_preferences"
                      if decision.strategy_id == "utilitarian_sum"
                      else _metric_for_rule(decision.triggering_rule_id),
            direction="supports",
            magnitude=0.4,
            evidence=decision.applies_when,
        ))

    # Opposing premises from considered_alternatives: the two strongest
    # near-misses. Use the dedicated "losing_alternative" criterion so the
    # rendered text names the strategy that didn't fire (the generic
    # strategy-metric renderers ignore the subject).
    opposing: list[Premise] = []
    for sid, rank, why in decision.considered_alternatives[:2]:
        opposing.append(Premise(
            subject=sid.replace("_", " "),
            criterion="losing_alternative",
            direction="opposes",
            magnitude=0.2,
            evidence=f"rank {rank}; {why}",
        ))

    return Argument(
        conclusion=conclusion,
        supporting=supporting,
        opposing=opposing,
        decisive_premise=supporting[0],
        sacrifice=None,
        runner_up=None,
    )


def _metric_for_rule(rule_id: str) -> str:
    """Map a triggering rule_id to the profile metric it reads. Used to
    label the decisive premise in a strategy Argument."""
    return {
        "strategy_veto":         "dealbreaker_density",
        "strategy_egalitarian":  "budget_spread_ratio",
        "strategy_copeland":     "vibe_variance",
        "strategy_borda":        "max_preference_intensity",
        "strategy_utilitarian":  "aligned_preferences",
    }.get(rule_id, "aligned_preferences")


def build_cbr_argument(adapted: AdaptedCase,
                       rules: dict) -> Argument:
    """Argument for the CBR retrieve+adapt step.

    Conclusion: "This resembles the {case} archetype, adapted for your group."
    Supporting: one premise per Adaptation (the audit log from adapt_case)
                + a decisive premise that cites the strongest similarity
                feature from the breakdown.
    Opposing:   a single soft premise when the similarity is below the
                weak-match threshold — the honesty move that flags a
                retrieval the caller shouldn't over-trust.

    Numbers are read from `rules.cbr_explanation` (with safe defaults) so
    the weak-match threshold is tunable without touching code.
    """
    # Decisive: the strongest similarity feature in the breakdown.
    if adapted.similarity_breakdown:
        best_feature = max(adapted.similarity_breakdown,
                           key=lambda k: adapted.similarity_breakdown[k])
        best_value = adapted.similarity_breakdown[best_feature]
        decisive = Premise(
            subject="the group",
            criterion="cbr_similarity",
            direction="supports",
            magnitude=min(1.0, max(0.0, best_value)),
            evidence=(f"{best_feature} (score {best_value:.2f}, "
                      f"overall similarity {adapted.similarity:.2f})"),
        )
    else:
        decisive = Premise(
            subject="the group",
            criterion="cbr_similarity",
            direction="supports",
            magnitude=adapted.similarity,
            evidence=f"overall similarity {adapted.similarity:.2f}",
        )

    supporting: list[Premise] = [decisive]
    for ad in adapted.adaptations[:4]:  # cap so the prose stays compact
        supporting.append(Premise(
            subject="the planner",
            criterion="cbr_adaptation",
            direction="supports",
            magnitude=0.3,
            evidence=ad.reason,
        ))

    weak_threshold = (rules.get("cbr_explanation", {})
                           .get("weak_match_threshold", 0.55))
    opposing: list[Premise] = []
    if adapted.similarity < weak_threshold:
        opposing.append(Premise(
            subject="the archetype",
            criterion="cbr_weak_match",
            direction="opposes",
            magnitude=0.3,
            evidence=(f"overall similarity {adapted.similarity:.2f} "
                      f"below {weak_threshold:.2f} — take the framing loosely"),
        ))

    return Argument(
        conclusion=(f"This plan resembles our **{adapted.source_case_name}** "
                    f"archetype, adapted for your group"),
        supporting=supporting,
        opposing=opposing,
        decisive_premise=decisive,
        sacrifice=None,
        runner_up=None,
    )


def _metric_for_strategy(strategy_id: str) -> str:
    return {
        "approval_veto":     "dealbreaker_density",
        "egalitarian_min":   "budget_spread_ratio",
        "copeland_pairwise": "vibe_variance",
        "borda_count":       "max_preference_intensity",
        "utilitarian_sum":   "aligned_preferences",
    }.get(strategy_id, "aligned_preferences")


# ---------------------------------------------------------------------------
# Per-stop explanation (new — Argument-driven; legacy preserved below)
# ---------------------------------------------------------------------------

def explain_stop(
    idx: int,
    stop: RouteStop,
    route: Route,
    per_user_scores: dict[str, dict[str, Score]],
    runner_up: Optional[RunnerUp],
    rules: dict,
    users: Optional[list[UserPreference]] = None,
) -> str:
    """Argument-driven per-stop explanation. Builds an Argument then
    linearizes it. The OLD implementation is preserved as
    `explain_stop_legacy` for side-by-side comparison.

    Signature is unchanged so existing callers (decision_system.plan_crawl,
    tests) don't break.
    """
    arg = build_stop_argument(idx, stop, route, per_user_scores,
                              runner_up, rules, users=users)
    return render_argument(arg)


def explain_stop_legacy(
    idx: int,
    stop: RouteStop,
    route: Route,
    per_user_scores: dict[str, dict[str, Score]],
    runner_up: Optional[RunnerUp],
    rules: dict,
    users: Optional[list[UserPreference]] = None,
) -> str:
    """≤80 words (soft). Names the bar, one user, concrete attribute(s),
    the top reason it beat its slot competitors, and — if genuinely
    informative — the runner-up with an unlock hint.

    Honesty rules (BUILD_PLAN §10):
      * never assert "fits the budget" without checking each user's cap
      * never hardcode group size in the dominant-user line
      * never quote a runner-up gap that's not in a comparable unit

    Phase 2: superseded by build_stop_argument + render_argument. Kept
    for side-by-side comparison + as a fallback while the new generator
    is validated. A/B spot-checks live in tests/test_argument.py.
    """
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

    # Per-user budget honesty: who is over budget at this bar?
    over_budget = []
    if users:
        for u in users:
            if u.max_per_drink and bar.avg_drink_price > u.max_per_drink + 1e-6:
                over_budget.append(u.name)

    # Lead sentence — vary by stop index + top criterion
    top_c = top_crits[0][0] if top_crits else "vibe"
    reason1 = CRITERION_PHRASES.get(top_c, "scores well")
    # If "fits the budget" is the headline reason but someone's over budget,
    # the headline is dishonest — swap to a neutral fallback and add the
    # disclaimer below.
    if top_c == "budget" and over_budget:
        if len(top_crits) > 1:
            reason1 = CRITERION_PHRASES.get(top_crits[1][0], "scores well overall")
        else:
            reason1 = "scores well overall"

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

    # BUDGET HONESTY: if anyone is over budget, surface it explicitly.
    if over_budget:
        if len(over_budget) == 1:
            extras.append(
                f"Heads-up: at ~${bar.avg_drink_price:.0f}/drink this is over "
                f"{over_budget[0]}'s ${_user_cap(users, over_budget[0]):.0f} cap."
            )
        else:
            named = ", ".join(over_budget[:-1]) + f" and {over_budget[-1]}"
            extras.append(
                f"Heads-up: at ~${bar.avg_drink_price:.0f}/drink this is over "
                f"the cap for {named}."
            )

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

    # Runner-up with unlock — use NORMALIZED gap so the threshold means the
    # same thing under utilitarian, Borda, and Copeland. Treat "close" as
    # the runner-up scoring within 10% of the winner.
    if runner_up and runner_up.bar.id != bar.id:
        rel_gap = getattr(runner_up, "relative_gap", None)
        is_close = (rel_gap is not None and rel_gap <= 0.10)
        # Fall back to absolute gap on utilitarian-shaped scores when relative
        # gap isn't populated (e.g. older callers).
        if rel_gap is None and 0 <= runner_up.gap < 0.20:
            is_close = True
        if is_close:
            ru_name = runner_up.bar.name
            hint = runner_up.unlock_hint
            # `unlock_hint_for` returns a parenthetical when no single criterion
            # tilts the runner-up ahead — don't stitch that into a "if you'd ..."
            # sentence (reads as broken grammar).
            if hint and hint.startswith("("):
                sentences.append(f"Close second: {ru_name} {hint}.")
            elif hint:
                sentences.append(
                    f"Close second: {ru_name} — it would have edged ahead "
                    f"if you'd {hint}."
                )
            else:
                sentences.append(f"Close second: {ru_name}.")

    # Dominant user framing if 2+ users AND a clear winner — rotate by stop idx.
    # Use the actual group size, not a hardcoded number.
    n_users = len(per_user_scores)
    if dom_user and n_users > 1 and idx % 2 == 0:
        if n_users == 2:
            other = next((u for u in per_user_scores if u != dom_user), None)
            if other:
                sentences.append(f"{dom_user} rated this higher than {other}.")
        else:
            sentences.append(f"{dom_user} rated this highest of the {n_users}.")

    return " ".join(s.rstrip(" ") if not s.endswith(".") else s for s in sentences)


def _user_cap(users: Optional[list[UserPreference]], name: str) -> float:
    if not users:
        return 0.0
    for u in users:
        if u.name == name:
            return u.max_per_drink
    return 0.0


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
    # Cap the user-name roll call so it doesn't blow up for 10+ person groups
    if len(group.users) <= 4:
        user_names = ", ".join(u.name for u in group.users)
    else:
        shown = [u.name for u in group.users[:3]]
        user_names = ", ".join(shown) + f", and {len(group.users) - 3} others"
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
        mult = extra.get("multiplier", 2.0)
        return (
            f"{bar.name} was excluded: its average drink price (${bar.avg_drink_price:.0f}) "
            f"is more than {mult:g}× {poorest}'s cap."
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
    if rule_id in ("accessibility_unmet", "accessible_restroom_unmet"):
        need = extra.get("need", "the requested access")
        unknown = extra.get("unknown", False)
        if unknown:
            return (f"{bar.name} was excluded: {need} is unverified for this bar — "
                    f"can't promise it meets the requirement.")
        return f"{bar.name} was excluded: doesn't provide {need}."
    if rule_id == "age_policy_mismatch":
        underage = extra.get("underage_user", "a member of the group")
        return f"{bar.name} was excluded: {bar.age_policy} only and {underage} is under 21."
    return f"{bar.name} was excluded: {reason}"


# ---------------------------------------------------------------------------
# Counterfactual explanation
# ---------------------------------------------------------------------------

def explain_counterfactual(cf_kind: str, cf_description: str,
                           original_route: Route,
                           alt_route: Route) -> str:
    """Describe how an alternative scenario's route differs from the original.

    Utility deltas are reported as a percentage of the base utility — raw
    deltas would be misleading because Borda/Copeland scores live on integer
    rank scales while utilitarian sums are continuous in [0,1].
    """
    if alt_route.is_empty:
        return f"{cf_description.capitalize()}, no feasible crawl would have materialized."

    orig_ids = [s.bar.id for s in original_route.stops]
    alt_ids = [s.bar.id for s in alt_route.stops]

    # Identify new / removed bars
    new = [s.bar.name for s in alt_route.stops if s.bar.id not in orig_ids]
    removed = [s.bar.name for s in original_route.stops if s.bar.id not in alt_ids]

    delta_util = alt_route.total_utility - original_route.total_utility
    pct_str = _format_delta_pct(delta_util, original_route.total_utility)

    pieces = [f"{cf_description.capitalize()},"]
    if not new and not removed and len(alt_ids) == len(orig_ids) and alt_ids == orig_ids:
        # No structural change — vary the wording so 3+ unchanged CFs don't
        # all read identically in the same plan.
        if abs(delta_util) < 0.1:
            verbs = ("the same crawl emerges — same stops, same order.",
                    "no change — the planner would still pick this exact crawl.",
                    "the plan is unchanged.")
            pieces.append(verbs[hash(cf_kind) % len(verbs)])
        else:
            pieces.append(
                f"the same stops in the same order, but the group's overall "
                f"score shifts {pct_str}."
            )
    elif new and removed:
        # Swap: the structure changed even if the score didn't. Phrase the
        # score-change explicitly to avoid sounding contradictory ("swap A for B
        # essentially unchanged" reads as nonsense).
        score_clause = (
            "with similar overall score" if pct_str == "essentially unchanged"
            else f"score change {pct_str}"
        )
        pieces.append(
            f"the plan would swap {', '.join(removed)} for {', '.join(new)} "
            f"({score_clause})."
        )
    elif new:
        pieces.append(f"{', '.join(new)} would be added ({pct_str}).")
    elif removed:
        pieces.append(f"{', '.join(removed)} would be dropped ({pct_str}).")
    else:
        # Same bars, different order
        pieces.append(
            f"the bars stay the same but the order changes ({pct_str})."
        )
    return " ".join(pieces)


def _format_delta_pct(delta: float, base: float) -> str:
    """Format a utility shift as a relative percent of the base. Falls back
    to a qualitative label when the base is near zero (avoids divide-by-zero
    and meaningless huge percentages)."""
    if base is None or abs(base) < 1e-6:
        if abs(delta) < 1e-3:
            return "no measurable change"
        return ("a small improvement" if delta > 0 else "a small drop")
    pct = 100.0 * delta / abs(base)
    if abs(pct) < 1.0:
        return "essentially unchanged"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}% in group score"


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
