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

from .models import Bar, GroupScore, Score, UserPreference


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

def select_strategy(profile: dict, rules: dict
                    ) -> tuple[str, str, str]:
    """Returns (strategy_name, rule_id, rationale_string).
    Rules are evaluated in priority order; first match wins."""
    selection = rules["group_strategy_rules"]["selection_rules"]

    if profile["dealbreaker_density"] > 0.20:
        rule = next(r for r in selection if r["id"] == "strategy_veto")
        percent = round(profile["dealbreaker_density"] * 100)
        return (rule["strategy"], rule["id"],
                rule["rationale_template"].replace("{percent}", str(percent)))

    if profile["budget_spread_ratio"] > 3.0:
        rule = next(r for r in selection if r["id"] == "strategy_egalitarian")
        ratio = round(profile["budget_spread_ratio"], 1)
        return (rule["strategy"], rule["id"],
                rule["rationale_template"].replace("{ratio}", str(ratio)))

    if profile["vibe_variance"] > 0.30:
        rule = next(r for r in selection if r["id"] == "strategy_copeland")
        return (rule["strategy"], rule["id"],
                rule["rationale_template"].replace("{variance}", f"{profile['vibe_variance']:.2f}"))

    if profile["max_preference_intensity"] > 0.35:
        rule = next(r for r in selection if r["id"] == "strategy_borda")
        return (rule["strategy"], rule["id"],
                rule["rationale_template"]
                    .replace("{user}", "one member")
                    .replace("{intensity}", f"{profile['max_preference_intensity']:.2f}"))

    rule = next(r for r in selection if r["id"] == "strategy_utilitarian")
    return (rule["strategy"], rule["id"], rule["rationale_template"])
