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
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from AoE2ScenarioParser import settings as _asp_settings
from AoE2ScenarioParser.datasets.buildings import BuildingInfo
from AoE2ScenarioParser.scenarios.aoe2_de_scenario import AoE2DEScenario

from . import terrain as T

#: The library's own progress printing, off. It writes timestamped,
#: ANSI-coloured, emoji-bearing lines straight to stdout on every load -
#: ``[13:47:51] Reading file: '...'`` - which is nobody's log but its own.
#: ``_load_scenario`` below redirects stdout for the same reason, but four
#: other readers in this file call ``from_file`` directly and leaked two of
#: these lines into every capture the pass took. This is the switch the
#: library provides, so it covers all five call sites at once and keeps the
#: terse run log free of timestamps (see ``automation/runlog.py``).
_asp_settings.PRINT_STATUS_UPDATES = False


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


@dataclass(frozen=True)
class Capture:
    """Everything this project reads out of one scenario, parsed once.

    ``AoE2DEScenario.from_file`` re-parses the entire file on every call,
    and the natural way to write an analysis is to call one reader per
    thing it needs - which meant a single start-quality profile parsed the
    same capture six times over, dominating its runtime. Load once, ask
    many times.
    """

    terrain: np.ndarray
    town_centers: list[tuple[int, float, float]]
    resources: list[tuple[str, float, float]]
    water_resources: list[tuple[str, float, float]]
    trees: list[tuple[str, float, float]]

    @property
    def land_mask(self) -> np.ndarray:
        """Coastline mask - shallows read as sea. See :func:`read_land_mask`."""
        return ~np.isin(self.terrain, list(T.WATER_IDS))

    @property
    def walkable_mask(self) -> np.ndarray:
        """Where a land unit can actually go.

        Shallows are fords, so they are walkable; forest is solid, so it is
        not. Forest was previously missing from this, which let reachability
        answer "yes" through the middle of a wood.
        """
        return ~np.isin(self.terrain, list(T.IMPASSABLE_IDS))

    @property
    def forest_mask(self) -> np.ndarray:
        return np.isin(self.terrain, list(T.FOREST_IDS))

    @property
    def dry_land_mask(self) -> np.ndarray:
        """Land, forested or not - the denominator for "how wooded is this"."""
        return ~np.isin(self.terrain, list(T.DEEP_WATER_IDS))


def read_capture(path: str | Path) -> Capture:
    """Parse a ``.scx``/``.aoe2scenario`` once into a :class:`Capture`."""
    scenario = _load_scenario(path)
    mm = scenario.map_manager
    grid = np.zeros((mm.map_height, mm.map_width), dtype=np.uint8)
    for tile in mm.terrain:
        grid[tile.y, tile.x] = tile.terrain_id

    tcs: list[tuple[int, float, float]] = []
    resources: list[tuple[str, float, float]] = []
    water: list[tuple[str, float, float]] = []
    trees: list[tuple[str, float, float]] = []
    for player_units in scenario.unit_manager.units:
        for unit in player_units:
            const = unit.unit_const
            if const == BuildingInfo.TOWN_CENTER.ID:
                tcs.append((int(unit.player), unit.x, unit.y))
            kind = RESOURCE_UNITS.get(const)
            if kind:
                resources.append((kind, unit.x, unit.y))
            wkind = WATER_UNITS.get(const)
            if wkind:
                water.append((wkind, unit.x, unit.y))
            if const in TREE_UNITS:
                trees.append(("tree", unit.x, unit.y))

    return Capture(
        terrain=grid,
        town_centers=sorted(tcs, key=lambda t: t[0]),
        resources=resources,
        water_resources=water,
        trees=trees,
    )


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
    # HUNTABLE_SMALL_A/B - minor supplementary food.
    #
    # The wild chickens are not a fourth skin in themes.inc; they come from
    # includes/object_groups.inc, which REDEFINES HUNTABLE_SMALL_A/B as an
    # object *group* of three chicken ids whenever a theme declares
    # WILD_CHICKEN_VARIATION_A/B. Grepping themes.inc alone says the role
    # is only ever 2100, and that is how this list came to miss them.
    #
    # Found 2026-08-16 chasing a stock Arabia capture that reported no
    # deer: the capture has 103 wild chickens in it and counted zero of
    # everything huntable-small. Same failure as the 2026-08-08 boar fix -
    # one role, several skins, only one listed.
    2100: "small_game",   # Arctic Hare
    2084: "small_game",   # Wild Chicken (brown)
    2086: "small_game",   # Wild Chicken (white)
    2088: "small_game",   # Wild Chicken (black)
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


#: Individual tree *objects* (the engine's ``TREE_*`` constants), as
#: opposed to forest terrain. These are what ``stragglers.inc`` scatters
#: around the town centre - the wood a player chops in the opening minutes,
#: and per RESOURCE_TEMPLATES.md the single biggest gap in what this
#: project generates. Read off ``includes/constants.inc``.
TREE_UNITS: frozenset[int] = frozenset({
    348,   # TREE_BAMBOO
    349,   # TREE_OAK
    350,   # TREE_PINE
    351,   # TREE_PALM
    411,   # TREE_OAK_FOREST
    413,   # TREE_PINE_SNOW
    414,   # TREE_JUNGLE
    1051,  # TREE_DRAGON
    1052,  # TREE_BAOBAB
    1063,  # TREE_ACACIA
    1144,  # TREE_MANGROVE
    1146,  # TREE_RAINFOREST
    1248,  # TREE_AUTUMN
    1249,  # TREE_AUTUMN_SNOW
    1250,  # TREE_DEAD
    1347,  # TREE_CYPRESS
    1348,  # TREE_PINE_ITALIAN
    1349,  # TREE_OLIVE
    1350,  # TREE_REEDS
    1717,  # TREE_BIRCH
    1984,  # TREE_BAMBOO_LUSH
    2016,  # TREE_PINE_ASIAN
    2017,  # TREE_PEACH_BLOSSOM
    2025,  # TREE_WILLOW
    2027,  # TREE_MAPLE
    2028,  # TREE_MAPLE_AUTUMN
    2567,  # TREE_OAK_GREEN
    2570,  # TREE_MONKEY_PUZZLE
    2580,  # TREE_BRAZILWOOD
    2583,  # TREE_WAX_PALM
})


def read_trees(path: str | Path) -> list[tuple[str, float, float]]:
    """Load a ``.scx`` and return each tree object as ``("tree", x, y)``."""
    scenario = AoE2DEScenario.from_file(str(path))
    out = []
    for player_units in scenario.unit_manager.units:
        for unit in player_units:
            if unit.unit_const in TREE_UNITS:
                out.append(("tree", unit.x, unit.y))
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
