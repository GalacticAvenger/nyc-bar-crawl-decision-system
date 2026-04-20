"""Streamlit UI for the bar-crawl decision system.

Design choice (v3): the vibe of the night is ONE selector for the whole group,
not a 45-tag multi-select per person. Each "night style" encodes an *arc* —
how the vibes ramp across the crawl (warm-up → peak → close). A bar scores
well if it fits ANY point in the arc; the open-hours filter naturally pushes
late-peak bars to the end of the route.

Per-person preferences only cover the stuff that genuinely differs between
people: name, budget, preferred drinks, noise tolerance. A single button
("Everyone's on the same page") mirrors Person 1's settings to the rest.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_loader import load_all  # noqa: E402
from src.decision_system import plan_crawl  # noqa: E402
from src.models import GroupInput, UserPreference  # noqa: E402
from src.visualize import render_map, render_timeline  # noqa: E402


# ---------------------------------------------------------------------------
# NIGHT STYLES — each has an arc (stop-by-stop vibes) + practical defaults.
# ---------------------------------------------------------------------------

NIGHT_STYLES = {
    "Chill bar hop": {
        "arc": [
            ("Warm-up",  {"conversation": 1.0, "unpretentious": 1.0, "post-work": 0.6, "walk-in": 0.6}),
            ("Middle",   {"lively": 1.0, "unpretentious": 0.8, "conversation": 0.6, "hidden-gem": 0.5}),
            ("Nightcap", {"cozy": 1.0, "dim": 0.8, "intimate": 0.7, "nightcap": 0.9}),
        ],
        "max_stops": 3, "start": time(20, 0), "end": time(1, 0),
        "noise": "lively", "drinks": ["cocktails", "beer"],
        "tagline": "Walkable, unpretentious, no cover. Conversation over volume.",
    },
    "Pregame → clubs": {
        "arc": [
            ("Warm-up",  {"pregame": 1.0, "lively": 1.0, "unpretentious": 0.8, "large-groups": 0.6}),
            ("Energy",   {"music-loud": 1.0, "lively": 1.0, "dj-set": 0.8, "crowd-loud": 0.7}),
            ("Peak",     {"dance-floor": 1.0, "dj-set": 1.0, "music-loud": 1.0, "late-close": 0.9}),
        ],
        "max_stops": 3, "start": time(21, 0), "end": time(3, 30),
        "noise": "loud", "drinks": ["cocktails", "shots", "beer"],
        "tagline": "Ramp from chatty opener to dance floor. Closes at 3–4am.",
    },
    "Dive bar tour": {
        "arc": [
            ("Warm-up",  {"divey": 1.0, "unpretentious": 1.0, "local-institution": 0.7, "historic": 0.6}),
            ("Middle",   {"divey": 1.0, "lively": 0.8, "games": 0.6, "crowd-loud": 0.5}),
            ("Nightcap", {"divey": 1.0, "cozy": 0.6, "late-close": 0.7}),
        ],
        "max_stops": 4, "start": time(20, 0), "end": time(1, 30),
        "noise": "lively", "drinks": ["beer", "shots"],
        "tagline": "Cheap, loud enough to be fun, old enough to have ghosts.",
    },
    "Date night": {
        "arc": [
            ("Opener",   {"intimate": 1.0, "conversation": 1.0, "polished": 0.8, "natural-wine": 0.4}),
            ("Main",     {"dim": 1.0, "intimate": 1.0, "craft-cocktails": 0.8, "cozy": 0.7}),
        ],
        "max_stops": 2, "start": time(19, 30), "end": time(23, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine"],
        "tagline": "Two rooms that let you talk. No surprises, no ejections.",
    },
    "Birthday party": {
        "arc": [
            ("Meetup",   {"large-groups": 1.0, "lively": 1.0, "pregame": 0.7, "birthday-party": 0.8}),
            ("Main",     {"birthday-party": 1.0, "large-groups": 1.0, "lively": 1.0, "crowd-loud": 0.7}),
            ("Peak",     {"dance-floor": 0.8, "music-loud": 0.9, "birthday-party": 1.0, "late-close": 0.7}),
        ],
        "max_stops": 3, "start": time(21, 0), "end": time(3, 0),
        "noise": "loud", "drinks": ["cocktails", "shots", "beer"],
        "tagline": "Venues that take reservations. Ends somewhere you can dance.",
    },
    "Post-dinner drinks": {
        "arc": [
            ("Main",     {"cozy": 1.0, "intimate": 0.8, "conversation": 0.9, "craft-cocktails": 0.7}),
            ("Nightcap", {"nightcap": 1.0, "dim": 0.8, "quiet": 0.6, "intimate": 0.7}),
        ],
        "max_stops": 2, "start": time(21, 30), "end": time(0, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine", "whiskey"],
        "tagline": "One proper cocktail and a nightcap. Short, honest, home by 1.",
    },
    "Late-night only": {
        "arc": [
            ("Arrive",   {"dim": 1.0, "lively": 1.0, "late-close": 1.0}),
            ("Peak",     {"dance-floor": 1.0, "music-loud": 1.0, "dj-set": 0.9, "late-close": 1.0}),
        ],
        "max_stops": 2, "start": time(23, 0), "end": time(3, 30),
        "noise": "loud", "drinks": ["cocktails", "shots"],
        "tagline": "For when the night starts where everyone else's ended.",
    },
    "Rooftop summer": {
        "arc": [
            ("Sunset",   {"rooftop": 1.0, "airy": 1.0, "polished": 0.7, "instagrammable": 0.8}),
            ("Main",     {"rooftop": 1.0, "polished": 0.8, "craft-cocktails": 0.7, "lively": 0.6}),
            ("Nightcap", {"intimate": 0.8, "rooftop": 0.6, "dim": 0.7}),
        ],
        "max_stops": 3, "start": time(19, 0), "end": time(0, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine"],
        "tagline": "Golden hour → skyline → something quieter. Warm-weather only.",
    },
    "Games night": {
        "arc": [
            ("Warm-up",  {"games": 1.0, "lively": 0.8, "unpretentious": 0.9, "large-groups": 0.6}),
            ("Main",     {"games": 1.0, "crowd-loud": 0.6, "large-groups": 0.7}),
        ],
        "max_stops": 2, "start": time(19, 30), "end": time(1, 0),
        "noise": "lively", "drinks": ["beer", "cocktails"],
        "tagline": "Pool, shuffleboard, darts, or board games. Less talking, more doing.",
    },
}


def _merge_arc_weights(arc: list[tuple[str, dict[str, float]]]) -> dict[str, float]:
    """Combine stop-by-stop vibe profiles into a single weighted profile the
    context-free scorer can use. Each stop contributes its own bump; the max
    across stops becomes the final weight (so ramping vibes don't get diluted
    when averaging)."""
    merged: dict[str, float] = {}
    for _role, weights in arc:
        for v, w in weights.items():
            merged[v] = max(merged.get(v, 0.0), w)
    return merged


def _next_friday() -> date:
    today = date.today()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _combine_with_next_day(d: date, start_t: time, end_t: time) -> tuple[datetime, datetime]:
    """If end_t ≤ start_t, the crawl crosses midnight — roll end to next day."""
    start_dt = datetime.combine(d, start_t)
    end_dt = datetime.combine(d, end_t)
    if end_t <= start_t:
        end_dt = datetime.combine(d + timedelta(days=1), end_t)
    return start_dt, end_dt


@st.cache_data
def _load():
    d = load_all()
    return d["bars"], d["cases"], d["rules"], d["vibe_vocab"]


def main():
    st.set_page_config(page_title="NYC Bar Crawl Planner",
                       page_icon="🍸", layout="wide")
    st.title("🍸 NYC Bar Crawl Planner")
    st.caption("Symbolic, explainable. 143 bars · 5 aggregation strategies · 20 archetypes.")

    bars, cases, rules, vocab = _load()

    # ===========================================================
    # 1. The night style — the SINGLE vibe control for the group.
    # ===========================================================
    st.sidebar.header("1. What kind of night?")
    style_name = st.sidebar.selectbox(
        "Pick a vibe for the night",
        list(NIGHT_STYLES.keys()),
        index=0,
    )
    style = NIGHT_STYLES[style_name]
    st.sidebar.caption(f"_{style['tagline']}_")

    # Show the arc so the ramp is visible
    with st.sidebar.expander(f"How it ramps — {len(style['arc'])} stages", expanded=True):
        for i, (role, weights) in enumerate(style["arc"], 1):
            top3 = sorted(weights.items(), key=lambda kv: -kv[1])[:3]
            top_tags = ", ".join(v for v, _ in top3)
            st.markdown(f"**Stop {i} · {role}** — {top_tags}")

    merged_vibes = _merge_arc_weights(style["arc"])

    # ===========================================================
    # 2. The group — everything per-person EXCEPT vibes.
    # ===========================================================
    with st.sidebar.form("plan_form", clear_on_submit=False):
        st.subheader("2. Group")
        num_users = st.slider("How many of you?", 1, 8, 2)

        # "Everyone's on the same page" — only meaningful for 2+
        same_prefs = False
        if num_users >= 2:
            same_prefs = st.checkbox(
                "Everyone's on the same page (copy Person 1 to everyone)",
                value=False,
                help="When the group is aligned on budget/drinks/noise, you only need to set Person 1.",
            )

        users = []
        for i in range(num_users):
            show_fields = (i == 0) or not same_prefs
            default_expanded = (i == 0)
            with st.expander(f"Person {i + 1}", expanded=default_expanded):
                name = st.text_input("Name", value=f"Friend {i + 1}", key=f"n{i}")
                if show_fields:
                    budget = st.slider("Max per drink ($)", 5, 40, 15, key=f"b{i}")
                    drinks = st.multiselect(
                        "Preferred drinks",
                        ["beer", "wine", "cocktails", "whiskey", "spirits", "shots"],
                        default=style["drinks"], key=f"d{i}",
                    )
                    noise = st.select_slider(
                        "Noise tolerance",
                        options=["library", "conversation", "lively", "loud", "deafening"],
                        value=style["noise"], key=f"noise{i}",
                    )
                else:
                    st.caption(
                        f"Inheriting Person 1's budget / drinks / noise. "
                        f"Uncheck the box above to customize."
                    )
                    budget = None
                    drinks = None
                    noise = None
                users.append({
                    "name": name, "budget": budget, "drinks": drinks, "noise": noise,
                })

        # If "same_prefs," fill downstream users from person 0
        if same_prefs and len(users) >= 2:
            p0 = users[0]
            for u in users[1:]:
                u["budget"] = p0["budget"]
                u["drinks"] = p0["drinks"]
                u["noise"] = p0["noise"]

        # Build UserPreference objects. Everyone gets the same night-style
        # vibe profile; individual prefs vary only on budget / drinks / noise.
        user_prefs = [
            UserPreference(
                name=u["name"],
                vibe_weights=dict(merged_vibes),
                max_per_drink=float(u["budget"]),
                preferred_drinks=tuple(u["drinks"]),
                preferred_noise=u["noise"],
            )
            for u in users
        ]

        # ===========================================================
        # 3. When & where.
        # ===========================================================
        st.subheader("3. When & where")
        d_val = st.date_input("Date", value=_next_friday())
        start_t = st.time_input("Start", value=style["start"])
        end_t = st.time_input(
            "End (rolls to next day if earlier than start)",
            value=style["end"],
        )
        max_stops = st.slider("Max stops", 1, 6, style["max_stops"])
        neighborhood_choices = sorted({b.neighborhood for b in bars})
        neighborhoods = st.multiselect(
            "Neighborhoods (empty = anywhere)",
            options=neighborhood_choices,
            default=["East Village", "Lower East Side"],
        )

        submitted = st.form_submit_button(
            "Plan crawl", type="primary", use_container_width=True,
        )

    # =====================================================================
    # Main pane
    # =====================================================================
    if not submitted:
        st.info(
            "Pick a night style on the left, set each person's budget + drinks, "
            "and hit **Plan crawl**. The night style controls the vibe arc — "
            "you don't need to pick individual vibes."
        )
        st.markdown("#### How to read the night-style arc")
        st.markdown(
            "Each style ramps across the night. A **Stop 1 · Warm-up** profile is what "
            "makes a bar good as the *first* stop; **Stop 3 · Peak** is what makes a bar "
            "good at the *end*. The planner picks bars that match any stage and "
            "sequences them by when they're actually open."
        )
        return

    start_dt, end_dt = _combine_with_next_day(d_val, start_t, end_t)
    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    st.caption(
        f"**{style_name}** — {start_dt.strftime('%a %-I:%M%p')} → "
        f"{end_dt.strftime('%a %-I:%M%p')} ({duration_hours:.1f} hours)"
    )

    group = GroupInput(
        users=user_prefs,
        start_time=start_dt, end_time=end_dt,
        max_stops=max_stops,
        neighborhoods=tuple(neighborhoods),
    )

    with st.spinner("Planning…"):
        result = plan_crawl(group, bars=bars, cases=cases, rules=rules)

    if not result.route.stops:
        st.error("No feasible crawl under these constraints.")
        st.markdown(result.explanations.summary)
        with st.expander(f"{len(result.excluded_bars)} bars excluded — why?"):
            for ex in result.excluded_bars[:40]:
                st.write("•", ex["reason"])
        return

    # -------- Results --------
    st.subheader("The plan")
    st.markdown(result.explanations.summary)

    col_map, col_timeline = st.columns([3, 2])
    with col_map:
        m = render_map(result.route, bars)
        from streamlit.components.v1 import html
        html(m._repr_html_(), height=500)
    with col_timeline:
        st.pyplot(render_timeline(result.route))

    st.subheader("Why each stop?")
    strategy_child = result.explanations.children[0]
    st.info(strategy_child.summary)
    for i, child in enumerate(
        result.explanations.children[1:1 + len(result.route.stops)], 1
    ):
        st.markdown(f"**Stop {i}.** {child.summary}")

    with st.expander("Counterfactuals — what would change if…"):
        cfs = [c for c in result.explanations.children if c.summary == "Counterfactuals"]
        if cfs:
            for sub in cfs[0].children:
                st.write("•", sub.summary)
        else:
            st.write("(none produced)")

    with st.expander("Per-person served-ness"):
        from src.explanation_engine import render_served_table
        st.markdown(render_served_table(result.per_user_report))

    with st.expander(f"{len(result.excluded_bars)} bars excluded"):
        for ex in result.excluded_bars[:30]:
            st.write("•", ex["reason"])


if __name__ == "__main__":
    main()
