"""Fast report template over tuning_matrix.py's precomputed results.jsonl.

Deliberately does NOT touch AoE2ScenarioParser or re-derive anything - every
preview image and every fact (placement geometry, resource ownership) was
already computed once, immediately after capture, by sample_analysis.py.
This script only reads JSON and fills in HTML, plus a cheap file-copy step
to archive the underlying .rms/.aoe2scenario into a git-tracked location -
it should run in a couple seconds regardless of how many samples are in
the file.

Every run of tuning_matrix.py is scoped under a --run-id; pass the same
--run-id here to build that run's own report + archived-data directory,
distinct from every other run's. Omitting --run-id builds the original,
pre-run-id report (reports/tuning_matrix_report.html) for backward
compatibility with the first matrix this repo ever ran.

Usage:
    uv run python automation/build_tuning_report.py
    uv run python automation/build_tuning_report.py --run-id my_sweep --resolution-defaults 50m,110m
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from tuning_matrix import WINDOWS, conditions_for, PARAM_DEFAULTS, resolve_params  # noqa: E402

MATRIX_OUT = REPO / "out" / "tuning_matrix"
RESULTS_PATH = MATRIX_OUT / "results.jsonl"
DATA_DIR = REPO / "reports" / "tuning_matrix_data"
REPORT_PATH = REPO / "reports" / "tuning_matrix_report.html"
REPORT_TITLE = "Puget Sound: parameter tuning matrix"

RESOURCE_KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]


def cell_id(win_key: str, cond_key: str, win_geo: dict, resolved: dict) -> str:
    """Short, deterministic fingerprint of everything that produced this
    cell's renders - the window's own geo (lon/lat/span/rotate) plus the
    complete resolved parameter set, not just what the condition overrode.

    Shared by a cell's .rms and every one of its .aoe2scenario samples, so
    matching IDs across files is a cheap way to confirm they belong
    together even if moved out of their reports/tuning_matrix_data/
    directory structure. Not a secret/security hash - just collision
    avoidance, so 8 hex chars is plenty.
    """
    payload = json.dumps({"window": win_key, "geo": win_geo, "condition": cond_key,
                           "params": resolved}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                            capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def load_records() -> dict:
    """(window, condition) -> list of records, in capture order."""
    by_cell = defaultdict(list)
    if not RESULTS_PATH.exists():
        return by_cell
    for line in RESULTS_PATH.open(encoding="utf-8"):
        rec = json.loads(line)
        by_cell[(rec["window"], rec["condition"])].append(rec)
    return by_cell


def archive_cell_files(win_key: str, cond_key: str, sample_indices: list[int],
                        cid: str) -> tuple[str | None, dict[int, str]]:
    """Copy this cell's .rms script and raw .aoe2scenario captures out of
    gitignored out/ into reports/tuning_matrix_data/, so the report can link
    to files that actually get checked in - not just embed a rendered PNG.

    Filenames bake in window + condition + (for samples) sample index, so
    the file is self-identifying even pulled out of its directory, plus the
    shared ``cid`` fingerprint (see cell_id()) so files that belong to the
    same resolved parameter set are visibly matched to each other.

    ``sample_indices`` are the actual recorded sample_index values (NOT
    necessarily a contiguous 0..n-1 range - a cell where sample 0 failed
    and only sample 1 succeeded has records=[1], and archiving by position
    rather than by this real index would look for the wrong filename).

    Returns (rms_relpath, {sample_index: scenario_relpath}) - paths
    relative to reports/, or None/{} if nothing was found on disk.
    """
    dest_dir = DATA_DIR / win_key / cond_key
    dest_dir.mkdir(parents=True, exist_ok=True)

    rms_relpath = None
    script_root = MATRIX_OUT / "scripts" / win_key / cond_key
    rms_candidates = sorted(script_root.rglob("*.rms"), key=lambda p: p.stat().st_mtime)
    if rms_candidates:
        # Multiple timestamped subdirs can exist if a batch was resumed
        # after a crash - they're byte-identical (deterministic script),
        # so the newest is as good as any.
        newest = rms_candidates[-1]
        rms_filename = f"{win_key}__{cond_key}__{cid}.rms"
        dest = dest_dir / rms_filename
        shutil.copyfile(newest, dest)
        rms_relpath = f"tuning_matrix_data/{win_key}/{cond_key}/{rms_filename}"

    scenario_relpaths = {}
    raw_dir = MATRIX_OUT / win_key / cond_key / "raw"
    for i in sample_indices:
        src = raw_dir / f"sample_{i:03d}.aoe2scenario"
        if src.exists():
            scenario_filename = f"{win_key}__{cond_key}__s{i:03d}__{cid}.aoe2scenario"
            dest = dest_dir / scenario_filename
            shutil.copyfile(src, dest)
            scenario_relpaths[i] = f"tuning_matrix_data/{win_key}/{cond_key}/{scenario_filename}"

    return rms_relpath, scenario_relpaths


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


def sample_card(rec: dict, scenario_relpath: str | None) -> str:
    placement = rec["placement"]
    resources = rec["resources"]
    any_zero = resources["any_player_zero_of_a_kind"]
    flag = (f"<span class='flag bad'>player {', '.join(resources['zero_kinds_by_player'])} "
            f"has zero of a kind</span>" if any_zero else "<span class='flag ok'>no zero-of-a-kind</span>")
    if scenario_relpath:
        filename = scenario_relpath.rsplit("/", 1)[-1]
        scenario_link = f'<a class="filelink" href="{scenario_relpath}">{filename}</a>'
    else:
        scenario_link = '<span class="filelink missing-link">.aoe2scenario missing</span>'
    return f'''
    <div class="sample">
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
            <div class="fact-row">{scenario_link}</div>
        </div>
        <img src="{rec['preview_png_b64']}" alt="real engine capture" loading="lazy">
    </div>'''


def params_table(resolved: dict) -> str:
    cells = "".join(f"<td>{k}</td><td>{v}</td>" for k, v in resolved.items())
    return f"<table class='params'><tr>{cells}</tr></table>"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", default=None,
                    help="build this run's own report/data dir instead of the "
                         "original default-named ones (out/tuning_matrix/<run-id>/, "
                         "reports/tuning_matrix_report_<run-id>.html, "
                         "reports/tuning_matrix_data_<run-id>/)")
    p.add_argument("--resolution-defaults", default="10m",
                    help="comma-separated resolution_default values to build "
                         "sections for, e.g. 50m,110m - passed straight through "
                         "to conditions_for()")
    return p.parse_args()


def main():
    global MATRIX_OUT, RESULTS_PATH, DATA_DIR, REPORT_PATH, REPORT_TITLE
    args = parse_args()
    resolution_defaults = [r.strip() for r in args.resolution_defaults.split(",") if r.strip()]
    # Timestamp leads the filename (not a suffix after a descriptive name) so
    # every report under reports/ sorts chronologically regardless of what
    # descriptive text a given run picks.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if args.run_id:
        MATRIX_OUT = REPO / "out" / "tuning_matrix" / args.run_id
        RESULTS_PATH = MATRIX_OUT / "results.jsonl"
        DATA_DIR = REPO / "reports" / f"tuning_matrix_data_{args.run_id}"
        REPORT_PATH = REPO / "reports" / f"{stamp}_tuning_matrix_report_{args.run_id}.html"
        REPORT_TITLE = f"Puget Sound: parameter tuning matrix - run {args.run_id}"
    else:
        REPORT_PATH = REPO / "reports" / f"{stamp}_tuning_matrix_report.html"

    by_cell = load_records()
    window_sections = []
    for win_key, win_title, lon, lat, span, rot in WINDOWS:
        win_geo = {"lon": lon, "lat": lat, "span": span, "rotate": rot}
        sweep_blocks = []
        for resolution_default in resolution_defaults:
            cond_blocks = []
            for cond_key, extra_args in conditions_for(win_key, resolution_default):
                records = by_cell.get((win_key, cond_key), [])
                resolved = resolve_params(extra_args)
                cid = cell_id(win_key, cond_key, win_geo, resolved)
                id_badge = f'<span class="cond-id" title="fingerprint of this cell\'s full window geo + resolved params">ID <code>{cid}</code></span>'
                sample_indices = [r["sample_index"] for r in records]
                rms_relpath, scenario_relpaths = archive_cell_files(win_key, cond_key, sample_indices, cid)
                if rms_relpath:
                    rms_filename = rms_relpath.rsplit("/", 1)[-1]
                    rms_link = f'<a class="filelink" href="{rms_relpath}">{rms_filename}</a>'
                else:
                    rms_link = '<span class="filelink missing-link">.rms missing</span>'

                if not records:
                    cond_blocks.append(f'''
                    <div class="cond">
                        <div class="cond-head"><h3>{cond_key}</h3>{id_badge}</div>
                        {params_table(resolved)}
                        <p class="missing">no captures</p>
                    </div>''')
                    continue
                samples_html = "\n".join(
                    sample_card(r, scenario_relpaths.get(r["sample_index"]))
                    for r in records
                )
                cond_blocks.append(f'''
                <div class="cond">
                    <div class="cond-head"><h3>{cond_key}</h3>{id_badge}</div>
                    {params_table(resolved)}
                    <p class="cond-sub">{rms_link}</p>
                    <div class="samples">{samples_html}</div>
                </div>''')

            if len(resolution_defaults) > 1:
                sweep_blocks.append(f'''
                <div class="sweep">
                    <h4 class="sweep-head">resolution default: {resolution_default}</h4>
                    <div class="conditions">{"".join(cond_blocks)}</div>
                </div>''')
            else:
                sweep_blocks.append(f'<div class="conditions">{"".join(cond_blocks)}</div>')

        window_sections.append(f'''
        <section class="window">
            <div class="window-head">
                <h2>{win_title}</h2>
                <p class="window-sub">{lon}, {lat} &middot; {span} km span &middot; rotate {rot}</p>
            </div>
            {"".join(sweep_blocks)}
        </section>''')

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = HTML_TEMPLATE.format(
        title=REPORT_TITLE,
        sections="\n".join(window_sections),
        generated_at=generated_at,
        commit=git_commit(),
        param_defaults_row="".join(f"<td>{k}</td><td>{v}</td>" for k, v in PARAM_DEFAULTS.items()),
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(f"wrote {REPORT_PATH} ({REPORT_PATH.stat().st_size / 1024:.0f} KB)")
    print(f"archived data under {DATA_DIR}")


HTML_TEMPLATE = """<title>{title} (real engine)</title>
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
.meta {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.78rem; color: var(--ink-dim); margin: 0 0 1.2rem;
}}
.lede {{ color: var(--ink-dim); max-width: 74ch; line-height: 1.55; font-size: 1rem; margin: 0 0 1.2rem; }}
.defaults {{
  background: var(--bg-elevated); border: 1px solid var(--rule); border-radius: 8px;
  padding: 0.8rem 1rem; margin-bottom: 2.5rem; overflow-x: auto;
}}
.defaults p {{ margin: 0 0 0.5rem; font-size: 0.85rem; color: var(--ink-dim); }}
table.params {{
  border-collapse: collapse; font-size: 0.68rem; margin: 0.5rem 0;
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}}
table.params td {{ border: 1px solid var(--rule); padding: 2px 6px; }}
table.params td:nth-child(odd) {{ color: var(--ink-dim); }}
table.params td:nth-child(even) {{ font-weight: 600; }}
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
.sweep {{ margin-bottom: 1.6rem; }}
.sweep-head {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.85rem; color: var(--accent); margin: 0 0 0.6rem;
  text-transform: uppercase; letter-spacing: 0.03em;
}}
.cond {{ background: var(--bg-elevated); border: 1px solid var(--rule); border-radius: 10px; padding: 1rem; overflow-x: auto; }}
.cond-head {{ display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; margin-bottom: 0.4rem; }}
.cond h3 {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.95rem; margin: 0; color: var(--accent);
}}
.cond-id {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.78rem; color: var(--ink-dim); background: var(--card-bg);
  border: 1px solid var(--rule); border-radius: 999px; padding: 0.15rem 0.7rem;
}}
.cond-id code {{ color: var(--ink); font-weight: 600; }}
.cond-sub {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.72rem; color: var(--ink-dim); margin: 0.4rem 0 0.7rem;
}}
.samples {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
.sample {{ display: flex; flex-direction: column; gap: 0.5rem; background: var(--card-bg); border-radius: 8px; padding: 0.6rem; width: 400px; }}
.sample img {{ width: 400px; height: 400px; border-radius: 5px; flex: none; object-fit: cover; }}
.facts {{ font-size: 0.78rem; }}
.fact-row {{ display: flex; flex-wrap: wrap; gap: 0.4rem 0.7rem; margin-bottom: 0.3rem; color: var(--ink-dim);
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; font-variant-numeric: tabular-nums; }}
.fact-row .filelink {{ overflow-wrap: anywhere; }}
.flag {{ display: inline-block; font-size: 0.72rem; padding: 0.1rem 0.5rem; border-radius: 999px; margin-bottom: 0.4rem; }}
.flag.ok {{ background: var(--good-bg); color: var(--good); }}
.flag.bad {{ background: var(--bad-bg); color: var(--bad); }}
table.res {{ border-collapse: collapse; font-size: 0.68rem; font-variant-numeric: tabular-nums; }}
table.res th, table.res td {{ border: 1px solid var(--rule); padding: 1px 4px; text-align: center; }}
table.res th {{ color: var(--ink-dim); font-weight: 500; }}
table.res td.zero {{ background: var(--bad-bg); color: var(--bad); font-weight: 600; }}
table.res td.low {{ color: var(--accent); }}
.filelink {{ color: var(--accent); text-decoration: none; font-size: 0.72rem;
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace; }}
.filelink:hover {{ text-decoration: underline; }}
.filelink.missing-link {{ color: var(--ink-dim); font-style: italic; }}
.missing {{ color: var(--ink-dim); font-style: italic; font-size: 0.85rem; }}
footer {{ color: var(--ink-dim); font-size: 0.8rem; margin-top: 3rem; line-height: 1.6; }}
</style>
<div class="wrap">
  <h1>{title}</h1>
  <p class="meta">Generated {generated_at} &middot; repo commit <code>{commit}</code></p>
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
  <div class="defaults">
    <p>Defaults every condition starts from (a condition's own table below shows what it actually overrode):</p>
    <table class="params"><tr>{param_defaults_row}</tr></table>
  </div>
  {sections}
  <footer>
    Built by build_tuning_report.py from tuning_matrix.py's results.jsonl -
    no scenario re-parsing happens at report-build time. Each condition's
    .rms script and every sample's .aoe2scenario are archived under
    reports/tuning_matrix_data/ and linked above.
  </footer>
</div>
"""

if __name__ == "__main__":
    main()
