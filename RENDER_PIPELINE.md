# Real-render verification pipeline

> **The mechanics here are superseded by `EDITOR_AUTOMATION.md`.**
> `ui_driver.ps1` and its Windows OCR seed poll have been deleted;
> `automation/editor.py` reads the screen with OmniParser and confirms a
> control before clicking it. The one-time manual setup below is also gone
> — `editor.ensure_ready()` walks the editor there itself.
>
> This file is kept for the part that has not changed: **why** the pipeline
> is GUI automation rather than a direct engine call. See "Why not call the
> engine directly" below before proposing that again.

`rwmaps` computes fairness and coastline fidelity entirely from the Python
land mask, before the game ever sees the script. That's necessary but not
sufficient — the *engine's own* random-map interpreter has its own ideas
about where things end up, and those ideas don't always match what the mask
analysis predicted. This pipeline closes that loop: it drives the real AoE2
DE Scenario Editor to generate and save actual `.aoe2scenario` files from a
candidate `.rms`, so the true, engine-produced result can be inspected
directly instead of trusted from a Python approximation.

Windows-only, GUI-automation-based (see "Why not call the engine directly"
below for why).

## What it does

1. You generate an `.rms` with the normal `rwmaps` CLI.
2. The script's content is swapped into one fixed "slot" file that's
   already selected in a running Scenario Editor session.
3. The editor's "Generate Map" button is clicked (and verified — see
   below), producing real engine output.
4. The editor's Save is clicked (and verified), producing a real
   `.aoe2scenario` file on disk.
5. That file is copied out to a unique path where it can be parsed.

Repeat step 1-5 for as many scripts/regions/parameter variants as you want
to compare, without ever touching the game's UI by hand again after a
one-time setup.

## Setup (no longer manual)

This used to be a six-step manual walk through the editor that had to be
redone after every crash, and every harness assumed someone had done it.
`editor.ensure_ready()` now does it — Main Menu → Editors → **Create
Scenario** → 8 players → Random Map → Huge [240], launching or recovering
the game first if it needs to — and then *proves* it worked with
`preflight()` before a generation is spent. Every harness in `automation/`
calls it before its first capture. The steps and their traps are in
`EDITOR_AUTOMATION.md`.

## Usage

```
uv run python automation/gen_loop.py "<name>" --region <region> --size 220 --players 8
```

Any extra `rwmaps` CLI flags (`--proj`, `--rotate`, `--biome`, ...) pass
through. Output lands in `out/loop-<timestamp>/`, both the `.rms` that was
generated and the `.aoe2scenario` the engine actually produced from it.

To read the true terrain grid the engine grew (not the Python
approximation):

```python
from rwmaps.scx_read import read_terrain_grid, read_land_mask
grid = read_terrain_grid("out/loop-.../whatever.aoe2scenario")  # [y][x] terrain ids
mask = read_land_mask("out/loop-.../whatever.aoe2scenario")     # [y][x] bool
```

## Current slot

`automation/gen_loop.py`'s `SLOT_PATH` points at
`AA_rw_placeholder_tester.rms` in the local mod's `random-map-scripts`
folder. The `AA_` prefix makes it sort near the front of the combined
script list (100+ entries once subscribed map packs are included), which
matters because reaching either end of that list crashes the game (see
"Why the fixed-slot trick" below). Functionally it doesn't matter
which file is used, only that it's the one currently selected in the
editor's "Random Map location" list. Switch `SLOT_PATH` if you reselect a
different file by hand.

## Why the fixed-slot trick, not just picking a new script each time

The editor's "Random Map location" list combines our own generated scripts
with everything from subscribed map packs — 100+ entries. Scrolling that
list to reach a new entry, or letting its selection sit near the very
start/end of the range, **reliably crashes the game** (silent process
exit, no crash dump) — reproduced repeatedly across multiple input methods
in an earlier debugging session. Real human clicks on a freshly-opened list
are fine; the crash is specifically about *reaching a list boundary*, via
any means.

The escape hatch: the editor re-reads the `.rms` file from disk fresh on
every "Generate Map" click — it does not cache script text at selection
time. So instead of navigating to a different list entry per script, one
entry is selected once, and its file content is swapped out on disk before
each generation. The list is never touched again.

## Why not call the engine directly (i.e. why not reverse-engineer it)

This was the first approach tried, and it was abandoned. `AoE2DE_s.exe`
exports zero of its own symbols (its entire export table is Wwise audio
middleware), so finding "the generate function" would require dynamic
tracing or disassembly. Worse: attaching Frida and installing *any*
`Interceptor.attach` hook on the process — even one that does nothing but
increment a counter — reliably crashes the game. Bare attachment (no hooks)
is fine; hooking anything isn't. Given that instability, and that the
UI-automation trick above achieves the same goal with far less risk, the
RE path isn't worth resuming unless the UI-automation approach hits a wall
of its own.

## Reliability engineering (history — the driver this described is gone)

`ui_driver.ps1` was deleted once every harness moved to
`editor.generate_and_save()`. Read `EDITOR_AUTOMATION.md` for what the
capture path does now. Four findings from that era still hold and are worth
not rediscovering:

- Both the "Generate Map" and "Save" clicks **intermittently fail to
  register** — confirmed directly, not inferred. Neither is safe to click
  once and assume.
- **Save needs two independent confirmations**: the Menu closing is *not*
  sufficient. It was observed reporting "closed" with no new file ever
  appearing, so a genuinely newer `.aoe2scenario` on disk is also
  required. `editor.save()` keeps both.
- Repeated clicks at the *exact same pixel* with no movement between them
  can fail to register as separate clicks (Windows' double-click merging).
  `editor.move()` carries this forward.
- **Pixel and colour diffs gave false readings** for whether a control is
  present, from UI shimmer between otherwise identical frames. That is why
  the replacement matches templates and reads the screen with OmniParser
  rather than comparing bytes. (A colour *mean* over one crop is still
  trustworthy for a binary "is it generating" — that is what the Generate
  button's redness is.)

The OCR half of it is not carried forward at all: it needed Windows
PowerShell 5.1 for the WinRT projection, needed the Seed box binarized to
read light-on-dark text, and could only ever mark the *end* of a
generation. Superseded, not ported.

## Next steps: what the at-scale generation capability is *for*

The point of being able to run this hundreds or thousands of times isn't
just to confirm the pipeline works — it's to **tune the parameters that
control coastline fidelity, town centre placement, and resource
placement**, against real engine output instead of a Python approximation.

The most visible problem so far: **town centre placement is suboptimal.**
`analysis.choose_starts` picks a start tile purely from the land mask (a
quality-vs-separation tradeoff, see `src/rwmaps/analysis.py`), and
`rms_land.build_land_generation` turns that into a small `create_land`
block (`assign_to_player`, ~240 tiles, radius-9 seed) at that position. But
the *exact* tile where the engine actually drops the Town Centre within
that assigned land isn't controlled by us — it's the RMS engine's own
placement logic, operating on a land blob whose final shape depends on
`clumping_factor`, `base_size`, and how it interacts with the coastline
land growing around it. There's currently no feedback loop confirming the
TC actually lands somewhere good (flat, not touching water, clear of the
coastline's own land encroachment) versus just trusting the *intended*
tile was fine.

To fix this, the natural next step is:

1. **Extend `scx_read.py`** to pull actual unit placements out of a saved
   `.aoe2scenario` (via `AoE2ScenarioParser`'s unit manager — currently
   `scx_read.py` only reads the terrain grid, not units), so the *actual*
   in-game Town Centre tile per player can be read back, not just assumed
   from the intended `land_position`.
2. **Compare** actual vs. intended TC position across many generations
   (different regions, sizes, seeds) to characterize how far off and in
   what direction placement typically drifts, and whether it correlates
   with anything (proximity to coastline, land blob shape, `clumping_factor`).
3. **Iterate** on `rms_land.py`'s player-land generation parameters (disc
   radius, `base_size`, `clumping_factor`, `other_zone_avoidance_distance`)
   using that feedback, rather than guessing — this is exactly the kind of
   tuning that needs real engine output at scale rather than a single
   manual check.
4. The same real-render loop should eventually also verify **resource
   placement** (README's existing "What's missing: Resource fairness" item
   — boar/berries/gold/stone reachable and roughly fair per player) once
   TC placement itself is solid, since resource placement logic in the RMS
   engine is relative to where the TC actually is.

None of steps 1-4 are built yet — this pipeline is the prerequisite
infrastructure for all of them.

## Files

Both in `automation/`:

- `editor.py` — the driver: launch, recover, set up, generate, save. Its
  `generate_and_save()` is the capture cycle every harness here calls.
  Mechanics in `EDITOR_AUTOMATION.md`.
- `gen_loop.py` — the Python orchestrator described above.
