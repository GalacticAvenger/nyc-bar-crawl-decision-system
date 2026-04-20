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

- **Unit tests**: 75 passing (`tests/`). Coverage spans qualitative thresholds, score formulas, Pareto filter, temporal window logic, Haversine accuracy (Times Square ↔ Union Square within 5%), all five aggregation strategies, meta-selector rule firing, CBR retrieval + adaptation, runner-up + counterfactual generation, and explanation template specificity (every stop explanation must name ≥1 user, ≥1 bar attribute, ≥1 weight/criterion).
- **Integration tests**: 7 full-pipeline scenarios (aligned friends, wide-budget gap, many-vetoes, infeasible window, empty group, determinism, time-window respect).
- **Notebook**: 45 cells, all execute top-to-bottom in a fresh kernel without errors (`jupyter nbconvert --execute demo.ipynb`).
- **Performance**: end-to-end `plan_crawl` on 143 bars × 3 users × 3 stops with structural counterfactuals: well under 1 second on a 2023 MacBook.

## 8. Limitations

The honest version:

- **Hours, happy hours, and specials are category defaults, not live data.** A real deployment would hit Google Places or a dedicated feed.
- **Vibes are heuristic.** We infer from Google category + user notes; two bars with the same Google category get the same default vibes even when they differ in character.
- **Router is exact only for ≤ 7 stops.** Realistic group crawls are 3–5, so this is fine; but a large party crawl of 10+ stops would fall back to 2-opt.
- **No preference learning.** Weights are user-supplied; we don't update from outcomes.
- **Single-night crawls only.** A weekend arc (Fri night → Sat brunch → Sat night) is out of scope.
- **English-only, US drink categories.** Internationalizing would require vocabulary extensions.
- **Dataset is geographically concentrated** in downtown Manhattan, Hell's Kitchen, Williamsburg, Bushwick, Astoria — matching where the author actually goes. A UES-only crawl has fewer plausible plans.

## 9. Future work

Natural extensions, in priority order:

1. **Live data integration** — Yelp/Google Places for real hours, current specials, and crowd telemetry. The decision system doesn't change; only the dataset-build stage does.
2. **Preference learning** — after a crawl, the user rates each stop, and the system updates their weights by gradient descent on a regret function. Note: the *decisions* remain symbolic; only the weights are learned.
3. **Multi-night arcs** — brunch → cocktails → dinner → nightcap across a weekend.
4. **Capacity forecasting** — a time-of-day + day-of-week model of crowding. Our `crowd_level_by_hour` is a category template; a real model would be learned.
5. **Richer stakeholder modeling** — e.g., one user is the designated driver (no alcohol), one wants food, one has a flight in the morning. The aggregation can extend to accommodate role-based constraints.
6. **Larger dataset** — expand to 500+ bars via the author's friends' lists (social-graph provenance preserved).

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

Yale Zoo compatibility: core modules use only `numpy`, `pandas`, `pyyaml`, `jsonschema`, `matplotlib`, `folium`, `jupyter`. Nothing system-level.

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
