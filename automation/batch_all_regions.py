"""Run the real-render loop (see gen_loop.py) for every region in
rwmaps.cli.REGIONS, then render a coastline-outline + Town-Centre PNG for
each from the real engine output (not the Python approximation).

Requires the one-time manual editor setup in RENDER_PIPELINE.md to already
be done (Map panel open, Random Map checked, AA_rw_placeholder_tester
selected, size/players set, Generate Map clicked once by hand).

Usage:
    uv run python automation/batch_all_regions.py --size 220 --players 8
"""

import argparse
import sys
import time
import traceback
from pathlib import Path

# AoE2ScenarioParser prints a unicode progress glyph while reading a
# scenario; Windows' default cp1252 stdout can't encode it (crashes reading
# every file) unless stdout is UTF-8 - matters most when stdout is
# redirected to a file, since that's what drops the console's UTF-8 code page.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import editor  # noqa: E402
import gen_loop  # noqa: E402
from rwmaps import real_preview, scx_read  # noqa: E402
from rwmaps.cli import REGIONS  # noqa: E402
from runlog import RunLog  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--players", type=int, default=8)
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / "out" / f"batch-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    log = RunLog(outdir, run_id=f"batch-{stamp}")
    log.attach_editor(editor)
    gen_loop.LOG = log
    log.event("plan", f"{len(REGIONS)} regions at {args.size}",
              regions=list(REGIONS), size=args.size, players=args.players)

    results = []
    for i, region in enumerate(REGIONS, start=1):
        log.event("region_start", f"region {i}/{len(REGIONS)} {region}",
                  region=region)
        sys.argv = [
            "gen_loop.py", region,
            "--region", region,
            "--size", str(args.size),
            "--players", str(args.players),
            "--outdir", str(outdir),
        ]
        try:
            dest = gen_loop.main()
        except Exception as e:
            log.event("region_failed", None, region=region, phase="generation",
                      error=str(e), traceback=traceback.format_exc())
            results.append((region, None, "generation failed"))
            continue

        try:
            mask = scx_read.read_land_mask(dest)
            tcs = scx_read.read_town_centers(dest)
            png = real_preview.save_real_render(
                mask, tcs, dest.with_suffix(".png"),
                title=f"{region}  {args.size}x{args.size}  {args.players}p  "
                      f"{len(tcs)} TCs (real engine render)",
            )
            log.ok("region_done", f"  {region}: {len(tcs)} TCs", region=region,
                   n_tcs=len(tcs), preview=str(png), file=str(dest))
            results.append((region, png, f"{len(tcs)} TCs"))
        except Exception as e:
            log.fail("region_done", f"  {region}: render/analysis FAILED",
                     region=region, phase="render", error=str(e),
                     traceback=traceback.format_exc())
            results.append((region, None, "render failed"))

    ok = sum(1 for _, png, _ in results if png)
    log.close(f"done {ok}/{len(results)} succeeded", captured=ok,
              expected=len(results), outdir=str(outdir),
              results=[{"region": r, "note": note} for r, _p, note in results])
    print(f"logs: {log.terse_path}  {log.json_path}")
    return outdir


if __name__ == "__main__":
    main()
