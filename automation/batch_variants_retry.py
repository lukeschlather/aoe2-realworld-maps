"""Rerun just the variants that failed in the first batch_variants.py pass
because gen_loop.py used to require --region (fixed now)."""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import gen_loop  # noqa: E402
from rwmaps import real_preview, scx_read  # noqa: E402

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

    for i, (name, extra) in enumerate(VARIANTS, start=1):
        print(f"\n[retry] ({i}/{len(VARIANTS)}) {name} {extra}")
        sys.argv = ["gen_loop.py", name, "--size", "220", "--players", "8",
                    "--outdir", str(outdir), *extra]
        try:
            dest = gen_loop.main()
        except BaseException:
            import traceback
            print(f"[retry] {name} FAILED during generation:")
            traceback.print_exc()
            continue
        mask = scx_read.read_land_mask(dest)
        tcs = scx_read.read_town_centers(dest)
        png = real_preview.save_real_render(
            mask, tcs, dest.with_suffix(".png"),
            title=f"{name}  {mask.shape[1]}x{mask.shape[0]}  {len(tcs)} TCs (real engine)",
        )
        print(f"[retry] {name}: {len(tcs)} TCs -> {png}")

    print(f"[retry] done. Output: {outdir}")


if __name__ == "__main__":
    main()
