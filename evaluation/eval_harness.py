"""Comprehensive evaluation harness for the Bar Crawl decision system.

Runs ~50+ scenarios spanning:
  * aligned groups, divergent groups, edge cases
  * each meta-selector strategy (utilitarian / egalitarian / Borda / Copeland / approval-veto)
  * each NIGHT_STYLE arc from the Streamlit UI (full-system test)
  * stress: 8-person groups, infeasibly tight windows, mass vetoes, all-cheap, all-splurge
  * robustness: determinism, perturbed inputs, parameter sensitivity
  * performance: latency vs candidates / users / max_stops
  * quality: strategy-rule firing, explanation length, runner-up gaps,
    counterfactual coherence, served-ness equity (Gini-style)

Writes all raw results to evaluation/results.json and a summary report
to evaluation/REPORT.md.
"""
from __future__ import annotations

import copy
import json
import math
import random
import statistics
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data_loader import load_all
from src.decision_system import plan_crawl
from src.group_aggregation import (
    aggregate, disagreement_profile, select_strategy,
)
from src.models import AccessibilityNeeds, GroupInput, UserPreference
from src.routing import walking_miles
from src.scoring import normalize_weights, score_bar_for_user

# Mirror NIGHT_STYLES from the Streamlit app so we exercise the full real-world arc surface
from datetime import time as dtime

NIGHT_STYLES = {
    "Chill bar hop": {
        "arc": [
            ("Warm-up",  {"post-work": 1.2, "conversation": 0.8, "walk-in": 0.6}),
            ("Middle",   {"hidden-gem": 1.0, "unpretentious": 0.7, "local-institution": 0.7}),
            ("Nightcap", {"nightcap": 1.3, "cozy": 1.0, "intimate": 0.6}),
        ],
        "max_stops": 3, "start": dtime(20, 0), "end": dtime(1, 0),
        "noise": "lively", "drinks": ["cocktails", "beer"], "walking_only": True,
    },
    "Pregame -> clubs": {
        "arc": [
            ("Warm-up",  {"pregame": 1.5, "unpretentious": 0.5, "large-groups": 0.5}),
            ("Energy",   {"music-loud": 1.2, "crowd-loud": 1.0, "dj-set": 0.8}),
            ("Peak",     {"dance-floor": 1.8, "dj-set": 1.2, "late-close": 1.0}),
        ],
        "max_stops": 3, "start": dtime(21, 0), "end": dtime(3, 30),
        "noise": "loud", "drinks": ["cocktails", "shots", "beer"], "walking_only": False,
    },
    "Dive bar tour": {
        "arc": [
            ("Warm-up",  {"divey": 1.5, "local-institution": 1.0, "historic": 0.8}),
            ("Middle",   {"divey": 1.5, "games": 0.8, "unpretentious": 0.6}),
            ("Nightcap", {"divey": 1.3, "late-close": 1.0, "cozy": 0.5}),
        ],
        "max_stops": 4, "start": dtime(20, 0), "end": dtime(1, 30),
        "noise": "lively", "drinks": ["beer", "shots"], "walking_only": True,
    },
    "Date night": {
        "arc": [
            ("Opener",   {"date": 1.5, "intimate": 1.0, "natural-wine": 0.8, "craft-cocktails": 0.6}),
            ("Main",     {"date": 1.3, "craft-cocktails": 1.2, "dim": 0.9, "intimate": 0.8}),
        ],
        "max_stops": 2, "start": dtime(19, 30), "end": dtime(23, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine"], "walking_only": True,
    },
    "Birthday party": {
        "arc": [
            ("Meetup",   {"large-groups": 1.5, "pregame": 0.8, "birthday-party": 0.8}),
            ("Main",     {"birthday-party": 1.5, "large-groups": 1.0, "crowd-loud": 0.8}),
            ("Peak",     {"dance-floor": 1.2, "birthday-party": 1.0, "music-loud": 0.9}),
        ],
        "max_stops": 3, "start": dtime(21, 0), "end": dtime(3, 0),
        "noise": "loud", "drinks": ["cocktails", "shots", "beer"], "walking_only": False,
    },
    "Post-dinner drinks": {
        "arc": [
            ("Main",     {"craft-cocktails": 1.3, "intimate": 1.0, "dim": 0.8}),
            ("Nightcap", {"nightcap": 1.5, "cozy": 1.0, "quiet": 0.7}),
        ],
        "max_stops": 2, "start": dtime(21, 30), "end": dtime(0, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine", "whiskey"], "walking_only": True,
    },
    "Late-night only": {
        "arc": [
            ("Arrive",   {"late-close": 1.5, "dim": 0.8, "divey": 0.6}),
            ("Peak",     {"dance-floor": 1.5, "dj-set": 1.2, "late-close": 1.0, "music-loud": 0.8}),
        ],
        "max_stops": 2, "start": dtime(23, 0), "end": dtime(3, 30),
        "noise": "loud", "drinks": ["cocktails", "shots"], "walking_only": False,
    },
    "Rooftop summer": {
        "arc": [
            ("Sunset",   {"rooftop": 2.0, "airy": 1.0, "instagrammable": 0.8}),
            ("Main",     {"rooftop": 1.8, "craft-cocktails": 0.8, "polished": 0.6}),
            ("Nightcap", {"craft-cocktails": 1.0, "dim": 0.8, "intimate": 0.7, "hidden-gem": 0.5}),
        ],
        "max_stops": 3, "start": dtime(19, 0), "end": dtime(0, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine"], "walking_only": False,
    },
    "Games night": {
        "arc": [
            ("Warm-up",  {"games": 2.0, "unpretentious": 0.5, "large-groups": 0.4}),
            ("Main",     {"games": 2.0, "crowd-loud": 0.5, "large-groups": 0.4}),
        ],
        "max_stops": 2, "start": dtime(19, 30), "end": dtime(1, 0),
        "noise": "lively", "drinks": ["beer", "cocktails"], "walking_only": True,
    },
}

STYLE_CRITERION_WEIGHTS = {
    "vibe": 0.50, "budget": 0.15, "drink_match": 0.08, "noise": 0.05,
    "distance": 0.05, "happy_hour_active": 0.03, "specials_match": 0.03,
    "crowd_fit": 0.03, "novelty": 0.03, "quality_signal": 0.05,
}

SOON = datetime(2025, 5, 2)  # a Friday


def _merge_arc(arc):
    out = {}
    for _r, w in arc:
        for v, ww in w.items():
            out[v] = max(out.get(v, 0.0), ww)
    return out


def _combine(d, st, et):
    s = datetime.combine(d, st)
    e = datetime.combine(d, et)
    if et <= st:
        e = datetime.combine(d + timedelta(days=1), et)
    return s, e


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def scenario_aligned_two_friends():
    users = [
        UserPreference(name="Alice",
                       vibe_weights={"craft-cocktails": 1.0, "date": 1.0, "intimate": 0.8},
                       max_per_drink=18,
                       preferred_drinks=("cocktails", "wine"),
                       preferred_noise="conversation"),
        UserPreference(name="Bob",
                       vibe_weights={"craft-cocktails": 1.0, "date": 0.9, "dim": 0.7},
                       max_per_drink=18,
                       preferred_drinks=("cocktails", "whiskey"),
                       preferred_noise="conversation"),
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 0),
                      max_stops=3,
                      neighborhoods=("East Village", "Lower East Side"))


def scenario_budget_gap():
    """Wide budget spread => egalitarian should fire."""
    users = [
        UserPreference(name="Highroller",
                       vibe_weights={"craft-cocktails": 1.0, "polished": 0.8},
                       max_per_drink=40,
                       preferred_drinks=("cocktails", "whiskey"),
                       preferred_noise="conversation"),
        UserPreference(name="Tightbudget",
                       vibe_weights={"divey": 1.0, "unpretentious": 0.8},
                       max_per_drink=8,
                       preferred_drinks=("beer", "shots"),
                       preferred_noise="lively"),
        UserPreference(name="Middle",
                       vibe_weights={"lively": 0.7, "post-work": 0.6},
                       max_per_drink=15,
                       preferred_drinks=("beer", "cocktails"),
                       preferred_noise="lively"),
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 19),
                      end_time=datetime(2025, 5, 2, 23, 30),
                      max_stops=3,
                      neighborhoods=("East Village",))


def scenario_many_vetoes(bars):
    """Three users each veto 12 random bars => density >> 20% => approval_veto."""
    rng = random.Random(7)
    sample = rng.sample(bars, 36)
    chunks = [[b.id for b in sample[i*12:(i+1)*12]] for i in range(3)]
    users = [
        UserPreference(name="Vetoer1",
                       vibe_weights={"craft-cocktails": 0.9, "intimate": 0.8},
                       max_per_drink=18,
                       preferred_drinks=("cocktails",), preferred_noise="conversation",
                       vetoes=tuple(chunks[0])),
        UserPreference(name="Vetoer2",
                       vibe_weights={"divey": 0.9, "lively": 0.7},
                       max_per_drink=12,
                       preferred_drinks=("beer",), preferred_noise="lively",
                       vetoes=tuple(chunks[1])),
        UserPreference(name="Vetoer3",
                       vibe_weights={"hidden-gem": 0.9, "post-work": 0.7},
                       max_per_drink=15,
                       preferred_drinks=("cocktails", "wine"),
                       preferred_noise="lively",
                       vetoes=tuple(chunks[2])),
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 0, 30),
                      max_stops=3)


def scenario_vibe_split():
    """High variance in vibe weights => copeland."""
    users = [
        UserPreference(name="Cocktail",
                       vibe_weights={"craft-cocktails": 1.0, "intimate": 0.9, "dim": 0.8},
                       max_per_drink=18, preferred_drinks=("cocktails",),
                       preferred_noise="conversation"),
        UserPreference(name="Dancer",
                       vibe_weights={"dance-floor": 1.0, "dj-set": 0.9, "late-close": 0.8},
                       max_per_drink=18, preferred_drinks=("cocktails", "shots"),
                       preferred_noise="loud"),
        UserPreference(name="Diver",
                       vibe_weights={"divey": 1.0, "unpretentious": 0.9, "games": 0.7},
                       max_per_drink=12, preferred_drinks=("beer",),
                       preferred_noise="lively"),
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 21),
                      end_time=datetime(2025, 5, 3, 1),
                      max_stops=3)


def scenario_intense_user():
    """One user with peaked weights => borda."""
    users = [
        UserPreference(name="ObsessedRooftop",
                       vibe_weights={"rooftop": 1.0, "airy": 0.05, "polished": 0.05,
                                     "instagrammable": 0.05, "craft-cocktails": 0.05},
                       max_per_drink=20, preferred_drinks=("cocktails",),
                       preferred_noise="conversation"),
        UserPreference(name="Casual1",
                       vibe_weights={"lively": 0.4, "post-work": 0.4, "conversation": 0.4},
                       max_per_drink=15, preferred_drinks=("beer",),
                       preferred_noise="lively"),
        UserPreference(name="Casual2",
                       vibe_weights={"hidden-gem": 0.4, "unpretentious": 0.4},
                       max_per_drink=15, preferred_drinks=("beer", "wine"),
                       preferred_noise="lively"),
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 19),
                      end_time=datetime(2025, 5, 2, 23, 30),
                      max_stops=3)


def scenario_solo():
    users = [
        UserPreference(name="Loner",
                       vibe_weights={"intimate": 1.0, "nightcap": 1.0, "cozy": 0.8},
                       max_per_drink=15,
                       preferred_drinks=("cocktails",),
                       preferred_noise="conversation"),
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 22),
                      end_time=datetime(2025, 5, 3, 1),
                      max_stops=2)


def scenario_eight_friends():
    rng = random.Random(11)
    vibe_pool = ["craft-cocktails", "intimate", "dance-floor", "dj-set",
                 "divey", "rooftop", "live-band", "lively", "post-work",
                 "hidden-gem", "games", "polished", "unpretentious"]
    users = []
    for i in range(8):
        vibes = rng.sample(vibe_pool, 4)
        weights = {v: rng.uniform(0.4, 1.0) for v in vibes}
        users.append(UserPreference(
            name=f"Friend{i+1}", vibe_weights=weights,
            max_per_drink=rng.choice([10, 12, 15, 18, 20, 25]),
            preferred_drinks=tuple(rng.sample(
                ["beer", "wine", "cocktails", "whiskey", "shots", "spirits"], 2)),
            preferred_noise=rng.choice(["conversation", "lively", "loud"]),
        ))
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 1, 30),
                      max_stops=4)


def scenario_infeasible_window():
    """Only 30 min total -> can't make a stop."""
    users = [UserPreference(name="X",
                            vibe_weights={"lively": 1.0},
                            max_per_drink=15)]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 2, 20, 30),
                      max_stops=3)


def scenario_pre_open_window():
    """Window 8am-10am — most bars closed."""
    users = [UserPreference(name="EarlyBird",
                            vibe_weights={"conversation": 1.0},
                            max_per_drink=12,
                            preferred_noise="conversation")]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 8),
                      end_time=datetime(2025, 5, 2, 10),
                      max_stops=2)


def scenario_neighborhood_zero(bars):
    """Neighborhood with zero bars -> empty survivors."""
    users = [UserPreference(name="X",
                            vibe_weights={"lively": 1.0},
                            max_per_drink=15)]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 0),
                      max_stops=3,
                      neighborhoods=("Staten Island",))


def scenario_all_cheap():
    users = [
        UserPreference(name=f"Saver{i}",
                       vibe_weights={"divey": 0.9, "unpretentious": 0.7},
                       max_per_drink=7,
                       preferred_drinks=("beer",),
                       preferred_noise="lively") for i in range(3)
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 2, 23, 30),
                      max_stops=3)


def scenario_all_splurge():
    users = [
        UserPreference(name=f"Splurger{i}",
                       vibe_weights={"craft-cocktails": 0.9, "polished": 0.8},
                       max_per_drink=40,
                       preferred_drinks=("cocktails", "whiskey"),
                       preferred_noise="conversation") for i in range(3)
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 0),
                      max_stops=3)


def scenario_step_free_required():
    users = [UserPreference(name="Mobility",
                            vibe_weights={"conversation": 0.8, "intimate": 0.7},
                            max_per_drink=18,
                            preferred_drinks=("cocktails",),
                            preferred_noise="conversation")]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 0),
                      max_stops=2,
                      accessibility_needs=AccessibilityNeeds(step_free=True))


def scenario_no_vibes_no_weights():
    """User with empty vibe_weights and empty criterion_weights => exercises defaults."""
    users = [UserPreference(name="Empty", vibe_weights={}, max_per_drink=15)]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 0),
                      max_stops=2)


def scenario_max_one_stop():
    users = [UserPreference(name="QuickPit",
                            vibe_weights={"nightcap": 1.0},
                            max_per_drink=15)]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 23),
                      end_time=datetime(2025, 5, 3, 1),
                      max_stops=1)


def scenario_six_stops():
    users = [
        UserPreference(name="A",
                       vibe_weights={"divey": 0.7, "lively": 0.7, "post-work": 0.6},
                       max_per_drink=14, preferred_noise="lively"),
        UserPreference(name="B",
                       vibe_weights={"divey": 0.7, "games": 0.6, "lively": 0.7},
                       max_per_drink=14, preferred_noise="lively"),
    ]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 19),
                      end_time=datetime(2025, 5, 3, 2),
                      max_stops=6)


def scenario_arc_chill_two():
    arc = NIGHT_STYLES["Chill bar hop"]["arc"]
    merged = _merge_arc(arc)
    users = [
        UserPreference(name=f"P{i+1}", vibe_weights=dict(merged),
                       criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
                       max_per_drink=15,
                       preferred_drinks=("cocktails", "beer"),
                       preferred_noise="lively") for i in range(2)
    ]
    s, e = _combine(SOON.date(), dtime(20, 0), dtime(1, 0))
    return GroupInput(users=users,
                      start_time=s, end_time=e, max_stops=3,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=True,
                      neighborhoods=())


def scenario_arc_pregame_clubs():
    arc = NIGHT_STYLES["Pregame -> clubs"]["arc"]
    merged = _merge_arc(arc)
    users = [UserPreference(
        name=f"P{i+1}", vibe_weights=dict(merged),
        criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
        max_per_drink=18,
        preferred_drinks=("cocktails", "shots", "beer"),
        preferred_noise="loud") for i in range(4)]
    s, e = _combine(SOON.date(), dtime(21, 0), dtime(3, 30))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=3,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=False)


def scenario_arc_rooftop_summer():
    arc = NIGHT_STYLES["Rooftop summer"]["arc"]
    merged = _merge_arc(arc)
    users = [UserPreference(
        name=f"P{i+1}", vibe_weights=dict(merged),
        criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
        max_per_drink=20, preferred_drinks=("cocktails", "wine"),
        preferred_noise="conversation") for i in range(3)]
    s, e = _combine(SOON.date(), dtime(19, 0), dtime(0, 30))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=3,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=False)


def scenario_arc_games_night():
    arc = NIGHT_STYLES["Games night"]["arc"]
    merged = _merge_arc(arc)
    users = [UserPreference(
        name=f"P{i+1}", vibe_weights=dict(merged),
        criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
        max_per_drink=14, preferred_drinks=("beer", "cocktails"),
        preferred_noise="lively") for i in range(3)]
    s, e = _combine(SOON.date(), dtime(19, 30), dtime(1, 0))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=2,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=True)


def scenario_arc_late_night():
    arc = NIGHT_STYLES["Late-night only"]["arc"]
    merged = _merge_arc(arc)
    users = [UserPreference(
        name=f"P{i+1}", vibe_weights=dict(merged),
        criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
        max_per_drink=18, preferred_drinks=("cocktails", "shots"),
        preferred_noise="loud") for i in range(2)]
    s, e = _combine(SOON.date(), dtime(23, 0), dtime(3, 30))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=2,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=False)


def scenario_arc_date_night():
    arc = NIGHT_STYLES["Date night"]["arc"]
    merged = _merge_arc(arc)
    users = [
        UserPreference(name="One", vibe_weights=dict(merged),
                       criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
                       max_per_drink=22,
                       preferred_drinks=("cocktails", "wine"),
                       preferred_noise="conversation"),
        UserPreference(name="Two", vibe_weights=dict(merged),
                       criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
                       max_per_drink=22,
                       preferred_drinks=("cocktails", "wine"),
                       preferred_noise="conversation"),
    ]
    s, e = _combine(SOON.date(), dtime(19, 30), dtime(23, 30))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=2,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=True)


def scenario_arc_birthday():
    arc = NIGHT_STYLES["Birthday party"]["arc"]
    merged = _merge_arc(arc)
    users = [UserPreference(
        name=f"P{i+1}", vibe_weights=dict(merged),
        criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
        max_per_drink=18,
        preferred_drinks=("cocktails", "shots", "beer"),
        preferred_noise="loud") for i in range(6)]
    s, e = _combine(SOON.date(), dtime(21, 0), dtime(3, 0))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=3,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=False)


def scenario_arc_dive_tour():
    arc = NIGHT_STYLES["Dive bar tour"]["arc"]
    merged = _merge_arc(arc)
    users = [UserPreference(
        name=f"P{i+1}", vibe_weights=dict(merged),
        criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
        max_per_drink=10, preferred_drinks=("beer", "shots"),
        preferred_noise="lively") for i in range(3)]
    s, e = _combine(SOON.date(), dtime(20, 0), dtime(1, 30))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=4,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=True)


def scenario_arc_post_dinner():
    arc = NIGHT_STYLES["Post-dinner drinks"]["arc"]
    merged = _merge_arc(arc)
    users = [UserPreference(
        name=f"P{i+1}", vibe_weights=dict(merged),
        criterion_weights=dict(STYLE_CRITERION_WEIGHTS),
        max_per_drink=18,
        preferred_drinks=("cocktails", "wine", "whiskey"),
        preferred_noise="conversation") for i in range(2)]
    s, e = _combine(SOON.date(), dtime(21, 30), dtime(0, 30))
    return GroupInput(users=users, start_time=s, end_time=e, max_stops=2,
                      arc_profile=tuple(dict(w) for _r, w in arc),
                      walking_only=True)


def scenario_zero_neighborhood_match(bars):
    """Neighborhoods that genuinely don't appear -> survivors=0."""
    users = [UserPreference(name="X", vibe_weights={"lively": 1.0},
                            max_per_drink=15)]
    return GroupInput(users=users,
                      start_time=datetime(2025, 5, 2, 20),
                      end_time=datetime(2025, 5, 3, 0),
                      max_stops=3,
                      neighborhoods=("Antarctica",))


def scenario_random_seed(seed: int, bars):
    """Randomized realistic group, parameterized by seed for distribution view."""
    rng = random.Random(seed)
    n_users = rng.randint(1, 6)
    vibe_pool = ["craft-cocktails", "intimate", "dance-floor", "divey",
                 "rooftop", "live-band", "lively", "post-work",
                 "hidden-gem", "games", "polished", "cozy", "nightcap"]
    users = []
    for i in range(n_users):
        weights = {v: rng.uniform(0.3, 1.0) for v in rng.sample(vibe_pool, 3)}
        users.append(UserPreference(
            name=f"R{seed}_{i}", vibe_weights=weights,
            max_per_drink=rng.choice([8, 10, 12, 15, 18, 20, 25, 30]),
            preferred_drinks=tuple(rng.sample(
                ["beer", "wine", "cocktails", "whiskey", "shots", "spirits"], 2)),
            preferred_noise=rng.choice(["conversation", "lively", "loud"]),
        ))
    start_h = rng.choice([18, 19, 20, 21, 22])
    duration_h = rng.choice([2, 3, 4, 5, 6])
    s = datetime(2025, 5, 2, start_h)
    e = s + timedelta(hours=duration_h)
    return GroupInput(users=users, start_time=s, end_time=e,
                      max_stops=rng.randint(1, 5))


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

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


def validate_route(result, group):
    """Check structural invariants. Returns list of (check, ok, detail)."""
    checks = []
    route = result.route
    stops = route.stops

    # 1. Route within window
    for i, st in enumerate(stops):
        in_window = (group.start_time <= st.arrival < group.end_time
                     and st.departure <= group.end_time)
        checks.append(("stop_in_window", in_window,
                       f"stop {i} {st.bar.name} arr {st.arrival} dep {st.departure}"))

    # 2. Times monotonic
    for i in range(1, len(stops)):
        mono = stops[i].arrival > stops[i - 1].departure - timedelta(seconds=1)
        checks.append(("monotonic_arrival", mono,
                       f"{i-1}->{i}: dep {stops[i-1].departure} arr {stops[i].arrival}"))

    # 3. No bar repeated
    ids = [s.bar.id for s in stops]
    checks.append(("unique_bars", len(ids) == len(set(ids)), str(ids)))

    # 4. Vetoed bars not in route
    vetoes = set().union(*(set(u.vetoes) for u in group.users))
    checks.append(("no_vetoed_bars_in_route", not (set(ids) & vetoes), str(set(ids) & vetoes)))

    # 5. Neighborhood respected (if specified)
    if group.neighborhoods:
        bad = [s.bar.name for s in stops if s.bar.neighborhood not in group.neighborhoods]
        checks.append(("neighborhood_respected", not bad, str(bad)))

    # 6. Walking distance positive iff >1 stop
    if len(stops) > 1:
        miles = route.total_walking_miles
        # Recompute
        recomputed = sum(walking_miles((stops[i-1].bar.lat, stops[i-1].bar.lon),
                                       (stops[i].bar.lat, stops[i].bar.lon))
                         for i in range(1, len(stops)))
        checks.append(("walking_miles_consistent",
                       abs(miles - recomputed) < 0.01,
                       f"reported {miles:.3f} vs {recomputed:.3f}"))

    # 7. Open at arrival
    from src.temporal import is_open
    for i, s in enumerate(stops):
        ok = is_open(s.bar, s.arrival)
        checks.append(("open_at_arrival", ok,
                       f"stop {i} {s.bar.name} at {s.arrival}"))

    # 8. Each stop respects max_per_drink * 2 (the dealbreaker bound)
    poorest = min(u.max_per_drink for u in group.users) if group.users else 0
    for s in stops:
        ok = s.bar.avg_drink_price <= 2.0 * poorest + 1e-6
        checks.append(("budget_dealbreaker_respected", ok,
                       f"{s.bar.name} ${s.bar.avg_drink_price} vs poorest cap ${poorest}"))

    # 9. max_stops respected
    checks.append(("max_stops_respected", len(stops) <= group.max_stops,
                   f"{len(stops)} <= {group.max_stops}"))

    return checks


# ---------------------------------------------------------------------------
# Run a single scenario
# ---------------------------------------------------------------------------

def run_scenario(name, group, bars, cases, rules):
    t0 = time.time()
    err = None
    try:
        res = plan_crawl(group, bars=bars, cases=cases, rules=rules)
    except Exception as e:
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        return {
            "name": name, "error": err, "time_ms": (time.time()-t0)*1000,
        }
    elapsed_ms = (time.time() - t0) * 1000

    invariants = validate_route(res, group)
    failed = [(c, d) for (c, ok, d) in invariants if not ok]

    # Per-user mean score & equity (Gini)
    per_user_means = [r.get("mean_score_on_route", 0.0)
                      for r in res.per_user_report.values()]

    # Strategy info
    strat = res.route.strategy_used
    rule = res.traces.get("strategy_rule")
    profile = res.traces.get("disagreement_profile", {})

    # Explanation lengths
    summary_words = len(res.explanations.summary.split())
    stop_word_counts = [len(c.summary.split())
                        for c in res.explanations.children[1:1+len(res.route.stops)]]

    # Runner-up gap distribution
    ru = res.traces.get("runner_ups", {})
    ru_gaps = [g for (_n, g) in ru.values()] if ru else []

    # Counterfactual variety
    cf_children = [c for c in res.explanations.children
                   if c.summary == "Counterfactuals"]
    cf_count = len(cf_children[0].children) if cf_children else 0

    # Top contributing criteria for the chosen route
    crit_counter = {}
    if res.route.stops:
        per_user = res.traces.get("per_user_scores", {})
        for stop in res.route.stops:
            agg = {}
            for u, sd in per_user.items():
                if stop.bar.id in sd:
                    for c, v in sd[stop.bar.id].weighted_contributions.items():
                        agg[c] = agg.get(c, 0.0) + v
            if agg:
                top_c = max(agg, key=agg.get)
                crit_counter[top_c] = crit_counter.get(top_c, 0) + 1

    return {
        "name": name,
        "time_ms": round(elapsed_ms, 2),
        "n_users": len(group.users),
        "max_stops": group.max_stops,
        "n_stops": len(res.route.stops),
        "stop_names": [s.bar.name for s in res.route.stops],
        "stop_arrivals": [s.arrival.isoformat() for s in res.route.stops],
        "neighborhoods_in_route": list({s.bar.neighborhood for s in res.route.stops}),
        "strategy": strat,
        "rule_fired": rule,
        "profile": {k: round(v, 3) if isinstance(v, float) else v
                    for k, v in profile.items()},
        "total_utility": round(res.route.total_utility, 3),
        "walking_miles": round(res.route.total_walking_miles, 3),
        "windows_captured": len(res.route.windows_captured),
        "excluded_count": len(res.excluded_bars),
        "exclusion_breakdown": _exclusion_breakdown(res.excluded_bars),
        "survivors_count": res.traces.get("survivors_count"),
        "case_matches": res.traces.get("case_matches"),
        "summary_words": summary_words,
        "stop_word_counts": stop_word_counts,
        "ru_gaps": [round(g, 3) for g in ru_gaps],
        "cf_count": cf_count,
        "per_user_means": [round(x, 3) for x in per_user_means],
        "served_gini": round(_gini(per_user_means), 3),
        "min_per_user_mean": round(min(per_user_means), 3) if per_user_means else None,
        "max_per_user_mean": round(max(per_user_means), 3) if per_user_means else None,
        "top_criterion_per_stop": crit_counter,
        "failed_invariants": failed,
        "error": err,
    }


def _exclusion_breakdown(excluded):
    out = {}
    for e in excluded:
        out[e["rule_id"]] = out.get(e["rule_id"], 0) + 1
    return out


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

SCENARIOS = []  # list of (name, builder)


def register():
    SCENARIOS.append(("aligned_two_friends", lambda b: scenario_aligned_two_friends()))
    SCENARIOS.append(("budget_gap_three", lambda b: scenario_budget_gap()))
    SCENARIOS.append(("many_vetoes_three", lambda b: scenario_many_vetoes(b)))
    SCENARIOS.append(("vibe_split_three", lambda b: scenario_vibe_split()))
    SCENARIOS.append(("intense_user_three", lambda b: scenario_intense_user()))
    SCENARIOS.append(("solo", lambda b: scenario_solo()))
    SCENARIOS.append(("eight_friends", lambda b: scenario_eight_friends()))
    SCENARIOS.append(("infeasible_window", lambda b: scenario_infeasible_window()))
    SCENARIOS.append(("morning_window", lambda b: scenario_pre_open_window()))
    SCENARIOS.append(("nonexistent_neighborhood", lambda b: scenario_zero_neighborhood_match(b)))
    SCENARIOS.append(("all_cheap", lambda b: scenario_all_cheap()))
    SCENARIOS.append(("all_splurge", lambda b: scenario_all_splurge()))
    SCENARIOS.append(("step_free_required", lambda b: scenario_step_free_required()))
    SCENARIOS.append(("empty_user_prefs", lambda b: scenario_no_vibes_no_weights()))
    SCENARIOS.append(("max_one_stop", lambda b: scenario_max_one_stop()))
    SCENARIOS.append(("six_stops_long_window", lambda b: scenario_six_stops()))
    SCENARIOS.append(("arc_chill_two", lambda b: scenario_arc_chill_two()))
    SCENARIOS.append(("arc_pregame_clubs_four", lambda b: scenario_arc_pregame_clubs()))
    SCENARIOS.append(("arc_rooftop_summer_three", lambda b: scenario_arc_rooftop_summer()))
    SCENARIOS.append(("arc_games_night_three", lambda b: scenario_arc_games_night()))
    SCENARIOS.append(("arc_late_night_two", lambda b: scenario_arc_late_night()))
    SCENARIOS.append(("arc_date_night_two", lambda b: scenario_arc_date_night()))
    SCENARIOS.append(("arc_birthday_six", lambda b: scenario_arc_birthday()))
    SCENARIOS.append(("arc_dive_tour_three", lambda b: scenario_arc_dive_tour()))
    SCENARIOS.append(("arc_post_dinner_two", lambda b: scenario_arc_post_dinner()))
    # Random "in the wild" scenarios for distribution view
    for s in range(20):
        SCENARIOS.append((f"random_seed_{s}", lambda b, s=s: scenario_random_seed(s, b)))


def main():
    print("Loading data...")
    d = load_all()
    bars, cases, rules = d["bars"], d["cases"], d["rules"]
    print(f"  bars: {len(bars)} cases: {len(cases)}")

    register()
    print(f"Running {len(SCENARIOS)} scenarios...")
    results = []
    for name, builder in SCENARIOS:
        group = builder(bars)
        r = run_scenario(name, group, bars, cases, rules)
        results.append(r)
        if r.get("error"):
            print(f"  ✗ {name}: ERROR")
        else:
            failed = r["failed_invariants"]
            print(f"  {'✗' if failed else '✓'} {name}: {r['n_stops']}/{r['max_stops']} stops, "
                  f"{r['strategy']} ({r['time_ms']:.1f}ms){' FAILS:'+str([c for c,_ in failed]) if failed else ''}")

    out_path = ROOT / "evaluation" / "results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {out_path}")

    # Summary stats
    print("\n=== SUMMARY ===")
    print(f"Total scenarios: {len(results)}")
    print(f"With error: {sum(1 for r in results if r.get('error'))}")
    print(f"With failed invariants: {sum(1 for r in results if r.get('failed_invariants'))}")
    print(f"Empty routes: {sum(1 for r in results if not r.get('stop_names'))}")

    # Strategy distribution
    from collections import Counter
    strats = Counter(r.get("strategy") for r in results if not r.get("error"))
    print(f"Strategies fired: {dict(strats)}")
    rules_c = Counter(r.get("rule_fired") for r in results if not r.get("error"))
    print(f"Rules fired: {dict(rules_c)}")

    # Latency
    latencies = [r["time_ms"] for r in results if not r.get("error")]
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies)//2]
        p95 = latencies[int(len(latencies)*0.95)]
        print(f"Latency p50/p95/max: {p50:.1f} / {p95:.1f} / {max(latencies):.1f} ms")

    print()
    return results


if __name__ == "__main__":
    main()
