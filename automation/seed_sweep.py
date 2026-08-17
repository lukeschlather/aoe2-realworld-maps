"""Run the same script through the real engine N times, capturing resource
reachability each time, to see the distribution across random seeds rather
than trusting a single lucky/unlucky roll. The .rms text (land_position,
discs, etc.) never changes across iterations - only the engine's own RNG
does, so this isolates "how often does placement luck cause a shortfall"
from "is this region structurally short on resources."

Writes one JSON line per successful iteration to <outdir>/results.jsonl as
it goes, so a crash or interruption partway through loses at most the
in-flight iteration - rerunning the same command resumes where it left off.

Requires the one-time manual editor setup already done (Map panel open,
Random Map checked, AA_rw_placeholder_tester selected, size/players set,
Generate Map clicked once) - same prerequisite as gen_loop.py.

Usage:
    uv run python automation/seed_sweep.py --name italy_240_v2 --region italy --n 100
"""

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
from rwmaps import scx_read  # noqa: E402
from rwmaps.analysis import resource_ownership  # noqa: E402
from runlog import RunLog  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--region")
    ap.add_argument("--size", type=int, default=240)
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--outdir", default=None)
    args, extra = ap.parse_known_args()

    outdir = REPO / (args.outdir or f"out/seedsweep-{args.name}")
    outdir.mkdir(parents=True, exist_ok=True)
    results_path = outdir / "results.jsonl"

    # Regenerate the .rms fresh (deterministic - land_position/discs never
    # change run to run) rather than trusting a stale path from an earlier
    # session, and swap it into the slot ONCE - every iteration below reuses
    # this exact script, only the engine's own RNG differs per click.
    log = RunLog(outdir, run_id=args.name)
    log.attach_editor(editor)
    log.event("plan", f"{args.n} seeds of {args.name}", name=args.name,
              n=args.n, size=args.size, players=args.players,
              region=args.region, extra_args=extra)

    rms_dir = outdir / "script"
    gen_cmd = ["uv", "run", "rwmaps", args.name,
               *(["--region", args.region] if args.region else []),
               "--size", str(args.size), "--players", str(args.players),
               "--outdir", str(rms_dir), "--no-preview", *extra]
    # Captured so rwmaps' narration lands in the JSON log, not on stdout
    # where no log would keep it.
    # One event, not a timer plus a detail event - both would carry this
    # duration and a query summing by kind would count the regen twice.
    t_regen = time.time()
    r = subprocess.run(gen_cmd, cwd=REPO, capture_output=True, text=True)
    regen_s = time.time() - t_regen
    log.event("regen", None, name=args.name, command=" ".join(gen_cmd),
              returncode=r.returncode, stdout=r.stdout[-4000:],
              ok=r.returncode == 0, duration_s=round(regen_s, 3))
    if r.returncode != 0:
        log.fail("regen_failed", "REGEN FAILED", stderr=r.stderr[-2000:],
                 duration_s=round(regen_s, 3))
        log.close("aborted")
        raise RuntimeError(f"rwmaps failed - its full stderr is in "
                           f"{log.json_path} under kind regen_failed")
    rms_files = list(rms_dir.rglob("*.rms"))
    if len(rms_files) != 1:
        log.fail("slot_swap", f"expected 1 .rms, found {len(rms_files)}",
                 found=[str(p) for p in rms_files])
        log.close("aborted")
        raise RuntimeError(f"expected exactly one .rms in {rms_dir}, found {rms_files}")
    shutil.copyfile(rms_files[0], SLOT_PATH)
    log.event("slot_swap", None, rms=str(rms_files[0]), slot=str(SLOT_PATH))

    # Timed by hand, so this is ONE event: a timer writes its own, and a
    # second explicit event of the same kind double-counts in any query that
    # sums durations by kind.
    t_pre = time.time()
    ok, why = editor.ensure_ready(args.players)
    preflight_s = time.time() - t_pre
    if not ok:
        log.fail("preflight_failed", f"ABORT editor unusable: {why}", why=why,
                 duration_s=round(preflight_s, 3))
        log.close("aborted")
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    log.ok("preflight", "editor ready", why=why,
           duration_s=round(preflight_s, 3))

    done = 0
    if results_path.exists():
        done = sum(1 for _ in results_path.open(encoding="utf-8"))
        log.event("resume", f"resuming at iteration {done}", done=done)

    with results_path.open("a", encoding="utf-8") as fh:
        for i in range(done, args.n):
            t0 = time.time()
            try:
                cap = editor.generate_and_save(SCENARIO_DIR)
            except Exception as e:
                log.fail("capture", f"  seed {i}: FAILED", seed_index=i,
                         error=str(e))
                continue

            dest = outdir / f"{args.name}_seed_{i:03d}.aoe2scenario"
            shutil.copyfile(cap.path, dest)

            with log.timer("analyze", seed_index=i):
                mask = scx_read.read_land_mask(dest)
                tcs = scx_read.read_town_centers(dest)
                resources = scx_read.read_resources(dest)
                per_player, unclaimed = resource_ownership(mask, tcs, resources)

            record = {"seed_index": i, "n_tcs": len(tcs),
                      "per_player": {str(k): v for k, v in per_player.items()},
                      "unclaimed": unclaimed}
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            # This sweep deletes its captures, so these two logs plus
            # results.jsonl are the only record a run leaves behind.
            log.ok("seed", f"  seed {i}: {len(tcs)} TCs unclaimed={unclaimed}",
                   seed_index=i, n_tcs=len(tcs), unclaimed=unclaimed,
                   generate_s=round(cap.generate_s, 3),
                   save_s=round(cap.save_s, 3),
                   iteration_s=round(time.time() - t0, 3))
            # Don't keep the raw .aoe2scenario around for a 100-run sweep -
            # only the extracted stats matter and these are ~200KB each.
            dest.unlink()

    log.close(f"done -> {results_path.name}", results=str(results_path))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
