# Working conventions for this repo

Concepts (fairness, aesthetics, how to change things) are in
`README_AGENTS.md`. The mechanism is `GENERATION.md`. The record system is
`PRESETS.md`. This file is only the conventions.

## The unit of record is a preset

Every map is one `presets/<label>.json`: the window, the **complete resolved
parameter set**, every `.rms` it has been built into (by sha256), and every
engine capture of those. Never re-specify a map anywhere else — no new
condition-set module, no hand-edited shipping list.

- **After any capture pass, run `automation/preset_import.py`.** It is
  idempotent, and it is what keeps a run from being a folder nobody can join
  back to a parameter set.
- **A preset before engine time, not after.** `preset_cli.py new` (or
  `mod_capture --presets`) so the samples carry the preset's hash.
- **Promote, don't re-specify.** `update_mod.py --promote-preset LABEL`
  flips the status and ships the exact script the engine was measured on
  into both mod roots, in one step; `preset_cli.py retire`/`demote` take it
  back out. A cached build is reused on *content*, never refreshed for
  being old — `--rebuild` is deliberate, and what it produces has not been
  captured yet.
- **Every path in the registry is a last known location, not a promise.**
  Storage is index-only: the registry is committed, the artifacts stay in
  `out/`. `preset_cli.py audit` says what is still there.

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
- **A report over a chosen set of maps is `preset_report.py`, and costs no
  engine time** — the samples, previews and fairness profiles of every past
  run are already on disk. Don't propose a capture pass to answer a question
  the existing captures answer. A builder keyed to one `--run-id` cannot
  compare across passes; that is what made this look expensive.

## Git

Commit incrementally as work happens, split by logical unit — not one lump
at the end.

Record work as it happens too: a session's entry in `HISTORY.md`, dated,
naming the run-ids and reports it produced.

## UI automation

Mechanics: `EDITOR_AUTOMATION.md`. Use `automation/editor.py`
(`ensure_ready`, `generate_and_save`, `locate`, `click_when_ready`) — it
reads the screen with **OmniParser** and confirms a control is present
before clicking it. There is no PowerShell driver any more; every capture
harness shares `generate_and_save()`, so never hand-roll a click sequence
or a coordinate.

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
- **Log through `automation/runlog.py`, never `print`.** Every harness
  writes a terse `log.txt` (for agents; no timestamps, no durations, so it
  diffs cleanly) and a verbose `events.jsonl` (every duration, absolute
  timestamps, failures as `ok: false`). Two lines to hook up: `RunLog(...)`
  then `attach_editor(editor)`. Capture subprocesses rather than letting
  them inherit stdout — a child printing past the logs is the recurring
  way the terse log stops being the whole story.
