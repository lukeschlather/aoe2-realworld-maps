"""Per-player start quality, measured from a real engine capture.

Three ideas, each replacing something weaker.

**1. Distance, not just counts.** Two players owning the same eight gold
tiles have very different games if one's gold is 12 tiles from the town
centre and the other's is 38. Distance is the quantity counts were standing
in for.

**2. Contested, not owned.** Assigning every resource to its single nearest
town centre is wrong on exactly the maps this project makes. On a tight
region like Britain, most of what a player can reach is equally reachable
by a neighbour - calling it that player's property overstates what they
actually have, and it forced an arbitrary tie-break (the old rule broke
exact ties by player index, which caused a real mis-attribution in the
Italy data). Every resource is now **exclusive**, **contested** or
**unclaimed**, and contested resources count for *both* players, because
both can genuinely go and take them.

**3. Forest is measured the same way, not by radius.** Wood used to be
counted in a straight-line disc around the town centre, which claimed
forest across water, forest behind another player, and forest nobody is
anywhere near. That last category is the interesting one: on Britain the
wood that looks "unfair" is largely in France and the Scandinavian corner,
where no player starts at all. Classifying forest tiles by the same
exclusive/contested/unclaimed rule makes that visible instead of smearing
it across whichever players happen to be closest.

Deliberately NOT a verdict, per ``CLAUDE.md``. Everything here is a
measured fact for a human to judge, with one exception: a player who can
reach *no* instance of a resource kind - not even a contested one - is
reported as an unambiguous problem, because there is nothing left to judge.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.ndimage import minimum_filter

from . import scx_read
from . import terrain as T
from .analysis import land_path_distance

#: Land resource kinds every player is expected to be able to reach. This
#: is the set the zero-of-a-kind check applies to. ``small_game`` is
#: excluded - plenty of good maps place none, so a zero there says nothing.
LAND_KINDS = ("gold", "stone", "forage", "sheep", "deer", "boar")

#: Reported and counted, but never zero-checked.
EXTRA_LAND_KINDS = ("small_game",)

WATER_KINDS = ("shore_fish", "deep_fish", "whale")

#: How far a villager will realistically walk for a resource.
OWNERSHIP_RADIUS = 30.0

#: A resource is contested when a second player can also reach it and is
#: within this many tiles of the nearest player's walking distance. Eight
#: tiles is a few seconds of villager walking - well inside "we will both
#: fight over this" and far outside "this is unambiguously mine".
CONTEST_MARGIN = 8.0

#: Radii the wood measurement reports at. 10 is "in the town centre's lap";
#: 20 is the forest actually being chopped in feudal.
WOOD_RADII = (10, 20)

#: Straight-line radius counted as dock-reachable water food.
WATER_RADIUS = 20.0


def _distance_stack(walk: np.ndarray, tcs) -> tuple[list[int], np.ndarray]:
    """(player ids, ``(n_players, H, W)`` walking-distance field)."""
    players = [p for p, _, _ in tcs]
    stack = np.stack([land_path_distance(walk, (int(y), int(x))) for _, x, y in tcs])
    return players, stack


def _classify(stack: np.ndarray, ys: np.ndarray, xs: np.ndarray,
              radius: float = OWNERSHIP_RADIUS, margin: float = CONTEST_MARGIN):
    """Label each point exclusive / contested / unclaimed.

    Returns ``(nearest_idx, runner_up_idx, claimed, contested)`` as arrays
    over the points, where the indices are rows of ``stack``. A contested
    point counts for BOTH ``nearest_idx`` and ``runner_up_idx``.
    """
    if ys.size == 0:
        empty_i = np.zeros(0, dtype=int)
        empty_b = np.zeros(0, dtype=bool)
        return empty_i, empty_i, empty_b, empty_b

    vals = stack[:, ys, xs]  # (n_players, n_points)
    if vals.shape[0] == 1:
        d1 = vals[0]
        nearest = np.zeros(d1.shape, dtype=int)
        claimed = d1 <= radius
        return nearest, nearest, claimed, np.zeros(d1.shape, dtype=bool)

    order = np.argsort(vals, axis=0, kind="stable")
    nearest, runner_up = order[0], order[1]
    d1 = np.take_along_axis(vals, order[0:1], axis=0)[0]
    d2 = np.take_along_axis(vals, order[1:2], axis=0)[0]

    claimed = d1 <= radius
    contested = claimed & (d2 <= radius) & ((d2 - d1) <= margin)
    return nearest, runner_up, claimed, contested


def _euclid_nearest(items, kind: str, tx: float, ty: float) -> tuple[float, int]:
    """Straight-line distance to the nearest ``kind`` and the count within
    ``WATER_RADIUS``. Straight-line because fish sit in water - there is no
    land path to them, and what matters is whether a dock on the player's
    own shore can work them."""
    best, n = math.inf, 0
    for k, x, y in items:
        if k != kind:
            continue
        d = math.hypot(x - tx, y - ty)
        best = min(best, d)
        if d <= WATER_RADIUS:
            n += 1
    return best, n


def profile_capture(path: str | Path) -> dict:
    """Measure every player's start from one captured ``.aoe2scenario``."""
    cap = scx_read.read_capture(path)
    walk = cap.walkable_mask
    tcs = cap.town_centers
    forest = cap.forest_mask
    all_kinds = LAND_KINDS + EXTRA_LAND_KINDS

    if not tcs:
        return {"n_players": 0, "per_player": {}, "spread": {},
                "unclaimed": {}, "zero_kinds_by_player": {}, "forest": {}}

    players, stack = _distance_stack(walk, tcs)
    index_of = {p: i for i, p in enumerate(players)}
    n_dry_land = int(cap.dry_land_mask.sum())
    land_stats = _land_ownership(stack, players, cap)

    exclusive = {p: dict.fromkeys(all_kinds, 0) for p in players}
    contested_n = {p: dict.fromkeys(all_kinds, 0) for p in players}
    unclaimed = dict.fromkeys(all_kinds, 0)

    for kind in all_kinds:
        pts = [(x, y) for k, x, y in cap.resources if k == kind]
        if not pts:
            continue
        xs = np.array([int(x) for x, _ in pts])
        ys = np.array([int(y) for _, y in pts])
        near, runner, claimed, contest = _classify(stack, ys, xs)
        for i in range(len(xs)):
            if not claimed[i]:
                unclaimed[kind] += 1
            elif contest[i]:
                contested_n[players[near[i]]][kind] += 1
                contested_n[players[runner[i]]][kind] += 1
            else:
                exclusive[players[near[i]]][kind] += 1

    forest_stats = _forest_ownership(stack, players, forest)
    # How wooded the region is overall, as a share of its dry land. This is
    # the number that separates "a small region legitimately carries less
    # wood" (fine - flavor) from "this region is smothered in trees"
    # (a generation setting, not geography). Stock reference points:
    # Thames ~0.06, Arabia ~0.14, Yucatan ~0.17.
    forest_stats["totals"]["share_of_land"] = (
        round(forest_stats["totals"]["total"] / n_dry_land, 3) if n_dry_land else None
    )

    per_player: dict[int, dict] = {}
    for player, tx, ty in tcs:
        dist = stack[index_of[player]]

        nearest = {}
        for kind in all_kinds:
            best = math.inf
            for k, x, y in cap.resources:
                if k != kind:
                    continue
                d = float(dist[int(y), int(x)])
                if d < best:
                    best = d
            nearest[kind] = None if math.isinf(best) else round(best, 1)

        wood = dict(forest_stats["per_player"][player])
        for r in WOOD_RADII:
            wood[f"open_tiles_within_{r}"] = int((np.isfinite(dist) & (dist <= r)).sum())
        wood["stragglers_within_6"] = sum(
            1 for _, x, y in cap.trees if math.hypot(x - tx, y - ty) <= 6.0
        )

        water = {}
        for kind in WATER_KINDS:
            near_d, n = _euclid_nearest(cap.water_resources, kind, tx, ty)
            water[f"nearest_{kind}"] = None if math.isinf(near_d) else round(near_d, 1)
            water[f"{kind}_within_{int(WATER_RADIUS)}"] = n

        per_player[player] = {
            "exclusive": exclusive[player],
            "contested": contested_n[player],
            # What the player can actually go and take. Contested resources
            # are included because both players genuinely can - this is the
            # number the zero-of-a-kind check uses.
            "counts": {k: exclusive[player][k] + contested_n[player][k]
                       for k in all_kinds},
            "nearest": nearest,
            "wood": wood,
            "water": water,
            "land": land_stats["per_player"][player],
        }

    return {
        "n_players": len(tcs),
        "per_player": {str(p): v for p, v in per_player.items()},
        # Resources no player can reach. Despite the name this is mostly a
        # GOOD thing and was being read as waste: it is the neutral, out-on-
        # the-map supply players have to leave home and fight over. Stock
        # maps carry a lot of it - Thames places more neutral forage (126)
        # and deer (99) than it gives every player put together - and a map
        # with none has nothing to contest.
        "unclaimed": unclaimed,
        "neutral_total": sum(unclaimed.values()),
        "forest": forest_stats["totals"],
        "land": land_stats["totals"],
        "spread": _spread(per_player, all_kinds),
        "zero_kinds_by_player": {
            str(p): [k for k in LAND_KINDS if v["counts"].get(k, 0) == 0]
            for p, v in per_player.items()
            if any(v["counts"].get(k, 0) == 0 for k in LAND_KINDS)
        },
    }


def _land_ownership(stack: np.ndarray, players: list[int], cap) -> dict:
    """Split buildable land into exclusive / contested / unclaimed.

    Land is a resource and was the one this module did not count. It is
    also the only one that cannot be topped up: a player can walk further
    for gold, but not for somewhere to put a farm.

    "Buildable" is dry and unforested - shallows are excluded because a
    ford is a route, not ground you can build on, and forest is excluded
    because it is wood you have to clear before it is land. Distances are
    still walked on the walkable mask, so a ford is a route.

    Same exclusive/contested/unclaimed rule as everything else here, so a
    tile two players can both reach counts for both.
    """
    buildable = ~np.isin(cap.terrain, list(T.WATER_IDS)) & ~cap.forest_mask
    ys, xs = np.nonzero(buildable)
    per_player = {p: {"land_exclusive": 0, "land_contested": 0} for p in players}
    if ys.size == 0:
        return {"per_player": per_player,
                "totals": {"exclusive": 0, "contested": 0, "unclaimed": 0,
                           "total": 0}}

    near, runner, claimed, contest = _classify(stack, ys, xs)
    excl = dict.fromkeys(players, 0)
    cont = dict.fromkeys(players, 0)
    n_unclaimed = 0
    for i in range(ys.size):
        if not claimed[i]:
            n_unclaimed += 1
        elif contest[i]:
            cont[players[near[i]]] += 1
            cont[players[runner[i]]] += 1
        else:
            excl[players[near[i]]] += 1

    for p in players:
        per_player[p] = {"land_exclusive": excl[p], "land_contested": cont[p]}
    return {
        "per_player": per_player,
        "totals": {
            "exclusive": int(sum(excl.values())),
            # halved: each contested tile was counted once per player
            "contested": int(sum(cont.values()) // 2),
            "unclaimed": int(n_unclaimed),
            "total": int(ys.size),
        },
    }


def _forest_ownership(stack: np.ndarray, players: list[int],
                      forest: np.ndarray) -> dict:
    """Split forest tiles into exclusive / contested / unclaimed wood.

    Forest is impassable, so no walking-distance field ever reaches *into*
    it. What matters is being able to stand next to a tree and chop it, so
    a forest tile's distance is the smallest distance of its 8 neighbours -
    a 3x3 minimum filter over each player's distance field.

    The ``unclaimed`` total is the interesting one for these maps: wood
    sitting in a part of the map no player starts near (France and the
    Scandinavian corner on Britain). Counting that as somebody's wood, which
    a plain radius measurement does, is what made Britain's wood look
    lopsided rather than simply distant.
    """
    ys, xs = np.nonzero(forest)
    per_player = {p: {} for p in players}
    if ys.size == 0:
        for p in players:
            per_player[p] = {"forest_exclusive": 0, "forest_contested": 0}
        return {"per_player": per_player,
                "totals": {"exclusive": 0, "contested": 0, "unclaimed": 0, "total": 0}}

    # Distance to a tile one may stand on adjacent to each forest tile.
    reach = np.stack([minimum_filter(d, size=3, mode="nearest") for d in stack])
    near, runner, claimed, contest = _classify(reach, ys, xs)

    excl = dict.fromkeys(players, 0)
    cont = dict.fromkeys(players, 0)
    n_unclaimed = 0
    for i in range(ys.size):
        if not claimed[i]:
            n_unclaimed += 1
        elif contest[i]:
            cont[players[near[i]]] += 1
            cont[players[runner[i]]] += 1
        else:
            excl[players[near[i]]] += 1

    for p in players:
        per_player[p] = {"forest_exclusive": excl[p], "forest_contested": cont[p]}
    return {
        "per_player": per_player,
        "totals": {
            "exclusive": int(sum(excl.values())),
            # halved: each contested tile was counted once per player
            "contested": int(sum(cont.values()) // 2),
            "unclaimed": int(n_unclaimed),
            "total": int(ys.size),
        },
    }


def _spread(per_player: dict[int, dict], kinds: tuple[str, ...]) -> dict:
    """How unequal the players' starts are, per kind. Neither end is
    labelled good or bad here."""
    out: dict[str, dict] = {}
    for kind in kinds:
        counts = [v["counts"].get(kind, 0) for v in per_player.values()]
        excl = [v["exclusive"].get(kind, 0) for v in per_player.values()]
        dists = [v["nearest"].get(kind) for v in per_player.values()]
        finite = [d for d in dists if d is not None]
        entry = {
            "count_min": min(counts) if counts else None,
            "count_max": max(counts) if counts else None,
            "count_mean": round(float(np.mean(counts)), 2) if counts else None,
            "exclusive_min": min(excl) if excl else None,
            "exclusive_max": max(excl) if excl else None,
            "n_players_without_any": sum(1 for c in counts if c == 0),
            "n_players_unreachable": sum(1 for d in dists if d is None),
        }
        if finite:
            entry.update({
                "nearest_min": min(finite),
                "nearest_max": max(finite),
                "nearest_mean": round(float(np.mean(finite)), 1),
                "nearest_range": round(max(finite) - min(finite), 1),
            })
        out[kind] = entry
    return out
