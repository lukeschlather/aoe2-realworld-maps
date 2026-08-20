"""Build the installable "Real World Maps" mod from the preset registry -
plus a debug variant that additionally carries the AA_rw_placeholder_tester
slot this project's tuning automation (tuning_matrix.py et al) depends on to
work around the Scenario Editor's list-widget crash bug (see
TUNING_STATUS.md / RENDER_PIPELINE.md).

**What ships is ``presets/*.json`` with ``status: shipped``.** There is no
hand-edited region list here any more. Adding a map to the mod is
``preset_cli.py promote``; the parameters, the window, the captures that
justified it and the date it was promoted all live in the one record.

**A build is reused, never rebuilt for its own sake.** Each preset records
the ``.rms`` it has been built into, by sha256, with every place a copy was
last seen. If one of those copies is still on disk and still hashes to what
was recorded, that file ships as-is: the map that ships is then provably the
map the engine was measured on, and a full rebuild costs seconds instead of
~70s per region of choose_starts annealing. Only a preset with no surviving
build generates, and what it generates is recorded so the next build reuses
it.

That is deliberately a *content* claim rather than a freshness claim. A
cached build was made by an older ``src/``, and it is not upgraded when
``src/`` changes - because a script that has been through the engine is
worth more than a script that is merely current. Pass ``--rebuild`` when the
point is to pick up a generation change; then capture the result before
trusting it.

The one edit a shipped copy gets is its first line: the header comment
carries the map name, so a preset promoted under a new name has that line
rewritten and nothing else. Measured, not assumed - shipped
"RW Great Britain N.rms" and the script captured as "Britain northup
France" differ in exactly that line.

Usage:
    uv run python automation/build_mod.py
    uv run python automation/build_mod.py --presets salish-sea --placeholder salish-sea
    uv run python automation/build_mod.py --rebuild greece
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
from rwmaps.presets import Build, Preset, Registry, sha256_file, utc_now  # noqa: E402
from runlog import git_commit  # noqa: E402

MOD_NAME = "Real World Maps"
DEBUG_MOD_NAME = "Real World Maps (Debug)"
PLACEHOLDER_SLOT = "AA_rw_placeholder_tester.rms"

#: Where a freshly generated build is kept so the next build can reuse it.
#: Working data (gitignored): the durable record is the sha256 in the
#: preset, and losing the cache costs a regeneration, not a fact.
BUILD_CACHE = REPO / "out" / "rms_cache"

#: prefixed onto every shipped script's filename (the in-game "Random Map
#: location" list shows the filename verbatim) so they all sort together
#: instead of being scattered alphabetically among 100+ subscribed-mod
#: entries. Doesn't touch PLACEHOLDER_SLOT, which needs to keep sorting
#: near the very front for the tuning automation's list-crash workaround
#: (see RENDER_PIPELINE.md).
SHIPPED_PREFIX = "RW "


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
#: They are ``status: retired`` presets now, so their parameters, their
#: captures and this reasoning stay joined up instead of the reasoning
#: living here and the parameters being lost.
RETIRED_REGIONS = ("Japan", "Caribbean", "New Zealand")


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
#:
#: Kept here as the recipe for *new* presets. The presets that use it carry
#: it in their own argv.
FOREST_SPLIT = ["--forest-clumps", "36", "--forest-alt", "PINE_FOREST",
                "--forest-spacing", "3"]

#: North toward the upper left - the engine's uncorrected view, and what
#: every region shipped before 2026-08-16 looks like. Orientation is
#: screen-space now and 0 means north-up, so a region that ships this way
#: says so out loud in its argv. This is a knob to revisit per region, not a
#: law: north-up reads better for most places (see the window-candidate
#: report).
NW = ["--north", "-45"]

#: North-up. Identical to omitting the flag, stated out loud for the same
#: reason NW is. The regions taken from the 2026-08-16 candidate report are
#: north-up because that is the orientation they were judged in - see
#: reports/20260816-210117_candidate_report_candidates_n2.html.
NORTH_UP = ["--north", "0"]


def shipped_filename(name: str) -> str:
    return f"{SHIPPED_PREFIX}{name}.rms"


def shipped_presets(registry: Registry | None = None) -> list[Preset]:
    reg = registry or Registry(REPO).load()
    return sorted((p for p in reg.presets.values() if p.status == "shipped"),
                  key=lambda p: p.name)


def shipped_regions(registry: Registry | None = None
                    ) -> list[tuple[str, list[str]]]:
    """``[(display name, rwmaps argv), ...]`` for everything that ships.

    The shape ``MOD_REGIONS`` used to have, kept as a function so the
    capture harnesses and report builders that iterate the shipping list do
    not each have to know about the registry.
    """
    return [(p.name, list(p.argv)) for p in shipped_presets(registry)]


def retitle(text: str, name: str) -> str:
    """The script with its header title line set to ``name``.

    ``rms._HEADER`` opens ``/* <title>``; that is the only place the map
    name appears, which is why a build is reusable across a rename.
    """
    lines = text.splitlines(True)
    if lines and lines[0].startswith("/*"):
        lines[0] = f"/* {name}\n"
    return "".join(lines)


def write_info(mod_root: Path, title: str, description: str) -> None:
    mod_root.mkdir(parents=True, exist_ok=True)
    (mod_root / "info.json").write_text(json.dumps({
        "Author": "rwmaps",
        "CacheStatus": 0,
        "Description": description,
        "Title": title,
    }), encoding="utf-8")


def generate(preset: Preset) -> tuple[Path | None, str, str]:
    """Build ``preset`` into the cache. ``(path, stdout, stderr)``."""
    out = BUILD_CACHE / preset.id
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["uv", "run", "rwmaps", preset.name, "--outdir", str(out),
           "--no-preview", *preset.argv]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stdout, r.stderr
    # rwmaps writes into a timestamped subdir per invocation.
    found = list(out.rglob("*.rms"))
    if len(found) != 1:
        return None, r.stdout, f"expected 1 .rms, found {len(found)}"
    return found[0], r.stdout, r.stderr


def _summary_from_stdout(text: str) -> dict:
    import preset_import
    return preset_import.parse_rwmaps_stdout(text)


def _parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--presets", "--regions", nargs="+", metavar="LABEL",
                   default=None, dest="presets",
                   help="build only these presets, in place, leaving the rest "
                        "of the mod untouched. Labels or display names.")
    p.add_argument("--rebuild", nargs="*", metavar="LABEL", default=None,
                   help="regenerate rather than reuse - all shipped presets "
                        "with no argument, or just the named ones. Use when "
                        "the point is to pick up a generation change; the "
                        "result has not been through the engine.")
    p.add_argument("--placeholder", metavar="LABEL", default=None,
                   help="preset to copy into the AA_rw_placeholder_tester "
                        "slot. Defaults to whichever built first.")
    p.add_argument("--list", action="store_true",
                   help="list what ships, and where each one's build stands")
    return p.parse_args()


def main():
    args = _parse_args()
    reg = Registry(REPO).load()
    shipped = shipped_presets(reg)
    if not shipped:
        sys.exit("nothing has status 'shipped' in presets/ - promote something "
                 "with automation/preset_cli.py promote <label>")

    if args.list:
        for p in shipped:
            hit = p.find_build(REPO)
            where = "reuse " + hit[1].name if hit else "GENERATE (no build on disk)"
            print(f"{p.label:22s} {p.name:18s} {p.n_captured:3d} caps  "
                  f"{where}\n{'':22s} {' '.join(p.argv)}")
        return

    selected = shipped
    partial = args.presets is not None
    if partial:
        selected = [reg.get(k) for k in args.presets]
        not_shipped = [p.label for p in selected if p.status != "shipped"]
        if not_shipped:
            sys.exit(f"not shipped: {not_shipped}. promote them first "
                     f"(preset_cli.py promote <label>) so the mod's contents "
                     f"and the registry cannot disagree.")

    force_all = args.rebuild is not None and not args.rebuild
    force = {reg.get(k).label for k in (args.rebuild or [])}

    # Two shipped presets under one display name write the same filename, and
    # the second one wins in silence. That is exactly what promoting a
    # replacement looks like before the old one is retired - the likeliest
    # way to lose a map is to ship it - so it is an error rather than a
    # last-write.
    by_file: dict[str, list[str]] = {}
    for p in shipped:
        by_file.setdefault(shipped_filename(p.name), []).append(p.label)
    clashes = {f: labels for f, labels in by_file.items() if len(labels) > 1}
    if clashes:
        lines = "\n".join(f"  {f}: {', '.join(labels)}"
                          for f, labels in sorted(clashes.items()))
        sys.exit("two shipped presets would write the same script:\n" + lines
                 + "\n\nretire the one being replaced (preset_cli.py retire "
                   "<label> --why ...) or ship the new one under a different "
                   "--name.")

    # Resolve every reusable build BEFORE anything is wiped, and copy it
    # into the cache. Two paths a build is recorded at are not stable: the
    # shipped copy in mod/, which a full build deletes on the line below,
    # and a capture run's scripts/ dir, which mod_capture clears when that
    # run-id is resumed. Resolving after the wipe silently turned the cache
    # into a 70-second regeneration for exactly the maps it was meant to
    # serve.
    plan: list[tuple[Preset, Path | None, Build | None]] = []
    for preset in selected:
        if force_all or preset.label in force:
            plan.append((preset, None, None))
            continue
        hit = preset.find_build(REPO)
        if not hit:
            plan.append((preset, None, None))
            continue
        build, path = hit
        cached = BUILD_CACHE / preset.id / f"{preset.label}.rms"
        if path.resolve() != cached.resolve():
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, cached)
            rel = cached.resolve().relative_to(REPO.resolve()).as_posix()
            if rel not in build.paths:
                build.paths.append(rel)
                reg.save(preset)
        plan.append((preset, cached, build))

    main_root = REPO / "mod" / MOD_NAME
    debug_root = REPO / "mod" / DEBUG_MOD_NAME
    # Full rebuild wipes: mod/ is generated from the registry, not
    # hand-edited, so a stale filename (a map promoted under an old name)
    # must not linger beside the current one. A --presets build is the
    # deliberate exception - it is an iteration aid.
    if not partial:
        for root in (main_root, debug_root):
            if root.exists():
                shutil.rmtree(root)
    main_scripts = main_root / "resources" / "_common" / "random-map-scripts"
    debug_scripts = debug_root / "resources" / "_common" / "random-map-scripts"
    main_scripts.mkdir(parents=True, exist_ok=True)
    debug_scripts.mkdir(parents=True, exist_ok=True)

    write_info(main_root, MOD_NAME,
               "Playable AoE2 DE random maps generated from real-world coastlines.")
    write_info(debug_root, DEBUG_MOD_NAME,
               "Same maps as 'Real World Maps', plus the AA_rw_placeholder_tester "
               "slot this project's tuning automation swaps candidate scripts into.")

    commit = git_commit()
    first_src = None
    placeholder_src = None
    failures = []
    reused = built = 0
    for preset, src, build in plan:
        if src is not None:
            reused += 1
            print(f"{preset.label}: reuse {build.sha256[:10]} built "
                  f"{build.built_utc[:10]} at {build.src_commit} ({src})")
        else:
            print(f"{preset.label}: generating ...")
            src, stdout, stderr = generate(preset)
            if src is None:
                print(f"  FAILED: {stderr[-800:]}")
                failures.append(preset.label)
                continue
            build = preset.record_build(Build(
                sha256=sha256_file(src), bytes=src.stat().st_size,
                src_commit=commit, built_utc=utc_now(),
                paths=[src.resolve().relative_to(REPO.resolve()).as_posix()],
                command=f"uv run rwmaps {preset.name!r} " + " ".join(preset.argv),
                summary=_summary_from_stdout(stdout)))
            reg.save(preset)
            built += 1
            print(f"  {build.sha256[:10]} {build.summary}")

        text = retitle(src.read_text(encoding="ascii"), preset.name)
        dest = main_scripts / shipped_filename(preset.name)
        # newline="\n": the engine's RMS parser needs unix line endings
        # (see rms.write_rms), and this path writes text on Windows.
        dest.write_text(text, encoding="ascii", newline="\n")
        (debug_scripts / shipped_filename(preset.name)).write_text(
            text, encoding="ascii", newline="\n")
        # Record the copy that ships. retitle() may have changed the header
        # line, so this can be a sha256 the registry has never seen - it is
        # merged by hash, so an unrenamed map just gains a path on its
        # existing build. Without it, `preset_cli.py audit` cannot answer
        # "is what ships what was captured?" for anything promoted after the
        # registry was reconstructed: its only other source of mod/ paths is
        # preset_import's frozen MOD_REGIONS_AT_IMPORT list.
        preset.record_build(Build(
            sha256=sha256_file(dest), bytes=dest.stat().st_size,
            src_commit=build.src_commit if build else commit,
            built_utc=build.built_utc if build else utc_now(),
            paths=[dest.resolve().relative_to(REPO.resolve()).as_posix()],
            command=f"uv run rwmaps {preset.name!r} " + " ".join(preset.argv),
            summary={"note": "the copy that ships, committed in mod/"}))
        reg.save(preset)
        if first_src is None:
            first_src = dest
        if args.placeholder and preset.label in (
                args.placeholder, reg.get(args.placeholder).label):
            placeholder_src = dest
        print(f"  -> {dest}")

    if args.placeholder and placeholder_src is None:
        sys.exit(f"--placeholder {args.placeholder!r} did not build - it must "
                 f"be one of the presets this run built")
    slot_src = placeholder_src or first_src
    if slot_src:
        shutil.copyfile(slot_src, debug_scripts / PLACEHOLDER_SLOT)
        why = "requested" if placeholder_src else "whatever built first"
        print(f"  -> {debug_scripts / PLACEHOLDER_SLOT} (placeholder slot, "
              f"content = {why}, currently {slot_src.name})")

    # The map-selection screen shows <script>.png from beside the script, and
    # a full build has just wiped both mod roots, so the icons have to be
    # redrawn here or the maps ship with the game's generic image. Engine-free
    # and a few seconds for the whole mod.
    build_thumbnails.write_icons()
    if failures:
        print(f"\nFAILED (not in either mod): {failures}")
    print(f"\ndone - {len(selected) - len(failures)}/{len(selected)} maps in "
          f"mod/{MOD_NAME}/ and mod/{DEBUG_MOD_NAME}/ "
          f"({reused} reused, {built} generated)"
          + (" (partial build - other maps left as they were)" if partial else ""))


if __name__ == "__main__":
    main()
