"""Where is the neutral supply, landmass by landmass?

Two questions this answers, both open in ``RESOURCE_REWORK_STATUS.md``:

1. Does ``includes/resources_neutral.inc`` fire at all? Our maps measured
   0 neutral resources of every kind while stock Thames carries more
   neutral forage and deer than it gives all eight players combined. No
   stock map references that include, so it had to be verified against a
   real capture rather than by reading it.
2. Are the islands stocked? The rescored placement will not seat a player
   on a small island - correctly - so without neutral supply the islands
   are empty rather than being a contested prize. "Neutral resources exist"
   and "neutral resources are spread over the map" are different claims,
   and the per-map total cannot tell them apart.

A resource is *neutral* here on exactly the same definition
:mod:`rwmaps.fairness` uses for ``unclaimed``: no town centre is within
``OWNERSHIP_RADIUS`` walking distance of it. Landmasses are 4-connected
components of walkable land, so a forest belt does not split an island in
two and a shallow ford does join two banks - the same walkability model the
reachability analysis uses.

Per ``CLAUDE.md`` this prints facts, not verdicts. Whether an island being
empty is a problem or is simply geography is the reader's call.

Usage:
    uv run python automation/neutral_supply.py --mod place_v1
    uv run python automation/neutral_supply.py --stock benchmarks --map Thames
    uv run python automation/neutral_supply.py --capture out/.../raw/x.aoe2scenario
    uv run python automation/neutral_supply.py --mod place_v1 --json out/neutral.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import scx_read  # noqa: E402
from rwmaps.fairness import (  # noqa: E402
    EXTRA_LAND_KINDS,
    LAND_KINDS,
    OWNERSHIP_RADIUS,
    _distance_stack,
)

KINDS = LAND_KINDS + EXTRA_LAND_KINDS

#: Landmasses below this are sandbars and rocks, not places anyone plays.
#: Reported as an aggregate row rather than one line each.
MIN_ISLAND_TILES = 60

#: ``min_distance_to_players`` values worth knowing the land budget for.
#: Every neutral-placement include is gated on one of these, and an include
#: whose gate admits no tiles places nothing however good its other
#: settings are - the "gated includes that silently do nothing" trap.
#: 26 is ``resources_neutral.inc``; 100 is ``remote_resources.inc``'s
#: self-defined REMOTE_DISTANCE.
PLAYER_DISTANCES = (0, 20, 26, 30, 40, 50, 60, 80, 100)


def landmass_profile(path: Path) -> dict:
    """Split one capture into landmasses and locate its neutral supply."""
    cap = scx_read.read_capture(path)
    walk = cap.walkable_mask
    tcs = cap.town_centers

    # Components of walkable land. Forest is impassable, so a forest belt
    # would cut an island in half if it were the mask - use dry land for
    # the components and walkability only for the distance field.
    land = cap.dry_land_mask
    labels, n_masses = ndimage.label(land)
    areas = ndimage.sum_labels(land, labels, index=range(1, n_masses + 1))

    if tcs:
        players, stack = _distance_stack(walk, tcs)
        # Walking distance to the *nearest* town centre, over all players.
        nearest_tc = stack.min(axis=0)
    else:
        players, nearest_tc = [], np.full(land.shape, np.inf)

    masses: dict[int, dict] = {
        i: {
            "label": i,
            "tiles": int(areas[i - 1]),
            "players": [],
            "resources": dict.fromkeys(KINDS, 0),
            "neutral": dict.fromkeys(KINDS, 0),
        }
        for i in range(1, n_masses + 1)
    }

    for player, tx, ty in tcs:
        lbl = int(labels[int(ty), int(tx)])
        if lbl:
            masses[lbl]["players"].append(player)

    off_land = 0
    for kind, x, y in cap.resources:
        if kind not in KINDS:
            continue
        iy, ix = int(y), int(x)
        lbl = int(labels[iy, ix])
        if not lbl:
            off_land += 1
            continue
        masses[lbl]["resources"][kind] += 1
        if not np.isfinite(nearest_tc[iy, ix]) or nearest_tc[iy, ix] > OWNERSHIP_RADIUS:
            masses[lbl]["neutral"][kind] += 1

    # How much land each include's min_distance_to_players gate even admits.
    # Deliberately permissive - dry land minus forest minus the 6-tile map
    # edge margin, and nothing else. The real placement additionally honours
    # avoid_forest_zone, avoid_cliff_zone, actor-area spacing and group
    # spacing, so this is an upper bound: a gate reading 0 here places
    # nothing, full stop, whatever its other settings say.
    placeable = land & ~cap.forest_mask
    placeable[:6, :] = placeable[-6:, :] = False
    placeable[:, :6] = placeable[:, -6:] = False
    if tcs:
        yy, xx = np.indices(land.shape)
        euclid_tc = np.full(land.shape, np.inf)
        for _, tx, ty in tcs:
            np.minimum(euclid_tc, np.hypot(xx - tx, yy - ty), out=euclid_tc)
    else:
        euclid_tc = np.full(land.shape, np.inf)
    gate_land = {d: int((placeable & (euclid_tc >= d)).sum()) for d in PLAYER_DISTANCES}

    rows = sorted(masses.values(), key=lambda m: -m["tiles"])
    for m in rows:
        m["resource_total"] = sum(m["resources"].values())
        m["neutral_total"] = sum(m["neutral"].values())

    totals = dict.fromkeys(KINDS, 0)
    neutral_totals = dict.fromkeys(KINDS, 0)
    for m in rows:
        for k in KINDS:
            totals[k] += m["resources"][k]
            neutral_totals[k] += m["neutral"][k]

    big = [m for m in rows if m["tiles"] >= MIN_ISLAND_TILES]
    unowned_big = [m for m in big if not m["players"]]
    return {
        "capture": str(path),
        "n_players": len(tcs),
        "n_landmasses": n_masses,
        "n_landmasses_big": len(big),
        "resources": totals,
        "neutral": neutral_totals,
        "neutral_total": sum(neutral_totals.values()),
        "resource_total": sum(totals.values()),
        "off_land_resources": off_land,
        # The item-2 question in one number: unowned landmasses big enough
        # to matter that carry nothing at all.
        "empty_unowned_masses": sum(1 for m in unowned_big if not m["resource_total"]),
        "unowned_masses": len(unowned_big),
        "gate_land": gate_land,
        "masses": rows,
    }


def _fmt_kinds(d: dict) -> str:
    return "  ".join(f"{k}={d[k]}" for k in KINDS)


def print_capture(prof: dict, detail: bool) -> None:
    print(f"\n{Path(prof['capture']).name}")
    print(f"  players {prof['n_players']}   landmasses {prof['n_landmasses']} "
          f"({prof['n_landmasses_big']} >= {MIN_ISLAND_TILES} tiles)")
    print(f"  all resources     {prof['resource_total']:>5}   {_fmt_kinds(prof['resources'])}")
    print(f"  neutral (>{OWNERSHIP_RADIUS:.0f} walk) {prof['neutral_total']:>5}   "
          f"{_fmt_kinds(prof['neutral'])}")
    print(f"  unowned landmasses {prof['unowned_masses']}, of which empty "
          f"{prof['empty_unowned_masses']}")
    g = prof["gate_land"]
    print("  placeable land at min_distance_to_players: "
          + "  ".join(f"{d}={g[d]}" for d in PLAYER_DISTANCES))
    if not detail:
        return
    print(f"  {'tiles':>7} {'players':<12} {'res':>4} {'neut':>5}  breakdown")
    small_tiles = small_res = 0
    for m in prof["masses"]:
        if m["tiles"] < MIN_ISLAND_TILES:
            small_tiles += m["tiles"]
            small_res += m["resource_total"]
            continue
        who = ",".join(str(p) for p in sorted(m["players"])) or "-"
        kinds = " ".join(f"{k}={m['resources'][k]}" for k in KINDS
                         if m["resources"][k])
        print(f"  {m['tiles']:>7} {who:<12} {m['resource_total']:>4} "
              f"{m['neutral_total']:>5}  {kinds}")
    if small_tiles:
        print(f"  {small_tiles:>7} {'(< min, all)':<12} {small_res:>4}")


def collect(root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = defaultdict(list)
    if not root.exists():
        return out
    for raw in sorted(root.glob("*/raw/*.aoe2scenario")):
        out[raw.parent.parent.name].append(raw)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mod", help="run id under out/mod_capture/")
    ap.add_argument("--stock", help="run id under out/stock_capture/")
    ap.add_argument("--capture", action="append", default=[],
                    help="an individual .aoe2scenario (repeatable)")
    ap.add_argument("--map", help="only this map name")
    ap.add_argument("--detail", action="store_true",
                    help="per-landmass table, not just the totals")
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
        groups[Path(c).stem] .append(Path(c))

    if args.map:
        groups = {k: v for k, v in groups.items() if args.map.lower() in k.lower()}
    if not groups:
        print("no captures found - check --mod/--stock run id", file=sys.stderr)
        return 1

    out: dict[str, list[dict]] = {}
    for name in sorted(groups):
        print(f"\n=== {name} ===")
        out[name] = []
        for path in groups[name]:
            prof = landmass_profile(path)
            out[name].append(prof)
            print_capture(prof, args.detail)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
