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
    rms_dir = outdir / "script"
    gen_cmd = ["uv", "run", "rwmaps", args.name,
               *(["--region", args.region] if args.region else []),
               "--size", str(args.size), "--players", str(args.players),
               "--outdir", str(rms_dir), "--no-preview", *extra]
    print(f"[seed_sweep] {' '.join(gen_cmd)}")
    subprocess.run(gen_cmd, cwd=REPO, check=True)
    rms_files = list(rms_dir.rglob("*.rms"))
    if len(rms_files) != 1:
        raise RuntimeError(f"expected exactly one .rms in {rms_dir}, found {rms_files}")
    shutil.copyfile(rms_files[0], SLOT_PATH)
    print(f"[seed_sweep] slot script: {rms_files[0].name}")

    ok, why = editor.ensure_ready(args.players)
    if not ok:
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    print(f"[seed_sweep] editor ready: {why}")

    done = 0
    if results_path.exists():
        done = sum(1 for _ in results_path.open(encoding="utf-8"))
        print(f"[seed_sweep] resuming at iteration {done}")

    with results_path.open("a", encoding="utf-8") as fh:
        for i in range(done, args.n):
            t0 = time.time()
            print(f"\n[seed_sweep] ({i + 1}/{args.n})")
            try:
                after = editor.generate_and_save(SCENARIO_DIR)
            except Exception as e:
                print(f"[seed_sweep] iteration {i} FAILED: {e}")
                continue

            dest = outdir / f"{args.name}_seed_{i:03d}.aoe2scenario"
            shutil.copyfile(after, dest)

            mask = scx_read.read_land_mask(dest)
            tcs = scx_read.read_town_centers(dest)
            resources = scx_read.read_resources(dest)
            per_player, unclaimed = resource_ownership(mask, tcs, resources)

            record = {"seed_index": i, "n_tcs": len(tcs),
                      "per_player": {str(k): v for k, v in per_player.items()},
                      "unclaimed": unclaimed}
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            dt = time.time() - t0
            print(f"[seed_sweep] {i}: {len(tcs)} TCs in {dt:.0f}s, unclaimed={unclaimed}")
            # Don't keep the raw .aoe2scenario around for a 100-run sweep -
            # only the extracted stats matter and these are ~200KB each.
            dest.unlink()

    print(f"[seed_sweep] done -> {results_path}")


if __name__ == "__main__":
    main()
