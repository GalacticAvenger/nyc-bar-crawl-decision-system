"""Core domain models. Every type in the system is defined here.

All dataclasses use keyword-only constructors where there are many fields,
and rely on immutability (`frozen=True`) where the object should not mutate
after creation. Explanations carry their own provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# Bar — the atomic unit of the dataset
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TemporalWindow:
    days: tuple[str, ...]
    start: str           # "HH:MM" (may exceed 24:00 for past-midnight close)
    end: str
    kind: str            # happy_hour | trivia | karaoke_night | theme_night | ...
    details: str = ""
    bonus: float = 0.0


@dataclass(frozen=True)
class Bar:
    id: str
    seed_id: str
    name: str
    neighborhood: str
    address: str
    lat: float
    lon: float
    bar_type: tuple[str, ...]
    vibe_tags: tuple[str, ...]
    price_tier: str
    avg_drink_price: float
    drink_specialties: tuple[str, ...]
    drink_categories_served: tuple[str, ...]
    noise_level: str
    capacity_estimate: int
    crowd_level_by_hour: dict[str, str]     # hour("17") -> "packed"
    outdoor_seating: Optional[bool]
    food_quality: Optional[str]
    kitchen_open: Optional[dict]
    happy_hour_windows: tuple[TemporalWindow, ...]
    specials: tuple[TemporalWindow, ...]
    open_hours: dict[str, Optional[list[str]]]   # "mon" -> ["17:00", "26:00"] or None
    age_policy: str
    accessibility: dict[str, Optional[bool]]
    reservations: str
    dress_code: str
    novelty: float
    description: Optional[str]
    good_for: tuple[str, ...]
    avoid_for: tuple[str, ...]
    google_rating: float
    google_review_count: int
    google_price_indicator: Optional[str]
    google_category: Optional[str]
    quality_signal: float
    user_note: Optional[str]
    primary_function: Optional[str]
    editorial_note: Optional[str]
    source: str

    def __repr__(self) -> str:
        return f"Bar({self.id}: {self.name!r} @ {self.neighborhood}, {self.price_tier})"


# ---------------------------------------------------------------------------
# User preferences + group input
# ---------------------------------------------------------------------------

@dataclass
class UserPreference:
    name: str
    vibe_weights: dict[str, float] = field(default_factory=dict)  # over vibe_vocab
    criterion_weights: dict[str, float] = field(default_factory=dict)  # over MCDA criteria
    max_per_drink: float = 15.0
    preferred_drinks: tuple[str, ...] = ()           # subset of drink_categories_served
    preferred_noise: str = "lively"
    vetoes: tuple[str, ...] = ()                      # bar ids
    age: int = 30

    def intensity(self) -> float:
        """Peakiness of vibe weights: max - mean."""
        if not self.vibe_weights:
            return 0.0
        vals = list(self.vibe_weights.values())
        return max(vals) - (sum(vals) / len(vals))


@dataclass
class AccessibilityNeeds:
    step_free: bool = False
    accessible_restroom: bool = False


@dataclass
class GroupInput:
    users: list[UserPreference]
    start_time: datetime
    end_time: datetime
    start_location: tuple[float, float] = (40.7265, -73.9815)  # default: East Village
    max_stops: int = 4
    neighborhoods: tuple[str, ...] = ()  # empty = any
    walking_only: bool = True
    accessibility_needs: AccessibilityNeeds = field(default_factory=AccessibilityNeeds)
    want_food: bool = False
    # Optional arc profile: one vibe_weights dict per stage of the night.
    # If provided, the planner scores stop N against arc_profile[stage_for(N)]
    # instead of a single flat user.vibe_weights, so a "warm-up" stop prefers
    # different vibes than a "peak" stop.
    arc_profile: Optional[tuple[dict[str, float], ...]] = None


# ---------------------------------------------------------------------------
# Strategy decision (VOTE-shaped meta-selector output)
# ---------------------------------------------------------------------------

@dataclass
class StrategyDecision:
    """What the meta-selector produced, in VOTE's shape.

    `strategy_id` is the machine name (still usable as the old string return).
    `rank` is A / B / C / D / E; A = strong moral/structural claim (honor
    dealbreakers, protect the worst-off), B = robust positional/pairwise, C =
    shallow fallback (simple utilitarian), D = reserved, E = margin too thin,
    deeper analysis warranted.
    `triggering_profile_signal` names the one metric that tipped the rule
    (e.g. "budget_spread_ratio=2.4 exceeded threshold 2.0").
    `considered_alternatives` lists the other four strategies with a short
    why_not_chosen string so explanations can cite them directly.
    `requires_deeper_analysis` flips to True when the chosen plan's
    normalized margin over its runner-up is below the configured threshold
    (rules.yaml group_strategy_rules.deeper_analysis.margin_threshold).
    """
    strategy_id: str
    rank: str
    narrative_name: str
    quote: str
    triggering_profile_signal: str
    applies_when: str
    considered_alternatives: list[tuple[str, str, str]] = field(default_factory=list)
    triggering_rule_id: str = ""
    rationale: str = ""
    requires_deeper_analysis: bool = False


# ---------------------------------------------------------------------------
# Case library
# ---------------------------------------------------------------------------

@dataclass
class Case:
    id: str
    name: str
    group_profile: dict[str, Any]
    context: dict[str, Any]
    solution_sequence: list[dict[str, Any]]
    success_narrative: str
    fails_when: list[str]
    example_bars_in_dataset: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CBR adaptation — the Revise step
# ---------------------------------------------------------------------------

@dataclass
class Adaptation:
    """A single, named modification applied to a retrieved case.

    `field_changed` describes the slot that was touched (e.g.
    "solution_sequence.length", "solution_sequence[1].vibe_profile",
    "context.start_neighborhoods"). `reason` is the English justification
    the explanation engine will quote back.
    """
    field_changed: str
    from_value: Any
    to_value: Any
    reason: str


@dataclass
class AdaptedCase:
    """A retrieved case, transformed to fit the current group's constraints.

    `adapted_sequence` has the same shape as `Case.solution_sequence` so
    downstream consumers can treat it interchangeably. `adaptations`
    carries the audit log — what changed and why — so the explanation
    engine can narrate the adaptations instead of pretending the case
    came out of the library already fitted.

    `unadapted_stages` records indices of stages that couldn't be adapted
    against the current bar dataset (no feasible candidates); the router
    treats those stages as soft priors rather than hard waypoints.
    """
    source_case_id: str
    source_case_name: str
    adapted_sequence: list[dict[str, Any]]
    adaptations: list[Adaptation] = field(default_factory=list)
    similarity: float = 0.0
    similarity_breakdown: dict[str, float] = field(default_factory=dict)
    unadapted_stages: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class Score:
    bar_id: str
    user_id: str
    per_criterion: dict[str, float]      # criterion -> raw score [0, 1]
    weighted_contributions: dict[str, float]  # criterion -> weight * score
    total: float                          # sum of weighted_contributions
    temporal_bonus: float = 0.0           # additive bonus captured at arrival
    total_with_bonus: float = 0.0         # total + temporal_bonus (set by router)


@dataclass
class GroupScore:
    bar_id: str
    total: float
    per_user_contribution: dict[str, float]
    losers: list[str]              # user ids whose top choice lost
    rank_context: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Route + stop
# ---------------------------------------------------------------------------

@dataclass
class RouteStop:
    bar: Bar
    arrival: datetime
    departure: datetime
    group_score: float                  # aggregated (per chosen strategy) at chosen time
    temporal_bonuses_captured: list[TemporalWindow] = field(default_factory=list)
    per_user_scores: dict[str, Score] = field(default_factory=dict)
    runner_up: Optional["RunnerUp"] = None


@dataclass
class RunnerUp:
    bar: Bar
    gap: float
    gap_criteria: dict[str, float]      # criterion -> delta (winner - runner_up)
    unlock_hint: str = ""                # natural-language "what would have to change"
    relative_gap: float = 0.0            # gap normalized to [0,1] vs winner score (or score range)


@dataclass
class Route:
    stops: list[RouteStop]
    total_utility: float
    total_walking_miles: float
    windows_captured: list[TemporalWindow]
    strategy_used: str
    strategy_rationale: str
    search_log: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.stops) == 0


# ---------------------------------------------------------------------------
# Explanation tree
# ---------------------------------------------------------------------------

@dataclass
class Explanation:
    summary: str
    children: list["Explanation"] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_text(self, indent: int = 0) -> str:
        lines = ["  " * indent + self.summary]
        for child in self.children:
            lines.append(child.as_text(indent + 1))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan result — the public return type
# ---------------------------------------------------------------------------

@dataclass
class PlanResult:
    route: Route
    explanations: Explanation
    alternatives: list[Route] = field(default_factory=list)
    traces: dict[str, Any] = field(default_factory=dict)
    excluded_bars: list[dict[str, Any]] = field(default_factory=list)  # {bar, reason, rule_id}
    per_user_report: dict[str, dict[str, Any]] = field(default_factory=dict)  # stakeholder table


# ---------------------------------------------------------------------------
# Dialogic replan — Phase 4
# ---------------------------------------------------------------------------

@dataclass
class Reaction:
    """A single reaction from one user to one stop in a plan.

    `verdict` is "accept", "reject", or "swap". `lock=True` means the stop
    must be preserved exactly in the replan even if re-scoring would move
    it. `optional_reason` is preserved in traces but not used by the
    preference-update logic (the rule reads the scored criteria instead).
    `swap_target_bar_id`, when present on a "swap" verdict, identifies a
    specific bar to treat as the implicit accept.
    """
    user_id: str
    stop_index: int
    verdict: str
    optional_reason: str = ""
    lock: bool = False
    swap_target_bar_id: Optional[str] = None


@dataclass
class PreferenceUpdate:
    """One multiplicative (or additive, for budget) change applied to a
    user's preferences. Kept as an explicit record so (a) the explanation
    engine can narrate it and (b) revert_user can undo just one user's
    updates without re-running the whole reaction sequence.
    """
    user_id: str
    field: str       # e.g. "criterion_weights.noise" | "max_per_drink"
    from_value: float
    to_value: float
    reason: str
    triggered_by_reaction: int  # index into the reactions list


@dataclass
class StopChange:
    """One per-stop diff between two plans."""
    stop_index: int
    change_type: str       # unchanged | replaced | added | removed | reordered
    before: Optional[str]  # bar name (None when added)
    after: Optional[str]   # bar name (None when removed)
    attributed_to: str     # "reaction N" | "ripple: <reason>" | "unattributed"


@dataclass
class DeltaArgument:
    """Compares two plans, stop-by-stop, with each change attributed back
    to either an explicit reaction or a preference-update ripple. A change
    that cannot be attributed is surfaced in `unattributed` so tests
    (and the explanation engine) can flag it as a bug."""
    conclusion: str
    per_stop_changes: list[StopChange] = field(default_factory=list)
    unattributed: list[StopChange] = field(default_factory=list)
    pref_updates: list[PreferenceUpdate] = field(default_factory=list)
