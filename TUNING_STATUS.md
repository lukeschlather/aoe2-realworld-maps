# Tuning status

Read this first when resuming work on the Puget Sound window/parameter
search - it should make a fresh conversation productive without needing to
re-derive session history from git log alone.

As of commit `d78c02d` + this session's uncommitted work (2026-07-31).

## Known-good defaults (ADOPTED 2026-07-31)

`rwmaps`'s own argparse defaults in `src/rwmaps/cli.py` now ship these
values - **not arbitrary, chosen from real-engine-verified captures a
human judged aesthetically recognizable**, not from the fairness-stat
matrix below (see "Why fairness stats were the wrong tool" further down):

| flag | new default | was |
|---|---|---|
| `--resolution` | `50m` | `10m` |
| `--overlap` | `0.85` | `1.0` |
| `--min-water-width` | `4` | `0` |
| `--min-land-width` | `3` | `0` |
| `--clumping-factor` | `8` (unchanged, confirmed known-good) | `8` |

Evidence: samples with cell IDs `4d8bbf2f`, `a3f19de6`, `f81b9399`,
`879ad483`, `05a50859` (all called "good" on sight) plus `86b94b33` (a
favorite, candidate for playtesting) - see
`reports/20260731-201149_aesthetic_comparison_report.html`. `c438f623` (a 110m sample) was
called out as bad. None of the good examples used `resolution=110m`, an
`overlap` other than `1.0`/`0.85`, or pushed any knob to an extreme.

**Don't revert these to "simpler" values (e.g. 0/0 consolidation, overlap
1.0) without re-running that visual verification** - see the comment block
above `--resolution` in `cli.py`.

**Historical reports are NOT affected and will still display correctly if
rebuilt**: `automation/tuning_matrix.py`'s `PARAM_DEFAULTS` dict is
deliberately frozen at the OLD values (10m/1.0/0/0), because every
already-captured condition that didn't explicitly pass one of these flags
(`baseline_r50m`, the `consolidate_*_overlap1.0_r50m` family) actually ran
under those old values before this change - updating `PARAM_DEFAULTS` to
match the new CLI defaults would silently mislabel that historical data.
A brand new sweep whose "baseline" condition should reflect the new
defaults needs to record its own resolved params at capture time rather
than reconstructing them from a static dict like this.

## Where things stand

Goal: find a real-world window (which slice of Puget Sound/the Salish Sea
to render) and a set of generation parameters that produce a recognizable,
playable AoE2 map - validated against **real engine captures** at every
step, never a Python approximation.

**The actual optimization target is human-judged recognizability of major
real-world features** (does a strait look like a strait, does an island
stay an island), **not statistical fairness** (TC separation, land-
reachability, resource counts). Two real-engine matrices were run
leaning on fairness stats before this was made explicit - see "Why
fairness stats were the wrong tool" below before trusting any fairness
number here as decision-driving.

Two real-engine matrices exist:
1. **Original 16-condition matrix**, 5 windows x 16 one-parameter-at-a-time
   conditions at the `10m` (then-)default, 151/160 samples. Report:
   `reports/20260731-014212_tuning_matrix_report.html`.
2. **`res_default_sweep`**, 5 windows x 14 conditions x 2 resolution
   defaults (50m, 110m), 264/280 samples. Report:
   `reports/20260731-201121_tuning_matrix_report_res_default_sweep.html`.

Both reports show, per sample: the real engine render (coastline + actual
TC placement + every resource dotted by which TC can actually reach it),
the full *resolved* parameter set (not just what differs from default), a
prominent per-condition ID badge (an 8-hex fingerprint of window geo +
resolved params, shared by a condition's `.rms` and all its samples), and
links to the archived `.rms`/`.aoe2scenario` files under
`reports/tuning_matrix_data/` (original) or
`reports/tuning_matrix_data_res_default_sweep/` (res_default_sweep) -
committed to git, not gitignored `out/`.

## Aesthetic recognizability metrics (new this session)

`automation/aesthetic_metrics.py` computes, for any archived
`.aoe2scenario`, metrics that try to track human-judged recognizability
rather than fairness:

- **`iou_10m`**: real engine mask vs. a freshly-rasterized 10m (finest,
  no consolidation/island-drop) reference for that window - resolution-
  independent fidelity to the actual real-world coastline. Weak signal in
  practice (thin margins between labeled good/bad examples).
- **`iou_own_target`** (real mask vs. that condition's OWN
  resolution/consolidation target) was tried and **rejected** - it scored
  a widely-disliked 110m sample *highest* of anything labeled, since
  reproducing an already-degraded target faithfully isn't recognizability.
  Not used anywhere downstream.
- **`bnd_ratio`**: coastline boundary-cell count (real ÷ 10m truth) - a
  detail/complexity-retained proxy. Showed a real but not huge gap between
  good and bad labeled examples.
- **`pockmark_score`**: average excess coastline roughness in patches
  where the TRUE coast is smooth - meant to catch "fractal noise on a
  coast that should be smooth." **No labeled example of this specific
  failure exists yet** - the one labeled-bad sample is an oversmoothing
  failure (opposite direction), so this metric is unvalidated. If you spot
  a pockmarked-coast example, its cell ID would let this finally be tested.
- **`island_topology`** (deleted / preserved / merged, per true island) +
  **preserved-fraction** (preserved ÷ (preserved+merged), among islands
  that didn't just vanish) - **the standout metric**. Directly
  operationalizes explicit user feedback: deleting an island entirely is
  fine, an island fusing into the mainland ("turning into a peninsula") is
  the specifically disliked failure. Preserved-fraction sat at 0.57-0.69
  for every good-labeled example and the favorite, then collapsed to 0.09
  for the one bad-labeled (110m) example - a ~6x gap, not a marginal one.
  **Caveat**: every good label happens to be 50m and the only bad label
  happens to be 110m, so this hasn't been tested against a *bad 50m*
  example yet (whether it generalizes beyond "detects low resolution" is
  still open).
- Also found via this metric, not yet investigated: **island
  preserved-fraction is ~0.00 for `west_shift` and `west_shift_zoomed`
  across nearly every 50m condition**, unlike the three wider windows
  (0.4-0.8 range) - may mean those two windows just don't contain much
  real archipelago to preserve, independent of parameters, rather than a
  tuning problem. Worth a look before spending more tuning time on those
  two windows specifically.

`automation/build_aesthetic_comparison_report.py` builds
`reports/20260731-201149_aesthetic_comparison_report.html` - **50m only** (110m already
established as uniformly worse for these window spans, so it's excluded
to cut noise), grouped into direct one-axis-at-a-time comparisons (3 cards
side by side: e.g. clumping factor 4 vs. 8 vs. 16 for the same window),
with the metrics above shown per card and whichever setting varies in
that row highlighted in its params table.

## Why fairness stats were the wrong tool

An early analysis of the `res_default_sweep` data leaned on aggregated
placement/resource facts (TC separation, landmass count, land-
reachability, zero-of-a-kind rate) to compare 10m vs 50m vs 110m. The user
corrected this directly: fairness can't be judged from N=1-2 samples per
cell (need 10+ for that), and fairness isn't the target metric at all -
recognizability is. Two implications that matter for future work:
1. Don't present fairness-stat aggregates from a breadth-over-parameters
   sweep (N=1-2/condition, intentionally - see "prioritize breadth" below)
   as if they settle a comparison between conditions.
2. When asked to analyze captures, foreground what's visually/
   topologically going on, not fairness tables.

## The 5 candidate windows

All at size 240 ("Huge"), 8 players, projection `laea`, rotate 0. Defined
in `automation/tuning_matrix.py`'s `WINDOWS` list.

| key | center (lon, lat) | span | km/tile |
|---|---|---|---|
| `salish_sea_wide` | -122.65, 47.95 | 420 km | 1.75 |
| `victoria_recenter` | -122.9, 48.15 | 260 km | 1.08 |
| `victoria_recenter_tighter` | -122.85, 48.05 | 200 km | 0.83 |
| `west_shift` | -122.85, 47.75 | 130 km | 0.54 |
| `west_shift_zoomed` | -122.8, 47.75 | 95 km | 0.40 |

`110m` visually collapses branching real inlets/islands into one generic
smooth channel for anything narrower than `salish_sea_wide` (420km span) -
confirmed across 3 independent random seeds showing the identical channel
shape for the same window, so it's a property of the 1:110M source data
at that zoom level, not RNG noise. The user considers 110m possibly still
fine or even preferable *for the widest window specifically*, not for
fairness/detail reasons but because coarser source data gives a more
consistent, predictably-sized result there - not yet acted on.

## What's tunable (full inventory, all exposed via `rwmaps` CLI flags)

| flag | default (2026-07-31+) | what it does | explored? |
|---|---|---|---|
| `--resolution` | `50m` | Natural Earth coastline source detail (`10m`/`50m`/`110m`) | yes - 110m rejected, 50m adopted |
| `--overlap` | `0.85` | disc-cover clearing overlap - lower = tighter fit, fewer interior slivers | yes (1.0, 0.85, 0.72) |
| `--min-water-width` / `--min-land-width` | `4` / `3` | consolidation: erase narrow water/land features below N tiles (`raster.simplify_features`) | yes (light/default/heavy per window) |
| `--clumping-factor` | `8` | engine-side `create_land` growth shape | yes (4, 8, 16) |
| `--max-radius` | 12.0 | largest disc size in the greedy cover | yes (8, 12, 18) - no clear winner |
| `--lands` | auto (~700 @ size 240) | total disc budget | yes (350, 700, 1200) - no clear winner |
| `--min-island-tiles` | 0 (off) | drops small islands below N tiles | yes (16, 64) - no clear winner |

`max_radius`/`lands`/`min_island_tiles` showed no signal in fairness
facts at either resolution - but per "why fairness stats were the wrong
tool" above, that says nothing about their effect on recognizability,
which hasn't actually been evaluated for these three yet.

## Automation scripts (`automation/`)

Pipeline order: scout -> capture -> analyze (folded into capture) ->
report -> aesthetic metrics -> comparison report.

- `scout_window.py` - fast Python-only screen (land%, IoU, TC separation)
  over ad hoc windows, before spending any real engine time.
- `sample_analysis.py` - **the core analysis module.** Turns one captured
  `.aoe2scenario` into a JSON-serializable summary (real TC placement,
  real resource ownership, a preview render) immediately after capture -
  read its module docstring, it explains why "fairness" is reported as
  neutral facts rather than a pass/fail verdict.
- `tuning_matrix.py` - the real-engine batch driver. Every run is scoped
  under a required `--run-id` (isolates `out/tuning_matrix/<run-id>/` and
  its `results.jsonl` so separate sweeps never collide). `conditions_for
  (window, resolution_default="10m")` returns the one-parameter-at-a-time
  matrix; at `"10m"` it's byte-for-byte the original 16 conditions, at
  any other value the two now-redundant resolution-comparison conditions
  are dropped, `--resolution <value>` is prepended to every remaining
  condition, and condition keys get an `_r<value>` suffix. Also holds
  `PARAM_DEFAULTS`/`resolve_params()` (moved here from
  `build_tuning_report.py` to avoid a circular import with
  `aesthetic_metrics.py`).
- `build_tuning_report.py` - fast report builder, reads `results.jsonl`
  only, archives each cell's `.rms`/`.aoe2scenario` under
  `reports/tuning_matrix_data[_<run-id>]/` with self-describing filenames
  (`{window}__{condition}__s{i}__{cid}.{ext}`). Takes an optional
  matching `--run-id` (+ `--resolution-defaults`); omit for the original
  default-named report.
- `aesthetic_metrics.py` - the recognizability metrics module (see above).
  `compute_metrics(win_key, real_mask)` is the reusable entry point;
  `cached_true_mask()` memoizes the expensive Natural Earth rasterization
  per (window, resolution, consolidation) combo since a report pass reuses
  the same handful of combos across dozens of conditions.
- `build_aesthetic_comparison_report.py` - the comparison-groups report
  (see above). Reads the `res_default_sweep` archive specifically; caches
  parsed `.aoe2scenario` results by (window, condition) since several
  compare groups share a condition (e.g. `consolidate_overlap0.85_r50m` is
  the reference baseline for 5 of 7 groups).
- `batch_capture.py` / `window_matrix.py` / `water_navigability.py` /
  `build_comparison_report.py` / `build_window_matrix_report.py` /
  `build_map_report.py` - earlier-generation tooling from before the
  capture-time-analysis pattern was adopted; superseded for new work but
  not deleted, still work standalone.

## Known quirks / gotchas (don't re-derive these)

- **`lands_high`'s very first Generate-map click attempt failed in every
  window it was tried on** - likely the editor needs longer to load a much
  bigger `.rms` (1200 disc blocks vs default 700) after the slot-swap than
  the first click's retry budget allows. Every cell still gets ≥1 sample.
- **Editor's Map Size must be "Huge [240]"** to match `SIZE = 240` used
  throughout `tuning_matrix.py`.
- **"Fairness verdict" was deliberately removed** from `sample_analysis.py`
  - a real-world archipelago map having players on separate islands is
  normal geography, not unfairness. Report facts, let a human judge.
- **The `.gitconfig` `core.excludesfile` warning on every git command** is
  a pre-existing duplicate entry in the user's global `~/.gitconfig`,
  unrelated to this project. Harmless noise.
- **Report/data commit convention**: raw per-run working data lives in
  gitignored `out/` (reproducible, not source). Final reports and their
  backing `.rms`/`.aoe2scenario` files are copied into `reports/` and
  **are** committed.
- **When exploring generation parameters, prioritize breadth over
  parameters over repeated sampling of RNG variance** - 1-2 samples per
  condition is enough; the point is breadth, not characterizing RNG
  variance (uncontrollable anyway). This is *why* N is too small for
  fairness claims - see "why fairness stats were the wrong tool" above.

## How to resume

1. Read this file. Open `reports/20260731-201149_aesthetic_comparison_report.html` for the
   current best view (50m only, comparisons + metrics side by side).
2. Metrics on a new/different sample: `aesthetic_metrics.compute_metrics
   (win_key, real_mask)` where `real_mask = scx_read.read_land_mask(path)`.
3. New capture run: `uv run python automation/tuning_matrix.py --run-id
   <name> [--resolution-default 50m|110m] [--n-samples N]` (resumable).
4. Rebuild a run's report: `uv run python automation/build_tuning_report.py
   --run-id <name> --resolution-defaults 50m[,110m]`.
5. One-time editor setup must already be done (see `RENDER_PIPELINE.md`):
   Map panel open, Random Map checked, `AA_rw_placeholder_tester` script
   selected, Map Size = Huge [240], Players = 8, Generate Map clicked once
   by hand to leave the editor in a known state.
