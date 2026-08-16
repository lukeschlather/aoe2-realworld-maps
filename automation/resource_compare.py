"""Compare per-player resource supply against the stock maps.

Stock is the yardstick: those maps are what "a reasonable amount" means,
and this project has no better definition. Arabia is held out as its own
reference rather than averaged into a stock mean - it is the open-land
map every discussion of this project's budget has been anchored on, and a
mean taken across Arabia, Black Forest and Team Islands describes no real
map at all.

Every number comes from ``rwmaps.fairness`` run over the archived captures
by ``resource_baseline.py``, so all cohorts are measured with one model.

**The unit is a player, not a map.** A map-wide total answers no question
worth asking - what matters is what one player has within walking range -
so a map's row pools every player of every capture of that map and reports
the distribution of those player observations. With 8 players a capture,
Arabia's three captures give 24 player observations and a shipped region's
ten give 80.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent

KINDS = ("gold", "stone", "forage", "sheep", "deer", "boar")

COHORT_ORDER = ["arabia", "stock", "shipped", "candidate", "retired"]
COHORT_LABEL = {
    "arabia": "Arabia (reference)",
    "stock": "other stock",
    "shipped": "shipped now",
    "candidate": "candidate",
    "retired": "RETIRED - not in the mod",
}

#: Retired maps get their own cohort rather than a tag inside "shipped".
#: They were listed among the shipped regions with a "(retired)" note and
#: that reads, at a glance, as though they still ship. They do not: the mod
#: has eight maps and none of these is one of them. Their captures stay
#: because they are the evidence for the retirement, not despite it.
def cohort_of(row: dict) -> str:
    if row.get("retired"):
        return "retired"
    return row["cohort"]


def load(path: Path | None = None) -> list[dict]:
    path = path or REPO / "out" / "resource_baseline.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def pool(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Pool player observations per (cohort, map).

    Returns ``{(cohort, map): {"n_caps", "retired", "counts": {kind: [...]},
    "nearest": {kind: [...]}, "wood": [...]}}`` where every list holds one
    entry per player per capture.
    """
    out: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"n_caps": 0, "retired": False,
                 "counts": defaultdict(list), "nearest": defaultdict(list),
                 "wood": [], "land": [], "unclaimed": defaultdict(list)})
    for r in rows:
        key = (cohort_of(r), r["map"])
        e = out[key]
        e["n_caps"] += 1
        e["retired"] = e["retired"] or r.get("retired", False)
        fair = r["fairness"]
        for kind in KINDS:
            e["unclaimed"][kind].append(fair["unclaimed"].get(kind, 0))
        for pp in fair["per_player"].values():
            for kind in KINDS:
                e["counts"][kind].append(pp["counts"].get(kind, 0))
                near = pp["nearest"].get(kind)
                if near is not None:
                    e["nearest"][kind].append(near)
            e["wood"].append(pp["wood"]["forest_exclusive"]
                             + pp["wood"]["forest_contested"])
            ld = pp.get("land")
            if ld:
                e["land"].append(ld["land_exclusive"] + ld["land_contested"])
    return out


def _stat(values: list[float]) -> tuple[float, float, float] | None:
    if not values:
        return None
    return min(values), statistics.median(values), max(values)


def _delta(value: float | None, ref: float | None) -> str:
    if value is None or ref is None:
        return "&mdash;"
    d = value - ref
    if abs(d) < 0.05:
        return '<span class="dim">0</span>'
    cls = "up" if d > 0 else "down"
    return f'<span class="{cls}">{d:+.0f}</span>'


def kind_table(pooled: dict, kind: str) -> str:
    """One table for one resource: every map, its players' distribution."""
    ref = pooled.get(("arabia", "Arabia"))
    ref_count = statistics.median(ref["counts"][kind]) if ref else None
    ref_near = (statistics.median(ref["nearest"][kind])
                if ref and ref["nearest"][kind] else None)

    rows = []
    for cohort in COHORT_ORDER:
        keys = sorted(k for k in pooled if k[0] == cohort)
        for i, key in enumerate(keys):
            e = pooled[key]
            c = _stat(e["counts"][kind])
            n = _stat(e["nearest"][kind])
            obs = len(e["counts"][kind])
            zeros = sum(1 for v in e["counts"][kind] if v == 0)
            zpct = 100.0 * zeros / obs if obs else 0.0
            zcell = ("<span class='dim'>&middot;</span>" if not zeros
                     else f"<b class='bad'>{zeros}</b>/{obs}"
                          f" <span class='dim'>({zpct:.0f}%)</span>")
            unc = statistics.median(e["unclaimed"][kind]) if e["unclaimed"][kind] else 0
            label = key[1]
            cohort_cell = (f'<td rowspan="{len(keys)}" class="cohort">'
                           f'{COHORT_LABEL[cohort]}</td>' if i == 0 else "")
            rows.append(
                f'<tr class="c-{cohort}">{cohort_cell}'
                f'<th>{label}</th>'
                f'<td class="dim">{e["n_caps"]}&times;8={obs}</td>'
                f'<td>{c[0]:.0f}</td><td><b>{c[1]:.0f}</b></td><td>{c[2]:.0f}</td>'
                f'<td>{_delta(c[1], ref_count)}</td>'
                + (f'<td>{n[1]:.0f}</td><td>{n[2]:.0f}</td>' if n
                   else '<td>&mdash;</td><td>&mdash;</td>')
                + f'<td>{_delta(n[1] if n else None, ref_near)}</td>'
                f'<td>{zcell}</td><td class="dim">{unc:.0f}</td></tr>')

    return f"""
    <h3 id="res-{kind}">{kind}</h3>
    <table class="cmp">
      <tr><th>cohort</th><th>map</th><th>players</th>
          <th colspan="3">count per player</th><th>&Delta;med</th>
          <th colspan="2">nearest (tiles)</th><th>&Delta;med</th>
          <th>players with none</th><th>unclaimed</th></tr>
      <tr class="sub"><th></th><th></th><th></th>
          <th>min</th><th>med</th><th>max</th><th>vs&nbsp;Arabia</th>
          <th>med</th><th>max</th><th>vs&nbsp;Arabia</th><th></th><th>med</th></tr>
      {''.join(rows)}
    </table>"""


def wood_table(pooled: dict) -> str:
    """Forest a player can actually reach, same pooling as the kinds."""
    ref = pooled.get(("arabia", "Arabia"))
    ref_med = statistics.median(ref["wood"]) if ref else None
    rows = []
    for cohort in COHORT_ORDER:
        keys = sorted(k for k in pooled if k[0] == cohort)
        for i, key in enumerate(keys):
            e = pooled[key]
            w = _stat(e["wood"])
            label = key[1]
            cohort_cell = (f'<td rowspan="{len(keys)}" class="cohort">'
                           f'{COHORT_LABEL[cohort]}</td>' if i == 0 else "")
            rows.append(
                f'<tr class="c-{cohort}">{cohort_cell}<th>{label}</th>'
                f'<td class="dim">{e["n_caps"]}&times;8={len(e["wood"])}</td>'
                f'<td>{w[0]:,.0f}</td><td><b>{w[1]:,.0f}</b></td>'
                f'<td>{w[2]:,.0f}</td><td>{_delta(w[1], ref_med)}</td></tr>')
    return f"""
    <h3 id="res-wood">wood</h3>
    <p class="legend">Forest tiles a player can reach
      (exclusive + contested), not the map's forest total.</p>
    <table class="cmp">
      <tr><th>cohort</th><th>map</th><th>players</th>
          <th>min</th><th>med</th><th>max</th><th>&Delta;med vs Arabia</th></tr>
      {''.join(rows)}
    </table>"""


def land_table(pooled: dict) -> str:
    """Buildable land per player - the resource that cannot be topped up.

    The ``min/med`` column is the one to read. A player can walk further
    for gold; there is no walking further for somewhere to put a farm, so
    how far the worst-off player sits below the middle of their own map is
    the whole question. Arabia runs 0.83-0.92.
    """
    ref = pooled.get(("arabia", "Arabia"))
    ref_med = statistics.median(ref["land"]) if ref and ref["land"] else None
    rows = []
    for cohort in COHORT_ORDER:
        keys = sorted(k for k in pooled if k[0] == cohort)
        for i, key in enumerate(keys):
            e = pooled[key]
            if not e["land"]:
                continue
            v = _stat(e["land"])
            ratio = v[0] / v[1] if v[1] else 0.0
            cls = " class='bad'" if ratio < 0.7 else ""
            label = key[1]
            cohort_cell = (f'<td rowspan="{len(keys)}" class="cohort">'
                           f'{COHORT_LABEL[cohort]}</td>' if i == 0 else "")
            rows.append(
                f'<tr class="c-{cohort}">{cohort_cell}<th>{label}</th>'
                f'<td class="dim">{e["n_caps"]}&times;8={len(e["land"])}</td>'
                f'<td>{v[0]:,.0f}</td><td><b>{v[1]:,.0f}</b></td>'
                f'<td>{v[2]:,.0f}</td>'
                f'<td{cls}>{ratio:.2f}</td>'
                f'<td>{_delta(v[1], ref_med)}</td></tr>')
    return f"""
    <h3 id="res-land">land</h3>
    <p class="legend">Buildable tiles a player can reach: dry (shallows
      excluded - a ford is a route, not ground) and unforested (clearable,
      but not land you have yet). <b>min/med</b> is how far the worst-off
      player on that map sits below its own middle; Arabia runs 0.83-0.92.</p>
    <table class="cmp">
      <tr><th>cohort</th><th>map</th><th>players</th>
          <th>min</th><th>med</th><th>max</th><th>min/med</th>
          <th>&Delta;med vs Arabia</th></tr>
      {''.join(rows)}
    </table>"""


def comparison_html(rows: list[dict]) -> str:
    if not rows:
        return ('<section><h2>Against stock</h2><p class="missing">'
                'no out/resource_baseline.json - run '
                '<code>automation/resource_baseline.py</code></p></section>')
    pooled = pool(rows)
    n_caps = len(rows)
    maps = len(pooled)
    tables = ("".join(kind_table(pooled, k) for k in KINDS)
              + wood_table(pooled) + land_table(pooled))
    return f"""
  <section>
    <h2>Against stock</h2>
    <p class="blurb">
      {n_caps} archived captures across {maps} maps, all re-profiled with the
      current <code>rwmaps.fairness</code> model so the cohorts are
      comparable. No new engine time; the stock benchmarks and the shipped
      N=10 pass were captured earlier and analysed with older models (the
      stock ones with no fairness block at all).
    </p>
    <div class="caveat">
      <b>The unit is a player, not a map.</b> Each row pools every player of
      every capture of that map, so Arabia's three captures give 24 player
      observations and a shipped region's ten give 80. A map-wide total
      answers no question worth asking; what a single player has within
      walking range does.
      <br><br>
      <b>Arabia is held out</b> rather than averaged into a stock mean. A
      mean over Arabia, Black Forest and Team Islands describes no real map.
      The other stock maps are shown so the <i>range</i> of reasonable is
      visible next to the reference.
      <br><br>
      <b>Capture counts differ by cohort</b> &mdash; 3 per stock map, 10 per
      shipped region, 2 per candidate. Deltas here are indicative of where a
      map sits, not a settled comparison; the candidate rows in particular
      rest on two generations each.
    </div>
    {tables}
  </section>"""


def historical_html(rows: list[dict], matrix_fn) -> str:
    """Per-capture matrices for the stock and shipped cohorts.

    Same matrix the candidate captures get, so a stock map and one of ours
    can be read against each other line by line rather than through a
    summary. Collapsed by map, because 129 of them open at once is not a
    document anyone reads.
    """
    if not rows:
        return ""
    by_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        c = cohort_of(r)
        if c in ("arabia", "stock", "shipped", "retired"):
            by_map[(c, r["map"])].append(r)

    blocks = []
    for cohort in ("arabia", "stock", "shipped", "retired"):
        keys = sorted(k for k in by_map if k[0] == cohort)
        if not keys:
            continue
        blocks.append(f'<h3>{COHORT_LABEL[cohort]}</h3>')
        for key in keys:
            caps = sorted(by_map[key], key=lambda r: r["sample"])
            retired = ' <span class="dim">(retired from the mod)</span>' \
                if caps[0].get("retired") else ""
            inner = "".join(
                f'<div class="sample"><p class="legend">{c["sample"]}</p>'
                f'{matrix_fn(c["fairness"])}</div>' for c in caps)
            blocks.append(
                f'<details class="region"><summary>{key[1]}{retired} '
                f'<span class="dim">&mdash; {len(caps)} captures</span>'
                f'</summary><div class="samples">{inner}</div></details>')
    return f"""
  <section>
    <h2>Historical captures, same matrix</h2>
    <p class="blurb">Every archived stock and shipped capture, with the
      identical per-player matrix the candidates get above. Nothing here was
      recaptured; these are the existing files re-read with the current
      model.</p>
    {''.join(blocks)}
  </section>"""
