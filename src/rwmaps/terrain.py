"""AoE2 terrain ids and the land/water palette used for real-world outlines.

Ids are the raw values stored in the scenario terrain grid. They were confirmed
against the terrain grids of the shipped ``real_world_*.scx`` maps.
"""

from __future__ import annotations

# --- water ---------------------------------------------------------------
# NOTE ON NAMING: the engine's own constants.inc calls id 22 WATER_DEEP and
# id 23 WATER_MEDIUM - the opposite of the names below, which predate anyone
# reading that file. The generated .rms uses the engine's spelling
# (``create_terrain MED_WATER`` etc. are resolved by the engine, not here),
# so scripts are unaffected; only these Python-side aliases are inverted.
# Left as-is rather than renamed because both ids are water either way and
# every caller only ever asks "is this water?" - but do not read meaning
# into which of the two is called "deep" here.
WATER = 1  # WATER_SHALLOW - the blue shore water
MED_WATER = 22  # engine name: WATER_DEEP
DEEP_WATER = 23  # engine name: WATER_MEDIUM
SHALLOWS = 4  # fordable by land units; never used for open ocean

# --- shoreline -----------------------------------------------------------
BEACH = 2

# --- land ----------------------------------------------------------------
GRASS = 0
GRASS2 = 12
GRASS3 = 9
DIRT = 6
DIRT3 = 3
PALM_DESERT = 13
DESERT = 14
SNOW = 32
ICE = 35

#: Open water: not crossable on foot. Read straight off the engine's
#: ``includes/constants.inc`` ``WATER_*`` block rather than guessed.
#:
#: Only ids 1/22/23 were listed here originally, which is fine for scripts
#: this project generates (they paint nothing else) but silently wrong for
#: any *stock* map - the modern water presets paint WATER_OCEAN/AZURE/GREEN/
#: BROWN/YELLOW, and a mask built from the old set counted every one of
#: those tiles as LAND. That would have quietly corrupted the stock-map
#: benchmarks this project is measuring itself against, in the direction of
#: making stock maps look like they have far more usable land than they do.
DEEP_WATER_IDS = frozenset({
    WATER,      # 1   WATER_SHALLOW
    MED_WATER,  # 22  WATER_DEEP
    DEEP_WATER,  # 23  WATER_MEDIUM
    15,         # WATER_SHORELESS
    57,         # WATER_OCEAN
    58,         # WATER_AZURE
    95,         # WATER_GREEN
    96,         # WATER_BROWN
    114,        # WATER_YELLOW
    116,        # WATER_YELLOW_DEEP
})

#: Shallow water: *walkable* by land units. Terrain-wise it is water (a
#: coastline should render it as sea), but a villager can cross it, so it
#: must not act as a barrier when asking "can this player reach that
#: resource" - a ford is a route, not a wall.
SHALLOW_IDS = frozenset({
    SHALLOWS,  # 4
    26,        # SHALLOWS_ICE
    54,        # SHALLOWS_MANGROVE
    59,        # SHALLOWS_AZURE
    111,       # SHALLOWS_SWAMP
    115,       # SHALLOWS_YELLOW
    28,        # WATER_WALKABLE
})

#: Everything the engine treats as water for the purposes of a real-world
#: outline - i.e. what should be painted as sea. Includes the shallows.
WATER_IDS = DEEP_WATER_IDS | SHALLOW_IDS

#: Everything a land unit can stand on or cross. The complement of
#: DEEP_WATER_IDS, not of WATER_IDS - shallows are walkable.
def is_walkable(terrain_id: int) -> bool:
    return terrain_id not in DEEP_WATER_IDS


#: Forest terrain, from the engine's ``FOREST_*`` constants.
#:
#: Forest terrain is not decoration: it is what carries wood, and wood
#: supply is a balance quantity the stock scripts budget explicitly
#: (``PLAYER_FOREST_TILES``, ``PLAYER_FOREST_TEAM_DEDUCTION``). This
#: project had no way to measure it at all, which is why its early metrics
#: could not tell Arabia and Black Forest apart - their single largest
#: difference is trees.
#:
#: ``FOREST_PLACEHOLDER`` (99) is included because a script that leaves it
#: unconverted still has it sitting on the map.
FOREST_IDS = frozenset({
    10,   # FOREST_OAK
    13,   # FOREST_PALM
    17,   # FOREST_JUNGLE
    18,   # FOREST_BAMBOO
    19,   # FOREST_PINE
    20,   # FOREST_OAK_BUSH
    21,   # FOREST_PINE_SNOW
    48,   # FOREST_DRAGON
    49,   # FOREST_BAOBAB
    50,   # FOREST_ACACIA
    55,   # FOREST_MANGROVE
    56,   # FOREST_RAIN
    88,   # FOREST_MEDITERRANEAN
    89,   # FOREST_BUSH
    90,   # FOREST_REEDS_SHALLOWS
    91,   # FOREST_REEDS_BEACH
    92,   # FOREST_REEDS
    99,   # FOREST_PLACEHOLDER
    104,  # FOREST_AUTUMN
    105,  # FOREST_AUTUMN_SNOW
    106,  # FOREST_DEAD
    110,  # FOREST_BIRCH
    112,  # FOREST_PALM_GRASS
    113,  # FOREST_BAMBOO_LUSH
    128,  # FOREST_SOUTH_AMERICA
})


class Palette:
    """Terrain ids the rasteriser paints for each class of tile.

    The random map script paints the *interesting* terrain on top of these using
    ``create_terrain ... base_terrain <x>``, so the base only needs to encode the
    land/water outline plus enough variety for the script to key off.
    """

    def __init__(
        self,
        land: int = GRASS,
        beach: int = BEACH,
        shallow: int = WATER,
        medium: int = MED_WATER,
        deep: int = DEEP_WATER,
        lake: int = WATER,
    ) -> None:
        self.land = land
        self.beach = beach
        self.shallow = shallow
        self.medium = medium
        self.deep = deep
        self.lake = lake


#: Reasonable defaults per broad climate, selectable from the CLI.
BIOMES: dict[str, Palette] = {
    "temperate": Palette(land=GRASS),
    "arid": Palette(land=DIRT),
    "desert": Palette(land=DESERT),
    "tropical": Palette(land=GRASS2),
    "arctic": Palette(land=SNOW, beach=ICE),
}
