"""Real-engine capture pass over the shipped "Real World Maps" mod: for
each preset with ``status: shipped``, generate its script once (using the
exact args that ship, no drift) and capture N=10 real engine samples - enough N for the fairness stats this project's earlier
research phases deliberately skipped (see TUNING_STATUS.md /
[[feedback-verification-and-automation]]: N=1-2 was fine for breadth-over-
parameters exploration, not for a fairness claim).

Reuses the shared capture primitives (SLOT_PATH swap,
editor.generate_and_save, sample_analysis.analyze_capture) - this script
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
from rwmaps.projection import north_from_legacy_rotate  # noqa: E402
from aesthetic_metrics import cached_true_mask_geo, compute_metrics_from_truth  # noqa: E402
from update_mod import (DEBUG_MOD_NAME, MOD_NAME,  # noqa: E402
                       shipped_filename, shipped_regions)
from rwmaps.presets import Preset, Registry  # noqa: E402
import editor  # noqa: E402
from frame_server import snapshot_ring  # noqa: E402
from rwmaps.fairness import profile_capture  # noqa: E402
from runlog import RunLog  # noqa: E402
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
    """Recover (lon, lat, span_km, north_deg) for a region entry.

    Resolves the same way cli.generate() does, so the aesthetic truth mask
    uses the exact window the region ships with. ``north_deg`` is screen
    space (0 = north up); a legacy grid-space ``--rotate`` is converted.
    """
    region = None
    center = None
    span = None
    north = 0.0
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
        elif tok == "--north":
            north = float(next(it))
        elif tok == "--rotate":   # pre-2026-08-16 grid-space value
            north = north_from_legacy_rotate(float(next(it)))

    if region:
        lon, lat, region_span = REGIONS[region]
        if span is None:
            span = region_span
    if center:
        lon, lat = (float(v) for v in center.split(","))
    if lon is None or lat is None or span is None:
        raise ValueError(f"could not resolve geo from extra_args={extra_args}")
    return lon, lat, span, north


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


#: A real 240x240 capture is ~100 KB. Anything this small is the game's Save
#: still in flight, not a map.
MIN_SCENARIO_BYTES = 20_000


def archive_when_settled(src: Path, dest: Path, *, tries: int = 60,
                         pause: float = 0.25) -> bool:
    """Copy ``src`` once the game has finished writing it.

    The Save dialog closing is not the same event as the file being complete.
    Measured 2026-08-19: a pass archived a 128-byte ``sample_000`` and lost
    the sample to "Error -5 while decompressing data: incomplete or truncated
    stream" - the copy caught the file mid-write, and the only sign was an
    analysis failure two steps later, which reads like a bad map rather than
    a bad read.

    So: wait for the size to stop changing, refuse anything implausibly
    small, and check the copy came out the same size as the source. Two equal
    reads rather than one, on the same principle as everything else in this
    harness - a single read straight after a click gets a transient.
    """
    last = -1
    stable = 0
    for _ in range(tries):
        try:
            size = src.stat().st_size
        except OSError:
            size = -1
        if size >= MIN_SCENARIO_BYTES and size == last:
            stable += 1
            if stable >= 2:
                try:
                    shutil.copyfile(src, dest)
                except PermissionError:
                    time.sleep(pause)
                    continue
                # The source can still grow between the stat and the copy, so
                # the copy itself is checked rather than assumed.
                if dest.stat().st_size == src.stat().st_size >= MIN_SCENARIO_BYTES:
                    return True
                stable = 0
        else:
            stable = 0
        last = size
        time.sleep(pause)
    return False


def newest_scenario():
    files = sorted(SCENARIO_DIR.glob("*.aoe2scenario"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


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
    p.add_argument("--from-git", metavar="REF", default=None,
                    help="capture the script committed at REF (e.g. HEAD) "
                         "instead of regenerating from src/. The regen path "
                         "can only ever produce src/ as it stands now, so "
                         "this is what makes 'did this change break the "
                         "engine' answerable with the engine rather than by "
                         "reading the diff. Recorded per sample.")
    p.add_argument("--presets", nargs="+", metavar="LABEL", default=None,
                    help="capture these presets from the registry instead of "
                         "what ships. The forward path: a preset exists, with "
                         "a hash and a window, before any engine time is spent "
                         "on it, so every sample this pass writes joins back "
                         "to it exactly rather than by matching argv strings.")
    p.add_argument("--region-set", type=Path, default=None,
                    help="JSON file of [[name, [rwmaps args...]], ...] to "
                         "capture INSTEAD of build_mod.MOD_REGIONS. This is "
                         "how a window that does not ship yet gets put "
                         "through the real engine: candidates have to be "
                         "captured before they can be judged, and adding "
                         "them to MOD_REGIONS to do that would ship them. "
                         "Everything downstream is unchanged - the same "
                         "editor, recovery, analysis and IoU-vs-truth check.")
    p.add_argument("--extra", nargs=argparse.REMAINDER, default=[],
                    help="extra rwmaps flags appended to every region's regen, "
                         "for testing a parameter that is not in MOD_REGIONS yet "
                         "(e.g. --extra --island-resources). Must come last.")
    return p.parse_args()


def main():
    args = parse_args()
    if args.region_set:
        source = [(n, list(e)) for n, e in
                  json.loads(args.region_set.read_text(encoding="utf-8"))]
        if args.from_git:
            raise SystemExit("--from-git reads mod/, so it cannot be combined "
                             "with --region-set (those regions do not ship)")
        # Not printed: the plan event below carries region_set, and stdout is
        # meant to be exactly the terse log, nothing more.
        pass
    elif args.presets:
        reg = Registry(REPO).load()
        # Keyed by LABEL, not display name: labels are unique and
        # filesystem-safe, display names collide (two Scandinavia windows
        # under one name is exactly how a capture ends up filed under
        # another map's geometry).
        source = [(p.label, list(p.argv)) for p in reg.select(args.presets)]
    else:
        source = shipped_regions()
    regions = source
    if args.regions:
        wanted = {r.strip() for r in args.regions.split(",")}
        regions = [(n, e) for n, e in source if n in wanted]
        missing = wanted - {n for n, _ in regions}
        if missing:
            raise SystemExit(f"unknown region(s): {missing}")

    outroot = REPO / "out" / "mod_capture" / args.run_id
    results_path = outroot / "results.jsonl"
    outroot.mkdir(parents=True, exist_ok=True)
    log = RunLog(outroot, args.run_id)
    log.event("plan", f"{len(regions)} regions x {args.n_samples} samples",
              regions=[n for n, _ in regions], n_samples=args.n_samples,
              from_git=args.from_git, region_set=str(args.region_set or ""))
    log.attach_editor(editor)

    recoveries = 0
    # Build the editor rather than demanding someone else did. This used to
    # abort if the game was not up, which was reasonable when setup was
    # manual and is now just a way to lose a pass to the state the *previous*
    # pass's crash left behind - mods off, editor at Blank Map / Small [144].
    # Timed by hand rather than with log.timer, so this is ONE event: the
    # timer writes its own, and a second explicit event would mean two
    # records of the same thing for a query to trip over.
    t_ready = time.time()
    ok, why = editor.ensure_ready(PLAYERS)
    ready_s = time.time() - t_ready
    if not ok:
        log.fail("editor_ready", f"ABORT editor unusable: {why}", why=why,
                 duration_s=round(ready_s, 3))
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    started_pid = game_pid()
    # The pid is in the JSON log, not the terse one: it is exactly the kind
    # of value that differs between two identical runs, and an agent reading
    # the short log wants "ready", not which process it happens to be.
    log.ok("editor_ready", "editor ready", why=why, pid=started_pid,
           duration_s=round(ready_s, 3))

    t_start = time.time()
    total = len(regions) * args.n_samples

    with results_path.open("a", encoding="utf-8") as results_fh:
        for region_i, (name, extra_args) in enumerate(regions, 1):
            done = already_done(results_path, name)
            if done >= args.n_samples:
                log.event("region_skip",
                          f"region {region_i}/{len(regions)} {name}: have "
                          f"{done}/{args.n_samples}, skipping",
                          region=name, have=done, want=args.n_samples)
                continue

            lon, lat, span, rot = resolve_geo(extra_args)
            # Resolve the preset identity once per region and stamp it into
            # every sample. Recovering it afterwards means matching argv
            # strings against a registry, which works and is fragile - argv
            # spellings drift, display names get reused. A hash in the row
            # does not.
            try:
                _p = Preset.create(name, name, [*extra_args, *args.extra])
                params_hash, preset_id = _p.params_hash, _p.id
            except Exception as e:
                params_hash = preset_id = None
                log.event("preset_unresolved", None, region=name, error=str(e))
            log.event("region_start", f"region {region_i}/{len(regions)} {name}",
                      region=name, index=region_i, of=len(regions),
                      lon=lon, lat=lat, span_km=span, north_deg=rot,
                      extra_args=extra_args)

            rms_dir = outroot / "scripts" / name
            # Clear it first. rwmaps writes into a fresh timestamped subdir
            # per invocation, so a region regenerated twice under one run-id
            # leaves two .rms behind and the "exactly one" check below then
            # skips it - permanently, since resuming regenerates and counts
            # three, four, five. That is exactly backwards for the flag whose
            # whole job is to resume: this pass crashed during Cramped Italy,
            # and the resume skipped Cramped Italy for having generated it
            # once already. Nothing here is worth keeping across runs - the
            # script is a deterministic function of the region's args, and
            # the copy that matters is archived beside its capture.
            if rms_dir.exists():
                shutil.rmtree(rms_dir)
            regen_s = 0.0   # --from-git reads a committed script; nothing to build
            if args.from_git:
                # Capture a committed script instead of regenerating one.
                # Regeneration always reflects src/ as it stands, so it cannot
                # be asked for the *previous* behaviour of a region - and when
                # a change to src/ makes the engine hang or crash, that is
                # exactly the comparison wanted: same region, same editor, one
                # script from before and one after. The scripts in mod/ are
                # committed build outputs, so a ref is all it takes.
                rms_dir.mkdir(parents=True, exist_ok=True)
                rel = (f"mod/{MOD_NAME}/resources/_common/random-map-scripts/"
                       f"{shipped_filename(name)}")
                dest = rms_dir / shipped_filename(name)
                r = subprocess.run(["git", "show", f"{args.from_git}:{rel}"],
                                   cwd=REPO, capture_output=True, text=True)
                if r.returncode != 0:
                    log.fail("from_git", f"  {name}: SKIP git show "
                             f"{args.from_git}:{rel} failed", region=name,
                             ref=args.from_git, path=rel,
                             stderr=r.stderr.strip()[:2000])
                    continue
                dest.write_text(r.stdout, encoding="ascii", newline="\n")
                log.ok("from_git", f"  {name}: script from {args.from_git}",
                       region=name, ref=args.from_git, path=rel,
                       bytes=dest.stat().st_size)
            else:
                gen_cmd = ["uv", "run", "rwmaps", name, "--outdir", str(rms_dir),
                           "--no-preview", *extra_args, *args.extra]
                t_regen = time.time()
                r = subprocess.run(gen_cmd, cwd=REPO, capture_output=True,
                                   text=True)
                regen_s = time.time() - t_regen
                # rwmaps' stdout was captured and discarded. It reports the
                # land fraction and coastline IoU of the script it just built,
                # which is the Python-side prediction the engine capture is
                # then measured against - worth keeping beside it.
                log.event("regen", None, region=name,
                          command=" ".join(gen_cmd), returncode=r.returncode,
                          stdout=r.stdout[-4000:], ok=r.returncode == 0,
                          duration_s=round(regen_s, 3))
                if r.returncode != 0:
                    log.fail("regen_failed", f"  {name}: REGEN FAILED",
                             region=name, stderr=r.stderr[-2000:],
                             duration_s=round(regen_s, 3))
                    continue

            rms_files = list(rms_dir.rglob("*.rms"))
            if len(rms_files) != 1:
                log.fail("slot_swap", f"  {name}: SKIP expected 1 .rms, found "
                         f"{len(rms_files)}", region=name,
                         found=[str(p) for p in rms_files])
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
                log.fail("slot_swap", f"  {name}: SKIP slot stayed locked by "
                         f"the game", region=name, slot=str(SLOT_PATH))
                continue

            ai_type = None
            for line in rms_files[0].read_text(encoding="ascii").splitlines():
                if "ai_info_map_type" in line:
                    ai_type = line.split()[1]
                    break

            # Confirm the editor will generate OUR script before spending
            # ~90s a sample on it - and fix it if it will not, rather than
            # ending the pass over a state a rebuild repairs. A disabled mod
            # silently substitutes the first stock script and the result
            # looks superficially right.
            with log.timer("preflight", region=name) as t:
                ok, why = editor.ensure_ready(PLAYERS)
            preflight_s = t.seconds
            if not ok:
                log.fail("preflight_failed", f"  {name}: ABORT {why}",
                         region=name, why=why,
                         duration_s=round(preflight_s, 3))
                raise SystemExit(f"\nABORTING before {name}: {why}")
            started_pid = game_pid()

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
                    log.fail("crash", f"  {name} sample {sample_i}: the game "
                             f"crashed", region=name, sample_index=sample_i,
                             was_pid=started_pid, now_pid=now_pid,
                             frames=_frames_note().strip() or None)
                    if recoveries >= MAX_RECOVERIES:
                        raise SystemExit(
                            f"\nABORTING: recovered {recoveries} times "
                            f"already. Something is wrong beyond one unlucky "
                            f"crash; rerun with the same --run-id to resume "
                            f"once it is understood."
                        )
                    recoveries += 1
                    log.event("recover_start",
                              f"  recovering ({recoveries}/{MAX_RECOVERIES})",
                              region=name, attempt=recoveries,
                              max_recoveries=MAX_RECOVERIES)
                    t_rec = time.time()
                    # Rebuild, then *ask* whether the rebuild worked, and
                    # rebuild again if it did not. setup() restores player
                    # count, Random Map and Huge [240] but not the Random Map
                    # selector, so post-recovery is when the silent wrong-map
                    # capture is most likely - and worse, the game disables
                    # our mods on the launch after a crash and records it
                    # *later* than recover() can see (measured: a clean
                    # quit/enable/relaunch holds `off=[]` for 268s, so there
                    # is no delay to wait out - the write comes only after a
                    # real crash, minutes in, after recover() has already
                    # checked and passed). Preflight is the check that
                    # actually catches it, and it used to abort the pass on
                    # it, discarding every remaining region over something a
                    # second recovery fixes: editor.recover() now treats a
                    # running game with our mods off as recoverable.
                    ok, why = editor.ensure_ready(PLAYERS)
                    if not ok:
                        log.fail("recover", f"  ABORT could not rebuild the "
                                 f"editor: {why}", region=name, why=why,
                                 duration_s=round(time.time() - t_rec, 3))
                        raise SystemExit(
                            f"\nABORTING: could not get the editor back to a "
                            f"usable state: {why}. Frames and logs above say "
                            f"how far it got."
                        )
                    started_pid = game_pid()
                    # Cold-start recovery is the most expensive thing a pass
                    # can do (game launch, cinematic, the whole editor walk),
                    # and it is the number a pass-duration estimate is most
                    # often missing. Recorded as its own duration.
                    log.ok("recover", f"  recovered, retrying sample",
                           region=name, attempt=recoveries, pid=started_pid,
                           why=why, duration_s=round(time.time() - t_rec, 3))
                before = newest_scenario()
                before_mtime = before.stat().st_mtime if before else 0
                try:
                    cap = editor.generate_and_save(SCENARIO_DIR)
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
                        log.fail("capture", f"  {name} sample {sample_i}: game "
                                 f"died mid-capture, retrying", region=name,
                                 sample_index=sample_i, error=str(e),
                                 died=True,
                                 frames=_frames_note().strip() or None)
                        continue  # sample_i NOT incremented - retry it
                    log.fail("capture", f"  {name} sample {sample_i}: capture "
                             f"FAILED", region=name, sample_index=sample_i,
                             error=str(e), died=False)
                    sample_i += 1
                    continue
                log.event("capture", None, region=name, sample_index=sample_i,
                          ok=True, generate_s=round(cap.generate_s, 3),
                          save_s=round(cap.save_s, 3),
                          total_s=round(cap.total_s, 3), file=str(cap.path))
                after = newest_scenario()
                if after is None or after.stat().st_mtime <= before_mtime:
                    log.fail("capture_file", f"  {name} sample {sample_i}: no "
                             f"new file, skipping", region=name,
                             sample_index=sample_i)
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
                if not archive_when_settled(after, dest):
                    log.fail("archive", f"  {name} sample {sample_i}: the "
                             f"capture never finished being written",
                             region=name, sample_index=sample_i,
                             src=str(after), bytes=after.stat().st_size)
                    sample_i += 1
                    continue

                # Is this file our map at all? Two free checks, before any
                # analysis, because the way this fails is silent and the
                # error it eventually raises names none of it.
                #
                # Measured on run ``britain_ramsey``: the editor had been
                # left mid-``setup`` by an interrupted pass - Blank Map,
                # "Small (3 player) [144]" - and preflight passed it, because
                # preflight can see the mod state and the selector template
                # and nothing else. Six samples generated stock Arabia at
                # 144x144, saved under the editor's own default name, and the
                # only symptom was "operands could not be broadcast together
                # with shapes (144,144) (240,240)" out of the aesthetic
                # metrics - a message about the wrong subject entirely.
                #
                # ``save()`` types SAVE_NAME into the file browser, so a
                # capture that lands under any other name did not come from
                # our slot; and the grid is 240 in every configuration this
                # project captures.
                if after.stem != editor.SAVE_NAME:
                    log.fail("wrong_slot",
                             f"  *** {name} sample {sample_i}: the editor "
                             f"saved {after.name}, not {editor.SAVE_NAME}"
                             f".aoe2scenario - it generated something other "
                             f"than {SLOT_PATH.name}. The editor is "
                             f"misconfigured, not the script; close the game "
                             f"and rerun so setup() runs.",
                             region=name, sample_index=sample_i,
                             saved=after.name, expected=editor.SAVE_NAME)
                    raise SystemExit(2)
                grid_n = int(scx_read.read_terrain_grid(dest).shape[0])
                if grid_n != SIZE:
                    log.fail("wrong_size",
                             f"  *** {name} sample {sample_i}: the capture is "
                             f"{grid_n}x{grid_n}, not {SIZE}x{SIZE} - the "
                             f"editor's Map Size is not Huge [{SIZE}]. Close "
                             f"the game and rerun so setup() runs.",
                             region=name, sample_index=sample_i,
                             grid=grid_n, expected=SIZE)
                    raise SystemExit(2)

                t_analyze = time.time()
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
                    log.fail("analyze", f"  {name} sample {sample_i}: ANALYSIS "
                             f"FAILED", region=name, sample_index=sample_i,
                             error=str(e), file=str(dest),
                             duration_s=round(time.time() - t_analyze, 3))
                    sample_i += 1
                    continue

                record = {
                    "region": name, "extra_args": extra_args, "ai_map_type": ai_type,
                    "preset_params_hash": params_hash, "preset_id": preset_id,
                    "from_git": args.from_git,
                    "lon": lon, "lat": lat, "span_km": span, "north_deg": rot,
                    "sample_index": sample_i,
                    # Per-phase, not one total: at the ~1000-generation goal
                    # scale the only actionable question is which phase owns
                    # the hours, and a single "captured+analyzed in Ns" cannot
                    # answer it. regen_s is per REGION (the script is built
                    # once and then sampled), so it is recorded on every
                    # sample of that region rather than divided between them.
                    "timing": {
                        "regen_s": round(regen_s, 2),
                        "preflight_s": round(preflight_s, 2),
                        "generate_s": round(cap.generate_s, 2),
                        "save_s": round(cap.save_s, 2),
                        "analyze_s": round(time.time() - t_analyze, 2),
                        "sample_total_s": round(time.time() - t1, 2),
                    },
                    **analysis, "aesthetic": aesthetic, "fairness": fairness,
                }
                results_fh.write(json.dumps(record) + "\n")
                results_fh.flush()
                place = analysis["placement"]
                # The terse line carries what a reader has to judge - is this
                # the right map, is anyone starved - and no times. Every
                # duration for this sample is already in events.jsonl under
                # kind "capture"/"analyze"/"sample", queryable exactly.
                log.ok("sample",
                       f"  {name} sample {sample_i}: iou={aesthetic['iou_10m']:.2f} "
                       f"landmasses={place['n_landmasses_with_a_player']} "
                       f"reachable={place['pairwise_land_reachable_fraction']} "
                       f"any_zero="
                       f"{analysis['legacy_resources_nearest_tc']['any_player_zero_of_a_kind']}",
                       region=name, sample_index=sample_i,
                       iou_10m=aesthetic["iou_10m"],
                       n_landmasses=place["n_landmasses_with_a_player"],
                       reachable=place["pairwise_land_reachable_fraction"],
                       file=str(dest), **record["timing"])
                # IoU against the region's own true coastline is the ground
                # truth for "the engine generated the map we asked for". A
                # script swap that silently does not reach the game shows up
                # here and nowhere else: the pass captures whatever was
                # installed before, and every downstream table then reports
                # one region's geometry under another region's name. Our
                # regions run ~0.85; a real region has never scored this low.
                if aesthetic["iou_10m"] < IOU_WRONG_MAP:
                    log.fail("wrong_map",
                             f"  *** WARNING {name} sample {sample_i}: "
                             f"iou={aesthetic['iou_10m']:.2f} is far below "
                             f"anything {name} should score - the capture is "
                             f"probably not {name}. Check that {SLOT_PATH} is "
                             f"the slot the editor is loading, then rerun.",
                             region=name, sample_index=sample_i,
                             iou_10m=aesthetic["iou_10m"],
                             threshold=IOU_WRONG_MAP, slot=str(SLOT_PATH))
                sample_i += 1

    captured = sum(1 for _ in results_path.open(encoding="utf-8"))
    log.close(f"done {captured}/{total} captured", captured=captured,
              expected=total, recoveries=recoveries,
              results=str(results_path))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
