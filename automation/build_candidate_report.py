"""Report over a ``mod_capture.py --region-set`` pass: what the engine
actually built for each candidate window, turned the way the game draws it.

Different from build_mod_report.py in three ways that matter here:

1. **Orientation.** ``sample_analysis`` renders its preview in grid space,
   which for a ``--rotate 45`` map is the geography tilted 45 degrees - not
   how anyone will ever see it. Every preview here is turned
   counter-clockwise by ``thumbnail.ICON_ROTATION``, the turn the engine
   itself applies, so the picture is the minimap. North is up in it exactly
   when the window's own ``--rotate`` is 45.

2. **Waterbodies.** Whether the lakes come out distinct is the open question
   about the Great Lakes windows, and no existing metric answers it. This
   measures it directly off the captured terrain: enclosed water bodies,
   with sizes and centroids, so "the lakes merged" and "five lakes" are
   different rows rather than the same IoU.

3. **N=2.** These are exploratory captures, at the sample count
   CLAUDE.md prescribes for breadth over parameters. Per-sample facts are
   listed individually and never averaged into a fairness claim - two
   samples cannot settle one, and presenting them as if they could is the
   specific mistake that document warns about.

Usage:
    uv run python automation/build_candidate_report.py --run-id candidates_n2
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from rwmaps import terrain as T  # noqa: E402
from rwmaps import thumbnail  # noqa: E402
from rwmaps.scx_read import read_capture  # noqa: E402
from rwmaps import resource_value as rv  # noqa: E402

from candidate_set import CANDIDATES  # noqa: E402
import resource_compare  # noqa: E402

#: Same floors the pre-generation screen used, so a captured map's structure
#: can be read against the truth mask it was cut from.
LANDMASS_FLOOR = 120
WATERBODY_FLOOR = 60

#: See window_candidates.OFFMAP - a turned grid is a diamond and the reader
#: has to be able to see where the map stops.
OFFMAP = (26, 26, 26)

GROUPS = [
    ("Michigan", "The window asked for by name (was \"GL Michigan-Huron\")."),
    ("Great Lakes", "The rest of the Great Lakes set, generated to see how "
                    "they render. All carry Salish Sea's raster settings."),
    ("Britain", "The shipped window turned north-up, and the same span with "
                "the centre pushed south for a thicker France."),
    ("New regions", "Proposed replacements, on Italy's plain defaults."),
]


def group_of(name: str) -> str:
    if name == "Michigan":
        return "Michigan"
    if name.startswith("GL "):
        return "Great Lakes"
    if name.startswith("Britain"):
        return "Britain"
    return "New regions"


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def pieces(binary: np.ndarray, floor: int, drop_edge: bool = False) -> list[int]:
    """Sizes of connected pieces, largest first."""
    labels, n = ndimage.label(binary, structure=np.ones((3, 3), bool))
    if not n:
        return []
    skip: set[int] = set()
    if drop_edge:
        skip = (set(labels[0].tolist()) | set(labels[-1].tolist())
                | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist()))
    sizes = np.bincount(labels.ravel())
    return sorted((int(sizes[i]) for i in range(1, n + 1)
                   if i not in skip and sizes[i] >= floor), reverse=True)


def structure_of(scenario: Path) -> dict:
    """Landmass and enclosed-waterbody structure of a real capture.

    Read off the terrain grid the engine wrote, not off the truth mask the
    script was cut from - the whole point is that those two differ.
    """
    grid = read_capture(scenario).terrain
    land = ~np.isin(grid, list(T.WATER_IDS))
    return {
        "landmasses": pieces(land, LANDMASS_FLOOR),
        "waterbodies": pieces(~land, WATERBODY_FLOOR, drop_edge=True),
        "land_pct": round(float(land.mean()) * 100, 1),
    }


def turned_preview(b64_png: str, px: int = 420) -> str:
    """The stored grid-space preview, turned into the minimap orientation."""
    raw = base64.b64decode(b64_png.split(",", 1)[-1])
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = img.rotate(thumbnail.ICON_ROTATION, expand=True,
                     resample=Image.BICUBIC, fillcolor=OFFMAP)
    img = img.resize((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def archive(run_out: Path, data_dir: Path, name: str,
            indices: list[int]) -> tuple[str | None, dict[int, str]]:
    dest_dir = data_dir / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    rms_rel = None
    scripts = sorted((run_out / "scripts" / name).rglob("*.rms"),
                     key=lambda p: p.stat().st_mtime)
    if scripts:
        shutil.copyfile(scripts[-1], dest_dir / f"{name}.rms")
        rms_rel = f"{data_dir.name}/{name}/{name}.rms"
    scen: dict[int, str] = {}
    for i in indices:
        src = run_out / name / "raw" / f"sample_{i:03d}.aoe2scenario"
        if src.exists():
            fn = f"{name}__s{i:03d}.aoe2scenario"
            shutil.copyfile(src, dest_dir / fn)
            scen[i] = f"{data_dir.name}/{name}/{fn}"
    return rms_rel, scen


#: Kinds the current fairness model zero-checks, in the order it lists
#: them. ``small_game`` is reported but never zero-checked - plenty of good
#: maps place none - so it is shown last and visually separated.
SPREAD_KINDS = ("gold", "stone", "forage", "sheep", "deer", "boar")
EXTRA_KINDS = ("small_game",)


def spread_table(fair: dict) -> str:
    """Every resource, for every player, from ``rwmaps.fairness``.

    Deliberately NOT a summary. A cross-player min-max per kind hides the
    thing worth seeing - which player is short of what - and a map-wide
    total hides it completely: two maps with identical gold totals play
    differently when one player's share is 4 tiles and another's is 30.
    So this is the full matrix, one row per player, one column per kind,
    and no row of the table is an aggregate over players.

    Cells read ``exclusive+contested @nearest``. Contested counts for both
    players because both can genuinely go and take it; nearest is walked on
    the walkable mask, so water and forest are barriers rather than
    straight-line shortcuts.

    The one footer row is per-resource, not a player total: ``unclaimed``
    is the neutral pool no player can reach at all.

    This replaces ``analyze_capture``'s ``resources`` block, which is kept
    in the record only so past runs stay comparable. That one assigns each
    resource to its single nearest town centre, breaks exact ties by player
    index, and measures straight lines. It disagrees in both directions on
    this run: four players flagged on Britain north-up where two are real,
    and a player with no reachable deer on GL Erie-Ontario missed entirely.
    """
    kinds = SPREAD_KINDS + EXTRA_KINDS
    players = sorted(fair["per_player"], key=int)

    head = "".join(
        f'<th class="{"dim" if k in EXTRA_KINDS else ""}">{k}</th>'
        for k in kinds)
    rows = []
    for p in players:
        pp = fair["per_player"][p]
        cells = []
        for k in kinds:
            excl = pp["exclusive"].get(k, 0)
            cont = pp["contested"].get(k, 0)
            near = pp["nearest"].get(k)
            if excl + cont == 0:
                # The only unambiguous problem: nothing of this kind at all.
                cls = "" if k in EXTRA_KINDS else ' class="bad"'
                cells.append(f"<td{cls}>0</td>")
                continue
            split = f"{excl}+{cont}" if cont else f"{excl}"
            dist = "" if near is None else \
                f" <span class='dim'>@{near:g}</span>"
            cells.append(f"<td>{split}{dist}</td>")
        # Objects are the ground truth; the wallet is what they are worth.
        # See rwmaps.resource_value for why both are shown and why the
        # food kinds are not collapsed into the food total alone.
        w = rv.wallet(pp["counts"],
                      pp["wood"]["forest_exclusive"]
                      + pp["wood"]["forest_contested"])
        cells.append(f'<td class="cur">{w[rv.FOOD]:,}</td>'
                     f'<td class="cur">{w[rv.WOOD]:,}</td>'
                     f'<td class="cur">{w[rv.GOLD]:,}</td>'
                     f'<td class="cur">{w[rv.STONE]:,}</td>')
        rows.append(f"<tr><th>P{p}</th>{''.join(cells)}</tr>")

    unclaimed = "".join(f'<td>{fair["unclaimed"].get(k, 0)}</td>' for k in kinds)
    return f"""
          <table class="spread">
            <tr><th>player</th>{head}
                <th class="cur">food</th><th class="cur">wood</th>
                <th class="cur">gold</th><th class="cur">stone</th></tr>
            {''.join(rows)}
            <tr class="foot"><th>unclaimed</th>{unclaimed}
                <td colspan="4"></td></tr>
          </table>
          <p class="legend">cells are
            <code>exclusive+contested&nbsp;@nearest</code> objects, then
            what they are worth. Gold 800, stone 350, berries 125, deer 140
            and boar 340 are confirmed in game; sheep, small game and fish
            are still assumed &mdash; see <code>rwmaps.resource_value</code>.
            Object counts are ground truth either way. Contested counts
            for both players, distance is walked on the walkable mask.
            <b>unclaimed</b> is the neutral pool no player can reach.</p>"""


def wood_water_table(fair: dict) -> str:
    """Wood and water per player - the two supplies not in the object list.

    Forest is classified by the same exclusive/contested rule rather than
    by a disc around the town centre, which used to claim forest across
    water and forest behind another player. ``unclaimed`` here is the wood
    nobody starts near, which on Britain is most of France.
    """
    players = sorted(fair["per_player"], key=int)
    fo = fair["forest"]
    lt = fair.get("land", {})
    fo_land = (f'{lt.get("unclaimed", 0):,} land tiles of {lt.get("total", 0):,}'
               if lt else "")
    rows = []
    for p in players:
        w = fair["per_player"][p]["wood"]
        aq = fair["per_player"][p]["water"]
        ld = fair["per_player"][p].get("land", {"land_exclusive": 0,
                                                "land_contested": 0})

        def fish(near_key: str, n_key: str) -> str:
            n, d = aq.get(n_key, 0), aq.get(near_key)
            if not n and d is None:
                return "<td>0</td>"
            return f"<td>{n} <span class='dim'>@{d:g}</span></td>" if d is not None \
                else f"<td>{n}</td>"

        rows.append(
            f"<tr><th>P{p}</th>"
            f"<td>{w['forest_exclusive']:,}</td>"
            f"<td>{w['forest_contested']:,}</td>"
            f"<td>{w['open_tiles_within_10']:,}</td>"
            f"<td>{w['open_tiles_within_20']:,}</td>"
            f"<td>{w['stragglers_within_6']}</td>"
            f"<td class=\"cur\">{ld['land_exclusive']:,}</td>"
            f"<td class=\"cur\">{ld['land_contested']:,}</td>"
            + fish("nearest_shore_fish", "shore_fish_within_20")
            + fish("nearest_deep_fish", "deep_fish_within_20")
            + fish("nearest_whale", "whale_within_20")
            + "</tr>")
    return f"""
          <table class="spread">
            <tr><th>player</th><th>forest excl</th><th>forest cont</th>
                <th>open&le;10</th><th>open&le;20</th><th>stragglers&le;6</th>
                <th class="cur">land excl</th><th class="cur">land cont</th>
                <th>shore fish&le;20</th><th>deep fish&le;20</th>
                <th>whale&le;20</th></tr>
            {''.join(rows)}
            <tr class="foot"><th>unclaimed</th>
                <td colspan="2">{fo['unclaimed']:,} forest tiles
                  <span class="dim">of {fo['total']:,},
                  {fo['share_of_land']*100:.0f}% of land is wood</span></td>
                <td colspan="6"></td>
                <td class="cur" colspan="2">{fo_land}</td></tr>
          </table>"""


def sample_card(r: dict, struct: dict, scen_rel: str | None) -> str:
    p = r["placement"]
    fair = r["fairness"]
    zero = fair["zero_kinds_by_player"] or {}
    zero_txt = ("none" if not zero else
                "; ".join(f"P{k}: {', '.join(v)}" for k, v in sorted(zero.items())))
    a = r["aesthetic"]
    masses = ", ".join(f"{v:,}" for v in struct["landmasses"][:6]) or "&mdash;"
    waters = ", ".join(f"{v:,}" for v in struct["waterbodies"][:8]) or "&mdash;"
    link = (f'<a class="fl" href="{scen_rel}">.aoe2scenario</a>'
            if scen_rel else '<span class="fl missing">capture missing</span>')
    return f"""
      <div class="sample">
        <img src="{r['_preview']}" loading="lazy" alt="capture, minimap orientation">
        <table>
          <tr><th>IoU vs 10m truth</th><td>{a['iou_10m']:.3f}</td></tr>
          <tr><th>land</th><td>{struct['land_pct']}%</td></tr>
          <tr><th>landmasses</th><td>{masses}</td></tr>
          <tr><th>enclosed water</th><td>{waters}</td></tr>
          <tr><th>islands preserved</th>
              <td>{a['islands_preserved']}/{a['islands_total']}
                  ({a['preserved_fraction']*100:.0f}%),
                  {a['islands_deleted']} deleted, {a['islands_merged']} merged</td></tr>
          <tr><th>landmasses with a player</th>
              <td>{p['n_landmasses_with_a_player']}</td></tr>
          <tr><th>min TC separation</th><td>{p['min_tc_separation']}</td></tr>
          <tr><th>pairwise land-reachable</th>
              <td>{p['pairwise_land_reachable_fraction']}</td></tr>
          <tr><th>players with zero of a kind</th>
              <td class="{'bad' if zero else ''}">{zero_txt}</td></tr>
          <tr><th>file</th><td>{link}</td></tr>
        </table>
        {spread_table(fair)}
        {wood_water_table(fair)}
      </div>"""


def region_section(name: str, extra_args: list[str], records: list[dict],
                   structs: dict[int, dict], rms_rel: str | None,
                   scen: dict[int, str]) -> str:
    if not records:
        return (f'<article class="region"><h3>{name}</h3>'
                f'<p class="missing">no samples captured</p></article>')
    r0 = records[0]
    rotate = r0["rotate"]
    rms_link = (f'<a class="fl" href="{rms_rel}">{name}.rms</a>' if rms_rel
                else '<span class="fl missing">.rms missing</span>')
    cards = "".join(sample_card(r, structs[r["sample_index"]],
                                scen.get(r["sample_index"])) for r in records)
    return f"""
    <article class="region">
      <h3>{name}</h3>
      <table class="params">
        <tr><th>centre</th><td>{r0['lon']}, {r0['lat']}</td>
            <th>span</th><td>{r0['span_km']:g} km
              ({r0['span_km']/240:.2f} km/tile)</td></tr>
        <tr><th>rotate</th><td>{rotate:g}&deg;
              (north up in game: {'yes' if rotate == 45 else 'no'})</td>
            <th>ai_info_map_type</th><td>{r0['ai_map_type']}</td></tr>
        <tr><th>grid</th><td>240&times;240, 8 players, laea</td>
            <th>script</th><td>{rms_link}</td></tr>
        <tr><th>full args</th><td colspan="3"><code>{' '.join(extra_args)}</code></td></tr>
      </table>
      <div class="samples">{cards}</div>
    </article>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    run_out = REPO / "out" / "mod_capture" / args.run_id
    results = run_out / "results.jsonl"
    if not results.exists():
        raise SystemExit(f"no results at {results}")

    by_region: dict[str, list[dict]] = defaultdict(list)
    for line in results.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            by_region[rec["region"]].append(rec)

    stamp_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    data_dir = REPO / "reports" / f"{stamp_id}_candidate_report_data_{args.run_id}"
    data_dir.mkdir(parents=True, exist_ok=True)

    sections: dict[str, list[str]] = defaultdict(list)
    total = 0
    for name, extra_args in CANDIDATES:
        recs = sorted(by_region.get(name, []), key=lambda r: r["sample_index"])
        idx = [r["sample_index"] for r in recs]
        rms_rel, scen = archive(run_out, data_dir, name, idx)
        structs = {}
        for r in recs:
            r["_preview"] = turned_preview(r["preview_png_b64"])
            src = data_dir / name / f"{name}__s{r['sample_index']:03d}.aoe2scenario"
            structs[r["sample_index"]] = (structure_of(src) if src.exists()
                                          else {"landmasses": [], "waterbodies": [],
                                                "land_pct": r["land_pct"]})
            print(f"  {name} s{r['sample_index']}: "
                  f"waters={structs[r['sample_index']]['waterbodies'][:5]}")
        total += len(recs)
        sections[group_of(name)].append(
            region_section(name, extra_args, recs, structs, rms_rel, scen))

    body = []
    for title, blurb in GROUPS:
        if not sections[title]:
            continue
        body.append(f'<section><h2>{title}</h2><p class="blurb">{blurb}</p>'
                    + "".join(sections[title]) + "</section>")

    baseline = resource_compare.load()
    if baseline:
        print(f"baseline: {len(baseline)} archived captures")
    comparison = resource_compare.comparison_html(baseline)
    historical = resource_compare.historical_html(baseline, spread_table)

    html = TEMPLATE.format(
        run_id=args.run_id,
        comparison=comparison,
        historical=historical,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        commit=git_commit(),
        total=total,
        data_dir=data_dir.name,
        body="".join(body),
    )
    out = REPO / "reports" / f"{stamp_id}_candidate_report_{args.run_id}.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n{total} samples -> {out} ({out.stat().st_size/1024:.0f} KB)")
    print(f"-> {data_dir}")


TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>Candidate captures ({run_id})</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ background:#141414; color:#e6e6e6; margin:0 auto; max-width:1240px;
        padding:2rem 1.5rem 5rem; font:15px/1.55 system-ui, sans-serif; }}
 h1 {{ margin:0 0 .3rem; font-size:1.7rem; }}
 h2 {{ margin:2.8rem 0 .4rem; font-size:1.3rem;
       border-bottom:1px solid #333; padding-bottom:.35rem; }}
 h3 {{ margin:0 0 .5rem; font-size:1.05rem; }}
 .meta {{ color:#9a9a9a; font-size:.86rem; margin:0 0 1.4rem; }}
 .blurb {{ color:#bdbdbd; margin:.5rem 0 1.3rem; max-width:72ch; }}
 .region {{ border:1px solid #2e2e2e; border-radius:8px; padding:1rem 1.1rem;
            margin:0 0 1.3rem; background:#191919; }}
 .samples {{ display:flex; gap:1.1rem; flex-wrap:wrap; margin-top:.9rem; }}
 .sample {{ flex:1 1 560px; min-width:520px; }}
 .sample img {{ width:100%; max-width:420px; display:block; border-radius:4px;
                background:#0d1b2a; }}
 table {{ border-collapse:collapse; font-size:.85rem; width:100%; margin-top:.4rem; }}
 th {{ text-align:left; color:#8f8f8f; font-weight:500; vertical-align:top;
       padding:.14rem .8rem .14rem 0; white-space:nowrap; width:1%; }}
 td {{ padding:.14rem 1.2rem .14rem 0; vertical-align:top; }}
 code {{ background:#242424; padding:.05rem .3rem; border-radius:3px;
         font-size:.82rem; }}
 .fl {{ color:#8fc7ff; }}
 .missing {{ color:#c98b6b; }}
 .bad {{ color:#e08a6a; }}
 .dim {{ color:#8f8f8f; }}
 table.spread {{ margin-top:.6rem; font-size:.8rem; }}
 table.spread th {{ color:#8f8f8f; border-bottom:1px solid #333;
                    padding-bottom:.2rem; width:auto; white-space:nowrap; }}
 table.spread td {{ padding:.12rem .7rem .12rem 0; white-space:nowrap; }}
 table.spread td:first-child {{ color:#bdbdbd; }}
 tr.foot th, tr.foot td {{ border-top:1px solid #333; color:#9a9a9a;
                           padding-top:.25rem; }}
 table.spread th {{ text-align:left; }}
 .legend {{ color:#8f8f8f; font-size:.76rem; margin:.3rem 0 .2rem;
            max-width:60ch; }}
 table.cmp {{ font-size:.82rem; margin:.4rem 0 1.6rem; }}
 table.cmp th {{ text-align:left; white-space:nowrap; }}
 table.cmp td {{ text-align:right; padding:.13rem .55rem .13rem 0;
                 white-space:nowrap; }}
 table.cmp td.cohort {{ text-align:left; color:#bdbdbd; vertical-align:top;
                        border-right:1px solid #333; padding-right:.8rem; }}
 table.cmp tr.sub th {{ color:#6f6f6f; font-weight:400; font-size:.75rem;
                        border-bottom:1px solid #333; }}
 table.cmp tr.c-arabia th, table.cmp tr.c-arabia td {{ background:#1d2419; }}
 table.cmp tr.c-candidate th {{ color:#cfe3ff; }}
 table.cmp tr.c-retired th, table.cmp tr.c-retired td {{
   color:#7a6a63; text-decoration:line-through;
   text-decoration-color:#4a3f3a; }}
 .up {{ color:#7fbf7f; }} .down {{ color:#e08a6a; }}
 td.cur, th.cur {{ border-left:1px solid #333; color:#cfc39a; }}
 details.region {{ border:1px solid #2e2e2e; border-radius:8px;
                   padding:.6rem .9rem; margin:0 0 .6rem; background:#191919; }}
 details.region summary {{ cursor:pointer; color:#dcdcdc; }}
 .caveat {{ border-left:3px solid #6b5a2a; background:#1e1a12; padding:.7rem 1rem;
            margin:1.2rem 0; color:#d8d0b8; max-width:80ch; }}
</style>
<h1>Candidate captures</h1>
<p class="meta">Run <code>{run_id}</code> &middot; {total} real engine captures
 &middot; generated {generated_at} &middot; repo commit <code>{commit}</code>
 &middot; scripts and captures in <code>{data_dir}</code></p>

<div class="caveat">
 Every picture is a <b>real engine capture</b>, turned counter-clockwise 45
 degrees so it is the minimap - north is up exactly when the window's
 <code>--rotate</code> is 45. Dots are real town centres and the land
 resources each one can walk to first.
 <br><br>
 <b>Supply comes from <code>rwmaps.fairness</code></b>, the current model:
 every resource is exclusive, contested or unclaimed, contested counts for
 both players because both can genuinely take it, and all distances are
 walked on the walkable mask so water and forest are barriers.
 <code>analyze_capture</code>'s older <code>resources</code> block is still
 in the record for comparability with past runs and is <i>not</i> used
 here - it assigns each resource to its single nearest TC and measures
 straight lines, and on this run it disagreed in both directions: four
 players flagged on Britain north-up where two are real, and a player with
 no reachable deer on GL Erie-Ontario missed entirely.
 <br><br>
 <b>N=2 per window.</b> That is the sample count for exploring breadth, and
 it cannot settle a fairness question. Everything below is listed per
 sample as a plain fact about that capture - not averaged, not scored, and
 not a comparison between windows. The one thing treated as an outright
 problem is a player with literally zero of some resource kind
 (<code>small_game</code> excluded: plenty of good maps place none).
</div>

{comparison}

{body}

{historical}
"""


if __name__ == "__main__":
    main()
