"""Build a single self-contained HTML report on the Italy map fix and the
100-seed reachability sweep, with images embedded as base64 so the report
lives as one committed file under reports/ (out/ is gitignored - generated
data, not source).

Usage:
    uv run python automation/build_italy_report.py
"""

import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
BASE = REPO / "out" / "baselines"
SWEEP = REPO / "out" / "seedsweep-italy_240_v2" / "results.jsonl"
KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]


def img_data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def sweep_stats():
    records = [json.loads(line) for line in SWEEP.open(encoding="utf-8")]
    samples = {k: [] for k in KINDS}
    zero_counts = {k: 0 for k in KINDS}
    total = 0
    clean = boar_ok = sheep_ok = both_ok = 0
    for rec in records:
        seed_clean = seed_boar = seed_sheep = True
        for counts in rec["per_player"].values():
            total += 1
            for k in KINDS:
                n = counts.get(k, 0)
                samples[k].append(n)
                if n == 0:
                    zero_counts[k] += 1
                    seed_clean = False
            if counts.get("boar", 0) < 2:
                seed_boar = False
            if counts.get("sheep", 0) < 4:
                seed_sheep = False
        clean += seed_clean
        boar_ok += seed_boar
        sheep_ok += seed_sheep
        both_ok += seed_boar and seed_sheep
    n = len(records)
    rows = []
    for k in KINDS:
        vals = sorted(samples[k])
        m = len(vals)
        rows.append(dict(
            kind=k, mean=sum(vals) / m, min=vals[0], p10=vals[int(0.10 * m)],
            median=vals[m // 2], max=vals[-1], pct_zero=100 * zero_counts[k] / total,
        ))
    return dict(n=n, rows=rows, clean=clean, boar_ok=boar_ok, sheep_ok=sheep_ok, both_ok=both_ok)


HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>Italy 240 - fix and reachability sweep</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; background:#111; color:#eee;
         margin:0; padding:24px; max-width:1200px; }
  h1 { font-size:1.5rem; } h2 { font-size:1.2rem; margin-top:2.5rem; border-bottom:1px solid #444; padding-bottom:4px; }
  h3 { font-size:1rem; color:#ffd27f; }
  .row { display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; margin-bottom:1rem; }
  .row img { max-width:560px; width:100%; border-radius:6px; border:1px solid #333; }
  table { border-collapse:collapse; font-size:0.88rem; margin-bottom:1rem; }
  th, td { border:1px solid #333; padding:5px 10px; text-align:center; }
  th { background:#222; }
  td.zero { background:#5a2d2d; color:#ffb3b3; font-weight:600; }
  td.low { background:#5a4a2d; color:#ffdca0; }
  .verdict { display:inline-block; padding:2px 10px; border-radius:4px; font-weight:600; font-size:0.85rem; }
  .verdict.good { background:#2d5a2f; color:#b3ffb8; }
  .diag { background:#1b1b1b; border:1px solid #333; border-radius:6px; padding:14px 18px; margin:10px 0; }
  code { background:#222; padding:1px 5px; border-radius:3px; }
</style></head><body>
"""


def table_html(counts_by_player, players):
    rows = ["<table><tr><th>P</th>" + "".join(f"<th>{k}</th>" for k in KINDS) + "</tr>"]
    for p in players:
        cells = []
        for k in KINDS:
            n = counts_by_player.get(str(p), counts_by_player.get(p, {})).get(k, 0)
            cls = "zero" if n == 0 else ("low" if n <= 1 else "")
            cls_attr = f' class="{cls}"' if cls else ""
            cells.append(f"<td{cls_attr}>{n}</td>")
        rows.append(f"<tr><td>{p}</td>{''.join(cells)}</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def main():
    sys.path.insert(0, str(REPO / "src"))
    from rwmaps import scx_read
    from rwmaps.analysis import resource_ownership

    f_before = BASE / "italy_240_240.aoe2scenario"
    f_after = BASE / "italy_240_v2_240.aoe2scenario"

    mask_a = scx_read.read_land_mask(f_after)
    tcs_a = scx_read.read_town_centers(f_after)
    res_a = scx_read.read_resources(f_after)
    per_player_a, _ = resource_ownership(mask_a, tcs_a, res_a)
    players_a = sorted(p for p, _, _ in tcs_a)

    stats = sweep_stats()

    parts = [HEAD]
    parts.append("<h1>Italy at size 240: TC-separation fix + 100-seed reachability sweep</h1>")
    parts.append("<p>Goal: no player should have a resource that's completely unreachable. "
                  "Contested resources (closer to a neighbor) are fine by design - the failure "
                  "mode is zero, not \"shared.\"</p>")

    parts.append("<h2>The bug: two players sharing one landmass's resource ring</h2>")
    parts.append("<div class='diag'>"
                  "<p>The stock resource include places each player's gold/stone/deer within a "
                  "fixed ring of their own start (e.g. stone 14-26 tiles out, deer 14-30 tiles "
                  "out) - independent of overall map size. Two starts closer together than "
                  "roughly double that ring radius (~55-60 tiles) end up with overlapping "
                  "rings, and every resource in the overlap goes to whichever player is a few "
                  "tiles closer, by placement luck.</p>"
                  "<p>In the original italy_240 generation: P5 and P8 were 38 tiles apart "
                  "(stone ring 26) - every nearby stone deposit fell to P8, leaving P5 with "
                  "<b>zero reachable stone</b>. P6 and P7 were 45 tiles apart (deer ring 30) - "
                  "same mechanism left P6 with <b>zero reachable deer</b>.</p></div>")

    parts.append("<h2>The fix</h2><div class='diag'>"
                  "<p><code>analysis.choose_starts</code> now runs farthest-point start "
                  "selection from several different quality-ranked seed points and keeps "
                  "whichever run maximizes the minimum TC-to-TC separation, then - if that's "
                  "still under a ~56-tile target - progressively admits lower-quality land and "
                  "retries rather than settling for the first solution found. This is a soft "
                  "preference, not a hard requirement: geography that genuinely can't fit "
                  "<code>players</code> starts that far apart still seats all of them, just "
                  "closer together.</p>"
                  "<p>For Italy this raised the worst-case pairwise TC separation from 36 to "
                  "46 tiles. Denmark (already good, 52 tiles) was unaffected - no regression.</p>"
                  "</div>")

    parts.append("<h2>Before / after (real engine, one generation each)</h2>")
    parts.append("<div class='row'>")
    parts.append(f"<div><h3>Before</h3><img src='{img_data_uri(BASE / 'italy_240_240.resources.png')}'></div>")
    parts.append(f"<div><h3>After</h3><img src='{img_data_uri(f_after.with_suffix('.resources.png'))}'></div>")
    parts.append("</div>")
    parts.append("<div>" + table_html(per_player_a, players_a) +
                  "<p>P5's stone: 0&rarr;9. P6's deer: 0&rarr;10. One residual soft spot - "
                  "P6 has boar=1 (everyone else has 2-3) - a pinch, not a lockout.</p></div>")

    parts.append("<h2>100-seed reachability sweep (fixed version, real engine each time)</h2>")
    parts.append("<p>The .rms script (land_position, discs, etc.) never changes across "
                  "seeds - only the engine's own RNG does, so this isolates \"how often does "
                  "placement luck cause a shortfall\" from \"is this region structurally "
                  "short on resources.\"</p>")
    parts.append("<table><tr><th>resource</th><th>mean</th><th>min</th><th>p10</th>"
                  "<th>median</th><th>max</th><th>% zero (of 800 player-seed samples)</th></tr>")
    for r in stats["rows"]:
        cls = ' class="zero"' if r["pct_zero"] > 0 else ""
        parts.append(f"<tr><td>{r['kind']}</td><td{cls}>{r['mean']:.1f}</td>"
                      f"<td>{r['min']}</td><td>{r['p10']}</td><td>{r['median']}</td>"
                      f"<td>{r['max']}</td><td{cls}>{r['pct_zero']:.1f}%</td></tr>")
    parts.append("</table>")

    n = stats["n"]
    parts.append("<div class='diag'><ul>"
                  f"<li><b>{stats['clean']}/{n} seeds ({100*stats['clean']/n:.0f}%)</b> have "
                  "zero shortfalls of any kind for any player.</li>"
                  "<li>Gold, stone, forage, sheep, and boar <b>never hit zero</b> across all "
                  "800 player-seed samples.</li>"
                  "<li>Sheep never dropped below 4 (100% of seeds meet that floor).</li>"
                  "<li>Deer is the only resource that ever reaches zero (2.2% of samples, "
                  "~17 of 100 seeds have exactly one player with no deer) - occasional bad "
                  "luck, never widespread.</li>"
                  f"<li>Boar meets the &ge;2 floor in <b>{stats['boar_ok']}/{n} seeds "
                  f"({100*stats['boar_ok']/n:.0f}%)</b>; the rest have one player at boar=1 "
                  "(never 0).</li>"
                  "</ul><p><b>Verdict:</b> by the \"unplayable = nothing reachable\" bar, this "
                  "region is playable essentially every time.</p></div>")

    # Timestamp leads the filename so every report under reports/ sorts
    # chronologically regardless of what descriptive text a future run picks.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = REPO / "reports" / f"{stamp}_italy_240_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
