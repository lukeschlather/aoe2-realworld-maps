"""Screen candidate map windows before spending engine time on any of them.

A "window" is the whole geographic setup of a map: projection, centre,
span and rotation. Choosing one is the decision that fixes whether a map
reads as the real place, and it is settled by eye - so this produces the
pictures and the plain geometry facts, and nothing else. It renders no
verdict about which window is good.

Cheap on purpose. Everything here is the *shipped* raster pipeline
(``raster.rasterize`` at the CLI's own 50m default, then
``simplify_features`` at the shipped 4/3 widths, then ``rms_land.cover_mask``
at the shipped 700 discs / 0.85 overlap / 12.0 max radius) run to the point
where the coastline is decided, and stopped there. It skips
``choose_starts``, which is ~70s of annealing a candidate does not need to
be judged on its geography, so a window costs ~5s instead of ~70s.

What that means for the pictures: they show the coastline a generated map
will be cut from, with **no** forest, elevation, resources or start
positions, and no engine RNG. They are not renders. For those, generate the
window for real and capture it - see RENDER_PIPELINE.md.

Three views per candidate:

* **truth** - the real coastline the window samples, north-up.
* **cover** - what 700 discs can actually approximate it with, north-up.
  This is the shape that ships; the gap between it and truth is the
  fidelity cost of the window.
* **minimap** - the same cover, turned the way the game draws the grid
  (counter-clockwise 45 degrees, see ``thumbnail.ICON_ROTATION``). North is
  up in this view only when the window's own ``--rotate`` is 45.

Usage:
    uv run python automation/window_candidates.py
    uv run python automation/window_candidates.py --groups britain,greatlakes
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import raster, rms_land, thumbnail  # noqa: E402
from rwmaps.cli import lands_for_size  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402

#: Everything the shipped defaults fix, restated here rather than imported
#: piecemeal, so a report can print the *complete* resolved parameter set
#: for a candidate instead of only what differs from a baseline.
#: Values are ``src/rwmaps/cli.py``'s own argparse defaults at 8 players.
SHIPPED = {
    "proj": "laea",
    "size": 240,
    "players": 8,
    "resolution": "50m",
    "min_water_width": 4,
    "min_land_width": 3,
    "min_island_tiles": 0,
    "overlap": 0.85,
    "max_radius": 12.0,
    "lands": lands_for_size(240),
}

#: A landmass smaller than this is not a place a player can start - it is
#: coastline speckle. Used only to describe a window, never to score it.
LANDMASS_FLOOR = 120

#: An enclosed water body smaller than this is an interstitial pocket, not
#: a lake or a sea. See ``thumbnail.MAX_POCKET_TILES`` for the same idea at
#: a much smaller scale.
WATERBODY_FLOOR = 60


@dataclass
class Candidate:
    group: str
    name: str
    lon: float
    lat: float
    span_km: float
    rotate: float
    note: str = ""
    proj: str = "laea"
    #: Overrides onto SHIPPED, for a candidate that needs a non-default knob.
    overrides: dict = field(default_factory=dict)

    @property
    def params(self) -> dict:
        return {**SHIPPED, "proj": self.proj, **self.overrides}

    @property
    def command(self) -> str:
        """The exact rwmaps invocation that builds this window."""
        p = self.params
        parts = [
            "uv run rwmaps", f'"{self.name}"',
            f"--center={self.lon},{self.lat}",
            f"--span-km {self.span_km:g}",
        ]
        if self.rotate:
            parts.append(f"--rotate {self.rotate:g}")
        if p["proj"] != "laea":
            parts.append(f"--proj {p['proj']}")
        for flag, key in (("--overlap", "overlap"),
                          ("--min-water-width", "min_water_width"),
                          ("--min-land-width", "min_land_width"),
                          ("--max-radius", "max_radius"),
                          ("--resolution", "resolution")):
            if p[key] != SHIPPED[key] or key in self.overrides:
                parts.append(f"{flag} {p[key]:g}" if isinstance(p[key], float)
                             else f"{flag} {p[key]}")
        return " ".join(parts)


# --------------------------------------------------------------------------
# The candidates themselves.
#
# Great Lakes and Britain are the two the user named. Every other group is a
# proposed replacement for the three regions retired on 2026-08-15 (Japan,
# Caribbean, New Zealand - see build_mod.RETIRED_REGIONS), chosen to lean
# continental/peninsular rather than archipelago: all three retirees were
# ISLANDS-type with narrow coastlines, which is also the shape that starved
# gold and stone placement (MOD_STATUS.md).
# --------------------------------------------------------------------------

CANDIDATES: list[Candidate] = [
    # --- Great Lakes: wanted more recognisable, and north-up -------------
    Candidate("greatlakes", "GL current (rotate 0)", -84.0, 44.5, 1600, 0,
              "the window in cli.REGIONS today - north reads to the upper left"),
    Candidate("greatlakes", "GL north-up, same window", -84.0, 44.5, 1600, 45,
              "current centre and span, rotated so north is up in game"),
    Candidate("greatlakes", "GL north-up 1400", -84.0, 44.5, 1400, 45),
    Candidate("greatlakes", "GL north-up 1200", -83.5, 44.5, 1200, 45),
    Candidate("greatlakes", "GL north-up 1000", -83.0, 45.0, 1000, 45),
    Candidate("greatlakes", "GL five lakes 1800", -85.0, 45.5, 1800, 45,
              "widest - tries to hold all five lakes inside the square"),
    Candidate("greatlakes", "GL Michigan-Huron", -85.0, 44.5, 1200, 45,
              "drops Superior and Ontario"),
    Candidate("greatlakes", "GL Huron-Erie", -82.0, 44.0, 1100, 45),
    Candidate("greatlakes", "GL Erie-Ontario", -79.5, 43.2, 900, 45,
              "the Niagara isthmus as the map's spine"),
    Candidate("greatlakes", "GL Superior", -87.5, 47.5, 1200, 45),

    # --- Britain: keep the current one, add a zoomed north-up ------------
    Candidate("britain", "Britain NW (current, keep)", -3.0, 54.5, 1300, 0,
              "the shipped window - north reads to the upper left"),
    Candidate("britain", "Britain north-up, same window", -3.0, 54.5, 1300, 45,
              "current centre and span, north up - reaches Norway"),
    Candidate("britain", "Britain north-up 1100", -2.8, 53.8, 1100, 45),
    Candidate("britain", "Britain north-up 1000", -2.5, 53.5, 1000, 45),
    Candidate("britain", "Britain north-up 950", -2.5, 53.2, 950, 45),
    Candidate("britain", "Britain north-up 900", -2.2, 53.0, 900, 45),
    Candidate("britain", "Britain north-up 1050 south", -3.0, 53.2, 1050, 45,
              "pushed south for more of the French Channel coast"),
    Candidate("britain", "Britain north-up 1150", -3.2, 54.0, 1150, 45),
    # The candidates above all put the continental patch's centroid near
    # 1.5E - that is Flanders and the Pas-de-Calais, not Normandy or
    # Brittany. Which one "a patch of France" means changes the window, so
    # both readings get candidates.
    Candidate("britain", "Britain north-up 1000 west", -4.0, 53.0, 1000, 45,
              "shifted west/south to reach Normandy and Brittany"),
    Candidate("britain", "Britain north-up 1050 west", -4.5, 52.8, 1050, 45),
    Candidate("britain", "Britain north-up 950 west", -4.0, 52.5, 950, 45),

    # --- proposed replacements -------------------------------------------
    Candidate("new", "Iberia", -4.0, 40.0, 1400, 45,
              "already in cli.REGIONS, never shipped; IoU 0.98 in the "
              "README candidate table"),
    Candidate("new", "Anatolia", 33.0, 39.0, 1500, 45,
              "already in cli.REGIONS, never shipped; IoU 0.97"),
    Candidate("new", "Gibraltar", -4.8, 36.2, 900, 45,
              "two continents and a strait"),
    Candidate("new", "Korea", 127.0, 36.5, 1200, 45),
    Candidate("new", "Red Sea", 37.5, 22.5, 2000, 45),
    Candidate("new", "Persian Gulf", 52.0, 27.0, 1700, 45),
    Candidate("new", "Baltic", 19.0, 59.0, 1500, 45),
    Candidate("new", "Levant and Cyprus", 33.5, 33.5, 1300, 45),
    Candidate("new", "Florida", -83.0, 27.5, 1700, 45),
    Candidate("new", "Bay of Biscay", -3.0, 45.0, 1400, 45),
    Candidate("new", "Adriatic", 16.5, 43.0, 1100, 45),
    Candidate("new", "Denmark", 10.0, 56.3, 1000, 45),
    Candidate("new", "Ireland", -8.0, 53.3, 900, 45),
]


def _pieces(binary: np.ndarray, floor: int, lon: np.ndarray, lat: np.ndarray,
            drop_edge: bool = False) -> list[dict]:
    """Connected pieces of ``binary`` as ``{tiles, lon, lat}``, largest first.

    The centroid is carried because a bare size cannot answer the question
    actually being asked of it. "Britain plus a 5,000-tile piece" is two
    completely different maps depending on whether that piece is Ireland or
    the French Channel coast, and only the coordinate distinguishes them.
    """
    labels, n = ndimage.label(binary, structure=np.ones((3, 3), bool))
    if not n:
        return []
    skip: set[int] = set()
    if drop_edge:
        skip = (set(labels[0].tolist()) | set(labels[-1].tolist())
                | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist()))
    sizes = np.bincount(labels.ravel())
    out = []
    for i in range(1, n + 1):
        if i in skip or sizes[i] < floor:
            continue
        sel = labels == i
        out.append({
            "tiles": int(sizes[i]),
            "lon": round(float(np.nanmean(lon[sel])), 1),
            "lat": round(float(np.nanmean(lat[sel])), 1),
        })
    return sorted(out, key=lambda d: -d["tiles"])


#: Painted outside the grid when a view is turned. Deliberately NOT
#: ``thumbnail.DEEP``: filling the corners with deep water hides where the
#: map actually ends, and a turned grid is a diamond - the playable square
#: has its corners at the picture's edge midpoints. A reader has to be able
#: to see that boundary to judge how much of the geography is really on the
#: map.
OFFMAP = (26, 26, 26)


def to_png(mask: np.ndarray, rotate_ccw: float, px: int = 300) -> Image.Image:
    """Minimap-style land/water picture, turned ``rotate_ccw`` degrees."""
    img = Image.fromarray(thumbnail.terrain_rgb(mask))
    if rotate_ccw % 360:
        img = img.rotate(rotate_ccw, expand=True, resample=Image.BICUBIC,
                         fillcolor=OFFMAP)
    return img.resize((px, px), Image.LANCZOS)


def b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def screen(c: Candidate, outdir: Path) -> dict:
    """Rasterise one candidate and write its three views."""
    p = c.params
    window = MapWindow.from_center(c.proj, c.lon, c.lat, c.span_km,
                                   p["size"], c.rotate)
    lon, lat = window.tile_lonlat()
    result = raster.rasterize(window, resolution=p["resolution"],
                              min_island_tiles=p["min_island_tiles"])
    truth = raster.simplify_features(result.land_mask,
                                     min_water_width=p["min_water_width"],
                                     min_land_width=p["min_land_width"])
    discs = rms_land.cover_mask(truth, p["lands"], max_radius=p["max_radius"],
                                overlap=p["overlap"])
    cover = rms_land.rasterize_discs(discs, p["size"])

    # North-up means north at the top of the picture. Inside the grid, north
    # sits at bearing `rotate` clockwise from grid-up (see
    # projection.MapWindow.tile_lonlat), so turning the grid counter-clockwise
    # by `rotate` puts it back at the top. At rotate 45 that is the same turn
    # the game itself applies, which is exactly why 45 is the north-up value.
    views = {
        "truth": to_png(truth, c.rotate),
        "cover": to_png(cover, c.rotate),
        "minimap": to_png(cover, thumbnail.ICON_ROTATION),
    }
    slug = c.name.lower().replace(" ", "_").replace(",", "").replace("(", "").replace(")", "")
    paths = {}
    for kind, img in views.items():
        path = outdir / c.group / f"{slug}__{kind}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        paths[kind] = str(path.relative_to(REPO)).replace("\\", "/")

    return {
        **asdict(c),
        "params": p,
        "command": c.command,
        "km_per_tile": c.span_km / p["size"],
        "land_fraction": float(truth.mean()),
        "cover_iou": rms_land.iou(cover, truth),
        "cover_misses_frac": rms_land.interior_holes(cover, truth),
        "landmasses": _pieces(truth, LANDMASS_FLOOR, lon, lat),
        "waterbodies": _pieces(~truth, WATERBODY_FLOOR, lon, lat, drop_edge=True),
        "png": paths,
        "b64": {k: b64(v) for k, v in views.items()},
    }


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


GROUP_TITLES = {
    "greatlakes": "Great Lakes",
    "britain": "Britain",
    "new": "Proposed replacements",
}

GROUP_BLURBS = {
    "greatlakes": (
        "Asked for: more recognisable, and north up. <b>Waterbodies</b> is the "
        "column that speaks to the first - it lists every enclosed water body "
        "of "
        f"{WATERBODY_FLOOR}+ tiles, largest first, so a window where the lakes "
        "merge into one blob is visibly a different row from one where five of "
        "them survive as five. Note that <code>--min-water-width 4</code> fills "
        "any channel under ~4 tiles, so the Straits of Mackinac and the Detroit "
        "and St Clair rivers close and Michigan/Huron/Erie separate rather than "
        "chain."
    ),
    "britain": (
        "The current shipped window is kept as-is (first row). The rest are "
        "north-up candidates, zoomed in far enough to drop Norway while keeping "
        "a patch of the French Channel coast. <b>Landmasses</b> is the column "
        "for that: the largest entry is Britain, and a continental patch big "
        "enough for two players has to show up as a second entry of real size."
    ),
    "new": (
        "Proposed replacements for Japan, Caribbean and New Zealand. All three "
        "retirees were ISLANDS-type with narrow coastlines, so these lean "
        "continental and peninsular. Iberia and Anatolia already exist in "
        "<code>cli.REGIONS</code> and have never shipped."
    ),
}


def render_html(rows: list[dict], stamp: str, commit: str, data_dir: str) -> str:
    def card(r: dict) -> str:
        fmt = lambda ps: " &middot; ".join(  # noqa: E731
            f'{d["tiles"]:,}<span class="dim"> at {d["lon"]},{d["lat"]}</span>'
            for d in ps) or "&mdash;"
        masses = fmt(r["landmasses"][:5])
        waters = fmt(r["waterbodies"][:6])
        p = r["params"]
        north_up = "yes" if r["rotate"] == 45 else "no"
        return f"""
    <article class="cand">
      <h3>{r['name']}</h3>
      {f'<p class="note">{r["note"]}</p>' if r["note"] else ''}
      <div class="views">
        <figure><img src="{r['b64']['truth']}" loading="lazy">
          <figcaption>truth &mdash; north up</figcaption></figure>
        <figure><img src="{r['b64']['cover']}" loading="lazy">
          <figcaption>700-disc cover &mdash; north up</figcaption></figure>
        <figure><img src="{r['b64']['minimap']}" loading="lazy">
          <figcaption>in-game minimap orientation</figcaption></figure>
      </div>
      <table class="facts">
        <tr><th>centre</th><td>{r['lon']}, {r['lat']}</td>
            <th>span</th><td>{r['span_km']:g} km ({r['km_per_tile']:.2f} km/tile)</td></tr>
        <tr><th>rotate</th><td>{r['rotate']:g}&deg; (north up in game: {north_up})</td>
            <th>projection</th><td>{p['proj']}</td></tr>
        <tr><th>land</th><td>{r['land_fraction']*100:.1f}%</td>
            <th>cover IoU</th><td>{r['cover_iou']:.3f}
              ({r['cover_misses_frac']*100:.1f}% of land missed)</td></tr>
        <tr><th>landmasses</th><td colspan="3">{masses}
              <span class="dim">tiles, &ge;{LANDMASS_FLOOR}</span></td></tr>
        <tr><th>waterbodies</th><td colspan="3">{waters}
              <span class="dim">tiles enclosed, &ge;{WATERBODY_FLOOR}</span></td></tr>
        <tr><th>grid</th><td colspan="3">{p['size']}&times;{p['size']},
              {p['players']} players, {p['lands']} discs, overlap {p['overlap']},
              max radius {p['max_radius']:g}, {p['resolution']} coastline,
              min water/land width {p['min_water_width']}/{p['min_land_width']},
              min island {p['min_island_tiles']} tiles</td></tr>
      </table>
      <pre class="cmd">{r['command']}</pre>
    </article>"""

    sections = []
    for group in ("greatlakes", "britain", "new"):
        got = [r for r in rows if r["group"] == group]
        if not got:
            continue
        sections.append(
            f'<section><h2>{GROUP_TITLES[group]}</h2>'
            f'<p class="blurb">{GROUP_BLURBS[group]}</p>'
            + "".join(card(r) for r in got) + "</section>"
        )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Window candidates</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ background:#141414; color:#e6e6e6; margin:0 auto; max-width:1180px;
        padding:2rem 1.5rem 5rem; font:15px/1.55 system-ui, sans-serif; }}
 h1 {{ margin:0 0 .3rem; font-size:1.7rem; }}
 h2 {{ margin:2.8rem 0 .4rem; font-size:1.3rem;
       border-bottom:1px solid #333; padding-bottom:.35rem; }}
 h3 {{ margin:0 0 .2rem; font-size:1.05rem; }}
 .meta, .note, .dim {{ color:#9a9a9a; }}
 .meta {{ font-size:.86rem; margin:0 0 1.4rem; }}
 .blurb {{ color:#bdbdbd; margin:.5rem 0 1.4rem; max-width:70ch; }}
 .note {{ font-size:.88rem; margin:0 0 .6rem; }}
 .cand {{ border:1px solid #2e2e2e; border-radius:8px; padding:1rem 1.1rem;
          margin:0 0 1.3rem; background:#191919; }}
 .views {{ display:flex; gap:.9rem; flex-wrap:wrap; margin:.6rem 0 .9rem; }}
 figure {{ margin:0; }}
 figure img {{ width:300px; max-width:100%; display:block; border-radius:4px;
               background:#0d1b2a; }}
 figcaption {{ color:#8f8f8f; font-size:.78rem; padding-top:.28rem; }}
 table.facts {{ border-collapse:collapse; font-size:.87rem; width:100%; }}
 table.facts th {{ text-align:left; color:#8f8f8f; font-weight:500;
                   padding:.16rem .8rem .16rem 0; white-space:nowrap;
                   vertical-align:top; width:1%; }}
 table.facts td {{ padding:.16rem 1.4rem .16rem 0; vertical-align:top; }}
 pre.cmd {{ background:#101010; border:1px solid #2b2b2b; border-radius:5px;
            padding:.55rem .7rem; margin:.8rem 0 0; overflow-x:auto;
            font-size:.8rem; color:#c8d6c8; }}
 code {{ background:#242424; padding:.05rem .3rem; border-radius:3px; }}
 .caveat {{ border-left:3px solid #6b5a2a; background:#1e1a12; padding:.7rem 1rem;
            margin:1.2rem 0; color:#d8d0b8; max-width:78ch; }}
</style>
<h1>Window candidates</h1>
<p class="meta">Generated {stamp} &middot; repo commit <code>{commit}</code>
 &middot; PNGs and <code>candidates.json</code> in
 <code>{data_dir}</code></p>

<div class="caveat">
 <b>These are not renders.</b> Each picture is the shipped raster pipeline run
 as far as the coastline and stopped: real Natural Earth 50m coastline, the
 shipped 4/3 feature-width simplification, and the 700-disc cover a generated
 <code>.rms</code> is built from. There is no forest, elevation, resource or
 start placement here and no engine RNG, because none of that is decided by
 the window. Nothing on this page says whether a window is good &mdash; that
 is the thing to judge by eye. Once windows are picked, they get generated for
 real and captured out of the engine.
</div>

{''.join(sections)}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default=None,
                    help="comma-separated subset of "
                         + ",".join(GROUP_TITLES))
    ap.add_argument("--tag", default="window_candidates")
    args = ap.parse_args()

    wanted = ({g.strip() for g in args.groups.split(",")}
              if args.groups else set(GROUP_TITLES))
    cands = [c for c in CANDIDATES if c.group in wanted]

    stamp_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    data_dir = REPO / "reports" / f"{stamp_id}_{args.tag}_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    t0 = time.time()
    for i, c in enumerate(cands, 1):
        t1 = time.time()
        rows.append(screen(c, data_dir))
        print(f"[{i}/{len(cands)}] {c.group}/{c.name}: "
              f"land {rows[-1]['land_fraction']*100:.1f}%, "
              f"IoU {rows[-1]['cover_iou']:.3f}, "
              f"{time.time()-t1:.1f}s")

    lean = [{k: v for k, v in r.items() if k != "b64"} for r in rows]
    (data_dir / "candidates.json").write_text(
        json.dumps(lean, indent=2), encoding="utf-8")

    html = render_html(
        rows,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        git_commit(),
        str(data_dir.relative_to(REPO)).replace("\\", "/"),
    )
    out = REPO / "reports" / f"{stamp_id}_{args.tag}.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n{len(rows)} candidates in {time.time()-t0:.0f}s")
    print(f"-> {out}")
    print(f"-> {data_dir}")


if __name__ == "__main__":
    main()
