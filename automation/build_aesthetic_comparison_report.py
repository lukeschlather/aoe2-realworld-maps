"""Build a 50m-only report grouping conditions into direct comparisons -
e.g. clumping factor 4 vs. 8 vs. 16 side by side for the same window - with
the differing settings highlighted and the aesthetic-recognizability
metrics from aesthetic_metrics.py shown per map, not just a linked file.

Reads the same reports/tuning_matrix_data_res_default_sweep/ archive and
out/tuning_matrix/res_default_sweep/results.jsonl that build_tuning_report.py
--run-id res_default_sweep produces - run that first if this can't find
data. Never touches AoE2ScenarioParser; re-derives the real land mask for
each shown sample straight from its already-archived .aoe2scenario via
scx_read (cheap, no engine time) so the metrics can be computed here rather
than needing them precomputed into results.jsonl.

Deliberately 110m-free: 110m was already established as uniformly worse
(automation/aesthetic_metrics.py, the res_default_sweep discussion) and
diluting a comparison-focused report with a resolution nobody is
considering for these windows just adds noise.

Usage:
    uv run python automation/build_aesthetic_comparison_report.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from rwmaps import scx_read  # noqa: E402
from tuning_matrix import WINDOWS, conditions_for, resolve_params  # noqa: E402
from build_tuning_report import cell_id  # noqa: E402
from aesthetic_metrics import compute_metrics  # noqa: E402

RUN_ID = "res_default_sweep"
RESULTS_PATH = REPO / "out" / "tuning_matrix" / RUN_ID / "results.jsonl"
DATA_DIR = REPO / "reports" / "tuning_matrix_data_res_default_sweep"

#: (group title, [condition keys in display order]) - all "_r50m" since this
#: report is 110m-free. Every group but the last holds every OTHER resolved
#: param fixed and varies exactly one axis; the last one is the two-setting
#: jump (consolidation width + overlap together) from bare rwmaps defaults
#: to the "good baseline" every other condition here builds on.
COMPARE_GROUPS = [
    ("Consolidation width: light -> default -> heavy (overlap 1.0)",
     ["consolidate_light_overlap1.0_r50m", "consolidate_default_overlap1.0_r50m",
      "consolidate_heavy_overlap1.0_r50m"]),
    ("Overlap: 1.0 -> 0.85 -> 0.72 (default consolidation width)",
     ["consolidate_default_overlap1.0_r50m", "consolidate_overlap0.85_r50m",
      "consolidate_overlap0.72_r50m"]),
    ("Clumping factor: 4 -> 8 -> 16",
     ["clumping_4_r50m", "consolidate_overlap0.85_r50m", "clumping_16_r50m"]),
    ("Max radius: 8 -> 12 -> 18",
     ["max_radius_8_r50m", "consolidate_overlap0.85_r50m", "max_radius_18_r50m"]),
    ("Lands budget: 350 -> 700 -> 1200",
     ["lands_low_r50m", "consolidate_overlap0.85_r50m", "lands_high_r50m"]),
    ("Min island tiles: 0 -> 16 -> 64",
     ["consolidate_overlap0.85_r50m", "min_island_16_r50m", "min_island_64_r50m"]),
    ("Raw rwmaps defaults vs. the recommended baseline",
     ["baseline_r50m", "consolidate_overlap0.85_r50m"]),
]

RESOURCE_KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]


def load_records() -> dict:
    by_cell = defaultdict(list)
    for line in RESULTS_PATH.open(encoding="utf-8"):
        rec = json.loads(line)
        by_cell[(rec["window"], rec["condition"])].append(rec)
    for key in by_cell:
        by_cell[key].sort(key=lambda r: r["sample_index"])
    return by_cell


def resolved_map(win_key: str) -> dict:
    return {ck: resolve_params(args) for ck, args in conditions_for(win_key, "50m")}


def differing_keys(resolved_list: list[dict]) -> set:
    keys = resolved_list[0].keys()
    diff = set()
    for k in keys:
        vals = {json.dumps(r[k], sort_keys=True) for r in resolved_list}
        if len(vals) > 1:
            diff.add(k)
    return diff


def params_table(resolved: dict, highlight: set) -> str:
    cells = []
    for k, v in resolved.items():
        cls = " class='hl'" if k in highlight else ""
        cells.append(f"<td{cls}>{k}</td><td{cls}>{v}</td>")
    return f"<table class='params'><tr>{''.join(cells)}</tr></table>"


def metrics_row(m: dict) -> str:
    pf = m["preserved_fraction"]
    pf_str = f"{pf:.2f}" if pf == pf else "n/a"  # NaN check
    return f'''<div class="metrics">
        <div class="metric"><span class="mv">{m['iou_10m']:.3f}</span><span class="ml">IoU vs 10m truth</span></div>
        <div class="metric"><span class="mv">{m['bnd_ratio']:.2f}</span><span class="ml">boundary ratio</span></div>
        <div class="metric"><span class="mv">{m['pockmark']:.4f}</span><span class="ml">pockmark score</span></div>
        <div class="metric"><span class="mv">{m['islands_deleted']}/{m['islands_preserved']}/{m['islands_merged']}</span><span class="ml">islands del/pres/merged</span></div>
        <div class="metric{' warn' if pf == pf and pf < 0.3 else ''}"><span class="mv">{pf_str}</span><span class="ml">island preserved-fraction</span></div>
    </div>'''


def find_scenario(win_key: str, cond_key: str, cid: str, sample_index: int) -> Path | None:
    p = DATA_DIR / win_key / cond_key / f"{win_key}__{cond_key}__s{sample_index:03d}__{cid}.aoe2scenario"
    return p if p.exists() else None


#: (window, condition) -> metrics dict - several compare groups share a
#: condition (e.g. consolidate_overlap0.85_r50m is the "good baseline" for
#: 5 of the 7 groups), and each metrics computation re-parses a full
#: .aoe2scenario via AoE2ScenarioParser, which is the slow part of this
#: script - never do that twice for the same cell.
_METRICS_CACHE = {}


def condition_card(win_key: str, cond_key: str, resolved: dict, highlight: set,
                    by_cell: dict) -> str:
    records = by_cell.get((win_key, cond_key), [])
    rid_html = f"<code>{cond_key}</code>"
    if not records:
        return f'''<div class="card">
            <h4>{rid_html}</h4>
            {params_table(resolved, highlight)}
            <p class="missing">no captures</p>
        </div>'''

    rec = records[0]
    cid = cell_id(win_key, cond_key, _win_geo(win_key), resolved)
    scenario_path = find_scenario(win_key, cond_key, cid, rec["sample_index"])
    metrics_html = ""
    link_html = '<span class="filelink missing-link">.aoe2scenario missing</span>'
    if scenario_path:
        cache_key = (win_key, cond_key)
        if cache_key not in _METRICS_CACHE:
            print(f"  parsing {scenario_path.name} ...", flush=True)
            real_mask = scx_read.read_land_mask(scenario_path)
            _METRICS_CACHE[cache_key] = compute_metrics(win_key, real_mask)
        m = _METRICS_CACHE[cache_key]
        metrics_html = metrics_row(m)
        link_html = f'<a class="filelink" href="tuning_matrix_data_res_default_sweep/{win_key}/{cond_key}/{scenario_path.name}">{scenario_path.name}</a>'

    return f'''<div class="card">
        <h4>{rid_html} <span class="cid">{cid}</span></h4>
        <img src="{rec['preview_png_b64']}" alt="real engine capture" loading="lazy">
        {metrics_html}
        {params_table(resolved, highlight)}
        <p class="cond-sub">{link_html}</p>
    </div>'''


_WINDOW_GEO_CACHE = {}


def _win_geo(win_key: str) -> dict:
    if not _WINDOW_GEO_CACHE:
        for wk, _, lon, lat, span, rot in WINDOWS:
            _WINDOW_GEO_CACHE[wk] = {"lon": lon, "lat": lat, "span": span, "rotate": rot}
    return _WINDOW_GEO_CACHE[win_key]


def main():
    by_cell = load_records()
    window_sections = []
    for win_key, win_title, lon, lat, span, rot in WINDOWS:
        print(f"=== {win_key} ===", flush=True)
        resolved_by_cond = resolved_map(win_key)
        group_blocks = []
        for title, cond_keys in COMPARE_GROUPS:
            resolved_list = [resolved_by_cond[ck] for ck in cond_keys]
            highlight = differing_keys(resolved_list)
            cards = "".join(
                condition_card(win_key, ck, resolved_by_cond[ck], highlight, by_cell)
                for ck in cond_keys
            )
            group_blocks.append(f'''
            <div class="group">
                <h3>{title}</h3>
                <div class="cards cards-{len(cond_keys)}">{cards}</div>
            </div>''')

        window_sections.append(f'''
        <section class="window">
            <div class="window-head">
                <h2>{win_title}</h2>
                <p class="window-sub">{lon}, {lat} &middot; {span} km span &middot; rotate {rot}</p>
            </div>
            {"".join(group_blocks)}
        </section>''')

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = HTML_TEMPLATE.format(sections="\n".join(window_sections), generated_at=generated_at)
    # Timestamp leads the filename (not a suffix after a descriptive name) so
    # every report under reports/ sorts chronologically regardless of what
    # descriptive text a future run picks.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = REPO / "reports" / f"{stamp}_aesthetic_comparison_report.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")


HTML_TEMPLATE = """<title>Puget Sound: 50m parameter comparisons + aesthetic metrics</title>
<style>
:root {{
  --bg: #eef2f0; --bg-elevated: #ffffff; --ink: #16211d; --ink-dim: #4d635c;
  --card-bg: #dfe8e4; --accent: #c96f2e; --rule: #c7d3ce;
  --hl-bg: #f6dfc2; --warn: #a13f3a; --warn-bg: #f3dedb;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #10161a; --bg-elevated: #161e23; --ink: #eef1ee; --ink-dim: #9fb0ac;
    --card-bg: #1a2227; --accent: #e2934f; --rule: #2a3339;
    --hl-bg: #4a3418; --warn: #d9827c; --warn-bg: #3a2020;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #10161a; --bg-elevated: #161e23; --ink: #eef1ee; --ink-dim: #9fb0ac;
  --card-bg: #1a2227; --accent: #e2934f; --rule: #2a3339;
  --hl-bg: #4a3418; --warn: #d9827c; --warn-bg: #3a2020;
}}
:root[data-theme="light"] {{
  --bg: #eef2f0; --bg-elevated: #ffffff; --ink: #16211d; --ink-dim: #4d635c;
  --card-bg: #dfe8e4; --accent: #c96f2e; --rule: #c7d3ce;
  --hl-bg: #f6dfc2; --warn: #a13f3a; --warn-bg: #f3dedb;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: -apple-system, "Segoe UI", sans-serif; padding: 2.5rem 1.5rem 5rem; }}
.wrap {{ max-width: 1360px; margin: 0 auto; }}
h1 {{ font-family: Charter, Georgia, serif; font-weight: 600; font-size: 2rem; margin: 0 0 0.3rem; }}
.meta {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; font-size: 0.78rem; color: var(--ink-dim); margin: 0 0 1.2rem; }}
.lede {{ color: var(--ink-dim); max-width: 74ch; line-height: 1.55; margin: 0 0 2rem; }}
.window {{ margin-bottom: 3rem; }}
.window-head {{ border-bottom: 2px solid var(--accent); padding-bottom: 0.6rem; margin-bottom: 1.3rem; display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap; }}
.window-head h2 {{ font-family: Charter, Georgia, serif; font-weight: 600; font-size: 1.5rem; margin: 0; }}
.window-sub {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; font-size: 0.8rem; color: var(--ink-dim); margin: 0; }}
.group {{ margin-bottom: 1.8rem; }}
.group h3 {{ font-family: Charter, Georgia, serif; font-size: 1.05rem; font-weight: 600; margin: 0 0 0.6rem; color: var(--ink); }}
.cards {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
.card {{ background: var(--bg-elevated); border: 1px solid var(--rule); border-radius: 10px; padding: 0.8rem; width: 340px; flex: none; }}
.card h4 {{ margin: 0 0 0.5rem; font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; font-size: 0.85rem; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; }}
.card .cid {{ color: var(--ink-dim); font-size: 0.7rem; font-weight: normal; }}
.card img {{ width: 100%; aspect-ratio: 1; border-radius: 6px; object-fit: cover; margin-bottom: 0.5rem; }}
.metrics {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.5rem; }}
.metric {{ background: var(--card-bg); border-radius: 6px; padding: 0.25rem 0.5rem; font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; display: flex; flex-direction: column; min-width: 84px; }}
.metric.warn {{ background: var(--warn-bg); color: var(--warn); }}
.metric .mv {{ font-size: 0.9rem; font-weight: 600; }}
.metric .ml {{ font-size: 0.62rem; color: var(--ink-dim); }}
.metric.warn .ml {{ color: var(--warn); }}
table.params {{ border-collapse: collapse; font-size: 0.62rem; margin: 0.4rem 0; font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; width: 100%; }}
table.params td {{ border: 1px solid var(--rule); padding: 2px 4px; }}
table.params td.hl {{ background: var(--hl-bg); font-weight: 700; }}
table.params td:nth-child(odd) {{ color: var(--ink-dim); }}
table.params td.hl:nth-child(odd) {{ color: var(--ink); }}
.cond-sub {{ margin: 0.3rem 0 0; }}
.filelink {{ color: var(--accent); text-decoration: none; font-size: 0.68rem; font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; word-break: break-all; }}
.filelink:hover {{ text-decoration: underline; }}
.filelink.missing-link {{ color: var(--ink-dim); font-style: italic; }}
.missing {{ color: var(--ink-dim); font-style: italic; font-size: 0.85rem; }}
</style>
<div class="wrap">
  <h1>Puget Sound: 50m parameter comparisons + aesthetic metrics</h1>
  <p class="meta">Generated {generated_at} &middot; 50m resolution only, source: res_default_sweep</p>
  <p class="lede">
    Each row holds every setting fixed except one (highlighted in each card's
    parameter table) so the visual effect of that one change is directly
    comparable. Metrics per map: IoU against the finest (10m) real coastline
    truth; boundary ratio (coastline detail retained vs. that truth, &lt;1
    means smoother/less detailed); pockmark score (added fractal noise where
    the true coast is smooth); and island fate counts (deleted / preserved /
    merged into the mainland) with the preserved-fraction of non-deleted
    islands flagged when it drops below 0.3 - low means islands are turning
    into peninsulas, the specific failure mode flagged as most objectionable.
    None of this is a verdict - it's context to look at alongside the real
    render, same as always.
  </p>
  {sections}
</div>
"""

if __name__ == "__main__":
    main()
