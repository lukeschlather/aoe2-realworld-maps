"""Fairness analysis for real-world map outlines.

Real-world coastlines are unfair in a way Arabia is not: one player can spawn on
an island and another in the middle of a continent. Everything here works on the
land mask alone, so a projection can be judged before the game ever runs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from . import terrain as T

#: 8-connectivity, matching how land units actually move.
_STRUCT = np.ones((3, 3), dtype=bool)


def land_mask_from_terrain(grid: np.ndarray) -> np.ndarray:
    """Recover the boolean land mask from a grid of terrain ids."""
    return ~np.isin(grid, list(T.WATER_IDS))


def components(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Label connected land masses.

    Returns ``(labels, sizes)`` where ``labels`` is 0 for water and ``sizes[i]``
    is the tile count of label ``i`` (``sizes[0]`` is meaningless).
    """
    labels, count = ndimage.label(mask, structure=_STRUCT)
    sizes = np.bincount(labels.reshape(-1), minlength=count + 1)
    return labels, sizes


def distance_to_water(mask: np.ndarray) -> np.ndarray:
    """Euclidean distance in tiles from each land tile to the nearest water."""
    return ndimage.distance_transform_edt(mask)


def land_path_distance(mask: np.ndarray, start: tuple[int, int]) -> np.ndarray:
    """Shortest walking distance over land from ``start`` to every tile.

    Breadth-first over 8-connected land, with diagonal steps costing the same as
    orthogonal ones - close enough for judging whether two starts are on the
    same landmass and roughly how far apart they are.
    """
    h, w = mask.shape
    dist = np.full(mask.shape, np.inf)
    sy, sx = start
    if not mask[sy, sx]:
        return dist
    dist[sy, sx] = 0.0
    queue = deque([(sy, sx)])
    while queue:
        y, x = queue.popleft()
        d = dist[y, x] + 1.0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and dist[ny, nx] > d:
                    dist[ny, nx] = d
                    queue.append((ny, nx))
    return dist


def start_quality(mask: np.ndarray, radius: int = 20) -> np.ndarray:
    """Fraction of land within ``radius`` tiles of each position.

    This is the single best cheap proxy for "is there room to actually play
    here": a start with 0.4 land within 20 tiles has half the space for
    resources, walls and a second town centre that a 0.9 start has.
    """
    yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    disc = ((yy**2 + xx**2) <= radius**2).astype(np.float64)
    disc /= disc.sum()
    return ndimage.convolve(mask.astype(np.float64), disc, mode="constant", cval=0.0)


def _farthest_point_pack_from_seed(
    coords: np.ndarray, players: int, seed: int
) -> list[int]:
    """Farthest-point / k-center-greedy selection starting from ``seed``."""
    picked = [seed]
    if players == 1 or len(coords) == 1:
        return picked
    dist = np.hypot(coords[:, 0] - coords[picked[0]][0], coords[:, 1] - coords[picked[0]][1])
    while len(picked) < players and len(picked) < len(coords):
        idx = int(np.argmax(dist))
        picked.append(idx)
        new_dist = np.hypot(coords[:, 0] - coords[idx][0], coords[:, 1] - coords[idx][1])
        dist = np.minimum(dist, new_dist)
    return picked


def _min_pairwise_dist(coords: np.ndarray, picked: list[int]) -> float:
    best = float("inf")
    for i in range(len(picked)):
        for j in range(i + 1, len(picked)):
            a, b = coords[picked[i]], coords[picked[j]]
            best = min(best, float(np.hypot(a[0] - b[0], a[1] - b[1])))
    return best


def _farthest_point_pack(
    coords: np.ndarray, quality: np.ndarray, players: int,
    min_separation: float = 0.0, n_seeds: int = 12,
) -> list[int]:
    """Pick ``players`` quality-filtered candidates, spread as far apart as
    possible (farthest-point / k-center-greedy selection).

    A quality-ranked "accept if it clears a separation bar" greedy pack (the
    previous approach here) never has to leave the single best-quality region
    of a landmass if it can keep finding acceptable points nearby - since
    quality is spatially smooth, that means every start can end up clustered
    on the one fattest part of the shape, even on an elongated coastline with
    plenty of usable land elsewhere. Farthest-point selection instead always
    grows toward whatever candidate is most isolated from what's already
    picked, which is what actually forces reach into a landmass's far ends -
    or its other islands, if the candidate pool spans more than one.

    Farthest-point's result depends on which point seeds it - forcing a
    per-step separation threshold sounds appealing but is path-dependent and
    can make the final minimum separation *worse* (an early forced pick can
    use up the "room" a later pick needed). Instead this runs the plain,
    well-behaved greedy from several different seeds (the top
    ``n_seeds`` candidates by quality) and keeps whichever run has the best
    achieved minimum pairwise separation - still just seating the best
    farthest-point solution available, not fighting the algorithm.
    ``min_separation`` is purely informational here (kept as a parameter for
    callers) once a seed search is in play; it doesn't change selection.
    """
    if len(coords) == 0:
        return []
    if players == 1 or len(coords) == 1:
        return [int(np.argmax(quality))]

    seed_candidates = np.argsort(-quality)[:n_seeds]
    best_pick, best_min_sep = None, -1.0
    for seed in seed_candidates:
        pick = _farthest_point_pack_from_seed(coords, players, int(seed))
        sep = _min_pairwise_dist(coords, pick)
        if sep > best_min_sep:
            best_min_sep, best_pick = sep, pick
    return best_pick


def choose_starts(
    mask: np.ndarray,
    players: int,
    *,
    radius: int = 20,
    min_inland: float = 3.0,
    edge_margin: int = 8,
    same_component: bool = False,
    min_component_tiles: int = 200,
    quality_percentile: float = 60.0,
    max_candidates: int = 6000,
    min_separation: float = 56.0,
) -> list[tuple[int, int]]:
    """Pick start tiles that are both *good* and *far apart*.

    Farthest-point sampling alone is a poor model of fair placement: maximising
    spread drives every start onto a coastal tip or peninsula, which is exactly
    where a player has least room. Instead this keeps only positions above a
    quality floor and then spreads players across those candidates by
    farthest-point selection (see ``_farthest_point_pack``).

    ``same_component`` restricts to the single largest connected landmass -
    useful for a map meant to be one continuous continent, but wrong for a
    genuine archipelago (Caribbean, Philippines, Indonesia, Denmark), where it
    throws away every island except whichever one happens to be biggest. The
    default instead keeps every component at least ``min_component_tiles``
    tiles (big enough to plausibly hold a start and its economy), so
    candidates can spread across separate islands too.

    ``min_separation`` is a soft floor (tiles) on TC-to-TC distance, passed to
    ``_farthest_point_pack``. The stock resource include places each player's
    gold/stone/deer 14-30 tiles from their own start regardless of map size
    (fixed tile counts, not scaled), so two starts closer than about
    ``2 * 28 = 56`` tiles have overlapping resource rings - a shared resource
    is fine, but the point of this floor is to make a resource landing
    reachable by *nobody* (an unlucky ring-overlap roll) much rarer. It's a
    preference, not a hard requirement: geography that can't fit ``players``
    starts this far apart still gets all of them seated, just closer.
    """
    depth = distance_to_water(mask)
    quality = start_quality(mask, radius)

    valid = depth >= min_inland
    if edge_margin:
        valid[:edge_margin, :] = False
        valid[-edge_margin:, :] = False
        valid[:, :edge_margin] = False
        valid[:, -edge_margin:] = False

    labels, sizes = components(mask)
    if same_component:
        usable = [i for i in range(1, len(sizes)) if (valid & (labels == i)).any()]
        if usable:
            best = max(usable, key=lambda i: int((valid & (labels == i)).sum()))
            valid &= labels == best
    elif min_component_tiles:
        big = np.zeros(len(sizes), dtype=bool)
        big[1:] = sizes[1:] >= min_component_tiles
        valid &= big[labels]

    if not valid.any():
        return []

    def candidates_at(percentile: float) -> tuple[np.ndarray, np.ndarray]:
        # The quality floor has to be computed *per landmass*, not as one
        # global percentile: "land within a 20-tile radius" is structurally
        # lower on a narrow island than on a big landmass no matter how solid
        # the island is, so a single global floor (set by whichever component
        # is biggest) can filter out every candidate on an otherwise
        # perfectly playable smaller island - which silently defeats
        # same_component=False for archipelagos.
        cand = np.zeros_like(valid)
        for lbl in np.unique(labels[valid]):
            comp = valid & (labels == lbl)
            floor = float(np.percentile(quality[comp], percentile))
            cand |= comp & (quality >= floor)
        if cand.sum() < players:
            cand = valid
        ys, xs = np.nonzero(cand)
        q = quality[ys, xs]
        if len(ys) > max_candidates:
            keep = np.argpartition(-q, max_candidates)[:max_candidates]
            ys, xs, q = ys[keep], xs[keep], q[keep]
        return np.stack([ys, xs], axis=1).astype(float), q

    coords, q = candidates_at(quality_percentile)

    if players <= 1:
        best = int(np.argmax(q))
        return [(int(coords[best][0]), int(coords[best][1]))]

    best_pick = _farthest_point_pack(coords, q, players)
    best_coords = coords
    best_sep = _min_pairwise_dist(coords, best_pick)

    # If the default quality floor can't seat `players` starts min_separation
    # apart, progressively admit lower-quality land (a real, if imperfect,
    # start is better than an artificially tight cluster) and keep whichever
    # attempt gets closest - this is a soft target, not a hard requirement,
    # since some coastlines genuinely can't fit `players` starts this far
    # apart no matter how much land is admitted.
    if best_sep < min_separation:
        for percentile in (40.0, 25.0, 10.0, 0.0):
            if percentile >= quality_percentile:
                continue
            c2, q2 = candidates_at(percentile)
            pick2 = _farthest_point_pack(c2, q2, players)
            sep2 = _min_pairwise_dist(c2, pick2)
            if sep2 > best_sep:
                best_pick, best_coords, best_sep = pick2, c2, sep2
            if best_sep >= min_separation:
                break

    return [(int(best_coords[i][0]), int(best_coords[i][1])) for i in best_pick]


def resource_ownership(
    mask: np.ndarray,
    tcs: list[tuple[int, float, float]],
    resources: list[tuple[str, float, float]],
    max_distance: float = 30.0,
) -> tuple[dict[int, dict[str, int]], dict[str, int]]:
    """Assign each resource to whichever TC can actually walk to it first.

    A resource only counts for a player if it's reachable over connected
    land (``land_path_distance``, not straight-line) from *that player's* TC
    and closer to it than to any other TC - a resource nearer another
    player's TC is effectively theirs, not a shared pool. ``max_distance``
    caps how far a villager will realistically walk for it (defaults to 30
    tiles, matching this project's own DEER placement's own
    ``max_distance_to_players``).

    Returns ``(per_player, unclaimed)`` - ``per_player[player][kind]`` is a
    count; ``unclaimed[kind]`` counts resources too far from every TC (by
    walking distance) to belong to anyone, or on a landmass no TC can reach.
    """
    paths = {p: land_path_distance(mask, (int(y), int(x))) for p, x, y in tcs}
    per_player: dict[int, dict[str, int]] = {p: {} for p, _, _ in tcs}
    unclaimed: dict[str, int] = {}

    for kind, x, y in resources:
        xi, yi = int(x), int(y)
        best_p, best_d = None, float("inf")
        for p, dist in paths.items():
            d = dist[yi, xi]
            if d < best_d:
                best_d, best_p = d, p
        if best_p is not None and best_d <= max_distance:
            per_player[best_p][kind] = per_player[best_p].get(kind, 0) + 1
        else:
            unclaimed[kind] = unclaimed.get(kind, 0) + 1

    return per_player, unclaimed


@dataclass
class PlayerReport:
    index: int
    y: int
    x: int
    component: int
    component_size: int
    land_within: float
    """Fraction of tiles within the working radius that are land."""
    dist_to_water: float
    nearest_opponent: float
    """Straight-line distance in tiles to the closest other start."""
    land_reachable: int
    """How many opponents can be reached on foot."""
    mean_land_path: float


@dataclass
class FairnessReport:
    players: list[PlayerReport]
    radius: int
    land_fraction: float
    component_count: int
    all_connected: bool
    land_within_spread: float
    """max - min of ``land_within``. Lower is fairer."""
    min_start_separation: float
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.warnings:
            return "unfair"
        if self.land_within_spread > 0.25:
            return "questionable"
        return "playable"

    def format(self) -> str:
        lines = [
            f"land coverage      : {self.land_fraction * 100:.1f}%",
            f"land masses        : {self.component_count}",
            f"all starts linked  : {'yes' if self.all_connected else 'NO'}",
            f"working radius     : {self.radius} tiles",
            "",
            f"{'P':>2}  {'pos':>9}  {'land@r':>7}  {'coast':>6}  {'nearest':>8}  "
            f"{'reach':>5}  {'landpath':>8}",
        ]
        for p in self.players:
            reach = f"{p.land_reachable}/{len(self.players) - 1}"
            path = "-" if not np.isfinite(p.mean_land_path) else f"{p.mean_land_path:.0f}"
            lines.append(
                f"{p.index:>2}  {f'{p.x},{p.y}':>9}  {p.land_within:>7.2f}  "
                f"{p.dist_to_water:>6.1f}  {p.nearest_opponent:>8.1f}  {reach:>5}  {path:>8}"
            )
        lines += [
            "",
            f"land@r spread      : {self.land_within_spread:.2f}  (lower is fairer)",
            f"min separation     : {self.min_start_separation:.1f} tiles",
        ]
        for w in self.warnings:
            lines.append(f"WARNING: {w}")
        lines.append(f"verdict            : {self.verdict}")
        return "\n".join(lines)


def evaluate(
    mask: np.ndarray,
    starts: list[tuple[int, int]],
    *,
    radius: int = 20,
) -> FairnessReport:
    """Score a set of start positions on a land mask."""
    labels, sizes = components(mask)
    depth = distance_to_water(mask)

    # Fraction of land in a disc of `radius` tiles around each start.
    yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    disc = (yy**2 + xx**2) <= radius**2
    padded = np.pad(mask, radius, constant_values=False)

    paths = [land_path_distance(mask, s) for s in starts]

    reports: list[PlayerReport] = []
    for i, (y, x) in enumerate(starts):
        window = padded[y : y + 2 * radius + 1, x : x + 2 * radius + 1]
        land_within = float(window[disc].mean())

        others = [j for j in range(len(starts)) if j != i]
        seps = [float(np.hypot(y - starts[j][0], x - starts[j][1])) for j in others]
        walk = [paths[i][starts[j]] for j in others]
        finite = [w for w in walk if np.isfinite(w)]

        reports.append(
            PlayerReport(
                index=i + 1,
                y=y,
                x=x,
                component=int(labels[y, x]),
                component_size=int(sizes[labels[y, x]]),
                land_within=land_within,
                dist_to_water=float(depth[y, x]),
                nearest_opponent=min(seps) if seps else float("nan"),
                land_reachable=len(finite),
                mean_land_path=float(np.mean(finite)) if finite else float("inf"),
            )
        )

    within = [p.land_within for p in reports]
    spread = (max(within) - min(within)) if within else 0.0
    all_connected = all(p.land_reachable == len(starts) - 1 for p in reports)
    min_sep = min(
        (p.nearest_opponent for p in reports if np.isfinite(p.nearest_opponent)),
        default=float("nan"),
    )

    size = mask.shape[0]
    warnings: list[str] = []
    if len(starts) < 2:
        warnings.append("fewer than two start positions could be placed")
    if not all_connected:
        stranded = [p.index for p in reports if p.land_reachable < len(starts) - 1]
        warnings.append(f"players {stranded} cannot reach every opponent by land")
    if within and min(within) < 0.35:
        poor = [p.index for p in reports if p.land_within < 0.35]
        warnings.append(f"players {poor} have very little land around their start")
    # Starts this close together mean the usable landmass is too small for the
    # player count - usually a sign the window has too much ocean in frame.
    if np.isfinite(min_sep) and min_sep < 0.15 * size:
        warnings.append(
            f"starts are only {min_sep:.0f} tiles apart on a {size}-tile map "
            f"({min_sep / size:.0%} of the map); the usable land is cramped for "
            f"{len(starts)} players"
        )

    return FairnessReport(
        players=reports,
        radius=radius,
        land_fraction=float(mask.mean()),
        component_count=int((sizes[1:] > 0).sum()),
        all_connected=all_connected,
        land_within_spread=spread,
        min_start_separation=min_sep,
        warnings=warnings,
    )


def assign_teams(
    starts: list[tuple[int, int]], teams: int = 2
) -> list[tuple[int, int]]:
    """Reorder starts so consecutive player numbers sit together.

    Lobbies put players 1..n/2 on one team and the rest on the other, so for a
    3v3 or 4v4 the placement has to be clustered, not interleaved - otherwise
    allies spawn across the map from each other and every game is a scramble.

    Uses Lloyd relaxation on the start positions, then walks the clusters in
    order, which keeps each team contiguous.
    """
    if teams < 2 or len(starts) < teams:
        return starts
    pts = np.array(starts, dtype=float)
    per = len(starts) // teams

    # Seed cluster centres at the two most distant starts, then relax.
    d2 = ((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
    i, j = np.unravel_index(int(np.argmax(d2)), d2.shape)
    centres = [pts[i], pts[j]]
    while len(centres) < teams:
        far = max(range(len(pts)),
                  key=lambda k: min(float(np.hypot(*(pts[k] - c))) for c in centres))
        centres.append(pts[far])
    centres = np.array(centres)

    for _ in range(12):
        dist = np.linalg.norm(pts[:, None, :] - centres[None, :, :], axis=-1)
        labels = dist.argmin(axis=1)
        for t in range(teams):
            if (labels == t).any():
                centres[t] = pts[labels == t].mean(axis=0)

    # Hand out exactly `per` starts per team, nearest-centre first, so team
    # sizes stay even even when the clusters are lopsided.
    dist = np.linalg.norm(pts[:, None, :] - centres[None, :, :], axis=-1)
    taken: set[int] = set()
    ordered: list[tuple[int, int]] = []
    for t in range(teams):
        order = sorted(range(len(pts)), key=lambda k: dist[k, t])
        picked = [k for k in order if k not in taken][:per]
        taken.update(picked)
        ordered.extend(starts[k] for k in picked)
    ordered.extend(starts[k] for k in range(len(starts)) if k not in taken)
    return ordered


def team_separation(starts: list[tuple[int, int]], teams: int = 2) -> tuple[float, float]:
    """(mean distance between allies, mean distance to the nearest enemy).

    Allies closer than enemies is the shape you want for a team game.
    """
    if teams < 2 or len(starts) < teams * 2:
        return float("nan"), float("nan")
    per = len(starts) // teams
    labels = [min(i // per, teams - 1) for i in range(len(starts))]
    ally, enemy = [], []
    for i, a in enumerate(starts):
        for j, b in enumerate(starts):
            if i >= j:
                continue
            d = float(np.hypot(a[0] - b[0], a[1] - b[1]))
            (ally if labels[i] == labels[j] else enemy).append(d)
    return (float(np.mean(ally)) if ally else float("nan"),
            float(np.mean(enemy)) if enemy else float("nan"))


#: A water body smaller than this is a pond: not worth a dock, no fish economy.
DOCKABLE_WATER_TILES = 400


def largest_water_body(mask: np.ndarray) -> int:
    """Tile count of the biggest connected stretch of water."""
    _, sizes = components(~mask)
    return int(sizes[1:].max()) if len(sizes) > 1 else 0


def water_access(mask: np.ndarray, starts: list[tuple[int, int]], radius: int = 30) -> float:
    """Fraction of starts with dock-worthy water within ``radius`` tiles.

    Reported as a diagnostic. It is deliberately *not* what picks the AI type:
    on a mostly-land map the figure swings wildly with where the starts happen
    to land, which made the same geography flip between ARABIA and COASTAL just
    by rotating it.
    """
    if not starts:
        return 0.0
    labels, sizes = components(~mask)
    big = np.isin(labels, [i for i in range(1, len(sizes))
                           if sizes[i] >= DOCKABLE_WATER_TILES])
    if not big.any():
        return 0.0
    near = 0
    yy, xx = np.ogrid[: mask.shape[0], : mask.shape[1]]
    for y, x in starts:
        disc = ((yy - y) ** 2 + (xx - x) ** 2) <= radius**2
        if (big & disc).any():
            near += 1
    return near / len(starts)


def choose_ai_map_type(mask: np.ndarray, starts: list[tuple[int, int]]) -> str:
    """Pick the ``ai_info_map_type`` that matches the generated geography.

    The decision that actually matters is **whether the AI will try to fish**.
    ``ARABIA`` means a dry land map with no fish, so the AI never builds a dock;
    picking it for a map full of lakes throws away the whole water economy. So
    this keys off water *reachable from the starts*, not the land fraction -
    Great Lakes is ~85% land but every start sits on a big lake, and it should
    be ``COASTAL``.

    Constants come from ``random_map.def``.
    """
    if len(starts) < 2:
        return "ARABIA"

    paths = [land_path_distance(mask, s) for s in starts]
    connected = all(
        np.isfinite(paths[i][starts[j]])
        for i in range(len(starts))
        for j in range(len(starts))
        if i != j
    )
    water = 1.0 - float(mask.mean())

    if not connected:
        # Players cannot walk to each other: the AI must go naval.
        return "ISLANDS" if water > 0.5 else "ARCHIPELAGO"
    if water < 0.05 or largest_water_body(mask) < DOCKABLE_WATER_TILES:
        # Genuinely dry. ARABIA tells the AI there are no fish at all, so it is
        # only ever right when that is actually true.
        return "ARABIA"
    if water < 0.45:
        # Land map with real water on it - fish booming is on the table.
        return "COASTAL"
    return "MEDITERRANEAN"


def analyze(
    mask: np.ndarray,
    players: int = 4,
    *,
    radius: int = 20,
    min_inland: float = 3.0,
    same_component: bool = True,
) -> FairnessReport:
    """Choose starts on ``mask`` and score them in one call."""
    starts = choose_starts(
        mask,
        players,
        radius=radius,
        min_inland=min_inland,
        same_component=same_component,
    )
    return evaluate(mask, starts, radius=radius)
