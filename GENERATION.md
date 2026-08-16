# How generation works

The end-to-end path from "a place on Earth" to "a `.rms` the engine can
generate", and from there to a captured scenario the analysis can measure.
This is the *mechanism* document. It says what runs and in what order; it
does not carry tuning history or open questions.

Written 2026-08-10. Where a number here is a default it was read out of the
code, not remembered - the code is authoritative and the file/function is
cited so a stale line here is checkable in one grep.

**Related docs, and what each is for:**

| doc | what it is | status |
|---|---|---|
| this file | the generation mechanism | live |
| `RESOURCE_TEMPLATES.md` | the two stock resource systems and their measured budgets | live, authoritative on stock |
| `STOCK_MAP_INVENTORY.md` | what stock scripts exist on disk, script name vs UI name | live |
| `EDITOR_AUTOMATION.md` | the UI automation that drives real engine renders | live |
| `RENDER_PIPELINE.md` | why the pipeline is GUI automation at all | **history** for the mechanics, live for the rationale |
| `RESOURCE_REWORK_STATUS.md` | the resource rework's open items | live, a work queue not a reference |
| `CLAUDE.md` | working conventions | live |
| `README_AGENTS.md` | concepts: fairness, aesthetics, how to make changes | live |
| `MOD_STATUS.md` | 2026-08-01 mod state | **history**, carries a superseded banner |
| `TUNING_STATUS.md` | 2026-07-31 window/parameter search | **history**, but its known-good defaults are the ones in `cli.py` today |

## The one thing that breaks maps

**The lobby Map Size must match the size baked into the script.** Land areas
are absolute tile counts while `land_position` is a percentage, so a size
mismatch does not degrade the map, it breaks it: the script asks for more
land tiles than the grid has and features vanish. Every shipped map is
240x240 and wants `Huge [240]`.

## Stage by stage

Everything below is one call to `generate()` in `src/rwmaps/cli.py` - it is
a single linear function, and reading it alongside this section is the
fastest way in.

### 1. Pick a window on the Earth - `projection.py`

`MapWindow.from_center(proj, lon, lat, span_km, size, rotate)`. A named
`--region` is just a `(lon, lat, span_km)` triple in `cli.py`'s `REGIONS`
dict; `--center LON,LAT --span-km N` gives the same thing ad hoc.

The grid is `[y][x]` with `y=0` **north** and `x=0` **west** - plain
north-up, confirmed by rendering the shipped `real_world_britain.scx`
terrain in all eight dihedral orientations. The engine's isometric camera
rotates the square 45 degrees on screen, which is why a north-up grid looks
diamond-oriented in game. `--rotate` turns the window before rasterising
(Japan ships at `--rotate 35`), which is how a diagonal country is made to
fill a square grid.

### 2. Rasterise the coastline - `raster.py`, `geodata.py`, `terrain.py`

`raster.rasterize(window, biome, resolution, min_island_tiles)` samples
Natural Earth land polygons (public domain, cached locally by `geodata.py`)
into a boolean land mask at `size x size`. `--resolution 50m` is the default
- a *source data* resolution, not a tile size.

`raster.simplify_features(mask, min_water_width, min_land_width)` then
removes features too thin to survive the disc encoding in stage 4 - a
one-tile-wide strait would be closed by the first disc that overlaps it, so
it is better to widen or drop it deliberately than to have the encoder do it
by accident. Defaults 4 and 3.

Terrain ids live in `terrain.py`, confirmed against shipped `real_world_*`
scenario grids.

### 3. Choose start positions - `analysis.py`

`choose_starts(mask, players, radius, spread_islands)`, working on the land
mask alone, so a projection can be judged before the engine ever runs.

Farthest-point sampling by itself is a bad model of fair placement: pure
spread drives every start onto a coastal tip, which is exactly where a
player has least room. So it keeps only candidates above a quality floor
(`quality_percentile` 60) and spreads across *those*. Components below
`min_component_tiles` (200) are dropped as too small to hold a start and its
economy, but every component above it is kept - restricting to the single
largest landmass would be wrong for a genuine archipelago.

`--spread-islands` additionally hands the farthest-point pack to
`_multistart_anneal`, simulated annealing against `_score_starts`. The
current objective scores **separation and available land, and nothing
else**. This is the slow part of the whole pipeline: roughly 70s per region.

Note the name is now misleading - under a land-availability objective, small
islands score badly, so it does *not* reliably spread players across
islands. See `RESOURCE_REWORK_STATUS.md` item 7.

### 4. Encode the coastline as discs - `rms_land.py`

An `.rms` cannot state "these tiles are land". It can only place
`create_land` blocks, each a blob grown from a seed point. So the coastline
is approximated as a union of discs:

`cover_mask(mask, budget, max_radius, overlap)` greedily drops a disc on the
most interior uncovered tile, sized to the local distance to the edge of
what remains. Big open areas are eaten by a few large discs, leaving the
budget for the fiddly coastline. `--overlap` (default 0.85) is the crux:
circles do not tile the plane, so clearing exactly the disc's own area
leaves gaps, and overlapping neighbours is how the union stays connected.

`build_land_generation()` renders those discs to `create_land` blocks. On a
240 grid the budget is 700 lands (`lands_for_size`), **all sharing
`land_id 1`** so the engine treats the whole coastline as one zone family.
Player starts are emitted first, as their own small `create_land` blocks
with `assign_to_player`, painted with `SPAWN_PLACEHOLDER` terrain - that
placeholder is what makes each player's own land addressable later for the
per-player forest pass.

`iou(approx, mask)` reports how much of the intended coastline survived. Per
`CLAUDE.md` this is a diagnostic, not the optimization target - the target is
human-judged recognizability, and a Python preview of the disc union is
explicitly **not** a substitute for a real engine render.

### 5. Assemble the script - `rms.py`, `rms_objects.py`

`build_rms(..., system_a=True)` -> `_build_rms_system_a()`. Section order
follows stock `Thames.rms`:

1. **header**, then `build_prelude(pins)` - `scaling.inc`, the biome define,
   the `MAP_CONSTANTS` block, `themes.inc`, `constants.inc`. The
   `MAP_CONSTANTS` block pins each resource role to one skin so a region
   looks the same every generation; `themes.inc` would otherwise re-roll
   them. It works because a second `#const` for a name already defined is
   ignored - first definition wins - so pinning *before* the include
   overrides it.
2. `build_player_setup()` - `direct_placement`, `ai_info_map_type`,
   `water_preset.inc`.
3. the `<LAND_GENERATION>` section from stage 4.
4. water depths, land terrain and forest (`_LAND_TERRAIN`).
5. `build_per_player_forest()` - each player gets a budgeted forest placed
   into their `SPAWN_PLACEHOLDER` land.
6. elevation.
7. `build_objects(flavor)` - the whole resource layer, below.

### 6. The resource layer - `rms_objects.py`

This is **System A**, the machinery current stock maps use, driven by a
`ResourceFlavor` dataclass (`FLAVORS["default"]` / `["archipelago"]`,
selected per region). It emits `#const` values and then `#include_drs` of
the stock includes, in this order:

| include | places | gate |
|---|---|---|
| `object_setup.inc` | object groups the role names resolve to | - |
| `town_centres` / `villagers` / `scouts` / `stragglers` | the start itself | - |
| `starting_resources.inc` | per-player gold / stone / forage | per player |
| `herdable_starting.inc`, `herdable.inc`, `huntable.inc`, `lureable.inc` | per-player sheep, deer, boar | per player |
| `neritic.inc`, `aquatic_saltwater.inc`, `aquatic_freshwater.inc` | fish | water |
| `resources_neutral.inc` | gold/stone/forage + `_B` huntables belonging to nobody | **off** - see below |
| island pass (ours, `_island_blocks`) | neutral gold and stone, islands included | `min_distance_to_players 26` |
| `relics.inc` | relics | needs a `RELIC_TYPE_*` define |
| `remote_resources.inc` | leftover-space gold/stone/forage/deer | `min_distance_to_players 100` |

**The neutral supply is ours, not the stock include's.** Measured, the
include works (Salish Sea 0 -> 333 neutral) but it takes no consts, so it
is all-or-nothing at a 37% neutral share against a stock band of 14-21%,
and every block in it carries `max_distance_to_other_zones 8` - a clause
measuring distance to a *different* zone, which excludes every island,
since an island sits across water from one. It left all five of Salish
Sea's unowned islands empty.

`_island_blocks` in `rms_objects.py` is that include's own gold and stone
blocks with only that clause dropped. **Gold and stone, no food** - neutral
food is a weak prize because players switch to farms late, and stock
agrees (Arabia: 24 neutral gold, 27 stone, 0 forage, 3 deer). Britain
measures 28/23/3 at a 13% share with every island stocked.

Three traps live in this table, all instances of the same one - **an
include can be present and place nothing**:

- `relics.inc` is one big `if RELIC_TYPE_UNRESTRICTED / elseif _BALANCED /
  elseif _PLAYER / elseif _SCATTER`. This project defined none of them, so
  the include was inert and **every map it ever shipped had zero relics**.
  Now emits `RELIC_COUNT 10` + `RELIC_TYPE_BALANCED`.
- `remote_resources.inc` self-defines `REMOTE_DISTANCE 100`, and on a
  240x240 map with 8 players essentially no land is 100+ tiles from
  everyone. Measured on Salish Sea: **0 placeable tiles at 100**, 1 tile at
  80, against 20,469 at 26. It is dead code as included. Overriding the
  const before the include would revive it, since first-`#const`-wins.

- `max_distance_to_other_zones` is the third: present, satisfied on the
  mainland, and unsatisfiable on every island, so the include placed
  hundreds of objects while looking like it had simply skipped the islands
  for want of room. They measured 100% open and 100% legal.

Check a gate before tuning anything behind it:

```sh
uv run python automation/neutral_supply.py --mod <run-id>
# prints "placeable land at min_distance_to_players: 0=... 26=... 100=..."
```

## Islands: what makes one worth sailing to

Real coastlines produce unowned islands, and they are the most distinctive
thing these maps have that stock maps do not. The design rules below are
the user's, and they are economic rather than geometric - measure against
them, not against "does it look stocked".

**Reachability is not the question.** Players build transports. Getting to
an island is harder than walking and entirely routine; nothing needs to be
reachable on foot to be contested.

**Buildability is the question, and shore tiles are unbuildable.** A
villager needs somewhere to stand, so the minimum viable island is roughly
**6x4 counting shore**: a 2x2 mining camp, a 2x2 gold or stone pile, a
one-tile gap between them (the placement leaves one), and walkable shore
around it. Measured, **about half of every small island here is BEACH**, so
tile count badly overstates what is usable - a 151-tile island on Salish
Sea has 71 buildable tiles, and a 68-tile one has 18. Any "can this island
be worked" measure must exclude beach; `neutral_supply.py` does.

**Adjacent tiny islands are one island.** Five rocks in a cluster with real
combined landmass play as a single objective. At a 12-tile gap Italy's 7
islands are 3 groups (772 / 1018 / 1505 tiles), Salish Sea's 5 are 4.

**Gold and stone are worth mining; wood mostly is not.** Each unit of gold
or stone yields 400, and wood is plentiful elsewhere, so a pile on an
island is worth the trip. A tree yields 75 and a lumber camp costs 100
wood, so **a tiny island should have zero trees on purpose** - a copse too
small to justify a camp is clutter, not a prize. Neutral *food* is weak for
the same class of reason: players switch to farms late, converting wood to
food with minimal micro.

What follows from that, and is **built** - `_island_trees` in
`rms_objects.py`, emitted by `build_per_island` after the piles so the
piles get first pick of the ground:

- small islands get roughly **one straggler tree per 6 buildable tiles** -
  enough to build with, not enough to justify a lumber camp;
- larger islands get **at least one tree cluster: a blob of about 2-5 tiles
  across, or bigger**. ("Copse" is not an engine term - it just means a blob
  this size, as opposed to a real forest.)

Both are **tree objects**, not forest terrain, and that makes them one
mechanism rather than two. `create_object` takes
`place_on_specific_land_id`, so island trees are aimed exactly like the
gold and stone pass, with no distance gate to fight:

| want | `number_of_objects` | `number_of_groups` | grouping |
|---|---|---|---|
| scattered singles on a small island | 1 | buildable tiles / 6 | loose, `min_distance_group_placement 2` |
| a blob on a larger one | 12 | 1 per 250 buildable tiles, 1-6 | `set_tight_grouping`, `group_placement_radius 2` |

Two things that are easy to get wrong here, both found by building it:

- **The scatter rate is a rule about small islands.** Applied to an
  811-tile one it asks for 98 loose trees, which is a carpet, not a
  scatter. Any island big enough to also carry copses uses a much sparser
  rate (`island_straggler_per_tiles_large`), because there the copses are
  the wood and the singles are only something to build a house against.
- **Spacing has to admit the density asked for.** One tree per 6 tiles is
  a mean spacing of about 2.4, so a `min_distance_group_placement` of 4
  silently caps the count at a third of the request - the same class of
  bug as a gate that admits no tiles.

Sizing needs to know how much of an island is *not* shore, so
`rms_land.Island` carries a `buildable` estimate: the mask eroded by
`SHORE_RINGS` (2). One ring is far too generous against what captures
measure - a 151-tile island had 71 buildable tiles and a 68-tile one 18.

Forest *terrain* is a different thing and is what gives the big islands
their real woods - Ireland's 582 wood tiles, Sardinia's 547.
`create_terrain` has **no** land-id targeting (no stock map does it), so
aiming terrain at an island needs the placeholder-terrain trick
`build_per_player_forest` uses: paint the island lands with their own
terrain, grow forest on `base_terrain <placeholder>`, convert back. Only
reach for that if object blobs turn out to be too small to matter on a
1000-tile island.

## Loose trees between the bases

Stock has an include for the map-wide version of this,
`includes/stragglers_neutral.inc`, used by Arabia, Arena, Baltic, Black
Forest and many more. **We pinned `STRAGGLER_NEUTRAL` from the day System A
landed and never included the file that reads it** - a pin with no
consumer, and the cost of it is exact.

The engine emits **one tree unit per forest terrain tile**, so a raw
tree-object count says nothing: Britain measures 4200 tree objects against
4160 forest tiles. Only trees standing *off* forest terrain are stragglers,
and counted that way every one of our eleven regions carried **40** of
them - 5 per player from `stragglers.inc` and nothing else whatsoever.
Stock carries 182 (Arabia) to 318 (Team Islands), so 140-280 of stock's
loose trees are neutral ones we had none of.

The include is now emitted (`neutral_stragglers`), at its stock defaults
including `STRAGGLER_ZONE_DISTANCE 4`. That clause is the family that has
silently placed nothing here twice, so it is the first suspect if a capture
comes back at 40 again - but it is left alone because a sweep already
showed *raising* a zone distance kills placement rather than freeing it
(gold 25 -> 0 at 14). It does not substitute for the per-island blocks
above: the same clause is what keeps every map-wide pass off the islands.

## Forest shape: how Black-Forest-ish is this map

`automation/forest_structure.py` measures the wood's *structure* against
real stock captures. Three numbers matter, and none of them is share of
land:

- **largest blob** - what fraction of all the wood sits in one mass. Stock
  open maps are 1-8%; stock Black Forest is 34%.
- **blocked perimeter** - the share of the ring at 20 walking tiles from a
  town centre that the player cannot stand on, with the wood removed from
  the *denominator* so water is already out of it. Arabia 8% mean / 18%
  worst; Yucatan (the densest normal stock map) 34/46; Black Forest 34/58.
- **detour** - open-land walking distance between two players over the same
  distance with the wood treated as walkable. Stock open maps 1.00-1.04,
  Black Forest 1.10.

Measured on our own archived captures, several regions sit **past** Black
Forest on shape: Greece 38% largest blob, 41% mean blocked, detour 1.15;
Caribbean 52% largest blob; Britain 35% and a player at 69% blocked. The
cause is granularity - the map-wide forest is one `create_terrain` with
**12 clumps**, so we produce 16-75 blobs against stock's 100-148.

**Do not read the corridor count on its own.** A wide-open ring is one
connected component for the same reason a sealed pocket is: stock Arabia
has 11 of 24 players on "one exit" at an 8% blocked perimeter. Walled-in is
the conjunction of one corridor *and* a mostly-blocked perimeter, and by
that test it is rare (3 players in 264) - the common failure is not one
door, it is most of the perimeter being trees.

`--forest-clumps` and `--forest-clumping-factor` are the knobs.

### Why islands come out bare today

Both causes are the same shape - a map-wide pass with no reason to prefer
an island:

- **Resources.** The neutral gold/stone pass places by distance, and on a
  land-rich map the mainland satisfies the spacing long before an island is
  needed. Black Sea places 116 neutral objects and stocks none of its
  islands; land-poor Caribbean stocks all of its with a third of that.
- **Forest.** The map-wide forest is a single `create_terrain FOREST` with
  **12 clumps** over the whole map. Twelve clumps land where there is room,
  which is the mainland. Only the two largest islands measured (Sardinia
  1505 tiles, Ireland 1281) get forest at all; Italy has a 1018-tile
  cluster with 632 buildable tiles and **zero** trees. This is also why the
  wood comes out as a few big masses - see *Forest shape* above.

`rms_land.py` gives every unowned island its own `land_id` (`_island_ids`,
`Island`), and `rms_objects.build_per_island` emits gold, stone and now
trees against those ids.

## Building and installing the mod

`mod/` is *generated*, not hand-edited - `build_mod.py` regenerates it from
its own `MOD_REGIONS` list, which is where each region's CLI flags live.

```sh
uv run python automation/build_mod.py --list          # region names + their flags

# full rebuild: 8 regions x ~70s of annealing, ~10 minutes
uv run python automation/build_mod.py

# one region, in place, other regions untouched: ~2 minutes
uv run python automation/build_mod.py --regions "Salish Sea" --placeholder "Salish Sea"

uv run python automation/install_mod.py --all
```

Two mods are built from the same scripts: **Real World Maps** (shipping) and
**Real World Maps (Debug)**, which additionally carries the
`AA_rw_placeholder_tester.rms` slot. That slot exists to work around a
reproducible Scenario Editor crash in its list widgets - the automation
never picks a map from the list, it overwrites one fixed filename and
regenerates. `--placeholder` chooses which region lands in that slot;
without it the slot gets whichever region built first.

**The installed copy is only as fresh as the last `install_mod.py` run.** It
has been stale by a whole rework at least once.

## Capturing and measuring

Generation produces a script; only the engine produces a map. The capture
loop drives the Scenario Editor by UI automation - see `RENDER_PIPELINE.md`
for the mechanics and for why calling the engine directly was tried and
abandoned.

The game must be running, in the Scenario Editor, with
`AA_rw_placeholder_tester` selected at Huge [240] / 8 players.

```sh
uv run python automation/mod_capture.py --run-id <id> --n-samples 1
# aborts immediately if the game is not running - a pass once burned 1.9
# hours reporting script errors when the game had simply exited

uv run python automation/compare_starts.py --stock benchmarks --mod <id>
uv run python automation/neutral_supply.py --mod <id> --detail
```

Captures land in `out/mod_capture/<id>/<Map>/raw/*.aoe2scenario` and are
read by `scx_read.py` - the actual tile grid the engine produced, which is
the only thing that counts as verification here.

Measurement modules:

- `fairness.py` - per-player start quality from a capture. Ownership is
  *walking* distance from the town centre (`OWNERSHIP_RADIUS` 30), so
  resources are exclusive / contested / unclaimed. "Unclaimed" is the
  neutral supply, and is a good thing, not waste.
- `neutral_supply.py` - the same ownership rule, but split by landmass, so
  "there are neutral resources" and "the islands have anything on them" can
  be told apart.
- `analysis.py` - mask-only analysis, no engine needed.

Per `CLAUDE.md`, these print facts, not verdicts. The only thing called a
problem is a player with literally zero of a resource kind; separation,
landmass count and pairwise reachability are geography, for a human to
judge.
