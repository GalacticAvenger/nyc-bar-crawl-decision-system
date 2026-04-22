# NYC Bar Crawl Decision System

**Yale CS 4580/5580 (Decision Systems) — Final Project**

A symbolic, explainable decision system that plans optimized NYC bar crawls for groups with competing preferences. Given a set of users (each with their own vibes, budget, and dealbreakers) and hard constraints (start/end time, neighborhoods, accessibility), the system produces a sequenced, time-stamped route, rich natural-language explanations for every decision, and a second entry point that lets the user react to the plan and have the system update preferences and replan — tracing every change back to either a reaction or a named ripple.

## Five academic techniques, working together

1. **Multi-Criteria Decision Analysis (MCDA)** with weighted utility + Pareto filtering — ten scorers across vibe, budget, drinks, noise, distance, happy-hour, specials, crowd-fit, novelty, and quality signal.
2. **Qualitative reasoning** — cheap/moderate/premium; library/conversation/lively/loud/deafening; next-door/short-walk/walk/hike; weak-signal/consensus-pick. Numbers in, labels out.
3. **VOTE-style group aggregation** with five strategies (utilitarian, egalitarian, Borda, Copeland, approval/veto) and a priority-ordered meta-selector. The selector returns a `StrategyDecision` carrying a **rank** (A = strong moral/structural claim, C = shallow fallback, E = margin too thin), a narrative name, a triggering profile signal, and a `considered_alternatives` list with a `why_not_chosen` string for every losing strategy — so the rationale for the rule that fired is comparable to the ones that didn't.
4. **Case-Based Reasoning** over 20 crawl archetypes, with a full R-loop: retrieve → **adapt** (length / vibe-injection / neighborhood retargeting, with each change logged as an `Adaptation` record) → seed into the router as a soft prior (tunable `cbr_seed_bonus`, default 0.15) → explanation cites every adaptation by name.
5. **Counterfactual & option generation** — per-stop runner-ups with strategy-agnostic relative gaps, unlock hints ("would have edged ahead if you'd weighted vibes differently"), structural counterfactuals ("+30 min", "+$10", "−1 vetoer"), and strategy counterfactuals (winner under each of the five aggregation strategies).

### Plus two additions that elevate the output quality

- **Structured arguments** — explanations are assembled as `Argument` objects (conclusion, supporting / opposing premises, decisive premise, sacrifice, runner-up) and linearized by a single renderer, instead of per-stop templates. The shape of the prose is driven by the data, not the stop index. Budget-honesty disclaimers, direction-aware rendering (`"fits your budget"` vs `"over your cap"`), and exhaustive placeholder-leak tests enforce the quality bar.
- **Dialogic replan** — `replan_with_reactions(previous_plan, reactions)` is a first-class second entry point. Reactions (accept / reject / swap / lock) drive a defensible multiplicative update rule ("Alex rejected the loudest stop — bumped his weight on the criteria where that bar scored in his bottom quartile by 30%, capped at 2× original"), locked stops are preserved exactly as fixed waypoints, and a `DeltaArgument` attributes every change in the new plan to either a specific reaction or a named preference-update ripple. Unattributable changes surface as a bug guard, not silently.

No neural nets, no ML training, no runtime API calls. Every decision is symbolic and traceable.

## Quickstart

```bash
pip install -r requirements.txt

# One-time: enrich the seed dataset into data/bars.json
python scripts/enrich_bars.py

# Primary deliverable — reproducible demo notebook
jupyter notebook notebooks/demo.ipynb

# Secondary — polished Streamlit UI, including the dialogic replan loop
streamlit run app/streamlit_app.py

# Tests — 147 unit + integration + phase tests
make test

# Evaluation harness (45 scenarios + 15 deep probes)
python evaluation/eval_harness.py
python evaluation/eval_deep.py
```

### The two public entry points

```python
from src.decision_system import plan_crawl, deeper_analysis
from src.dialogic import replan_with_reactions
from src.models import GroupInput, Reaction, UserPreference

# 1. Produce a plan
result = plan_crawl(group_input)

# If the plan's margin is tight, the selector flips rank to E
if result.traces["strategy_decision"].requires_deeper_analysis:
    diff = deeper_analysis(result)  # side-by-side winner vs runner-up

# 2. React to the plan and replan
reactions = [
    Reaction(user_id="Alex", stop_index=1, verdict="reject"),
    Reaction(user_id="Sarah", stop_index=0, verdict="accept", lock=True),
]
new_result = replan_with_reactions(result, reactions, group_input)
# new_result.traces["preference_updates"] — what changed and why
# new_result.traces["delta_argument"]     — attribution for each stop diff
```

## Repo layout

```
ARCHITECTURE.md        Module map + data flow + README divergences
CLAUDE.md              Standing rules for automated work on this repo
data/                  seed_bars.json, vibe_vocab.json, rules.yaml,
                       case_library.json, category_to_vibes.yaml,
                       default_hours.yaml, bar_overrides.yaml,
                       bars.json (produced by enrich_bars.py)
schemas/               bar.schema.json
src/                   Core modules (models, qualitative, scoring,
                       temporal, routing, group_aggregation,
                       case_based, option_generation, argument,
                       explanation_engine, dialogic, visualize,
                       decision_system, data_loader)
tests/                 Pytest suite — 147 tests across modules,
                       end-to-end, and the four phases
evaluation/            eval_harness.py (45 scenarios),
                       eval_deep.py (15 deep probes),
                       REPORT.md + FIXES.md + JSON result logs
scripts/               parse_seed.py, enrich_bars.py,
                       neighborhood_audit.py
notebooks/             demo.ipynb (canonical entry point)
app/                   streamlit_app.py (UI with reaction loop)
docs/                  WRITEUP.md, BUILD_LOG.md, dataset_report.md,
                       qa_scenarios.md, screenshots/
```

## Dataset honesty note

The 143-bar dataset begins with the author's personal Google Maps bar list. **Bar names, Google ratings, and review counts are real.** Happy hours, specials, exact open hours, and noise/crowd estimates are plausible category-based inferences — not authoritative. See `docs/WRITEUP.md` §Limitations and the disclaimer header at the top of `data/bars.json`.

## The thesis

This is a **decision system**, not a recommender. A recommender returns a ranked list. A decision system explains every choice, surfaces every trade-off, answers "why not Y instead?" before you ask, and — when the user pushes back — updates its model of their preferences in a way that can be narrated and reverted. The intelligence lives in the explanation, not the score.
