"""Targeted geographic overrides: say "there is an island *here*".

Every other knob in this project is global - ``--min-water-width`` applies
to the whole map and cannot know that the channel it is filling is the
Oeresund. That works until the geography that matters is smaller than a
tile, and then no global setting can recover it. At the shipped 240 grid a
2000 km window is 8.3 km per tile, and the Danish straits measure roughly
4 km (Oeresund), 16 km (Great Belt) and 1 km (Little Belt). Two of those
three are sub-tile: they are not lost to a badly chosen threshold, they are
below the sampling rate, and the only way to put them back is to say where
they go.

Three kinds, and the distinction between the last two is the whole point:

``island``
    Force land. Carves a recognisable island out of a coastline the disc
    cover would otherwise smear into the mainland.
``water``
    Force water. Cuts a channel, and **changes how land units move** - what
    is walkable today becomes a crossing that needs a boat.
``channel``
    A *line* of shallows, from one point to another. A strait is a line, not
    a blob: one disc at the Great Belt leaves Zealand joined to Funen at
    both ends of it, so separating two islands needs a chain. Spec carries
    two points: ``channel:lon1,lat1,lon2,lat2,width_km``.
``shallows``
    Emit a ``create_land`` of ``SHALLOWS`` terrain. Shallows are passable by
    boats *and* fordable by land units, so this **adds** naval passage
    without removing any land route that exists today. That is what makes it
    the right tool for the Danish straits: Denmark stays walkable exactly as
    it is now, and boats can still get from the Baltic to the Kattegat.

The last two run in both directions, and the second direction is worth
stating because it is not obvious from the name. Laid across *land*, a
channel of shallows adds a sea route - the Danish straits. Laid across
*water*, the same block adds a **land** route: a ford across a strait that
is otherwise a boat crossing, joining two landmasses for land units without
removing the sea route through it either. ``britain-crossings`` is that use.
Both are purely additive, which is the property ``water`` does not have.

``water`` and ``shallows`` are therefore not interchangeable, and
``WATER_SHALLOW`` (terrain 1, the blue shore water) is a third thing again -
passable by boats and *not* fordable, so it would change land movement just
as ``water`` does. Only ``SHALLOWS`` (terrain 4) is purely additive. See
``terrain.py``.

The engine mechanism is not invented here: stock ``Canals.rms``,
``Loch Ness.rms`` and ``Aquarena.rms`` all place ``create_land`` blocks with
``terrain_type SHALLOWS``, which is exactly what ``rms_land._land_block``
already emits.

Mask kinds (``island``/``water``) are applied *after*
``raster.simplify_features``, so the width filters cannot undo them, and
*before* ``choose_starts``, so a forced island can hold a start and a forced
channel is not walked across.

Specs are ``kind:lon,lat,radius_km``::

    --feature shallows:12.75,55.85,12
    --feature island:11.8,55.55,55
    --feature-preset danish-straits
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KINDS = ("island", "water", "shallows", "channel")


@dataclass(frozen=True)
class Feature:
    kind: str
    lon: float
    lat: float
    radius_km: float
    note: str = ""
    #: Second endpoint, for ``channel`` only. A strait is a line.
    lon2: float | None = None
    lat2: float | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown feature kind {self.kind!r}; "
                             f"expected one of {KINDS}")
        if self.kind == "channel" and (self.lon2 is None or self.lat2 is None):
            raise ValueError("a channel needs two endpoints: "
                             "channel:lon1,lat1,lon2,lat2,width_km")


def parse(spec: str) -> Feature:
    """``kind:lon,lat,radius_km``, or ``channel:lon1,lat1,lon2,lat2,width_km``."""
    try:
        kind, rest = spec.split(":", 1)
        kind = kind.strip().lower()
        nums = [float(v) for v in rest.split(",")]
        if kind == "channel":
            lon, lat, lon2, lat2, radius = nums
            return Feature(kind, lon, lat, radius, lon2=lon2, lat2=lat2)
        lon, lat, radius = nums
    except ValueError as e:
        raise ValueError(
            f"bad feature spec {spec!r}; want kind:lon,lat,radius_km, or "
            f"channel:lon1,lat1,lon2,lat2,width_km "
            f"(kinds: {', '.join(KINDS)})") from e
    return Feature(kind, lon, lat, radius)


#: Reusable named sets, so a window does not re-derive them and the
#: coordinates get reviewed once. Radii are deliberately generous - a disc
#: has to be at least a tile across at 8.3 km/tile to survive at all.
PRESETS: dict[str, list[Feature]] = {
    # Boat passage through the Danish straits, land movement untouched. The
    # Oeresund is ~4 km and the Little Belt ~1 km, both sub-tile at 8.3
    # km/tile, so there is no Baltic-to-Kattegat route for min-water-width
    # to find however low it is set. Each strait is a channel, not a disc:
    # one blob in the middle of the Great Belt leaves Zealand joined to
    # Funen at both ends of it.
    "danish-straits": [
        Feature("channel", 12.65, 55.45, 12, "Oeresund, Copenhagen-Malmoe",
                lon2=12.90, lat2=56.15),
        Feature("channel", 10.95, 54.95, 13, "Great Belt, Zealand-Funen",
                lon2=11.10, lat2=55.95),
        Feature("channel", 9.60, 55.15, 11, "Little Belt, Funen-Jutland",
                lon2=9.95, lat2=55.75),
    ],
    # The straits above, plus the two islands forced to land so the disc
    # cover cannot leave a hole where they should be. Land movement is still
    # untouched - the separation is shallows, which are fordable.
    "zealand-funen": [
        Feature("island", 11.80, 55.55, 48, "Zealand"),
        Feature("island", 10.35, 55.30, 32, "Funen"),
        Feature("channel", 12.65, 55.45, 12, "Oeresund",
                lon2=12.90, lat2=56.15),
        Feature("channel", 10.95, 54.95, 13, "Great Belt",
                lon2=11.10, lat2=55.95),
        Feature("channel", 9.60, 55.15, 11, "Little Belt",
                lon2=9.95, lat2=55.75),
    ],
    # ``zealand-funen`` with the Oeresund running the WHOLE length of the
    # sound. The one in that preset starts at 55.45, and the sound opens to
    # the Baltic at about 55.25 - Drogden and Falsterbo - so it stopped
    # short and left Zealand joined to Scania at the southern end. Measured
    # on the land mask with the shallows carved out, i.e. the same view the
    # capture's land-piece list takes:
    #
    # | window            | zealand-funen        | this preset          |
    # |-------------------|----------------------|----------------------|
    # | baseline 21.5,63.8| Zealand mainland     | Zealand mainland     |
    # | south 10 tiles    | Zealand mainland     | **Zealand 135 t**    |
    # | south 15 tiles    | Zealand mainland     | **Zealand 135 t**    |
    # | south 20 tiles    | Zealand mainland     | **Zealand 135 t**    |
    #
    # Funen separates either way (45 tiles, which is its real area at 69
    # km2/tile). 135 tiles is Zealand with Lolland and Falster fused onto
    # it, which is what that archipelago looks like at 8.3 km/tile.
    #
    # It does nothing for the baseline window, where Denmark sits closer to
    # the edge - so this is a fix for the southward-shifted family, not a
    # strict improvement everywhere. Paper measurement; an engine capture is
    # what decides whether the engine renders the band it is asked for.
    #
    # A NEW NAME rather than an edit to ``zealand-funen``, and that is a
    # rule: **entries here are append-only.** A preset name is part of a
    # preset's argv and therefore of its ``params_hash``, but the
    # coordinates behind the name are not - so editing this table in place
    # would silently change what an already-captured record means while its
    # hash went on claiming the parameters had not moved.
    "zealand-funen-sound": [
        Feature("island", 11.80, 55.55, 48, "Zealand"),
        Feature("island", 10.35, 55.30, 32, "Funen"),
        Feature("channel", 12.55, 55.25, 12, "Oeresund, whole sound",
                lon2=12.95, lat2=56.20),
        Feature("channel", 10.95, 54.95, 13, "Great Belt",
                lon2=11.10, lat2=55.95),
        Feature("channel", 9.60, 55.15, 11, "Little Belt",
                lon2=9.95, lat2=55.75),
    ],
    # As above but the belts are cut as real water, so Zealand and Funen are
    # islands you need a boat to reach. This DOES change land movement, and
    # is here as the comparison rather than the recommendation.
    "zealand-funen-cut": [
        Feature("island", 11.80, 55.55, 48, "Zealand"),
        Feature("island", 10.35, 55.30, 32, "Funen"),
        Feature("water", 12.75, 55.85, 11, "Oeresund cut"),
        Feature("water", 11.00, 55.45, 12, "Great Belt cut"),
        Feature("water", 9.75, 55.45, 10, "Little Belt cut"),
    ],
    # Shallows used the other way round: fords across three straits, so land
    # units can walk between the three landmasses both Britain windows put on
    # the map. Both ship as three separate pieces today - Britain+Wales+
    # Scotland, Ireland, and the continent - and nothing joins them.
    #
    # Where each line goes, and why it is not the textbook narrowest point:
    #
    # * **North Channel**, Larne to the Rhins of Galloway, ~44 km. The real
    #   narrowest is Torr Head to the Mull of Kintyre at ~21 km, but Kintyre
    #   is a 37-tile island on the ``britain`` window and open water on
    #   ``great-britain-n``, so a ford to it joins nothing to anything.
    # * **St George's Channel**, St Davids Head to Carnsore Point, ~79 km.
    #   This is the long one and it is the one the request named; there is no
    #   shorter Wales-to-Ireland line.
    # * **Strait of Dover**, Kent to Cap Gris-Nez, ~34 km.
    #
    # 18 km radius (3.3 tiles at 5.42 km/tile) is chosen on the continuity
    # margin, not on width. Modelled in *script* space - discs quantised
    # through ``to_land_position`` first, which is what the full-sound
    # Oeresund got wrong - by ``automation/crossing_model.py``:
    #
    # | radius | shallows over water | worst overlap between neighbours |
    # |--------|---------------------|----------------------------------|
    # | 15 km  | 189 t               | 5.5 - 4.2 = **1.3 tiles**        |
    # | 18 km  | 246 t               | 6.6 - 4.2 = 2.4 tiles            |
    # | 21 km  | 274 t               | 7.8 - 5.0 = 2.8 tiles            |
    #
    # All three connect on paper. 15 km leaves 1.3 tiles of overlap where the
    # quantiser is least kind, and the engine grows these lands organically
    # rather than stamping the disc, so a 1.3-tile overlap is where a chain
    # pinches shut. 21 km buys 0.4 tiles more margin for 28 more tiles of
    # shallows. All three chains come out continuous on both windows at 18.
    #
    # Paper only. The engine decides whether a villager can walk it.
    "britain-crossings": [
        Feature("channel", -5.95, 54.85, 18, "North Channel, Larne-Galloway",
                lon2=-4.95, lat2=54.85),
        Feature("channel", -5.25, 51.88, 18,
                "St George's Channel, Pembrokeshire-Wexford",
                lon2=-6.45, lat2=52.22),
        Feature("channel", 1.20, 51.15, 18, "Strait of Dover, Kent-Gris-Nez",
                lon2=1.70, lat2=50.85),
    ],
}


def resolve(specs: list[str] | None, presets: list[str] | None) -> list[Feature]:
    """Presets first, then explicit specs, so a spec can follow up a preset."""
    out: list[Feature] = []
    for name in presets or []:
        key = name.strip().lower()
        if key not in PRESETS:
            raise SystemExit(f"unknown feature preset {name!r}; "
                             f"known: {', '.join(sorted(PRESETS))}")
        out.extend(PRESETS[key])
    out.extend(parse(s) for s in specs or [])
    return out


#: How many tiles off a feature's nearest tile may be before the feature is
#: treated as off-window, and how close to the border counts as clamped.
#:
#: This check earns its keep. A fixed 1.5-degree tolerance was ~11 tiles at
#: 8.3 km/tile, so a Danish-straits preset applied to a window whose west
#: edge cuts through Jutland did not skip - it stamped the Oeresund at
#: land_position 1 57, against the far west border, which is a silent
#: corruption of a kind that looks plausible in a table. Measured on windows
#: that genuinely contain Denmark, the nearest tile is 0.04-0.09 degrees off;
#: rejecting past ~3 tiles keeps all of those and none of the clamps.
OFF_WINDOW_TILES = 3.0

#: A hit this close to the border is assumed to be a clamp, not a find.
EDGE_GUARD_TILES = 1


def _disc(window, f: Feature) -> tuple[int, int, float] | None:
    """Feature -> ``(y, x, radius_tiles)``, or None if it is off this window.

    Tile scale comes off the window itself (``span`` is CRS units, i.e.
    metres) rather than from a separately-passed size, so the two cannot
    disagree.
    """
    lon, lat = window.tile_lonlat()
    # Latitude is weighted so a degree of each counts about the same; this is
    # a nearest-tile lookup, not a distance measurement.
    d2 = (lon - f.lon) ** 2 + ((lat - f.lat) * 2.0) ** 2
    iy, ix = np.unravel_index(int(np.nanargmin(d2)), d2.shape)
    km_per_tile = (window.span / 1000.0) / window.size
    # A tile is this many degrees, give or take - enough to turn the tile
    # budget above into the units d2 is measured in.
    deg_per_tile = km_per_tile / 111.0 * 2.0
    if float(np.sqrt(d2[iy, ix])) > OFF_WINDOW_TILES * deg_per_tile:
        return None
    n = window.size - 1
    if min(iy, ix, n - iy, n - ix) <= EDGE_GUARD_TILES:
        return None
    return int(iy), int(ix), max(1.0, f.radius_km / km_per_tile)


def _channel_discs(window, f: Feature):
    """A channel as overlapping discs along its segment.

    Spaced at half a radius so the chain is continuous - a gap anywhere in a
    strait rejoins the two sides and undoes the whole point of it.
    """
    a = _disc(window, f)
    b = _disc(window, Feature("shallows", f.lon2, f.lat2, f.radius_km))
    if a is None or b is None:
        return None
    (y0, x0, r), (y1, x1, _) = a, b
    length = max(abs(y1 - y0), abs(x1 - x0))
    steps = max(1, int(length / max(0.5, r * 0.5)))
    return [(y0 + (y1 - y0) * i / steps, x0 + (x1 - x0) * i / steps, r)
            for i in range(steps + 1)]


def _stamp(shape: tuple[int, int], y: int, x: int, radius: float) -> np.ndarray:
    ys, xs = np.ogrid[: shape[0], : shape[1]]
    return (ys - y) ** 2 + (xs - x) ** 2 <= radius ** 2


def apply_mask(mask: np.ndarray, window,
               features: list[Feature]) -> tuple[np.ndarray, list[str]]:
    """Force land/water where asked. ``shallows`` is not a mask kind.

    Returns the new mask and one line per feature, applied or skipped -
    "the preset silently did nothing on this window" is the failure worth
    being loud about.
    """
    out = mask.copy()
    notes: list[str] = []
    for f in features:
        # Both shallows kinds are RMS-stage, not mask-stage. Leaving
        # "channel" out of this guard made every channel carve *water* into
        # the mask as well - the exact opposite of the additive behaviour a
        # channel exists to provide.
        if f.kind in ("shallows", "channel"):
            continue
        placed = _disc(window, f)
        if placed is None:
            notes.append(f"SKIPPED {f.kind} {f.note} at {f.lon},{f.lat} "
                         f"- not on this window")
            continue
        y, x, r = placed
        before = int(out.sum())
        out[_stamp(out.shape, y, x, r)] = f.kind == "island"
        notes.append(f"{f.kind} {f.note} at {f.lon},{f.lat} "
                     f"r={r:.1f}t -> land {int(out.sum()) - before:+d} tiles")
    return out, notes


def shallow_discs(window, features: list[Feature]):
    """``shallows`` features as ``rms_land.Disc``s, for the RMS emitter."""
    from .rms_land import Disc  # local import: avoids an import cycle

    discs, notes = [], []
    for f in features:
        if f.kind not in ("shallows", "channel"):
            continue
        if f.kind == "channel":
            chain = _channel_discs(window, f)
            if chain is None:
                notes.append(f"SKIPPED channel {f.note} "
                             f"{f.lon},{f.lat}->{f.lon2},{f.lat2} "
                             f"- not on this window")
                continue
            discs.extend(Disc(int(round(y)), int(round(x)), r)
                         for y, x, r in chain)
            notes.append(f"channel {f.note} {f.lon},{f.lat}->{f.lon2},{f.lat2} "
                         f"-> {len(chain)} shallows discs r={chain[0][2]:.1f}t")
            continue
        placed = _disc(window, f)
        if placed is None:
            notes.append(f"SKIPPED shallows {f.note} at {f.lon},{f.lat} "
                         f"- not on this window")
            continue
        y, x, r = placed
        discs.append(Disc(y, x, r))
        notes.append(f"shallows {f.note} at {f.lon},{f.lat} r={r:.1f}t")
    return discs, notes
