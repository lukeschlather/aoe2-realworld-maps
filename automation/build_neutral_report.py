"""Baseline report: how much neutral supply do stock maps carry, and where?

Answers the question the resource rework kept assuming the answer to. "Our
maps have no neutral resources" is only actionable next to a target, and
the target was being read off **Thames**, which this report shows is the
outlier of the stock set, not the reference. Arabia - the map the game's
own balance team tunes and the AI is trained against - sits at a small
fraction of Thames's neutral share.

Also measures, for every map, how much placeable land each
``min_distance_to_players`` gate admits, which is what decides whether an
include places anything at all.

Reads the JSON that ``neutral_supply.py --json`` writes.

Usage:
    uv run python automation/neutral_supply.py --stock benchmarks --json out/neutral_stock.json
    uv run python automation/neutral_supply.py --mod sysa_n10 --json out/neutral_ours.json
    uv run python automation/build_neutral_report.py \
        --stock out/neutral_stock.json --ours out/neutral_ours.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps.fairness import EXTRA_LAND_KINDS, LAND_KINDS, OWNERSHIP_RADIUS  # noqa: E402

KINDS = LAND_KINDS + EXTRA_LAND_KINDS
GATES = (0, 20, 26, 30, 40, 50, 60, 80, 100)


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _mean(profs: list[dict], get) -> float:
    return statistics.fmean([get(p) for p in profs]) if profs else 0.0


def summarise(name: str, profs: list[dict]) -> dict:
    total = _mean(profs, lambda p: p["resource_total"])
    neutral = _mean(profs, lambda p: p["neutral_total"])
    return {
        "name": name,
        "n": len(profs),
        "total": total,
        "neutral": neutral,
        "share": neutral / total if total else 0.0,
        "kinds": {k: _mean(profs, lambda p, k=k: p["neutral"][k]) for k in KINDS},
        "masses_big": _mean(profs, lambda p: p["n_landmasses_big"]),
        "unowned": _mean(profs, lambda p: p["unowned_masses"]),
        "empty_unowned": _mean(profs, lambda p: p["empty_unowned_masses"]),
        "gates": {g: _mean(profs, lambda p, g=g: p["gate_land"][str(g)]) for g in GATES},
        "captures": sorted({p["capture"] for p in profs}),
    }


def _rel(path: str) -> str:
    """A repo-relative link, so the report points at the real artifact."""
    try:
        return str(Path(path).resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return path


def rows_html(rows: list[dict], link_captures: bool) -> str:
    out = []
    for r in rows:
        kinds = "".join(f"<td class='n'>{r['kinds'][k]:.0f}</td>" for k in KINDS)
        cap = ""
        if link_captures and r["captures"]:
            cap = (f"<a href='{_rel(r['captures'][0])}'>capture</a>")
        out.append(
            f"<tr><td>{r['name']}</td><td class='n'>{r['n']}</td>"
            f"<td class='n'>{r['total']:.0f}</td>"
            f"<td class='n'>{r['neutral']:.0f}</td>"
            f"<td class='n share'>{r['share']:.0%}</td>{kinds}"
            f"<td class='n'>{r['masses_big']:.1f}</td>"
            f"<td class='n'>{r['unowned']:.1f}</td>"
            f"<td class='n'>{r['empty_unowned']:.1f}</td>"
            f"<td>{cap}</td></tr>")
    return "\n".join(out)


def gates_html(rows: list[dict]) -> str:
    out = []
    for r in rows:
        cells = "".join(
            f"<td class='n{' dead' if r['gates'][g] < 1 else ''}'>{r['gates'][g]:.0f}</td>"
            for g in GATES)
        out.append(f"<tr><td>{r['name']}</td>{cells}</tr>")
    return "\n".join(out)


CSS = """
:root { --bg:#fbfaf8; --fg:#1d1c1a; --muted:#6a665f; --rule:#e0dcd4;
        --accent:#7a4a1e; --dead:#a11; }
* { box-sizing:border-box; }
body { margin:0; padding:2.5rem 1.5rem 5rem; background:var(--bg); color:var(--fg);
  font-family:-apple-system,"Segoe UI",sans-serif; line-height:1.55; }
main { max-width:1180px; margin:0 auto; }
h1 { font-family:Charter,Georgia,serif; font-weight:600; font-size:2rem; margin:0 0 .2rem; }
h2 { font-family:Charter,Georgia,serif; font-weight:600; font-size:1.35rem;
  color:var(--accent); margin:2.5rem 0 .4rem; }
.meta { color:var(--muted); font-size:.85rem; margin:0 0 2rem; }
code { font-family:ui-monospace,Consolas,monospace; font-size:.9em;
  background:#f0ede7; padding:.1em .35em; border-radius:3px; }
p { max-width:70ch; }
table { border-collapse:collapse; width:100%; margin:.8rem 0 .4rem; font-size:.85rem; }
th,td { text-align:left; padding:.35rem .5rem; border-bottom:1px solid var(--rule); }
th { font-weight:600; font-size:.75rem; text-transform:uppercase;
  letter-spacing:.04em; color:var(--muted); }
td.n { text-align:right; font-family:ui-monospace,Consolas,monospace;
  font-variant-numeric:tabular-nums; }
td.share { font-weight:600; }
td.dead { color:var(--dead); font-weight:600; }
.note { color:var(--muted); font-size:.8rem; max-width:70ch; }
.scroll { overflow-x:auto; }
"""


def build(stock: dict, ours: dict, out_html: Path, data_dir: Path,
          sources: list[Path]) -> Path:
    stock_rows = [summarise(n, stock[n]) for n in sorted(stock)]
    our_rows = [summarise(n, ours[n]) for n in sorted(ours)]
    ref = [r for r in stock_rows if r["name"] != "Thames"]
    band = (min(r["share"] for r in ref), max(r["share"] for r in ref)) if ref else (0, 0)
    thames = next((r for r in stock_rows if r["name"] == "Thames"), None)

    data_dir.mkdir(parents=True, exist_ok=True)
    for src in sources:
        if src.exists():
            shutil.copyfile(src, data_dir / src.name)

    khead = "".join(f"<th>{k}</th>" for k in KINDS)
    ghead = "".join(f"<th>{g}</th>" for g in GATES)
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Neutral supply baseline</title>
<style>{CSS}</style>
<main>
<h1>Neutral supply: what the stock maps actually do</h1>
<p class="meta">Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
 &middot; repo commit <code>{git_commit()}</code>
 &middot; data in <a href="{data_dir.name}/">{data_dir.name}/</a></p>

<p><strong>Neutral</strong> here means exactly what <code>fairness.py</code> calls
<em>unclaimed</em>: no town centre within <strong>{OWNERSHIP_RADIUS:.0f} tiles of
walking distance</strong>. It is the supply a player has to leave home and contest,
and every number below is a mean over that map's captures.</p>

<h2>The reference band</h2>
<p>Thames was being used as the reference for how much neutral supply a map should
carry. It is the outlier of the stock set at
<strong>{thames['share']:.0%}</strong> of all resources neutral. Every other stock
map measured, Arabia included, sits between
<strong>{band[0]:.0%}</strong> and <strong>{band[1]:.0%}</strong>.</p>

<div class="scroll"><table>
<tr><th>stock map</th><th>n</th><th>all res</th><th>neutral</th><th>share</th>
{khead}<th>masses</th><th>unowned</th><th>empty</th><th></th></tr>
{rows_html(stock_rows, True)}
</table></div>
<p class="note">masses = landmasses of 60+ tiles; unowned = those with no town
centre; empty = unowned ones carrying no resources at all.</p>

<h2>Ours, before the neutral pass</h2>
<div class="scroll"><table>
<tr><th>our map</th><th>n</th><th>all res</th><th>neutral</th><th>share</th>
{khead}<th>masses</th><th>unowned</th><th>empty</th><th></th></tr>
{rows_html(our_rows, True)}
</table></div>

<h2>What each <code>min_distance_to_players</code> gate admits</h2>
<p>Placeable land in tiles - dry land minus forest minus the 6-tile map-edge
margin, and nothing else. Deliberately a permissive upper bound: the real
placement also honours <code>avoid_forest_zone</code>,
<code>avoid_cliff_zone</code> and actor-area spacing, so a gate reading
<span style="color:var(--dead);font-weight:600">0</span> here places nothing
whatever its other settings say.</p>
<p><code>resources_neutral.inc</code> gates at <strong>26</strong>.
<code>remote_resources.inc</code> self-defines
<code>REMOTE_DISTANCE 100</code>.</p>

<div class="scroll"><table>
<tr><th>map</th>{ghead}</tr>
{gates_html(stock_rows)}
{gates_html(our_rows)}
</table></div>
</main>
"""
    out_html.write_text(html, encoding="utf-8")
    return out_html


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stock", required=True)
    ap.add_argument("--ours", required=True)
    ap.add_argument("--tag", default="neutral_baseline")
    args = ap.parse_args()

    stock = json.loads(Path(args.stock).read_text(encoding="utf-8"))
    ours = json.loads(Path(args.ours).read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    reports = REPO / "reports"
    out = build(stock, ours, reports / f"{stamp}_{args.tag}.html",
                reports / f"{stamp}_{args.tag}_data",
                [Path(args.stock), Path(args.ours)])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
