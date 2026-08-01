"""Real-engine parameter exploration: for each (window, condition) cell,
regenerate the script once, capture a handful of real samples, and analyze
each IMMEDIATELY (fairness from actual TC placement + resource ownership +
a small preview render) - appending one JSON line to a shared results.jsonl
as it goes. The report builder then only reads that file; it never touches
AoE2ScenarioParser itself, so building the report is fast regardless of how
many samples this collects.

Every run is scoped under a required --run-id so separate sweeps (e.g. one
run exploring resolution, another re-running the full parameter matrix with
a different default) never share output directories or a results.jsonl -
each gets its own out/tuning_matrix/<run-id>/ tree, and build_tuning_report.py
turns a given run-id into its own report file + archived-data directory.

Usage:
    uv run python automation/tuning_matrix.py --run-id my_sweep
    uv run python automation/tuning_matrix.py --run-id my_sweep --resolution-default 50m
"""

from __future__ import annotations

import argparse
import json
import math
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

from rwmaps import install as install_mod  # noqa: E402
from sample_analysis import analyze_capture  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"
UI_DRIVER = Path(__file__).parent / "ui_driver.ps1"

GENERATE_BTN = (256, 1028)
MENU_BTN = (1834, 23)
SAVE_BTN = (960, 436)
CANCEL_BTN = (960, 737)

SIZE = 240
PLAYERS = 8
N_SAMPLES = 2

#: (window_key, title, lon, lat, span_km, rotate) - all 5 candidates stay in
#: scope; the point of varying more parameters is to find whether the
#: windows that didn't shine under the first tuning pass do better under
#: different settings, not to prune them for looking bad under one setting.
WINDOWS = [
    ("salish_sea_wide", "Salish Sea Wide", -122.65, 47.95, 420, 0),
    ("victoria_recenter", "Victoria Recenter", -122.9, 48.15, 260, 0),
    ("victoria_recenter_tighter", "Victoria Recenter Tighter", -122.85, 48.05, 200, 0),
    ("west_shift", "West Shift", -122.85, 47.75, 130, 0),
    ("west_shift_zoomed", "West Shift Zoomed", -122.8, 47.75, 95, 0),
]

#: per-window (min_water_width, min_land_width) "default" consolidation,
#: plus light/heavy variants - same targeting logic as window_matrix.py
#: (each window's own km/tile, aimed at a roughly constant ~5.3km real
#: width threshold).
CONSOLIDATE_WIDTH = {
    "salish_sea_wide": {"light": (2, 1), "default": (3, 2), "heavy": (4, 3)},
    "victoria_recenter": {"light": (3, 2), "default": (5, 3), "heavy": (7, 5)},
    "victoria_recenter_tighter": {"light": (4, 3), "default": (6, 4), "heavy": (8, 6)},
    "west_shift": {"light": (6, 4), "default": (10, 7), "heavy": (14, 10)},
    "west_shift_zoomed": {"light": (8, 6), "default": (13, 9), "heavy": (18, 13)},
}

#: default disc budget at size 240 (rms_land.lands_for_size(240) == 700
#: exactly, since 240 is the base/`at` size) - low/high bracket it.
LANDS_DEFAULT, LANDS_LOW, LANDS_HIGH = 700, 350, 1200


def conditions_for(window_name: str, resolution_default: str = "10m") -> list[tuple[str, list[str]]]:
    """The one-parameter-at-a-time matrix for a window.

    ``resolution_default`` picks which Natural Earth coastline resolution
    every condition in this call assumes as its baseline. At the original
    "10m" default, this returns the exact original 16 conditions (including
    the resolution_50m/resolution_110m comparison conditions), unchanged -
    old runs and the original report stay reproducible. For any other
    value, those two now-redundant comparison conditions are dropped (you
    can't usefully compare "resolution 50m" against itself), every
    remaining condition gets ``--resolution <value>`` prepended, and every
    condition key gets an ``_r<value>`` suffix so a run sweeping multiple
    resolution defaults can share one results.jsonl without key collisions.
    """
    ww, lw = CONSOLIDATE_WIDTH[window_name]["default"]
    ww_light, lw_light = CONSOLIDATE_WIDTH[window_name]["light"]
    ww_heavy, lw_heavy = CONSOLIDATE_WIDTH[window_name]["heavy"]

    def consolidate(width_pair, extra=None):
        w, l = width_pair
        args = ["--min-water-width", str(w), "--min-land-width", str(l)]
        return args + (extra or [])

    # "Good baseline" for the one-parameter-at-a-time exploration below:
    # default consolidation width + overlap 0.85 (the best-performing value
    # found in the first pass) + every other knob left at its default.
    def from_good_baseline(extra: list[str]) -> list[str]:
        return consolidate((ww, lw), ["--overlap", "0.85"] + extra)

    base = [
        ("baseline", []),
        ("consolidate_default_overlap1.0", consolidate((ww, lw))),
        ("consolidate_light_overlap1.0", consolidate((ww_light, lw_light))),
        ("consolidate_heavy_overlap1.0", consolidate((ww_heavy, lw_heavy))),
        ("consolidate_overlap0.85", from_good_baseline([])),
        ("consolidate_overlap0.72", consolidate((ww, lw), ["--overlap", "0.72"])),
        ("clumping_4", from_good_baseline(["--clumping-factor", "4"])),
        ("clumping_16", from_good_baseline(["--clumping-factor", "16"])),
        ("max_radius_8", from_good_baseline(["--max-radius", "8"])),
        ("max_radius_18", from_good_baseline(["--max-radius", "18"])),
        ("lands_low", from_good_baseline(["--lands", str(LANDS_LOW)])),
        ("lands_high", from_good_baseline(["--lands", str(LANDS_HIGH)])),
        ("min_island_16", from_good_baseline(["--min-island-tiles", "16"])),
        ("min_island_64", from_good_baseline(["--min-island-tiles", "64"])),
    ]

    if resolution_default == "10m":
        return base[:12] + [
            ("resolution_50m", from_good_baseline(["--resolution", "50m"])),
            ("resolution_110m", from_good_baseline(["--resolution", "110m"])),
        ] + base[12:]

    suffix = f"_r{resolution_default}"
    res_prefix = ["--resolution", resolution_default]
    return [(key + suffix, res_prefix + args) for key, args in base]


#: The cli.py argparse defaults AS THEY WERE when every condition captured
#: so far actually ran - conditions_for() only specifies what differs from
#: this, so resolve_params() below can recover the COMPLETE resolved
#: settings per condition (for report display), not just the diff.
#:
#: DELIBERATELY FROZEN, not synced to cli.py's present-day defaults: as of
#: 2026-07-31 cli.py's real shipped defaults changed to the known-good
#: values (resolution=50m, overlap=0.85, min_water_width=4,
#: min_land_width=3 - see the comment above --resolution in cli.py). Every
#: condition captured before that change which didn't explicitly pass one
#: of these flags (e.g. "baseline_r50m", the "consolidate_*_overlap1.0_r50m"
#: family) actually ran under THESE OLD values, and updating this dict to
#: match the new cli.py defaults would silently mislabel that historical
#: data if its report is ever rebuilt. A future sweep that wants its
#: baseline condition to reflect the new recommended defaults should record
#: its own resolved params at capture time rather than reconstructing them
#: from a dict like this one.
PARAM_DEFAULTS = {
    "resolution": "10m",
    "overlap": 1.0,
    "max_radius": 12.0,
    "clumping_factor": 8,
    "lands": "auto (700 @ size 240)",
    "min_island_tiles": 0,
    "min_water_width": 0,
    "min_land_width": 0,
}

#: extra_args flag -> PARAM_DEFAULTS key
FLAG_TO_KEY = {
    "--resolution": "resolution",
    "--overlap": "overlap",
    "--max-radius": "max_radius",
    "--clumping-factor": "clumping_factor",
    "--lands": "lands",
    "--min-island-tiles": "min_island_tiles",
    "--min-water-width": "min_water_width",
    "--min-land-width": "min_land_width",
}


def resolve_params(extra_args: list[str]) -> dict:
    """The complete parameter set a condition actually runs with - defaults
    overridden by whatever this condition's extra_args specify."""
    resolved = dict(PARAM_DEFAULTS)
    it = iter(extra_args)
    for flag in it:
        value = next(it)
        key = FLAG_TO_KEY.get(flag)
        if key:
            resolved[key] = value
    return resolved


def newest_scenario():
    files = sorted(SCENARIO_DIR.glob("*.aoe2scenario"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def click_sequence(before_mtime: float):
    ps = f"""
. "{UI_DRIVER}"
Reset-IfMenuStuck {CANCEL_BTN[0]} {CANCEL_BTN[1]}
$ok = Click-GenerateMapVerified {GENERATE_BTN[0]} {GENERATE_BTN[1]}
if (-not $ok) {{ exit 1 }}
Click-At {MENU_BTN[0]} {MENU_BTN[1]}
Start-Sleep -Milliseconds 200
$beforeTime = [DateTimeOffset]::FromUnixTimeMilliseconds({math.ceil(before_mtime * 1000) + 200}).LocalDateTime
$ok = Click-SaveVerified {SAVE_BTN[0]} {SAVE_BTN[1]} {MENU_BTN[0]} {MENU_BTN[1]} "{SCENARIO_DIR}" $beforeTime
if (-not $ok) {{ exit 2 }}
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True)
    if result.returncode == 1:
        raise RuntimeError("Generate Map never registered a seed change")
    if result.returncode == 2:
        raise RuntimeError("Save never closed the Menu")
    if result.returncode != 0:
        raise RuntimeError(f"click sequence failed rc={result.returncode}: {result.stderr}")


def already_done(results_path: Path, window_key: str, cond_key: str) -> int:
    if not results_path.exists():
        return 0
    n = 0
    for line in results_path.open(encoding="utf-8"):
        rec = json.loads(line)
        if rec["window"] == window_key and rec["condition"] == cond_key:
            n += 1
    return n


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True,
                    help="scopes output under out/tuning_matrix/<run-id>/ - "
                         "reuse the same run-id across invocations (e.g. one "
                         "per --resolution-default) to share one results.jsonl")
    p.add_argument("--resolution-default", default="10m", choices=["10m", "50m", "110m"],
                    help="baked into every condition's args as --resolution "
                         "(except at 10m, rwmaps' own default, where this is a no-op "
                         "and the original resolution_50m/110m comparison conditions "
                         "are kept instead)")
    p.add_argument("--n-samples", type=int, default=N_SAMPLES)
    return p.parse_args()


def main():
    args = parse_args()
    outroot = REPO / "out" / "tuning_matrix" / args.run_id
    results_path = outroot / "results.jsonl"
    outroot.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    cells = [(wk, wt, lon, lat, span, rot, ck, extra)
             for wk, wt, lon, lat, span, rot in WINDOWS
             for ck, extra in conditions_for(wk, args.resolution_default)]
    total = len(cells)

    with results_path.open("a", encoding="utf-8") as results_fh:
        for i, (win_key, win_title, lon, lat, span, rot, cond_key, extra_args) in enumerate(cells, 1):
            done = already_done(results_path, win_key, cond_key)
            if done >= args.n_samples:
                print(f"[{i}/{total}] {win_key}/{cond_key}: already have {done}/{args.n_samples}, skipping")
                continue

            print(f"\n[{i}/{total}] {win_key}/{cond_key}  (elapsed {time.time()-t_start:.0f}s)")
            cell_dir = outroot / win_key / cond_key
            rms_dir = outroot / "scripts" / win_key / cond_key
            gen_cmd = ["uv", "run", "rwmaps", f"{win_key}_{cond_key}",
                       f"--center={lon},{lat}", "--span-km", str(span), "--rotate", str(rot),
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

            for sample_i in range(done, args.n_samples):
                t1 = time.time()
                before = newest_scenario()
                before_mtime = before.stat().st_mtime if before else 0
                try:
                    click_sequence(before_mtime)
                except Exception as e:
                    print(f"  sample {sample_i}: capture FAILED ({e})")
                    continue
                after = newest_scenario()
                if after is None or after.stat().st_mtime <= before_mtime:
                    print(f"  sample {sample_i}: no new file, skipping")
                    continue

                # Persist the raw capture BEFORE analyzing - the game
                # reuses one filename for Save, so the next iteration's
                # capture overwrites this one; without archiving it here,
                # a bug found later in analyze_capture (as happened once
                # already this session) would need the whole batch re-run
                # through the engine just to fix the analysis.
                archive_dir = cell_dir / "raw"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / f"sample_{sample_i:03d}.aoe2scenario"
                shutil.copyfile(after, dest)

                try:
                    analysis = analyze_capture(dest, size=SIZE)
                except Exception as e:
                    print(f"  sample {sample_i}: ANALYSIS FAILED ({e})")
                    continue

                record = {
                    "window": win_key, "window_title": win_title, "condition": cond_key,
                    "extra_args": extra_args, "sample_index": sample_i,
                    **analysis,
                }
                results_fh.write(json.dumps(record) + "\n")
                results_fh.flush()
                print(f"  sample {sample_i}: captured+analyzed in {time.time()-t1:.1f}s "
                      f"(landmasses={analysis['placement']['n_landmasses_with_a_player']}, "
                      f"reachable={analysis['placement']['pairwise_land_reachable_fraction']}, "
                      f"any_zero={analysis['resources']['any_player_zero_of_a_kind']})")

    print(f"\nDONE in {time.time()-t_start:.0f}s -> {results_path}")


if __name__ == "__main__":
    main()
