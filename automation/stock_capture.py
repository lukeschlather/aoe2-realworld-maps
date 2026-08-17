"""Real-engine capture pass over *stock* AoE2:DE map scripts, so this
project has measured resource-distribution benchmarks to aim at instead of
guessing what "fair" looks like.

Everything in `RESOURCE_TEMPLATES.md` about System A is a *static read* of
the include files. This is the render half: copy a stock `.rms`/`.rms2`
verbatim into the mod slot the tuning automation already uses, generate it
in the Scenario Editor, and analyze the engine's actual output the same way
`mod_capture.py` analyzes ours - identical code path, so the numbers are
directly comparable to `out/mod_capture/*/results.jsonl`.

Two things this establishes that a static read cannot:

* **`#include_drs includes/<name>.inc` resolves from a mod directory.**
  `RESOURCE_TEMPLATES.md` left this open ("needing a real render") and it
  gates the whole System A port - if it did not resolve, System A would
  have to be inlined the way the community maps do it. Any System A map
  captured here with a full resource complement settles it affirmatively.
* **What the stock resource budget actually is per player**, as placed,
  not as requested. Silent placement failure is the entire bug class this
  project keeps hitting, so "what the script asks for" and "what lands" are
  different questions.

The slot trick is load-bearing, not a shortcut: selecting a *different*
entry in the editor's Random Map list reproducibly crashes the game (see
`MOD_STATUS.md`), so every script - ours or stock - is captured by
overwriting one fixed filename that is already selected.

Usage:
    uv run python automation/stock_capture.py --run-id benchmarks
    uv run python automation/stock_capture.py --run-id benchmarks --maps Arabia,Thames
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import editor  # noqa: E402
from runlog import RunLog  # noqa: E402
from sample_analysis import analyze_capture  # noqa: E402
from slot import SCENARIO_DIR, SLOT_PATH, put_slot  # noqa: E402, F401

#: The live stock scripts. Per `STOCK_MAP_INVENTORY.md`, this - not
#: `random-map-scripts/`, and not the `.backup.20201109` snapshot - is what
#: the game actually runs.
STOCK_DIR = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\AoE2DE"
    r"\resources\_common\drs\gamedata_x2"
)

SIZE = 240
PLAYERS = 8
N_SAMPLES = 3

#: (label, filename, why it is in the benchmark set). Chosen to span the
#: flavor axes `RESOURCE_TEMPLATES.md` identifies, not just to be "stock":
#: resource system, tier generosity, land/water topology, and placement
#: mode. The user named the first four; the rest fill in axes those four
#: leave silent.
STOCK_MAPS: list[tuple[str, str, str]] = [
    # --- the four the user asked for ---
    ("Arabia", "Arabia.rms",
     "System A reference: six resource tiers + two additional, the most "
     "generous stock budget, and forest.inc. No water at all, so it is the "
     "land-resource baseline and silent on everything water-touching."),
    ("Thames", "Thames.rms",
     "System A, direct_placement, explicit create_land at hardcoded "
     "land_position with zone/land_id - structurally the closest stock map "
     "to what rms_land.py emits. Irregular (river) water."),
    ("Yucatan", "Yucatan.rms",
     "System A, jungle/tropical, no aquatic includes at all - a land map "
     "with a dense-forest flavor, and a check that heavy forest does not "
     "starve resource placement."),
    ("City of Lakes", "cityoflakes.rms2",
     "System B (classic GeneratingObjects.inc) community map, .rms2 - the "
     "other resource system entirely, and a water map. The control that "
     "says whether System A vs B is even visible in these metrics."),
    # --- axes the four above leave silent ---
    ("Loch Ness", "Loch Ness.rms",
     "System A, direct_placement, irregular lake water, and the one stock "
     "map that raises *_ZONE_DISTANCE to 14 - the water-constrained-land "
     "lever RESOURCE_TEMPLATES.md recommends trying before spacing cuts."),
    ("Team Islands", "Team_Islands.rms",
     "System A island map: keeps default spacing but pushes "
     "*_ADDITIONAL_DISTANCE to 40. The 'plentiful but fragmented land' "
     "answer, as opposed to Great Wall's 'narrow land' answer."),
    ("Coastal", "Coastal.rms",
     "System A genuinely-coastal map with coastal_blending.inc/BEACH_TERRAIN "
     "and a uniform-width water surround. The radially-symmetric water our "
     "maps deliberately are not."),
    ("Black Forest", "Black_Forest.rms",
     "System A with RESOURCE_SPACING_DEFAULT 6 - the same tightening value "
     "as Great Wall but from the opposite cause (space walled in by trees, "
     "not absent). Separates 'tight spacing' from 'narrow coastline'."),
    ("Arena", "Arena.rms",
     "System A walled start. Already captured once ad-hoc in "
     "out/stock_maps/smoke1; included so the benchmark set has it under the "
     "same run-id and sample count as everything else."),
]


def already_done(results_path: Path, label: str) -> int:
    if not results_path.exists():
        return 0
    return sum(1 for line in results_path.open(encoding="utf-8")
               if json.loads(line)["map"] == label)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True,
                   help="scopes output under out/stock_capture/<run-id>/ - reuse "
                        "the same run-id to resume a partially-completed pass")
    p.add_argument("--n-samples", type=int, default=N_SAMPLES)
    p.add_argument("--maps", default=None,
                   help="comma-separated subset of labels (default: all)")
    return p.parse_args()


def main():
    args = parse_args()
    maps = STOCK_MAPS
    if args.maps:
        wanted = {m.strip() for m in args.maps.split(",")}
        maps = [m for m in STOCK_MAPS if m[0] in wanted]
        missing = wanted - {m[0] for m in maps}
        if missing:
            raise SystemExit(f"unknown map(s): {missing}")

    outroot = REPO / "out" / "stock_capture" / args.run_id
    results_path = outroot / "results.jsonl"
    outroot.mkdir(parents=True, exist_ok=True)
    log = RunLog(outroot, args.run_id)
    log.attach_editor(editor)
    log.event("plan", f"{len(maps)} stock maps x {args.n_samples} samples",
              maps=[m[0] for m in maps], n_samples=args.n_samples, size=SIZE,
              players=PLAYERS, stock_dir=str(STOCK_DIR))

    # Timed by hand, so this is ONE event: a timer writes its own, and a
    # second explicit event of the same kind double-counts in any query that
    # sums durations by kind.
    t_pre = time.time()
    ok, why = editor.ensure_ready(PLAYERS)
    preflight_s = time.time() - t_pre
    if not ok:
        log.fail("preflight_failed", f"ABORT editor unusable: {why}", why=why,
                 duration_s=round(preflight_s, 3))
        log.close("aborted")
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    log.ok("preflight", "editor ready", why=why,
           duration_s=round(preflight_s, 3))

    t_start = time.time()
    captured = 0

    with results_path.open("a", encoding="utf-8") as results_fh:
        for map_i, (label, filename, rationale) in enumerate(maps, 1):
            done = already_done(results_path, label)
            if done >= args.n_samples:
                log.event("map_skip", f"map {map_i}/{len(maps)} {label}: have "
                          f"{done}/{args.n_samples}, skipping", map=label,
                          have=done, want=args.n_samples)
                continue

            src = STOCK_DIR / filename
            if not src.exists():
                log.fail("map_missing", f"map {map_i}/{len(maps)} {label}: "
                         f"MISSING {filename}", map=label, path=str(src))
                continue

            log.event("map_start", f"map {map_i}/{len(maps)} {label}",
                      map=label, script=filename, rationale=rationale)
            # Copy verbatim. The slot is .rms even for a .rms2 source - per
            # STOCK_MAP_INVENTORY.md the extension is packaging history, not
            # a language difference.
            put_slot(src)

            map_dir = outroot / label
            for sample_i in range(done, args.n_samples):
                t1 = time.time()
                try:
                    cap = editor.generate_and_save(SCENARIO_DIR)
                except Exception as e:
                    log.fail("capture", f"  {label} sample {sample_i}: capture "
                             f"FAILED", map=label, sample_index=sample_i,
                             error=str(e))
                    continue

                archive_dir = map_dir / "raw"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / f"sample_{sample_i:03d}.aoe2scenario"
                shutil.copyfile(cap.path, dest)

                with log.timer("analyze", map=label, sample_index=sample_i):
                    try:
                        analysis = analyze_capture(dest, size=SIZE)
                    except Exception as e:
                        log.fail("analyze", f"  {label} sample {sample_i}: "
                                 f"ANALYSIS FAILED", map=label,
                                 sample_index=sample_i, error=str(e),
                                 file=str(dest))
                        continue

                record = {
                    "map": label, "script": filename, "rationale": rationale,
                    "sample_index": sample_i, **analysis,
                }
                results_fh.write(json.dumps(record) + "\n")
                results_fh.flush()
                captured += 1
                counts = analysis["resources"]["per_player"]
                # Stock maps take far longer to generate than ours (Arabia
                # ~82s against ~3s), which is exactly the kind of thing the
                # JSON log is for - and exactly the kind of number that would
                # make the terse log unstable.
                log.ok("sample",
                       f"  {label} sample {sample_i}: land={analysis['land_pct']}% "
                       f"tcs={analysis['n_tcs']} "
                       f"any_zero={analysis['resources']['any_player_zero_of_a_kind']} "
                       f"p1={counts.get('1')}",
                       map=label, sample_index=sample_i, file=str(dest),
                       land_pct=analysis["land_pct"], n_tcs=analysis["n_tcs"],
                       generate_s=round(cap.generate_s, 3),
                       save_s=round(cap.save_s, 3),
                       sample_total_s=round(time.time() - t1, 3))

    log.close(f"done {captured} captured", captured=captured,
              expected=len(maps) * args.n_samples, results=str(results_path),
              wall_s=round(time.time() - t_start, 1))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
