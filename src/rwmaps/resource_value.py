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

Provenance, because these numbers are not all equally solid
-----------------------------------------------------------

The authoritative source is ``resources/_common/dat/empires2_x2_p1.dat``,
the binary genie dat, and this project has no parser for it. Three other
places were checked on 2026-08-16:

* **A saved scenario does not store amounts.** The on-disk ``UnitStruct``
  is exactly ``x, y, z, reference_id, unit_const, status, rotation,
  initial_animation_frame, garrisoned_in_id, caption_string_id,
  caption_string`` (AoE2ScenarioParser's DE v1.58 structure). There is no
  amount field, so a capture cannot be asked what a gold mine was worth,
  and a map that alters amounts is indistinguishable from one that does
  not once saved. Do not go looking again.
* **Mods alter amounts at generation time**, via
  ``effect_amount GAIA_SET_ATTRIBUTE <unit> ATTR_STORAGE_VALUE <n>``, which
  rewrites the running game's unit-type table rather than any per-object
  data. Zetnus HyperRandom does exactly this - and because it has to name
  the value, its script IS a readable source for a couple of them.
* **The localisation strings** carry no per-object amounts.

Then on 2026-08-16 the user read five of them straight off a real RW
Britain generation in game, which beats every source above: gold 800,
stone 350, berries 125, deer 140, boar 340. That settles the ones that
matter - the two currencies maps actually differ on, and the best food.
Note it also settles boar at 340 rather than the 300 some public sources
give.

Each entry below is tagged with where it comes from. ``CONFIRMED`` means
observed in game or read out of a script that sets it explicitly;
``ASSUMED`` means the commonly documented DE value and nothing stronger.

Every report that uses these shows the **object counts alongside**, and
those counts are ground truth read straight from the capture. Treat the
assumed amounts as a convenience for scale, correct them when better
numbers turn up, and do not let a decision rest on a few percent.
"""

from __future__ import annotations

FOOD = "food"
GOLD = "gold"
STONE = "stone"
WOOD = "wood"

#: What one forest tile is worth. CONFIRMED, near enough: Zetnus
#: HyperRandom randomises every tree type it touches over ``rnd(50,150)``,
#: symmetric about 100, and the one it narrows (``REEDTREE``) it gives
#: ``rnd(25,75)``, symmetric about 50. A randomiser centres on the value it
#: is perturbing.
#:
#: Worth having because wood is the resource the late game actually turns
#: on - past the point where farms take over, food comes from wood - and
#: forest tiles are already counted per player by ``rwmaps.fairness``.
WOOD_PER_FOREST_TILE = 100

#: role -> (currency, amount carried by one object).
#:
#: Where a role has several skins with different values, the typical skin's
#: value is used and the spread is noted - a capture records the role, not
#: the skin, so there is nothing finer to key on.
RESOURCE_AMOUNTS: dict[str, tuple[str, int]] = {
    # One gold mine tile. CONFIRMED in game, RW Britain, 2026-08-16.
    "gold": (GOLD, 800),
    # One stone mine tile. CONFIRMED in game, RW Britain, 2026-08-16.
    "stone": (STONE, 350),
    # FORAGE_PLANT. Slow to gather but safe and close.
    # CONFIRMED in game, RW Britain, 2026-08-16.
    "forage": (FOOD, 125),
    # HERDABLE_A. Walks to you; no gather-rate penalty. ASSUMED - one of
    # the two land amounts still unconfirmed.
    "sheep": (FOOD, 100),
    # HUNTABLE_A/B. Flees, so it needs chasing or a mill.
    # CONFIRMED: HyperRandom sets both `DEER` and `DLC_ZEBRA` to 140, and
    # zebra is one of the nine HUNTABLE_A skins - so the whole role is 140,
    # not just the classic deer.
    "deer": (FOOD, 140),
    # LUREABLE_A. The most valuable food on the map per object AND per
    # second, because it is lured to the town centre and gathered there.
    # CONFIRMED in game, RW Britain, 2026-08-16 - so 340, not the 300 some
    # public sources give. Skins vary: wild boar and javelina alike,
    # elephant and rhinoceros carry more.
    "boar": (FOOD, 340),
    # HUNTABLE_SMALL_A/B - wild chickens and the arctic hare. ASSUMED, and
    # the least certain number here by some way.
    "small_game": (FOOD, 30),
}

#: Water food. All ASSUMED, and separate because it needs a dock and ships
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


def wallet(counts: dict[str, int], forest_tiles: int = 0) -> dict[str, int]:
    """Food / gold / stone / wood represented by a bag of object counts.

    ``counts`` is a role -> number mapping, e.g. one player's ``counts``
    block out of :func:`rwmaps.fairness.profile_capture`. ``forest_tiles``
    is that player's reachable forest (exclusive + contested), which the
    same function already reports.
    """
    out = {FOOD: 0, GOLD: 0, STONE: 0, WOOD: forest_tiles * WOOD_PER_FOREST_TILE}
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
