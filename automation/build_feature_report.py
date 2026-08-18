"""What the engine actually did with the ``features`` layer.

Reads a ``mod_capture`` run and reports the things the existing builders
cannot, because none of them knows shallows exist:

* **Did SHALLOWS render at all?** Counted straight off the captured
  scenario's terrain grid (id 4), not inferred from the script. This is the
  question the whole facility rests on and it has exactly one honest answer.
* **Are Zealand and Funen separate islands?** Answered by labelling the
  captured land mask and looking up the connected piece at each island's own
  tile. "There is a 111-tile piece somewhere" does not answer it; "the piece
  at Zealand's tile is 111 tiles and is not the mainland" does.
* **Can a boat get from the Baltic to the Atlantic?** Connectivity plus the
  bottleneck width of the route, measured on the capture rather than on the
  mask the script was built from.
* **A zoom on Denmark.** A 111-tile island is about 10 tiles across, which
  at 300px for a 240-tile map is three pixels. The whole-map view cannot
  show what was asked about, so every condition also gets a crop.

Every picture is in the **in-game orientation** - turned by
``thumbnail.ICON_ROTATION``, the same turn the engine applies. Up is up in
the game and nothing else, including in the crops. ``window_candidates.py``
got this wrong by drawing the raw axis-aligned grid and calling it
"north up"; do not reintroduce that here.

Usage:
    uv run python automation/build_feature_report.py --run-id scand_feat
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rwmaps import scx_read, thumbnail  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402

#: SHALLOWS terrain, the one this report exists to look for.
SHALLOWS_ID = 4

#: Painted over the terrain picture wherever SHALLOWS landed. Unlike the
#: overlay in window_candidates, this one is READ FROM THE CAPTURE - it is
#: what the engine made, not where blocks were aimed.
SHALLOW_MARK = (90, 230, 255)

#: Places to interrogate, as (label, lon, lat).
PROBES = [
    ("Zealand", 11.80, 55.55),
    ("Funen", 10.35, 55.30),
    ("Copenhagen", 12.57, 55.68),
]

#: The route that has to work, and the crop the pictures zoom to.
BALTIC = (19.0, 58.8)
ATLANTIC = (2.0, 66.0)
DENMARK = (11.3, 55.6)
CROP_TILES = 46

#: A land piece at least this big is the mainland, not an island.
MAINLAND_TILES = 5000


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def nav_width(sail: np.ndarray, a, b) -> float:
    """Bottleneck of the a->b sea route, in tiles. 0 = no route."""
    dist = ndimage.distance_transform_edt(sail)
    lo, hi = 0.0, float(dist.max())
    for _ in range(22):
        mid = (lo + hi) / 2
        lab, _n = ndimage.label(dist >= mid, structure=np.ones((3, 3), bool))
        if lab[a] and lab[a] == lab[b]:
            lo = mid
        else:
            hi = mid
    return lo


def picture(land: np.ndarray, shallow: np.ndarray, px: int,
            crop: tuple[int, int] | None = None) -> Image.Image:
    """Terrain picture in the IN-GAME orientation, shallows marked.

    Crop happens before the turn; the turn is the same either way, so a crop
    is in the same frame as the full view.
    """
    rgb = thumbnail.terrain_rgb(land).copy()
    rgb[shallow] = SHALLOW_MARK
    if crop is not None:
        y, x = crop
        h = CROP_TILES
        y0, x0 = max(0, y - h), max(0, x - h)
        y1, x1 = min(rgb.shape[0], y + h), min(rgb.shape[1], x + h)
        rgb = rgb[y0:y1, x0:x1]
    img = Image.fromarray(rgb)
    if thumbnail.ICON_ROTATION % 360:
        img = img.rotate(thumbnail.ICON_ROTATION, expand=True,
                         resample=Image.NEAREST, fillcolor=(26, 26, 26))
    return img.resize((px, px), Image.NEAREST)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    root = REPO / "out" / "mod_capture" / args.run_id
    results = root / "results.jsonl"
    if not results.exists():
        raise SystemExit(f"no results at {results}")
    rows = [json.loads(l) for l in results.open(encoding="utf-8")]
    if not rows:
        raise SystemExit("empty results")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    cards = []
    for rec in rows:
        name, si = rec["region"], rec["sample_index"]
        scx = root / name / "raw" / f"sample_{si:03d}.aoe2scenario"
        if not scx.exists():
            continue
        grid = np.asarray(scx_read.read_terrain_grid(scx))
        land = scx_read.read_land_mask(scx)
        walk = scx_read.read_walkable_mask(scx)
        shallow = grid == SHALLOWS_ID
        sail = ~land

        window = MapWindow.from_center("laea", rec["lon"], rec["lat"],
                                       rec["span_km"], land.shape[0],
                                       rec.get("north_deg", 0.0))
        lon, lat = window.tile_lonlat()

        def tile(tlon, tlat, mask=None):
            d2 = (lon - tlon) ** 2 + ((lat - tlat) * 2.0) ** 2
            if mask is not None:
                d2 = np.where(mask, d2, np.inf)
            return np.unravel_index(int(np.nanargmin(d2)), d2.shape)

        lab, _n = ndimage.label(land, structure=np.ones((3, 3), bool))
        probe_rows = []
        for label, plon, plat in PROBES:
            y, x = tile(plon, plat)
            piece = int(lab[y, x])
            size = int((lab == piece).sum()) if piece else 0
            if not piece:
                verdict, cls = "water", "water"
            elif size >= MAINLAND_TILES:
                verdict, cls = f"part of the mainland ({size:,} t)", "bad"
            else:
                verdict, cls = f"SEPARATE island, {size:,} tiles", "good"
            probe_rows.append(
                f"<tr><td>{esc(label)}</td><td class='{cls}'>{esc(verdict)}</td></tr>")

        a, b = tile(*ATLANTIC, sail), tile(*BALTIC, sail)
        slab, _s = ndimage.label(sail, structure=np.ones((3, 3), bool))
        sea_ok = bool(slab[b]) and slab[b] == slab[a]
        bottleneck = nav_width(sail, a, b)

        wl, _w = ndimage.label(walk, structure=np.ones((3, 3), bool))
        wsizes = sorted((int((wl == i).sum()) for i in range(1, _w + 1)),
                        reverse=True)[:4]
        lsizes = sorted((int((lab == i).sum()) for i in range(1, _n + 1)),
                        reverse=True)[:4]

        dy, dx = tile(*DENMARK)
        cards.append(f"""
    <article>
      <h3>{esc(name)} <span class="dim">sample {si}</span></h3>
      <div class="views">
        <figure><img src="{b64(picture(land, shallow, 300))}">
          <figcaption>whole map &mdash; in-game orientation</figcaption></figure>
        <figure><img src="{b64(picture(land, shallow, 300, crop=(dy, dx)))}">
          <figcaption>Denmark, {CROP_TILES * 2} tiles across &mdash;
            in-game orientation</figcaption></figure>
      </div>
      <table>
        <tr><th>SHALLOWS (terrain 4) in the capture</th>
            <td class="{'good' if shallow.any() else 'water'}">
              <b>{int(shallow.sum()):,} tiles</b>
              {'&mdash; the mechanism works in the engine' if shallow.any()
               else '&mdash; none'}</td></tr>
        <tr><th>Baltic &rarr; Atlantic by sea</th>
            <td class="{'good' if sea_ok else 'bad'}">
              {'YES' if sea_ok else 'NO'} &middot; bottleneck
              <b>{bottleneck:.2f} tiles</b></td></tr>
        <tr><th>largest land pieces</th>
            <td>{esc(', '.join(f'{s:,}' for s in lsizes))}</td></tr>
        <tr><th>largest WALKABLE pieces</th>
            <td>{esc(', '.join(f'{s:,}' for s in wsizes))}
              <span class="dim">&mdash; shallows are fordable, so walkable
              can be one piece while land is several</span></td></tr>
        {''.join(probe_rows)}
        <tr><th>coastline IoU</th>
            <td>{rec['aesthetic']['iou_10m']:.3f}</td></tr>
        <tr><th>arguments</th>
            <td><code>{esc(' '.join(rec['extra_args']))}</code></td></tr>
      </table>
    </article>""")

    html = TEMPLATE.format(
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        commit=git_commit(), run_id=esc(args.run_id),
        n=len(cards), cards="".join(cards))
    out = REPO / "reports" / f"{stamp}_feature_report_{args.run_id}.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(cards)} captures)")
    return 0


TEMPLATE = """<meta charset="utf-8">
<title>Feature layer in the engine</title>
<style>
:root {{ color-scheme: dark; }}
body {{ background:#141414; color:#e6e6e6; margin:0 auto; max-width:1100px;
  padding:2rem 1.5rem 5rem; font:15px/1.55 system-ui, sans-serif; }}
h1 {{ font-size:1.6rem; margin:0 0 .3rem; }}
h3 {{ font-size:1rem; margin:0 0 .6rem; }}
.meta {{ color:#8f8f8f; font-size:.8rem; margin:0 0 1.4rem; }}
.lede {{ max-width:76ch; }}
article {{ border:1px solid #2b2b2b; border-radius:8px; padding:1rem;
  margin:1.2rem 0; background:#1b1b1b; }}
.views {{ display:flex; gap:.8rem; flex-wrap:wrap; margin-bottom:.7rem; }}
figure {{ margin:0; }}
figure img {{ width:300px; display:block; border-radius:4px;
  image-rendering:pixelated; }}
figcaption {{ color:#8f8f8f; font-size:.75rem; padding-top:.3rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.85rem; }}
th,td {{ border-bottom:1px solid #2b2b2b; padding:.35rem .5rem;
  text-align:left; vertical-align:top; }}
th {{ color:#8f8f8f; font-weight:600; width:34%; }}
.good {{ color:#7ee787; }} .bad {{ color:#ff7b72; }} .water {{ color:#8f8f8f; }}
.dim {{ color:#8f8f8f; font-weight:400; font-size:.8rem; }}
code {{ font-family:ui-monospace,Consolas,monospace; font-size:.85em; }}
.key {{ display:inline-block; width:10px; height:10px; border-radius:2px;
  background:rgb(90,230,255); margin-right:.3rem; }}
</style>
<h1>The feature layer, in the engine</h1>
<p class="meta">Generated {generated_at} &middot; commit <code>{commit}</code>
  &middot; run-id <code>{run_id}</code> &middot; {n} real captures</p>
<p class="lede">Every number here is read off the captured
  <code>.aoe2scenario</code> - the terrain grid the engine actually produced -
  not off the script that asked for it. <span class="key"></span>marks tiles
  the engine painted as SHALLOWS (terrain 4). Both pictures are in the
  <b>in-game orientation</b>; up is up in the game and nothing else. The
  second is a crop, because a 111-tile island is about ten tiles across and
  is a few pixels wide in a whole-map view.</p>
{cards}
"""


if __name__ == "__main__":
    raise SystemExit(main())
