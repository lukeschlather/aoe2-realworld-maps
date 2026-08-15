"""Reading a shipped .rms back into the terrain it describes."""

from __future__ import annotations

import numpy as np
import pytest

from rwmaps.rms import PLAYER_SPAWN_PLACEHOLDER, build_rms
from rwmaps.rms_land import build_land_generation, cover_mask, iou, rasterize_discs
from rwmaps.thumbnail import (
    AOE_PLAYER_COLORS,
    ICON_PX,
    ICON_ROTATION,
    fill_pockets,
    fit_scale,
    parse_script,
    render,
    render_icon,
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


def test_icon_matches_the_game_s_format(tmp_path):
    """420x420 RGBA, full-bleed diamond, corners on the edge midpoints.

    Measured off the stock mapicons/rm_arabia.png and the subscribed mods
    that ship their own - the game shows <script>.png from beside the script.
    """
    _, _, path = _script(tmp_path)
    icon = render_icon(parse_script(path))
    assert icon.size == (ICON_PX, ICON_PX)
    assert icon.mode == "RGBA"

    alpha = np.array(icon)[..., 3]
    mid = ICON_PX // 2
    assert alpha[mid].all(), "the diamond spans the full width at its middle"
    assert alpha[mid, mid] == 255
    for corner in [(0, 0), (0, -1), (-1, 0), (-1, -1)]:
        assert alpha[corner] == 0, "square corners are transparent"
    # Every row is a slice of the diamond, so its opaque width tracks the
    # distance to the nearest tip.
    for row in (0, ICON_PX // 4, ICON_PX - 1):
        width = int((alpha[row] > 0).sum())
        expected = 2 * (mid - abs(row - mid))
        assert abs(width - expected) <= 12, f"row {row}: {width} vs ~{expected}"


def test_icon_puts_north_at_the_upper_left():
    """The rotation is counter-clockwise - see thumbnail.ICON_ROTATION.

    Measured off the stock real-world maps' icons: rwm_iberia puts Africa
    (south) at the lower right, rwm_britain puts Scotland (north) at the
    upper left. Clockwise instead of counter-clockwise is a 90 degree error,
    which is exactly the bug this pins down.
    """
    size = 120
    mid = ICON_PX / 2
    for edge, band, side in [
        ("north", (slice(None, size // 4), slice(None)), (-1, -1)),
        ("east", (slice(None), slice(-size // 4, None)), (+1, -1)),
        ("south", (slice(-size // 4, None), slice(None)), (+1, +1)),
        ("west", (slice(None), slice(None, size // 4)), (-1, +1)),
    ]:
        mask = np.zeros((size, size), dtype=bool)
        mask[band] = True
        ys, xs = np.nonzero(np.array(_rotate_mask(mask))[..., 3] > 0)
        cx, cy = xs.mean() - mid, ys.mean() - mid
        assert np.sign(cx) == side[0] and np.sign(cy) == side[1], (
            f"{edge} landed at ({cx:+.0f}, {cy:+.0f}), expected quadrant {side}")


def _rotate_mask(mask):
    """The icon pipeline's rotation alone, for orientation checks."""
    from PIL import Image

    rgba = np.zeros(mask.shape + (4,), np.uint8)
    rgba[..., 3] = mask * 255
    img = Image.fromarray(rgba).resize((ICON_PX, ICON_PX), Image.NEAREST)
    img = img.rotate(ICON_ROTATION, expand=True, resample=Image.NEAREST,
                     fillcolor=(0, 0, 0, 0))
    return img.resize((ICON_PX, ICON_PX), Image.NEAREST)


def test_icon_starts_use_the_game_s_player_colours(tmp_path):
    _, _, path = _script(tmp_path)
    icon = render_icon(parse_script(path)).convert("RGB")
    pixels = np.array(icon).reshape(-1, 3)
    for color in AOE_PLAYER_COLORS[:2]:            # the two starts in _script
        close = (np.abs(pixels.astype(int) - color).sum(1) < 30).sum()
        assert close > 100, f"no marker drawn in player colour {color}"


def test_terrain_bands_are_distinct():
    rgb = terrain_rgb(_island())
    assert len(np.unique(rgb.reshape(-1, 3), axis=0)) == 4
