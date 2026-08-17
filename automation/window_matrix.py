"""Full real-engine matrix: for each candidate window, generate baseline and
several consolidation/overlap variants, capturing multiple real samples of
each so quality can be judged from actual variety, not one cherry-picked
render or a Python approximation.

Regenerates each (window, condition) script once, then loops only the
Generate->Save click sequence for --n samples - the expensive part (script
regen) never repeats within a cell.

Usage:
    uv run python automation/window_matrix.py
"""

from __future__ import annotations

import shutil
import subprocess
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
from rwmaps.projection import MapWindow  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"

SIZE = 240
PLAYERS = 8
N_SAMPLES = 5
OUTROOT = REPO / "out" / "window_matrix"

#: (name, lon, lat, span_km, rotate)
WINDOWS = [
    ("salish_sea_wide", -122.65, 47.95, 420, 0),
    ("victoria_recenter", -122.9, 48.15, 260, 0),
    ("victoria_recenter_tighter", -122.85, 48.05, 200, 0),
    ("west_shift", -122.85, 47.75, 130, 0),
    ("west_shift_zoomed", -122.8, 47.75, 95, 0),
]

#: per-window (min_water_width, min_land_width) in tiles, targeting ~5.3km
#: real width - computed once from each window's km/tile and safety-checked
#: (Python-only) to confirm no catastrophic main-channel fragmentation.
CONSOLIDATE_WIDTH = {
    "salish_sea_wide": (3, 2),
    "victoria_recenter": (5, 3),
    "victoria_recenter_tighter": (6, 4),
    "west_shift": (10, 7),
    "west_shift_zoomed": (13, 9),
}

#: (condition_name, extra rwmaps CLI args as a list)
def conditions_for(window_name: str) -> list[tuple[str, list[str]]]:
    ww, lw = CONSOLIDATE_WIDTH[window_name]
    return [
        ("baseline", []),
        ("consolidate_overlap1.0", ["--min-water-width", str(ww), "--min-land-width", str(lw)]),
        ("consolidate_overlap0.85", ["--min-water-width", str(ww), "--min-land-width", str(lw), "--overlap", "0.85"]),
        ("consolidate_overlap0.72", ["--min-water-width", str(ww), "--min-land-width", str(lw), "--overlap", "0.72"]),
    ]


def main():
    ok, why = editor.ensure_ready(PLAYERS)
    if not ok:
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    print(f"editor ready: {why}")
    t_start = time.time()
    total_cells = sum(len(conditions_for(name)) for name, *_ in WINDOWS)
    cell_i = 0

    for name, lon, lat, span, rot in WINDOWS:
        for cond_name, extra_args in conditions_for(name):
            cell_i += 1
            cell_dir = OUTROOT / name / cond_name
            cell_dir.mkdir(parents=True, exist_ok=True)
            rms_dir = cell_dir / "script"

            print(f"\n[{cell_i}/{total_cells}] {name} / {cond_name}  "
                  f"(elapsed {time.time()-t_start:.0f}s)")

            gen_cmd = ["uv", "run", "rwmaps", f"{name}_{cond_name}",
                       f"--center={lon},{lat}", "--span-km", str(span),
                       "--rotate", str(rot),
                       "--size", str(SIZE), "--players", str(PLAYERS),
                       "--outdir", str(rms_dir), "--no-preview", *extra_args]
            t0 = time.time()
            r = subprocess.run(gen_cmd, cwd=REPO, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  REGEN FAILED: {r.stderr[-800:]}")
                continue
            print(f"  regen: {time.time()-t0:.1f}s")

            rms_files = list(rms_dir.rglob("*.rms"))
            if len(rms_files) != 1:
                print(f"  SKIP: expected 1 .rms, found {len(rms_files)}")
                continue
            shutil.copyfile(rms_files[0], SLOT_PATH)

            existing = sorted(cell_dir.glob("sample_*.aoe2scenario"))
            done = len(existing)
            if done >= N_SAMPLES:
                print(f"  already have {done}/{N_SAMPLES}, skipping")
                continue

            for i in range(done, N_SAMPLES):
                t1 = time.time()
                try:
                    after = editor.generate_and_save(SCENARIO_DIR).path
                except Exception as e:
                    print(f"  sample {i}: FAILED ({e})")
                    continue
                dest = cell_dir / f"sample_{i:03d}.aoe2scenario"
                shutil.copyfile(after, dest)
                print(f"  sample {i}: captured in {time.time()-t1:.1f}s")

    print(f"\nDONE in {time.time()-t_start:.0f}s -> {OUTROOT}")


if __name__ == "__main__":
    main()
