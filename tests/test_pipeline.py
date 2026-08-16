"""End-to-end checks for the projection -> raster pipeline."""

from __future__ import annotations

import numpy as np
import pytest

from rwmaps import raster, terrain
from rwmaps.projection import MapWindow


def test_grid_is_north_up():
    """Row 0 must be north of the last row, column 0 west of the last column."""
    window = MapWindow.from_center("eqc", lon=0.0, lat=0.0, span_km=2000, size=16)
    lon, lat = window.tile_lonlat()
    assert lat[0, 0] > lat[-1, 0], "row 0 should be the north edge"
    assert lon[0, 0] < lon[0, -1], "column 0 should be the west edge"


def test_center_lands_on_center_tile():
    window = MapWindow.from_center("laea", lon=-3.0, lat=54.5, span_km=1000, size=100)
    lon, lat = window.tile_lonlat()
    assert lon[50, 50] == pytest.approx(-3.0, abs=0.2)
    assert lat[50, 50] == pytest.approx(54.5, abs=0.2)


def test_rotation_moves_geography():
    kwargs = dict(proj="laea", lon=10.0, lat=45.0, span_km=1500, size=32)
    plain = MapWindow.from_center(**kwargs).tile_lonlat()[1]
    turned = MapWindow.from_center(**kwargs, north_deg=90).tile_lonlat()[1]
    assert not np.allclose(plain, turned, equal_nan=True)


@pytest.mark.parametrize("proj", ["laea", "stere", "merc", "eqc", "aeqd", "ortho"])
def test_projections_produce_finite_centres(proj):
    window = MapWindow.from_center(proj, lon=12.0, lat=42.0, span_km=1200, size=24)
    lon, lat = window.tile_lonlat()
    assert np.isfinite(lon).mean() > 0.9


def test_britain_is_mostly_land_in_the_middle_and_water_at_the_edges():
    window = MapWindow.from_center("laea", lon=-3.0, lat=54.5, span_km=1300, size=120)
    result = raster.rasterize(window, terrain.BIOMES["temperate"])
    assert 0.15 < result.land_fraction < 0.5
    # The Atlantic occupies the west edge of this window.
    west_edge = result.land_mask[:, :3]
    assert west_edge.mean() < 0.15


def test_ocean_is_banded_by_depth():
    window = MapWindow.from_center("laea", lon=-3.0, lat=54.5, span_km=1300, size=120)
    grid = raster.rasterize(window, terrain.BIOMES["temperate"]).terrain
    present = set(np.unique(grid).tolist())
    for tid in (terrain.WATER, terrain.MED_WATER, terrain.DEEP_WATER, terrain.BEACH):
        assert tid in present, f"terrain id {tid} missing from the raster"




def test_whole_world_window_covers_both_hemispheres():
    # A 2:1 world projection fitted into a square leaves empty bands north and
    # south of the map, so the extreme sampled latitude is short of the pole.
    window = MapWindow.whole_world("eck4", size=256)
    lon, lat = window.tile_lonlat()
    assert np.nanmax(lat) > 80
    assert np.nanmin(lat) < -80
    assert np.nanmax(lon) > 170
    assert np.nanmin(lon) < -170


def test_orientation_is_screen_space():
    """``north_deg`` says where north points ON SCREEN, 0 = straight up.

    Pinned because the grid and the screen are 45 degrees apart and this
    project spent a long time documenting that gap instead of removing it.
    The engine turns the grid 45 degrees counter-clockwise to draw it, so a
    grid stored north-up displays with north toward the upper left - which
    is ``north_deg == -45``, not 0.
    """
    from rwmaps.projection import (SCREEN_TURN, MapWindow,
                                   north_from_legacy_rotate)

    kw = dict(proj="laea", lon=-3.0, lat=54.5, span_km=1000, size=64)
    assert MapWindow.from_center(**kw, north_deg=0).grid_rotate_deg == SCREEN_TURN
    assert MapWindow.from_center(**kw, north_deg=-SCREEN_TURN).grid_rotate_deg == 0
    # A window with no rotation applied inside the grid is the engine's
    # uncorrected view, and must NOT be described as north-up.
    assert north_from_legacy_rotate(0) == -SCREEN_TURN
    assert north_from_legacy_rotate(SCREEN_TURN) == 0
