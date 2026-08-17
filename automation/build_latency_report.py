"""Where a capture pass's wall-clock actually goes, per phase and per region.

Reads the ``timing`` block ``mod_capture.py`` now writes into every record,
so nothing here re-parses a scenario or re-runs the engine. The question it
exists to answer is the one a single "captured in 61s" line cannot: at the
~1000-generation goal scale, which phase owns the hours, and which of them
is worth engineering.

Two things kept deliberately separate:

* **Engine time vs our time.** The engine generating a 240x240 map is a
  cost this project does not control. Everything else - building the
  script, verifying the editor, driving the clicks, parsing the result - is
  ours, and only the second number is actionable.
* **Per-region vs per-sample.** A script is built once per region and then
  sampled N times, so regen is amortised over N. Reporting it per sample
  would make the pass look slower per capture than it is at any N > 1.

Usage:
    uv run python automation/build_latency_report.py --run-id latency_4regions
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rwmaps.cli import build_parser  # noqa: E402

#: Phases, in the order they run, and whether the time is ours or the game's.
#: "ours" is the only column an engineering decision can move.
PHASES = [
    ("regen_s", "build the .rms", "ours", "per region"),
    ("preflight_s", "verify the editor", "ours", "per region"),
    ("generate_s", "engine generates", "engine", "per sample"),
    ("save_s", "drive Menu -> Save", "ours", "per sample"),
    ("analyze_s", "parse + score", "ours", "per sample"),
]


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def load(results_path: Path) -> dict[str, list[dict]]:
    by_region: dict[str, list[dict]] = defaultdict(list)
    for line in results_path.open(encoding="utf-8"):
        rec = json.loads(line)
        by_region[rec["region"]].append(rec)
    return by_region


def resolved_params(extra_args: list[str]) -> dict:
    """The complete parameter set this region runs with, defaults included.

    Taken from ``rwmaps``'s own parser rather than a copied table, so it
    cannot drift from what the CLI actually defaults to - a hardcoded copy
    of these values already exists in tuning_matrix.py and is already
    wrong about two of them.
    """
    ns = build_parser().parse_args(["_report", *extra_args])
    return {k: v for k, v in sorted(vars(ns).items()) if k != "name"}


def stats(values: list[float]) -> dict:
    vs = [v for v in values if v is not None]
    if not vs:
        return {"n": 0, "med": 0.0, "lo": 0.0, "hi": 0.0, "mean": 0.0}
    return {"n": len(vs), "med": statistics.median(vs), "lo": min(vs),
            "hi": max(vs), "mean": statistics.fmean(vs)}


def per_sample_seconds(rec: dict, n_samples: int) -> dict[str, float]:
    """One sample's phase costs, with the per-region phases amortised.

    A region pays regen and preflight once and then produces N samples, so
    the honest per-capture number divides those by N. At N=1 they are the
    whole story; at N=10 they are noise. Both readings come from this.
    """
    t = rec["timing"]
    out = {}
    for key, _label, _who, scope in PHASES:
        v = float(t.get(key, 0.0))
        out[key] = v / n_samples if scope == "per region" else v
    return out


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def bar(segments: list[tuple[str, float, str]], total: float, width: int = 640) -> str:
    """A stacked bar as inline SVG - no library, no external request."""
    if total <= 0:
        return ""

    x = 0.0
    parts = []
    for label, value, colour in segments:
        w = value / total * width
        if w > 0:
            parts.append(
                f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="22" fill="{colour}">'
                f'<title>{esc(label)}: {value:.1f}s ({value/total*100:.0f}%)</title></rect>')
            if w > 34:
                parts.append(
                    f'<text x="{x + w/2:.1f}" y="15.5" text-anchor="middle" '
                    f'font-size="10" fill="#0d1117">{value:.0f}s</text>')
        x += w
    return (f'<svg viewBox="0 0 {width} 22" width="100%" height="22" '
            f'role="img" preserveAspectRatio="none">{"".join(parts)}</svg>')


PHASE_COLOUR = {
    "regen_s": "#8ab4f8",
    "preflight_s": "#c9a0dc",
    "generate_s": "#f0883e",
    "save_s": "#7ee787",
    "analyze_s": "#e3b341",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    run_root = REPO / "out" / "mod_capture" / args.run_id
    results_path = run_root / "results.jsonl"
    if not results_path.exists():
        raise SystemExit(f"no results at {results_path}")
    by_region = load(results_path)
    if not by_region:
        raise SystemExit(f"{results_path} is empty")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    reports = REPO / "reports"
    data_dir = reports / f"capture_latency_data_{args.run_id}"
    data_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- latency
    all_samples = []
    for name, recs in by_region.items():
        for rec in recs:
            all_samples.append((name, per_sample_seconds(rec, len(recs))))

    phase_rows = []
    grand_med = 0.0
    for key, label, who, scope in PHASES:
        st = stats([s[key] for _n, s in all_samples])
        raw = stats([float(r["timing"].get(key, 0.0))
                     for recs in by_region.values() for r in recs])
        grand_med += st["med"]
        phase_rows.append(
            f"<tr><td>{esc(label)}</td>"
            f"<td><span class='who {who}'>{who}</span></td>"
            f"<td class='n'>{st['med']:.1f}</td>"
            f"<td class='n'>{st['lo']:.1f}–{st['hi']:.1f}</td>"
            f"<td class='n'>{raw['med']:.1f}</td>"
            f"<td class='scope'>{esc(scope)}</td></tr>")

    ours_med = sum(stats([s[k] for _n, s in all_samples])["med"]
                   for k, _l, who, _sc in PHASES if who == "ours")
    engine_med = sum(stats([s[k] for _n, s in all_samples])["med"]
                     for k, _l, who, _sc in PHASES if who == "engine")
    observed_total = stats([float(r["timing"]["sample_total_s"])
                            for recs in by_region.values() for r in recs])

    # A pass's real duration, from the phase medians rather than a guess.
    per_1000_h = grand_med * 1000 / 3600
    engine_1000_h = engine_med * 1000 / 3600

    # ------------------------------------------------------- per region
    region_rows = []
    sections = []
    for name in sorted(by_region):
        recs = sorted(by_region[name], key=lambda r: r["sample_index"])
        n = len(recs)
        amortised = [per_sample_seconds(r, n) for r in recs]
        segs = [(label, statistics.median([a[key] for a in amortised]),
                 PHASE_COLOUR[key]) for key, label, _w, _s in PHASES]
        seg_total = sum(v for _l, v, _c in segs)
        ious = [r["aesthetic"]["iou_10m"] for r in recs]
        gen = stats([float(r["timing"]["generate_s"]) for r in recs])
        region_rows.append(
            f"<tr><td>{esc(name)}</td><td class='n'>{n}</td>"
            f"<td class='n'>{float(recs[0]['timing']['regen_s']):.0f}</td>"
            f"<td class='n'>{gen['med']:.0f}</td>"
            f"<td class='n'>{seg_total:.0f}</td>"
            f"<td class='bar'>{bar(segs, seg_total)}</td></tr>")

        # verification + artifacts for this region
        dest_dir = data_dir / name.replace(" ", "_")
        dest_dir.mkdir(parents=True, exist_ok=True)
        rms_link = ""
        rms_candidates = sorted((run_root / "scripts" / name).rglob("*.rms"))
        if rms_candidates:
            rms_name = f"{name.replace(' ', '_')}.rms"
            shutil.copyfile(rms_candidates[-1], dest_dir / rms_name)
            rms_link = (f"<a class='filelink' href='{data_dir.name}/"
                        f"{name.replace(' ', '_')}/{rms_name}'>{esc(rms_name)}</a>")

        cards = []
        for r in recs:
            i = r["sample_index"]
            src = run_root / name / "raw" / f"sample_{i:03d}.aoe2scenario"
            scx_link = ""
            if src.exists():
                scx_name = f"{name.replace(' ', '_')}__s{i:03d}.aoe2scenario"
                shutil.copyfile(src, dest_dir / scx_name)
                scx_link = (f"<a class='filelink' href='{data_dir.name}/"
                            f"{name.replace(' ', '_')}/{scx_name}'>.aoe2scenario</a>")
            a = r["aesthetic"]
            t = r["timing"]
            img = r.get("preview_png_b64", "")
            cards.append(
                f"<figure><img src='{img}' alt='{esc(name)} sample {i}'>"
                f"<figcaption>"
                f"IoU <b>{a['iou_10m']:.3f}</b> &middot; "
                f"islands {a['islands_preserved']}/{a['islands_total']} &middot; "
                f"land {r['land_pct']}% &middot; {r['n_tcs']} TCs<br>"
                f"generate {float(t['generate_s']):.0f}s &middot; "
                f"save {float(t['save_s']):.1f}s &middot; "
                f"parse {float(t['analyze_s']):.0f}s {scx_link}"
                f"</figcaption></figure>")

        params = resolved_params(recs[0]["extra_args"])
        param_rows = "".join(
            f"<tr><td><code>{esc(k)}</code></td><td>{esc(v)}</td></tr>"
            for k, v in params.items())
        sections.append(f"""
  <section>
    <h3>{esc(name)} <span class="sub">{esc(recs[0]['ai_map_type'])} &middot;
      IoU {min(ious):.3f}–{max(ious):.3f} &middot; {rms_link}</span></h3>
    <div class="cards">{''.join(cards)}</div>
    <details><summary>complete resolved parameter set ({len(params)} values,
      defaults included)</summary>
      <table class="params">{param_rows}</table>
      <p class="note">Region's own arguments:
        <code>{esc(' '.join(recs[0]['extra_args']))}</code></p>
    </details>
  </section>""")

    legend = " ".join(
        f"<span class='key'><i style='background:{PHASE_COLOUR[k]}'></i>{esc(l)}</span>"
        for k, l, _w, _s in PHASES)

    html = TEMPLATE.format(
        title=esc(args.title or f"Capture latency — {args.run_id}"),
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        commit=git_commit(),
        n_regions=len(by_region),
        n_samples=len(all_samples),
        phase_rows="".join(phase_rows),
        region_rows="".join(region_rows),
        legend=legend,
        grand_med=f"{grand_med:.0f}",
        ours_med=f"{ours_med:.0f}",
        engine_med=f"{engine_med:.0f}",
        ours_pct=f"{ours_med / grand_med * 100:.0f}" if grand_med else "0",
        observed_med=f"{observed_total['med']:.0f}",
        observed_lo=f"{observed_total['lo']:.0f}",
        observed_hi=f"{observed_total['hi']:.0f}",
        per_1000_h=f"{per_1000_h:.1f}",
        engine_1000_h=f"{engine_1000_h:.1f}",
        per_hour=f"{3600 / grand_med:.0f}" if grand_med else "0",
        sections="".join(sections),
        run_id=esc(args.run_id),
    )
    out = reports / f"{stamp}_capture_latency_{args.run_id}.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}")
    print(f"  {len(by_region)} regions, {len(all_samples)} samples")
    print(f"  median per capture {grand_med:.0f}s "
          f"(engine {engine_med:.0f}s, ours {ours_med:.0f}s)")
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
h3 {{ font-size:1rem; margin:1.8rem 0 .5rem; }}
.sub {{ color:var(--dim); font-weight:400; font-size:.8rem; }}
.meta {{ color:var(--dim); font-size:.8rem; margin:0 0 1.4rem; }}
.lede {{ color:var(--ink); max-width:78ch; }}
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
td.bar {{ width:45%; }}
td.scope, .note {{ color:var(--dim); font-size:.78rem; }}
.who {{ font-size:.7rem; padding:.1rem .4rem; border-radius:4px; }}
.who.ours {{ background:#1f6feb33; color:#8ab4f8; }}
.who.engine {{ background:#f0883e33; color:var(--accent); }}
.legend {{ display:flex; gap:1.1rem; flex-wrap:wrap; margin:.7rem 0 0;
  color:var(--dim); font-size:.75rem; }}
.key i {{ display:inline-block; width:9px; height:9px; border-radius:2px;
  margin-right:.35rem; }}
.scroll {{ overflow-x:auto; }}
.cards {{ display:flex; gap:.8rem; flex-wrap:wrap; }}
figure {{ margin:0; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; padding:.5rem; width:250px; }}
figure img {{ width:100%; display:block; border-radius:4px; }}
figcaption {{ color:var(--dim); font-size:.72rem; margin-top:.4rem; }}
figcaption b {{ color:var(--ink); }}
details {{ margin:.7rem 0 0; }}
summary {{ cursor:pointer; color:var(--dim); font-size:.78rem; }}
table.params {{ margin-top:.5rem; font-size:.75rem; max-width:640px; }}
code {{ font-family:ui-monospace,Consolas,monospace; font-size:.9em; }}
.filelink {{ color:var(--accent); text-decoration:none; font-size:.72rem;
  font-family:ui-monospace,Consolas,monospace; }}
.filelink:hover {{ text-decoration:underline; }}
footer {{ color:var(--dim); font-size:.78rem; margin-top:3rem; }}
</style>
<div class="wrap">
  <h1>{title}</h1>
  <p class="meta">Generated {generated_at} &middot; repo commit <code>{commit}</code>
    &middot; {n_regions} regions &middot; {n_samples} real engine captures
    &middot; run-id <code>{run_id}</code></p>
  <p class="lede">
    Every number here is measured wall-clock from
    <code>mod_capture.py</code>'s own per-phase timing, over real engine
    captures of {n_regions} different maps. The split that matters is
    <span class="who engine">engine</span> versus
    <span class="who ours">ours</span>: the engine generating a 240&times;240
    map is a cost this project does not control, and everything else is.
    Per-region phases (building the script, verifying the editor) are
    amortised over that region's samples, because a pass builds a script
    once and then samples it.
  </p>

  <div class="headline">
    <div class="tile"><b>{grand_med}s</b><span>median per capture, amortised</span></div>
    <div class="tile"><b>{engine_med}s</b><span>of that is the engine</span></div>
    <div class="tile"><b>{ours_med}s</b><span>is ours ({ours_pct}%)</span></div>
    <div class="tile"><b>{per_hour}</b><span>captures per hour</span></div>
    <div class="tile"><b>{per_1000_h} h</b><span>for 1000 captures
      ({engine_1000_h} h unavoidable)</span></div>
  </div>

  <h2>Where one capture's time goes</h2>
  <div class="scroll"><table>
    <tr><th>phase</th><th>whose</th><th>median (s)</th><th>range (s)</th>
      <th>unamortised median</th><th>charged</th></tr>
    {phase_rows}
  </table></div>
  <p class="note">"Unamortised" is the raw measurement before dividing the
    per-region phases over that region's samples — the number you pay at
    N=1. Observed end-to-end per sample (engine + save + parse, excluding
    the per-region phases): median {observed_med}s, range
    {observed_lo}–{observed_hi}s.</p>

  <h2>Per map</h2>
  <div class="scroll"><table>
    <tr><th>map</th><th>samples</th><th>regen (s)</th><th>engine (s)</th>
      <th>per capture (s)</th><th>where the time goes</th></tr>
    {region_rows}
  </table></div>
  <div class="legend">{legend}</div>

  <h2>The captures themselves</h2>
  <p class="note">IoU is the engine's coastline against the real-world 10m
    coastline for that window — the check that the map generated is the map
    that was asked for, and the only thing here that a wrong-script capture
    cannot fake. Judge the pictures; the numbers only say the pipeline ran.</p>
  {sections}

  <footer>
    Built by build_latency_report.py from mod_capture.py's results.jsonl —
    no scenario is re-parsed and no engine time is spent at report-build
    time. Scripts and raw captures are archived under
    reports/capture_latency_data_{run_id}/ and linked above.
  </footer>
</div>
"""


if __name__ == "__main__":
    raise SystemExit(main())
