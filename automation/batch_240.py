"""LEGACY ORIENTATION: the ``--rotate`` values below are grid-space and
predate the 2026-08-16 switch to screen-space ``--north``. ``rwmaps`` no
longer accepts ``--rotate``, so these will fail until converted -
``north = rotate - 45`` (projection.north_from_legacy_rotate). Left
unconverted because this script targets regions that no longer ship.

Regenerate a handful of specific maps at size 240 (requires the editor's
Map Size dropdown already switched to Huge [240] by hand - see
RENDER_PIPELINE.md; that list is one of the crash-prone ones automation must
never touch).

Usage:
    uv run python automation/batch_240.py
"""

import sys
import time
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import editor  # noqa: E402
import gen_loop  # noqa: E402
from rwmaps import real_preview, scx_read  # noqa: E402
from runlog import RunLog  # noqa: E402

MIN_ISLAND = 20

VARIANTS = [
    ("italy_240", ["--region", "italy"]),
    ("denmark_v2_240", ["--region", "denmark", "--min-island-tiles", str(MIN_ISLAND)]),
    ("japan_rot35_v2_240", ["--region", "japan", "--rotate", "35",
                            "--min-island-tiles", str(MIN_ISLAND)]),
]


def main():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / "out" / f"size240-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    # One log for the whole batch, handed to gen_loop, rather than gen_loop
    # opening one per capture and leaving three separate records of one run.
    log = RunLog(outdir, run_id=f"size240-{stamp}")
    log.attach_editor(editor)
    gen_loop.LOG = log
    log.event("plan", f"{len(VARIANTS)} variants at 240",
              variants=[n for n, _ in VARIANTS])

    captured = 0
    for i, (name, extra) in enumerate(VARIANTS, start=1):
        log.event("variant", f"variant {i}/{len(VARIANTS)} {name}",
                  name=name, extra_args=extra)
        sys.argv = ["gen_loop.py", name, "--size", "240", "--players", "8",
                    "--outdir", str(outdir), *extra]
        try:
            dest = gen_loop.main()
        except BaseException as e:
            # gen_loop already logged the failure with its cause; the
            # traceback goes to the JSON log, not the terse one.
            log.event("variant_failed", None, name=name, error=str(e),
                      traceback=traceback.format_exc())
            continue
        mask = scx_read.read_land_mask(dest)
        tcs = scx_read.read_town_centers(dest)
        png = real_preview.save_real_render(
            mask, tcs, dest.with_suffix(".png"),
            title=f"{name}  {mask.shape[1]}x{mask.shape[0]}  {len(tcs)} TCs (real engine)",
        )
        captured += 1
        log.ok("variant_done", f"  {name}: {len(tcs)} TCs",
               name=name, n_tcs=len(tcs), preview=str(png), file=str(dest))

    log.close(f"done {captured}/{len(VARIANTS)} captured", captured=captured,
              expected=len(VARIANTS), outdir=str(outdir))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
