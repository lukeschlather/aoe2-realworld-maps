"""Build the full visual report for the window x consolidation x overlap
real-engine matrix (window_matrix.py's output): one section per window,
each showing baseline and all consolidation/overlap variants side by side,
each as a grid of actual real engine captures. No Python approximations
anywhere - every image is read from a captured .aoe2scenario's own terrain
grid.

Usage:
    uv run python automation/build_window_matrix_report.py
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from rwmaps import scx_read  # noqa: E402

MATRIX_ROOT = REPO / "out" / "window_matrix"

WINDOWS = [
    ("salish_sea_wide", "Salish Sea Wide", "-122.65, 47.95 · 420 km span · 1.75 km/tile"),
    ("victoria_recenter", "Victoria Recenter", "-122.9, 48.15 · 260 km span · 1.08 km/tile"),
    ("victoria_recenter_tighter", "Victoria Recenter Tighter", "-122.85, 48.05 · 200 km span · 0.83 km/tile"),
    ("west_shift", "West Shift", "-122.85, 47.75 · 130 km span · 0.54 km/tile"),
    ("west_shift_zoomed", "West Shift Zoomed", "-122.8, 47.75 · 95 km span · 0.40 km/tile"),
]

CONDITIONS = [
    ("baseline", "Baseline", "no consolidation, overlap 1.0"),
    ("consolidate_overlap1.0", "Consolidated", "min-water/land-width, overlap 1.0"),
    ("consolidate_overlap0.85", "Consolidated + overlap 0.85", "min-water/land-width, overlap 0.85"),
    ("consolidate_overlap0.72", "Consolidated + overlap 0.72", "min-water/land-width, overlap 0.72"),
]

SEA = (28, 61, 92)
LAND = (94, 122, 84)


def render_mask(mask: np.ndarray, px: int = 230) -> Image.Image:
    size = mask.shape[0]
    scale = max(1, px // size)
    rgb = np.zeros(mask.shape + (3,), np.uint8)
    rgb[...] = SEA
    rgb[mask] = LAND
    return Image.fromarray(rgb).resize((size * scale, size * scale), Image.NEAREST)


def png_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def main():
    window_sections = []
    for win_key, win_title, win_sub in WINDOWS:
        cond_blocks = []
        for cond_key, cond_title, cond_sub in CONDITIONS:
            cell_dir = MATRIX_ROOT / win_key / cond_key
            files = sorted(cell_dir.glob("sample_*.aoe2scenario"))
            if not files:
                cond_blocks.append(f'''
                <div class="cond">
                    <h3>{cond_title}</h3>
                    <p class="cond-sub">{cond_sub}</p>
                    <p class="missing">no captures</p>
                </div>''')
                continue
            land_pcts = []
            cards = []
            for f in files:
                mask = scx_read.read_land_mask(f)
                land_pcts.append(100 * mask.mean())
                img = render_mask(mask)
                cards.append(png_data_uri(img))
            mean_land = sum(land_pcts) / len(land_pcts)
            cards_html = "\n".join(
                f'<img src="{uri}" alt="real engine capture" loading="lazy">' for uri in cards
            )
            cond_blocks.append(f'''
                <div class="cond">
                    <h3>{cond_title}</h3>
                    <p class="cond-sub">{cond_sub} &middot; n={len(files)} &middot; mean land {mean_land:.0f}%</p>
                    <div class="cond-grid">
                        {cards_html}
                    </div>
                </div>''')

        window_sections.append(f'''
        <section class="window">
            <div class="window-head">
                <h2>{win_title}</h2>
                <p class="window-sub">{win_sub}</p>
            </div>
            <div class="conditions">
                {"".join(cond_blocks)}
            </div>
        </section>''')

    html = HTML_TEMPLATE.format(sections="\n".join(window_sections))
    out = REPO / "reports" / "window_matrix_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


HTML_TEMPLATE = """<title>Puget Sound: window x tuning matrix (real engine)</title>
<style>
:root {{
  --bg: #eef2f0;
  --bg-elevated: #ffffff;
  --ink: #16211d;
  --ink-dim: #4d635c;
  --card-bg: #dfe8e4;
  --accent: #c96f2e;
  --rule: #c7d3ce;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #10161a; --bg-elevated: #161e23; --ink: #eef1ee; --ink-dim: #9fb0ac;
    --card-bg: #1a2227; --accent: #e2934f; --rule: #2a3339;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #10161a; --bg-elevated: #161e23; --ink: #eef1ee; --ink-dim: #9fb0ac;
  --card-bg: #1a2227; --accent: #e2934f; --rule: #2a3339;
}}
:root[data-theme="light"] {{
  --bg: #eef2f0; --bg-elevated: #ffffff; --ink: #16211d; --ink-dim: #4d635c;
  --card-bg: #dfe8e4; --accent: #c96f2e; --rule: #c7d3ce;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: -apple-system, "Segoe UI", sans-serif;
  padding: 2.5rem 1.5rem 5rem;
}}
.wrap {{ max-width: 1500px; margin: 0 auto; }}
h1 {{
  font-family: Charter, Georgia, serif; font-weight: 600; font-size: 2rem;
  letter-spacing: -0.01em; text-wrap: balance; margin: 0 0 0.3rem;
}}
.lede {{
  color: var(--ink-dim); max-width: 70ch; line-height: 1.55; font-size: 1rem;
  margin: 0 0 2.5rem;
}}
.window {{ margin-bottom: 3.5rem; }}
.window-head {{
  border-bottom: 2px solid var(--accent); padding-bottom: 0.6rem; margin-bottom: 1.3rem;
  display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
}}
.window-head h2 {{
  font-family: Charter, Georgia, serif; font-weight: 600; font-size: 1.5rem; margin: 0;
}}
.window-sub {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.82rem; color: var(--ink-dim); margin: 0;
}}
.conditions {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.1rem;
}}
.cond {{
  background: var(--bg-elevated); border: 1px solid var(--rule); border-radius: 10px;
  padding: 0.9rem;
}}
.cond h3 {{ font-size: 1rem; margin: 0 0 0.15rem; }}
.cond-sub {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.72rem; color: var(--ink-dim); margin: 0 0 0.7rem;
}}
.cond-grid {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 0.4rem;
}}
.cond-grid img {{
  display: block; width: 100%; border-radius: 5px; border: 1px solid var(--rule);
}}
.missing {{ color: var(--ink-dim); font-style: italic; font-size: 0.85rem; }}
footer {{ color: var(--ink-dim); font-size: 0.8rem; margin-top: 3rem; line-height: 1.6; }}
</style>
<div class="wrap">
  <h1>Puget Sound: 5 windows &times; 4 tuning conditions, real engine only</h1>
  <p class="lede">
    Every thumbnail below is a real AoE2 DE engine render (Scenario Editor
    "Generate Map"), read directly from a captured <code>.aoe2scenario</code>'s
    terrain grid - no Python disc-cover approximation anywhere on this page.
    Each condition shows every sample that was successfully captured (not a
    cherry-picked one), so actual run-to-run variety is visible. All at size
    240 (Huge), 8 players.
  </p>
  {sections}
  <footer>
    Consolidation widths (min-water-width / min-land-width) were chosen per
    window from its own km/tile to target a roughly constant ~5.3km real
    threshold, then only sanity-checked in Python for gross fragmentation
    before spending engine time - the quality judgment itself is made from
    the renders above, not from that check.
  </footer>
</div>
"""

if __name__ == "__main__":
    main()
