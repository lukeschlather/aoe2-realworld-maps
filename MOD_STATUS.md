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
one player missing a resource kind entirely):
- Chesapeake Bay, Black Sea, Salish Sea: 0/10 - clean.
- Greece, Scandinavia: 1/10 (Scandinavia 1/9) - essentially noise-floor.
- Italy: 4/10 - **root-caused, see below, not a generation defect.**
- Britain: 8/10, Japan/Caribbean/New Zealand: 10/10 - tagged
  `RW (Broken) <name>.rms` in the shipped mod pending an actual fix.

**Root cause found for Italy (and likely the four "Broken" ones too):**
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

**Likely explains Britain/Japan/Caribbean/New Zealand too** (all
ISLANDS-type, all picked *because* they have real island geography) but
this has NOT been checked per-region yet - only Italy has been traced in
detail so far.

**Not yet decided / explicitly deferred by the user (2026-08-01):** how to
fix `choose_starts()` - e.g. reserve start slots per qualifying landmass
proportional to size, or some other explicit spreading mechanism - and
whether to fix the `resource_ownership()` tie-break at the same time.
Whatever the fix, it should be prototyped and reviewed on Italy alone
before spending another ~1hr of engine time re-running the full N=10 pass
across all 10 regions.

## How to resume

1. Read this file, then `TUNING_STATUS.md` if deeper research history is
   needed for context.
2. Decide the `choose_starts()` island-spreading fix design (see above) -
   this is the actual next open question, not a rebuild/rerun task.
3. Once a fix is picked: prototype it, sanity-check Italy's start
   placement in Python (cheap, no engine time) before committing to a
   full engine rerun.
4. Re-run `automation/mod_capture.py` (new `--run-id`, ~1hr, drives real
   mouse/keyboard - don't touch the machine meanwhile) and
   `automation/build_mod_report.py` to confirm the fix actually improves
   the zero-of-a-kind rates, especially for the four `(Broken)`-tagged
   regions.
5. Remove the `(Broken)` tag (and re-sync the installed local mod) for
   any region the rerun confirms is fixed.
