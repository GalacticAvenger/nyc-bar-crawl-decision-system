"""Routing — walking distances + TSP with time windows & profit.

The problem: given a set of candidate bars (already group-scored) and a
group input (start/end time, start location, max stops), produce a route
that maximizes (sum of utilities + temporal bonuses) minus walking penalty,
subject to time-window feasibility (each stop is open at its arrival).

Algorithm:
  1. Greedy construction — from start, pick the reachable + open + highest-
     utility bar; advance time by walking + stop duration; repeat.
  2. 2-opt improvement — swap adjacent pairs; keep if feasible AND improves
     the objective.
  3. For ≤ 7-stop candidate sets, enumerate all permutations as a sanity check.

The router emits a search_log so the explanation engine can say
"we checked all 720 orderings; this one was best" or similar.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from .models import Bar, GroupInput, Route, RouteStop, TemporalWindow
from .temporal import is_open, temporal_bonus, day_name


EARTH_RADIUS_MILES = 3958.8


def walking_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance in miles + small NYC east-west crossing penalty."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(h))
    miles = EARTH_RADIUS_MILES * c
    ew_component = abs(dlon) * math.cos((lat1 + lat2) / 2) * EARTH_RADIUS_MILES
    return miles + 0.05 * ew_component


def walking_minutes(miles: float) -> float:
    """Assume 3 mph walking pace in NYC (slightly slow for avenues/crowds)."""
    return miles * 20.0  # 60 min / 3 mph


# ---------------------------------------------------------------------------
# Walking penalty (used inside the routing objective)
# ---------------------------------------------------------------------------

def stage_for(stop_idx: int, total_stops: int, num_stages: int) -> int:
    """Map a stop position to an arc stage.
    With 3 stages and 3 stops: 0→0, 1→1, 2→2.
    With 3 stages and 5 stops: 0→0, 1→0/1, 2→1, 3→1/2, 4→2.
    Single-stage arc always returns 0.
    """
    if num_stages <= 1:
        return 0
    if total_stops <= 1:
        return num_stages - 1
    # Half-up rounding so the last stop reliably hits the last stage.
    return min(num_stages - 1,
               int(stop_idx * (num_stages - 1) / (total_stops - 1) + 0.5))


def _scores_for_stage(
    group_scores_by_stage: list[dict[str, float]] | dict[str, float],
    stage_idx: int,
) -> dict[str, float]:
    """Retrieve the scores dict for a given stage. Accepts either a list of
    dicts (per-stage) or a single dict (no staging)."""
    if isinstance(group_scores_by_stage, dict):
        return group_scores_by_stage
    return group_scores_by_stage[stage_idx]


def _walking_penalty(miles: float, rules: dict, walking_only: bool = True) -> float:
    """Leg-distance penalty.
    When `walking_only` is False, a leg longer than the comfortable walking
    cap switches to a flat transit cost (Uber / subway) — the intuition is
    that a crawl that was always going to involve transport shouldn't
    penalize a 4-mile hop the same way it penalizes an aimless 4-mile walk.
    """
    cfg = rules.get("walking_and_distance", {})
    cap_miles = cfg.get("comfortable_max_miles", 0.6)
    per_mile = cfg.get("per_mile_penalty", 0.08)

    # Short leg — always walk, always penalize lightly
    if miles <= cap_miles:
        return per_mile * miles

    if walking_only:
        # Long walk — amplified penalty (stamina + time cost)
        return (per_mile * cap_miles
                + cfg.get("amplified_per_mile_penalty_over_threshold", 0.20) * (miles - cap_miles))

    # Long leg with transit allowed — flat transit fee replaces per-mile
    return cfg.get("transit_override", {}).get("transit_fixed_penalty", 0.10)


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------

@dataclass
class Step:
    """Internal: a bar chosen for a specific arrival/departure."""
    bar: Bar
    arrival: datetime
    departure: datetime
    utility: float           # group_score (context-free component)
    bonus: float             # temporal bonus captured at arrival
    windows: list[TemporalWindow]


def _arrival_after(prev_loc: tuple[float, float], prev_time: datetime,
                   bar: Bar) -> datetime:
    """Compute arrival time at `bar` starting from prev_loc/time."""
    miles = walking_miles(prev_loc, (bar.lat, bar.lon))
    return prev_time + timedelta(minutes=walking_minutes(miles))


def _is_feasible(bar: Bar, arrival: datetime, group: GroupInput,
                 stop_minutes: int) -> bool:
    if arrival >= group.end_time:
        return False
    if not is_open(bar, arrival):
        return False
    # Must be open for at least stop_minutes
    depart = arrival + timedelta(minutes=stop_minutes)
    if depart > group.end_time:
        return False
    return True


# ---------------------------------------------------------------------------
# Greedy construction
# ---------------------------------------------------------------------------

def greedy_route(
    candidates: list[Bar],
    group_scores_by_stage: list[dict[str, float]] | dict[str, float],
    group: GroupInput,
    rules: dict,
    user_budget_weight: float = 0.0,
) -> tuple[list[Step], list[str]]:
    """Greedy: from start, always pick the highest (utility + bonus − walk)
    bar that is still reachable and open. At step i, score against the
    arc stage mapped from stage_for(i, max_stops, num_stages).
    Returns (steps, log_messages)."""
    log: list[str] = []
    chosen: list[Step] = []
    used_ids: set[str] = set()

    current_loc = group.start_location
    current_time = group.start_time
    stop_minutes = rules.get("routing_config", {}).get("default_stop_duration_minutes", 45)

    num_stages = (1 if isinstance(group_scores_by_stage, dict)
                  else len(group_scores_by_stage))

    for step_idx in range(group.max_stops):
        stage_idx = stage_for(step_idx, group.max_stops, num_stages)
        scores = _scores_for_stage(group_scores_by_stage, stage_idx)
        best: Optional[tuple[float, Step]] = None
        for bar in candidates:
            if bar.id in used_ids:
                continue
            if bar.id not in scores:
                continue
            arrival = _arrival_after(current_loc, current_time, bar)
            if not _is_feasible(bar, arrival, group, stop_minutes):
                continue
            miles = walking_miles(current_loc, (bar.lat, bar.lon))
            penalty = _walking_penalty(miles, rules, walking_only=group.walking_only)
            bonus, windows = temporal_bonus(bar, arrival, rules,
                                            user_budget_weight=user_budget_weight,
                                            user_wants_food=group.want_food)
            util = scores[bar.id]
            net = util + bonus - penalty
            if best is None or net > best[0]:
                best = (net, Step(
                    bar=bar, arrival=arrival,
                    departure=arrival + timedelta(minutes=stop_minutes),
                    utility=util, bonus=bonus, windows=windows,
                ))
        if best is None:
            log.append(f"step {step_idx + 1}: no feasible bar remaining; stopping.")
            break
        _, step = best
        chosen.append(step)
        used_ids.add(step.bar.id)
        current_loc = (step.bar.lat, step.bar.lon)
        current_time = step.departure
        log.append(f"step {step_idx + 1} (stage {stage_idx}): chose {step.bar.name} "
                   f"(util {step.utility:.3f} + bonus {step.bonus:.3f}) at "
                   f"{step.arrival.strftime('%a %H:%M')}.")
    return chosen, log


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------

def _recompute_schedule(
    bar_order: list[Bar],
    group_scores_by_stage: list[dict[str, float]] | dict[str, float],
    group: GroupInput,
    rules: dict,
    user_budget_weight: float = 0.0,
) -> Optional[tuple[list[Step], float]]:
    """Given a fixed order of bars, compute arrival/departure, feasibility,
    and total objective. At position i, use the arc-stage-specific scores.
    Returns (steps, objective) or None if infeasible."""
    stop_minutes = rules.get("routing_config", {}).get("default_stop_duration_minutes", 45)
    current_loc = group.start_location
    current_time = group.start_time
    steps: list[Step] = []
    total = 0.0
    num_stages = (1 if isinstance(group_scores_by_stage, dict)
                  else len(group_scores_by_stage))
    total_stops = len(bar_order)
    for i, bar in enumerate(bar_order):
        stage_idx = stage_for(i, total_stops, num_stages)
        scores = _scores_for_stage(group_scores_by_stage, stage_idx)
        arrival = _arrival_after(current_loc, current_time, bar)
        if not _is_feasible(bar, arrival, group, stop_minutes):
            return None
        miles = walking_miles(current_loc, (bar.lat, bar.lon))
        penalty = _walking_penalty(miles, rules, walking_only=group.walking_only)
        bonus, windows = temporal_bonus(bar, arrival, rules,
                                        user_budget_weight=user_budget_weight,
                                        user_wants_food=group.want_food)
        util = scores.get(bar.id, 0.0)
        total += util + bonus - penalty
        steps.append(Step(bar=bar, arrival=arrival,
                          departure=arrival + timedelta(minutes=stop_minutes),
                          utility=util, bonus=bonus, windows=windows))
        current_loc = (bar.lat, bar.lon)
        current_time = arrival + timedelta(minutes=stop_minutes)
    return steps, total


# ---------------------------------------------------------------------------
# 2-opt improvement (feasibility-preserving)
# ---------------------------------------------------------------------------

def two_opt_improve(
    steps: list[Step],
    group_scores_by_stage: list[dict[str, float]] | dict[str, float],
    group: GroupInput,
    rules: dict,
    user_budget_weight: float = 0.0,
) -> tuple[list[Step], list[str]]:
    """Try swapping pairs of stops; accept any swap that's feasible AND improves
    the total objective. Continue until no improvement. Return (steps, log)."""
    if len(steps) < 2:
        return steps, []
    cfg = rules.get("routing_config", {}).get("two_opt", {})
    max_iter = cfg.get("max_iterations", 50)
    tol = cfg.get("improvement_tolerance", 0.01)

    current = list(steps)
    current_total = sum(s.utility + s.bonus for s in current)  # approximate
    log: list[str] = []

    for it in range(max_iter):
        improved = False
        n = len(current)
        for i in range(n - 1):
            for j in range(i + 1, n):
                # Reverse the subpath [i..j]
                new_order = [s.bar for s in current]
                new_order[i:j + 1] = list(reversed(new_order[i:j + 1]))
                result = _recompute_schedule(new_order, group_scores_by_stage, group, rules,
                                             user_budget_weight=user_budget_weight)
                if result is None:
                    continue
                new_steps, new_total = result
                if new_total > current_total + tol:
                    log.append(f"2-opt iter {it + 1}: reverse [{i}..{j}] "
                               f"improved {current_total:.3f} → {new_total:.3f}")
                    current = new_steps
                    current_total = new_total
                    improved = True
                    break
            if improved:
                break
        if not improved:
            log.append(f"2-opt converged after {it + 1} iterations.")
            break
    return current, log


# ---------------------------------------------------------------------------
# Exact enumeration for small candidate sets
# ---------------------------------------------------------------------------

def enumerate_exact(
    candidates: list[Bar],
    group_scores_by_stage: list[dict[str, float]] | dict[str, float],
    group: GroupInput,
    rules: dict,
    user_budget_weight: float = 0.0,
) -> tuple[Optional[list[Step]], float, int]:
    """Enumerate all permutations of candidate subsets up to max_stops.
    Returns (best_steps_or_None, best_objective, perms_tried)."""
    best_steps: Optional[list[Step]] = None
    best_total = float("-inf")
    perms_tried = 0
    n = len(candidates)
    max_k = min(group.max_stops, n)
    for k in range(1, max_k + 1):
        for subset in itertools.combinations(candidates, k):
            for perm in itertools.permutations(subset):
                perms_tried += 1
                result = _recompute_schedule(list(perm), group_scores_by_stage, group, rules,
                                             user_budget_weight=user_budget_weight)
                if result is None:
                    continue
                steps, total = result
                if total > best_total:
                    best_total = total
                    best_steps = steps
    return best_steps, best_total, perms_tried


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def best_route(
    candidates: list[Bar],
    group_scores_by_stage: list[dict[str, float]] | dict[str, float],
    group: GroupInput,
    rules: dict,
    strategy_used: str = "",
    strategy_rationale: str = "",
    user_budget_weight: float = 0.0,
) -> Route:
    """Orchestrate: greedy → 2-opt → (if small) exact check. Return Route.
    `group_scores_by_stage` can be a single dict (no arc) or a list of dicts
    (one per arc stage)."""
    log: list[str] = []
    greedy_steps, greedy_log = greedy_route(
        candidates, group_scores_by_stage, group, rules,
        user_budget_weight=user_budget_weight,
    )
    log.extend(["GREEDY:"] + greedy_log)

    if not greedy_steps:
        return Route(stops=[], total_utility=0.0, total_walking_miles=0.0,
                     windows_captured=[], strategy_used=strategy_used,
                     strategy_rationale=strategy_rationale, search_log=log)

    improved, opt_log = two_opt_improve(
        greedy_steps, group_scores_by_stage, group, rules,
        user_budget_weight=user_budget_weight,
    )
    log.extend(["2-OPT:"] + opt_log)
    chosen_steps = improved

    chosen_bars = [s.bar for s in chosen_steps]
    if len(chosen_bars) <= rules.get("routing_config", {}).get("exact_enumeration_max_stops", 7):
        exact_steps, exact_total, perms = enumerate_exact(
            chosen_bars, group_scores_by_stage, group, rules,
            user_budget_weight=user_budget_weight,
        )
        current_total = _total_of(chosen_steps)
        log.append(f"EXACT: enumerated {perms} permutations over {len(chosen_bars)} bars.")
        if exact_steps and exact_total > current_total + 1e-6:
            log.append(f"EXACT: exact ordering {exact_total:.3f} beats 2-opt {current_total:.3f}.")
            chosen_steps = exact_steps

    return _route_from_steps(chosen_steps, strategy_used, strategy_rationale, log)


def _total_of(steps: list[Step]) -> float:
    return sum(s.utility + s.bonus for s in steps)


def _route_from_steps(steps: list[Step], strategy_used: str,
                      strategy_rationale: str, log: list[str]) -> Route:
    total_util = sum(s.utility + s.bonus for s in steps)
    total_miles = 0.0
    windows: list[TemporalWindow] = []
    stops: list[RouteStop] = []
    for i, s in enumerate(steps):
        stops.append(RouteStop(
            bar=s.bar,
            arrival=s.arrival,
            departure=s.departure,
            group_score=s.utility,
            temporal_bonuses_captured=s.windows,
        ))
        windows.extend(s.windows)
        if i > 0:
            prev = steps[i - 1]
            total_miles += walking_miles((prev.bar.lat, prev.bar.lon),
                                         (s.bar.lat, s.bar.lon))
    return Route(
        stops=stops,
        total_utility=total_util,
        total_walking_miles=total_miles,
        windows_captured=windows,
        strategy_used=strategy_used,
        strategy_rationale=strategy_rationale,
        search_log=log,
    )
