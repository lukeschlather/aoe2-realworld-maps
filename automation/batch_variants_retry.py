"""LEGACY ORIENTATION: the ``--rotate`` values below are grid-space and
predate the 2026-08-16 switch to screen-space ``--north``. ``rwmaps`` no
longer accepts ``--rotate``, so these will fail until converted -
``north = rotate - 45`` (projection.north_from_legacy_rotate). Left
unconverted because this script targets regions that no longer ship.

Rerun just the variants that failed in the first batch_variants.py pass
because gen_loop.py used to require --region (fixed now)."""

import sys
import time
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
    ("caribbean_tight_v2", ["--center=-75.0,18.0", "--span-km", "1400",
                             "--min-island-tiles", str(MIN_ISLAND)]),
    ("philippines_tight_v2", ["--center=122.0,12.0", "--span-km", "1000",
                               "--min-island-tiles", str(MIN_ISLAND)]),
    ("indonesia_tight_v2", ["--center=117.0,-2.0", "--span-km", "2200",
                             "--min-island-tiles", str(MIN_ISLAND)]),
    ("japan_rot35_v2", ["--region", "japan", "--rotate", "35",
                         "--min-island-tiles", str(MIN_ISLAND)]),
]


def main():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / "out" / f"variants-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    log = RunLog(outdir, run_id=f"retry-{stamp}")
    log.attach_editor(editor)
    gen_loop.LOG = log
    log.event("plan", f"{len(VARIANTS)} variants (retry)",
              variants=[n for n, _ in VARIANTS])

    captured = 0
    for i, (name, extra) in enumerate(VARIANTS, start=1):
        log.event("variant", f"variant {i}/{len(VARIANTS)} {name}",
                  name=name, extra_args=extra)
        sys.argv = ["gen_loop.py", name, "--size", "220", "--players", "8",
                    "--outdir", str(outdir), *extra]
        try:
            dest = gen_loop.main()
        except BaseException as e:
            import traceback
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
        log.ok("variant_done", f"  {name}: {len(tcs)} TCs", name=name,
               n_tcs=len(tcs), preview=str(png), file=str(dest))

    log.close(f"done {captured}/{len(VARIANTS)} captured", captured=captured,
              expected=len(VARIANTS), outdir=str(outdir))
    print(f"logs: {log.terse_path}  {log.json_path}")


if __name__ == "__main__":
    main()
