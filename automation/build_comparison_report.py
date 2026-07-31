"""Build a visual, real-engine-render comparison report across tuning
variants of the same window, so quality can be judged by looking at actual
captures rather than trusting summary statistics alone.

Renders every real .aoe2scenario capture in each named batch as a small
real coastline image (no Python approximation), tags it with the measured
Elliott Bay connectivity state, and lays batches out as labeled contact
sheets next to a legend map showing where each named feature actually is.

Usage: edit BATCHES/WINDOW/FEATURE_POINTS below and run
    uv run python automation/build_comparison_report.py
"""

from __future__ import annotations

import base64
import glob
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "automation"))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from rwmaps import scx_read  # noqa: E402
from rwmaps.analysis import DOCKABLE_WATER_TILES, components  # noqa: E402
from rwmaps.projection import MapWindow  # noqa: E402
from water_navigability import DEFAULT_POINTS, lonlat_to_tile  # noqa: E402

WINDOW = dict(center="-122.6,47.8", span_km=130, size=220, rotate=0)

BATCHES = [
    ("Baseline", "default clumping, overlap 1.0, no consolidation", [
        "out/cf_test/loop-20260730-174645/seattle_cf8_220.aoe2scenario",
        *sorted(glob.glob("out/nav_probe/loop-*/seattle_nav_probe_220.aoe2scenario")),
    ]),
    ("+ Consolidate narrow features", "min-water-width 9, min-land-width 6", [
        *sorted(glob.glob("out/nav_probe_simplified/loop-*/seattle_simplified_220.aoe2scenario")),
    ]),
    ("+ Consolidate, overlap 0.72", "min-water-width 9, min-land-width 6, overlap 0.72", [
        *sorted(glob.glob("out/nav_probe_overlap072/seattle_overlap072_*.aoe2scenario")),
    ]),
]

FOCUS_FEATURE = "Elliott Bay"

SEA = (28, 61, 92)
LAND = (94, 122, 84)

def _font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_mask(mask: np.ndarray, px: int = 260) -> Image.Image:
    size = mask.shape[0]
    scale = max(1, px // size)
    rgb = np.zeros(mask.shape + (3,), np.uint8)
    rgb[...] = SEA
    rgb[mask] = LAND
    return Image.fromarray(rgb).resize((size * scale, size * scale), Image.NEAREST)


def png_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def build_legend(window, first_capture: Path) -> str:
    mask = scx_read.read_land_mask(first_capture)
    img = render_mask(mask, px=520)
    scale = img.width / mask.shape[0]
    d = ImageDraw.Draw(img)
    f = _font(15)
    colors = ["#e2934f", "#7fc8a9", "#e2934f", "#e2934f", "#7fc8a9"]
    for i, (label, (lon, lat)) in enumerate(DEFAULT_POINTS.items()):
        if label in ("Hood Canal (south)",):
            continue
        row, col = lonlat_to_tile(window, lon, lat)
        if not (0 <= row < mask.shape[0] and 0 <= col < mask.shape[1]):
            continue
        cx, cy = col * scale, row * scale
        r = 7
        c = colors[i % len(colors)]
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=3)
        d.text((cx + r + 5, cy - 9), label, fill="#eef1ee", font=f,
               stroke_width=2, stroke_fill="#10161a")
    return png_data_uri(img)


def main():
    lon, lat = (float(v) for v in WINDOW["center"].split(","))
    window = MapWindow.from_center("laea", lon, lat, WINDOW["span_km"], WINDOW["size"], WINDOW["rotate"])
    focus_row, focus_col = lonlat_to_tile(window, *DEFAULT_POINTS[FOCUS_FEATURE])

    all_files = [f for _, _, files in BATCHES for f in files]
    legend_uri = build_legend(window, Path(all_files[-1]))

    sections = []
    for label, subtitle, files in BATCHES:
        cards = []
        n_dockable = 0
        for f in files:
            mask = scx_read.read_land_mask(f)
            water = ~mask
            labels_grid, sizes = components(water)
            comp = int(labels_grid[focus_row, focus_col])
            tiles = int(sizes[comp]) if comp > 0 else 0
            dockable = tiles >= DOCKABLE_WATER_TILES
            n_dockable += dockable
            img = render_mask(mask, px=240)
            cards.append((png_data_uri(img), tiles, dockable))
        rate = 100 * n_dockable / len(files) if files else 0
        cards_html = "\n".join(
            f'''<figure class="card">
                <img src="{uri}" alt="real engine capture" loading="lazy">
                <figcaption class="{'ok' if ok else 'no'}">
                    <span class="dot"></span>{FOCUS_FEATURE}: {tiles:,} tiles
                </figcaption>
            </figure>'''
            for uri, tiles, ok in cards
        )
        sections.append(f'''
        <section class="batch">
            <div class="batch-head">
                <h2>{label}</h2>
                <p class="subtitle">{subtitle}</p>
                <div class="rate-pill {'good' if rate >= 90 else ('warn' if rate >= 40 else 'bad')}">
                    {rate:.0f}% <span>{FOCUS_FEATURE.lower()} navigable</span>
                    <span class="n">n={len(files)}</span>
                </div>
            </div>
            <div class="grid">
                {cards_html}
            </div>
        </section>''')

    html = HTML_TEMPLATE.format(legend=legend_uri, sections="\n".join(sections),
                                 focus=FOCUS_FEATURE)
    out = REPO / "out" / "comparison_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    return out


HTML_TEMPLATE = """<title>Puget Sound tuning: real-engine comparison</title>
<style>
:root {{
  --bg: #eef2f0;
  --bg-elevated: #ffffff;
  --ink: #16211d;
  --ink-dim: #4d635c;
  --card-bg: #dfe8e4;
  --accent: #c96f2e;
  --accent-dim: #e2934f;
  --good: #2f7a4f;
  --good-bg: #dcefe1;
  --warn: #9a6b1e;
  --warn-bg: #f5e8cf;
  --bad: #a13f3a;
  --bad-bg: #f3dedb;
  --rule: #c7d3ce;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #10161a;
    --bg-elevated: #161e23;
    --ink: #eef1ee;
    --ink-dim: #9fb0ac;
    --card-bg: #1a2227;
    --accent: #e2934f;
    --accent-dim: #8a5a30;
    --good: #6fbd80;
    --good-bg: #1b3324;
    --warn: #d9ad5e;
    --warn-bg: #3a2f16;
    --bad: #d9827c;
    --bad-bg: #3a2020;
    --rule: #2a3339;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #10161a; --bg-elevated: #161e23; --ink: #eef1ee; --ink-dim: #9fb0ac;
  --card-bg: #1a2227; --accent: #e2934f; --accent-dim: #8a5a30;
  --good: #6fbd80; --good-bg: #1b3324; --warn: #d9ad5e; --warn-bg: #3a2f16;
  --bad: #d9827c; --bad-bg: #3a2020; --rule: #2a3339;
}}
:root[data-theme="light"] {{
  --bg: #eef2f0; --bg-elevated: #ffffff; --ink: #16211d; --ink-dim: #4d635c;
  --card-bg: #dfe8e4; --accent: #c96f2e; --accent-dim: #e2934f;
  --good: #2f7a4f; --good-bg: #dcefe1; --warn: #9a6b1e; --warn-bg: #f5e8cf;
  --bad: #a13f3a; --bad-bg: #f3dedb; --rule: #c7d3ce;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: -apple-system, "Segoe UI", sans-serif;
  padding: 2.5rem 1.5rem 5rem;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; }}
h1 {{
  font-family: Charter, Georgia, serif;
  font-weight: 600; font-size: 2.1rem; letter-spacing: -0.01em;
  text-wrap: balance; margin: 0 0 0.3rem;
}}
.lede {{
  color: var(--ink-dim); max-width: 62ch; line-height: 1.55; font-size: 1.02rem;
  margin: 0 0 2.2rem;
}}
h2 {{
  font-family: Charter, Georgia, serif; font-weight: 600;
  font-size: 1.35rem; margin: 0 0 0.15rem; text-wrap: balance;
}}
.legend-block {{
  display: flex; gap: 1.8rem; align-items: flex-start; flex-wrap: wrap;
  background: var(--bg-elevated); border: 1px solid var(--rule);
  border-radius: 10px; padding: 1.4rem; margin-bottom: 2.8rem;
}}
.legend-block img {{
  width: 320px; max-width: 100%; border-radius: 6px; display: block;
  border: 1px solid var(--rule);
}}
.legend-text {{ flex: 1; min-width: 240px; }}
.legend-text h2 {{ margin-bottom: 0.5rem; }}
.legend-text p {{ color: var(--ink-dim); line-height: 1.6; font-size: 0.95rem; }}
.legend-text code {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  background: var(--card-bg); padding: 0.1em 0.4em; border-radius: 4px;
  font-size: 0.88em;
}}
.batch {{ margin-bottom: 3.2rem; }}
.batch-head {{
  display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
  border-bottom: 1px solid var(--rule); padding-bottom: 0.8rem; margin-bottom: 1.1rem;
}}
.subtitle {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  color: var(--ink-dim); font-size: 0.82rem; margin: 0; flex: 1 1 auto;
}}
.rate-pill {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 1.15rem; font-weight: 600; padding: 0.35rem 0.9rem;
  border-radius: 999px; white-space: nowrap;
}}
.rate-pill span {{ font-size: 0.65rem; font-weight: 500; text-transform: uppercase;
  letter-spacing: 0.04em; opacity: 0.75; margin-left: 0.35rem; }}
.rate-pill .n {{ margin-left: 0.6rem; opacity: 0.6; }}
.rate-pill.good {{ background: var(--good-bg); color: var(--good); }}
.rate-pill.warn {{ background: var(--warn-bg); color: var(--warn); }}
.rate-pill.bad {{ background: var(--bad-bg); color: var(--bad); }}
.grid {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.9rem;
}}
.card {{
  margin: 0; background: var(--card-bg); border: 1px solid var(--rule);
  border-radius: 8px; overflow: hidden;
}}
.card img {{ display: block; width: 100%; height: auto; }}
.card figcaption {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 0.72rem; padding: 0.4rem 0.55rem; display: flex; align-items: center; gap: 0.4rem;
}}
.card .dot {{ width: 8px; height: 8px; border-radius: 50%; flex: none; }}
.card figcaption.ok {{ color: var(--good); }}
.card figcaption.ok .dot {{ background: var(--good); }}
.card figcaption.no {{ color: var(--bad); }}
.card figcaption.no .dot {{ background: var(--bad); }}
footer {{ color: var(--ink-dim); font-size: 0.82rem; margin-top: 3rem; line-height: 1.6; }}
</style>
<div class="wrap">
  <h1>Puget Sound: does the tuning actually help?</h1>
  <p class="lede">
    Every image below is a real AoE2 DE engine render (Scenario Editor
    "Generate Map"), not a Python approximation - one independent
    generation per thumbnail, same window each time, only the engine's own
    random seed differs. The three groups are the same coastline window run
    through three successive tuning changes. Judge by looking, not by
    trusting the percentage.
  </p>

  <div class="legend-block">
    <img src="{legend}" alt="labeled reference map">
    <div class="legend-text">
      <h2>What to look for</h2>
      <p>
        Amber pins mark <strong>Elliott Bay</strong> and <strong>the Strait
        entrance</strong> - features this tuning pass tried to keep open.
        Teal pins mark <strong>Hood Canal</strong> and <strong>Sinclair
        Inlet</strong> - features deliberately consolidated into land, since
        they're too narrow (a few tiles) to render reliably at this
        resolution regardless of tuning. Reference map is drawn from one of
        the real captures below, not a synthetic preview.
      </p>
      <p>
        Each thumbnail's caption reads the connected water region under the
        {focus} pin directly from that capture's terrain grid - <code>&ge;
        400 tiles</code> counts as genuinely open, dockable water.
      </p>
    </div>
  </div>

  {sections}

  <footer>
    Tile counts read directly from each captured <code>.aoe2scenario</code>'s
    terrain grid via <code>scx_read</code> - not estimated, not simulated.
    Window: Puget Sound, center -122.6, 47.8, 130&nbsp;km span, 220&times;220
    tiles (Large [8p] [220]).
  </footer>
</div>
"""

if __name__ == "__main__":
    main()
