"""
Parse the user's pasted Google Maps bar list into structured seed_bars.json.

Run: python scripts/parse_seed.py
Output: data/seed_bars.json
"""

import json
import re
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "seed_bars.json"

# The raw Google Maps text block — preserves provenance for the writeup.
RAW = r"""Slainte | Irish Pub & Restaurant
4.5(1,133)
$20–30
· Irish pub


Sunswick 35/35
4.5(589)
$10–20
· Grill


The Bonnie
4.5(1,670)
$$
· Bar


Bohemian Hall & Beer Garden
4.3(2,311)
$20–30
· Beer Garden


Bushwick Country Club
4.5(236)
$10–20
· Bar


Carmelo’s
4.4(593)
$
· Bar


Banzarbar
4.7(202)
$30–50
· Bar


Bar Orai
4.5(190)
$30–50
· Bar


So & So's Neighborhood Piano Bar
4.4(164)
Restaurant


56709
4.7(204)
Cocktail bar


929
4.7(505)
Cocktail bar


Ask For Janice
4.0(94)
$30–50
· Bar


The Back Room
4.2(2,000)
$10–20
· Cocktail bar


Please Don't Tell
4.3(2,417)
$$$
· Cocktail bar


Cafe Select
4.1(1,108)
$$
· Restaurant


The Sultan Room
4.4(278)
$20–30
· Night club


SILO
4.4(489)
Dance club


Barcade
4.3(1,888)
$$
· Bar


Coyote Ugly New York
4.0(821)
$10–20
· Bar
From never can say goodbye author


Union Hall
4.5(1,627)
$20–30
· Bar


VALERIE
4.4(2,217)
Cocktail bar
can book 20-30 people


LPR
4.4(2,202)
$$$
· Live Music


Turntable LP Bar & Karaoke
4.5(692)
$30–50
· Bar


Studio 151
4.1(268)
$100+
· Bar


Lucy's
4.2(283)
$20–30
· Bar


Elsewhere
4.4(3,003)
Live Music


H0L0
4.3(406)
Live Music


Bossa Nova Civic Club
4.2(693)
$10–20
· Bar


Mood Ring
4.3(478)
$20–30
· Bar


All Night Skate
4.7(513)
$20–30
· Bar


Nowadays
4.3(1,858)
$30–40
· Bar


The Gutter
4.2(995)
$20–30
· Bar


Magic Hour Rooftop Bar & Lounge
3.9(5,252)
$$$
· Lounge bar


Public Records
4.3(1,710)
Restaurant


Record Room
4.1(369)
$100+
· Bar


Puffy's Tavern
4.4(382)
$20–30
· Gastropub


Carneval
4.7(1,172)
$$
· Restaurant


Arlene's Grocery
4.4(1,012)
$$
· Live Music


Sip&Guzzle
4.2(684)
$100+
· Cocktail bar


Webster Hall
4.2(3,105)
$$$
· Live Music


Baby Grand LES
4.7(56)
Bar


Bar Bonobo
4.4(197)
Cocktail bar


Mister Paradise
4.5(324)
Cocktail bar


Jungle Bird
4.5(599)
$$
· Bar
bain top roof takeover


Barcade
4.5(2,328)
$20–30
· Bar


duckduck
4.4(488)
$10–20
· Bar


Pete's Candy Store
4.6(757)
$10–20
· Bar


Stella & Fly
4.8(406)
Permanently closed
laptop friendly


Cafe Balearica
4.1(195)
Bar


Blue & Gold Tavern
4.3(384)
$10–20
· Bar


UES.
3.7(693)
Ice Cream


Speakeasy Magick
4.7(181)
Performing arts theater


Vida Verde - Tequila Bar
4.6(4,210)
$$
· Cocktail bar


Otto's Shrunken Head
4.5(899)
$
· Bar


Cowgirl
4.2(1,565)
$20–30
· Southwestern American


Burp Castle
4.6(707)
$10–20
· Bar
Only whispering allowed lol


The Nines
3.9(336)
$100+
· Restaurant


Beetle House
4.1(2,688)
$50–100
· Bar


Paradise Lost
4.4(470)
$20–30
· Cocktail bar


Sake Bar Decibel
4.4(1,223)
$20–30
· Bar


Motel No Tell
4.5(274)
$$
· Bar


Space Billiard Pool Hall & Sports Bar | Koreatown NYC
4.5(2,251)
$$
· Pool hall


Space Ping Pong Sports Bar & Lounge | Koreatown NYC
4.7(1,180)
Sports bar


BELTANE
4.7(120)
Cocktail bar


Ethyl's Bar & Restaurant
4.1(748)
$20–30
· Grill


McSorley’s Old Ale House
4.7(8,665)
$10–20
· Pub


Loreley Beer Garden
4.2(1,987)
$$
· Beer Garden


Rosie Dunn's Victorian Pub
4.5(348)
$20–30
· Irish pub


The Gem Saloon
4.4(1,150)
$20–30
· Bar


Celtic Pub Restaurant
3.9(275)
Irish pub


Pig 'N' Whistle Public House
4.5(2,024)
$20–30
· Irish pub


The Mean Fiddler
4.4(3,657)
$$
· Irish pub


Sunken Harbor Club
4.7(351)
Cocktail bar


Beer Authority
4.3(4,491)
$20–30
· Sports bar


Madame George
4.7(531)
$20–30
· Cocktail bar


The Rhymers' Club
4.9(51)
$1–10
· Cocktail bar


11th St. Bar
4.5(494)
$20–30
· Bar


Maiden Lane
4.4(199)
$10–20
· Bar


Sing Sing Ave A.
4.0(695)
$$
· Karaoke bar


169 Bar
4.1(1,250)
$
· Bar


Planet Rose
4.1(402)
$
· Karaoke bar


La Caverna
4.1(772)
$
· Lounge bar


Von
4.4(614)
$10–20
· Bar


Eastpoint Bar
4.5(238)
$20–30
· Bar


Double Down Saloon
4.3(615)
$10–20
· Bar


The Five Lamps
4.5(243)
$20–30
· Bar


George & Jack's Tap Room
4.4(395)
$20–30
· Bar


Pioneers Bar NYC
4.2(1,192)
$10–20
· Sports bar


The Stumble Inn
4.3(1,499)
$20–30
· Sports bar


Downtown Social
4.2(133)
$10–20
· Sports bar


AWOL Bar & Grill
4.0(368)
$20–30
· Karaoke bar


The Ragtrader & Bo Peep Cocktail and Highball Store
4.7(2,910)
$$
· Bar


Fairweather
4.2(21)
$20–30
· Cocktail bar


In Vino Veritas
4.6(36)
$$
· Wine store


Lillie's Victorian Establishment
4.3(3,680)
$$
· Restaurant


e's BAR
4.4(989)
$20–30
· Bar


Jake's Dilemma
4.0(1,527)
$20–30
· Sports bar


Prohibition
4.5(1,035)
$$
· New American


Surf Bar Seafood Restaurant & Grill
4.4(1,295)
$20–30
· Seafood


Spritzenhaus33
4.3(1,465)
$20–30
· Bar


O'Hara's Restaurant and Pub
4.6(5,666)
$20–30
· American


Verlaine
4.4(1,143)
$$
· Cocktail bar


Cellar Dog
4.3(664)
$10–20
· Bar


Brandy's Piano Bar
4.5(611)
$20–30
· Piano bar


Treadwell Park
4.4(1,611)
$20–30
· Beer hall


Mulligan's Pub
4.4(1,247)
$20–30
· Irish pub


Local 42 Bar
4.4(201)
$10–20
· Bar


Valhalla NYC
4.4(1,453)
$20–30
· Grill


Empanada Mama Hell’s Kitchen
4.4(5,677)
$$
· Latin American


Dutch Fred's
4.6(2,765)
$$
· Cocktail bar


As Is NYC
4.6(1,052)
$20–30
· Bar


Beer Culture
4.6(1,269)
$10–20
· Bar


Rudy's Bar & Grill
4.6(6,141)
$10–20
· Grill


Maiz
4.5(1,166)
$20–30
· Mexican


The Uncommons
4.6(1,606)
$
· Board game club


Bar Pisellino
4.1(923)
$20–30
· Bar


230 Fifth Rooftop Bar
4.3(24,910)
$$
· Bar


Old Mates Pub
4.5(425)
Pub


Poco NYC
4.3(1,449)
Permanently closed


Oscar Wilde
4.3(5,838)
$$$
· New American


Bibliotheque
4.4(527)
$10–20
· Wine bar


La Esquina Brasserie
4.1(2,805)
$$
· Mexican


Pineapple Club
4.7(1,157)
$$
· Cocktail bar


msocial Rooftop
4.5(179)
$20–30
· Bar


Lost in Paradise Rooftop
4.7(6,621)
$$
· Cocktail bar


The Flatiron Room NoMad
4.5(1,973)
$100+
· American


Iggy's
4.3(715)
$
· Karaoke bar


Hold Fast Kitchen and Spirits
4.7(929)
$20–30
· Bar


The Pony Bar
4.5(810)
$20–30
· Bar


Patent Pending
4.4(1,090)
$20–30
· Cocktail bar


Avoca
4.8(1,770)
$$
· Bar


Clemente Bar
4.7(208)
$100+
· Cocktail bar


A la Turka
4.3(1,166)
$$
· Mediterranean


Ray’s
4.1(439)
Bar


Wicked Willy's
4.1(1,651)
$$
· American


Down the Hatch
4.3(1,486)
$20–30
· Grill


DROM
4.1(1,177)
$$
· Live Music
went for ary's birthday


The Skinny Bar and Lounge
3.9(663)
$20–30
· Bar


Bayard's Ale House
4.1(357)
$$
· Irish pub


House of Yes
4.5(3,922)
Event venue


Reichenbach Hall
4.6(7,089)
$$
· German


The Standard Biergarten
4.1(1,753)
$30–40
· Beer Garden


The Penny Farthing
4.2(1,088)
$20–30
· Sports bar
good m-th deals like th aperol spritz


Bar Bianchi
4.0(163)
$50–100
· Restaurant
Expensive but nice and preppy


Cafe Cluny
4.4(1,365)
$$
· Modern French


Quique Crudo
4.6(212)
$100+
· Mexican


Papatzul Soho
4.1(876)
Permanently closed


Taqueria Ramirez
4.7(1,889)
$10–20
· Mexican


VICTORIA!
4.8(75)
$20–30
· Bar


La Victoria NYC
3.5(277)
$100+
· Night club


Good Room
4.1(578)
$40–50
· Night club


Mr. Purple
4.1(3,138)
$$$
· Bar


Wiggle Room
3.4(272)
$20–30
· Bar


The Spaniard
4.2(1,597)
$20–30
· Bar


Brass Monkey
4.1(2,074)
$20–30
· Bar


Overlook
4.1(591)
$20–30
· Sports bar


The Factory 380
4.5(512)
$20–30
· Cocktail bar


Evergreen Museum & Library
4.6(141)
Museum


Peculier Pub
4.4(1,096)
$10–20
· Pub
"""

# Editorial decisions on ambiguous entries
EDITORIAL_DECISIONS = {
    "Sunswick 35/35": {"keep": True, "primary_function": "neighborhood_bar",
        "rationale": "Classic Astoria craft beer bar; 'Grill' is a food-service label."},
    "So & So's Neighborhood Piano Bar": {"keep": True, "primary_function": "piano_bar",
        "rationale": "Piano bar is the primary draw; food is incidental."},
    "Public Records": {"keep": True, "primary_function": "music_cocktail_venue",
        "rationale": "Famous Gowanus sound-system bar with food menu; bar is the draw."},
    "Speakeasy Magick": {"keep": True, "primary_function": "themed_cocktail_experience",
        "rationale": "Cocktail-focused magic show at the McKittrick; drinking venue."},
    "Cowgirl": {"keep": True, "primary_function": "themed_bar_restaurant",
        "rationale": "West Village institution known for its bar scene and margaritas."},
    "Ethyl's Bar & Restaurant": {"keep": True, "primary_function": "themed_bar",
        "rationale": "Bar in name; UES 70s-themed bar is the hook."},
    "Lillie's Victorian Establishment": {"keep": True, "primary_function": "themed_bar",
        "rationale": "Iconic Victorian bar; restaurant label undersells the bar scene."},
    "Prohibition": {"keep": True, "primary_function": "bar_with_food",
        "rationale": "UWS cocktail bar; name makes it obvious."},
    "Surf Bar Seafood Restaurant & Grill": {"keep": True, "primary_function": "tiki_bar",
        "rationale": "Williamsburg tiki bar with sand on the floor."},
    "O'Hara's Restaurant and Pub": {"keep": True, "primary_function": "irish_pub",
        "rationale": "FiDi Irish pub; September 11 firefighters' bar."},
    "Valhalla NYC": {"keep": True, "primary_function": "craft_beer_bar",
        "rationale": "Hell's Kitchen beer bar with 46+ taps."},
    "Rudy's Bar & Grill": {"keep": True, "primary_function": "dive_bar",
        "rationale": "Iconic cheap Hell's Kitchen dive with free hot dogs."},
    "The Uncommons": {"keep": True, "primary_function": "board_game_bar",
        "rationale": "Board game cafe with full bar; niche but bar-forward."},
    "Oscar Wilde": {"keep": True, "primary_function": "themed_bar",
        "rationale": "Flatiron literary-themed bar; 'New American' mislabels it."},
    "The Flatiron Room NoMad": {"keep": True, "primary_function": "whiskey_bar",
        "rationale": "Premier whiskey lounge; food is secondary."},
    "Wicked Willy's": {"keep": True, "primary_function": "themed_party_bar",
        "rationale": "Village pirate-themed party bar."},
    "Hold Fast Kitchen and Spirits": {"keep": True, "primary_function": "bar_with_food",
        "rationale": "Hell's Kitchen cocktail bar; name signals both."},
    "Down the Hatch": {"keep": True, "primary_function": "sports_bar",
        "rationale": "Village sports bar; 'Grill' is food-service label."},
    "The Nines": {"keep": True, "primary_function": "lounge_bar",
        "rationale": "NoHo cocktail lounge; restaurant label is weak."},

    # Excluded — primarily restaurants/non-bars
    "Cafe Select": {"keep": False, "rationale": "Primarily a Swiss restaurant; bar is incidental."},
    "Carneval": {"keep": False, "rationale": "Italian restaurant; food-forward."},
    "UES.": {"keep": False, "rationale": "Ice cream shop, not a bar."},
    "In Vino Veritas": {"keep": False, "rationale": "Wine store, not a wine bar."},
    "Cafe Cluny": {"keep": False, "rationale": "French restaurant; food-primary."},
    "Quique Crudo": {"keep": False, "rationale": "Mexican restaurant; not a drinks venue."},
    "Taqueria Ramirez": {"keep": False, "rationale": "Mexican taqueria; no bar program."},
    "La Esquina Brasserie": {"keep": False, "rationale": "Primarily a Mexican restaurant."},
    "Empanada Mama Hell\u2019s Kitchen": {"keep": False, "rationale": "Empanada-focused restaurant; drinks secondary."},
    "Maiz": {"keep": False, "rationale": "Mexican restaurant."},
    "A la Turka": {"keep": False, "rationale": "Mediterranean restaurant."},
    "Bar Bianchi": {"keep": False, "rationale": "Despite 'bar' in name, primarily an Italian restaurant per user note."},
    "Evergreen Museum & Library": {"keep": False, "rationale": "It's a museum; not a bar."},
}

PRICE_RATING_RE = re.compile(r"^(\d+\.\d+)\s*\(\s*([\d,]+)\s*\)$")
PRICE_INDICATORS = {"$", "$$", "$$$", "$$$$"}
PRICE_RANGE_RE = re.compile(r"^\$[\d]+[\u2013\-][\d]+\+?$|^\$[\d]+\+$|^\$\d+[\u2013\-]\d+$")


def normalize_price(indicator):
    if indicator is None:
        return "unknown", None
    s = indicator.strip().replace("\u2013", "-")
    if s == "$":  return "cheap", (1, 10)
    if s == "$$": return "moderate", (10, 25)
    if s == "$$$": return "premium", (25, 50)
    if s == "$$$$": return "splurge", (50, 150)
    m = re.match(r"^\$(\d+)-(\d+)$", s)
    if m:
        low, high = int(m.group(1)), int(m.group(2))
        mid = (low + high) / 2
        if mid < 10: tier = "cheap"
        elif mid < 25: tier = "moderate"
        elif mid < 50: tier = "premium"
        else: tier = "splurge"
        return tier, (low, high)
    m = re.match(r"^\$(\d+)\+$", s)
    if m:
        low = int(m.group(1))
        return ("splurge" if low >= 50 else "premium"), (low, low * 3)
    return "unknown", None


def parse_block(block, idx):
    lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    name = lines[0]
    m = PRICE_RATING_RE.match(lines[1])
    if not m:
        return None
    rating = float(m.group(1))
    review_count = int(m.group(2).replace(",", ""))
    remaining = lines[2:]

    price_indicator = None
    category = None
    status = "active"
    extra_notes = []
    saw_closed = False

    for line in remaining:
        s = line.strip()
        if s.lower() == "permanently closed":
            status = "permanently_closed"
            saw_closed = True
            continue
        if s.startswith("$") and PRICE_RANGE_RE.match(s):
            price_indicator = s
            continue
        if s in PRICE_INDICATORS:
            price_indicator = s
            continue
        if s.startswith("·"):
            category = s.lstrip("·").strip()
            continue
        if category is None and not saw_closed:
            if len(s) < 40 and not any(c.isdigit() for c in s):
                category = s
                continue
        extra_notes.append(s)

    user_note = " | ".join(extra_notes) if extra_notes else None
    price_tier, price_range = normalize_price(price_indicator)

    cat_lower = (category or "").lower()
    bar_keywords = ["bar", "pub", "cocktail", "tavern", "beer", "lounge", "saloon",
                    "karaoke", "piano", "sports bar", "night club", "dance club",
                    "beer garden", "beer hall", "pool hall", "live music", "event venue",
                    "gastropub", "wine bar"]
    non_bar_keywords = ["museum", "ice cream", "wine store"]
    is_primarily_bar = None
    if cat_lower:
        if any(k in cat_lower for k in non_bar_keywords):
            is_primarily_bar = False
        elif any(k in cat_lower for k in bar_keywords):
            is_primarily_bar = True
        else:
            is_primarily_bar = None
    needs_review = (status != "permanently_closed") and (is_primarily_bar is not True)

    editorial = EDITORIAL_DECISIONS.get(name)
    primary_function = None
    if editorial:
        if editorial["keep"]:
            include_in_dataset = True
            is_primarily_bar = True
            needs_review = False
            editorial_note = editorial["rationale"]
            primary_function = editorial.get("primary_function")
        else:
            include_in_dataset = False
            editorial_note = editorial["rationale"]
    else:
        if status == "permanently_closed":
            include_in_dataset = False
            editorial_note = "Permanently closed per Google Maps."
        elif is_primarily_bar is True:
            include_in_dataset = True
            editorial_note = None
        else:
            include_in_dataset = True
            editorial_note = "Ambiguous — Claude Code should verify bar-primacy in Phase 1."

    return {
        "id": f"seed_{idx:03d}",
        "name": name,
        "google_rating": rating,
        "google_review_count": review_count,
        "google_price_indicator": price_indicator,
        "normalized_price_tier": price_tier,
        "estimated_price_range_usd": list(price_range) if price_range else None,
        "google_category": category,
        "user_note": user_note,
        "status": status,
        "is_primarily_bar": is_primarily_bar,
        "needs_review": needs_review,
        "include_in_dataset": include_in_dataset,
        "editorial_note": editorial_note,
        "primary_function": primary_function,
        "source": "user_seed_list",
        "neighborhood": None,
        "address": None,
        "lat": None,
        "lon": None,
        "vibe_tags": [],
        "drink_specialties": [],
        "avg_drink_price": None,
        "noise_level": None,
        "happy_hour_windows": [],
        "specials": [],
        "open_hours": None,
        "capacity_estimate": None,
        "crowd_level_by_hour": {},
        "age_policy": "21+",
        "accessibility": {"step_free": None, "accessible_restroom": None},
        "reservations": "unknown",
        "dress_code": "unknown",
        "outdoor_seating": None,
        "food_quality": None,
        "kitchen_open": None,
        "novelty": None,
        "good_for": [],
        "avoid_for": [],
        "description": None,
    }


def main():
    blocks = re.split(r"\n\s*\n", RAW.strip())
    parsed = []
    for i, block in enumerate(blocks, start=1):
        result = parse_block(block, i)
        if result is not None:
            parsed.append(result)

    included = [b for b in parsed if b.get("include_in_dataset")]
    excluded_closed = [b for b in parsed if b.get("status") == "permanently_closed"]
    excluded_editorial = [b for b in parsed if not b.get("include_in_dataset") and b.get("status") != "permanently_closed"]
    notes_count = sum(1 for b in included if b.get("user_note"))

    print(f"Total entries parsed: {len(parsed)}")
    print(f"  Included: {len(included)}")
    print(f"  Excluded (closed): {len(excluded_closed)}")
    print(f"  Excluded (editorial): {len(excluded_editorial)}")
    print(f"  Entries with user notes: {notes_count}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(parsed, indent=2, ensure_ascii=False))
    print(f"Wrote {len(parsed)} entries to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
