"""Capture N real engine generations of the SAME script, keeping every raw
.aoe2scenario file (unlike seed_sweep.py, which extracts stats and deletes
the file to stay small at ~1000-generation scale).

Regenerates the .rms once, swaps it into the slot once, then loops only the
Generate -> Save click sequence - the expensive part (script regen, ~8s
with the geodata WKB cache warm) never repeats. Use this instead of calling
gen_loop.py in a shell loop, which pays that cost every single iteration.

Usage:
    uv run python automation/batch_capture.py seattle_probe --center=-122.6,47.8 \\
        --span-km 130 --size 220 --players 8 --n 8 --outdir out/nav_probe2 \\
        -- --min-water-width 9 --min-land-width 6
"""

from __future__ import annotations

import argparse
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

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--region")
    ap.add_argument("--center")
    ap.add_argument("--span-km", type=float)
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--outdir", required=True)
    args, extra = ap.parse_known_args()

    outdir = REPO / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    rms_dir = outdir / "script"

    gen_cmd = ["uv", "run", "rwmaps", args.name,
               *(["--region", args.region] if args.region else []),
               *([f"--center={args.center}"] if args.center else []),
               *(["--span-km", str(args.span_km)] if args.span_km is not None else []),
               "--size", str(args.size), "--players", str(args.players),
               "--outdir", str(rms_dir), "--no-preview", *extra]
    print(f"[batch_capture] regenerating script once: {' '.join(gen_cmd)}")
    t0 = time.time()
    subprocess.run(gen_cmd, cwd=REPO, check=True)
    print(f"[batch_capture] regen took {time.time()-t0:.1f}s")

    rms_files = list(rms_dir.rglob("*.rms"))
    if len(rms_files) != 1:
        raise RuntimeError(f"expected exactly one .rms in {rms_dir}, found {rms_files}")
    shutil.copyfile(rms_files[0], SLOT_PATH)
    print(f"[batch_capture] slot script: {rms_files[0].name}")

    ok, why = editor.ensure_ready(args.players)
    if not ok:
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    print(f"[batch_capture] editor ready: {why}")

    captured = []
    for i in range(args.n):
        t0 = time.time()
        print(f"[batch_capture] ({i + 1}/{args.n}) ...")
        try:
            after = editor.generate_and_save(SCENARIO_DIR)
        except Exception as e:
            print(f"[batch_capture] iteration {i} FAILED: {e}")
            continue
        dest = outdir / f"{args.name}_{i:03d}.aoe2scenario"
        shutil.copyfile(after, dest)
        captured.append(dest)
        print(f"[batch_capture] {i}: captured {dest.name} in {time.time()-t0:.1f}s")

    print(f"[batch_capture] done: {len(captured)}/{args.n} captured in {outdir}")


if __name__ == "__main__":
    main()
