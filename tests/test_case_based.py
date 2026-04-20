"""Tests for case_based.py."""

from datetime import datetime

from src.case_based import retrieve, adapt, warm_start_from_case, similarity
from src.data_loader import load_bars, load_case_library
from src.models import GroupInput, UserPreference


def _group_for(size, budget, vibes, neighborhoods=()):
    users = [UserPreference(name=f"U{i}", max_per_drink=budget,
                             vibe_weights=vibes) for i in range(size)]
    return GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 0),
        start_location=(40.7265, -73.9815),
        max_stops=3,
        neighborhoods=neighborhoods,
    )


def test_retrieve_returns_top_3_sorted():
    cases = load_case_library()
    group = _group_for(size=2, budget=15,
                       vibes={"intimate": 1.0, "polished": 0.8},
                       neighborhoods=("LES",))
    top = retrieve(group, cases)
    assert len(top) == 3
    # Sorted descending
    assert top[0][1] >= top[1][1] >= top[2][1]


def test_retrieve_date_group_prefers_intimate_case():
    cases = load_case_library()
    group = _group_for(size=2, budget=15,
                       vibes={"intimate": 1.0, "polished": 0.9, "conversation": 0.8},
                       neighborhoods=("LES",))
    top = retrieve(group, cases)
    # LES + intimate + 2 people → LES Speakeasy Ladder should be top
    assert top[0][0].id == "case_les_speakeasy_ladder"


def test_retrieve_astoria_group_prefers_beer_garden():
    cases = load_case_library()
    group = _group_for(size=5, budget=12,
                       vibes={"outdoor": 1.0, "large-groups": 0.9},
                       neighborhoods=("Astoria",))
    top = retrieve(group, cases)
    top_ids = [c.id for c, _, _ in top]
    assert "case_astoria_beer_garden_night" in top_ids


def test_adapt_yields_concrete_bars_per_step():
    cases = load_case_library()
    bars = load_bars()
    case = next(c for c in cases if c.id == "case_les_speakeasy_ladder")
    adapted = adapt(case, bars)
    assert len(adapted) == len(case.solution_sequence)
    for step_bars in adapted:
        assert len(step_bars) >= 1, "each step should yield at least one match"


def test_warm_start_produces_distinct_bars():
    cases = load_case_library()
    bars = load_bars()
    case = next(c for c in cases if c.id == "case_hells_kitchen_pub_crawl")
    group = _group_for(size=4, budget=10,
                       vibes={"unpretentious": 1.0, "lively": 0.7},
                       neighborhoods=("Hell's Kitchen",))
    seed = warm_start_from_case(case, bars, group)
    assert seed is not None
    assert len(seed) == len(case.solution_sequence)
    # All distinct bar ids
    assert len({b.id for b in seed}) == len(seed)


def test_similarity_components_named():
    cases = load_case_library()
    group = _group_for(size=2, budget=15, vibes={"intimate": 1.0})
    sim, breakdown = similarity(cases[0], group)
    assert 0.0 <= sim <= 1.0
    assert set(breakdown.keys()) == {"size", "budget", "neighborhood", "vibe"}
