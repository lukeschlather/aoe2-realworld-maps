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

import gen_loop  # noqa: E402
from rwmaps import real_preview, scx_read  # noqa: E402
from rwmaps.cli import REGIONS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--players", type=int, default=8)
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / "out" / f"batch-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, region in enumerate(REGIONS, start=1):
        print(f"\n[batch] ({i}/{len(REGIONS)}) {region}")
        sys.argv = [
            "gen_loop.py", region,
            "--region", region,
            "--size", str(args.size),
            "--players", str(args.players),
            "--outdir", str(outdir),
        ]
        try:
            dest = gen_loop.main()
        except Exception:
            print(f"[batch] {region} FAILED during generation:")
            traceback.print_exc()
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
            print(f"[batch] {region}: {len(tcs)} TCs -> {png}")
            results.append((region, png, f"{len(tcs)} TCs"))
        except Exception:
            print(f"[batch] {region} FAILED during render/analysis:")
            traceback.print_exc()
            results.append((region, None, "render failed"))

    print("\n[batch] summary:")
    for region, png, note in results:
        print(f"  {region:20s} {note:20s} {png or ''}")
    ok = sum(1 for _, png, _ in results if png)
    print(f"[batch] {ok}/{len(results)} succeeded. Output: {outdir}")
    return outdir


if __name__ == "__main__":
    main()
