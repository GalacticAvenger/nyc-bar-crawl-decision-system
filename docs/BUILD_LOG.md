# Build Log

Running notes on what was built, what was tricky, and what got deferred. Cited from the writeup.

## Phase 0 — Repo setup (complete)

- Scaffolded `data/`, `schemas/`, `src/`, `tests/`, `scripts/`, `notebooks/`, `app/`, `docs/`.
- Wrote `requirements.txt`, `Makefile`, `.gitignore`, `LICENSE`, `README.md`.
- Wrote the small data/config files: `vibe_vocab.json`, `rules.yaml`, `category_to_vibes.yaml`, `default_hours.yaml`, `case_library.json`, `schemas/bar.schema.json`.
- Ran `scripts/parse_seed.py` → produced `data/seed_bars.json` (159 raw entries, 143 included, 13 editorial exclusions, 3 permanently closed, 6 user notes preserved).

## Phase 1 — Dataset enrichment (planned)

Enrichment is fully deterministic, driven by a single script `scripts/enrich_bars.py`. Steps:
- Geocoding is handled from a hand-curated address table (no network calls). Ambiguous names like "Barcade" (two locations) resolve to distinct addresses.
- Neighborhood is inferred from coordinates using `data/nyc_neighborhoods.geojson` (simplified polygons per neighborhood).
- Vibes come from `category_to_vibes.yaml` defaults, then `primary_function_overrides`, then `user_note_overrides` — in that order, with later layers overriding earlier ones.
- Hours, happy hours, specials come from `default_hours.yaml` by category. The dataset disclaimer header calls this out.
- `quality_signal` is computed post-hoc across the dataset, normalized to `[0, 1]`.
