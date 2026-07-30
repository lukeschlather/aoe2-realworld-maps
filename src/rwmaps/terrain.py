"""AoE2 terrain ids and the land/water palette used for real-world outlines.

Ids are the raw values stored in the scenario terrain grid. They were confirmed
against the terrain grids of the shipped ``real_world_*.scx`` maps.
"""

from __future__ import annotations

# --- water ---------------------------------------------------------------
WATER = 1  # "Water" - the shallow blue shore water
MED_WATER = 22
DEEP_WATER = 23
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

#: Everything the engine treats as water for the purposes of a real-world outline.
WATER_IDS = frozenset({WATER, MED_WATER, DEEP_WATER, SHALLOWS})


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
