"""Build the installable "Real World Maps" mod - just .rms scripts, no
engine time, cheap to (re)build - plus a debug variant that additionally
carries the AA_rw_placeholder_tester slot this project's tuning automation
(tuning_matrix.py et al) depends on to work around the Scenario Editor's
list-widget crash bug (see TUNING_STATUS.md / RENDER_PIPELINE.md).

Every region here relies on rwmaps's own known-good defaults (resolution
50m, overlap 0.85, min-water/land-width 4/3, clumping-factor 8 - see the
comment above --resolution in src/rwmaps/cli.py) EXCEPT Salish Sea, which
overrides consolidation width to victoria_recenter's own verified value
(5/3, cell 0a8509cf) since that's a specific already-verified-good data
point rather than the general-purpose default.

Only Salish Sea has been validated against real engine captures so far.
The other 9 regions are a first cut, picked for geographic variety
(archipelago, fjords, enclosed sea, bay, island nations) - the planned
10-generations-per-region fairness/AI pass is the next step to actually
verify them, not this script.

Usage:
    uv run python automation/build_mod.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

MOD_NAME = "Real World Maps"
DEBUG_MOD_NAME = "Real World Maps (Debug)"
PLACEHOLDER_SLOT = "AA_rw_placeholder_tester.rms"

#: (display name, extra rwmaps CLI args beyond the name itself).
MOD_REGIONS = [
    ("Salish Sea", ["--center=-122.9,48.15", "--span-km", "260",
                     "--overlap", "0.85", "--min-water-width", "5", "--min-land-width", "3"]),
    ("Italy", ["--region", "italy"]),
    ("Britain", ["--region", "britain"]),
    ("Greece", ["--region", "greece"]),
    ("Japan", ["--region", "japan", "--rotate", "35"]),
    ("Chesapeake Bay", ["--region", "chesapeake"]),
    ("Black Sea", ["--region", "blacksea"]),
    ("Scandinavia", ["--region", "scandinavia"]),
    ("Caribbean", ["--region", "caribbean"]),
    ("New Zealand", ["--region", "newzealand"]),
]


def write_info(mod_root: Path, title: str, description: str) -> None:
    mod_root.mkdir(parents=True, exist_ok=True)
    (mod_root / "info.json").write_text(json.dumps({
        "Author": "rwmaps",
        "CacheStatus": 0,
        "Description": description,
        "Title": title,
    }), encoding="utf-8")


def main():
    tmp_out = REPO / "out" / "mod_build"
    if tmp_out.exists():
        shutil.rmtree(tmp_out)
    tmp_out.mkdir(parents=True)

    main_root = REPO / "mod" / MOD_NAME
    debug_root = REPO / "mod" / DEBUG_MOD_NAME
    main_scripts = main_root / "resources" / "_common" / "random-map-scripts"
    debug_scripts = debug_root / "resources" / "_common" / "random-map-scripts"
    main_scripts.mkdir(parents=True, exist_ok=True)
    debug_scripts.mkdir(parents=True, exist_ok=True)

    write_info(main_root, MOD_NAME,
               "Playable AoE2 DE random maps generated from real-world coastlines.")
    write_info(debug_root, DEBUG_MOD_NAME,
               "Same maps as 'Real World Maps', plus the AA_rw_placeholder_tester "
               "slot this project's tuning automation swaps candidate scripts into.")

    first_rms = None
    failures = []
    for name, extra in MOD_REGIONS:
        region_out = tmp_out / name
        cmd = ["uv", "run", "rwmaps", name, "--outdir", str(region_out),
               "--no-preview", *extra]
        print(f"generating {name}: {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAILED: {r.stderr[-800:]}")
            failures.append(name)
            continue
        rms_files = list(region_out.rglob("*.rms"))
        if len(rms_files) != 1:
            print(f"  SKIP: expected 1 .rms, found {len(rms_files)}")
            failures.append(name)
            continue
        dest_main = main_scripts / f"{name}.rms"
        shutil.copyfile(rms_files[0], dest_main)
        shutil.copyfile(rms_files[0], debug_scripts / f"{name}.rms")
        if first_rms is None:
            first_rms = rms_files[0]
        print(f"  -> {dest_main}")

    if first_rms:
        shutil.copyfile(first_rms, debug_scripts / PLACEHOLDER_SLOT)
        print(f"  -> {debug_scripts / PLACEHOLDER_SLOT} (placeholder slot, "
              f"content = whatever generated first, currently {first_rms.parent.name})")

    shutil.rmtree(tmp_out)
    if failures:
        print(f"\nFAILED regions (not in either mod): {failures}")
    print(f"\ndone - {len(MOD_REGIONS) - len(failures)}/{len(MOD_REGIONS)} regions in "
          f"mod/{MOD_NAME}/ and mod/{DEBUG_MOD_NAME}/")


if __name__ == "__main__":
    main()
