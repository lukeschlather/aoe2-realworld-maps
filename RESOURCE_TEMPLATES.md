# Stock resource-generation templates

Started 2026-08-07 from a specific bug report: sheep sometimes land outside
a player's initial LOS, and Extreme AI looks sluggish early - both
consistent with resource placement not matching what the AI (tuned against
the stock maps) expects. Investigating that led to reading the actual stock
`.rms`/`.inc` files rather than guessing, per this project's usual
verify-against-ground-truth approach (see `CLAUDE.md`).

## Where the stock files live

AoE2:DE Steam install on this machine:
`C:\Program Files (x86)\Steam\steamapps\common\AoE2DE`

- `resources\_common\drs\gamedata_x2\` - the current stock maps themselves
  (`Arabia.rms`, `Islands.rms`, `real_world_britain.rms`, etc.) and, flat in
  the same folder, the *classic*-system includes (`GeneratingObjects.inc`,
  `F_WaterMasking.inc`, `random_map.def`).
- `resources\_common\drs\gamedata_x2\includes\` - the *modern*-system
  includes (`herdable_starting.inc`, `herdable.inc`, `themes.inc`,
  `land_and_water_resources.inc`, `starting_resources.inc`,
  `object_setup.inc`).
- `resources\_common\drs\gamedata_x2.backup.20201109\` - a pre-2021-rework
  snapshot, useful for diffing old vs. modern without needing the game's own
  version control.
- **Not** `resources\_common\random-map-scripts\` - that only holds
  whatever Workshop mods happen to be subscribed on this machine, not stock
  content.

`rwmaps` (`src/rwmaps/rms.py`) currently generates resources via
`#include_drs land_and_water_resources.inc` - confirmed by direct read to be
the old, generic, stable-unit-id include: `SHEEP` (id 594) placed as a
4-object loose group at `min/max_distance_to_players 10/12` plus a 2-object
group at `14/30`, with no `find_closest`, no placement-retry mechanism, and
no `max_distance_to_other_zones` cap. That's the same class of
silent-placement-failure already root-caused for gold/stone on narrow
coastlines (`MOD_STATUS.md`, "Narrow-coastline resource starvation"), just
never previously checked for sheep specifically.

## Two competing systems, still both live in the stock game

Reading actual stock scripts (not docs) turned up two entirely different,
still-coexisting resource-placement systems:

### System A - modern (`includes/herdable_starting.inc` + friends)

Used by ~50 stock maps - confirmed via
`grep -l "includes/starting_resources.inc" *.rms`: `Arabia`, `Arena`,
`Baltic`, `Black_Forest`, `Bogland`, `Coastal`, `CoastalForest`,
`Greenland`, `Isthmus`, `Loch Ness`, `Migration`, `nomad`, `Paradise
Island`, `Scandanavia`, `Shipwreck`, `Team_Islands`, `Yucatan`, … and
**`real_world_manchuria`** - the sole Real World map already ported to
System A. It ships in the UI as **"Great Wall"**; see
`STOCK_MAP_INVENTORY.md` for the full script-name-to-UI-name table (8 of
the 29 diverge). It is a useful reference for *technique* but a poor
wholesale donor - read the "Flavor" section at the end of this document
before copying anything from it.

(The earlier note that only five maps used System A came from grepping
`HERDABLE_STARTING_COUNT_`, which undercounts: sheep are one include among
a dozen, and `starting_resources.inc` is the better marker.)

Sheep guarantee mechanism (`Arabia.rms` sets
`#define HERDABLE_STARTING_COUNT_FOUR`):
- Sheep #1: `find_closest` + `set_circular_placement` +
  `enable_tile_shuffling`, `min_distance_to_players 8`,
  `max_distance_to_other_zones 2` - searches the whole circle around the TC
  and shuffles tiles until it fits, rather than trying one fixed spot and
  giving up.
- Sheep #2-4: `actor_area_to_place_in 500` / `actor_area_radius 0` - dropped
  directly onto sheep #1's spot, so all 4 form one tight herd.

Gold/stone/forage in this system go through `includes/starting_resources.inc`
instead, driven entirely by named `#const` distances
(`GOLD_PRIMARY_DISTANCE 12`, `GOLD_SECONDARY_DISTANCE 22`, presumably a
`GOLD_TERTIARY_DISTANCE`, analogous `FORAGE_BUSH_*`/`STONE_*` families), each
placed as `min_distance_to_players DISTANCE` /
`max_distance_to_players (DISTANCE + 1)` - a narrow fixed ring, not a wide
min/max range. No obvious "confined coastline" preset spotted yet in a
first pass - see open questions below.

### System B - classic (`GeneratingObjects.inc`, `GNR_*` defines)

Used by `Highland.rms`, `Islands.rms`, `Archipelago.rms`,
`Mediterranean.rms`, `Continental.rms`, `Aquarena.rms`, `Bog_Islands.rms`,
`Pacific_Islands.rms`, and - notably - **every stock "Real World" bonus
map** (`real_world_britain.rms`, `real_world_italy.rms`,
`real_world_nippon.rms`, `real_world_caribbean.rms`, etc. - full list of ~29
under `real_world_*.rms`, each paired with a fixed `.scx` for the actual
coastline; the `.rms` only adds terrain variety + resources on top, no
`<LAND_GENERATION>` section at all).

Sheep: always exactly 4, `set_loose_grouping`, `min_distance_to_players 7`,
`max_distance_to_players 10`, `find_closest` - tighter than System A's
floor-8 approach, and constant across every region regardless of land
shape (confirmed: same numbers whether or not `GNR_RESCLOSE` is set, since
that define only affects gold/stone/forage, not sheep).

Gold/stone/forage carry a **resource-tightness tier** selected per map via
defines, unrelated to sheep:

| tier | archetype examples | Real World examples |
|---|---|---|
| default (wide) | `Highland`, `Islands`, `Archipelago`, `Mediterranean` | Australia, China, India, Siberia, Black Sea, Caucasus, Amazon, Bohemia |
| `GNR_RESCLOSE` | `Continental` | Britain, Caribbean, Italy, Nippon (Japan), France, Spain, Byzantium, Jutland, Madagascar, Mideast, Texas |
| `GNR_RESSUPERCLOSE` | `Aquarena`, `Bog_Islands`, `Pacific_Islands` | Indochina, Malacca, Philippines |

**Britain, Caribbean, Italy, and Japan all have exact-name stock
counterparts, and all four independently landed on `GNR_RESCLOSE`** - the
same tightening this project reinvented from scratch as
`--tight-resources` (see `MOD_STATUS.md`). Real, independent validation of
that fix, not a coincidence.

None of the `real_world_*.rms` scripts set `ai_info_map_type` themselves
(checked all ~29; only `real_world_bohemia.rms` does, and only inside a
nomad-start branch) - so "borrow the resource template" does not hand us
the AI map type for free. That stays governed independently by this
project's own `choose_ai_map_type()` (`src/rwmaps/analysis.py`).

## Cross-reference: our shipped regions vs. stock templates

| our region | our `ai_info_map_type` | closest stock template | tier there |
|---|---|---|---|
| Britain | ISLANDS | `real_world_britain.rms` (exact) | RESCLOSE |
| Caribbean | ISLANDS | `real_world_caribbean.rms` (exact) | RESCLOSE |
| Japan | ISLANDS | `real_world_nippon.rms` (exact) | RESCLOSE |
| Italy | MEDITERRANEAN | `real_world_italy.rms` (exact) | RESCLOSE |
| Black Sea | COASTAL | `real_world_blacksea.rms` (exact) | default |
| Chesapeake Bay | COASTAL | `real_world_texas.rms` (closest US-coastal) | RESCLOSE |
| Greece | MEDITERRANEAN | `real_world_byzantium.rms` (nearby Aegean geography) | RESCLOSE (maybe RESSUPERCLOSE - Greece's real coastline is more fragmented than Byzantium's) |
| New Zealand | ISLANDS | no exact match; closest by fragmentation is the Philippines/`Aquarena` tier | RESSUPERCLOSE, untried - NZ was our worst pre-fix offender |
| Salish Sea | ARCHIPELAGO | no Pacific NW analog; `Pacific_Islands.rms` is the closest fragmentation match | RESSUPERCLOSE |
| Scandinavia | MEDITERRANEAN (ours) | stock has a dedicated `SCANDINAVIA` `ai_info_map_type` + `Scandanavia.rms`/`Isthmus.rms` (System A, not System B) | n/a - flags that `choose_ai_map_type()` may be folding a fjord/skerry shape into the wrong bucket |

## Decision (2026-08-07)

User's call: base `rwmaps` generation on **System A** (modern,
`herdable_starting.inc`-family) rather than System B, despite System B being
what the name-matched Real World bonus maps use.

---

# System A in full (2026-08-07, second pass)

The first pass only read the sheep block. This pass read the whole modern
pipeline: `Arabia.rms` end-to-end (the canonical System A map),
`real_world_manchuria.rms` / "Great Wall" (see below, and the flavor
caveats at the end of this document), and
`starting_resources.inc`, `herdable_starting.inc`, `herdable.inc`,
`huntable.inc`, `lureable.inc`, `stragglers.inc`, `object_setup.inc`,
`object_groups.inc`, `neritic.inc`, `oysters.inc`, `whales.inc`,
`aquatic_saltwater.inc`, `remote_resources.inc`, `town_centres.inc`,
`forest.inc`, `themes.inc`, `constants.inc`.

## The load-bearing mechanic: `#const` first-definition-wins

Every System A include opens its blocks with a `/* Default Values */`
comment followed by `#const` declarations. Those only take effect if the map
script did **not** already declare that const. So the calling convention is:

```
#const GOLD_PRIMARY_DISTANCE 10      /* override, optional */
#define GOLD_PRIMARY                 /* opt in to this tier */
#include_drs includes/starting_resources.inc
```

This is why the includes are parameterised entirely by name rather than by
arguments, and it is the single mechanism behind all per-map tuning. Every
`if SOMETHING_ZONE_AVOIDANCE` / `if GOLD_PRIMARY_TERRAIN` in the includes is
an optional hook that stays inert unless the map defines the name.

## Answers to the three open questions

**1. Does System A have narrow-land-aware behaviour?** Yes, but not as a
named tier like `GNR_RESCLOSE`. It is spread across four independent knobs:

| knob | default | what it does |
|---|---|---|
| `RESOURCE_SPACING_DEFAULT` | set per map (Arabia 12) | `actor_area_radius` on every primary resource - how far apart resource piles must be |
| `RESOURCE_SPACING_FAR` | set per map (Arabia 18) | radius of the "Far … Spacer" placeholder rings |
| `*_PRIMARY_DISTANCE` etc. | forage 12, gold 12, gold-secondary 22, gold-tertiary 28, forage-secondary 16, forage-tertiary 28 | `min_distance_to_players` for that tier |
| `*_ZONE_DISTANCE` | 4 | `max_distance_to_other_zones` for that tier |

`CONFINED_SETUP` is indeed only the lobby "Confined" option (it feeds
`CONFINED_INCREASE`, a terrain-density multiplier) - not real-world
coastlines. Not the mechanism we want.

**2. Is overriding consts sufficient to replace `--tight-resources`?**
Yes - and the stock game already demonstrates exactly this, on exactly our
kind of map. `real_world_manchuria.rms` is the **one Real World map that has
been ported to System A**, and like all 29 `real_world_*` maps it has no
`<LAND_GENERATION>` (its coastline comes from the paired `.scx`, resources
are layered on top). That is structurally the same problem we have - though
note it solves it with a much more austere resource budget than we
necessarily want (see "Flavor" below). Its complete tightening:

```
#const RESOURCE_SPACING_DEFAULT 6     /* Arabia uses 12 */
#const RESOURCE_SPACING_FAR 14        /* Arabia uses 18 */
#const RESOURCE_RESTRICTION 1

#const FORAGE_BUSH_PRIMARY_DISTANCE 10   /* default 12 */
#define FORAGE_BUSH_PRIMARY
#const GOLD_PRIMARY_DISTANCE 10          /* default 12 */
#define GOLD_PRIMARY
#const STONE_PRIMARY_DISTANCE 10         /* default 12 */
#define STONE_PRIMARY
#include_drs includes/starting_resources.inc

#const HERDABLE_DISTANCE 16
#const HUNTABLE_SMALL_COUNT 8
#const HUNTABLE_SMALL_GROUPS 1
#const LUREABLE_DISTANCE 12
```

Three things to note, all of which contradict what our current script does:

- It enables **only the PRIMARY tier** of forage/gold/stone. No
  `GOLD_SECONDARY`, no `GOLD_TERTIARY`, no `*_ADDITIONAL`. Arabia enables
  six tiers plus two additional; Manchuria enables three tiers total. On
  land-constrained maps the far tiers are simply not asked for, rather than
  asked for and allowed to silently fail.
- It includes `remote_resources.inc` **unconditionally** (Arabia gates it
  behind `SPACIOUS_SETUP`). That is the "leftover space gets filled" pass -
  gold/stone/forage/huntable at `min_distance_to_players 100`, tolerant
  `max_distance_to_other_zones 8`.
- Compare `Team_Islands.rms` (System A, genuinely island-shaped): it keeps
  the default spacing but pushes `*_ADDITIONAL_DISTANCE` out to 40 rather
  than tightening the primaries. So the stock game has two different
  answers depending on whether land is *narrow* (Manchuria) or *plentiful
  but fragmented* (Team Islands) - a distinction that maps onto our regions.

So the modern equivalent of `--tight-resources` is: lower
`RESOURCE_SPACING_DEFAULT` and the `*_PRIMARY_DISTANCE` consts, and stop
requesting far tiers. That replaces our additive backstop rather than
supplementing it. `Loch Ness.rms` shows the other lever independently -
it raises `*_ZONE_DISTANCE` from 4 to 14 for its four primary tiers.

**3. Do we lose stable unit ids?** No. The theme roles are ordinary
`#const`s (`FORAGE_PLANT`, `HERDABLE_A`, `HUNTABLE_A`, `HUNTABLE_SMALL_A`,
`LUREABLE_A`, `NERITIC_A`, `SALTWATER_A`, `FRESHWATER_A`, `WHALE_A`,
`PREDATOR_A`, `RIPARIAN_A`, `FURBEARER_A`, `BIRD_A`, `WILD_ANIMAL`,
`NOMAD_SCOUT`) assigned inside `themes.inc`'s per-biome branches. Because of
first-definition-wins, a map that declares them itself before including
`themes.inc` pins them - which is precisely what `real_world_britain.rms`
does with `#const HERDABLE_A 594`. We can pin every role to one skin and
keep `RESOURCE_UNITS` in `scx_read.py` exact.

One wrinkle: `HERDABLE_A` / `HUNTABLE_SMALL_A` are sometimes a `#const`
(a unit id) and sometimes a **`create_object_group`** built in
`object_groups.inc` (e.g. `COW_VARIATION_A` builds a HERDABLE_A group of
four cow skins). Both are usable as an object name in `create_object`, but
only the `#const` form gives a single predictable id.

## What our current template is actually missing

Our `<OBJECTS_GENERATION>` (`src/rwmaps/rms.py`) is
`#include_drs land_and_water_resources.inc` plus hand-written trees, deer
and fish. Against System A, the following are **absent entirely**:

| missing | include | gameplay effect |
|---|---|---|
| **Straggler trees at the TC** | `stragglers.inc` | 5 trees within ~5 tiles of the TC, `find_closest` + `enable_tile_shuffling`. This is the wood players chop in the first minutes. We place none. Biggest single gap. |
| **Town Centre placement object** | `town_centres.inc` | TC placed via `PLACEHOLDER_AMPHIBIOUS_TILE` + `second_object`, registering `actor_area 10` |
| **The whole actor-area system** | all | ~60 numbered `avoid_actor_area` zones keep resources from overlapping each other, the TC, villagers, scout and walls. Without it, piles can and do land on top of each other |
| **Placeholder-zone scaffolding** | `object_setup.inc` | actor areas 2000/2010/2020 (neutral zones by map fraction) and 2030 (shore zone) that later objects place into or avoid |
| **`require_path`** | resource includes | guarantees the resource is walkable-reachable from the player. Directly relevant to our island maps |
| **`find_closest` + `set_circular_placement` + `enable_tile_shuffling`** | resource includes | the retry mechanism. The old include tries one spot and gives up - the root cause of our zero-of-a-kind bugs |
| **Villagers / scouts** | `villagers.inc`, `scouts.inc` | we rely on engine defaults |
| **Boar (`LUREABLE`), small huntables** | `lureable.inc`, `huntable.inc` | we place a bare `DEER` and, under the backstop, one `BOAR` |
| **Relics** | `relics.inc` | monk gold |
| **Remote resources** | `remote_resources.inc` | the fill pass for leftover map area |
| **Predators, riparian, furbearers** | resp. includes | wolves etc. - minor but they exist |
| **Shore/deep fish role separation** | `neritic.inc`, `aquatic_*.inc`, `whales.inc`, `oysters.inc` | we hand-roll `SHORE_FISH`/`FISH`/`SALMON`/`MARLIN1`; modern uses `PLACEHOLDER_WATER_TILE` + `second_object`, which guarantees legal water placement instead of hoping |

The `PLACEHOLDER_*_TILE` + `second_object` idiom (ids in `constants.inc`:
`PLACEHOLDER_WATER_TILE 1547`, `PLACEHOLDER_AMPHIBIOUS_TILE 1545`,
`PLACEHOLDER_GENERIC 1902`, …) is how modern scripts force a placement to
respect a terrain domain. Worth adopting for fish regardless of anything
else.

## Cosmetic vs. gameplay - the classification we kept getting wrong

Prior confusion (noted by the user) was treating trees as cosmetic. The
actual split, reading the stock scripts:

**Gameplay-critical, currently under-served by us:**
- **Forest terrain** - forest *terrain* is what auto-spawns wood-bearing
  trees. `FOREST_PLACEHOLDER` (terrain id 99) is converted by `forest.inc`
  into per-player forests with explicit tile budgets
  (`PLAYER_FOREST_TILES`), avoidance (`PLAYER_FOREST_AVOIDANCE`) and a
  team-size deduction (`PLAYER_FOREST_TEAM_DEDUCTION 0.72`). Wood supply is
  a balance quantity, not decoration.
- **Straggler trees** (`STRAGGLER_SPAWN`) - see above.
- `STRAGGLER_FOREST` terrain - small extra wood clumps away from spawns.
- Elevation - blocks line of sight and gives combat bonuses;
  `enable_balanced_elevation` exists precisely because it is not cosmetic.
- Cliffs - hard pathing blockers.

**Genuinely cosmetic** (safe to skip, and gated behind `#define AESTHETICS`
in stock scripts, which is a useful signal): `AESTHETIC_FLAT`,
`AESTHETIC_GROUPED`, `AESTHETIC_SCATTER`, `birds.inc`, `colour.inc`
(`color_correction`), `decay.inc`, and the `BASE_BLEND_A..D` /
`BASE_FOREST_VARIATION_*` terrain-painting passes.

**Cosmetic-looking but affects gameplay:**
- `SOLID_OBJECT` / `SOLID_SURROUND` (boulders, rock clusters) - these are
  *collidable*. They shape pathing and wall lines despite sitting in the
  `AESTHETICS` block.
- `BEACH_TERRAIN` - decides where `oysters.inc` can place food.
- `WATER_SHALLOW` - shallows are walkable; a "decorative" pond can open or
  close a land route. Also the terrain `object_setup.inc` uses to define the
  shore actor area 2030.
- `terrain_mask` / `layer_to_place_on` passes - purely visual *unless* an
  object uses `layer_to_place_on` to find them, which `AESTHETIC_FLAT` does.

## Consequence for report thumbnails

Forests should render as a distinct dark green in report previews rather
than being folded into generic land - wood placement is a balance quantity
and currently invisible in our previews. `src/rwmaps/preview.py` only has
`SEA`/`LAND`/`SPILL`/`MISS`, so forest is not represented at all today.

---

# Flavor: what a template imports besides correctness (2026-08-07, third pass)

A first version of this document recommended porting
`real_world_manchuria.rms` wholesale. That was too fast, for two reasons the
user flagged, both correct:

1. **It was never established that the map is live.** It is - it ships as
   **"Great Wall"**. Script names and UI names diverge for 8 of the 29 Real
   World maps. See `STOCK_MAP_INVENTORY.md`, written for exactly this.
2. **A donor template carries that map's flavor, not just its
   correctness.** Copying Great Wall's resource block makes our maps play
   like Great Wall.

## Great Wall is a bad wholesale donor

Reading its actual config rather than pattern-matching on "real world map
with no `<LAND_GENERATION>`":

- It is **1 of only 2 System A maps out of 52 that omit
  `includes/stragglers.inc`** (the other is `Shipwreck`). It defines
  `#const STRAGGLER_SPAWN 1051 /* Dragon Tree */` and then never places
  them. Great Wall gives you **no straggler trees at the town centre.**
  That is directly opposed to the "biggest single gap" finding earlier in
  this document. Porting it wholesale would have entrenched the gap it was
  supposed to fix.
- It also omits `predators.inc`, and enables **PRIMARY tier only** for
  forage/gold/stone (no secondary, tertiary or additional) - the most
  austere resource layout of any System A map examined.

Its *tightening technique* is still the right reference. Its *resource
budget* is not. Those are separable, and conflating them was the error.

## Arabia as baseline - with one correction

The working model - Arabia is simple and uniform, and other maps are
tweaks relative to it - holds, with one measured exception. On resource
**spacing**, Arabia is not the centre of the distribution, it is the loose
end of it:

| map | `RESOURCE_SPACING_DEFAULT` | `RESOURCE_SPACING_FAR` |
|---|---|---|
| Arabia | **12** | 18 |
| Coastal, Baltic, Scandinavia, Team Islands, Loch Ness | 10 | 16-18 |
| Black Forest | **6** | 10 |
| Great Wall | **6** | 14 |

So 10 is the common value and Arabia is deliberately the most spread out -
consistent with it being the open, uncluttered, "fair by construction" map.
And note **Black Forest and Great Wall land on the same 6**, from opposite
causes: Black Forest has plenty of land but it is walled in by trees, Great
Wall has little usable land. That kills the tidy story that spacing 6 is a
narrow-coastline signal. It is a *usable-space* signal, whatever constrains
the space.

## The flavor table

`automation/compare_resource_flavor.py` resolves each donor map's System A
parameters (map override where present, include default otherwise) and
prints them side by side. Output is committed as `STOCK_MAP_FLAVOR.txt`;
regenerate with:

```sh
python automation/compare_resource_flavor.py --out STOCK_MAP_FLAVOR.txt
```

Read the script's docstring for the parse's honest limits - conditional
branches are flattened and marked `*`, `start_random` values are marked
`rnd`, arithmetic is not evaluated. It is a static read, not a render.

Flavor axes it makes visible, with the ones that matter most to us first:

- **Straggler trees** - near-universal (50/52), and its absence is a strong
  flavor statement. We currently place none.
- **`forest.inc`** (per-player forests around the spawn) - a *minority*
  feature, only ~22 of 52 maps. Arabia and Team Islands use it; Black
  Forest, Coastal, Baltic, Scandinavia, Loch Ness and Great Wall all
  hand-roll their forest terrain instead. So "modern" does not imply
  `forest.inc`; it is an Arabia-family choice.
- **Which resource tiers are requested** - ranges from Great Wall's
  PRIMARY-only to Arabia's six tiers plus two additional. This is the
  single biggest lever on how rich a map feels.
- **Berries** - Scandinavia defines **no `FORAGE_BUSH_*` tier at all**. Its
  UI description confirms this is deliberate: *"In the northern wilderness
  the berry bushes have all frozen, but there are plenty of animals."* A
  whole food class simply absent, as flavor.
- **Water food** - `SALTWATER_COUNT` 8192 (Arabia's default) vs 1024
  (Coastal, Baltic, Team Islands, Great Wall); `WHALE_COUNT` 0 by default
  and only switched on by water maps.

This is also the answer to why our early fairness metrics could not tell
Arabia and Black Forest apart: their largest difference is tree generation,
and we measure neither forest coverage nor straggler placement.

## Suggested port shape (not yet implemented)

Take the **mechanism** from System A generally, the **tightening
technique** from Great Wall, and the **resource budget** from whichever
donor matches the flavor we want - explicitly chosen per region, not
inherited by accident.

1. Pin every theme role with explicit `#const`s and keep `RESOURCE_UNITS`
   exact; do not pull in `themes.inc`'s biome randomisation.
2. `#const RESOURCE_SPACING_DEFAULT` / `_FAR` / `RESOURCE_RESTRICTION`,
   then `object_setup.inc`.
3. `town_centres.inc`, `villagers.inc`, `scouts.inc`, and
   **`stragglers.inc`** - include it, unlike Great Wall.
4. Choose tiers per region as an explicit flavor decision. Great Wall's
   PRIMARY-only is the austere end; Arabia's six-plus-two is the generous
   end. This replaces the `--tight-resources` flag rather than stacking
   with it.
5. `herdable_starting.inc` + `herdable.inc` + `huntable.inc` +
   `lureable.inc`.
6. Fish via `neritic.inc` / `aquatic_saltwater.inc` / `whales.inc` with
   `PLACEHOLDER_WATER_TILE`.
7. `relics.inc`, then `remote_resources.inc`.

Open, and needing a real render to settle: whether `#include_drs
includes/<name>.inc` resolves from a script installed in a **mod**
directory. Our current script only uses flat-namespace
`land_and_water_resources.inc`, so the `includes/` subpath is untested from
`resources\_common\random-map-scripts\`. If it does not resolve, System A
has to be inlined - which is exactly what the community maps
(`Acclivity.rms` et al., ~7k lines, zero includes) do, so there is a proven
precedent either way.

Per `CLAUDE.md`, the per-region tier and tightening choices need real-engine
captures to judge, not a synthetic preview - and the zero-of-a-kind check is
the only pass/fail metric here; everything else is a fact to surface.
