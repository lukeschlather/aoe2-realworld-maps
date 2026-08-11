"""How the forest is *shaped*, as opposed to how much of it there is.

`neutral_supply.py` answers "is there anything on this island"; this answers
the questions a share-of-land number cannot:

* **Do stragglers exist?** ``stragglers.inc`` places five tree *objects* per
  player next to the town centre, and they are the wood a player chops in
  the opening minutes. It carries ``max_distance_to_other_zones 2``, one of
  the clauses this project has already been bitten by twice, so "the include
  is present" is not evidence that anything landed.
* **Is a player walled in?** Measured per player as the share of their
  outward perimeter that forest blocks. The corridor count on its own is
  **ambiguous and must not be read alone**: on a wide-open map the ring at
  20 tiles is one unbroken annulus, so it reports a single "exit" for the
  same reason a sealed pocket does - measured, stock Arabia has 11 of 24
  players on one corridor and a blocked share of 8%. Walled-in is the
  *conjunction*: one corridor **and** a mostly-blocked perimeter.
* **Do two players have to walk around the wood to reach each other?** The
  detour factor - open-land walking distance over the same distance with
  forest treated as walkable - separates "dense but permeable" from
  "impenetrable".
* **What does an island's wood look like?** Forest tiles *and* tree objects
  per unowned island, against its buildable area, which is the design rule
  in ``GENERATION.md``: a small island wants scattered stragglers, a big one
  wants a real copse, and a rock wants nothing.

Every number here is a plain measurement. Per ``CLAUDE.md`` nothing in this
file decides whether a map is good.

Usage:
    uv run python automation/forest_structure.py --mod sysa_n10 --summary
    uv run python automation/forest_structure.py --stock benchmarks
    uv run python automation/forest_structure.py --mod islands_n2 --islands
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import ndimage

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rwmaps import scx_read  # noqa: E402
from rwmaps.analysis import land_path_distance  # noqa: E402
from rwmaps.rms_land import MIN_ISLAND_TILES  # noqa: E402

#: Same beach set ``neutral_supply.py`` excludes - shore is walkable but
#: unbuildable, and counting it doubles the apparent size of a small island.
BEACH_IDS = frozenset({2, 26, 51, 52, 53, 54, 55, 56, 57, 58})

#: Radii the "how boxed in am I" ring is measured at. 20 is the edge of the
#: opening base; 32 is roughly where a scout has to commit to a direction.
RING_RADII = (20, 32)

#: A ring segment thinner than this is not a road, it is a gap between two
#: trees that a villager can slip through. Counting those as "a way out"
#: made every map look open.
MIN_EXIT_TILES = 3

#: A player counts as walled in when this much of their perimeter is
#: blocked *and* only one corridor crosses it. Stock's open maps sit at
#: 0-18% blocked and dense Yucatan at 34-46%, so half is comfortably past
#: anything stock does.
WALLED_IN = 0.5

#: A tree object within this walking distance of a town centre is that
#: player's straggler wood. ``stragglers.inc`` aims for 5 within ~5-9 tiles.
STRAGGLER_RADIUS = 12

#: Forest blob sizes worth naming separately. A blob of a handful of tiles
#: is a copse a player builds around; a 500-tile one is terrain.
BLOB_BUCKETS = ((1, 4), (5, 24), (25, 99), (100, 399), (400, 10**9))


def _ring(dist: np.ndarray, radius: int) -> np.ndarray:
    return np.isfinite(dist) & (dist >= radius) & (dist < radius + 2)


def _exits(ring: np.ndarray) -> int:
    """Separate corridors in one distance ring, ignoring hairline gaps."""
    labels, n = ndimage.label(ring, structure=np.ones((3, 3), dtype=bool))
    if not n:
        return 0
    sizes = ndimage.sum_labels(ring, labels, index=range(1, n + 1))
    return int((sizes >= MIN_EXIT_TILES).sum())


def _perimeter(open_d: np.ndarray, land_d: np.ndarray, forest: np.ndarray,
               radius: int) -> dict:
    """How much of the perimeter at ``radius`` is wall, and how many gaps.

    The perimeter is defined on the map *with the wood removed*, so it is
    the shape geography alone gives the player - coastline still clips it,
    which is correct, since water is not something forest tuning can open.
    Against that fixed denominator:

    ``blocked`` is the share of the perimeter a player cannot stand on, and
    ``exits`` counts the separate corridors through it that are actually
    walkable from the town centre - a gap behind a wall of trees is not a
    way out. One exit is the "enclosed with a single way out" case.
    """
    ring = _ring(land_d, radius)
    total = int(ring.sum())
    if not total:
        return {"perimeter": 0, "blocked": None, "exits": 0, "open": 0}
    walkable = ring & ~forest & np.isfinite(open_d)
    return {
        "perimeter": total,
        "open": int(walkable.sum()),
        "blocked": round(1 - walkable.sum() / total, 3),
        "exits": _exits(walkable),
    }


def _blob_stats(forest: np.ndarray) -> dict:
    labels, n = ndimage.label(forest, structure=np.ones((3, 3), dtype=bool))
    if not n:
        return {"n_blobs": 0, "largest": 0, "buckets": {f"{a}-{b}": 0
                                                        for a, b in BLOB_BUCKETS}}
    sizes = ndimage.sum_labels(forest, labels, index=range(1, n + 1))
    total = float(sizes.sum())
    buckets = {}
    for lo, hi in BLOB_BUCKETS:
        sel = (sizes >= lo) & (sizes <= hi)
        key = f"{lo}-{hi}" if hi < 10**8 else f"{lo}+"
        buckets[key] = {"blobs": int(sel.sum()),
                        "tiles": int(sizes[sel].sum()),
                        "share": round(float(sizes[sel].sum() / total), 3)}
    return {
        "n_blobs": int(n),
        "largest": int(sizes.max()),
        "largest_share": round(float(sizes.max() / total), 3),
        "median": int(np.median(sizes)),
        "buckets": buckets,
    }


def forest_profile(path: Path) -> dict:
    cap = scx_read.read_capture(path)
    forest = cap.forest_mask
    dry = cap.dry_land_mask
    open_walk = cap.walkable_mask            # land + shallows, forest removed
    no_wood = open_walk | forest             # the same map with the wood gone

    # The engine emits one tree *unit* per forest terrain tile, so the raw
    # object count is dominated by the forest and says nothing about
    # stragglers. Measured on Britain: 4200 tree objects against 4160 forest
    # tiles, and the 40 left over are exactly 5 per player - what
    # stragglers.inc asks for. A tree standing off forest terrain is a
    # straggler; one standing on it is the forest.
    trees = [(x, y) for _, x, y in cap.trees]
    stragglers = [(x, y) for x, y in trees if not forest[int(y), int(x)]]
    tcs = cap.town_centers

    prof: dict = {
        "capture": str(path),
        "dry_land": int(dry.sum()),
        "forest_tiles": int(forest.sum()),
        "forest_share": round(float(forest.sum() / dry.sum()), 3) if dry.sum() else 0.0,
        "tree_objects": len(trees),
        "straggler_objects": len(stragglers),
        "blobs": _blob_stats(forest),
        "players": {},
        "pairs": [],
    }

    if not tcs:
        return prof

    open_d, land_d = {}, {}
    for player, tx, ty in tcs:
        seed = (int(ty), int(tx))
        open_d[player] = land_path_distance(open_walk, seed)
        land_d[player] = land_path_distance(no_wood, seed)

    for player, tx, ty in tcs:
        od, ld = open_d[player], land_d[player]
        row: dict = {}
        for r in RING_RADII:
            ring = _perimeter(od, ld, forest, r)
            row[f"exits_{r}"] = ring["exits"]
            row[f"perimeter_{r}"] = ring["perimeter"]
            row[f"blocked_{r}"] = ring["blocked"]
        # Stragglers: loose tree objects the player can walk to in the opening.
        row["stragglers"] = sum(
            1 for x, y in stragglers
            if _adjacent_reachable(od, int(y), int(x), STRAGGLER_RADIUS))
        row["open_land_20"] = int((np.isfinite(od) & (od <= 20)).sum())
        prof["players"][player] = row

    ids = [p for p, _, _ in tcs]
    for i, p in enumerate(ids):
        for q in ids[i + 1:]:
            _, qx, qy = next(t for t in tcs if t[0] == q)
            d_open = float(open_d[p][int(qy), int(qx)])
            d_land = float(land_d[p][int(qy), int(qx)])
            prof["pairs"].append({
                "a": p, "b": q,
                "open": None if not np.isfinite(d_open) else round(d_open, 1),
                "land": None if not np.isfinite(d_land) else round(d_land, 1),
                "detour": (round(d_open / d_land, 2)
                           if np.isfinite(d_open) and np.isfinite(d_land) and d_land
                           else None),
            })

    prof["islands"] = _island_rows(cap, forest, dry, stragglers, tcs)
    return prof


def _adjacent_reachable(dist: np.ndarray, y: int, x: int, radius: float) -> bool:
    """A tree stands *on* an unwalkable tile once it exists, so its own tile
    has no distance. What matters is a villager standing next to it, which is
    the same 3x3 minimum ``fairness.py`` uses for wood ownership."""
    h, w = dist.shape
    sub = dist[max(0, y - 1):min(h, y + 2), max(0, x - 1):min(w, x + 2)]
    return bool(np.isfinite(sub).any() and sub.min() <= radius)


def _island_rows(cap, forest, dry, trees, tcs) -> list[dict]:
    labels, n = ndimage.label(dry)
    if not n:
        return []
    owned = {int(labels[int(ty), int(tx)]) for _, tx, ty in tcs}
    sizes = ndimage.sum_labels(dry, labels, index=range(1, n + 1))
    buildable_land = dry & ~forest & ~np.isin(cap.terrain, list(BEACH_IDS))
    buildable = ndimage.binary_erosion(buildable_land,
                                       structure=np.ones((2, 2), dtype=bool))
    tree_labels = [int(labels[int(y), int(x)]) for x, y in trees]
    rows = []
    for lbl in range(1, n + 1):
        if lbl in owned or sizes[lbl - 1] < MIN_ISLAND_TILES:
            continue
        sel = labels == lbl
        rows.append({
            "tiles": int(sizes[lbl - 1]),
            "buildable": int((buildable_land & sel).sum()),
            "camp_spots": int((buildable & sel).sum()),
            "forest_tiles": int((forest & sel).sum()),
            "trees": sum(1 for t in tree_labels if t == lbl),
        })
    return sorted(rows, key=lambda r: -r["tiles"])


# ---------------------------------------------------------------- printing


def _mean(rows, f):
    vals = [f(r) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else float("nan")


def print_capture(prof: dict) -> None:
    b = prof["blobs"]
    print(f"  {Path(prof['capture']).name}")
    print(f"    forest {prof['forest_tiles']:>5} tiles "
          f"({prof['forest_share']:.0%} of land), "
          f"{b['n_blobs']} blobs, largest {b['largest']} "
          f"({b.get('largest_share', 0):.0%} of the wood), "
          f"{prof['straggler_objects']} loose trees")
    for player, row in sorted(prof["players"].items()):
        print(f"    p{player}: stragglers {row['stragglers']:>2}  "
              f"exits@20 {row['exits_20']}  blocked@20 "
              f"{_pct(row['blocked_20'])}  "
              f"exits@32 {row['exits_32']}  blocked@32 {_pct(row['blocked_32'])}")
    det = [p["detour"] for p in prof["pairs"] if p["detour"] is not None]
    if det:
        print(f"    pair detour (open walk / walk if wood were gone): "
              f"mean {sum(det)/len(det):.2f}, worst {max(det):.2f}, "
              f"{len(det)}/{len(prof['pairs'])} pairs land-connected")


def _pct(v) -> str:
    return "  -  " if v is None else f"{v:>4.0%}"


def print_summary(groups: dict[str, list[dict]]) -> None:
    print(f"\n{'map':<16} {'wood%':>6} {'blobs':>6} {'big%':>6} {'loose':>6} "
          f"{'strag':>6} {'0-str':>6} | {'blk20':>6} {'p90':>5} {'max':>5} "
          f"{'blk32':>6} {'walled':>7} {'detour':>7}")
    print("-" * 106)
    for name, rows in sorted(groups.items()):
        players = [p for r in rows for p in r["players"].values()]
        if not players:
            continue
        det = [p["detour"] for r in rows for p in r["pairs"]
               if p["detour"] is not None]
        big = _mean(rows, lambda r: r["blobs"].get("largest_share"))
        blocked = sorted(p["blocked_20"] for p in players
                         if p["blocked_20"] is not None) or [float("nan")]
        walled = sum(1 for p in players
                     if p["exits_20"] <= 1 and (p["blocked_20"] or 0) >= WALLED_IN)
        print(f"{name:<16} "
              f"{_mean(rows, lambda r: r['forest_share']):>5.0%} "
              f"{_mean(rows, lambda r: r['blobs']['n_blobs']):>6.0f} "
              f"{big:>5.0%} "
              f"{_mean(rows, lambda r: r['straggler_objects']):>6.0f} "
              f"{_mean(players, lambda p: p['stragglers']):>6.1f} "
              f"{sum(1 for p in players if not p['stragglers']):>6} | "
              f"{_mean(players, lambda p: p['blocked_20']):>5.0%} "
              f"{blocked[int(0.9 * (len(blocked) - 1))]:>4.0%} "
              f"{blocked[-1]:>4.0%} "
              f"{_mean(players, lambda p: p['blocked_32']):>5.0%} "
              f"{walled:>4}/{len(players):<3}"
              f"{(sum(det)/len(det) if det else float('nan')):>7.2f}")
    print("\nwood%  forest tiles as a share of dry land")
    print("big%   share of all forest sitting in the single largest blob")
    print("loose  tree objects NOT standing on forest terrain, map-wide - "
          "the engine emits one tree unit per forest tile, so only these "
          "are stragglers")
    print("strag  of those, how many are within 12 walking tiles of a town "
          "centre, per player; 0-str counts players with none")
    print("blkN   share of the perimeter at N tiles a player cannot stand "
          "on, measured with the wood removed so water is already out of "
          "the denominator; mean over players, then p90 and worst at 20")
    print(f"walled players whose perimeter is >={WALLED_IN:.0%} blocked AND "
          "has a single corridor through it. Corridor count alone means "
          "nothing - a wide-open ring is also one component")
    print("detour open-land walk between two players over the same walk "
          "with the wood removed")


def print_islands(groups: dict[str, list[dict]]) -> None:
    print(f"\n{'map':<16} {'tiles':>6} {'buildable':>10} {'camp':>5} "
          f"{'forest':>7} {'trees':>6}  what is on it")
    print("-" * 78)
    for name, rows in sorted(groups.items()):
        for prof in rows:
            for isl in prof.get("islands", []):
                wood = isl["forest_tiles"] + isl["trees"]
                note = "bare of wood" if not wood else ""
                print(f"{name:<16} {isl['tiles']:>6} {isl['buildable']:>10} "
                      f"{isl['camp_spots']:>5} {isl['forest_tiles']:>7} "
                      f"{isl['trees']:>6}  {note}")


def collect(root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = defaultdict(list)
    if not root.exists():
        return out
    for raw in sorted(root.glob("*/raw/*.aoe2scenario")):
        out[raw.parent.parent.name].append(raw)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mod", help="run id under out/mod_capture/")
    ap.add_argument("--stock", help="run id under out/stock_capture/")
    ap.add_argument("--capture", action="append", default=[],
                    help="an individual .aoe2scenario (repeatable)")
    ap.add_argument("--map", help="only maps whose name contains this")
    ap.add_argument("--limit", type=int, default=0,
                    help="at most this many captures per map")
    ap.add_argument("--summary", action="store_true",
                    help="one row per map instead of per capture")
    ap.add_argument("--islands", action="store_true",
                    help="per-island wood table")
    ap.add_argument("--json", help="write the full profiles here")
    args = ap.parse_args()

    groups: dict[str, list[Path]] = defaultdict(list)
    if args.mod:
        for name, paths in collect(REPO / "out" / "mod_capture" / args.mod).items():
            groups[name] += paths
    if args.stock:
        for name, paths in collect(REPO / "out" / "stock_capture" / args.stock).items():
            groups[name] += paths
    for c in args.capture:
        groups[Path(c).stem].append(Path(c))
    if args.map:
        groups = {k: v for k, v in groups.items() if args.map.lower() in k.lower()}
    if not groups:
        print("no captures found - check --mod/--stock run id", file=sys.stderr)
        return 1

    out: dict[str, list[dict]] = {}
    for name in sorted(groups):
        paths = groups[name][:args.limit] if args.limit else groups[name]
        if not (args.summary or args.islands):
            print(f"\n=== {name} ===")
        out[name] = []
        for path in paths:
            prof = forest_profile(path)
            out[name].append(prof)
            if not (args.summary or args.islands):
                print_capture(prof)
    if args.summary:
        print_summary(out)
    if args.islands:
        print_islands(out)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
