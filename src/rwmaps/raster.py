"""Turn a :class:`~rwmaps.projection.MapWindow` into an AoE2 terrain grid."""

from __future__ import annotations

import numpy as np
import shapely
from scipy import ndimage
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


def _disk(radius: int) -> np.ndarray:
    """A circular boolean structuring element of the given radius, in tiles."""
    yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    return (yy**2 + xx**2) <= radius**2


def simplify_features(
    mask: np.ndarray, min_water_width: int = 0, min_land_width: int = 0
) -> np.ndarray:
    """Deliberately consolidate narrow coastline features before disc-cover
    ever sees them, instead of leaving their fate to per-generation RNG luck.

    Real-world coastlines have a long tail of straits, inlets and spits far
    narrower than the disc-cover approximation (and the engine's own organic
    ``create_land`` growth on top of it) can reliably reproduce - a real,
    measured example: an 8-generation sample of one Puget Sound window found
    a 21-tile-wide Elliott Bay rendered as genuine, dockable water only 1/3
    of the time, and sub-5-tile features (Rich Passage, Sinclair/Dyes Inlet)
    essentially never. Rather than hope the RNG is kind, this makes the
    decision explicit and deterministic:

    - ``min_water_width`` closes (dilate-then-erode) the land mask with a
      disk of radius ``min_water_width // 2``, filling any water channel
      narrower than that width with land - the tunable version of "delete
      Hood Canal" or "delete Rich Passage."
    - ``min_land_width`` opens (erode-then-dilate) the land mask with a disk
      of radius ``min_land_width // 2``, erasing any land bridge/spit
      narrower than that width - guards against a one-tile land sliver
      randomly cutting a wide strait in two (observed once crossing the
      Strait of Juan de Fuca near Port Angeles).

    Both default to 0 (no-op, preserves prior behaviour). Order matters:
    closing runs first so a freshly-filled narrow channel can't immediately
    be reopened by the land-bridge pass.
    """
    # Neither of scipy's border_value choices is correct here: border_value=0
    # (the default) treats everything just outside the grid as water, which
    # erodes real land at the map's edge down to nothing (verified: an
    # 80-100%-land edge came back 0% land after closing) - but border_value=1
    # over-corrects the other way, painting false land onto genuine open
    # water that reaches the edge (verified: an all-water array gained land
    # on part of its border). The actual fix is to not let the operation see
    # a "border" at all: pad by replicating each edge's own values outward,
    # run the operation, then crop back - real land-to-edge stays land, real
    # water-to-edge stays water, regardless of which the true geography is.
    def _edge_safe(op, arr: np.ndarray, structure: np.ndarray) -> np.ndarray:
        r = structure.shape[0] // 2
        padded = np.pad(arr, r, mode="edge")
        result = op(padded, structure=structure, border_value=1)
        return result[r:-r, r:-r]

    out = mask
    if min_water_width > 0:
        out = _edge_safe(ndimage.binary_closing, out, _disk(max(1, min_water_width // 2)))
    if min_land_width > 0:
        out = _edge_safe(ndimage.binary_opening, out, _disk(max(1, min_land_width // 2)))
    return out


def drop_small_islands(mask: np.ndarray, min_tiles: int = 16) -> np.ndarray:
    """Remove connected land components smaller than ``min_tiles``.

    The shipped real-world maps take this liberty by hand: a speckle of
    single-tile islets doesn't read as geography, it reads as rendering
    noise, and it isn't worth a player's attention either. ``min_tiles=16``
    is a 4x4-tile equivalent - the smallest patch that could plausibly hold
    anything. This does not (yet) try to bridge a dropped island to a
    neighbour via shallows first; it just removes it.
    """
    if min_tiles <= 0:
        return mask
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    if count == 0:
        return mask
    sizes = np.bincount(labels.reshape(-1), minlength=count + 1)
    keep = sizes >= min_tiles
    keep[0] = False  # background label
    return keep[labels]


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
    min_island_tiles: int = 0,
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

    if min_island_tiles:
        is_land = drop_small_islands(is_land, min_island_tiles)

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
