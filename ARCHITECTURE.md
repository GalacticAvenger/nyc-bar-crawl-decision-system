# Architecture Map

Produced as pre-refactor reference. Describes every module, its public surface, and the data flow from `plan_crawl` to `PlanResult`. Also flags where the current code diverges from `README.md`. **No code was changed to produce this document.**

---

## 1. Module map

All core code lives in [src/](src/). Each module has a matching test file in [tests/](tests/) unless noted.

### [src/models.py](src/models.py) — domain types

Pure dataclasses; no logic beyond `Bar.__repr__`, `UserPreference.intensity`, and `Route.is_empty`. Frozen where mutation would be a bug (`Bar`, `TemporalWindow`); mutable elsewhere.

- `TemporalWindow` (frozen) — `days`, `start`, `end`, `kind`, `details`, `bonus`.
- `Bar` (frozen) — 40+ fields: identity, geo, `bar_type`, `vibe_tags`, pricing, drinks, noise, crowd-by-hour, accessibility, hours, user/editorial notes, quality signal, source.
- `UserPreference` — `vibe_weights`, `criterion_weights`, `max_per_drink`, `preferred_drinks`, `preferred_noise`, `vetoes`, `age`. `.intensity()` returns peakiness = max − mean.
- `AccessibilityNeeds` — `step_free`, `accessible_restroom`.
- `GroupInput` — users + temporal window + location + `max_stops` + `neighborhoods` + `walking_only` + `accessibility_needs` + `want_food` + optional `arc_profile: tuple[dict[str, float], ...]`.
- `Case` — CBR archetype: `group_profile`, `context`, `solution_sequence`, `success_narrative`, `fails_when`, `example_bars_in_dataset`.
- `Score` — `per_criterion`, `weighted_contributions`, `total`, `temporal_bonus`, `total_with_bonus`.
- `GroupScore` — `total`, `per_user_contribution`, `losers`, `rank_context`.
- `RouteStop` — bar + arrival/departure + `group_score` + `temporal_bonuses_captured` + `per_user_scores` + `runner_up`.
- `RunnerUp` — `bar`, `gap`, `gap_criteria`, `unlock_hint`, `relative_gap`.
- `Route` — `stops`, `total_utility`, `total_walking_miles`, `windows_captured`, `strategy_used`, `strategy_rationale`, `search_log`.
- `Explanation` — tree: `summary`, `children`, `evidence`; `.as_text(indent)`.
- `PlanResult` — the public return: `route`, `explanations`, `alternatives`, `traces`, `excluded_bars`, `per_user_report`.

### [src/data_loader.py](src/data_loader.py) — I/O

- `load_rules(path=None) -> dict` — reads [data/rules.yaml](data/rules.yaml).
- `load_vibe_vocab(path=None) -> dict` — reads [data/vibe_vocab.json](data/vibe_vocab.json).
- `load_bars(path=None, validate=True) -> list[Bar]` — reads [data/bars.json](data/bars.json); optionally validates against [schemas/bar.schema.json](schemas/bar.schema.json) via `jsonschema` (soft-fails if not installed).
- `load_case_library(path=None) -> list[Case]` — reads [data/case_library.json](data/case_library.json).
- `load_all(data_dir=None) -> dict` — returns `{"bars", "cases", "rules", "vibe_vocab"}`.

### [src/qualitative.py](src/qualitative.py) — numbers → labels

Applies `rules["qualitative_thresholds"]`.

- `price_tier(avg_drink_price, rules) -> str` — cheap / moderate / premium / splurge.
- `distance_bucket(miles, rules) -> str` — next-door / short-walk / walk / hike / transit-worthy.
- `quality_bucket(quality_signal, rules) -> str` — weak / moderate / strong / consensus_pick.
- `crowd_at(bar, hour) -> str`.
- `noise_label_phrase(noise_level, rules) -> str`.
- `qualify(bar, hour, rules) -> dict[str, str]` — full qualitative profile.
- `phrase_for(bucket, kind) -> str` — NL phrasing (e.g. `phrase_for("premium", "price")` → `"premium"`).

### [src/scoring.py](src/scoring.py) — MCDA

Ten criteria, all scalar-output in [0, 1]; weights from `user.criterion_weights` (else `rules["scoring_defaults"]["default_weights"]`), normalized to sum 1 via `normalize_weights`.

- `score_vibe(bar, user, rules)` — cosine between user weight vector and bar's binary `vibe_tags`.
- `score_budget(bar, user)` — `exp(-over_cap / cap)`.
- `score_drink_match(bar, user)` — Jaccard of `preferred_drinks` vs `drink_categories_served`.
- `score_noise(bar, user)` — ordinal distance on `NOISE_ORDER`.
- `score_distance(bar, prev_location, rules)` — per-mile penalty + amplification past `comfortable_max_miles`.
- `score_happy_hour(bar, arrival_hour, day)` — 1/0.
- `score_specials_match(bar, arrival_hour, day)` — 1/0.
- `score_crowd_fit(bar, arrival_hour, preferred_crowd="lively")` — ordinal distance.
- `score_novelty(bar)`, `score_quality_signal(bar)` — passthrough.
- `normalize_weights(raw) -> dict`.
- `score_bar_for_user(bar, user, rules, arrival_hour=None, day=None, prev_location=None) -> Score` — the public entry.
- `pareto_filter(scores) -> (kept, dominated_pairs)` — used only if a caller wants explicit domination traces.

`CRITERIA` tuple is the canonical ordering.

### [src/temporal.py](src/temporal.py) — hours + window bonuses

Handles past-midnight hours (`"26:00"` = 2am next day) + previous-day carryover.

- `day_name(dt) -> str` — mon/tue/…/sun.
- `is_open(bar, dt) -> bool`.
- `active_windows(bar, dt) -> list[TemporalWindow]`.
- `temporal_bonus(bar, arrival_dt, rules, user_budget_weight=0.0, user_wants_food=False) -> (bonus, windows)` — happy-hour scaled by budget weight, specials by `kind_multiplier`, kitchen bonus when `want_food`.
- `earliest_arrival_to_catch(window, at_bar, after) -> datetime | None`.

### [src/group_aggregation.py](src/group_aggregation.py) — VOTE-style aggregation + meta-selector

Five strategies; signatures are uniform except `approval_veto` takes `users` for its veto map. `aggregate()` is the dispatch.

- `aggregate_utilitarian_sum(per_user)` — `G = Σ U`.
- `aggregate_egalitarian_min(per_user)` — Rawlsian `G = min U`; populates `losers` for users above the min + tolerance.
- `aggregate_borda_count(per_user)` — positional: each user's rank-i bar gets `n − i` points.
- `aggregate_copeland_pairwise(per_user)` — pairwise-majority wins count.
- `aggregate_approval_veto(per_user, users, approval_threshold=0.55)` — veto ⇒ `-inf`; otherwise approval count.
- `aggregate(strategy, per_user, users) -> dict[bar_id, GroupScore]` — public dispatch.
- `disagreement_profile(users, bars) -> dict` — `dealbreaker_density`, `budget_spread_ratio`, `vibe_variance`, `max_preference_intensity`, `group_size`.
- `select_strategy(profile, rules) -> (strategy_name, rule_id, rationale_string)` — priority-ordered rule firing; thresholds parsed from `rules.yaml` via `_threshold_for`.

### [src/routing.py](src/routing.py) — greedy → 2-opt → exact

- `walking_miles(a, b) -> float` — haversine + small east-west penalty (NYC avenues are long).
- `walking_minutes(miles) -> float` — 3 mph.
- `stage_for(stop_idx, total_stops, num_stages) -> int` — maps a stop position to its arc stage.
- `greedy_route(candidates, group_scores_by_stage, group, rules, user_budget_weight) -> (steps, log)`.
- `two_opt_improve(steps, group_scores_by_stage, group, rules, user_budget_weight) -> (steps, log)` — feasibility-preserving reversal.
- `enumerate_exact(candidates, group_scores_by_stage, group, rules, user_budget_weight) -> (steps, total, perms_tried)` — used when candidate set ≤ `routing_config.exact_enumeration_max_stops` (default 7).
- `best_route(...)` — orchestrator, returns `Route`.
- Internal helpers: `_walking_penalty`, `_arrival_after`, `_is_feasible`, `_recompute_schedule`, `_route_from_steps`, `_scores_for_stage`, `Step` dataclass.

`group_scores_by_stage` accepts either a flat `dict[bar_id, float]` (no arc) or a `list[dict]` (one per arc stage).

### [src/case_based.py](src/case_based.py) — CBR retrieve + adapt (retrieve side only today)

Currently implements the R1 (Retrieve) and a partial R3 (Reuse) of the CBR loop. **No `adapt_case` yet** — this is Phase 3's target.

- `similarity(case, group) -> (score, breakdown)` — weighted blend: vibe 0.45 (cosine over `solution_sequence.vibe_profile`), budget 0.20 (tier-range expansion with adjacent-tier partial credit via `_expand_tier_spec`), neighborhood 0.20, size 0.15.
- `retrieve(group, case_lib, top_k=3) -> list[(Case, sim, breakdown)]`.
- `adapt(case, bars, max_per_step=5) -> list[list[Bar]]` — for each step in `solution_sequence`, returns top-N concrete bars matching `bar_type` and scoring well against `vibe_profile`.
- `warm_start_from_case(case, bars, group) -> list[Bar] | None` — picks one concrete bar per step for a router warm-start. Currently unused by `decision_system.plan_crawl` — the CBR output is advisory / narrative-only.
- Helpers: `_budget_tier_of`, `_expand_tier_spec`, `_case_size_match`, `_case_budget_match`, `_case_neighborhood_match`, `_case_vibe_match`, `_index_solution_profile`, `_matches_bar_type`, `_vibe_score_for_profile`.

### [src/option_generation.py](src/option_generation.py) — runner-ups, unlocks, counterfactuals

- `find_runner_ups(route, group_scores, per_user_scores, bars) -> dict[stop_idx, RunnerUp]` — populates absolute `gap`, `gap_criteria`, and **strategy-agnostic `relative_gap`** (normalized to score range).
- `unlock_hint_for(winner, runner_up, per_user_scores) -> str` — single-criterion NL phrase.
- `unlock_analysis(route, runner_ups, per_user) -> dict` — mutates `ru.unlock_hint`.
- `Counterfactual` dataclass — `kind`, `description`, `modified_group`, `delta_summary`.
- `make_extra_time_cf`, `make_extra_budget_cf`, `make_remove_vetoer_cf`.
- `all_structural_counterfactuals(group) -> list[Counterfactual]`.
- `strategy_counterfactuals(per_user, users) -> dict[strategy, dict[bar_id, float]]` — runs all five strategies.
- `strategy_winner(strategy_scores) -> bar_id | None`.

### [src/explanation_engine.py](src/explanation_engine.py) — template-based NL

No free-form text. Every output is a template with slots filled from upstream traces.

- `explain_strategy(strategy_name, rule_fired, profile, users, rules) -> str` — one paragraph per the 5 rules.
- `explain_stop(idx, stop, route, per_user_scores, runner_up, rules, users=None) -> str` — per-stop ≤80-word target; enforces budget-honesty, avoids hardcoded group size, renders runner-up only when `relative_gap ≤ 0.10`.
- `explain_route(route, group, rules) -> str` — ≤200-word plan summary.
- `explain_exclusion(bar, reason, rule_id, extra=None) -> str` — one-line per-rule-id.
- `explain_counterfactual(cf_kind, cf_description, original_route, alt_route) -> str` — reports delta as **percent of base** (strategy-agnostic).
- `per_user_served_report(route, per_user_scores, users) -> dict[user, dict]` — mean-on-route / top5-in-route / vetoes-respected / in-budget-stops.
- `render_served_table(report) -> str` — markdown.
- Helpers: `_top_contributors`, `_format_time`, `_dominant_user_for_bar`, `_lead_verb`, `_format_delta_pct`, `_user_cap`.

Tables: `CRITERION_PHRASES` (10 entries), `STOP_LEAD_VERBS` (7 entries).

### [src/decision_system.py](src/decision_system.py) — orchestrator

Public entry `plan_crawl(group, bars=None, cases=None, rules=None, compute_counterfactuals=True) -> PlanResult`. See §2 for the full data flow.

- `_apply_dealbreakers(bars, group, rules) -> (survivors, excluded)` — 7 hard rules with rule_ids.
- `_open_in_window(bar, start, end) -> bool` — 30-min step sweep.
- `_score_all_users(bars, group, rules) -> dict[user][bar] → Score` — scores against a canonical mid-window time.
- `_score_all_users_arc(bars, group, rules) -> list[dict[user][bar] → Score]` — one per stage, with stage weights max-merged against the user's personal vibe weights.
- `_avg_budget_weight(group, rules) -> float` — average of users' normalized `budget` criterion weight; scales HH bonus.
- `_maybe_recenter_start(group, bars) -> GroupInput` — if `neighborhoods` set AND `start_location` is the default East Village anchor, re-anchor to the neighborhood centroid.
- `_is_default_start(loc) -> bool`, `_humanize_rule(rule_id) -> str`.

### [src/visualize.py](src/visualize.py) — rendering

- `render_map(route, all_bars=None, save_path=None)` — Folium map, numbered markers + dashed connecting line.
- `_stop_popup_html(stop_num, stop) -> str`.
- `render_timeline(route, save_path=None, title="Crawl timeline")` — matplotlib Gantt.
- `render_score_breakdown(route, per_user_scores, save_path=None)` — stacked bar per stop, segments = per-criterion weighted contributions averaged across users.

---

## 2. Data flow: `plan_crawl` → `PlanResult`

Entry: [src/decision_system.py:225](src/decision_system.py#L225).

```
plan_crawl(group, bars, cases, rules, compute_counterfactuals=True)
│
├─ load_all() if any of bars/cases/rules is None            ← data_loader.py
│
├─ _maybe_recenter_start(group, bars)                        ← decision_system.py:466
│    if neighborhoods set AND start_location == default EV anchor:
│        start_location ← centroid of bars in those neighborhoods
│
├─ traces = {}
│
├─ STEP 0 — profile & strategy (BEFORE filtering, so vetoes still count)
│    profile = disagreement_profile(group.users, bars)       ← group_aggregation.py:177
│        dealbreaker_density, budget_spread_ratio, vibe_variance,
│        max_preference_intensity, group_size
│    strat_name, rule_id, rationale = select_strategy(profile, rules)
│        priority-ordered rule firing; thresholds from rules.yaml
│    traces["strategy_used" / "strategy_rule" / "strategy_rationale"]
│
├─ STEP 1 — dealbreaker filter
│    survivors, excluded = _apply_dealbreakers(bars, group, rules)
│        rules fired in this order (first match wins):
│          user_veto / neighborhood_excluded / budget_gross_mismatch
│          / age_policy_mismatch / accessibility_unmet
│          / accessible_restroom_unmet / closed_at_arrival
│        each excluded[i] = {bar, rule_id, reason}
│    if not survivors:
│        return PlanResult(empty route + Counter-most-common rule summary)
│
├─ STEP 2 — per-user scoring (context-free layer)
│    per_user = _score_all_users(survivors, group, rules)
│        scored against mid-window canonical time
│        → dict[user][bar] = Score
│
├─ STEP 4 — aggregate
│    IF group.arc_profile:
│        per_user_by_stage = _score_all_users_arc(...)
│            for each stage: merged = stage_weights ⊕max user.vibe_weights
│        group_scores_by_stage = [aggregate(strat_name, stage_pu, users)
│                                 for stage_pu in per_user_by_stage]
│        approval_veto -∞ bars are promoted to the excluded list
│        per_user ← per_user_by_stage[0]  (used for reports)
│        routable = bars feasible in ANY stage (UNION)
│    ELSE:
│        group_scores = aggregate(strat_name, per_user, users)
│        group_scores_by_stage = [group_scores]     (single stage)
│        routable = {b | b.id in group_scores}
│
├─ STEP 5 — CBR retrieval (advisory)
│    case_matches = retrieve(group, cases, top_k=3)           ← case_based.py:177
│        weighted blend: vibe 0.45 / budget 0.20 / neighborhood 0.20 / size 0.15
│    traces["case_matches"] = top 3 (case_id, sim)
│
├─ STEP 6 — routing
│    avg_budget_weight = _avg_budget_weight(group, rules)
│    route = best_route(routable, group_scores_by_stage, group, rules,
│                       strategy_used=strat_name,
│                       strategy_rationale=rationale,
│                       user_budget_weight=avg_budget_weight)
│        │
│        ├─ greedy_route(...) → steps, log              ← routing.py:145
│        │     at stop i: score using stage_for(i, max_stops, num_stages)
│        │     pick argmax(util + bonus − walking_penalty) that is feasible
│        │
│        ├─ two_opt_improve(...) → steps, log           ← routing.py:253
│        │     reverse [i..j], accept if feasible AND total↑
│        │
│        └─ IF len(chosen) ≤ exact_enumeration_max_stops (7):
│               enumerate_exact(...) → sanity check     ← routing.py:304
│
├─ STEP 7 — option generation (if route non-empty)
│    union_scores = max over all stages (for runner-up comparison)
│    runner_ups = find_runner_ups(route, union_scores, per_user, routable)
│                  → relative_gap normalized to score range
│    runner_ups = unlock_analysis(route, runner_ups, per_user)
│                  → unlock_hint populated
│
│    if compute_counterfactuals and route.stops:
│        for cf in all_structural_counterfactuals(group):
│            alt = plan_crawl(cf.modified_group, ..., compute_counterfactuals=False)
│            alternatives.append(alt.route)
│            cf_texts.append(explain_counterfactual(...))
│        strategy_cf_winners = {strategy: strategy_winner(...) for strategy}
│
├─ STEP 8 — explanations
│    route_text  = explain_route(route, group, rules)
│    strat_text  = explain_strategy(strat_name, rule_id, profile, users, rules)
│    stop_texts  = [explain_stop(i, stop, route, per_user, runner_ups[i], rules, users)
│                   for i, stop in enumerate(route.stops)]
│
│    explanation = Explanation(
│        summary  = route_text,
│        children = [ strat_text,
│                     *stop_texts,
│                     Counterfactuals( [cf_texts] ),        # if any
│                     CaseMatchNarrative ],                  # if case_matches
│        evidence = { strategy, rule_fired, profile, excluded_count, case_matches }
│    )
│
├─ STEP 9 — stakeholder report
│    per_user_report = per_user_served_report(route, per_user, users)
│
└─ RETURN PlanResult(
        route, explanations, alternatives, traces, excluded_bars, per_user_report
    )
```

**Key contract:** every trace the explanation engine cites is computed upstream and read, never re-derived. `PlanResult.traces` is the audit log.

---

## 3. Data files

| File | Role | Versioned? |
|---|---|---|
| [data/bars.json](data/bars.json) | 143 bars — the main catalog | Produced by `scripts/enrich_bars.py` |
| [data/seed_bars.json](data/seed_bars.json) | Raw seed from Google Maps list (159 entries, 143 kept) | Hand-curated |
| [data/rules.yaml](data/rules.yaml) | All thresholds, 7 dealbreaker rules, 5 strategy-selection rules, bonuses, routing config | Hand-written, `_version: 1.0` |
| [data/vibe_vocab.json](data/vibe_vocab.json) | 42 vibes in 4 facets + opposing_pairs | v2.1 |
| [data/case_library.json](data/case_library.json) | 20 CBR archetypes | v2.0 |
| [data/category_to_vibes.yaml](data/category_to_vibes.yaml) | Enrichment config: category → default vibes | — |
| [data/default_hours.yaml](data/default_hours.yaml) | Enrichment config: category → default hours/HH/specials | — |
| [data/bar_overrides.yaml](data/bar_overrides.yaml) | Enrichment overrides for specific bars | — |
| [schemas/bar.schema.json](schemas/bar.schema.json) | JSON Schema for bar records | — |

---

## 4. Tests + evaluation

- [tests/](tests/) — 99 pytest tests total (76 original + 23 in `test_evaluation_fixes.py` pinning the eval-driven fixes); 1 pre-existing skip. Module-level coverage: every `src/` module has a matching `test_*.py`.
- [evaluation/eval_harness.py](evaluation/eval_harness.py) — 45 scenarios across aligned / disagreement-forcing / edge / all 9 night styles / 20 random.
- [evaluation/eval_deep.py](evaluation/eval_deep.py) — 15 deep probes (determinism, user-order invariance, structural invariants, threshold transitions, dataset coverage).
- [evaluation/REPORT.md](evaluation/REPORT.md) — original 9 issues.
- [evaluation/FIXES.md](evaluation/FIXES.md) — post-fix verdict (all 9 resolved; 45/45 invariants; p50 26 ms / p95 58 ms).

---

## 5. Divergences from README.md

The README is mostly accurate but eight items are stale or understated.

| # | README says | Reality | Where |
|---|---|---|---|
| 1 | Repo layout lists `data/nyc_neighborhoods.geojson` | **Not present** in [data/](data/). Only referenced in [README.md:40](README.md#L40) and [docs/BUILD_LOG.md](docs/BUILD_LOG.md). `scripts/enrich_bars.py` grep shows no import. The neighborhood audit script and the enrichment pipeline work off the seed data and `bar_overrides.yaml` instead. | [data/](data/) |
| 2 | Repo layout omits `data/bar_overrides.yaml` | Exists (9.6 KB) and is read by `scripts/enrich_bars.py` — the only documented enrichment override file. | [data/bar_overrides.yaml](data/bar_overrides.yaml) |
| 3 | Repo layout omits the entire `evaluation/` directory | Present with `eval_harness.py`, `eval_deep.py`, `REPORT.md`, `FIXES.md`, and `results.json` / `deep_results.json`. Materially expands the deliverable surface. | [evaluation/](evaluation/) |
| 4 | `plan_crawl(group, bars, cases, rules)` | Actual signature has a 5th kwarg: `compute_counterfactuals: bool = True`. Set to `False` internally when `plan_crawl` recurses to evaluate structural counterfactuals (otherwise infinite recursion). | [src/decision_system.py:225](src/decision_system.py#L225) |
| 5 | "no runtime API calls. Every decision is symbolic and traceable." | True for `src/`. Dataset enrichment in [scripts/enrich_bars.py](scripts/enrich_bars.py) is offline and deterministic (no API calls) — consistent with the claim but worth stating explicitly. | — |
| 6 | No mention of `arc_profile` / "night style arc" | The Streamlit app's central idea (9 curated night styles, each a stage-by-stage vibe arc) drives `GroupInput.arc_profile` and `_score_all_users_arc` — this is the scoring model when `arc_profile` is set. README frames the UI as a "polished Streamlit UI" but the arc semantics deserve first-class billing. | [app/streamlit_app.py:35](app/streamlit_app.py#L35), [src/models.py:119](src/models.py#L119) |
| 7 | "20 crawl archetypes" for CBR | Accurate count. But README implies CBR is an active retrieval step that warm-starts the router; today it is **advisory only** — `warm_start_from_case` exists but is never called from `plan_crawl`. CBR output is used as a narrative anchor in the explanation, not as a routing seed. This is exactly what Phase 3 targets. | [src/decision_system.py:344](src/decision_system.py#L344), [src/case_based.py:221](src/case_based.py#L221) |
| 8 | Test count (implicit; docs count says 76) | 99 tests now (76 + 23 `test_evaluation_fixes.py`); `evaluation/FIXES.md` reflects the new total but `README.md` doesn't. | [tests/test_evaluation_fixes.py](tests/test_evaluation_fixes.py) |

### Minor notes

- `docs/WRITEUP.md`, `docs/dataset_report.md`, `docs/qa_scenarios.md`, `docs/screenshots/` exist and round out the deliverable; README mentions `docs/WRITEUP.md` but not the others.
- `README.md` says "no neural nets, no ML training, no runtime API calls." Accurate for `src/`; `scripts/` is offline enrichment, also no API calls.
- The five-technique thesis in the README is accurate and matches what `src/` does today.

---

## 6. Invariants that hold today (from `evaluation/REPORT.md` §2)

Every produced route satisfies, across 45 scenarios + 100 random:

1. Each stop's bar is open at its arrival.
2. Arrivals are strictly monotonic (`departure[i] < arrival[i+1]`).
3. Arrivals within `[start_time, end_time)`; departures ≤ `end_time`.
4. No bar repeats within a route.
5. No vetoed bar appears.
6. If `neighborhoods` is set, every chosen bar is inside it.
7. `len(stops) ≤ max_stops`.
8. `total_walking_miles` matches direct re-computation to within 0.01.
9. Every chosen bar's `avg_drink_price ≤ 2 × poorest user's cap`.
10. Determinism: same input → identical route signature + total utility + walking miles.
11. User-order invariance: shuffling `group.users` produces zero route differences.

These are the load-bearing invariants the refactor phases must preserve.
