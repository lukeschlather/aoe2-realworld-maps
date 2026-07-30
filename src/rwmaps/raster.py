"""Turn a :class:`~rwmaps.projection.MapWindow` into an AoE2 terrain grid."""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from . import terrain as T
from .geodata import lakes, land
from .projection import MapWindow


def _clip_to_points(geom: BaseGeometry, lon: np.ndarray, lat: np.ndarray) -> BaseGeometry:
    """Trim a global geometry to the lon/lat extent actually sampled.

    Point-in-polygon is done in geographic space, so no polygon ever has to be
    pushed through the projection - that keeps pathological projections (poles,
    antimeridian, limited-domain azimuthals) from producing garbage outlines.
    """
    finite = np.isfinite(lon) & np.isfinite(lat)
    if not finite.any():
        return shapely.geometry.GeometryCollection()
    lo_x, hi_x = float(np.nanmin(lon)), float(np.nanmax(lon))
    lo_y, hi_y = float(np.nanmin(lat)), float(np.nanmax(lat))
    # A window that reaches nearly all the way round is cheaper left unclipped.
    if hi_x - lo_x > 350.0:
        return geom
    pad = 1.0
    return shapely.intersection(
        geom, box(lo_x - pad, max(-90.0, lo_y - pad), hi_x + pad, min(90.0, hi_y + pad))
    )


def _contains(geom: BaseGeometry, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Vectorised point-in-polygon; NaN coordinates are False."""
    if geom.is_empty:
        return np.zeros(lon.shape, dtype=bool)
    shapely.prepare(geom)
    safe_lon = np.where(np.isfinite(lon), lon, 0.0)
    safe_lat = np.where(np.isfinite(lat), lat, 0.0)
    hit = shapely.contains_xy(geom, safe_lon, safe_lat)
    return np.asarray(hit) & np.isfinite(lon) & np.isfinite(lat)


def _grow(mask: np.ndarray, steps: int) -> np.ndarray:
    """Binary dilation by ``steps`` using 8-connectivity."""
    out = mask.copy()
    for _ in range(steps):
        padded = np.pad(out, 1, constant_values=False)
        acc = np.zeros_like(out)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                acc |= padded[dy : dy + out.shape[0], dx : dx + out.shape[1]]
        out = acc
    return out


class RasterResult:
    def __init__(self, terrain: np.ndarray, land_mask: np.ndarray, offmap: np.ndarray):
        self.terrain = terrain
        self.land_mask = land_mask
        self.offmap = offmap

    @property
    def land_fraction(self) -> float:
        return float(self.land_mask.mean())


def rasterize(
    window: MapWindow,
    palette: T.Palette | None = None,
    *,
    include_lakes: bool = True,
    beach_width: int = 1,
    shallow_width: int = 2,
    medium_width: int = 5,
    offmap_is_land: bool = False,
    resolution: str = "10m",
) -> RasterResult:
    """Rasterise real coastlines into a grid of AoE2 terrain ids.

    Water is banded by distance from the shore into shore water / medium /
    deep so the result reads like the shipped real-world maps rather than a
    flat blue field.
    """
    palette = palette or T.Palette()
    lon, lat = window.tile_lonlat()
    offmap = ~np.isfinite(lon)

    land_geom = _clip_to_points(land(resolution), lon, lat)
    is_land = _contains(land_geom, lon, lat)

    if include_lakes:
        lake_geom = _clip_to_points(lakes(resolution), lon, lat)
        is_land &= ~_contains(lake_geom, lon, lat)

    if offmap_is_land:
        is_land |= offmap

    grid = np.full(is_land.shape, palette.deep, dtype=np.uint8)
    # Water depth bands, measured outward from the coastline.
    near = _grow(is_land, beach_width + shallow_width)
    mid = _grow(near, medium_width)
    grid[mid & ~is_land] = palette.medium
    grid[near & ~is_land] = palette.shallow
    grid[is_land] = palette.land
    if beach_width:
        coast = _grow(~is_land, beach_width) & is_land
        grid[coast] = palette.beach

    return RasterResult(grid, is_land, offmap)
