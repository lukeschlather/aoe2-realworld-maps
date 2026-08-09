"""Real-engine capture pass over the shipped "Real World Maps" mod: for
each of the 10 regions in build_mod.py's MOD_REGIONS, generate its script
once (using the exact args that ship, no drift) and capture N=10 real
engine samples - enough N for the fairness stats this project's earlier
research phases deliberately skipped (see TUNING_STATUS.md /
[[feedback-verification-and-automation]]: N=1-2 was fine for breadth-over-
parameters exploration, not for a fairness claim).

Reuses tuning_matrix.py's proven capture primitives (SLOT_PATH swap,
click_sequence, sample_analysis.analyze_capture) unchanged - this script
only supplies a different iteration shape (10 independent named regions,
one condition each, N=10) and additionally scores every sample against
aesthetic_metrics.compute_metrics_from_truth() using the region's own
lon/lat/span/rotate (not a tuning_matrix.WINDOWS lookup, since these
regions aren't part of that frozen research set).

Every run is scoped under a required --run-id, same convention as
tuning_matrix.py: out/mod_capture/<run-id>/results.jsonl.

Usage:
    uv run python automation/mod_capture.py --run-id first_pass
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
from rwmaps import scx_read  # noqa: E402
from rwmaps.cli import REGIONS  # noqa: E402
from aesthetic_metrics import cached_true_mask_geo, compute_metrics_from_truth  # noqa: E402
from build_mod import MOD_REGIONS  # noqa: E402
from rwmaps.fairness import profile_capture  # noqa: E402
from sample_analysis import analyze_capture  # noqa: E402
from tuning_matrix import (  # noqa: E402
    CANCEL_BTN,
    GENERATE_BTN,
    MENU_BTN,
    SAVE_BTN,
    UI_DRIVER,
)

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"

SIZE = 240
PLAYERS = 8
N_SAMPLES = 10


def resolve_geo(extra_args: list[str]) -> tuple[float, float, float, float]:
    """Recover (lon, lat, span_km, rotate) for a MOD_REGIONS entry, the same
    way cli.generate() resolves them from --region/--center/--span-km/
    --rotate - needed so the aesthetic truth mask uses the exact window
    each region actually ships with."""
    region = None
    center = None
    span = None
    rotate = 0.0
    it = iter(extra_args)
    for tok in it:
        if tok == "--region":
            region = next(it)
        elif tok.startswith("--center="):
            center = tok.split("=", 1)[1]
        elif tok == "--center":
            center = next(it)
        elif tok == "--span-km":
            span = float(next(it))
        elif tok == "--rotate":
            rotate = float(next(it))

    if region:
        lon, lat, region_span = REGIONS[region]
        if span is None:
            span = region_span
    if center:
        lon, lat = (float(v) for v in center.split(","))
    if lon is None or lat is None or span is None:
        raise ValueError(f"could not resolve geo from extra_args={extra_args}")
    return lon, lat, span, rotate


def game_is_running() -> bool:
    """Is the game process alive at all?

    Cheap, and it separates two failures that look identical from inside the
    click loop: a script the engine will not generate, and no engine. A pass
    once spent 1.9 hours reporting "Generate Map never registered a seed
    change" for ten regions in a row because the game had exited after the
    first one - three clicks x a 90s budget each, per region, into an empty
    desktop. Nothing about those runs said anything about the scripts.
    """
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "if (Get-Process -Name AoE2DE_s -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }"],
        capture_output=True, text=True)
    return "yes" in r.stdout


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


def already_done(results_path: Path, region: str) -> int:
    if not results_path.exists():
        return 0
    n = 0
    for line in results_path.open(encoding="utf-8"):
        rec = json.loads(line)
        if rec["region"] == region:
            n += 1
    return n


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True,
                    help="scopes output under out/mod_capture/<run-id>/ - reuse "
                         "the same run-id to resume a partially-completed pass")
    p.add_argument("--n-samples", type=int, default=N_SAMPLES)
    p.add_argument("--regions", default=None,
                    help="comma-separated subset of region names to run (default: "
                         "all 10) - handy for smoke-testing the pipeline on one "
                         "region/sample before committing to a full pass")
    return p.parse_args()


def main():
    args = parse_args()
    regions = MOD_REGIONS
    if args.regions:
        wanted = {r.strip() for r in args.regions.split(",")}
        regions = [(n, e) for n, e in MOD_REGIONS if n in wanted]
        missing = wanted - {n for n, _ in regions}
        if missing:
            raise SystemExit(f"unknown region(s): {missing}")

    outroot = REPO / "out" / "mod_capture" / args.run_id
    results_path = outroot / "results.jsonl"
    outroot.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    total = len(regions) * args.n_samples

    with results_path.open("a", encoding="utf-8") as results_fh:
        for region_i, (name, extra_args) in enumerate(regions, 1):
            done = already_done(results_path, name)
            if done >= args.n_samples:
                print(f"[region {region_i}/{len(regions)}] {name}: "
                      f"already have {done}/{args.n_samples}, skipping")
                continue

            lon, lat, span, rot = resolve_geo(extra_args)
            print(f"\n[region {region_i}/{len(regions)}] {name}  "
                  f"(elapsed {time.time()-t_start:.0f}s)")

            rms_dir = outroot / "scripts" / name
            gen_cmd = ["uv", "run", "rwmaps", name, "--outdir", str(rms_dir),
                       "--no-preview", *extra_args]
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
            # The game holds the slot file open while generating, so a swap
            # issued too soon after the previous sample dies with EACCES.
            # A multi-hour pass must not fall over on that.
            for attempt in range(40):
                try:
                    shutil.copyfile(rms_files[0], SLOT_PATH)
                    break
                except PermissionError:
                    time.sleep(0.5)
            else:
                print(f"  SKIP: slot stayed locked by the game")
                continue

            ai_type = None
            for line in rms_files[0].read_text(encoding="ascii").splitlines():
                if "ai_info_map_type" in line:
                    ai_type = line.split()[1]
                    break

            region_dir = outroot / name
            for sample_i in range(done, args.n_samples):
                t1 = time.time()
                before = newest_scenario()
                before_mtime = before.stat().st_mtime if before else 0
                try:
                    click_sequence(before_mtime)
                except Exception as e:
                    # Distinguish "the engine rejected this" from "there is no
                    # engine" before burning the rest of the pass on retries.
                    if not game_is_running():
                        raise SystemExit(
                            "\nABORTING: the game is not running. Every remaining "
                            "capture would fail the same way and tell us nothing "
                            "about the scripts. Relaunch AoE2, open the Scenario "
                            "Editor on the AA_rw_placeholder_tester map, and rerun "
                            "with the same --run-id to resume."
                        ) from e
                    print(f"  sample {sample_i}: capture FAILED ({e})")
                    continue
                after = newest_scenario()
                if after is None or after.stat().st_mtime <= before_mtime:
                    print(f"  sample {sample_i}: no new file, skipping")
                    continue

                # Archive the raw capture before analyzing - the game
                # reuses one filename for Save, so the next sample's
                # capture overwrites this one (same reasoning as
                # tuning_matrix.py: a bug found later in analysis
                # shouldn't require re-running the whole batch).
                archive_dir = region_dir / "raw"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / f"sample_{sample_i:03d}.aoe2scenario"
                shutil.copyfile(after, dest)

                try:
                    analysis = analyze_capture(dest, size=SIZE)
                    real_mask = scx_read.read_land_mask(dest)
                    truth_10m = cached_true_mask_geo(lon, lat, span, rot, size=SIZE)
                    aesthetic = compute_metrics_from_truth(truth_10m, real_mask)
                    # Recorded alongside, not instead of, analyze_capture's
                    # numbers: that function's nearest-TC ownership is what
                    # every previously captured run used, so replacing it
                    # would make this run incomparable to them. The fairness
                    # profile is the current model (exclusive / contested /
                    # unclaimed, walkable-mask distances, wood and openness).
                    fairness = profile_capture(dest)
                except Exception as e:
                    print(f"  sample {sample_i}: ANALYSIS FAILED ({e})")
                    continue

                record = {
                    "region": name, "extra_args": extra_args, "ai_map_type": ai_type,
                    "lon": lon, "lat": lat, "span_km": span, "rotate": rot,
                    "sample_index": sample_i,
                    **analysis, "aesthetic": aesthetic, "fairness": fairness,
                }
                results_fh.write(json.dumps(record) + "\n")
                results_fh.flush()
                print(f"  sample {sample_i}: captured+analyzed in {time.time()-t1:.1f}s "
                      f"(landmasses={analysis['placement']['n_landmasses_with_a_player']}, "
                      f"reachable={analysis['placement']['pairwise_land_reachable_fraction']}, "
                      f"any_zero={analysis['resources']['any_player_zero_of_a_kind']}, "
                      f"iou_10m={aesthetic['iou_10m']:.2f})")

    print(f"\nDONE in {time.time()-t_start:.0f}s -> {results_path}")


if __name__ == "__main__":
    main()
