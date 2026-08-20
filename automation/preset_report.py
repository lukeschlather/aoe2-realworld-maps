"""A report over any set of presets, from data already on disk.

Every other report builder in this directory is keyed to one ``--run-id``,
which is why choosing between windows captured in different passes looked
like it needed a fresh capture pass. It did not: the samples exist, the
previews exist, the fairness profiles exist. What was missing was a report
that takes *presets* rather than a run.

**No engine time. No regeneration.** Everything here is read from
``results.jsonl`` and the preset registry, so asking for a different set of
presets costs seconds.

Reads against stock, as CLAUDE.md requires: the stock cohort and Arabia come
from ``out/resource_baseline.json`` (Arabia held out as the reference rather
than averaged into the band). Facts only - no scores, no verdicts, and the
per-sample rows are laid out so the geometry and the previews are what the
eye lands on first.

Storage is index-only by default: the report links artifacts where they
live. ``--archive`` copies each preset's build and captured scenarios into
``reports/<stamp>_preset_report_data/`` the way the older report builders
do, for a set worth making durable.

Usage:
    uv run python automation/preset_report.py --presets scand-shift-10 \\
        scand-shift-15 scand-shift-20 scand-shallows scandinavia
    uv run python automation/preset_report.py --status shipped --title "What ships"
    uv run python automation/preset_report.py --window f0d2a6dc
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from rwmaps.presets import Preset, Registry  # noqa: E402
from preset_import import KINDS, _median, summarize_row  # noqa: E402
from runlog import git_commit  # noqa: E402

BASELINE = REPO / "out" / "resource_baseline.json"
KIND_LABEL = {"gold": "gold", "stone": "stone", "forage": "forage",
              "sheep": "sheep", "deer": "deer", "boar": "boar",
              "small_game": "small game"}


def esc(x) -> str:
    return html.escape(str(x))


def fmt(x, nd=1) -> str:
    """Numbers, comma-grouped, with a real em dash for "not measured".

    A median comes back as an int or a float depending on whether the sample
    count was odd, so both have to print the same way or a table column
    reads as two different quantities.
    """
    if x is None:
        return "&mdash;"
    if isinstance(x, (int, float)):
        return f"{x:,.{nd}f}"
    return str(x)


# --------------------------------------------------------------------------
# reading the data back
# --------------------------------------------------------------------------

def load_rows(paths: set[str]) -> dict[str, list[dict]]:
    """``{results.jsonl path: rows}``, read once and shared."""
    out = {}
    for rel in sorted(paths):
        p = REPO / rel
        if not p.is_file():
            continue
        rows = []
        for line in p.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        out[rel] = rows
    return out


def samples_for(preset: Preset, rows_by_file: dict[str, list[dict]]
                ) -> list[tuple[str, dict, dict]]:
    """``[(run_id, summary, full row or {}), ...]`` for a preset.

    The summary in the registry is what survives ``out/`` being wiped; the
    full row is what carries the preview image and the whole per-player
    fairness profile. Both, so the report is as rich as the data still
    present and no poorer than the registry.
    """
    out = []
    for cap in sorted(preset.captures, key=lambda c: c.captured_utc):
        rows = rows_by_file.get(cap.results, [])
        by_index = {}
        for r in rows:
            if r.get("region") == (cap.region or preset.name):
                by_index[r.get("sample_index")] = r
        for s in cap.samples:
            out.append((cap.run_id, s, by_index.get(s.get("sample_index"), {})))
    return out


def aggregate(samples: list[dict]) -> dict:
    """Median across samples of each per-sample per-player median.

    Two levels on purpose. Inside a sample the unit is a player (CLAUDE.md);
    across samples the median resists the one generation that placed nothing
    where a mean would not.
    """
    if not samples:
        return {}
    def med(key, sub=None):
        vals = []
        for s in samples:
            f = s.get("fairness") or {}
            v = (f.get(sub) or {}).get(key) if sub else (f.get(key) if sub is None and key in f else s.get(key))
            if v is not None:
                vals.append(v)
        return _median(vals) if vals else None
    out = {
        "n": len(samples),
        "iou": _median([s["iou_10m"] for s in samples if s.get("iou_10m") is not None]),
        "land_pct": _median([s["land_pct"] for s in samples if s.get("land_pct") is not None]),
        "n_tcs": _median([s["n_tcs"] for s in samples if s.get("n_tcs") is not None]),
        "sep": _median([s["min_tc_separation"] for s in samples
                        if s.get("min_tc_separation") is not None]),
        "masses": _median([s["landmasses_with_a_player"] for s in samples
                           if s.get("landmasses_with_a_player") is not None]),
        "reach": _median([s["reachable_fraction"] for s in samples
                          if s.get("reachable_fraction") is not None]),
    }
    for k in KINDS:
        out[f"med_{k}"] = med(k, "median")
        out[f"min_{k}"] = med(k, "min")
    out["land_med"] = med("land_exclusive_median")
    out["land_min"] = med("land_exclusive_min")
    out["wood_med"] = med("wood_exclusive_median")
    out["radius"] = med("ownership_radius")
    if out["land_med"] and out["land_min"]:
        out["land_ratio"] = round(out["land_min"] / out["land_med"], 2)
    return out


def stock_rows() -> list[tuple[str, str, dict]]:
    """``[(cohort, map, aggregate), ...]`` from the stock capture baseline."""
    if not BASELINE.is_file():
        return []
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    by_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in data:
        if r["cohort"] in ("arabia", "stock"):
            by_map[(r["cohort"], r["map"])].append(
                summarize_row({"fairness": r["fairness"]}))
    return [(c, m, aggregate(s)) for (c, m), s in sorted(by_map.items())]


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;
  background:#11151a;color:#dfe6ee}
main{max-width:1500px;margin:0 auto;padding:24px 28px 80px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:34px 0 8px}
h3{font-size:16px;margin:22px 0 6px;color:#9fd0ff}
.meta{color:#8b9bb0;font-size:12.5px;margin:0 0 18px}
p.lead{color:#b9c6d6;max-width:80ch}
table{border-collapse:collapse;font-size:12.5px;margin:8px 0 4px}
th,td{border:1px solid #263140;padding:3px 7px;text-align:right;
  white-space:nowrap}
th{background:#18202a;color:#9fb4cc;font-weight:600}
td.l,th.l{text-align:left}
tr.stock td{background:#161d17;color:#a9c9ab}
tr.arabia td{background:#1b1d13;color:#ddd39a;font-weight:600}
tr.sub td{background:#141a21;color:#8b9bb0}
.card{border:1px solid #263140;border-radius:8px;padding:14px 16px;
  margin:14px 0;background:#141a21}
.wrap{overflow-x:auto}
code{background:#0d1116;padding:1px 5px;border-radius:4px;font-size:12px;
  color:#cfe3ff}
.args{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
  color:#cfe3ff;word-break:break-all}
.samples{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px}
.sample{width:300px}
.sample img{width:300px;border:1px solid #263140;border-radius:6px;
  background:#000}
.sample .cap{font-size:11.5px;color:#8b9bb0;margin-top:3px}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:9px;
  border:1px solid #38506b;color:#9fd0ff;margin-left:6px}
.gone{color:#e08b8b}
details{margin:8px 0}
summary{cursor:pointer;color:#9fd0ff;font-size:12.5px}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));
  gap:2px 14px;font-size:12px;margin-top:6px}
.pgrid div{border-bottom:1px dotted #263140;display:flex;
  justify-content:space-between;gap:8px}
.pgrid span:last-child{color:#cfe3ff}
"""

COMPARE_COLS = [
    ("n", "N", 0), ("land_pct", "land %", 1), ("iou", "IoU", 3),
    ("n_tcs", "TCs", 0), ("sep", "min TC sep", 1),
    ("masses", "landmasses", 1), ("reach", "reachable", 2),
]


def compare_table(rows: list[tuple[str, str, dict]]) -> str:
    """rows: (css class, label, aggregate)."""
    head = "".join(f"<th>{esc(l)}</th>" for _, l, _ in COMPARE_COLS)
    head += "".join(f"<th>{esc(KIND_LABEL[k])}</th>" for k in KINDS)
    head += "<th>land</th><th>worst land</th><th>min/med</th><th>wood</th>"
    body = []
    for cls, label, a in rows:
        if not a:
            body.append(f'<tr class="{cls}"><td class="l">{esc(label)}</td>'
                        f'<td colspan="{len(COMPARE_COLS)+len(KINDS)+4}">'
                        f'no capture on record</td></tr>')
            continue
        cells = "".join(f"<td>{fmt(a.get(k), nd)}</td>" for k, _, nd in COMPARE_COLS)
        cells += "".join(f"<td>{fmt(a.get(f'med_{k}'), 0)}"
                         + (f" <span style='color:#7f8fa3'>/{fmt(a.get(f'min_{k}'), 0)}</span>"
                            if a.get(f"min_{k}") is not None else "")
                         + "</td>" for k in KINDS)
        cells += (f"<td>{fmt(a.get('land_med'), 0)}</td>"
                  f"<td>{fmt(a.get('land_min'), 0)}</td>"
                  f"<td>{fmt(a.get('land_ratio'), 2)}</td>"
                  f"<td>{fmt(a.get('wood_med'), 0)}</td>")
        body.append(f'<tr class="{cls}"><td class="l">{esc(label)}</td>{cells}</tr>')
    return (f'<div class="wrap"><table><tr><th class="l">map</th>{head}</tr>'
            + "".join(body) + "</table></div>")


def preset_card(p: Preset, samples: list[tuple[str, dict, dict]],
                archive: Path | None) -> str:
    params = "".join(
        f"<div><span>{esc(k)}</span><span>{esc(v)}</span></div>"
        for k, v in sorted(p.params.items()) if not k.startswith("_"))
    builds = []
    for b in p.builds:
        alive = [x for x in b.paths if (REPO / x).is_file()]
        builds.append(
            f"<li><code>{b.sha256[:12]}</code> {b.bytes:,} B, built "
            f"{esc(b.built_utc[:10])} at <code>{esc(b.src_commit)}</code>"
            + (f" &middot; {esc(b.summary)}" if b.summary else "")
            + "<br>" + "<br>".join(
                f"<span class='args'>{esc(x)}</span>" if (REPO / x).is_file()
                else f"<span class='args gone'>{esc(x)} (gone)</span>"
                for x in b.paths)
            + f"<br><span class='cap'>{len(alive)}/{len(b.paths)} copies on "
              f"disk</span></li>")

    caps = []
    for c in sorted(p.captures, key=lambda c: c.captured_utc):
        live = sum(1 for s in c.scenarios if (REPO / s).is_file())
        caps.append(
            f"<li><b>{esc(c.run_id)}</b> N={c.n_samples} &middot; "
            f"{esc(c.captured_utc[:10])} &middot; commit "
            f"<code>{esc(c.commit)}</code> "
            f"<span class='cap'>({esc(c.commit_source)})</span> &middot; "
            f"filed as {esc(c.region or p.name)} &middot; {live}/"
            f"{len(c.scenarios)} scenarios on disk"
            + (f" &middot; <a href='{esc(Path(c.report).name)}'>report</a>"
               if c.report else "") + "</li>")

    tiles = []
    for run, s, row in samples:
        img = row.get("preview_png_b64")
        f = s.get("fairness") or {}
        med = f.get("median") or {}
        tiles.append(
            "<div class='sample'>"
            + (f"<img src='{img}' alt=''>" if img else
               "<div class='cap'>preview not in results.jsonl</div>")
            + f"<div class='cap'>{esc(run)} s{s.get('sample_index')} &middot; "
              f"IoU {fmt(s.get('iou_10m'), 3)} &middot; land "
              f"{fmt(s.get('land_pct'))}% &middot; {s.get('n_tcs')} TCs "
              f"&middot; sep {fmt(s.get('min_tc_separation'))} &middot; "
              f"{s.get('landmasses_with_a_player')} landmass(es), reach "
              f"{fmt(s.get('reachable_fraction'), 2)}"
            + (f"<br>median/player: gold {fmt(med.get('gold'),0)}, stone "
               f"{fmt(med.get('stone'),0)}, food "
               f"{fmt(med.get('forage'),0)}/{fmt(med.get('sheep'),0)}/"
               f"{fmt(med.get('deer'),0)}/{fmt(med.get('boar'),0)}, land "
               f"{fmt(f.get('land_exclusive_median'),0)} "
               f"(worst {fmt(f.get('land_exclusive_min'),0)}) @ r"
               f"{fmt(f.get('ownership_radius'),0)}" if med else "")
            + "</div></div>")

    archived = ""
    if archive is not None:
        links = archive_preset(p, archive)
        if links:
            archived = ("<h4>archived beside this report</h4><ul>"
                        + "".join(f"<li><span class='args'>{esc(x)}</span></li>"
                                  for x in links) + "</ul>")

    return f"""
<div class="card" id="{esc(p.label)}">
  <h3>{esc(p.name)} <span class="tag">{esc(p.status)}</span>
      <span class="tag">{esc(p.label)}</span></h3>
  <p class="meta">{esc(p.describe_window())} &middot; window
     <code>{p.window_hash[:8]}</code> &middot; params
     <code>{p.params_hash[:8]}</code>
     {(' &middot; also known as ' + esc(', '.join(p.origin['also_known_as'])))
      if p.origin.get('also_known_as') else ''}</p>
  {f'<p class="lead">{esc(p.note)}</p>' if p.note else ''}
  <p class="args">{esc(p.command)}</p>
  {''.join(f'<p class="meta">legacy: {esc(l)}</p>' for l in p.legacy_notes)}
  <details><summary>complete resolved parameter set ({len(p.params) - 2}
     values, defaults included)</summary>
     <div class="pgrid">{params}</div></details>
  <details><summary>builds ({len(p.builds)}) and captures
     ({p.n_captured} samples in {len(p.captures)} runs)</summary>
     <ul>{''.join(builds) or '<li>no build on record</li>'}</ul>
     <ul>{''.join(caps) or '<li>never captured</li>'}</ul>
     {archived}</details>
  <div class="samples">{''.join(tiles) or
     "<div class='cap'>no samples on record</div>"}</div>
</div>"""


def archive_preset(p: Preset, data_dir: Path) -> list[str]:
    """Copy a preset's build and scenarios beside the report.

    Off by default. The project's storage policy is index-only - the
    registry is committed, the artifacts are not - so making a set durable
    is a deliberate act for a set worth it, rather than the side effect of
    writing a report.
    """
    out = []
    dest = data_dir / p.label
    dest.mkdir(parents=True, exist_ok=True)
    hit = p.find_build(REPO)
    if hit:
        _b, src = hit
        target = dest / f"{p.label}.rms"
        shutil.copyfile(src, target)
        out.append(target.relative_to(REPO).as_posix())
    for c in p.captures:
        for s in c.scenarios:
            src = REPO / s
            if src.is_file():
                target = dest / f"{c.run_id}__{Path(s).name}"
                shutil.copyfile(src, target)
                out.append(target.relative_to(REPO).as_posix())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--presets", nargs="+", metavar="LABEL", default=None)
    ap.add_argument("--status", choices=("shipped", "candidate", "screened",
                                        "retired"))
    ap.add_argument("--window", metavar="HASH_PREFIX")
    ap.add_argument("--title", default="Preset report")
    ap.add_argument("--intro", default="",
                    help="a paragraph of context, shown under the title")
    ap.add_argument("--no-stock", action="store_true",
                    help="leave out the stock/Arabia reference rows")
    ap.add_argument("--archive", action="store_true",
                    help="copy each preset's build and scenarios into "
                         "reports/<stamp>_preset_report_data/")
    ap.add_argument("--slug", default="presets",
                    help="suffix for the report filename")
    args = ap.parse_args()

    reg = Registry(REPO).load()
    presets = reg.select(args.presets, status=args.status)
    if args.window:
        presets = [p for p in presets if p.window_hash.startswith(args.window)]
    if not presets:
        sys.exit("no presets selected")

    rows_by_file = load_rows({c.results for p in presets for c in p.captures})
    per_preset = {p.label: samples_for(p, rows_by_file) for p in presets}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    data_dir = REPO / "reports" / f"{stamp}_preset_report_data_{args.slug}"
    archive = data_dir if args.archive else None

    compare = []
    for p in presets:
        agg = aggregate([s for _run, s, _row in per_preset[p.label]])
        compare.append(("", f"{p.name}  [{p.label}]", agg))
    if not args.no_stock:
        for cohort, name, agg in stock_rows():
            compare.append(("arabia" if cohort == "arabia" else "stock",
                            f"{name}  (stock)", agg))

    total = sum(len(v) for v in per_preset.values())
    cards = "".join(preset_card(p, per_preset[p.label], archive) for p in presets)
    html_doc = f"""<!doctype html>
<meta charset="utf-8"><title>{esc(args.title)}</title>
<style>{CSS}</style>
<main>
<h1>{esc(args.title)}</h1>
<p class="meta">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
 &middot; commit <code>{esc(git_commit())}</code>
 &middot; {len(presets)} presets &middot; {total} captured samples
 &middot; no engine time: every number and every picture here was already on
 disk</p>
{f'<p class="lead">{esc(args.intro)}</p>' if args.intro else ''}
<p class="lead">Each row is the median across that preset's samples of the
 median across its 8 players, within
 <code>fairness.OWNERSHIP_RADIUS</code> = 30 walked tiles. The small grey
 number beside each resource is the <em>worst-off</em> player. Stock rows
 come from <code>out/resource_baseline.json</code>; Arabia is the reference
 and is held out of the stock band rather than averaged into it. Counts are
 resources, not objects. Nothing here is a score.</p>
<h2>Side by side</h2>
{compare_table(compare)}
<h2>Presets</h2>
{cards}
</main>
"""
    out = REPO / "reports" / f"{stamp}_preset_report_{args.slug}.html"
    out.write_text(html_doc, encoding="utf-8")
    print(f"{len(presets)} presets, {total} samples -> {out}")
    if archive:
        print(f"archived artifacts -> {data_dir}")
    for p in presets:
        print(f"  {p.label:26s} {len(per_preset[p.label]):3d} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
