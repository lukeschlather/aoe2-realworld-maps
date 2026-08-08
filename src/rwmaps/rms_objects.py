"""System A (modern) resource generation for generated real-world maps.

Replaces the ``#include_drs land_and_water_resources.inc`` layer that every
script this project produced until now. Per ``STOCK_MAP_INVENTORY.md`` that
include is a **1999 orphan**: its own header reads ``24 JUNE 99``, no
shipping map has referenced it in at least five years, and it predates
``find_closest``, ``require_path``, ``enable_tile_shuffling`` and the actor
area system entirely. It tries one spot per object and gives up silently -
which is the whole silent-placement-failure bug class this project kept
re-discovering, not a subtlety of tuning.

What replaces it is the machinery the current stock maps actually use,
assembled per ``RESOURCE_TEMPLATES.md``'s port shape:

* **mechanism** from System A generally - ``find_closest`` +
  ``set_circular_placement`` + ``enable_tile_shuffling`` + ``require_path``,
  so a placement that does not fit searches instead of vanishing;
* **calling convention** from ``Thames.rms``, the closest structural
  analogue in the game (``direct_placement``, explicit ``create_land`` with
  ``zone``/``land_id``, irregular river water - exactly what
  ``rms_land.py`` emits);
* **the water-constrained-land lever** from ``Loch Ness.rms``, which raises
  ``*_ZONE_DISTANCE`` to 14 rather than cutting spacing;
* **resource budget** chosen explicitly per region as flavor, never
  inherited by accident - the mistake that made ``Great Wall`` look like a
  good wholesale donor when it ships *no straggler trees at all*.

Two things this deliberately does NOT copy:

* ``themes.inc``'s per-generation biome re-skinning. Every role is pinned
  to one unit id up-front (first-definition-wins, the same trick
  ``real_world_britain.rms`` uses with ``#const HERDABLE_A 594``), so a
  region looks the same every generation and ``scx_read.RESOURCE_UNITS``
  stays exact.
* Great Wall's PRIMARY-only austerity. Stragglers are included.

Verified before writing: ``#include_drs includes/<name>.inc`` **does**
resolve from a mod directory, against a control - see the probe recorded in
``RESOURCE_TEMPLATES.md``. That was the one open blocker on this port.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ThemePins:
    """One unit id per resource role, pinned so a region generates the same
    way every time. Ids are the ``themes.inc`` role constants; see
    ``scx_read.RESOURCE_UNITS`` for the full role -> id tables."""

    forage_plant: int = 59      # Berry Bush
    herdable: int = 594         # Sheep
    huntable: int = 65          # Deer
    huntable_small: int = 2100  # Arctic Hare
    lureable: int = 48          # Wild Boar
    straggler_tree: int = 349   # TREE_OAK
    neritic: int = 69           # Shore Fish
    saltwater: int = 457        # Tuna
    freshwater: int = 456       # Salmon
    whale: int = 2625           # Whale
    #: A ``themes.inc`` branch still has to be named or the include has no
    #: biome to fall through; the pins above outrank whatever it sets.
    theme: str = "PALAEARCTIC_EUROPE_TEMPERATE"
    #: ``water_preset.inc`` mood. WATER_POND is what both irregular-water
    #: donors (Thames, Loch Ness) use; open coastlines want WATER_OCEAN.
    water_preset: str = "WATER_OCEAN"
    beach_terrain: int = 2      # BEACH


@dataclass
class ResourceFlavor:
    """How generous and how tightly packed a region's resources are.

    Defaults are the *common* stock values rather than Arabia's. Arabia is
    deliberately the loosest map in the game on spacing (12/18 against a
    field of 10/16-18), because it is the open, uncluttered one - it is a
    poor default for a coastline.
    """

    #: Actor-area radius around each resource pile. Stock values run 6
    #: (Black Forest, Great Wall) to 12 (Arabia, the loosest map in the
    #: game). 4 is below the whole stock range, chosen from measurement
    #: rather than taste: on a real coastline the ring a resource is asked
    #: to place in is often mostly water, and spacing is what decides
    #: whether a second pile still fits on the land that is left. On
    #: Britain, dropping 6 -> 4 (everything else held) took the number of
    #: players missing at least one resource kind from 6 of 8 to 1 of 8.
    spacing_default: int = 4
    spacing_far: int = 10
    restriction: int = 1

    #: ``max_distance_to_other_zones`` for every requested tier.
    #:
    #: **Leave this at the stock 4.** RESOURCE_TEMPLATES.md recommends
    #: raising it to 14 "per Loch Ness ... the lever aimed at
    #: water-constrained land", reasoning from a static read of the include
    #: files. A real-engine sweep on one fixed coastline says that is
    #: backwards - raising it does not loosen placement, it destroys it:
    #:
    #: | value | forage | gold | stone |
    #: |-------|--------|------|-------|
    #: | 4     | 42     | 25   | 5     |
    #: | 14    | 6      | 0    | 0     |
    #: | 40    | 0      | 0    | 0     |
    #: | 99    | 0      | 0    | 0     |
    #:
    #: (totals across 8 players, one sample each.) Whatever the engine
    #: actually does with this value, "bigger is more permissive" is not it.
    #: This is exactly the case CLAUDE.md is about: the include files are
    #: not a substitute for a render.
    zone_distance: int = 4

    #: Which tiers to request. More tiers = a richer map. Great Wall asks
    #: for PRIMARY only; Arabia asks for six plus two additional.
    forage_tiers: tuple[str, ...] = ("PRIMARY",)
    gold_tiers: tuple[str, ...] = ("PRIMARY", "SECONDARY")
    stone_tiers: tuple[str, ...] = ("PRIMARY", "SECONDARY")

    #: Per-tier ``min_distance_to_players`` overrides; leave a tier out to
    #: take the include's own default (forage 12, gold 12/22/28, stone 12).
    #:
    #: The defaults below pull every ring in toward the town centre. The
    #: stock rings assume land in every direction at that radius; on a
    #: coastline the far ones frequently land in open water and the
    #: placement is simply lost. Measured on Britain, pulling gold/stone
    #: primary 12 -> 9 and secondary 22 -> 16 roughly doubled placed stone.
    distances: dict[str, int] = field(default_factory=lambda: {
        "FORAGE_BUSH_PRIMARY": 10,
        "GOLD_PRIMARY": 9,
        "STONE_PRIMARY": 9,
        "GOLD_SECONDARY": 16,
        "STONE_SECONDARY": 16,
    })

    herdable_starting_count: str = "FOUR"
    huntable_count: int = 4
    huntable_groups: int = 1
    huntable_distance: int | None = None
    huntable_small_count: int = 0
    huntable_small_groups: int = 1
    lureable: bool = True
    #: ``LUREABLE_DISTANCE`` - how far out boar are placed (include default
    #: 18). Boar are the biggest single early food source and the one a
    #: player most notices missing, so on a fragmented coastline this wants
    #: pulling in toward land that actually exists. Great Wall uses 12.
    #: Measured on Britain: 18 -> 12 removed the last remaining
    #: zero-of-a-kind (one player with no boar) and cut mean walking
    #: distance to the nearest boar from 18.0 tiles to 11.2.
    lureable_distance: int | None = 12
    lureable_groups: int | None = 2

    #: Shore fish spacing - lower packs more in. Every map here is a
    #: coastline, so this is a real part of the food budget, not garnish.
    neritic_spacing: int = 20
    saltwater_count: int = 1024
    freshwater_count: int = 0
    whale_count: int = 0

    relics: bool = True
    #: The "fill the leftover space" pass. Great Wall runs it
    #: unconditionally because it is land-starved; Arabia gates it behind
    #: SPACIOUS_SETUP. Our narrow regions are closer to Great Wall.
    remote_resources: bool = True


def build_prelude(pins: ThemePins) -> str:
    """Everything above ``<PLAYER_SETUP>``: role pins, then the theme and
    constant includes. Order matters - ``#const`` is first-definition-wins,
    so pins must come before ``themes.inc`` to outrank it."""
    return f"""\
#const MAPSCALE_MODIFIER 1
#include_drs includes/scaling.inc

#define {pins.theme}

/* Every resource role pinned to one skin, so a region looks the same in
 * every generation and the analysis tables stay exact. themes.inc would
 * otherwise re-roll these per generation. */
#define MAP_CONSTANTS
if MAP_CONSTANTS
  #const STRAGGLER_SPAWN          {pins.straggler_tree}
  #const STRAGGLER_NEUTRAL        {pins.straggler_tree}
  #const BEACH_TERRAIN            {pins.beach_terrain}

  #const FORAGE_PLANT             {pins.forage_plant}
  #const HERDABLE_A               {pins.herdable}
  #const HUNTABLE_A               {pins.huntable}
  #const HUNTABLE_SMALL_A         {pins.huntable_small}
  #const LUREABLE_A               {pins.lureable}

  #const NERITIC_A                {pins.neritic}
  #const SALTWATER_A              {pins.saltwater}
  #const FRESHWATER_A             {pins.freshwater}
  #const WHALE_A                  {pins.whale}
endif

#include_drs includes/themes.inc
#include_drs includes/constants.inc
"""


def build_player_setup(pins: ThemePins, ai_map_type: str) -> str:
    """The ``<PLAYER_SETUP>`` body, modelled on Thames."""
    return f"""\
<PLAYER_SETUP>
  #include_drs includes/preliminaries.inc
  #include_drs includes/gaia_civilisation.inc

  #define {pins.water_preset}
  #include_drs includes/water_preset.inc
  water_definition WATER_PRESET

  direct_placement
  ai_info_map_type {ai_map_type} 0 0
"""


def _tier_block(kind: str, tiers: tuple[str, ...], flavor: ResourceFlavor) -> list[str]:
    """``#const``/``#define`` lines that request one resource kind's tiers.

    Each tier gets its zone distance set explicitly rather than inheriting
    the include's default of 4 - see ``ResourceFlavor.zone_distance``.
    """
    out: list[str] = []
    for tier in tiers:
        name = f"{kind}_{tier}"
        distance = flavor.distances.get(name)
        if distance is not None:
            out.append(f"  #const {name}_DISTANCE {distance}")
        out.append(f"  #const {name}_ZONE_DISTANCE {flavor.zone_distance}")
        out.append(f"  #define {name}")
    return out


def build_objects(flavor: ResourceFlavor) -> str:
    """The ``<OBJECTS_GENERATION>`` body."""
    lines: list[str] = [
        "<OBJECTS_GENERATION>",
        "",
        "/* --- scaffolding ------------------------------------------------ */",
        "",
        f"  #const RESOURCE_SPACING_DEFAULT {flavor.spacing_default}",
        f"  #const RESOURCE_SPACING_FAR     {flavor.spacing_far}",
        f"  #const RESOURCE_RESTRICTION     {flavor.restriction}",
        "",
        "  /* actor areas 2000/2010/2020 (neutral zones) and 2030 (shore),",
        "   * plus the object groups everything else avoids. Without this the",
        "   * avoid_actor_area lines below have nothing to avoid and piles can",
        "   * land on top of each other. */",
        "  #include_drs includes/object_setup.inc",
        "",
        "/* --- the start itself -------------------------------------------- */",
        "",
        "  /* The 1999 include placed TOWN_CENTER and VILLAGER itself, so these",
        "   * are mandatory now rather than optional. */",
        "  #include_drs includes/town_centres.inc",
        "  #include_drs includes/villagers.inc",
        "  #include_drs includes/scouts.inc",
        "",
        "  /* The wood a player chops in the first minutes. RESOURCE_TEMPLATES.md",
        "   * calls this the single biggest gap in what we generated before -",
        "   * we placed none at all. 50 of 52 System A maps include it. */",
        "  #include_drs includes/stragglers.inc",
        "",
        "/* --- gold / stone / berries -------------------------------------- */",
        "",
    ]
    lines += _tier_block("FORAGE_BUSH", flavor.forage_tiers, flavor)
    lines.append("")
    lines += _tier_block("GOLD", flavor.gold_tiers, flavor)
    lines.append("")
    lines += _tier_block("STONE", flavor.stone_tiers, flavor)
    lines += [
        "",
        "  #include_drs includes/starting_resources.inc",
        "",
        "/* --- food --------------------------------------------------------- */",
        "",
        f"  #define HERDABLE_STARTING_COUNT_{flavor.herdable_starting_count}",
        "  #include_drs includes/herdable_starting.inc",
        "  #include_drs includes/herdable.inc",
        "",
        f"  #const HUNTABLE_COUNT  {flavor.huntable_count}",
        f"  #const HUNTABLE_GROUPS {flavor.huntable_groups}",
    ]
    if flavor.huntable_distance is not None:
        lines.append(f"  #const HUNTABLE_DISTANCE {flavor.huntable_distance}")
    # huntable.inc covers BOTH the normal and the small huntable role, so
    # the small consts have to be set before it is included, not after -
    # there is no separate huntable_small.inc. HUNTABLE_SMALL_GROUPS
    # defaults to 0 in the include, i.e. off unless a map asks for it.
    if flavor.huntable_small_count:
        lines += [
            f"  #const HUNTABLE_SMALL_COUNT  {flavor.huntable_small_count}",
            f"  #const HUNTABLE_SMALL_GROUPS {flavor.huntable_small_groups}",
        ]
    lines.append("  #include_drs includes/huntable.inc")
    if flavor.lureable:
        lines.append("")
        if flavor.lureable_distance is not None:
            lines.append(f"  #const LUREABLE_DISTANCE {flavor.lureable_distance}")
        if flavor.lureable_groups is not None:
            lines.append(f"  #const LUREABLE_GROUPS {flavor.lureable_groups}")
        lines.append("  #include_drs includes/lureable.inc")

    lines += [
        "",
        "/* --- water food ---------------------------------------------------- */",
        "",
        "  /* Modern fish placement uses the role includes rather than the",
        "   * hand-rolled SHORE_FISH/FISH/SALMON/MARLIN blocks this project used",
        "   * to emit, which had to guess at legal water. */",
        f"  #const NERITIC_SPACING {flavor.neritic_spacing}",
        "  #include_drs includes/neritic.inc",
        "",
        f"  #const SALTWATER_COUNT {flavor.saltwater_count}",
        "  #include_drs includes/aquatic_saltwater.inc",
        "",
        f"  #const FRESHWATER_COUNT {flavor.freshwater_count}",
        "  #include_drs includes/aquatic_freshwater.inc",
    ]
    if flavor.whale_count:
        lines += [
            "",
            f"  #const WHALE_COUNT {flavor.whale_count}",
            "  #include_drs includes/whales.inc",
        ]
    if flavor.relics:
        lines += ["", "/* --- relics ------------------------------------------------------- */",
                  "", "  #include_drs includes/relics.inc"]
    if flavor.remote_resources:
        lines += [
            "",
            "/* --- leftover space ----------------------------------------------- */",
            "",
            "  /* gold/stone/forage/huntable at min_distance_to_players 100 with a",
            "   * tolerant max_distance_to_other_zones 8. Great Wall runs this",
            "   * unconditionally because it is land-starved; Arabia gates it",
            "   * behind SPACIOUS_SETUP. Our regions are the Great Wall case. */",
            "  #include_drs includes/remote_resources.inc",
        ]
    lines.append("")
    return "\n".join(lines)
