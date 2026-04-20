"""Option generation & counterfactuals.

For every stop in the final route, we answer (before the user asks):

  runner_up         What was the 2nd-best bar for this slot, and by how much
                    and on which axes did the winner beat it?
  unlock            What would have to change in the group's preferences for
                    the runner-up to win? (delta on a specific weight)
  structural CFs    +30 min / +$10 / −1 vetoer: what changes?
  strategy CFs      Under each alternative aggregation strategy, who wins?

These are computed during search, not narrated post-hoc. The explanation
engine reads them straight from the PlanResult.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from .group_aggregation import aggregate, disagreement_profile, select_strategy
from .models import Bar, GroupInput, Route, RouteStop, RunnerUp, Score, UserPreference


# ---------------------------------------------------------------------------
# Runner-ups per stop
# ---------------------------------------------------------------------------

def find_runner_ups(
    route: Route,
    group_scores: dict[str, float],
    per_user_scores: dict[str, dict[str, Score]],
    bars: list[Bar],
) -> dict[int, RunnerUp]:
    """For each stop, find the 2nd-highest-utility bar (that's not already in
    the route). Return dict[stop_index] -> RunnerUp."""
    used_ids = {s.bar.id for s in route.stops}
    result: dict[int, RunnerUp] = {}
    for idx, stop in enumerate(route.stops):
        # Candidates = bars other than used ones
        others = [b for b in bars if b.id not in used_ids and b.id in group_scores]
        if not others:
            continue
        ranked = sorted(others, key=lambda b: -group_scores[b.id])
        best_alt = ranked[0]
        gap = group_scores[stop.bar.id] - group_scores[best_alt.id]
        # Compute per-criterion gap (averaged across users who scored both)
        crit_gap: dict[str, float] = {}
        for u_id, u_scores in per_user_scores.items():
            if stop.bar.id in u_scores and best_alt.id in u_scores:
                for c, v in u_scores[stop.bar.id].per_criterion.items():
                    crit_gap[c] = crit_gap.get(c, 0.0) + (v - u_scores[best_alt.id].per_criterion.get(c, 0.0))
        if per_user_scores:
            n = len(per_user_scores)
            crit_gap = {c: v / n for c, v in crit_gap.items()}
        result[idx] = RunnerUp(
            bar=best_alt,
            gap=gap,
            gap_criteria=crit_gap,
            unlock_hint="",
        )
    return result


# ---------------------------------------------------------------------------
# Unlock analysis
# ---------------------------------------------------------------------------

def unlock_hint_for(
    winner: Bar,
    runner_up: Bar,
    per_user_scores: dict[str, dict[str, Score]],
) -> str:
    """Identify the single criterion where the runner-up beats the winner most,
    and return a readable hint — "if the group cared more about X, Bar Y would
    win"."""
    # Average per-criterion delta (runner_up - winner), find most positive
    deltas: dict[str, float] = {}
    for user_scores in per_user_scores.values():
        if winner.id in user_scores and runner_up.id in user_scores:
            wcrits = user_scores[winner.id].per_criterion
            rcrits = user_scores[runner_up.id].per_criterion
            for c in wcrits:
                deltas[c] = deltas.get(c, 0.0) + (rcrits.get(c, 0) - wcrits[c])
    if not deltas:
        return ""
    best_crit = max(deltas, key=deltas.get)
    if deltas[best_crit] <= 0:
        return "(runner-up doesn't beat the winner on any single criterion)"
    phrase_map = {
        "vibe": "weighted vibes differently",
        "budget": "had a tighter budget",
        "drink_match": "specifically wanted a drink this bar carries",
        "noise": "wanted a different noise level",
        "distance": "were starting from a different location",
        "happy_hour_active": "arrived during its happy hour",
        "specials_match": "were there during its special event",
        "crowd_fit": "wanted a different crowd energy",
        "novelty": "prioritized novelty",
        "quality_signal": "prioritized widely-loved picks",
    }
    return phrase_map.get(best_crit, f"weighted {best_crit} higher")


def unlock_analysis(
    route: Route,
    runner_ups: dict[int, RunnerUp],
    per_user_scores: dict[str, dict[str, Score]],
) -> dict[int, RunnerUp]:
    """Populate RunnerUp.unlock_hint. Mutates and returns."""
    for idx, ru in runner_ups.items():
        ru.unlock_hint = unlock_hint_for(route.stops[idx].bar, ru.bar, per_user_scores)
    return runner_ups


# ---------------------------------------------------------------------------
# Structural counterfactuals
# ---------------------------------------------------------------------------

@dataclass
class Counterfactual:
    """A what-if scenario on the group inputs."""
    kind: str               # "extra_time" | "extra_budget" | "remove_vetoer" | "walking_only_off"
    description: str        # human-readable ("if the group had 30 more minutes")
    modified_group: GroupInput
    delta_summary: str = ""      # concise change ("end_time + 30min")


def make_extra_time_cf(group: GroupInput, minutes: int = 30) -> Counterfactual:
    mod = copy.deepcopy(group)
    mod.end_time = mod.end_time + timedelta(minutes=minutes)
    return Counterfactual(
        kind="extra_time",
        description=f"if the group had {minutes} more minutes",
        modified_group=mod,
        delta_summary=f"end_time + {minutes} min",
    )


def make_extra_budget_cf(group: GroupInput, delta: float = 10.0) -> Counterfactual:
    mod = copy.deepcopy(group)
    for u in mod.users:
        u.max_per_drink += delta
    return Counterfactual(
        kind="extra_budget",
        description=f"if each user had ${int(delta)} more per drink",
        modified_group=mod,
        delta_summary=f"max_per_drink + ${int(delta)}",
    )


def make_remove_vetoer_cf(group: GroupInput) -> Optional[Counterfactual]:
    vetoers = [u for u in group.users if u.vetoes]
    if not vetoers:
        return None
    mod = copy.deepcopy(group)
    # Remove the user with the most vetoes
    worst = max(vetoers, key=lambda u: len(u.vetoes))
    mod.users = [u for u in mod.users if u.name != worst.name]
    return Counterfactual(
        kind="remove_vetoer",
        description=f"if {worst.name} hadn't vetoed anything",
        modified_group=mod,
        delta_summary=f"drop vetoer {worst.name}",
    )


def all_structural_counterfactuals(group: GroupInput) -> list[Counterfactual]:
    out = [
        make_extra_time_cf(group, minutes=30),
        make_extra_budget_cf(group, delta=10.0),
    ]
    rv = make_remove_vetoer_cf(group)
    if rv:
        out.append(rv)
    return out


# ---------------------------------------------------------------------------
# Strategy counterfactuals
# ---------------------------------------------------------------------------

def strategy_counterfactuals(
    per_user_scores: dict[str, dict[str, Score]],
    users: list[UserPreference],
) -> dict[str, dict[str, float]]:
    """Run EACH aggregation strategy and return its top-ranked bar per strategy.
    Returns dict[strategy_name] -> dict[bar_id -> group_score]."""
    out: dict[str, dict[str, float]] = {}
    strategies = ["utilitarian_sum", "egalitarian_min", "borda_count",
                  "copeland_pairwise", "approval_veto"]
    for strat in strategies:
        gs = aggregate(strat, per_user_scores, users)
        # Drop -inf (vetoed) entries
        filtered = {bid: g.total for bid, g in gs.items() if g.total != float("-inf")}
        out[strat] = filtered
    return out


def strategy_winner(strategy_scores: dict[str, float]) -> Optional[str]:
    """Return the bar_id with the highest score under this strategy."""
    if not strategy_scores:
        return None
    return max(strategy_scores, key=strategy_scores.get)
