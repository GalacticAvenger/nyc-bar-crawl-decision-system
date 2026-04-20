"""Tests for qualitative.py."""

from src.data_loader import load_bars, load_rules
from src.qualitative import (
    price_tier, distance_bucket, quality_bucket, crowd_at, qualify,
    phrase_for,
)


def test_price_tier_boundaries():
    rules = load_rules()
    assert price_tier(5.50, rules) == "cheap"
    assert price_tier(7.99, rules) == "cheap"
    assert price_tier(8.00, rules) == "moderate"
    assert price_tier(13.99, rules) == "moderate"
    assert price_tier(14.00, rules) == "premium"
    assert price_tier(19.99, rules) == "premium"
    assert price_tier(22.00, rules) == "splurge"


def test_distance_bucket_boundaries():
    rules = load_rules()
    assert distance_bucket(0.05, rules) == "next-door"
    assert distance_bucket(0.20, rules) == "short-walk"
    assert distance_bucket(0.40, rules) == "walk"
    assert distance_bucket(0.80, rules) == "hike"
    assert distance_bucket(1.50, rules) == "transit-worthy"


def test_quality_bucket_boundaries():
    rules = load_rules()
    assert quality_bucket(0.10, rules) == "weak_signal"
    assert quality_bucket(0.50, rules) == "moderate_signal"
    assert quality_bucket(0.75, rules) == "strong_signal"
    assert quality_bucket(0.95, rules) == "consensus_pick"


def test_crowd_at_uses_bar_hourly():
    bars = load_bars()
    b = bars[0]
    # bar.crowd_level_by_hour is a dict of str hour -> level
    for hour_str, level in b.crowd_level_by_hour.items():
        hour = int(hour_str)
        assert crowd_at(b, hour) == level


def test_qualify_returns_complete_profile():
    bars = load_bars()
    rules = load_rules()
    prof = qualify(bars[0], 21, rules)
    assert set(prof.keys()) == {"price_tier", "noise_level", "crowd", "quality"}


def test_phrase_for_known_bucket():
    assert phrase_for("cheap", "price") == "cheap"
    assert phrase_for("library", "noise") == "library-quiet"
    assert phrase_for("packed", "crowd") == "packed"
    assert phrase_for("consensus_pick", "quality") == "overwhelming consensus"
