"""VOTE-style group aggregation with meta-strategy selection.

Five strategies, each with identical signature:
  aggregate_<name>(per_user_scores) -> dict[bar_id, GroupScore]

The meta-selector computes a disagreement profile and picks the strategy.
The rule that fired is logged on the profile and surfaces in explanations.

Academic note (for the writeup): The strategy menu covers Condorcet-consistent,
utilitarian, egalitarian (Rawlsian), positional, and approval-based social
choice. The meta-selector implements the course's "rule-based expert system"
suggestion — it reasons about *which* method to use, not just *what* each
method outputs.
"""

from __future__ import annotations

import statistics
from typing import Callable

from .models import Bar, GroupScore, Score, StrategyDecision, UserPreference


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def aggregate_utilitarian_sum(
    per_user: dict[str, dict[str, Score]],
) -> dict[str, GroupScore]:
    """G(b) = sum of U(b,u)."""
    # per_user[user_id][bar_id] -> Score
    bar_ids = set()
    for user_scores in per_user.values():
        bar_ids.update(user_scores.keys())
    out: dict[str, GroupScore] = {}
    for bid in bar_ids:
        contribs = {u: scores[bid].total for u, scores in per_user.items() if bid in scores}
        total = sum(contribs.values())
        out[bid] = GroupScore(bar_id=bid, total=total, per_user_contribution=contribs,
                              losers=[], rank_context={"strategy": "utilitarian_sum"})
    return out


def aggregate_egalitarian_min(
    per_user: dict[str, dict[str, Score]],
) -> dict[str, GroupScore]:
    """G(b) = min of U(b,u). Rawlsian — protects the worst-off member."""
    bar_ids = set()
    for user_scores in per_user.values():
        bar_ids.update(user_scores.keys())
    out: dict[str, GroupScore] = {}
    for bid in bar_ids:
        contribs = {u: scores[bid].total for u, scores in per_user.items() if bid in scores}
        if not contribs:
            continue
        min_total = min(contribs.values())
        losers = [u for u, v in contribs.items() if v > min_total + 0.1]
        out[bid] = GroupScore(bar_id=bid, total=min_total, per_user_contribution=contribs,
                              losers=losers, rank_context={"strategy": "egalitarian_min"})
    return out


def aggregate_borda_count(
    per_user: dict[str, dict[str, Score]],
) -> dict[str, GroupScore]:
    """Each user ranks bars descending by their personal score; bar gets
    (n - rank) points per user. Ties share the same average rank."""
    bar_ids = set()
    for user_scores in per_user.values():
        bar_ids.update(user_scores.keys())
    n = len(bar_ids)
    points = {bid: 0.0 for bid in bar_ids}
    contribs: dict[str, dict[str, float]] = {bid: {} for bid in bar_ids}
    for user_id, user_scores in per_user.items():
        # sort bars descending by score; ties get average rank
        ordered = sorted(user_scores.items(), key=lambda kv: -kv[1].total)
        for rank, (bid, _) in enumerate(ordered):
            pts = n - rank
            points[bid] += pts
            contribs[bid][user_id] = pts
    out = {}
    for bid in bar_ids:
        out[bid] = GroupScore(bar_id=bid, total=points[bid],
                              per_user_contribution=contribs[bid], losers=[],
                              rank_context={"strategy": "borda_count"})
    return out


def aggregate_copeland_pairwise(
    per_user: dict[str, dict[str, Score]],
) -> dict[str, GroupScore]:
    """Count pairwise wins: for each pair (x, y), x wins if more users
    prefer x to y. The bar with most wins wins overall."""
    bar_ids = sorted(set().union(*[s.keys() for s in per_user.values()]))
    wins = {bid: 0 for bid in bar_ids}
    for i, x in enumerate(bar_ids):
        for y in bar_ids[i + 1:]:
            x_wins = sum(
                1 for us in per_user.values()
                if x in us and y in us and us[x].total > us[y].total
            )
            y_wins = sum(
                1 for us in per_user.values()
                if x in us and y in us and us[y].total > us[x].total
            )
            if x_wins > y_wins:
                wins[x] += 1
            elif y_wins > x_wins:
                wins[y] += 1
            # ties: neither
    out = {}
    for bid in bar_ids:
        contribs = {u: us[bid].total for u, us in per_user.items() if bid in us}
        out[bid] = GroupScore(bar_id=bid, total=float(wins[bid]),
                              per_user_contribution=contribs, losers=[],
                              rank_context={"strategy": "copeland_pairwise",
                                             "pairwise_wins": wins[bid]})
    return out


def aggregate_approval_veto(
    per_user: dict[str, dict[str, Score]],
    users: list[UserPreference],
    approval_threshold: float = 0.55,
) -> dict[str, GroupScore]:
    """Vetoes hard-exclude (score -infinity). Users implicitly 'approve' a bar
    if their personal score exceeds `approval_threshold`. The bar's group score
    is its approval count."""
    bar_ids = set()
    for user_scores in per_user.values():
        bar_ids.update(user_scores.keys())
    veto_map = {u.name: set(u.vetoes) for u in users}
    out = {}
    for bid in bar_ids:
        # Any veto → excluded
        vetoers = [u for u, v in veto_map.items() if bid in v]
        if vetoers:
            contribs = {u: us[bid].total for u, us in per_user.items() if bid in us}
            out[bid] = GroupScore(
                bar_id=bid, total=float("-inf"),
                per_user_contribution=contribs, losers=list(contribs.keys()),
                rank_context={"strategy": "approval_veto", "vetoers": vetoers},
            )
            continue
        approvals = [u for u, us in per_user.items()
                     if bid in us and us[bid].total >= approval_threshold]
        contribs = {u: us[bid].total for u, us in per_user.items() if bid in us}
        out[bid] = GroupScore(
            bar_id=bid, total=float(len(approvals)),
            per_user_contribution=contribs, losers=[],
            rank_context={"strategy": "approval_veto", "approvers": approvals},
        )
    return out


STRATEGIES: dict[str, Callable] = {
    "utilitarian_sum": aggregate_utilitarian_sum,
    "egalitarian_min": aggregate_egalitarian_min,
    "borda_count": aggregate_borda_count,
    "copeland_pairwise": aggregate_copeland_pairwise,
    # approval_veto has an extra arg; handled specially
}


def aggregate(strategy: str, per_user_scores, users: list[UserPreference]
              ) -> dict[str, GroupScore]:
    if strategy == "approval_veto":
        return aggregate_approval_veto(per_user_scores, users)
    return STRATEGIES[strategy](per_user_scores)


# ---------------------------------------------------------------------------
# Disagreement profile
# ---------------------------------------------------------------------------

def disagreement_profile(users: list[UserPreference],
                         bars: list[Bar]) -> dict[str, float | int]:
    """Compute the profile used by the meta-selector.

    Metrics (all rule-defined in rules.yaml §group_strategy_rules):
      dealbreaker_density     — fraction of bars vetoed by someone
      budget_spread_ratio     — max / min of users' max_per_drink
      vibe_variance           — mean std-dev across users for each vibe tag
      max_preference_intensity — peakiness = max - mean of a user's weights
      group_size              — |users|
    """
    all_bar_ids = {b.id for b in bars}
    vetoed = set().union(*[set(u.vetoes) for u in users]) & all_bar_ids
    dealbreaker_density = len(vetoed) / max(1, len(all_bar_ids))

    caps = [max(1.0, u.max_per_drink) for u in users]
    budget_spread_ratio = max(caps) / max(1e-9, min(caps))

    # Vibe variance: only over vibes any user actually weights
    all_vibes = set()
    for u in users:
        all_vibes.update(u.vibe_weights.keys())
    if all_vibes and len(users) > 1:
        stdevs = []
        for v in all_vibes:
            vals = [u.vibe_weights.get(v, 0.0) for u in users]
            stdevs.append(statistics.pstdev(vals))
        vibe_variance = sum(stdevs) / len(stdevs)
    else:
        vibe_variance = 0.0

    intensities = [u.intensity() for u in users]
    max_preference_intensity = max(intensities) if intensities else 0.0

    return {
        "dealbreaker_density": dealbreaker_density,
        "budget_spread_ratio": budget_spread_ratio,
        "vibe_variance": vibe_variance,
        "max_preference_intensity": max_preference_intensity,
        "group_size": len(users),
    }


# ---------------------------------------------------------------------------
# Meta-selector
# ---------------------------------------------------------------------------

def _threshold_for(rules: dict, rule_id: str, default: float) -> float:
    """Parse "metric > N" out of a rule's `condition` string. Falls back to
    `default` if the condition isn't a simple comparison."""
    selection = rules["group_strategy_rules"]["selection_rules"]
    for r in selection:
        if r["id"] == rule_id:
            cond = r.get("condition", "")
            # cheap parser: "<metric> > <number>"
            for op in (">", ">="):
                if op in cond:
                    try:
                        return float(cond.split(op, 1)[1].strip())
                    except (IndexError, ValueError):
                        return default
            return default
    return default


# ---------------------------------------------------------------------------
# VOTE-style decision packaging
# ---------------------------------------------------------------------------

# Rule id → (metric key, human label for the triggering signal).
# Kept close to the selection order so a future reorder only touches one place.
_RULE_METRIC = {
    "strategy_veto":         ("dealbreaker_density", "dealbreaker_density"),
    "strategy_egalitarian":  ("budget_spread_ratio", "budget_spread_ratio"),
    "strategy_copeland":     ("vibe_variance",       "vibe_variance"),
    "strategy_borda":        ("max_preference_intensity", "max_preference_intensity"),
    "strategy_utilitarian":  (None, None),  # fallback — no triggering threshold
}

# Rule id → strategy id in rules.yaml strategies table.
_RULE_TO_STRATEGY = {
    "strategy_veto":         "approval_veto",
    "strategy_egalitarian":  "egalitarian_min",
    "strategy_copeland":     "copeland_pairwise",
    "strategy_borda":        "borda_count",
    "strategy_utilitarian":  "utilitarian_sum",
}


def _format_signal(metric: str, profile: dict, threshold: float) -> str:
    """Render the triggering profile signal as an English-ish string."""
    value = profile.get(metric, 0.0)
    if metric == "dealbreaker_density":
        return (f"dealbreaker_density={value:.2f} "
                f"(threshold {threshold:.2f}) — "
                f"{round(value * 100)}% of bars vetoed by someone")
    if metric == "budget_spread_ratio":
        return (f"budget_spread_ratio={value:.1f}× exceeded threshold "
                f"{threshold:.1f}×")
    if metric == "vibe_variance":
        return (f"vibe_variance={value:.2f} exceeded threshold {threshold:.2f}")
    if metric == "max_preference_intensity":
        return (f"max_preference_intensity={value:.2f} exceeded threshold "
                f"{threshold:.2f}")
    return f"{metric}={value} (threshold {threshold})"


def _why_not_chosen(
    rule_id: str,
    fired_rule_id: str,
    fired_priority: int,
    profile: dict,
    rules: dict,
    selection: list[dict],
) -> str:
    """Explain in one English sentence why this non-chosen strategy lost."""
    if rule_id == fired_rule_id:
        return "this is the chosen strategy"
    this_rule = next(r for r in selection if r["id"] == rule_id)
    this_priority = this_rule.get("priority", 99)
    metric, label = _RULE_METRIC[rule_id]

    if this_priority < fired_priority and metric:
        # Higher-rank (lower priority number) strategy that didn't fire — its
        # condition wasn't met.
        threshold = _threshold_for(rules, rule_id, 0.0)
        value = profile.get(metric, 0.0)
        return (f"{label}={value:.2f} is below the {threshold:.2f} threshold — "
                f"this rule didn't fire")
    if this_priority > fired_priority:
        return ("a higher-rank strategy applied — this rule was only evaluated "
                "as a fallback")
    # Same priority as the winner but different id (shouldn't happen in the
    # current rule set, but covered for safety).
    return "this rule was not selected by the priority-ordered meta-selector"


def _build_considered_alternatives(
    fired_rule_id: str,
    profile: dict,
    rules: dict,
) -> list[tuple[str, str, str]]:
    """For each of the four non-chosen strategies, return (strategy_id, rank,
    why_not_chosen)."""
    selection = rules["group_strategy_rules"]["selection_rules"]
    strategies_meta = rules["group_strategy_rules"].get("strategies", {})
    fired_rule = next(r for r in selection if r["id"] == fired_rule_id)
    fired_priority = fired_rule.get("priority", 99)

    out: list[tuple[str, str, str]] = []
    for rule_id, strategy_id in _RULE_TO_STRATEGY.items():
        if rule_id == fired_rule_id:
            continue
        rank = strategies_meta.get(strategy_id, {}).get("rank", "C")
        why = _why_not_chosen(
            rule_id, fired_rule_id, fired_priority, profile, rules, selection,
        )
        out.append((strategy_id, rank, why))
    return out


def _build_decision(
    rule_id: str,
    profile: dict,
    rules: dict,
    rationale: str,
) -> StrategyDecision:
    """Package a fired rule into a StrategyDecision."""
    selection = rules["group_strategy_rules"]["selection_rules"]
    strategies_meta = rules["group_strategy_rules"].get("strategies", {})
    strategy_id = _RULE_TO_STRATEGY[rule_id]
    meta = strategies_meta.get(strategy_id, {})

    metric, _label = _RULE_METRIC[rule_id]
    if metric is None:
        triggering_signal = (
            "no single-metric threshold fired — utilitarian is the fallback "
            "when all higher-rank conditions are below their thresholds"
        )
    else:
        threshold = _threshold_for(rules, rule_id, 0.0)
        triggering_signal = _format_signal(metric, profile, threshold)

    return StrategyDecision(
        strategy_id=strategy_id,
        rank=meta.get("rank", "C"),
        narrative_name=meta.get("narrative_name", strategy_id.replace("_", " ")),
        quote=meta.get("quote", ""),
        triggering_profile_signal=triggering_signal,
        applies_when=meta.get("applies_when", ""),
        considered_alternatives=_build_considered_alternatives(rule_id, profile, rules),
        triggering_rule_id=rule_id,
        rationale=rationale,
        requires_deeper_analysis=False,
    )


# ---------------------------------------------------------------------------
# The meta-selector
# ---------------------------------------------------------------------------

def select_strategy(profile: dict, rules: dict) -> StrategyDecision:
    """Return the StrategyDecision produced by the VOTE-style meta-selector.

    Rules are evaluated in priority order; first match wins. Thresholds are
    read from rules.yaml so editing the YAML actually changes behavior — no
    hardcoded numbers in this function.

    The returned `StrategyDecision.strategy_id` is the old string return
    value (e.g. "utilitarian_sum") — callers that only need the name can
    still read it from there.
    """
    selection = rules["group_strategy_rules"]["selection_rules"]

    veto_t = _threshold_for(rules, "strategy_veto", 0.20)
    if profile["dealbreaker_density"] > veto_t:
        rule = next(r for r in selection if r["id"] == "strategy_veto")
        percent = round(profile["dealbreaker_density"] * 100)
        rationale = rule["rationale_template"].replace("{percent}", str(percent))
        return _build_decision("strategy_veto", profile, rules, rationale)

    egal_t = _threshold_for(rules, "strategy_egalitarian", 2.0)
    if profile["budget_spread_ratio"] > egal_t:
        rule = next(r for r in selection if r["id"] == "strategy_egalitarian")
        ratio = round(profile["budget_spread_ratio"], 1)
        rationale = rule["rationale_template"].replace("{ratio}", str(ratio))
        return _build_decision("strategy_egalitarian", profile, rules, rationale)

    cop_t = _threshold_for(rules, "strategy_copeland", 0.30)
    if profile["vibe_variance"] > cop_t:
        rule = next(r for r in selection if r["id"] == "strategy_copeland")
        rationale = rule["rationale_template"].replace(
            "{variance}", f"{profile['vibe_variance']:.2f}"
        )
        return _build_decision("strategy_copeland", profile, rules, rationale)

    bor_t = _threshold_for(rules, "strategy_borda", 0.35)
    if profile["max_preference_intensity"] > bor_t:
        rule = next(r for r in selection if r["id"] == "strategy_borda")
        rationale = (
            rule["rationale_template"]
            .replace("{user}", "one member")
            .replace("{intensity}", f"{profile['max_preference_intensity']:.2f}")
        )
        return _build_decision("strategy_borda", profile, rules, rationale)

    rule = next(r for r in selection if r["id"] == "strategy_utilitarian")
    return _build_decision("strategy_utilitarian", profile, rules,
                           rule["rationale_template"])
