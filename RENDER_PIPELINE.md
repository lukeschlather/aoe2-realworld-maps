# Real-render verification pipeline

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

## One-time manual setup

In the running game:

1. Main Menu → Editors → Create Scenario.
2. Map panel → check "Random Map".
3. Map Size → pick the size that matches what you'll generate (e.g. "Large
   (8 player) [220]").
4. Random Map location → select the slot script by hand once. **Currently
   `AA_rw_placeholder_tester`** (see "Current slot" below).
5. Players → set player count (8, to guarantee enough player slots/TCs for
   any script).
6. Click "Generate Map" once by hand, just to leave the editor sitting on
   this panel in a known state.

After this, `automation/gen_loop.py` drives everything else. The
editor must stay open on this panel — the automation never navigates
menus, only clicks Generate Map / Menu / Save on the panel you've already
set up.

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

## Reliability engineering (read before touching `ui_driver.ps1`)

Both the "Generate Map" and "Save" clicks **intermittently fail to
register** — confirmed directly, not inferred. This isn't rare; Generate
Map typically needs on the order of 5-8 retries, Save typically needs 2.
The automation compensates rather than assumes:

- **Generate Map** is verified by OCR-reading the Seed value box (bottom
  left of the Map panel) before and after each click attempt, retrying
  until it changes.
- **Save** is verified by requiring *both* the Menu to close (checked via
  OCR of the "Main Menu" title) *and* a genuinely newer `.aoe2scenario`
  file to appear on disk. Menu-closed alone was tried first and is **not**
  sufficient — it was observed to report "closed" with no new file ever
  appearing, for reasons not fully understood. If the menu closes but no
  file shows up after ~2s of polling, the Menu is reopened and Save is
  retried.
- A stuck-open Menu left over from a prior failed/partial run silently
  eats all clicks aimed at whatever's underneath it. Every run checks for
  and clears this first (`Reset-IfMenuStuck`), but only when actually
  detected open — not as a routine click.
- Repeated clicks at the *exact same pixel* with no movement between them
  can fail to register as separate clicks (looks like Windows' double-click
  merging). Retry attempts jitter the cursor a couple pixels and back.
- Detection uses OCR (Windows' built-in `Windows.Media.Ocr`), not pixel or
  color comparison — both were tried and both gave false readings, likely
  from subtle UI shimmer/animation that changes encoded bytes or average
  color slightly between otherwise-identical-looking frames.
  - This requires **Windows PowerShell 5.1 (`powershell.exe`), not
    PowerShell 7 (`pwsh`)** — the WinRT OCR projection only works under
    the .NET Framework-based PowerShell.
  - Light-text-on-dark-background elements (the Seed box) read as empty
    from the OCR engine until binarized (threshold to pure black/white by
    luminance midpoint first).
- Coordinates in `gen_loop.py` (`GENERATE_BTN`, `MENU_BTN`, `SAVE_BTN`,
  `CANCEL_BTN`) are physical pixels specific to this machine's current
  display layout (3840x1080, two 1920x1080 monitors) and will need
  re-finding if that changes.

One run currently takes ~35-40 seconds end to end, dominated by retries.
Not yet tested at real scale (only a handful of consecutive runs so far,
all successful).

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

- `ui_driver.ps1` — the automation library: DPI-aware click/move
  primitives, OCR-based state checks, verified Generate/Save click
  functions. Must be dot-sourced under `powershell.exe`.
- `gen_loop.py` — the Python orchestrator described above.
