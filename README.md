# RW Maps

This has a mod "Real World Maps" which includes a bunch of RW maps.

All the code is vibecoded by Claude Sonnet, for some reason we ended up with code both in automation/ and src/rwmaps/ and there are even some tests in tests.

I'm not entirely sure how to operate the code, but Sonnet can usually figure it out with some vague prompting. To make it easier to operate, automation and src/rwmaps could be consolidated. The UI automation is super-jank, but this is kind of necessary; both autohotkey and Powershell mouse clicks seem to generate a lot of crashes. We arrived at a hacky solution where you have to select `AA_rw_placeholder` in the scenario editor (via map -> Random map) and set it to Map Size => Huge, then this enables an agent to copy over the `AA_rw_placeholder.rms` file, generate a map, save the map as a scenario. The agent can then copy the scenario and analyze the resource locations and actual shoreline. (We can't specify the *exact* shoreline in an RMS, there is still some randomness.) So this enables us to run lots of seeds, compare resource distributions and how badly the shoreline ends up mangled. 

The reports/ folder has some examples of intermediate maps generated and resource scarcity.

I have some ideas about how to reduce the crashiness to allow an agent to click around in the UI more freely and need less handholding. I think probably rewriting the UI automation to use python and OCR, possibly making a map of the scenario editor would help. When left to its own devices Claude doesn't do a very good job even clicking on the correct boxes - I suspect that simply recording a human using the UI and using the pixels the human clicks on for specific UI elements might be enough to stop the crash. Though with how often it crashes, it does seem there is something specific to how Powershell/Python uses the UI that causes crashes, I still feel it's possible the UI automation is just really good at finding the one pixel that triggers a crash.

Other hypotheses for why it's crashing: 

* bot detection is active in the scenario editor because it's just always on whether or not you're in a multiplayer game. (pretty sure this is not the case)
* there's some sort of a mismatch because my UI scale on my machine is not at 100% (it's like 150% or something) and my resolution has some weird interaction with whatever thing is calculating coordinates between AOE2 and the desktop environment which is processing the UI automation click coordinates.


## Operating it

```sh
uv run python automation/build_mod.py --list      # regions and their flags

# one region, ~2 min (a full rebuild is ~17: 11 regions x ~70s of annealing)
uv run python automation/build_mod.py --regions "Salish Sea" --placeholder "Salish Sea"
uv run python automation/install_mod.py --all

# capture needs the game running, Scenario Editor, AA_rw_placeholder_tester,
# Huge [240], 8 players. It aborts immediately if the game is not up.
uv run python automation/mod_capture.py --run-id <id> --n-samples 1

uv run python automation/compare_starts.py --stock benchmarks --mod <id>
uv run python automation/neutral_supply.py --mod <id> --detail
uv run pytest tests -q
```

`mod/` is generated, never hand-edited. The lobby Map Size must be `Huge
[240]` - land areas are absolute tile counts, so the wrong size breaks the
map rather than shrinking it.

## The docs

Live:

| doc | what it is |
|---|---|
| **GENERATION.md** | **how generation works, end to end - start here.** Its *Islands* section holds the island design rules: shore is unbuildable, adjacent rocks are one island, gold/stone are worth a transport trip and a small copse is not |
| RESOURCE_TEMPLATES.md | the two stock resource systems, and their measured budgets. Authoritative on resources |
| STOCK_MAP_INVENTORY.md | what stock scripts are on disk; script name vs UI name |
| **EDITOR_AUTOMATION.md** | **how the capture pipeline drives the Scenario Editor - start here for anything UI.** Verify-before-click, what each check costs, the mods failure that silently captures the wrong map |
| RENDER_PIPELINE.md | the original PowerShell pipeline. Superseded for `mod_capture` by EDITOR_AUTOMATION.md; `stock_capture` still uses it |
| RESOURCE_REWORK_STATUS.md | the resource rework's open items - a work queue, not a reference |
| CLAUDE.md | working conventions: verify with real renders, no agent quality verdicts, commit incrementally |

History - kept for the record, and carrying known-bad assumptions:

| doc | what it was | why it is stale |
|---|---|---|
| MOD_STATUS.md | 2026-08-01 mod state | its resource analysis rests on a 1999 orphan include no shipping map uses; carries a banner saying so |
| TUNING_STATUS.md | 2026-07-31 window/parameter search | superseded as narrative, but the known-good defaults it settled on are the ones in `cli.py` today |
| README_AGENTS.md | older agent-written readme | its CLI examples predate the mod build |

