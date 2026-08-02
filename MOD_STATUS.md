# Mod status

Read this first when resuming the "Real World Maps" mod-building work.
Originally written 2026-08-01 after a context reset; updated same-day
after the N=10-per-region real-engine capture pass actually ran, so this
now reflects real fairness data, not just a plan.

For the parameter-tuning research history that led here (window search,
known-good defaults, aesthetic-recognizability metrics), see
`TUNING_STATUS.md` - this file picks up from "the mod now exists" onward.

## Where things stand

The project has shifted from parameter-tuning research to actually
shipping a playable mod - per the user's own framing, this is now **the
main artifact**, and reports from here on should focus on explaining the
process behind each map, not just raw stats.

**Done, committed:**
- `mod/Real World Maps/` - 10 playable `.rms` scripts, filenames prefixed
  `RW ` so they sort together in the in-game "Random Map location" list
  among 100+ subscribed-mod entries. Four are additionally tagged
  `(Broken)` - see "N=10 capture pass results" below.
- `mod/Real World Maps (Debug)/` - the same 10, plus an
  `AA_rw_placeholder_tester.rms` slot so this project's existing tuning
  automation can keep swapping candidate scripts in without hitting the
  Scenario Editor's crash-on-new-list-entry bug
- `automation/build_mod.py` - regenerates all 10 from source (cheap,
  Python-only, no engine time) - **the source of truth for region
  definitions**, see `MOD_REGIONS` in that file. Does a full
  `shutil.rmtree` of both mod roots before regenerating, so renames (like
  the `RW `/`(Broken)` prefixing) never leave stale old filenames behind.
- `automation/mod_capture.py` + `automation/build_mod_report.py` - the
  N=10-per-region real-engine capture pass and its report builder, see
  below.
- `.gitattributes` now forces `*.rms text eol=lf` - a real bug caught
  before commit: without it, a fresh checkout under Windows'
  `core.autocrlf=true` would silently corrupt every shipped script to CRLF
- Installed to the local AoE2 DE mods folder (`mods/local/`) under the
  Steam-id profile folder. `automation/install_mod.py` now automates the
  re-sync (delete + copy) after any `build_mod.py` rerun - defaults to the
  debug variant, `--mod "Real World Maps"` or `--all` for the rest.

**The 10 regions:**

| region | source | verified against real captures? |
|---|---|---|
| Salish Sea | `victoria_recenter` window (renamed), consolidate width overridden to 5/3 (cell `0a8509cf`, called good on sight) | **yes** - extensively, see `TUNING_STATUS.md` |
| Italy | `--region italy`, same window as the old `20260730-161348_italy_240_report.html` | yes, N=10 - see findings below |
| Britain, Greece, Japan, Chesapeake Bay, Black Sea, Scandinavia, Caribbean, New Zealand | `--region <x>`, bare rwmaps defaults, no overrides | yes, N=10 - see findings below |

Japan additionally passes `--rotate 35` (inherited from a pre-existing
`batch_240.py` precedent - a geometric/orientation choice, not a
generation-quality one).

**Auto-detected AI map types** (read straight off the generated scripts via
`grep ai_info_map_type`, current as of this writing):
- `Salish Sea`: ARCHIPELAGO
- `Britain`, `Caribbean`, `Japan`, `New Zealand`: ISLANDS
- `Black Sea`, `Chesapeake Bay`: COASTAL
- `Greece`, `Italy`, `Scandinavia`: MEDITERRANEAN

**Don't read these as climate labels.** `choose_ai_map_type()` in
`src/rwmaps/analysis.py` picks purely on "will the AI try to fish/go
naval": `ARABIA` = dry, no water at all; `COASTAL` = land map with real,
reachable water; `MEDITERRANEAN` = high water fraction but players still
land-connected to each other; `ISLANDS`/`ARCHIPELAGO` = players NOT
land-connected (islands vs. archipelago split on total water fraction).
Scandinavia landing on MEDITERRANEAN isn't obviously wrong under that
definition - it just means high water% + still land-connected - but
hasn't been sanity-checked against an actual capture yet. That
sanity-check is part of "making sure the AI is set up properly," the
still-open task below.

## N=10 capture pass results (2026-08-01, run-id `full_pass`)

`automation/mod_capture.py --run-id full_pass` ran all 10 regions x 10
samples (99/100 - Scandinavia sample 5 hit the documented intermittent
"Generate Map never registered a seed change" failure, retries exhausted,
not investigated further since 9/10 is still plenty of N).
`automation/build_mod_report.py --run-id full_pass` built the report -
see the newest `reports/*_mod_report_full_pass.html` (filenames are
timestamp-prefixed now, see below).

**Zero-of-a-kind rate per region** (fraction of 10 samples with at least
one player missing a resource kind entirely), as originally captured:
- Chesapeake Bay, Black Sea, Salish Sea: 0/10 - clean.
- Greece, Scandinavia: 1/10 (Scandinavia 1/9) - essentially noise-floor.
- Italy: 4/10 - **root-caused, see "Italy" below - a different mechanism
  from the other four, not yet fixed.**
- Britain: 8/10, Japan/Caribbean/New Zealand: 10/10 - **all four
  root-caused and fixed as of 2026-08-01, see "Narrow-coastline resource
  starvation" below.**

### Narrow-coastline resource starvation (Britain/Japan/Caribbean/New Zealand) - FIXED

Root cause, confirmed by direct measurement, not guesswork: land resources
(`GOLD`/`STONE`/`SHEEP`/`BOAR`/`DEER`) come entirely from the stock,
unmodified `land_and_water_resources.inc` (`#include_drs` in
`src/rwmaps/rms.py` - this is the exact same generic include the vanilla
Arabia/Arena/etc. scripts use). Its distances assume a roughly boxy
landmass with land in every direction: far gold needs land 25-35 tiles
out, far stone 20-26, deer 14-30 - and additionally, gold/stone require
`max_distance_to_other_zones 7`. A narrow coastal strip (New Zealand,
Japan, Caribbean archipelago fingers, Britain's coastline) simply doesn't
have land that far out in the cross-strip direction, so those placements
fail silently (AoE2 RMS placement failures are silent - fewer objects
spawn, no error) - measured directly per player via land-fraction-within-
ring: Italy/Salish Sea sit at 0.85-1.00 land coverage in every resource
ring; New Zealand/Japan players commonly saw 0.10-0.40 in the 18-35 tile
rings. Per-region breakdown of *which* resource kind actually failed
(summed over 10 samples x 8 players = 80 max): New Zealand stone=67,
gold=21; Japan stone=57, gold=18; Caribbean stone=55, gold=22; Britain
stone=14 (its milder 8/10 rate) - boar/deer (stock ranges top out at
22/30, not 35) were only ever single-digit misses. Stone was always the
dominant failure.

**Fix**: `RmsOptions.tight_resource_backstop` / CLI `--tight-resources`
(`src/rwmaps/rms.py`, `src/rwmaps/cli.py`) adds a supplemental close-range
(8-14/16 tile) gold/stone/deer/boar placement on top of the stock include
- doesn't touch or replace the stock include's own attempts (which still
run, and still succeed fine on wide coastlines), so it's opt-in per region
rather than a blanket change. A first version covering only gold/stone,
tested on New Zealand (N=10 real engine samples), eliminated stone/gold
zero-of-a-kind entirely (0/10, down from ~84%/26% of player-samples) but
left a residual 7/10 any-zero rate from occasional single boar/deer
misses; extending the backstop to also cover deer/boar brought New Zealand
to **0/10 any-zero** in a second N=10 retest. Re-tested (N=10 each) on
Japan (10/10 -> 1/10, single deer miss), Caribbean (10/10 -> 0/10), and
Britain (8/10 -> 0/10) with the same fix - all four now ship with
`--tight-resources` in `MOD_REGIONS` (`automation/build_mod.py`), and
`BROKEN_REGIONS` is now empty. The installed local mod has been rebuilt
and re-synced with these fixes.

**Not yet done**: a fresh full N=10 `mod_capture.py` pass covering *all*
10 regions under the new settings (only ad-hoc 10-sample spot-checks per
fixed region have been run so far, not through the permanent
`mod_capture.py`/`build_mod_report.py` pipeline against a new `--run-id`)
- worth doing before calling this phase fully closed, so the mod report
reflects the fix rather than the stale `full_pass` data.

### Italy (4/10) - crowding, ROOT-CAUSED AND FIXED 2026-08-01

`choose_starts()` in `src/rwmaps/analysis.py` is *structurally* able to
spread players across separate landmasses (`same_component=False` keeps
every component >=`min_component_tiles` as a candidate pool - see its own
docstring), but in practice does not by default. Reproduced directly on
Italy's exact shipped window/params: 4 components >=200 tiles exist
(mainland 26546 tiles, plus ~1691/~811/~287 - Tunisia, Sardinia, Corsica by
size and lon/lat centroid, confirmed via `MapWindow.tile_lonlat()`), and
**all 8 default-mode starts land on the single mainland component, none on
the other three**. Traced every Italy zero-of-a-kind case in the capture
data to a resource at the *exact same* walking distance from the "zero"
player and a neighbor - `resource_ownership()` breaks that tie by player
index (lower wins) - a real but secondary bug, a symptom of the crowding
(too-close neighbors on the same overcrowded landmass), not the root cause.

**This is a genuinely different failure mode from the narrow-coastline
resource starvation above**: Italy's players are NOT resource-starved by
geometry (the mainland is huge, land-fraction-in-ring is ~1.0 everywhere)
- the problem is purely that `choose_starts()` never uses the other 3
landmasses it structurally could, and even among the mainland's own 8
picks, farthest-point selection can wander into France or the Balkans (a
window's "mainland" component is a REAL, not raster-artifact, landmass
that includes them - Italy truly is land-connected there) instead of the
Italian peninsula itself.

**Two rejected fixes** (both left no trace in the shipped code, just
recorded here so nobody re-tries them): forcing exactly one guaranteed
seed per qualifying component (wrong once a component barely clears
`min_component_tiles` but is dwarfed by its neighbors - the user's
objection); biasing candidate quality by raw distance from the window's
own geometric center (wrong the other way - a huge component can
legitimately have great land far from center, e.g. in France, and there's
no reason to avoid it just for being far from an artifact of how the
window happens to be centered - also the user's objection, and it
empirically failed to reach the peninsula anyway).

**What actually worked**: a new `spread_islands: bool = False` parameter on
`choose_starts()` (`src/rwmaps/analysis.py`) and CLI `--spread-islands`,
off by default - confirmed byte-identical output on all 10 shipped regions
with it off, see git history. Three pieces, all necessary (confirmed by
removing each one individually and watching the result degrade):

1. **Geodesic, not Euclidean, distance** for both farthest-point growth and
   Lloyd relaxation (`_lloyd_relax`, `_farthest_point_pack_from_seed_geodesic`)
   - a peninsula is genuinely far by walking distance even though it isn't
   far in a straight line, the same reasoning that already makes a
   genuinely separate island register as isolated. This alone was not
   sufficient (see below).
2. **One seed forced per qualifying component**, but with the *remaining*
   seats grown ONLY within the single largest component
   (`_forced_component_seeds_geodesic`) - geodesic growth alone can still
   double up on the same modest island (confirmed: it kept re-seating a
   second pick on Tunisia while Corsica sat empty, since "maximize the
   minimum pairwise distance" has no notion of "don't reuse a landmass
   while another sits unused").
3. **Multi-start selection scored by (duplicate penalty, distinct
   components used, geodesic separation)** in that priority order, not
   separation alone (`_lloyd_relax_multistart`) - otherwise a
   duplicate-island configuration can numerically out-score a properly
   spread one on raw separation and win anyway.

Result on Italy: 4/4 real landmasses used (mainland, Tunisia, Sardinia,
Corsica), no duplicates, no landmass skipped, and the Italian peninsula
itself reliably gets 1-2 of the mainland's picks (confirmed: Po Valley +
Piedmont in the validated run) rather than zero. Confirmed via a full
Python-only (no engine time) sweep across all 10 shipped regions with
`--spread-islands` forced on that nothing crashes or hangs, and via a
byte-identical-output diff with it off that no other region's placement
changed at all.

**Shipped as two variants** (both with `--tight-resources` too, since
Corsica/Sardinia/Tunisia are themselves narrow islands and would likely
hit the same starvation problem otherwise - not yet re-verified with a
real engine capture, see below): `RW Cramped Italy.rms` (the original,
all-8-on-mainland behavior - the user explicitly wants to keep and
playtest this, "not necessarily bad, it's just different") and
`RW Italy.rms` (`--spread-islands`, the new spread-across-islands
behavior). `automation/build_mod.py`'s `MOD_REGIONS` has both.

**Not yet done**: neither Italy variant nor the four narrow-coastline
fixes have been through a real N=10 engine capture pass since these
changes - only ad-hoc Python-only geometry checks (Cramped Italy did get
one earlier N=10 engine spot-check with `--tight-resources` alone, before
`--spread-islands` existed, at 0/10 any-zero). Worth doing before calling
this phase fully closed.

## How to resume

1. Read this file, then `TUNING_STATUS.md` if deeper research history is
   needed for context.
2. Run a fresh full N=10 `mod_capture.py` pass (new `--run-id`) covering
   all 11 shipped regions (10 + the new Cramped Italy/Italy split) to
   confirm everything holds up at full N through the real engine, and
   get `build_mod_report.py`'s report caught up - this is the main
   remaining task, nothing is conceptually unresolved anymore.
3. If the "Italy" (spread-islands) variant's mainland-internal spread
   looks worth tightening further (two of its picks landed close together
   - Po Valley and Piedmont, both northern Italy - in the validated run),
   that's a nice-to-have, not a correctness bug: no landmass is skipped or
   doubled, which was the actual bar the user set ("perfect distribution
   is not necessarily a goal... provided it's not consistently weighted
   away from specific areas").
