"""Deep probes — issues the bulk harness can miss.

  1. Determinism: same input N times → same output.
  2. Order-sensitivity: shuffling user order → same plan?
  3. Perturbation: ±$1 budget, +5 min window → small or large change?
  4. Strategy switching at thresholds.
  5. Accessibility filter behavior with all-None data.
  6. Open-hours filter on past-midnight crossings.
  7. Walking penalty calibration.
  8. Equity: who is served best/worst across many random groups.
  9. Performance scaling.
 10. Explanation duplication / template robustness.
 11. Counterfactual sanity (alt route ≠ degenerate).
 12. Runner-up gap distribution (are runner-ups truly competitive?).
 13. CBR retrieval: does the right archetype win for the right group?
 14. Dataset health: % of bars exposed in random scenarios over many seeds.
 15. Vibe vocab leakage (user vibes outside vocab).
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data_loader import load_all
from src.decision_system import plan_crawl, _apply_dealbreakers
from src.group_aggregation import disagreement_profile, select_strategy
from src.models import AccessibilityNeeds, GroupInput, UserPreference
from src.routing import walking_miles
from src.case_based import retrieve, similarity
from src.temporal import is_open

random.seed(99)


# ---------------------------------------------------------------------------

def _sample_group(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 5)
    vibe_pool = ["craft-cocktails", "intimate", "dance-floor", "divey",
                 "rooftop", "live-band", "lively", "post-work",
                 "hidden-gem", "games", "polished", "cozy", "nightcap"]
    users = []
    for i in range(n):
        weights = {v: rng.uniform(0.3, 1.0) for v in rng.sample(vibe_pool, 3)}
        users.append(UserPreference(
            name=f"S{seed}u{i}", vibe_weights=weights,
            max_per_drink=rng.choice([10, 12, 15, 18, 20]),
            preferred_drinks=tuple(rng.sample(
                ["beer", "wine", "cocktails", "whiskey", "shots"], 2)),
            preferred_noise=rng.choice(["conversation", "lively", "loud"]),
        ))
    s = datetime(2025, 5, 2, rng.choice([19, 20, 21]))
    e = s + timedelta(hours=rng.choice([3, 4, 5]))
    return GroupInput(users=users, start_time=s, end_time=e,
                      max_stops=rng.randint(2, 4))


# Probe 1: Determinism

def probe_determinism(bars, cases, rules):
    print("\n--- Probe 1: Determinism ---")
    g = _sample_group(123)
    runs = [plan_crawl(g, bars=bars, cases=cases, rules=rules) for _ in range(5)]
    sigs = [tuple(s.bar.id for s in r.route.stops) for r in runs]
    utils = [round(r.route.total_utility, 6) for r in runs]
    miles = [round(r.route.total_walking_miles, 6) for r in runs]
    sames = len(set(sigs)) == 1 and len(set(utils)) == 1 and len(set(miles)) == 1
    print(f"  5 reruns, identical: {sames}; route signatures: {set(sigs)}")
    return {"deterministic": sames, "route_sigs": [list(s) for s in set(sigs)]}


# Probe 2: User-order sensitivity

def probe_user_order(bars, cases, rules):
    print("\n--- Probe 2: User-order sensitivity ---")
    g = _sample_group(50)
    base = plan_crawl(g, bars=bars, cases=cases, rules=rules)
    base_sig = tuple(s.bar.id for s in base.route.stops)
    diffs = 0
    for shuffle_seed in range(10):
        rng = random.Random(shuffle_seed)
        users2 = list(g.users)
        rng.shuffle(users2)
        g2 = GroupInput(
            users=users2, start_time=g.start_time, end_time=g.end_time,
            start_location=g.start_location, max_stops=g.max_stops,
            neighborhoods=g.neighborhoods, walking_only=g.walking_only,
            accessibility_needs=g.accessibility_needs,
            want_food=g.want_food, arc_profile=g.arc_profile,
        )
        r = plan_crawl(g2, bars=bars, cases=cases, rules=rules)
        sig = tuple(s.bar.id for s in r.route.stops)
        if sig != base_sig:
            diffs += 1
    print(f"  Different routes from user-order shuffle: {diffs}/10")
    return {"order_sensitive_diffs": diffs}


# Probe 3: Sensitivity to single-user budget perturbation

def probe_budget_perturbation(bars, cases, rules):
    print("\n--- Probe 3: ±$1 budget perturbation ---")
    diffs = 0
    util_deltas = []
    for seed in range(20):
        g = _sample_group(seed)
        base = plan_crawl(g, bars=bars, cases=cases, rules=rules)
        # Bump one user's budget by +$1
        u0 = g.users[0]
        u0_new = UserPreference(
            name=u0.name, vibe_weights=u0.vibe_weights,
            criterion_weights=u0.criterion_weights,
            max_per_drink=u0.max_per_drink + 1,
            preferred_drinks=u0.preferred_drinks,
            preferred_noise=u0.preferred_noise,
            vetoes=u0.vetoes, age=u0.age,
        )
        g2 = GroupInput(
            users=[u0_new] + g.users[1:],
            start_time=g.start_time, end_time=g.end_time,
            start_location=g.start_location, max_stops=g.max_stops,
            neighborhoods=g.neighborhoods, walking_only=g.walking_only,
            accessibility_needs=g.accessibility_needs,
            want_food=g.want_food, arc_profile=g.arc_profile,
        )
        r2 = plan_crawl(g2, bars=bars, cases=cases, rules=rules)
        sig1 = tuple(s.bar.id for s in base.route.stops)
        sig2 = tuple(s.bar.id for s in r2.route.stops)
        if sig1 != sig2:
            diffs += 1
        util_deltas.append(r2.route.total_utility - base.route.total_utility)
    print(f"  Routes that changed for +$1: {diffs}/20")
    print(f"  utility deltas: mean {statistics.mean(util_deltas):+.4f}, "
          f"max abs {max(abs(d) for d in util_deltas):.4f}")
    return {"perturbation_route_diffs": diffs,
            "util_delta_mean": round(statistics.mean(util_deltas), 4),
            "util_delta_max_abs": round(max(abs(d) for d in util_deltas), 4)}


# Probe 4: Strategy switching at thresholds

def probe_threshold_switch(bars, cases, rules):
    """As budget spread crosses 3.0×, strategy should switch to egalitarian."""
    print("\n--- Probe 4: Strategy threshold transitions ---")
    out = {}
    base_users = [
        UserPreference(name="Big", vibe_weights={"craft-cocktails": 0.9, "polished": 0.7},
                       max_per_drink=20),
        UserPreference(name="Mid", vibe_weights={"lively": 0.6, "post-work": 0.5},
                       max_per_drink=12),
    ]
    transitions = []
    for poor_cap in [10, 8, 7, 6, 5, 4]:
        u3 = UserPreference(name="Tight",
                            vibe_weights={"divey": 0.6, "unpretentious": 0.5},
                            max_per_drink=poor_cap)
        users = base_users + [u3]
        prof = disagreement_profile(users, bars)
        # Phase 1: select_strategy returns a StrategyDecision — pull the
        # strategy name + rule id off the dataclass.
        _decision = select_strategy(prof, rules)
        strat, rule_id = _decision.strategy_id, _decision.triggering_rule_id
        transitions.append({
            "poor_cap": poor_cap,
            "budget_spread_ratio": round(prof["budget_spread_ratio"], 2),
            "strategy": strat, "rule": rule_id,
        })
    for t in transitions:
        print(f"  poor_cap={t['poor_cap']:>2} ratio={t['budget_spread_ratio']:>4.1f}x "
              f"-> {t['strategy']} ({t['rule']})")
    out["budget_transitions"] = transitions

    # Veto-density threshold
    vt = []
    for n_vetoes in [0, 5, 15, 25, 35, 50]:
        rng = random.Random(0)
        veto_sample = [b.id for b in rng.sample(bars, n_vetoes)] if n_vetoes else []
        users = [UserPreference(name="V", vibe_weights={"lively": 0.5},
                                max_per_drink=15, vetoes=tuple(veto_sample))]
        prof = disagreement_profile(users, bars)
        # Phase 1: select_strategy returns a StrategyDecision — pull the
        # strategy name + rule id off the dataclass.
        _decision = select_strategy(prof, rules)
        strat, rule_id = _decision.strategy_id, _decision.triggering_rule_id
        vt.append({
            "n_vetoes": n_vetoes,
            "density": round(prof["dealbreaker_density"], 3),
            "strategy": strat, "rule": rule_id,
        })
    for t in vt:
        print(f"  vetoes={t['n_vetoes']:>2} density={t['density']:.3f} "
              f"-> {t['strategy']} ({t['rule']})")
    out["veto_transitions"] = vt
    return out


# Probe 5: Accessibility filter behavior

def probe_accessibility(bars, cases, rules):
    print("\n--- Probe 5: Accessibility filter behavior ---")
    sf_true = sum(1 for b in bars if b.accessibility.get("step_free") is True)
    sf_false = sum(1 for b in bars if b.accessibility.get("step_free") is False)
    sf_none = sum(1 for b in bars if b.accessibility.get("step_free") is None)
    print(f"  step_free in dataset: True={sf_true} False={sf_false} None={sf_none}")
    # If user requires step_free, what gets filtered?
    g = GroupInput(
        users=[UserPreference(name="Mob", vibe_weights={"conversation": 0.7},
                              max_per_drink=15, preferred_noise="conversation")],
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=2, accessibility_needs=AccessibilityNeeds(step_free=True),
    )
    survivors, excluded = _apply_dealbreakers(bars, g, rules)
    excluded_for_a11y = [e for e in excluded if e["rule_id"] == "accessibility_unmet"]
    print(f"  With step_free=True user: survivors={len(survivors)}, "
          f"a11y-excluded={len(excluded_for_a11y)}")
    if sf_true == 0 and len(survivors) == 0:
        print("  STATUS: filter is conservative — admits only explicit step_free=True. ✅")
    return {
        "step_free_true": sf_true, "step_free_false": sf_false, "step_free_none": sf_none,
        "with_a11y_filter_survivors": len(survivors),
        "a11y_excluded_count": len(excluded_for_a11y),
    }


# Probe 6: Open-hours past-midnight handling

def probe_past_midnight(bars):
    print("\n--- Probe 6: Past-midnight open-hours sanity ---")
    # Find bars with past-midnight closing (e.g. close_h > 24)
    pm_count = 0
    for b in bars:
        for day, hours in b.open_hours.items():
            if hours and len(hours) == 2:
                close_h = float(hours[1].split(":")[0]) + float(hours[1].split(":")[1]) / 60
                if close_h > 24:
                    pm_count += 1
                    break
    print(f"  bars with past-midnight closing on at least one day: {pm_count}")
    # Pick one and probe at 1 AM
    for b in bars:
        for day, hours in b.open_hours.items():
            if hours and float(hours[1].split(":")[0]) > 24:
                # Test at 1 AM (next day)
                # day index: friday hours close 26:00 -> 2 AM Saturday
                # We'll just test that is_open returns True at 1 AM following day
                day_idx = ["mon","tue","wed","thu","fri","sat","sun"].index(day)
                # day_idx is the day the bar opened. Next day at 1 AM = day_idx+1, hour 1
                # Let's pick a Friday in May 2025: 2025-05-02 is Friday
                base = datetime(2025, 5, 5)  # Monday
                for offset in range(7):
                    if (base + timedelta(days=offset)).weekday() == day_idx:
                        target = base + timedelta(days=offset+1, hours=1)
                        opn = is_open(b, target)
                        print(f"  Test: {b.name} ({day} {hours}) open at {target}? {opn}")
                        return {"past_midnight_bars": pm_count,
                                "sample_open_at_1am": opn}
    return {"past_midnight_bars": pm_count}


# Probe 7: Walking-distance calibration

def probe_walking_distances(bars):
    print("\n--- Probe 7: Walking distances ---")
    # Sample inter-bar distances
    rng = random.Random(0)
    samples = []
    for _ in range(2000):
        a, b = rng.sample(bars, 2)
        m = walking_miles((a.lat, a.lon), (b.lat, b.lon))
        samples.append(m)
    samples.sort()
    print(f"  inter-bar distance percentiles: "
          f"p10={samples[200]:.2f} p50={samples[1000]:.2f} "
          f"p90={samples[1800]:.2f} max={samples[-1]:.2f} miles")
    print(f"  fraction within 0.6mi (walkable): "
          f"{sum(1 for s in samples if s <= 0.6)/len(samples):.2%}")
    return {
        "p10_miles": round(samples[200], 3),
        "p50_miles": round(samples[1000], 3),
        "p90_miles": round(samples[1800], 3),
        "max_miles": round(samples[-1], 3),
        "frac_walkable_06": round(sum(1 for s in samples if s <= 0.6)/len(samples), 3),
    }


# Probe 8: Equity across many groups

def probe_served_equity(bars, cases, rules):
    print("\n--- Probe 8: Served-ness equity (Gini) over 50 random groups ---")
    ginis = []
    min_means = []
    for s in range(50):
        g = _sample_group(s + 200)
        try:
            r = plan_crawl(g, bars=bars, cases=cases, rules=rules)
        except Exception:
            continue
        if not r.route.stops:
            continue
        means = [v["mean_score_on_route"] for v in r.per_user_report.values()]
        if not means:
            continue
        min_means.append(min(means))
        ginis.append(_gini(means))
    print(f"  Gini mean/median/max: {statistics.mean(ginis):.3f} / "
          f"{statistics.median(ginis):.3f} / {max(ginis):.3f}")
    print(f"  worst-served-user mean across runs: "
          f"min={min(min_means):.3f} median={statistics.median(min_means):.3f}")
    return {
        "gini_mean": round(statistics.mean(ginis), 3),
        "gini_median": round(statistics.median(ginis), 3),
        "gini_max": round(max(ginis), 3),
        "worst_user_mean_min": round(min(min_means), 3),
        "worst_user_mean_median": round(statistics.median(min_means), 3),
        "n_runs": len(ginis),
    }


# Probe 9: Performance scaling

def probe_perf(bars, cases, rules):
    print("\n--- Probe 9: Performance scaling ---")
    # Vary group size
    rows = []
    for n_users in [1, 2, 4, 6, 8, 10]:
        rng = random.Random(31 + n_users)
        users = [UserPreference(
            name=f"U{i}",
            vibe_weights={v: rng.uniform(0.3, 1.0) for v in rng.sample(
                ["craft-cocktails", "intimate", "dance-floor", "divey",
                 "rooftop", "live-band", "lively", "post-work",
                 "hidden-gem", "games", "polished", "cozy"], 4)},
            max_per_drink=rng.choice([10, 15, 18, 20]),
            preferred_drinks=("cocktails", "beer"),
            preferred_noise="lively") for i in range(n_users)]
        g = GroupInput(users=users,
                       start_time=datetime(2025, 5, 2, 20),
                       end_time=datetime(2025, 5, 3, 1),
                       max_stops=4)
        ts = []
        for _ in range(3):
            t0 = time.time()
            plan_crawl(g, bars=bars, cases=cases, rules=rules,
                       compute_counterfactuals=False)
            ts.append((time.time()-t0)*1000)
        rows.append({"n_users": n_users, "median_ms": round(statistics.median(ts), 1)})
        print(f"  n_users={n_users:>2} median={statistics.median(ts):>6.1f} ms")

    # Vary max_stops (with counterfactuals on)
    rows2 = []
    for ms in [1, 2, 3, 4, 5, 6]:
        users = [UserPreference(name="A", vibe_weights={"divey": 0.7, "lively": 0.6},
                                max_per_drink=15, preferred_noise="lively"),
                 UserPreference(name="B", vibe_weights={"games": 0.7, "lively": 0.6},
                                max_per_drink=15, preferred_noise="lively")]
        g = GroupInput(users=users,
                       start_time=datetime(2025, 5, 2, 19),
                       end_time=datetime(2025, 5, 3, 2),
                       max_stops=ms)
        ts = []
        for _ in range(3):
            t0 = time.time()
            plan_crawl(g, bars=bars, cases=cases, rules=rules,
                       compute_counterfactuals=True)
            ts.append((time.time()-t0)*1000)
        rows2.append({"max_stops": ms, "median_ms": round(statistics.median(ts), 1)})
        print(f"  max_stops={ms:>2} median={statistics.median(ts):>6.1f} ms (full pipeline)")
    return {"by_users": rows, "by_max_stops": rows2}


# Probe 10: Explanation quality

def probe_explanation_quality(bars, cases, rules):
    print("\n--- Probe 10: Explanation quality ---")
    # Run 30 random groups, collect: word counts, repetition rate
    summary_words = []
    stop_words = []
    duplicate_stops = 0
    total_routes = 0
    for s in range(30):
        g = _sample_group(s + 400)
        r = plan_crawl(g, bars=bars, cases=cases, rules=rules)
        if not r.route.stops:
            continue
        total_routes += 1
        summary_words.append(len(r.explanations.summary.split()))
        # stop explanations
        starts = []
        for child in r.explanations.children[1:1+len(r.route.stops)]:
            stop_words.append(len(child.summary.split()))
            starts.append(child.summary[:30])
        # check duplicate openings within a single route
        if len(starts) != len(set(starts)):
            duplicate_stops += 1
    print(f"  summary words: median {statistics.median(summary_words):.0f}, "
          f"p95 {sorted(summary_words)[int(len(summary_words)*0.95)]:.0f}")
    print(f"  stop words: median {statistics.median(stop_words):.0f}, "
          f"max {max(stop_words):.0f}")
    over_80 = sum(1 for w in stop_words if w > 80)
    over_200 = sum(1 for w in summary_words if w > 200)
    print(f"  stop explanations >80 words (target): {over_80}/{len(stop_words)}")
    print(f"  summaries >200 words (target): {over_200}/{len(summary_words)}")
    print(f"  routes with duplicate stop openings: {duplicate_stops}/{total_routes}")
    return {
        "summary_words_median": statistics.median(summary_words),
        "stop_words_median": statistics.median(stop_words),
        "stop_words_max": max(stop_words),
        "stop_over_80": over_80,
        "summary_over_200": over_200,
        "duplicate_stop_openings_routes": duplicate_stops,
    }


# Probe 11: Counterfactual sanity

def probe_counterfactuals(bars, cases, rules):
    print("\n--- Probe 11: Counterfactual sanity ---")
    samples = []
    n_with_cf = 0
    n_identical_cf = 0
    n_better_cf = 0
    for s in range(20):
        g = _sample_group(s + 600)
        r = plan_crawl(g, bars=bars, cases=cases, rules=rules)
        if not r.route.stops:
            continue
        base_sig = tuple(x.bar.id for x in r.route.stops)
        base_util = r.route.total_utility
        if not r.alternatives:
            continue
        n_with_cf += 1
        for alt in r.alternatives:
            asig = tuple(x.bar.id for x in alt.stops)
            if asig == base_sig:
                n_identical_cf += 1
            if alt.total_utility > base_util + 0.001:
                n_better_cf += 1
    print(f"  scenarios w/ counterfactuals: {n_with_cf}")
    print(f"  CF that produced identical route: {n_identical_cf}")
    print(f"  CF with strictly higher utility than base: {n_better_cf}")
    return {"n_with_cf": n_with_cf,
            "n_identical_cf": n_identical_cf,
            "n_better_cf": n_better_cf}


# Probe 12: Runner-up gap distribution

def probe_runner_ups(bars, cases, rules):
    print("\n--- Probe 12: Runner-up gap distribution ---")
    gaps = []
    n_close = 0
    n_far = 0
    for s in range(40):
        g = _sample_group(s + 800)
        r = plan_crawl(g, bars=bars, cases=cases, rules=rules)
        if not r.route.stops:
            continue
        ru = r.traces.get("runner_ups", {})
        for _idx, (_n, gap) in ru.items():
            gaps.append(gap)
            if gap < 0.05:
                n_close += 1
            elif gap > 0.5:
                n_far += 1
    print(f"  runner-up gaps: count={len(gaps)} "
          f"mean={statistics.mean(gaps):.3f} median={statistics.median(gaps):.3f}")
    print(f"  very-close (<0.05): {n_close} ; very-far (>0.5): {n_far}")
    return {
        "n_gaps": len(gaps),
        "gap_mean": round(statistics.mean(gaps), 4) if gaps else None,
        "gap_median": round(statistics.median(gaps), 4) if gaps else None,
        "gap_p90": round(sorted(gaps)[int(len(gaps)*0.9)], 4) if gaps else None,
        "n_close": n_close, "n_far": n_far,
    }


# Probe 13: CBR retrieval

def probe_cbr_retrieval(bars, cases, rules):
    print("\n--- Probe 13: CBR retrieval coherence ---")
    # Date-night group should retrieve a date/intimate case as #1
    g_date = GroupInput(
        users=[UserPreference(
            name="A", vibe_weights={"date": 1.0, "intimate": 1.0,
                                    "craft-cocktails": 0.8},
            max_per_drink=22, preferred_drinks=("cocktails", "wine"),
            preferred_noise="conversation")] * 2,
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=2,
    )
    matches = retrieve(g_date, cases, top_k=3)
    print(f"  Date-group top-3 cases: {[(c.id, round(s, 3)) for c, s, _ in matches]}")
    # Birthday-large group
    g_party = GroupInput(
        users=[UserPreference(name=f"P{i}",
                              vibe_weights={"birthday-party": 1.0,
                                            "large-groups": 0.9, "dance-floor": 0.7},
                              max_per_drink=18,
                              preferred_drinks=("cocktails", "shots"),
                              preferred_noise="loud") for i in range(7)],
        start_time=datetime(2025, 5, 2, 21),
        end_time=datetime(2025, 5, 3, 2),
        max_stops=3,
    )
    matches2 = retrieve(g_party, cases, top_k=3)
    print(f"  Party-group top-3 cases: {[(c.id, round(s, 3)) for c, s, _ in matches2]}")
    # Tight-budget dive group
    g_div = GroupInput(
        users=[UserPreference(name="X",
                              vibe_weights={"divey": 1.0, "unpretentious": 0.8,
                                            "games": 0.7},
                              max_per_drink=8,
                              preferred_drinks=("beer", "shots"),
                              preferred_noise="lively")] * 3,
        start_time=datetime(2025, 5, 2, 20),
        end_time=datetime(2025, 5, 3, 0),
        max_stops=3,
    )
    matches3 = retrieve(g_div, cases, top_k=3)
    print(f"  Dive-group top-3 cases: {[(c.id, round(s, 3)) for c, s, _ in matches3]}")
    return {
        "date_top3": [(c.id, round(s, 3)) for c, s, _ in matches],
        "party_top3": [(c.id, round(s, 3)) for c, s, _ in matches2],
        "dive_top3": [(c.id, round(s, 3)) for c, s, _ in matches3],
    }


# Probe 14: Dataset coverage by random scenarios

def probe_coverage(bars, cases, rules):
    print("\n--- Probe 14: Dataset coverage ---")
    used_ids = set()
    n_routes = 0
    for s in range(100):
        g = _sample_group(s + 1000)
        r = plan_crawl(g, bars=bars, cases=cases, rules=rules,
                       compute_counterfactuals=False)
        if r.route.stops:
            n_routes += 1
            used_ids.update(s_.bar.id for s_ in r.route.stops)
    print(f"  unique bars chosen across {n_routes} routes: "
          f"{len(used_ids)} / {len(bars)} ({100*len(used_ids)/len(bars):.1f}%)")
    return {"unique_bars_used": len(used_ids), "total_bars": len(bars),
            "coverage_pct": round(100*len(used_ids)/len(bars), 1)}


# Probe 15: Vibe vocab leakage

def probe_vibe_leakage(bars):
    print("\n--- Probe 15: Vibe vocab leakage ---")
    vocab = set()
    import json
    v = json.load(open(ROOT/"data/vibe_vocab.json"))
    for facet, lst in v["facets"].items():
        vocab.update(lst)
    used = set()
    for b in bars:
        used.update(b.vibe_tags)
    extras_in_bars = used - vocab
    unused_in_vocab = vocab - used
    print(f"  Bar tags outside vocab: {sorted(extras_in_bars)}")
    print(f"  Vocab tags never used: {sorted(unused_in_vocab)}")
    return {
        "extras_in_bars": sorted(extras_in_bars),
        "unused_vocab": sorted(unused_in_vocab),
    }


def _gini(values):
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(values)
    cum = 0.0
    for i, v in enumerate(sorted_v, 1):
        cum += i * v
    s = sum(sorted_v)
    if s == 0:
        return 0.0
    return (2 * cum) / (n * s) - (n + 1) / n


def main():
    d = load_all()
    bars, cases, rules = d["bars"], d["cases"], d["rules"]
    out = {}
    out["determinism"] = probe_determinism(bars, cases, rules)
    out["user_order"] = probe_user_order(bars, cases, rules)
    out["budget_perturb"] = probe_budget_perturbation(bars, cases, rules)
    out["thresholds"] = probe_threshold_switch(bars, cases, rules)
    out["accessibility"] = probe_accessibility(bars, cases, rules)
    out["past_midnight"] = probe_past_midnight(bars)
    out["walking_distances"] = probe_walking_distances(bars)
    out["equity"] = probe_served_equity(bars, cases, rules)
    out["perf"] = probe_perf(bars, cases, rules)
    out["explanation"] = probe_explanation_quality(bars, cases, rules)
    out["counterfactuals"] = probe_counterfactuals(bars, cases, rules)
    out["runner_ups"] = probe_runner_ups(bars, cases, rules)
    out["cbr"] = probe_cbr_retrieval(bars, cases, rules)
    out["coverage"] = probe_coverage(bars, cases, rules)
    out["vibe_leakage"] = probe_vibe_leakage(bars)
    p = ROOT / "evaluation" / "deep_results.json"
    with open(p, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nWrote {p}")


if __name__ == "__main__":
    main()
