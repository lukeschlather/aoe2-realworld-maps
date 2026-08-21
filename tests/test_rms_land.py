"""Encoding a coastline as create_land blocks."""

from __future__ import annotations

import re

import numpy as np

from rwmaps.rms import build_rms
from rwmaps.rms_land import (
    PLAYER_LAND_TILES,
    ROTATION_LABELS,
    Disc,
    build_land_generation,
    cover_mask,
    iou,
    rasterize_discs,
    rotate_position,
    rotation_roll,
    to_land_position,
)


def _island(size=120):
    mask = np.zeros((size, size), dtype=bool)
    yy, xx = np.ogrid[:size, :size]
    mask |= ((yy - 40) ** 2 + (xx - 40) ** 2) <= 22**2
    mask |= ((yy - 85) ** 2 + (xx - 80) ** 2) <= 14**2
    return mask


def test_cover_improves_with_budget():
    mask = _island()
    scores = [iou(rasterize_discs(cover_mask(mask, n), mask.shape[0]), mask)
              for n in (10, 50, 200)]
    # Saturates rather than climbing forever, so only require no regression
    # beyond noise between the smallest and largest budgets.
    assert scores[-1] >= scores[0] - 0.01, f"more lands should not be worse: {scores}"
    assert scores[-1] > 0.9


def test_overlap_keeps_the_interior_solid():
    """Non-overlapping discs leave a pond in every gap between circles."""
    from rwmaps.rms_land import interior_holes

    # A broad landmass is where interstitial gaps actually accumulate.
    mask = np.zeros((140, 140), dtype=bool)
    mask[15:125, 15:125] = True
    tight = interior_holes(
        rasterize_discs(cover_mask(mask, 200, overlap=1.0), mask.shape[0]), mask)
    lapped = interior_holes(
        rasterize_discs(cover_mask(mask, 200, overlap=0.72), mask.shape[0]), mask)
    assert lapped <= tight, f"overlap made speckle worse: {tight:.3f} -> {lapped:.3f}"
    assert lapped < 0.05, f"interior still speckled: {lapped:.3f}"


def test_cover_stays_inside_the_mask():
    mask = _island()
    for d in cover_mask(mask, 100):
        assert mask[d.y, d.x], "a disc was centred on water"


def test_land_position_is_integer_percent_and_north_up():
    size = 220
    assert to_land_position(0, 0, size) == (0, 0)
    assert to_land_position(size - 1, size - 1, size) == (100, 100)
    px, py = to_land_position(0, size - 1, size)
    assert (px, py) == (100, 0), "column 0 is west, row 0 is north"


def test_tile_budget_matches_the_real_land_area():
    """Discs overlap, so their areas must be rescaled or the map floods."""
    mask = _island()
    target = int(mask.sum())
    discs = cover_mask(mask, 150)
    text = build_land_generation(discs, mask.shape[0], [(40, 40)], target_tiles=target)
    tiles = [int(m) for m in re.findall(r"number_of_tiles\s+(\d+)", text)]
    assert abs(sum(tiles) - target) <= len(tiles) + PLAYER_LAND_TILES


def test_base_size_never_exceeds_the_tile_budget():
    """base_size alone covers (2b+1)^2 tiles; if that exceeds the budget it overgrows."""
    mask = _island()
    discs = cover_mask(mask, 100)
    text = build_land_generation(discs, mask.shape[0], target_tiles=int(mask.sum()))
    blocks = text.split("create_land")[1:]
    for block in blocks:
        base = int(re.search(r"base_size\s+(\d+)", block).group(1))
        tiles = int(re.search(r"number_of_tiles\s+(\d+)", block).group(1))
        assert (2 * base + 1) ** 2 <= max(tiles * 4, 25)


def test_players_get_their_own_assigned_land():
    mask = _island()
    starts = [(40, 40), (85, 80)]
    text = build_land_generation(cover_mask(mask, 20), mask.shape[0], starts)
    for i in (1, 2):
        assert f"assign_to_player              {i}" in text


def test_script_is_self_contained():
    mask = _island()
    land = build_land_generation(cover_mask(mask, 20), mask.shape[0], [(40, 40)])
    text = build_rms("T", "laea", mask.shape[0], land)
    assert "<PLAYER_SETUP>" in text
    assert "direct_placement" in text
    assert "<LAND_GENERATION>" in text, "the coastline must live in the script"
    assert "base_terrain WATER" in text
    # Comments may mention .scx; no executable line may depend on one.
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    assert ".scx" not in code, "the script must not reference a companion file"


def test_script_declares_an_ai_map_type():
    """Without this the AI misplays watery maps - never builds a dock, etc."""
    mask = _island()
    land = build_land_generation(cover_mask(mask, 20), mask.shape[0], [(40, 40)])
    text = build_rms("T", "laea", mask.shape[0], land, ai_map_type="ISLANDS")
    assert "ai_info_map_type ISLANDS" in text


def test_fish_have_water_to_spawn_in():
    """base_terrain is WATER, so the script must create the deeper bands itself.

    Under System A the fish themselves come from the role includes rather
    than from hand-written create_object blocks, but the reason this test
    exists is unchanged: chaining the depth bands off DEEP_WATER (which the
    shipped real-world scripts do, because their .scx already supplies deep
    ocean) leaves deep-water fish nowhere legal to spawn.
    """
    mask = _island()
    land = build_land_generation(cover_mask(mask, 20), mask.shape[0], [(40, 40)])
    text = build_rms("T", "laea", mask.shape[0], land)
    # depth bands must be created from WATER upward, not assumed to exist
    assert re.search(r"create_terrain MED_WATER\s*\{[^}]*base_terrain\s+WATER", text)
    assert re.search(r"create_terrain DEEP_WATER\s*\{[^}]*base_terrain\s+MED_WATER", text)
    for include in ("neritic.inc", "aquatic_saltwater.inc"):
        assert f"#include_drs includes/{include}" in text


def test_legacy_path_still_hand_places_fish():
    """The pre-System-A layer stays reachable for engine A/B comparison."""
    mask = _island()
    land = build_land_generation(cover_mask(mask, 20), mask.shape[0], [(40, 40)])
    text = build_rms("T", "laea", mask.shape[0], land, system_a=False)
    assert "land_and_water_resources.inc" in text
    for fish in ("SHORE_FISH", "SALMON", "MARLIN1"):
        assert f"create_object {fish}" in text
    assert re.search(r"MARLIN1\s*\{[^}]*terrain_to_place_on\s+DEEP_WATER", text)


def test_system_a_replaces_the_1999_include():
    """The orphaned 1999 include must not survive into a System A script."""
    mask = _island()
    land = build_land_generation(cover_mask(mask, 20), mask.shape[0], [(40, 40)])
    text = build_rms("T", "laea", mask.shape[0], land)
    assert "land_and_water_resources.inc" not in text
    # the things it used to place itself now have to be asked for explicitly
    for include in ("town_centres.inc", "villagers.inc", "stragglers.inc",
                    "starting_resources.inc"):
        assert f"#include_drs includes/{include}" in text


def test_per_player_forest_splits_before_budgeting():
    """The split pass is load-bearing and must come before the grow pass.

    Each `create_terrain ... number_of_clumps 1` off SPAWN_PLACEHOLDER
    consumes exactly one player region, giving it its own terrain id.
    Without that, a single budgeted forest block is free to spend the whole
    budget on one player - which is the very failure the per-player forest
    exists to fix.
    """
    from rwmaps.rms import PLACEHOLDER_CLEANUP_PASSES, build_per_player_forest

    text = build_per_player_forest(forest="FOREST", land="GRASS",
                                   tiles=100, clumps=3, n_players=8)
    split = text.index("PLACEHOLDER_TERRAIN_A")
    grow = text.index("number_of_tiles                100")
    assert split < grow, "regions must be split before any forest is budgeted"

    # split once, grow once, then cleaned up repeatedly
    for letter in "ABCDEFGH":
        assert text.count(f"PLACEHOLDER_TERRAIN_{letter}") == (
            2 + PLACEHOLDER_CLEANUP_PASSES
        )

    # and nothing may be left painted as a placeholder
    assert text.rindex("SPAWN_PLACEHOLDER") > grow


def test_placeholder_cleanup_is_repeated():
    """One cleanup pass leaves black placeholder tiles on the map.

    ``create_terrain`` grows its clumps from random seeds and stops when the
    tile budget is spent, so a single pass strands fragments - measured at
    4-46 leftover tiles on every one of ten Britain captures, drawn in game
    as a black placeholder texture. The stock ``includes/forest.inc`` repeats
    the identical block sixteen times, which is the fix.
    """
    from rwmaps.rms import PLACEHOLDER_CLEANUP_PASSES, build_per_player_forest

    text = build_per_player_forest(forest="FOREST", land="GRASS",
                                   tiles=100, clumps=3, n_players=8)
    assert PLACEHOLDER_CLEANUP_PASSES > 1
    for name in [f"PLACEHOLDER_TERRAIN_{c}" for c in "ABCDEFGH"] + [
        "SPAWN_PLACEHOLDER"
    ]:
        cleanup = f"create_terrain GRASS {{ base_terrain {name} "
        assert text.count(cleanup) == PLACEHOLDER_CLEANUP_PASSES


def test_per_player_forest_needs_placeholder_player_lands():
    """The forest is addressable only because the player lands carry a
    placeholder terrain - the two have to be switched on together."""
    import numpy as np

    from rwmaps.rms import PLAYER_SPAWN_PLACEHOLDER, RmsOptions, build_rms
    from rwmaps.rms_land import build_land_generation, cover_mask

    mask = np.zeros((80, 80), bool)
    mask[20:60, 20:60] = True
    land = build_land_generation(cover_mask(mask, 20), 80, [(40, 40)],
                                 player_terrain=PLAYER_SPAWN_PLACEHOLDER)
    assert f"terrain_type                  {PLAYER_SPAWN_PLACEHOLDER}" in land
    text = build_rms("T", "laea", 80, land, RmsOptions(per_player_forest=True))
    assert "per-player forest" in text
    # the coastline itself must NOT be painted with the placeholder
    assert land.count(PLAYER_SPAWN_PLACEHOLDER) == 1


def test_a_quarter_turn_is_exact_and_cycles_in_four():
    """Rotation is done in percent space, so it must be exact in integers."""
    for px, py in ((0, 0), (40, 30), (100, 0), (13, 87)):
        p = (px, py)
        assert rotate_position(*p, 4) == p, "four turns is the identity"
        assert rotate_position(*p, 1) != p or p == (50, 50)
        # A quarter turn preserves distance from the centre, which is what
        # makes the four orientations the same map.
        for q in range(4):
            rx, ry = rotate_position(px, py, q)
            assert (rx - 50) ** 2 + (ry - 50) ** 2 == (px - 50) ** 2 + (py - 50) ** 2


def test_rotation_shares_total_one_hundred():
    for n in (2, 4):
        roll = rotation_roll(n)
        pcts = [int(v) for v in re.findall(r"percent_chance (\d+)", roll)]
        assert len(pcts) == n and sum(pcts) == 100
    assert rotation_roll(1) == "", "one orientation needs no roll"


def test_every_land_carries_all_four_orientations():
    """The engine picks the orientation, so every land must be in every branch.

    A land that appears under only some labels would generate a different
    coastline in those games, not a rotated one.
    """
    mask = _island()
    starts = [(40, 40), (85, 80)]
    text = build_land_generation(cover_mask(mask, 40), mask.shape[0], starts,
                                 rotations=4, shallows=[Disc(50, 50, 4.0)])
    assert text.count("#define ROT_") == 4
    blocks = text.split("create_land")[1:]
    assert blocks
    for block in blocks:
        found = re.findall(r"(?:if|elseif) (ROT_\w+)\n\s*land_position\s+(\d+) (\d+)",
                           block)
        assert [f[0] for f in found] == list(ROTATION_LABELS)
        first = (int(found[0][1]), int(found[0][2]))
        for q, f in enumerate(found):
            assert (int(f[1]), int(f[2])) == rotate_position(*first, q)
        assert "endif" in block


def test_one_orientation_emits_no_conditional():
    """The default has to stay byte-identical to what the mod already ships."""
    mask = _island()
    args = (cover_mask(mask, 40), mask.shape[0], [(40, 40)])
    plain = build_land_generation(*args)
    assert "ROT_" not in plain and "start_random" not in plain
    assert build_land_generation(*args, rotations=1) == plain
