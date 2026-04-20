"""Streamlit UI wrapping the decision system.

Run: streamlit run app/streamlit_app.py
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
# "Kind of night" presets — tuned for people in their 20s
# ---------------------------------------------------------------------------

NIGHT_PRESETS = {
    "Bar hop (chill)": {
        "vibes": ["lively", "unpretentious", "conversation", "hidden-gem"],
        "max_stops": 3,
        "start": time(20, 0),
        "end": time(1, 0),        # next day
        "noise": "lively",
        "drinks": ["cocktails", "beer"],
        "hint": "Start early, 3 bars, walk between them. No club.",
    },
    "Pregame → clubs": {
        "vibes": ["lively", "pregame", "dancing", "loud", "rowdy"],
        "max_stops": 3,
        "start": time(21, 0),
        "end": time(3, 30),
        "noise": "loud",
        "drinks": ["cocktails", "shots", "beer"],
        "hint": "Warm-up bar → energy bar → dance floor till close.",
    },
    "Dive bar tour": {
        "vibes": ["divey", "unpretentious", "local-institution", "historic"],
        "max_stops": 4,
        "start": time(20, 0),
        "end": time(1, 30),
        "noise": "lively",
        "drinks": ["beer", "shots"],
        "hint": "Cheap, walkable, no pretension. Classic EV/HK move.",
    },
    "Date night": {
        "vibes": ["intimate", "conversation", "polished", "dim", "cozy"],
        "max_stops": 2,
        "start": time(19, 30),
        "end": time(23, 30),
        "noise": "conversation",
        "drinks": ["cocktails", "wine"],
        "hint": "Two well-chosen spots. Room to talk, room to leave.",
    },
    "Birthday party": {
        "vibes": ["lively", "large-groups", "birthday-party", "themed"],
        "max_stops": 3,
        "start": time(21, 0),
        "end": time(3, 0),
        "noise": "loud",
        "drinks": ["cocktails", "shots", "beer"],
        "hint": "Venues that take reservations; ends at karaoke or a dance floor.",
    },
    "Post-dinner drinks": {
        "vibes": ["cozy", "intimate", "conversation", "dim"],
        "max_stops": 2,
        "start": time(21, 30),
        "end": time(0, 30),
        "noise": "conversation",
        "drinks": ["cocktails", "wine", "whiskey"],
        "hint": "One proper cocktail, maybe a nightcap.",
    },
    "Late-night only": {
        "vibes": ["dancing", "loud", "dim", "lively"],
        "max_stops": 2,
        "start": time(23, 0),
        "end": time(3, 30),
        "noise": "loud",
        "drinks": ["cocktails", "shots"],
        "hint": "For when the night starts where everyone else's ended.",
    },
    "Custom": {
        "vibes": ["lively", "conversation"],
        "max_stops": 3,
        "start": time(20, 0),
        "end": time(0, 30),
        "noise": "lively",
        "drinks": ["cocktails"],
        "hint": "Dial in everything yourself.",
    },
}


def _next_friday() -> date:
    """Default to the next upcoming Friday — 20-something default is a Fri night."""
    today = date.today()
    days_ahead = (4 - today.weekday()) % 7   # Mon=0, Fri=4
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _combine_with_next_day(d: date, start_t: time, end_t: time) -> tuple[datetime, datetime]:
    """If end_t ≤ start_t, treat it as next day. Any bar night past midnight needs this."""
    start_dt = datetime.combine(d, start_t)
    if end_t <= start_t:
        end_dt = datetime.combine(d + timedelta(days=1), end_t)
    else:
        end_dt = datetime.combine(d, end_t)
    return start_dt, end_dt


@st.cache_data
def _load():
    dd = load_all()
    return dd["bars"], dd["cases"], dd["rules"], dd["vibe_vocab"]


def main():
    st.set_page_config(page_title="NYC Bar Crawl Planner",
                       page_icon="🍸", layout="wide")
    st.title("🍸 NYC Bar Crawl Planner")
    st.caption("Symbolic, explainable. 143 bars · 5 aggregation strategies · 20 archetypes.")

    bars, cases, rules, vocab = _load()

    all_vibes = []
    for facet in vocab["facets"].values():
        all_vibes.extend(facet)

    # ----- Sidebar form (Enter submits) -----
    with st.sidebar:
        st.header("What kind of night?")
        preset_name = st.selectbox("Vibe preset", list(NIGHT_PRESETS.keys()), index=0)
        preset = NIGHT_PRESETS[preset_name]
        st.caption(preset["hint"])

        with st.form("plan_form", clear_on_submit=False):
            st.subheader("Group")
            num_users = st.slider("How many of you?", 1, 8, 3)
            users = []
            for i in range(num_users):
                with st.expander(f"Person {i + 1}", expanded=(i == 0)):
                    name = st.text_input("Name", value=f"Friend {i + 1}", key=f"n{i}")
                    chosen_vibes = st.multiselect(
                        "Vibes", options=all_vibes,
                        default=preset["vibes"], key=f"v{i}",
                    )
                    vibe_weights = {v: 1.0 for v in chosen_vibes}
                    budget = st.slider("Max per drink ($)", 5, 40, 15, key=f"b{i}")
                    drinks = st.multiselect(
                        "Preferred drinks",
                        ["beer", "wine", "cocktails", "whiskey", "spirits", "shots"],
                        default=preset["drinks"], key=f"d{i}",
                    )
                    noise = st.select_slider(
                        "Preferred noise",
                        options=["library", "conversation", "lively", "loud", "deafening"],
                        value=preset["noise"], key=f"noise{i}",
                    )
                    users.append(UserPreference(
                        name=name, vibe_weights=vibe_weights,
                        max_per_drink=float(budget),
                        preferred_drinks=tuple(drinks),
                        preferred_noise=noise,
                    ))

            st.subheader("When & where")
            d = st.date_input("Date", value=_next_friday())
            start_t = st.time_input("Start", value=preset["start"])
            end_t = st.time_input("End (rolls to next day if earlier than start)",
                                   value=preset["end"])
            max_stops = st.slider("Max stops", 1, 6, preset["max_stops"])
            neighborhood_choices = sorted({b.neighborhood for b in bars})
            neighborhoods = st.multiselect(
                "Neighborhoods (empty = anywhere)",
                options=neighborhood_choices,
                default=["East Village", "Lower East Side"],
            )

            submitted = st.form_submit_button("Plan crawl", type="primary",
                                               use_container_width=True)

    # ----- Main pane -----
    if not submitted:
        st.info(
            "Set the group on the left and hit **Plan crawl** (or press Enter in any field). "
            "Pick a *Vibe preset* up top to fill in sensible defaults — **Pregame → clubs**, "
            "**Date night**, **Birthday party**, and so on."
        )
        return

    start_dt, end_dt = _combine_with_next_day(d, start_t, end_t)
    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    st.caption(
        f"Window: **{start_dt.strftime('%a %-I:%M%p')} → {end_dt.strftime('%a %-I:%M%p')}** "
        f"({duration_hours:.1f} hours)"
    )

    group = GroupInput(
        users=users,
        start_time=start_dt, end_time=end_dt,
        max_stops=max_stops,
        neighborhoods=tuple(neighborhoods),
    )

    with st.spinner("Planning…"):
        result = plan_crawl(group, bars=bars, cases=cases, rules=rules)

    if not result.route.stops:
        st.error("No feasible crawl.")
        st.markdown(result.explanations.summary)
        with st.expander(f"{len(result.excluded_bars)} bars excluded — why?"):
            for ex in result.excluded_bars[:40]:
                st.write("•", ex["reason"])
        return

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
