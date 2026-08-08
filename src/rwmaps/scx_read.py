"""Read the terrain grid out of a ``.scx`` scenario file.

This is the other half of what ``preview.py`` fakes: instead of a Python
rasterisation of the disc union, this reads the *actual* tile grid the game
engine produced when a ``.rms`` was generated and saved as a scenario. Point
this at a file saved from the Scenario Editor's "Generate Map" button to see
the real coastline the engine grew, not an approximation of it.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import numpy as np
from AoE2ScenarioParser.datasets.buildings import BuildingInfo
from AoE2ScenarioParser.scenarios.aoe2_de_scenario import AoE2DEScenario

from . import terrain as T


def _load_scenario(path: str | Path) -> AoE2DEScenario:
    """Load a ``.scx``/``.aoe2scenario``, discarding the library's own
    progress printing.

    ``AoE2DEScenario.from_file`` prints its progress unconditionally (no
    verbose/quiet flag), including emoji status glyphs - Windows' default
    console encoding (cp1252) can't represent those and raises
    ``UnicodeEncodeError`` partway through, which silently kills a script
    that isn't running in an interactive terminal (e.g. under a redirected/
    backgrounded stdout), well before the crash is visible anywhere useful.
    None of this output is ours to show, so redirect it away entirely
    rather than rely on the caller happening to filter/tolerate it.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        return AoE2DEScenario.from_file(str(path))


def read_terrain_grid(path: str | Path) -> np.ndarray:
    """Load a ``.scx`` and return its terrain-id grid as ``[y][x]`` uint8.

    Row 0 is the north edge and column 0 the west edge, matching the
    convention used everywhere else in this project.
    """
    scenario = _load_scenario(path)
    mm = scenario.map_manager
    grid = np.zeros((mm.map_height, mm.map_width), dtype=np.uint8)
    for tile in mm.terrain:
        grid[tile.y, tile.x] = tile.terrain_id
    return grid


def read_land_mask(path: str | Path) -> np.ndarray:
    """Load a ``.scx`` and return its land/water mask as ``[y][x]`` bool.

    This is the *coastline* mask - what should be drawn as land. Shallows
    count as water here because they read as sea; use
    :func:`read_walkable_mask` for reachability questions instead.
    """
    grid = read_terrain_grid(path)
    water = np.zeros(grid.shape, dtype=bool)
    for wid in T.WATER_IDS:
        water |= grid == wid
    return ~water


def read_walkable_mask(path: str | Path) -> np.ndarray:
    """Load a ``.scx`` and return where a land unit can actually go.

    Differs from :func:`read_land_mask` only in that shallows/fords are
    walkable. That distinction matters for resource *ownership*: a bush
    across a shallow ford is reachable on foot, and treating the ford as a
    barrier makes it look unreachable - understating what a player actually
    has, which is precisely the error a fairness metric must not make.
    """
    grid = read_terrain_grid(path)
    blocked = np.zeros(grid.shape, dtype=bool)
    for wid in T.DEEP_WATER_IDS:
        blocked |= grid == wid
    return ~blocked


def read_town_centers(path: str | Path) -> list[tuple[int, float, float]]:
    """Load a ``.scx`` and return each player's actual Town Centre placement.

    Returns ``(player, x, y)`` tuples in the same ``[y][x]``-north-up tile
    convention as ``read_terrain_grid`` - this is where the engine's own
    placement logic actually dropped the TC, not the ``land_position`` the
    script asked for.
    """
    scenario = AoE2DEScenario.from_file(str(path))
    tcs = []
    for player_units in scenario.unit_manager.units:
        for unit in player_units:
            if unit.unit_const == BuildingInfo.TOWN_CENTER.ID:
                tcs.append((int(unit.player), unit.x, unit.y))
    return sorted(tcs, key=lambda t: t[0])


#: Gaia resources, keyed by unit id, mapped to the economic *role* they
#: fill rather than to their cosmetic skin.
#:
#: Modern DE random maps route every animal/bush placement through
#: ``includes/themes.inc``, which re-skins each role per biome (a "sheep"
#: may place as a Capybara, Goat, Turkey, Water Buffalo, Goose or Pig; a
#: "boar" as a Javelina, Rhinoceros, Elephant or Tapir). The skin is
#: cosmetic; the role is what a player's economy actually depends on. Every
#: id below was read out of ``includes/themes.inc`` directly - none of this
#: is in AoE2ScenarioParser's ``UnitInfo`` dataset.
#:
#: Two corrections made 2026-08-08 after a real Arabia capture reported
#: ``boar: 0`` on a map that unquestionably places boar:
#:
#: * The ``boar`` role is ``LUREABLE_A``, which has five skins (48 Wild
#:   Boar, 822 Javelina, 1139 Rhinoceros, 1301 Elephant, 2589 Tapir). Only
#:   the classic id 48 was listed, so every re-skinned biome silently
#:   counted zero boar - which the fairness check would then report as a
#:   player having none.
#: * Id 2100 (Arctic Hare) was filed under ``boar``. It is
#:   ``HUNTABLE_SMALL_A``, a genuinely different role - a small huntable
#:   that does not fight back and is not lurable. It now has its own kind.
RESOURCE_UNITS: dict[int, str] = {
    66: "gold",
    102: "stone",
    # FORAGE_PLANT - berry bushes and their reskins
    59: "forage", 1059: "forage", 2599: "forage", 2650: "forage",
    # HERDABLE_A - docile, walk-up food
    594: "sheep", 1243: "sheep", 833: "sheep", 2590: "sheep",
    1245: "sheep", 1060: "sheep", 1142: "sheep",
    # HUNTABLE_A / HUNTABLE_B - docile but flees, needs chasing
    65: "deer", 2591: "deer", 2597: "deer", 2340: "deer", 1239: "deer",
    1796: "deer", 1896: "deer", 1026: "deer", 1019: "deer",
    # LUREABLE_A - aggressive, lured back to the TC. The big early food
    # spike, and the role most sensitive to placement distance.
    48: "boar", 822: "boar", 1139: "boar", 1301: "boar", 2589: "boar",
    # HUNTABLE_SMALL_A - minor supplementary food (Great Wall asks for 8)
    2100: "small_game",
}

#: Water food, by role. Kept separate from ``RESOURCE_UNITS`` because it is
#: a different economy (needs a dock and fishing ships, not villagers on
#: foot) - but NOT ignored, which is what an earlier version of this module
#: did. Every map this project generates is a coastline, and on an island
#: region a player with no reachable shore fish is meaningfully worse off
#: than one with plenty. Treating fish as out of scope made that invisible.
#:
#: ``shore`` fish are the ones a dock can reach immediately and are by far
#: the most decisive early; ``deep`` needs a fishing ship out in open water.
WATER_UNITS: dict[int, str] = {
    # NERITIC_A - shore fish, harvestable from a dock
    69: "shore_fish", 1141: "shore_fish",
    # SALTWATER_A/B + FRESHWATER_A/B - open-water fish
    53: "deep_fish", 450: "deep_fish", 455: "deep_fish", 456: "deep_fish",
    457: "deep_fish", 458: "deep_fish",
    # WHALE_A - the big one
    2625: "whale",
}


def read_resources(path: str | Path) -> list[tuple[str, float, float]]:
    """Load a ``.scx`` and return each land-economy resource as ``(kind, x, y)``."""
    scenario = AoE2DEScenario.from_file(str(path))
    out = []
    for player_units in scenario.unit_manager.units:
        for unit in player_units:
            kind = RESOURCE_UNITS.get(unit.unit_const)
            if kind:
                out.append((kind, unit.x, unit.y))
    return out


def read_water_resources(path: str | Path) -> list[tuple[str, float, float]]:
    """Load a ``.scx`` and return each water-economy resource as ``(kind, x, y)``."""
    scenario = AoE2DEScenario.from_file(str(path))
    out = []
    for player_units in scenario.unit_manager.units:
        for unit in player_units:
            kind = WATER_UNITS.get(unit.unit_const)
            if kind:
                out.append((kind, unit.x, unit.y))
    return out
