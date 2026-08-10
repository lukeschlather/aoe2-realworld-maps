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

## Open, roughly in order of expected payoff

### 1. Verify `resources_neutral.inc` actually fires

Still the blocker: it needs one capture and a count, and the game was
unavailable when the baseline above was measured. The mod **has** been
rebuilt and installed with the include (and the relic fix) as of
2026-08-10 - it was previously stale by a whole rework, so any capture
before this date was testing neither.

```sh
uv run python automation/mod_capture.py --run-id neutral_v1 --n-samples 1 --regions "Salish Sea"
uv run python automation/neutral_supply.py --mod neutral_v1 --detail
```

Salish Sea is the right first target: 20540 tiles at the gate, so if it
places nothing there the include is dead rather than merely squeezed.

**The warning sign stands and is now sharper:** no stock map references it,
where 42 reference `remote_resources.inc`. It was included anyway because
the signal that mattered for the 1999 trap was *staleness* (May 2020 file,
`24 JUNE 99` header, predates actor areas) and this file carries the
current May-27 timestamp and uses actor areas throughout. If it is dead,
the cheaper fallback is now known: pin `REMOTE_DISTANCE` down to ~40 and
use the include stock actually uses, rather than hand-rolling Thames's
blocks.

Note also that `resources_neutral.inc` places the **`_B` roles**, which
`MAP_CONSTANTS` does not pin - it pins only `_A`. `themes.inc` re-rolls
`HUNTABLE_B` (Deer/Mouflon) and `HERDABLE_B` (Goose/Pig/Sheep/Cow) per
generation, so neutral huntables will vary in skin between generations
where per-player ones do not. Pinning them is not obviously safe:
`HERDABLE_SMALL_B`/`HUNTABLE_SMALL_B` are `create_object_group`s from
`object_groups.inc`, not `#const`s, and `HERDABLE_B` is a group in 3 of
its 12 theme branches. Do not bundle this into the verification capture -
if the pin breaks the script, the neutral signal is lost with it.

### 2. Islands are devoid of resources

Measured above rather than assumed: Greece 5.5 unowned 60+ tile landmasses
per generation, all empty; Japan 5.0/5.0; Caribbean 4.6/4.6; Cramped Italy
4.1 of 6.0.

Item 1 may still fix this for free, but the gate table says not evenly -
the regions with the most empty islands (Japan, Caribbean, New Zealand) are
the ones whose 26-tile gate admits the least land. Capture, then read the
per-landmass table (`neutral_supply.py --detail`), not just the total.

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
