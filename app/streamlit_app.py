"""Streamlit UI wrapping the decision system.

Run: streamlit run app/streamlit_app.py

Purpose: give a non-technical reviewer a way to poke at the system without
touching Python. Primary deliverable is still the Jupyter notebook.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_loader import load_all  # noqa: E402
from src.decision_system import plan_crawl  # noqa: E402
from src.models import AccessibilityNeeds, GroupInput, UserPreference  # noqa: E402
from src.visualize import render_map, render_timeline  # noqa: E402


@st.cache_data
def _load():
    d = load_all()
    return d["bars"], d["cases"], d["rules"], d["vibe_vocab"]


def main():
    st.set_page_config(page_title="NYC Bar Crawl Decision System",
                       page_icon="🍸", layout="wide")
    st.title("🍸 NYC Bar Crawl Decision System")
    st.caption(
        "Yale CS 4580/5580 — a symbolic, explainable planner. "
        "143 bars, 5 aggregation strategies, 20 case-based archetypes."
    )

    bars, cases, rules, vocab = _load()

    st.sidebar.header("Group")
    num_users = st.sidebar.slider("How many of you?", 1, 5, 3)
    users = []
    all_vibes = []
    for facet in vocab["facets"].values():
        all_vibes.extend(facet)

    for i in range(num_users):
        with st.sidebar.expander(f"User {i + 1}", expanded=(i == 0)):
            name = st.text_input(f"Name", value=f"Friend {i + 1}", key=f"n{i}")
            chosen_vibes = st.multiselect(f"Vibes", options=all_vibes,
                                            default=all_vibes[:2], key=f"v{i}")
            vibe_weights = {v: 1.0 for v in chosen_vibes}
            budget = st.slider("Max per drink ($)", 5, 40, 15, key=f"b{i}")
            drinks = st.multiselect("Preferred drinks",
                                     ["beer", "wine", "cocktails", "whiskey", "spirits"],
                                     default=["cocktails"], key=f"d{i}")
            users.append(UserPreference(
                name=name,
                vibe_weights=vibe_weights,
                max_per_drink=float(budget),
                preferred_drinks=tuple(drinks),
            ))

    st.sidebar.header("When & where")
    d = st.sidebar.date_input("Date", value=date(2026, 4, 24))
    start_t = st.sidebar.time_input("Start", value=time(19, 0))
    end_t = st.sidebar.time_input("End", value=time(23, 30))
    max_stops = st.sidebar.slider("Max stops", 1, 6, 3)
    neighborhood_choices = sorted({b.neighborhood for b in bars})
    neighborhoods = st.sidebar.multiselect("Neighborhoods (empty = anywhere)",
                                            options=neighborhood_choices,
                                            default=["East Village", "Lower East Side"])

    group = GroupInput(
        users=users,
        start_time=datetime.combine(d, start_t),
        end_time=datetime.combine(d, end_t),
        max_stops=max_stops,
        neighborhoods=tuple(neighborhoods),
    )

    if st.sidebar.button("Plan crawl", type="primary"):
        with st.spinner("Planning..."):
            result = plan_crawl(group, bars=bars, cases=cases, rules=rules)

        if not result.route.stops:
            st.error(result.explanations.summary)
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

        with st.expander("Counterfactuals"):
            cfs = [c for c in result.explanations.children if c.summary == "Counterfactuals"]
            if cfs:
                for sub in cfs[0].children:
                    st.write("•", sub.summary)

        with st.expander(f"{len(result.excluded_bars)} bars excluded"):
            for ex in result.excluded_bars[:25]:
                st.write(ex["reason"])


if __name__ == "__main__":
    main()
