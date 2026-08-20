"""Will a chain of shallows actually join the two sides? Answered on paper.

The engine is what decides, but a chain of shallows is cheap to get wrong in
a way that costs a whole capture pass to discover, so this predicts the
answer first.

**It carves the discs where the SCRIPT puts them, not where ``features.py``
computes them.** That distinction is the whole reason this file exists.
``land_position`` is integer *percent*, so at size 240 a disc centre
quantises to 2.39-tile steps. The full-sound Oeresund candidate was chosen
off a model that skipped that step: its 47 discs are 1.4 tiles across, they
collapsed onto 21 distinct positions, and consecutive discs either stacked
or sat 2.4 tiles apart with water in between. Every gap is a land bridge the
engine land growth then fills, so the strait it modelled as open came out
shut in 9 captures of 9 (``bc0bc20``). Quantising first reproduces the
engine answer.

The same arithmetic runs the other way for a crossing meant to *join* two
landmasses: a gap anywhere in the chain is a stretch of open water no land
unit can ford, and the crossing silently does nothing. So the number this
file leads with is the **quantised step against the disc radius** - a chain
is continuous only where consecutive centres land within 2r of each other.

What it reports, per condition:

* every anchor connected piece in the script-space land, with and without
  the shallows - so "Ireland and Britain are one piece now" is a lookup at
  the tile Ireland actually occupies, not "there is a big piece somewhere".
* per channel: discs asked for, distinct positions after quantising, the
  largest step between consecutive centres, and whether that step gaps.

Paper, not evidence. Shallows here are stamped discs; the engine grows
lands organically. Capture it before believing it.

Usage:
    uv run python automation/crossing_model.py --presets britain great-britain-n
    uv run python automation/crossing_model.py --name X -- --center=-3,54.5 \
        --span-km 1300 --north -45 --feature-preset britain-crossings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "automation"))

from rwmaps import cli, features, raster, rms_land, terrain  # noqa: E402
from rwmaps.presets import Registry  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402

#: Places to look up a connected piece at, for the British Isles windows.
#: Chosen inland, so a coastline that wanders by a tile cannot turn an anchor
#: into a water tile and make the answer "no piece" instead of "not joined".
ANCHORS: dict[str, tuple[float, float]] = {
    "Britain": (-1.90, 52.48),      # Birmingham, as far from any coast as GB gets
    "Ireland": (-7.70, 53.20),      # the midlands, well inside Ireland
    "France": (2.35, 48.86),        # Paris
    "Wales": (-3.20, 52.10),
    "Scotland": (-4.25, 55.86),     # Glasgow
    "N-Ireland": (-6.30, 54.55),    # west of Lough Neagh
    "Kintyre": (-5.60, 55.55),
    "Kent": (1.08, 51.28),          # the Dover end, English side
    "Pas-de-Calais": (1.85, 50.95),  # Calais, the French end
}


def script_space(discs, size: int) -> np.ndarray:
    """Rasterise discs at the positions the RMS will actually carry.

    ``rms_land.rasterize_discs`` stamps them at their exact tile, which is
    the picture the cover was computed in, not the one the engine is told
    about. Round-tripping through ``to_land_position`` is the difference.
    """
    yy, xx = np.ogrid[:size, :size]
    out = np.zeros((size, size), dtype=bool)
    for d in discs:
        px, py = rms_land.to_land_position(d.y, d.x, size)
        y = int(round(py / 100.0 * (size - 1)))
        x = int(round(px / 100.0 * (size - 1)))
        out |= ((yy - y) ** 2 + (xx - x) ** 2) <= d.radius ** 2
    return out


#: A land piece smaller than this is an islet, not a side of a strait.
MIN_LANDMASS_TILES = 500


def quantised_chain(window, f: features.Feature, size: int, mask=None):
    """Per-channel continuity, and whether the ends land on real ground.

    Two ways a chain fails, and the second one cost a capture pass before it
    was checked here at all:

    * **It gaps.** ``land_position`` is integer percent, so centres quantise
      to 2.39-tile steps at 240 and consecutive discs can end up further
      apart than they are wide.
    * **Its end disc eats the land it was aiming at.** Shallows are painted
      last, over the coastline, so an end disc centred offshore at the tip of
      a headland overlaps that headland in a thin crescent - and then
      *converts the crescent to shallows*, cutting the tip off the landmass
      instead of joining to it. Measured on run ``britain_crossings_wide``:
      the St George's line, aimed 1 tile off Pembrokeshire, left the Welsh
      coastal tiles as 25-, 5- and 5-tile fragments with Britain proper 4.1
      tiles beyond, and the ford read SHUT. A wider radius makes this worse,
      not better. The same line has its Irish end in water on ``britain``.

    So the end check is not "is there land within a radius" - a disc 1 tile
    offshore passes that and still fails. It reports, per end, the distance
    to the nearest land piece of at least ``MIN_LANDMASS_TILES`` (the only
    kind worth joining) and **how many tiles of the end disc actually fall on
    one**. A thin overlap is the failure signature; an end sitting inland has
    a fat one and nothing to sever.
    """
    chain = features._channel_discs(window, f)
    if chain is None:
        return None
    pos = []
    for y, x, r in chain:
        px, py = rms_land.to_land_position(int(round(y)), int(round(x)), size)
        pos.append((round(py / 100.0 * (size - 1)), round(px / 100.0 * (size - 1))))
    steps = [float(np.hypot(b[0] - a[0], b[1] - a[1]))
             for a, b in zip(pos, pos[1:]) if a != b]
    r = chain[0][2]
    biggest = max(steps) if steps else 0.0
    out = {"discs": len(chain), "distinct": len(set(pos)), "radius_t": r,
           "max_step": biggest, "continuous": biggest <= 2.0 * r,
           "ends": None}
    if mask is not None:
        lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
        big = np.zeros_like(mask)
        for i in range(1, n + 1):
            piece = lab == i
            if int(piece.sum()) >= MIN_LANDMASS_TILES:
                big |= piece
        dist = ndimage.distance_transform_edt(~big)
        yy, xx = np.ogrid[:size, :size]
        ends = []
        for y, x in ((chain[0][0], chain[0][1]), (chain[-1][0], chain[-1][1])):
            iy, ix = int(round(y)), int(round(x))
            disc = ((yy - iy) ** 2 + (xx - ix) ** 2) <= r ** 2
            ends.append({"offshore_t": float(dist[iy, ix]),
                         "on_land": int((disc & big).sum()),
                         "disc_tiles": int(disc.sum())})
        out["ends"] = ends
        # Both conditions, because either alone lets a known failure through:
        # 1 tile offshore passes any radius-sized distance test, and a fat
        # overlap on the wrong side of a 1-tile gap is still a gap.
        out["anchored"] = all(e["offshore_t"] == 0.0
                              and e["on_land"] >= 0.25 * e["disc_tiles"]
                              for e in ends)
    return out


def model(name: str, argv: list[str]) -> dict:
    """Everything this file can say about one condition, from its argv."""
    args = cli.build_parser().parse_args([name, *argv])
    lon = lat = span = None
    if args.region:
        lon, lat, span = cli.REGIONS[args.region]
    if args.center:
        lon, lat = (float(v) for v in args.center.split(","))
    if args.span_km is not None:
        span = args.span_km
    size, _lobby = cli.size_for_players(args.players)
    if args.size:
        size = args.size

    window = MapWindow.from_center(args.proj, lon, lat, span, size, args.north)
    res = raster.rasterize(window, terrain.BIOMES[args.biome],
                           resolution=args.resolution,
                           min_island_tiles=args.min_island_tiles)
    mask = res.land_mask
    if args.min_water_width or args.min_land_width:
        mask = raster.simplify_features(mask, min_water_width=args.min_water_width,
                                        min_land_width=args.min_land_width)
    feats = features.resolve(args.features, args.feature_presets)
    mask_notes: list[str] = []
    shallows: list = []
    chains: list[tuple[str, dict | None]] = []
    if feats:
        mask, mask_notes = features.apply_mask(mask, window, feats)
        shallows, _ = features.shallow_discs(window, feats)
        for f in feats:
            if f.kind == "channel":
                chains.append((f.note or f"{f.lon},{f.lat}",
                               quantised_chain(window, f, size, mask)))

    lands = args.lands or cli.lands_for_size(size)
    discs = rms_land.cover_mask(mask, lands, max_radius=args.max_radius,
                               overlap=args.overlap)
    land = script_space(discs, size)
    shall = script_space(shallows, size) if shallows else np.zeros_like(land)
    joined = land | shall

    lon_g, lat_g = window.tile_lonlat()
    km_per_tile = (window.span / 1000.0) / size
    tolerance = 3.0 * (km_per_tile / 111.0 * 2.0)
    tiles = {}
    for anchor, (alon, alat) in ANCHORS.items():
        d2 = (lon_g - alon) ** 2 + ((lat_g - alat) * 2.0) ** 2
        iy, ix = np.unravel_index(int(np.nanargmin(d2)), d2.shape)
        if float(np.sqrt(d2[iy, ix])) <= tolerance:
            tiles[anchor] = (int(iy), int(ix))

    def pieces(m):
        lab, n = ndimage.label(m)
        return lab, {i: int((lab == i).sum()) for i in range(1, n + 1)}

    lab_a, size_a = pieces(land)
    lab_b, size_b = pieces(joined)
    return {
        "name": name, "argv": argv, "size": size, "km_per_tile": km_per_tile,
        "land_pct": float(mask.mean() * 100),
        "script_iou": rms_land.iou(land, mask),
        "shallows_tiles": int((shall & ~land).sum()),
        "mask_notes": mask_notes, "chains": chains,
        "anchors": {a: {"tile": t,
                        "before": (int(lab_a[t]), size_a.get(int(lab_a[t]), 0)),
                        "after": (int(lab_b[t]), size_b.get(int(lab_b[t]), 0))}
                    for a, t in tiles.items()},
    }


def report(m: dict) -> None:
    print(f"\n===== {m['name']}   {' '.join(m['argv'])}")
    print(f"  {m['size']}x{m['size']} at {m['km_per_tile']:.3f} km/tile, "
          f"mask land {m['land_pct']:.1f}%, script IoU {m['script_iou']:.3f}, "
          f"{m['shallows_tiles']} shallows tiles laid over water")
    for line in m["mask_notes"]:
        print(f"  mask: {line}")
    for note, ch in m["chains"]:
        if ch is None:
            print(f"  channel {note}: SKIPPED - not on this window")
            continue
        verdict = "continuous" if ch["continuous"] else "*** GAP ***"
        print(f"  channel {note}: {ch['discs']} discs -> {ch['distinct']} "
              f"distinct positions, r={ch['radius_t']:.1f}t, max step "
              f"{ch['max_step']:.1f}t vs 2r={2 * ch['radius_t']:.1f}t "
              f"-> {verdict}")
        if ch.get("ends") is not None:
            where = " / ".join(
                f"{e['offshore_t']:.0f}t offshore, {e['on_land']}/"
                f"{e['disc_tiles']} of the end disc on a "
                f">={MIN_LANDMASS_TILES}t landmass" for e in ch["ends"])
            print(f"{'':4s}ends: {where} -> "
                  + ("anchored" if ch["anchored"] else "*** BAD END ***"))
    print(f"  {'anchor':14s} {'tile':>10s}  {'piece, no shallows':>22s}"
          f"  {'with shallows':>22s}")
    for a, d in m["anchors"].items():
        b, af = d["before"], d["after"]
        print(f"  {a:14s} {str(tuple(d['tile'])):>10s}  "
              f"{('piece %d (%d t)' % b) if b[0] else 'water':>22s}  "
              f"{('piece %d (%d t)' % af) if af[0] else 'water':>22s}")
    groups: dict[int, list[str]] = {}
    for a, d in m["anchors"].items():
        if d["after"][0]:
            groups.setdefault(d["after"][0], []).append(a)
    print("  joined after shallows:")
    for pid, names in sorted(groups.items(),
                             key=lambda kv: -m["anchors"][kv[1][0]]["after"][1]):
        print(f"    piece {pid:3d} ({m['anchors'][names[0]]['after'][1]:6d} t): "
              f"{', '.join(sorted(names))}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--presets", nargs="+", help="preset labels to model")
    ap.add_argument("--name", help="condition name, with rwmaps argv after --")
    ap.add_argument("argv", nargs="*", help="rwmaps argv (after --)")
    args = ap.parse_args()
    conditions = []
    if args.presets:
        reg = Registry(REPO).load()
        conditions += [(p.name, list(p.argv)) for p in reg.select(args.presets)]
    if args.name:
        conditions.append((args.name, list(args.argv)))
    if not conditions:
        ap.error("give --presets, or --name with argv after --")
    for name, argv in conditions:
        report(model(name, argv))


if __name__ == "__main__":
    main()
