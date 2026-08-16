"""Run the CURRENT fairness model over every capture this project already
has, so stock maps, the shipped maps and new candidates are all measured
the same way.

No new engine time. Everything read here was captured earlier; the point
is that the older passes were analyzed with the older model (or, for the
stock benchmarks, with no fairness block at all - ``stock_capture.py``
predates ``rwmaps.fairness``). Re-profiling from the archived
``.aoe2scenario`` files is what makes the three cohorts comparable at all:
a delta between a number from the nearest-TC model and a number from the
exclusive/contested model would be meaningless.

All three cohorts are 240x240 at 8 players, confirmed per capture, so the
per-player counts are directly comparable without scaling.

Stock is the yardstick. Arabia is kept separate from the other stock maps
rather than pooled into a "stock average": it is the open-land reference
every discussion of this project's resource budget has used, and averaging
it together with Black Forest and Team Islands would produce a band that
describes no real map.

Writes out/resource_baseline.json, which the report builder reads.

Usage:
    uv run python automation/resource_baseline.py
    uv run python automation/resource_baseline.py --only stock
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps.fairness import profile_capture  # noqa: E402

OUT = REPO / "out" / "resource_baseline.json"

#: The most recent full N=10 pass over the shipped mod. Later than the two
#: earlier passes and the only one taken with the System A resource layer,
#: which is what ships now.
SHIPPED_PASS = REPO / "reports" / "20260809-052633_mod_report_data_sysa_n10"

#: Retired from the mod on 2026-08-15 and NOT in it: the mod ships eight
#: maps and none of these is one of them. Their captures are kept because
#: they are the evidence for the retirement - short on stone, wood and
#: land alike - and ``resource_compare`` files them under their own
#: cohort so they cannot be read as current.
RETIRED = {"Japan", "Caribbean", "New Zealand"}

#: Arabia is its own cohort - see the module docstring.
ARABIA = "Arabia"


def cohorts() -> list[tuple[str, str, Path]]:
    """(cohort, map name, scenario path) for every capture on disk."""
    found: list[tuple[str, str, Path]] = []

    stock_root = REPO / "out" / "stock_capture" / "benchmarks"
    for d in sorted(p for p in stock_root.iterdir() if p.is_dir()):
        cohort = "arabia" if d.name == ARABIA else "stock"
        for scn in sorted(d.rglob("*.aoe2scenario")):
            found.append((cohort, d.name, scn))

    if SHIPPED_PASS.exists():
        for d in sorted(p for p in SHIPPED_PASS.iterdir() if p.is_dir()):
            for scn in sorted(d.glob("*.aoe2scenario")):
                found.append(("shipped", d.name, scn))

    cand_root = REPO / "out" / "mod_capture" / "candidates_n2"
    if cand_root.exists():
        for d in sorted(p for p in cand_root.iterdir() if p.is_dir()):
            for scn in sorted(d.rglob("*.aoe2scenario")):
                found.append(("candidate", d.name, scn))

    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None,
                    help="comma-separated cohorts: arabia,stock,shipped,candidate")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    todo = cohorts()
    if args.only:
        want = {c.strip() for c in args.only.split(",")}
        todo = [t for t in todo if t[0] in want]

    # Resume: re-profiling 157 captures is ~15 minutes and there is no
    # reason to redo one that is already in the file.
    done: dict[str, dict] = {}
    if args.out.exists():
        done = {r["path"]: r for r in json.loads(args.out.read_text("utf-8"))}

    rows = []
    t0 = time.time()
    for i, (cohort, name, scn) in enumerate(todo, 1):
        key = str(scn.relative_to(REPO)).replace("\\", "/")
        if key in done:
            rows.append(done[key])
            continue
        try:
            fair = profile_capture(scn)
        except Exception as e:
            print(f"[{i}/{len(todo)}] {name}: FAILED {e}")
            continue
        rows.append({
            "cohort": cohort,
            "map": name,
            "retired": name in RETIRED,
            "path": key,
            "sample": scn.stem,
            "fairness": fair,
        })
        n_zero = len(fair.get("zero_kinds_by_player") or {})
        print(f"[{i}/{len(todo)}] {cohort}/{name}/{scn.stem}: "
              f"{fair['n_players']} players, {n_zero} with a zero kind "
              f"({time.time()-t0:.0f}s elapsed)")

    args.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\n{len(rows)} captures profiled -> {args.out}")


if __name__ == "__main__":
    main()
