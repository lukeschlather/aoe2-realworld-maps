"""Quantitative analysis of a batch_all_regions.py run: real land fraction,
TC on-land/on-water counts, and a coastline-speckle metric (small enclosed
water pockets), read from the real .aoe2scenario files - not the Python
approximation.

Usage:
    uv run python automation/analyze_batch.py out/batch-<stamp>
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

from rwmaps import scx_read  # noqa: E402


def speckle_fraction(mask: np.ndarray, max_pond_tiles: int = 4) -> float:
    """Fraction of the map's water that sits in small enclosed pockets
    (<= max_pond_tiles) fully surrounded by land - the artifact the
    overlap/FILL_FACTOR tuning was trying to suppress."""
    water = ~mask
    labeled, n = ndimage.label(water, structure=np.ones((3, 3)))
    if n == 0:
        return 0.0
    sizes = ndimage.sum(water, labeled, index=range(1, n + 1))
    small = sizes[sizes <= max_pond_tiles].sum()
    total_water = water.sum()
    return float(small / total_water) if total_water else 0.0


def main():
    root = Path(sys.argv[1])
    files = sorted(root.rglob("*.aoe2scenario"))

    rows = []
    for f in files:
        mask = scx_read.read_land_mask(f)
        tcs = scx_read.read_town_centers(f)
        on_land = sum(1 for _, x, y in tcs if mask[int(y), int(x)])
        speck = speckle_fraction(mask)
        rows.append({
            "region": f.stem,
            "land_pct": 100.0 * mask.mean(),
            "tcs_total": len(tcs),
            "tcs_on_land": on_land,
            "tcs_in_water": len(tcs) - on_land,
            "speckle_pct": 100.0 * speck,
        })

    print(f"{'region':22s} {'land%':>7s} {'TCs':>5s} {'on land':>8s} {'in water':>9s} {'speckle%':>9s}")
    for r in rows:
        print(f"{r['region']:22s} {r['land_pct']:7.1f} {r['tcs_total']:5d} "
              f"{r['tcs_on_land']:8d} {r['tcs_in_water']:9d} {r['speckle_pct']:9.2f}")

    total_tcs = sum(r["tcs_total"] for r in rows)
    total_water_tcs = sum(r["tcs_in_water"] for r in rows)
    avg_speckle = sum(r["speckle_pct"] for r in rows) / len(rows)
    print(f"\n{len(rows)} regions, {total_tcs} TCs total, "
          f"{total_water_tcs} landed in water, avg speckle {avg_speckle:.2f}%")

    csv_path = root / "analysis.csv"
    with open(csv_path, "w", encoding="utf-8") as fh:
        fh.write("region,land_pct,tcs_total,tcs_on_land,tcs_in_water,speckle_pct\n")
        for r in rows:
            fh.write(f"{r['region']},{r['land_pct']:.2f},{r['tcs_total']},"
                      f"{r['tcs_on_land']},{r['tcs_in_water']},{r['speckle_pct']:.3f}\n")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
