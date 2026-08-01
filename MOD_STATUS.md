# Mod status

Read this first when resuming the "Real World Maps" mod-building work.
Written 2026-08-01 specifically because the conversation building it was
reset for context - this captures exactly where things stand and what's
not yet decided, so a fresh session doesn't have to re-derive it.

For the parameter-tuning research history that led here (window search,
known-good defaults, aesthetic-recognizability metrics), see
`TUNING_STATUS.md` - this file picks up from "the mod now exists" onward.

## Where things stand

The project has shifted from parameter-tuning research to actually
shipping a playable mod - per the user's own framing, this is now **the
main artifact**, and reports from here on should focus on explaining the
process behind each map, not just raw stats.

**Done, committed:**
- `mod/Real World Maps/` - 10 playable `.rms` scripts (just scripts, no
  fixed scenario - cheap to generate and install, all playable)
- `mod/Real World Maps (Debug)/` - the same 10, plus an
  `AA_rw_placeholder_tester.rms` slot so this project's existing tuning
  automation can keep swapping candidate scripts in without hitting the
  Scenario Editor's crash-on-new-list-entry bug
- `automation/build_mod.py` - regenerates all 10 from source (cheap,
  Python-only, no engine time) - **the source of truth for region
  definitions**, see `MOD_REGIONS` in that file
- `.gitattributes` now forces `*.rms text eol=lf` - a real bug caught
  before commit: without it, a fresh checkout under Windows'
  `core.autocrlf=true` would silently corrupt every shipped script to CRLF

**The 10 regions:**

| region | source | verified against real captures? |
|---|---|---|
| Salish Sea | `victoria_recenter` window (renamed), consolidate width overridden to 5/3 (cell `0a8509cf`, called good on sight) | **yes** - extensively, see `TUNING_STATUS.md` |
| Italy | `--region italy`, same window as the old `italy_240_report.html` | no - new defaults untested on this window |
| Britain, Greece, Japan, Chesapeake Bay, Black Sea, Scandinavia, Caribbean, New Zealand | `--region <x>`, bare rwmaps defaults, no overrides | no - first cut picked for geographic variety, unverified |

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

## What's NOT built yet - the next phase

The user's plan (stated 2026-08-01): for each of the 10 regions, run
**10 real-engine generations** to finally get fairness metrics with
enough N to mean something. (Recall
[[feedback-verification-and-automation]] / the equivalent note in
`TUNING_STATUS.md`: N=1-2 was deliberately fine for the earlier
breadth-over-parameters research, but is NOT enough for any fairness
claim - this phase is specifically about getting real N so fairness
numbers are trustworthy for once.) After that: tune for fairness, verify
AI setup - not scoped in detail yet, depends on what the N=10 data shows.

**Nothing has been built for this phase yet.** Specifically missing:

1. **A capture-driver script.** `automation/tuning_matrix.py` is shaped
   for a window x condition cross product across the 5 Puget-Sound-area
   research windows - it does not fit "10 independent named regions, one
   condition each, 10 samples each." Needs a new script (suggested name:
   `automation/mod_capture.py`) that:
   - Imports `MOD_REGIONS` directly from `build_mod.py` rather than
     redefining the list - keeps captured/tested settings identical to
     what's actually shipped, no drift.
   - Reuses the existing regen -> copy-to-`SLOT_PATH` -> `click_sequence`
     -> `sample_analysis.analyze_capture()` -> append-to-`results.jsonl`
     pattern already proven out in `tuning_matrix.py`.
   - N=10 per region (not 1-2) - the one place in this project so far
     where a real N for statistics actually matters.
   - Should be `--run-id`-scoped like `tuning_matrix.py`, e.g.
     `out/mod_capture/<run-id>/results.jsonl`.

2. **A report builder.** Needs a new script (suggested name:
   `automation/build_mod_report.py`) building `reports/mod_report.html`
   with, per region:
   - The resolved generation settings and reasoning ("what went into this
     map") - explicitly requested to be the report's focus, not just
     numbers.
   - All 10 samples shown side by side (100 images total across 10
     regions is a lot of content - may want per-region collapsible
     sections, not decided yet).
   - min/median/max across the 10 samples for: TC separation,
     landmasses-with-players, pairwise-reachable-fraction, any-zero-
     resource rate, and probably per-resource-kind counts - **exact
     aggregation design for the resource counts (6 kinds x 8 players x 10
     samples is a lot of numbers) hasn't been worked out yet.**
   - AI map type per region, explained using the real semantics above,
     not just a bare category name.
   - **RESOLVED 2026-08-01**: yes, also show the aesthetic-recognizability
     metrics (`automation/aesthetic_metrics.py` - IoU vs. 10m truth,
     boundary ratio, pockmark score, island preserved-fraction) per
     region, min/median/max across the 10 samples, alongside the fairness
     stats above. Use `compute_metrics()` from that module directly -
     don't reimplement.

3. **Time estimate**: 10 regions x 10 samples = 100 captures, each
   needing 1 regen (cheap) + 10 click-sequences. At the ~36s/sample
   empirical average from prior sweeps (see `TUNING_STATUS.md`), that's
   roughly **1 hour of real UI-automation engine time** - similar order
   to the `res_default_sweep` run. Same caveats apply: this drives actual
   mouse/keyboard, don't touch the machine while it runs, and it should be
   launched as a detached background process (a `nohup ... & disown`
   pattern was used for `res_default_sweep` - reuse that approach).

## How to resume

1. Read this file, then `TUNING_STATUS.md` if deeper research history is
   needed for context. No open questions remain - both the metrics scope
   and the report shape are decided above; go straight to building.
2. Build `automation/mod_capture.py`, run it in the background (~1hr,
   don't touch the machine meanwhile).
3. Build `automation/build_mod_report.py`, generate `reports/mod_report.html`.
4. Only after the user reviews fairness/AI results per region: the "final
   tuning to make them a bit fairer" pass the user mentioned - not scoped
   yet, will depend on what the report shows.
