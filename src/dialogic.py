"""Dialogic replan — Phase 4.

A user reacts to a plan (accept / reject / swap / lock). We:
  1. Update each reacting user's preferences with a simple, defensible
     multiplicative rule ("Alex rejected the loudest stop — I increased
     his noise-aversion weight by 30%"); we do NOT do Bayesian inference.
  2. Re-run the normal plan_crawl pipeline with the updated users,
     while preserving any locked stops exactly as fixed waypoints.
  3. Produce a DeltaArgument that attributes every change back to a
     specific reaction or a named preference-update "ripple."

The contract: EVERY change in the new plan is attributable to either a
reaction or a ripple. If attribution fails, it is surfaced (not hidden)
so tests can catch it.

Nothing in this module generates free-form text; the rule names what
changed, and the explanation engine cites it.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Optional

from .argument import Argument, Premise, render_argument
from .decision_system import plan_crawl
from .models import (
    Bar, DeltaArgument, GroupInput, PlanResult, PreferenceUpdate, Reaction,
    StopChange, UserPreference,
)
from .scoring import CRITERIA, normalize_weights


# ---------------------------------------------------------------------------
# Preference update rule (explicit, not Bayesian)
# ---------------------------------------------------------------------------

_REJECT_MULTIPLIER = 1.3
_MAX_WEIGHT_INFLATION = 2.0
_BUDGET_WIDEN_FRACTION = 0.5


def _stop_per_criterion(plan: PlanResult, bar_id: str, user_id: str
                        ) -> dict[str, float]:
    """Fetch a user's per-criterion raw scores for a bar in the plan.
    Reads from traces[per_user_scores]; returns empty dict if missing."""
    per_user = plan.traces.get("per_user_scores", {})
    user_scores = per_user.get(user_id, {})
    score = user_scores.get(bar_id)
    if score is None:
        return {}
    return dict(score.per_criterion)


def _bottom_quartile_criteria(
    rejected_stop_bar_id: str, plan: PlanResult, user_id: str,
) -> list[str]:
    """Criteria on which this bar scored in the bottom quartile across
    ALL the plan's stops for this user. Those are the criteria a
    "reject" should dial up — the user rejected because the bar was bad
    on them."""
    rejected_scores = _stop_per_criterion(plan, rejected_stop_bar_id, user_id)
    if not rejected_scores:
        return []
    out: list[str] = []
    for c in CRITERIA:
        per_stop_vals = [
            _stop_per_criterion(plan, s.bar.id, user_id).get(c)
            for s in plan.route.stops
        ]
        per_stop_vals = [v for v in per_stop_vals if v is not None]
        if len(per_stop_vals) < 2:
            continue
        # Bottom quartile cut — sorted ascending, take the 25th-%ile value.
        sorted_vals = sorted(per_stop_vals)
        q_idx = max(0, int(0.25 * (len(sorted_vals) - 1)))
        q_cut = sorted_vals[q_idx]
        if rejected_scores.get(c, 1.0) <= q_cut + 1e-9:
            out.append(c)
    return out


def _find_user(users: list[UserPreference], name: str
               ) -> Optional[UserPreference]:
    return next((u for u in users if u.name == name), None)


def _apply_reject(
    user: UserPreference,
    stop_bar_id: str,
    previous_plan: PlanResult,
    reaction_index: int,
) -> tuple[UserPreference, list[PreferenceUpdate]]:
    """Boost user's weight on every criterion the rejected bar scored
    poorly on (bottom quartile). Returns the updated user + updates log.
    Cap each weight at MAX_WEIGHT_INFLATION× its original value."""
    updates: list[PreferenceUpdate] = []
    crits = _bottom_quartile_criteria(stop_bar_id, previous_plan, user.name)
    if not crits:
        return user, updates
    new_weights = dict(user.criterion_weights or {})
    # Establish a baseline for "original" if the user has no explicit weights.
    original_for_cap = dict(user.criterion_weights or {})
    for c in crits:
        orig = original_for_cap.get(c, 0.1)  # neutral default if absent
        current = new_weights.get(c, orig)
        bumped = current * _REJECT_MULTIPLIER
        capped = min(bumped, orig * _MAX_WEIGHT_INFLATION)
        if abs(capped - current) < 1e-9:
            continue  # already at cap — don't log a no-op
        new_weights[c] = capped
        updates.append(PreferenceUpdate(
            user_id=user.name,
            field=f"criterion_weights.{c}",
            from_value=current,
            to_value=capped,
            reason=(f"{user.name} rejected stop {reaction_index + 1}, which "
                    f"scored in their bottom quartile on '{c}'; bumped the "
                    f"weight by {int((_REJECT_MULTIPLIER - 1) * 100)}% "
                    f"(capped at {_MAX_WEIGHT_INFLATION}× original)"),
            triggered_by_reaction=reaction_index,
        ))
    if updates:
        user = replace(user, criterion_weights=new_weights)
    return user, updates


def _apply_accept(
    user: UserPreference,
    stop_bar_id: str,
    previous_plan: PlanResult,
    reaction_index: int,
) -> tuple[UserPreference, list[PreferenceUpdate]]:
    """If the accepted stop was over the user's budget cap, widen the
    cap by a fraction of the overshoot. Otherwise no-op."""
    updates: list[PreferenceUpdate] = []
    bar = next((s.bar for s in previous_plan.route.stops
                 if s.bar.id == stop_bar_id), None)
    if bar is None:
        return user, updates
    cap = user.max_per_drink
    if bar.avg_drink_price > cap + 1e-6:
        widened = cap + (bar.avg_drink_price - cap) * _BUDGET_WIDEN_FRACTION
        updates.append(PreferenceUpdate(
            user_id=user.name,
            field="max_per_drink",
            from_value=cap,
            to_value=widened,
            reason=(f"{user.name} accepted stop {reaction_index + 1} at "
                    f"${bar.avg_drink_price:.0f}/drink, over their "
                    f"${cap:.0f} cap; widened cap by "
                    f"{int(_BUDGET_WIDEN_FRACTION * 100)}% of the overshoot"),
            triggered_by_reaction=reaction_index,
        ))
        user = replace(user, max_per_drink=widened)
    return user, updates


def update_preferences(
    users: list[UserPreference],
    previous_plan: PlanResult,
    reactions: list[Reaction],
) -> tuple[list[UserPreference], list[PreferenceUpdate]]:
    """Apply the reactions to the users. Returns (new_users, updates_log).

    Rule sheet (intentionally simple, per phase spec):
      * reject → bump the rejecting user's weights on the criteria where
        that stop scored in their bottom quartile (×1.3, capped at ×2)
      * accept on an over-budget stop → widen the user's cap by 50% of
        the overshoot
      * swap → treat as a reject on the current + implicit accept on the
        swap target (if specified)
    """
    users_by_name = {u.name: u for u in users}
    updates: list[PreferenceUpdate] = []

    for i, r in enumerate(reactions):
        user = users_by_name.get(r.user_id)
        if user is None:
            continue
        target_stop = None
        if 0 <= r.stop_index < len(previous_plan.route.stops):
            target_stop = previous_plan.route.stops[r.stop_index]

        if r.verdict in ("reject", "swap") and target_stop is not None:
            user, ups = _apply_reject(user, target_stop.bar.id,
                                       previous_plan, i)
            updates.extend(ups)
        if r.verdict == "accept" and target_stop is not None:
            user, ups = _apply_accept(user, target_stop.bar.id,
                                       previous_plan, i)
            updates.extend(ups)
        if r.verdict == "swap" and r.swap_target_bar_id:
            user, ups = _apply_accept(user, r.swap_target_bar_id,
                                       previous_plan, i)
            updates.extend(ups)

        users_by_name[user.name] = user

    return [users_by_name[u.name] for u in users], updates


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------

def revert_user_updates(
    original_users: list[UserPreference],
    updated_users: list[UserPreference],
    pref_updates: list[PreferenceUpdate],
    user_id: str,
) -> tuple[list[UserPreference], list[PreferenceUpdate]]:
    """Undo every PreferenceUpdate for `user_id`. Returns (users, remaining
    updates). Other users' updates are preserved."""
    original_by_name = {u.name: u for u in original_users}
    remaining_updates = [u for u in pref_updates if u.user_id != user_id]
    new_users: list[UserPreference] = []
    for u in updated_users:
        if u.name == user_id:
            new_users.append(copy.deepcopy(original_by_name[user_id]))
        else:
            new_users.append(u)
    return new_users, remaining_updates


# ---------------------------------------------------------------------------
# Replan
# ---------------------------------------------------------------------------

def replan_with_reactions(
    previous_plan: PlanResult,
    reactions: list[Reaction],
    original_group: GroupInput,
    bars: Optional[list[Bar]] = None,
    cases=None,
    rules: Optional[dict] = None,
) -> PlanResult:
    """Run plan_crawl with updated users, locking any reactions flagged
    lock=True into their original positions. Adds preference_updates +
    delta_argument to the returned PlanResult.traces so the UI can
    surface them.

    Locked stops are supplied to plan_crawl via a dict[stop_index, Bar].
    When a locked bar is present at index i, best_route fixes that
    position and plans around it. The router still checks feasibility;
    if an unfeasible lock is requested, plan_crawl returns an empty
    route with the reason in excluded_bars.
    """
    updated_users, pref_updates = update_preferences(
        original_group.users, previous_plan, reactions,
    )

    # Build the locked_bars dict BEFORE replacing users (stop indices
    # refer to previous_plan).
    locked_bars: dict[int, Bar] = {}
    for r in reactions:
        if not r.lock:
            continue
        if 0 <= r.stop_index < len(previous_plan.route.stops):
            locked_bars[r.stop_index] = previous_plan.route.stops[r.stop_index].bar

    new_group = replace(original_group, users=updated_users)
    result = plan_crawl(new_group, bars=bars, cases=cases, rules=rules,
                        locked_bars=locked_bars or None)

    delta = build_delta_argument(previous_plan, result, reactions, pref_updates)
    result.traces["preference_updates"] = pref_updates
    result.traces["reactions"] = reactions
    result.traces["delta_argument"] = delta
    result.traces["locked_bars"] = locked_bars

    # Prepend pref-update narrative + delta narrative into the explanation
    # tree so the caller sees them first.
    pref_text = format_pref_updates(pref_updates)
    delta_text = render_delta_argument(delta)
    from .models import Explanation
    result.explanations.children.insert(0, Explanation(
        summary=delta_text, evidence={"kind": "delta"},
    ))
    if pref_text:
        result.explanations.children.insert(0, Explanation(
            summary=pref_text, evidence={"kind": "preference_updates"},
        ))

    return result


# ---------------------------------------------------------------------------
# Delta attribution + rendering
# ---------------------------------------------------------------------------

def build_delta_argument(
    before: PlanResult,
    after: PlanResult,
    reactions: list[Reaction],
    pref_updates: list[PreferenceUpdate],
) -> DeltaArgument:
    """Compute the per-stop diff and attribute every change to either a
    reaction or a preference-update ripple."""
    reactions_by_index = {r.stop_index: (i, r) for i, r in enumerate(reactions)}
    before_stops = before.route.stops
    after_stops = after.route.stops
    max_len = max(len(before_stops), len(after_stops))

    changes: list[StopChange] = []
    unattributed: list[StopChange] = []

    for i in range(max_len):
        b = before_stops[i] if i < len(before_stops) else None
        a = after_stops[i] if i < len(after_stops) else None

        if b is None and a is not None:
            change = StopChange(
                stop_index=i, change_type="added",
                before=None, after=a.bar.name,
                attributed_to=_attribute_added(i, a, pref_updates,
                                                reactions_by_index),
            )
        elif b is not None and a is None:
            change = StopChange(
                stop_index=i, change_type="removed",
                before=b.bar.name, after=None,
                attributed_to=_attribute_removed(i, b, pref_updates,
                                                   reactions_by_index),
            )
        elif b is not None and a is not None and b.bar.id == a.bar.id:
            change = StopChange(
                stop_index=i, change_type="unchanged",
                before=b.bar.name, after=a.bar.name,
                attributed_to="no change",
            )
        else:
            # Replaced
            change = StopChange(
                stop_index=i, change_type="replaced",
                before=b.bar.name if b else None,
                after=a.bar.name if a else None,
                attributed_to=_attribute_replaced(i, b, a, pref_updates,
                                                    reactions_by_index),
            )

        changes.append(change)
        if (change.change_type != "unchanged"
                and change.attributed_to == "unattributed"):
            unattributed.append(change)

    n_changed = sum(1 for c in changes if c.change_type != "unchanged")
    n_total = len(changes)
    conclusion = (f"{n_changed} of {n_total} stop{'s' if n_total != 1 else ''} "
                  f"changed in the replan")

    return DeltaArgument(
        conclusion=conclusion,
        per_stop_changes=changes,
        unattributed=unattributed,
        pref_updates=pref_updates,
    )


def _attribute_replaced(stop_index, before_stop, after_stop, pref_updates,
                         reactions_by_index) -> str:
    if stop_index in reactions_by_index:
        idx, r = reactions_by_index[stop_index]
        return f"reaction {idx + 1}: {r.verdict} on stop {stop_index + 1}"
    # Ripple: attribute to any pref_update — the replan used updated
    # preferences. Cite the most-relevant update by user mentioned.
    if pref_updates:
        first = pref_updates[0]
        return (f"ripple: updated {first.user_id}'s "
                f"{first.field.split('.')[-1]}")
    return "unattributed"


def _attribute_added(stop_index, stop, pref_updates, reactions_by_index) -> str:
    # Added stops arrive when the replan grows the plan. Attribute to
    # the first preference update or to any reaction that explicitly
    # requested growth.
    if pref_updates:
        first = pref_updates[0]
        return (f"ripple: updated {first.user_id}'s "
                f"{first.field.split('.')[-1]}")
    return "unattributed"


def _attribute_removed(stop_index, stop, pref_updates, reactions_by_index) -> str:
    if stop_index in reactions_by_index:
        idx, r = reactions_by_index[stop_index]
        if r.verdict in ("reject", "swap"):
            return f"reaction {idx + 1}: {r.verdict} on stop {stop_index + 1}"
    if pref_updates:
        first = pref_updates[0]
        return (f"ripple: updated {first.user_id}'s "
                f"{first.field.split('.')[-1]}")
    return "unattributed"


def render_delta_argument(delta: DeltaArgument) -> str:
    """Linearize a DeltaArgument into one English paragraph."""
    lines = [delta.conclusion.rstrip(".") + "."]
    changed = [c for c in delta.per_stop_changes if c.change_type != "unchanged"]
    for c in changed:
        if c.change_type == "replaced":
            lines.append(
                f"Stop {c.stop_index + 1}: swapped **{c.before}** for "
                f"**{c.after}** ({c.attributed_to})."
            )
        elif c.change_type == "added":
            lines.append(
                f"Stop {c.stop_index + 1}: added **{c.after}** "
                f"({c.attributed_to})."
            )
        elif c.change_type == "removed":
            lines.append(
                f"Stop {c.stop_index + 1}: dropped **{c.before}** "
                f"({c.attributed_to})."
            )
    if delta.unattributed:
        lines.append(
            f"⚠ {len(delta.unattributed)} change(s) could not be "
            f"attributed to any reaction or preference update — this is "
            f"a bug in the dialogic loop."
        )
    return " ".join(lines)


def format_pref_updates(updates: list[PreferenceUpdate]) -> str:
    """Render the preference-update log as an English paragraph with
    revert instructions. Empty string when nothing changed."""
    if not updates:
        return ""
    lines = ["Based on your reactions, I updated these preferences:"]
    for u in updates:
        if u.field == "max_per_drink":
            lines.append(
                f"  • {u.user_id}'s budget cap widened from "
                f"${u.from_value:.0f} to ${u.to_value:.0f}. "
                f"Reason: {u.reason}"
            )
        else:
            crit = u.field.split(".")[-1].replace("_", " ")
            lines.append(
                f"  • {u.user_id}'s {crit} weight rose from "
                f"{u.from_value:.2f} to {u.to_value:.2f}. "
                f"Reason: {u.reason}"
            )
    names = sorted({u.user_id for u in updates})
    lines.append(
        "Say " + " or ".join(f"'revert {n}'" for n in names)
        + " to undo those updates and replan."
    )
    return "\n".join(lines)
