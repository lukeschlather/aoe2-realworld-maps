"""End-to-end real-render loop: generate an .rms, swap it into the fixed
editor slot, generate and save in the editor, then rename the resulting
.aoe2scenario to a unique name.

The editor no longer has to be set up by hand: ``editor.ensure_ready()``
walks it there from whatever state it is in, and every click it makes is
verified against the screen first (see EDITOR_AUTOMATION.md).

Logs both ways (see ``runlog.py``): a terse ``log.txt`` and a queryable
``events.jsonl`` in the run's output directory. A caller looping over this
function - ``batch_240.py`` and friends do - should set ``gen_loop.LOG`` to
its own ``RunLog`` first, so a batch of 100 captures is one record instead
of 100 one-line ones.

Usage:
    uv run python automation/gen_loop.py <name> --region <region> [rwmaps args...]
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import editor  # noqa: E402
from rwmaps import install as install_mod  # noqa: E402
from runlog import RunLog  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"

#: A log owned by a caller that loops over ``main()``. When None, ``main()``
#: opens its own for the one capture it takes and closes it on the way out.
LOG: RunLog | None = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--region")  # or pass --center/--span-km through `extra`
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--outdir", default="out")
    args, extra = ap.parse_known_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / args.outdir / f"loop-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    log = LOG or RunLog(outdir, run_id=f"loop-{stamp}")
    owned = LOG is None
    if owned:
        log.attach_editor(editor)
    log.event("capture_start", f"{args.name}", name=args.name,
              region=args.region, size=args.size, players=args.players,
              extra_args=extra, outdir=str(outdir))
    try:
        gen_cmd = [
            "uv", "run", "rwmaps", args.name,
            *(["--region", args.region] if args.region else []),
            "--size", str(args.size),
            "--players", str(args.players), "--outdir", str(outdir), "--no-preview",
            *extra,
        ]
        # Captured, not inherited: rwmaps narrates its own generation
        # ("land 30.5%  coastline IoU 0.96 ...") and inheriting stdout puts
        # those lines on the terminal and in no log at all, which is exactly
        # the leak the two-log split exists to close. They are worth keeping,
        # so they go to the JSON log.
        # One event, not a timer plus a detail event: both would carry this
        # duration and a query summing duration_s by kind would count the
        # regen twice.
        t_regen = time.time()
        r = subprocess.run(gen_cmd, cwd=REPO, capture_output=True, text=True)
        regen_s = time.time() - t_regen
        log.event("regen", None, name=args.name, command=" ".join(gen_cmd),
                  returncode=r.returncode, stdout=r.stdout[-4000:],
                  ok=r.returncode == 0, duration_s=round(regen_s, 3))
        if r.returncode != 0:
            log.fail("regen_failed", f"  {args.name}: REGEN FAILED",
                     name=args.name, stderr=r.stderr[-2000:],
                     duration_s=round(regen_s, 3))
            raise RuntimeError(f"rwmaps failed for {args.name} - its full "
                               f"stderr is in {log.json_path} under kind "
                               f"regen_failed")

        rms_files = list(outdir.rglob("*.rms"))
        if len(rms_files) != 1:
            log.fail("slot_swap", f"  {args.name}: expected 1 .rms, found "
                     f"{len(rms_files)}", name=args.name,
                     found=[str(p) for p in rms_files])
            raise RuntimeError(
                f"expected exactly one .rms in {outdir}, found {rms_files}")
        rms_path = rms_files[0]
        shutil.copyfile(rms_path, SLOT_PATH)
        log.event("slot_swap", None, name=args.name, rms=str(rms_path),
                  slot=str(SLOT_PATH))

        # Check the editor is on our slot with the mods on before spending a
        # generation on it - with the mods off the editor silently generates a
        # stock map under this run's name instead.
        with log.timer("preflight", name=args.name) as t:
            ok, why = editor.ensure_ready(args.players)
        if not ok:
            log.fail("preflight_failed", f"  {args.name}: editor unusable: "
                     f"{why}", name=args.name, why=why,
                     duration_s=round(t.seconds, 3))
            raise RuntimeError(f"the editor is not usable: {why}")

        cap = editor.generate_and_save(SCENARIO_DIR)
        dest = outdir / f"{rms_path.stem}.aoe2scenario"
        shutil.copyfile(cap.path, dest)
        log.ok("capture", f"  {args.name}: captured {dest.name}",
               name=args.name, file=str(dest),
               generate_s=round(cap.generate_s, 3),
               save_s=round(cap.save_s, 3))
        return dest
    except Exception as e:
        # Logged here rather than left to the caller: four batch wrappers
        # catch this and each would have to know how to record it.
        log.fail("capture", f"  {args.name}: FAILED", name=args.name,
                 error=str(e), error_type=type(e).__name__)
        raise
    finally:
        if owned:
            log.close(f"done {args.name}")
            print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
