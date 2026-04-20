"""Tests for data_loader.py."""
import pytest

from src.data_loader import load_bars, load_case_library, load_rules, load_vibe_vocab, load_all


def test_load_bars_count():
    bars = load_bars()
    assert len(bars) == 143, "expected 143 enriched bars"


def test_bars_have_required_fields():
    bars = load_bars()
    for b in bars[:5]:
        assert b.id.startswith("bar_")
        assert b.seed_id.startswith("seed_")
        assert len(b.vibe_tags) >= 3
        assert b.price_tier in ("cheap", "moderate", "premium", "splurge")
        assert b.noise_level in ("library", "conversation", "lively", "loud", "deafening")
        assert 40.49 <= b.lat <= 40.92, f"{b.name} lat out of NYC bounds"
        assert -74.26 <= b.lon <= -73.70, f"{b.name} lon out of NYC bounds"


def test_user_notes_preserved():
    bars = load_bars()
    with_notes = [b for b in bars if b.user_note]
    assert len(with_notes) == 6, f"expected 6 user_note entries, got {len(with_notes)}"


def test_burp_castle_is_whispering():
    bars = load_bars()
    burp = next(b for b in bars if "Burp Castle" in b.name)
    assert burp.noise_level == "library"
    assert "quiet" in burp.vibe_tags


def test_load_case_library():
    cases = load_case_library()
    assert len(cases) == 20
    assert cases[0].id.startswith("case_")
    assert cases[0].solution_sequence


def test_load_rules_structure():
    rules = load_rules()
    assert "qualitative_thresholds" in rules
    assert "scoring_defaults" in rules
    assert "group_strategy_rules" in rules
    assert rules["scoring_defaults"]["default_weights"]["vibe"] > 0


def test_load_vibe_vocab():
    vocab = load_vibe_vocab()
    expected = vocab["_total_vibes"]
    all_vibes = set()
    for facet in vocab["facets"].values():
        all_vibes.update(facet)
    assert len(all_vibes) == expected
    # v2 — spot-check a few new vibes are present
    assert "craft-cocktails" in all_vibes
    assert "dance-floor" in all_vibes
    assert "queer-centered" in all_vibes
    # v2 — the deletions really are gone
    assert "lgbtq-friendly" not in all_vibes
    assert "dancing" not in all_vibes
    assert "rowdy" not in all_vibes


def test_load_all():
    d = load_all()
    assert d["bars"] and d["cases"] and d["rules"] and d["vibe_vocab"]
