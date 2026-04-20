"""
Phase 1 enrichment: seed_bars.json → bars.json.

Fully deterministic, offline. Re-running produces byte-identical output.

Inputs:
  data/seed_bars.json          — 159 raw entries (parsed from Google Maps list)
  data/category_to_vibes.yaml  — category → default vibes/noise/drinks/etc.
  data/default_hours.yaml      — default open hours + happy hour per type
  data/bar_addresses.json      — hand-curated (name, seed_id) → neighborhood + address
  data/vibe_vocab.json         — fixed vibe vocabulary for validation

Output: data/bars.json (143 enriched bars matching schemas/bar.schema.json).
"""

import hashlib
import json
import math
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

# ---------------------------------------------------------------------------
# NEIGHBORHOOD CENTROIDS (lat, lon) — used as base for jittered bar coords.
# ---------------------------------------------------------------------------
NEIGHBORHOODS = {
    "East Village":      (40.7265, -73.9815),
    "Lower East Side":   (40.7184, -73.9867),
    "Greenwich Village": (40.7336, -74.0027),
    "West Village":      (40.7358, -74.0042),
    "NoHo":              (40.7275, -73.9930),
    "SoHo":              (40.7233, -73.9997),
    "Tribeca":           (40.7163, -74.0086),
    "FiDi":              (40.7075, -74.0113),
    "Chinatown":         (40.7158, -73.9970),
    "Chelsea":           (40.7465, -74.0014),
    "Flatiron":          (40.7410, -73.9897),
    "NoMad":             (40.7446, -73.9882),
    "Gramercy":          (40.7370, -73.9845),
    "Kips Bay":          (40.7428, -73.9777),
    "Midtown":           (40.7549, -73.9840),
    "Midtown West":      (40.7610, -73.9900),
    "Hell's Kitchen":    (40.7638, -73.9918),
    "Koreatown":         (40.7479, -73.9874),
    "Union Square":      (40.7359, -73.9911),
    "Upper East Side":   (40.7736, -73.9566),
    "Upper West Side":   (40.7870, -73.9754),
    "Williamsburg":      (40.7143, -73.9566),
    "Bushwick":          (40.6943, -73.9213),
    "Greenpoint":        (40.7298, -73.9550),
    "Gowanus":           (40.6735, -73.9880),
    "Park Slope":        (40.6710, -73.9814),
    "Astoria":           (40.7644, -73.9235),
    "Long Island City":  (40.7447, -73.9485),
    "Ridgewood":         (40.7046, -73.9050),
}


# ---------------------------------------------------------------------------
# Per-seed-id neighborhood + address assignments.
# Keyed by seed_id so the two "Barcade" entries resolve to distinct locations.
# Format: seed_id -> (neighborhood, street_address)
# ---------------------------------------------------------------------------
BAR_LOCATIONS = {
    "seed_001": ("East Village", "304 Bowery, New York, NY 10012"),  # Slainte
    "seed_002": ("Astoria", "35-02 35th St, Astoria, NY 11106"),  # Sunswick 35/35
    "seed_003": ("Astoria", "29-12 23rd Ave, Astoria, NY 11105"),  # The Bonnie
    "seed_004": ("Astoria", "29-19 24th Ave, Astoria, NY 11102"),  # Bohemian Hall
    "seed_005": ("Bushwick", "618 Grand St, Brooklyn, NY 11211"),  # Bushwick Country Club
    "seed_006": ("Astoria", "24-06 23rd Ave, Astoria, NY 11102"),  # Carmelo's
    "seed_007": ("Lower East Side", "141 E Houston St, New York, NY 10002"),  # Banzarbar
    "seed_008": ("Lower East Side", "35 Canal St, New York, NY 10002"),  # Bar Orai
    "seed_009": ("West Village", "248 W 10th St, New York, NY 10014"),  # So & So's Piano Bar
    "seed_010": ("Lower East Side", "56 Chrystie St, New York, NY 10002"),  # 56709
    "seed_011": ("Lower East Side", "929 Rivington Ct, New York, NY 10002"),  # 929
    "seed_012": ("Lower East Side", "128 Rivington St, New York, NY 10002"),  # Ask For Janice
    "seed_013": ("Lower East Side", "102 Norfolk St, New York, NY 10002"),  # The Back Room
    "seed_014": ("East Village", "113 St Marks Pl, New York, NY 10009"),  # Please Don't Tell
    "seed_015": ("SoHo", "212 Lafayette St, New York, NY 10012"),  # Cafe Select (excluded)
    "seed_016": ("Bushwick", "234 Starr St, Brooklyn, NY 11237"),  # The Sultan Room
    "seed_017": ("Bushwick", "19 Stagg St, Brooklyn, NY 11206"),  # SILO
    "seed_018": ("East Village", "6 St Marks Pl, New York, NY 10003"),  # Barcade (St. Marks)
    "seed_019": ("East Village", "153 1st Ave, New York, NY 10003"),  # Coyote Ugly
    "seed_020": ("Park Slope", "702 Union St, Brooklyn, NY 11215"),  # Union Hall
    "seed_021": ("Upper East Side", "1463 3rd Ave, New York, NY 10028"),  # VALERIE
    "seed_022": ("Greenwich Village", "158 Bleecker St, New York, NY 10012"),  # LPR
    "seed_023": ("Koreatown", "32 W 32nd St, New York, NY 10001"),  # Turntable LP
    "seed_024": ("Midtown", "151 W 26th St, New York, NY 10001"),  # Studio 151
    "seed_025": ("East Village", "135 Ave A, New York, NY 10009"),  # Lucy's
    "seed_026": ("Bushwick", "599 Johnson Ave, Brooklyn, NY 11237"),  # Elsewhere
    "seed_027": ("Ridgewood", "1093 Wyckoff Ave, Brooklyn, NY 11237"),  # H0L0
    "seed_028": ("Bushwick", "1271 Myrtle Ave, Brooklyn, NY 11221"),  # Bossa Nova Civic Club
    "seed_029": ("Bushwick", "1260 Myrtle Ave, Brooklyn, NY 11221"),  # Mood Ring
    "seed_030": ("Bushwick", "19 Stagg St, Brooklyn, NY 11206"),  # All Night Skate
    "seed_031": ("Ridgewood", "56-06 Cooper Ave, Queens, NY 11385"),  # Nowadays
    "seed_032": ("Williamsburg", "200 N 14th St, Brooklyn, NY 11249"),  # The Gutter
    "seed_033": ("NoMad", "485 5th Ave, New York, NY 10017"),  # Magic Hour
    "seed_034": ("Gowanus", "233 Butler St, Brooklyn, NY 11217"),  # Public Records
    "seed_035": ("Bushwick", "73 Irving Ave, Brooklyn, NY 11237"),  # Record Room
    "seed_036": ("Tribeca", "81 Hudson St, New York, NY 10013"),  # Puffy's Tavern
    "seed_037": ("Williamsburg", "129 Havemeyer St, Brooklyn, NY 11211"),  # Carneval (excluded)
    "seed_038": ("Lower East Side", "95 Stanton St, New York, NY 10002"),  # Arlene's Grocery
    "seed_039": ("West Village", "136 W Houston St, New York, NY 10012"),  # Sip&Guzzle
    "seed_040": ("East Village", "125 E 11th St, New York, NY 10003"),  # Webster Hall
    "seed_041": ("Lower East Side", "50 Clinton St, New York, NY 10002"),  # Baby Grand LES
    "seed_042": ("NoHo", "300 Bowery, New York, NY 10012"),  # Bar Bonobo
    "seed_043": ("East Village", "105 1st Ave, New York, NY 10003"),  # Mister Paradise
    "seed_044": ("Flatiron", "174 5th Ave, New York, NY 10010"),  # Jungle Bird
    "seed_045": ("Williamsburg", "388 Union Ave, Brooklyn, NY 11211"),  # Barcade (Williamsburg)
    "seed_046": ("East Village", "116 Ave A, New York, NY 10009"),  # duckduck
    "seed_047": ("Williamsburg", "709 Lorimer St, Brooklyn, NY 11211"),  # Pete's Candy Store
    "seed_048": ("Williamsburg", "140 Meserole Ave, Brooklyn, NY 11222"),  # Stella & Fly (closed)
    "seed_049": ("Greenpoint", "123 Franklin St, Brooklyn, NY 11222"),  # Cafe Balearica
    "seed_050": ("East Village", "79 St Marks Pl, New York, NY 10003"),  # Blue & Gold Tavern
    "seed_051": ("Upper East Side", "1707 1st Ave, New York, NY 10128"),  # UES (excluded)
    "seed_052": ("Chelsea", "530 W 27th St, New York, NY 10001"),  # Speakeasy Magick (McKittrick)
    "seed_053": ("East Village", "330 E 11th St, New York, NY 10003"),  # Vida Verde Tequila Bar
    "seed_054": ("East Village", "538 E 14th St, New York, NY 10009"),  # Otto's Shrunken Head
    "seed_055": ("West Village", "519 Hudson St, New York, NY 10014"),  # Cowgirl
    "seed_056": ("East Village", "41 E 7th St, New York, NY 10003"),  # Burp Castle
    "seed_057": ("NoHo", "9 Great Jones St, New York, NY 10012"),  # The Nines
    "seed_058": ("East Village", "308 E 6th St, New York, NY 10003"),  # Beetle House
    "seed_059": ("Bushwick", "19 Stagg St, Brooklyn, NY 11206"),  # Paradise Lost
    "seed_060": ("East Village", "240 E 9th St, New York, NY 10003"),  # Sake Bar Decibel
    "seed_061": ("East Village", "340 E 6th St, New York, NY 10003"),  # Motel No Tell
    "seed_062": ("Koreatown", "34 W 32nd St, 12th Fl, New York, NY 10001"),  # Space Billiard
    "seed_063": ("Koreatown", "17 W 32nd St, New York, NY 10001"),  # Space Ping Pong
    "seed_064": ("Lower East Side", "112 Forsyth St, New York, NY 10002"),  # BELTANE
    "seed_065": ("Upper East Side", "1629 2nd Ave, New York, NY 10028"),  # Ethyl's
    "seed_066": ("East Village", "15 E 7th St, New York, NY 10003"),  # McSorley's
    "seed_067": ("East Village", "7 Rivington St, New York, NY 10002"),  # Loreley
    "seed_068": ("Hell's Kitchen", "358 W 44th St, New York, NY 10036"),  # Rosie Dunn's
    "seed_069": ("NoMad", "Between 5th Ave / Broadway, New York, NY 10001"),  # The Gem Saloon
    "seed_070": ("Midtown West", "470 8th Ave, New York, NY 10018"),  # Celtic Pub
    "seed_071": ("Midtown West", "922 3rd Ave, New York, NY 10022"),  # Pig 'N' Whistle
    "seed_072": ("Hell's Kitchen", "266 W 47th St, New York, NY 10036"),  # Mean Fiddler
    "seed_073": ("Brooklyn Heights", "34 Water St, Brooklyn, NY 11201"),  # Sunken Harbor Club
    "seed_074": ("Midtown West", "300 W 40th St, New York, NY 10018"),  # Beer Authority
    "seed_075": ("East Village", "17 2nd Ave, New York, NY 10003"),  # Madame George
    "seed_076": ("Lower East Side", "170 Forsyth St, New York, NY 10002"),  # Rhymers' Club
    "seed_077": ("East Village", "510 E 11th St, New York, NY 10009"),  # 11th St. Bar
    "seed_078": ("FiDi", "162 Front St, New York, NY 10038"),  # Maiden Lane
    "seed_079": ("East Village", "81 Ave A, New York, NY 10009"),  # Sing Sing Ave A
    "seed_080": ("Lower East Side", "169 E Broadway, New York, NY 10002"),  # 169 Bar
    "seed_081": ("East Village", "219 Ave A, New York, NY 10009"),  # Planet Rose
    "seed_082": ("Lower East Side", "122 Rivington St, New York, NY 10002"),  # La Caverna
    "seed_083": ("NoHo", "3 Bleecker St, New York, NY 10012"),  # Von
    "seed_084": ("Gramercy", "309 2nd Ave, New York, NY 10003"),  # Eastpoint Bar
    "seed_085": ("East Village", "14 Ave A, New York, NY 10009"),  # Double Down Saloon
    "seed_086": ("Greenpoint", "1153 Manhattan Ave, Brooklyn, NY 11222"),  # Five Lamps
    "seed_087": ("Upper East Side", "403 E 73rd St, New York, NY 10021"),  # George & Jack's
    "seed_088": ("Upper East Side", "1560 2nd Ave, New York, NY 10028"),  # Pioneers Bar NYC
    "seed_089": ("Upper West Side", "76 3rd Ave, New York, NY 10003"),  # Stumble Inn
    "seed_090": ("FiDi", "86 Fulton St, New York, NY 10038"),  # Downtown Social
    "seed_091": ("Hell's Kitchen", "339 W 51st St, New York, NY 10019"),  # AWOL
    "seed_092": ("Midtown", "135 W 36th St, New York, NY 10018"),  # Ragtrader
    "seed_093": ("Brooklyn Heights", "61 Columbia Heights, Brooklyn, NY 11201"),  # Fairweather
    "seed_094": ("East Village", "315 E 10th St, New York, NY 10009"),  # In Vino Veritas (excluded)
    "seed_095": ("NoHo", "13 E 17th St, New York, NY 10003"),  # Lillie's Victorian
    "seed_096": ("Upper West Side", "511 Amsterdam Ave, New York, NY 10024"),  # e's BAR
    "seed_097": ("Upper West Side", "430 Amsterdam Ave, New York, NY 10024"),  # Jake's Dilemma
    "seed_098": ("Upper West Side", "503 Columbus Ave, New York, NY 10024"),  # Prohibition
    "seed_099": ("Williamsburg", "139 N 6th St, Brooklyn, NY 11249"),  # Surf Bar
    "seed_100": ("Greenpoint", "33 Nassau Ave, Brooklyn, NY 11222"),  # Spritzenhaus33
    "seed_101": ("FiDi", "120 Cedar St, New York, NY 10006"),  # O'Hara's
    "seed_102": ("Chelsea", "110 W 17th St, New York, NY 10011"),  # Verlaine
    "seed_103": ("West Village", "75 Christopher St, New York, NY 10014"),  # Cellar Dog
    "seed_104": ("Upper West Side", "235 W 84th St, New York, NY 10024"),  # Brandy's Piano Bar
    "seed_105": ("Upper East Side", "1125 1st Ave, New York, NY 10065"),  # Treadwell Park
    "seed_106": ("Upper East Side", "1644 1st Ave, New York, NY 10028"),  # Mulligan's
    "seed_107": ("FiDi", "48 Broad St, New York, NY 10004"),  # Local 42
    "seed_108": ("Hell's Kitchen", "815 9th Ave, New York, NY 10019"),  # Valhalla
    "seed_109": ("Hell's Kitchen", "765 9th Ave, New York, NY 10019"),  # Empanada Mama (excluded)
    "seed_110": ("Hell's Kitchen", "307 W 47th St, New York, NY 10036"),  # Dutch Fred's
    "seed_111": ("Hell's Kitchen", "734 10th Ave, New York, NY 10019"),  # As Is NYC
    "seed_112": ("Hell's Kitchen", "328 W 45th St, New York, NY 10036"),  # Beer Culture
    "seed_113": ("Hell's Kitchen", "627 9th Ave, New York, NY 10036"),  # Rudy's
    "seed_114": ("Hell's Kitchen", "660 10th Ave, New York, NY 10036"),  # Maiz (excluded)
    "seed_115": ("Greenwich Village", "230 Thompson St, New York, NY 10012"),  # The Uncommons
    "seed_116": ("West Village", "52 Grove St, New York, NY 10014"),  # Bar Pisellino
    "seed_117": ("NoMad", "230 5th Ave, New York, NY 10001"),  # 230 Fifth
    "seed_118": ("West Village", "85 Christopher St, New York, NY 10014"),  # Old Mates
    "seed_119": ("Lower East Side", "33 Orchard St, New York, NY 10002"),  # Poco NYC (closed)
    "seed_120": ("Flatiron", "45 W 27th St, New York, NY 10001"),  # Oscar Wilde
    "seed_121": ("Upper East Side", "1725 2nd Ave, New York, NY 10128"),  # Bibliotheque
    "seed_122": ("SoHo", "114 Kenmare St, New York, NY 10012"),  # La Esquina (excluded)
    "seed_123": ("Lower East Side", "138 E Houston St, New York, NY 10002"),  # Pineapple Club
    "seed_124": ("Midtown", "226 W 52nd St, New York, NY 10019"),  # msocial Rooftop
    "seed_125": ("Lower East Side", "141 E Houston St, New York, NY 10002"),  # Lost in Paradise
    "seed_126": ("NoMad", "9 W 26th St, New York, NY 10010"),  # Flatiron Room NoMad
    "seed_127": ("West Village", "132 W 4th St, New York, NY 10012"),  # Iggy's
    "seed_128": ("Hell's Kitchen", "434 W 51st St, New York, NY 10019"),  # Hold Fast
    "seed_129": ("Hell's Kitchen", "481 10th Ave, New York, NY 10018"),  # Pony Bar
    "seed_130": ("Flatiron", "49 W 27th St, New York, NY 10001"),  # Patent Pending
    "seed_131": ("Hell's Kitchen", "405 W 57th St, New York, NY 10019"),  # Avoca
    "seed_132": ("Flatiron", "23 E 20th St, New York, NY 10003"),  # Clemente Bar
    "seed_133": ("Upper East Side", "1417 2nd Ave, New York, NY 10021"),  # A la Turka (excluded)
    "seed_134": ("Williamsburg", "9 Hope St, Brooklyn, NY 11211"),  # Ray's
    "seed_135": ("Greenwich Village", "149 Bleecker St, New York, NY 10012"),  # Wicked Willy's
    "seed_136": ("Greenwich Village", "179 W 4th St, New York, NY 10014"),  # Down the Hatch
    "seed_137": ("East Village", "85 Ave A, New York, NY 10009"),  # DROM
    "seed_138": ("Midtown West", "174 W 4th St, New York, NY 10014"),  # Skinny Bar
    "seed_139": ("FiDi", "124 Washington St, New York, NY 10006"),  # Bayard's Ale House
    "seed_140": ("Bushwick", "2 Wyckoff Ave, Brooklyn, NY 11237"),  # House of Yes
    "seed_141": ("Midtown West", "5 W 37th St, New York, NY 10018"),  # Reichenbach Hall
    "seed_142": ("Midtown West", "848 6th Ave, New York, NY 10001"),  # Standard Biergarten
    "seed_143": ("West Village", "103 Perry St, New York, NY 10014"),  # Penny Farthing
    "seed_144": ("West Village", "10 Jones St, New York, NY 10014"),  # Bar Bianchi (excluded)
    "seed_145": ("West Village", "284 W 12th St, New York, NY 10014"),  # Cafe Cluny (excluded)
    "seed_146": ("SoHo", "188 Grand St, New York, NY 10013"),  # Quique Crudo (excluded)
    "seed_147": ("SoHo", "80 Spring St, New York, NY 10012"),  # Papatzul (closed)
    "seed_148": ("Greenpoint", "94 Broadway, Brooklyn, NY 11211"),  # Taqueria Ramirez (excluded)
    "seed_149": ("Upper East Side", "350 E 75th St, New York, NY 10021"),  # VICTORIA!
    "seed_150": ("Flatiron", "150 W 22nd St, New York, NY 10011"),  # La Victoria
    "seed_151": ("Greenpoint", "98 Meserole Ave, Brooklyn, NY 11222"),  # Good Room
    "seed_152": ("Lower East Side", "180 Orchard St, New York, NY 10002"),  # Mr. Purple
    "seed_153": ("East Village", "214 Ave A, New York, NY 10009"),  # Wiggle Room
    "seed_154": ("Midtown West", "190 W 47th St, New York, NY 10036"),  # The Spaniard
    "seed_155": ("Midtown West", "1190 6th Ave, New York, NY 10036"),  # Brass Monkey
    "seed_156": ("Upper East Side", "225 E 84th St, New York, NY 10028"),  # Overlook
    "seed_157": ("Midtown West", "380 W 37th St, New York, NY 10018"),  # Factory 380
    "seed_158": ("Upper East Side", "4545 3rd Ave, Bronx, NY 10458"),  # Evergreen Museum (excluded)
    "seed_159": ("Greenwich Village", "145 Bleecker St, New York, NY 10012"),  # Peculier Pub
}


# ---------------------------------------------------------------------------
# Capacity estimates by bar_type (rough but plausible).
# ---------------------------------------------------------------------------
CAPACITY_BY_TYPE = {
    "cocktail_bar": 60, "speakeasy": 40, "wine_bar": 50, "piano_bar": 80,
    "pub": 120, "irish_pub": 150, "gastropub": 90, "beer_garden": 250,
    "beer_hall": 200, "sports_bar": 150, "pool_hall": 100, "karaoke": 80,
    "nightclub": 300, "dance": 250, "event_venue": 400, "music_venue": 200,
    "lounge": 70, "generic_bar": 80, "bar_with_food": 90,
    "board_game_bar": 60, "themed": 80,
}


def deterministic_jitter(seed_id: str) -> tuple[float, float]:
    """Small deterministic (lat, lon) jitter based on seed_id hash.
    Range roughly ±0.004° (~0.3 mi) so bars within a neighborhood aren't co-located."""
    h = hashlib.sha256(seed_id.encode()).digest()
    dlat = (h[0] - 128) / 128 * 0.004
    dlon = (h[1] - 128) / 128 * 0.004
    return dlat, dlon


def pick_capacity(bar_types: list[str]) -> int:
    for t in bar_types:
        if t in CAPACITY_BY_TYPE:
            return CAPACITY_BY_TYPE[t]
    return 80


def crowd_template(bar_types: list[str]) -> dict[str, str]:
    """Return hour → crowd_level for 12..26 (noon to 2am next day, using 0..23 keys)."""
    # Different profiles peak at different hours.
    if "nightclub" in bar_types or "dance" in bar_types or "event_venue" in bar_types:
        prof = {22: "mellow", 23: "lively", 0: "packed", 1: "packed", 2: "lively"}
    elif "karaoke" in bar_types:
        prof = {19: "mellow", 20: "lively", 21: "packed", 22: "packed", 23: "lively", 0: "mellow"}
    elif "sports_bar" in bar_types:
        prof = {15: "mellow", 16: "lively", 17: "packed", 18: "packed", 19: "lively", 20: "mellow"}
    elif "pub" in bar_types or "irish_pub" in bar_types or "gastropub" in bar_types:
        prof = {17: "mellow", 18: "lively", 19: "packed", 20: "packed", 21: "lively", 22: "lively"}
    elif "beer_garden" in bar_types or "beer_hall" in bar_types:
        prof = {16: "mellow", 17: "lively", 18: "packed", 19: "packed", 20: "lively", 21: "mellow"}
    elif "cocktail_bar" in bar_types or "wine_bar" in bar_types or "lounge" in bar_types:
        prof = {19: "mellow", 20: "lively", 21: "packed", 22: "packed", 23: "lively"}
    else:  # generic_bar and friends
        prof = {18: "mellow", 19: "lively", 20: "packed", 21: "packed", 22: "lively", 23: "mellow"}

    # Fill all evening hours 17-23 + 0-2
    result = {}
    for h in list(range(17, 24)) + [0, 1, 2]:
        if h in prof:
            result[str(h)] = prof[h]
        else:
            # Interpolate: morning hours 'dead', then warm up
            if h < 17: result[str(h)] = "dead"
            else: result[str(h)] = "mellow"
    return result


def avg_drink_price_for(price_tier: str, price_range: list | None) -> float:
    """Pick a plausible avg_drink_price per the bar's tier."""
    if price_range:
        lo, hi = price_range
        # Scale: high end of the google range is for food+drinks; use mid-low for drinks alone.
        return round(min(50, max(2, lo + 0.3 * (hi - lo))), 1)
    defaults = {"cheap": 6.0, "moderate": 11.0, "premium": 16.0, "splurge": 24.0, "unknown": 12.0}
    return defaults.get(price_tier, 12.0)


def drink_specialties_for(bar_types: list[str], name: str) -> list[str]:
    name_lower = name.lower()
    out = []
    if "irish_pub" in bar_types or "pub" in bar_types:
        out.extend(["Guinness", "whiskey"])
    if "cocktail_bar" in bar_types:
        out.append("craft cocktails")
    if "wine_bar" in bar_types:
        out.append("natural wines")
    if "beer_garden" in bar_types or "beer_hall" in bar_types:
        out.append("German lagers")
    if "whiskey" in name_lower or "whisky" in name_lower:
        out.append("whiskey flight")
    if "tequila" in name_lower:
        out.extend(["tequila", "mezcal"])
    if "sake" in name_lower:
        out.append("sake")
    if "tiki" in bar_types:
        out.extend(["tiki drinks", "rum"])
    if "karaoke" in bar_types:
        out.append("highballs")
    return out[:4]


def drink_categories_for(cat_vibes_entry: dict) -> list[str]:
    return list(cat_vibes_entry.get("drink_categories", ["beer", "cocktails"]))


def apply_user_note_overrides(vibes: set[str], note_overrides: list, user_note: str | None):
    """Mutates `vibes`. Returns forced noise override or None."""
    if not user_note:
        return None
    note_lower = user_note.lower()
    forced_noise = None
    for rule in note_overrides:
        if rule["pattern"].lower() in note_lower:
            for v in rule.get("add_vibes", []):
                vibes.add(v)
            if rule.get("force_noise"):
                forced_noise = rule["force_noise"]
    return forced_noise


def apply_primary_function_overrides(vibes: set[str], pf_overrides: dict, primary_function: str | None):
    if not primary_function:
        return
    override = pf_overrides.get(primary_function)
    if override:
        for v in override.get("add_vibes", []):
            vibes.add(v)


def build_open_hours(hours_profile: dict) -> dict:
    return dict(hours_profile["hours"])


def build_happy_hour_windows(hours_profile: dict) -> list[dict]:
    hh = hours_profile.get("default_happy_hour")
    if not hh:
        return []
    return [{
        "days": hh["days"],
        "start": hh["start"],
        "end": hh["end"],
        "kind": "happy_hour",
        "details": hh["details"],
        "bonus": hh["bonus"],
    }]


def build_specials(hours_profile: dict) -> list[dict]:
    out = []
    for s in hours_profile.get("default_specials") or []:
        out.append({
            "days": s["days"],
            "start": s["start"],
            "end": s["end"],
            "kind": s["kind"],
            "details": s["details"],
            "bonus": s["bonus"],
        })
    return out


def novelty_for(bar_types: list[str], vibes: set[str], primary_function: str | None) -> float:
    score = 0.4
    if "themed" in bar_types: score += 0.2
    if "themed" in vibes: score += 0.1
    if "hidden-gem" in vibes: score += 0.2
    if primary_function in ("themed_cocktail_experience", "themed_party_bar", "board_game_bar", "tiki_bar"):
        score += 0.2
    return round(min(1.0, score), 2)


def food_quality_for(bar_types: list[str], cat: str | None) -> str:
    if "pub" in bar_types or "irish_pub" in bar_types: return "pub_food"
    if "gastropub" in bar_types or "bar_with_food" in bar_types: return "full_menu"
    if "beer_garden" in bar_types or "beer_hall" in bar_types: return "pub_food"
    if "sports_bar" in bar_types: return "pub_food"
    if "cocktail_bar" in bar_types or "wine_bar" in bar_types: return "snacks"
    if "nightclub" in bar_types or "karaoke" in bar_types: return "none"
    return "snacks"


def good_avoid_for(bar_types: list[str], cat_vibes_entry: dict) -> tuple[list[str], list[str]]:
    good = list(cat_vibes_entry.get("good_for", ["middle"]))
    avoid = list(cat_vibes_entry.get("avoid_for", []))
    # Schema enum uses first_meet/large_groups/sports_watching; the YAML uses hyphens.
    remap = {"first-meet": "first_meet", "large-groups": "large_groups", "sports-watching": "sports_watching"}
    good = [remap.get(g, g) for g in good]
    # Filter good to schema values
    allowed_good = {"start", "middle", "nightcap", "anytime", "large_groups", "date", "first_meet", "pregame"}
    good = [g for g in good if g in allowed_good]
    allowed_avoid = {"date", "first_meet", "conversation", "pregame", "nightcap", "large_groups", "sports_watching"}
    avoid = [remap.get(a, a) for a in avoid if remap.get(a, a) in allowed_avoid]
    return good, avoid


def enrich_one(seed, cat_vibes, hours_yaml, vibe_vocab, note_overrides, pf_overrides, bar_idx):
    # --- Location ---
    loc = BAR_LOCATIONS.get(seed["id"])
    if loc is None:
        raise ValueError(f"Missing BAR_LOCATIONS entry for {seed['id']} ({seed['name']})")
    neighborhood, address = loc
    centroid = NEIGHBORHOODS.get(neighborhood)
    if centroid is None:
        # Fall back to East Village if a stray borough leaks in (e.g., Brooklyn Heights)
        aliases = {"Brooklyn Heights": (40.6969, -73.9938)}
        if neighborhood in aliases:
            centroid = aliases[neighborhood]
        else:
            raise ValueError(f"Unknown neighborhood {neighborhood!r} for {seed['id']}")
    dlat, dlon = deterministic_jitter(seed["id"])
    lat = round(centroid[0] + dlat, 6)
    lon = round(centroid[1] + dlon, 6)

    # --- Category → defaults ---
    cat = seed.get("google_category") or "Bar"
    # Fallback if we don't have a mapping for the exact category
    cat_entry = cat_vibes.get(cat)
    if cat_entry is None:
        cat_entry = cat_vibes["Bar"]
    bar_types = list(cat_entry["bar_type"])

    # --- Vibes: defaults → primary_function override → user_note override ---
    vibes = set(cat_entry["default_vibes"])
    apply_primary_function_overrides(vibes, pf_overrides, seed.get("primary_function"))
    forced_noise = apply_user_note_overrides(vibes, note_overrides, seed.get("user_note"))

    # Sanity: vibes must be a subset of vocab
    all_vocab = set()
    for facet in vibe_vocab["facets"].values():
        all_vocab.update(facet)
    vibes = {v for v in vibes if v in all_vocab}
    # Ensure at least 3 vibes (pad with defaults if needed)
    if len(vibes) < 3:
        vibes.update(["lively", "conversation", "unpretentious"])
    # Cap at 8 — stable deterministic order (sorted)
    vibes_list = sorted(vibes)[:8]

    # --- Noise ---
    noise = forced_noise or cat_entry["default_noise"]

    # --- Hours / happy hour / specials ---
    hours_key = cat_entry["default_hours_key"]
    hours_profile = hours_yaml[hours_key]
    open_hours = build_open_hours(hours_profile)
    happy_hour_windows = build_happy_hour_windows(hours_profile)
    specials = build_specials(hours_profile)

    # --- Price ---
    price_tier = seed.get("normalized_price_tier") or "moderate"
    if price_tier == "unknown":
        price_tier = cat_entry.get("price_skew", "moderate")
    avg_drink = avg_drink_price_for(price_tier, seed.get("estimated_price_range_usd"))

    # --- Other attributes ---
    drink_specs = drink_specialties_for(bar_types, seed["name"])
    drink_cats = drink_categories_for(cat_entry)
    capacity = pick_capacity(bar_types)
    crowd_map = crowd_template(bar_types)
    outdoor = "beer_garden" in bar_types or "outdoor" in vibes
    food_q = food_quality_for(bar_types, cat)
    good, avoid = good_avoid_for(bar_types, cat_entry)

    # Age policy — most NYC bars are 21+; nightclubs after 10pm anyway
    age_policy = "21+"

    # Build description from name + key traits
    desc_parts = [f"{cat}" if cat else "Bar"]
    if seed.get("primary_function"):
        desc_parts.append(f"({seed['primary_function'].replace('_', ' ')})")
    if seed.get("user_note"):
        desc_parts.append(f"— note: {seed['user_note']}")
    description = " ".join(desc_parts)

    return {
        "id": f"bar_{bar_idx:03d}",
        "seed_id": seed["id"],
        "name": seed["name"],
        "neighborhood": neighborhood,
        "address": address,
        "lat": lat,
        "lon": lon,
        "bar_type": bar_types,
        "vibe_tags": vibes_list,
        "price_tier": price_tier,
        "avg_drink_price": avg_drink,
        "drink_specialties": drink_specs,
        "drink_categories_served": drink_cats,
        "noise_level": noise,
        "capacity_estimate": capacity,
        "crowd_level_by_hour": crowd_map,
        "outdoor_seating": outdoor,
        "food_quality": food_q,
        "kitchen_open": None,
        "happy_hour_windows": happy_hour_windows,
        "specials": specials,
        "open_hours": open_hours,
        "age_policy": age_policy,
        "accessibility": {"step_free": None, "accessible_restroom": None},
        "reservations": "accepted" if "cocktail_bar" in bar_types or "lounge" in bar_types else "none",
        "dress_code": "smart_casual" if "lounge" in bar_types or price_tier == "splurge" else "casual",
        "novelty": novelty_for(bar_types, vibes, seed.get("primary_function")),
        "description": description,
        "good_for": good,
        "avoid_for": avoid,
        "google_rating": seed["google_rating"],
        "google_review_count": seed["google_review_count"],
        "google_price_indicator": seed.get("google_price_indicator"),
        "google_category": seed.get("google_category"),
        "quality_signal": 0.0,  # filled in second pass
        "user_note": seed.get("user_note"),
        "primary_function": seed.get("primary_function"),
        "editorial_note": seed.get("editorial_note"),
        "source": "user_seed_list",
    }


def compute_quality_signals(bars: list[dict]):
    """Normalize rating * log10(reviews+1) to [0, 1] across the dataset."""
    raw = [b["google_rating"] * math.log10(b["google_review_count"] + 1) for b in bars]
    lo, hi = min(raw), max(raw)
    span = hi - lo if hi > lo else 1.0
    for b, r in zip(bars, raw):
        b["quality_signal"] = round((r - lo) / span, 4)


def main():
    seed = json.loads((DATA / "seed_bars.json").read_text())
    cat_vibes = yaml.safe_load((DATA / "category_to_vibes.yaml").read_text())
    hours_yaml = yaml.safe_load((DATA / "default_hours.yaml").read_text())
    vibe_vocab = json.loads((DATA / "vibe_vocab.json").read_text())

    note_overrides = cat_vibes.pop("user_note_overrides", [])
    pf_overrides = cat_vibes.pop("primary_function_overrides", {})
    cat_vibes.pop("_version", None)

    included_seeds = [s for s in seed if s.get("include_in_dataset")]
    print(f"Enriching {len(included_seeds)} seeds...")

    bars = []
    for i, s in enumerate(included_seeds, start=1):
        bar = enrich_one(s, cat_vibes, hours_yaml, vibe_vocab, note_overrides, pf_overrides, i)
        bars.append(bar)

    compute_quality_signals(bars)

    # Write a disclaimer header as a comment in a companion README,
    # since JSON doesn't support comments. The disclaimer lives at the
    # top of this file and in the writeup.
    out_path = DATA / "bars.json"
    out_path.write_text(json.dumps(bars, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(bars)} enriched bars to {out_path}")

    # Quick self-check
    assert len(bars) == 143, f"Expected 143 bars, got {len(bars)}"
    user_note_count = sum(1 for b in bars if b.get("user_note"))
    assert user_note_count == 6, f"Expected 6 user notes preserved, got {user_note_count}"
    print(f"  {user_note_count} user_note entries preserved")
    neighborhoods = {b["neighborhood"] for b in bars}
    print(f"  {len(neighborhoods)} unique neighborhoods")


if __name__ == "__main__":
    main()
