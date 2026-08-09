# Resource rework status - where to pick this up

Written 2026-08-09 at the end of a long session, as a handoff. Reading
order for anything resource-related:

1. `STOCK_MAP_INVENTORY.md` - what is on disk, script name vs UI name
2. `RESOURCE_TEMPLATES.md` - the two stock resource systems, and the
   measured stock budgets. **Authoritative.**
3. this file - what changed in the rework, and what is still open
4. `MOD_STATUS.md` / `TUNING_STATUS.md` - history only, carry known-bad
   assumptions, `MOD_STATUS.md` has a banner saying so

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

## Open, roughly in order of expected payoff

### 1. Verify `resources_neutral.inc` actually fires

**Our maps have no neutral resources at all.** Everything System A places
is per-player, so every resource belongs to somebody and there is nothing
on the map worth leaving base for. Measured, resources no player can reach
from home:

| map | gold | stone | forage | deer |
|---|---|---|---|---|
| stock Thames | 40 | 8 | **126** | **99** |
| stock Yucatan | 114 | **141** | 78 | 57 |
| stock Arabia | 24 | 29 | 0 | 9 |
| ours (Salish Sea, Black Sea) | **0** | **0** | **0** | **0** |

Thames places more neutral forage and deer than it gives all eight players
combined. `ResourceFlavor.neutral_resources` now includes
`includes/resources_neutral.inc`, which places gold/stone/forage plus the
`_B` huntable/herdable/lureable roles at `min_distance_to_players 26`.

**Unverified, and it carries a warning sign:** no stock map references that
include - the same signal that made the 1999 file a trap. It was included
anyway because the signal that actually mattered there was *staleness* (May
2020 file, `24 JUNE 99` header, predates actor areas) and this file carries
the current May-27 timestamp shared by `starting_resources.inc` and
`forest.inc` and uses actor areas throughout. **Capture one map and count.**
If it is dead, hand-roll the blocks the way `Thames.rms` does (it uses
`place_on_specific_land_id` against lands it creates itself, so the
positioning needs adapting, but the block structure is directly reusable).

### 2. Islands are devoid of resources

Consequence of two things compounding: the rescored placement will not seat
a player on a small island (correctly - a 454-tile Corsica start is a bad
start), and there are no neutral resources. So the islands end up empty
rather than being a contested prize.

Item 1 may fix this for free - neutral resources are placed 26+ tiles from
any player, which on these maps *is* the islands. Measure it before
designing anything more elaborate: capture, then check the neutral counts
per landmass rather than per player.

### 3. Relics - fixed but unverified

`relics.inc` is one big `if RELIC_TYPE_UNRESTRICTED / elseif _BALANCED /
elseif _PLAYER / elseif _SCATTER`. We defined none of them, so the include
was inert and **every map this project ever shipped had zero relics.**
Measured: stock Arabia 10, Thames 7, Yucatan 10, City of Lakes 14, ours 0.

Now emits `RELIC_COUNT 10` + `RELIC_TYPE_BALANCED` (Arabia's choice).
Open: which type suits a real coastline, and specifically which one puts
relics out on the empty islands. Thames uses `UNRESTRICTED` with
`RELIC_DISTANCE 0` / `RELIC_SPACING 12`. Target is 5-15, and the upper end
reads as more flavorful.

### 4. Why does copying Arabia not transfer to a coastline?

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

### 5. Hard regions need different windows, not better algorithms

Japan (17% land), New Zealand (12%), Caribbean (21%). Across 880 starts,
reachable open ground below 600 tiles broke a start 68% of the time and
above 1000 essentially never. These windows do not contain enough land per
player at 240x240 for eight players. The user's own read: pick different
projections/orientations that spread players across islands without anyone
being cramped. No scoring function can conjure land the window lacks.

### 6. `choose_starts` is slow

~73s per region, up from near-instant, because annealing now runs
everywhere. Already capped the BFS and cut the budget 5x400 -> 3x150 (from
~220s). Against the ~1000-generation goal that is ~20 hours. The cost is
full-array `binary_dilation` per wavefront step; cropping to the frontier's
bounding box is the obvious fix and has not been tried.

### 7. `--spread-islands` is now misnamed

Under the new objective it does not spread across islands - small islands
score badly on available land, so Sardinia/Corsica/Sicily/Tunisia get
nothing and the picks go to France and the Balkans instead. The concept is
still right; it just has to be conditional on the island being big enough
to hold a start. Either rename it or make it enforce that explicitly.

## How to resume

The game must be running, in the Scenario Editor, with
`AA_rw_placeholder_tester` selected at Huge [240] / 8 players.

```sh
# rebuild + install the shipped mod (the installed copy is only as fresh as
# the last build_mod.py run - it was stale by a whole rework once)
uv run python automation/build_mod.py
uv run python automation/install_mod.py --all

# resume the interrupted N=1 sanity pass (skips regions already captured)
uv run python automation/mod_capture.py --run-id place_v1 --n-samples 1

# compare anything captured against the stock benchmarks
uv run python automation/compare_starts.py --stock benchmarks --mod place_v1
```

`mod_capture.py` now aborts immediately if the game is not running, rather
than spending 90s per retry on an empty desktop - a pass burned 1.9 hours
that way, reporting "Generate Map never registered a seed change" for ten
regions in a row when the game had simply exited.

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
  included and both placed nothing.
