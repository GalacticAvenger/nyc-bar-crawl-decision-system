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
from src.dialogic import replan_with_reactions  # noqa: E402
from src.models import GroupInput, Reaction, UserPreference  # noqa: E402
from src.visualize import render_map, render_timeline  # noqa: E402


# ---------------------------------------------------------------------------
# NIGHT STYLES — each has an arc (stop-by-stop vibes) + practical defaults.
# ---------------------------------------------------------------------------

NIGHT_STYLES = {
    # Each stage's weights emphasize DISTINGUISHING vibes (games, dance-floor,
    # divey, rooftop, craft-cocktails, natural-wine, tiki...) over generic ones
    # (lively, unpretentious) — since the generics match ~70% of the dataset and
    # drown the differentiating signal in cosine similarity.
    "Chill bar hop": {
        "arc": [
            ("Warm-up",  {"post-work": 1.2, "conversation": 0.8, "walk-in": 0.6}),
            ("Middle",   {"hidden-gem": 1.0, "unpretentious": 0.7, "local-institution": 0.7}),
            ("Nightcap", {"nightcap": 1.3, "cozy": 1.0, "intimate": 0.6}),
        ],
        "max_stops": 3, "start": time(20, 0), "end": time(1, 0),
        "noise": "lively", "drinks": ["cocktails", "beer"], "walking_only": True,
        "tagline": "Walkable, unpretentious, no cover. Conversation over volume.",
    },
    "Pregame → clubs": {
        "arc": [
            ("Warm-up",  {"pregame": 1.5, "unpretentious": 0.5, "large-groups": 0.5}),
            ("Energy",   {"music-loud": 1.2, "crowd-loud": 1.0, "dj-set": 0.8}),
            ("Peak",     {"dance-floor": 1.8, "dj-set": 1.2, "late-close": 1.0}),
        ],
        "max_stops": 3, "start": time(21, 0), "end": time(3, 30),
        "noise": "loud", "drinks": ["cocktails", "shots", "beer"], "walking_only": False,
        "tagline": "Ramp from chatty opener to dance floor. Closes at 3–4am. Uber to the club is assumed.",
    },
    "Dive bar tour": {
        "arc": [
            ("Warm-up",  {"divey": 1.5, "local-institution": 1.0, "historic": 0.8}),
            ("Middle",   {"divey": 1.5, "games": 0.8, "unpretentious": 0.6}),
            ("Nightcap", {"divey": 1.3, "late-close": 1.0, "cozy": 0.5}),
        ],
        "max_stops": 4, "start": time(20, 0), "end": time(1, 30),
        "noise": "lively", "drinks": ["beer", "shots"], "walking_only": True,
        "tagline": "Cheap, loud enough to be fun, old enough to have ghosts.",
    },
    "Date night": {
        "arc": [
            ("Opener",   {"date": 1.5, "intimate": 1.0, "natural-wine": 0.8, "craft-cocktails": 0.6}),
            ("Main",     {"date": 1.3, "craft-cocktails": 1.2, "dim": 0.9, "intimate": 0.8}),
        ],
        "max_stops": 2, "start": time(19, 30), "end": time(23, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine"], "walking_only": True,
        "tagline": "Two rooms that let you talk. No surprises, no ejections.",
    },
    "Birthday party": {
        "arc": [
            ("Meetup",   {"large-groups": 1.5, "pregame": 0.8, "birthday-party": 0.8}),
            ("Main",     {"birthday-party": 1.5, "large-groups": 1.0, "crowd-loud": 0.8}),
            ("Peak",     {"dance-floor": 1.2, "birthday-party": 1.0, "music-loud": 0.9}),
        ],
        "max_stops": 3, "start": time(21, 0), "end": time(3, 0),
        "noise": "loud", "drinks": ["cocktails", "shots", "beer"], "walking_only": False,
        "tagline": "Venues that take reservations. Ends somewhere you can dance.",
    },
    "Post-dinner drinks": {
        "arc": [
            ("Main",     {"craft-cocktails": 1.3, "intimate": 1.0, "dim": 0.8}),
            ("Nightcap", {"nightcap": 1.5, "cozy": 1.0, "quiet": 0.7}),
        ],
        "max_stops": 2, "start": time(21, 30), "end": time(0, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine", "whiskey"], "walking_only": True,
        "tagline": "One proper cocktail and a nightcap. Short, honest, home by 1.",
    },
    "Late-night only": {
        "arc": [
            ("Arrive",   {"late-close": 1.5, "dim": 0.8, "divey": 0.6}),
            ("Peak",     {"dance-floor": 1.5, "dj-set": 1.2, "late-close": 1.0, "music-loud": 0.8}),
        ],
        "max_stops": 2, "start": time(23, 0), "end": time(3, 30),
        "noise": "loud", "drinks": ["cocktails", "shots"], "walking_only": False,
        "tagline": "For when the night starts where everyone else's ended.",
    },
    "Rooftop summer": {
        "arc": [
            ("Sunset",   {"rooftop": 2.0, "airy": 1.0, "instagrammable": 0.8}),
            ("Main",     {"rooftop": 1.8, "craft-cocktails": 0.8, "polished": 0.6}),
            ("Nightcap", {"craft-cocktails": 1.0, "dim": 0.8, "intimate": 0.7, "hidden-gem": 0.5}),
        ],
        "max_stops": 3, "start": time(19, 0), "end": time(0, 30),
        "noise": "conversation", "drinks": ["cocktails", "wine"], "walking_only": False,
        "tagline": "Golden hour → skyline → something quieter. Warm-weather only.",
    },
    "Games night": {
        "arc": [
            ("Warm-up",  {"games": 2.0, "unpretentious": 0.5, "large-groups": 0.4}),
            ("Main",     {"games": 2.0, "crowd-loud": 0.5, "large-groups": 0.4}),
        ],
        "max_stops": 2, "start": time(19, 30), "end": time(1, 0),
        "noise": "lively", "drinks": ["beer", "cocktails"], "walking_only": True,
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


# Curated per-person "must-haves" — 10 high-signal options that layer on top
# of the night style. Each UI label maps to one or more vibe_vocab entries.
# Chosen because they're (a) commonly understood by a 20s audience and
# (b) strongly differentiating in the dataset.
PERSONAL_MUST_HAVES = {
    "🍸 Craft cocktails":    ["craft-cocktails"],
    "💃 Dance floor":        ["dance-floor", "dj-set"],
    "🎸 Live music":         ["live-band"],
    "🌇 Rooftop":            ["rooftop", "airy"],
    "🎱 Games / pool":       ["games"],
    "🍺 Divey":              ["divey", "unpretentious"],
    "🕯️ Intimate & dim":     ["intimate", "dim", "cozy"],
    "🏳️‍🌈 Queer-centered":   ["queer-centered"],
    "💎 Hidden gem":         ["hidden-gem"],
    "📜 Historic / local":   ["local-institution", "historic"],
}


def _personal_vibe_weights(selected_labels: list[str]) -> dict[str, float]:
    """Convert UI labels into a flat vibe_weights dict (weight 1.0 per vibe)."""
    out: dict[str, float] = {}
    for label in selected_labels:
        for v in PERSONAL_MUST_HAVES.get(label, []):
            out[v] = max(out.get(v, 0.0), 1.0)
    return out


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
    # The group-size slider and "same page" checkbox live OUTSIDE the form.
    # Widgets inside a Streamlit form only re-render on submit; these two
    # control how many Person expanders are drawn, so they need to
    # re-render IMMEDIATELY when the user moves them.
    st.sidebar.subheader("2. Group")
    num_users = st.sidebar.slider("How many of you?", 1, 8, 2, key="num_users")
    same_prefs = False
    if num_users >= 2:
        same_prefs = st.sidebar.checkbox(
            "Everyone's on the same page (copy Person 1 to everyone)",
            value=False, key="same_prefs",
            help="When the group is aligned on budget/drinks/noise, you only need to set Person 1.",
        )

    with st.sidebar.form("plan_form", clear_on_submit=False):
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
                    must_haves = st.multiselect(
                        "Personal must-haves (optional — layers on top of the night style)",
                        options=list(PERSONAL_MUST_HAVES.keys()),
                        default=[], key=f"mh{i}",
                        help=(
                            "These add to the night style for just you. If you pick "
                            "'Dance floor', bars with a dance floor get a boost in your "
                            "personal utility — even on a Chill bar hop night."
                        ),
                    )
                else:
                    st.caption(
                        f"Inheriting Person 1's budget / drinks / noise / must-haves. "
                        f"Uncheck the box above to customize."
                    )
                    budget = None
                    drinks = None
                    noise = None
                    must_haves = None
                users.append({
                    "name": name, "budget": budget, "drinks": drinks, "noise": noise,
                    "must_haves": must_haves,
                })

        # If "same_prefs," fill downstream users from person 0
        if same_prefs and len(users) >= 2:
            p0 = users[0]
            for u in users[1:]:
                u["budget"] = p0["budget"]
                u["drinks"] = p0["drinks"]
                u["noise"] = p0["noise"]
                u["must_haves"] = p0["must_haves"]

        # Build UserPreference objects. Everyone shares the night-style arc
        # (set via GroupInput.arc_profile); individuals layer their own
        # "must-haves" on top via user.vibe_weights. When a style is picked,
        # the vibe CRITERION weight jumps from 0.30 → 0.50 so vibes actually
        # drive the decision.
        style_criterion_weights = {
            "vibe": 0.50,
            "budget": 0.15,
            "drink_match": 0.08,
            "noise": 0.05,
            "distance": 0.05,
            "happy_hour_active": 0.03,
            "specials_match": 0.03,
            "crowd_fit": 0.03,
            "novelty": 0.03,
            "quality_signal": 0.05,
        }
        user_prefs = []
        for u in users:
            personal = _personal_vibe_weights(u["must_haves"] or [])
            # Start from the merged arc as a baseline (so when arc_profile is
            # ignored — e.g., by a caller without arc support — the user's
            # weights still reflect the night style). Then layer personal.
            base = dict(merged_vibes)
            for v, w in personal.items():
                base[v] = max(base.get(v, 0.0), w * 1.5)
            user_prefs.append(UserPreference(
                name=u["name"],
                vibe_weights=base,
                criterion_weights=dict(style_criterion_weights),
                max_per_drink=float(u["budget"]),
                preferred_drinks=tuple(u["drinks"]),
                preferred_noise=u["noise"],
            ))

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

    # Build the arc profile — one vibe_weights dict per stage of the night.
    # This is what tells the planner that "stop 1 = warm-up", "stop N = peak".
    arc_profile = tuple(dict(weights) for _role, weights in style["arc"])

    group = GroupInput(
        users=user_prefs,
        start_time=start_dt, end_time=end_dt,
        max_stops=max_stops,
        neighborhoods=tuple(neighborhoods),
        arc_profile=arc_profile,
        walking_only=style.get("walking_only", True),
    )

    # Stash inputs so a replan click (which doesn't go through the form)
    # can use the same group + data without re-submitting the form.
    st.session_state["group"] = group
    st.session_state["bars"] = bars
    st.session_state["cases"] = cases
    st.session_state["rules"] = rules

    with st.spinner("Planning…"):
        result = plan_crawl(group, bars=bars, cases=cases, rules=rules)
    st.session_state["result"] = result

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

    # -------- Dialogic replan (Phase 4) --------
    _render_reaction_ui(result)


def _render_reaction_ui(result):
    """Per-stop accept/reject/swap + lock checkboxes + Replan button.

    Each stop's reaction state is stored under st.session_state so a
    Replan click can rebuild the Reaction list without a form submit.
    """
    st.subheader("React to stops")
    st.caption(
        "For each stop, pick a verdict (and optionally lock it). "
        "Clicking **Replan** applies your reactions, updates preferences, "
        "and re-plans the crawl — preserving any locked stops."
    )

    group = st.session_state.get("group")
    if group is None:
        return

    reaction_rows = []
    for i, stop in enumerate(result.route.stops):
        col_info, col_verdict, col_user, col_lock = st.columns([3, 2, 2, 1])
        with col_info:
            st.markdown(f"**Stop {i + 1}:** {stop.bar.name}")
        with col_verdict:
            verdict = st.selectbox(
                "Verdict", options=["no-op", "accept", "reject", "swap"],
                key=f"verdict_{i}", label_visibility="collapsed",
            )
        with col_user:
            reacting = st.selectbox(
                "From", options=[u.name for u in group.users],
                key=f"user_{i}", label_visibility="collapsed",
            )
        with col_lock:
            lock = st.checkbox("Lock", key=f"lock_{i}")
        if verdict != "no-op":
            reaction_rows.append(Reaction(
                user_id=reacting, stop_index=i, verdict=verdict, lock=lock,
            ))
        elif lock:  # lock without a verdict — preserve in place
            reaction_rows.append(Reaction(
                user_id=group.users[0].name, stop_index=i,
                verdict="accept", lock=True,
            ))

    if st.button("Replan", type="primary"):
        if not reaction_rows:
            st.warning("No reactions set — pick at least one verdict.")
            return
        with st.spinner("Replanning…"):
            new_result = replan_with_reactions(
                result, reaction_rows, group,
                bars=st.session_state["bars"],
                cases=st.session_state["cases"],
                rules=st.session_state["rules"],
            )
        st.session_state["result"] = new_result

        st.markdown("### Updated plan")
        pref_child = next((c for c in new_result.explanations.children
                            if c.evidence.get("kind") == "preference_updates"),
                           None)
        delta_child = next((c for c in new_result.explanations.children
                             if c.evidence.get("kind") == "delta"), None)
        if pref_child:
            st.info(pref_child.summary)
        if delta_child:
            st.markdown(delta_child.summary)
        if not new_result.route.stops:
            st.error("Replan produced no feasible route (locked stop infeasible?).")
            return
        st.markdown(new_result.explanations.summary)
        for i, stop in enumerate(new_result.route.stops):
            st.markdown(f"**Stop {i + 1}:** {stop.bar.name} "
                         f"({stop.bar.neighborhood}, "
                         f"arrive {stop.arrival.strftime('%-I:%M%p').lower()})")


if __name__ == "__main__":
    main()
