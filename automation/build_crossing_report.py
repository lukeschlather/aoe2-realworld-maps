"""Did the fords work? Read off the capture, per crossing.

Sibling of ``build_feature_report.py``, which asks the Danish question -
"are Zealand and Funen separate islands" - and has that geography baked in.
This asks the opposite one: three chains of shallows were laid across three
straits to **join** landmasses, so for each crossing,

* is the strait crossable on foot at that strait, and
* how wide is the crossing at its pinch?

Both are measured **inside a corridor around that strait** - the tiles
within a set distance of the chain's own centreline - by component sets.
Getting the locality right took three tries, and each wrong answer was
plausible:

* An unrestricted connectivity test answers a different question. With
  three crossings on one map, Ireland reaches France whenever *any* of them
  is open, so the whole-map test said YES six times out of six while two of
  the three fords were shut. And ``nav_width`` over a whole route returns
  the narrowest point anywhere along it - usually a forest pinch inland -
  not the width of the crossing.
* A pair of probe tiles lands wherever it lands. One anchor fell inside a
  forest-enclosed pocket and reported no route across a ford that was open;
  another snapped to a one-tile islet instead of the continent. Hence
  component sets - the set of passable components touching each side.
* Locality is measured in a **stadium around the chain's own centreline**
  rather than a square around its midpoint. The two agreed on every sample
  measured, so this is not a bug fix - a square is just the wrong shape for
  a 25-tile diagonal chain and would eventually clip one. Worth recording
  that the square was *suspected* of a false SHUT on St George's Channel and
  cleared: unrestricted, that chain's ground component does touch Ireland
  and Britain, but only because the North Channel joins them, which is
  exactly the whole-map confusion above. The SHUT was real - the chain's
  Welsh end disc sat a tile offshore, ate the tip of Pembrokeshire into
  shallows, and left Britain proper 4.1 tiles beyond it.

Each crossing is measured twice, and the pair is the interesting part:
once with **water as the only barrier** (``land | SHALLOWS``) and once with
**forest counted too** (``walkable_mask``). Water is a hard barrier; trees
are choppable. If the two disagree, the crossing is there and something can
be done about it; if both say shut, the chain pinched.

**The naval question is reported both ways on purpose.** This project's
``land_mask`` reads shallows as sea, so every "a boat can get through"
number it has ever produced *assumed* ships enter shallows rather than
measuring it - including the Baltic-to-Kattegat claim from run
``scand_feat``. Whether AoE2 lets a ship into terrain 4 is not settled by
anything on disk here, and it is load-bearing for the Strait of Dover: if
ships are blocked, a chain across the Channel severs a naval route while
adding a land one. So each crossing gets two rows - the sea route with
shallows counted as water, and with shallows counted as blocking - and the
difference between them is exactly the exposure.

Pictures are the stored capture render, turned to the in-game orientation,
with shallows checkerboarded on; the helpers come from
``build_feature_report`` so there is one copy of the rotation arithmetic.

Usage:
    uv run python automation/build_crossing_report.py --run-id britain_crossings
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from build_feature_report import (SHALLOWS_ID, b64, crop_at, esc,  # noqa: E402
                                  git_commit, nav_width, turned, whole)
from rwmaps import features, scx_read  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402

#: One row per crossing: the two land anchors that have to end up joined,
#: the two SEA anchors either side of the strait, and where to crop.
#:
#: Land anchors are inland, so a coastline a tile out cannot turn one into a
#: water tile and make the answer "no piece" instead of "not joined". Sea
#: anchors are well offshore for the same reason in reverse.
CROSSINGS = [
    {"label": "North Channel",
     "land": (("N-Ireland", -6.30, 54.55), ("Scotland", -4.25, 55.86)),
     "sea": (("Irish Sea", -5.30, 53.90), ("Malin/Atlantic", -6.80, 55.70)),
     "crop": (-5.45, 54.85), "half_tiles": 26},
    {"label": "St George's Channel",
     "land": (("Ireland", -7.70, 53.20), ("Wales", -3.20, 52.10)),
     "sea": (("Irish Sea", -5.40, 53.50), ("Celtic Sea", -6.60, 51.10)),
     "crop": (-5.85, 52.05), "half_tiles": 30},
    {"label": "Strait of Dover",
     "land": (("Kent", 1.08, 51.28), ("Pas-de-Calais", 1.85, 50.95)),
     "sea": (("English Channel", 0.90, 50.30), ("North Sea", 2.60, 51.85)),
     "crop": (1.45, 51.05), "half_tiles": 24},
]

#: A land piece smaller than this is an islet, not a side of a strait.
MIN_LANDMASS_TILES = 500


def _feature_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """``(--feature specs, --feature-preset names)`` out of a recorded argv.

    Read off the capture rather than hard-coded, so the report works for any
    radius variant of the crossings without a second copy of the coordinates
    that could disagree with the one the engine was given.
    """
    specs, presets = [], []
    it = iter(argv)
    for a in it:
        if a == "--feature":
            specs.append(next(it, ""))
        elif a == "--feature-preset":
            presets.append(next(it, ""))
    return specs, presets


def _corridor(shape, a, b, half: int) -> np.ndarray:
    """Tiles within ``half`` of the segment ``a``-``b``: the strait, locally.

    A square centred on the chain midpoint was the obvious thing and it was
    wrong. On the 20-tile St George's chain the corridor from the shallows to
    Wales runs out past the corner of that square, so the component test
    reported SHUT on a ford that was open - measured: unrestricted, the same
    chain's ground component touches Ireland, Britain and the continent in
    all three samples. A stadium around the line clips the rest of the map
    without clipping the crossing.
    """
    ys, xs = np.ogrid[:shape[0], :shape[1]]
    ay, ax = float(a[0]), float(a[1])
    by, bx = float(b[0]), float(b[1])
    vy, vx = by - ay, bx - ax
    vv = vy * vy + vx * vx
    if vv == 0:
        t = np.zeros(shape)
    else:
        t = np.clip(((ys - ay) * vy + (xs - ax) * vx) / vv, 0.0, 1.0)
    dy = (ys - ay) - t * vy
    dx = (xs - ax) - t * vx
    return (dy * dy + dx * dx) <= float(half) ** 2


def _ford(passable, llab, pa: int, pb: int, eight, tile, f):
    """Is the strait crossable inside this box, and how wide at the pinch?

    Component SETS, not probe tiles: the set of passable components that
    touch side A, against the set that touches side B.
    """
    lab, _n = ndimage.label(passable, structure=eight)
    ca = set(np.unique(lab[(llab == pa) & passable]).tolist()) - {0}
    cb = set(np.unique(lab[(llab == pb) & passable]).tolist()) - {0}
    shared = ca & cb
    if not shared:
        return False, 0.0
    comp = np.isin(lab, list(shared))
    ta, tb = comp & (llab == pa), comp & (llab == pb)
    if not (ta.any() and tb.any()):
        return True, float("nan")
    return True, nav_width(comp, tile(f.lon, f.lat, ta),
                           tile(f.lon2, f.lat2, tb))


def _e2e(passable, llab, pa: int, pb: int, eight) -> bool:
    """Do the two landmasses share any passable component at all?"""
    lab, _n = ndimage.label(passable, structure=eight)
    ca = set(np.unique(lab[(llab == pa) & passable]).tolist()) - {0}
    cb = set(np.unique(lab[(llab == pb) & passable]).tolist()) - {0}
    return bool(ca & cb)


#: The end-to-end question the three crossings add up to.
END_TO_END = (("Ireland", -7.70, 53.20), ("France", 2.35, 48.86))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
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
        cap = scx_read.read_capture(scx)
        grid = np.asarray(cap.terrain)
        land = cap.land_mask       # shallows read as sea
        walk = cap.walkable_mask   # shallows are fords
        shallow = grid == SHALLOWS_ID
        sail_with = ~land                  # shallows sailable
        sail_without = ~land & ~shallow    # shallows block ships

        window = MapWindow.from_center("laea", rec["lon"], rec["lat"],
                                       rec["span_km"], land.shape[0],
                                       rec.get("north_deg", 0.0))
        lon, lat = window.tile_lonlat()

        def tile(tlon, tlat, mask=None):
            d2 = (lon - tlon) ** 2 + ((lat - tlat) * 2.0) ** 2
            if mask is not None:
                d2 = np.where(mask, d2, np.inf)
            return np.unravel_index(int(np.nanargmin(d2)), d2.shape)

        eight = np.ones((3, 3), bool)
        wlab, wn = ndimage.label(walk, structure=eight)
        llab, ln = ndimage.label(land, structure=eight)

        # An anchor has to be snapped onto a tile that is *walkable* and on a
        # landmass worth the name, not merely onto the nearest land. Snapping
        # to land alone gets this wrong two ways, both of them measured on
        # this run: Britain's own inland anchor lands on forest, which is
        # land and is not walkable, so a single-tile probe reported "no
        # route" across a ford that plainly worked; and Calais snapped onto a
        # one-tile islet 1 tile offshore instead of the continent.
        big = np.zeros_like(land)
        for i in range(1, ln + 1):
            piece = llab == i
            if int(piece.sum()) >= MIN_LANDMASS_TILES:
                big |= piece
        footing = walk & big

        def joined(mask, a, b):
            """(connected?, bottleneck tiles) for a route from a to b."""
            lab, _n = ndimage.label(mask, structure=eight)
            if not lab[a] or lab[a] != lab[b]:
                return False, 0.0
            return True, nav_width(mask, a, b)

        # The ford has to be measured AT THE STRAIT, in a box around it, and
        # by component sets rather than by a pair of probe tiles. Both
        # corrections were forced by getting it wrong on run
        # britain_crossings:
        #
        # * Whole-map connectivity answers a different question. With three
        #   crossings on the map, Ireland reaches France whenever ANY of them
        #   is open, so an unrestricted test said YES six times out of six
        #   while two of the three fords were shut. And ``nav_width`` over a
        #   whole route returns the narrowest point anywhere along it -
        #   typically a forest pinch inland - not the width of the crossing.
        # * A single probe tile lands wherever it lands. One anchor fell in a
        #   forest-enclosed pocket and reported "no route" across a ford that
        #   was open.
        #
        # So: components inside the box, the set touching each side, and the
        # answer is whether those sets intersect.
        feats = [f for f in features.resolve(
                     *_feature_args(rec.get("extra_args") or []))
                 if f.kind == "channel"]
        rows_html = []
        for c in CROSSINGS:
            f = next((f for f in feats if f.note.startswith(c["label"])), None)
            (an, alon, alat), (bn, blon, blat) = c["land"]
            pa = int(llab[tile(alon, alat, big)])
            pb = int(llab[tile(blon, blat, big)])
            local = _corridor(walk.shape, tile(f.lon, f.lat),
                              tile(f.lon2, f.lat2),
                              c["half_tiles"]) if f is not None else None
            verdicts = []
            for tag, passable in (("water only", land | shallow),
                                  ("forest too", walk)):
                if local is None:
                    verdicts.append((tag, None, 0.0))
                    continue
                ok, width = _ford(passable & local, llab, pa, pb, eight,
                                  tile, f)
                verdicts.append((tag, ok, width))
            agree = len({v[1] for v in verdicts}) == 1
            body = " &middot; ".join(
                f"{tag}: {'OPEN' if ok else 'SHUT' if ok is not None else 'n/a'}"
                + (f" <b>{w:.2f} t</b>" if ok else "")
                for tag, ok, w in verdicts)
            allok = all(v[1] for v in verdicts)
            rows_html.append(
                f"<tr><th>{esc(c['label'])} &mdash; ford {esc(an)} "
                f"&rarr; {esc(bn)}</th>"
                f"<td class='{'good' if allok else 'bad'}'>{body}"
                + ("" if agree else " &mdash; <b>trees, not water</b>")
                + (f" <span class='dim'>&middot; "
                   f"{int((shallow & local).sum())} shallows tiles in the "
                   f"crop</span>" if local is not None else "")
                + "</td></tr>")
            (sa, salon, salat), (sb, sblon, sblat) = c["sea"]
            p, q = tile(salon, salat, sail_with), tile(sblon, sblat, sail_with)
            ok_w, w_w = joined(sail_with, p, q)
            ok_n, w_n = joined(sail_without, p, q)
            same = ok_w == ok_n
            rows_html.append(
                f"<tr><th class='sub'>&hookrightarrow; sail {esc(sa)} "
                f"&rarr; {esc(sb)}</th>"
                f"<td class='{'water' if same else 'bad'}'>"
                f"shallows sailable: {'YES' if ok_w else 'NO'}"
                + (f" ({w_w:.2f} t)" if ok_w else "")
                + f" &middot; shallows blocking: {'YES' if ok_n else 'NO'}"
                + (f" ({w_n:.2f} t)" if ok_n else "")
                + ("" if same else " &mdash; <b>this route depends on it</b>")
                + "</td></tr>")

        (en, elon, elat), (fn, flon, flat) = END_TO_END
        e, f = tile(elon, elat, footing), tile(flon, flat, footing)
        # Component sets here as well: a probe pair put France in the smaller
        # of two walkable pieces and reported NO on a sample whose three fords
        # were all open.
        pe = int(llab[tile(elon, elat, big)])
        pf = int(llab[tile(flon, flat, big)])
        ok_e2e = _e2e(walk, llab, pe, pf, eight)
        lsizes = sorted((int((llab == i).sum()) for i in range(1, ln + 1)),
                        reverse=True)[:4]
        wsizes = sorted((int((wlab == i).sum()) for i in range(1, wn + 1)),
                        reverse=True)[:4]

        turned_img, (pscale, _), _ = turned(rec["preview_png_b64"], shallow)
        crops = "".join(
            f"""<figure><img src="{b64(crop_at(turned_img, tile(*c['crop']),
                                               pscale, land.shape[0],
                                               c['half_tiles']))}">
            <figcaption>{esc(c['label'])},
              {c['half_tiles'] * 2} tiles across</figcaption></figure>"""
            for c in CROSSINGS)

        cards.append(f"""
    <article>
      <h3>{esc(name)} <span class="dim">sample {si}</span></h3>
      <div class="views">
        <figure><img src="{b64(whole(turned_img))}">
          <figcaption>the whole capture &mdash; resources dotted by nearest
            player, TCs ringed &mdash; in-game orientation</figcaption></figure>
        {crops}
      </div>
      <table>
        <tr><th>SHALLOWS (terrain 4) in the capture</th>
            <td class="{'good' if shallow.any() else 'bad'}">
              <b>{int(shallow.sum()):,} tiles</b>
              {'' if shallow.any() else '&mdash; the chains did not render'}</td></tr>
        <tr><th>walk {esc(en)} &rarr; {esc(fn)} <span class="dim">end to
              end</span></th>
            <td class="{'good' if ok_e2e else 'bad'}">
              {'YES' if ok_e2e else 'NO'}
              <span class="dim">&mdash; whichever fords are open; no width,
                because the pinch on a route this long is a forest belt
                inland and not a crossing</span></td></tr>
        {''.join(rows_html)}
        <tr><th>largest land pieces <span class="dim">shallows read as
              sea</span></th>
            <td>{esc(', '.join(f'{s:,}' for s in lsizes))}</td></tr>
        <tr><th>largest WALKABLE pieces</th>
            <td>{esc(', '.join(f'{s:,}' for s in wsizes))}</td></tr>
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
    out = REPO / "reports" / f"{stamp}_crossing_report_{args.run_id}.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(cards)} captures)")
    return 0


TEMPLATE = """<meta charset="utf-8">
<title>The British Isles crossings, in the engine</title>
<style>
:root {{ color-scheme: dark; }}
body {{ background:#141414; color:#e6e6e6; margin:0 auto; max-width:1180px;
  padding:2rem 1.5rem 5rem; font:15px/1.55 system-ui, sans-serif; }}
h1 {{ font-size:1.6rem; margin:0 0 .3rem; }}
h3 {{ font-size:1rem; margin:0 0 .6rem; }}
.meta {{ color:#8f8f8f; font-size:.8rem; margin:0 0 1.4rem; }}
.lede {{ max-width:78ch; }}
article {{ border:1px solid #2b2b2b; border-radius:8px; padding:1rem;
  margin:1.2rem 0; background:#1b1b1b; }}
.views {{ display:flex; gap:.7rem; flex-wrap:wrap; margin-bottom:.7rem; }}
figure {{ margin:0; }}
figure img {{ width:265px; display:block; border-radius:4px;
  image-rendering:pixelated; }}
figcaption {{ color:#8f8f8f; font-size:.75rem; padding-top:.3rem;
  max-width:265px; }}
table {{ border-collapse:collapse; width:100%; font-size:.85rem; }}
th,td {{ border-bottom:1px solid #2b2b2b; padding:.35rem .5rem;
  text-align:left; vertical-align:top; }}
th {{ color:#8f8f8f; font-weight:600; width:38%; }}
th.sub {{ padding-left:1.4rem; font-weight:400; }}
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
<h1>The British Isles crossings, in the engine</h1>
<p class="meta">Generated {generated_at} &middot; commit <code>{commit}</code>
  &middot; run-id <code>{run_id}</code> &middot; {n} real captures</p>
<p class="lede">Three chains of shallows, laid across the North Channel, St
  George's Channel and the Strait of Dover, to join the three landmasses both
  Britain windows put on the map. Every number is read off the captured
  <code>.aoe2scenario</code>. <span class="key"></span>is SHALLOWS (terrain
  4), checkerboarded because shallows are the one terrain that is
  <b>both</b>: land units ford them, and this project's masks read them as
  sea.</p>
<p class="lede">Each ford is measured <b>at its own strait</b>, inside the
  crop shown beside it, by component sets rather than probe tiles - a
  whole-map test says YES whenever any one of the three crossings is open,
  which is not the question. It is measured twice: with <b>water only</b> as
  a barrier, and with <b>forest too</b>. Water is hard, trees are choppable,
  so a disagreement means the crossing is there and can be cleared, while
  both saying SHUT means the chain pinched.</p>
<p class="lede">Each crossing also carries a sea row given <b>both ways</b> -
  shallows sailable and shallows blocking - because nothing measured in this
  project settles which one AoE2 does, and for the Strait of Dover the answer
  decides whether a naval route was severed to add a land one.</p>
{cards}
"""


if __name__ == "__main__":
    raise SystemExit(main())
