# Resource rework status - where to pick this up

Updated 2026-08-10. Reading order for anything resource-related:

1. `GENERATION.md` - how generation works end to end, including the
   **Islands** section, which carries the island design rules (buildability,
   beach, clustering, what wood is worth). Start there.
2. `RESOURCE_TEMPLATES.md` - the two stock resource systems, and the
   measured stock budgets. **Authoritative on stock.**
3. this file - what the rework changed, what is measured, what is open
4. `MOD_STATUS.md` / `TUNING_STATUS.md` - history only, carry known-bad
   assumptions, `MOD_STATUS.md` has a banner saying so

**Shipped: `37ed72e`.** System A resources, relics fixed,
`resources_neutral.inc` dropped, map-wide gold/stone neutral pass on.
Britain measures 28 neutral gold / 23 stone at a 13% share against stock
Arabia's 24 / 27 at 14%. `mod/` is committed and matches the installed copy.

## What changed

The resource layer moved off `land_and_water_resources.inc` (a 1999 orphan
no shipping map references) onto **System A**, the machinery current stock
maps use - see `src/rwmaps/rms_objects.py`. Start placement was rescored.
Both are committed. Neither is fully engine-verified.

Measured at N=10 (110 real captures, `out/mod_capture/sysa_n10`, report in
`reports/20260809-052633_mod_report_sysa_n10.html`) - bad starts, meaning a
player who can reach *none* of some resource kind, out of 80 per region:

| clean | | still failing | |
|---|---|---|---|
| Salish Sea | 0/80 | Italy (spread) | 17/80 |
| Black Sea | 0/80 | Caribbean | 20/80 |
| Chesapeake Bay | 0/80 | New Zealand | 33/80 |
| Britain | 0/80 | Japan | 43/80 |
| Scandinavia | 1/80 | | |
| Greece | 1/80 | | |
| Cramped Italy | 1/80 | | |

Britain was 8/10 broken samples before. The three 1/80 cases are a single
missing deer, which is inside stock behaviour - **stock Arabia has 16 of 24
starts with no owned deer, and Loch Ness places no deer at all.**

The placement rescore (separation + available land, coverage removed) has
NOT been through a capture pass. Its mask-model A/B says Japan's sub-600
starts go 3 -> 0 and Italy's 2 -> 0, and it puts two starts on the Italian
peninsula rather than one. Only Salish Sea was captured before the game
exited, and it looked healthy (worst open ground 1079, TC separation 62).

## The baseline (2026-08-10, no engine time needed)

Report: `reports/20260810-081421_neutral_baseline.html`, data alongside it.
Tool: `automation/neutral_supply.py`, validated against stock Thames, whose
published numbers it reproduces exactly. All figures are means over the
archived captures (stock `benchmarks`, ours `sysa_n10` = 110 captures).

**Neutral = `fairness.py`'s `unclaimed`: no town centre within
`OWNERSHIP_RADIUS` (30) tiles of *walking* distance.**

### Thames is the outlier; the reference band is 14-21%

| stock map | all res | neutral | share | the neutral supply is |
|---|---|---|---|---|
| Arabia | 381 | 54 | **14%** | 24 gold, 27 stone, 3 deer |
| City of Lakes | 409 | 59 | **15%** | 35 gold, 24 stone |
| Team Islands | 445 | 91 | **20%** | 24 gold, 20 stone, 47 forage |
| Loch Ness | 368 | 76 | **21%** | 41 gold, 19 stone, 12 deer |
| Yucatan | 1060 | 382 | 36% | 126 gold, 121 stone, 76 forage |
| Thames | 623 | 311 | **50%** | 118 forage, 99 deer |

Neutral supply in the band is **overwhelmingly gold and stone**, not the
food-heavy profile Thames suggested. Aim at Arabia's shape, not Thames's.

### Ours, all 11 regions

| region | all res | neutral | share | unowned masses | of which empty |
|---|---|---|---|---|---|
| Black Sea | 317 | 0 | 0% | 2.6 | 2.6 |
| Salish Sea | 319 | 0 | 0% | 3.6 | 3.6 |
| Scandinavia | 317 | 0 | 0% | 0.6 | 0.6 |
| Chesapeake Bay | 318 | 1 | 0% | 2.0 | 2.0 |
| Greece | 318 | 2 | 1% | 5.5 | 5.5 |
| Italy | 273 | 2 | 1% | 2.9 | 2.9 |
| Caribbean | 272 | 3 | 1% | 4.6 | 4.6 |
| New Zealand | 231 | 3 | 1% | 0.2 | 0.2 |
| Britain | 322 | 7 | 2% | 2.2 | 1.2 |
| Japan | 235 | 9 | 4% | 5.0 | 5.0 |
| Cramped Italy | 332 | 14 | 4% | 6.0 | 4.1 |

Not literally zero everywhere, as previously recorded, but 0-4% against a
14-21% band. And "unowned masses" is item 2 measured directly: Greece
carries 5.5 unowned landmasses of 60+ tiles per generation and **every one
of them is empty**; Japan 5.0 of 5.0; Caribbean 4.6 of 4.6.

### Stock does not use a neutral pass at all

**No stock script - 0 of 196 - references `resources_neutral.inc`.** Arabia
still has 54 neutral resources. So stock maps get their neutral supply as
*spill-over*: per-player rings that happen to land more than 30 walking
tiles from every town centre. Ours produce no spill-over because placement
keeps everything close to home and narrow coastlines clip the rings that
would otherwise reach.

That reframes the choice. The include currently wired up
(`resources_neutral.inc`) is the one with **zero** stock consumers; the one
with 42 stock consumers (`remote_resources.inc`) is already included and
mis-gated. Test both in the same pass rather than committing to either.

### The gates, measured

Placeable land in tiles (dry land minus forest minus the 6-tile edge
margin - a permissive upper bound, so a 0 is conclusive):

| map | at 26 (`resources_neutral`) | at 100 (`remote_resources`) |
|---|---|---|
| Salish Sea | 20540 | **0** |
| Black Sea | 20220 | **0** |
| Chesapeake Bay | 14654 | **0** |
| Scandinavia | 10606 | 13 |
| Italy | 10942 | **0** |
| Cramped Italy | 8601 | 1962 |
| Greece | 5289 | 396 |
| Britain | 2761 | 487 |
| Caribbean | 1979 | **0** |
| Japan | 1930 | **0** |
| **New Zealand** | **463** | **0** |
| stock Arabia | 31058 | **0** |
| stock Thames | 32950 | 5672 |

Two conclusions:

1. **`remote_resources.inc` is dead as configured, on stock maps too.** It
   self-defines `REMOTE_DISTANCE 100`; no stock map overrides it; Arabia
   includes it (behind `SPACIOUS_SETUP`, which *is* defined at 8p/Huge) and
   gets nothing from it. One `#const REMOTE_DISTANCE` pinned before the
   include revives it, first-definition-wins, the same mechanism
   `MAP_CONSTANTS` already relies on.
2. **The 26-tile gate is itself marginal on the hard regions.** New Zealand
   admits 463 tiles, Japan 1930, Caribbean 1979 - against 20000+ on Salish
   Sea and Black Sea. So `resources_neutral.inc`, even if it fires
   perfectly, will under-deliver on exactly the archipelago regions whose
   islands are emptiest. A single fixed distance cannot serve both ends of
   this range; it wants to be a per-region `ResourceFlavor` value, which is
   already the mechanism for this kind of split.

## RESOLVED 2026-08-10: the include fires, and the islands are fixable

Two captures, Salish Sea, one sample each (`out/mod_capture/neutral_v1`,
`out/mod_capture/island_v1`), against the N=10 baseline above:

| condition | all res | neutral | share | unowned islands | empty |
|---|---|---|---|---|---|
| per-player only (N=10) | 320 | 0 | 0% | 3.6 | **3.6 - all** |
| + `resources_neutral.inc` | 895 | 333 | 37% | 5 | **5 - all** |
| + island pass | 1159 | 535 | 46% | 3 | **1** |

**`resources_neutral.inc` works** - the warning sign about no stock map
referencing it did not pan out. Salish Sea's neutral supply went 0 -> 333.

**It leaves every island empty, and the cause is
`max_distance_to_other_zones 8`.** Every block in the include carries it.
The islands were not short of anywhere to put things: they measured 100%
open (unforested) and 100% legal on every constraint checkable from outside
the engine, at 73-161 tiles each. Emitting the include's own blocks minus
that one clause (`--island-resources`) stocked them - a 150-tile island took
stone, a 66-tile island took gold. A clause measuring distance to a
*different* zone excludes anything across water, which is exactly the
mainland-yes/island-no pattern observed.

### NOT SOLVED: 35 bare islands across 22 captures (`islands_n2`)

All 11 regions, N=2, every capture IoU 0.70-0.91 (no wrong-map captures).
"BARE" = an unowned 60+ tile island with a 2x2 camp spot and no gold or
stone on it.

| region | islands | stocked | BARE | gold | stone | share |
|---|---|---|---|---|---|---|
| Caribbean | 3.5 | 3.5 | **0** | 36 | 22 | 17% |
| New Zealand | 0.5 | 0.5 | **0** | 2 | 6 | 2% |
| Scandinavia | 0.0 | 0.0 | **0** | 38 | 48 | 20% |
| Britain | 2.0 | 1.5 | 1 | 30 | 20 | 13% |
| Chesapeake Bay | 2.0 | 0.5 | 3 | 42 | 41 | 18% |
| Greece | 5.5 | 4.0 | 3 | 36 | 46 | 19% |
| Italy | 6.5 | 5.0 | 3 | 60 | 54 | 24% |
| Cramped Italy | 6.5 | 4.5 | 4 | 63 | 56 | 27% |
| Black Sea | 3.5 | 0.0 | **7** | 60 | 56 | 23% |
| Japan | 6.0 | 2.5 | **7** | 14 | 11 | 8% |
| Salish Sea | 4.0 | 0.5 | **7** | 50 | 48 | 20% |

**The maps with the most neutral supply have the emptiest islands.** Black
Sea places 116 neutral objects at a 23% share and stocks *none* of its 3.5
islands; Salish Sea 98 objects at 20% and stocks 0.5 of 4. Caribbean stocks
every island with a third of that supply. So this is not a volume problem
and turning the knobs will not fix it.

Nor is it island size. Caribbean's 88-, 67- and 65-tile islands are all
stocked; Salish Sea's 151- and 108-tile islands are bare, as are Black
Sea's 88 and 82.

What separates them is **how much mainland competes for the same piles**.
The pass is map-wide, not island-aware: `number_of_groups 1024` with
spacing 40 places groups until it runs out of legal spots, and on a
land-rich map the mainland offers thousands of spots that satisfy the
spacing long before an island is ever needed. Caribbean's gate admits 1979
placeable tiles, so its mainland saturates and the islands get used; Salish
Sea's admits 20540 and they never do.

**The fix is to target islands explicitly, not to place more.**
`place_on_specific_land_id` is the mechanism, and this project generates
the lands itself, so it can give each island its own land id and emit a
small dedicated block per island - which is what `Thames.rms` does. That
needs `rms_land.build_land_generation` to assign per-component land ids and
report which components are unowned islands, and `rms_objects` to emit one
block each.

### SHIPPED 2026-08-10: gold/stone island pass, include dropped

The include is **off** and `--island-resources` is **on by default**.
Rationale: the include takes no consts, so it cannot be tuned down off its
37% share, and it leaves islands empty regardless. Our own pass is
parameterised and does both jobs.

**Gold and stone only, no food.** Neutral food is a weak prize - players
switch to farms late, so wood converts to food with minimal micro, making a
neutral deer herd worth much less than a neutral gold pile. The stock band
independently agrees: Arabia's neutral supply is 24 gold and 27 stone
against 0 forage and 3 deer.

Measured, `out/mod_capture/neutral_v3`, one capture per region, against the
N=10 baseline:

| region | | neutral gold | neutral stone | neutral food | share | unowned islands | empty |
|---|---|---|---|---|---|---|---|
| Britain | before | 2.8 | 0.5 | 0 | 2% | 2.2 | 1.2 |
| Britain | **after** | **28** | **23** | 3 | **13%** | 1 | **0** |
| Italy | before | 0.0 | 0.8 | 0 | 1% | 2.9 | **2.9 - all** |
| Italy | **after** | **50** | **61** | 5 | 24% | 5 | **0** |
| stock Arabia | | 24 | 27 | 3 | 14% | - | - |

Britain lands on Arabia almost exactly. Italy runs a little rich at 24%
because it genuinely has more unclaimed land to put piles on (935 placeable
tiles even at distance 100, against Britain's 582). Volume knobs if it
needs pulling in: `island_gold_spacing` / `island_stone_spacing` (40) and
`island_group_size` (5).

Britain's 1285-tile unclaimed island - previously 4 gold, 0 stone, 100%
neutral wood - now carries 9 gold and 5 stone alongside its 527 wood tiles.
Italy's Sardinia/Corsica/Sicily all carry gold and stone.

**Open:** some stocked islands have no wood at all (Italy's 217- and
127-tile islands are 0 forest; its 714-tile island has 2 tiles). Gold on an
island you cannot build on is a weaker prize than it looks. Forest
placement is a separate lever from the resource pass.

**Not measured:** the other 9 regions, and any region at N>1. Japan,
Caribbean and New Zealand have much tighter gates (463-1979 placeable tiles
at 26 vs Britain's 3458), and per the user New Zealand has no unclaimed
islands to stock in the first place, so it is not the right target.

### Superseded: the calibration problem this solved

Both passes overshoot badly. Neutral share is **37% with the include alone
and 46% with both**, against a stock band of **14-21%**, and total
resources went 320 -> 1159. Salish Sea now carries more resources than
stock Yucatan, the richest map measured.

`resources_neutral.inc` is **not tunable** - it takes no consts, so it is
all-or-nothing at 333 objects. Our own pass is fully parameterized. So the
cleanest route is probably to **drop the include and keep only the
hand-rolled pass**, tuned to hit the band: holding per-player supply at
~320, 14-21% wants roughly **65 neutral objects**, against the 200 the
island pass currently adds and the 333 the include adds. Levers are
`island_group_size` (4) and the group spacings (24/28).

That also disposes of the residual risk in shipping an include no stock map
uses.

Not yet measured: whether the same fix stocks islands on the archipelago
regions, where the 26-tile gate is much tighter (New Zealand 463 tiles,
Japan 1930, Caribbean 1979 - see the gate table above). Salish Sea, at
20540, is the easy case.

## Open, roughly in order of expected payoff

Items 1 and 2 of the original list are done: `resources_neutral.inc` fires
but is dropped as untunable, and the islands question turned out to be
mostly a measurement error plus a real wood gap. See above, and the
"Islands" section of `GENERATION.md` for the design rules.

### 1. Trees on islands - the one thing actually wrong

The state of play, measured per island cluster (12-tile gap):

| region | cluster tiles | buildable | wood tiles |
|---|---|---|---|
| Salish Sea, Black Sea, Greece | 64-353 | 24-193 | **0** |
| Italy | 772 | 505 | 25 |
| Italy | **1018** | **632** | **0** |
| Italy (Sardinia) | 1505 | 785 | 547 |
| Britain (Ireland) | 1281 | 566 | 582 |

Zero trees on the small ones is **correct and should stay** - a tree yields
75 wood and a lumber camp costs 100, so a copse too small to justify a camp
is clutter. The wrong one is Italy's 1018-tile cluster: 632 buildable tiles
and not one tree, which is a place a player would genuinely settle.

Target, per the user, and **no analysis needed to start - just build it**:

- small islands: about **one straggler tree per 6 buildable tiles**, enough
  to build with, not enough to justify a camp;
- larger islands: at least one **tree blob of roughly 2-5 tiles across, or
  bigger**. ("Copse" is not an engine term, just a blob of that size as
  distinct from a real forest.)

Both are **tree objects**, so this is one mechanism, not two:
`create_object` takes `place_on_specific_land_id`, exactly as
`build_per_island` already does for gold and stone. Scattered singles are
`number_of_objects 1` with `number_of_groups` = buildable/6 and loose
grouping; a blob is `number_of_objects` ~6-12 in one group with
`set_tight_grouping` and `group_placement_radius 2`.

No terrain work is needed for blobs this size - an earlier version of this
note claimed they needed the placeholder-terrain trick, which is only true
for real forest *terrain*, the thing that gives Ireland its 582 wood tiles.
Keep that in reserve in case object blobs read as too thin on a 1000-tile
island.

**Loose end found while checking this:** we pin `STRAGGLER_NEUTRAL 349` and
never include `includes/stragglers_neutral.inc` - a pin with no consumer.
That include is well-precedented (Arabia, Arena, Baltic, Black Forest) and
worth adopting for map-wide neutral trees. It will not fix the islands
though: it carries `max_distance_to_other_zones STRAGGLER_ZONE_DISTANCE`,
the same clause that keeps every map-wide pass off them.

Cause of the gap: the map-wide forest is one `create_terrain FOREST` with
12 clumps for the entire map, so clumps land on the mainland and mid-size
islands get none by luck.

### 2. Forest generation is too Black Forest-ish overall

Not just on islands. The current forest reads as one large mass with few
pathways through it - closer to **Black Forest** than to an open map - and
Black Forest has never been studied here. Worth doing: read what it
actually does, and what the open maps do differently, rather than tuning
`forest_percent` and `number_of_clumps` by feel.

Lower priority than item 1: fixing the islands does not depend on it.

### 3. Per-island land ids: committed, unshipped, unmeasured

`rms_land._island_ids` gives every unowned 60+ tile landmass its own
`land_id`, and `rms_objects.build_per_island` places gold and stone against
those ids with no distance gate at all. Committed in `92fbfb2`, deliberately
**not** in the shipped mod (`37ed72e`), and never captured.

It was written to fix "35 bare islands", a number that the beach correction
has since undercut - most of those islands are marginal by nature and the
map-wide pass gives them roughly what they warrant. Decide whether it is
still wanted before spending engine time. If it is, the one risk to check
is per-player placement: each island id is another zone, and
`starting_resources.inc` and `huntable.inc` are keyed on zone distance.

### 4. Drop the problem regions; find better windows - DONE 2026-08-15/16

Japan, New Zealand and Caribbean are land-starved at 240x240 for 8 players
(17%, 12%, 21% land). The user's decision is to **stop tuning against them**
- do not use them to evaluate resource work - and instead go find other
regions, viewports and projections that spread players across islands
without cramping anyone. No scoring function conjures land the window
lacks.


### 5. Relics - fixed but unverified

`relics.inc` is one big `if RELIC_TYPE_UNRESTRICTED / elseif _BALANCED /
elseif _PLAYER / elseif _SCATTER`. We defined none of them, so the include
was inert and **every map this project ever shipped had zero relics.**
Measured: stock Arabia 10, Thames 7, Yucatan 10, City of Lakes 14, ours 0.

Now emits `RELIC_COUNT 10` + `RELIC_TYPE_BALANCED` (Arabia's choice).
Open: which type suits a real coastline, and specifically which one puts
relics out on the empty islands. Thames uses `UNRESTRICTED` with
`RELIC_DISTANCE 0` / `RELIC_SPACING 12`. Target is 5-15, and the upper end
reads as more flavorful.

### 6. Why does copying Arabia not transfer to a coastline?

An open question worth actual exploration rather than another knob. Arabia
was taken as the reference, but it is a strange reference for this project:
**it has no water at all**, and every map here is a coastline. Its resource
rings assume land in every direction at every radius, which is precisely
what a coastline is not - and `RESOURCE_TEMPLATES.md` already found that
stock competitive water maps get their fairness from *rotational symmetry
about the map centre*, which real geography cannot supply.

Concrete anomaly to start from: Salish Sea generates cleanly (0/80 bad
starts) while looking nothing like Arabia, and Thames - `direct_placement`,
explicit `create_land`, irregular river water - is the structurally closest
stock map but is itself unusual among System A maps. Worth asking what
Thames and Salish Sea have in common that Arabia does not, rather than
continuing to treat Arabia as the target.

### 7. `choose_starts` is slow

~73s per region, up from near-instant, because annealing now runs
everywhere. Already capped the BFS and cut the budget 5x400 -> 3x150 (from
~220s). Against the ~1000-generation goal that is ~20 hours. The cost is
full-array `binary_dilation` per wavefront step; cropping to the frontier's
bounding box is the obvious fix and has not been tried.

### 8. `--spread-islands` is now misnamed

Under the new objective it does not spread across islands - small islands
score badly on available land, so Sardinia/Corsica/Sicily/Tunisia get
nothing and the picks go to France and the Balkans instead. The concept is
still right; it just has to be conditional on the island being big enough
to hold a start. Either rename it or make it enforce that explicitly.

## How to resume

The game must be running, in the Scenario Editor, with
`AA_rw_placeholder_tester` selected at Huge [240] / 8 players.

```sh
# ONE region, ~2 min, leaving the other ten alone. A full build is ~17 min
# and there is rarely a reason to pay it.
uv run python automation/build_mod.py --regions "Italy" --placeholder "Italy"
uv run python automation/install_mod.py --all

# capture one region; mod_capture regenerates the script itself, so a
# build_mod run is NOT a prerequisite for testing
uv run python automation/mod_capture.py --run-id <id> --n-samples 2 --regions "Italy"
# --extra passes flags through to the regen, for a parameter not yet in
# MOD_REGIONS: ... --extra --island-resources

uv run python automation/neutral_supply.py --mod <id> --summary   # per region
uv run python automation/neutral_supply.py --mod <id> --detail    # per island
uv run python automation/compare_starts.py --stock benchmarks --mod <id>
```

Test regions **independently and only the ones in question**. There is no
value in rebuilding or capturing a region with no islands to judge.

`mod_capture.py` aborts immediately if the game is not running, rather than
spending 90s per retry on an empty desktop - a pass burned 1.9 hours that
way. It also warns when a capture's coastline IoU is far below what the
region should score, which is the only signal that distinguishes "the
script swap never reached the game" from "unlucky seed".

**The shipped mod is `37ed72e`** - System A resources, relics, no
`resources_neutral.inc`, map-wide gold/stone neutral pass. Britain measures
28 neutral gold / 23 stone at a 13% share against stock Arabia's 24 / 27 at
14%. `mod/` is committed, and the installed copy matches it byte for byte.

## Lessons worth not relearning

- **A failed UI action is evidence about the automation until proven
  otherwise.** Stock Arabia read as "cannot generate from a mod directory"
  when the truth was an 82s generation against a 1.5s budget, plus a driver
  that re-clicked mid-generation and restarted it each time.
- **Reading `.inc` files is not a substitute for a render.**
  `RESOURCE_TEMPLATES.md` recommended raising `*_ZONE_DISTANCE` to 14; a
  real sweep showed that eliminates placement entirely (gold 25 -> 0). The
  stock default of 4 is correct.
- **Check what a metric would say about a map you already trust.** The
  zero-of-a-kind check fires on stock Arabia. Forest share said Britain was
  over-wooded when it is the most wood-*poor* map measured.
- **A relative metric cannot see "this map is poor overall."** Britain is
  simultaneously the densest-forested map and the one with the least
  reachable wood per player, because it has so little land.
- **Watch for gated includes that silently do nothing.** Relics needed a
  `RELIC_TYPE_*` define; `HUNTABLE_SMALL_GROUPS` defaults to 0. Both were
  included and both placed nothing. A third of the same kind:
  `max_distance_to_other_zones 8` is satisfiable on a mainland and
  unsatisfiable on any island, so `resources_neutral.inc` placed hundreds
  of objects and still left every island empty.
- **Write the file where the game reads it.** `install.MOD_NAME` pointed at
  "Real World Projections", a mod nothing loaded. The capture slot-swap
  wrote there and the game kept regenerating the previously installed
  script, so a two-region pass reported Salish Sea's geometry under
  Britain's and Italy's names. Coastline IoU caught it: 0.25 against a
  normal 0.80-0.90. The mod is deleted and `MOD_NAME` fixed.
- **Commit the artifact you evaluated.** The mod behind the good
  measurements was built and installed but never committed, and
  `build_mod.py` wipes `mod/` before regenerating - so it survived only in
  the game's directory, and only by luck. Commit `mod/` with the run that
  judged it.
- **A metric that includes the wrong tiles will confirm whatever you
  already think.** Counting BEACH as buildable made every small island look
  comfortably workable; half of each one is beach. The conclusion happened
  to survive the correction, on far thinner margins than claimed.
- **Bigger totals are not better coverage.** The maps carrying the *most*
  neutral supply had the emptiest islands, because a distance-gated pass
  saturates on whatever land is most abundant. Coverage is about aim, not
  volume.

## The island-resource crash, 2026-08-12

Aiming resources at an island by its own `land_id` **kills the engine
during generation**, and it is the `place_on_specific_land_id` clause that
does it - not any object placed through it.

Measured on Britain at 240/8, alternating the current script against the
one committed in `mod/` inside a single editor session (so nothing that
drifts over a session can be handed to one side):

| script | result |
|---|---|
| committed `mod/` script (no island land ids) | **5/5 generated**, 52-60s |
| current `src/` output | **0/5**, dead 65-70s in, every time |

The committed scripts also took **all 11 regions in one pass with no crash
and no recovery**, so this is not the automation and not one unlucky
region: Cramped Italy, Italy and Britain each died from the current output
and each generated from the committed one.

Which half is at fault, by cutting blocks out of the *identical* crashing
file (`automation/crash_bisect.py`):

| variant | aimed blocks left | result |
|---|---|---|
| `place_on_specific_land_id` line deleted from every block | 0 | generated |
| lands moved back to `land_id 1`, aiming lines kept | 6, naming ids that no longer exist | generated |
| `--cut copse` | 5 | crash 64.9s |
| `--cut piles` (stragglers only) | 2 | crash 63.6s |
| `--cut trees` (gold/stone only) | 4 | crash 65.5s |
| unmodified | 6 | crash, 5/5 |

So: the 139 extra `create_land` blocks on ids 10/11 are harmless by
themselves, an id that does not resolve is ignored rather than fatal, and
**any** block that successfully aims at a real non-player land is enough.
Cutting either half still crashes; cutting the aiming generates a map.

Two details worth carrying forward:

- **It has two faces.** With only the *large* island's placements left,
  generation hung past 300s rather than dying. A hang leaves the editor
  alive and busy, so the next Generate click never registers - which looks
  like a click problem and is not one.
- **It is a null dereference.** Windows reports an access violation
  reading address `0x9`, sometimes via BugSplat and sometimes via its own
  Application Error box. The second form parks an opaque window over the
  main menu, which reads as "OmniParser cannot find EDITORS".

This supersedes the copse hypothesis that `crash_bisect.py` used to record
(12 objects in 3 tight groups): cutting that block alone still crashes.

**Not yet known:** whether any formulation of per-island targeting is
safe - e.g. a tighter `land_percent`/`base_size` on the island lands, or
avoiding `land_id` targeting altogether in favour of terrain- or
distance-based placement. Every variant tested here either aims at a real
non-player land (fails) or does not aim at all (works).

## Access, measured on the 11-region HEAD pass (N=1 per map, 2026-08-12)

`automation/access_report.py --run-id head_n1`, over captures generated
from the committed scripts - i.e. **without** any per-island resource
targeting. The question it answers: does the map already do what the
island work was meant to do?

**A 2x2 camp spot is a non-issue.** Of 69 unowned islands with >= 8
non-shore tiles across all 11 maps, **0** lack a 2x2 buildable spot, and
**0** carry resources without one. Nothing needs fixing here.

**Bare islands are real, and not only tiny ones.** 37 of those 69 are
completely empty - no gold, stone, food *or* trees. "No resources" and
"no resources and no wood" are the same set, so nothing is trees-only.

| map | islands >=8 non-shore | empty | largest empty (non-shore tiles) |
|---|---|---|---|
| Chesapeake Bay | 3 | 3 | 71 |
| Scandinavia | 6 | 5 | 60 |
| Greece | 12 | 7 | 81 |
| Cramped Italy | 11 | 7 | 111 |
| Salish Sea | 8 | 6 | 29 |
| Caribbean | 8 | 4 | 15 |
| Japan | 6 | 2 | 65 |
| Black Sea | 3 | 2 | 44 |
| Italy | 7 | 1 | 33 |
| Britain | 5 | 0 | - |
| New Zealand | 0 | - | - |

An empty 8-tile rock is geography; an empty 111-tile island with camp
spots is a landing worth making with nothing on it. Both are in that
column, so read the size, not the count.

**Getting out of the base is where the maps actually bite.** Measured on
walking-distance rings with only corridors reachable from the town centre
counted (`forest_structure.py`'s definition - see the warning below), 4 of
88 starts are tight and one is sealed:

| map | player | exits@20 | blocked@20 | exits@32 | blocked@32 | where |
|---|---|---|---|---|---|---|
| Britain | 2 | 1 | 0.87 | **0** | **1.00** | 3.6E 49.0N, northern France |
| Britain | 3 | 3 | 0.43 | 2 | 0.75 | 5.4E 51.7N |
| Greece | 1 | 5 | 0.47 | 2 | 0.51 | 27.8E 39.8N |
| Caribbean | 8 | 2 | 0.74 | 2 | 0.50 | 63.8W 8.9N |

Britain's player 2 has one corridor out at 20 tiles and, at 32, no
walkable ring tile at all - every tile of a 148-tile perimeter is wood.
`pairwise_land_reachable_fraction` reports this map as fine, because the
player *is* land-connected; the corridor is simply one tile-wide-ish and
through forest.

**Ponds ringed by forest did not reproduce here.** Inland water bodies
(excluding the sea) have essentially no forest behind their shore on any
map - the highest is Japan at 0.13, and most are 0.00. Britain's three
inland ponds are on the mainland, not on the Norway island; that island
came out 1296 tiles at 42% forest with 459 camp spots and 16 resources.
**N=1 per map**, so this does not contradict "most generations" - it means
the one sample does not show it, and repeat sampling of Britain is what
would settle it.

**A measurement warning worth keeping.** A first version of
`access_report.py` measured its own rings on straight-line distance and
reported *no* walled players anywhere, including 35% blocked where
`forest_structure.py` reported 100%. Straight-line rings count open ground
lying behind a wall of trees as an exit. Walking distance from the town
centre does not. The walking-distance number is the correct one, and the
disagreement is exactly the failure this metric exists to catch.

## Forest structure on Britain and Greece, 2026-08-13

Goal: more woods, not fused into one mass, without spending the wood.
Measured on real captures, 22 generations across nine conditions.

**A single forest terrain has no spacing against itself.**
`spacing_to_other_terrain_types` is clearance from *other* terrain types,
so nothing stops one forest's clumps growing into each other. This is why
the obvious knob backfires: asking Greece for 36 clumps instead of 12 gave
it **fewer** woods (21 blobs against 28) with **61%** of the wood in the
largest instead of 33%. Greece's blocked-perimeter share would not move at
all across the single-forest conditions - 41, 47, 47, 41, 42 percent for
12 clumps, 36, 36 compact, 36 ragged, 60 compact.

Two directions were tried on clump shape and the compact one is wrong:
`clumping_factor 15` made **both** maps worse (Britain's worst start went
to 84% blocked and it regained a walled player), because a compact clump
is individually solid. Ragged (factor 4) roughly tied baseline.

**What works is splitting the budget across two forest terrain types.**
FOREST and PINE_FOREST are other terrain types to each other, so the
spacing clause applies between the woods and opens lanes. The budget is
divided, not reduced.

Shipped (`FOREST_SPLIT` in `automation/build_mod.py`): 36 clumps,
`--forest-alt PINE_FOREST`, spacing 3. Greece additionally needs
`--forest-percent 14`, because the second block is squeezed by the spacing
and under-places - a nominal 10 split 5+5 effectively spends about 5, which
cost Greece 32% of its wood.

N=3 per map, 24 player-starts each, against the N=1 baseline:

| map | wood | blobs | largest blob | blk20 | p90 | worst |
|---|---|---|---|---|---|---|
| Britain before | 21% | 27 | 37% | 22% | 58% | 60% |
| Britain after | **21%** | 93 | **7%** | 25% | **32%** | **52%** |
| Greece before | 25% | 28 | 33% | 41% | 55% | 62% |
| Greece after | **23%** | 126 | **5%** | **33%** | **43%** | **45%** |

Britain pays no wood at all; Greece pays 8%, inside the 10% budget. No
start on either map is walled, sealed or tight across those 48 starts,
against a baseline where Britain's player in France had a single corridor
out at 87% blocked and nothing walkable at all at 32 tiles.

Spacing 6 was measured and is worse than 3: it costs more wood (16%/15%)
and buys no structure.

**Controlled against the obvious confound.** Since the split under-places
its second block, most of its effect could have been "less map-wide wood".
It is not: at matched wood, a single forest at `--forest-percent 5` gives
Britain 34 blobs / 19% largest / 61% worst, where the split gives 85 / 12%
/ 35%; Greece 67 / 6% / 42% against 192 / 3% / 31%.

`--forest-last` was added while diagnosing this and is **untested**.
Spacing counts only terrain already created and the forest has always been
emitted first, right after the water, so its spacing clause has been inert
on every script shipped to date. Emitting it after the alt patches should
make it bite; the option exists, the default is unchanged.
