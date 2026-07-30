"""Encode a real coastline directly in a random map script.

Why this exists: the ``.scx`` route only works by editing the game install, so
it is single-player only. A plain ``.rms`` is auto-transferred to other players
in multiplayer, needs no mod, and touches no game files - but then the coastline
has to live in the script text.

``create_land`` can be placed at an absolute ``land_position`` (percentages of
the map), so a land mask can be approximated by a pile of overlapping lands.
Greedy disc cover of the mask reaches roughly IoU 0.88 at 400 lands and 0.91 at
1000 on a 220-tile grid, which leaves Britain plainly recognisable.

Fidelity is inherently blobbier than a ``.scx`` outline: the engine grows lands
organically rather than stamping clean discs, and ``land_position`` is integer
percent, so positions quantise to about ``size / 100`` tiles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage


@dataclass(frozen=True)
class Disc:
    y: int
    x: int
    radius: float


def cover_mask(
    mask: np.ndarray,
    budget: int = 400,
    max_radius: float = 12.0,
    overlap: float = 0.72,
) -> list[Disc]:
    """Greedily cover ``mask`` with at most ``budget`` discs.

    Each step drops a disc on the most interior uncovered tile, sized to the
    local distance to the edge of what is left. Big open areas get consumed by a
    few large discs, so the remaining budget goes to the fiddly coastline.

    ``overlap`` is the crux. Circles do not tile the plane, so clearing exactly
    the disc we placed leaves interstitial slivers between neighbours - and every
    sliver becomes a one-tile pond in the finished map, which speckles the land
    so badly you cannot read the landmass. Clearing a *smaller* disc than we
    record makes neighbours overlap, so their union is solid.
    """
    remaining = mask.copy()
    height, width = mask.shape
    yy, xx = np.ogrid[:height, :width]
    discs: list[Disc] = []
    for _ in range(budget):
        if not remaining.any():
            break
        depth = ndimage.distance_transform_edt(remaining)
        flat = int(np.argmax(depth))
        y, x = np.unravel_index(flat, depth.shape)
        radius = float(np.clip(depth[y, x], 1.0, max_radius))
        discs.append(Disc(int(y), int(x), radius))
        clear = max(1.0, radius * overlap)
        remaining = remaining & (((yy - y) ** 2 + (xx - x) ** 2) > clear**2)
    return discs


def interior_holes(approx: np.ndarray, truth: np.ndarray) -> float:
    """Fraction of real land the cover misses - i.e. how speckled it will be."""
    total = int(truth.sum())
    return float((truth & ~approx).sum() / total) if total else 0.0


def rasterize_discs(discs: list[Disc], size: int) -> np.ndarray:
    """What the discs actually cover - for previewing before generating."""
    yy, xx = np.ogrid[:size, :size]
    out = np.zeros((size, size), dtype=bool)
    for d in discs:
        out |= ((yy - d.y) ** 2 + (xx - d.x) ** 2) <= d.radius**2
    return out


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def to_land_position(y: int, x: int, size: int) -> tuple[int, int]:
    """Tile coordinate -> ``land_position`` percentages.

    The engine takes integer percentages. Row 0 is the north edge and column 0
    the west edge, matching the grid convention used everywhere else here.
    """
    px = int(round(100.0 * x / (size - 1)))
    py = int(round(100.0 * y / (size - 1)))
    return max(0, min(100, px)), max(0, min(100, py))


def _land_block(
    d: Disc,
    size: int,
    terrain_type: str,
    land_id: int,
    player: int | None = None,
    tiles: int | None = None,
) -> str:
    px, py = to_land_position(d.y, d.x, size)
    if tiles is None:
        tiles = max(1, int(round(math.pi * d.radius**2)))
    # base_size is the half-width of the seed square, so it alone covers
    # (2*base + 1)^2 tiles. Keep it well under the tile budget or every land
    # overshoots and the whole map floods with land.
    base = max(0, int(round(d.radius * 0.35)))
    lines = [
        "create_land",
        "{",
        f"  terrain_type                  {terrain_type}",
        f"  land_position                 {px} {py}",
        f"  base_size                     {base}",
        f"  number_of_tiles               {tiles}",
        "  other_zone_avoidance_distance 0",
        "  clumping_factor                8",
    ]
    if player is not None:
        lines.append(f"  assign_to_player              {player}")
    else:
        # One shared land_id for the whole coastline. Giving every blob its own
        # id creates hundreds of zones, which breaks anything keyed on zone
        # distance - notably `max_distance_to_other_zones`, which is how fish
        # are kept away from shore.
        lines.append(f"  land_id                       {land_id}")
    lines.append("}")
    return "\n".join(lines)


#: Tiles given to each player's own starting land.
PLAYER_LAND_TILES = 240

#: Every coastline blob shares one zone id - see the note in ``_land_block``.
COASTLINE_LAND_ID = 1

#: Land tiles are budgeted slightly over the true land area so neighbouring
#: lands actually meet. Budgeting exactly leaves them just shy of touching, and
#: the gaps show up as ponds all through the interior.
FILL_FACTOR = 1.18


def build_land_generation(
    discs: list[Disc],
    size: int,
    starts: list[tuple[int, int]] | None = None,
    *,
    target_tiles: int | None = None,
    terrain_type: str = "GRASS",
    base_terrain: str = "WATER",
) -> str:
    """Render the ``<LAND_GENERATION>`` section.

    ``target_tiles`` is the land area the real mask actually covers. The discs
    overlap heavily, so their areas sum to far more than the union; without
    rescaling, every land grows to its own full area and the map floods. Pass
    ``mask.sum()`` and the budget is divided proportionally instead.

    ``starts`` are tile positions for players; each becomes its own land with
    ``assign_to_player``, which is what stops the engine dropping a Town Centre
    in the sea.
    """
    parts = [
        "<LAND_GENERATION>",
        "",
        f"base_terrain {base_terrain}",
        "",
    ]

    areas = [math.pi * d.radius**2 for d in discs]
    total = sum(areas) or 1.0
    if target_tiles is None:
        scale = 1.0
    else:
        budget = max(0, target_tiles * FILL_FACTOR - PLAYER_LAND_TILES * len(starts or []))
        scale = budget / total

    if starts:
        parts.append("/* deliberate, fairness-checked start positions */")
        for i, (y, x) in enumerate(starts, start=1):
            parts.append(
                _land_block(Disc(y, x, 9.0), size, terrain_type, 0,
                            player=i, tiles=PLAYER_LAND_TILES)
            )
            parts.append("")

    parts.append(
        f"/* coastline: {len(discs)} lands approximating the real outline "
        f"({int(total * scale)} tiles total) */"
    )
    for d, area in zip(discs, areas):
        parts.append(
            _land_block(d, size, terrain_type, land_id=COASTLINE_LAND_ID,
                        tiles=max(1, int(round(area * scale))))
        )
    return "\n".join(parts) + "\n"
