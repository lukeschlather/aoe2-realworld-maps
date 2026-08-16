"""Build the installable "Real World Maps" mod - just .rms scripts, no
engine time, cheap to (re)build - plus a debug variant that additionally
carries the AA_rw_placeholder_tester slot this project's tuning automation
(tuning_matrix.py et al) depends on to work around the Scenario Editor's
list-widget crash bug (see TUNING_STATUS.md / RENDER_PIPELINE.md).

Every region here relies on rwmaps's own known-good defaults (resolution
50m, overlap 0.85, min-water/land-width 4/3, clumping-factor 8 - see the
comment above --resolution in src/rwmaps/cli.py) EXCEPT Salish Sea, which
overrides consolidation width to victoria_recenter's own verified value
(5/3, cell 0a8509cf) since that's a specific already-verified-good data
point rather than the general-purpose default.

All regions have been through an N=10-per-region real-engine capture pass
(reports/20260809-052633_mod_report_sysa_n10.html). Salish Sea was the
original hand-verified data point; the rest were a first cut picked for
geographic variety, verified/fixed since. Three of that cut were retired on
2026-08-15 - see RETIRED_REGIONS below.

Italy ships as two variants: "Cramped Italy" (all 8 players crowded onto
the single connected mainland/France/Balkans landmass - the original,
unmodified behavior) and "Italy" (`--spread-islands`, spreading players
across Sardinia/Corsica/Tunisia and the Italian peninsula itself instead of
just the mainland's far corners - see MOD_STATUS.md for the full
investigation).

A full build is ~8 regions x ~70s of choose_starts annealing, so roughly
10 minutes - far too slow to sit inside an edit/generate/capture loop. Pass
``--regions`` to rebuild just the ones you are working on: that skips the
wipe and overwrites only those scripts in place, leaving the other regions'
scripts alone. ``--placeholder`` then drops the region you care about into
the AA_rw_placeholder_tester slot the capture automation drives, so a
single-map iteration is one build command and one install.

Usage:
    uv run python automation/build_mod.py
    uv run python automation/build_mod.py --regions "Salish Sea" --placeholder "Salish Sea"
    uv run python automation/build_mod.py --list
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import build_thumbnails  # noqa: E402

MOD_NAME = "Real World Maps"
DEBUG_MOD_NAME = "Real World Maps (Debug)"
PLACEHOLDER_SLOT = "AA_rw_placeholder_tester.rms"

#: prefixed onto every shipped script's filename (the in-game "Random Map
#: location" list shows the filename verbatim) so they all sort together
#: instead of being scattered alphabetically among 100+ subscribed-mod
#: entries. Doesn't touch PLACEHOLDER_SLOT, which needs to keep sorting
#: near the very front for the tuning automation's list-crash workaround
#: (see RENDER_PIPELINE.md).
SHIPPED_PREFIX = "RW "

#: Originally flagged by the N=10 mod_capture.py real-engine pass
#: (out/mod_capture/full_pass/results.jsonl, see reports/*_mod_report_*.html)
#: as having a high "any player zero-of-a-kind" rate - Britain 8/10, Japan/
#: Caribbean/New Zealand 10/10 (all ISLANDS-type, narrow coastlines starving
#: gold/stone placement - see MOD_STATUS.md). All four are fixed as of
#: 2026-08-01 via `--tight-resources` (see MOD_REGIONS below) and re-verified
#: with a 10-sample real-engine retest each (0-1/10 any-zero, down from
#: 8-10/10) - so this set is empty for now. Italy's own crowding problem
#: (also root-caused 2026-08-01, see MOD_STATUS.md and the git history of
#: choose_starts()/`--spread-islands` in src/rwmaps/analysis.py) is handled
#: differently: two named variants ship side by side below rather than one
#: region tagged Broken.
BROKEN_REGIONS = set()

#: Dropped from the shipped mod 2026-08-15 because their projections do not
#: read as the real place.
#:
#: Re-profiling the archived N=10 captures on 2026-08-16 showed they were
#: also **broken on supply**, which is a harder fact than the aesthetic
#: call. Stone per player within 30 walked tiles, against stock Arabia's
#: median of 9 (80 player-samples each, 24 for Arabia):
#:
#: | map         | median stone | players with none |
#: |-------------|--------------|-------------------|
#: | Arabia      | 9            | 0/24              |
#: | Japan       | **2**        | **39/80**         |
#: | New Zealand | 5            | 28/80             |
#: | Caribbean   | 9            | 14/80             |
#: | Britain     | 9            | 0/80              |
#: | Salish Sea  | 9            | 0/80              |
#:
#: Two stone is not a start. Every shipped region that survived has zero
#: players missing gold or stone across all 80 samples. Japan also ran a
#: median of 7 gold against Arabia's 15, with 18/80 players at none.
#:
#: Land, added to the model 2026-08-16, says the same thing a third time
#: and says it about the shape rather than the placement. Worst-off player
#: as a fraction of that map's own median - every stock map holds
#: 0.79-0.96:
#:
#: | map         | min land | median | min/med |
#: |-------------|----------|--------|---------|
#: | Arabia      | 2,676    | 3,248  | 0.82    |
#: | New Zealand | **300**  | 848    | 0.35    |
#: | Japan       | 456      | 756    | 0.60    |
#:
#: A long thin island chain has no interior, so eight starts have nowhere
#: to go but the coastline and no arrangement of them fixes it. Neither
#: window is redeemable by tuning, and at 240 tiles the two look
#: interchangeable anyway. Do not revisit these two; pick chunkier targets.
#:
#: The windows stay in ``cli.REGIONS`` - that dict is a library of starting
#: points, not a shipping list. Replacements are being chosen from the
#: window-candidate report.
RETIRED_REGIONS = ("Japan", "Caribbean", "New Zealand")


def shipped_filename(name: str) -> str:
    tag = "(Broken) " if name in BROKEN_REGIONS else ""
    return f"{SHIPPED_PREFIX}{tag}{name}.rms"


#: (display name, extra rwmaps CLI args beyond the name itself).
#:
#: `--tight-resources` was dropped from all six regions that carried it
#: (2026-08-08). It existed to backstop `land_and_water_resources.inc`'s
#: silent placement failures by adding a second, closer set of gold/stone/
#: deer/boar on top of the ones that vanished. Resources now come from
#: System A (see `src/rwmaps/rms_objects.py`), which retries a placement
#: that does not fit rather than dropping it, so the backstop has nothing
#: left to back up - it would simply double-place. RESOURCE_TEMPLATES.md is
#: explicit that the modern tuning *replaces* this rather than stacking
#: with it. Per-region resource flavor is now a `ResourceFlavor` choice, not
#: a CLI flag.
#: More woods, kept apart. A single forest terrain has no spacing against
#: itself, so its clumps fuse: asking Greece for 36 clumps instead of 12
#: gave it FEWER woods (21 against 28) with 61% of the wood in the largest.
#: Split across two terrain types they are "other terrain types" to each
#: other and the spacing clause finally applies between them.
#:
#: Measured N=3 per map, 24 player-starts each, against the N=1 baseline:
#:
#: | map     | wood     | blobs   | largest  | p90 blocked | worst |
#: |---------|----------|---------|----------|-------------|-------|
#: | Britain | 21 -> 21 | 27 -> 93| 37% -> 7%| 58% -> 32%  | 60->52|
#: | Greece  | 25 -> 23 | 28 ->126| 33% -> 5%| 55% -> 43%  | 62->45|
#:
#: and no start on either map is walled, sealed or tight in 48 starts,
#: against Britain's France player sitting on one corridor at 87% blocked.
FOREST_SPLIT = ["--forest-clumps", "36", "--forest-alt", "PINE_FOREST",
                "--forest-spacing", "3"]

#: Every shipped region generates with north toward the upper left - the
#: engine's uncorrected view - which is what they have always looked like.
#: Before 2026-08-16 that was the default and went unwritten; orientation is
#: screen-space now and 0 means north-up, so it has to be said out loud.
#: This is a knob to revisit per region, not a law: north-up reads better
#: for most places (see the window-candidate report).
NW = ["--north", "-45"]

MOD_REGIONS = [
    ("Salish Sea", ["--center=-122.9,48.15", "--span-km", "260",
                     "--overlap", "0.85", "--min-water-width", "5", "--min-land-width", "3", *NW]),
    ("Cramped Italy", ["--region", "italy", *NW]),
    ("Italy", ["--region", "italy", "--spread-islands", *NW]),
    # Britain and Greece split their forest across two terrain types so it
    # stops fusing into one mass - see FOREST_SPLIT below. Britain pays no
    # wood for it; Greece needs its budget raised to 14 because the second
    # block under-places, which nets out at 23% wood against 25% before.
    ("Britain", ["--region", "britain", *FOREST_SPLIT, *NW]),
    ("Greece", ["--region", "greece", *FOREST_SPLIT, "--forest-percent", "14", *NW]),
    ("Chesapeake Bay", ["--region", "chesapeake", *NW]),
    ("Black Sea", ["--region", "blacksea", *NW]),
    ("Scandinavia", ["--region", "scandinavia", *NW]),
]


def write_info(mod_root: Path, title: str, description: str) -> None:
    mod_root.mkdir(parents=True, exist_ok=True)
    (mod_root / "info.json").write_text(json.dumps({
        "Author": "rwmaps",
        "CacheStatus": 0,
        "Description": description,
        "Title": title,
    }), encoding="utf-8")


def _parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--regions", nargs="+", metavar="NAME", default=None,
                   help="rebuild only these regions, in place, leaving the "
                        "rest of the mod untouched. Names as in --list.")
    p.add_argument("--placeholder", metavar="NAME", default=None,
                   help="region to copy into the AA_rw_placeholder_tester "
                        "slot. Defaults to whichever region built first.")
    p.add_argument("--list", action="store_true", help="list region names and exit")
    return p.parse_args()


def _select(names: list[str] | None) -> list[tuple[str, list[str]]]:
    """MOD_REGIONS entries matching ``names``, case-insensitively."""
    if not names:
        return MOD_REGIONS
    by_lower = {n.lower(): (n, extra) for n, extra in MOD_REGIONS}
    chosen, unknown = [], []
    for want in names:
        hit = by_lower.get(want.lower())
        if hit is None:
            unknown.append(want)
        else:
            chosen.append(hit)
    if unknown:
        known = ", ".join(n for n, _ in MOD_REGIONS)
        sys.exit(f"unknown region(s): {unknown}\nknown regions: {known}")
    return chosen


def main():
    args = _parse_args()
    if args.list:
        for name, extra in MOD_REGIONS:
            print(f"{name:<16} {' '.join(extra)}")
        return
    regions = _select(args.regions)
    partial = args.regions is not None

    tmp_out = REPO / "out" / "mod_build"
    if tmp_out.exists():
        shutil.rmtree(tmp_out)
    tmp_out.mkdir(parents=True)

    main_root = REPO / "mod" / MOD_NAME
    debug_root = REPO / "mod" / DEBUG_MOD_NAME
    # Full rebuild each time - mod/ is regenerated from MOD_REGIONS, not
    # hand-edited, so stale filenames (e.g. from before SHIPPED_PREFIX was
    # added) must not linger alongside the newly-prefixed ones. A --regions
    # build is the deliberate exception: it is an iteration aid, so it
    # overwrites its own scripts and leaves every other file in place.
    if not partial:
        if main_root.exists():
            shutil.rmtree(main_root)
        if debug_root.exists():
            shutil.rmtree(debug_root)
    main_scripts = main_root / "resources" / "_common" / "random-map-scripts"
    debug_scripts = debug_root / "resources" / "_common" / "random-map-scripts"
    main_scripts.mkdir(parents=True, exist_ok=True)
    debug_scripts.mkdir(parents=True, exist_ok=True)

    write_info(main_root, MOD_NAME,
               "Playable AoE2 DE random maps generated from real-world coastlines.")
    write_info(debug_root, DEBUG_MOD_NAME,
               "Same maps as 'Real World Maps', plus the AA_rw_placeholder_tester "
               "slot this project's tuning automation swaps candidate scripts into.")

    first_rms = None
    placeholder_rms = None
    failures = []
    for name, extra in regions:
        region_out = tmp_out / name
        cmd = ["uv", "run", "rwmaps", name, "--outdir", str(region_out),
               "--no-preview", *extra]
        print(f"generating {name}: {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAILED: {r.stderr[-800:]}")
            failures.append(name)
            continue
        rms_files = list(region_out.rglob("*.rms"))
        if len(rms_files) != 1:
            print(f"  SKIP: expected 1 .rms, found {len(rms_files)}")
            failures.append(name)
            continue
        dest_main = main_scripts / shipped_filename(name)
        shutil.copyfile(rms_files[0], dest_main)
        shutil.copyfile(rms_files[0], debug_scripts / shipped_filename(name))
        if first_rms is None:
            first_rms = rms_files[0]
        if args.placeholder and name.lower() == args.placeholder.lower():
            placeholder_rms = rms_files[0]
        print(f"  -> {dest_main}")

    if args.placeholder and placeholder_rms is None:
        sys.exit(f"--placeholder {args.placeholder!r} did not build - it must "
                 f"be one of the regions this run generated")
    slot_src = placeholder_rms or first_rms
    if slot_src:
        shutil.copyfile(slot_src, debug_scripts / PLACEHOLDER_SLOT)
        why = "requested" if placeholder_rms else "whatever generated first"
        print(f"  -> {debug_scripts / PLACEHOLDER_SLOT} (placeholder slot, "
              f"content = {why}, currently {slot_src.parent.name})")

    shutil.rmtree(tmp_out)
    # The map-selection screen shows <script>.png from beside the script, and
    # a full build has just wiped both mod roots, so the icons have to be
    # redrawn here or the maps ship with the game's generic image. Engine-free
    # and a few seconds for the whole mod.
    build_thumbnails.write_icons()
    if failures:
        print(f"\nFAILED regions (not in either mod): {failures}")
    print(f"\ndone - {len(regions) - len(failures)}/{len(regions)} regions in "
          f"mod/{MOD_NAME}/ and mod/{DEBUG_MOD_NAME}/"
          + (" (partial build - other regions left as they were)" if partial else ""))


if __name__ == "__main__":
    main()
