# NYC Bar Crawl Decision System

**Yale CS 4580/5580 (Decision Systems) — Final Project**

A symbolic, explainable decision system that plans optimized NYC bar crawls for groups with competing preferences. Given a set of users (each with their own vibes, budget, and dealbreakers) and hard constraints (start/end time, neighborhoods, accessibility), the system produces a sequenced, time-stamped route plus rich natural-language explanations for every decision.

## Five academic techniques, working together

1. **Multi-Criteria Decision Analysis (MCDA)** with weighted utility + Pareto filtering.
2. **Qualitative reasoning** — cheap/moderate/premium; library/conversation/lively/loud/deafening.
3. **VOTE-style group preference aggregation** with five strategies (utilitarian, egalitarian, Borda, Copeland, approval/veto) and a **meta-selector** that picks a strategy based on the group's disagreement profile.
4. **Case-Based Reasoning (CBR)** over 20 crawl archetypes.
5. **Counterfactual & option generation** — runner-ups, unlock analyses, structural counterfactuals ("+30 min", "+$10", "−1 vetoer").

No neural nets, no ML training, no runtime API calls. Every decision is symbolic and traceable.

## Quickstart

```bash
pip install -r requirements.txt

# One-time: enrich the seed dataset into data/bars.json
python scripts/enrich_bars.py

# Primary deliverable — reproducible demo notebook
jupyter notebook notebooks/demo.ipynb

# Secondary — polished Streamlit UI
streamlit run app/streamlit_app.py

# Tests
make test
```

## Repo layout

```
data/           seed_bars.json, vibe_vocab.json, rules.yaml, case_library.json,
                category_to_vibes.yaml, default_hours.yaml, nyc_neighborhoods.geojson,
                bars.json (produced by enrich_bars.py)
schemas/        bar.schema.json
src/            Core modules (models, qualitative, scoring, temporal, routing,
                group_aggregation, case_based, option_generation, explanation_engine,
                visualize, decision_system)
tests/          pytest-discoverable unit + integration tests
scripts/        parse_seed.py, enrich_bars.py, neighborhood_audit.py
notebooks/      demo.ipynb (canonical entry point)
app/            streamlit_app.py (optional UI)
docs/           WRITEUP.md, BUILD_LOG.md, screenshots/
```

## Dataset honesty note

The 143-bar dataset begins with the author's personal Google Maps bar list. **Bar names, Google ratings, and review counts are real.** Happy hours, specials, exact open hours, and noise/crowd estimates are plausible category-based inferences — not authoritative. See `docs/WRITEUP.md` §Limitations and the disclaimer header at the top of `data/bars.json`.

## The thesis

This is a **decision system**, not a recommender. A recommender returns a ranked list. A decision system explains every choice, surfaces every trade-off, and can answer "why not Y instead?" before you ask. The intelligence lives in the explanation, not the score.
