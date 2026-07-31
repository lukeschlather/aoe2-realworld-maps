"""Stage 1 of the new-window workflow: a fast, Python-only geographic screen.

Generalizes ``rwmaps-batch``'s tabulation from the named ``REGIONS`` dict to
arbitrary ad hoc ``--center``/``--span-km`` windows, so a batch of candidate
projections can be scored (land%, coastline IoU, TC separation, ai type,
fairness verdict) before the game is ever involved. Nothing here renders in
the engine - see ``gen_loop.py`` / ``seed_sweep.py`` for the real-engine
stages that follow once a candidate looks good on paper.

Edit CANDIDATES below and run:

    uv run python automation/scout_window.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rwmaps.cli import generate, timestamped_dir  # noqa: E402

#: (name, lon, lat, span_km, rotate). Starting points - edit freely.
CANDIDATES: list[tuple[str, float, float, float, float]] = [
    ("Salish Sea Wide", -122.65, 47.95, 420, 0),
    ("Victoria Recenter", -122.9, 48.15, 260, 0),
    ("Victoria Recenter Tighter", -122.85, 48.05, 200, 0),
    ("West Shift", -122.85, 47.75, 130, 0),
    ("West Shift Zoomed", -122.8, 47.75, 95, 0),
]

PLAYERS = 8
SIZE = 240


def main() -> int:
    outdir = timestamped_dir(REPO / "out" / "scout")
    rows = []
    for name, lon, lat, span_km, rotate in CANDIDATES:
        opts = SimpleNamespace(
            name=name, region=None, center=f"{lon},{lat}", span_km=span_km,
            proj="laea", rotate=rotate, size=SIZE, players=PLAYERS,
            teams=2, lands=None, biome="temperate", resolution="10m",
            no_elevation=False, ai_map_type=None, clumping_factor=8, min_island_tiles=0,
            min_water_width=0, min_land_width=0, overlap=1.0, max_radius=12.0,
            outdir=outdir, install=False, mod_name="Real World Projections",
            no_preview=False, quiet=True,
        )
        try:
            rows.append(generate(opts))
            print(f"  ok  {name}")
        except Exception as exc:  # noqa: BLE001 - keep going
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")

    if not rows:
        return 1

    head = (f"\n{'candidate':<28}{'land%':>7}{'IoU':>6}{'minsep':>8}"
            f"{'ally':>6}{'enemy':>7}{'ai':>15}  verdict")
    print(head)
    print("-" * (len(head) - 1))
    for r in sorted(rows, key=lambda r: -r["separation"]):
        print(f"{r['name']:<28}{r['land_pct']:>7.1f}{r['iou']:>6.2f}"
              f"{r['separation']:>8.0f}{r['ally_dist']:>6.0f}{r['enemy_dist']:>7.0f}"
              f"{r['ai']:>15}  {r['verdict']}")
    print(f"\npreviews: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
