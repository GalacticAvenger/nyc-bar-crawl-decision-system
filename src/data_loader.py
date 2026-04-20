"""Load and validate data files into strongly-typed models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as e:
    raise ImportError("PyYAML required: pip install pyyaml") from e

from .models import Bar, Case, TemporalWindow


DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_SCHEMA_DIR = Path(__file__).parent.parent / "schemas"


def load_rules(path: Path | None = None) -> dict:
    path = path or (DEFAULT_DATA_DIR / "rules.yaml")
    return yaml.safe_load(path.read_text())


def load_vibe_vocab(path: Path | None = None) -> dict:
    path = path or (DEFAULT_DATA_DIR / "vibe_vocab.json")
    return json.loads(path.read_text())


def _dict_to_window(d: dict) -> TemporalWindow:
    return TemporalWindow(
        days=tuple(d["days"]),
        start=d["start"],
        end=d["end"],
        kind=d["kind"],
        details=d.get("details", ""),
        bonus=d.get("bonus", 0.0),
    )


def _dict_to_bar(d: dict) -> Bar:
    return Bar(
        id=d["id"],
        seed_id=d["seed_id"],
        name=d["name"],
        neighborhood=d["neighborhood"],
        address=d["address"],
        lat=d["lat"],
        lon=d["lon"],
        bar_type=tuple(d["bar_type"]),
        vibe_tags=tuple(d["vibe_tags"]),
        price_tier=d["price_tier"],
        avg_drink_price=d["avg_drink_price"],
        drink_specialties=tuple(d.get("drink_specialties", [])),
        drink_categories_served=tuple(d["drink_categories_served"]),
        noise_level=d["noise_level"],
        capacity_estimate=d["capacity_estimate"],
        crowd_level_by_hour=dict(d["crowd_level_by_hour"]),
        outdoor_seating=d.get("outdoor_seating"),
        food_quality=d.get("food_quality"),
        kitchen_open=d.get("kitchen_open"),
        happy_hour_windows=tuple(_dict_to_window(w) for w in d.get("happy_hour_windows", [])),
        specials=tuple(_dict_to_window(w) for w in d.get("specials", [])),
        open_hours=dict(d["open_hours"]),
        age_policy=d["age_policy"],
        accessibility=dict(d["accessibility"]),
        reservations=d.get("reservations", "unknown"),
        dress_code=d.get("dress_code", "unknown"),
        novelty=d.get("novelty", 0.5),
        description=d.get("description"),
        good_for=tuple(d.get("good_for", [])),
        avoid_for=tuple(d.get("avoid_for", [])),
        google_rating=d["google_rating"],
        google_review_count=d["google_review_count"],
        google_price_indicator=d.get("google_price_indicator"),
        google_category=d.get("google_category"),
        quality_signal=d["quality_signal"],
        user_note=d.get("user_note"),
        primary_function=d.get("primary_function"),
        editorial_note=d.get("editorial_note"),
        source=d["source"],
    )


def load_bars(path: Path | None = None, validate: bool = True) -> list[Bar]:
    """Load bars.json. If `validate`, also check against the JSON schema."""
    path = path or (DEFAULT_DATA_DIR / "bars.json")
    raw = json.loads(path.read_text())
    if validate:
        try:
            from jsonschema import Draft7Validator
            schema = json.loads((DEFAULT_SCHEMA_DIR / "bar.schema.json").read_text())
            validator = Draft7Validator(schema)
            errors = []
            for entry in raw:
                for err in validator.iter_errors(entry):
                    errors.append(f"{entry.get('id', '?')}: {err.message}")
            if errors:
                raise ValueError(
                    f"Schema validation failed on {len(errors)} bar(s):\n  "
                    + "\n  ".join(errors[:5])
                )
        except ImportError:
            # jsonschema not installed — fall through with a warning
            pass
    return [_dict_to_bar(d) for d in raw]


def load_case_library(path: Path | None = None) -> list[Case]:
    path = path or (DEFAULT_DATA_DIR / "case_library.json")
    raw = json.loads(path.read_text())
    cases = []
    for d in raw["cases"]:
        cases.append(Case(
            id=d["id"],
            name=d["name"],
            group_profile=d["group_profile"],
            context=d["context"],
            solution_sequence=d["solution_sequence"],
            success_narrative=d["success_narrative"],
            fails_when=d.get("fails_when", []),
            example_bars_in_dataset=d.get("example_bars_in_dataset", []),
        ))
    return cases


def load_all(data_dir: Path | None = None) -> dict[str, Any]:
    """Convenience — load everything in one call."""
    return {
        "bars": load_bars(),
        "cases": load_case_library(),
        "rules": load_rules(),
        "vibe_vocab": load_vibe_vocab(),
    }
