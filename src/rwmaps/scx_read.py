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
    """Load a ``.scx`` and return its land/water mask as ``[y][x]`` bool."""
    grid = read_terrain_grid(path)
    water = np.zeros(grid.shape, dtype=bool)
    for wid in T.WATER_IDS:
        water |= grid == wid
    return ~water


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


#: Land-economy gaia resources a villager reaches on foot - gold, stone,
#: forage bushes, sheep, deer, boar. Fish are a separate water/dock economy
#: and deliberately excluded. Gold/stone/classic-sheep/deer/boar/forage ids
#: are the well-known, stable ones used by the older stock resource include
#: (``land_and_water_resources.inc``) this project's own ``rms.py`` uses.
#:
#: The *current* stock Arabia (and other modern DE random maps) instead
#: route herdable/huntable/forage placement through ``includes/themes.inc``,
#: which randomly re-skins each role per generation (a "sheep" might place
#: as a Capybara, Goat, Turkey, Water Buffalo or Pig; a "deer" as a Guanaco,
#: Rhea, Mouflon, Ibex, Gazelle, Argali, Ostrich or Zebra; a forage bush as
#: a Fruit Bush, Papaya Tree or Pineapple Bush) - functionally identical,
#: cosmetically different. Every reskin id found in that file (as of this
#: game version) is included below under the role it actually fills, found
#: by reading ``includes/themes.inc`` directly since none of this is in
#: AoE2ScenarioParser's ``UnitInfo`` dataset.
RESOURCE_UNITS: dict[int, str] = {
    66: "gold",
    102: "stone",
    # forage-bush role (FORAGE_PLANT)
    59: "forage", 1059: "forage", 2599: "forage", 2650: "forage",
    # herdable role (HERDABLE_A) - docile, walk-up food
    594: "sheep", 1243: "sheep", 833: "sheep", 2590: "sheep",
    1245: "sheep", 1060: "sheep", 1142: "sheep",
    # huntable role (HUNTABLE_A) - docile but flees, needs chasing
    65: "deer", 2591: "deer", 2597: "deer", 2340: "deer", 1239: "deer",
    1796: "deer", 1896: "deer", 1026: "deer", 1019: "deer",
    # classic wild boar and the small-huntable role (HUNTABLE_SMALL_A) -
    # aggressive/fights back, distinct from the docile huntable role above.
    48: "boar", 2100: "boar",
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


def _tree_unit_ids() -> frozenset[int]:
    """Every individually-choppable tree object id the parser knows about.

    Unlike gold/stone/forage/sheep/deer/boar, wood isn't placed by the RMS
    script as a fixed, curated set of reskin ids (see ``RESOURCE_UNITS``) -
    it comes from whatever the engine scatters onto ``FOREST``-family terrain
    at generation time, which draws from a long, biome-dependent tree object
    list (oak, pine, jungle, palm, birch, ...) that ``AoE2ScenarioParser``
    exposes under ``datasets.other.OtherInfo`` rather than ``datasets.units``.
    Scanned by name prefix instead of hand-curated because there's no single
    themes.inc-style table to read it from, and missing a biome-specific tree
    id would silently undercount wood on exactly the maps that use it.
    """
    from AoE2ScenarioParser.datasets.other import OtherInfo

    ids = set()
    for name in dir(OtherInfo):
        if not name.startswith("TREE_"):
            continue
        info = getattr(OtherInfo, name)
        ids.add(int(info.ID))
    return frozenset(ids)


WOOD_UNITS: frozenset[int] = _tree_unit_ids()


def read_trees(path: str | Path) -> list[tuple[str, float, float]]:
    """Load a ``.scx`` and return each standing, choppable tree as ``("wood", x, y)``.

    Kept separate from ``read_resources`` (rather than folded into
    ``RESOURCE_UNITS``) since trees number in the thousands per map and come
    from a different id space (``WOOD_UNITS``, scanned dynamically - see
    ``_tree_unit_ids``) than the hand-curated economy-resource reskins.
    """
    scenario = _load_scenario(path)
    out = []
    for player_units in scenario.unit_manager.units:
        for unit in player_units:
            if unit.unit_const in WOOD_UNITS:
                out.append(("wood", unit.x, unit.y))
    return out
