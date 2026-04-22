"""Structured arguments — the two-layer explanation model.

An Argument is a structured, trace-driven object. Its fields carry the
reasoning the upstream pipeline already produced (scores, runner-ups,
strategy profile signals, etc.); nothing is re-derived here.

A linearizer renders an Argument into English. The shape of the rendered
text is driven by the Argument's structure (decisive premise, opposing
premises, sacrifice, runner-up), not by per-stop index or ad-hoc
templates. The intent is prose that reads like someone thinking out loud
rather than a form filled in.

Design constraints (standing for Phase 2):
  * Every Argument has a non-empty decisive_premise — the Mi-Stance.
  * Supporting premises should sum to ≥ 0.5 of total magnitude.
  * Rendered English must never contain literal template placeholders
    (tests enforce this).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


PremiseDirection = Literal["supports", "opposes"]


@dataclass
class Premise:
    """A single reason — for or against — attached to a conclusion.

    `magnitude` is a normalized contribution to the decision, in [0, 1],
    sourced from upstream weighted contributions (never recomputed).
    `evidence` is the concrete data point being cited ("cap of $45",
    "weighted noise at 0.8").
    """
    subject: str            # "Sarah" | "the group" | "Alex"
    criterion: str          # "budget" | "noise" | "vibe" | "strategy"
    direction: PremiseDirection
    magnitude: float        # [0, 1]; upstream normalized contribution
    evidence: str


@dataclass
class Argument:
    """A structured argument for a single conclusion.

    supporting + opposing carry all premises; the decisive_premise is the
    one reason that tipped the decision — typically the highest-magnitude
    supporting premise. sacrifice names what had to be given up for the
    runner_up, if any. runner_up holds just the name; full runner-up
    detail lives on the stop/plan trace.
    """
    conclusion: str
    supporting: list[Premise] = field(default_factory=list)
    opposing: list[Premise] = field(default_factory=list)
    decisive_premise: Optional[Premise] = None
    sacrifice: Optional[str] = None
    runner_up: Optional[str] = None

    def supporting_magnitude(self) -> float:
        return sum(p.magnitude for p in self.supporting)

    def total_magnitude(self) -> float:
        return (sum(p.magnitude for p in self.supporting)
                + sum(p.magnitude for p in self.opposing))


# ---------------------------------------------------------------------------
# English rendering for a single premise
# ---------------------------------------------------------------------------

# Per-criterion rendering templates. Each entry is a function of
# (subject, evidence, direction) → short phrase. Keeping them as
# explicit functions (not f-strings we lazily-eval) means a missing
# slot fails a test rather than silently shipping malformed prose.
#
# Direction matters for criteria that flip semantics under "opposes"
# (budget, noise, distance) — "fits the budget" is incoherent when the
# bar is over the cap. For criteria that only appear on one side
# (user_note, dominant_user, strategy signals), the function ignores
# direction.
_CRITERION_RENDERERS = {
    # bar-level attribute criteria
    "user_note": lambda subj, ev, d:
        f"{subj} had noted: _{ev}_",
    "dominant_user": lambda subj, ev, d:
        f"{subj} rated this {ev}",
    "quality_consensus": lambda subj, ev, d:
        f"strong consensus pick ({ev})",
    "temporal_window": lambda subj, ev, d:
        f"arrival lands inside the bar's {ev}",
    "vibe": lambda subj, ev, d: (
        f"the vibe misses what {subj} wanted ({ev})"
        if d == "opposes" else
        f"the vibe matches what {subj} wanted ({ev})"
    ),
    "budget": lambda subj, ev, d: (
        f"it's over {subj}'s cap ({ev})"
        if d == "opposes" else
        f"it fits {subj}'s budget ({ev})"
    ),
    "drink_match": lambda subj, ev, d: (
        f"it doesn't carry the drinks {subj} wanted ({ev})"
        if d == "opposes" else
        f"it carries the drinks {subj} asked for ({ev})"
    ),
    "noise": lambda subj, ev, d: (
        f"the noise is off for {subj} ({ev})"
        if d == "opposes" else
        f"the noise is where {subj} wanted it ({ev})"
    ),
    "distance": lambda subj, ev, d: (
        f"it's a longer walk ({ev})"
        if d == "opposes" else
        f"it's a short walk ({ev})"
    ),
    "happy_hour_active": lambda subj, ev, d:
        f"arrival lands inside its happy-hour window ({ev})",
    "specials_match": lambda subj, ev, d:
        f"there's an event running when {subj} would arrive ({ev})",
    "crowd_fit": lambda subj, ev, d: (
        f"the crowd energy doesn't fit {subj} ({ev})"
        if d == "opposes" else
        f"the crowd energy fits {subj} ({ev})"
    ),
    "novelty": lambda subj, ev, d:
        f"it's a distinctive, less-obvious pick ({ev})",
    "quality_signal": lambda subj, ev, d:
        f"it's a widely-loved spot ({ev})",
    # strategy-level criteria (only ever appear under supports; the
    # opposing side cites OTHER strategies whose rule didn't fire)
    "dealbreaker_density": lambda subj, ev, d:
        f"enough bars are vetoed that ignoring dealbreakers would be dishonest ({ev})",
    "budget_spread_ratio": lambda subj, ev, d:
        f"the budget gap is wide enough that averaging would leave {subj} out ({ev})",
    "vibe_variance": lambda subj, ev, d:
        f"the group splits on vibe with no clear aligned center ({ev})",
    "max_preference_intensity": lambda subj, ev, d:
        f"one member's weights are sharp enough that raw scoring would steamroll the rest ({ev})",
    "aligned_preferences": lambda subj, ev, d:
        f"preferences are aligned enough to just sum utilities ({ev})",
    "losing_alternative": lambda subj, ev, d:
        f"{subj} was not chosen ({ev})",
    # CBR / adaptation criteria (Phase 3)
    "cbr_similarity": lambda subj, ev, d:
        f"strongest archetype match on {ev}",
    "cbr_adaptation": lambda subj, ev, d:
        f"we adapted the archetype: {ev}",
    "cbr_weak_match": lambda subj, ev, d:
        f"the nearest archetype is a weaker-than-usual match ({ev})",
    # plan-level framing criteria
    "overall_fit": lambda subj, ev, d:
        f"overall it's the best fit for {subj} ({ev})",
    "sacrifice": lambda subj, ev, d:
        f"{subj} would have been better served elsewhere ({ev})",
}


def render_premise(p: Premise) -> str:
    """Render a Premise into a short English clause.

    Falls back to a neutral phrasing when the criterion isn't in the
    renderer map — but the evidence is always surfaced so the reader can
    see the source.
    """
    renderer = _CRITERION_RENDERERS.get(p.criterion)
    if renderer is not None:
        return renderer(p.subject, p.evidence, p.direction)
    # Fallback: neutral phrasing; still concrete because `evidence` is cited.
    verb = "against it" if p.direction == "opposes" else "in its favor"
    return f"{p.criterion.replace('_', ' ')} ({p.evidence}) is {verb} for {p.subject}"


# ---------------------------------------------------------------------------
# English rendering for a full Argument
# ---------------------------------------------------------------------------

def _join_clauses(clauses: list[str]) -> str:
    """Join short clauses into one English sentence. Oxford comma on 3+."""
    clauses = [c for c in clauses if c]
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    if len(clauses) == 2:
        return f"{clauses[0]} and {clauses[1]}"
    return ", ".join(clauses[:-1]) + f", and {clauses[-1]}"


def render_argument(arg: Argument) -> str:
    """Emit English in this shape:

      [conclusion]. [top 2 supporting premises]. The decisive factor:
      [decisive_premise]. But [top opposing premise] — [sacrifice].
      The closest alternative was [runner_up].

    Sections after the conclusion are only emitted when the underlying
    data is present; the shape adapts to the Argument, not the stop
    index.
    """
    if not arg.conclusion:
        # An Argument with no conclusion is malformed; fail loudly rather
        # than silently ship.
        raise ValueError("Argument.conclusion is empty — cannot render")

    parts: list[str] = [arg.conclusion.rstrip(".") + "."]

    # Top supporting premises (ignore the decisive one so we don't
    # double-cite it). Editorial / personal criteria — user_note,
    # dominant_user — are always included if present: they're the
    # concrete, human signals that make prose feel like a real
    # recommendation rather than a scorecard.
    PRESERVED_CRITERIA = {"user_note", "dominant_user",
                          "temporal_window", "quality_consensus"}
    candidates = [p for p in arg.supporting if p is not arg.decisive_premise]
    preserved = [p for p in candidates if p.criterion in PRESERVED_CRITERIA]
    ranked = [p for p in candidates if p.criterion not in PRESERVED_CRITERIA]

    # Top 1 scored (non-decisive) premise + every preserved premise, in
    # the order they were appended. Cap the total at 4 so prose stays
    # compact.
    top_supporting = (ranked[:1] + preserved)[:4]
    if top_supporting:
        clauses = [render_premise(p) for p in top_supporting]
        joined = _join_clauses(clauses)
        # Sentence-case only the first character — str.capitalize() would
        # downcase the rest and destroy proper nouns like "Alice".
        if joined:
            joined = joined[0].upper() + joined[1:]
        parts.append(joined + ".")

    # Decisive premise — the Mi-Stance.
    if arg.decisive_premise is not None:
        parts.append(
            f"The decisive factor: {render_premise(arg.decisive_premise)}."
        )

    # But-clause + sacrifice.
    if arg.opposing:
        top_opposing = arg.opposing[0]
        clause = f"But {render_premise(top_opposing)}"
        if arg.sacrifice:
            clause += f" — {arg.sacrifice.rstrip('.')}."
        else:
            clause += "."
        parts.append(clause)

    # Runner-up.
    if arg.runner_up:
        parts.append(f"The closest alternative was {arg.runner_up}.")

    return " ".join(parts)
