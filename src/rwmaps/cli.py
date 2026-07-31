"""Generate a playable AoE2 DE random map around a real coastline."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import install as install_mod
from . import raster, rms, rms_land, terrain
from .analysis import (
    assign_teams,
    choose_ai_map_type,
    choose_starts,
    evaluate,
    team_separation,
)
from .preview import save_preview
from .projection import PROJECTIONS, MapWindow

#: (lon, lat, span_km). Starting points, not a closed list - use --center.
REGIONS: dict[str, tuple[float, float, float]] = {
    "britain": (-3.0, 54.5, 1300),
    "britain-tight": (-2.5, 53.5, 800),
    "britain-wide": (-4.0, 55.0, 2200),
    "ireland": (-8.0, 53.3, 700),
    "greatlakes": (-84.0, 44.5, 1600),
    "greatlakes-tight": (-83.0, 45.0, 1000),
    "italy": (13.0, 42.0, 1300),
    "italy-tight": (12.5, 43.0, 800),
    "greece": (23.5, 38.5, 900),
    "japan": (138.0, 37.0, 1600),
    "iberia": (-4.0, 40.0, 1400),
    "anatolia": (33.0, 39.0, 1500),
    "scandinavia": (16.0, 62.0, 2000),
    "denmark": (10.5, 56.0, 700),
    "caribbean": (-75.0, 18.0, 2600),
    "newzealand": (172.5, -41.5, 1500),
    "chesapeake": (-76.2, 38.5, 600),
    "blacksea": (34.0, 43.5, 1800),
    "indonesia": (117.0, -2.0, 4000),
    "philippines": (122.0, 12.0, 1800),
}


#: Grid size to generate for, by player count, and the lobby "Map Size" that
#: matches it. Land areas are absolute tile counts, so the script is tuned for
#: one size - ``land_position`` is percentage-based, so positions still land
#: correctly at other sizes, but the land/water ratio drifts.
#:
#: These run a step larger than the stock defaults for the same player count:
#: real coastlines waste a lot of the square on open water, so a 4-player-sized
#: map holding 8 players ends up unplayably cramped.
#: The only sizes the lobby offers, read off the Map Size dropdown.
LOBBY_SIZES: dict[int, str] = {
    120: "Tiny (2 player) [120]",
    144: "Small (3 player) [144]",
    168: "Medium (4 player) [168]",
    200: "Normal (6 player) [200]",
    220: "Large (8 player) [220]",
    240: "Huge [240]",
    480: "Ludicrous [480]",
}

PLAYER_SIZE: dict[int, int] = {2: 168, 3: 200, 4: 220, 5: 240, 6: 240, 7: 240, 8: 240}

#: Sizes worth generating for team play. Anything below 6 players is pointless
#: here, so 200 is the floor.
TEAM_SIZES: tuple[int, ...] = (200, 220, 240, 480)


def lands_for_size(size: int, base: int = 700, at: int = 240) -> int:
    """Discs needed to hold fidelity as the grid grows.

    Blob radius scales with the grid, so disc count has to scale with *area* to
    keep the same coastline detail. Capped, because the script is one
    ``create_land`` block per disc and 480x480 would otherwise be enormous.
    """
    return int(min(1600, max(250, round(base * (size / at) ** 2))))


def size_for_players(players: int) -> tuple[int, str]:
    """Grid size and the lobby label to pick for it."""
    for n in sorted(PLAYER_SIZE):
        if players <= n:
            size = PLAYER_SIZE[n]
            return size, LOBBY_SIZES[size]
    return 480, LOBBY_SIZES[480]


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def timestamped_dir(root: Path) -> Path:
    """``out/<UTC stamp>/`` so successive runs do not pile up on each other."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = root / stamp
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rwmaps",
        description="Generate AoE2 DE random maps whose coastline is a real place.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("projections: " + ", ".join(PROJECTIONS) + "\n"
                "  (or any PROJ string / EPSG code)\n"
                "regions:     " + ", ".join(REGIONS)),
    )
    p.add_argument("name", help="map name, e.g. 'Great Lakes'")

    loc = p.add_argument_group("location")
    loc.add_argument("--region", choices=sorted(REGIONS))
    loc.add_argument("--center", metavar="LON,LAT")
    loc.add_argument("--span-km", type=float)

    proj = p.add_argument_group("projection")
    proj.add_argument("--proj", default="laea",
                      help="projection name, PROJ string, or EPSG code")
    proj.add_argument("--rotate", type=float, default=0.0,
                      help="rotate the geography within the grid, degrees (0 = north up)")

    grid = p.add_argument_group("map")
    grid.add_argument("--size", type=int,
                      help="grid size in tiles (default: chosen from --players)")
    grid.add_argument("--players", type=int, default=8)
    grid.add_argument("--teams", type=int, default=2,
                      help="cluster starts into this many teams so allies spawn "
                           "together (0 disables)")
    grid.add_argument("--lands", type=int,
                      help="create_land blocks approximating the coastline "
                           "(default: scaled from --size)")
    grid.add_argument("--biome", default="temperate", choices=sorted(terrain.BIOMES))
    grid.add_argument("--resolution", default="10m", choices=["10m", "50m", "110m"])
    grid.add_argument("--no-elevation", action="store_true")
    grid.add_argument("--ai-map-type", help="override the auto-detected ai_info_map_type")
    grid.add_argument("--clumping-factor", type=int, default=8,
                      help="create_land clumping_factor - higher grows a more "
                           "solid/rounder blob in-engine, lower spreads out "
                           "raggedly (default 8, matches prior behavior)")
    grid.add_argument("--overlap", type=float, default=1.0,
                      help="disc-cover clearing overlap (default 1.0 = no shrink). "
                           "Lower values shrink the clearing radius so discs overlap "
                           "more, tightening fit at the cost of eroding small real "
                           "features - was reverted to 1.0 project-wide for that "
                           "reason, but may be safe again once narrow features are "
                           "deliberately consolidated away via --min-water-width")
    grid.add_argument("--max-radius", type=float, default=12.0,
                      help="largest disc radius used by the greedy disc-cover "
                           "(default 12 tiles) - smaller values hug narrow "
                           "corridors/indentations more tightly at the cost of "
                           "needing more discs for the same budget")
    grid.add_argument("--min-island-tiles", type=int, default=0,
                      help="drop connected land blobs smaller than this many tiles "
                           "(e.g. 16 for a 4x4-tile floor) - matches the liberty the "
                           "shipped real-world maps take with speckle islands")
    grid.add_argument("--min-water-width", type=int, default=0,
                      help="fill water channels narrower than this many tiles with "
                           "land (morphological closing) - deliberately consolidates "
                           "straits/inlets too narrow to render reliably instead of "
                           "leaving their fate to per-generation RNG luck")
    grid.add_argument("--min-land-width", type=int, default=0,
                      help="erase land bridges/spits narrower than this many tiles "
                           "(morphological opening) - guards against a stray sliver "
                           "of land randomly cutting a wide strait in two")

    out = p.add_argument_group("output")
    out.add_argument("--outdir", type=Path, default=Path("out"))
    out.add_argument("--install", action="store_true",
                     help="copy into the AoE2 DE local mod folder")
    out.add_argument("--mod-name", default=install_mod.MOD_NAME)
    out.add_argument("--no-preview", action="store_true")
    out.add_argument("--quiet", action="store_true")
    return p


def generate(args) -> dict:
    """Build one map. Returns a summary dict so batch callers can tabulate."""
    if args.region:
        lon, lat, span = REGIONS[args.region]
    else:
        if not args.center or args.span_km is None:
            raise SystemExit("error: give --region, or both --center LON,LAT and --span-km")
        lon = lat = span = None
    if args.center:
        lon, lat = (float(v) for v in args.center.split(","))
    if args.span_km is not None:
        span = args.span_km

    size, lobby_size = size_for_players(args.players)
    if args.size:
        size = args.size
        lobby_size = LOBBY_SIZES.get(size, f"NOT SELECTABLE IN THE LOBBY ({size})")

    window = MapWindow.from_center(args.proj, lon, lat, span, size, args.rotate)
    result = raster.rasterize(
        window, terrain.BIOMES[args.biome], resolution=args.resolution,
        min_island_tiles=args.min_island_tiles,
    )
    mask = result.land_mask
    if args.min_water_width or args.min_land_width:
        mask = raster.simplify_features(
            mask, min_water_width=args.min_water_width, min_land_width=args.min_land_width,
        )
        result.land_mask = mask

    # Scale the working radius with the map: on a 255 grid a 20-tile radius is
    # a much smaller share of the map than it is on a 168.
    radius = max(16, round(20 * size / 220))
    starts = choose_starts(mask, args.players, radius=radius)
    if len(starts) < args.players:
        print(f"[rwmaps] warning: only placed {len(starts)}/{args.players} starts",
              file=sys.stderr)
    if args.teams >= 2:
        starts = assign_teams(starts, args.teams)
    ally_d, enemy_d = team_separation(starts, args.teams) if args.teams >= 2 else (
        float("nan"), float("nan"))
    report = evaluate(mask, starts, radius=radius) if starts else None
    ai_type = args.ai_map_type or choose_ai_map_type(mask, starts)

    lands = args.lands or lands_for_size(size)
    discs = rms_land.cover_mask(mask, lands, max_radius=args.max_radius, overlap=args.overlap)
    approx = rms_land.rasterize_discs(discs, size)
    fidelity = rms_land.iou(approx, mask)

    opts = rms.BIOME_RMS[args.biome]
    if args.no_elevation:
        opts = rms.RmsOptions(**{**opts.__dict__, "elevation": False})

    land_section = rms_land.build_land_generation(
        discs, size, starts,
        target_tiles=int(mask.sum()), terrain_type=opts.land,
        clumping_factor=args.clumping_factor,
    )
    script = rms.build_rms(args.name, args.proj, size, land_section, opts, ai_type)

    # The grid size is baked into the land areas, so it belongs in the name -
    # picking the wrong lobby size is the one way to get a broken map.
    stem = f"{_slug(args.name)}_{size}"
    args.outdir.mkdir(parents=True, exist_ok=True)
    rms_path = rms.write_rms(args.outdir / f"{stem}.rms", script)
    png = None
    if not args.no_preview:
        png = save_preview(
            mask, approx, starts, args.outdir / f"{stem}.png",
            title=(f"{args.name}  {args.proj}"
                   f"{f' rot{args.rotate:g}' if args.rotate else ''}  "
                   f"{size}x{size}  {args.players}p  land {100*result.land_fraction:.0f}%  "
                   f"IoU {fidelity:.2f}  ai {ai_type}"),
        )

    installed = None
    if args.install:
        scripts = install_mod.ensure_mod(args.mod_name)
        installed = scripts / rms_path.name
        installed.write_bytes(rms_path.read_bytes())

    summary = {
        "stem": stem,
        "name": args.name,
        "proj": args.proj,
        "rotate": args.rotate,
        "size": size,
        "lobby_size": lobby_size,
        "players": args.players,
        "land_pct": 100 * result.land_fraction,
        "iou": fidelity,
        "lands": len(discs),
        "starts": len(starts),
        "ai": ai_type,
        "verdict": report.verdict if report else "n/a",
        "spread": report.land_within_spread if report else float("nan"),
        "separation": report.min_start_separation if report else float("nan"),
        "ally_dist": ally_d,
        "enemy_dist": enemy_d,
        "rms": rms_path,
        "png": png,
        "installed": installed,
    }

    if not args.quiet:
        print(f"[rwmaps] {args.name}: {args.proj}"
              f"{f' rot{args.rotate:g}' if args.rotate else ''}, "
              f"{size}x{size}, {window.km_per_tile:.1f} km/tile")
        print(f"[rwmaps] land {summary['land_pct']:.1f}%  coastline IoU {fidelity:.2f}  "
              f"ai_info_map_type {ai_type}")
        if report:
            print(f"[rwmaps] {len(starts)} starts, min separation "
                  f"{report.min_start_separation:.0f}, verdict {report.verdict}")
        if args.teams >= 2 and ally_d == ally_d:
            shape = "allies together" if ally_d < enemy_d else "TEAMS INTERLEAVED"
            print(f"[rwmaps] teams: ally {ally_d:.0f} vs enemy {enemy_d:.0f} -> {shape}")
        print(f"[rwmaps] wrote {rms_path}")
        if installed:
            print(f"[rwmaps] installed {installed.name}")
        print(f"[rwmaps] >>> in the lobby set Map Size to {lobby_size} "
              f"({size}x{size}) for {args.players} players")
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.outdir = timestamped_dir(args.outdir)
    generate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
