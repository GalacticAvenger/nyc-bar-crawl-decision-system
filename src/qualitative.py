"""Qualitative arithmetic layer — numbers in, labels out.

Every label used in explanations is produced here. Rules in rules.yaml
supply the thresholds; this module just applies them.
"""

from __future__ import annotations

from typing import Literal

from .models import Bar


PRICE_TIERS = ("cheap", "moderate", "premium", "splurge")
NOISE_LEVELS = ("library", "conversation", "lively", "loud", "deafening")
DISTANCE_BUCKETS = ("next-door", "short-walk", "walk", "hike", "transit-worthy")
CROWD_LEVELS = ("dead", "mellow", "lively", "packed", "overflowing")
QUALITY_BUCKETS = ("weak_signal", "moderate_signal", "strong_signal", "consensus_pick")


def price_tier(avg_drink_price: float, rules: dict) -> str:
    """Map dollars → qualitative tier using rules['qualitative_thresholds']['price_tier']."""
    thresholds = rules["qualitative_thresholds"]["price_tier"]
    # cheap: max=8 → price < 8
    if avg_drink_price < thresholds["cheap"]["max"]:
        return "cheap"
    if avg_drink_price < thresholds["moderate"]["max"]:
        return "moderate"
    if avg_drink_price < thresholds["premium"]["max"]:
        return "premium"
    return "splurge"


def distance_bucket(miles: float, rules: dict) -> str:
    buckets = rules["qualitative_thresholds"]["distance_bucket"]
    if miles < buckets["next-door"]["max"]:
        return "next-door"
    if miles < buckets["short-walk"]["max"]:
        return "short-walk"
    if miles < buckets["walk"]["max"]:
        return "walk"
    if miles < buckets["hike"]["max"]:
        return "hike"
    return "transit-worthy"


def quality_bucket(quality_signal: float, rules: dict) -> str:
    buckets = rules["qualitative_thresholds"]["quality_signal"]
    if quality_signal < buckets["weak_signal"]["max"]:
        return "weak_signal"
    if quality_signal < buckets["moderate_signal"]["max"]:
        return "moderate_signal"
    if quality_signal < buckets["strong_signal"]["max"]:
        return "strong_signal"
    return "consensus_pick"


def crowd_at(bar: Bar, hour: int) -> str:
    """Look up crowd_level_by_hour at hour; default 'mellow' if unknown."""
    return bar.crowd_level_by_hour.get(str(hour), "mellow")


def noise_label_phrase(noise_level: str, rules: dict) -> str:
    descriptions = rules["qualitative_thresholds"]["noise_level"]
    return descriptions.get(noise_level, noise_level)


def qualify(bar: Bar, hour: int, rules: dict) -> dict[str, str]:
    """Full qualitative profile of a bar at a given hour — used in explanations."""
    return {
        "price_tier": bar.price_tier,  # authoritative: set during enrichment
        "noise_level": bar.noise_level,
        "crowd": crowd_at(bar, hour),
        "quality": quality_bucket(bar.quality_signal, rules),
    }


# Convenience — natural-language phrasing
PRICE_PHRASES = {
    "cheap":    "cheap",
    "moderate": "mid-priced",
    "premium":  "premium",
    "splurge":  "splurge-tier",
}

NOISE_PHRASES = {
    "library":      "library-quiet",
    "conversation": "conversational",
    "lively":       "lively",
    "loud":         "loud",
    "deafening":    "shout-required",
}

CROWD_PHRASES = {
    "dead":         "empty",
    "mellow":       "mellow",
    "lively":       "buzzy",
    "packed":       "packed",
    "overflowing":  "overflowing",
}

QUALITY_PHRASES = {
    "weak_signal":      "thin review base",
    "moderate_signal":  "solid consensus",
    "strong_signal":    "strong consensus",
    "consensus_pick":   "overwhelming consensus",
}


def phrase_for(bucket: str, kind: str) -> str:
    """Natural-language phrasing for a qualitative bucket."""
    table = {
        "price": PRICE_PHRASES,
        "noise": NOISE_PHRASES,
        "crowd": CROWD_PHRASES,
        "quality": QUALITY_PHRASES,
    }[kind]
    return table.get(bucket, bucket)
