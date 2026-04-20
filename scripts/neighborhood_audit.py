"""Generate docs/dataset_report.md summarizing the enriched bars.json."""

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"


def main():
    bars = json.loads((DATA / "bars.json").read_text())
    lines = ["# Dataset Report\n",
             f"**{len(bars)} bars** enriched from the user's Google Maps list.\n"]

    # Neighborhood distribution
    nc = Counter(b["neighborhood"] for b in bars)
    lines.append("## Neighborhoods\n")
    lines.append("| Neighborhood | Count |")
    lines.append("|---|---|")
    for n, c in nc.most_common():
        lines.append(f"| {n} | {c} |")

    # Price tier distribution
    pc = Counter(b["price_tier"] for b in bars)
    lines.append("\n## Price tier\n")
    lines.append("| Tier | Count |")
    lines.append("|---|---|")
    for tier in ("cheap", "moderate", "premium", "splurge"):
        lines.append(f"| {tier} | {pc.get(tier, 0)} |")

    # Bar types
    tc = Counter()
    for b in bars:
        for t in b["bar_type"]:
            tc[t] += 1
    lines.append("\n## Bar types\n")
    lines.append("| Type | Count |")
    lines.append("|---|---|")
    for t, c in tc.most_common():
        lines.append(f"| {t} | {c} |")

    # Vibe distribution
    vc = Counter()
    for b in bars:
        for v in b["vibe_tags"]:
            vc[v] += 1
    lines.append("\n## Vibe tag frequency\n")
    lines.append("| Vibe | Count |")
    lines.append("|---|---|")
    for v, c in vc.most_common():
        lines.append(f"| {v} | {c} |")

    # User notes (the 6 preserved)
    user_notes = [b for b in bars if b.get("user_note")]
    lines.append(f"\n## User notes ({len(user_notes)} preserved)\n")
    for b in user_notes:
        lines.append(f"- **{b['name']}** ({b['neighborhood']}): _{b['user_note']}_")

    # Top quality signals
    sorted_bars = sorted(bars, key=lambda b: -b["quality_signal"])
    lines.append("\n## Top 10 by quality_signal\n")
    lines.append("| Name | Rating × log(reviews) | Signal |")
    lines.append("|---|---|---|")
    for b in sorted_bars[:10]:
        lines.append(f"| {b['name']} | {b['google_rating']} × log10({b['google_review_count']}) | {b['quality_signal']:.3f} |")

    lines.append("\n## Bottom 10 by quality_signal\n")
    lines.append("| Name | Rating × log(reviews) | Signal |")
    lines.append("|---|---|---|")
    for b in sorted_bars[-10:]:
        lines.append(f"| {b['name']} | {b['google_rating']} × log10({b['google_review_count']}) | {b['quality_signal']:.3f} |")

    # Gap audit
    lines.append("\n## Gap audit\n")
    gaps = []
    for nhood in nc:
        hood_bars = [b for b in bars if b["neighborhood"] == nhood]
        if len(hood_bars) >= 5:
            tiers = {b["price_tier"] for b in hood_bars}
            vibes = set()
            for b in hood_bars:
                vibes.update(b["vibe_tags"])
            if len(tiers) < 2:
                gaps.append(f"- **{nhood}** ({len(hood_bars)} bars): only 1 price tier ({tiers}).")
            if len(vibes) < 5:
                gaps.append(f"- **{nhood}** ({len(hood_bars)} bars): only {len(vibes)} distinct vibes.")
    if gaps:
        lines.append("\n".join(gaps))
    else:
        lines.append("No gaps — every neighborhood with ≥5 bars has ≥2 price tiers and ≥5 distinct vibes.")

    out = "\n".join(lines) + "\n"
    (DOCS / "dataset_report.md").write_text(out)
    print(f"Wrote dataset report to {DOCS / 'dataset_report.md'}")
    print(f"  {len(bars)} bars, {len(nc)} neighborhoods, {len(tc)} bar types, {len(vc)} vibes")


if __name__ == "__main__":
    main()
