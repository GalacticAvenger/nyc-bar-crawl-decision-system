"""Regenerate every artifact in docs/screenshots/ from current code.

Two phases:
  A. Programmatic visualizations (always runs)
       route_map.html, timeline.png, score_breakdown.png from the canonical
       aligned-trio plan; pregame_clubs_*.{html,png} from the Phase 4.1
       House-of-Yes demo; argument_internals.md and replan_demo.md from
       direct introspection of build_stop_argument and replan_with_reactions.
  B. Streamlit UI screenshots (optional — needs playwright + a running
       streamlit server on :8501)
       ui_initial.png, ui_plan.png, ui_replan.png

Usage:
  # Phase A only (no browser needed)
  python scripts/regenerate_screenshots.py

  # Phase A + B (start streamlit first)
  streamlit run app/streamlit_app.py --server.headless=true \\
      --server.port=8501 --browser.gatherUsageStats=false &
  python scripts/regenerate_screenshots.py --with-ui
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # non-interactive backend for headless render

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.argument import render_argument
from src.data_loader import load_all
from src.decision_system import plan_crawl
from src.dialogic import replan_with_reactions
from src.explanation_engine import build_stop_argument
from src.models import GroupInput, Reaction, UserPreference
from src.visualize import render_map, render_score_breakdown, render_timeline


SHOT = ROOT / "docs" / "screenshots"
URL = "http://localhost:8501"


def regenerate_visualizations() -> None:
    """Phase A — programmatic visualization artifacts."""
    SHOT.mkdir(parents=True, exist_ok=True)
    d = load_all()
    bars, cases, rules = d["bars"], d["cases"], d["rules"]

    # === Plan 1: aligned trio (the canonical demo) ===
    users = [
        UserPreference(
            name="Alice",
            vibe_weights={"intimate": 0.9, "conversation": 0.8, "polished": 0.7},
            max_per_drink=20, preferred_drinks=("cocktails",),
        ),
        UserPreference(
            name="Bob",
            vibe_weights={"lively": 0.9, "unpretentious": 0.6,
                            "local-institution": 0.7},
            max_per_drink=12, preferred_drinks=("beer",),
        ),
        UserPreference(
            name="Carol",
            vibe_weights={"hidden-gem": 0.9, "conversation": 0.8, "intimate": 0.7},
            max_per_drink=16, preferred_drinks=("cocktails",),
        ),
    ]
    group = GroupInput(
        users=users,
        start_time=datetime(2026, 4, 24, 19, 0),
        end_time=datetime(2026, 4, 24, 23, 30),
        max_stops=3,
        neighborhoods=("East Village", "Lower East Side"),
    )
    result = plan_crawl(group, bars=bars, cases=cases, rules=rules)
    print(f"plan-1: {[s.bar.name for s in result.route.stops]}")
    render_map(result.route, bars, save_path=SHOT / "route_map.html")
    render_timeline(result.route, save_path=SHOT / "timeline.png",
                    title="Friday-night crawl — aligned trio")
    render_score_breakdown(result.route, result.traces["per_user_scores"],
                           save_path=SHOT / "score_breakdown.png")

    # === Plan 2: Pregame->clubs (Phase 4.1) ===
    club_users = [
        UserPreference(
            name=f"Friend {i}", max_per_drink=18,
            vibe_weights={"dance-floor": 1.8, "dj-set": 1.2, "late-close": 1.0,
                            "music-loud": 1.2, "pregame": 1.5},
            preferred_drinks=("cocktails", "beer", "shots"),
            preferred_noise="loud",
        )
        for i in (1, 2, 3)
    ]
    arc = (
        {"pregame": 1.5, "unpretentious": 0.5, "large-groups": 0.5},
        {"music-loud": 1.2, "crowd-loud": 1.0, "dj-set": 0.8},
        {"dance-floor": 1.8, "dj-set": 1.2, "late-close": 1.0},
    )
    club_group = GroupInput(
        users=club_users,
        start_time=datetime(2026, 4, 24, 21, 0),
        end_time=datetime(2026, 4, 25, 3, 30),
        max_stops=3,
        neighborhoods=("East Village", "Lower East Side", "Bushwick", "Williamsburg"),
        arc_profile=arc,
        walking_only=False,
        budget_multiplier=2.5,
    )
    club_result = plan_crawl(club_group, bars=bars, cases=cases, rules=rules)
    print(f"plan-2: {[s.bar.name for s in club_result.route.stops]}")
    render_map(club_result.route, bars,
               save_path=SHOT / "pregame_clubs_route.html")
    render_timeline(club_result.route,
                    save_path=SHOT / "pregame_clubs_timeline.png",
                    title="Pregame -> clubs — House of Yes demo")

    # === argument_internals.md ===
    stop = result.route.stops[0]
    arg = build_stop_argument(0, stop, result.route,
                               result.traces["per_user_scores"],
                               stop.runner_up, rules, users=users)
    lines = ["# Phase 2: structured Argument internals\n",
             f"\n**Conclusion**: {arg.conclusion}\n",
             "\n## Supporting premises\n"]
    for p in arg.supporting:
        marker = "**(decisive)** " if p is arg.decisive_premise else ""
        lines.append(f"- {marker}`{p.subject}` / `{p.criterion}` / "
                     f"mag={p.magnitude:.2f} — {p.evidence}\n")
    if arg.opposing:
        lines.append("\n## Opposing premises\n")
        for p in arg.opposing:
            lines.append(f"- `{p.subject}` / `{p.criterion}` / "
                         f"mag={p.magnitude:.2f} — {p.evidence}\n")
    if arg.sacrifice:
        lines.append(f"\n## Sacrifice\n{arg.sacrifice}\n")
    if arg.runner_up:
        lines.append(f"\n## Runner-up\n{arg.runner_up}\n")
    lines.append(f"\n## Rendered prose\n> {render_argument(arg)}\n")
    (SHOT / "argument_internals.md").write_text("".join(lines))

    # === replan_demo.md ===
    reactions = [
        Reaction(user_id="Alice", stop_index=0, verdict="reject",
                 optional_reason="Alice didn't love this room"),
        Reaction(user_id="Bob", stop_index=1, verdict="accept", lock=True),
    ]
    new_result = replan_with_reactions(
        result, reactions, group, bars=bars, cases=cases, rules=rules,
    )
    pref_child = next((c for c in new_result.explanations.children
                        if c.evidence.get("kind") == "preference_updates"), None)
    delta_child = next((c for c in new_result.explanations.children
                         if c.evidence.get("kind") == "delta"), None)
    md = ["# Phase 4: dialogic replan demo\n", "\n## Original plan\n"]
    for i, s in enumerate(result.route.stops, 1):
        md.append(f"{i}. **{s.bar.name}** ({s.bar.neighborhood}, "
                  f"arrive {s.arrival.strftime('%-I:%M%p').lower()})\n")
    md.append("\n## Reactions\n")
    for r in reactions:
        lock = " (LOCKED)" if r.lock else ""
        md.append(f"- {r.user_id} → stop {r.stop_index + 1}: "
                  f"**{r.verdict}**{lock} — _{r.optional_reason}_\n")
    if pref_child:
        md.append("\n## Preference updates (auto-narrated)\n```\n")
        md.append(pref_child.summary)
        md.append("\n```\n")
    if delta_child:
        md.append("\n## Delta narrative (every change attributed)\n```\n")
        md.append(delta_child.summary)
        md.append("\n```\n")
    md.append("\n## New plan\n")
    if new_result.route.stops:
        for i, s in enumerate(new_result.route.stops, 1):
            was_locked = any(r.lock and r.stop_index == i - 1 for r in reactions)
            marker = " (LOCKED — preserved exactly)" if was_locked else ""
            md.append(f"{i}. **{s.bar.name}** ({s.bar.neighborhood}, "
                      f"arrive {s.arrival.strftime('%-I:%M%p').lower()})"
                      f"{marker}\n")
    else:
        md.append("(no feasible replan)\n")
    (SHOT / "replan_demo.md").write_text("".join(md))
    print(f"wrote: argument_internals.md, replan_demo.md")


def regenerate_ui_screenshots() -> None:
    """Phase B — Playwright captures of the live Streamlit UI."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; skipping UI screenshots")
        print("  pip install playwright && python -m playwright install chromium")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1100},
                                    device_scale_factor=2)
        page = ctx.new_page()

        print("ui-1: initial splash...")
        page.goto(URL, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOT / "ui_initial.png"), full_page=True)

        print("ui-2: planning...")
        page.get_by_role("button", name="Plan crawl").click()
        page.wait_for_selector("text=The plan", timeout=20_000)
        page.wait_for_timeout(3500)
        page.screenshot(path=str(SHOT / "ui_plan.png"), full_page=True)

        print("ui-3: replanning with a reject...")
        boxes = page.locator('[data-testid="stSelectbox"]').all()
        if len(boxes) >= 6:
            verdict_box = boxes[-6]  # first reaction selectbox = stop 0 verdict
            verdict_box.click()
            page.wait_for_timeout(500)
            page.locator("li").filter(has_text="reject").first.click()
            page.wait_for_timeout(800)
        replan = page.locator("button").filter(has_text="Replan")
        if replan.count() > 0:
            replan.first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            replan.first.click()
            page.wait_for_timeout(6000)
            try:
                page.get_by_text("Updated plan").scroll_into_view_if_needed()
                page.wait_for_timeout(500)
            except Exception:
                pass
            page.screenshot(path=str(SHOT / "ui_replan.png"), full_page=True)

        browser.close()
    print("done.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-ui", action="store_true",
                    help="Also capture Streamlit UI screenshots via Playwright "
                          "(requires a streamlit server on :8501)")
    args = ap.parse_args()

    print("Phase A: regenerating visualizations...")
    regenerate_visualizations()

    if args.with_ui:
        print("\nPhase B: capturing Streamlit UI...")
        regenerate_ui_screenshots()


if __name__ == "__main__":
    main()
