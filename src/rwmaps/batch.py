"""``rwmaps-batch`` — generate candidate maps, one version per lobby map size.

Land areas are absolute tile counts, so a script is only correct at the grid
size it was generated for. Rather than trying to make one script cover every
size, we emit a separate map per size and put the size in the name, so the
lobby choice is unambiguous: ``rw_great_lakes_240`` goes with ``Huge [240]``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from . import install as install_mod
from .cli import LOBBY_SIZES, TEAM_SIZES, generate, timestamped_dir

#: (name, region, projection, rotation).
#:
#: ``rotate 45`` cancels the engine's isometric rotation, so the landmass reads
#: north-up on screen instead of tilted - much easier to recognise in play.
SPREAD: list[tuple[str, str, str, float]] = [
    ("RW Great Lakes", "greatlakes", "laea", 0),
    ("RW Great Lakes NorthUp", "greatlakes", "laea", 45),
    ("RW Black Sea", "blacksea", "laea", 0),
    ("RW Black Sea NorthUp", "blacksea", "laea", 45),
    ("RW Chesapeake", "chesapeake", "laea", 0),
    ("RW Anatolia", "anatolia", "laea", 0),
    ("RW Iberia", "iberia", "laea", 0),
    ("RW Italy", "italy", "laea", 0),
]

#: Players to place, by grid size. Bigger maps get more starts.
PLAYERS_FOR_SIZE = {200: 6, 220: 8, 240: 8, 480: 8}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rwmaps-batch",
        description="Generate candidate real-world maps, one per lobby map size.",
    )
    p.add_argument("--sizes", type=int, nargs="+", default=[220, 240],
                   choices=TEAM_SIZES,
                   help="grid sizes to generate (default 220 240)")
    p.add_argument("--teams", type=int, default=2)
    p.add_argument("--lands", type=int, help="override the size-scaled default")
    p.add_argument("--outdir", type=Path, default=Path("out"))
    p.add_argument("--install", action="store_true")
    p.add_argument("--mod-name", default=install_mod.MOD_NAME)
    p.add_argument("--only", help="substring filter on the map name")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    outdir = timestamped_dir(args.outdir)
    rows = []
    for size in args.sizes:
        players = PLAYERS_FOR_SIZE.get(size, 8)
        for name, region, proj, rotate in SPREAD:
            if args.only and args.only.lower() not in name.lower():
                continue
            opts = SimpleNamespace(
                name=name, region=region, center=None, span_km=None,
                proj=proj, rotate=rotate, size=size, players=players,
                teams=args.teams, lands=args.lands, biome="temperate",
                resolution="10m", no_elevation=False, ai_map_type=None,
                outdir=outdir, install=args.install, mod_name=args.mod_name,
                no_preview=False, quiet=True,
            )
            try:
                rows.append(generate(opts))
                print(f"  ok  {opts.name} @{size}")
            except Exception as exc:  # noqa: BLE001 - keep going
                print(f"  FAIL {opts.name} @{size}: {type(exc).__name__}: {exc}")

    if not rows:
        return 1

    head = (f"\n{'map':<30}{'grid':>6}{'p':>3}{'land%':>7}{'IoU':>6}"
            f"{'minsep':>8}{'ally':>6}{'enemy':>7}{'ai':>15}  verdict")
    print(head)
    print("-" * (len(head) - 1))
    for r in sorted(rows, key=lambda r: (r["size"], -r["land_pct"])):
        print(f"{r['stem']:<30}{r['size']:>6}{r['players']:>3}{r['land_pct']:>7.1f}"
              f"{r['iou']:>6.2f}{r['separation']:>8.0f}{r['ally_dist']:>6.0f}"
              f"{r['enemy_dist']:>7.0f}{r['ai']:>15}  {r['verdict']}")

    print("\nlobby Map Size must match the number in the map name:")
    for size in sorted({r["size"] for r in rows}):
        print(f"  _{size}  ->  {LOBBY_SIZES[size]}")
    print(f"\noutput: {outdir}")
    if args.install:
        print(f"installed to {rows[0]['installed'].parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
