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

from .case_based import retrieve
from .data_loader import load_all
from .explanation_engine import (
    explain_counterfactual, explain_exclusion, explain_route, explain_stop,
    explain_strategy, per_user_served_report,
)
from .group_aggregation import aggregate, disagreement_profile, select_strategy
from .models import (
    Bar, Case, Explanation, GroupInput, GroupScore, PlanResult, Route, RouteStop, Score,
    UserPreference,
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
    """Filter hard-dealbreaking bars. Return (survivors, excluded_with_reason)."""
    excluded: list[dict] = []
    survivors: list[Bar] = []
    veto_map = {v: u.name for u in group.users for v in u.vetoes}
    poorest = min(group.users, key=lambda u: u.max_per_drink).name
    poorest_cap = min(u.max_per_drink for u in group.users)
    allowed_hoods = set(group.neighborhoods) if group.neighborhoods else None

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
        # 3. budget gross mismatch
        if b.avg_drink_price > 2.0 * poorest_cap:
            excluded.append({
                "bar": b, "rule_id": "budget_gross_mismatch",
                "reason": explain_exclusion(b, "", "budget_gross_mismatch",
                                            extra={"poorest_user": poorest}),
            })
            continue
        # 4. accessibility
        if group.accessibility_needs.step_free and b.accessibility.get("step_free") is False:
            excluded.append({
                "bar": b, "rule_id": "accessibility_unmet",
                "reason": f"{b.name} was excluded: no step-free access.",
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
) -> PlanResult:
    """Run the full pipeline. If `bars`, `cases`, or `rules` are None,
    load them from the canonical data dir."""
    if bars is None or cases is None or rules is None:
        loaded = load_all()
        bars = bars or loaded["bars"]
        cases = cases or loaded["cases"]
        rules = rules or loaded["rules"]

    traces: dict = {}

    # 0. Disagreement profile computed against the FULL bar list.
    # Must precede dealbreaker filtering — otherwise veto-density goes to zero
    # because vetoed bars have already been dropped. This ordering is what
    # lets the veto-based rule fire at all.
    profile = disagreement_profile(group.users, bars)
    traces["disagreement_profile"] = profile
    strat_name, rule_id, rationale = select_strategy(profile, rules)
    traces["strategy_used"] = strat_name
    traces["strategy_rule"] = rule_id
    traces["strategy_rationale"] = rationale

    # 1. Dealbreakers
    survivors, excluded = _apply_dealbreakers(bars, group, rules)
    traces["survivors_count"] = len(survivors)
    traces["excluded_count"] = len(excluded)

    if not survivors:
        return PlanResult(
            route=Route(stops=[], total_utility=0.0, total_walking_miles=0.0,
                        windows_captured=[], strategy_used="",
                        strategy_rationale=""),
            explanations=Explanation(
                summary="No candidate bars survived the hard constraints. "
                        "See excluded_bars for the rule that blocked each.",
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

    # 5. CBR retrieval (advisory; used for explanation narrative)
    case_matches = retrieve(group, cases, top_k=3) if cases else []
    traces["case_matches"] = [(c.id, round(sim, 3)) for c, sim, _ in case_matches]

    # 6. Routing
    avg_budget_weight = _avg_budget_weight(group, rules)
    route = best_route(
        routable, group_scores_by_stage, group, rules,
        strategy_used=strat_name, strategy_rationale=rationale,
        user_budget_weight=avg_budget_weight,
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
    strat_text = explain_strategy(strat_name, rule_id, profile, group.users, rules)
    stop_exps = []
    for idx, stop in enumerate(route.stops):
        ru = runner_ups.get(idx)
        text = explain_stop(idx, stop, route, per_user, ru, rules)
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
    if case_matches:
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
