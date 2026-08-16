# Working conventions for this repo

## Verification philosophy

Don't make playability or coastline-recognition judgment calls yourself —
produce real artifacts and let the user judge them personally. "Agents can't
recognize the coastline the way a human would; game design is hard and
sometimes you have to play it to find out." A synthetic Python preview (e.g.
disc-union rendering) is not a substitute for a real engine render — one was
previously mistaken for validation and a real captured render later showed
materially worse accuracy and visible water-feature shrinkage the preview
didn't reveal. See `RENDER_PIPELINE.md` for the real-render pipeline this
depends on.

When asked to evaluate map/terrain generation quality: produce real engine
renders or clearly-labeled real data, and surface the data rather than
offering an opinion on whether a result "looks right."

**The optimization target is human-judged recognizability of major
real-world features (does a strait look like a strait, does an island stay
an island) — NOT statistical fairness.** Don't present fairness-stat
aggregates (TC separation, land-reachability, zero-of-a-kind rate) from a
small-N sweep as if they settle a comparison between conditions; that needs
N=10+ per condition, which breadth-over-parameters sweeps intentionally
don't have. When asked to analyze a batch of captures, foreground what's
visually/topologically going on, not fairness tables.

**Never fold geometry facts into a pass/fail verdict.** A real-world
archipelago map normally has players on separate islands — that's geography,
not unfairness. TC separation, landmass count and pairwise
land-reachability are plain facts for the user to judge, never a computed
verdict.

**Measure resources in resources, not in objects, and always against
stock.** Six bushes and six gold mines are both "6" and are nothing alike;
a boar is worth more than three sheep and gathers faster than any of them.
Convert with `rwmaps/resource_value.py`, keep the per-kind breakdown beside
any food total (boar is the best food on the map and a total hides that),
and show object counts next to amounts — the counts are ground truth, the
amounts are provisional.

"Zero of a kind" is **not** the unambiguous problem this file used to call
it. Stock Arabia rolls a 50% chance of placing no deer at all, so half of
all Arabia games trip it by design; Loch Ness and Black Forest leave a
third of players with no sheep. Read any such rate against the stock rate
for a comparable map, never against zero.

Every count depends on `fairness.OWNERSHIP_RADIUS` (30 tiles walked), and
that choice dominates the answer — Arabia gives a player 9 stone within 30
tiles and 16 within 50. **Quote the radius whenever quoting a count.**

What *is* worth flagging is supply far below what stock maps give for the
same currency at the same radius — the retired Japan window sat at a median
of 2 stone per player against Arabia's 9, with 39 of 80 player-samples
having none at all. See `RESOURCE_TEMPLATES.md`.

## Automation and scale

The goal scale is real (~1000 generations for review), so solutions must be
fully automatable with zero manual steps per run. Splitting a workflow so the
user does one manual step per iteration doesn't save any time at that scale
— design for zero-touch automation from the start whenever scale is
mentioned or implied. When a technical blocker appears, going deep (e.g.
reverse-engineering the game binary) is preferable to a lower-fidelity
workaround, as long as the direction is sound — see `RENDER_PIPELINE.md`'s
"Why not call the engine directly" for a case where that path was tried and
correctly abandoned in favor of a simpler working alternative.

**When exploring generation parameters, prioritize breadth over parameters
over repeated sampling of RNG variance.** 1-2 samples per parameter condition
is enough; RNG variance isn't controllable anyway, so spend the saved engine
time covering more distinct parameters/values instead of resampling the same
one repeatedly.

## Reports

- Show the *complete* resolved parameter set per condition (every relevant
  parameter, defaults included), not just what differs from a baseline.
- Include a generation timestamp and the repo commit hash the report was
  built from.
- Link to the actual underlying files (`.rms` script, `.aoe2scenario`
  capture), not just an embedded preview image — the user wants to inspect
  or reuse the exact source artifact.
- **Never publish this project's reports as Artifacts (claude.ai uploads).**
  Reports are local HTML files, committed to `reports/` (self-contained,
  embedded base64 previews), with backing data in `reports/<name>_data/`
  (also committed). Gitignored `out/` is for reproducible working data only.

## Git hygiene

Commit incrementally, with clear history, as work happens — not just in one
lump at the end. Split commits by logical unit (e.g. one commit for a
tuning-knob addition, a separate one for a performance fix, a separate one
for a generated report) rather than batching unrelated changes together.

## UI-automation debugging (see `RENDER_PIPELINE.md` for the mechanics)

When automating game UI clicks and something doesn't work, verify against
real, directly-observable ground truth instead of trusting an assumed
success signal (e.g. the Scenario Editor's Seed value box provably changes
on a real Generate Map click; a file's timestamp provably changes on a real
Save). Don't trust a single read immediately after a click — transient
frames can misreport. If two different verification approaches disagree,
investigate rather than picking the more convenient one.

Prefer OCR (Windows' built-in `Windows.Media.Ocr`) over pixel/byte/color
comparison for reading game UI state — byte-diffs and color-average
heuristics have both given false readings here, apparently from subtle UI
shimmer/animation between otherwise-identical-looking frames.

Speed matters even during debugging — tune polling intervals and retry
counts for actual responsiveness; don't pad with generous fixed sleeps out
of caution, and don't waste time on dead screen states.
