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
from runlog import RunLog  # noqa: E402

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

    log = RunLog(outdir, run_id=args.name)
    log.attach_editor(editor)
    log.event("plan", f"{args.n} samples of {args.name}", name=args.name,
              n=args.n, size=args.size, players=args.players,
              region=args.region, center=args.center, span_km=args.span_km,
              extra_args=extra)

    gen_cmd = ["uv", "run", "rwmaps", args.name,
               *(["--region", args.region] if args.region else []),
               *([f"--center={args.center}"] if args.center else []),
               *(["--span-km", str(args.span_km)] if args.span_km is not None else []),
               "--size", str(args.size), "--players", str(args.players),
               "--outdir", str(rms_dir), "--no-preview", *extra]
    # Once, not per sample - that is the whole point of this harness over
    # calling gen_loop in a shell loop. Captured so rwmaps' own narration
    # lands in the JSON log rather than on the terminal only.
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

    captured = []
    for i in range(args.n):
        try:
            cap = editor.generate_and_save(SCENARIO_DIR)
        except Exception as e:
            log.fail("capture", f"  sample {i}: FAILED", sample_index=i,
                     error=str(e))
            continue
        dest = outdir / f"{args.name}_{i:03d}.aoe2scenario"
        shutil.copyfile(cap.path, dest)
        captured.append(dest)
        log.ok("capture", f"  sample {i}: captured {dest.name}",
               sample_index=i, file=str(dest),
               generate_s=round(cap.generate_s, 3),
               save_s=round(cap.save_s, 3))

    log.close(f"done {len(captured)}/{args.n} captured",
              captured=len(captured), expected=args.n, outdir=str(outdir))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
