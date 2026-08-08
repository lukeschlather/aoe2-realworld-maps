"""Put our maps' starts and the stock maps' starts in the same table.

The point is to stop guessing what "fair" and "normal" look like. Stock
Arabia is what the game's own balance team ships and what the AI is tuned
against, so its per-player resource budget and its spread across players are
a measured reference - not an opinion.

Reads archived ``.aoe2scenario`` captures (``out/stock_capture/<run>/`` and
``out/mod_capture/<run>/``), profiles each with :mod:`rwmaps.fairness`, and
aggregates per map. Profiles are cached per file, since parsing a scenario
is much slower than everything else here and captures never change once
archived.

Per ``CLAUDE.md`` this prints facts, not verdicts. The only thing called a
problem is a player with literally zero of a land resource kind. Sample
counts are small on purpose (breadth over parameters), so treat every
number as "what these N samples did", not a fairness claim.

Usage:
    uv run python automation/compare_starts.py --stock benchmarks --mod full_pass_v2
    uv run python automation/compare_starts.py --stock benchmarks --json out/starts.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps.fairness import LAND_KINDS, profile_capture  # noqa: E402

CACHE = REPO / "out" / "start_profiles_cache.json"


def _load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def _profile(path: Path, cache: dict) -> dict:
    key = f"{path}:{path.stat().st_mtime_ns}"
    if key not in cache:
        cache[key] = profile_capture(path)
    return cache[key]


def collect(root: Path, cache: dict) -> dict[str, list[dict]]:
    """Every archived capture under ``root``, grouped by map name."""
    out: dict[str, list[dict]] = {}
    if not root.exists():
        return out
    for raw in sorted(root.glob("*/raw/*.aoe2scenario")):
        name = raw.parent.parent.name
        out.setdefault(name, []).append(_profile(raw, cache))
    return out


def _agg(profiles: list[dict]) -> dict:
    """Fold N samples x 8 players into per-map numbers.

    Counts and distances are pooled across every player of every sample -
    the question is "what does a start on this map look like", and each
    player is one start.
    """
    rows: dict[str, dict] = {}
    for kind in LAND_KINDS:
        counts, dists = [], []
        for prof in profiles:
            for p in prof["per_player"].values():
                counts.append(p["counts"].get(kind, 0))
                d = p["nearest"].get(kind)
                if d is not None:
                    dists.append(d)
        rows[kind] = {
            "count_mean": round(statistics.fmean(counts), 1) if counts else None,
            "count_min": min(counts) if counts else None,
            "count_max": max(counts) if counts else None,
            "dist_mean": round(statistics.fmean(dists), 1) if dists else None,
            "dist_max": max(dists) if dists else None,
            "n_starts_without_any": sum(1 for c in counts if c == 0),
            "n_starts": len(counts),
        }

    wood_10, wood_20, strag, shore = [], [], [], []
    for prof in profiles:
        for p in prof["per_player"].values():
            wood_10.append(p["wood"]["forest_tiles_within_10"])
            wood_20.append(p["wood"]["forest_tiles_within_20"])
            strag.append(p["wood"]["stragglers_within_6"])
            shore.append(p["water"]["shore_fish_within_20"])

    zero_samples = sum(1 for prof in profiles if prof["zero_kinds_by_player"])
    return {
        "n_samples": len(profiles),
        "kinds": rows,
        "wood": {
            "forest_within_10_mean": round(statistics.fmean(wood_10), 1) if wood_10 else None,
            "forest_within_20_mean": round(statistics.fmean(wood_20), 1) if wood_20 else None,
            "stragglers_within_6_mean": round(statistics.fmean(strag), 1) if strag else None,
        },
        "shore_fish_within_20_mean": round(statistics.fmean(shore), 1) if shore else None,
        "samples_with_a_zero": zero_samples,
    }


def _print_table(groups: dict[str, dict]) -> None:
    names = list(groups)
    print()
    print("Per-start resource budget - mean count per player, and mean walking")
    print("distance from the town centre to the nearest one (tiles).")
    print("'zero' counts STARTS with none of that kind, out of all starts sampled.")
    print()
    head = f"{'map':<16}{'N':>3}  " + "".join(f"{k:>21}" for k in LAND_KINDS)
    print(head)
    print("-" * len(head))
    for name in names:
        agg = groups[name]
        cells = ""
        for kind in LAND_KINDS:
            r = agg["kinds"][kind]
            zero = r["n_starts_without_any"]
            mark = f" !{zero}" if zero else "   "
            cells += f"{str(r['count_mean']):>7}@{str(r['dist_mean']):>7}{mark:>6}"
        print(f"{name:<16}{agg['n_samples']:>3}  {cells}")

    print()
    head2 = (f"{'map':<16}{'forest<=10':>12}{'forest<=20':>12}"
             f"{'stragglers<=6':>15}{'shorefish<=20':>15}{'samples w/ a zero':>19}")
    print(head2)
    print("-" * len(head2))
    for name in names:
        agg = groups[name]
        w = agg["wood"]
        print(f"{name:<16}{str(w['forest_within_10_mean']):>12}"
              f"{str(w['forest_within_20_mean']):>12}"
              f"{str(w['stragglers_within_6_mean']):>15}"
              f"{str(agg['shore_fish_within_20_mean']):>15}"
              f"{str(agg['samples_with_a_zero']) + '/' + str(agg['n_samples']):>19}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", default=None, help="run-id under out/stock_capture/")
    ap.add_argument("--mod", action="append", default=[],
                    help="run-id under out/mod_capture/ (repeatable)")
    ap.add_argument("--json", default=None, help="also write the aggregate as JSON")
    args = ap.parse_args()

    cache = _load_cache()
    groups: dict[str, dict] = {}
    try:
        if args.stock:
            for name, profs in collect(REPO / "out" / "stock_capture" / args.stock, cache).items():
                groups[f"[stock] {name}"] = _agg(profs)
        for run in args.mod:
            for name, profs in collect(REPO / "out" / "mod_capture" / run, cache).items():
                groups[f"{name}"] = _agg(profs)
    finally:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache), encoding="utf-8")

    if not groups:
        raise SystemExit("no captures found - check the run ids")
    _print_table(groups)
    if args.json:
        Path(args.json).write_text(json.dumps(groups, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
