# NYC Bar Crawl Decision System — Writeup

**Yale CS 4580/5580 (Decision Systems), Spring 2026 — Samuel Meddin**

## Abstract

I built a symbolic, explainable decision system that plans NYC bar crawls for a group of people whose preferences don't agree. Given a group, a time window, and some constraints (neighborhoods, accessibility), it produces a sequenced, time-stamped route — and for every choice it made, it can tell you which preferences drove it, which ones lost, what the runner-up was, and what would have changed the outcome. There's a second entry point that lets you react to the plan (accept / reject / swap / lock individual stops) and replan with updated preferences, with every change traced back to either a reaction or a named ripple. 143 bars, 20 archetypes, no neural networks, no runtime API calls.

## 1. Why bar crawls?

Group bar planning stresses three parts of the decision-systems toolkit at once. **Preferences genuinely conflict** — one person wants conversational cocktails, another wants a loud dive, a third is on a student budget. **Constraints are time-sensitive** — happy hour ends at 7, the club doesn't open until 10, the kitchen closes at 11. **Geometry matters** — walking between stops costs time, and east-west blocks in NYC are roughly 3× north-south blocks. None of these are hard individually; the interesting part is composing them while keeping the reasoning legible.

I picked it because it's a real problem (I do this every weekend) and because the explanation surface is where the work lives — not in the score.

## 2. Pipeline

```
INPUT (Users, Constraints)
    │
    ├── data_loader (bars, cases, rules, vocab)
    │
    ├── disagreement_profile  ─►  select_strategy (rule-based, returns StrategyDecision)
    │
    ├── dealbreaker filter    ─►  excluded_bars (with rule_id + English reason)
    │
    ├── score_bar_for_user (per user × per bar × 10 criteria)
    │
    ├── aggregate (chosen strategy)  ─►  group_scores
    │
    ├── case_based.adapt_case (Phase 3)  ─►  AdaptedCase (seed sequence)
    │
    ├── best_route: greedy → 2-opt → exact (≤7 stops), with locks + seed prior
    │
    ├── option_generation: runner-ups, unlock hints, structural + strategy CFs
    │
    └── explanation_engine: build_*_argument → render_argument → Explanation tree
```

The crucial design rule: every downstream module reads the *reasoning trace* from upstream, not just the values. The explanation engine never re-derives anything. This is what lets a stop's English explanation cite the exact criterion that won, the user whose weight pulled hardest, and the runner-up's relative gap — the data is already on `PlanResult.traces`.

## 3. The five techniques

**3.1 Multi-Criteria Decision Analysis with Pareto filtering.** Every bar gets ten criterion scores in [0,1]: vibe (cosine of user weight vector × bar tag set), budget (exponential decay above the cap), drink_match (Jaccard), noise (ordinal distance), distance (per-mile penalty with amplification past a threshold), happy_hour_active, specials_match, crowd_fit, novelty, quality_signal. Per-user utility is the weighted sum; the `Score` object keeps both raw per-criterion values and the weighted contributions, so explanations can name the *top contributing criterion* instead of citing a single opaque number. Pareto filtering drops bars strictly dominated on every axis and logs the dominator pair.

**3.2 Qualitative arithmetic.** Numbers in, labels out — price tier (cheap/moderate/premium/splurge), noise level, distance bucket, crowd level, quality signal — with thresholds in `data/rules.yaml`. Rules and explanations operate on labels. Changing a threshold means editing one YAML file; no code touches.

**3.3 VOTE-style group aggregation with a meta-strategy selector.** Five strategies — utilitarian sum, egalitarian min (Rawlsian), Borda count, Copeland pairwise majority, approval/veto. The interesting part is that the system doesn't pick a strategy in advance: it computes a disagreement profile (dealbreaker density, budget spread, vibe variance, max preference intensity, group size) and a priority-ordered rule fires the right strategy for *this* group. Approval/veto if anyone has dealbreakers; egalitarian if budgets diverge; Copeland if vibes split; Borda if one user is way more peaked than the rest; otherwise utilitarian. The chosen strategy and the rule that fired are part of the explanation, not metadata.

One ordering detail that took me a while to catch: the disagreement profile has to be computed *before* the dealbreaker filter, otherwise vetoed bars are already gone and the veto-density signal collapses to zero — approval/veto would never fire.

**3.4 Case-Based Reasoning.** A library of 20 hand-authored crawl archetypes (LES Speakeasy Ladder, Bushwick Dance Crawl, Hell's Kitchen Pub Crawl, etc.). Each case stores solution *shapes* — bar_type + vibe_profile per stage — not specific bars. Retrieval scores cases on size / budget / neighborhood / vibe similarity. Phase 3 closes the loop with adaptation (see §6).

**3.5 Counterfactual + option generation.** For every stop the system precomputes, before you ask: the runner-up bar (with per-criterion gap and an unlock hint — *"would have edged ahead if you'd weighted vibes differently"*); structural counterfactuals (+30 min, +$10 per drink, drop a vetoer — re-run the planner and report the delta); and strategy counterfactuals (the winner under each of the five aggregation methods). All computed during search, stored on `PlanResult.traces`. The explanation engine just looks them up.

## 4. Dataset

I started with my actual personal Google Maps bar list — 159 entries with names, ratings, review counts. `scripts/parse_seed.py` structures it; `scripts/enrich_bars.py` produces 143 bars with all fields populated (16 editorial exclusions: 3 closed, 13 actually restaurants). Bar names and Google ratings are real. Hours, happy hours, specials, noise levels, and crowd estimates are *plausible category-based inferences* — a real deployment would hit Google Places. Every "X has happy hour 5–7" is a hypothesis, not a fact, and the writeup + the JSON disclaimer header both say so. The dataset skews Manhattan-downtown + Brooklyn (Bushwick / Williamsburg) + Queens (Astoria) because that's where I actually go.

## 5. Worked example

Three friends, Alice (intimate / conversation, $20 cap, cocktails), Bob (lively / unpretentious, $12, beer), Carol (hidden-gem / conversation, $16, cocktails) — Friday 7pm to 11:30pm, EV + LES only, three stops. Vibe variance lands at 0.39, so the meta-selector fires Copeland (rule: vibe variance > 0.30). The plan: **Lost in Paradise Rooftop → The Back Room → Vida Verde**, with explanations like:

> We open at **Lost in Paradise Rooftop** at 7:08pm, a mid-priced, conversational spot in Lower East Side. The vibe matches what the group wanted (tags: airy, conversation, craft-cocktails), strong consensus pick (4.7★ over 6,621 reviews), and Alice rated this highest of the 3. The decisive factor: it's a widely-loved spot. But it's over Bob's cap (~$14/drink over Bob's $12 cap) — Bob is paying over their cap at this stop.

Every clause is composed from upstream traces — the dominant user, the top weighted contribution, the over-budget honesty disclaimer. Nothing is generative. A bad explanation would say *"Based on your preferences, we recommend these bars."* The whole point of the project is that you can do better with templates + structured arguments + traces, without an LLM in the loop.

## 6. After the proposal: four phases I added

The proposal covered §3.1–3.5 above. After approval I added four phases. Each is independent (each ships value alone) but they compose. Each was test-first, gated by a green pytest + green eval harness before commit.

**Phase 1 — VOTE-shaped strategy decisions.** The meta-selector used to return a `(name, rule_id, rationale)` tuple. I packaged that into a `StrategyDecision` dataclass that also carries a rank (A = strong moral / structural claim like approval-veto or egalitarian; B = positional / pairwise like Borda or Copeland; C = shallow utilitarian fallback; E = "margin too thin, deeper analysis warranted"), a narrative_name usable in prose, the triggering profile signal as an English string, and a `considered_alternatives` list — for each of the four losing strategies, why it didn't fire (either "a higher-rank rule applied" or the specific threshold the metric failed to clear). I also added a deeper-analysis tier: when the chosen plan's mean normalized runner-up gap falls below a YAML threshold (default 0.05), the rank gets reset to E and `requires_deeper_analysis=True`; a `deeper_analysis(plan_result)` function returns a side-by-side per-stop diff. The system now says "we picked, but it was close — here's the side-by-side."

**Phase 2 — Structured arguments.** Per-stop and strategy explanations were template-driven but the templates were ad-hoc — a per-stop function with branching, a per-strategy function with five if-elif arms. I split it into a `Premise` dataclass (subject, criterion, direction, magnitude, evidence) and an `Argument` dataclass (conclusion, supporting, opposing, decisive_premise, sacrifice, runner_up). `build_stop_argument` and `build_strategy_argument` assemble the structured object; `render_argument` linearizes it into prose with a fixed shape: *[conclusion]. [top supporting + preserved editorial premises]. The decisive factor: [decisive]. But [top opposing] — [sacrifice]. The closest alternative was [runner_up].* Per-criterion renderers are direction-aware so "budget" reads as *"fits the group's budget"* under `supports` and *"it's over Alex's cap"* under `opposes` — the old direction-blind template would have produced incoherent prose like *"it fits Alex's budget (~$22 over Alex's $18 cap)"*. There's an exhaustive test that walks every renderer × direction × subject × evidence + a full plan-tree to make sure rendered prose never contains a literal `{placeholder}` token.

**Phase 3 — Closing the CBR R-loop.** Original CBR was retrieve-only; the top-3 cases got cited in prose but never influenced routing. Phase 3 implements the *Revise* step. `adapt_case(case, group, bars, rules) → AdaptedCase` does three layered adaptations: (a) length — pad or trim the solution_sequence to match `max_stops` (drop the lowest-priority stage by `vibe_profile` magnitude when trimming); (b) vibe — if a user has a must-have vibe (weight ≥ 0.8) absent from the case, inject it into the richest stage's profile; (c) constraint — if the case targets a neighborhood the group excluded, retarget to the nearest allowed neighborhood by centroid distance. Every change becomes an `Adaptation(field_changed, from_value, to_value, reason)` record. The adapted sequence is fed to the router as a `seed_sequence`; each candidate gets an additive `cbr_seed_bonus * (matched stage-vibe weight / total)` (magnitude tunable in YAML, default 0.15). Critically this is a *prior* not a *constraint* — strong off-archetype bars can still win. A test verifies that by checking some chosen bar lands outside the seed's vibe union.

**Phase 4 — Dialogic replan with bounded preference learning.** A second public entry point: `replan_with_reactions(previous_plan, reactions, original_group, bars, cases, rules)`. A `Reaction(user_id, stop_index, verdict, lock)` is one user's reaction to one stop; verdict is accept / reject / swap; `lock=True` pins the stop in place. The preference-update rule is intentionally simple, not Bayesian: on a reject, for each criterion where the rejected stop scored in the user's bottom quartile across the plan, multiply that user's weight on that criterion by 1.3, capped at 2× the original; on an accept of an over-budget stop, widen the cap by half the overshoot. Every change emits a `PreferenceUpdate` record with English reason ("Alex rejected stop 2, which scored in their bottom quartile on 'noise'; bumped the weight by 30% capped at 2× original"). The router has a new `_greedy_fill_with_locks` path that fixes locked positions and greedy-fills the rest. `build_delta_argument` produces a `DeltaArgument` where every changed stop is attributed to either a specific reaction or a named preference-update ripple — and any unattributable change is surfaced (treated as a bug, not hidden). `revert_user_updates` undoes one user's changes without losing the others'; the pref-update narrative ends with *"Say 'revert Alex' or 'revert Sarah' to undo those updates and replan."* The Streamlit UI surfaces all of this end-to-end (per-stop verdict + lock + Replan button).

**Phase 4.1 — Style-aware budget realism.** A late UX fix prompted by an actual symptom: *"Pregame → clubs"* was producing karaoke bars instead of nightclubs. Two compounding causes: the default neighborhood filter (EV + LES) hard-excluded every dance-floor bar in the dataset (all in Bushwick / Williamsburg), and the hardcoded `2.0×` budget gross-mismatch multiplier excluded real clubs that legitimately run $20–32/drink. I moved the multiplier to YAML (`dealbreaker_rules.budget_gross_mismatch.multiplier`), added a per-plan `GroupInput.budget_multiplier` override, and gave each `NIGHT_STYLES` entry its own slider default + multiplier — Pregame→clubs / Birthday / Late-night / Rooftop now get 2.5×. After the fix, with neighborhoods expanded to include Bushwick, Pregame→clubs lands *Planet Rose → House of Yes → Sing Sing Ave A.* — House of Yes is an actual Bushwick dance club. The peak slot pulls real nightlife.

## 7. Evaluation

- **Unit tests** — 151 passing, 1 skipped. Covers qualitative thresholds, score formulas, Pareto filter, temporal-window logic, Haversine accuracy (Times Square ↔ Union Square within 5%), all five aggregation strategies, meta-selector rule firing, CBR retrieval + adaptation, runner-up + counterfactuals, explanation template specificity, plus the four post-proposal phases (StrategyDecision shape + deeper-analysis trigger, Argument placeholder-leak guard, CBR adaptation + dealbreaker preservation, dialogic replan + locked-stop fixity + revert).
- **Eval harness** — `evaluation/eval_harness.py` runs 45 scenarios (aligned, disagreement-forcing, edge, all 9 night styles, 20 random); `evaluation/eval_deep.py` runs 15 deep probes (determinism, user-order invariance, structural invariants, threshold transitions, dataset coverage, runner-up gap distribution, CBR coherence, vibe vocab leakage). Detailed findings + fixes in `evaluation/REPORT.md` and `evaluation/FIXES.md`.
- **Invariants** — across all 45 scenarios + 100 random plans: every stop is open at arrival, monotonic times hold, no veto bar appears, neighborhood + max-stops respected, walking-distance arithmetic re-verifies, same input → identical route signature.
- **Notebook** — re-executes top-to-bottom in a fresh kernel (`jupyter nbconvert --execute notebooks/demo.ipynb`).
- **Performance** — end-to-end `plan_crawl` on 143 bars × 3 users × 3 stops with structural counterfactuals: p50 ≈ 28 ms / p95 ≈ 60 ms on a 2023 MacBook.

## 8. Limitations

The honest version:

- Hours / happy hours / specials are category defaults, not live data. A real deployment hits Google Places.
- Vibes are heuristic — inferred from Google category + my own user notes; two bars with the same Google category get the same defaults even when they're materially different.
- The router is exact only for ≤7 stops; larger crawls fall back to greedy + 2-opt. Realistic group crawls are 3–5, so this is fine.
- Preference learning is bounded — multiplicative reject-bumps capped at 2× original, additive budget-widening at 50% of overshoot. I deliberately avoided gradient descent or Bayesian updates so the updates stay narratable. Trade-off: subtle drift takes more rounds to express than a Bayesian update would.
- Locked-stop replan path skips global 2-opt — the user pinned them, so I don't overrule.
- Single-night crawls only; no multi-day (Fri-Sat-Sun) arc.
- Dataset is geographically concentrated. The eval harness measured 27/143 (~19%) unique-bar coverage across 100 random plans — a known funnel from the default East Village start.

## 9. Future work

In rough priority order:

1. **Live data integration** — Yelp / Google Places for real hours, current specials, crowd telemetry. Highest-leverage upgrade because it turns "plausible defaults" into facts.
2. **Multi-night arcs** — Fri night → Sat brunch → Sat night.
3. **Capacity forecasting** — a learned crowding model from Google Popular Times instead of category templates.
4. **Richer stakeholder modeling** — designated driver, food-required, early-flight constraints. The `UserPreference` shape needs a `roles` field.
5. **Larger dataset** — 500+ bars via friends' lists. Bottleneck is curation, not architecture.
6. **Replan-loop UX** — drag-to-reorder, mobile-first; the current radio-button form is functional but not native-feeling.
7. **Constrained 2-opt across locks** — reclaim some objective without overruling the user's pins.
8. **Bayesian preference posterior** — sample-efficient but harder to explain. Worth a side-by-side experiment against the current multiplicative rule.

## 10. How to run

```bash
pip install -r requirements.txt

# Regenerate the dataset (deterministic; reads seed_bars.json → bars.json)
python scripts/enrich_bars.py

# Tests
make test

# The canonical demo
jupyter notebook notebooks/demo.ipynb

# Streamlit UI (includes the dialogic reaction loop)
streamlit run app/streamlit_app.py
```

Yale Zoo compatibility: core modules use only `numpy`, `pandas`, `pyyaml`, `jsonschema`, `matplotlib`, `folium`, `jupyter` — nothing system-level. The Streamlit UI adds `streamlit` (also pure-Python). Screenshot regeneration uses Playwright + Chromium, but that's author-side tooling — the code under review needs neither.

## 11. Screenshots

Every artifact in `docs/screenshots/` is regenerated programmatically by `scripts/regenerate_screenshots.py` from the current code (no hand-edited images). Highlights:

- `ui_initial.png`, `ui_plan.png`, `ui_replan.png` — Streamlit captures showing the splash, a planned crawl with Phase 2 Argument-shaped prose, and a post-replan view with the auto-narrated preference updates.
- `route_map.html` + `timeline.png` + `score_breakdown.png` — Folium map, Gantt, and per-criterion stacked-bar from the canonical aligned-trio plan.
- `pregame_clubs_route.html` + `pregame_clubs_timeline.png` — Phase 4.1 demo: House of Yes ends up in the peak slot.
- `argument_internals.md` + `replan_demo.md` — markdown introspection of one stop's structured `Argument` and one `replan_with_reactions` transcript, so you can see the structured reasoning that produces the prose.

## 12. Division of labor

Solo project.
