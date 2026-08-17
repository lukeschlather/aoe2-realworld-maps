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
from runlog import RunLog  # noqa: E402

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
    OUTROOT.mkdir(parents=True, exist_ok=True)
    log = RunLog(OUTROOT, run_id="window_matrix")
    log.attach_editor(editor)
    total_cells = sum(len(conditions_for(name)) for name, *_ in WINDOWS)
    log.event("plan", f"{len(WINDOWS)} windows x conditions = "
              f"{total_cells} cells x {N_SAMPLES} samples",
              windows=[w[0] for w in WINDOWS], cells=total_cells,
              n_samples=N_SAMPLES, size=SIZE, players=PLAYERS)

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
    cell_i = 0
    captured = 0

    for name, lon, lat, span, rot in WINDOWS:
        for cond_name, extra_args in conditions_for(name):
            cell_i += 1
            cell_dir = OUTROOT / name / cond_name
            cell_dir.mkdir(parents=True, exist_ok=True)
            rms_dir = cell_dir / "script"

            log.event("cell_start", f"cell {cell_i}/{total_cells} {name}/{cond_name}",
                      window=name, condition=cond_name, lon=lon, lat=lat,
                      span_km=span, rotate=rot, extra_args=extra_args)

            gen_cmd = ["uv", "run", "rwmaps", f"{name}_{cond_name}",
                       f"--center={lon},{lat}", "--span-km", str(span),
                       "--rotate", str(rot),
                       "--size", str(SIZE), "--players", str(PLAYERS),
                       "--outdir", str(rms_dir), "--no-preview", *extra_args]
            t_regen = time.time()
            r = subprocess.run(gen_cmd, cwd=REPO, capture_output=True,
                               text=True)
            regen_s = time.time() - t_regen
            # rwmaps' own narration was being captured and thrown away; it
            # reports the land fraction and coastline IoU of the script it
            # just built, which is worth having beside the capture it made.
            log.event("regen", None, window=name, condition=cond_name,
                      command=" ".join(gen_cmd), returncode=r.returncode,
                      stdout=r.stdout[-4000:], ok=r.returncode == 0,
                      duration_s=round(regen_s, 3))
            if r.returncode != 0:
                log.fail("regen_failed", f"  {name}/{cond_name}: REGEN FAILED",
                         window=name, condition=cond_name,
                         stderr=r.stderr[-2000:],
                         duration_s=round(regen_s, 3))
                continue

            rms_files = list(rms_dir.rglob("*.rms"))
            if len(rms_files) != 1:
                log.fail("slot_swap", f"  {name}/{cond_name}: SKIP expected 1 "
                         f".rms, found {len(rms_files)}", window=name,
                         condition=cond_name,
                         found=[str(p) for p in rms_files])
                continue
            shutil.copyfile(rms_files[0], SLOT_PATH)

            existing = sorted(cell_dir.glob("sample_*.aoe2scenario"))
            done = len(existing)
            if done >= N_SAMPLES:
                log.event("cell_skip", f"  {name}/{cond_name}: have "
                          f"{done}/{N_SAMPLES}, skipping", window=name,
                          condition=cond_name, have=done, want=N_SAMPLES)
                continue

            for i in range(done, N_SAMPLES):
                try:
                    cap = editor.generate_and_save(SCENARIO_DIR)
                except Exception as e:
                    log.fail("capture", f"  {name}/{cond_name} sample {i}: "
                             f"FAILED", window=name, condition=cond_name,
                             sample_index=i, error=str(e))
                    continue
                dest = cell_dir / f"sample_{i:03d}.aoe2scenario"
                shutil.copyfile(cap.path, dest)
                captured += 1
                log.ok("capture", f"  {name}/{cond_name} sample {i}: captured",
                       window=name, condition=cond_name, sample_index=i,
                       file=str(dest), generate_s=round(cap.generate_s, 3),
                       save_s=round(cap.save_s, 3))

    log.close(f"done {captured}/{total_cells * N_SAMPLES} captured",
              captured=captured, expected=total_cells * N_SAMPLES,
              outdir=str(OUTROOT), wall_s=round(time.time() - t_start, 1))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
