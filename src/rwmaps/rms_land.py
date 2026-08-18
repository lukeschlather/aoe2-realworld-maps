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
    overlap: float = 1.0,
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
    clumping_factor: int = 8,
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
        f"  clumping_factor                {clumping_factor}",
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

#: Islands get their own ids, counting up from here. Kept clear of
#: ``COASTLINE_LAND_ID`` and of the player lands, which use none.
ISLAND_LAND_ID_BASE = 10

#: An island smaller than this is a rock, not a place worth sailing to.
#: Matches ``neutral_supply.py``'s MIN_ISLAND_TILES so the thing generated
#: and the thing measured are the same thing.
MIN_ISLAND_TILES = 60


#: How many rings to peel off an island to estimate the part of it that can
#: actually be built on. The engine paints BEACH along every shoreline, and
#: beach is walkable but unbuildable, so raw tile count badly overstates a
#: small island - measured on real captures, a 151-tile island had 71
#: buildable tiles and a 68-tile one had 18. One ring is far too generous at
#: that ratio; two lands close to it. This is an estimate on the *mask*,
#: which the disc cover only approximates, so treat it as an order of
#: magnitude for sizing tree counts, not a promise.
SHORE_RINGS = 2


@dataclass(frozen=True)
class Island:
    """An unowned landmass that resources can be placed onto by land id."""

    land_id: int
    tiles: int
    n_discs: int
    #: Estimated non-shore tiles - what a camp or a tree can actually go on.
    #: Drives how many trees the island is asked for; see ``SHORE_RINGS``.
    buildable: int = 0


def _island_ids(discs: list[Disc], mask: np.ndarray,
                starts: list[tuple[int, int]] | None,
                ) -> tuple[dict[int, int], list[Island]]:
    """Assign a land id per unowned island; return (disc index -> id, islands).

    Resources cannot be aimed at an island by distance - measured, a
    map-wide neutral pass saturates on the mainland long before it needs an
    island, so Black Sea and Salish Sea came back with every island bare
    while land-poor Caribbean stocked all of its. ``place_on_specific_land_id``
    aims directly, and that needs the island to *be* its own land id.

    Only unowned islands get their own id. The mainland and any landmass
    holding a start stay on ``COASTLINE_LAND_ID``, because every extra id is
    another zone and ``starting_resources.inc`` and ``huntable.inc`` are
    keyed on zone distance - the reason the whole coastline shares one id in
    the first place. This adds a handful of zones, not hundreds.
    """
    labels, n = ndimage.label(mask)
    owned = {int(labels[y, x]) for y, x in (starts or [])}
    owned.discard(0)

    sizes = ndimage.sum_labels(mask, labels, index=range(1, n + 1))
    inland = ndimage.binary_erosion(mask, iterations=SHORE_RINGS)
    island_of_label: dict[int, int] = {}
    islands: list[Island] = []
    for lbl in range(1, n + 1):
        if lbl in owned or sizes[lbl - 1] < MIN_ISLAND_TILES:
            continue
        land_id = ISLAND_LAND_ID_BASE + len(islands)
        island_of_label[lbl] = land_id
        islands.append(Island(land_id, int(sizes[lbl - 1]), 0,
                              int((inland & (labels == lbl)).sum())))

    by_disc: dict[int, int] = {}
    counts: dict[int, int] = {}
    for i, d in enumerate(discs):
        lbl = int(labels[int(round(d.y)), int(round(d.x))])
        land_id = island_of_label.get(lbl)
        if land_id is not None:
            by_disc[i] = land_id
            counts[land_id] = counts.get(land_id, 0) + 1

    # An island whose discs all sit off-centre would get an id nothing is
    # assigned to, and a create_object aimed at it would place nothing.
    islands = [Island(i.land_id, i.tiles, counts.get(i.land_id, 0), i.buildable)
               for i in islands if counts.get(i.land_id)]
    live = {i.land_id for i in islands}
    return {k: v for k, v in by_disc.items() if v in live}, islands

def build_land_generation(
    discs: list[Disc],
    size: int,
    starts: list[tuple[int, int]] | None = None,
    *,
    target_tiles: int | None = None,
    terrain_type: str = "GRASS",
    base_terrain: str = "WATER",
    clumping_factor: int = 8,
    player_terrain: str | None = None,
    mask: np.ndarray | None = None,
    islands_out: list[Island] | None = None,
    shallows: list[Disc] | None = None,
) -> str:
    """Render the ``<LAND_GENERATION>`` section.

    ``target_tiles`` is the land area the real mask actually covers. The discs
    overlap heavily, so their areas sum to far more than the union; without
    rescaling, every land grows to its own full area and the map floods. Pass
    ``mask.sum()`` and the budget is divided proportionally instead.

    ``starts`` are tile positions for players; each becomes its own land with
    ``assign_to_player``, which is what stops the engine dropping a Town Centre
    in the sea.

    ``player_terrain`` paints those player lands - and only those - with a
    different terrain than the coastline. Passing a placeholder terrain here
    is what makes a per-player forest possible: it leaves eight disjoint,
    individually addressable regions, one per start, which
    ``rms.build_per_player_forest`` then splits and budgets separately. Only
    the terrain *label* changes; the land's shape and size are untouched, so
    the coastline is unaffected.

    ``mask`` opts into per-island land ids: every unowned landmass of
    ``MIN_ISLAND_TILES`` or more gets its own id, and the islands found are
    appended to ``islands_out`` so the resource layer can aim at them with
    ``place_on_specific_land_id``. Without it every blob shares
    ``COASTLINE_LAND_ID`` as before.
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
        budget = max(0, target_tiles - PLAYER_LAND_TILES * len(starts or []))
        scale = budget / total

    if starts:
        parts.append("/* deliberate, fairness-checked start positions */")
        for i, (y, x) in enumerate(starts, start=1):
            parts.append(
                _land_block(Disc(y, x, 9.0), size,
                            player_terrain or terrain_type, 0,
                            player=i, tiles=PLAYER_LAND_TILES,
                            clumping_factor=clumping_factor)
            )
            parts.append("")

    island_ids: dict[int, int] = {}
    if mask is not None:
        island_ids, found = _island_ids(discs, mask, starts)
        if islands_out is not None:
            islands_out.extend(found)
        if found:
            parts.append(
                f"/* {len(found)} unowned islands carry their own land ids "
                f"({ISLAND_LAND_ID_BASE}+), so the resource layer can place "
                f"onto them by id rather than hoping a map-wide pass reaches "
                f"them - measured, it does not */"
            )

    parts.append(
        f"/* coastline: {len(discs)} lands approximating the real outline "
        f"({int(total * scale)} tiles total) */"
    )
    for i, (d, area) in enumerate(zip(discs, areas)):
        parts.append(
            _land_block(d, size, terrain_type,
                        land_id=island_ids.get(i, COASTLINE_LAND_ID),
                        tiles=max(1, int(round(area * scale))),
                        clumping_factor=clumping_factor)
        )

    # Shallows go last: create_land blocks paint in order, and these are
    # meant to cut across coastline that has already been laid down.
    #
    # They reuse COASTLINE_LAND_ID rather than taking ids of their own. A
    # fresh id per patch would add dozens of zones, and starting_resources
    # and huntable are keyed on zone distance - see the note in _land_block.
    # Sharing the coastline's id adds none.
    #
    # clumping_factor 1 keeps a channel a channel. The coastline default of
    # 8 deliberately makes ragged organic blobs, which along a strait means
    # a chain that pinches shut somewhere - and a strait that closes
    # anywhere is not a strait.
    if shallows:
        parts.append(
            f"/* {len(shallows)} shallows patches: passable by boats AND "
            f"fordable by land units, so these add naval passage without "
            f"removing any land route */"
        )
        for d in shallows:
            parts.append(
                _land_block(d, size, "SHALLOWS", land_id=COASTLINE_LAND_ID,
                            tiles=max(1, int(round(math.pi * d.radius ** 2))),
                            clumping_factor=1)
            )
    return "\n".join(parts) + "\n"
