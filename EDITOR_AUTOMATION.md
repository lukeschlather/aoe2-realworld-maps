# Driving the Scenario Editor

How the capture pipeline talks to the game, as of 2026-08-17. Supersedes
the PowerShell half of `RENDER_PIPELINE.md`: `ui_driver.ps1` and its
Windows OCR seed poll are deleted, and every capture harness in
`automation/` shares one cycle, `editor.generate_and_save()`.

The governing rule, and the reason most of this exists:

> **Never click a control without first confirming it is there.**

The suspicion is that clicking a control that is not yet in place is what
crashes the editor. One blind click was proven: the save sequence slept a
fixed 200 ms after opening the Menu, then clicked (960, 436) — which with
no overlay up is the middle of the map, where a click in the Scenario
Editor is a brush stroke, not a no-op. Verification is only worth having
if it is cheap enough to run before *every* click, which is why the
checks are tiered.

## The files

| file | what it does |
|---|---|
| `automation/controls.py` | the control registry: a box, a click point and a template crop per control. `click()` verifies then clicks, or raises |
| `automation/editor.py` | the driver: launch, recover, set up, generate, save. `generate_and_save()` is *the* capture cycle — `mod_capture`, `tuning_matrix`, `stock_capture`, `gen_loop`, `batch_capture`, `seed_sweep` and `window_matrix` all call it, and none of them holds a coordinate of its own |
| `automation/omni.py` | OmniParser bridge (models live in the user's `vmauto` checkout, not here) |
| `automation/frame_server.py` | 1 fps ring of frames + a viewer, for watching a run and for forensics after a crash |
| `automation/crash_bisect.py` | cut one block out of a crashing `.rms` at a time |

## What each check costs

Measured on this machine, 1920×1080, CPU only:

| check | cost | good for |
|---|---|---|
| pixel mean over a crop (button colour, dialog present) | ~1 ms + grab | is it generating, is a dialog up |
| template match, with a small local search | **~130 ms** | is this exact control here, right now |
| OmniParser over a 160×50 region | 2.9 s | relocating a control that moved |
| OmniParser over a 420×160 region | 4.0 s | " |
| OmniParser over the full screen | 17.8 s | unexpected screens, diagnosis |
| OmniParser model load | 20.9 s, **once** via `Server` | — |

Three things worth knowing before optimising any of it:

- The 130 ms is **almost entirely the screen grab**, not the comparison.
  Verifying several controls from one grab is nearly free; grabbing per
  control is what costs.
- Region cropping is worth about **6×, not an order of magnitude** —
  there is a ~2.9 s floor no crop gets under.
- OmniParser's OCR gets **worse** on small crops (less context): it read
  `Map Size` as `MAp Size` on a 420×160 region. Region passes are for
  relocating a control, not for reading values.

## When a check fails, in order of cost

`editor.why_not_there()`. The order matters because the first two are
~50 ms and have *different* correct responses:

1. **The game crashed** — pid changed. Clicking anything is wrong; recover.
2. **Somebody is using the machine** — the game is not foreground. Its
   clicks would land in whatever is on top. Wait; do not click.
3. **The UI has not settled** — by far the most common. Wait and look again.
4. **The screen is genuinely not what we expected** — only this one earns
   an OmniParser pass, and it writes an annotated image, which is what a
   human wants to look at anyway.

## Signals

**Generation: the Generate button's colour.** It greys while the engine
works (redness ~107 idle, ~68 generating) and returns to red when it
finishes. Better than the seed box in the way that matters: it marks the
**start** as well as the end, so a crash *during* generation is
distinguishable from a generation that never began. The seed only ever
changes at the end and cannot tell those apart. It is also not a screen
read of a fullscreen D3D application several times a second while the
engine is under load, which the old OCR poll was.

**CPU load works but is not sufficient alone.** It fires correctly
(measured 1.56 against a 0.95 baseline) but called a lull inside
generation the end, reporting done at 4 s of a generation still running a
minute later. Kept as a gate, never as the authority.

**Crash: the process id, not the process name.** A relaunched game is a
different pid with the same name, and it comes back at Blank Map /
Small [144]. Land areas here are absolute tile counts, so the wrong size
does not shrink a map, it breaks it — silently.

## Mods: the failure that looks like nothing

A crash makes the game **disable every mod**, and that state persists. The
"re-enable your mods?" prompt appears once on the next launch; miss it and
there is nothing left to click.

With the mods off, `AA_rw_placeholder_tester` is not in the Random Map
list at all, and the editor falls back to the **first stock script**. The
map still generates, still comes out 240×240 with 8 players, and is simply
a different map. This happened: a capture recorded as "Britain" had the
selector on `Acclivity` and was 100% land. `IOU_WRONG_MAP` caught it at
0.30 against Britain's usual 0.80–0.90 — but only after a full
generate-and-save had been spent on it.

**Never press Escape while that prompt is up.** Escape dismisses it
without re-enabling anything. The cutscene-skip loop did exactly this and
caused the capture above.

`mods/mod-status.json` in the user profile is the game's own record and
answers this instantly:

```sh
uv run python -c "import sys;sys.path.insert(0,'automation');import editor;print(editor.mods_enabled())"
```

`editor.enable_mods()` writes it back, and **only with the game closed** —
a running game rewrites the file on exit. `recover()` calls it before
launching.

### Raising the mods' priority, through the UI

Our two mods sit near the bottom of the Mods list (priority 23 and 25 of
about 25), so reaching them means scrolling a long list. Moving them to
priority 1 puts them at the top and removes that scrolling — the same idea
as naming the slot script `AA_` so it sorts first and needs no click.

The manual process, per the user:

1. Main Menu → **Mods** (a button in the main menu).
2. Find our mods — they are **last** in the list, so scroll to the bottom.
3. Select one, then press **priority up** repeatedly until it reaches 1.
4. Repeat for the other.

Two things observed while looking at this screen:

- **A tooltip can obscure the "priority down" button** when the mouse
  rests nearby. Anything automating this must move the cursor off before
  reading the screen, or it will be reading the tooltip.
- The screen is **not mapped into the control registry yet**. The parse
  taken for it caught that tooltip and a scroll happening at the same
  time, so it was not usable. `editor.enable_mods(priority=1)` does the
  same thing by editing `mod-status.json` with the game closed, which is
  the reliable route until the screen is mapped.

## Skipping the opening cinematic

`wait_for_main_menu()` presses Escape until the main menu's own template
verifies, so it returns when the menu is *actually* there rather than
after a fixed sleep. It refuses to press anything while the mods prompt is
up, for the reason above.

Two alternatives, neither taken:

- **Click instead of Escape** — a click cannot "activate" a focused
  control the way Return could, but it still lands somewhere, so on the
  main menu it needs a known-empty target.
- **Remove the video** — `resources/_common/movies/aoeiide_titlevideo.wmv`
  in the game install. This skips the intro with no input at all, but it
  modifies the game install (Steam's verify-integrity would restore it).
  Left as the user's call rather than done silently.

## The registry

`automation/regions.json` plus `automation/templates/*.png`. **Machine
specific** — physical pixel boxes at this display layout, the same
assumption `tuning_matrix.py`'s coordinates already make. Re-learn if the
resolution changes:

```sh
uv run python automation/controls.py --learn generate="generate map"
uv run python automation/controls.py --verify-all
```

Learning is batched: one OmniParser pass learns every control visible on
that screen, because eight one-at-a-time passes would cost two and a half
minutes to look at the same screen eight times.

**Matching is ranked, not first-substring-wins.** Plain "contains" picked
the wrong control three times in a single pass — `map` matched *Generate
Map*, `seed` matched *Seed Map*, `random map` matched *Random Map
location*. Exact match wins, then the shortest containing text, then
distance to an optional `near` hint, which is what separates the *Save
Scenario* button from the *Save Scenario* title.

### Two traps

**A template verifying at diff 0.0 does not mean the click point is on the
control.** The File Name field's box was drawn 10 px left of the actual
field. It matched perfectly, every time, and typed into nothing.

**The first save of every session opens a file browser.** Later saves are
silent, writing to the same file. The old code knew only the silent form,
so it left the dialog open and reported "the menu closed but no file
appeared" — a failure the notes recorded at the time as not understood.
`editor.save()` answers the dialog, naming the scenario `rw_capture_slot`
so every later save is silent.

## Running it

```sh
uv run python automation/editor.py --status
uv run python automation/editor.py --recover --setup --generate

# watch a run, and keep the frames before a crash
uv run python automation/frame_server.py --fps 2 --ring 900   # :8765

# a capture pass; recovers from up to 3 crashes and retakes lost samples
uv run python automation/mod_capture.py --run-id <id> --n-samples 1
```

`setup()` walks Main Menu → Editors → **Create Scenario** → 8 players →
Random Map → Huge [240]. **Create Scenario, never Load Scenario** —
loading a scenario to save the player-count clicks is what the user
identified as destabilising the editor.

## Still open

- **The Mods screen is not mapped** (above).
- **Nothing checks the selector before generating.** `omni.py
  --check-editor` tests it; wiring it into the capture loop once per
  region would catch a wrong map in seconds instead of after a full
  generate-and-save.
- **A full pass over all regions has not been run** on this pipeline. One
  region has, end to end.
- Of the harnesses migrated off PowerShell on 2026-08-17, `gen_loop` and
  `batch_capture` have been re-run through the engine (Britain: a
  cold-start capture at IoU 0.838, then 3/3 consecutive at 0.803–0.824,
  ~61s each). `tuning_matrix`, `stock_capture`, `seed_sweep` and
  `window_matrix` are import-checked and share that exact cycle, but have
  **not each been run**. Their old coordinates were stale anyway — every
  one of them clicked Generate at (256, 1028) against the registry's
  verified (305, 1024) — so none of them was trustworthy before this.
- The editor crash itself is **unexplained**. Ruled out by measurement:
  file copying (the slot is byte-identical to its git blob by sha256),
  dangling land ids, the island copse block, seed `-1` (just the default
  on a new scenario), and `mods/local/info.json` (a 2022 leftover). The
  working assumption is that it is not the mod content — a computed cache
  clashing with swapped mod files, or an unlucky click — so the pipeline
  survives one rather than trying to prevent it.
