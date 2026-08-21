"""Fairness analysis for real-world map outlines.

Real-world coastlines are unfair in a way Arabia is not: one player can spawn on
an island and another in the middle of a continent. Everything here works on the
land mask alone, so a projection can be judged before the game ever runs.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.ndimage import binary_dilation

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


#: 8-connected neighbourhood, as a structuring element.
_NEIGHBOURHOOD = np.ones((3, 3), dtype=bool)


def land_path_distance(
    mask: np.ndarray, start: tuple[int, int], max_distance: float | None = None
) -> np.ndarray:
    """Shortest walking distance over land from ``start`` to every tile.

    Breadth-first over 8-connected land, with diagonal steps costing the same as
    orthogonal ones - close enough for judging whether two starts are on the
    same landmass and roughly how far apart they are.

    Implemented as a wavefront: because every step costs exactly 1, the set of
    tiles at distance d is one binary dilation of the set at distance d-1,
    intersected with the unvisited land. That is the same traversal an explicit
    queue does, but it advances a whole distance ring per numpy operation
    instead of one tile per Python loop iteration - roughly a thousand array ops
    for a 240x240 map rather than millions of scalar ones. This function is the
    inner loop of every fairness comparison (eight sources per capture, and the
    project's stated goal is ~1000 captures), so the constant factor is not
    incidental.

    ``max_distance`` stops the wavefront early, leaving everything beyond it
    at ``inf``. One full-map call costs ~110ms, and the start-placement
    annealer makes one per proposal - so an uncapped search costs minutes per
    region. Callers that only care about distances up to some bound (the
    annealer's score saturates past twice the separation floor) should pass
    it; the returned values within the bound are identical either way.
    """
    dist = np.full(mask.shape, np.inf)
    sy, sx = start
    if not mask[sy, sx]:
        return dist

    frontier = np.zeros(mask.shape, dtype=bool)
    frontier[sy, sx] = True
    visited = frontier.copy()
    dist[sy, sx] = 0.0

    d = 0.0
    while frontier.any():
        if max_distance is not None and d >= max_distance:
            break
        d += 1.0
        frontier = binary_dilation(frontier, _NEIGHBOURHOOD) & mask & ~visited
        dist[frontier] = d
        visited |= frontier
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


def _spatial_stratified_top(ys: np.ndarray, xs: np.ndarray, q: np.ndarray, budget: int) -> np.ndarray:
    """Downsample to at most ``budget`` candidates while preserving
    geographic spread, unlike a pure top-``budget``-by-quality cut.

    Confirmed as the actual root cause of Italy's "no picks anywhere in the
    peninsula" failure (previously misattributed to Lloyd relaxation/the
    scoring in ``choose_starts``' ``spread_starts`` path): the mainland
    component's per-component candidate budget was being filled entirely by
    tiles tied at the maximum quality score (the flat Po valley/France/Balkans
    plains, thousands of tiles at quality==1.0), leaving the Apennine
    peninsula - real, qualifying land, just hillier and coastal on both sides
    so its quality tops out around 0.89 - with literally zero candidates in
    the pool. No downstream selection algorithm can place a start somewhere
    that was never a candidate to begin with.

    Bins the candidates' bounding box into a roughly-square grid sized so the
    number of cells is close to ``budget``, keeps the single best-quality
    candidate per occupied cell first (guaranteeing every populated region
    gets at least one shot), then tops up with the next-best remaining
    candidates (regardless of cell) if occupied cells fall short of budget.
    """
    n = len(ys)
    if n <= budget:
        return np.arange(n)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    n_bins_side = max(1, int(np.ceil(np.sqrt(budget))))
    bh = max(1.0, (y1 - y0 + 1) / n_bins_side)
    bw = max(1.0, (x1 - x0 + 1) / n_bins_side)
    by = np.clip(((ys - y0) / bh).astype(int), 0, n_bins_side - 1)
    bx = np.clip(((xs - x0) / bw).astype(int), 0, n_bins_side - 1)
    cell = by * n_bins_side + bx

    order = np.argsort(-q)
    keep: list[int] = []
    seen_cells: set[int] = set()
    for idx in order:
        c = int(cell[idx])
        if c not in seen_cells:
            seen_cells.add(c)
            keep.append(int(idx))
            if len(keep) >= budget:
                break
    if len(keep) < budget:
        kept = set(keep)
        for idx in order:
            i = int(idx)
            if i not in kept:
                keep.append(i)
                kept.add(i)
                if len(keep) >= budget:
                    break
    return np.array(keep, dtype=int)


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


def _farthest_point_pack_from_seed_geodesic(
    mask: np.ndarray, coords: np.ndarray, players: int, seed: int
) -> list[int]:
    """Geodesic version of ``_farthest_point_pack_from_seed`` - grows by
    land-path (``land_path_distance``), not straight-line, distance.

    Matters as the SEEDING step for ``_anneal_starts``: a good initialization
    still helps annealing converge faster and more reliably even though it
    hillclimbs a real objective now (see ``_score_starts``) rather than only
    reassigning candidates to their nearest existing pick the way the
    previous Lloyd-relaxation approach did. If the initial seed set already
    puts two picks near the same small landmass (a real risk with Euclidean
    seeding, which has no notion that two Euclidean-distant points can still
    be geodesically close, e.g. both on the same small island reached from
    different angles), that's a worse starting point for the search to climb
    out of. Seeding by geodesic distance instead means a second
    point already assigned to some island reads as "not particularly far"
    from a first point already there, so the growth naturally reaches for a
    genuinely different, still-unclaimed landmass or peninsula next.
    """
    picked = [seed]
    if players == 1 or len(coords) == 1:
        return picked
    # A candidate on a different, disconnected landmass has genuinely
    # infinite land-path distance - and should register as maximally
    # attractive to farthest-point growth (it IS maximally isolated), not
    # excluded. np.inf participates correctly in both argmax and minimum.
    ys, xs = coords[:, 0].astype(int), coords[:, 1].astype(int)
    y0, x0 = int(coords[seed][0]), int(coords[seed][1])
    dist = land_path_distance(mask, (y0, x0))[ys, xs]
    while len(picked) < players and len(picked) < len(coords):
        idx = int(np.argmax(dist))
        picked.append(idx)
        y1, x1 = int(coords[idx][0]), int(coords[idx][1])
        new_dist = land_path_distance(mask, (y1, x1))[ys, xs]
        dist = np.minimum(dist, new_dist)
    return picked


def _forced_component_seeds_geodesic(
    mask: np.ndarray, coords: np.ndarray, quality: np.ndarray, players: int,
    comp_ids: np.ndarray,
) -> list[int]:
    """One seed per qualifying landmass component (largest components first
    if there are more components than ``players``), then the remaining
    seats grown by geodesic farthest-point selection across the whole pool.

    Three pieces were needed, confirmed empirically on Italy - any two alone
    still leave a real failure mode:

    - Geodesic farthest-point growth alone (no forced per-component seeds)
      sometimes seats two picks on the same modest island (Tunisia) while a
      genuinely separate one (Corsica) sits untouched, because "maximize the
      minimum pairwise distance" has no notion of "don't reuse a landmass
      while another sits unused" (see ``_multistart_anneal``, which now uses
      this as one of several starting points for its own search).
    - Forcing one seed per component fixes that at the SEED stage (no island
      can be skipped or doubled there) - but growing the *remaining* seats
      by unrestricted farthest-point selection across the WHOLE candidate
      pool can still re-discover that same small island as "farthest" from
      wherever its own single seed landed, re-introducing the exact
      duplicate the forced seeding was meant to prevent.
    - So the remaining seats are restricted to the single LARGEST qualifying
      component's own candidates - the one component that actually has
      enough distinct area to justify more than its guaranteed one, and
      grown by GEODESIC (not Euclidean) distance so they don't wander into a
      huge component's far corner (e.g. France/the Balkans, which really are
      land-connected to Italy's own peninsula, not a raster artifact)
      instead of reaching the actually-isolated-by-walking-distance
      peninsula.
    """
    unique_comps = np.unique(comp_ids)
    comp_sizes = sorted(((int((comp_ids == c).sum()), c) for c in unique_comps), reverse=True)
    picked = []
    for _, c in comp_sizes[:players]:
        in_comp = np.nonzero(comp_ids == c)[0]
        picked.append(int(in_comp[np.argmax(quality[in_comp])]))

    ys, xs = coords[:, 0].astype(int), coords[:, 1].astype(int)
    if len(picked) < players:
        largest_comp = comp_sizes[0][1]
        growth_pool = np.nonzero(comp_ids == largest_comp)[0]
        gys, gxs = ys[growth_pool], xs[growth_pool]
        dist = np.full(len(growth_pool), np.inf)
        for s in picked:
            y0, x0 = int(coords[s][0]), int(coords[s][1])
            dist = np.minimum(dist, land_path_distance(mask, (y0, x0))[gys, gxs])
        while len(picked) < players and (dist > -1).any():
            local_idx = int(np.argmax(dist))
            idx = int(growth_pool[local_idx])
            picked.append(idx)
            y1, x1 = int(coords[idx][0]), int(coords[idx][1])
            new_dist = land_path_distance(mask, (y1, x1))[gys, gxs]
            dist = np.minimum(dist, new_dist)
            dist[local_idx] = -1.0  # already chosen, never pick again
    return picked[:players]


#: TC-to-TC walking distance (tiles) beyond which a patch of land no longer
#: counts as "reachable" by some player for the coverage term in
#: ``_score_starts`` - roughly a resource ring's width past ``min_separation``
#: / 2, matching this project's own DEER ``max_distance_to_players`` and
#: ``resource_ownership``'s default.
_COVERAGE_RADIUS = 40.0


#: Radius, in walking tiles, over which a start's "available land" is
#: counted. Matches ``start_quality``'s radius and the band every resource
#: tier places into.
_LAND_RADIUS = 20.0

#: Reachable land (tiles within ``_LAND_RADIUS``) a start needs before extra
#: land stops mattering. Taken from the N=10 capture pass over 880 real
#: starts, which measured how often a start could reach *none* of some
#: resource kind against how much open ground it had:
#:
#: | reachable tiles | starts | missing something |
#: |-----------------|--------|-------------------|
#: | 200-399         | 15     | 100%              |
#: | 400-599         | 111    | 68%               |
#: | 600-799         | 86     | 22%               |
#: | 800-999         | 114    | 4%                |
#: | 1000+           | 554    | ~0%               |
#:
#: 1000 is where the failure rate reaches zero. This is measured on the
#: land mask before forest exists, so it reads slightly higher than the
#: capture-time figure for the same spot.
_LAND_TARGET = 1000.0


def _score_starts(
    picks_yx: list[tuple[int, int]],
    paths: list[np.ndarray], min_separation: float,
) -> float:
    """Single scalar loss (lower is better) over the two things that decide
    whether a start layout is any good: **how far apart the players are**
    and **how much land each one actually has**.

    This replaces a four-term loss whose dominant component (weight 2.0 of
    4.4) was *coverage* - the fraction of the map's land more than 40
    walking tiles from every pick. Coverage is a property of the map, not of
    any player: it rewards spreading picks out to blanket the landmass, and
    it is indifferent to whether the spot a pick lands on can support a
    start. On Italy that is the difference between seating players where the
    geography is interesting and seating them wherever the remaining
    uncovered land happens to be. Coverage and distance-to-water are both
    gone; nothing here is a proxy for anything.

    - **available land**: reachable land within ``_LAND_RADIUS`` walking
      tiles of each pick, scored mostly on the WORST-off pick. Reachable,
      not a straight-line disc, so land across a channel does not count.
      This is the term with direct evidence behind it - see
      ``_LAND_TARGET``, measured over 880 real starts.
    - **separation**: the *minimum* pairwise walking distance between picks,
      rewarded up to twice ``min_separation`` and penalised hard below it.
      Maximising the minimum is what stops one cramped pair hiding behind
      good separation elsewhere, which is what the old spread-uniformity
      term was approximating.

    The two pull against each other on purpose, and that tension is the
    point: separation alone drives every start onto a coastal tip or a
    peninsula's end, which is exactly where a player has least room to play.
    """
    lands = [float((p <= _LAND_RADIUS).sum()) for p in paths]
    worst_land = min(lands)
    mean_land = float(np.mean(lands))
    # Weighted toward the worst-off player: a layout is as fair as its most
    # cramped start, and averaging lets seven good starts hide one unplayable
    # one. Both terms saturate at _LAND_TARGET so that beyond "enough", extra
    # land stops competing with separation.
    land_score = (0.75 * min(worst_land / _LAND_TARGET, 1.0)
                  + 0.25 * min(mean_land / _LAND_TARGET, 1.0))

    # Separation is measured two ways because they mean different things,
    # and using only one of them is a real bug either way.
    #
    # The FLOOR is straight-line. An earlier version scored a geodesically
    # unreachable pair as "on another island, therefore maximally far",
    # which let the search put two town centres NINE tiles apart across a
    # strait and call it perfect separation - measured on Japan, where min
    # separation collapsed 46 -> 9. Water is not distance: those two players
    # are in each other's laps the moment either builds a dock.
    #
    # The REWARD is walking distance, which is what makes a peninsula
    # genuinely far even when it is close as the crow flies - the reason
    # this project moved to geodesic distances in the first place.
    k = len(picks_yx)
    walk_seps, line_seps = [], []
    for i in range(k):
        for j in range(i + 1, k):
            y, x = picks_yx[j]
            d = paths[i][y, x]
            line = math.hypot(picks_yx[i][0] - y, picks_yx[i][1] - x)
            line_seps.append(line)
            # Unreachable (or past the search cap) falls back to the
            # straight-line distance rather than to "infinitely far".
            walk_seps.append(d if np.isfinite(d) else line)
    min_walk, min_line = min(walk_seps), min(line_seps)
    sep_score = min(min_walk / (2.0 * min_separation), 1.0)
    sep_penalty = max(0.0, (min_separation - min_line) / min_separation)

    # The floor is weighted well above the land term on purpose: no amount
    # of elbow room compensates for spawning next to a neighbour.
    return -(1.0 * land_score + 0.8 * sep_score) + 3.0 * sep_penalty


def _anneal_starts(
    mask: np.ndarray, coords: np.ndarray,
    players: int, init_pick: list[int], min_separation: float,
    iters: int, rng: np.random.Generator,
) -> tuple[list[int], float]:
    """Simulated annealing over which candidate each of the ``players`` picks
    occupies, directly minimizing ``_score_starts``.

    Needed because the four objectives it balances can't be reached by any
    single greedy pass or geometric proxy - farthest-point growth and Lloyd
    relaxation each optimize something that isn't quite the goal (see
    ``_score_starts``). Proposing a random candidate swap and scoring it
    against the real objective lets the search escape a local optimum that a
    proxy-based method gets stuck in: annealing accepts an occasional
    worse-scoring move early on (``temp`` starts high, cools linearly)
    specifically so a swap that looks locally bad but opens up an unclaimed
    landmass isn't rejected before its benefit shows up in later moves.
    Proposals target the objective on both ends: the pick chosen for a move
    is usually the one with the LEAST available land (the term the loss
    weights most heavily), and the destination is usually a tile far from
    every current pick. A random fallback on each keeps the search from
    tunnelling on one player.
    """
    n = len(coords)
    # Distances past twice the separation floor cannot change the score:
    # sep_score saturates there and the floor penalty only looks below
    # min_separation. Capping the wavefront there makes each proposal ~3x
    # cheaper with identical results - and this loop runs thousands of times
    # per region.
    cap = 2.0 * min_separation
    picks = list(init_pick)
    picks_yx = [(int(coords[i][0]), int(coords[i][1])) for i in picks]
    paths = [land_path_distance(mask, p, cap) for p in picks_yx]
    cur_loss = _score_starts(picks_yx, paths, min_separation)
    best_picks, best_loss = list(picks), cur_loss

    cxs, cys = coords[:, 1].astype(int), coords[:, 0].astype(int)
    for step in range(iters):
        temp = max(1e-6, 0.3 * (1.0 - step / iters))
        if rng.random() < 0.6:
            # Move the most cramped player - the loss is dominated by the
            # worst-off pick, so that is where an improvement can come from.
            slot = int(np.argmin([float((p <= _LAND_RADIUS).sum()) for p in paths]))
        else:
            slot = int(rng.integers(0, players))
        if rng.random() < 0.6:
            min_dist = np.min(np.stack(paths), axis=0)
            cand_dist = min_dist[cys, cxs]
            cand_dist = np.where(np.isfinite(cand_dist), cand_dist, 0.0)
            w = cand_dist ** 2 + 1e-6
            new_idx = int(rng.choice(n, p=w / w.sum()))
        else:
            new_idx = int(rng.integers(0, n))
        if new_idx == picks[slot]:
            continue

        trial_yx = list(picks_yx)
        trial_yx[slot] = (int(coords[new_idx][0]), int(coords[new_idx][1]))
        trial_paths = list(paths)
        trial_paths[slot] = land_path_distance(mask, trial_yx[slot], cap)
        loss = _score_starts(trial_yx, trial_paths, min_separation)

        if loss < cur_loss or rng.random() < math.exp(-(loss - cur_loss) / temp):
            picks[slot] = new_idx
            picks_yx, paths, cur_loss = trial_yx, trial_paths, loss
            if loss < best_loss:
                best_loss, best_picks = loss, list(picks)

    return best_picks, best_loss


def _multistart_anneal(
    mask: np.ndarray, coords: np.ndarray,
    quality: np.ndarray, players: int, labels: np.ndarray,
    min_separation: float,
    n_seeds: int = 3, iters: int = 150, seed: int = 12345,
) -> list[int]:
    """Run ``_anneal_starts`` from several different initializations - the
    same quality-ranked geodesic farthest-point packs and forced-per-
    component seed used previously - and keep whichever converges to the
    lowest ``_score_starts`` loss.

    Multiple starts still matter even with a real objective to hillclimb:
    annealing isn't guaranteed to escape every local optimum in a fixed
    iteration budget, so starting from a few structurally different layouts
    (already-spread-out vs. one-guaranteed-seed-per-island) makes it far less
    likely every attempt lands in the same one. Uses a fixed RNG seed so
    ``update_mod.py`` reruns produce the same script byte-for-byte.
    """
    if len(coords) == 0:
        return []
    if players == 1 or len(coords) == 1:
        return [int(np.argmax(quality))]

    ys, xs = coords[:, 0].astype(int), coords[:, 1].astype(int)
    comp_ids = labels[ys, xs]

    rng = np.random.default_rng(seed)
    inits = [_farthest_point_pack_from_seed_geodesic(mask, coords, players, int(s))
             for s in np.argsort(-quality)[:n_seeds]]
    if len(np.unique(comp_ids)) > 1:
        inits.append(_forced_component_seeds_geodesic(mask, coords, quality, players, comp_ids))

    best_pick, best_loss = None, float("inf")
    for init in inits:
        if len(init) < players:
            continue
        picks, loss = _anneal_starts(
            mask, coords, players, init, min_separation, iters, rng,
        )
        if loss < best_loss:
            best_loss, best_pick = loss, picks
    if best_pick is None:
        # Every seed init came up short of `players` distinct picks (rare) -
        # fall back to the plain farthest-point pack, unrelaxed.
        best_pick = _farthest_point_pack(coords, quality, players)
    return best_pick


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
    or its other islands, if the candidate pool spans more than one. (It can
    also push too FAR toward an extremity - see ``_multistart_anneal``, which
    ``choose_starts`` optionally runs afterward to pull an over-extreme pack
    back toward genuinely representative positions.)

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
    spread_starts: bool = False,
    anneal: bool = True,
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
    candidates CAN spread across separate islands - but by default don't
    unless farthest-point selection happens to reach them on its own, which
    on a window with one huge elongated landmass plus much smaller separate
    ones (e.g. Italy's mainland plus Sardinia/Corsica) it typically doesn't:
    farthest-point selection can seat every start along the big landmass's
    own far ends before ever needing a smaller island - or, just as easily,
    push too far the OTHER way, wandering to a remote corner of the big
    landmass itself well past the place the window was built to depict (this
    happens on Italy specifically because it's *really* land-connected to
    France and the Balkans, not a raster artifact - so its "mainland"
    component genuinely includes all three, and plain farthest-point growth
    has no reason to prefer the actual Italian peninsula over French or
    Balkan territory within that same component).

    ``spread_starts=True`` addresses both by handing the ordinary
    farthest-point pack below to ``_multistart_anneal``, which runs simulated
    annealing directly against ``_score_starts`` - a real scalar objective
    combining coverage (how much qualifying land sits farther than
    ``_COVERAGE_RADIUS`` tiles from every pick), a hard-ish floor on every
    pairwise separation (not just the worst one), spread uniformity, and
    distance-to-water. An earlier version of this used Lloyd relaxation
    (iteratively reassigning candidates to their nearest pick and
    re-centering each pick at the quality-weighted centroid of its own
    cluster) instead - rejected after it kept re-centering multiple picks
    toward the same high-quality region (Italy's Po valley) even from
    initializations that started elsewhere, silently leaving the Italian
    peninsula with zero picks while seating two others 16 tiles apart on the
    mainland; its duplicate-seat scoring only penalized duplicates on a
    non-largest component, so that mainland pair scored as fine. Optimizing
    the real objective directly (not a geometric proxy for it) is what fixes
    both failures at once - see ``_score_starts``. Left off by default so
    this doesn't silently change already-verified start placements on every
    existing region that happens to have more than one qualifying landmass
    (Salish Sea, Britain, Japan, Caribbean, New Zealand, Greece all do) -
    it's meant to be opted into per-region, e.g. for an uncrowded "Italy"
    variant, not applied blanket.

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
        #
        # Under spread_starts the floor is forced to 0 (i.e. skipped, modulo
        # the min_inland/edge_margin/min_component_tiles filters already baked
        # into `valid`) regardless of the requested percentile: confirmed on
        # Italy that even a *per-landmass* floor can wash out a genuinely
        # distant, lower-quality sub-region of an oversized multi-region
        # component. The mainland component here spans the Po valley/France/
        # Balkans plains (quality saturates near 1.0 over thousands of tiles)
        # *and* the Apennine peninsula (real, playable land, but coastal on
        # both sides so quality tops out around 0.89) - a 60th-percentile
        # floor computed over that whole component lands above 0.95, at which
        # point every peninsula tile is already gone before the candidate
        # pool is even built, let alone downsampled. spread_starts exists
        # specifically to admit real-but-lower-quality sub-regions, so it
        # relies on _spatial_stratified_top's per-cell selection (using
        # quality only as an in-cell tie-break) to keep the candidate count
        # bounded instead.
        floor_percentile = 0.0 if spread_starts else percentile
        cand = np.zeros_like(valid)
        for lbl in np.unique(labels[valid]):
            comp = valid & (labels == lbl)
            floor = float(np.percentile(quality[comp], floor_percentile))
            cand |= comp & (quality >= floor)
        if cand.sum() < players:
            cand = valid
        ys, xs = np.nonzero(cand)
        q = quality[ys, xs]
        comp_ids = labels[ys, xs]
        if len(ys) > max_candidates:
            if spread_starts:
                # Per-component, not one global top-K by quality: a global cut
                # keeps whichever component has the most raw land (mainland
                # candidates always outnumber a small island's), which can
                # prune every single island candidate before farthest-point
                # selection ever sees them - silently defeating
                # same_component=False for archipelagos. Gated behind
                # spread_starts (rather than applied unconditionally) because
                # this alone measurably changes which candidates even exist
                # for the *unmodified* farthest-point search below to find on
                # every already-shipped multi-landmass region (Salish Sea,
                # Britain, Japan, Caribbean, New Zealand, Greece), not just
                # Italy - the whole point of spread_starts=False is to
                # reproduce that already-verified behavior exactly.
                comps_present = np.unique(comp_ids)
                per_comp_budget = max(1, max_candidates // len(comps_present))
                keep_parts = []
                for c in comps_present:
                    idx = np.nonzero(comp_ids == c)[0]
                    if len(idx) > per_comp_budget:
                        # Spatially stratified, not pure top-K-by-quality: a
                        # component spanning a huge, quality-saturated plain
                        # next to a smaller, hillier sub-region (e.g. Italy's
                        # mainland, whose Po valley/France/Balkans plains tie
                        # at quality==1.0 in numbers far exceeding the budget)
                        # would otherwise fill the whole budget from the
                        # plain alone, leaving the sub-region with zero
                        # candidates - see _spatial_stratified_top.
                        idx = idx[_spatial_stratified_top(ys[idx], xs[idx], q[idx], per_comp_budget)]
                    keep_parts.append(idx)
                keep = np.concatenate(keep_parts)
            else:
                keep = np.argpartition(-q, max_candidates)[:max_candidates]
            ys, xs, q, comp_ids = ys[keep], xs[keep], q[keep], comp_ids[keep]
        return np.stack([ys, xs], axis=1).astype(float), q, comp_ids

    coords, q, comp_ids = candidates_at(quality_percentile)

    if players <= 1:
        best = int(np.argmax(q))
        return [(int(coords[best][0]), int(coords[best][1]))]

    best_pick = _farthest_point_pack(coords, q, players)
    best_coords, best_q = coords, q
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
            c2, q2, comp2 = candidates_at(percentile)
            pick2 = _farthest_point_pack(c2, q2, players)
            sep2 = _min_pairwise_dist(c2, pick2)
            if sep2 > best_sep:
                best_pick, best_coords, best_q, best_sep = pick2, c2, q2, sep2
            if best_sep >= min_separation:
                break

    # Anneal on every region, not only under spread_starts. The objective
    # (separation + available land, see _score_starts) is what makes a start
    # layout good on ANY coastline; farthest-point packing above is now only
    # an initialization for it. Previously this ran only for spread_starts,
    # which left every other region on raw farthest-point selection - and
    # that is what seats a player on a scrap of land, measured at N=10 as
    # the single strongest predictor of a broken start.
    # ``anneal=False`` leaves the raw farthest-point pack, which is what every
    # region except spread_starts used before. Kept so old and new placement
    # can be measured against each other on the same mask.
    if anneal:
        best_pick = _multistart_anneal(
            mask, best_coords, best_q, players, labels, min_separation,
        )

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
