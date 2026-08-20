"""Report builder over mod_capture.py's precomputed results.jsonl - one
section per shipped region, N up to 10 samples each, showing both the
fairness facts (TC separation, reachability, resource zero-of-a-kind) and
the aesthetic-recognizability metrics (IoU vs. 10m truth, boundary ratio,
pockmark, island preserved-fraction) side by side, plus min/median/max
across the samples for each numeric metric.

Explicitly focused on explaining *why* each region is generated the way it
is (its resolved settings, why they were chosen), not just surfacing
numbers - see MOD_STATUS.md.

Like build_tuning_report.py, this never touches AoE2ScenarioParser or
Natural Earth data itself - every fact and preview PNG was already
computed once by mod_capture.py right after capture. This only reads JSON
and archives files that already exist on disk.

Usage:
    uv run python automation/build_mod_report.py --run-id first_pass
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

import json  # noqa: E402

from build_mod import shipped_regions  # noqa: E402
from mod_capture import resolve_geo  # noqa: E402

RESOURCE_KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]

#: one human-readable line of "why this region looks the way it does",
#: shown above each section - the report's stated focus is process, not
#: just numbers.
REGION_NOTES = {
    "Salish Sea": ("Consolidation width overridden to 5/3 tiles (rather than "
                   "the general-purpose 4/3 default) - this exact window was "
                   "the one used to verify the project's known-good defaults "
                   "in the first place, so it keeps its own verified value "
                   "rather than inheriting the generic one."),
    "Japan": ("Rotated 35 degrees from north-up, inherited from a "
              "pre-existing precedent (batch_240.py) - a geometric/"
              "orientation choice about how the coastline sits in the grid, "
              "not a generation-quality one."),
}
DEFAULT_NOTE = "Uses rwmaps's own known-good defaults untouched (resolution 50m, overlap 0.85, min-water/land-width 4/3, clumping-factor 8)."

AI_TYPE_EXPLANATION = {
    "ARABIA": "dry, no water at all",
    "COASTAL": "land map with real, reachable water",
    "MEDITERRANEAN": "high water fraction but players still land-connected to each other",
    "ISLANDS": "players NOT land-connected (lower total water fraction)",
    "ARCHIPELAGO": "players NOT land-connected (higher total water fraction)",
}


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                            capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def load_records(results_path: Path) -> dict:
    by_region = defaultdict(list)
    if not results_path.exists():
        return by_region
    for line in results_path.open(encoding="utf-8"):
        rec = json.loads(line)
        by_region[rec["region"]].append(rec)
    return by_region


def archive_region_files(matrix_out: Path, data_dir: Path, name: str,
                          sample_indices: list[int]) -> tuple[str | None, dict[int, str]]:
    """Copy this region's .rms + raw .aoe2scenario captures out of gitignored
    out/ into reports/, so the report links to files actually checked in."""
    dest_dir = data_dir / name
    dest_dir.mkdir(parents=True, exist_ok=True)

    rms_relpath = None
    script_root = matrix_out / "scripts" / name
    rms_candidates = sorted(script_root.rglob("*.rms"), key=lambda p: p.stat().st_mtime)
    if rms_candidates:
        newest = rms_candidates[-1]
        rms_filename = f"{name}.rms"
        dest = dest_dir / rms_filename
        shutil.copyfile(newest, dest)
        rms_relpath = f"{data_dir.name}/{name}/{rms_filename}"

    scenario_relpaths = {}
    raw_dir = matrix_out / name / "raw"
    for i in sample_indices:
        src = raw_dir / f"sample_{i:03d}.aoe2scenario"
        if src.exists():
            scenario_filename = f"{name}__s{i:03d}.aoe2scenario"
            dest = dest_dir / scenario_filename
            shutil.copyfile(src, dest)
            scenario_relpaths[i] = f"{data_dir.name}/{name}/{scenario_filename}"
    return rms_relpath, scenario_relpaths


def _legacy_res(rec: dict) -> dict:
    """The nearest-TC resource block, under either key.

    Renamed to `legacy_resources_nearest_tc` on 2026-08-16; archived runs
    still carry it as `resources`.
    """
    return rec.get("legacy_resources_nearest_tc") or rec.get("resources", {})


def stat_row(label: str, values: list[float], fmt: str = "{:.2f}") -> str:
    values = [v for v in values if v is not None and v == v]  # drop None/NaN
    if not values:
        return f"<tr><td>{label}</td><td colspan='3' class='missing'>no data</td></tr>"
    lo, med, hi = min(values), statistics.median(values), max(values)
    return (f"<tr><td>{label}</td><td>{fmt.format(lo)}</td>"
            f"<td>{fmt.format(med)}</td><td>{fmt.format(hi)}</td></tr>")


def summary_table(records: list[dict]) -> str:
    seps = [r["placement"]["min_tc_separation"] for r in records]
    reach = [r["placement"]["pairwise_land_reachable_fraction"] for r in records]
    landmasses = [r["placement"]["n_landmasses_with_a_player"] for r in records]
    any_zero_rate = (sum(1 for r in records if _legacy_res(r)["any_player_zero_of_a_kind"])
                      / len(records)) if records else float("nan")
    iou = [r["aesthetic"]["iou_10m"] for r in records]
    bnd = [r["aesthetic"]["bnd_ratio"] for r in records]
    pock = [r["aesthetic"]["pockmark"] for r in records]
    presf = [r["aesthetic"]["preserved_fraction"] for r in records]

    rows = [
        stat_row("min TC separation (tiles)", seps, "{:.0f}"),
        stat_row("pairwise land-reachable fraction", reach, "{:.2f}"),
        stat_row("landmasses with a player", landmasses, "{:.0f}"),
        stat_row("coastline IoU vs. 10m truth", iou, "{:.3f}"),
        stat_row("boundary ratio (complexity vs. truth)", bnd, "{:.3f}"),
        stat_row("pockmark score (added noise vs. truth)", pock, "{:.4f}"),
        stat_row("island preserved-fraction", presf, "{:.2f}"),
    ]
    return f'''
    <table class="summary">
      <tr><th>metric</th><th>min</th><th>median</th><th>max</th></tr>
      {"".join(rows)}
      <tr><td>any player zero-of-a-kind rate</td>
          <td colspan="3">{any_zero_rate*100:.0f}% of {len(records)} samples</td></tr>
    </table>'''


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
    resources = _legacy_res(rec)
    aesthetic = rec["aesthetic"]
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
            <div class="fact-row">
                <span>IoU {aesthetic['iou_10m']:.3f}</span>
                <span>bnd ratio {aesthetic['bnd_ratio']:.2f}</span>
                <span>pockmark {aesthetic['pockmark']:.4f}</span>
                <span>islands preserved {aesthetic['preserved_fraction']*100:.0f}%
                    ({aesthetic['islands_preserved']}/{aesthetic['islands_preserved']+aesthetic['islands_merged']})</span>
            </div>
            {flag}
            {resource_table(resources['per_player'])}
            <div class="fact-row">{scenario_link}</div>
        </div>
        <img src="{rec['preview_png_b64']}" alt="real engine capture" loading="lazy">
    </div>'''


def region_section(name: str, extra_args: list[str], records: list[dict],
                    rms_link_html: str) -> str:
    lon, lat, span, rot = resolve_geo(extra_args)
    ai_type = records[0]["ai_map_type"] if records else None
    ai_expl = AI_TYPE_EXPLANATION.get(ai_type, "")
    ai_badge = (f'<span class="ai-badge" title="{ai_expl}">ai_info_map_type '
                f'<code>{ai_type}</code></span>' if ai_type else "")
    note = REGION_NOTES.get(name, DEFAULT_NOTE)
    args_str = " ".join(extra_args) if extra_args else "(no overrides)"

    n_captured = len(records)
    if not records:
        body = '<p class="missing">no captures yet</p>'
    else:
        samples_html = "\n".join(
            sample_card(r, r.get("_scenario_relpath")) for r in records
        )
        body = f'''
        {summary_table(records)}
        <details class="samples-toggle" open>
            <summary>{n_captured} sample(s)</summary>
            <div class="samples">{samples_html}</div>
        </details>'''

    return f'''
    <section class="region">
        <div class="region-head">
            <h2>{name}</h2>
            {ai_badge}
        </div>
        <p class="region-note">{note}</p>
        <p class="region-sub">{lon}, {lat} &middot; {span} km span &middot; rotate {rot}
            &middot; <code>{args_str}</code> {rms_link_html}</p>
        {body}
    </section>'''


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True,
                    help="build the report for out/mod_capture/<run-id>/results.jsonl")
    return p.parse_args()


def main():
    args = parse_args()
    matrix_out = REPO / "out" / "mod_capture" / args.run_id
    results_path = matrix_out / "results.jsonl"
    # Timestamp comes FIRST in the filename, not after a descriptive prefix -
    # sorting has to survive whatever name a future run/agent picks (e.g. a
    # "mod_new_report" would sort before "mod_report" alphabetically and
    # silently break chronological order if the stamp weren't the leading
    # token).
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    data_dir = REPO / "reports" / f"{stamp}_mod_report_data_{args.run_id}"
    report_path = REPO / "reports" / f"{stamp}_mod_report_{args.run_id}.html"

    by_region = load_records(results_path)
    sections = []
    total_captured = 0
    shipped = shipped_regions()
    for name, extra_args in shipped:
        records = sorted(by_region.get(name, []), key=lambda r: r["sample_index"])
        sample_indices = [r["sample_index"] for r in records]
        rms_relpath, scenario_relpaths = archive_region_files(matrix_out, data_dir, name, sample_indices)
        for r in records:
            r["_scenario_relpath"] = scenario_relpaths.get(r["sample_index"])
        if rms_relpath:
            rms_filename = rms_relpath.rsplit("/", 1)[-1]
            rms_link_html = f'<a class="filelink" href="{rms_relpath}">{rms_filename}</a>'
        else:
            rms_link_html = '<span class="filelink missing-link">.rms missing</span>'
        total_captured += len(records)
        sections.append(region_section(name, extra_args, records, rms_link_html))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = HTML_TEMPLATE.format(
        title=f"Real World Maps: mod capture report ({args.run_id})",
        sections="\n".join(sections),
        generated_at=generated_at,
        commit=git_commit(),
        total_captured=total_captured,
        total_expected=len(shipped) * 10,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html, encoding="utf-8")
    print(f"wrote {report_path} ({report_path.stat().st_size / 1024:.0f} KB)")
    print(f"archived data under {data_dir}")


HTML_TEMPLATE = """<title>{title}</title>
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
.lede {{ color: var(--ink-dim); max-width: 74ch; line-height: 1.55; font-size: 1rem; margin: 0 0 2rem; }}
.region {{ margin-bottom: 3rem; background: var(--bg-elevated); border: 1px solid var(--rule);
  border-radius: 10px; padding: 1.3rem 1.5rem; }}
.region-head {{ display: flex; align-items: baseline; gap: 0.8rem; flex-wrap: wrap; }}
.region-head h2 {{ font-family: Charter, Georgia, serif; font-weight: 600; font-size: 1.5rem; margin: 0; color: var(--accent); }}
.ai-badge {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.75rem; color: var(--ink-dim); background: var(--card-bg);
  border: 1px solid var(--rule); border-radius: 999px; padding: 0.15rem 0.7rem; cursor: help;
}}
.ai-badge code {{ color: var(--ink); font-weight: 600; }}
.region-note {{ color: var(--ink-dim); font-size: 0.9rem; max-width: 80ch; margin: 0.5rem 0; line-height: 1.5; }}
.region-sub {{
  font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.78rem; color: var(--ink-dim); margin: 0 0 1rem;
}}
table.summary {{ border-collapse: collapse; font-size: 0.82rem; margin-bottom: 1rem; }}
table.summary th, table.summary td {{ border: 1px solid var(--rule); padding: 3px 10px; text-align: right; }}
table.summary th:first-child, table.summary td:first-child {{ text-align: left; }}
table.summary th {{ color: var(--ink-dim); font-weight: 500; }}
.samples-toggle summary {{ cursor: pointer; font-size: 0.85rem; color: var(--accent); margin-bottom: 0.6rem; }}
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
  <p class="meta">Generated {generated_at} &middot; repo commit <code>{commit}</code>
     &middot; {total_captured}/{total_expected} samples captured</p>
  <p class="lede">
    Every image is a real AoE2 DE engine capture of one of the 10 regions
    shipped in the "Real World Maps" mod, generated with the exact settings
    that ship (see each region's note below for the reasoning). Fairness
    facts (TC separation, landmass connectivity, resource counts) come
    straight from the engine's own placement, not a Python approximation.
    Aesthetic-recognizability metrics (coastline IoU, boundary ratio,
    pockmark, island preserved-fraction) compare that same real capture
    against the finest-detail (10m) real-world coastline for that window -
    a resolution-independent measure of how close the result looks to the
    actual place. "Landmass(es) w/ players" and "% pairs land-reachable"
    are geography facts, not a fairness verdict - a real-world coastline
    map often has players on separate islands by design. The only thing
    flagged as a problem is a player having literally zero of some
    resource kind.
  </p>
  {sections}
  <footer>
    Built by build_mod_report.py from mod_capture.py's results.jsonl - no
    scenario re-parsing happens at report-build time. Each region's .rms
    script and every sample's .aoe2scenario are archived under
    reports/mod_report_data_&lt;run-id&gt;/ and linked above.
  </footer>
</div>
"""

if __name__ == "__main__":
    main()
