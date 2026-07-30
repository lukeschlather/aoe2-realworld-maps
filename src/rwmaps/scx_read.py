"""Read the terrain grid out of a ``.scx`` scenario file.

This is the other half of what ``preview.py`` fakes: instead of a Python
rasterisation of the disc union, this reads the *actual* tile grid the game
engine produced when a ``.rms`` was generated and saved as a scenario. Point
this at a file saved from the Scenario Editor's "Generate Map" button to see
the real coastline the engine grew, not an approximation of it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from AoE2ScenarioParser.scenarios.aoe2_de_scenario import AoE2DEScenario

from . import terrain as T


def read_terrain_grid(path: str | Path) -> np.ndarray:
    """Load a ``.scx`` and return its terrain-id grid as ``[y][x]`` uint8.

    Row 0 is the north edge and column 0 the west edge, matching the
    convention used everywhere else in this project.
    """
    scenario = AoE2DEScenario.from_file(str(path))
    mm = scenario.map_manager
    grid = np.zeros((mm.map_height, mm.map_width), dtype=np.uint8)
    for tile in mm.terrain:
        grid[tile.y, tile.x] = tile.terrain_id
    return grid


def read_land_mask(path: str | Path) -> np.ndarray:
    """Load a ``.scx`` and return its land/water mask as ``[y][x]`` bool."""
    grid = read_terrain_grid(path)
    water = np.zeros(grid.shape, dtype=bool)
    for wid in T.WATER_IDS:
        water |= grid == wid
    return ~water
