"""Can what is on the map actually be reached and worked?

Three questions, all asked of real captures, and all of them about access
rather than about totals. `neutral_supply.py` already answers "how much is
there and where"; this answers whether a player can get at it.

1. **Islands worth landing on.** An island can carry resources once it has
   somewhere to stand that is not shore, so the bar here is *non-shore
   tiles*, not total tiles - half of a small island is BEACH, which is
   walkable but unbuildable. For every unowned island over that bar: does it
   carry anything at all, and does a 2x2 mining camp fit? An island with
   resources and no camp spot is worse than an empty one, because the trip
   is wasted rather than declined.

2. **Bases you can walk out of.** `forest_structure.py` measures the ring at
   20 and 32 tiles; the number that matters for playability is the
   conjunction it warns about - few exits *and* a mostly blocked perimeter.
   A base with one corridor through dense wood is reachable in the
   land-connectivity sense and still miserable to play, which is exactly the
   gap between "pairwise_land_reachable_fraction" saying 1.0 and a player
   being walled in.

3. **Water you cannot use.** A pond fully enclosed by forest is not a lake,
   it is a hole: no shore to dock from, and any fish in it are decoration.
   Reported per region with what encloses each one.

Each start is given a lon/lat so a walled player can be recognised as a
place ("that is the one in France") instead of a player number.

Per CLAUDE.md every number here is a measurement. Whether a bare rock or a
tight corridor is a problem or is simply the geography of the region is the
reader's call.

Usage:
    uv run python automation/access_report.py --run-id head_n1
    uv run python automation/access_report.py --run-id head_n1 --map Britain
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rwmaps import scx_read  # noqa: E402
from rwmaps import terrain as T  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402
from forest_structure import WALLED_IN, forest_profile  # noqa: E402

#: Shore. Walkable, unbuildable, and half of every small island - the whole
#: reason this file counts non-shore tiles rather than tiles.
BEACH_IDS = frozenset({2, 26, 51, 52, 53, 54, 55, 56, 57, 58})

#: An island below this many non-shore tiles cannot hold a camp and the
#: workers to use it, so nothing is expected of it. The user's bar.
MIN_WORKABLE_NONSHORE = 8



def _forest_mask(terrain: np.ndarray) -> np.ndarray:
    return np.isin(terrain, list(T.FOREST_IDS))


# The ring measurement lives in forest_structure.forest_profile and is NOT
# reimplemented here. A first version of this file measured its own rings on
# straight-line distance and disagreed with it flatly - on Britain's player 2
# it reported 35% blocked at 32 tiles where forest_structure reported 100%,
# and no walled players anywhere against its one. forest_structure is the
# correct one, for a reason worth writing down: it measures the ring in
# *walking* distance and counts only corridors reachable from the town
# centre, so open ground lying behind a wall of trees is not scored as an
# exit. Straight-line rings count exactly that ground as open, which makes a
# sealed base look fine - the specific error this whole report exists to
# catch.


def _enclosed_ponds(terrain: np.ndarray, forest: np.ndarray,
                    land: np.ndarray) -> list[dict]:
    """Inland water, and whether wood stands behind its shore.

    Two corrections over the obvious version, both from its first output:

    * **Do not read the tile touching the water.** The engine paints BEACH
      along every shoreline, so the immediate bank is beach essentially
      always - the first run reported ``forest_bank 0.0`` for every pond on
      every map, which measured the engine's shore painting and nothing
      else. What matters is what stands *behind* the beach, so the ring is
      taken at 2-4 tiles inland.
    * **The largest body is the sea**, even when it does not touch the map
      edge. Black Sea's "pond" came back at 9162 tiles, which is the Black
      Sea. Only bodies below a fraction of it are inland water.
    """
    water = np.zeros(terrain.shape, dtype=bool)
    for wid in (*T.DEEP_WATER_IDS, *getattr(T, "SHALLOW_WATER_IDS", ())):
        water |= terrain == wid
    lbl, n = ndimage.label(water)
    if not n:
        return []
    sizes = ndimage.sum_labels(water, lbl, index=range(1, n + 1))
    biggest = sizes.max() if len(sizes) else 0
    edge = set(lbl[0, :]) | set(lbl[-1, :]) | set(lbl[:, 0]) | set(lbl[:, -1])
    out = []
    for i in range(1, n + 1):
        if i in edge or sizes[i - 1] >= 0.25 * biggest:
            continue
        body = lbl == i
        size = int(body.sum())
        if size < 4:
            continue
        near = ndimage.binary_dilation(body, iterations=4) & ~body
        inner = ndimage.binary_dilation(body, iterations=1)
        shell = near & ~inner & land
        shell_n = int(shell.sum())
        if not shell_n:
            continue
        ys, xs = np.nonzero(body)
        out.append({
            "tiles": size,
            "shell_tiles": shell_n,
            "forest_behind_shore": round(float((shell & forest).sum() / shell_n), 3),
            "centre": [int(ys.mean()), int(xs.mean())],
        })
    return sorted(out, key=lambda p: -p["tiles"])


def profile(capture: Path, lon: float, lat: float, span_km: float,
            rotate: float, size: int) -> dict:
    terrain = scx_read.read_terrain_grid(capture)
    walk = scx_read.read_walkable_mask(capture)
    land = scx_read.read_land_mask(capture)
    forest = _forest_mask(terrain)
    beach = np.isin(terrain, list(BEACH_IDS))
    tcs = scx_read.read_town_centers(capture)
    res = scx_read.read_resources(capture)

    win = MapWindow.from_center("laea", lon, lat, span_km, size, rotate)
    glon, glat = win.tile_lonlat()

    def where(y: int, x: int) -> list[float] | None:
        y = min(max(y, 0), size - 1)
        x = min(max(x, 0), size - 1)
        if not (np.isfinite(glon[y, x]) and np.isfinite(glat[y, x])):
            return None
        return [round(float(glon[y, x]), 2), round(float(glat[y, x]), 2)]

    # --- islands, by non-shore tiles ------------------------------------
    lbl, n = ndimage.label(land, structure=np.array([[0, 1, 0],
                                                     [1, 1, 1],
                                                     [0, 1, 0]], dtype=bool))
    player_labels = {int(lbl[int(round(y)), int(round(x))])
                     for _, x, y in tcs
                     if 0 <= int(round(y)) < size and 0 <= int(round(x)) < size}
    buildable = ndimage.binary_erosion(land & ~forest & ~beach,
                                       structure=np.ones((2, 2), dtype=bool))
    res_at = np.zeros(terrain.shape, dtype=int)
    for _, x, y in res:
        yy, xx = int(round(y)), int(round(x))
        if 0 <= yy < size and 0 <= xx < size:
            res_at[yy, xx] += 1

    islands = []
    for i in range(1, n + 1):
        if i in player_labels:
            continue
        body = lbl == i
        nonshore = int((body & ~beach).sum())
        if nonshore < MIN_WORKABLE_NONSHORE:
            continue
        ys, xs = np.nonzero(body)
        forest_tiles = int((body & forest).sum())
        n_res = int(res_at[body].sum())
        islands.append({
            "tiles": int(body.sum()),
            "nonshore": nonshore,
            "forest": forest_tiles,
            "camp_spots": int((buildable & body).sum()),
            "resources": n_res,
            # Empty means nothing to gather at all. Wood counts: an island of
            # trees is a lumber trip, not a bare rock, and calling it bare
            # would overstate how many islands are pointless to visit.
            "empty": bool(n_res == 0 and forest_tiles == 0),
            "at": where(int(ys.mean()), int(xs.mean())),
        })
    islands.sort(key=lambda m: -m["nonshore"])

    # --- players, measured by forest_structure, located here -------------
    fp = forest_profile(capture)
    at = {pid: where(int(round(y)), int(round(x))) for pid, x, y in tcs}
    players = []
    for pid, row in sorted(fp["players"].items(), key=lambda kv: int(kv[0])):
        blk20 = row["blocked_20"] or 0.0
        blk32 = row["blocked_32"] or 0.0
        players.append({
            "player": int(pid),
            "at": at.get(int(pid)),
            **{k: row[k] for k in row if k.startswith(("exits_", "blocked_",
                                                       "perimeter_"))},
            "stragglers": row["stragglers"],
            # forest_structure's own definition, plus the harder case it does
            # not name: a ring with no walkable corridor out of it at all.
            "walled": bool(row["exits_20"] <= 1 and blk20 >= WALLED_IN),
            "sealed_32": bool(row["exits_32"] == 0 and row["perimeter_32"] > 0),
            "tight": bool(row["exits_32"] <= 2 and blk32 >= WALLED_IN),
        })

    return {
        "capture": str(capture),
        "islands": islands,
        "players": players,
        "ponds": _enclosed_ponds(terrain, forest, land),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True, help="run id under out/mod_capture/")
    ap.add_argument("--map", help="only maps whose name contains this")
    ap.add_argument("--json", help="write the full profiles here")
    args = ap.parse_args()

    root = REPO / "out" / "mod_capture" / args.run_id
    rows = [json.loads(l) for l in (root / "results.jsonl").open(encoding="utf-8")]
    out = {}
    for rec in rows:
        name = rec["region"]
        if args.map and args.map.lower() not in name.lower():
            continue
        cap = root / name / "raw" / f"sample_{rec['sample_index']:03d}.aoe2scenario"
        if not cap.exists():
            print(f"{name}: no capture at {cap}")
            continue
        out[name] = profile(cap, rec["lon"], rec["lat"], rec["span_km"],
                            rec["rotate"], 240)

    print(f"\nislands with >= {MIN_WORKABLE_NONSHORE} non-shore tiles\n")
    print(f"{'map':<16}{'islands':>8}{'empty':>7}{'no gold/stone/food':>20}"
          f"{'no camp':>9}{'res, no camp':>13}")
    print("-" * 74)
    for name, p in out.items():
        isl = p["islands"]
        bare = [m for m in isl if m["empty"]]
        nores = [m for m in isl if m["resources"] == 0]
        nocamp = [m for m in isl if m["camp_spots"] == 0]
        stranded = [m for m in isl if m["resources"] and not m["camp_spots"]]
        print(f"{name:<16}{len(isl):>8}{len(bare):>7}{len(nores):>20}"
              f"{len(nocamp):>9}{len(stranded):>13}")

    print("\nstarts that are hard to leave (walking-distance rings, only "
          "corridors reachable from the TC count)\n")
    print(f"{'map':<16}{'p':>3}{'exits20':>8}{'blk20':>7}{'exits32':>8}"
          f"{'blk32':>7}  {'flags':<22}lon/lat")
    print("-" * 84)
    any_flag = False
    for name, p in out.items():
        for pl in p["players"]:
            flags = [f for f in ("walled", "sealed_32", "tight") if pl[f]]
            if not flags:
                continue
            any_flag = True
            print(f"{name:<16}{pl['player']:>3}{pl['exits_20']:>8}"
                  f"{pl['blocked_20']:>7}{pl['exits_32']:>8}"
                  f"{pl['blocked_32']:>7}  {','.join(flags):<22}{pl['at']}")
    if not any_flag:
        print("  none")

    print("\ninland water (not the sea), and what stands behind its shore\n")
    print(f"{'map':<16}{'tiles':>7}{'forest behind shore':>21}  tile y,x")
    print("-" * 56)
    for name, p in out.items():
        for pond in p["ponds"][:3]:
            print(f"{name:<16}{pond['tiles']:>7}"
                  f"{pond['forest_behind_shore']:>21}  {pond['centre']}")

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
