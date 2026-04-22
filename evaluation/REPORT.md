# Bar Crawl Decision System — Evaluation Report

**Author:** automated test sweep
**System under test:** the repository at HEAD (commit `65de8a7` at evaluation time), 143-bar dataset, 20-case library, full pipeline (`src/decision_system.plan_crawl`).
**Test artifacts:** [evaluation/eval_harness.py](eval_harness.py) (45 scenarios), [evaluation/eval_deep.py](eval_deep.py) (15 deep probes), [results.json](results.json), [deep_results.json](deep_results.json).

---

## TL;DR — verdict

The pipeline is **functionally solid**: 75/76 unit tests pass; all 45 evaluation scenarios produce valid (or correctly empty) routes; structural invariants — open-at-arrival, monotonic times, vetoes respected, neighborhood respected, max_stops cap, walking-distance arithmetic — hold across **every** route.

But evaluation surfaced **9 issues worth fixing** before this is shippable, ranked below by severity. Three are *correctness/honesty* bugs (the system silently does the wrong thing); the rest are calibration, coverage, or polish issues.

| # | Issue | Severity | Where |
|---|---|---|---|
| 1 | Step-free accessibility filter is silently inert (data has only `None`, not `False`) | **HIGH** — safety/honesty | [decision_system.py:85](../src/decision_system.py#L85), data |
| 2 | Stop explanation hardcodes "scored this highest of the three" regardless of group size | **HIGH** — correctness | [explanation_engine.py:252](../src/explanation_engine.py#L252) |
| 3 | "Fits the budget" narrative is asserted at the *aggregate* level even when a low-cap user is over budget on every stop | **HIGH** — honesty | [explanation_engine.py:194](../src/explanation_engine.py#L194), [explanation_engine.py:67](../src/explanation_engine.py#L67) |
| 4 | CBR retrieval is non-discriminative (Date group → "Whiskey Appreciation" beats "Cocktail Flight Date"; Dive group → "Nightcap Duo" beats "East Village Dive Tour") | MEDIUM — quality | [case_based.py:24-69](../src/case_based.py#L24-L69) |
| 5 | Runner-up gap units differ across strategies (utilitarian: ±0.03, Borda: 12, Copeland: 3) — `gap < 1.5` threshold in `explain_stop` only fires for utilitarian | MEDIUM — quality | [option_generation.py:48](../src/option_generation.py#L48), [explanation_engine.py:242](../src/explanation_engine.py#L242) |
| 6 | Counterfactual utility deltas reported in raw strategy units (e.g. "+36.00" under Borda); reads alarmingly | MEDIUM — explanation quality | [explanation_engine.py:355](../src/explanation_engine.py#L355) |
| 7 | Dataset coverage: only 26/143 bars (18%) ever chosen across 100 random scenarios; the default start location funnels into ~31 walkable bars | MEDIUM — UX/data | [models.py:109](../src/models.py#L109) (default `start_location`) |
| 8 | `age_policy_mismatch` rule is defined in `rules.yaml` but never enforced in `_apply_dealbreakers`; all bars are 21+ so the inverse case (age<21 user) is silently allowed | MEDIUM — correctness | [decision_system.py:48-102](../src/decision_system.py#L48-L102) |
| 9 | Egalitarian threshold (3.0× spread) is too lenient — at 2.2× ratio one user is left out of *every* stop while strategy stays utilitarian | LOW — calibration | [rules.yaml:236](../data/rules.yaml#L236) |

Plus minor: 3 vibe-vocab tags (`industry`, `no-cover`, `sidewalk-seating`) never appear in any bar; exact-enumeration latency at `max_stops=6` reaches 162 ms (factorial); narrative for empty-route case omits the strategy explanation (no users → no profile).

Full detail and evidence below.

---

## 1. Test inventory

| Suite | Count | Pass | Notes |
|---|---|---|---|
| Existing pytest | 76 | 75 ✅ | 1 skipped (`test_scenario_no_users_gracefully` — a known edge case) |
| Eval harness scenarios | 45 | 45 ✅ | 0 errors; 0 invariant failures; 3 expected empty routes |
| Deep probes | 15 | — | enumerated below |

Scenarios in the harness:
- **Aligned**: `aligned_two_friends`, `solo`, `all_cheap`, `all_splurge`
- **Disagreement-driven** (forces each meta-rule): `budget_gap_three` (egalitarian), `many_vetoes_three` (approval/veto), `vibe_split_three` (Copeland), `intense_user_three` (Borda)
- **Group sizes**: 1, 2, 3, 6, 8
- **Edge constraints**: `infeasible_window` (30 min), `morning_window` (8–10am), `nonexistent_neighborhood`, `step_free_required`, `empty_user_prefs`, `max_one_stop`, `six_stops_long_window`
- **Full UI surface (arc-staged)**: 9 night-style scenarios (`Chill bar hop`, `Pregame -> clubs`, `Dive bar tour`, `Date night`, `Birthday party`, `Post-dinner drinks`, `Late-night only`, `Rooftop summer`, `Games night`)
- **Random** (parameterized seed for distribution view): 20 sampled groups

Strategy firing distribution across the 45 scenarios: utilitarian 17, Borda 14, Copeland 8, egalitarian 3, approval/veto 1, no-strategy (empty-route case) 2. Every meta-selector branch is exercised at least once.

---

## 2. Correctness — what works

### Determinism ✅
5 reruns of the same input produce **identical** route signatures, total utility, and walking miles (Probe 1). Good.

### User-order invariance ✅
Shuffling the `users` list 10 times produced **0** different routes for the same group (Probe 2). The aggregation strategies are symmetric in users, as they should be.

### Structural invariants ✅
For every stop in every produced route:
- bar is open at arrival
- arrival within `[start_time, end_time)`, departure ≤ `end_time`
- arrivals strictly monotonic (departure of stop _i_ < arrival of stop _i+1_)
- no bar repeated within a route
- no vetoed bar appears
- if `neighborhoods` is set, every chosen bar is inside it
- `len(stops) ≤ max_stops`
- `total_walking_miles` matches direct re-computation to within 0.01
- every chosen bar's `avg_drink_price ≤ 2 × poorest user's cap` (the dealbreaker bound)

**No invariant violation across 45 + 100 random scenarios.**

### Strategy threshold transitions ✅
The meta-selector switches cleanly at the documented thresholds (Probe 4):

```
Budget-spread sweep (group of 3):
  poor_cap=$10  ratio=2.0× -> utilitarian
  poor_cap=$ 7  ratio=2.9× -> utilitarian
  poor_cap=$ 6  ratio=3.3× -> egalitarian      ← clean transition
  poor_cap=$ 4  ratio=5.0× -> egalitarian

Veto-density sweep:
  vetoes=15  density=10.5%  -> utilitarian
  vetoes=25  density=17.5%  -> utilitarian
  vetoes=35  density=24.5%  -> approval_veto   ← clean transition at 20%
```

### Past-midnight open hours ✅
All 143 bars have at least one day with `close_h > 24:00`. Sample probe: Slainte (Mon 12:00–26:00) → `is_open` returns True at 1 AM Tuesday morning. Logic is correct (Probe 6).

### Equity ✅ (caveat: with limitations — see §3.3)
Across 50 random groups, the served-ness Gini coefficient over per-user mean scores has **mean 0.035, max 0.078**. Worst-served-user mean across groups: median 0.474. The aggregation strategies are doing their job *on average* — no user is systematically starved as long as the meta-selector picks the right strategy. (Bug §3.3 below: when the meta-selector picks **wrong**, equity collapses for the affected user.)

### Performance — bounded for typical use ✅
| Workload | Median latency |
|---|---|
| 1-user, 4-stop, no CFs | 4.4 ms |
| 8-user, 4-stop, no CFs | 10.7 ms |
| 2-user, 1-stop, **with** CFs | 13.1 ms |
| 2-user, 4-stop, **with** CFs | 20.7 ms |
| 2-user, 6-stop, **with** CFs | **162 ms** ← non-trivial |

Counterfactuals dominate cost (each CF re-runs the pipeline). Exact enumeration over the greedy-chosen set scales factorially: enumerating 7 bars × all subsets/permutations took 212 ms in isolation (Probe 9 follow-up). The router's fallback that "exact ordering beats 2-opt" almost never fires in practice — 2-opt already finds the same answer — but the cost is paid every time. With `max_stops > 7` this would explode (~13s at n=10).

### Explanation length conformance ✅
Across 30 random groups (Probe 10):
- Summary words: **median 66**, p95 75 — well under the 200-word cap.
- Stop words: median 41, max 54 — well under the 80-word cap.
- 0 routes had duplicate "stop opening" sentences (varied lead verbs work).

---

## 3. Issues — ranked by severity

### 3.1 [HIGH, correctness/honesty] Step-free accessibility filter is silently inert

**Probe 5 evidence:**

```
step_free in dataset: True=0  False=0  None=143
With step_free=True user: survivors=122, a11y-excluded=0
```

The dealbreaker check at [decision_system.py:85](../src/decision_system.py#L85):
```python
if group.accessibility_needs.step_free and b.accessibility.get("step_free") is False:
    excluded.append(...)
```
…uses `is False`, which is correct **if** the data uses three-valued accessibility (True/False/Unknown). But every bar in `data/bars.json` has `step_free: None`. So the filter never trips: a wheelchair user requesting step_free gets 122 survivors with no warning — most of which are inaccessible. The system silently ignores their constraint.

**Impact:** safety / liability if a real user trusts this. Same applies to `accessible_restroom` — the rule is in `rules.yaml` but no code checks it at all.

**Fix options:** (a) treat `None` as `False` when accessibility is requested ("data unknown, can't guarantee — excluding"); (b) populate the data; (c) add an explicit warning on the result when the filter fired but the dataset is sparse.

### 3.2 [HIGH, correctness] Stop narrative says "of the three" regardless of group size

[explanation_engine.py:252](../src/explanation_engine.py#L252):
```python
if dom_user and len(per_user_scores) > 1 and idx % 2 == 0:
    sentences.append(f"{dom_user} scored this highest of the three.")
```

Reproduced with a 2-person group:
> "We open at **Vida Verde - Tequila Bar** (8:04pm) — it fits the budget, and it's a mid-priced, conversational room in East Village. […] **Hi scored this highest of the three.**"

There are two users. Should be `"of the {len(per_user_scores)}"` or just `"scored this highest"`. Wrong on every multi-user route.

### 3.3 [HIGH, honesty] "Fits the budget" is aggregate-level, but a low-cap user can be over budget on every stop

Reproduced:
- Group: Hi ($22 cap), Lo ($10 cap). Both like craft cocktails.
- Budget-spread ratio 2.2× → under the egalitarian threshold (3.0×) → utilitarian fires.
- Plan picks two $14.50 bars.
- Per-user report:
  ```
  Hi: in_budget_stops 2/2
  Lo: in_budget_stops 0/2   ← every stop is over Lo's cap
  ```
- Stop explanation says "it fits the budget" without qualifying *whose*.

This is the **honesty rule** the system claims to honor (BUILD_PLAN §10: "be honest about trade-offs"). Saying "fits the budget" when one user is over on every stop is the opposite of honest. The per-user report does surface this, but the headline narrative does not.

**Compound issue:** the egalitarian threshold (`budget_spread_ratio > 3.0`) is too lenient for this scenario; a 2.2× spread already produces a one-sided plan. Either tighten the threshold (suggest `> 2.0`), or have the explanation engine read the per-user report and add a disclaimer ("Lo is over budget at every stop — the planner picked utilitarian because the spread was 2.2×, just under the egalitarian threshold of 3.0×").

### 3.4 [MEDIUM, quality] CBR retrieval is non-discriminative

Probe 13 results:

| Group | Top retrieved case | Should-have-won | Why it loses |
|---|---|---|---|
| Date night (vibes: date+intimate+craft-cocktails) | `case_whiskey_appreciation` (0.613) | `case_cocktail_flight_date` (0.600) | 0.013 gap — essentially noise |
| Birthday party (7 ppl, dance vibes) | 3-way tie at 0.412 (`bushwick_dance`, `large_group_birthday`, `late_night_after_hours`) | `case_large_group_birthday` clearly | tied — no discrimination |
| Dive group ($8 cap, divey+games+unpretentious) | `case_nightcap_duo` (0.575) | `case_east_village_dive_tour` (0.560) | dive case loses by 0.015 |

Root cause for dive case (traced via similarity breakdown):

```
case_east_village_dive_tour: size=1.0 budget=0.3  neighborhood=0.5 vibe=0.5  → 0.560
case_nightcap_duo:           size=1.0 budget=1.0  neighborhood=0.7 vibe=0.0  → 0.575
```

- The dive case's `budget_tier: cheap` matches the group's avg cap of $8 → but `_budget_tier_of(8)` returns `"moderate"` (the boundary `< 8` is exclusive at 8), so the case is penalized to 0.3.
- The nightcap case has `budget_tier: moderate_to_premium` and gets 1.0 because `"moderate" in "moderate_to_premium"` is a Python substring match — accidentally permissive.
- Vibe match uses crude word-substring matching against a "vibe_summary" string: dive's summary "divey + unpretentious" gets 0.5, nightcap's "cozy + brief" gets 0.0 — but the budget penalty dominates.

**This is a genuine quality bug**: the system *says* it retrieves "the most-similar archetype" for the explanation, and it confidently reports the wrong one. The narrative will literally say "This plan resembles our **After-Dinner Nightcap** archetype" for a dive crawl.

**Fix:** (a) use range-overlap budget matching, not substring; (b) make the boundary inclusive (`<= 8`); (c) compute vibe match against `vibe_profile` weights in `solution_sequence` rather than the prose summary.

### 3.5 [MEDIUM, quality] Runner-up gap is in raw strategy units; the explainer's `gap < 1.5` threshold is calibrated only for utilitarian

Distribution across 40 random scenarios (Probe 12 follow-up):

```
utilitarian_sum: n=38  mean=-0.011  median=-0.024  max=0.333
copeland:        n=60  mean= 2.800  median= 2.000  max=13.000
borda:           n=14  mean=12.143  median= 9.500  max=40.000
```

Two issues:
1. **Negative gaps under utilitarian**: the runner-up has a *higher* raw group score than the chosen stop in many cases. Reason: the chosen stop won the routing objective (which adds temporal bonus minus walking penalty) but lost the raw score. Fine, but the narrative line "Close second: X (gap: 0.05)" pretends the chosen stop won by margin.
2. **Threshold doesn't generalize**: `explain_stop` only mentions runner-ups when `gap < 1.5`. Under Copeland and Borda this is *almost always satisfied for utilitarian and never for the others* — the runner-up sentence appears or vanishes based on which strategy fired, not on whether the alternative is genuinely close.

**Fix:** normalize gap (e.g., `gap / max_score`) before thresholding, or pick a strategy-specific threshold from `rules.yaml`.

### 3.6 [MEDIUM, explanation quality] Counterfactual utility deltas reported in raw strategy units

Reproduced:
> "If each user had $10 more per drink, the crawl would have been identical — same stops but **total utility would shift by +36.00**."

Under Copeland this means "the same bar would win 36 more pairwise contests". Under utilitarian it would mean "+36 score points". The narrative reads identically. To a user, "+36" sounds like a huge effect; in practice for Copeland it's just integer counts inflating because adding budget headroom changes how many *other* bars become permissible.

**Fix:** report deltas as a percent of base utility, or omit the number when units are not interpretable across strategies.

### 3.7 [MEDIUM, UX/data] Dataset coverage: 18% of bars chosen across 100 random scenarios

Across 100 random groups (Probe 14):
- Unique bars chosen: **26 / 143 (18.2%)**
- 117 bars **never** chosen
- Top picks: Planet Rose (41×), Lost in Paradise Rooftop (31×), The Uncommons (29×), The Back Room (27×), Arlene's Grocery (21×)

Root cause: `GroupInput.start_location` defaults to `(40.7265, -73.9815)` — East Village. With `walking_only=True`, only 31 bars are within the 0.6-mile "comfortable max" radius; only 46 within 1.0 mile. The other 97 bars (Brooklyn, Astoria, UES, Midtown East, Hell's Kitchen, etc.) are all > 1 mile from the default and therefore taking a heavy walking penalty.

This isn't a bug per se — the system honors the constraint. But the implication is:
- The system feels "magical" if you're an East Villager and "broken" if you're not.
- A new user has no idea they need to override `start_location` to access most of the dataset.

**Fix:** (a) prompt for start location in the UI; (b) auto-derive a centroid from the chosen neighborhoods if any; (c) when `walking_only=True` *and* fewer than N candidates are reachable, suggest expanding to transit.

### 3.8 [MEDIUM, correctness] `age_policy_mismatch` rule is documented but never enforced

[rules.yaml:126-131](../data/rules.yaml#L126-L131) defines `age_policy_mismatch` as a hard dealbreaker. The `_apply_dealbreakers` function in [decision_system.py:48-102](../src/decision_system.py#L48-L102) checks vetoes, neighborhoods, budget, accessibility (partially — see §3.1), and open-hours, but not age. All 143 bars have `age_policy: "21+"` and `UserPreference.age` defaults to 30, so the gap hasn't bitten yet. Add a 19-year-old user and the system silently puts them in 21+ bars.

### 3.9 [LOW, calibration] Egalitarian threshold too lenient

See §3.3. A 2.2× budget spread is enough to leave one user over budget on every stop, but the rule only fires at `> 3.0×`. Suggest reducing to `> 2.0` and re-validating with the existing test suite.

### Minor findings

- **Vibe vocab leakage**: 3 vocab tags (`industry`, `no-cover`, `sidewalk-seating`) are declared in `data/vibe_vocab.json` but never appear in any bar. A user can express preference for them with no possible match. Either remove from vocab or add to bars.
- **Empty-route narrative omits strategy**: when no bars survive (`morning_window`, `nonexistent_neighborhood`), `Route.strategy_used = ""` and the explanation is a single sentence. Could surface *why* survivors=0 more proactively (top-2 exclusion reasons by count).
- **`explain_counterfactual` "would have been identical"** message is repeated for every CF that doesn't change the route — across the 60 CFs run in Probe 11, **24 produced identical routes**. That's informative ("more time wouldn't have changed anything") but the wording is monotonous.
- **Default `start_location`** is hardcoded in `models.py`. Surface it in the UI.

---

## 4. Quantitative summary

```
SCENARIOS              45  (full system, no errors, no invariant failures)
DEEP PROBES            15  (determinism, equity, perf, CBR, explanation quality, …)
RANDOM SAMPLES        100  (coverage probe)
TOTAL plan_crawl CALLS ≈ 460 across the evaluation

UNIT TESTS         75 / 76 pass  (1 skipped)
INVARIANT FAILURES      0 / 45 scenarios
ERRORS                  0 / 45 scenarios

LATENCY            p50 ≈ 26 ms   p95 ≈ 58 ms   max 162 ms (6-stop, full pipeline)

DETERMINISM          ✅ identical across 5 reruns
USER-ORDER           ✅ invariant across 10 shuffles
PERTURBATION (+$1)   route stable in 20/20 cases (utility shift bounded for utilitarian; Copeland shifts in integer rank counts — by-design)

STRATEGY ROUTING     all 5 strategies reachable  ✅
                     veto threshold transitions cleanly at 20% density  ✅
                     egalitarian threshold transitions cleanly at 3.0×  ✅ (but threshold too lenient — see §3.9)

EQUITY (Gini)        mean 0.035, max 0.078       ✅ on average
                     but degrades when meta-selector picks "wrong" strategy (§3.3)

DATASET COVERAGE     26 / 143 bars used (18%) across 100 random groups (§3.7)
```

---

## 5. Suggested fix priority

If this is going to be shipped or graded:

1. **Now:** §3.1 (accessibility), §3.2 ("of the three"), §3.3 (budget-honesty disclaimer + tightened threshold §3.9), §3.8 (age policy)
2. **Soon:** §3.4 (CBR), §3.5 + §3.6 (gap normalization across strategies)
3. **Polish:** §3.7 (UI start-location), vibe-vocab cleanup, varied counterfactual phrasing

Each of §3.1, §3.2, §3.4, §3.5, §3.6 would be a one-pager change; §3.3 has a minor design call (where to put the disclaimer); §3.7 is a UX redesign of the Streamlit start-location selector.

---

## 6. What this evaluation did *not* cover

To be honest about the limits of this report:

- **Visual rendering**: `render_map` and `render_timeline` are exercised only indirectly. No screenshot comparison.
- **Real Streamlit UI flows**: I did not run the Streamlit server in a browser. I did exercise every NIGHT_STYLE arc through the same code paths the UI uses, including arc-staged scoring.
- **Notebook**: `notebooks/demo.ipynb` was not executed cell-by-cell.
- **Real user studies**: All "honesty" judgments are by inspection of generated text, not reader feedback.
- **Adversarial inputs**: I did not fuzz with malformed YAML, missing JSON keys, or NaN coordinates. The data loader is trusted.
- **Concurrency**: `plan_crawl` is single-threaded; no concurrent-call testing.
- **Real-world calibration**: None of the bar data (happy hours, noise, crowd estimates) was validated against ground truth — they are author-generated inferences per the README's honesty note.
