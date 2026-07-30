"""Report on the maps that look playable or fixable at size 240, with a
diagnosis of specific per-player resource shortfalls where they exist.

Japan (rotated) is deliberately excluded here - the earlier resource
analysis showed 5/8 players with zero stone and thin gold across the board,
which is a resource-scarcity problem inherent to the landmass at this
scale/viewport, not a placement problem this pipeline can fix.

Usage:
    uv run python automation/fixable_report.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import scx_read  # noqa: E402
from rwmaps.analysis import land_path_distance, resource_ownership  # noqa: E402

KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]
BASE = REPO / "out" / "baselines"

HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>rwmaps fixable-maps report</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; background:#111; color:#eee; margin:0; padding:24px; max-width:1400px; }
  h1 { font-size:1.4rem; } h2 { font-size:1.2rem; margin-top:2.5rem; border-bottom:1px solid #444; padding-bottom:4px; }
  h3 { font-size:1rem; color:#ffb3b3; }
  .row { display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; margin-bottom:1rem; }
  .row img { max-width:620px; border-radius:6px; border:1px solid #333; }
  table { border-collapse:collapse; font-size:0.85rem; margin-bottom:1rem; }
  th, td { border:1px solid #333; padding:4px 8px; text-align:center; }
  th { background:#222; }
  td.zero { background:#5a2d2d; color:#ffb3b3; font-weight:600; }
  td.low { background:#5a4a2d; color:#ffdca0; }
  .verdict { display:inline-block; padding:2px 10px; border-radius:4px; font-weight:600; }
  .verdict.good { background:#2d5a2f; color:#b3ffb8; }
  .verdict.fixable { background:#5a4a2d; color:#ffdca0; }
  .diag { background:#1b1b1b; border:1px solid #333; border-radius:6px; padding:12px 16px; margin:10px 0; }
  code { background:#222; padding:1px 5px; border-radius:3px; }
</style></head><body>
<h1>Which real-world maps are playable at size 240?</h1>
<p>Compared against a corrected Arabia baseline (gold=15, stone=9, forage=6,
sheep=8, deer=2-6, boar=0 per player - modern Arabia rolls a random regional
wildlife reskin, resolved here via <code>includes/themes.inc</code>) and this
project's own floor: boar &ge;2 and sheep &ge;4 for every player, no zeros
in gold/stone/deer.</p>
"""


def table_html(counts_by_player, players):
    rows = ["<table><tr><th>P</th>" + "".join(f"<th>{k}</th>" for k in KINDS) + "</tr>"]
    for p in players:
        cells = []
        for k in KINDS:
            n = counts_by_player.get(p, {}).get(k, 0)
            cls = "zero" if n == 0 else ("low" if n <= 1 else "")
            cls_attr = f' class="{cls}"' if cls else ""
            cells.append(f"<td{cls_attr}>{n}</td>")
        rows.append(f"<tr><td>{p}</td>{''.join(cells)}</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def diagnose_shortfall(mask, tc_by_player, resources, target_p, kind, top_n=3):
    """For a player with zero of `kind`, show the nearest candidates and who
    actually won them, plus the TC-to-TC distance that explains the overlap."""
    paths = {p: land_path_distance(mask, (int(y), int(x))) for p, (x, y) in tc_by_player.items()}
    candidates = [(x, y) for k, x, y in resources if k == kind]
    rows = []
    for x, y in candidates:
        d_target = paths[target_p][int(y), int(x)]
        best_p, best_d = None, float("inf")
        for p, dist in paths.items():
            dp = dist[int(y), int(x)]
            if dp < best_d:
                best_d, best_p = dp, p
        rows.append((x, y, d_target, best_p, best_d))
    rows.sort(key=lambda r: r[2])
    lines = []
    for x, y, dt, bp, bd in rows[:top_n]:
        import numpy as np
        tc_dist = float(np.hypot(*(tc_by_player[target_p][i] - tc_by_player[bp][i] for i in (0, 1))))
        lines.append(
            f"nearest {kind} at ({x:.0f},{y:.0f}): P{target_p} would walk {dt:.0f} tiles, "
            f"but P{bp} is closer at {bd:.0f} (P{target_p}-P{bp} TCs are {tc_dist:.0f} tiles apart)"
        )
    return lines


def main():
    parts = [HEAD]

    # --- Denmark: keeper, no diagnosis needed ---
    f = BASE / "denmark_v2_240_240.aoe2scenario"
    mask = scx_read.read_land_mask(f)
    tcs = scx_read.read_town_centers(f)
    resources = scx_read.read_resources(f)
    per_player, unclaimed = resource_ownership(mask, tcs, resources)
    players = sorted(p for p, _, _ in tcs)
    rel = (f.with_suffix(".resources.png")).relative_to(REPO / "out")
    parts.append("<h2>denmark_v2_240 <span class='verdict good'>good - ship as-is</span></h2>")
    parts.append("<div class='row'>")
    parts.append(f"<img src='{rel.as_posix()}'>")
    parts.append("<div>" + table_html(per_player, players) +
                  "<p>Every player clears both floors (sheep&ge;4, boar&ge;2) with no "
                  "zeros anywhere. Comparable to or better than Arabia (deer 6-7 vs "
                  "Arabia's 2-6).</p></div>")
    parts.append("</div>")

    # --- Italy: was fixable, now fixed ---
    f_before = BASE / "italy_240_240.aoe2scenario"
    mask_b = scx_read.read_land_mask(f_before)
    tcs_b = scx_read.read_town_centers(f_before)
    resources_b = scx_read.read_resources(f_before)
    tc_by_player_b = {p: (x, y) for p, x, y in tcs_b}

    f = BASE / "italy_240_v2_240.aoe2scenario"
    mask = scx_read.read_land_mask(f)
    tcs = scx_read.read_town_centers(f)
    resources = scx_read.read_resources(f)
    per_player, unclaimed = resource_ownership(mask, tcs, resources)
    players = sorted(p for p, _, _ in tcs)
    rel = (f.with_suffix(".resources.png")).relative_to(REPO / "out")
    parts.append("<h2>italy_240 <span class='verdict good'>fixed - larger min TC separation</span></h2>")
    parts.append("<div class='row'>")
    parts.append(f"<img src='{rel.as_posix()}'>")
    parts.append("<div>" + table_html(per_player, players) +
                  "<p>No more zeros: P5's stone went 0&rarr;9, P6's deer went 0&rarr;10. "
                  "One residual soft spot - P6 has boar=1, one short of the 2-boar floor "
                  "(everyone else has 2-3).</p></div>")
    parts.append("</div>")

    parts.append("<h3>What the fix was</h3><div class='diag'>"
                  "<p><code>choose_starts</code> now runs farthest-point selection from "
                  "several different quality-ranked seeds and keeps whichever run "
                  "maximizes the minimum TC-to-TC separation, and if that's still under "
                  "a ~56-tile target (2x the stock resource include's widest ring), it "
                  "progressively admits lower-quality land and retries rather than "
                  "settling for the first (possibly tight) farthest-point solution. For "
                  "Italy that raised the worst pairwise separation from 36 to 46 tiles.</p>"
                  "</div>")

    parts.append("<h3>Before the fix, for reference</h3><div class='diag'><ul>")
    for line in diagnose_shortfall(mask_b, tc_by_player_b, resources_b, 5, "stone"):
        parts.append(f"<li>{line}</li>")
    for line in diagnose_shortfall(mask_b, tc_by_player_b, resources_b, 6, "deer"):
        parts.append(f"<li>{line}</li>")
    parts.append("</ul><p>P5-P8 were 38 tiles apart (stone ring is up to 26) and P6-P7 "
                  "were 45 (deer ring is up to 30) - both inside overlap range, and every "
                  "contested deposit happened to fall to the neighbor.</p></div>")

    parts.append("<h2 style='color:#888'>japan_rot35_v2_240 - excluded</h2>"
                  "<p style='color:#888'>Not included here: 5/8 players have zero stone "
                  "and gold is thin across the board (2-11/player) even before accounting "
                  "for placement overlap - the arc is too narrow to support 8 full "
                  "economies at this scale/viewport, independent of start placement.</p>")

    out = REPO / "out" / "fixable_report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
