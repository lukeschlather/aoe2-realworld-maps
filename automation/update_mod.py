"""Update ``mod/`` - the installable "Real World Maps" mod, and the debug
variant that also carries the AA_rw_placeholder_tester slot every capture
harness writes over (see EDITOR_AUTOMATION.md).

Usage:
    # promote a preset and ship it - the usual way a map reaches the mod
    uv run python automation/update_mod.py --promote-preset salish-sea

    # every shipped map, from scratch (wipes mod/, so a renamed or retired
    # map's script cannot linger)
    uv run python automation/update_mod.py --all

    # ... regenerating rather than reusing, to pick up a generation change
    uv run python automation/update_mod.py --all --rebuild greece

``uv run python automation/install_mod.py --all`` then syncs mod/ into the
game. What ships is ``presets/*.json`` with ``status: shipped``; nothing
here is a hand-edited list.

**A build is reused, never rebuilt for its own sake.** Each preset records
the ``.rms`` it has been built into by sha256, with every place a copy was
last seen; if a copy is still on disk and still hashes to what was
recorded, that file ships as-is. The map that ships is then provably the
map the engine was measured on, and a full update costs seconds instead of
~70s per region of choose_starts annealing.

That is deliberately a *content* claim, not a freshness one: a cached build
made by an older ``src/`` is not upgraded when ``src/`` changes, because a
script that has been through the engine is worth more than one that is
merely current. ``--rebuild`` is how you opt out, and what it produces has
not been captured yet.

The one edit a shipped copy gets is its first line: the header comment
carries the map name, so a preset promoted under a new name has that line
rewritten and nothing else (measured - shipped "RW Great Britain N.rms" and
the script captured as "Britain northup France" differ in exactly that
line).
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


#: Withdrawn 2026-08-15/16: the projections do not read as the real place,
#: and re-profiling their N=10 captures found them broken on supply and on
#: land too (Japan: median 2 stone against Arabia's 9; New Zealand: its
#: worst-off player on 0.35 of its own median land, where every stock map
#: holds 0.79-0.96). The numbers and the reasoning are on the retired
#: presets themselves - ``preset_cli.py show japan`` - and in HISTORY.md.
#: Named here only so the report builders can label them. Do not revisit
#: these windows; a long thin island chain has no interior to give eight
#: starts.
RETIRED_REGIONS = ("Japan", "Caribbean", "New Zealand")


#: More woods, kept apart: one forest terrain has no spacing against
#: itself, so its clumps fuse - split across two terrains they are "other
#: terrain types" to each other and the spacing clause applies. Measured
#: N=3 (RESOURCE_REWORK_STATUS.md): same wood, 3-4x the blobs, largest
#: blob 37%->7%, no walled or sealed start in 48. The recipe for *new*
#: presets; the presets using it carry it in their own argv.
FOREST_SPLIT = ["--forest-clumps", "36", "--forest-alt", "PINE_FOREST",
                "--forest-spacing", "3"]


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
    p.add_argument("--promote-preset", nargs="+", metavar="LABEL",
                   default=None, dest="promote",
                   help="mark these presets shipped and put them in the mod, "
                        "leaving the rest of it untouched. Already-shipped "
                        "labels are simply re-shipped.")
    p.add_argument("--name", help="with one --promote-preset: the in-game map "
                                  "name to ship it under")
    p.add_argument("--why", help="with --promote-preset: what decided it - "
                                 "stored as the preset's note")
    p.add_argument("--all", action="store_true",
                   help="every shipped preset, from scratch: mod/ is wiped "
                        "first, so a renamed or retired map's script cannot "
                        "linger beside the current one")
    p.add_argument("--rebuild", nargs="*", metavar="LABEL", default=None,
                   help="regenerate rather than reuse - everything being "
                        "built with no argument, or just the named ones. Use "
                        "when the point is to pick up a generation change; "
                        "the result has not been through the engine.")
    args = p.parse_args()
    if not args.all and not args.promote:
        p.error("nothing to do - pass --all, or --promote-preset LABEL")
    if args.name and len(args.promote or []) != 1:
        p.error("--name applies to a single --promote-preset")
    return args


def promote_preset(reg: Registry, label: str, name: str | None = None,
                   why: str | None = None) -> Preset:
    """Flip one preset to ``shipped`` and say what that will ship.

    The registry half of ``--promote-preset``: the status, the new name if
    it is being renamed (the old one is kept in ``also_known_as``, since a
    capture run recorded under it still belongs to this preset), and the
    date. Saved before anything is built, so a build that fails leaves the
    decision recorded rather than losing it.
    """
    p = reg.get(label)
    if name and name != p.name:
        aka = p.origin.setdefault("also_known_as", [])
        if p.name not in aka:
            aka.append(p.name)
        p.name = name
    p.status = "shipped"
    p.origin["promoted"] = utc_now()
    if why:
        p.note = why
    reg.save(p)
    hit = p.find_build(REPO)
    print(f"{p.label} -> shipped as {p.name!r}")
    print(f"  {p.describe_window()}")
    print(f"  {p.n_captured} captures on record across {len(p.captures)} runs")
    if hit:
        build, path = hit
        print(f"  build on disk, hash-verified: {build.sha256[:12]} {path}")
        print("  shipping that script as-is - no regeneration")
    else:
        print("  no build on disk - generating one (~70s of annealing) and "
              "recording it")
    return p


def unship(name: str) -> list[Path]:
    """Remove a map's script and icon from both mod roots.

    Called when a preset stops being shipped (``preset_cli.py retire`` /
    ``demote``): only ``--all`` wipes mod/, so without this a withdrawn map
    keeps playing until someone remembers to run a full update.
    """
    gone = []
    for root in (REPO / "mod" / MOD_NAME, REPO / "mod" / DEBUG_MOD_NAME):
        rms = (root / "resources" / "_common" / "random-map-scripts"
               / shipped_filename(name))
        for path in (rms, rms.with_suffix(".png")):
            if path.is_file():
                path.unlink()
                gone.append(path)
    return gone


def main(args=None):
    args = _parse_args() if args is None else args
    reg = Registry(REPO).load()

    if getattr(args, "promote", None):
        for label in args.promote:
            promote_preset(reg, label, name=args.name, why=args.why)
        print()

    shipped = shipped_presets(reg)
    if not shipped:
        sys.exit("nothing has status 'shipped' in presets/ - ship something "
                 "with --promote-preset <label>")

    selected = shipped
    # Everything but --all leaves the rest of the mod as it is.
    labels = getattr(args, "presets", None) or getattr(args, "promote", None)
    partial = not args.all
    if partial:
        selected = [reg.get(k) for k in labels]
        not_shipped = [p.label for p in selected if p.status != "shipped"]
        if not_shipped:
            sys.exit(f"not shipped: {not_shipped}. pass them to "
                     f"--promote-preset instead, so the mod's contents and "
                     f"the registry cannot disagree.")

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
        print(f"  -> {dest}")

    # The debug mod's slot only has to hold *a* valid script so the entry
    # exists in the editor's Random Map list: every capture harness writes
    # over the installed copy (slot.py) before it generates, and nothing
    # reads the committed one. So fill it when mod/ was just wiped or when
    # it is missing, and otherwise leave it alone - rewriting it on every
    # promote churned 350KB of committed file for no reader.
    slot = debug_scripts / PLACEHOLDER_SLOT
    if first_src is not None and (args.all or not slot.is_file()):
        shutil.copyfile(first_src, slot)
        print(f"  -> {slot} (placeholder slot, content = {first_src.name})")

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
