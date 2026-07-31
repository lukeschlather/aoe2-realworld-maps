# Tuning status

Read this first when resuming work on the Puget Sound window/parameter
search - it should make a fresh conversation productive without needing to
re-derive this session's history from git log alone.

As of commit `693724f` (2026-07-31).

## Where things stand

Goal: find a real-world window (which slice of Puget Sound/the Salish Sea
to render) and a set of generation parameters that produce a recognizable,
playable AoE2 map - validated against **real engine captures** at every
step, never a Python approximation, per the user's standing preference
(see `feedback_verification_and_automation` in the memory system).

A full real-engine parameter matrix just completed: **151/160 samples**
across **5 candidate windows** x **16 one-parameter-at-a-time conditions**.

**The report:** `reports/tuning_matrix_report.html` - open directly in a
browser, no server needed. For every sample it shows:
- the real engine render (coastline + actual TC placement + every resource
  dotted by which TC can actually reach it)
- the full *resolved* parameter set for that condition (not just what
  differs from default)
- a link to that exact sample's `.aoe2scenario` and the condition's `.rms`
  script, both archived under `reports/tuning_matrix_data/` (committed to
  git - not gitignored `out/`, which only holds reproducible working data)
- placement facts (TC separation, landmasses-with-players, pairwise
  land-reachability) and per-player resource counts

Report generation itself takes ~4 seconds regardless of sample count -
`automation/build_tuning_report.py` only reads precomputed
`out/tuning_matrix/results.jsonl`, it never re-parses a `.aoe2scenario`.

## Key finding worth prioritizing next

**`--resolution 50m`** (coarser Natural Earth coastline source data, vs the
`10m` default) looked promising enough in this matrix that the user is
considering dropping the other explored axes (overlap, consolidation
width, clumping-factor, max-radius, disc budget, island-dropping) in favor
of focusing there. **This has not been deep-dived yet** - only one
data point per window (`resolution_50m` condition, N=2 samples) exists so
far. The natural next step is more samples of `resolution=50m` across all
5 windows, and possibly crossing it with the other parameters (does 50m +
consolidation, or 50m + a specific overlap, do even better than 50m
alone?).

## The 5 candidate windows currently in play

All at size 240 ("Huge"), 8 players, projection `laea`, rotate 0. Defined
in `automation/tuning_matrix.py`'s `WINDOWS` list.

| key | center (lon, lat) | span | km/tile |
|---|---|---|---|
| `salish_sea_wide` | -122.65, 47.95 | 420 km | 1.75 |
| `victoria_recenter` | -122.9, 48.15 | 260 km | 1.08 |
| `victoria_recenter_tighter` | -122.85, 48.05 | 200 km | 0.83 |
| `west_shift` | -122.85, 47.75 | 130 km | 0.54 |
| `west_shift_zoomed` | -122.8, 47.75 | 95 km | 0.40 |

User reactions so far (from before this last matrix ran): liked
`victoria_recenter` at `consolidate + overlap 0.85`; liked `west_shift`
baseline aesthetically but suspected it's unplayable; guessed
`west_shift` consolidated might be a compromise. **All 5 windows were kept
in scope for this matrix deliberately** - the point of varying more
parameters was to see whether windows that didn't shine under the first
tuning pass do better under different settings, not to prune them.

## What's tunable (full inventory, all exposed via `rwmaps` CLI flags)

| flag | default | what it does | explored this matrix? |
|---|---|---|---|
| `--overlap` | 1.0 | disc-cover clearing overlap - lower = tighter fit, fewer interior slivers | yes (1.0, 0.85, 0.72) |
| `--max-radius` | 12.0 | largest disc size in the greedy cover | yes (8, 12, 18) |
| `--clumping-factor` | 8 | engine-side `create_land` growth shape | yes (4, 8, 16) |
| `--min-water-width` / `--min-land-width` | 0 (off) | consolidation: erase narrow water/land features below N tiles (`raster.simplify_features`) | yes (light/default/heavy per window) |
| `--lands` | auto (~700 @ size 240) | total disc budget | yes (350, 700, 1200) |
| `--resolution` | `10m` | Natural Earth coastline source detail (`10m`/`50m`/`110m`) | yes - **50m looks promising, under-explored** |
| `--min-island-tiles` | 0 (off) | drops small islands below N tiles | yes (16, 64) |

Each window's 16 conditions vary exactly one of these at a time from a
"good baseline" (default consolidation width for that window + overlap
0.85), so effects are attributable - see `conditions_for()` in
`automation/tuning_matrix.py` for the literal CLI args each condition
resolves to.

## Automation scripts (`automation/`)

Pipeline order: scout -> capture -> analyze (now folded into capture) ->
report.

- `scout_window.py` - fast Python-only screen (land%, IoU, TC separation)
  over ad hoc windows, before spending any real engine time.
- `sample_analysis.py` - **the core analysis module.** Turns one captured
  `.aoe2scenario` into a JSON-serializable summary (real TC placement,
  real resource ownership, a preview render) immediately after capture -
  read its module docstring, it explains why "fairness" is reported as
  neutral facts (n_landmasses_with_a_player, pairwise_land_reachable_
  fraction) rather than a pass/fail verdict, and why that matters.
- `tuning_matrix.py` - the real-engine batch driver used for this matrix.
  Regenerates each (window, condition) script once, then loops the real
  Generate->Save click sequence for N samples (currently 2 - intentionally
  small; the point is breadth over parameters, not characterizing RNG
  variance, which can't be controlled anyway), calling `sample_analysis`
  immediately after each capture and appending one line to
  `out/tuning_matrix/results.jsonl`. Resumable - skips (window, condition)
  cells that already have N samples recorded.
- `build_tuning_report.py` - fast (~4s) report template that only reads
  `results.jsonl` plus archives each cell's `.rms`/`.aoe2scenario` into
  `reports/tuning_matrix_data/` for the report to link to. **Never**
  touches `AoE2ScenarioParser` itself - if you're tempted to add analysis
  logic here, it belongs in `sample_analysis.py` at capture time instead.
- `batch_capture.py` / `window_matrix.py` / `water_navigability.py` /
  `build_comparison_report.py` / `build_window_matrix_report.py` /
  `build_map_report.py` - earlier-generation tooling from before the
  capture-time-analysis pattern was adopted; superseded by the above for
  new work, but not deleted since they still work standalone (e.g.
  `water_navigability.py`'s end-to-end path-connectivity check for a named
  strait has no equivalent in the new pipeline yet).

## Known quirks / gotchas (don't re-derive these)

- **`lands_high`'s very first Generate-map click attempt failed in every
  one of the 5 windows it was tried on** - all 9 total sample gaps in this
  matrix are exactly this pattern (sample 0 fails, sample 1 succeeds).
  Likely the editor needs longer to actually load a much bigger `.rms`
  (1200 disc blocks vs the default 700) after the slot-swap than the
  first click's retry budget allows. Every cell still has ≥1 real sample.
  If pursuing bigger disc budgets further, consider a longer settle delay
  after swapping in a large script, before the first Generate click.
- **Editor's Map Size must be "Huge [240]"** to match `SIZE = 240` used
  throughout `tuning_matrix.py` - this was manually changed by the user
  mid-session (it was "Large (8 player) [220]" earlier); if it's been
  changed back, captures will be silently wrong-sized.
- **"Fairness verdict" was deliberately removed.** An earlier version of
  `sample_analysis.py` folded `analysis.evaluate()`'s land-path
  connectivity check into an "unfair" label - wrong for a real-world
  archipelago map, where separate islands are normal geography, not
  unfairness. Don't reintroduce a verdict; report facts and let a human
  judge, per explicit user direction this session.
- **The `.gitconfig` `core.excludesfile` warning on every git command** is
  a pre-existing duplicate entry in the user's global `~/.gitconfig`
  (`~/.gitignore` vs `dotfiles/primary/.gitignore`), unrelated to this
  project. Harmless noise, not a bug in anything here - the user was
  offered a fix and hadn't decided as of this writing.
- **Report/data commit convention**: raw per-run working data lives in
  gitignored `out/` (reproducible, not source). Final reports and their
  backing `.rms`/`.aoe2scenario` files are copied into `reports/` and
  **are** committed - `reports/tuning_matrix_data/` is ~30MB currently;
  be mindful this grows with each new batch and consider pruning superseded
  data if it gets unwieldy.

## How to resume

1. Read this file, then open `reports/tuning_matrix_report.html` and look
   at the `resolution_50m` condition across all 5 windows.
2. To run more samples of an existing or new condition: add it to
   `conditions_for()` in `automation/tuning_matrix.py`, then
   `uv run python automation/tuning_matrix.py` (resumable, safe to
   interrupt).
3. To rebuild the report after new data lands:
   `uv run python automation/build_tuning_report.py` (~4s).
4. One-time editor setup must already be done (see `RENDER_PIPELINE.md`):
   Map panel open, Random Map checked, `AA_rw_placeholder_tester` script
   selected, Map Size = Huge [240], Players = 8, Generate Map clicked once
   by hand to leave the editor in a known state.
