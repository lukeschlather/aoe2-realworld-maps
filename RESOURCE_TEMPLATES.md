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

Used by `Arabia.rms`, `Coastal.rms`, `Scandanavia.rms`, `Isthmus.rms`,
`Baltic.rms` (confirmed via `grep HERDABLE_STARTING_COUNT_ .rms` - these are
the only stock maps that set it and `#include_drs
includes/herdable_starting.inc`).

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

## Decision and open question (2026-08-07)

User's call: base `rwmaps` generation on **System A** (modern,
`herdable_starting.inc`-family) rather than System B, despite System B being
what the name-matched Real World bonus maps use. Rationale not fully
spelled out yet in-session - revisit if unclear later.

**Open, not yet answered**: System B's per-region tightness tiers
(`GNR_RESCLOSE`/`GNR_RESSUPERCLOSE`) map directly onto Britain/Caribbean/
Italy/Japan's already-diagnosed narrow-coastline needs. System A has no
equivalent tiering mechanism spotted yet - `starting_resources.inc`'s
placement is entirely `#const`-distance-driven (see System A section above),
which *might* make "tight resources" easier to port (override the consts
directly) rather than harder (no separate backstop hack needed) - but this
is a hypothesis from a first read, not confirmed. Needs an actual side-by-
side dive into `starting_resources.inc`, `object_setup.inc`, and
`herdable_starting.inc` in full (not just the sheep block already read) to
find out:
1. Whether System A has *any* narrow-coastline-aware behavior already
   (e.g. via `CONFINED_SETUP`, which exists but appears tied to the
   "Confined" lobby option, not to real-world narrow coastlines).
2. Whether overriding the `#const *_DISTANCE` values per-region is
   sufficient to reproduce what `--tight-resources` currently achieves via
   the additive backstop, or whether the modern placement's stricter
   `max_distance_to_other_zones`/zone-avoidance behavior fights a narrow
   coastline in some new way the old include didn't.
3. Whether porting System A wholesale means giving up the
   stable-unit-id guarantee (`RESOURCE_UNITS` in `scx_read.py`) since its
   forage/herdable/huntable placement goes through the themed
   `includes/themes.inc` roles (`HERDABLE_A`, `FORAGE_BUSH_PRIMARY`, etc.)
   rather than literal `SHEEP`/`FORAGE` objects - or whether those theme
   roles can be pinned to a single skin (as `real_world_britain.rms` does
   with `#const HERDABLE_A 594`) without dragging in the rest of System A's
   biome-selection machinery.
