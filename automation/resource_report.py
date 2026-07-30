"""Score each captured scenario's land-economy resource reachability per TC
and build an HTML report: coastline + TC + resource-ownership map, plus a
per-player table of gold/stone/forage/sheep/deer/boar counts.

A resource only counts for a player if it's reachable over connected land
(not straight-line) from that player's own TC, closer to it than to any
other TC, and within a walking-distance cap (default 30 tiles) - see
`analysis.resource_ownership`.

Usage:
    uv run python automation/resource_report.py out/size240-<stamp>
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import real_preview, scx_read  # noqa: E402
from rwmaps.analysis import resource_ownership  # noqa: E402

KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]

HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>rwmaps resource reachability report</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; background:#111; color:#eee; margin:0; padding:24px; }
  h1 { font-size:1.4rem; } h2 { font-size:1.1rem; margin-top:2.5rem; border-bottom:1px solid #444; padding-bottom:4px; }
  .row { display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; margin-bottom:2rem; }
  .row img { max-width:620px; border-radius:6px; border:1px solid #333; }
  table { border-collapse:collapse; font-size:0.85rem; }
  th, td { border:1px solid #333; padding:4px 8px; text-align:center; }
  th { background:#222; }
  td.zero { background:#5a2d2d; color:#ffb3b3; font-weight:600; }
  td.low { background:#5a4a2d; color:#ffdca0; }
  .unclaimed { color:#888; font-size:0.8rem; margin-top:6px; }
</style></head><body>
<h1>Land-economy resource reachability per TC</h1>
<p>A resource counts for a player only if it's reachable by land-walking
distance from that player's TC (not straight-line), it's closer to that TC
than to any other, and it's within a 30-tile walking cap. Cells shaded red
= zero of that resource; orange = only one.</p>
"""


def table_html(counts_by_player, players):
    rows = ["<table><tr><th>P</th>" + "".join(f"<th>{k}</th>" for k in KINDS) + "</tr>"]
    for p in players:
        cells = []
        for k in KINDS:
            n = counts_by_player.get(p, {}).get(k, 0)
            cls = "zero" if n == 0 else ("low" if n == 1 else "")
            cls_attr = f' class="{cls}"' if cls else ""
            cells.append(f"<td{cls_attr}>{n}</td>")
        rows.append(f"<tr><td>{p}</td>{''.join(cells)}</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def main():
    root = Path(sys.argv[1]).resolve()
    files = sorted(root.rglob("*.aoe2scenario"))
    parts = [HEAD]

    for f in files:
        mask = scx_read.read_land_mask(f)
        tcs = scx_read.read_town_centers(f)
        resources = scx_read.read_resources(f)
        per_player, unclaimed = resource_ownership(mask, tcs, resources)

        png = f.with_suffix(".resources.png")
        real_preview.save_resource_map(
            mask, tcs, resources, per_player, unclaimed, png,
            title=f"{f.stem} - resource ownership",
        )

        players = sorted(p for p, _, _ in tcs)
        rel = png.relative_to(REPO / "out")
        unclaimed_str = ", ".join(f"{k}={n}" for k, n in sorted(unclaimed.items())) or "none"
        parts.append(f"<h2>{f.stem}</h2><div class='row'>")
        parts.append(f"<img src='{rel.as_posix()}'>")
        parts.append("<div>" + table_html(per_player, players) +
                      f"<div class='unclaimed'>unclaimed/unreachable: {unclaimed_str}</div></div>")
        parts.append("</div>")

        print(f"[resource_report] {f.stem}: {len(resources)} resources, "
              f"unclaimed={unclaimed_str}")
        for p in players:
            row = per_player.get(p, {})
            print(f"    P{p}: " + " ".join(f"{k}={row.get(k,0)}" for k in KINDS))

    parts.append("</body></html>")
    out = REPO / "out" / "resource_report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"[resource_report] wrote {out}")


if __name__ == "__main__":
    main()
