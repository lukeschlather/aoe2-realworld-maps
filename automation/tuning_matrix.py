"""LEGACY ORIENTATION: the ``--rotate`` values below are grid-space and
predate the 2026-08-16 switch to screen-space ``--north``. ``rwmaps`` no
longer accepts ``--rotate``, so these will fail until converted -
``north = rotate - 45`` (projection.north_from_legacy_rotate). Left
unconverted because this script targets regions that no longer ship.

Real-engine parameter exploration: for each (window, condition) cell,
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
from runlog import RunLog  # noqa: E402
from sample_analysis import analyze_capture  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"

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
    log = RunLog(outroot, args.run_id)
    log.attach_editor(editor)

    cells = [(wk, wt, lon, lat, span, rot, ck, extra)
             for wk, wt, lon, lat, span, rot in WINDOWS
             for ck, extra in conditions_for(wk, args.resolution_default)]
    total = len(cells)
    log.event("plan", f"{total} cells x {args.n_samples} samples",
              cells=total, n_samples=args.n_samples, size=SIZE,
              players=PLAYERS, resolution_default=args.resolution_default)

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
        for i, (win_key, win_title, lon, lat, span, rot, cond_key, extra_args) in enumerate(cells, 1):
            done = already_done(results_path, win_key, cond_key)
            if done >= args.n_samples:
                log.event("cell_skip", f"cell {i}/{total} {win_key}/{cond_key}: "
                          f"have {done}/{args.n_samples}, skipping",
                          window=win_key, condition=cond_key, have=done,
                          want=args.n_samples)
                continue

            # extra_args, not resolve_params(extra_args): that helper resolves
            # against this file's hardcoded PARAM_DEFAULTS, which no longer
            # match the CLI's own defaults (it still says resolution 10m and
            # overlap 1.0; rwmaps ships 50m and 0.85). It is kept because old
            # reports were built with it, but writing it into a log meant for
            # queries would be recording a known-wrong number. The arguments
            # actually passed are the ground truth; build_latency_report.py
            # resolves the full set from rwmaps's own parser.
            log.event("cell_start", f"cell {i}/{total} {win_key}/{cond_key}",
                      window=win_key, condition=cond_key, lon=lon, lat=lat,
                      span_km=span, rotate=rot, extra_args=extra_args)
            cell_dir = outroot / win_key / cond_key
            rms_dir = outroot / "scripts" / win_key / cond_key
            gen_cmd = ["uv", "run", "rwmaps", f"{win_key}_{cond_key}",
                       f"--center={lon},{lat}", "--span-km", str(span), "--rotate", str(rot),
                       "--size", str(SIZE), "--players", str(PLAYERS),
                       "--outdir", str(rms_dir), "--no-preview", *extra_args]
            t_regen = time.time()
            r = subprocess.run(gen_cmd, cwd=REPO, capture_output=True,
                               text=True)
            regen_s = time.time() - t_regen
            # rwmaps' narration was captured and discarded; it reports the
            # land fraction and coastline IoU of the script it just built.
            log.event("regen", None, window=win_key, condition=cond_key,
                      command=" ".join(gen_cmd), returncode=r.returncode,
                      stdout=r.stdout[-4000:], ok=r.returncode == 0,
                      duration_s=round(regen_s, 3))
            if r.returncode != 0:
                log.fail("regen_failed", f"  {win_key}/{cond_key}: REGEN FAILED",
                         window=win_key, condition=cond_key,
                         stderr=r.stderr[-2000:],
                         duration_s=round(regen_s, 3))
                continue

            rms_files = list(rms_dir.rglob("*.rms"))
            if len(rms_files) != 1:
                log.fail("slot_swap", f"  {win_key}/{cond_key}: SKIP expected 1 "
                         f".rms, found {len(rms_files)}", window=win_key,
                         condition=cond_key,
                         found=[str(p) for p in rms_files])
                continue
            shutil.copyfile(rms_files[0], SLOT_PATH)

            for sample_i in range(done, args.n_samples):
                t1 = time.time()
                try:
                    cap = editor.generate_and_save(SCENARIO_DIR)
                except Exception as e:
                    log.fail("capture", f"  {win_key}/{cond_key} sample "
                             f"{sample_i}: capture FAILED", window=win_key,
                             condition=cond_key, sample_index=sample_i,
                             error=str(e))
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
                shutil.copyfile(cap.path, dest)

                with log.timer("analyze", window=win_key, condition=cond_key,
                               sample_index=sample_i):
                    try:
                        analysis = analyze_capture(dest, size=SIZE)
                    except Exception as e:
                        log.fail("analyze", f"  {win_key}/{cond_key} sample "
                                 f"{sample_i}: ANALYSIS FAILED",
                                 window=win_key, condition=cond_key,
                                 sample_index=sample_i, error=str(e),
                                 file=str(dest))
                        continue

                record = {
                    "window": win_key, "window_title": win_title, "condition": cond_key,
                    "extra_args": extra_args, "sample_index": sample_i,
                    **analysis,
                }
                results_fh.write(json.dumps(record) + "\n")
                results_fh.flush()
                captured += 1
                place = analysis["placement"]
                log.ok("sample",
                       f"  {win_key}/{cond_key} sample {sample_i}: "
                       f"landmasses={place['n_landmasses_with_a_player']} "
                       f"reachable={place['pairwise_land_reachable_fraction']} "
                       f"any_zero="
                       f"{analysis['resources']['any_player_zero_of_a_kind']}",
                       window=win_key, condition=cond_key,
                       sample_index=sample_i, file=str(dest),
                       n_landmasses=place["n_landmasses_with_a_player"],
                       reachable=place["pairwise_land_reachable_fraction"],
                       generate_s=round(cap.generate_s, 3),
                       save_s=round(cap.save_s, 3),
                       sample_total_s=round(time.time() - t1, 3))

    log.close(f"done {captured} captured", captured=captured,
              expected=total * args.n_samples, results=str(results_path),
              wall_s=round(time.time() - t_start, 1))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
