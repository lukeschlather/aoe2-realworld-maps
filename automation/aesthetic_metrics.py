"""Exploratory: try to find quantifiable metrics that track the user's own
aesthetic "is this coastline recognizable" judgment, using a small labeled
set of cell IDs (see reports/tuning_matrix_report_res_default_sweep.html)
as the only ground truth available so far.

For each labeled cell this recomputes two reference masks in Python
(never touching the engine) and compares them to the REAL engine-grown
mask pulled straight out of the archived .aoe2scenario:

  - iou_own_target: real engine mask vs the exact target this condition's
    own resolution/consolidation settings intended (execution fidelity -
    did the disc-cover + in-engine growth reproduce what THIS condition
    was even trying to build).
  - iou_10m_truth: real engine mask vs the finest-detail (10m, no
    consolidation/island-drop) reference for that window - a resolution-
    independent measure of how close the final result is to the actual
    real-world coastline, which is the thing a human would be judging
    recognizability against.
  - boundary_ratio: coastline boundary-cell count of the real engine mask
    divided by the same for the 10m truth mask - a cheap complexity/detail
    proxy (a heavily smoothed/blobby result has a much lower ratio than a
    faithfully jagged one).

N is tiny (1 bad label, a handful of good ones) - nowhere near enough to
fit or validate a threshold. Treat this as "do any of these separate the
labeled examples at all", not a calibrated model.

Usage:
    uv run python automation/aesthetic_metrics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402

from rwmaps import raster, scx_read, terrain  # noqa: E402
from rwmaps.analysis import components  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402
from rwmaps.rms_land import iou  # noqa: E402
from tuning_matrix import WINDOWS  # noqa: E402

DATA_DIR = REPO / "reports" / "tuning_matrix_data_res_default_sweep"

#: label -> (window_key, condition_key, cell_id) - resolved by grepping the
#: archived filenames for each ID the user quoted.
LABELED = {
    "good_1": ("salish_sea_wide", "consolidate_light_overlap1.0_r50m", "4d8bbf2f"),
    "good_2": ("salish_sea_wide", "consolidate_overlap0.85_r50m", "a3f19de6"),
    "good_3": ("victoria_recenter", "consolidate_default_overlap1.0_r50m", "f81b9399"),
    "good_4": ("victoria_recenter_tighter", "consolidate_default_overlap1.0_r50m", "879ad483"),
    "bad_1": ("salish_sea_wide", "baseline_r110m", "c438f623"),
    "favorite": ("victoria_recenter_tighter", "max_radius_18_r50m", "86b94b33"),
}

WINDOW_BY_KEY = {w[0]: w for w in WINDOWS}
SIZE = 240
PLAYERS = 8
PROJ = "laea"
BIOME = "temperate"


def boundary_mask(mask: np.ndarray) -> np.ndarray:
    """Per-cell indicator: land cells 4-adjacent to a water cell - a cheap
    coastline "length"/complexity proxy without a real perimeter/contour lib."""
    b = np.zeros_like(mask, dtype=bool)
    b[:-1, :] |= mask[:-1, :] & ~mask[1:, :]
    b[1:, :] |= mask[1:, :] & ~mask[:-1, :]
    b[:, :-1] |= mask[:, :-1] & ~mask[:, 1:]
    b[:, 1:] |= mask[:, 1:] & ~mask[:, :-1]
    return b & mask


def boundary_cells(mask: np.ndarray) -> int:
    return int(boundary_mask(mask).sum())


def island_topology(true_mask: np.ndarray, gen_mask: np.ndarray,
                     min_island_tiles: int = 8) -> list[dict]:
    """Classify every significant island in the TRUE reference as deleted
    (became water in the generated map - fine per the user's stated
    preference), preserved (still its own landmass, any shape/size), or
    merged (absorbed into the generated map's mainland - a "peninsula",
    the one the user specifically dislikes)."""
    true_labels, true_sizes = components(true_mask)
    true_sizes = true_sizes.copy()
    true_sizes[0] = 0
    mainland_label = int(np.argmax(true_sizes))

    gen_labels, gen_sizes = components(gen_mask)
    gen_sizes = gen_sizes.copy()
    gen_sizes[0] = 0
    gen_mainland_label = int(np.argmax(gen_sizes)) if gen_sizes.max() > 0 else None

    results = []
    for lbl in range(1, len(true_sizes)):
        if lbl == mainland_label or true_sizes[lbl] < min_island_tiles:
            continue
        island = true_labels == lbl
        gen_vals = gen_labels[island]
        land_frac = float((gen_vals != 0).mean())
        if land_frac < 0.5:
            status = "deleted"
        else:
            land_vals = gen_vals[gen_vals != 0]
            vals, counts = np.unique(land_vals, return_counts=True)
            dominant = int(vals[np.argmax(counts)])
            status = "merged" if dominant == gen_mainland_label else "preserved"
        results.append({"true_size": int(true_sizes[lbl]), "land_frac": land_frac, "status": status})
    return results


def pockmark_score(true_mask: np.ndarray, gen_mask: np.ndarray, block: int = 20,
                    smooth_thresh: float = 0.05) -> float:
    """Average, over TRUE-smooth blocks only, of how much rougher the
    generated coastline is there than the true one - "added fractal noise
    where the real coast has none" rather than a global roughness score
    (which would also penalize legitimately complex real areas)."""
    true_b = boundary_mask(true_mask)
    gen_b = boundary_mask(gen_mask)
    h, w = true_mask.shape
    excess, n_smooth = 0.0, 0
    for y in range(0, h, block):
        for x in range(0, w, block):
            t_density = true_b[y:y + block, x:x + block].mean()
            if t_density > smooth_thresh:
                continue
            n_smooth += 1
            g_density = gen_b[y:y + block, x:x + block].mean()
            excess += max(0.0, g_density - t_density)
    return excess / n_smooth if n_smooth else float("nan")


def true_mask_geo(lon: float, lat: float, span: float, rot: float, resolution: str,
                   min_island_tiles: int, min_water_width: int, min_land_width: int,
                   size: int = SIZE) -> np.ndarray:
    """Same rasterization true_mask() does, but keyed directly off window
    geometry rather than a lookup into tuning_matrix.WINDOWS - lets callers
    outside that frozen 5-window research set (e.g. the mod's 10 named
    regions in update_mod.py, which use --region/--center, not that dict)
    reuse this without needing an entry there."""
    window = MapWindow.from_center(PROJ, lon, lat, span, size, rot)
    result = raster.rasterize(window, terrain.BIOMES[BIOME], resolution=resolution,
                               min_island_tiles=min_island_tiles)
    mask = result.land_mask
    if min_water_width or min_land_width:
        mask = raster.simplify_features(mask, min_water_width=min_water_width,
                                         min_land_width=min_land_width)
    return mask


def true_mask(win_key: str, resolution: str, min_island_tiles: int,
              min_water_width: int, min_land_width: int) -> np.ndarray:
    _, _, lon, lat, span, rot = WINDOW_BY_KEY[win_key]
    return true_mask_geo(lon, lat, span, rot, resolution, min_island_tiles,
                          min_water_width, min_land_width)


_TRUE_MASK_CACHE: dict[tuple, np.ndarray] = {}


def cached_true_mask(win_key: str, resolution: str, min_island_tiles: int,
                      min_water_width: int, min_land_width: int) -> np.ndarray:
    """Natural Earth rasterization is the expensive part of every metric
    here - memoize by the params that actually change it, since a report
    pass recomputes the same handful of (window, resolution, consolidation)
    combinations across dozens of conditions/samples."""
    key = (win_key, resolution, min_island_tiles, min_water_width, min_land_width)
    if key not in _TRUE_MASK_CACHE:
        _TRUE_MASK_CACHE[key] = true_mask(win_key, resolution, min_island_tiles,
                                           min_water_width, min_land_width)
    return _TRUE_MASK_CACHE[key]


_TRUE_MASK_GEO_CACHE: dict[tuple, np.ndarray] = {}


def cached_true_mask_geo(lon: float, lat: float, span: float, rot: float,
                          resolution: str = "10m", min_island_tiles: int = 0,
                          min_water_width: int = 0, min_land_width: int = 0,
                          size: int = SIZE) -> np.ndarray:
    key = (lon, lat, span, rot, resolution, min_island_tiles, min_water_width,
           min_land_width, size)
    if key not in _TRUE_MASK_GEO_CACHE:
        _TRUE_MASK_GEO_CACHE[key] = true_mask_geo(
            lon, lat, span, rot, resolution, min_island_tiles,
            min_water_width, min_land_width, size)
    return _TRUE_MASK_GEO_CACHE[key]


def compute_metrics_from_truth(truth_10m: np.ndarray, real_mask: np.ndarray) -> dict:
    """All aesthetic-recognizability metrics for one real engine capture,
    given an already-resolved 10m truth mask.

    Deliberately always compares against the finest (10m, no consolidation/
    island-drop) reference regardless of what resolution this condition
    itself used - iou_own_target (real mask vs. a condition's OWN, possibly
    already-degraded target) was tried and rejected: it scored a widely
    disliked 110m sample *highest* of anything labeled, because reproducing
    a low-fidelity target faithfully isn't the same as looking like the
    real place. See automation/aesthetic_metrics.py's module docstring.
    """
    i_10m = iou(real_mask, truth_10m)
    b_real = boundary_cells(real_mask)
    b_10m = boundary_cells(truth_10m)
    b_ratio = b_real / b_10m if b_10m else float("nan")
    pock = pockmark_score(truth_10m, real_mask)
    islands = island_topology(truth_10m, real_mask)
    n_del = sum(1 for i in islands if i["status"] == "deleted")
    n_pres = sum(1 for i in islands if i["status"] == "preserved")
    n_merge = sum(1 for i in islands if i["status"] == "merged")
    preserved_fraction = n_pres / (n_pres + n_merge) if (n_pres + n_merge) else float("nan")
    return {
        "iou_10m": i_10m,
        "bnd_ratio": b_ratio,
        "pockmark": pock,
        "islands_total": len(islands),
        "islands_deleted": n_del,
        "islands_preserved": n_pres,
        "islands_merged": n_merge,
        "preserved_fraction": preserved_fraction,
    }


def compute_metrics(win_key: str, real_mask: np.ndarray) -> dict:
    """All aesthetic-recognizability metrics for one real engine capture,
    resolving truth from tuning_matrix's window-key registry."""
    return compute_metrics_from_truth(cached_true_mask(win_key, "10m", 0, 0, 0), real_mask)


def scenario_paths(win_key: str, cond_key: str, cid: str) -> list[Path]:
    cell_dir = DATA_DIR / win_key / cond_key
    return sorted(cell_dir.glob(f"{win_key}__{cond_key}__s*__{cid}.aoe2scenario"))


def main():
    print(f"{'label':10s} {'window/condition':60s} {'iou_10m':>7s} {'bnd_r':>6s} "
          f"{'pockmk':>7s} {'isl_n':>5s} {'del':>4s} {'pres':>4s} {'MERGE':>5s}")
    rows = []
    for label, (win_key, cond_key, cid) in LABELED.items():
        paths = scenario_paths(win_key, cond_key, cid)
        if not paths:
            print(f"{label:10s} {win_key+'/'+cond_key:60s}  NO ARCHIVED SAMPLES FOUND")
            continue

        for p in paths:
            real_mask = scx_read.read_land_mask(p)
            m = compute_metrics(win_key, real_mask)
            tag = f"{win_key}/{cond_key} [{p.name.split('__')[2]}]"
            print(f"{label:10s} {tag:60s} {m['iou_10m']:7.3f} {m['bnd_ratio']:6.3f} "
                  f"{m['pockmark']:7.4f} {m['islands_total']:5d} {m['islands_deleted']:4d} "
                  f"{m['islands_preserved']:4d} {m['islands_merged']:5d}")
            rows.append((label, m['iou_10m'], m['bnd_ratio'], m['pockmark'], m['islands_total'],
                         m['islands_deleted'], m['islands_preserved'], m['islands_merged']))

    print("\nper-cell average (both samples):")
    from collections import defaultdict
    by_label = defaultdict(list)
    for row in rows:
        by_label[row[0]].append(row[1:])
    for label, vals in by_label.items():
        n = len(vals)
        avg = [sum(v[i] for v in vals) / n for i in range(len(vals[0]))]
        print(f"  {label:10s} n={n} iou_10m={avg[0]:.3f} bnd_ratio={avg[1]:.3f} "
              f"pockmark={avg[2]:.4f} islands={avg[3]:.1f} deleted={avg[4]:.1f} "
              f"preserved={avg[5]:.1f} MERGED={avg[6]:.1f}")


if __name__ == "__main__":
    main()
