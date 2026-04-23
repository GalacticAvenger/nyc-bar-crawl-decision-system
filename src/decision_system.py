"""Orchestrator — the one-function public API.

`plan_crawl(group, bars, cases, rules)` walks the entire pipeline:

    1. Intake → dealbreaker filter (exclusions carry rule_id + explanation)
    2. Score each surviving bar for each user (MCDA, context-free layer)
    3. Compute disagreement profile → meta-select an aggregation strategy
    4. Aggregate per-user scores into group scores under chosen strategy
    5. Retrieve & adapt the most-similar CBR case (for the explanation)
    6. Route: greedy → 2-opt → exact check (time-window feasible)
    7. Option generation: runner-ups + unlock hints + structural/strategy CFs
    8. Assemble explanation tree + stakeholder table

Every intermediate trace is preserved on PlanResult.traces so the
explanation engine can cite it.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Optional

from .case_based import adapt_case, retrieve
from .data_loader import load_all
from .argument import render_argument
from .explanation_engine import (
    build_cbr_argument, explain_counterfactual, explain_exclusion,
    explain_route, explain_stop, explain_strategy, per_user_served_report,
)
from .group_aggregation import aggregate, disagreement_profile, select_strategy
from .models import (
    AdaptedCase, Bar, Case, Explanation, GroupInput, GroupScore, PlanResult,
    Route, RouteStop, Score, StrategyDecision, UserPreference,
)
from .option_generation import (
    all_structural_counterfactuals, find_runner_ups, strategy_counterfactuals,
    strategy_winner, unlock_analysis,
)
from .routing import best_route, walking_miles
from .scoring import normalize_weights, score_bar_for_user
from .temporal import day_name, is_open


# ---------------------------------------------------------------------------
# Dealbreakers
# ---------------------------------------------------------------------------

def _apply_dealbreakers(
    bars: list[Bar], group: GroupInput, rules: dict,
) -> tuple[list[Bar], list[dict]]:
    """Filter hard-dealbreaking bars. Return (survivors, excluded_with_reason).

    Accessibility rule (per BUILD_PLAN §safety): when the group requests
    step-free or accessible-restroom access, a bar is admitted ONLY if the
    field is explicitly True. `False` and `None` (unknown) both exclude — we
    cannot promise an unverified bar is safe for the user.
    """
    excluded: list[dict] = []
    survivors: list[Bar] = []
    if not group.users:
        # No users = no group prefs to dealbreak against. Return all bars unfiltered.
        return list(bars), []
    veto_map = {v: u.name for u in group.users for v in u.vetoes}
    poorest = min(group.users, key=lambda u: u.max_per_drink).name
    poorest_cap = min(u.max_per_drink for u in group.users)
    allowed_hoods = set(group.neighborhoods) if group.neighborhoods else None
    underage = [u for u in group.users if u.age < 21]

    # Resolve the budget_gross_mismatch multiplier. Priority: per-plan
    # override on GroupInput → rules.yaml value → hardcoded 2.0 fallback.
    # Club-heavy night styles (Pregame→clubs, Rooftop summer) override this
    # to 2.5 so real nightlife venues aren't silently filtered out.
    budget_mult = group.budget_multiplier
    if budget_mult is None:
        for r in rules.get("dealbreaker_rules", []):
            if r.get("id") == "budget_gross_mismatch":
                budget_mult = r.get("multiplier", 2.0)
                break
        if budget_mult is None:
            budget_mult = 2.0

    for b in bars:
        # 1. user veto
        if b.id in veto_map:
            excluded.append({
                "bar": b, "rule_id": "user_veto",
                "reason": explain_exclusion(b, "vetoed", "user_veto",
                                            extra={"vetoer": veto_map[b.id]}),
            })
            continue
        # 2. neighborhood filter
        if allowed_hoods and b.neighborhood not in allowed_hoods:
            excluded.append({
                "bar": b, "rule_id": "neighborhood_excluded",
                "reason": explain_exclusion(b, "", "neighborhood_excluded",
                                            extra={"allowed": ", ".join(sorted(allowed_hoods))}),
            })
            continue
        # 3. budget gross mismatch (multiplier is tunable per plan)
        if b.avg_drink_price > budget_mult * poorest_cap:
            excluded.append({
                "bar": b, "rule_id": "budget_gross_mismatch",
                "reason": explain_exclusion(b, "", "budget_gross_mismatch",
                                            extra={"poorest_user": poorest,
                                                   "multiplier": budget_mult}),
            })
            continue
        # 4a. age_policy_mismatch: 21+ bars exclude underage users.
        if underage and b.age_policy and b.age_policy.replace(" ", "").lower() in ("21+", "21andover"):
            excluded.append({
                "bar": b, "rule_id": "age_policy_mismatch",
                "reason": explain_exclusion(
                    b, "", "age_policy_mismatch",
                    extra={"underage_user": underage[0].name}),
            })
            continue
        # 4b. step-free access: exclude unless explicitly True (None = unknown = exclude).
        if group.accessibility_needs.step_free and b.accessibility.get("step_free") is not True:
            unknown = b.accessibility.get("step_free") is None
            excluded.append({
                "bar": b, "rule_id": "accessibility_unmet",
                "reason": explain_exclusion(
                    b, "", "accessibility_unmet",
                    extra={"need": "step-free access", "unknown": unknown}),
            })
            continue
        # 4c. accessible restroom: same conservative policy.
        if (group.accessibility_needs.accessible_restroom
                and b.accessibility.get("accessible_restroom") is not True):
            unknown = b.accessibility.get("accessible_restroom") is None
            excluded.append({
                "bar": b, "rule_id": "accessible_restroom_unmet",
                "reason": explain_exclusion(
                    b, "", "accessible_restroom_unmet",
                    extra={"need": "an accessible restroom", "unknown": unknown}),
            })
            continue
        # 5. open_at_start: must be open at start_time (otherwise it can't be stop 1;
        #    but it could still be a later stop, so we don't hard-exclude here)
        #    We only hard-exclude bars that are closed for the ENTIRE planning window.
        if not _open_in_window(b, group.start_time, group.end_time):
            excluded.append({
                "bar": b, "rule_id": "closed_at_arrival",
                "reason": explain_exclusion(b, "", "closed_at_arrival",
                                            extra={"arrival_time": group.start_time.strftime("%a %H:%M")}),
            })
            continue
        survivors.append(b)
    return survivors, excluded


def _open_in_window(bar: Bar, start: datetime, end: datetime) -> bool:
    """True if bar is open at any datetime in [start, end]."""
    step = max(timedelta(minutes=30), (end - start) / 10)
    t = start
    while t <= end:
        if is_open(bar, t):
            return True
        t += step
    return is_open(bar, end)


# ---------------------------------------------------------------------------
# Scoring pass
# ---------------------------------------------------------------------------

def _score_all_users(
    bars: list[Bar], group: GroupInput, rules: dict,
) -> dict[str, dict[str, Score]]:
    """Score every bar for every user at a canonical mid-window time.
    (Context-free layer; routing later adds temporal/distance context.)"""
    mid_time = group.start_time + (group.end_time - group.start_time) / 2
    hour = mid_time.hour
    day = day_name(mid_time)
    out: dict[str, dict[str, Score]] = {}
    for u in group.users:
        out[u.name] = {}
        for b in bars:
            out[u.name][b.id] = score_bar_for_user(
                b, u, rules,
                arrival_hour=hour, day=day,
                prev_location=group.start_location,
            )
    return out


def _score_all_users_arc(
    bars: list[Bar], group: GroupInput, rules: dict,
) -> list[dict[str, dict[str, Score]]]:
    """Score every bar for every user ONCE PER ARC STAGE.

    Returns a list[stage_idx] of dict[user_name][bar_id] → Score.
    At each stage, each user's vibe_weights are replaced with that stage's
    profile so a bar scores high for "warm-up" only if its vibe_tags match
    the warm-up stage's vibes, not the peak stage's.
    """
    assert group.arc_profile is not None
    mid_time = group.start_time + (group.end_time - group.start_time) / 2
    hour = mid_time.hour
    day = day_name(mid_time)
    out: list[dict[str, dict[str, Score]]] = []
    for stage_weights in group.arc_profile:
        stage_out: dict[str, dict[str, Score]] = {}
        for u in group.users:
            # Stage weights define the night's shape at THIS stop; the user's
            # personal vibe_weights (already merged with the baseline arc + their
            # must-haves by the UI) are combined in via MAX so personal prefs
            # don't get wiped when we apply the stage-specific profile. Personal
            # must-haves that land in a stage where they weren't "supposed" to
            # matter still get to express themselves.
            merged = dict(stage_weights)
            for v, w in (u.vibe_weights or {}).items():
                merged[v] = max(merged.get(v, 0.0), w)
            u_stage = UserPreference(
                name=u.name,
                vibe_weights=merged,
                criterion_weights=u.criterion_weights,
                max_per_drink=u.max_per_drink,
                preferred_drinks=u.preferred_drinks,
                preferred_noise=u.preferred_noise,
                vetoes=u.vetoes,
                age=u.age,
            )
            stage_out[u.name] = {}
            for b in bars:
                stage_out[u.name][b.id] = score_bar_for_user(
                    b, u_stage, rules,
                    arrival_hour=hour, day=day,
                    prev_location=group.start_location,
                )
        out.append(stage_out)
    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def plan_crawl(
    group: GroupInput,
    bars: Optional[list[Bar]] = None,
    cases: Optional[list[Case]] = None,
    rules: Optional[dict] = None,
    compute_counterfactuals: bool = True,
    locked_bars: Optional[dict[int, Bar]] = None,
) -> PlanResult:
    """Run the full pipeline. If `bars`, `cases`, or `rules` are None,
    load them from the canonical data dir."""
    if bars is None or cases is None or rules is None:
        loaded = load_all()
        bars = bars or loaded["bars"]
        cases = cases or loaded["cases"]
        rules = rules or loaded["rules"]

    # If the caller specified neighborhoods but kept the default start_location
    # (East Village), recenter on the neighborhoods' centroid. Otherwise users
    # who pick "Bushwick" still pay distance penalties as if they're starting
    # from EV — the source of the 18% dataset-coverage finding in the eval.
    group = _maybe_recenter_start(group, bars)

    traces: dict = {}

    # 0. Disagreement profile computed against the FULL bar list.
    # Must precede dealbreaker filtering — otherwise veto-density goes to zero
    # because vetoed bars have already been dropped. This ordering is what
    # lets the veto-based rule fire at all.
    profile = disagreement_profile(group.users, bars)
    traces["disagreement_profile"] = profile
    decision = select_strategy(profile, rules)
    strat_name = decision.strategy_id
    rule_id = decision.triggering_rule_id
    rationale = decision.rationale
    traces["strategy_decision"] = decision
    traces["strategy_used"] = strat_name
    traces["strategy_rule"] = rule_id
    traces["strategy_rationale"] = rationale

    # 1. Dealbreakers
    survivors, excluded = _apply_dealbreakers(bars, group, rules)
    traces["survivors_count"] = len(survivors)
    traces["excluded_count"] = len(excluded)

    if not survivors:
        # Surface the top reasons in the headline so the user knows what to relax,
        # not just "no bars".
        from collections import Counter
        breakdown = Counter(e["rule_id"] for e in excluded)
        top_reasons = ", ".join(f"{n} {_humanize_rule(r)}" for r, n in breakdown.most_common(3))
        summary = (
            f"No candidate bars survived the hard constraints "
            f"({len(excluded)} excluded: {top_reasons}). "
            f"Try relaxing the most-cited rule above — see excluded_bars for "
            f"the rule that blocked each individual bar."
        )
        return PlanResult(
            route=Route(stops=[], total_utility=0.0, total_walking_miles=0.0,
                        windows_captured=[], strategy_used="",
                        strategy_rationale=""),
            explanations=Explanation(
                summary=summary,
                children=[], evidence=traces,
            ),
            excluded_bars=excluded, traces=traces,
        )

    # 2. Per-user scoring
    per_user = _score_all_users(survivors, group, rules)
    traces["per_user_scores"] = per_user

    # 3. (Strategy was selected in step 0, before filtering.)
    # 4. Aggregate. If the group has an arc_profile, aggregate ONCE PER STAGE
    # so different stops can be scored against different stage weights.
    group_scores_by_stage: list[dict[str, float]]
    if group.arc_profile:
        group_scores_by_stage = []
        per_user_by_stage = _score_all_users_arc(survivors, group, rules)
        for stage_pu in per_user_by_stage:
            gs = aggregate(strat_name, stage_pu, group.users)
            stage_scores = {
                bid: g.total for bid, g in gs.items()
                if g.total != float("-inf")
            }
            group_scores_by_stage.append(stage_scores)
            # Veto-driven exclusions (consistent across stages — same vetoes everywhere)
            if strat_name == "approval_veto":
                for bid, g in gs.items():
                    if g.total == float("-inf"):
                        if not any(e["bar"].id == bid for e in excluded):
                            bar = next(b for b in survivors if b.id == bid)
                            vetoers = g.rank_context.get("vetoers", ["someone"])
                            excluded.append({
                                "bar": bar, "rule_id": "user_veto",
                                "reason": explain_exclusion(bar, "", "user_veto",
                                                            extra={"vetoer": ", ".join(vetoers)}),
                            })
        # For reporting / per-user served-ness, use the stage-0 per_user.
        per_user = per_user_by_stage[0]
        traces["per_user_scores"] = per_user
        # Use the UNION of all stage-feasible bars as routable
        union_ids = set().union(*(s.keys() for s in group_scores_by_stage))
        routable = [b for b in survivors if b.id in union_ids]
    else:
        group_scores_full = aggregate(strat_name, per_user, group.users)
        group_scores: dict[str, float] = {
            bid: g.total for bid, g in group_scores_full.items()
            if g.total != float("-inf")
        }
        if strat_name == "approval_veto":
            for bid, g in group_scores_full.items():
                if g.total == float("-inf"):
                    bar = next(b for b in survivors if b.id == bid)
                    if not any(e["bar"].id == bid for e in excluded):
                        vetoers = g.rank_context.get("vetoers", ["someone"])
                        excluded.append({
                            "bar": bar, "rule_id": "user_veto",
                            "reason": explain_exclusion(bar, "", "user_veto",
                                                        extra={"vetoer": ", ".join(vetoers)}),
                        })
        group_scores_by_stage = [group_scores]
        routable = [b for b in survivors if b.id in group_scores]

    # 5. CBR retrieve → adapt (Phase 3: close the R-loop)
    case_matches = retrieve(group, cases, top_k=3) if cases else []
    traces["case_matches"] = [(c.id, round(sim, 3)) for c, sim, _ in case_matches]
    adapted: Optional[AdaptedCase] = None
    if case_matches:
        top_case, top_sim, breakdown = case_matches[0]
        adapted = adapt_case(top_case, group, bars, rules,
                             similarity_value=top_sim,
                             similarity_breakdown=breakdown)
        traces["adapted_case"] = adapted

    # 6. Routing (seeded by the adapted case when available)
    avg_budget_weight = _avg_budget_weight(group, rules)
    route = best_route(
        routable, group_scores_by_stage, group, rules,
        strategy_used=strat_name, strategy_rationale=rationale,
        user_budget_weight=avg_budget_weight,
        seed_sequence=adapted,
        locked_bars=locked_bars,
    )
    traces["search_log_length"] = len(route.search_log)

    # 7. Option generation — uses the UNION of all stage scores as the
    #    "overall" utility for runner-up comparisons. Runner-up for stop N
    #    is a bar that could have filled slot N; use stop-N's stage scores.
    #    Union for simplicity; could be refined per-stop.
    if len(group_scores_by_stage) == 1:
        union_scores = dict(group_scores_by_stage[0])
    else:
        union_scores = {}
        for stage_scores in group_scores_by_stage:
            for bid, s in stage_scores.items():
                union_scores[bid] = max(union_scores.get(bid, float("-inf")), s)
    runner_ups = find_runner_ups(route, union_scores, per_user, routable)
    runner_ups = unlock_analysis(route, runner_ups, per_user)
    traces["runner_ups"] = {idx: (ru.bar.name, ru.gap) for idx, ru in runner_ups.items()}
    # Attach each runner-up to its stop so deeper_analysis(plan_result) can
    # build a side-by-side diff without digging through traces.
    for idx, stop in enumerate(route.stops):
        if idx in runner_ups:
            stop.runner_up = runner_ups[idx]

    # Deeper-analysis trigger. When the mean relative_gap across stops is
    # below the configured threshold, the winning plan is on a knife's edge —
    # the rank flips to E and the caller is signalled that deeper_analysis()
    # is worth invoking. Threshold lives in rules.yaml so it's tunable.
    margin_threshold = (
        rules.get("group_strategy_rules", {})
        .get("deeper_analysis", {})
        .get("margin_threshold", 0.05)
    )
    if runner_ups:
        gaps = [ru.relative_gap for ru in runner_ups.values()
                if ru.relative_gap is not None]
        if gaps:
            mean_margin = sum(gaps) / len(gaps)
            traces["plan_margin"] = mean_margin
            if mean_margin < margin_threshold:
                decision.requires_deeper_analysis = True
                decision.rank = "E"
                traces["strategy_decision"] = decision  # rebind, same object

    alternatives: list[Route] = []
    cf_texts: list[str] = []
    if compute_counterfactuals and route.stops:
        # Structural
        for cf in all_structural_counterfactuals(group):
            alt = plan_crawl(cf.modified_group, bars=bars, cases=cases,
                             rules=rules, compute_counterfactuals=False)
            alternatives.append(alt.route)
            cf_texts.append(explain_counterfactual(cf.kind, cf.description,
                                                    route, alt.route))
        # Strategy
        strat_scores = strategy_counterfactuals(per_user, group.users)
        traces["strategy_cf_winners"] = {s: strategy_winner(sc) for s, sc in strat_scores.items()}

    # 8. Explanations
    route_text = explain_route(route, group, rules)
    strat_text = explain_strategy(strat_name, rule_id, profile, group.users,
                                   rules, decision=decision)
    stop_exps = []
    for idx, stop in enumerate(route.stops):
        ru = runner_ups.get(idx)
        text = explain_stop(idx, stop, route, per_user, ru, rules,
                            users=group.users)
        stop_exps.append(Explanation(summary=text, evidence={"stop_index": idx}))

    children = [
        Explanation(summary=strat_text, evidence={"kind": "strategy"}),
        *stop_exps,
    ]
    if cf_texts:
        children.append(Explanation(
            summary="Counterfactuals",
            children=[Explanation(summary=t) for t in cf_texts],
        ))
    if adapted is not None:
        # Phase 3: render the CBR step as a structured Argument so
        # adaptations are cited, not hidden. Keep the archetype's
        # success_narrative as a trailing second sentence — it's the
        # one-liner that gives the archetype colour.
        cbr_arg_text = render_argument(build_cbr_argument(adapted, rules))
        top_case = next(c for c, _s, _b in case_matches
                        if c.id == adapted.source_case_id)
        if top_case.success_narrative:
            cbr_arg_text = f"{cbr_arg_text} ({top_case.success_narrative})"
        children.append(Explanation(summary=cbr_arg_text,
                                     evidence={"kind": "case_match"}))
    elif case_matches:
        # Fallback: retrieval worked but no adaptation (e.g. cases=None
        # supplied to a downstream caller). Preserve the legacy sentence
        # so the children list stays non-empty.
        top_case, sim, _ = case_matches[0]
        case_text = (f"This plan resembles our **{top_case.name}** archetype "
                     f"(similarity {sim:.2f}): {top_case.success_narrative}")
        children.append(Explanation(summary=case_text, evidence={"kind": "case_match"}))

    explanation = Explanation(
        summary=route_text,
        children=children,
        evidence={
            "strategy": strat_name,
            "rule_fired": rule_id,
            "profile": profile,
            "excluded_count": len(excluded),
            "case_matches": traces["case_matches"],
        },
    )

    # 9. Stakeholder table
    per_user_report = per_user_served_report(route, per_user, group.users)
    traces["per_user_report"] = per_user_report

    return PlanResult(
        route=route,
        explanations=explanation,
        alternatives=alternatives,
        traces=traces,
        excluded_bars=excluded,
        per_user_report=per_user_report,
    )


def _avg_budget_weight(group: GroupInput, rules: dict) -> float:
    """Average of users' budget criterion weight (used to scale happy-hour bonus)."""
    total = 0.0
    n = 0
    for u in group.users:
        w = u.criterion_weights or rules["scoring_defaults"]["default_weights"]
        w = normalize_weights(w)
        total += w.get("budget", 0.0)
        n += 1
    return total / max(1, n)


_RULE_LABELS = {
    "user_veto": "vetoed",
    "neighborhood_excluded": "outside requested neighborhoods",
    "budget_gross_mismatch": "over budget cap",
    "accessibility_unmet": "not step-free",
    "accessible_restroom_unmet": "no accessible restroom",
    "age_policy_mismatch": "21+ only",
    "closed_at_arrival": "closed during your window",
}


def _humanize_rule(rule_id: str) -> str:
    return _RULE_LABELS.get(rule_id, rule_id.replace("_", " "))


_DEFAULT_START_LOCATION = (40.7265, -73.9815)  # mirror models.GroupInput default


def _maybe_recenter_start(group: GroupInput, bars: list[Bar]) -> GroupInput:
    """If the caller specified neighborhoods AND didn't override start_location,
    re-anchor the start to the centroid of bars in those neighborhoods so that
    distance penalties don't push the planner toward East Village by default."""
    if not group.neighborhoods:
        return group
    if _is_default_start(group.start_location):
        nh_set = set(group.neighborhoods)
        nh_bars = [b for b in bars if b.neighborhood in nh_set]
        if nh_bars:
            lat = sum(b.lat for b in nh_bars) / len(nh_bars)
            lon = sum(b.lon for b in nh_bars) / len(nh_bars)
            # Build a shallow copy with the new start (GroupInput is a regular
            # dataclass; copy.replace via dataclasses.replace would be ideal,
            # but mutating is cheap enough here).
            import dataclasses
            return dataclasses.replace(group, start_location=(lat, lon))
    return group


def _is_default_start(loc: tuple[float, float]) -> bool:
    if not loc or len(loc) != 2:
        return True
    return (abs(loc[0] - _DEFAULT_START_LOCATION[0]) < 1e-6
            and abs(loc[1] - _DEFAULT_START_LOCATION[1]) < 1e-6)


# ---------------------------------------------------------------------------
# Deeper analysis — the E-tier callable
# ---------------------------------------------------------------------------

def deeper_analysis(plan_result: PlanResult, rules: Optional[dict] = None) -> dict:
    """Side-by-side per-stop diff of the winner vs the runner-up at each stop.

    Invoked by the caller when `plan_result.traces["strategy_decision"]
    .requires_deeper_analysis` is True. Does not re-plan; reads the
    runner-up data already populated on each stop.

    Returns a dict with:
      margin         — mean normalized gap across stops (lower = tighter)
      margin_threshold — the configured trigger threshold
      stop_diffs     — list[dict] with one entry per stop:
                        stop_index, winner {name, group_score},
                        runner_up {name, relative_gap, gap, unlock_hint, criteria_gap}
      strategy_decision — the triggering StrategyDecision (for narrative)
    """
    route = plan_result.route
    stop_diffs: list[dict] = []
    for idx, stop in enumerate(route.stops):
        entry: dict = {
            "stop_index": idx,
            "winner": {
                "bar_name": stop.bar.name,
                "bar_id": stop.bar.id,
                "group_score": stop.group_score,
                "neighborhood": stop.bar.neighborhood,
                "price_tier": stop.bar.price_tier,
            },
            "runner_up": None,
        }
        ru = stop.runner_up
        if ru is not None:
            top_criteria_gaps = sorted(
                ru.gap_criteria.items(), key=lambda kv: -abs(kv[1])
            )[:3]
            entry["runner_up"] = {
                "bar_name": ru.bar.name,
                "bar_id": ru.bar.id,
                "relative_gap": ru.relative_gap,
                "gap": ru.gap,
                "unlock_hint": ru.unlock_hint,
                "neighborhood": ru.bar.neighborhood,
                "price_tier": ru.bar.price_tier,
                "top_criteria_gaps": top_criteria_gaps,
            }
        stop_diffs.append(entry)

    mean_margin = plan_result.traces.get("plan_margin")
    decision = plan_result.traces.get("strategy_decision")
    if rules is None:
        rules = load_all()["rules"]
    threshold = (rules.get("group_strategy_rules", {})
                      .get("deeper_analysis", {})
                      .get("margin_threshold", 0.05))
    return {
        "margin": mean_margin,
        "margin_threshold": threshold,
        "stop_diffs": stop_diffs,
        "strategy_decision": decision,
    }
