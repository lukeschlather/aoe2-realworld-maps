"""Screen candidate map windows before spending engine time on any of them.

A "window" is the whole geographic setup of a map: projection, centre,
span and rotation. Choosing one is the decision that fixes whether a map
reads as the real place, and it is settled by eye - so this produces the
pictures and the plain geometry facts, and nothing else. It renders no
verdict about which window is good.

Cheap on purpose. Everything here is the *shipped* raster pipeline
(``raster.rasterize`` at the CLI's own 50m default, then
``simplify_features`` at the shipped 4/3 widths, then ``rms_land.cover_mask``
at the shipped 700 discs / 0.85 overlap / 12.0 max radius) run to the point
where the coastline is decided, and stopped there. It skips
``choose_starts``, which is ~70s of annealing a candidate does not need to
be judged on its geography, so a window costs ~5s instead of ~70s.

What that means for the pictures: they show the coastline a generated map
will be cut from, with **no** forest, elevation, resources or start
positions, and no engine RNG. They are not renders. For those, generate the
window for real and capture it - see RENDER_PIPELINE.md.

Two views per candidate, and **both are in the in-game orientation** -
the grid turned counter-clockwise by ``thumbnail.ICON_ROTATION``, which is
what the engine does to draw it. Up in these pictures is up in the game, and
that is the only thing up is ever allowed to mean here.

* **truth** - the real coastline the window samples.
* **cover** - what 700 discs can actually approximate it with. This is the
  shape that ships; the gap between it and truth is the fidelity cost of
  the window.

Where geographic north ends up is then a property of the window rather than
of the picture, and it is exactly what ``--north`` sets: at ``--north 0``
north is up on screen, at ``--north -45`` it is toward the upper left. The
caption states it per candidate.

This used to be wrong and the wrongness was subtle. ``truth`` and ``cover``
were turned by ``north + ICON_ROTATION``, i.e. rendered with geographic
north at the top of the image whatever the window's orientation - and
described as "north up", a phrase that then meant something different from
what ``--north`` means. At the shipped ``--north -45`` that sum is zero, so
those two views showed the **raw grid, axis-aligned**, which is 45 degrees
off anything a player ever sees. ``thumbnail.render`` already warned that
showing the raw grid "was a way to be confidently wrong about which way a
map faces"; this module was doing it in two of its three pictures.

Usage:
    uv run python automation/window_candidates.py
    uv run python automation/window_candidates.py --groups britain,greatlakes
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import features, raster, rms_land, thumbnail  # noqa: E402
from rwmaps.cli import lands_for_size  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402

#: Everything the shipped defaults fix, restated here rather than imported
#: piecemeal, so a report can print the *complete* resolved parameter set
#: for a candidate instead of only what differs from a baseline.
#: Values are ``src/rwmaps/cli.py``'s own argparse defaults at 8 players.
SHIPPED = {
    "proj": "laea",
    "size": 240,
    "players": 8,
    "resolution": "50m",
    "min_water_width": 4,
    "min_land_width": 3,
    "min_island_tiles": 0,
    "overlap": 0.85,
    "max_radius": 12.0,
    "lands": lands_for_size(240),
}

#: A landmass smaller than this is not a place a player can start - it is
#: coastline speckle. Used only to describe a window, never to score it.
LANDMASS_FLOOR = 120

#: An enclosed water body smaller than this is an interstitial pocket, not
#: a lake or a sea. See ``thumbnail.MAX_POCKET_TILES`` for the same idea at
#: a much smaller scale.
WATERBODY_FLOOR = 60


@dataclass
class Candidate:
    group: str
    name: str
    lon: float
    lat: float
    span_km: float
    north: float
    note: str = ""
    proj: str = "laea"
    #: Overrides onto SHIPPED, for a candidate that needs a non-default knob.
    overrides: dict = field(default_factory=dict)
    #: ``features.parse`` specs, and/or preset names, applied to this window.
    #: Mask kinds (island/water) change the pictures; shallows and channels
    #: are RMS-stage and are drawn as an OVERLAY, clearly labelled - see the
    #: note on SHALLOW_MARK.
    features: list = field(default_factory=list)
    presets: list = field(default_factory=list)

    @property
    def params(self) -> dict:
        return {**SHIPPED, "proj": self.proj, **self.overrides}

    @property
    def command(self) -> str:
        """The exact rwmaps invocation that builds this window."""
        p = self.params
        parts = [
            "uv run rwmaps", f'"{self.name}"',
            f"--center={self.lon},{self.lat}",
            f"--span-km {self.span_km:g}",
        ]
        if self.north:
            parts.append(f"--north {self.north:g}")
        if p["proj"] != "laea":
            parts.append(f"--proj {p['proj']}")
        for name in self.presets:
            parts.append(f"--feature-preset {name}")
        for spec in self.features:
            parts.append(f"--feature {spec}")
        for flag, key in (("--overlap", "overlap"),
                          ("--min-water-width", "min_water_width"),
                          ("--min-land-width", "min_land_width"),
                          ("--max-radius", "max_radius"),
                          ("--resolution", "resolution")):
            if p[key] != SHIPPED[key] or key in self.overrides:
                parts.append(f"{flag} {p[key]:g}" if isinstance(p[key], float)
                             else f"{flag} {p[key]}")
        return " ".join(parts)


# --------------------------------------------------------------------------
# The candidates themselves.
#
# Great Lakes and Britain are the two the user named. Every other group is a
# proposed replacement for the three regions retired on 2026-08-15 (Japan,
# Caribbean, New Zealand - see update_mod.RETIRED_REGIONS), chosen to lean
# continental/peninsular rather than archipelago: all three retirees were
# ISLANDS-type with narrow coastlines, which is also the shape that starved
# gold and stone placement (MOD_STATUS.md).
# --------------------------------------------------------------------------

CANDIDATES: list[Candidate] = [
    # --- Great Lakes: wanted more recognisable, and north-up -------------
    Candidate("greatlakes", "GL current (north -45)", -84.0, 44.5, 1600, -45,
              "the window in cli.REGIONS today - north toward the upper left"),
    Candidate("greatlakes", "GL north-up, same window", -84.0, 44.5, 1600, 0,
              "current centre and span, rotated so north is up in game"),
    Candidate("greatlakes", "GL north-up 1400", -84.0, 44.5, 1400, 0),
    Candidate("greatlakes", "GL north-up 1200", -83.5, 44.5, 1200, 0),
    Candidate("greatlakes", "GL north-up 1000", -83.0, 45.0, 1000, 0),
    Candidate("greatlakes", "GL five lakes 1800", -85.0, 45.5, 1800, 0,
              "widest - tries to hold all five lakes inside the square"),
    Candidate("greatlakes", "GL Michigan-Huron", -85.0, 44.5, 1200, 0,
              "drops Superior and Ontario"),
    Candidate("greatlakes", "GL Huron-Erie", -82.0, 44.0, 1100, 0),
    Candidate("greatlakes", "GL Erie-Ontario", -79.5, 43.2, 900, 0,
              "the Niagara isthmus as the map's spine"),
    Candidate("greatlakes", "GL Superior", -87.5, 47.5, 1200, 0),

    # --- Britain: keep the current one, add a zoomed north-up ------------
    Candidate("britain", "Britain NW (current, keep)", -3.0, 54.5, 1300, -45,
              "the shipped window - north reads to the upper left"),
    Candidate("britain", "Britain north-up, same window", -3.0, 54.5, 1300, 0,
              "current centre and span, north up - reaches Norway"),
    Candidate("britain", "Britain north-up 1100", -2.8, 53.8, 1100, 0),
    Candidate("britain", "Britain north-up 1000", -2.5, 53.5, 1000, 0),
    Candidate("britain", "Britain north-up 950", -2.5, 53.2, 950, 0),
    Candidate("britain", "Britain north-up 900", -2.2, 53.0, 900, 0),
    Candidate("britain", "Britain north-up 1050 south", -3.0, 53.2, 1050, 0,
              "pushed south for more of the French Channel coast"),
    Candidate("britain", "Britain north-up 1150", -3.2, 54.0, 1150, 0),
    # The candidates above all put the continental patch's centroid near
    # 1.5E - that is Flanders and the Pas-de-Calais, not Normandy or
    # Brittany. Which one "a patch of France" means changes the window, so
    # both readings get candidates.
    Candidate("britain", "Britain north-up 1000 west", -4.0, 53.0, 1000, 0,
              "shifted west/south to reach Normandy and Brittany"),
    Candidate("britain", "Britain north-up 1050 west", -4.5, 52.8, 1050, 0),
    Candidate("britain", "Britain north-up 950 west", -4.0, 52.5, 950, 0),

    # --- proposed replacements -------------------------------------------
    Candidate("new", "Iberia", -4.0, 40.0, 1400, 0,
              "already in cli.REGIONS, never shipped; IoU 0.98 in the "
              "README candidate table"),
    Candidate("new", "Anatolia", 33.0, 39.0, 1500, 0,
              "already in cli.REGIONS, never shipped; IoU 0.97"),
    Candidate("new", "Gibraltar", -4.8, 36.2, 900, 0,
              "two continents and a strait"),
    Candidate("new", "Korea", 127.0, 36.5, 1200, 0),
    Candidate("new", "Red Sea", 37.5, 22.5, 2000, 0),
    Candidate("new", "Persian Gulf", 52.0, 27.0, 1700, 0),
    Candidate("new", "Baltic", 19.0, 59.0, 1500, 0),
    Candidate("new", "Levant and Cyprus", 33.5, 33.5, 1300, 0),
    Candidate("new", "Florida", -83.0, 27.5, 1700, 0),
    Candidate("new", "Bay of Biscay", -3.0, 45.0, 1400, 0),
    Candidate("new", "Adriatic", 16.5, 43.0, 1100, 0),
    Candidate("new", "Denmark", 10.0, 56.3, 1000, 0),
    Candidate("new", "Ireland", -8.0, 53.3, 900, 0),

    # --- Scandinavia: less open sea, coastline still legible --------------
    # Asked for 2026-08-17: the shipped window has a big empty sea, and the
    # fix wanted is the one that worked for Britain - keep the coastline
    # readable, trade dead water for land, by moving the window rather than
    # by changing any raster knob. Every row below is the shipped raster
    # pipeline; only centre, span and orientation vary.
    #
    # The shipped window is 2000 km at 16,62 with north toward the upper
    # left. Its dead water is the Norwegian Sea / North Atlantic off the
    # west and north-west coast: unlike the Baltic and the Gulf of Bothnia,
    # which are bounded by land on every side, that corner runs straight
    # off the map.
    Candidate("scandinavia", "Scand current NW (ships today)", 16.0, 62.0, 2000, -45,
              "the baseline every other row here is to be read against"),
    Candidate("scandinavia", "Scand current, north-up", 16.0, 62.0, 2000, 0,
              "same window, north-up - Britain's fix came out of the "
              "north-up family, so the orientation is on the table too"),
    # Push south, which is exactly Britain's fix: swap Arctic Norway and
    # open Atlantic for Denmark, the north German plain and Poland.
    Candidate("scandinavia", "Scand south 60N", 16.0, 60.0, 2000, -45),
    Candidate("scandinavia", "Scand south 58.5N", 16.0, 58.5, 2000, -45,
              "far enough south that the Baltic, not the Atlantic, is the "
              "map's main water"),
    Candidate("scandinavia", "Scand south 60N, north-up", 16.0, 60.0, 2000, 0),
    # Push east instead: drop the Atlantic entirely and gain Finland and
    # Karelia. Risks the fjord coastline, which is the most recognisable
    # thing about the region.
    Candidate("scandinavia", "Scand east 20E", 20.0, 62.0, 2000, -45),
    Candidate("scandinavia", "Scand southeast 19,60", 19.0, 60.0, 2000, -45),
    # Tighten the span as well as moving it, so the enclosed seas fill the
    # map instead of the ocean.
    Candidate("scandinavia", "Scand 1700 south", 16.0, 60.0, 1700, -45),
    Candidate("scandinavia", "Scand 1500 Baltic", 17.0, 59.0, 1500, -45,
              "compare 'Baltic' in the proposed-replacements group, which "
              "is the same idea at 19,59 north-up"),
    Candidate("scandinavia", "Scand 1300 Baltic", 16.5, 58.5, 1300, -45),
    Candidate("scandinavia", "Scand 1500 Baltic, north-up", 17.0, 59.0, 1500, 0),
    # Keep the fjords deliberately, and pay for them by cropping tighter.
    Candidate("scandinavia", "Scand peninsula 1600", 13.5, 60.5, 1600, -45,
              "west enough to keep the Norwegian coast as the feature"),
    Candidate("scandinavia", "Scand Bothnia 1600", 19.5, 62.5, 1600, -45,
              "the Gulf of Bothnia as an enclosed inland sea"),

    # --- connecting the seas, and Denmark ---------------------------------
    # The second round, which had no pictures until now: min-water-width is
    # what landlocks the Baltic, and the northern sea needs a shift.
    Candidate("connect", "east 20E @ mww4 (as first screened)",
              20.0, 62.0, 2000, -45,
              "reference. The Baltic is a 5,351-tile ENCLOSED lake here and "
              "the northern sea is a second, separate ocean"),
    Candidate("connect", "east 20E @ mww2", 20.0, 62.0, 2000, -45,
              "same window, width filter relaxed. In the raw coastline the "
              "Baltic and Atlantic are one 19,277-tile ocean; mww4 splits "
              "them. At 2 the Baltic reconnects - the north still does not",
              overrides={"min_water_width": 2}),
    Candidate("connect", "20,62.5 @ mww2", 20.0, 62.5, 2000, -45,
              "smallest shift that makes every sea one navigable ocean",
              overrides={"min_water_width": 2}),
    Candidate("connect", "22,63 @ mww2", 22.0, 63.0, 2000, -45,
              "one ocean, and a void radius of 93 - slightly better than the "
              "97 of the window this started from",
              overrides={"min_water_width": 2}),
    # Denmark. The islands are mask kinds and change these pictures; the
    # straits are shallows and are only an overlay - see SHALLOW_MARK.
    Candidate("connect", "22,63 @ mww2 + danish-straits",
              22.0, 63.0, 2000, -45,
              "straits only: nothing about the land changes, the overlay "
              "shows where the SHALLOWS blocks go",
              overrides={"min_water_width": 2}, presets=["danish-straits"]),
    Candidate("connect", "22,63 @ mww2 + zealand-funen",
              22.0, 63.0, 2000, -45,
              "islands forced to land as well. Forcing island added only 17 "
              "tiles, because Denmark is already fused land here - whether "
              "these read as two islands is the open question",
              overrides={"min_water_width": 2}, presets=["zealand-funen"]),
    Candidate("connect", "22,63 @ mww2 + zealand-funen-cut",
              22.0, 63.0, 2000, -45,
              "the belts cut as real water instead. This one DOES change how "
              "land units move, and is the comparison, not the proposal",
              overrides={"min_water_width": 2}, presets=["zealand-funen-cut"]),

    # --- which way is "a bit to the right"? --------------------------------
    # At --north -45 north is toward the upper LEFT in game, so screen-right
    # is SOUTHEAST and screen-left is NORTHWEST. Both are shown because the
    # two do opposite things and the answer should be picked off pictures
    # that are finally in the right orientation.
    Candidate("shift", "cut @ 22,63 (baseline)", 22.0, 63.0, 2000, -45,
              "the row this sweep is nudging",
              overrides={"min_water_width": 2}, presets=["zealand-funen-cut"]),
    Candidate("shift", "cut, screen-RIGHT a little (22.5,62.7)",
              22.5, 62.7, 2000, -45,
              "southeast. More land, less ocean",
              overrides={"min_water_width": 2}, presets=["zealand-funen-cut"]),
    Candidate("shift", "cut, screen-RIGHT more (23,62.5)", 23.0, 62.5, 2000, -45,
              "southeast again - this is where the Baltic route SEVERS",
              overrides={"min_water_width": 2}, presets=["zealand-funen-cut"]),
    Candidate("shift", "cut, screen-LEFT a little (21.5,63.3)",
              21.5, 63.3, 2000, -45,
              "northwest. Less land, more ocean",
              overrides={"min_water_width": 2}, presets=["zealand-funen-cut"]),
    Candidate("shift", "cut, screen-LEFT more (21,63.6)", 21.0, 63.6, 2000, -45,
              "northwest again",
              overrides={"min_water_width": 2}, presets=["zealand-funen-cut"]),
]


def _pieces(binary: np.ndarray, floor: int, lon: np.ndarray, lat: np.ndarray,
            drop_edge: bool = False) -> list[dict]:
    """Connected pieces of ``binary`` as ``{tiles, lon, lat}``, largest first.

    The centroid is carried because a bare size cannot answer the question
    actually being asked of it. "Britain plus a 5,000-tile piece" is two
    completely different maps depending on whether that piece is Ireland or
    the French Channel coast, and only the coordinate distinguishes them.
    """
    labels, n = ndimage.label(binary, structure=np.ones((3, 3), bool))
    if not n:
        return []
    skip: set[int] = set()
    if drop_edge:
        skip = (set(labels[0].tolist()) | set(labels[-1].tolist())
                | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist()))
    sizes = np.bincount(labels.ravel())
    out = []
    for i in range(1, n + 1):
        if i in skip or sizes[i] < floor:
            continue
        sel = labels == i
        out.append({
            "tiles": int(sizes[i]),
            "lon": round(float(np.nanmean(lon[sel])), 1),
            "lat": round(float(np.nanmean(lat[sel])), 1),
        })
    return sorted(out, key=lambda d: -d["tiles"])


def _nav_width(water: np.ndarray, a: tuple[int, int],
               b: tuple[int, int]) -> float:
    """Widest disc that can travel from ``a`` to ``b`` through water, in tiles.

    The bottleneck of a route, which is the thing "is there a way through"
    cannot express: two seas can be one connected component and still be
    joined by a single-tile thread. Binary search on clearance - keeping only
    water at least ``r`` from land and asking whether ``a`` and ``b`` are
    still connected there is exactly the question of whether a disc of radius
    ``r`` fits the whole way.
    """
    dist = ndimage.distance_transform_edt(water)
    lo, hi = 0.0, float(dist.max())
    for _ in range(24):
        mid = (lo + hi) / 2
        lab, _n = ndimage.label(dist >= mid, structure=np.ones((3, 3), bool))
        if lab[a] and lab[a] == lab[b]:
            lo = mid
        else:
            hi = mid
    return lo


def _nearest_water(lon, lat, water, tlon: float, tlat: float):
    d2 = np.where(water, (lon - tlon) ** 2 + ((lat - tlat) * 2.0) ** 2, np.inf)
    return np.unravel_index(int(np.argmin(d2)), d2.shape)


#: The two ends of the route that matters for Scandinavia: the Baltic, and
#: the open Atlantic north-west of Norway.
BALTIC_LONLAT = (19.0, 58.8)
ATLANTIC_LONLAT = (2.0, 66.0)


def _open_water(land: np.ndarray, lon: np.ndarray,
                lat: np.ndarray) -> dict:
    """The sea that runs off the edge of the map, and how empty it gets.

    ``waterbodies`` deliberately drops every edge-touching component,
    because it exists to count lakes and enclosed seas. That leaves the
    open ocean - the thing a player actually complains about when a window
    has "a big empty sea" - completely unmeasured, and land% cannot stand
    in for it: a window can be half water and read fine if the water is
    channels and fjords, or read badly if the same water is one dead
    corner.

    Two facts, both plain measurements:

    * ``open_pct`` - how much of the map is water connected to an edge.
    * ``void_radius`` - the distance from the emptiest water tile to the
      nearest land, in tiles. This is the radius of the largest circle of
      pure sea that fits on the map, i.e. how far a boat can get from
      anything at all. A fjord coastline scores low here however much
      water it has; one open basin scores high. ``void_lon``/``void_lat``
      say *which* sea that is, because the fix for a void in the Norwegian
      Sea is a different window from the fix for one in the Baltic.

    Neither is a verdict and neither has a threshold. They are here so a
    row can be compared against the window that ships today.
    """
    water = ~land
    labels, n = ndimage.label(water, structure=np.ones((3, 3), bool))
    edge = (set(labels[0].tolist()) | set(labels[-1].tolist())
            | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())) - {0}
    open_tiles = int(np.isin(labels, list(edge)).sum()) if edge else 0
    dist = ndimage.distance_transform_edt(water)
    iy, ix = np.unravel_index(int(np.argmax(dist)), dist.shape)
    void_comp = int((labels == labels[iy, ix]).sum()) if n else 0
    a = _nearest_water(lon, lat, water, *BALTIC_LONLAT)
    b = _nearest_water(lon, lat, water, *ATLANTIC_LONLAT)
    return {
        "nav_width_tiles": round(_nav_width(water, a, b), 2),
        "open_pct": round(open_tiles / land.size * 100, 1),
        "void_radius": round(float(dist.max()), 1),
        "void_lon": round(float(lon[iy, ix]), 1),
        "void_lat": round(float(lat[iy, ix]), 1),
        "void_sea_tiles": void_comp,
        "void_sea_pct": round(void_comp / land.size * 100, 1),
    }


#: Painted outside the grid when a view is turned. Deliberately NOT
#: ``thumbnail.DEEP``: filling the corners with deep water hides where the
#: map actually ends, and a turned grid is a diamond - the playable square
#: has its corners at the picture's edge midpoints. A reader has to be able
#: to see that boundary to judge how much of the geography is really on the
#: map.
OFFMAP = (26, 26, 26)

#: Shallows are painted on top of the mask pictures in this colour.
#:
#: They are an OVERLAY and not a render. Shallows never enter the land mask -
#: they are create_land blocks the engine paints at generation time - so this
#: shows *where the blocks go*, not what the engine will make of them. The
#: repo has been burned once by a Python preview being taken for validation,
#: so: judging whether Zealand and Funen read as two islands needs a real
#: capture, and this picture cannot answer it.
SHALLOW_MARK = (120, 200, 230)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def to_png(mask: np.ndarray, rotate_ccw: float, px: int = 300,
           shallows: np.ndarray | None = None) -> Image.Image:
    """Minimap-style land/water picture, turned ``rotate_ccw`` degrees.

    ``shallows`` is stamped on afterwards in ``SHALLOW_MARK`` - an overlay,
    not part of the mask. See that constant.
    """
    rgb = thumbnail.terrain_rgb(mask)
    if shallows is not None and shallows.any():
        rgb = rgb.copy()
        rgb[shallows] = SHALLOW_MARK
    img = Image.fromarray(rgb)
    if rotate_ccw % 360:
        img = img.rotate(rotate_ccw, expand=True, resample=Image.BICUBIC,
                         fillcolor=OFFMAP)
    return img.resize((px, px), Image.LANCZOS)


def b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def screen(c: Candidate, outdir: Path) -> dict:
    """Rasterise one candidate and write its three views."""
    p = c.params
    window = MapWindow.from_center(c.proj, c.lon, c.lat, c.span_km,
                                   p["size"], c.north)
    lon, lat = window.tile_lonlat()
    result = raster.rasterize(window, resolution=p["resolution"],
                              min_island_tiles=p["min_island_tiles"])
    truth = raster.simplify_features(result.land_mask,
                                     min_water_width=p["min_water_width"],
                                     min_land_width=p["min_land_width"])
    # Same order the CLI uses: mask overrides after the width filters, so
    # they cannot be closed back up.
    feature_notes: list[str] = []
    shallow_overlay = None
    if c.features or c.presets:
        feats = features.resolve(c.features or None, c.presets or None)
        truth, notes = features.apply_mask(truth, window, feats)
        discs, snotes = features.shallow_discs(window, feats)
        feature_notes = notes + snotes
        if discs:
            shallow_overlay = rms_land.rasterize_discs(discs, p["size"])
    discs = rms_land.cover_mask(truth, p["lands"], max_radius=p["max_radius"],
                                overlap=p["overlap"])
    cover = rms_land.rasterize_discs(discs, p["size"])

    # North-up means north at the top of the picture. Inside the grid, north
    # sits at bearing `rotate` clockwise from grid-up (see
    # projection.MapWindow.tile_lonlat), so turning the grid counter-clockwise
    # by `rotate` puts it back at the top. At rotate 45 that is the same turn
    # the game itself applies, which is exactly why 45 is the north-up value.
    # One rotation, the engine's. Not `north + ICON_ROTATION`: that renders
    # geographic north at the top of the image, which is a different frame
    # from the one the map is played in, and at --north -45 it degenerates to
    # the raw un-turned grid. See the module docstring.
    views = {
        "truth": to_png(truth, thumbnail.ICON_ROTATION,
                        shallows=shallow_overlay),
        "cover": to_png(cover, thumbnail.ICON_ROTATION,
                        shallows=shallow_overlay),
    }
    slug = c.name.lower().replace(" ", "_").replace(",", "").replace("(", "").replace(")", "")
    paths = {}
    for kind, img in views.items():
        path = outdir / c.group / f"{slug}__{kind}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        paths[kind] = str(path.relative_to(REPO)).replace("\\", "/")

    return {
        **asdict(c),
        "params": p,
        "command": c.command,
        "km_per_tile": c.span_km / p["size"],
        "land_fraction": float(truth.mean()),
        "cover_iou": rms_land.iou(cover, truth),
        "cover_misses_frac": rms_land.interior_holes(cover, truth),
        "landmasses": _pieces(truth, LANDMASS_FLOOR, lon, lat),
        "waterbodies": _pieces(~truth, WATERBODY_FLOOR, lon, lat, drop_edge=True),
        "open_water": _open_water(truth, lon, lat),
        "feature_notes": feature_notes,
        "png": paths,
        "b64": {k: b64(v) for k, v in views.items()},
    }


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


GROUP_TITLES = {
    "greatlakes": "Great Lakes",
    "britain": "Britain",
    "new": "Proposed replacements",
    "scandinavia": "Scandinavia — less open sea",
    "connect": "Scandinavia — connecting the seas, and Denmark",
    "shift": "Scandinavia — which way is “a bit to the right”?",
}

GROUP_BLURBS = {
    "shift": (
        "<code>zealand-funen-cut</code> at 22,63 nudged both ways, because "
        "“right” is a screen direction and at <code>--north -45</code> "
        "north is toward the upper left in game - which makes screen-right "
        "<b>southeast</b> and screen-left <b>northwest</b>. The two do "
        "opposite things, so guessing was not worth it.<br><br>"
        "Read the <b>navigable width</b> row. The Baltic-to-Atlantic route is "
        "already only about 1.4 tiles at its bottleneck, and moving southeast "
        "makes it worse until it <b>severs completely at 23,62.5</b> - "
        "southeast trades ocean for land (60.4% land at the baseline against "
        "65.5% there). If the aim is a wider ocean at the thin parts, "
        "screen-<i>left</i> is the direction, or widen the straits directly "
        "with a bigger carved channel rather than by moving the window."
    ),
    "connect": (
        "The second round of the Scandinavia work, which was numbers-only "
        "until now. Two asks: make the seas connect, and make the water "
        "around Copenhagen passable by boats. They turn out to have "
        "<b>one</b> root cause - <code>--min-water-width 4</code> seals the "
        "Danish straits, which both landlocks the Baltic into an enclosed "
        "5,351-tile lake and removes the only route past Copenhagen. In the "
        "raw coastline the Baltic and the Atlantic are a single 19,277-tile "
        "ocean. Compare the first two rows: same window, filter at 4 then "
        "at 2.<br><br>"
        "The northern sea is a genuine window problem rather than a filter "
        "one, and needs a shift north - 20,62.5 is the smallest that works, "
        "22,63 keeps a slightly better void radius.<br><br>"
        "<b>The shallows in these pictures are an overlay, not a render.</b> "
        "Shallows never enter the land mask - they are "
        "<code>create_land</code> blocks the engine paints at generation "
        "time - so the pale blue marks show <i>where the blocks go</i> and "
        "nothing about what the engine makes of them. Whether Zealand and "
        "Funen end up reading as two recognisable islands cannot be "
        "answered from this page; it needs a real capture. Note also that "
        "forcing <code>island</code> there adds only 17 tiles, because "
        "Denmark is already one fused landmass at this scale."
    ),
    "scandinavia": (
        "Asked for: <b>somewhat</b> less water, with the coastline still "
        "clearly defined - the same trade that turned Britain's north-up "
        "window into <code>Great Britain N</code>, where pushing the centre "
        "2 degrees south swapped open sea for enough of the continent to "
        "hold two town centres without closing the water that makes Britain "
        "an island. Nothing here changes a raster knob; only centre, span "
        "and orientation move.<br><br>"
        "<b>Land%</b> is the wrong column to read for this and is kept only "
        "for continuity: the shipped Scandinavia window is already 52.6% "
        "land in the engine, more than Britain's 29.3%, so its problem was "
        "never the amount of water. The two columns that speak to it are "
        "<b>open sea%</b> - water connected to the map edge, i.e. ocean "
        "that runs off the map rather than a bounded sea - and <b>void "
        "radius</b>, the distance in tiles from the emptiest water tile to "
        "the nearest land. A fjord or archipelago coast can hold a lot of "
        "water at a small void radius; one dead basin cannot. The Baltic "
        "and the Gulf of Bothnia are bounded on every side, so a window "
        "that leans on them keeps its water and loses the emptiness.<br><br>"
        "Neither column is a threshold or a score. Read every row against "
        "the first one, which is what ships today, and judge the pictures."
    ),
    "greatlakes": (
        "Asked for: more recognisable, and north up. <b>Waterbodies</b> is the "
        "column that speaks to the first - it lists every enclosed water body "
        "of "
        f"{WATERBODY_FLOOR}+ tiles, largest first, so a window where the lakes "
        "merge into one blob is visibly a different row from one where five of "
        "them survive as five. Note that <code>--min-water-width 4</code> fills "
        "any channel under ~4 tiles, so the Straits of Mackinac and the Detroit "
        "and St Clair rivers close and Michigan/Huron/Erie separate rather than "
        "chain."
    ),
    "britain": (
        "The current shipped window is kept as-is (first row). The rest are "
        "north-up candidates, zoomed in far enough to drop Norway while keeping "
        "a patch of the French Channel coast. <b>Landmasses</b> is the column "
        "for that: the largest entry is Britain, and a continental patch big "
        "enough for two players has to show up as a second entry of real size."
    ),
    "new": (
        "Proposed replacements for Japan, Caribbean and New Zealand. All three "
        "retirees were ISLANDS-type with narrow coastlines, so these lean "
        "continental and peninsular. Iberia and Anatolia already exist in "
        "<code>cli.REGIONS</code> and have never shipped."
    ),
}


def render_html(rows: list[dict], stamp: str, commit: str, data_dir: str) -> str:
    def card(r: dict) -> str:
        fmt = lambda ps: " &middot; ".join(  # noqa: E731
            f'{d["tiles"]:,}<span class="dim"> at {d["lon"]},{d["lat"]}</span>'
            for d in ps) or "&mdash;"
        masses = fmt(r["landmasses"][:5])
        waters = fmt(r["waterbodies"][:6])
        ow = r["open_water"]
        fnotes = r.get("feature_notes") or []
        feature_row = ""
        if fnotes:
            feature_row = (
                '<tr><th>features</th><td colspan="3">'
                + "<br>".join(_esc(n) for n in fnotes)
                + ' <span class="dim">&mdash; shallows are an overlay, not a '
                  'render: only an engine capture shows what they become'
                  '</span></td></tr>')
        p = r["params"]
        # Where north points in the pictures above, said in words, because
        # a signed degree count is the thing that keeps getting misread.
        bearing = {0: "north is UP in game", -45: "north toward the UPPER LEFT",
                   45: "north toward the UPPER RIGHT",
                   90: "north to the RIGHT", -90: "north to the LEFT",
                   180: "north is DOWN"}.get(
            r["north"], f"north {r['north']:g}° clockwise from up")
        return f"""
    <article class="cand">
      <h3>{r['name']}</h3>
      {f'<p class="note">{r["note"]}</p>' if r["note"] else ''}
      <div class="views">
        <figure><img src="{r['b64']['truth']}" loading="lazy">
          <figcaption>real coastline &mdash; in-game orientation</figcaption></figure>
        <figure><img src="{r['b64']['cover']}" loading="lazy">
          <figcaption>700-disc cover (this is what ships) &mdash;
            in-game orientation</figcaption></figure>
      </div>
      <table class="facts">
        <tr><th>centre</th><td>{r['lon']}, {r['lat']}</td>
            <th>span</th><td>{r['span_km']:g} km ({r['km_per_tile']:.2f} km/tile)</td></tr>
        <tr><th>north on screen</th><td>{r['north']:g}&deg; &mdash; {bearing}</td>
            <th>projection</th><td>{p['proj']}</td></tr>
        <tr><th>land</th><td>{r['land_fraction']*100:.1f}%</td>
            <th>cover IoU</th><td>{r['cover_iou']:.3f}
              ({r['cover_misses_frac']*100:.1f}% of land missed)</td></tr>
        <tr><th>landmasses</th><td colspan="3">{masses}
              <span class="dim">tiles, &ge;{LANDMASS_FLOOR}</span></td></tr>
        <tr><th>waterbodies</th><td colspan="3">{waters}
              <span class="dim">tiles enclosed, &ge;{WATERBODY_FLOOR}</span></td></tr>
        {feature_row}
        <tr><th>navigable width</th><td colspan="3">
              <b>{ow.get('nav_width_tiles', 0):.2f} tiles</b> from the Baltic
              to the open Atlantic &mdash; the bottleneck of the route, in the
              land mask. 0 means no way through at all.
              <span class="dim">Shallows are navigable terrain the mask does
              not model, so where a candidate carries them this is a LOWER
              bound.</span></td></tr>
        <tr><th>open sea</th><td colspan="3">{ow['open_pct']:.1f}% of the map
              runs off the edge &middot; emptiest point <b>{ow['void_radius']:.0f}
              tiles</b> from land, at {ow['void_lon']:g}, {ow['void_lat']:g}
              &middot; that sea is {ow['void_sea_tiles']:,} tiles
              ({ow['void_sea_pct']:.1f}% of the map)
              <span class="dim">&mdash; measurements, not thresholds</span></td></tr>
        <tr><th>grid</th><td colspan="3">{p['size']}&times;{p['size']},
              {p['players']} players, {p['lands']} discs, overlap {p['overlap']},
              max radius {p['max_radius']:g}, {p['resolution']} coastline,
              min water/land width {p['min_water_width']}/{p['min_land_width']},
              min island {p['min_island_tiles']} tiles</td></tr>
      </table>
      <pre class="cmd">{r['command']}</pre>
    </article>"""

    sections = []
    # Iterate the registry, not a hardcoded tuple. A group added to
    # CANDIDATES and GROUP_TITLES but missing from that tuple was screened,
    # written to candidates.json and given its PNGs, and then silently left
    # out of the HTML - a 2.7 KB report after a clean "13 candidates" run
    # and exit 0, which is the worst possible way for this to fail.
    for group in GROUP_TITLES:
        got = [r for r in rows if r["group"] == group]
        if not got:
            continue
        sections.append(
            f'<section><h2>{GROUP_TITLES[group]}</h2>'
            f'<p class="blurb">{GROUP_BLURBS[group]}</p>'
            + "".join(card(r) for r in got) + "</section>"
        )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Window candidates</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ background:#141414; color:#e6e6e6; margin:0 auto; max-width:1180px;
        padding:2rem 1.5rem 5rem; font:15px/1.55 system-ui, sans-serif; }}
 h1 {{ margin:0 0 .3rem; font-size:1.7rem; }}
 h2 {{ margin:2.8rem 0 .4rem; font-size:1.3rem;
       border-bottom:1px solid #333; padding-bottom:.35rem; }}
 h3 {{ margin:0 0 .2rem; font-size:1.05rem; }}
 .meta, .note, .dim {{ color:#9a9a9a; }}
 .meta {{ font-size:.86rem; margin:0 0 1.4rem; }}
 .blurb {{ color:#bdbdbd; margin:.5rem 0 1.4rem; max-width:70ch; }}
 .note {{ font-size:.88rem; margin:0 0 .6rem; }}
 .cand {{ border:1px solid #2e2e2e; border-radius:8px; padding:1rem 1.1rem;
          margin:0 0 1.3rem; background:#191919; }}
 .views {{ display:flex; gap:.9rem; flex-wrap:wrap; margin:.6rem 0 .9rem; }}
 figure {{ margin:0; }}
 figure img {{ width:300px; max-width:100%; display:block; border-radius:4px;
               background:#0d1b2a; }}
 figcaption {{ color:#8f8f8f; font-size:.78rem; padding-top:.28rem; }}
 table.facts {{ border-collapse:collapse; font-size:.87rem; width:100%; }}
 table.facts th {{ text-align:left; color:#8f8f8f; font-weight:500;
                   padding:.16rem .8rem .16rem 0; white-space:nowrap;
                   vertical-align:top; width:1%; }}
 table.facts td {{ padding:.16rem 1.4rem .16rem 0; vertical-align:top; }}
 pre.cmd {{ background:#101010; border:1px solid #2b2b2b; border-radius:5px;
            padding:.55rem .7rem; margin:.8rem 0 0; overflow-x:auto;
            font-size:.8rem; color:#c8d6c8; }}
 code {{ background:#242424; padding:.05rem .3rem; border-radius:3px; }}
 .caveat {{ border-left:3px solid #6b5a2a; background:#1e1a12; padding:.7rem 1rem;
            margin:1.2rem 0; color:#d8d0b8; max-width:78ch; }}
</style>
<h1>Window candidates</h1>
<p class="meta">Generated {stamp} &middot; repo commit <code>{commit}</code>
 &middot; PNGs and <code>candidates.json</code> in
 <code>{data_dir}</code></p>

<div class="caveat">
 <b>These are not renders.</b> Each picture is the shipped raster pipeline run
 as far as the coastline and stopped: real Natural Earth 50m coastline, the
 shipped 4/3 feature-width simplification, and the 700-disc cover a generated
 <code>.rms</code> is built from. There is no forest, elevation, resource or
 start placement here and no engine RNG, because none of that is decided by
 the window. Nothing on this page says whether a window is good &mdash; that
 is the thing to judge by eye. Once windows are picked, they get generated for
 real and captured out of the engine.
</div>

{''.join(sections)}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default=None,
                    help="comma-separated subset of "
                         + ",".join(GROUP_TITLES))
    ap.add_argument("--tag", default="window_candidates")
    args = ap.parse_args()

    wanted = ({g.strip() for g in args.groups.split(",")}
              if args.groups else set(GROUP_TITLES))
    cands = [c for c in CANDIDATES if c.group in wanted]

    stamp_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    data_dir = REPO / "reports" / f"{stamp_id}_{args.tag}_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    t0 = time.time()
    for i, c in enumerate(cands, 1):
        t1 = time.time()
        rows.append(screen(c, data_dir))
        print(f"[{i}/{len(cands)}] {c.group}/{c.name}: "
              f"land {rows[-1]['land_fraction']*100:.1f}%, "
              f"IoU {rows[-1]['cover_iou']:.3f}, "
              f"{time.time()-t1:.1f}s")

    lean = [{k: v for k, v in r.items() if k != "b64"} for r in rows]
    (data_dir / "candidates.json").write_text(
        json.dumps(lean, indent=2), encoding="utf-8")

    html = render_html(
        rows,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        git_commit(),
        str(data_dir.relative_to(REPO)).replace("\\", "/"),
    )
    out = REPO / "reports" / f"{stamp_id}_{args.tag}.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n{len(rows)} candidates in {time.time()-t0:.0f}s")
    print(f"-> {out}")
    print(f"-> {data_dir}")


if __name__ == "__main__":
    main()
