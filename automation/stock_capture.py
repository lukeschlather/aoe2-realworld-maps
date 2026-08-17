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
from rwmaps import install as install_mod  # noqa: E402
from sample_analysis import analyze_capture  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"

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


def put_slot(src: Path):
    """Copy a script into the slot, normalised to the ascii/LF form the
    engine's parser wants (same as ``rwmaps.rms.write_rms`` - a CRLF copy is
    a silent failure mode).

    Retries on PermissionError: the game holds the slot file open while it
    is generating, so a swap issued too soon after the previous sample dies
    with EACCES rather than anything descriptive.
    """
    data = src.read_bytes().replace(b"\r\n", b"\n")
    for _ in range(40):
        try:
            SLOT_PATH.write_bytes(data)
            return
        except PermissionError:
            time.sleep(0.5)
    raise RuntimeError(f"slot stayed locked by the game: {SLOT_PATH}")


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
    ok, why = editor.ensure_ready(PLAYERS)
    if not ok:
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    print(f"editor ready: {why}")
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
    t_start = time.time()

    with results_path.open("a", encoding="utf-8") as results_fh:
        for map_i, (label, filename, rationale) in enumerate(maps, 1):
            done = already_done(results_path, label)
            if done >= args.n_samples:
                print(f"[{map_i}/{len(maps)}] {label}: have {done}/{args.n_samples}, skipping")
                continue

            src = STOCK_DIR / filename
            if not src.exists():
                print(f"[{map_i}/{len(maps)}] {label}: MISSING {src}")
                continue

            print(f"\n[{map_i}/{len(maps)}] {label}  ({filename}, "
                  f"elapsed {time.time()-t_start:.0f}s)")
            # Copy verbatim. The slot is .rms even for a .rms2 source - per
            # STOCK_MAP_INVENTORY.md the extension is packaging history, not
            # a language difference.
            put_slot(src)

            map_dir = outroot / label
            for sample_i in range(done, args.n_samples):
                t1 = time.time()
                try:
                    after = editor.generate_and_save(SCENARIO_DIR).path
                except Exception as e:
                    print(f"  sample {sample_i}: capture FAILED ({e})")
                    continue

                archive_dir = map_dir / "raw"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / f"sample_{sample_i:03d}.aoe2scenario"
                shutil.copyfile(after, dest)

                try:
                    analysis = analyze_capture(dest, size=SIZE)
                except Exception as e:
                    print(f"  sample {sample_i}: ANALYSIS FAILED ({e})")
                    continue

                record = {
                    "map": label, "script": filename, "rationale": rationale,
                    "sample_index": sample_i, **analysis,
                }
                results_fh.write(json.dumps(record) + "\n")
                results_fh.flush()
                counts = analysis["resources"]["per_player"]
                print(f"  sample {sample_i}: {time.time()-t1:.1f}s "
                      f"land={analysis['land_pct']}% tcs={analysis['n_tcs']} "
                      f"any_zero={analysis['resources']['any_player_zero_of_a_kind']} "
                      f"p1={counts.get('1')}")

    print(f"\nDONE in {time.time()-t_start:.0f}s -> {results_path}")


if __name__ == "__main__":
    main()
