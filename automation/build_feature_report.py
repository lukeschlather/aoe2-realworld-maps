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

**The pictures are the stored capture render, not a re-derivation.**
``sample_analysis._render`` already draws the captured scenario properly -
real coastline, every land resource dotted by which player can walk to it
first, every TC ringed and numbered - and stores it as
``preview_png_b64``. This report decodes that and turns it; it does not
repaint from a land mask. An earlier version of this file did repaint, using
``thumbnail.terrain_rgb`` on the binary mask, and the result looked like a
pre-render thumbnail because that is effectively what it was: same
synthetic painter, none of the resources or starts, none of the detail that
makes a capture worth looking at.

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

import math

import numpy as np
from PIL import Image
from scipy import ndimage

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rwmaps import scx_read, thumbnail  # noqa: E402
import capture_render  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402

#: SHALLOWS terrain, the one this report exists to look for.
SHALLOWS_ID = 4

#: Shallows are drawn as a CHECKERBOARD of the render's own water and land
#: colours, alternating by tile parity, rather than as some third invented
#: colour.
#:
#: The reason is semantic and not decorative: shallows are the one terrain
#: that is *both* - boats sail them and land units ford them - so a flat teal
#: read as "a kind of water", which is exactly the wrong intuition and the
#: distinction this whole facility turns on. Half water, half land says what
#: they are. Colours are imported from sample_analysis so they track the
#: render they are painted onto instead of drifting from it.
#:
#: Unlike the overlay in window_candidates, these tiles are READ FROM THE
#: CAPTURE - what the engine actually painted, not where blocks were aimed.
from sample_analysis import LAND as _LAND, SEA as _SEA  # noqa: E402

SHALLOW_CHECKER = (_SEA, _LAND)

#: Painted into the corners the turn creates. Deliberately not water: a
#: reader has to be able to see where the playable square actually ends.
OFFMAP = (26, 26, 26)

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


def _rot_point(x: float, y: float, w: int, h: int, deg: float,
               nw: int, nh: int) -> tuple[float, float]:
    """Where (x, y) lands after ``Image.rotate(deg, expand=True)``.

    PIL rotates counter-clockwise about the centre and then re-centres on the
    grown canvas, so the same transform has to be applied by hand to follow a
    tile through it. Worked out rather than guessed: the crop below is
    centred on this, and a wrong sign here would silently crop open sea and
    look like "the islands did not render".
    """
    a = math.radians(deg)
    dx, dy = x - w / 2.0, y - h / 2.0
    return (math.cos(a) * dx + math.sin(a) * dy + nw / 2.0,
            -math.sin(a) * dx + math.cos(a) * dy + nh / 2.0)


def _rotated_size(w: int, h: int, deg: float) -> tuple[int, int]:
    a = math.radians(deg)
    c, s_ = abs(math.cos(a)), abs(math.sin(a))
    return int(round(w * c + h * s_)), int(round(w * s_ + h * c))


def turned(preview_b64: str, shallow: np.ndarray, px: int = 320,
           scenario=None) -> tuple[Image.Image, tuple[float, float], float]:
    """The capture render, shallows marked, in the in-game orientation.

    Mirrors ``build_candidate_report.turned_preview`` for the turn itself.
    Returns the image plus the scale and rotated geometry the crop needs, so
    the crop cannot use a different transform than the picture.

    ``scenario`` is the capture this row came from. Given one, the picture is
    ``capture_render.grid_image`` - the utility treatment, with forest, trees
    and fish in it - and ``preview_b64`` is only the fallback for a row whose
    scenario is no longer on disk. The stored preview was retired as a report
    visual on 2026-08-20: it has no trees and no fish, and on a coastline map
    those are most of what a reader is looking for.

    The utility render comes back grid-aligned on purpose. The crops below
    follow tiles through the rotation by hand, so a finished diamond would
    leave them nothing to measure against.
    """
    img = capture_render.grid_image(scenario)
    if img is None:
        raw = base64.b64decode(preview_b64.split(",", 1)[-1])
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    scale = w / shallow.shape[0]

    # Shallows are painted BEFORE the turn, in grid space, where their tile
    # coordinates mean something. read_land_mask reads shallows as sea, so the
    # stored render cannot show them and they have to be added here.
    if shallow.any():
        a = np.asarray(img).copy()
        ys, xs = np.nonzero(shallow)
        k = max(1, int(round(scale)))
        for y, x in zip(ys, xs):
            # Parity of the tile, so neighbouring shallows alternate and the
            # patch reads as a checkerboard at map scale rather than as
            # noise. A sub-tile checker at 3 px/tile would just dither.
            a[int(y * scale):int(y * scale) + k,
              int(x * scale):int(x * scale) + k] = SHALLOW_CHECKER[(int(y) + int(x)) % 2]
        img = Image.fromarray(a)

    if thumbnail.ICON_ROTATION % 360:
        img = img.rotate(thumbnail.ICON_ROTATION, expand=True,
                         resample=Image.BICUBIC, fillcolor=OFFMAP)
    return img, (scale, 0.0), float(px)


def whole(img: Image.Image, px: int = 320) -> Image.Image:
    return img.resize((px, px), Image.LANCZOS)


def crop_at(img: Image.Image, grid_yx: tuple[int, int], scale: float,
            grid_size: int, half_tiles: int, px: int = 320) -> Image.Image:
    """A SQUARE crop of the turned image, centred on a grid tile.

    Square is the whole point and the previous version got it wrong: it
    cropped in grid space and clamped each edge independently, so a tile near
    the map's south edge produced a 62x92 region that was then resized to a
    square and came out visibly stretched. Here the box is kept square and
    *shifted* to stay inside the canvas instead of being trimmed.
    """
    w0 = h0 = grid_size * scale
    nw, nh = _rotated_size(int(w0), int(h0), thumbnail.ICON_ROTATION)
    cx, cy = _rot_point(grid_yx[1] * scale, grid_yx[0] * scale,
                        int(w0), int(h0), thumbnail.ICON_ROTATION, nw, nh)
    half = half_tiles * scale
    x0 = min(max(0.0, cx - half), max(0.0, img.width - 2 * half))
    y0 = min(max(0.0, cy - half), max(0.0, img.height - 2 * half))
    side = min(2 * half, float(min(img.width, img.height)))
    box = (int(x0), int(y0), int(x0 + side), int(y0 + side))
    return img.crop(box).resize((px, px), Image.LANCZOS)


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
        # One parse. read_terrain_grid/read_land_mask/read_walkable_mask are
        # three separate calls that each re-parse the file, which made this
        # report cost minutes for six captures.
        cap = scx_read.read_capture(scx)
        grid = np.asarray(cap.terrain)
        land = cap.land_mask          # property: shallows read as sea
        walk = cap.walkable_mask      # property: shallows are fords
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
        wlab, _wn = ndimage.label(walk, structure=np.ones((3, 3), bool))
        sizes = {i: int((lab == i).sum()) for i in range(1, _n + 1)}
        mainland = max(sizes, key=sizes.get) if sizes else 0
        # Walkable components the mainland reaches. Compared as SETS rather
        # than by sampling one tile: an island's own centre tile is often
        # forest or water, and a single-tile probe then reports "n/a" for a
        # ford that is plainly there.
        main_w = set(np.unique(wlab[(lab == mainland) & walk]).tolist()) - {0}
        probe_rows = []
        for label, plon, plat in PROBES:
            y, x = tile(plon, plat)
            piece = int(lab[y, x])
            size = sizes.get(piece, 0)
            if not piece:
                verdict, cls = "water", "water"
            elif piece == mainland:
                verdict, cls = f"part of the mainland ({size:,} t)", "bad"
            else:
                comps = set(np.unique(wlab[(lab == piece) & walk]).tolist()) - {0}
                ford = "fordable from the mainland" if (comps & main_w)                     else "boat only"
                verdict = f"SEPARATE island, {size:,} tiles &middot; {ford}"
                cls = "good"
            probe_rows.append(
                f"<tr><td>{esc(label)}</td><td class='{cls}'>{verdict}</td></tr>")

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
        turned_img, (pscale, _), _ = turned(rec["preview_png_b64"], shallow,
                                            scenario=scx)
        cards.append(f"""
    <article>
      <h3>{esc(name)} <span class="dim">sample {si}</span></h3>
      <div class="views">
        <figure><img src="{b64(whole(turned_img))}">
          <figcaption>the capture as
            <code>sample_analysis</code> renders it &mdash; resources dotted by
            nearest player, TCs ringed &mdash; in-game orientation</figcaption></figure>
        <figure><img src="{b64(crop_at(turned_img, (dy, dx), pscale, land.shape[0], CROP_TILES))}">
          <figcaption>Denmark, {CROP_TILES * 2} tiles across &mdash;
            same render, same orientation</figcaption></figure>
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
.key {{ display:inline-block; width:12px; height:12px; border-radius:2px;
  margin-right:.3rem; vertical-align:-2px;
  background:
    linear-gradient(45deg, rgb(28,61,92) 25%, transparent 25%) 0 0/6px 6px,
    linear-gradient(-45deg, rgb(28,61,92) 25%, transparent 25%) 0 0/6px 6px,
    rgb(94,122,84); }}
</style>
<h1>The feature layer, in the engine</h1>
<p class="meta">Generated {generated_at} &middot; commit <code>{commit}</code>
  &middot; run-id <code>{run_id}</code> &middot; {n} real captures</p>
<p class="lede">Every number here is read off the captured
  <code>.aoe2scenario</code> - the terrain grid the engine actually produced -
  not off the script that asked for it. <span class="key"></span>is how SHALLOWS (terrain 4) are drawn: a
  checkerboard of the render's own water and land colours, because shallows
  are the one terrain that is <b>both</b> - boats sail them, land units ford
  them. Those tiles are read off the capture, not off the script. Both pictures are in the
  <b>in-game orientation</b>; up is up in the game and nothing else. The
  second is a crop, because a 111-tile island is about ten tiles across and
  is a few pixels wide in a whole-map view.</p>
{cards}
"""


if __name__ == "__main__":
    raise SystemExit(main())
