"""Aggregate gold/stone/wood totals and per-player spread across every
sample under a run directory laid out as ``<root>/<map_name>/raw/*.aoe2scenario``
(the shape ``capture_stock_maps.py`` and ``mod_capture.py`` both produce) -
one row per map/region, averaged over its samples.

Built to answer a specific question: how do our own regions' resource
totals/spreads compare to stock Arabia/Arena/Capricious on the same
metric (``analysis.resource_ownership``, walking-distance-capped at 30
tiles), so a suspiciously low total or a suspiciously wide per-player
spread on one of our maps can be flagged against a real baseline instead
of an arbitrary threshold.

Usage:
    uv run python automation/resource_compare_report.py out/stock_maps/wood_compare_v1
    uv run python automation/resource_compare_report.py out/stock_maps/wood_compare_v1 out/mod_capture/full_pass_v2
"""

import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import scx_read  # noqa: E402
from rwmaps.analysis import resource_ownership  # noqa: E402

KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar", "wood"]


def analyze_one(f: Path) -> dict:
    mask = scx_read.read_land_mask(f)
    tcs = scx_read.read_town_centers(f)
    resources = scx_read.read_resources(f)
    trees = scx_read.read_trees(f)
    per_player, unclaimed = resource_ownership(mask, tcs, resources + trees)

    totals = {k: sum(row.get(k, 0) for row in per_player.values()) + unclaimed.get(k, 0)
              for k in KINDS}
    per_player_totals = {k: [row.get(k, 0) for row in per_player.values()] for k in KINDS}
    return {
        "n_players": len(tcs),
        "totals": totals,
        "unclaimed": {k: unclaimed.get(k, 0) for k in KINDS},
        "per_player": per_player_totals,
    }


def main():
    roots = [Path(a).resolve() for a in sys.argv[1:]]
    if not roots:
        raise SystemExit(__doc__)

    rows = []
    for root in roots:
        for raw_dir in sorted(root.glob("*/raw")):
            map_name = raw_dir.parent.name
            files = sorted(raw_dir.glob("*.aoe2scenario"))
            if not files:
                continue
            samples = []
            for f in files:
                try:
                    samples.append(analyze_one(f))
                except Exception as e:
                    print(f"  [{map_name}] {f.name}: FAILED ({e})", file=sys.stderr)
            if not samples:
                continue
            rows.append((map_name, samples))
            print(f"[resource_compare] {map_name}: {len(samples)} samples analyzed")

    print()
    header = f"{'map':<18}{'n':>3} | " + " | ".join(f"{k:>16}" for k in KINDS)
    print(header)
    print("-" * len(header))
    for map_name, samples in rows:
        cells = []
        for k in KINDS:
            total_mean = statistics.mean(s["totals"][k] for s in samples)
            pp_mins = [min(s["per_player"][k]) if s["per_player"][k] else 0 for s in samples]
            pp_maxs = [max(s["per_player"][k]) if s["per_player"][k] else 0 for s in samples]
            pp_min_mean = statistics.mean(pp_mins)
            pp_max_mean = statistics.mean(pp_maxs)
            cells.append(f"{total_mean:6.0f} ({pp_min_mean:.0f}-{pp_max_mean:.0f})")
        print(f"{map_name:<18}{samples[0]['n_players']:>3} | " + " | ".join(f"{c:>16}" for c in cells))

    print("\ncolumn = mean map total (mean per-player min - mean per-player max), across samples")


if __name__ == "__main__":
    main()
