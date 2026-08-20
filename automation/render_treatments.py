"""Side-by-side render treatments, for every shipped map, every stock
benchmark, and any preset named on the command line.

One row per map, one column per treatment, all from the same captured
scenario so the comparison is between *treatments* and nothing else:

1. **existing analysis** - ``sample_analysis._render``, what every capture
   report has shown: coast, resource dots coloured by who can walk to them
   first, TC rings.
2. **utility** - the same idea plus the thing it never drew: forest, in dark
   forest green, and tree objects on top of it. Wood is the resource a start
   lives or dies by and it was the only one with no mark on the picture.
3. **mod icon** - the treatment the shipped maps use on the map-selection
   screen (``thumbnail.render_icon``), fed by the capture rather than by the
   ``.rms``, so this column differs from the shipped icon only in where its
   terrain came from.
4-7. **four new thumbnail treatments** - Town Centres and trees only, since
   a resource dot is information the player does not have yet. See
   ``rwmaps/render_styles.py``.

No engine time: every scenario here is already on disk. One parse per map
(~4.5s), then seven renders off it.

Usage:
    uv run python automation/render_treatments.py
    uv run python automation/render_treatments.py --presets scand-shift-15 --no-stock
    uv run python automation/render_treatments.py --px 480 --slug big
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from PIL import Image  # noqa: E402

from rwmaps import render_styles as rs  # noqa: E402
from rwmaps import thumbnail  # noqa: E402
from rwmaps.presets import Registry  # noqa: E402
import sample_analysis  # noqa: E402
from preset_report import turned_preview  # noqa: E402
from runlog import git_commit  # noqa: E402

BASELINE = REPO / "out" / "resource_baseline.json"

#: Presets shown by default beyond what ships: the window the last session
#: left as the open question.
DEFAULT_EXTRA = ("scand-shift-15",)


def b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def esc(x) -> str:
    import html
    return html.escape(str(x))


# --------------------------------------------------------------------------
# what to draw
# --------------------------------------------------------------------------

def newest_scenario(paths: list[str]) -> Path | None:
    live = [REPO / p for p in paths if (REPO / p).is_file()]
    return max(live, key=lambda p: p.stat().st_mtime) if live else None


def preset_subjects(labels: list[str] | None, status: str | None
                    ) -> list[tuple[str, str, Path]]:
    """``(group, name, scenario)`` for presets with a capture still on disk."""
    reg = Registry(REPO).load()
    presets = reg.select(labels, status=status)
    out = []
    for p in presets:
        paths = [s for c in p.captures for s in c.scenarios]
        scen = newest_scenario(paths)
        if scen is None:
            print(f"  skip {p.label}: no captured scenario on disk")
            continue
        out.append((("shipped" if p.status == "shipped" else "candidate"),
                    f"{p.name}  [{p.label}]", scen))
    return out


def stock_subjects() -> list[tuple[str, str, Path]]:
    if not BASELINE.is_file():
        return []
    rows = json.loads(BASELINE.read_text(encoding="utf-8"))
    best: dict[str, Path] = {}
    for r in rows:
        if r["cohort"] not in ("arabia", "stock"):
            continue
        p = REPO / r["path"]
        if p.is_file():
            best.setdefault(r["map"], p)
    return [("stock", f"{m}  (stock)", p) for m, p in sorted(best.items())]


# --------------------------------------------------------------------------
# the existing analysis render, reproduced exactly
# --------------------------------------------------------------------------

def existing_render(scene: rs.Scene, px: int) -> tuple[Image.Image, dict]:
    """``sample_analysis._render`` on this capture, plus its ownership map.

    Called rather than reimplemented: it is a pure function of mask, TCs and
    resources, so calling it is the only way to be sure this column is the
    treatment it claims to be. It also computes which player can walk to
    each resource first, which the utility column then reuses instead of
    walking every distance twice.
    """
    mask = scene.land
    tcs = scene.tcs
    resources = scene.resources
    data_uri = sample_analysis._render(mask, tcs, resources, px=px * 2)
    raw = base64.b64decode(data_uri.split(",", 1)[1])
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = rs.turn(img, px)

    owner: dict[tuple[str, float, float], object] = {}
    if tcs:
        paths = {p: sample_analysis.land_path_distance(mask, (int(y), int(x)))
                 for p, x, y in tcs}
        for kind, x, y in resources:
            best_p, best_d = None, float("inf")
            for p, dist in paths.items():
                d = dist[int(y), int(x)]
                if d < best_d:
                    best_d, best_p = d, p
            owner[(kind, x, y)] = best_p if best_d <= 30.0 else None
    return img, owner


def treatments_for(scene: rs.Scene, px: int) -> list[tuple[str, str, Image.Image]]:
    existing, owner = existing_render(scene, px)
    out = [("existing", "Existing analysis", existing),
           ("utility", "Utility + forest",
            rs.utility(scene, px=px, resource_owner=owner)),
           ("mod-icon", "Shipped mod icon", rs.mod_icon_from_scene(scene, px=px))]
    for key, title, _why, fn in rs.AESTHETIC_TREATMENTS:
        out.append((key, title, fn(scene, px=px)))
    return out


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

CSS = """
body{font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;
  background:#11151a;color:#dfe6ee}
main{max-width:none;margin:0;padding:22px 26px 80px}
h1{font-size:25px;margin:0 0 4px} h2{font-size:18px;margin:30px 0 6px}
.meta{color:#8b9bb0;font-size:12.5px;margin:0 0 14px}
p.lead{color:#b9c6d6;max-width:82ch}
table.legend{border-collapse:collapse;font-size:12.5px;margin:10px 0 22px}
table.legend td,table.legend th{border:1px solid #263140;padding:4px 8px;
  text-align:left;vertical-align:top}
table.legend th{background:#18202a;color:#9fb4cc}
table.legend td:first-child{white-space:nowrap;color:#9fd0ff;font-weight:600}
.row{border-top:1px solid #263140;padding:14px 0 6px}
.row h3{font-size:16px;margin:0 0 2px;color:#dfe6ee}
.row .src{color:#7f8fa3;font-size:11.5px;font-family:ui-monospace,Consolas,
  monospace;word-break:break-all;margin-bottom:8px}
.strip{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px}
.panel{flex:0 0 auto}
.panel img{display:block;border-radius:4px;background:#0d1116}
.panel .cap{font-size:11.5px;color:#9fb4cc;margin-top:3px;text-align:center}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:9px;
  border:1px solid #38506b;color:#9fd0ff;margin-left:6px}
.warn{color:#e0b48b;font-size:11.5px}
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--presets", nargs="+", metavar="LABEL", default=None,
                    help="presets to draw in addition to what ships "
                         f"(default: {' '.join(DEFAULT_EXTRA)})")
    ap.add_argument("--only", nargs="+", metavar="LABEL", default=None,
                    help="draw ONLY these presets - no shipped set, no stock")
    ap.add_argument("--no-stock", action="store_true")
    ap.add_argument("--px", type=int, default=360, help="panel size")
    ap.add_argument("--slug", default="treatments")
    args = ap.parse_args()

    subjects: list[tuple[str, str, Path]] = []
    if args.only:
        subjects += preset_subjects(args.only, None)
    else:
        subjects += preset_subjects(None, "shipped")
        subjects += preset_subjects(list(args.presets or DEFAULT_EXTRA), None)
        if not args.no_stock:
            subjects += stock_subjects()
    if not subjects:
        sys.exit("nothing to draw")

    print(f"{len(subjects)} maps x {3 + len(rs.AESTHETIC_TREATMENTS)} treatments")
    rows = []
    titles: list[str] = []
    unknown_seen: dict[int, int] = {}
    for group, name, scen in subjects:
        print(f"  {name} ... ", end="", flush=True)
        scene = rs.scene_from_scenario(scen, name=name)
        for tid, n in scene.unknown_ids.items():
            unknown_seen[tid] = unknown_seen.get(tid, 0) + n
        panels = treatments_for(scene, args.px)
        titles = [t for _k, t, _i in panels]
        strip = "".join(
            f"<div class='panel'><img src='{b64(img)}' width='{args.px}' "
            f"height='{args.px}' alt=''><div class='cap'>{esc(title)}</div></div>"
            for _key, title, img in panels)
        rows.append(
            f"<div class='row'><h3>{esc(name)}"
            f"<span class='tag'>{esc(group)}</span>"
            f"<span class='tag'>{len(scene.tcs)} TCs &middot; "
            f"{len(scene.trees):,} tree objects &middot; "
            f"{100 * scene.forest.mean():.1f}% forest terrain</span></h3>"
            f"<div class='src'>{esc(scen.relative_to(REPO).as_posix())}</div>"
            f"<div class='strip'>{strip}</div></div>")
        print("done")

    legend_rows = [
        ("Existing analysis", "What every capture report has shown. Coast, "
         "every land resource dotted in the colour of whoever can walk to it "
         "first, TC rings. Facts for judging a generation, not a picture of "
         "a map."),
        ("Utility + forest", "The same, plus forest terrain in dark forest "
         "green and every tree object on top of it. Wood is the resource a "
         "start lives or dies by, and it was the only one with no mark on "
         "the picture. Fords are drawn as their own colour, since they are "
         "neither land nor sea."),
        ("Shipped mod icon", "The treatment the maps ship with today "
         "(thumbnail.render_icon), fed by this capture instead of by the "
         ".rms. Land and water and player diamonds only - which is all the "
         "script can know, because forest and objects are engine RNG."),
    ] + [(t, why) for _k, t, why, _fn in rs.AESTHETIC_TREATMENTS]

    legend = "".join(f"<tr><td>{esc(t)}</td><td>{esc(w)}</td></tr>"
                     for t, w in legend_rows)
    warn = ""
    if unknown_seen:
        top = sorted(unknown_seen.items(), key=lambda kv: -kv[1])[:8]
        warn = ("<p class='warn'>terrain ids not in "
                "<code>render_styles.CLASS_IDS</code>, drawn as plain grass: "
                + ", ".join(f"{i} ({n:,} tiles)" for i, n in top) + "</p>")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = REPO / "reports" / f"{stamp}_render_treatments_{args.slug}.html"
    out.write_text(f"""<!doctype html>
<meta charset="utf-8"><title>Render treatments</title>
<style>{CSS}</style>
<main>
<h1>Render treatments, side by side</h1>
<p class="meta">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
 &middot; commit <code>{esc(git_commit())}</code>
 &middot; {len(subjects)} maps &times; {len(titles)} treatments
 &middot; no engine time: one captured scenario per map, already on disk</p>
<p class="lead">Every panel in a row is the same capture, drawn a different
 way, in the <b>in-game orientation</b> - the grid turned counter-clockwise
 by <code>thumbnail.ICON_ROTATION</code>, which is the only view anybody
 sees. The four new treatments show <b>Town Centres and trees only</b>: a
 resource dot is information a player does not have when they are choosing a
 map. Terrain classes come from the game's own
 <code>includes/constants.inc</code>.</p>
<p class="lead">Two things these pictures cannot know. A stock map's in-game
 look owes a lot to its <b>colour correction</b> (Arabia runs
 <code>CC_DESERT</code>), which is a script setting and is not in the terrain
 grid - so Arabia is drawn as the green it literally is, not the yellow it
 plays as. And <b>elevation</b> is not in the grid either; where a treatment
 shades, it is shading distance to the coast and says so.</p>
{warn}
<table class="legend"><tr><th>treatment</th><th>what it is for</th></tr>
{legend}</table>
{''.join(rows)}
</main>
""", encoding="utf-8")
    size_mb = out.stat().st_size / 1e6
    print(f"\n-> {out}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
