"""Render coastline-outline + Town-Centre PNGs for .aoe2scenario files that
were already captured by a batch_all_regions.py run (or any gen_loop.py run)
- no GUI automation, just re-runs the analysis/render step.

Exists because AoE2ScenarioParser prints a unicode progress glyph that
crashes under Windows' default cp1252 stdout encoding when stdout has been
redirected to a file; reconfiguring stdout to UTF-8 up front avoids it
without touching the library.

Usage:
    uv run python automation/render_existing.py out/batch-<stamp>
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import real_preview, scx_read  # noqa: E402


def main():
    root = Path(sys.argv[1])
    files = sorted(root.rglob("*.aoe2scenario"))
    print(f"[render_existing] found {len(files)} scenario files under {root}")

    results = []
    for f in files:
        try:
            mask = scx_read.read_land_mask(f)
            tcs = scx_read.read_town_centers(f)
            png = real_preview.save_real_render(
                mask, tcs, f.with_suffix(".png"),
                title=f"{f.stem}  {mask.shape[1]}x{mask.shape[0]}  "
                      f"{len(tcs)} TCs (real engine render)",
            )
            print(f"[render_existing] {f.stem}: {len(tcs)} TCs -> {png}")
            results.append((f.stem, png, f"{len(tcs)} TCs"))
        except Exception as e:
            print(f"[render_existing] {f.stem} FAILED: {e!r}")
            results.append((f.stem, None, "failed"))

    ok = sum(1 for _, png, _ in results if png)
    print(f"\n[render_existing] {ok}/{len(results)} succeeded.")
    return results


if __name__ == "__main__":
    main()
