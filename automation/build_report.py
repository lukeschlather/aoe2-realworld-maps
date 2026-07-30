"""Score every captured real-engine render (baseline batch + variants batch)
on how well it places Town Centres, and build a single self-contained HTML
report with embedded PNGs for review.

TC placement score (0-100), the thing this project actually asked for:
  - coverage   (40%): fraction of the real landmass within `radius` tiles of
                any actual TC - directly measures "how much land goes unused."
  - separation (40%): the closest pair of TCs, normalized against the
                same 0.15*size floor `analysis.evaluate` already warns on.
  - on_land    (20%): fraction of TCs the engine actually dropped on solid
                ground (not water/edge) - a basic placement-sanity check.

Fidelity (IoU) is reported separately, not blended in - it measures
coastline recognizability, not TC placement, but matters more for rotated
(non-north-up) maps, which are harder to read as the real place, so it's
called out per row rather than folded into one number that would hide the
tradeoff.

Usage:
    uv run python automation/build_report.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
from rwmaps import raster, scx_read, terrain  # noqa: E402
from rwmaps.cli import REGIONS  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402
from rwmaps.rms_land import iou as mask_iou  # noqa: E402

BASELINE_DIR = REPO / "out" / "batch-20260729-184244"

# label -> (lon, lat, span_km, proj, rotate) for every variant run by
# batch_variants.py - mirrors that file's VARIANTS list.
VARIANT_PARAMS = {
    "japan_v2_220": (*REGIONS["japan"], "laea", 0.0),
    "newzealand_v2_220": (*REGIONS["newzealand"], "laea", 0.0),
    "philippines_v2_220": (*REGIONS["philippines"], "laea", 0.0),
    "indonesia_v2_220": (*REGIONS["indonesia"], "laea", 0.0),
    "caribbean_v2_220": (*REGIONS["caribbean"], "laea", 0.0),
    "denmark_v2_220": (*REGIONS["denmark"], "laea", 0.0),
    "britain_wide_v2_220": (*REGIONS["britain-wide"], "laea", 0.0),
    "caribbean_tight_v2_220": (-75.0, 18.0, 1400.0, "laea", 0.0),
    "philippines_tight_v2_220": (122.0, 12.0, 1000.0, "laea", 0.0),
    "indonesia_tight_v2_220": (117.0, -2.0, 2200.0, "laea", 0.0),
    "japan_rot35_v2_220": (*REGIONS["japan"][:2], REGIONS["japan"][2], "laea", 35.0),
}

PROBLEM_REGIONS = {"japan", "newzealand", "philippines", "indonesia",
                    "caribbean", "denmark", "britain-wide"}


def true_mask_for(lon, lat, span, proj, rotate, size=220):
    window = MapWindow.from_center(proj, lon, lat, span, size, rotate)
    result = raster.rasterize(window, terrain.BIOMES["temperate"])
    return result.land_mask


def tc_placement_score(mask: np.ndarray, tcs: list[tuple[int, float, float]], size: int):
    radius = max(16, round(20 * size / 220))
    if not tcs:
        return dict(coverage=0.0, separation=0.0, on_land=0.0, total=0.0, n_tcs=0)

    yy, xx = np.mgrid[0:size, 0:size]
    covered = np.zeros((size, size), dtype=bool)
    on_land = 0
    pts = []
    for _, x, y in tcs:
        xi, yi = int(x), int(y)
        if 0 <= yi < size and 0 <= xi < size and mask[yi, xi]:
            on_land += 1
        pts.append((y, x))
        covered |= ((yy - y) ** 2 + (xx - x) ** 2) <= radius ** 2

    total_land = int(mask.sum())
    coverage = float((mask & covered).sum() / total_land) if total_land else 0.0

    min_sep = float("inf")
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = float(np.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))
            min_sep = min(min_sep, d)
    sep_floor = 0.15 * size
    separation = min(1.0, min_sep / sep_floor) if np.isfinite(min_sep) else 0.0

    on_land_frac = on_land / len(tcs)
    total = 100 * (0.4 * coverage + 0.4 * separation + 0.2 * on_land_frac)
    return dict(coverage=coverage, separation=separation, on_land=on_land_frac,
                total=total, n_tcs=len(tcs))


def collect_entries():
    entries = []

    for f in sorted(BASELINE_DIR.rglob("*.aoe2scenario")):
        stem = f.stem  # e.g. "britain_tight_220"
        region_slug = stem.rsplit("_", 1)[0]
        region_key = region_slug.replace("_", "-")
        if region_key not in REGIONS:
            # try plain underscore->as-is match (no hyphenated regions collide here)
            candidates = [k for k in REGIONS if k.replace("-", "_") == region_slug]
            if not candidates:
                print(f"[build_report] skip unmatched baseline file {f}")
                continue
            region_key = candidates[0]
        lon, lat, span = REGIONS[region_key]
        entries.append(dict(
            label=stem, group="baseline", region=region_key,
            problem=region_key in PROBLEM_REGIONS,
            lon=lon, lat=lat, span=span, proj="laea", rotate=0.0,
            scenario=f, png=f.with_suffix(".png"),
        ))

    variants_dirs = sorted(REPO.glob("out/variants-*"))
    for vdir in variants_dirs:
        for f in sorted(vdir.rglob("*.aoe2scenario")):
            stem = f.stem
            params = VARIANT_PARAMS.get(stem)
            if params is None:
                print(f"[build_report] skip unmatched variant file {f}")
                continue
            lon, lat, span, proj, rotate = params
            entries.append(dict(
                label=stem, group="variant", region=stem,
                problem=True,
                lon=lon, lat=lat, span=span, proj=proj, rotate=rotate,
                scenario=f, png=f.with_suffix(".png"),
            ))

    return entries


def score_entries(entries):
    for e in entries:
        real_mask = scx_read.read_land_mask(e["scenario"])
        tcs = scx_read.read_town_centers(e["scenario"])
        size = real_mask.shape[0]

        true_mask = true_mask_for(e["lon"], e["lat"], e["span"], e["proj"], e["rotate"], size)
        e["fidelity_iou"] = mask_iou(real_mask, true_mask)

        score = tc_placement_score(real_mask, tcs, size)
        e.update(score)
        print(f"[build_report] {e['label']:26s} score={e['total']:5.1f} "
              f"cov={e['coverage']:.2f} sep={e['separation']:.2f} "
              f"onland={e['on_land']:.2f} iou={e['fidelity_iou']:.2f} tcs={e['n_tcs']}")
    return entries


HTML_HEAD = """<!doctype html>
<html><head><meta charset="utf-8">
<title>rwmaps TC placement report</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; background:#111; color:#eee; margin:0; padding:24px; }
  h1 { font-size:1.4rem; }
  h2 { font-size:1.1rem; margin-top:2.5rem; border-bottom:1px solid #444; padding-bottom:4px; }
  .grid { display:flex; flex-wrap:wrap; gap:18px; }
  .card { background:#1b1b1b; border:1px solid #333; border-radius:6px; padding:10px; width:360px; }
  .card img { width:100%; border-radius:4px; display:block; }
  .card .label { font-weight:600; margin:6px 0 2px; }
  .scorebar { height:8px; border-radius:4px; background:#333; overflow:hidden; margin:4px 0; }
  .scorebar > div { height:100%; }
  table.scores { width:100%; font-size:0.82rem; border-collapse:collapse; margin-top:4px; }
  table.scores td { padding:1px 4px; }
  table.scores td.k { color:#999; }
  .badge { display:inline-block; font-size:0.7rem; padding:1px 6px; border-radius:3px; margin-left:6px; }
  .badge.problem { background:#5a2d2d; color:#ffb3b3; }
  .badge.rotated { background:#2d3d5a; color:#b3c8ff; }
  .total { font-size:1.3rem; font-weight:700; }
</style></head><body>
<h1>rwmaps: TC placement quality report</h1>
<p>Score = 40% land coverage within reach of a TC + 40% min TC-to-TC separation
+ 20% TCs actually on solid ground. All from real engine output (captured
.aoe2scenario files), not the Python approximation. Fidelity (IoU) is shown
separately - it measures coastline recognizability, weight it more heavily
for rotated maps since those are harder to read as the real place.</p>
"""


def score_color(v):
    if v >= 70:
        return "#3f9142"
    if v >= 45:
        return "#c9a227"
    return "#a83f3f"


def card_html(e):
    rel_png = e["png"].relative_to(REPO / "out")
    badges = ""
    if e.get("problem"):
        badges += '<span class="badge problem">was clustered</span>'
    if abs(e.get("rotate", 0)) > 0.01:
        badges += f'<span class="badge rotated">rot {e["rotate"]:g}&deg;</span>'
    color = score_color(e["total"])
    return f"""
<div class="card">
  <img src="{rel_png.as_posix()}" loading="lazy">
  <div class="label">{e['label']} {badges}</div>
  <div class="total" style="color:{color}">{e['total']:.0f}</div>
  <div class="scorebar"><div style="width:{e['total']:.0f}%; background:{color}"></div></div>
  <table class="scores">
    <tr><td class="k">coverage</td><td>{100*e['coverage']:.0f}%</td>
        <td class="k">separation</td><td>{100*e['separation']:.0f}%</td></tr>
    <tr><td class="k">on land</td><td>{100*e['on_land']:.0f}% ({e['n_tcs']} TCs)</td>
        <td class="k">fidelity (IoU)</td><td>{e['fidelity_iou']:.2f}</td></tr>
  </table>
</div>"""


def build_html(entries):
    baseline = [e for e in entries if e["group"] == "baseline"]
    variants = [e for e in entries if e["group"] == "variant"]

    problem_regions = sorted({e["region"] for e in baseline if e["problem"]})
    good_regions = sorted({e["region"] for e in baseline if not e["problem"]})

    parts = [HTML_HEAD]

    parts.append("<h2>Problem regions - baseline (old choose_starts, clustered)</h2><div class='grid'>")
    for e in sorted([x for x in baseline if x["problem"]], key=lambda x: x["label"]):
        parts.append(card_html(e))
    parts.append("</div>")

    parts.append("<h2>Same regions - new algorithm / island filtering / viewport variants</h2><div class='grid'>")
    for e in sorted(variants, key=lambda x: x["label"]):
        parts.append(card_html(e))
    parts.append("</div>")

    parts.append("<h2>Regions that were already fine (baseline, regression check)</h2><div class='grid'>")
    for e in sorted([x for x in baseline if not x["problem"]], key=lambda x: x["label"]):
        parts.append(card_html(e))
    parts.append("</div>")

    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    entries = collect_entries()
    print(f"[build_report] {len(entries)} entries found")
    entries = score_entries(entries)
    html = build_html(entries)
    out = REPO / "out" / "report.html"
    out.write_text(html, encoding="utf-8")
    print(f"[build_report] wrote {out}")


if __name__ == "__main__":
    main()
