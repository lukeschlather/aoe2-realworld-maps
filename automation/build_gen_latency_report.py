"""What one Generate Map click costs, per script, and how much it varies.

Reads ``out/gen_latency/<run-id>/results.jsonl`` and nothing else - no
scenario is re-parsed and no engine time is spent building this.

The report is built around the spread rather than the average, because the
average was never the open question. Every map gets its samples drawn
individually on a strip with the median and the interquartile box behind
them, so a map that is slow-and-steady and a map that is usually fast with
one 3x outlier do not collapse into the same number. Sorted by median, and
coloured by group, so "are ours slower than stock" is answered by looking
at where the colours sit rather than by a single ratio.

Two things it deliberately reports rather than scores:

* **Round index.** Samples are interleaved, so round 1 of every map ran
  before round 2 of any of them. If the pass drifts - the engine warming
  up, the machine heating, a leak across generations - it shows up as a
  slope against round, and that would mean the per-map spread is partly the
  clock rather than the script. The drift panel is there to be checked
  before the per-map spread is believed.
* **Failures.** A generation that never started, timed out, or killed the
  game is in the record with its detail string, and is excluded from the
  timing statistics but not from the count. A pass that quietly dropped its
  slowest samples would report exactly the wrong thing.

Usage:
    uv run python automation/build_gen_latency_report.py --run-id latency_v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GROUP_COLOUR = {
    "ours": "#8ab4f8",
    "stock": "#7ee787",
    "hyperrandom": "#f0883e",
}
GROUP_ORDER = ["ours", "stock", "hyperrandom"]


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def quantile(vs: list[float], q: float) -> float:
    """Linear-interpolated quantile, defined for n=1 and n=2.

    ``statistics.quantiles`` raises below n=2 and gives a box that is wider
    than the data at small n. This pass is meant to be read while it is
    still running, so the small-n case is the normal case, not an edge one.
    """
    if not vs:
        return 0.0
    s = sorted(vs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def summarise(vs: list[float]) -> dict:
    if not vs:
        return {"n": 0, "med": 0.0, "lo": 0.0, "hi": 0.0, "q1": 0.0,
                "q3": 0.0, "sd": 0.0, "cv": 0.0, "spread": 0.0}
    med = statistics.median(vs)
    sd = statistics.stdev(vs) if len(vs) > 1 else 0.0
    return {
        "n": len(vs), "med": med, "lo": min(vs), "hi": max(vs),
        "q1": quantile(vs, 0.25), "q3": quantile(vs, 0.75), "sd": sd,
        "cv": sd / med * 100 if med else 0.0,
        "spread": max(vs) - min(vs),
    }


def strip(vs: list[float], st: dict, vmax: float, colour: str,
          width: int = 460, height: int = 24) -> str:
    """One map's samples as dots over an IQR box - inline SVG, no library.

    Every sample is drawn. A box plot alone would hide n, and n is small
    enough here that hiding it would be dishonest.
    """
    if not vs or vmax <= 0:
        return ""

    def x(v: float) -> float:
        return v / vmax * width

    mid = height / 2
    parts = [
        # min-max whisker
        f'<line x1="{x(st["lo"]):.1f}" y1="{mid}" x2="{x(st["hi"]):.1f}" '
        f'y2="{mid}" stroke="{colour}" stroke-opacity=".45" stroke-width="1.5"/>',
        # interquartile box
        f'<rect x="{x(st["q1"]):.1f}" y="{mid - 6}" '
        f'width="{max(x(st["q3"]) - x(st["q1"]), 1):.1f}" height="12" '
        f'fill="{colour}" fill-opacity=".18" stroke="{colour}" '
        f'stroke-opacity=".5"/>',
        # median
        f'<line x1="{x(st["med"]):.1f}" y1="{mid - 8}" x2="{x(st["med"]):.1f}" '
        f'y2="{mid + 8}" stroke="{colour}" stroke-width="2.5"/>',
    ]
    for v in vs:
        parts.append(
            f'<circle cx="{x(v):.1f}" cy="{mid}" r="2.6" fill="{colour}" '
            f'fill-opacity=".85"><title>{v:.1f}s</title></circle>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" '
            f'height="{height}" preserveAspectRatio="none" role="img">'
            f'{"".join(parts)}</svg>')


def axis(vmax: float, width: int = 460) -> str:
    """A shared scale under the strips, so they can be compared by eye."""
    step = 20 if vmax <= 120 else (30 if vmax <= 240 else 60)
    ticks = []
    v = 0.0
    while v <= vmax:
        px = v / vmax * width
        anchor = "start" if v == 0 else ("end" if v + step > vmax else "middle")
        ticks.append(
            f'<line x1="{px:.1f}" y1="0" x2="{px:.1f}" y2="4" stroke="#30363d"/>'
            f'<text x="{px:.1f}" y="14" font-size="9" fill="#8b949e" '
            f'text-anchor="{anchor}">{v:.0f}s</text>')
        v += step
    return (f'<svg viewBox="0 0 {width} 18" width="100%" height="18" '
            f'preserveAspectRatio="none" role="img">{"".join(ticks)}</svg>')


def drift_chart(rows: list[dict], vmax: float, width: int = 900,
                height: int = 200) -> str:
    """Every sample against the round it ran in.

    The control on the whole report: interleaving only removes the
    confound between map and moment if the moment is not itself trending.
    """
    ok = [r for r in rows if r.get("verified")]
    if not ok:
        return ""
    rounds = max(r["round"] for r in ok) + 1
    pad_l, pad_b = 34, 20
    plot_w, plot_h = width - pad_l - 8, height - pad_b - 8

    def px(rnd: int) -> float:
        return pad_l + (plot_w / 2 if rounds == 1
                        else rnd / (rounds - 1) * plot_w)

    def py(v: float) -> float:
        return 8 + plot_h - v / vmax * plot_h

    parts = []
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = py(vmax * frac)
        parts.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - 8}" y2="{y:.1f}" '
            f'stroke="#30363d" stroke-width=".7"/>'
            f'<text x="{pad_l - 5}" y="{y + 3:.1f}" font-size="9" '
            f'fill="#8b949e" text-anchor="end">{vmax * frac:.0f}</text>')
    for rnd in range(rounds):
        parts.append(
            f'<text x="{px(rnd):.1f}" y="{height - 5}" font-size="9" '
            f'fill="#8b949e" text-anchor="middle">{rnd + 1}</text>')
    # Median per round, as a line - the trend, if there is one.
    by_round = defaultdict(list)
    for r in ok:
        by_round[r["round"]].append(r["generate_s"])
    pts = " ".join(f"{px(rd):.1f},{py(statistics.median(vs)):.1f}"
                   for rd, vs in sorted(by_round.items()))
    for r in ok:
        parts.append(
            f'<circle cx="{px(r["round"]):.1f}" cy="{py(r["generate_s"]):.1f}" '
            f'r="2.8" fill="{GROUP_COLOUR.get(r["group"], "#8b949e")}" '
            f'fill-opacity=".7"><title>{esc(r["map"])} round '
            f'{r["round"] + 1}: {r["generate_s"]:.1f}s</title></circle>')
    if len(by_round) > 1:
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#e6edf3" '
                     f'stroke-width="1.6" stroke-opacity=".8"/>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">'
            f'{"".join(parts)}</svg>')


def sha8(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:8]
    except OSError:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    run_root = REPO / "out" / "gen_latency" / args.run_id
    results_path = run_root / "results.jsonl"
    if not results_path.exists():
        raise SystemExit(f"no results at {results_path}")
    rows = [json.loads(l) for l in results_path.open(encoding="utf-8")]
    if not rows:
        raise SystemExit(f"{results_path} is empty")

    # The plan event carries the resolved run configuration and each
    # script's identity at run time - the report must not re-read the
    # scripts and quietly describe a file that has changed since.
    plan = {}
    events_path = run_root / "events.jsonl"
    if events_path.exists():
        for line in events_path.open(encoding="utf-8"):
            rec = json.loads(line)
            if rec.get("kind") == "plan":
                plan = rec
    script_meta = {m["map"]: m for m in plan.get("maps", [])}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    reports = REPO / "reports"
    data_dir = reports / f"gen_latency_data_{args.run_id}"
    data_dir.mkdir(parents=True, exist_ok=True)

    by_map: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_map[r["map"]].append(r)

    ok_rows = [r for r in rows if r.get("verified")]
    failures = [r for r in rows if not r.get("verified")]
    vmax = max((r["generate_s"] for r in ok_rows), default=1.0) * 1.06

    # ------------------------------------------------------------ per map
    entries = []
    for label, recs in by_map.items():
        vs = [r["generate_s"] for r in recs if r.get("verified")]
        entries.append({
            "map": label, "group": recs[0]["group"],
            "script": recs[0]["script"],
            "bytes": recs[0].get("script_bytes", 0),
            "vs": vs, "st": summarise(vs), "recs": recs,
            "n_fail": sum(1 for r in recs if not r.get("verified")),
        })
    entries.sort(key=lambda e: (-e["st"]["med"], e["map"]))

    map_rows = "".join(
        f"<tr><td>{esc(e['map'])}</td>"
        f"<td><span class='who' style='background:{GROUP_COLOUR[e['group']]}22;"
        f"color:{GROUP_COLOUR[e['group']]}'>{esc(e['group'])}</span></td>"
        f"<td class='n'>{e['st']['n']}</td>"
        f"<td class='n'>{e['st']['med']:.1f}</td>"
        f"<td class='n'>{e['st']['lo']:.1f}–{e['st']['hi']:.1f}</td>"
        f"<td class='n'>{e['st']['spread']:.1f}</td>"
        f"<td class='n'>{e['st']['sd']:.1f}</td>"
        f"<td class='n'>{e['st']['cv']:.0f}%</td>"
        f"<td class='n'>{e['bytes'] / 1024:.0f}K</td>"
        f"<td class='strip'>{strip(e['vs'], e['st'], vmax, GROUP_COLOUR[e['group']])}</td>"
        f"</tr>" for e in entries)

    # ---------------------------------------------------------- per group
    group_rows = []
    for g in GROUP_ORDER:
        vs = [r["generate_s"] for r in ok_rows if r["group"] == g]
        if not vs:
            continue
        st = summarise(vs)
        meds = [e["st"]["med"] for e in entries if e["group"] == g and e["st"]["n"]]
        group_rows.append(
            f"<tr><td><span class='who' style='background:{GROUP_COLOUR[g]}22;"
            f"color:{GROUP_COLOUR[g]}'>{esc(g)}</span></td>"
            f"<td class='n'>{len(meds)}</td><td class='n'>{st['n']}</td>"
            f"<td class='n'>{st['med']:.1f}</td>"
            f"<td class='n'>{st['lo']:.1f}–{st['hi']:.1f}</td>"
            f"<td class='n'>{min(meds):.1f}–{max(meds):.1f}</td>"
            f"<td class='n'>{st['cv']:.0f}%</td></tr>")

    # ---------------------------------------------------------- failures
    if failures:
        fail_rows = "".join(
            f"<tr><td>{esc(r['map'])}</td><td class='n'>{r['round'] + 1}</td>"
            f"<td class='n'>{r['generate_s']:.1f}</td>"
            f"<td>{esc(r.get('generate_detail', ''))}</td></tr>"
            for r in failures)
        fail_html = f"""
  <h2>Failures</h2>
  <p class="note">Excluded from every timing statistic above, and listed
    here in full so they are not silently dropped. A generation that never
    started and one that timed out are different things, which is why the
    button-colour signal is worth having: it marks the start as well as the
    end.</p>
  <div class="scroll"><table>
    <tr><th>map</th><th>round</th><th>elapsed (s)</th><th>what happened</th></tr>
    {fail_rows}
  </table></div>"""
    else:
        fail_html = ("\n  <p class='note'>No failed generations in this run: "
                     f"all {len(rows)} attempts started, finished, and wrote a "
                     "scenario file with a newer mtime.</p>")

    # ---------------------------------------------------- scripts + config
    script_rows = []
    for e in entries:
        meta = script_meta.get(e["map"], {})
        src = Path(meta.get("path", ""))
        link = esc(e["script"])
        if e["group"] == "ours" and src.exists():
            dest = data_dir / src.name
            shutil.copyfile(src, dest)
            link = (f"<a class='filelink' href='{data_dir.name}/"
                    f"{src.name.replace(' ', '%20')}'>{esc(src.name)}</a>")
        script_rows.append(
            f"<tr><td>{esc(e['map'])}</td><td>{link}</td>"
            f"<td class='n'>{e['bytes'] / 1024:.0f}K</td>"
            f"<td><code>{sha8(src) if src.exists() else '?'}</code></td>"
            f"<td class='path'><code>{esc(meta.get('path', ''))}</code></td></tr>")

    config = {
        "map size": f"{plan.get('size', '?')}x{plan.get('size', '?')} (Huge)",
        "players": plan.get("players", "?"),
        "samples requested per map": plan.get("n_samples", "?"),
        "sample ordering": "interleaved (round-robin over all maps)",
        "generation timeout": f"{plan.get('timeout_s', '?')}s",
        "saved after generating": plan.get("save", "?"),
        "timed interval": "Generate Map click -> button returns to red",
        "start/end signal": "Generate button redness (idle ~107, busy ~68)",
        "poll interval": "0.5s (editor.generate default)",
        "scripts rebuilt": "no - every script captured as it exists on disk",
        "scenarios analysed": "no",
        "slot": "AA_rw_placeholder_tester.rms (selection never changed)",
        "run command": plan.get("command", "?"),
    }
    config_rows = "".join(
        f"<tr><td><code>{esc(k)}</code></td><td>{esc(v)}</td></tr>"
        for k, v in config.items())

    ours = [r["generate_s"] for r in ok_rows if r["group"] == "ours"]
    stock = [r["generate_s"] for r in ok_rows if r["group"] == "stock"]
    hyper = [r["generate_s"] for r in ok_rows if r["group"] == "hyperrandom"]
    ours_med = statistics.median(ours) if ours else 0.0
    stock_med = statistics.median(stock) if stock else 0.0
    ratio = (f"{ours_med / stock_med:.2f}x" if stock_med else "n/a")

    legend = " ".join(
        f"<span class='key'><i style='background:{GROUP_COLOUR[g]}'></i>{g}</span>"
        for g in GROUP_ORDER)

    n_rounds = max(r["round"] for r in rows) + 1
    html = TEMPLATE.format(
        title=esc(args.title or f"Generation latency — {args.run_id}"),
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        commit=git_commit(),
        run_id=esc(args.run_id),
        n_maps=len(by_map),
        n_samples=len(ok_rows),
        n_attempts=len(rows),
        n_rounds=n_rounds,
        ours_med=f"{ours_med:.0f}",
        stock_med=f"{stock_med:.0f}",
        hyper_med=f"{statistics.median(hyper):.0f}" if hyper else "—",
        ratio=ratio,
        engine_h=f"{statistics.median([r['generate_s'] for r in ok_rows]) * 1000 / 3600:.1f}"
                 if ok_rows else "0",
        map_rows=map_rows,
        group_rows="".join(group_rows),
        axis=axis(vmax),
        drift=drift_chart(rows, vmax),
        legend=legend,
        fail_html=fail_html,
        script_rows="".join(script_rows),
        config_rows=config_rows,
        data_dir=data_dir.name,
    )
    out = reports / f"{stamp}_gen_latency_{args.run_id}.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}")
    print(f"  {len(by_map)} maps, {len(ok_rows)}/{len(rows)} verified samples, "
          f"{n_rounds} round(s)")
    print(f"  median generate: ours {ours_med:.1f}s, stock {stock_med:.1f}s "
          f"({ratio})")
    return 0


TEMPLATE = """<meta charset="utf-8">
<title>{title}</title>
<style>
:root {{ --bg:#0d1117; --panel:#161b22; --ink:#e6edf3; --dim:#8b949e;
  --line:#30363d; --accent:#f0883e; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:2.5rem 1.5rem 4rem; }}
h1 {{ font-size:1.7rem; margin:0 0 .3rem; }}
h2 {{ font-size:1.15rem; margin:2.5rem 0 .6rem; }}
.meta {{ color:var(--dim); font-size:.8rem; margin:0 0 1.4rem; }}
.lede {{ max-width:78ch; }}
.headline {{ display:flex; gap:1rem; flex-wrap:wrap; margin:1.4rem 0; }}
.tile {{ background:var(--panel); border:1px solid var(--line); border-radius:8px;
  padding:.9rem 1.1rem; min-width:150px; }}
.tile b {{ display:block; font-size:1.5rem; line-height:1.2; }}
.tile span {{ color:var(--dim); font-size:.75rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.85rem; }}
th,td {{ border-bottom:1px solid var(--line); padding:.42rem .55rem; text-align:left;
  vertical-align:middle; }}
th {{ color:var(--dim); font-weight:600; font-size:.72rem; text-transform:uppercase;
  letter-spacing:.04em; }}
td.n {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.strip {{ width:38%; min-width:220px; }}
td.path {{ font-size:.68rem; color:var(--dim); }}
.note {{ color:var(--dim); font-size:.78rem; max-width:78ch; }}
.who {{ font-size:.7rem; padding:.1rem .45rem; border-radius:4px; }}
.legend {{ display:flex; gap:1.1rem; flex-wrap:wrap; margin:.7rem 0 0;
  color:var(--dim); font-size:.75rem; }}
.key i {{ display:inline-block; width:9px; height:9px; border-radius:2px;
  margin-right:.35rem; }}
.scroll {{ overflow-x:auto; }}
.axis {{ margin-left:auto; }}
details {{ margin:.9rem 0 0; }}
summary {{ cursor:pointer; color:var(--dim); font-size:.78rem; }}
code {{ font-family:ui-monospace,Consolas,monospace; font-size:.9em; }}
.filelink {{ color:var(--accent); text-decoration:none;
  font-family:ui-monospace,Consolas,monospace; font-size:.78rem; }}
.filelink:hover {{ text-decoration:underline; }}
.panel {{ background:var(--panel); border:1px solid var(--line);
  border-radius:8px; padding:1rem 1.1rem; margin:.8rem 0; }}
footer {{ color:var(--dim); font-size:.78rem; margin-top:3rem; }}
</style>
<div class="wrap">
  <h1>{title}</h1>
  <p class="meta">Generated {generated_at} &middot; repo commit <code>{commit}</code>
    &middot; run-id <code>{run_id}</code> &middot; {n_maps} scripts &middot;
    {n_samples}/{n_attempts} verified generations &middot; {n_rounds} interleaved round(s)</p>

  <p class="lede">Wall-clock from the Generate Map click until the button
    comes back, measured in the real engine at {n_maps} scripts &times;
    240&times;240 &times; 8 players. Nothing was rebuilt and nothing was
    analysed, so every second here is the engine's. Each strip draws the
    individual samples over the interquartile box, because the open question
    was the spread and a median alone cannot show it.</p>

  <div class="headline">
    <div class="tile"><b>{ours_med}s</b><span>median, our maps</span></div>
    <div class="tile"><b>{stock_med}s</b><span>median, stock maps</span></div>
    <div class="tile"><b>{hyper_med}s</b><span>median, hyper-random</span></div>
    <div class="tile"><b>{ratio}</b><span>ours vs stock</span></div>
    <div class="tile"><b>{engine_h} h</b><span>engine time per 1000 generations</span></div>
  </div>

  <h2>Per script</h2>
  <p class="note">Sorted slowest median first. The dot cloud is every
    verified sample; the box is Q1&ndash;Q3, the thick tick the median, the
    thin line min&ndash;max. All strips share one scale, so they can be read
    against each other. <b>spread</b> is max&minus;min and <b>CV</b> is the
    standard deviation as a percentage of the median &mdash; the
    scale-free way to ask which scripts are erratic.</p>
  <div class="scroll"><table>
    <tr><th>script</th><th>group</th><th>n</th><th>median (s)</th>
      <th>range (s)</th><th>spread</th><th>sd</th><th>cv</th><th>size</th>
      <th>samples</th></tr>
    {map_rows}
    <tr><td colspan="9"></td><td>{axis}</td></tr>
  </table></div>
  <div class="legend">{legend}</div>

  <h2>Per group</h2>
  <div class="scroll"><table>
    <tr><th>group</th><th>scripts</th><th>samples</th><th>median (s)</th>
      <th>sample range (s)</th><th>per-script medians (s)</th><th>cv</th></tr>
    {group_rows}
  </table></div>
  <p class="note">"Per-script medians" is the range the individual maps'
    medians span &mdash; a group whose sample range is wide only because it
    contains one slow map is a different finding from one where every map is
    variable, and the two columns separate them.</p>

  <h2>Drift across the pass</h2>
  <p class="note">Every sample against the round it ran in, with the white
    line the median of each round. Interleaving removes the confound between
    a map and the moment it ran <em>only if</em> the moment is not itself
    trending &mdash; so this is the control that has to be flat before the
    per-script spread above means what it appears to mean.</p>
  <div class="panel">{drift}</div>

  {fail_html}

  <h2>What was measured</h2>
  <div class="scroll"><table>{config_rows}</table></div>

  <details><summary>the exact scripts, as they were on disk during the run</summary>
    <div class="scroll"><table>
      <tr><th>map</th><th>file</th><th>size</th><th>sha256[:8]</th><th>path</th></tr>
      {script_rows}
    </table></div>
    <p class="note">Our scripts are copied into
      <code>reports/{data_dir}/</code> and linked. Stock scripts are the
      game's own files and are identified by path and hash rather than
      copied into this repo.</p>
  </details>

  <footer>
    Built by build_gen_latency_report.py from gen_latency.py's
    results.jsonl. No engine time is spent at report-build time.
  </footer>
</div>
"""


if __name__ == "__main__":
    raise SystemExit(main())
