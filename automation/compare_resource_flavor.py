"""Resolve and compare the System A resource/forest "flavor" of stock maps.

Motivation: porting a resource template imports that map's *flavor*, not just
its correctness. Arabia is the de-facto baseline - simple, uniform, and fair
almost by construction - and most other maps are describable as tweaks
relative to it. This script makes those tweaks explicit instead of leaving
them to eyeball comparison.

Method. System A parameterises every include through ``#const`` names with
first-definition-wins semantics: an include declares ``/* Default Values */``
that apply only if the calling map has not already declared that name. So a
map's resolved value for a parameter is::

    map override, if the map declares it; otherwise the include default

This script parses both sides and prints the resolved table.

Honest limits of the parse (do not over-trust the output):

* Conditional branches are flattened. A ``#const`` inside ``if DEATH_MATCH``
  and a different one inside ``else`` both get recorded; such names are
  reported with a ``*`` and all observed values, not silently collapsed.
* ``start_random`` / ``percent_chance`` blocks mean some parameters are drawn
  per-generation. Those are marked ``rnd``.
* Arithmetic expressions (``(X * MAPSCALE_AREA)``) are reported verbatim, not
  evaluated.

This is a static read of the scripts. It is not a substitute for a real
engine render - see CLAUDE.md.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

STOCK = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\AoE2DE"
    r"\resources\_common\drs\gamedata_x2"
)

#: Donor candidates, keyed by the name the game's UI shows (script names and
#: UI names diverge - real_world_manchuria ships as "Great Wall").
#:
#: Ordered roughly from "least like us" to "most like us". Arabia is the
#: usual mental baseline but it has *no water at all*, and every map we
#: generate is a coastline - so Arabia is the wrong reference for anything
#: water-touching. Thames and Loch Ness are the closest structural analogs:
#: System A + direct_placement + explicit create_land + irregular water.
DONORS: dict[str, str] = {
    "Arabia": "Arabia.rms",
    "Black Forest": "Black_Forest.rms",
    "Scandinavia": "Scandanavia.rms",
    "Great Wall (manchuria)": "real_world_manchuria.rms",
    "Coastal": "Coastal.rms",
    "Baltic": "Baltic.rms",
    "Team Islands": "Team_Islands.rms",
    "Paradise Island": "Paradise Island.rms",
    "Loch Ness": "Loch Ness.rms",
    "Thames": "Thames.rms",
}

#: Parameters worth comparing, grouped for readable output. The include that
#: supplies each default is looked up automatically.
GROUPS: dict[str, list[str]] = {
    "spacing (global)": [
        "RESOURCE_SPACING_DEFAULT",
        "RESOURCE_SPACING_FAR",
        "RESOURCE_RESTRICTION",
    ],
    "forage": [
        "FORAGE_BUSH_PRIMARY_SIZE",
        "FORAGE_BUSH_PRIMARY_DISTANCE",
        "FORAGE_BUSH_PRIMARY_ZONE_DISTANCE",
        "FORAGE_BUSH_SECONDARY_DISTANCE",
        "FORAGE_BUSH_TERTIARY_DISTANCE",
        "FORAGE_BUSH_ADDITIONAL_DISTANCE",
    ],
    "gold": [
        "GOLD_PRIMARY_SIZE",
        "GOLD_PRIMARY_DISTANCE",
        "GOLD_PRIMARY_ZONE_DISTANCE",
        "GOLD_SECONDARY_DISTANCE",
        "GOLD_TERTIARY_DISTANCE",
        "GOLD_ADDITIONAL_DISTANCE",
        "GOLD_ADDITIONAL_EDGE_DISTANCE",
    ],
    "stone": [
        "STONE_PRIMARY_SIZE",
        "STONE_PRIMARY_DISTANCE",
        "STONE_PRIMARY_ZONE_DISTANCE",
        "STONE_SECONDARY_DISTANCE",
        "STONE_ADDITIONAL_DISTANCE",
    ],
    "food animals": [
        "HERDABLE_STARTING_DISTANCE",
        "HERDABLE_STARTING_ZONE_DISTANCE",
        "HERDABLE_DISTANCE",
        "HUNTABLE_COUNT",
        "HUNTABLE_DISTANCE",
        "HUNTABLE_SMALL_COUNT",
        "HUNTABLE_SMALL_GROUPS",
        "HUNTABLE_SMALL_DISTANCE",
        "LUREABLE_COUNT",
        "LUREABLE_GROUPS",
        "LUREABLE_DISTANCE",
    ],
    "trees / wood": [
        "STRAGGLER_SPAWN_COUNT",
        "PLAYER_FOREST_BASE_COUNT",
        "PLAYER_FOREST_CLUMPS",
        "PLAYER_FOREST_TILES",
        "PLAYER_FOREST_AVOIDANCE",
        "PLAYER_FOREST_TEAM_DEDUCTION",
    ],
    "water food": [
        "NERITIC_SPACING",
        "SALTWATER_COUNT",
        "SALTWATER_SPACING",
        "SALTWATER_ZONE_DISTANCE",
        "WHALE_COUNT",
        "WHALE_ZONE_DISTANCE",
        "OYSTER_COUNT",
    ],
    "remote fill": [
        "REMOTE_DISTANCE",
        "REMOTE_SPACING",
    ],
}

#: Tier / feature switches - presence is the signal, not a value.
SWITCHES = [
    "FORAGE_BUSH_PRIMARY", "FORAGE_BUSH_SECONDARY", "FORAGE_BUSH_TERTIARY",
    "FORAGE_BUSH_ADDITIONAL",
    "GOLD_PRIMARY", "GOLD_SECONDARY", "GOLD_TERTIARY", "GOLD_ADDITIONAL",
    "STONE_PRIMARY", "STONE_SECONDARY", "STONE_TERTIARY", "STONE_ADDITIONAL",
]

#: Includes whose presence changes what exists on the map at all.
FEATURE_INCLUDES = [
    "stragglers.inc", "starting_resources.inc", "herdable_starting.inc",
    "herdable.inc", "huntable.inc", "lureable.inc", "furbearers.inc",
    "predators.inc", "riparian.inc", "neritic.inc", "oysters.inc",
    "whales.inc", "aquatic_freshwater.inc", "aquatic_saltwater.inc",
    "stragglers_neutral.inc", "stragglers_coastal.inc", "reeds.inc",
    "relics.inc", "remote_resources.inc", "forest.inc", "cliffs.inc",
    "water_preset.inc", "coastal_blending.inc", "water_blending.inc",
]

#: How the map lays out land and water. This is the axis Arabia cannot speak
#: to (it has no water) and the one our real-coastline maps differ on most.
#: Every competitive stock water map is rotationally regular about the map
#: centre; ours are real coastlines and are not regular in any way.
TOPOLOGY_MARKERS = [
    ("direct_placement", r"\bdirect_placement\b"),
    ("random_placement", r"\brandom_placement\b"),
    ("grouped_by_team", r"\bgrouped_by_team\b"),
    ("create_player_lands", r"\bcreate_player_lands\b"),
    ("circle_radius", r"\bcircle_radius\b"),
    ("set_circular_base", r"\bset_circular_base\b"),
    ("explicit land_position", r"\bland_position\b"),
    ("symmetric *_border", r"\b(top|bottom|left|right)_border\b"),
    ("set_zone_by_team", r"\bset_zone_by_team\b"),
    ("zone / land_id", r"\b(zone|land_id)\s+\d"),
    ("water base_terrain", r"base_terrain\s+(WATER\w*|SHALLOWS)"),
    ("WATER_POND", r"#define\s+WATER_POND"),
    ("WATER_OCEAN", r"#define\s+WATER_OCEAN"),
    ("WATER_DEFAULT", r"#define\s+WATER_DEFAULT"),
]

_CONST = re.compile(r"^\s*#const\s+([A-Z_][A-Z0-9_]*)\s+(.+?)\s*$", re.M)
_DEFINE = re.compile(r"^\s*#define\s+([A-Z_][A-Z0-9_]*)\s*$", re.M)
_INCLUDE = re.compile(r"#include_drs\s+(\S+)")


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def scan(path: Path) -> dict:
    """Collect consts, defines and includes from one script."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = _strip_comments(raw)

    consts: dict[str, list[str]] = {}
    for name, value in _CONST.findall(text):
        consts.setdefault(name, [])
        if value not in consts[name]:
            consts[name].append(value)

    defines = set(_DEFINE.findall(text))
    includes = {Path(p).name for p in _INCLUDE.findall(text)}

    # Which names sit inside a start_random block (so vary per generation).
    random_names: set[str] = set()
    for block in re.findall(r"start_random(.*?)end_random", text, flags=re.S):
        random_names |= set(n for n, _ in _CONST.findall(block))
        random_names |= set(_DEFINE.findall(block))

    topology = {
        label: bool(re.search(pat, text))
        for label, pat in TOPOLOGY_MARKERS
    }
    # ai_info_map_type is worth seeing next to the water topology - several
    # water maps declare ARABIA regardless of how wet they are.
    m = re.search(r"ai_info_map_type\s+([A-Z_]+)", text)
    topology["ai_info_map_type"] = m.group(1) if m else "-"

    return {
        "consts": consts,
        "defines": defines,
        "includes": includes,
        "random": random_names,
        "topology": topology,
    }


def include_defaults(inc_dir: Path) -> dict[str, tuple[str, str]]:
    """Map parameter name -> (default value, defining include)."""
    out: dict[str, tuple[str, str]] = {}
    for inc in sorted(inc_dir.glob("*.inc")):
        text = _strip_comments(inc.read_text(encoding="utf-8", errors="replace"))
        for name, value in _CONST.findall(text):
            out.setdefault(name, (value, inc.name))
    return out


def render(donors: dict[str, str], stock: Path) -> str:
    defaults = include_defaults(stock / "includes")
    scans = {label: scan(stock / f) for label, f in donors.items()}
    labels = list(donors)

    # Column width driven by the longest cell actually printed.
    def cell(label: str, name: str) -> str:
        s = scans[label]
        if name in s["consts"]:
            vals = s["consts"][name]
            txt = "|".join(vals)
            if len(vals) > 1:
                txt = "*" + txt
            if name in s["random"]:
                txt = "rnd:" + txt
            return txt
        return "."

    lines: list[str] = []
    lines.append("System A resource/forest flavor - resolved parameters")
    lines.append("")
    lines.append("Legend:  .  = uses include default (shown in the default column)")
    lines.append("         *  = script sets this in more than one branch; all values shown")
    lines.append("       rnd: = chosen inside a start_random block, varies per generation")
    lines.append("")

    head = f"{'parameter':<34} {'default':>10}  " + "  ".join(
        f"{l[:17]:>17}" for l in labels
    )
    lines.append(head)
    lines.append("-" * len(head))

    for group, names in GROUPS.items():
        lines.append("")
        lines.append(f"## {group}")
        for name in names:
            dflt, _src = defaults.get(name, ("-", ""))
            dflt = dflt if len(dflt) <= 10 else dflt[:9] + "~"
            row = f"{name:<34} {dflt:>10}  " + "  ".join(
                f"{cell(l, name)[:17]:>17}" for l in labels
            )
            lines.append(row)

    lines.append("")
    lines.append("## resource tiers requested (#define)")
    for sw in SWITCHES:
        marks = "  ".join(
            f"{('yes' if sw in scans[l]['defines'] else '-'):>17}" for l in labels
        )
        lines.append(f"{sw:<34} {'':>10}  {marks}")

    lines.append("")
    lines.append("## feature includes present")
    for inc in FEATURE_INCLUDES:
        marks = "  ".join(
            f"{('yes' if inc in scans[l]['includes'] else '-'):>17}" for l in labels
        )
        lines.append(f"{inc:<34} {'':>10}  {marks}")

    lines.append("")
    lines.append("## land/water topology  (the axis Arabia cannot speak to)")
    lines.append(f"{'ai_info_map_type':<34} {'':>10}  " + "  ".join(
        f"{scans[l]['topology']['ai_info_map_type'][:17]:>17}" for l in labels
    ))
    for label, _pat in TOPOLOGY_MARKERS:
        marks = "  ".join(
            f"{('yes' if scans[l]['topology'][label] else '-'):>17}" for l in labels
        )
        lines.append(f"{label:<34} {'':>10}  {marks}")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock", type=Path, default=STOCK,
                    help="path to gamedata_x2")
    ap.add_argument("--out", type=Path, default=None,
                    help="write to this file instead of stdout")
    args = ap.parse_args()

    missing = [f for f in DONORS.values() if not (args.stock / f).exists()]
    if missing:
        raise SystemExit(f"missing donor scripts under {args.stock}: {missing}")

    text = render(DONORS, args.stock)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
