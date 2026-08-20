# What was done, and when

A dated record of the work, reconstructed 2026-08-19 from the git log, the
reports in `reports/`, and the 32 capture runs the preset registry now
indexes. Sessions are days; times are local.

Two other views of the same history, both live rather than transcribed:

- `uv run python automation/preset_cli.py history -v` - every engine capture
  run, oldest first: date, run-id, samples, the commit it ran at, the presets
  it covered, and the report it produced.
- `uv run python automation/preset_cli.py show <label>` - one map's whole
  history: parameters, every build, every capture.

Status docs carry the detail this file only points at: `MOD_STATUS.md` (the
mod), `TUNING_STATUS.md` (the window/parameter search),
`RESOURCE_REWORK_STATUS.md` (System A), `RENDER_PIPELINE.md` and
`EDITOR_AUTOMATION.md` (the capture harness), `PRESETS.md` (this record
system).

---

## 2026-07-29 - the generator exists

`1dd8b91` rwmaps: real coastline -> `create_land` discs -> a playable `.rms`,
plus the first real-render verification pipeline.

## 2026-07-30 - measuring instead of judging

TC clustering fixed, resource-reachability analysis added, coastline
consolidation and disc-cover knobs added, window-scouting and batch-capture
tooling, the first window x tuning matrix report. `b134404` set the rule the
project still runs on: capture-time analysis reports **real placement, not a
verdict**.

## 2026-07-31 - defaults chosen, and the mod exists

The 5-window x 16-condition tuning matrix (`reports/…tuning_matrix_report`),
then the `res_default_sweep`. Adopted the known-good defaults still in
`cli.py`: **50 m resolution, overlap 0.85, consolidate 4/3**, chosen from
engine captures a human judged recognisable. Aesthetic-recognisability
metrics added. `2ce4530` added the installable mod - the project's main
artifact. Reports began carrying the full resolved parameter set per
condition, and archiving their `.rms`/`.aoe2scenario`.

## 2026-08-01 - first N=10 pass, and Italy's crowding

Run **`full_pass`** (99 samples, `8626b77`,
`reports/20260801-053411_mod_report_full_pass.html`). Two findings acted on
the same day: narrow-coastline resource starvation (backstopped with
`--tight-resources`, later superseded), and Italy seating all 8 players on
one landmass - root-caused and fixed with geodesic start-spreading, shipping
**Cramped Italy** and **Italy** as separate maps. Run **`full_pass_v2`** (59
samples) re-verified.

## 2026-08-07 - stock as the yardstick

No engine time. Documented the stock resource-generation templates in full,
established that `land_and_water_resources.inc` is a **1999 orphan no
shipping map has used in five years**, and moved the donor recommendation to
Thames/Loch Ness. `RESOURCE_TEMPLATES.md`, `STOCK_MAP_INVENTORY.md`.

## 2026-08-08 - System A, and a fairness model worth the name

Replaced the 1999 include with **System A** (`74debc9`). Added stock-map
benchmarks with their own capture harness. Rebuilt the model around
**exclusive / contested / unclaimed** and around *enough* rather than
*even*; gave every player a budgeted forest. Run **`sysa_n10`** (110
samples, `e1b6e01`,
`reports/20260809-052633_mod_report_sysa_n10.html`) - still the deepest
evidence any shipped map has.

## 2026-08-09 to 08-10 - neutral supply and the islands

Neutral map resources added and made visible to the analysis, then baselined
against stock with **Arabia held out as the reference**: Arabia 14-21% is
the band, Thames 50% the outlier. Found `max_distance_to_other_zones` was
excluding islands from the neutral pass, dropped the untunable include, and
shipped a tunable island pass instead. Islands then got trees, a 2x2
camp-spot test, and beach excluded from buildable land. Forest *shape* became
measurable. Runs: `place_v1`, `islands_n2` (22), `neutral_v1`, `neutral_v3`,
`island_v1`, `trees_v1`.

## 2026-08-11 to 08-12 - the harness stops losing passes

The session that made unattended capture possible: control-finding with a
vision model, verify-before-click, crash-and-relaunch recovery mid-click,
reading mod state from the game's own record rather than the screen, a
crash-bisector, and `--from-git` so "did this change break the engine" is
answerable with the engine. Named the island-resource crash precisely: it is
`place_on_specific_land_id` aiming at a real non-player land, not any one
block (Britain 0/5 generated with it against 5/5 without). Split the map-wide
forest across terrain types so it stops fusing, and shipped that on Britain
and Greece. Runs: `head_n1` (11), `python_path_v1`, `smoke_n1`.

## 2026-08-13 - forest conditions, and icons

Nine one-condition forest runs (`fA_base` … `fI_p5single`) plus `vBritain`
and `vGreece` verification runs, all at `307c18e`/`8a27fb0`. Then map icons:
drawn from the shipped `.rms` itself, in the game's own map-selection
format.

## 2026-08-14 - the icons were rotated the wrong way

Verified in the real game, kept the capture, found the rotation was
clockwise where it had to be counter-clockwise, redrew, recaptured.

## 2026-08-15 - retirements, and screening without engine time

Repeated the placeholder-to-land cleanup so no black tiles survive, and
rebuilt every shipped script (`a9d5dfd` - the reason three shipped maps no
longer match any captured build). Added `window_candidates.py`: screen a
window for ~5s instead of ~70s by stopping before `choose_starts`. **Retired
Japan, Caribbean and New Zealand** - their projections do not read as the
real place, and re-profiling showed Japan at a median of 2 stone per player
against Arabia's 9, with 39/80 players at none. Taught `mod_capture` to
drive windows that do not ship yet, then captured all 14 candidate windows,
N=2. Runs: `placeholder_fix_britain` (5), `placeholder_fix_xcheck` (4),
`cand_smoke`.

## 2026-08-16 - the model learns to count land

Run **`candidates_n2`** (28 samples, `afeda38`,
`reports/20260816-210117_candidate_report_candidates_n2.html`). Candidates
read against stock with Arabia held out. Three model fixes in one day: wild
chickens counted as small game (a whole role had been invisible), wood added
to the wallet, and **land counted** - the one resource that cannot be topped
up. Land immediately explained the retirements a third way and about the
*shape* rather than the placement: New Zealand's worst-off player gets 0.35
of that map's own median, against 0.79-0.96 for every stock map. Then
`d56001a`: **orientation is specified in screen space**, so "north up" means
north is up - the change every pre-08-16 record has to be translated
through.

## 2026-08-17 - the harness gets measured

Retired the PowerShell driver, with one verified capture cycle for every
harness. Timed each capture phase rather than estimating it: run
`latency_4regions` (12 samples,
`reports/20260817-135412_capture_latency_latency_4regions.html`), which
found and removed 8s of every capture spent waiting for dialogs that never
come. Two run logs per harness from then on - a terse `log.txt` for agents,
`events.jsonl` for timing. Run `save_fix_check` (3).

## 2026-08-18 - Scandinavia, and geography smaller than a tile

- **Shipped Michigan and Great Britain N** (`6c9b972`) from the previous
  session's N=2 candidate pass - the mod's first additions since the
  retirements, and still N=2 each.
- Screened **13 Scandinavia windows** for the empty-sea problem, then a
  second round for connecting the seas.
- `59b3370` added **targeted geographic overrides** - `island`, `water`,
  `shallows`, `channel`, and named presets (`danish-straits`,
  `zealand-funen`, `zealand-funen-cut`) - for geography smaller than a tile:
  the Øresund is 4 km against 8.3 km/tile. Shallows are passable by boats
  *and* fordable by land units, so they add naval passage without cutting a
  land route.
- `c743241` fixed a subtle, load-bearing bug: the `truth` and `cover`
  pictures were drawn at `north + ICON_ROTATION`, i.e. 45 degrees off
  anything a player sees, while captioned "north up".
- Run **`scand_feat`** (11 samples, `1663138`,
  `reports/20260818-181500_feature_report_scand_feat.html`): the feature
  layer in the engine, read off the capture rather than off the script.
  Shallows work - 120-130 SHALLOWS tiles per capture, Baltic->Atlantic
  passable. The chosen condition was deepened to N=7 in the same run-id.
- Run **`scand_shift`** (6 samples, `8eff8f9`,
  `reports/20260818-234952_feature_report_scand_shift.html`): the window
  moved 10/15/20 tiles south. Established first that screen-up-left is
  geographic *north*, so the two halves of the request pointed opposite
  ways, and that north is the wrong way on every measure. South delivers:
  land 52.6% -> 57.4/59.5/60.5%. **Denmark fuses to the mainland** - Funen
  was a separate island in 1 of 6 samples, Zealand in none. IoU held at
  0.84-0.85.

Left behind for the next session: those 17 captures and their 6 `.rms` lived
only in gitignored `out/` - `build_feature_report.py`, unlike the candidate
and mod report builders, archives no data dir.

## 2026-08-19 - one record per map

`PRESETS.md` is the mechanism; this is what it changed.

- **Presets.** A map's window, complete resolved parameter set, builds and
  captures in one committed record, joined by hash. 92 reconstructed from
  what was already on disk: 10 shipped, 3 retired, 26 candidates, 53 paper
  screens, with 405 capture rows across 32 runs attached to the presets they
  resolve to.
- **`build_mod` builds from the registry** and reuses a build rather than
  re-annealing: a full rebuild went from ~10 minutes to ~20s and left all
  ten shipped scripts byte-identical. `MOD_REGIONS` is gone; promotion is a
  status flip.
- **Reports can be asked for a set of presets** instead of a run-id, which
  is what made choosing between the Scandinavia windows cost seconds instead
  of another capture pass:
  `reports/20260819-175336_preset_report_scandinavia.html`, 42 samples
  across 5 runs, no engine time.
- **Two facts the reconstruction settled.** Seven of the ten shipped maps
  ship a script hash-identical to one the engine measured (Great Britain N
  apart from its header comment, which is where the map name lives). Black
  Sea, Italy and Cramped Italy do not: they were rebuilt on 2026-08-15,
  after their last capture, so their engine evidence is about older scripts.

## 2026-08-20 - Scandinavia is the shift-15 window

No engine time. The evidence was already on disk: run `scand_shift`
(2026-08-18) for the southern windows, and runs `scand_sound` /
`scand_sound_top` the previous evening for the N=10 pass on the full-sound
variant that was going to ship instead
(`reports/20260819-215621_preset_report_scand_sound.html`, `bc0bc20`).

- **`scand-shift-15` promoted, shipped as "Scandinavia"; the old
  `scandinavia` retired** (`e5aea9d`). Same place 15 tiles south, with the
  `zealand-funen` overrides. Land 52.1-53.0% -> 59.4-59.7% at the same IoU
  (0.847-0.848), TC separation 47.9/40.0 -> 49.7, 8 TCs and one fully
  reachable landmass in both samples, AI map type MEDITERRANEAN -> COASTAL.
  The retired preset keeps its 25 captures across 5 runs - it is the baseline
  every Scandinavia candidate was read against.
- **It ships on N=2, and one of the two samples is under the land band.**
  Exclusive land min/median is 0.77 and 0.81 against the 0.79-0.96 every
  stock map holds; the sound variant measured 0.88 at N=10 but failed at
  separating Zealand, which is what it existed for. Three of the ten maps -
  Michigan, Great Britain N, Scandinavia - now ship at a window with no N=10
  pass behind it. That is the obvious next capture pass.
- **`build_mod` records the copy it ships** (`0723953`). `preset_cli.py audit`
  looks for a build with a `mod/` path to answer "is what ships what was
  captured?", and nothing wrote one for a map promoted after the registry was
  reconstructed - `preset_import` only knows the frozen
  `MOD_REGIONS_AT_IMPORT` list. Scandinavia read "no build recorded in mod/",
  indistinguishable from shipping an uncaptured script; it reads "YES apart
  from the header comment - scand_shift" now. The other nine shipped scripts
  came out byte-identical to before.

## 2026-08-20 (later) - fords across the British Isles

Three engine passes, 18 captures, to make one feature work. All three reports
are committed; `britain-crossings-anchored` is the answer.

- **The shallows facility, used the other way round.** A channel laid across
  *land* adds a sea route, which is what the Danish straits use it for. Laid
  across *water* the same `create_land` of SHALLOWS adds a **land** route,
  because shallows are fordable. `britain-crossings` is three of them - North
  Channel, St George's Channel, Strait of Dover - so land units can walk
  between the three landmasses both Britain windows put on the map.
- **`automation/crossing_model.py`** predicts a chain on paper, carving discs
  where the *script* puts them rather than where `features.py` computes them.
  That is the `bc0bc20` lesson applied forward, and it was still not enough
  twice over.
- **Run `britain_crossings` (18 km, N=3 per window)**
  `reports/20260820-023109…` superseded by the regenerated
  `20260820-024754_crossing_report_britain_crossings.html`. SHALLOWS render -
  579-656 tiles - so the mechanism works. Dover 6/6, North Channel 4/6,
  St George's 4/6. Measured with water as the only barrier and again with
  forest: the two agreed in all 18 checks, so what shuts a chain is water.
  The model had said all three were continuous with 2.4 tiles to spare; it
  stamps clean discs and the engine grows ragged ones.
- **Run `britain_crossings_wide` (24 km)**
  `reports/20260820-024701_crossing_report_britain_crossings_wide.html`.
  Chosen on `base_size`, which `_land_block` sets to `round(radius * 0.35)`:
  18 km gives 1 (a 3x3 seed) and 24 km gives 2 (5x5), so consecutive seeds
  abut before any growth. North Channel 4/6 -> **6/6**. St George's stayed at
  4/6, failing on the same window both times.
- **It was never the radius.** Both ends of the St George's line sat
  offshore, one window each - Welsh end in water on `great-britain-n`, Irish
  end in water on `britain` - and the crossing failed on whichever window its
  offshore end was on. Shallows paint last, over the coastline, so an end disc
  a tile off a headland overlaps it in a thin crescent and then converts the
  crescent to shallows, cutting the tip off instead of joining to it.
  Pembrokeshire came out as 25-, 5- and 5-tile fragments with Britain proper
  4.1 tiles beyond. A wider radius makes that worse, which is why 24 km did
  not help. `crossing_model.py` checks it now: per end, distance to the
  nearest >=500-tile landmass *and* how much of the end disc lands on one -
  "land within a radius" passes a disc 1 tile offshore, which is exactly the
  failing case.
- **Run `britain_crossings_anchored`**
  `reports/20260820-031519_crossing_report_britain_crossings_anchored.html`.
  St George's moved to -4.90,51.95 / -6.60,52.30. **18 of 18 fords open**,
  Ireland to France on foot in all six, one dominant walkable piece of
  12,821-17,493 tiles. 8 TCs and unchanged TC separation every sample, IoU
  0.809-0.851 against shipped baselines of 0.805-0.848 - no fidelity cost.
  Cost is 996-1,050 shallows tiles, 1.7-1.8% of the map.
- **Open, and it decides whether these ship: can a ship enter terrain 4?**
  Both Irish Sea exits are spanned by shallows in all six captures. If ships
  are blocked the Irish Sea is an enclosed lake and a fleet built there is
  trapped; Dover survives either way. Nothing here settles it, because
  `land_mask` reads shallows as sea *by construction* - the same assumption
  under the Baltic-to-Kattegat claim from run `scand_feat`. The report prints
  both readings per route.
- **A metric that cannot see a ford.** `sample_analysis.analyze_capture` reads
  `land_mask`, so all 18 captures report `landmasses=3` and
  `reachable=0.29/0.32`, identical to the shipped baselines, on maps where a
  villager walks Ireland to France. Left alone rather than quietly changed: it
  would move a metric under 400+ recorded samples.

Both presets stay `candidate`; neither shipped map is touched.


## 2026-08-20 (later still) - four ford maps ship

Promoted the 18 km and 24 km crossings presets on both Britain windows, at
the user's pick, and shipped them under the names they asked for:

| shipped name       | preset                           | fords |
|--------------------|----------------------------------|-------|
| `Brit Shallows`    | `britain-crossings`              | 18 km |
| `Brit LShallows`   | `britain-crossings-wide`         | 24 km |
| `Brit Shallows N`  | `great-britain-n-crossings`      | 18 km |
| `Brit LShallows N` | `great-britain-n-crossings-wide` | 24 km |

`build_mod.py` reused all four builds from cache, hash-verified, so each
shipped script is the script the engine measured in runs `britain_crossings`
and `britain_crossings_wide` - `preset_cli.py audit` reports all four as
"YES apart from the header comment (renamed)". 14/14 maps in the mod, 0
regenerated, and `install_mod.py --all` re-synced both variants to
`mods/local/`.

The pair that measured 18 of 18 fords open,
`britain-crossings-anchored` / `great-britain-n-crossings-anchored`, stays
`candidate` and does not ship. On what ships, St George's Channel was shut
in 2 of 6 captures on each radius, so Ireland is not always walkable; the
North Channel is 4/6 at 18 km and 6/6 at 24 km, Dover 6/6 on both. That is
the difference between the two names in game.

## 2026-08-20 (later still) - the Welsh end gets something to land on

**Played first, measured second.** Several hand-driven generations of both
wide windows joined Wales to Ireland every time but one, and the break was on
the *18 km* preset, with a small island off St Davids Head - Ramsey, which
usually merges into Britain - taking the ford instead of Britain. So the
chain was continuous and still joined nothing.

- **There is no Ramsey piece in the mask.** Ramsey is *water*, 1.0 tile off
  Britain on `britain` and 2.2 off on `great-britain-n`; the island in the
  game comes out of the disc cover and the engine's land growth. On
  `great-britain-n` the wide St George's line's Welsh end (-5.25,51.88) is
  itself water 1.0 tile offshore and St Davids 1.4 offshore.
- **`britain-crossings-ramsey`** keeps the three wide lines where they are and
  adds a fourth, -5.32,51.87 to -5.05,51.84 at 24 km, from the water off
  Ramsey into solid Pembrokeshire. Chosen on quantised positions and end
  anchoring: 2 distinct positions, 2.8/2.0-tile steps, and 35/61 and 38/61 of
  the inland end disc on Britain against the Strait of Dover's 30/61. A
  longer line to Haverfordwest scored 51/61 and was rejected - it floods that
  much more of Pembrokeshire into lagoon for no better anchor.
- **Run `britain_ramsey` (N=3 per window)**
  `reports/20260820-111452_crossing_report_britain_ramsey.html`. **18 of 18
  fords open**, St George's included on all six. 913-985 shallows tiles
  against the wide preset's 812-915, IoU 0.793-0.848 against 0.797-0.847 - no
  fidelity cost for the extra chain.
- **The baseline failure reproduced, on the regenerated wide report**
  `reports/20260820-102447_crossing_report_britain_crossings_wide.html`:
  St George's SHUT in 2 of 6, both on `great-britain-n`, which is the window
  whose Welsh end is offshore. On `britain` it was open 3/3 - which is why
  hand play mostly saw it work.

### What the reports show now, and three harness fixes

- **The old analysis render is retired as a report visual.** Fish and whales
  are drawn as white dots, sized by kind: a map carries several hundred deep
  fish against ~120 shore (379/126 measured), and one size made the sea a
  starfield that buried the coast. Every builder draws through the new
  `automation/capture_render.py` - render, ownership walk and cache in one
  place - and falls back to the stored `preview_png_b64` only when the
  scenario is gone.
- **`ensure_ready` used to abort a pass it could repair.** `recover()` reports
  "game already running" and does nothing when the game is up with mods on, so
  `setup()` walked a main menu that was not there. A hand-driven session
  leaves exactly that state, and nothing in the editor puts the Random Map
  selector back - the placeholder is picked up by sorting first on a *fresh*
  editor. It closes the game now. The not-foreground branch was also
  swallowing every other preflight failure, because "not foreground" is the
  first thing preflight checks.
- **`setup()`'s fixed-coordinate clicks now wait for the game to own the
  input.** With a Steam chat window on top, AppActivate could not take the
  foreground and the walk clicked on anyway, into whatever was there.
- **A capture is checked against being our map before it is analysed.** An
  interrupted pass left the editor on Blank Map / Small (3 player) [144],
  preflight passed it - it can see the mod state and the selector template,
  not the Map Size or the Blank/Random radio - and six samples generated stock
  Arabia at 144x144. The only symptom was a numpy broadcast error out of the
  aesthetic metrics. `save()` types `rw_capture_slot`, so a capture under any
  other name did not come from our slot, and every capture here is 240x240;
  either check now aborts the pass.
