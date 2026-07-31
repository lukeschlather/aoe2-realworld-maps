"""Stage 3 of the new-window workflow: an end-user-facing detail page for one
map, built entirely from real engine data (a representative captured
``.aoe2scenario`` plus a ``seed_sweep.py`` results file).

Unlike ``build_italy_report.py`` (a narrative of a specific tuning fix), this
is meant to be regenerated for any new window: what the map looks like, its
resource distribution, and its town centre placement - no "here's the bug we
fixed" framing, just the current map's characteristics.

Usage:
    uv run python automation/build_map_report.py puget_sound_core \\
        --title "Puget Sound" \\
        --hero out/seattle/loop-.../puget_sound_core_220.aoe2scenario \\
        --sweep out/seedsweep-puget_sound_core/results.jsonl \\
        --lobby-size "Large (8 player) [220]" --players 8 \\
        --land-pct-predicted 88.2 --iou 0.93 --ai COASTAL
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]


def img_data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def sweep_stats(sweep_path: Path):
    records = [json.loads(line) for line in sweep_path.open(encoding="utf-8")]
    samples = {k: [] for k in KINDS}
    zero_counts = {k: 0 for k in KINDS}
    total = 0
    clean = both_low = 0
    for rec in records:
        seed_clean = True
        for counts in rec["per_player"].values():
            total += 1
            for k in KINDS:
                n = counts.get(k, 0)
                samples[k].append(n)
                if n == 0:
                    zero_counts[k] += 1
                    seed_clean = False
        clean += seed_clean
    n = len(records)
    rows = []
    for k in KINDS:
        vals = sorted(samples[k])
        m = len(vals)
        rows.append(dict(
            kind=k, mean=sum(vals) / m, min=vals[0], p10=vals[int(0.10 * m)],
            median=vals[m // 2], max=vals[-1], pct_zero=100 * zero_counts[k] / total,
        ))
    return dict(n=n, total=total, rows=rows, clean=clean)


HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title} - rwmaps</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#111; color:#eee;
         margin:0; padding:24px; max-width:1200px; }}
  h1 {{ font-size:1.6rem; margin-bottom:4px; }}
  .subtitle {{ color:#999; margin-top:0; }}
  h2 {{ font-size:1.2rem; margin-top:2.5rem; border-bottom:1px solid #444; padding-bottom:4px; }}
  .stats {{ display:flex; gap:28px; flex-wrap:wrap; margin:14px 0 24px; }}
  .stat {{ background:#1b1b1b; border:1px solid #333; border-radius:8px; padding:10px 16px; min-width:110px; }}
  .stat .v {{ font-size:1.4rem; font-weight:600; color:#ffd27f; }}
  .stat .k {{ font-size:0.78rem; color:#999; text-transform:uppercase; letter-spacing:0.03em; }}
  .row {{ display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; margin-bottom:1rem; }}
  .row img {{ max-width:640px; width:100%; border-radius:6px; border:1px solid #333; }}
  table {{ border-collapse:collapse; font-size:0.88rem; margin-bottom:1rem; }}
  th, td {{ border:1px solid #333; padding:5px 10px; text-align:center; }}
  th {{ background:#222; }}
  td.zero {{ background:#5a2d2d; color:#ffb3b3; font-weight:600; }}
  td.low {{ background:#5a4a2d; color:#ffdca0; }}
  .diag {{ background:#1b1b1b; border:1px solid #333; border-radius:6px; padding:14px 18px; margin:10px 0; }}
  code {{ background:#222; padding:1px 5px; border-radius:3px; }}
</style></head><body>
"""


def player_table(counts_by_player, players, low_threshold=1):
    rows = ["<table><tr><th>P</th>" + "".join(f"<th>{k}</th>" for k in KINDS) + "</tr>"]
    for p in players:
        cells = []
        for k in KINDS:
            n = counts_by_player.get(p, counts_by_player.get(str(p), {})).get(k, 0)
            cls = "zero" if n == 0 else ("low" if n <= low_threshold else "")
            cls_attr = f' class="{cls}"' if cls else ""
            cells.append(f"<td{cls_attr}>{n}</td>")
        rows.append(f"<tr><td>{p}</td>{''.join(cells)}</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--title", required=True)
    ap.add_argument("--hero", required=True, type=Path,
                     help="a representative .aoe2scenario for the hero + resource images")
    ap.add_argument("--sweep", type=Path, help="seed_sweep.py results.jsonl")
    ap.add_argument("--lobby-size", required=True)
    ap.add_argument("--players", type=int, required=True)
    ap.add_argument("--land-pct-predicted", type=float,
                     help="Python land-mask fraction from the fast screen (scout_window.py)")
    ap.add_argument("--iou", type=float, help="coastline IoU from the fast screen")
    ap.add_argument("--ai", default="", help="ai_info_map_type")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    from rwmaps import real_preview, scx_read
    from rwmaps.analysis import resource_ownership

    mask = scx_read.read_land_mask(args.hero)
    tcs = scx_read.read_town_centers(args.hero)
    resources = scx_read.read_resources(args.hero)
    per_player, unclaimed = resource_ownership(mask, tcs, resources)
    players = sorted(p for p, _, _ in tcs)

    tmp = REPO / "out" / "_report_tmp"
    hero_png = real_preview.save_real_render(
        mask, tcs, tmp / f"{args.name}_hero.png",
        title=f"{args.title} - real engine render, {len(tcs)} players",
    )
    res_png = real_preview.save_resource_map(
        mask, tcs, resources, per_player, unclaimed, tmp / f"{args.name}_resources.png",
        title=f"{args.title} - resource ownership",
    )

    parts = [HEAD.format(title=args.title)]
    parts.append(f"<h1>{args.title}</h1>")
    parts.append(f"<p class='subtitle'>Real-world coastline, generated for AoE2 DE. "
                  f"Lobby Map Size must be set to <code>{args.lobby_size}</code>.</p>")

    stats = [
        ("players", args.players),
        ("real land%", f"{100 * mask.mean():.0f}%"),
    ]
    if args.land_pct_predicted is not None:
        stats.append(("predicted land%", f"{args.land_pct_predicted:.0f}%"))
    if args.iou is not None:
        stats.append(("coastline IoU", f"{args.iou:.2f}"))
    if args.ai:
        stats.append(("ai map type", args.ai))
    stats.append(("TCs placed", f"{len(tcs)}/{args.players}"))
    parts.append("<div class='stats'>" + "".join(
        f"<div class='stat'><div class='v'>{v}</div><div class='k'>{k}</div></div>"
        for k, v in stats
    ) + "</div>")

    parts.append("<h2>The map</h2>")
    parts.append("<p>Real, engine-rendered coastline (not a Python approximation) with each "
                  "player's actual Town Centre tile, as the random-map generator placed it.</p>")
    parts.append(f"<div class='row'><img src='{img_data_uri(hero_png)}'></div>")

    parts.append("<h2>Resource distribution</h2>")
    parts.append("<p>Each dot is a land-economy resource (gold/stone/forage/sheep/deer/boar), "
                  "coloured by whichever player's Town Centre can actually walk to it first "
                  "(closer than any other TC, within a 30-tile cap). Grey = unreachable by "
                  "anyone within that cap.</p>")
    parts.append(f"<div class='row'><img src='{img_data_uri(res_png)}'></div>")
    unclaimed_str = ", ".join(f"{k}={n}" for k, n in sorted(unclaimed.items())) or "none"
    parts.append("<div>" + player_table(per_player, players) +
                  f"<p style='color:#888;font-size:0.85rem'>unclaimed/unreachable: "
                  f"{unclaimed_str}</p></div>")

    if args.sweep and args.sweep.exists():
        stats2 = sweep_stats(args.sweep)
        n = stats2["n"]
        parts.append("<h2>What to expect across games</h2>")
        parts.append(f"<p>The map layout (coastline, land budget) never changes between games - "
                      f"only the engine's own random seed does. This shows the spread across "
                      f"<b>{n} independent real generations</b> of this same window, "
                      f"{args.players} players each ({stats2['total']} player-samples total).</p>")
        parts.append("<table><tr><th>resource</th><th>mean</th><th>min</th><th>p10</th>"
                      "<th>median</th><th>max</th><th>% zero</th></tr>")
        for r in stats2["rows"]:
            cls = ' class="zero"' if r["pct_zero"] > 0 else ""
            parts.append(f"<tr><td>{r['kind']}</td><td{cls}>{r['mean']:.1f}</td>"
                          f"<td>{r['min']}</td><td>{r['p10']:.0f}</td><td>{r['median']}</td>"
                          f"<td>{r['max']}</td><td{cls}>{r['pct_zero']:.1f}%</td></tr>")
        parts.append("</table>")
        parts.append(f"<div class='diag'><b>{stats2['clean']}/{n} generations "
                      f"({100 * stats2['clean'] / n:.0f}%)</b> had every player reach every "
                      f"resource kind at least once.</div>")

    parts.append("</body></html>")
    out = args.out or (REPO / "reports" / f"{args.name}_report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
