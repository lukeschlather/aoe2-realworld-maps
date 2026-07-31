"""Does a named water feature actually stay a single, dockable body of
water across real generations, or does the disc-based land cover fragment
it into disconnected slivers?

Unlike coastline IoU (how well the Python disc-union matches the land
mask), this checks the thing that actually matters for "does this feel
like Puget Sound": is Hood Canal still Hood Canal, connected end to end
and big enough for ships/fish, or did the RNG pinch it into three ponds?

Uses ``analysis.components`` on the *water* mask and the project's
existing ``DOCKABLE_WATER_TILES`` threshold (400 tiles - the same bar
used to decide ai_info_map_type) as "big enough to be a real body of
water," and tags each named point with which connected component it
landed in, so features that used to share one strait but got cut apart
by the RNG are visible as a difference in component id, not just a
subjective look.

Usage:
    uv run python automation/water_navigability.py \\
        --center=-122.6,47.8 --span-km 130 --size 220 \\
        out/cf_test/loop-*/seattle_cf*_220.aoe2scenario
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from pyproj import Transformer  # noqa: E402

from rwmaps import scx_read  # noqa: E402
from rwmaps.analysis import DOCKABLE_WATER_TILES, components  # noqa: E402
from rwmaps.projection import WGS84, MapWindow  # noqa: E402

#: label -> (lon, lat). Edit per region being probed.
DEFAULT_POINTS: dict[str, tuple[float, float]] = {
    "Elliott Bay": (-122.35, 47.60),
    "Strait / Admiralty Inlet": (-122.68, 48.13),
    "Hood Canal (mid)": (-122.95, 47.60),
    "Hood Canal (south)": (-123.05, 47.42),
    "Sinclair Inlet (Kitsap)": (-122.63, 47.55),
    "Dyes Inlet (Kitsap)": (-122.68, 47.66),
    "Rich Passage": (-122.53, 47.58),
}

#: label -> ordered chain of (lon, lat) waypoints along a feature's real
#: centerline. A single point-in-component check can't catch a thin land
#: sliver disconnecting two ends of the same strait (observed once near Port
#: Angeles) - this instead requires every waypoint along the path to land in
#: the SAME connected water component, which a mid-path land bridge breaks.
DEFAULT_PATHS: dict[str, list[tuple[float, float]]] = {
    "Strait of Juan de Fuca (full length)": [
        (-124.35, 48.35),  # Pacific mouth, near Cape Flattery
        (-123.85, 48.25),
        (-123.40, 48.20),  # off Port Angeles
        (-123.00, 48.15),
        (-122.68, 48.13),  # Admiralty Inlet entrance
    ],
}


def lonlat_to_tile(window: MapWindow, lon: float, lat: float) -> tuple[int, int]:
    fwd = Transformer.from_crs(WGS84, window.crs, always_xy=True)
    px, py = fwd.transform(lon, lat)
    gx, gy = px - window.center_x, py - window.center_y
    step = window.span / window.size
    col = int(round((gx + window.span / 2) / step - 0.5))
    row = int(round((-gy + window.span / 2) / step - 0.5))
    return row, col


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+", help="glob(s) of .aoe2scenario files")
    ap.add_argument("--center", required=True)
    ap.add_argument("--span-km", type=float, required=True)
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--rotate", type=float, default=0.0)
    args = ap.parse_args()

    lon, lat = (float(v) for v in args.center.split(","))
    window = MapWindow.from_center("laea", lon, lat, args.span_km, args.size, args.rotate)
    points = {label: lonlat_to_tile(window, plon, plat)
              for label, (plon, plat) in DEFAULT_POINTS.items()}

    files = sorted({p for pat in args.scenarios for p in glob.glob(pat)})
    if not files:
        raise SystemExit("no scenario files matched")

    # hit[label] = list of (component_size, is_dockable) per generation,
    # co_component[label] = list of frozensets of labels sharing its component
    hits: dict[str, list[tuple[int, bool]]] = {label: [] for label in points}
    shared: dict[str, list[frozenset]] = {label: [] for label in points}

    for f in files:
        mask = scx_read.read_land_mask(f)
        water = ~mask
        labels_grid, sizes = components(water)
        comp_of = {}
        for label, (row, col) in points.items():
            if not (0 <= row < mask.shape[0] and 0 <= col < mask.shape[1]):
                comp_of[label] = -1
                continue
            comp_of[label] = int(labels_grid[row, col])
        for label, comp_id in comp_of.items():
            if comp_id <= 0:
                hits[label].append((0, False))
                shared[label].append(frozenset())
                continue
            size = int(sizes[comp_id])
            hits[label].append((size, size >= DOCKABLE_WATER_TILES))
            co = frozenset(other for other, c in comp_of.items()
                            if c == comp_id and other != label)
            shared[label].append(co)

    n = len(files)
    print(f"{n} real generation(s) analyzed, dockable threshold = "
          f"{DOCKABLE_WATER_TILES} tiles\n")
    head = f"{'feature':<28}{'dockable %':>11}{'mean tiles':>12}{'min tiles':>11}   consistently joined to"
    print(head)
    print("-" * len(head))
    for label in points:
        sizes_hits = hits[label]
        n_dockable = sum(1 for _, ok in sizes_hits if ok)
        tiles = [s for s, _ in sizes_hits]
        mean_t = sum(tiles) / len(tiles)
        min_t = min(tiles)
        # "consistently joined to" = features that shared this one's component
        # in every single generation (a stable connection), not just sometimes.
        always_with = set.intersection(*[set(s) for s in shared[label]]) if shared[label] else set()
        joined = ", ".join(sorted(always_with)) or "(nothing consistently)"
        print(f"{label:<28}{100*n_dockable/n:>10.0f}%{mean_t:>12.0f}{min_t:>11}   {joined}")

    # Path checks: every waypoint along a named feature's centerline must land
    # in the SAME connected water component - a single mid-path land bridge
    # (e.g. a stray sliver cutting the Strait of Juan de Fuca near Port
    # Angeles) breaks this even though every individual point still reads as
    # "water," which a point-only check can't see.
    path_points = {
        path_label: [lonlat_to_tile(window, plon, plat) for plon, plat in waypoints]
        for path_label, waypoints in DEFAULT_PATHS.items()
    }
    if path_points:
        print(f"\n{'path (end-to-end connectivity)':<40}{'intact %':>10}   notes")
        print("-" * 70)
        for path_label, tiles_list in path_points.items():
            intact_count = 0
            checked = 0
            off_grid_any = False
            for f in files:
                mask = scx_read.read_land_mask(f)
                water = ~mask
                labels_grid, _ = components(water)
                comp_ids = []
                for row, col in tiles_list:
                    if not (0 <= row < mask.shape[0] and 0 <= col < mask.shape[1]):
                        off_grid_any = True
                        continue
                    comp_ids.append(int(labels_grid[row, col]))
                if not comp_ids:
                    continue
                checked += 1
                if all(c > 0 for c in comp_ids) and len(set(comp_ids)) == 1:
                    intact_count += 1
            if checked == 0:
                print(f"{path_label:<40}{'n/a':>10}   all waypoints off-grid for this window")
            else:
                note = "some waypoints off-grid" if off_grid_any else ""
                print(f"{path_label:<40}{100*intact_count/checked:>9.0f}%   {note}")


if __name__ == "__main__":
    main()
