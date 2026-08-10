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
| `RESOURCE_TEMPLATES.md` | the two stock resource systems and their measured budgets | live, authoritative on resources |
| `STOCK_MAP_INVENTORY.md` | what stock scripts exist on disk, script name vs UI name | live |
| `RENDER_PIPELINE.md` | the UI automation that drives real engine renders | live |
| `RESOURCE_REWORK_STATUS.md` | the resource rework's open items | live, a work queue not a reference |
| `CLAUDE.md` | working conventions - verification philosophy, git hygiene | live |
| `MOD_STATUS.md` | 2026-08-01 mod state | **history**, carries a superseded banner |
| `TUNING_STATUS.md` | 2026-07-31 window/parameter search | **history**, but its known-good defaults are the ones in `cli.py` today |
| `README_AGENTS.md` | older agent-written readme | **history**, its CLI examples predate the mod build |

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
| `resources_neutral.inc` | gold/stone/forage + `_B` huntables belonging to nobody | `min_distance_to_players 26` |
| `relics.inc` | relics | needs a `RELIC_TYPE_*` define |
| `remote_resources.inc` | leftover-space gold/stone/forage/deer | `min_distance_to_players 100` |

Two traps live in this table, both instances of the same one - **an include
can be present and place nothing**:

- `relics.inc` is one big `if RELIC_TYPE_UNRESTRICTED / elseif _BALANCED /
  elseif _PLAYER / elseif _SCATTER`. This project defined none of them, so
  the include was inert and **every map it ever shipped had zero relics**.
  Now emits `RELIC_COUNT 10` + `RELIC_TYPE_BALANCED`.
- `remote_resources.inc` self-defines `REMOTE_DISTANCE 100`, and on a
  240x240 map with 8 players essentially no land is 100+ tiles from
  everyone. Measured on Salish Sea: **0 placeable tiles at 100**, 1 tile at
  80, against 20,469 at 26. It is dead code as included. Overriding the
  const before the include would revive it, since first-`#const`-wins.

Check a gate before tuning anything behind it:

```sh
uv run python automation/neutral_supply.py --mod <run-id>
# prints "placeable land at min_distance_to_players: 0=... 26=... 100=..."
```

## Building and installing the mod

`mod/` is *generated*, not hand-edited - `build_mod.py` regenerates it from
its own `MOD_REGIONS` list, which is where each region's CLI flags live.

```sh
uv run python automation/build_mod.py --list          # region names + their flags

# full rebuild: 11 regions x ~70s of annealing, ~17 minutes
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
