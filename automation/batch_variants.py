"""LEGACY ORIENTATION: the ``--rotate`` values below are grid-space and
predate the 2026-08-16 switch to screen-space ``--north``. ``rwmaps`` no
longer accepts ``--rotate``, so these will fail until converted -
``north = rotate - 45`` (projection.north_from_legacy_rotate). Left
unconverted because this script targets regions that no longer ship.

Run the real-render loop for a curated set of experiment variants (new
choose_starts algorithm, small-island filtering, tighter viewports, rotation)
against the regions that clustered badly in the first full batch.

Requires the one-time manual editor setup already done (see
RENDER_PIPELINE.md) - same prerequisite as batch_all_regions.py.

Usage:
    uv run python automation/batch_variants.py
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

MIN_ISLAND = 20  # ~4.5x4.5 tiles - kills single/few-tile speckle, keeps

# name, extra rwmaps CLI args (region/center/span/rotate/min-island-tiles)
VARIANTS = [
    ("japan_v2", ["--region", "japan", "--min-island-tiles", str(MIN_ISLAND)]),
    ("newzealand_v2", ["--region", "newzealand", "--min-island-tiles", str(MIN_ISLAND)]),
    ("philippines_v2", ["--region", "philippines", "--min-island-tiles", str(MIN_ISLAND)]),
    ("indonesia_v2", ["--region", "indonesia", "--min-island-tiles", str(MIN_ISLAND)]),
    ("caribbean_v2", ["--region", "caribbean", "--min-island-tiles", str(MIN_ISLAND)]),
    ("denmark_v2", ["--region", "denmark", "--min-island-tiles", str(MIN_ISLAND)]),
    ("britain_wide_v2", ["--region", "britain-wide", "--min-island-tiles", str(MIN_ISLAND)]),
    # Tighter viewports on the worst archipelago spreads - same centre, smaller
    # span. Note: "--center" (space-separated) + a negative longitude value
    # trips argparse's "is this actually another flag?" heuristic since
    # "-75.0,18.0" isn't a pure negative number (has a comma) - the "--opt=val"
    # form sidesteps that ambiguity entirely, so it's used here regardless of
    # sign.
    ("caribbean_tight_v2", ["--center=-75.0,18.0", "--span-km", "1400",
                             "--min-island-tiles", str(MIN_ISLAND)]),
    ("philippines_tight_v2", ["--center=122.0,12.0", "--span-km", "1000",
                               "--min-island-tiles", str(MIN_ISLAND)]),
    ("indonesia_tight_v2", ["--center=117.0,-2.0", "--span-km", "2200",
                             "--min-island-tiles", str(MIN_ISLAND)]),
    # Rotation experiment: Japan's arc runs diagonally: try aligning it more
    # with the square grid to see if more of the arc fits without wasted ocean.
    ("japan_rot35_v2", ["--region", "japan", "--rotate", "35",
                         "--min-island-tiles", str(MIN_ISLAND)]),
]


def main():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / "out" / f"variants-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    log = RunLog(outdir, run_id=f"variants-{stamp}")
    log.attach_editor(editor)
    gen_loop.LOG = log
    log.event("plan", f"{len(VARIANTS)} variants",
              variants=[n for n, _ in VARIANTS])

    results = []
    for i, (name, extra) in enumerate(VARIANTS, start=1):
        log.event("variant", f"variant {i}/{len(VARIANTS)} {name}",
                  name=name, extra_args=extra)
        sys.argv = ["gen_loop.py", name, "--size", "220", "--players", "8",
                    "--outdir", str(outdir), *extra]
        try:
            dest = gen_loop.main()
        except BaseException as e:
            # BaseException, not Exception - argparse errors inside gen_loop's
            # own parser raise SystemExit, which isn't an Exception subclass
            # and would otherwise silently kill the rest of the batch.
            log.event("variant_failed", None, name=name, phase="generation",
                      error=str(e), traceback=traceback.format_exc())
            results.append((name, None, "generation failed"))
            continue

        try:
            mask = scx_read.read_land_mask(dest)
            tcs = scx_read.read_town_centers(dest)
            png = real_preview.save_real_render(
                mask, tcs, dest.with_suffix(".png"),
                title=f"{name}  {mask.shape[1]}x{mask.shape[0]}  {len(tcs)} TCs (real engine)",
            )
            log.ok("variant_done", f"  {name}: {len(tcs)} TCs", name=name,
                   n_tcs=len(tcs), preview=str(png), file=str(dest))
            results.append((name, png, f"{len(tcs)} TCs"))
        except Exception as e:
            log.fail("variant_done", f"  {name}: render/analysis FAILED",
                     name=name, phase="render", error=str(e),
                     traceback=traceback.format_exc())
            results.append((name, None, "render failed"))

    ok = sum(1 for _, png, _ in results if png)
    log.close(f"done {ok}/{len(results)} succeeded", captured=ok,
              expected=len(results), outdir=str(outdir),
              results=[{"name": n, "note": note} for n, _p, note in results])
    print(f"logs: {log.terse_path}  {log.json_path}")
    return outdir


if __name__ == "__main__":
    main()
