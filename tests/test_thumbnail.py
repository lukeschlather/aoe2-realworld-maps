"""Reading a shipped .rms back into the terrain it describes."""

from __future__ import annotations

import numpy as np
import pytest

from rwmaps.rms import PLAYER_SPAWN_PLACEHOLDER, build_rms
from rwmaps.rms_land import build_land_generation, cover_mask, iou, rasterize_discs
from rwmaps.thumbnail import (
    fill_pockets,
    fit_scale,
    parse_script,
    render,
    terrain_rgb,
)


SIZE = 120


def _island(size=SIZE):
    mask = np.zeros((size, size), dtype=bool)
    yy, xx = np.ogrid[:size, :size]
    mask |= ((yy - 40) ** 2 + (xx - 40) ** 2) <= 22**2
    mask |= ((yy - 85) ** 2 + (xx - 80) ** 2) <= 14**2
    return mask


def _script(tmp_path, starts=((40, 40), (85, 80))):
    from rwmaps.rms import BIOME_RMS, write_rms

    mask = _island()
    discs = cover_mask(mask, 150, overlap=0.85)
    land = build_land_generation(discs, SIZE, list(starts),
                                 target_tiles=int(mask.sum()), terrain_type="GRASS",
                                 player_terrain=PLAYER_SPAWN_PLACEHOLDER)
    text = build_rms("Test Isle", "laea", SIZE, land, BIOME_RMS["temperate"], "ISLANDS")
    return mask, discs, write_rms(tmp_path / "test_isle.rms", text)


def test_parse_recovers_the_generated_geometry(tmp_path):
    mask, discs, path = _script(tmp_path)
    script = parse_script(path)

    assert script.size == SIZE
    # The player lands are extra blocks on top of the coastline cover.
    assert len(script.coastline) == len(discs)
    assert len(script.discs) == len(discs) + 2
    # Quantisation is the only loss on centres: land_position is integer
    # percent, so a centre can move by half a percent of the grid per axis.
    tolerance = SIZE / 100.0
    for original, recovered in zip(discs, script.coastline):
        assert abs(original.y - recovered.y) <= tolerance
        assert abs(original.x - recovered.x) <= tolerance

    # Radii come back uniformly shrunk (see the module docstring), so what
    # has to survive is their ratios - one constant restores them all.
    scale = fit_scale(script.discs, SIZE, script.land_tiles)
    for original, recovered in zip(discs, script.coastline):
        assert recovered.radius * scale == pytest.approx(original.radius, rel=0.15)

    assert iou(script.land_mask, mask) > 0.85, "thumbnail should track the coastline"
    assert script.land_mask.sum() == pytest.approx(mask.sum(), rel=0.1)


def test_unscaled_radii_would_crack_the_land(tmp_path):
    """Skipping fit_scale leaves holes between discs that really overlap."""
    _, _, path = _script(tmp_path)
    script = parse_script(path)
    raw = rasterize_discs(script.discs, SIZE)
    assert raw.sum() < 0.9 * script.land_mask.sum()


def test_starts_come_back_numbered(tmp_path):
    _, _, path = _script(tmp_path, starts=((40, 40), (85, 80)))
    script = parse_script(path)
    assert [p for p, _, _ in script.starts] == [1, 2]
    ys = {(y, x) for _, y, x in script.starts}
    assert all(any(abs(y - sy) <= 2 and abs(x - sx) <= 2 for sy, sx in ys)
               for y, x in [(40, 40), (85, 80)])


def test_pockets_fill_but_the_sea_survives():
    mask = np.zeros((60, 60), dtype=bool)
    mask[10:50, 10:50] = True
    mask[30, 30] = False              # an interstitial pinhole
    mask[20:28, 25] = False           # an eight-tile sliver between discs
    filled = fill_pockets(mask)
    assert filled[30, 30] and filled[24, 25], "slivers and pinholes should close"
    assert not filled[0, 0] and not filled[:10].any(), "open sea must stay water"

    # A channel through to the sea is a strait, not a pocket, at any width.
    strait = mask.copy()
    strait[:, 30] = False
    assert not fill_pockets(strait)[:, 30].any()

    # And a big enclosed body of water is a lake, so it stays wet.
    lake = mask.copy()
    lake[20:40, 20:40] = False
    assert not fill_pockets(lake)[20:40, 20:40].any()


def test_edge_land_is_not_eaten():
    """Land running off the map edge must survive the pocket pass intact."""
    mask = np.zeros((40, 40), dtype=bool)
    mask[:, :20] = True
    assert fill_pockets(mask).sum() == mask.sum()


def test_render_is_square_and_painted(tmp_path):
    _, _, path = _script(tmp_path)
    img = render(parse_script(path), px=64)
    assert img.size == (64, 64)
    assert len(img.getcolors(maxcolors=1 << 16)) > 3, "water, shallows, beach, land"


def test_terrain_bands_are_distinct():
    rgb = terrain_rgb(_island())
    assert len(np.unique(rgb.reshape(-1, 3), axis=0)) == 4
