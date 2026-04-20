"""Tests for scoring.py."""

from src.data_loader import load_bars, load_rules
from src.models import UserPreference
from src.scoring import (
    score_bar_for_user, normalize_weights, pareto_filter,
    score_vibe, score_budget, score_drink_match, score_noise,
    CRITERIA,
)


def test_normalize_weights_sums_to_one():
    w = normalize_weights({"vibe": 0.5, "budget": 0.3})
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_normalize_weights_handles_negatives():
    w = normalize_weights({"vibe": -0.5, "budget": 1.0})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["vibe"] == 0.0


def test_normalize_weights_all_zero_returns_uniform():
    w = normalize_weights({})
    expected = 1.0 / len(CRITERIA)
    for c in CRITERIA:
        assert abs(w[c] - expected) < 1e-9


def test_vibe_cosine_favors_matching_tags():
    bars = load_bars()
    rules = load_rules()
    intimate_bar = next(b for b in bars if "intimate" in b.vibe_tags)
    rowdy_bar = next(b for b in bars if "rowdy" in b.vibe_tags and "intimate" not in b.vibe_tags)
    user = UserPreference(name="X", vibe_weights={"intimate": 1.0})
    intimate_score = score_vibe(intimate_bar, user, rules)
    rowdy_score = score_vibe(rowdy_bar, user, rules)
    assert intimate_score > rowdy_score


def test_budget_penalty_decays_above_cap():
    bars = load_bars()
    user = UserPreference(name="X", max_per_drink=10.0)
    cheap = next(b for b in bars if b.avg_drink_price <= 8)
    splurge = next(b for b in bars if b.avg_drink_price >= 20)
    assert score_budget(cheap, user) >= 0.95   # below cap → full credit
    assert score_budget(splurge, user) < 0.6   # above cap → real penalty


def test_drink_match_jaccard():
    bars = load_bars()
    user = UserPreference(name="X", preferred_drinks=("beer", "whiskey"))
    # a pub serves both
    pub = next(b for b in bars if "pub" in b.bar_type or "irish_pub" in b.bar_type)
    # a nightclub doesn't
    club = next(b for b in bars if "nightclub" in b.bar_type)
    assert score_drink_match(pub, user) > score_drink_match(club, user)


def test_score_bar_for_user_produces_full_trace():
    bars = load_bars()
    rules = load_rules()
    user = UserPreference(name="Alice", vibe_weights={"intimate": 1.0, "conversation": 0.8})
    s = score_bar_for_user(bars[0], user, rules)
    assert set(s.per_criterion.keys()) == set(CRITERIA)
    assert set(s.weighted_contributions.keys()) == set(CRITERIA)
    # total equals sum of weighted contributions
    assert abs(s.total - sum(s.weighted_contributions.values())) < 1e-9


def test_pareto_filter_removes_dominated():
    """Construct 3 scores where one is strictly dominated on every criterion."""
    from src.models import Score
    a = Score(bar_id="a", user_id="u", per_criterion={c: 0.9 for c in CRITERIA},
              weighted_contributions={}, total=0.9)
    b = Score(bar_id="b", user_id="u", per_criterion={c: 0.5 for c in CRITERIA},
              weighted_contributions={}, total=0.5)
    c_unique = Score(bar_id="c", user_id="u",
                     per_criterion={**{c: 0.3 for c in CRITERIA}, "vibe": 0.99},
                     weighted_contributions={}, total=0.35)
    kept, dominated = pareto_filter([a, b, c_unique])
    kept_ids = {s.bar_id for s in kept}
    # a dominates b on all axes; b should be dropped
    # c beats a on vibe, so c survives
    assert "a" in kept_ids
    assert "b" not in kept_ids
    assert "c" in kept_ids
    # The dominated pair is (b, a)
    losers = [pair[0].bar_id for pair in dominated]
    winners = [pair[1].bar_id for pair in dominated]
    assert "b" in losers
    assert "a" in winners


def test_happy_hour_scoring_time_sensitive():
    from src.scoring import score_happy_hour
    bars = load_bars()
    # Find a bar with a happy hour window
    bar = next(b for b in bars if b.happy_hour_windows)
    w = bar.happy_hour_windows[0]
    inside_hour = int(w.start.split(":")[0])
    outside_hour = (inside_hour + 6) % 24
    day_in = w.days[0]
    day_out = "xxx"  # nonexistent
    assert score_happy_hour(bar, inside_hour, day_in) == 1.0
    assert score_happy_hour(bar, outside_hour, day_in) == 0.0
    assert score_happy_hour(bar, inside_hour, day_out) == 0.0
    assert score_happy_hour(bar, None, None) == 0.0
