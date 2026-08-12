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
from build_mod import DEBUG_MOD_NAME, MOD_REGIONS  # noqa: E402
import editor  # noqa: E402
from frame_server import snapshot_ring  # noqa: E402
from rwmaps.fairness import profile_capture  # noqa: E402
from sample_analysis import analyze_capture  # noqa: E402

# The placeholder slot lives in the DEBUG mod - that is the whole reason
# the debug variant exists, and it is where install_mod.py --all syncs it.
# install.MOD_NAME is a third, legacy mod ("Real World Projections") from
# before build_mod existed; writing the slot there silently does nothing,
# because the Scenario Editor is loading the debug mod's copy. That cost a
# whole two-region capture pass, which regenerated Britain and Italy
# correctly, wrote them where nothing reads, and then captured the
# previously-installed Salish Sea script twice - reporting Salish's
# geometry under Britain's and Italy's names.
SLOT_PATH = (install_mod.scripts_dir(DEBUG_MOD_NAME)
             / "AA_rw_placeholder_tester.rms")
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"

#: Below this, the captured coastline is not the region we asked for. Real
#: captures across all 11 regions run 0.80-0.90; the two-region pass that
#: captured Salish Sea under Britain's name scored 0.25.
IOU_WRONG_MAP = 0.55

SIZE = 240
PLAYERS = 8

#: How many crashes a single pass will recover from before giving up.
#: Recovering forever would turn a systematic failure into an all-night
#: loop that captures nothing and says so nowhere.
MAX_RECOVERIES = 3
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


def _frames_note() -> str:
    """Freeze the frame viewer's ring, if one is running, and say where.

    The crash that motivated this left nothing behind at all - no picture
    of the state the editor was in, no record of the click before it. If
    ``frame_server.py`` is up, the seconds leading to the abort are still
    in its ring right now and will be overwritten shortly, so grab them
    here rather than asking someone to remember to.
    """
    path = snapshot_ring()
    return f" Frames leading up to it: {path}." if path else ""


def game_pid() -> str | None:
    """The running game's process id, or None if there is no game.

    Cheap, and it separates two failures that look identical from inside the
    click loop: a script the engine will not generate, and no engine. A pass
    once spent 1.9 hours reporting "Generate Map never registered a seed
    change" for ten regions in a row because the game had exited after the
    first one - three clicks x a 90s budget each, per region, into an empty
    desktop. Nothing about those runs said anything about the scripts.

    The **id**, not just the presence of a process by that name. The editor
    crashes, and when it is relaunched it comes back at its defaults - Blank
    Map, Small [144] - not on the placeholder slot at Huge [240] that every
    capture assumes. A name check answers "yes, the game is running" to that
    and the pass keeps clicking into a map size that makes the scripts
    meaningless, since land areas here are absolute tile counts. A changed
    pid is proof the process we validated against is gone.
    """
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "(Get-Process -Name AoE2DE_s -ErrorAction SilentlyContinue"
         " | Select-Object -First 1 -ExpandProperty Id)"],
        capture_output=True, text=True)
    pid = r.stdout.strip()
    return pid or None


def game_is_running() -> bool:
    return game_pid() is not None


def newest_scenario():
    files = sorted(SCENARIO_DIR.glob("*.aoe2scenario"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def click_sequence(before_mtime: float):
    """Generate, then save - in Python, and never on a blind click.

    Replaces the PowerShell driver. Three things changed, each of which had
    caused a wrong result before:

    * Generation is watched by the **Generate button's colour** rather than
      by OCR-polling the seed box. The button greys on start and returns to
      red on finish, so a crash *during* generation is distinguishable from
      a generation that never started - the seed only ever changes at the
      end and cannot tell those apart. It also stops screen-grabbing a
      fullscreen D3D application a few times a second while the engine is
      under load.
    * Save **verifies the Menu overlay is really up** before clicking Save.
      The old sequence slept 200ms and clicked (960, 436), which without
      the overlay is the middle of the map, where a click is a brush
      stroke. That is the leading suspicion for the editor's crashes.
    * Save answers the **Save Scenario file browser**, which the first save
      of every session opens. The old code only knew the silent form, so it
      left the dialog open and reported "the menu closed but no file
      appeared" - a failure mode recorded as not understood at the time.

    ``before_mtime`` is unused now: ``editor.save`` takes its own baseline
    immediately before clicking, which is tighter than one taken by the
    caller several seconds earlier.
    """
    result = editor.generate()
    if not result.ok:
        raise RuntimeError(f"generate: {result.detail}")
    if editor.save(SCENARIO_DIR) is None:
        raise RuntimeError("save produced no new scenario file")


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
    p.add_argument("--extra", nargs=argparse.REMAINDER, default=[],
                    help="extra rwmaps flags appended to every region's regen, "
                         "for testing a parameter that is not in MOD_REGIONS yet "
                         "(e.g. --extra --island-resources). Must come last.")
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

    recoveries = 0
    started_pid = game_pid()
    if started_pid is None:
        raise SystemExit(
            "ABORTING: the game is not running. Launch AoE2, open the "
            "Scenario Editor on AA_rw_placeholder_tester at Huge [240] with "
            "8 players, and rerun."
        )
    print(f"game pid {started_pid} - a change means it crashed and came back "
          f"at its defaults, which no capture after that point can be trusted "
          f"against")

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
                       "--no-preview", *extra_args, *args.extra]
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

            # Confirm the editor will generate OUR script before spending
            # ~90s a sample on it. A disabled mod silently substitutes the
            # first stock script and the result looks superficially right.
            ok, why = editor.preflight()
            print(f"  preflight: {why}")
            if not ok:
                raise SystemExit(f"\nABORTING before {name}: {why}")

            region_dir = outroot / name
            # A while loop, not `for sample_i in range(...)`: a sample lost
            # to a crash must be *retried*, and `continue` in a for loop
            # advances the counter. Every path out of the body therefore
            # increments sample_i explicitly, except the crash path, which
            # deliberately does not - MAX_RECOVERIES is what bounds that one.
            sample_i = done
            while sample_i < args.n_samples:
                t1 = time.time()
                # Checked before every sample, not only after a click throws.
                # A crash-and-relaunch does not necessarily make a click fail
                # - it makes it succeed against the wrong editor state - so
                # waiting for an exception to ask is waiting for the one
                # signal this failure does not send.
                # A loop, not an if: this runs *before* the sample is
                # attempted, so recovering leaves sample_i untouched and the
                # lost sample is simply taken again. An `if ... continue`
                # here would skip the sample it was trying to save.
                while (now_pid := game_pid()) != started_pid:
                    # The editor crashes, the cause is not understood, and
                    # the working assumption is that it is not something
                    # this project does - a cache clashing with swapped mod
                    # files, or an unlucky click. So the pass survives one
                    # rather than trying to prevent it: recover, rebuild the
                    # editor state, and retry the sample that was lost.
                    #
                    # Recovery is not optional politeness. A crash disables
                    # every mod on the next launch and brings the editor back
                    # at Blank Map / Small [144], and land areas here are
                    # absolute tile counts, so continuing without rebuilding
                    # the state captures a different map at a size that
                    # breaks it - silently.
                    print(f"  the game is pid {now_pid}, was {started_pid} - "
                          f"it crashed.{_frames_note()}")
                    if recoveries >= MAX_RECOVERIES:
                        raise SystemExit(
                            f"\nABORTING: recovered {recoveries} times "
                            f"already. Something is wrong beyond one unlucky "
                            f"crash; rerun with the same --run-id to resume "
                            f"once it is understood."
                        )
                    recoveries += 1
                    print(f"  recovering ({recoveries}/{MAX_RECOVERIES})")
                    if not (editor.recover() and editor.setup(PLAYERS)):
                        raise SystemExit(
                            "\nABORTING: could not get the editor back to a "
                            "usable state. Frames and logs above say how far "
                            "it got."
                        )
                    started_pid = game_pid()
                    # setup() rebuilds player count, Random Map and Huge
                    # [240] - it does NOT put the Random Map selector back
                    # on our slot, so post-recovery is exactly when the
                    # silent wrong-map capture preflight exists to catch is
                    # most likely. Ask before spending a sample, not after.
                    ok, why = editor.preflight()
                    if not ok:
                        raise SystemExit(
                            f"\nABORTING: recovered, but the editor would not "
                            f"generate our script: {why}"
                        )
                    print(f"  recovered as pid {started_pid} ({why}), "
                          f"retrying sample")
                before = newest_scenario()
                before_mtime = before.stat().st_mtime if before else 0
                try:
                    click_sequence(before_mtime)
                except Exception as e:
                    # Distinguish "the engine rejected this" from "the engine
                    # died". A crash *during* the click sequence used to abort
                    # the whole pass, even though this script already carries
                    # the machinery and the budget to come back from one - and
                    # the crash it aborted on had happened mid-generation, on
                    # region 2 of 11, discarding nine regions' worth of work
                    # over one recoverable event. It is the same failure the
                    # loop above already handles; the only reason it landed
                    # here instead is that the game happened to die while a
                    # click was in flight rather than between two samples. So
                    # fall through to that loop, which recovers, re-preflights,
                    # and retries this same sample - bounded by MAX_RECOVERIES,
                    # so a script that reliably kills the engine still stops
                    # the pass instead of looping on it forever.
                    if not game_is_running():
                        print(f"  sample {sample_i}: the game died mid-capture "
                              f"({e}) - recovering and retrying this sample"
                              f"{_frames_note()}")
                        continue  # sample_i NOT incremented - retry it
                    print(f"  sample {sample_i}: capture FAILED ({e})")
                    sample_i += 1
                    continue
                after = newest_scenario()
                if after is None or after.stat().st_mtime <= before_mtime:
                    print(f"  sample {sample_i}: no new file, skipping")
                    sample_i += 1
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
                    sample_i += 1
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
                # IoU against the region's own true coastline is the ground
                # truth for "the engine generated the map we asked for". A
                # script swap that silently does not reach the game shows up
                # here and nowhere else: the pass captures whatever was
                # installed before, and every downstream table then reports
                # one region's geometry under another region's name. Our
                # regions run ~0.85; a real region has never scored this low.
                if aesthetic["iou_10m"] < IOU_WRONG_MAP:
                    print(f"  *** WARNING: iou_10m {aesthetic['iou_10m']:.2f} is far "
                          f"below anything {name} should score. The capture is "
                          f"probably not {name} at all - check that\n"
                          f"      {SLOT_PATH}\n"
                          f"      is the slot the Scenario Editor is actually "
                          f"loading, then rerun.")
                sample_i += 1

    print(f"\nDONE in {time.time()-t_start:.0f}s -> {results_path}")


if __name__ == "__main__":
    main()
