# RW Maps

This has a mod "Real World Maps" which includes a bunch of RW maps. Ten
ship right now: Black Sea, Britain, Chesapeake Bay, Cramped Italy, Great
Britain N, Greece, Italy, Michigan, Salish Sea, Scandinavia. Japan,
Caribbean and New Zealand were retired 2026-08-15 - unrecognisable
projections, and measurably short on stone, wood and land besides.

Michigan and Great Britain N were added 2026-08-17 from the candidate
report (`reports/20260816-210117_candidate_report_candidates_n2.html`) - they
were picked by eye off an N=2 breadth sweep. They also ship north-up
(`--north 0`) rather than with the north-toward-upper-left view the other
eight use, because that is the orientation they were judged in.

Scandinavia was **rewindowed 2026-08-20**: the `scand-shift-15` preset, the
same place 15 tiles south, with the `zealand-funen` overrides for the Danish
geography that is smaller than a tile. Land 59.4-59.7% against the old
window's 52%, at the same IoU. The window it replaces is `status: retired`
with its 25 captures intact.

So three of the ten - Michigan, Great Britain N and Scandinavia - have
**not** had an N=10 real-engine pass at the window they ship.

All the code is vibecoded by Claude Sonnet, for some reason we ended up with code both in automation/ and src/rwmaps/ and there are even some tests in tests.

I'm not entirely sure how to operate the code, but Sonnet can usually figure it out with some vague prompting. To make it easier to operate, automation and src/rwmaps could be consolidated. The UI automation is super-jank, but this is kind of necessary; both autohotkey and Powershell mouse clicks seem to generate a lot of crashes. We arrived at a hacky solution where you have to select `AA_rw_placeholder` in the scenario editor (via map -> Random map) and set it to Map Size => Huge, then this enables an agent to copy over the `AA_rw_placeholder.rms` file, generate a map, save the map as a scenario. The agent can then copy the scenario and analyze the resource locations and actual shoreline. (We can't specify the *exact* shoreline in an RMS, there is still some randomness.) So this enables us to run lots of seeds, compare resource distributions and how badly the shoreline ends up mangled. 

The reports/ folder has some examples of intermediate maps generated and resource scarcity.

I have some ideas about how to reduce the crashiness to allow an agent to click around in the UI more freely and need less handholding. I think probably rewriting the UI automation to use python and OCR, possibly making a map of the scenario editor would help. When left to its own devices Claude doesn't do a very good job even clicking on the correct boxes - I suspect that simply recording a human using the UI and using the pixels the human clicks on for specific UI elements might be enough to stop the crash. Though with how often it crashes, it does seem there is something specific to how Powershell/Python uses the UI that causes crashes, I still feel it's possible the UI automation is just really good at finding the one pixel that triggers a crash.

**Update: that idea got built.** The UI automation is Python now, and it
does map the editor - `automation/omni.py` runs OmniParser over a
screenshot and hands back labelled boxes, so `automation/editor.py` finds
each control by reading the screen and confirms it is there before
clicking. Crash recovery is automatic. `EDITOR_AUTOMATION.md` has the
details. The old PowerShell/Windows-OCR driver is gone — every capture
harness now shares `editor.generate_and_save()`.

Other hypotheses for why it was crashing: 

* bot detection is active in the scenario editor because it's just always on whether or not you're in a multiplayer game. (pretty sure this is not the case)
* there's some sort of a mismatch because my UI scale on my machine is not at 100% (it's like 150% or something) and my resolution has some weird interaction with whatever thing is calculating coordinates between AOE2 and the desktop environment which is processing the UI automation click coordinates.


## Operating it

```sh
# ship a map: flips the preset to shipped and puts the script in mod/
uv run python automation/update_mod.py --promote-preset salish-sea

# every shipped map, from scratch - ~20s, because each one ships the exact
# script the engine was measured on and nothing re-anneals
uv run python automation/update_mod.py --all
uv run python automation/install_mod.py --all

# the record: one JSON per map, window + resolved parameters + builds +
# captures. See PRESETS.md.
uv run python automation/preset_cli.py list
uv run python automation/preset_cli.py show scandinavia
uv run python automation/preset_cli.py audit      # is what ships what was captured?
uv run python automation/preset_cli.py history    # every capture run, oldest first
uv run python automation/preset_import.py         # fold new runs into the registry

# a report over any set of presets, across runs, with no engine time
uv run python automation/preset_report.py --presets scandinavia scand-shift-10

# seven ways to draw the same capture - a utility view that finally shows
# forest, the shipped icon treatment, and four stylised thumbnails. Every
# shipped map, every stock benchmark, side by side. No engine time.
uv run python automation/render_treatments.py

# map-selection icons + the reports/ gallery, engine-free, ~12s for the mod.
# build_mod.py already calls this; run it alone after editing the renderer.
uv run python automation/build_thumbnails.py

# capture needs the game running, Scenario Editor, AA_rw_placeholder_tester,
# Huge [240], 8 players. It rebuilds that state rather than demanding it.
uv run python automation/mod_capture.py --run-id <id> --n-samples 1
uv run python automation/mod_capture.py --run-id <id> --presets korea florida

uv run python automation/compare_starts.py --stock benchmarks --mod <id>
uv run python automation/neutral_supply.py --mod <id> --detail
uv run pytest tests -q
```

`mod/` is generated from `presets/*.json` with `status: shipped`, never
hand-edited; adding a map to it is `update_mod.py --promote-preset`. The lobby Map Size must be `Huge
[240]` - land areas are absolute tile counts, so the wrong size breaks the
map rather than shrinking it.

Each script ships a `<stem>.png` beside it: the image the map-selection
screen shows, 420x420 RGBA and isometric, matching the format stock
`mapicons` and other map-pack mods use. Verified on the real screen
(`reports/map_thumbnails.html`), not just against the file format.

## The docs

Live:

| doc | what it is |
|---|---|
| **PRESETS.md** | **one record per map: window, complete resolved parameters, every build, every capture.** What ships, how a candidate gets promoted, and why a cached build is reused rather than regenerated |
| HISTORY.md | what was done and when, session by session, with the runs and reports each produced |
| **GENERATION.md** | **how generation works, end to end - start here.** Its *Islands* section holds the island design rules: shore is unbuildable, adjacent rocks are one island, gold/stone are worth a transport trip and a small copse is not |
| RESOURCE_TEMPLATES.md | the two stock resource systems, and their measured budgets. Authoritative on resources |
| STOCK_MAP_INVENTORY.md | what stock scripts are on disk; script name vs UI name |
| **EDITOR_AUTOMATION.md** | **how the capture pipeline drives the Scenario Editor - start here for anything UI.** Verify-before-click, what each check costs, the mods failure that silently captures the wrong map |
| RENDER_PIPELINE.md | the original PowerShell pipeline, now deleted. Mechanics superseded by EDITOR_AUTOMATION.md; keep it for *why* this is GUI automation and not a direct engine call |
| RESOURCE_REWORK_STATUS.md | the resource rework's open items - a work queue, not a reference |
| **README_AGENTS.md** | **the concepts: what "fair" and "recognisable" mean here, and how to change things safely.** The fairness model, the stock yardstick, resource amounts, and the load-bearing gotchas |
| CLAUDE.md | working conventions: verify with real renders, no agent quality verdicts, commit incrementally |

History - kept for the record, and carrying known-bad assumptions:

| doc | what it was | why it is stale |
|---|---|---|
| MOD_STATUS.md | 2026-08-01 mod state | its resource analysis rests on a 1999 orphan include no shipping map uses; carries a banner saying so |
| TUNING_STATUS.md | 2026-07-31 window/parameter search | superseded as narrative, but the known-good defaults it settled on are the ones in `cli.py` today |

