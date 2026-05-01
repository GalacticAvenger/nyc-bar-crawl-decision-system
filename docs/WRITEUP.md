# NYC Bar Crawl Decision System — Writeup

**Yale CS 4580/5580 (Decision Systems), Spring 2026**

## Abstract

This project builds a **symbolic, explainable decision system** that plans a sequenced NYC bar crawl for a group of users with competing preferences, time-sensitive constraints (happy hours, kitchens closing, late-night-only venues), and multi-criteria trade-offs (vibe, budget, drink match, noise, novelty, quality signal, and more). The system combines five course-aligned AI techniques — multi-criteria decision analysis with Pareto filtering, qualitative arithmetic, VOTE-style group aggregation with a rule-based meta-strategy selector, case-based reasoning over twenty crawl archetypes, and counterfactual/option generation. The central design commitment is that **every decision carries its provenance**: the route object stores not just "Bar X at 8:30pm" but *why* Bar X beat its runner-ups, *which* preferences drove the choice, *which* preferences lost, and *what* the closest alternative was. Output is a sequenced, time-stamped route with rich natural-language explanations plus a Folium map, Gantt timeline, and per-stop score breakdown. 143 curated NYC bars, 20 archetypes, no neural networks, no runtime API calls.

## 1. Domain motivation — why bar crawls?

Group bar-crawl planning is a real decision problem that stresses three parts of the decision-systems toolkit simultaneously:

**Preferences genuinely conflict.** One person wants conversational cocktails; another wants a loud dive; a third is on a student budget. A naive recommender that returns "the bar with the highest score" will leave someone unhappy. The question isn't *what does the group like*; it's *how should we mediate what the group disagrees about*.

**Constraints are time-sensitive.** Happy hours close at 7pm. Nightclubs don't open until 10pm. An Irish pub's trivia night is Tuesday only. A kitchen closes at 11pm. The planner must schedule stops so that each one *lands inside* the temporal window where its bonuses apply.

**The city has geometry.** Walking between stops costs time and effort; crossing avenues in NYC is meaningfully longer east-west than north-south. A greedy ordering that ignores geometry produces plans nobody would actually follow.

These three pressures — preference mediation, temporal reasoning, and spatial optimization — make bar crawl planning a good vehicle for a decision system. None of them are hard individually; the challenge is composing them while keeping the reasoning legible.

## 2. Pipeline overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    INPUT (Users, Constraints)                     │
├──────────────────────────────────────────────────────────────────┤
│  ┌─── data_loader ──────── (bars, cases, rules, vocab) ─────┐    │
│  │                                                           │    │
│  │                                                           ▼    │
│  │   dealbreaker filter ──────────► excluded_bars (with rules)  │
│  │                                                           │    │
│  │                                                           ▼    │
│  │   disagreement_profile ──────► select_strategy (rule-based) │
│  │                                                           │    │
│  │                                                           ▼    │
│  │   score_bar_for_user (per user, per bar) ────► per_user_scores │
│  │                                                           │    │
│  │                                                           ▼    │
│  │   aggregate (chosen strategy) ─────────────────► group_scores │
│  │                                                           │    │
│  │                                                           ▼    │
│  │   best_route: greedy → 2-opt → exact (≤ 7 stops)         │    │
│  │                                                           │    │
│  │                                                           ▼    │
│  │   find_runner_ups + unlock_analysis                      │    │
│  │   all_structural_counterfactuals (re-runs planner)        │    │
│  │   strategy_counterfactuals (every strategy's winner)      │    │
│  │                                                           │    │
│  │                                                           ▼    │
│  │   explanation_engine: templates + traces → Explanation tree  │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

The crucial design constraint: every downstream module receives not just the *values* it needs but the *reasoning trace* from upstream. The explanation engine never re-derives anything; it just composes traces into natural language via templates.

## 3. Techniques in depth

### 3.1 Multi-Criteria Decision Analysis with Pareto filtering

Every bar is scored on ten criteria: `vibe`, `budget`, `drink_match`, `noise`, `distance`, `happy_hour_active`, `specials_match`, `crowd_fit`, `novelty`, `quality_signal`. Each score lives in `[0, 1]` and comes from an explicit formula:

- `vibe(b, u) = cosine(u.vibe_weights, b.vibe_tags)`
- `budget(b, u) = exp(−max(0, b.avg_drink − u.max_per_drink) / u.max_per_drink)` — smooth penalty above the cap, full credit below
- `drink_match(b, u) = jaccard(u.preferred_drinks, b.drink_categories_served)`
- `noise(b, u) = 1 − |ord(b.noise) − ord(u.preferred_noise)| / max_diff`
- `quality_signal(b) = normalize(rating × log10(reviews + 1))` over the dataset

Per-user utility is the weighted sum `U(b, u) = Σ w_u[c] · score_c(b, u)`; the Score object retains both the raw per-criterion vector and the weighted contributions, so downstream modules can pick the *top contributing criterion* for any decision. This is what makes explanations specific: "Bar X won on vibe (contrib 0.28) and quality_signal (0.11)" rather than "Bar X had the highest score."

Pareto filtering runs on each user's score vector before aggregation: if bar Y is strictly dominated on every criterion by some bar X, Y is dropped and the `(Y, X)` pair is logged so the explanation engine can say *"Y wasn't considered because X beats it on every axis you care about."*

### 3.2 Qualitative arithmetic

Every quantitative feature has a qualitative projection with thresholds defined in `rules.yaml`:

- Price: `cheap (<$8) | moderate ($8–14) | premium ($14–20) | splurge ($20+)`
- Noise: `library | conversation | lively | loud | deafening`
- Distance: `next-door | short-walk | walk | hike | transit-worthy`
- Crowd (time-aware): `dead | mellow | lively | packed | overflowing`
- Quality signal: `weak_signal | moderate_signal | strong_signal | consensus_pick`

Rules and explanations operate on labels, not numbers. `IF budget = cheap AND bar = splurge THEN exclude WITH reason = "price mismatch"` reads better than its numeric equivalent, and changes to thresholds (a classic cause of scoring regressions) are localized to the rules file.

### 3.3 VOTE-style group aggregation with meta-strategy selection

Individual utilities must be combined into a group utility. This project implements five strategies:

1. **Utilitarian sum** — `G(b) = Σ_u U(b, u)`. Maximizes average happiness.
2. **Egalitarian min (Rawlsian)** — `G(b) = min_u U(b, u)`. Protects the unhappiest member.
3. **Borda count** — each user ranks bars; bar gets `(n − rank)` points per user.
4. **Copeland pairwise** — for every pair `(x, y)`, who wins under majority? The bar with the most pairwise wins wins overall (Condorcet-style).
5. **Approval / veto** — each user's vetoes hard-exclude; approvals tally.

The **meta-strategy selector** is the academically interesting part. Rather than hard-coding a method, the system computes a *disagreement profile* (dealbreaker density, budget spread, vibe variance, preference intensity, group size) and applies rules (stored in `rules.yaml`) to pick a strategy:

```
IF dealbreaker_density > 0.2                THEN approval_veto      "someone has hard constraints"
ELIF budget_spread > 3×                     THEN egalitarian_min    "budget gap risks leaving poorest member miserable"
ELIF vibe_variance > 0.3                    THEN copeland_pairwise  "split on vibe; want Condorcet-style consensus"
ELIF max_preference_intensity > 0.35        THEN borda_count         "one strong preference; prevent steamroll"
ELSE                                        THEN utilitarian_sum    "group is aligned; maximize total welfare"
```

The chosen strategy and the rule that fired are **part of the final explanation**. This is a concrete implementation of the course's "rule-based expert system" and "VOTE" suggestions: the system reasons about *which method of reasoning to use*, not just about the object-level decision.

One important ordering detail: the disagreement profile is computed against the **full** bar list before the dealbreaker filter runs. Otherwise the veto-density signal disappears (vetoed bars have already been dropped), and the approval/veto rule can never fire.

### 3.4 Temporal constraint reasoning

Each bar carries zero or more `TemporalWindow(start, end, kind, bonus)` entries. The routing objective rewards hitting active windows at arrival:

```
net_utility(bar, arrival) = group_score(bar) + temporal_bonus(bar, arrival) − walking_penalty
```

Happy-hour bonuses scale with the user's *budget weight* — budget-sensitive groups value cheap-drink windows more. Specials are bucketed by `kind` (trivia, karaoke, live music, theme night, industry night) with kind-specific multipliers. Past-midnight bars are handled by the 24+ hours convention (`"26:00"` = 2am next day); `is_open(bar, dt)` checks both today's window and the previous day's carry-over.

### 3.5 Routing — TSP with time windows and profit

The problem is a small-scale TSP-with-time-windows-and-profit (typically ≤ 8 stops). The router uses:

1. **Greedy construction** from the start location: at each step, pick the highest `utility + bonus − walking_penalty` bar that's reachable and open.
2. **2-opt local search** on the greedy result, with feasibility repair — any swap that violates a window is rejected.
3. **Exact enumeration** as a sanity check when the chosen set has ≤ 7 bars (720 permutations max). If the exact ordering beats 2-opt, we log that and accept it — gold for the explanation ("we checked all 720 orderings; this was best").

Walking distances come from Haversine with a small avenue-crossing penalty (NYC east-west blocks are about 3× as long as north-south blocks). The routing module emits a `search_log` the explanation engine reads verbatim.

### 3.6 Case-Based Reasoning

A curated library of 20 crawl archetypes (`case_library.json`) contains solution *shapes*, not specific bars: each case lists `bar_type` + `vibe_profile` per step. At plan time the system retrieves the most-similar cases using weighted similarity over group size, budget tier, neighborhood fit, and vibe summary. Adaptation maps each abstract step to concrete candidate bars; the top match becomes a potential warm start for the router (and a narrative anchor: *"this plan resembles our LES Speakeasy Ladder archetype"*).

The 20 cases were hand-authored to reflect archetypes that match the actual seed dataset — Astoria Beer Garden Night, LES Speakeasy Ladder, Bushwick Dance Crawl, Hell's Kitchen Pub Crawl, etc. A case referencing bar types not present in the seed data would be useless.

### 3.7 Option generation and counterfactuals

For every stop the system precomputes — **before the user asks**:

- **Runner-up**: the 2nd-best bar for that slot (excluding bars already in the route), the gap, and the per-criterion breakdown.
- **Unlock hint**: the single criterion where the runner-up beats the winner most strongly, phrased naturally ("it would have won if you'd had a tighter budget" / "if you'd prioritized widely-loved picks").
- **Structural counterfactuals**: +30 min / +$10 per drink / −1 vetoer — the planner re-runs on the modified group, and the explanation summarizes the delta.
- **Strategy counterfactuals**: the top bar under each of the five aggregation strategies, so the user can see what a different method would have picked.

These aren't post-hoc narration — they're computed during search and stored on `PlanResult.traces`. The explanation engine just looks them up.

## 4. Dataset — 143 bars, provenance-first

The dataset begins with a raw export of the author's personal Google Maps bar list — 159 entries including names, Google ratings, review counts, and price indicators. `scripts/parse_seed.py` structures this into `data/seed_bars.json`. Editorial decisions (16 exclusions: 3 permanently closed, 13 primarily restaurants/museums) are preserved alongside the data with rationale strings.

`scripts/enrich_bars.py` then produces `data/bars.json` — 143 bars with all fields populated:

- **Geocoding**: each bar assigned a neighborhood (hand-curated; NYC knowledge required for the 143 bars), then a jittered lat/lon around the neighborhood's centroid. Ambiguous names (two Barcade entries — Williamsburg and St. Marks) resolve to their correct distinct locations.
- **Vibe tags**: layered — Google category defaults (`category_to_vibes.yaml`) → primary-function overrides → user-note overrides. The whispering rule at Burp Castle fires via the `"whispering"` pattern in user notes and *forces* noise level to `library`.
- **Hours**: category-based defaults from `default_hours.yaml`. Bars open Monday 4pm–2am, pubs noon–2am, nightclubs Thu-Sat 10pm–4am, wine bars Tue-Sun 5pm–midnight, etc.
- **Happy hours and specials**: *plausible category defaults* — one happy hour per category, one featured special when the category suggests it.
- **quality_signal**: computed post-hoc across the dataset: `normalize(rating × log10(reviews + 1))` → `[0, 1]`.

**Honesty disclaimer**: bar names and Google ratings are real. Happy hours, exact hours, noise levels, and crowd estimates are plausible inferences — not authoritative. The dataset report (`docs/dataset_report.md`) and the writeup disclose this. The system's explanations never claim fabricated attributes as fact; every "X has happy hour from 5–7" is a hypothesis rooted in the bar's category.

Neighborhood distribution is Manhattan-heavy (downtown + Hell's Kitchen dominance), with Brooklyn concentrated in Bushwick + Williamsburg and Queens in Astoria. Demo scenarios lean into these strong neighborhoods rather than trying to paper over the bias.

## 5. Worked example

**Group**: Alice (vibes: intimate, conversation, polished; $20 budget; cocktails), Bob (lively, unpretentious, local-institution; $12; beer), Carol (hidden-gem, conversation, intimate; $16; cocktails). Friday 7pm to 11:30pm, start in East Village, three stops, limited to East Village + Lower East Side.

Disagreement profile: vibe variance 0.39 — vibes genuinely diverge. The meta-selector fires `strategy_copeland` (rule: vibe variance > 0.30), logging: *"Copeland pairwise-majority finds a Condorcet-style consensus robust to this split."*

The planner produces: **Lost in Paradise Rooftop → Barcade → Otto's Shrunken Head**, 1.7 miles of walking, zero windows captured (arrival times after happy-hour close). The three stops read differently:

> We open at **Lost in Paradise Rooftop** (7:13pm) — it matches the vibe you're after, and it's a mid-priced, conversational room in Lower East Side. Strong consensus pick: 4.7★ over 6,621 reviews. Alice scored this highest of the three.
>
> From there, over to **Barcade** at 8:08pm, a mid-priced lively spot in East Village. It fits the budget.
>
> Closing at **Otto's Shrunken Head** at 8:58pm, a cheap lively spot in East Village. It matches the vibe you're after. Close second: Vida Verde - Tequila Bar — it would have edged ahead if you'd prioritized widely-loved picks. Bob scored this highest of the three.

Each sentence is composed from traces: the winning criterion, the qualitative tags, the dominant user, and the runner-up's unlock hint. Nothing is generative.

## 6. Explanation philosophy

The BUILD_PLAN's Quality Bar requires every explanation to be **specific, causal, honest about trade-offs, counterfactually aware, strategy-aware, personal where possible, quality-aware, compact, and falsifiable**. Template-based generation — not LLM generation — delivers all nine.

Bad explanation (what we avoid):

> Based on your preferences, we recommend these three bars. They match your vibe and budget.

Good explanation (what the system actually emits, see §5):

- Names the bar and the neighborhood.
- States the dominant reason in natural language (*matches the vibe you're after*, not *vibe=0.89*).
- Surfaces qualitative tags (*mid-priced, conversational room*).
- Cites quality signal when it's high (*4.7★ over 6,621 reviews*) or calls out thin review bases when it's not.
- Names a specific user (*Alice scored this highest of the three*).
- Surfaces `user_note` when present (*you'd noted: "only whispering allowed lol"*).
- Includes runner-up with unlock hint (*"would have edged ahead if you'd prioritized…"*).

The per-user served-ness table (§3, stakeholder taxonomy) is the final honesty move: each user sees their own mean score on the route, how many of their top-5 bars made it, whether their vetoes were respected, and how many stops landed within their budget.

## 7. Evaluation

- **Unit tests**: 151 passing, 1 skipped (`tests/`). Coverage spans qualitative thresholds, score formulas, Pareto filter, temporal-window logic, Haversine accuracy (Times Square ↔ Union Square within 5%), all five aggregation strategies, meta-selector rule firing, CBR retrieval + **adaptation** (Phase 3), runner-up + counterfactual generation, explanation template specificity, and the four post-proposal phases (StrategyDecision shape + deeper-analysis trigger, structured-Argument placeholder-leak guard, CBR adaptation + dealbreaker preservation, dialogic replan + locked-stop fixity + revert semantics).
- **Integration tests**: full-pipeline scenarios (aligned friends, wide-budget gap, many-vetoes, infeasible window, empty group, determinism, time-window respect).
- **Evaluation harness**: `evaluation/eval_harness.py` runs 45 scenarios across aligned / disagreement-forcing / edge / all 9 night-style / 20 random groups. `evaluation/eval_deep.py` runs 15 deep probes (determinism, user-order invariance, structural invariants, threshold transitions, dataset coverage, runner-up gap distribution, CBR retrieval coherence, vibe vocab leakage). Detailed findings + fixes in `evaluation/REPORT.md` and `evaluation/FIXES.md`.
- **Invariants**: across all 45 scenarios + 100 random plans — every stop is open at arrival, monotonic times hold, no veto bar appears, neighborhood + max-stops constraints respected, walking-distance arithmetic re-verifies. Same input ⇒ identical route signature.
- **Notebook**: cells execute top-to-bottom in a fresh kernel (`jupyter nbconvert --execute notebooks/demo.ipynb`).
- **Performance**: end-to-end `plan_crawl` on 143 bars × 3 users × 3 stops with structural counterfactuals: p50 ≈ 28 ms / p95 ≈ 60 ms on a 2023 MacBook.

## 8. Limitations

The honest version (post-Phase-4):

- **Hours, happy hours, and specials are category defaults, not live data.** A real deployment would hit Google Places or a dedicated feed.
- **Vibes are heuristic.** We infer from Google category + user notes; two bars with the same Google category get the same default vibes even when they differ in character.
- **Router is exact only for ≤ 7 stops.** Realistic group crawls are 3–5, so this is fine; but a large party crawl of 10+ stops would fall back to 2-opt.
- **Preference learning is bounded** (Phase 4) — multiplicative reject-bumps capped at 2× original, additive budget-widening at 50% of overshoot. The system intentionally avoids gradient-descent or Bayesian updates so updates remain narratable. The downside: subtle drift from many small reactions takes more rounds to express than a Bayesian update would.
- **Locked-stop replan path bypasses 2-opt.** Phase 4's `_greedy_fill_with_locks` greedy-fills around fixed positions and skips global swap — the rationale is that the user pinned them, so the planner shouldn't overrule. A more aggressive route quality could be reclaimed with a constrained 2-opt that only swaps unlocked positions.
- **Single-night crawls only.** A weekend arc (Fri night → Sat brunch → Sat night) is out of scope.
- **English-only, US drink categories.** Internationalizing would require vocabulary extensions.
- **Dataset is geographically concentrated** in downtown Manhattan, Hell's Kitchen, Williamsburg, Bushwick, Astoria — matching where the author actually goes. A UES-only crawl has fewer plausible plans. The eval harness measured 27/143 (≈19%) unique-bar coverage across 100 random plans — a known funnel from the default East Village start.

## 8.5 Post-proposal additions: deepening the explanation surface (Phases 1–4)

After the proposal was approved, four additional phases were implemented to push the system closer to what the course's VOTE / Slade / Schank-style explanation literature actually asks for. Each phase was test-first, gated by a green pytest run + green eval harness before commit. The phases are independent (each ships value alone) but compose cleanly.

### 8.5.1 Phase 1 — VOTE-shaped strategy decisions

The original meta-selector returned a `(strategy_name, rule_id, rationale_string)` tuple — enough to drive scoring, not enough to explain. Phase 1 packages the meta-selector's output into a `StrategyDecision` dataclass that carries:

- `strategy_id` (the old string return, preserved for back-compat)
- `rank` ∈ {A, B, C, E} — A = strong moral / structural claim (`approval_veto`, `egalitarian_min`); B = positional / pairwise (`borda_count`, `copeland_pairwise`); C = shallow fallback (`utilitarian_sum`); E = "margin too thin, deeper analysis warranted"
- `narrative_name` (e.g. `"Protect the person who'd otherwise be left out"`) — phrase usable in explanation prose
- `quote` — a quotable sentence a group member might say that captures the strategy's spirit
- `triggering_profile_signal` — the specific metric + comparison that fired the rule (e.g. `"budget_spread_ratio=2.4× exceeded threshold 2.0×"`)
- `applies_when` — the English version of the machine-readable condition
- `considered_alternatives` — for each of the four losing strategies, a `(strategy_id, rank, why_not_chosen)` triple. `why_not_chosen` cites either *"a higher-rank strategy applied"* or the specific threshold the metric failed to clear.

Plus a deeper-analysis tier: when the chosen plan's mean normalized runner-up gap (across stops) falls below a YAML-configurable threshold (default 0.05), the decision's `rank` is reset to `E` and `requires_deeper_analysis=True`. A new `decision_system.deeper_analysis(plan_result)` function returns a side-by-side per-stop diff (winner vs runner-up + criteria gaps + unlock hints) for the caller to render. This is the system saying *"we picked, but it was close — here's the side-by-side."*

The rank/quote/applies_when fields live in `data/rules.yaml` so adding a new strategy is a config edit, not a code change.

### 8.5.2 Phase 2 — Structured Arguments

Per-stop and strategy explanations were template-driven but ad-hoc — a per-stop function with branching, a per-strategy function with five if-elif arms. Phase 2 introduces a two-layer model:

- `Premise(subject, criterion, direction, magnitude, evidence)` — a single reason, for or against, with a normalized contribution magnitude and a concrete evidence string ("$14/drink, moderate tier" / "rank A; dealbreaker_density=0.0 below threshold").
- `Argument(conclusion, supporting, opposing, decisive_premise, sacrifice, runner_up)` — assembled from upstream traces; never re-derives.
- `render_argument(arg) → str` — a single linearizer that emits prose in this fixed shape: `[conclusion]. [top scored supporting + preserved editorial premises]. The decisive factor: [decisive premise]. But [top opposing] — [sacrifice]. The closest alternative was [runner_up].`

Per-criterion renderers are direction-aware: budget under `supports` reads *"it fits the group's budget"*; under `opposes` it reads *"it's over Alex's cap"* — the old direction-blind template would have produced *"it fits Alex's budget (~$22 over Alex's $18 cap)"*, which is incoherent prose.

`build_stop_argument` and `build_strategy_argument` build the structured object. The strategy Argument's decisive premise cites `decision.triggering_profile_signal`; its opposing premises cite the top two `considered_alternatives` (with the strategy named in prose). A CBR Argument generator (added in Phase 3) follows the same pattern.

The hard constraint, enforced by tests: rendered prose must never contain literal template placeholders like `{subject}` or `{criterion}`. An exhaustive sweep over every renderer × direction × subject × evidence + a full plan-tree walk on a real `plan_crawl` output guarantees this — a missing slot fails CI, not the user's screen.

The legacy per-stop function is preserved as `explain_stop_legacy` so a side-by-side comparison is always available.

### 8.5.3 Phase 3 — Adaptive CBR (closing Slade's R-loop)

The original CBR was retrieve-only: top-3 cases were displayed as a narrative anchor but never influenced routing. Phase 3 implements the *Revise* step — `adapt_case(case, group, bars, rules) → AdaptedCase` performs three layered adaptations:

1. **Length adaptation** — pad or trim the case's `solution_sequence` to match `group.max_stops`. Trimming drops the lowest-priority stage (smallest `vibe_profile` magnitude); padding repeats the final stage's profile with `role="extended"`.
2. **Vibe adaptation** — if any user has a must-have vibe (weight ≥ 0.8) absent from every stage of the case, inject it into the richest stage's `vibe_profile`.
3. **Constraint adaptation** — if the case targets a neighborhood the group excluded, retarget to the nearest allowed neighborhood (centroid distance over the bar dataset).

Every change is logged as an `Adaptation(field_changed, from_value, to_value, reason)` record. Stages with no feasible bars in the current dataset (under the group's neighborhood constraint) are flagged as `unadapted_stages` — the router treats them as soft priors rather than required waypoints, so the system fails gracefully instead of crashing on edge cases.

The adapted case is then passed to `best_route` as a `seed_sequence`. At each stop position, candidates receive an additive **CBR seed bonus** = `cbr_seed_bonus * (matched stage-vibe weight / total stage-vibe weight)`. The bonus magnitude is YAML-configurable (`routing_config.cbr_seed_bonus: 0.15`). Critically, this is a **prior, not a constraint** — a strongly-scoring off-archetype bar can still win the slot. Tests verify the prior nature by demonstrating that some chosen bar in some real plan has tags outside the seed's vibe-union.

The CBR Argument (rendered into the plan's explanation tree) reads, for example: *"This plan resembles our **East Village Dive Tour** archetype, adapted for your group. We adapted the archetype: group's max_stops=3 is below archetype's 4; dropped stage 'middle_2' (lowest-priority). The decisive factor: strongest archetype match on vibe (score 0.78, overall similarity 0.70)."*

When similarity falls below a configurable weak-match threshold, an opposing premise fires: *"the nearest archetype is a weaker-than-usual match — take the framing loosely."*

### 8.5.4 Phase 4 — Dialogic replan with bounded preference learning

A first-class second public entry point: `replan_with_reactions(previous_plan, reactions, original_group, bars, cases, rules)`.

A `Reaction(user_id, stop_index, verdict, lock, swap_target_bar_id)` is one user's reaction to one stop. `verdict` is `accept | reject | swap`; `lock=True` pins the stop in place across the replan.

**Preference update rule** (intentionally simple, defensibly explainable, never Bayesian):

- On a `reject`: for each criterion where the rejected stop's bar scored in the user's bottom quartile across the plan, multiply that user's weight on that criterion by 1.3, capped at 2× the original.
- On an `accept` of an over-budget stop: widen the user's `max_per_drink` cap by half the overshoot.
- On a `swap`: treat as a reject on the current stop + an implicit accept on the swap target if specified.

Every change emits a `PreferenceUpdate(user_id, field, from_value, to_value, reason, triggered_by_reaction)` record with English reason: *"Alex rejected stop 2, which scored in their bottom quartile on 'noise'; bumped the weight by 30% (capped at 2× original)"*.

**Locked-stop routing** — a new `_greedy_fill_with_locks` path in `routing.best_route` honors `locked_bars: dict[idx, Bar]`, fixing those positions and greedy-filling the rest. The router does not 2-opt across locks (the user pinned them). Feasibility (in-window arrival, bar-open) is still enforced; an infeasible lock returns an empty route with the reason logged.

**Delta attribution** — `build_delta_argument(before, after, reactions, pref_updates)` produces a `DeltaArgument` where every changed stop is attributed to either:
- a specific `Reaction` (if the reaction targeted that stop index), or
- a named `PreferenceUpdate` ripple ("ripple: updated Alex's noise"), or
- flagged in `unattributed` if neither applies.

Tests assert that `unattributed` is empty for normal flows. An unattributable change is treated as a **bug**, not a side-effect to hide.

**Revert semantics** — `revert_user_updates(original_users, updated_users, pref_updates, user_id)` undoes one user's preference changes without losing the others'. The pref-update narrative ends with *"Say 'revert Alex' or 'revert Sarah' to undo those updates and replan"*.

The Streamlit UI surfaces this end-to-end: a per-stop verdict selector (accept / reject / swap) + a lock checkbox, a "Replan" button, and post-replan rendering of the pref-update paragraph + the delta narrative + the new plan.

### 8.5.5 Phase 4.1 — Style-aware budget realism

A late-cycle UX fix prompted by an observed failure: *"Pregame → clubs"* night style was producing karaoke bars instead of nightclubs. Root cause was two-fold and not in scoring: (a) the default neighborhood filter (East Village + LES) hard-excluded every dance-floor bar in the dataset (Bushwick / Williamsburg), and (b) the hardcoded 2.0× budget gross-mismatch multiplier excluded real clubs that legitimately run $20–32/drink.

Fix:

- Moved the budget multiplier to `data/rules.yaml` (`dealbreaker_rules.budget_gross_mismatch.multiplier`) — no hardcoded magic number in `src/` per the standing rule.
- Added `GroupInput.budget_multiplier: Optional[float]` per-plan override.
- Each `NIGHT_STYLES` entry in the Streamlit app now declares its own `budget` (slider default) and `budget_multiplier` (relaxation): `Pregame → clubs` and `Late-night only` and `Rooftop summer` and `Birthday party` get 2.5×; conversation styles stay at 2.0×.
- The exclusion explainer cites the actual multiplier that fired (e.g. *"$32 is more than 2.5× Alex's $10 cap"*), not a hardcoded "2×".
- Mr. Purple's vibe tagging in `bar_overrides.yaml` corrected — added `dj-set / late-close / music-loud / instagrammable`, dropped `conversation` (which was in an `opposing_pair` with `dance-floor` and silently penalized the bar on every club-heavy stage).

After the fix, `Pregame → clubs` with neighborhoods expanded to include Bushwick produces *Planet Rose → House of Yes → Sing Sing Ave A.* — House of Yes is an actual Bushwick dance club. The peak slot now pulls real nightlife.

### 8.5.6 Cumulative impact

| Metric | Before phases | After phase 4.1 |
|---|---|---|
| Tests | 75 | 151 |
| Public API entry points | 1 (`plan_crawl`) | 3 (`plan_crawl`, `deeper_analysis`, `replan_with_reactions`) |
| `src/` modules | 11 | 13 (+ `argument`, `dialogic`) |
| CBR loop | Retrieve-only (R1) | Full R1+R2+R3 (Retrieve, Reuse, Revise) |
| Strategy output | tuple (name, rule, rationale) | `StrategyDecision` dataclass with rank + considered_alternatives + deeper-analysis trigger |
| Per-stop explanation | per-criterion template branching | `Argument` → `render_argument` (data-driven shape) |
| Preference learning | none | bounded multiplicative + revertable |
| Eval harness scenarios | 0 | 45 + 15 deep probes |
| Latency p50 / p95 | not benchmarked | 28 / 60 ms |

## 9. Future work

Natural extensions, in priority order. (Items the proposal listed as future work that Phases 1–4 actually shipped have been removed; the surviving list is everything still genuinely open.)

1. **Live data integration** — Yelp / Google Places for real hours, current specials, and crowd telemetry. The decision system doesn't change; only the dataset-build stage does. This is the single highest-leverage upgrade because it turns "plausible category defaults" into facts.
2. **Multi-night arcs** — brunch → cocktails → dinner → nightcap across a weekend. The temporal layer already supports past-midnight hours; what's missing is a multi-day `GroupInput` and an arc model that reasons about Friday-night hangover affecting Saturday-noon brunch choices.
3. **Capacity forecasting** — a time-of-day + day-of-week model of crowding. Our `crowd_level_by_hour` is a category template; a real model would be learned from Google Popular Times or BestTime.
4. **Richer stakeholder modeling** — e.g., one user is the designated driver (no alcohol), one wants food, one has a flight in the morning. The aggregation can extend to accommodate role-based constraints; the `UserPreference` shape would need a `roles` field.
5. **Larger dataset** — expand to 500+ bars via the author's friends' lists (social-graph provenance preserved). The bottleneck is curation effort, not architecture.
6. **Replan-loop UX iteration** — the current Streamlit reaction UI is functional but minimal (radio + buttons). A drag-to-reorder, tap-to-swap mobile-first interaction would make the dialogic loop feel native rather than form-driven.
7. **Locked-stop 2-opt** — Phase 4's lock path skips global swap; a constrained 2-opt that swaps only unlocked positions could reclaim some objective without overruling the user's pins.
8. **Bayesian preference posterior** — the multiplicative reject-bump rule is intentionally simple to keep updates narratable. A Bayesian alternative would be more sample-efficient but harder to explain. The right experiment is to run both side-by-side and measure user trust + correctness on identical scenarios.

## 9.5 Screenshots

All artifacts in `docs/screenshots/` are programmatically regenerated from the current code (no hand-edited images).

**Streamlit UI** (captured via headless Playwright against `streamlit run app/streamlit_app.py`):

- `ui_initial.png` — splash + sidebar before any plan; the night-style arc visible on the left
- `ui_plan.png` — full plan render: map + Gantt timeline + Phase 1 strategy explanation ("We used **Respect the intensity of preference**…") + Phase 2 Argument-shaped per-stop prose ("The decisive factor: …")
- `ui_replan.png` — after a `reject` reaction on stop 1: the **Updated plan** section shows the auto-narrated preference-update paragraph ("Friend 1's noise weight rose from 0.10 to 0.13. Reason: rejected stop 1, which scored in their bottom quartile on 'noise'…") followed by the new plan with the locked stop preserved

**Visualizations** (`src/visualize.py` outputs):

- `route_map.html` — Folium interactive map of the aligned-trio plan
- `timeline.png` — matplotlib Gantt of the same plan
- `score_breakdown.png` — per-criterion stacked bar chart, averaged across users
- `pregame_clubs_route.html` + `pregame_clubs_timeline.png` — Phase 4.1 fix demo: `Pregame → clubs` with neighborhoods expanded to Bushwick now produces *Planet Rose → House of Yes → Sing Sing Ave A.*; House of Yes is an actual Bushwick dance club, demonstrating that the budget-multiplier + neighborhood-default fixes pull real nightlife into the dataset's reach.

**Inspection artifacts** (markdown showing internals other UIs would hide):

- `argument_internals.md` — raw structured `Argument` for one stop: every `Premise` listed with subject / criterion / direction / magnitude / evidence, the decisive premise marked, the rendered prose at the bottom — proves the prose is *composed*, not hand-written
- `replan_demo.md` — full transcript of a `replan_with_reactions` invocation: original plan, reactions, auto-narrated preference updates, delta narrative with per-stop attribution, and the new plan with locked stops marked

## 10. How to run

```bash
# First-time setup
pip install -r requirements.txt

# Regenerate the dataset (deterministic; reads seed_bars.json → bars.json)
python scripts/enrich_bars.py
python scripts/neighborhood_audit.py    # produces docs/dataset_report.md

# Tests
make test

# The canonical demo — the primary deliverable
jupyter notebook notebooks/demo.ipynb

# Optional polished UI
streamlit run app/streamlit_app.py
```

Yale Zoo compatibility: core decision-system modules use only `numpy`, `pandas`, `pyyaml`, `jsonschema`, `matplotlib`, `folium`, `jupyter` — nothing system-level. The optional Streamlit UI adds `streamlit` (also pure-Python). Screenshot regeneration uses Playwright + Chromium, but those are author-side tooling — the code under review needs neither.

To regenerate the screenshots after code changes:
```bash
python -m pip install playwright && python -m playwright install chromium
streamlit run app/streamlit_app.py --server.headless=true --server.port=8501 &
python scripts/regenerate_screenshots.py    # see scripts/ for the Playwright driver
```

## 11. Division of labor

Solo project.

## Appendix A — Full exclusion trace example

For the three-user Friday-night group (§5), 111 of 143 bars were excluded before scoring. Cause distribution:

- 89 outside the requested neighborhoods (East Village + LES)
- 14 not open at any feasible arrival time
- 7 over Bob's $12 cap by more than 2×
- 1 vetoed (if any)

Each is logged on `PlanResult.excluded_bars` with `rule_id` and a natural-language reason — the writeup and the notebook both display representative samples.

## Appendix B — All rules in one file

`data/rules.yaml` is the authoritative source. Its sections:

1. `qualitative_thresholds` — price, noise, distance, crowd, quality_signal buckets
2. `scoring_defaults` — MCDA criterion weights, budget penalty shape, vibe cosine config
3. `dealbreaker_rules` — 8 hard-exclusion rules with explanation templates
4. `temporal_bonuses` — happy-hour, specials, kitchen-open bonuses + kind multipliers
5. `novelty_and_variety` — per-repeat-type penalties, hidden-gem novelty bonus
6. `walking_and_distance` — per-mile penalty, comfortable cap, amplification
7. `group_strategy_rules` — profile metrics + selection rules + strategy definitions
8. `routing_config` — 2-opt iteration limits, exact-enumeration threshold, stop durations

Changing behavior — tightening budget tolerance, loosening walking cap, swapping a strategy's default — requires editing this file only, not the code. This is the architectural payoff of the symbolic design.
