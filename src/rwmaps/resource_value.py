"""What a resource object is actually worth, so supply can be counted in
resources rather than in objects.

Counting objects answers the wrong question. Six berry bushes and six gold
mines are both "6", and they are not remotely the same thing; nor are a
boar and a sheep, which differ by more than three to one. What a player
has is an amount of food, an amount of gold and an amount of stone within
walking range, and that is what this converts a capture into.

Two things this deliberately does NOT do:

**It does not merge the food kinds into one number and stop there.** Food
is broadly fungible in the sense that you need a certain amount of it to
reach the point where farms take over, but the kinds are not
interchangeable in play: boar is the most valuable food on the map because
it gathers fastest when lured to the town centre, deer need chasing or a
mill, sheep walk to you, and berries are slow but safe. So the per-kind
breakdown stays, and the food total is reported *beside* it, never
instead.

**It does not pretend gold and stone are comparable to food.** They are
reported as three separate currencies.

.. warning::

   **The amounts below are provisional.** They are the standard
   Definitive Edition values as commonly documented, and they were NOT
   read out of the game's own data. The authoritative source is
   ``resources/_common/dat/empires2_x2_p1.dat``, which is the binary genie
   dat, and this project has no parser for it. Public sources disagree on
   some entries - wild boar is variously given as 300 and 340 - and the
   small-game and fish numbers are the least certain of the set.

   Every report that uses these shows the **object counts alongside**, and
   those counts are ground truth read straight from the capture. Treat the
   amounts as a convenience for scale, correct this table when better
   numbers are available, and do not let a decision rest on a difference
   of a few percent.
"""

from __future__ import annotations

FOOD = "food"
GOLD = "gold"
STONE = "stone"

#: role -> (currency, amount carried by one object).
#:
#: Where a role has several skins with different values, the typical skin's
#: value is used and the spread is noted - a capture records the role, not
#: the skin, so there is nothing finer to key on.
RESOURCE_AMOUNTS: dict[str, tuple[str, int]] = {
    # One gold mine tile.
    "gold": (GOLD, 800),
    # One stone mine tile.
    "stone": (STONE, 350),
    # FORAGE_PLANT. Slow to gather but safe and close.
    "forage": (FOOD, 125),
    # HERDABLE_A. Walks to you; no gather-rate penalty.
    "sheep": (FOOD, 100),
    # HUNTABLE_A/B. Flees, so it needs chasing or a mill.
    "deer": (FOOD, 140),
    # LUREABLE_A. The most valuable food on the map per object AND per
    # second, because it is lured to the town centre and gathered there.
    # Skins vary: wild boar and javelina alike, elephant and rhinoceros
    # carry more.
    "boar": (FOOD, 340),
    # HUNTABLE_SMALL_A/B - wild chickens and the arctic hare. Minor, and
    # the least certain number here.
    "small_game": (FOOD, 30),
}

#: Water food, same caveat, and separate because it needs a dock and ships
#: rather than villagers on foot.
WATER_AMOUNTS: dict[str, tuple[str, int]] = {
    "shore_fish": (FOOD, 225),
    "deep_fish": (FOOD, 225),
    "whale": (FOOD, 400),
}

#: Food roles in the order a player actually uses them, best first. Boar
#: leads because gather rate, not object value, is what decides the early
#: game.
FOOD_ROLES = ("boar", "deer", "sheep", "forage", "small_game")


def value_of(role: str, count: int) -> tuple[str, int]:
    """``(currency, total)`` for ``count`` objects of ``role``."""
    table = {**RESOURCE_AMOUNTS, **WATER_AMOUNTS}
    if role not in table:
        return (FOOD, 0)
    currency, each = table[role]
    return currency, each * count


def wallet(counts: dict[str, int]) -> dict[str, int]:
    """Total food / gold / stone represented by a bag of object counts.

    ``counts`` is a role -> number mapping, e.g. one player's ``counts``
    block out of :func:`rwmaps.fairness.profile_capture`.
    """
    out = {FOOD: 0, GOLD: 0, STONE: 0}
    for role, n in counts.items():
        currency, total = value_of(role, n)
        if total:
            out[currency] += total
    return out


def food_breakdown(counts: dict[str, int]) -> list[tuple[str, int, int]]:
    """``[(role, objects, food)]`` best-gathering role first.

    Kept as a list rather than collapsed to a single number because which
    food a player has changes how fast they get it, and a food total that
    hides "all of it is berries" is not describing the same start as one
    that hides "two boar".
    """
    rows = []
    for role in FOOD_ROLES:
        n = counts.get(role, 0)
        currency, total = value_of(role, n)
        if currency == FOOD:
            rows.append((role, n, total))
    return rows
