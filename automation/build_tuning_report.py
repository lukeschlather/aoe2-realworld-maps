"""Fast report template over tuning_matrix.py's precomputed results.jsonl.

Deliberately does NOT touch AoE2ScenarioParser or re-derive anything - every
preview image and every fact (placement geometry, resource ownership) was
already computed once, immediately after capture, by sample_analysis.py.
This script only reads JSON and fills in HTML, so it should run in well
under a second regardless of how many samples are in the file.

Usage:
    uv run python automation/build_tuning_report.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from tuning_matrix import WINDOWS, conditions_for  # noqa: E402

RESULTS_PATH = REPO / "out" / "tuning_matrix" / "results.jsonl"

RESOURCE_KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]


def load_records() -> dict:
    """(window, condition) -> list of records, in capture order."""
    by_cell = defaultdict(list)
    if not RESULTS_PATH.exists():
        return by_cell
    for line in RESULTS_PATH.open(encoding="utf-8"):
        rec = json.loads(line)
        by_cell[(rec["window"], rec["condition"])].append(rec)
    return by_cell


def resource_table(per_player: dict) -> str:
    rows = ["<table class='res'><tr><th>P</th>" +
            "".join(f"<th>{k}</th>" for k in RESOURCE_KINDS) + "</tr>"]
    for p in sorted(per_player, key=int):
        counts = per_player[p]
        cells = []
        for k in RESOURCE_KINDS:
            n = counts.get(k, 0)
            cls = " class='zero'" if n == 0 else (" class='low'" if n == 1 else "")
            cells.append(f"<td{cls}>{n}</td>")
        rows.append(f"<tr><td>{p}</td>{''.join(cells)}</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def sample_card(rec: dict) -> str:
    placement = rec["placement"]
    resources = rec["resources"]
    any_zero = resources["any_player_zero_of_a_kind"]
    flag = (f"<span class='flag bad'>player {', '.join(resources['zero_kinds_by_player'])} "
            f"has zero of a kind</span>" if any_zero else "<span class='flag ok'>no zero-of-a-kind</span>")
    return f'''
    <div class="sample">
        <img src="{rec['preview_png_b64']}" alt="real engine capture" loading="lazy">
        <div class="facts">
            <div class="fact-row">
                <span>{rec['n_tcs']} TCs</span>
                <span>land {rec['land_pct']:.0f}%</span>
                <span>min sep {placement['min_tc_separation']}</span>
            </div>
            <div class="fact-row">
                <span>{placement['n_landmasses_with_a_player']} landmass(es) w/ players</span>
                <span>{placement['pairwise_land_reachable_fraction']*100:.0f}% pairs land-reachable</span>
            </div>
            {flag}
            {resource_table(resources['per_player'])}
        </div>
    </div>'''


def main():
    by_cell = load_records()
    window_sections = []
    for win_key, win_title, lon, lat, span, rot in WINDOWS:
        cond_blocks = []
        for cond_key, extra_args in conditions_for(win_key):
            records = by_cell.get((win_key, cond_key), [])
            args_str = " ".join(extra_args) if extra_args else "(none)"
            if not records:
                cond_blocks.append(f'''
                <div class="cond">
                    <h3>{cond_key}</h3>
                    <p class="cond-sub">{args_str}</p>
                    <p class="missing">no captures</p>
                </div>''')
                continue
            samples_html = "\n".join(sample_card(r) for r in records)
            cond_blocks.append(f'''
            <div class="cond">
                <h3>{cond_key}</h3>
                <p class="cond-sub">{args_str}</p>
                <div class="samples">{samples_html}</div>
            </div>''')

        window_sections.append(f'''
        <section class="window">
            <div class="window-head">
                <h2>{win_title}</h2>
                <p class="window-sub">{lon}, {lat} &middot; {span} km span &middot; rotate {rot}</p>
            </div>
            <div class="conditions">
                {"".join(cond_blocks)}
            </div>
        </section>''')

    html = HTML_TEMPLATE.format(sections="\n".join(window_sections))
    out = REPO / "reports" / "tuning_matrix_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


HTML_TEMPLATE = """<title>Puget Sound: parameter tuning matrix (real engine)</title>
<style>
:root {{
  --bg: #eef2f0; --bg-elevated: #ffffff; --ink: #16211d; --ink-dim: #4d635c;
  --card-bg: #dfe8e4; --accent: #c96f2e; --rule: #c7d3ce;
  --good: #2f7a4f; --good-bg: #dcefe1; --bad: #a13f3a; --bad-bg: #f3dedb;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #10161a; --bg-elevated: #161e23; --ink: #eef1ee; --ink-dim: #9fb0ac;
    --card-bg: #1a2227; --accent: #e2934f; --rule: #2a3339;
    --good: #6fbd80; --good-bg: #1b3324; --bad: #d9827c; --bad-bg: #3a2020;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #10161a; --bg-elevated: #161e23; --ink: #eef1ee; --ink-dim: #9fb0ac;
  --card-bg: #1a2227; --accent: #e2934f; --rule: #2a3339;
  --good: #6fbd80; --good-bg: #1b3324; --bad: #d9827c; --bad-bg: #3a2020;
}}
:root[data-theme="light"] {{
  --bg: #eef2f0; --bg-elevated: #ffffff; --ink: #16211d; --ink-dim: #4d635c;
  --card-bg: #dfe8e4; --accent: #c96f2e; --rule: #c7d3ce;
  --good: #2f7a4f; --good-bg: #dcefe1; --bad: #a13f3a; --bad-bg: #f3dedb;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: -apple-system, "Segoe UI", sans-serif;
  padding: 2.5rem 1.5rem 5rem;
}}
.wrap {{ max-width: 1600px; margin: 0 auto; }}
h1 {{
  font-family: Charter, Georgia, serif; font-weight: 600; font-size: 2rem;
  letter-spacing: -0.01em; text-wrap: balance; margin: 0 0 0.3rem;
}}
.lede {{ color: var(--ink-dim); max-width: 74ch; line-height: 1.55; font-size: 1rem; margin: 0 0 2.5rem; }}
.window {{ margin-bottom: 3.5rem; }}
.window-head {{
  border-bottom: 2px solid var(--accent); padding-bottom: 0.6rem; margin-bottom: 1.3rem;
  display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
}}
.window-head h2 {{ font-family: Charter, Georgia, serif; font-weight: 600; font-size: 1.5rem; margin: 0; }}
.window-sub {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.8rem; color: var(--ink-dim); margin: 0;
}}
.conditions {{ display: flex; flex-direction: column; gap: 1rem; }}
.cond {{ background: var(--bg-elevated); border: 1px solid var(--rule); border-radius: 10px; padding: 1rem; }}
.cond h3 {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.95rem; margin: 0 0 0.1rem; color: var(--accent);
}}
.cond-sub {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.72rem; color: var(--ink-dim); margin: 0 0 0.7rem;
}}
.samples {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
.sample {{ display: flex; gap: 0.7rem; background: var(--card-bg); border-radius: 8px; padding: 0.6rem; }}
.sample img {{ width: 200px; height: 200px; border-radius: 5px; flex: none; object-fit: cover; }}
.facts {{ font-size: 0.78rem; min-width: 210px; }}
.fact-row {{ display: flex; gap: 0.7rem; margin-bottom: 0.3rem; color: var(--ink-dim);
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; font-variant-numeric: tabular-nums; }}
.flag {{ display: inline-block; font-size: 0.72rem; padding: 0.1rem 0.5rem; border-radius: 999px; margin-bottom: 0.4rem; }}
.flag.ok {{ background: var(--good-bg); color: var(--good); }}
.flag.bad {{ background: var(--bad-bg); color: var(--bad); }}
table.res {{ border-collapse: collapse; font-size: 0.68rem; font-variant-numeric: tabular-nums; }}
table.res th, table.res td {{ border: 1px solid var(--rule); padding: 1px 4px; text-align: center; }}
table.res th {{ color: var(--ink-dim); font-weight: 500; }}
table.res td.zero {{ background: var(--bad-bg); color: var(--bad); font-weight: 600; }}
table.res td.low {{ color: var(--accent); }}
.missing {{ color: var(--ink-dim); font-style: italic; font-size: 0.85rem; }}
footer {{ color: var(--ink-dim); font-size: 0.8rem; margin-top: 3rem; line-height: 1.6; }}
</style>
<div class="wrap">
  <h1>Puget Sound: parameter tuning matrix</h1>
  <p class="lede">
    Every image is a real AoE2 DE engine capture; every fact next to it
    (Town Centre separation, which landmass each player is on, resource
    counts per player) was computed directly from that same capture's own
    terrain grid and unit placement right after it was generated - not a
    Python approximation, not a deferred re-parse. "Landmass(es) w/
    players" and "% pairs land-reachable" are geography facts, not a
    fairness verdict - a real-world coastline map often has players on
    separate islands by design. The only thing flagged as a problem is a
    player having literally zero of some resource kind.
  </p>
  {sections}
  <footer>
    Built by build_tuning_report.py from tuning_matrix.py's results.jsonl -
    no scenario re-parsing happens at report-build time.
  </footer>
</div>
"""

if __name__ == "__main__":
    main()
