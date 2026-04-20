"""Visualization: Folium map, Gantt-style timeline, per-criterion score breakdown."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import Bar, Route, Score
from .qualitative import phrase_for


# ---------------------------------------------------------------------------
# Folium map
# ---------------------------------------------------------------------------

STOP_COLORS = ["#E84545", "#F9A825", "#2E7D32", "#1565C0", "#6A1B9A", "#37474F"]


def render_map(route: Route, all_bars: Optional[list[Bar]] = None,
               save_path: Optional[Path] = None):
    """Render an interactive Folium map: numbered markers for route stops +
    small translucent markers for other bars (optional).

    Returns the Folium Map object. If `save_path` given, also saves to HTML.
    """
    try:
        import folium
    except ImportError as e:
        raise ImportError("folium required: pip install folium") from e

    if not route.stops:
        # Empty map centered on NYC
        m = folium.Map(location=[40.73, -73.99], zoom_start=13, tiles="cartodbpositron")
        if save_path:
            m.save(str(save_path))
        return m

    # Center on the route's geographic mean
    lats = [s.bar.lat for s in route.stops]
    lons = [s.bar.lon for s in route.stops]
    center = (sum(lats) / len(lats), sum(lons) / len(lons))
    m = folium.Map(location=center, zoom_start=14, tiles="cartodbpositron")

    # Optional: background markers for other bars
    if all_bars:
        route_ids = {s.bar.id for s in route.stops}
        for b in all_bars:
            if b.id in route_ids:
                continue
            folium.CircleMarker(
                location=(b.lat, b.lon), radius=3,
                color="#AAAAAA", weight=0.5, opacity=0.5, fill=True, fill_opacity=0.3,
                tooltip=f"{b.name} ({b.neighborhood})",
            ).add_to(m)

    # Route stops
    for i, stop in enumerate(route.stops):
        color = STOP_COLORS[i % len(STOP_COLORS)]
        popup = folium.Popup(_stop_popup_html(i + 1, stop), max_width=360)
        folium.Marker(
            location=(stop.bar.lat, stop.bar.lon),
            popup=popup,
            tooltip=f"{i + 1}. {stop.bar.name}",
            icon=folium.Icon(color="red" if i == 0 else ("green" if i == len(route.stops) - 1 else "blue"),
                              icon="glass", prefix="fa"),
        ).add_to(m)

    # Connecting polyline — one segment per leg
    coords = [(s.bar.lat, s.bar.lon) for s in route.stops]
    if len(coords) > 1:
        folium.PolyLine(
            coords, color="#333333", weight=3, opacity=0.6, dash_array="5,5",
        ).add_to(m)

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(save_path))
    return m


def _stop_popup_html(stop_num: int, stop) -> str:
    """HTML for a stop's popup — name, time, price, noise, top reasons."""
    bar = stop.bar
    arrival = stop.arrival.strftime("%-I:%M%p").lower()
    lines = [
        f"<b>Stop {stop_num}: {bar.name}</b>",
        f"Arrive {arrival}",
        f"<i>{bar.neighborhood}</i> · {bar.price_tier} (~${bar.avg_drink_price:.0f}) · {bar.noise_level}",
    ]
    if bar.user_note:
        lines.append(f"<em>Note: {bar.user_note}</em>")
    if stop.temporal_bonuses_captured:
        w = stop.temporal_bonuses_captured[0]
        lines.append(f"⏰ {w.kind.replace('_', ' ')}: {w.details}")
    return "<br>".join(lines)


# ---------------------------------------------------------------------------
# Gantt-style timeline
# ---------------------------------------------------------------------------

def render_timeline(route: Route, save_path: Optional[Path] = None,
                    title: str = "Crawl timeline"):
    """Gantt: horizontal bar per stop, arrival→departure. Active windows
    drawn as lighter overlays."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError as e:
        raise ImportError("matplotlib required") from e

    if not route.stops:
        fig, ax = plt.subplots(figsize=(8, 2))
        ax.text(0.5, 0.5, "No route to visualize", ha="center", va="center",
                transform=ax.transAxes, fontsize=12, color="#888")
        ax.set_axis_off()
        if save_path:
            fig.savefig(str(save_path), bbox_inches="tight", dpi=100)
        return fig

    n = len(route.stops)
    fig, ax = plt.subplots(figsize=(10, 1 + 0.5 * n))
    for i, stop in enumerate(route.stops):
        color = STOP_COLORS[i % len(STOP_COLORS)]
        duration = (stop.departure - stop.arrival).total_seconds() / 60.0
        ax.barh(n - 1 - i, width=duration, left=_num_minutes(stop.arrival, route),
                color=color, alpha=0.85, edgecolor="black", linewidth=0.5)
        ax.text(_num_minutes(stop.arrival, route) + 2, n - 1 - i,
                stop.bar.name, va="center", fontsize=9, color="white", fontweight="bold")

    # Window overlays
    for i, stop in enumerate(route.stops):
        for w in stop.temporal_bonuses_captured:
            ax.axvline(x=_num_minutes(stop.arrival, route), linestyle="--",
                        color="#333", alpha=0.3)

    # X axis in minutes since route start
    ax.set_xlabel("Minutes after start")
    ax.set_yticks(range(n))
    ax.set_yticklabels([f"Stop {n - i}" for i in range(n)])
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(save_path), bbox_inches="tight", dpi=100)
    return fig


def _num_minutes(dt: datetime, route: Route) -> float:
    """Minutes since the route's first arrival."""
    start = route.stops[0].arrival
    return (dt - start).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# Score breakdown (stacked bar)
# ---------------------------------------------------------------------------

def render_score_breakdown(route: Route,
                           per_user_scores: dict[str, dict[str, Score]],
                           save_path: Optional[Path] = None):
    """Stacked-bar: one bar per stop, segments = per-criterion weighted
    contributions, averaged across users."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise ImportError("matplotlib required") from e

    if not route.stops:
        fig, ax = plt.subplots(figsize=(6, 2))
        ax.text(0.5, 0.5, "No route", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        if save_path:
            fig.savefig(str(save_path), bbox_inches="tight", dpi=100)
        return fig

    # Build data: stops × criteria
    crits = ["vibe", "budget", "drink_match", "noise", "distance",
             "happy_hour_active", "specials_match", "crowd_fit",
             "novelty", "quality_signal"]
    stop_labels = [s.bar.name for s in route.stops]
    segments: dict[str, list[float]] = {c: [] for c in crits}
    for s in route.stops:
        for c in crits:
            vals = []
            for u_scores in per_user_scores.values():
                if s.bar.id in u_scores:
                    vals.append(u_scores[s.bar.id].weighted_contributions.get(c, 0.0))
            avg = sum(vals) / max(1, len(vals)) if vals else 0.0
            segments[c].append(avg)

    fig, ax = plt.subplots(figsize=(10, 4 + 0.3 * len(route.stops)))
    bottom = [0.0] * len(route.stops)
    cmap = plt.get_cmap("tab10")
    for i, c in enumerate(crits):
        ax.bar(stop_labels, segments[c], bottom=bottom,
               label=c.replace("_", " "), color=cmap(i % 10), edgecolor="white")
        bottom = [b + v for b, v in zip(bottom, segments[c])]

    ax.set_ylabel("Avg weighted contribution per user")
    ax.set_title("Per-stop score breakdown")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(save_path), bbox_inches="tight", dpi=100)
    return fig
