"""Per-player start quality, measured from a real engine capture.

This replaces "count how many of each resource a player owns" as the only
question the project asks about fairness. Counts alone are a weak signal:
two players can own the same eight gold tiles and have completely different
games if one player's gold is 12 tiles from the town centre and the other's
is 38. **Distance is the fairness quantity that counts is a proxy for**, and
it is also the quantity that is tie-immune - ``resource_ownership`` breaks
an exact distance tie by player index, which produced a real (if secondary)
mis-attribution in this project's Italy data.

So every kind gets both: how many the player owns, and how far they must
walk to the nearest one.

Deliberately NOT a verdict, per ``CLAUDE.md``. Everything here is a
measured fact for a human to judge, with exactly one exception carried over
from ``sample_analysis``: a player having literally zero of a resource kind
is reported as an unambiguous problem, because there is nothing to judge -
that player cannot mine gold at all.

Three things are measured here that nothing in this project measured
before, each flagged as a gap by ``RESOURCE_TEMPLATES.md``:

* **Wood.** Forest *terrain* is what carries wood, and straggler trees at
  the town centre are what a player chops in the first minutes. The
  document calls the missing stragglers "the biggest single gap"; until now
  the metric could not see either.
* **Water food.** Every map this project generates is a coastline. Shore
  fish are a real economy, and a player with none is worse off in a way
  land counts do not show.
* **Distance, at all.**
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import scx_read
from . import terrain as T
from .analysis import land_path_distance

#: Land resource kinds every player is expected to have some of. This is
#: the set the zero-of-a-kind check applies to. ``small_game`` is
#: deliberately excluded - plenty of perfectly good maps place none, so a
#: zero there says nothing.
LAND_KINDS = ("gold", "stone", "forage", "sheep", "deer", "boar")

#: Reported and counted, but never zero-checked.
EXTRA_LAND_KINDS = ("small_game",)

WATER_KINDS = ("shore_fish", "deep_fish", "whale")

#: How far a villager will realistically walk for a resource. Matches the
#: existing ``resource_ownership`` default so ownership counts stay
#: comparable with previously captured data.
OWNERSHIP_RADIUS = 30.0

#: Radii the wood measurement reports at. 10 is "in the town centre's lap"
#: (straggler range); 20 is "the forest you will actually be chopping in
#: feudal".
WOOD_RADII = (10, 20)

#: Straight-line radius counted as dock-reachable water food.
WATER_RADIUS = 20.0


def _nearest(dist: np.ndarray, items: list[tuple[str, float, float]], kind: str) -> float:
    """Walking distance to the nearest instance of ``kind``, or inf."""
    best = math.inf
    for k, x, y in items:
        if k != kind:
            continue
        d = float(dist[int(y), int(x)])
        if d < best:
            best = d
    return best


def _euclid_nearest(items: list[tuple[str, float, float]], kind: str,
                    tx: float, ty: float) -> tuple[float, int]:
    """Straight-line distance to nearest ``kind`` and count within
    ``WATER_RADIUS``. Straight-line, not walking, because fish sit in water
    - there is no land path to them, and what matters is whether a dock
    placed on the player's own shore can work them."""
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
    land_res = cap.resources
    water_res = cap.water_resources
    trees = cap.trees

    forest = cap.forest_mask
    dry_land = cap.dry_land_mask

    all_kinds = LAND_KINDS + EXTRA_LAND_KINDS

    # Walking-distance field per player, over WALKABLE tiles (shallows are
    # fords, not walls - see read_walkable_mask).
    dists = {p: land_path_distance(walk, (int(y), int(x))) for p, x, y in tcs}

    # Ownership: nearest reachable TC wins, same rule as
    # analysis.resource_ownership, recomputed here against the walkable
    # mask so it agrees with the distances reported alongside it.
    owned: dict[int, dict[str, int]] = {p: dict.fromkeys(all_kinds, 0) for p in dists}
    unclaimed: dict[str, int] = dict.fromkeys(all_kinds, 0)
    for kind, x, y in land_res:
        best_p, best_d = None, math.inf
        for p, dist in dists.items():
            d = float(dist[int(y), int(x)])
            if d < best_d:
                best_d, best_p = d, p
        if best_p is not None and best_d <= OWNERSHIP_RADIUS:
            owned[best_p][kind] += 1
        else:
            unclaimed[kind] = unclaimed.get(kind, 0) + 1

    per_player: dict[int, dict] = {}
    for player, tx, ty in tcs:
        dist = dists[player]
        yi, xi = int(ty), int(tx)

        # Wood is measured as a *proportion* and as *openness*, not as an
        # absolute count. A region like Britain will legitimately carry less
        # wood than Arabia simply because it has less land, and that is
        # flavor rather than a defect. The two things that do matter are
        # whether a player has a reasonable share of what land they have,
        # and whether they are walled in by it.
        #
        # Forest is impassable, so `dist` (a walk over walkable tiles) stops
        # at the tree line - which is exactly what makes "walled in"
        # measurable: open_tiles_within_N counts the tiles this player can
        # actually reach on foot, and it collapses when a TC is enclosed.
        # Forest tiles are counted by straight-line radius instead, since by
        # construction they are never reachable.
        wood = {}
        yy, xx = np.ogrid[:forest.shape[0], :forest.shape[1]]
        radius2 = (xx - tx) ** 2 + (yy - ty) ** 2
        for r in WOOD_RADII:
            disc = radius2 <= r * r
            n_forest = int((forest & disc).sum())
            n_land = int((dry_land & disc).sum())
            open_reachable = int((np.isfinite(dist) & (dist <= r)).sum())
            wood[f"forest_tiles_within_{r}"] = n_forest
            wood[f"open_tiles_within_{r}"] = open_reachable
            # Share of this player's nearby dry land that is wood. None when
            # there is no dry land at all nearby to take a share of.
            wood[f"forest_fraction_within_{r}"] = (
                round(n_forest / n_land, 2) if n_land else None
            )
        wood["stragglers_within_6"] = sum(
            1 for _, x, y in trees if math.hypot(x - tx, y - ty) <= 6.0
        )

        water = {}
        for kind in WATER_KINDS:
            near, n = _euclid_nearest(water_res, kind, tx, ty)
            water[f"nearest_{kind}"] = None if math.isinf(near) else round(near, 1)
            water[f"{kind}_within_{int(WATER_RADIUS)}"] = n

        nearest = {}
        for kind in all_kinds:
            d = _nearest(dist, land_res, kind)
            nearest[kind] = None if math.isinf(d) else round(d, 1)

        per_player[player] = {
            "counts": owned[player],
            "nearest": nearest,
            "wood": wood,
            "water": water,
        }

    return {
        "n_players": len(tcs),
        "per_player": {str(p): v for p, v in per_player.items()},
        "unclaimed": unclaimed,
        "spread": _spread(per_player, all_kinds),
        # The one thing treated as an unambiguous problem - see module
        # docstring. Uses ownership counts, not distance, so it keeps the
        # same meaning it has in previously captured data.
        "zero_kinds_by_player": {
            str(p): [k for k in LAND_KINDS if v["counts"].get(k, 0) == 0]
            for p, v in per_player.items()
            if any(v["counts"].get(k, 0) == 0 for k in LAND_KINDS)
        },
    }


def _spread(per_player: dict[int, dict], kinds: tuple[str, ...]) -> dict:
    """How unequal the players' starts are, per kind.

    Reported for both counts and nearest-distance. A map where every player
    has 7 gold at ~12 tiles is even; one where the range is 3-14 piles at
    11-38 tiles is not. Neither is labelled good or bad here.
    """
    out: dict[str, dict] = {}
    for kind in kinds:
        counts = [v["counts"].get(kind, 0) for v in per_player.values()]
        dists = [v["nearest"].get(kind) for v in per_player.values()]
        finite = [d for d in dists if d is not None]
        entry = {
            "count_min": min(counts) if counts else None,
            "count_max": max(counts) if counts else None,
            "count_mean": round(float(np.mean(counts)), 2) if counts else None,
            "n_players_without_any": sum(1 for c in counts if c == 0),
            "n_players_unreachable": sum(1 for d in dists if d is None),
        }
        if finite:
            entry.update({
                "nearest_min": min(finite),
                "nearest_max": max(finite),
                "nearest_mean": round(float(np.mean(finite)), 1),
                # Plain max-minus-min in tiles: the most and least
                # convenient start for this resource. Easier to reason
                # about at a glance than a normalised index, and this is
                # meant to be read, not thresholded.
                "nearest_range": round(max(finite) - min(finite), 1),
            })
        out[kind] = entry
    return out
