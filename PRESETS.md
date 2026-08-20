# Presets: one record per map

A **preset** is one map, completely specified, with everything that has ever
been learned about it attached: the window it samples, the complete resolved
parameter set, every `.rms` it has been built into, every engine capture of
those builds, and its status in the mod.

`presets/<label>.json`, one file per preset, committed. 92 of them as of
2026-08-19.

Concepts live in `README_AGENTS.md`, the generation mechanism in
`GENERATION.md`, the conventions in `CLAUDE.md`. This file is the record
system.

## Why

A map's specification used to live in three places that could not be joined
up:

| where | held | could not answer |
|---|---|---|
| `build_mod.MOD_REGIONS` | the shipping list | when, at which commit, on what evidence |
| `automation/*_set.py` -> `out/*_set.json` | a session's candidate conditions | what those conditions produced |
| `out/mod_capture/<run>/results.jsonl` | what the engine did, keyed by display name | which parameter set it was |

So "which parameters produced this scenario" was answerable only by reading
a docstring, and "report on these four windows" was not answerable at all -
every report builder is keyed to a single `--run-id`. Choosing between
windows captured in different passes therefore looked like it needed a new
capture pass, when the samples were already on disk.

## Identity

Three hashes, each answering a different question.

- **`window_hash`** - sha256 of `(proj, lon, lat, span_km, north_deg, size,
  players)`. The geographic window and nothing else, so *"same place, other
  knobs"* is a query: `preset_cli.py window <label>`.
- **`params_hash`** - sha256 of the **complete resolved parameter set**,
  argparse defaults included, resolved by the CLI's own parser so it cannot
  drift from what a generation would use. A built `.rms` is a deterministic
  function of this (`choose_starts` anneals with a fixed RNG seed), so it is
  also the build cache key.
- **`id`** - `<label>-<params_hash[:8]>`. `label` is the human handle and the
  filename; it is unique, unlike a display name.

Two deliberate exclusions from `params_hash`:

- **The map name.** It reaches the script only as the first line of its
  header comment. Measured, not assumed: shipped `RW Great Britain N.rms`
  and the script captured as "Britain northup France" differ in exactly that
  one line. So promoting a candidate to ship under a different name reuses
  its script instead of re-annealing for 70 seconds to change a comment.
- **How the window was spelled.** `--region scandinavia` and
  `--center=16.0,62.0 --span-km 2000` hash alike, because they are the same
  map. Otherwise rewriting a preset into explicit form reads as a new map
  and throws away a byte-identical build.

## Lifecycle

```
screened  ->  candidate  ->  shipped
                          \-> retired
```

- **screened** - a window drawn and judged on paper by
  `window_candidates.py`. No build, no capture. In the registry so the
  decision record is complete: this window was looked at, on this date, in
  this report, beside these others.
- **candidate** - has a build and/or captures, does not ship.
- **shipped** - `build_mod.py` puts it in the mod. Nothing else does.
- **retired** - shipped once, withdrawn. Kept because the *reason* is
  evidence (see `build_mod.RETIRED_REGIONS`).

A full pass, with no manual step in it:

```bash
# 1. define the windows to try (or let window_candidates.py screen them first)
uv run python automation/preset_cli.py new --note "less ocean" \
    scand-south "Scandinavia" -- \
    --center=21.58,63.0 --span-km 2000 --min-water-width 2 --north -45

# 2. put them through the real engine. Each row it writes carries the
#    preset's params_hash, so the samples join back exactly.
uv run python automation/mod_capture.py --run-id scand_south \
    --presets scand-south --n-samples 2

# 3. fold the run into the registry (idempotent)
uv run python automation/preset_import.py

# 4. look at any set of presets, across runs, with no engine time
uv run python automation/preset_report.py --presets scand-south scandinavia \
    --slug scand_south --title "Scandinavia south, against what ships"

# 5. ship one. build_mod reuses the exact script the engine was measured on.
uv run python automation/preset_cli.py promote scand-south --name Scandinavia \
    --why "..."
uv run python automation/build_mod.py --presets scand-south
```

## Builds are content-addressed, and reused

Each preset records the `.rms` it has been built into by `sha256`, with
every place a copy was last seen - the build cache, the shipped mod, a
capture run's `scripts/` dir, a committed report data dir.

`build_mod.py` reuses a build whose copy is still on disk **and still hashes
to what was recorded**. Re-hashing is the point: a path under `out/` can be
overwritten by the next run of the harness that wrote it, and shipping a
script that merely *sits where the captured one sat* is how a report ends up
describing one map under another map's name.

Consequences worth stating plainly:

- A full mod build is ~20s of copying, not ~10 minutes of annealing.
- What ships is provably the script the engine was measured on. Verified: a
  full rebuild leaves all ten shipped scripts byte-identical.
- **A cached build is not upgraded when `src/` changes.** That is a content
  claim, not a freshness claim, and it is the right way round: a script that
  has been through the engine is worth more than a script that is merely
  current. `--rebuild` when the point is to pick up a generation change -
  then capture the result before trusting it.
- Reuse resolves *before* anything is wiped, and copies the build into
  `out/rms_cache/<preset id>/`. Two of the paths a build lives at are not
  stable: the `mod/` copy a full build deletes, and a capture run's
  `scripts/` dir that resuming that run-id clears.

## Storage: index-only

The registry is committed. The artifacts are not.

`presets/*.json` carries, per capture sample, the geometry facts and - per
resource kind - the median and the worst-off player within
`fairness.OWNERSHIP_RADIUS` (30 walked tiles). That is what has to outlive
`out/`. The `.aoe2scenario` files and the full per-player fairness profiles
stay in gitignored working data, pointed at rather than copied.

So **every path in a preset is a last known location, never a promise.**
`preset_cli.py audit` says which are still there;
`preset_report.py --archive` copies a set worth making durable into
`reports/<stamp>_preset_report_data/`, the way the older report builders do.

## Reading history back

Reconstruction from records written against older code needed three
translations, each of which silently corrupts something if missed:

- **`--rotate D` (grid space, pre-2026-08-16) -> `--north D-45`.** An old
  `--rotate 45` is today's `--north 0`. Feeding a grid-space value in as
  `north_deg` builds a truth mask 45 degrees off and corrupts IoU with no
  error.
- **A record with no orientation flag at all.** Before 2026-08-16 that meant
  north -45; today it means 0. The guess has to be made **per source**, not
  globally: the condition sets were rewritten onto screen space when it
  landed, and assuming the old default there turned "Britain north-up" into
  the shipped window and merged the two records.
- **`--spread-islands` -> `--spread-starts`** (renamed 2026-08-01).
- **Per-player land is absent, not zero, before 2026-08-16.** Defaulting it
  to 0 writes "this map gives a player no land" into the record for maps
  whose land was never measured, and drags the median of a mixed-vintage
  preset to zero.

Every substitution a preset needed is recorded in its `legacy_notes`.

## Commands

| command | what it answers |
|---|---|
| `preset_cli.py list [--status S] [--captured]` | what exists, how much evidence each has |
| `preset_cli.py show <label>` | everything: resolved params, builds, captures, per-sample numbers |
| `preset_cli.py window <label>` | which other presets share this window |
| `preset_cli.py history [-v]` | every capture run, oldest first, with commit and N |
| `preset_cli.py audit` | is what ships what was captured; which artifacts are gone; what is promotable now |
| `preset_cli.py new`, `promote`, `retire`, `note` | edit the registry |
| `preset_cli.py region-set ... -o FILE` | a `mod_capture --region-set` file, for harnesses that still take one |
| `preset_import.py [--dry-run]` | fold every run, condition set and screen on disk into the registry |
| `preset_report.py --presets ...` | an HTML report over any set of presets, no engine time |
| `build_mod.py [--presets ...] [--rebuild] [--list]` | build the mod from `status: shipped` |
