# Working conventions for this repo

Concepts (fairness, aesthetics, how to change things) are in
`README_AGENTS.md`. The mechanism is `GENERATION.md`. This file is only the
conventions.

## Verification

**Produce real artifacts and let the user judge them.** Don't offer an
opinion on whether a result "looks right". A synthetic Python preview is
not a substitute for an engine render — one was once mistaken for
validation and a real capture later showed materially worse accuracy.
Surface the data; the judgement is the user's.

**The target is human-judged recognisability** — does a strait look like a
strait, does an island stay an island. Not statistical fairness. When
analysing a batch, foreground what's visually and topologically going on.

**Never fold geometry into a verdict.** TC separation, landmass count and
reachability are facts to report, not scores.

**Small N settles nothing.** 1–2 samples per condition is right for
breadth (RNG variance isn't controllable — spend engine time on more
conditions). Never present aggregates from such a sweep as deciding a
comparison; that needs N=10+.

## Balance, in one page

Details and numbers: `README_AGENTS.md`, then `RESOURCE_TEMPLATES.md`.

- **Stock maps are the yardstick.** They define "a reasonable amount". Read
  every number against a comparable stock map, never against zero or an
  intuition. Arabia is the reference; it is held out from the stock band
  rather than averaged into it.
- **Count resources, not objects.** Six bushes and six gold mines are both
  "6". Convert with `rwmaps/resource_value.py`. Keep the per-kind breakdown
  beside any food total — boar is the best food on the map and a total
  hides that.
- **Quote the radius.** Every count is relative to
  `fairness.OWNERSHIP_RADIUS` (30 walked tiles) — Arabia gives a player 9
  stone within 30 and 16 within 50. The profile stamps it into its output;
  carry it through into anything you write.
- **The unit is a player, not a map.** Map-wide totals hide the only thing
  that matters — what one player has within walking range.
- **"Zero of a kind" is not a verdict.** Stock Arabia places no deer at all
  in half its games, by design.
- **Land is a resource, and the only one that can't be topped up.** Every
  stock map keeps its worst-off player at 0.79–0.96 of that map's median. A
  window that can't clear that is a bad window, not a tuning problem.
- Use `rwmaps.fairness` (exclusive / contested / unclaimed, walked
  distances). `legacy_resources_nearest_tc` is the superseded model, kept
  only so old runs stay comparable.

## Automation and scale

The goal scale is real (~1000 generations), so **design for zero manual
steps per run** from the start. A workflow needing one human action per
iteration saves nothing at that scale. When blocked, going deep beats a
lower-fidelity workaround — as long as the direction is sound.

## Reports

- Show the **complete resolved parameter set** per condition, defaults
  included, not just the diff from a baseline.
- Include a generation timestamp and the repo commit hash.
- Link the real artifacts (`.rms`, `.aoe2scenario`), not just a preview.
- **Never publish as Artifacts.** Reports are local HTML committed to
  `reports/` (self-contained, base64 previews), data in
  `reports/<name>_data/`. Gitignored `out/` is working data only.

## Git

Commit incrementally as work happens, split by logical unit — not one lump
at the end.

## UI automation

Mechanics: `EDITOR_AUTOMATION.md`. Use `automation/editor.py`
(`ensure_ready`, `locate`, `click_when_ready`) — it reads the screen with
**OmniParser** and confirms a control is present before clicking it. The
older PowerShell driver `ui_driver.ps1` and its Windows-OCR seed poll are
legacy, still used by `tuning_matrix.py` and friends; don't build new work
on them.

- **Never click a control without confirming it is there.** Blind clicks
  are the leading suspect for the editor crashes.
- **Verify against observable ground truth**, not an assumed success signal
  — a file's mtime provably changes on a real Save. Don't trust a single
  read straight after a click; transient frames misreport. If two checks
  disagree, investigate rather than believing the convenient one.
- Never trust pixel/byte/colour diffs for UI state; both have given false
  readings from UI shimmer.
- Keep it fast — tune polling for responsiveness rather than padding with
  cautious sleeps.
