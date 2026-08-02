# RW Maps

This has a mod "Real World Maps" which includes a bunch of RW maps.

All the code is vibecoded by Claude Sonnet, for some reason we ended up with code both in automation/ and src/rwmaps/ and there are even some tests in tests.

I'm not entirely sure how to operate the code, but Sonnet can usually figure it out with some vague prompting. To make it easier to operate, automation and src/rwmaps could be consolidated. The UI automation is super-jank, but this is kind of necessary; both autohotkey and Powershell mouse clicks seem to generate a lot of crashes. We arrived at a hacky solution where you have to select `AA_rw_placeholder` in the scenario editor (via map -> Random map) and set it to Map Size => Huge, then this enables an agent to copy over the `AA_rw_placeholder.rms` file, generate a map, save the map as a scenario. The agent can then copy the scenario and analyze the resource locations and actual shoreline. (We can't specify the *exact* shoreline in an RMS, there is still some randomness.) So this enables us to run lots of seeds, compare resource distributions and how badly the shoreline ends up mangled. 

The reports/ folder has some examples of intermediate maps generated and resource scarcity.

I have some ideas about how to reduce the crashiness to allow an agent to click around in the UI more freely and need less handholding. I think probably rewriting the UI automation to use python and OCR, possibly making a map of the scenario editor would help. When left to its own devices Claude doesn't do a very good job even clicking on the correct boxes - I suspect that simply recording a human using the UI and using the pixels the human clicks on for specific UI elements might be enough to stop the crash. Though with how often it crashes, it does seem there is something specific to how Powershell/Python uses the UI that causes crashes, I still feel it's possible the UI automation is just really good at finding the one pixel that triggers a crash.

Other hypotheses for why it's crashing: 

* bot detection is active in the scenario editor because it's just always on whether or not you're in a multiplayer game. (pretty sure this is not the case)
* there's some sort of a mismatch because my UI scale on my machine is not at 100% (it's like 150% or something) and my resolution has some weird interaction with whatever thing is calculating coordinates between AOE2 and the desktop environment which is processing the UI automation click coordinates.


* MOD_STATUS.md - agent notes about building the mod
* TUNING_STATUS.md - agent notes about tuning done to identify the parameters used to generate good RMS scripts.
* README-AGENTS.md older agent-written readme
* RENDER_PIPELINE.md - agent notes about UI automation to test generating maps

