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
  Steam-id profile folder - re-sync manually after any `build_mod.py`
  rerun (delete + copy both mod roots; there's no install script for this
  yet, it's been done by hand each time so far).

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

### Italy (4/10) - a different mechanism, NOT yet fixed

`choose_starts()` in `src/rwmaps/analysis.py` is *structurally* able to
spread players across separate landmasses (`same_component=False` keeps
every component >=`min_component_tiles` as a candidate pool - see its own
docstring), but in practice does not. Reproduced directly on Italy's exact
shipped window/params: 4 components >=200 tiles exist (mainland 26546
tiles, plus ~1691/~811/~287 - almost certainly Sicily-ish, Sardinia,
Corsica by size), and **all 8 chosen starts land on the single mainland
component, none on the other three**. The farthest-point-pack + quality
floor is satisfied by mainland-only candidates before it ever needs to
reach for a smaller island, so nothing forces it to use them.

This is exactly why the stock AoE2 "Italy" map spreads players across
Italy/Corsica/Sardinia/Tunisia rather than the mainland alone, and why our
version instead crowds all 8 onto one landmass - which is *why* two
players (e.g. Salish-Sea-adjacent P5/P8 in Italy sample 0) end up close
enough that their resource rings overlap and tie. Traced every Italy
zero-of-a-kind case in the capture data: each one is a resource at the
*exact same* walking distance from both the "zero" player and a neighbor
- `resource_ownership()` breaks that tie by player index (lower wins),
so the higher-numbered player shows "zero" even though the resource is
equally reachable to them in principle. That tie-break is a real, minor,
separate bug worth fixing eventually, but it's a symptom of the crowding,
not the root cause - the user's own diagnosis (2026-08-01) landed on the
crowding as "the real problem," and direct reproduction above confirms it.

This is a genuinely different failure mode from the narrow-coastline
resource starvation above (Italy's players are NOT resource-starved by
geometry - the mainland is huge and land-fraction-in-ring is ~1.0
everywhere; the problem is purely that `choose_starts()` never uses the
other 3 landmasses it could). **Not yet decided / explicitly deferred by
the user (2026-08-01):** how to fix `choose_starts()` - e.g. reserve start
slots per qualifying landmass proportional to size, or some other
explicit spreading mechanism - and whether to fix the
`resource_ownership()` tie-break at the same time. Whatever the fix, it
should be prototyped and reviewed on Italy alone (cheap, Python-only, no
engine time) before spending engine time re-verifying.

## How to resume

1. Read this file, then `TUNING_STATUS.md` if deeper research history is
   needed for context.
2. Optionally: run a fresh full N=10 `mod_capture.py` pass (new
   `--run-id`) to confirm the four narrow-coastline fixes hold up at full
   N and get the mod report caught up - not urgent, ad-hoc spot-checks
   already confirm the fix works.
3. Decide the `choose_starts()` island-spreading fix design for Italy (see
   above) - this is the real open question, deferred by the user
   2026-08-01, not yet started.
4. Once a fix is picked: prototype it, sanity-check Italy's start
   placement in Python (cheap, no engine time) before committing to a
   full engine rerun.
