"""Map an arbitrary cartographic projection onto the AoE2 tile grid.

Grid convention, confirmed by rendering the shipped ``real_world_britain.scx``
terrain in all eight dihedral orientations: the grid is stored ``[y][x]`` with
``y = 0`` at the **north** edge and ``x = 0`` at the **west** edge, i.e. plain
north-up. The engine's isometric view then rotates the square 45 degrees
counter-clockwise on screen, so a grid stored north-up is NOT displayed
north-up.

**Orientation is specified in screen space, because that is the only frame
anyone makes decisions in.** ``north_deg`` is where north points on screen,
clockwise from straight up:

* ``0`` - north straight up. This is the default, and what you want unless
  you have a reason.
* ``-45`` - north toward the upper left, which is what the engine does to a
  grid with no correction applied.

The grid-space rotation that produces it is an implementation detail
(:attr:`MapWindow.grid_rotate_deg`) and nothing outside this module should
need it. Specifying orientation in grid space is what produced years of
"north-up means 45" confusion in this project's own docs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from pyproj import CRS, Transformer

WGS84 = CRS.from_epsg(4326)

#: Degrees the engine turns the grid counter-clockwise when it draws it.
#: The isometric camera; measured against the stock real-world maps' own
#: icons (see ``thumbnail.ICON_ROTATION``, which is the same turn applied to
#: a picture). A grid stored north-up therefore displays with north toward
#: the upper left, not up.
SCREEN_TURN = 45.0


def north_from_legacy_rotate(rotate_deg: float) -> float:
    """Convert a pre-2026-08-16 grid-space ``rotate`` to ``north_deg``.

    Records written before the switch (``results.jsonl``, archived reports)
    store the grid rotation under the key ``rotate``. Feeding one of those
    straight in as ``north_deg`` builds a truth mask 45 degrees off, which
    corrupts IoU silently rather than failing - so anything reading an old
    record must come through here.
    """
    return rotate_deg - SCREEN_TURN

#: Handy projection families. ``{lon}``/``{lat}`` are filled from the centre.
PROJECTIONS: dict[str, str] = {
    # Equal-area, minimal distortion for a region centred anywhere. Good default.
    "laea": "+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    # Conformal; preserves local shape and angles.
    "stere": "+proj=stere +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    # The classic web/marine projection.
    "merc": "+proj=merc +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    # Plate carree - lon/lat straight onto the grid.
    "eqc": "+proj=eqc +lat_ts={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    # Equidistant azimuthal: true distance from the centre point.
    "aeqd": "+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    # The globe as seen from space.
    "ortho": "+proj=ortho +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    # Whole-world pseudocylindrical equal-area projections.
    "eck4": "+proj=eck4 +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    "moll": "+proj=moll +lon_0={lon} +datum=WGS84 +units=m +no_defs",
    "robin": "+proj=robin +lon_0={lon} +datum=WGS84 +units=m +no_defs",
}


def build_crs(proj: str, lon: float, lat: float) -> CRS:
    """Resolve ``proj`` to a CRS.

    ``proj`` may be a key of :data:`PROJECTIONS`, a raw PROJ string, an EPSG
    code such as ``"EPSG:3035"``, or anything else :class:`pyproj.CRS` accepts.
    """
    if proj in PROJECTIONS:
        return CRS.from_proj4(PROJECTIONS[proj].format(lon=lon, lat=lat))
    try:
        return CRS.from_user_input(proj.format(lon=lon, lat=lat))
    except Exception:
        return CRS.from_user_input(proj)


@dataclass
class MapWindow:
    """A square window in projected space sampled onto an ``size x size`` grid."""

    crs: CRS
    center_x: float
    center_y: float
    span: float
    """Width and height of the window in the CRS's units (metres, usually)."""
    size: int
    north_deg: float = 0.0
    """Where north points on screen, clockwise from straight up. 0 = north up."""

    _to_wgs: Transformer = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._to_wgs = Transformer.from_crs(self.crs, WGS84, always_xy=True)

    @classmethod
    def from_center(
        cls,
        proj: str,
        lon: float,
        lat: float,
        span_km: float,
        size: int,
        north_deg: float = 0.0,
    ) -> "MapWindow":
        """Build a window ``span_km`` across, centred on ``lon``/``lat``.

        ``north_deg`` is screen-space - see the module docstring.
        """
        crs = build_crs(proj, lon, lat)
        fwd = Transformer.from_crs(WGS84, crs, always_xy=True)
        cx, cy = fwd.transform(lon, lat)
        if not (math.isfinite(cx) and math.isfinite(cy)):
            raise ValueError(
                f"centre ({lon}, {lat}) is outside the domain of projection {proj!r}"
            )
        return cls(crs, cx, cy, span_km * 1000.0, size, north_deg)

    @classmethod
    def whole_world(cls, proj: str = "eck4", lon: float = 0.0, size: int = 480) -> "MapWindow":
        """A window covering the entire globe in a world projection."""
        crs = build_crs(proj, lon, 0.0)
        fwd = Transformer.from_crs(WGS84, crs, always_xy=True)
        xs, ys = [], []
        for test_lon in np.linspace(lon - 179.9, lon + 179.9, 73):
            for test_lat in (-89.9, -45.0, 0.0, 45.0, 89.9):
                x, y = fwd.transform(((test_lon + 180) % 360) - 180, test_lat)
                if math.isfinite(x) and math.isfinite(y):
                    xs.append(x)
                    ys.append(y)
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        return cls(crs, (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, span, size)

    @property
    def grid_rotate_deg(self) -> float:
        """Rotation applied inside the grid, derived from :attr:`north_deg`.

        In the grid, real north sits at bearing ``grid_rotate_deg`` clockwise
        from grid-up. The engine then turns the picture 45 degrees
        counter-clockwise, which moves that bearing to
        ``grid_rotate_deg - 45`` on screen. Setting that equal to
        ``north_deg`` gives the offset below.
        """
        return self.north_deg + SCREEN_TURN

    def tile_lonlat(self) -> tuple[np.ndarray, np.ndarray]:
        """Longitude/latitude of every tile centre, shaped ``(size, size)``.

        Row 0 is the north edge, column 0 the west edge. Tiles that fall outside
        the projection's domain come back as NaN.
        """
        n = self.size
        step = self.span / n
        # Tile centres, north-up: y decreases as the row index grows.
        offs = (np.arange(n) + 0.5) * step - self.span / 2
        gx, gy = np.meshgrid(offs, -offs)

        if self.grid_rotate_deg % 360:
            theta = math.radians(self.grid_rotate_deg)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            gx, gy = gx * cos_t - gy * sin_t, gx * sin_t + gy * cos_t

        px = self.center_x + gx
        py = self.center_y + gy
        lon, lat = self._to_wgs.transform(px, py)
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        bad = ~np.isfinite(lon) | ~np.isfinite(lat) | (np.abs(lat) > 90.0000001)
        lon[bad] = np.nan
        lat[bad] = np.nan
        return lon, lat

    @property
    def km_per_tile(self) -> float:
        return self.span / self.size / 1000.0
