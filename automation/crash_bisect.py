"""Bisect a crashing .rms by deleting one block at a time from the exact
file that crashes.

The crash is in the script, not the automation: it reproduces by hand,
repeatedly, across a reboot. So the thing to vary is the script - and the
way to vary it is to cut blocks out of the *identical* file rather than to
regenerate with different flags. Regenerating re-rolls start placement and
the whole land cover with it, which means a variant that does not crash
has told you nothing: it is a different map.

ANSWERED, 2026-08-12. It is not any one block. It is the
``place_on_specific_land_id`` clause itself, whenever it names a land id
that really exists as a non-player land. Measured on Britain at 240/8,
alternating against the committed script in one editor session:

* committed script (no island land ids at all): **5/5 generated**, 52-60s.
  The same scripts took all 11 regions with no crash and no recovery.
* current script: **0/5**, dead 65-70s in, every time.
* every aimed block kept but the ``place_on_specific_land_id`` *line*
  deleted from each: generated. So the extra ``create_land`` blocks on ids
  10/11 are harmless on their own - 139 of them, and fine.
* aimed lines kept but the lands put back on id 1, so the ids they name no
  longer exist: generated. An id that does not resolve is ignored, not
  fatal.
* ``--cut copse``: **still crashed** (64.9s). The prime suspect recorded
  here previously - 12 objects in 3 tight groups - is exonerated.
* ``--cut piles`` (only the two straggler blocks left aimed): crashed, 63.6s.
* ``--cut trees`` (only the four gold/stone blocks left aimed): crashed, 65.5s.

Cutting either half leaves a crash, and cutting the aiming leaves a
working map, so no object type is special and no single block is the
culprit. One partial exception worth knowing: with only the *large*
island's placements left, generation hung past 300s instead of dying, so
the failure has two faces.

The engine dies as an access violation reading address 0x9 - a null
dereference - sometimes into BugSplat and sometimes into Windows' own
Application Error box.

The cuts below remain useful for narrowing a *different* crash; keep them.
``--cut land-id`` is the one that describes this one.

Usage:
    uv run python automation/crash_bisect.py --list
    uv run python automation/crash_bisect.py --cut land-id --install
    uv run python automation/crash_bisect.py --cut copse --source some.rms
    uv run python automation/crash_bisect.py --restore
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rwmaps import install as install_mod  # noqa: E402
from update_mod import DEBUG_MOD_NAME  # noqa: E402

SLOT = install_mod.scripts_dir(DEBUG_MOD_NAME) / "AA_rw_placeholder_tester.rms"
WORK = REPO / "out" / "crash_bisect"
BACKUP = WORK / "slot_original.rms"

#: One create_object block: the header, its brace body, and the closing
#: brace at the same indent. Non-greedy so it stops at the first close.
_BLOCK = r"\n[ \t]*create_object[ \t]+{obj}\b.*?\n[ \t]*\}}\n"


def _blocks(text: str) -> list[tuple[str, str]]:
    """(object name, whole block text) for every create_object in the file."""
    out = []
    for m in re.finditer(r"\n[ \t]*create_object[ \t]+(\w+)\b.*?\n[ \t]*\}\n",
                         text, re.S):
        out.append((m.group(1), m.group(0)))
    return out


def cut_copse(text: str) -> tuple[str, str]:
    """Remove tree blocks that place a tight group - the copses."""
    removed = 0
    for _, block in _blocks(text):
        if ("STRAGGLER_NEUTRAL" in block and "set_tight_grouping" in block
                and "place_on_specific_land_id" in block):
            text = text.replace(block, "\n")
            removed += 1
    return text, f"removed {removed} island copse block(s)"


def cut_scatter(text: str) -> tuple[str, str]:
    removed = 0
    for _, block in _blocks(text):
        if ("STRAGGLER_NEUTRAL" in block and "set_loose_grouping" in block
                and "place_on_specific_land_id" in block):
            text = text.replace(block, "\n")
            removed += 1
    return text, f"removed {removed} island scatter block(s)"


def cut_island_trees(text: str) -> tuple[str, str]:
    text, a = cut_copse(text)
    text, b = cut_scatter(text)
    return text, f"{a}; {b}"


def cut_neutral_stragglers(text: str) -> tuple[str, str]:
    out = re.sub(r"\n[ \t]*#include_drs[ \t]+includes/stragglers_neutral\.inc",
                 "", text)
    return out, ("removed the stragglers_neutral.inc include"
                 if out != text else "include NOT FOUND")


def cut_per_island_piles(text: str) -> tuple[str, str]:
    """The gold/stone aimed by land id - the other user of that clause."""
    removed = 0
    for name, block in _blocks(text):
        if name in ("GOLD", "STONE") and "place_on_specific_land_id" in block:
            text = text.replace(block, "\n")
            removed += 1
    return text, f"removed {removed} per-island gold/stone block(s)"


def cut_all_land_id(text: str) -> tuple[str, str]:
    """Everything using place_on_specific_land_id, whatever the object.

    The widest cut, and the one that tests the clause itself rather than
    any object placed through it. Two islands measured on Salish Sea took
    nothing from *any* land-id block despite having room, which is
    consistent with ids that do not exist in the generated map - and an id
    that does not resolve is a plausible thing for the engine to fall over
    on.
    """
    removed = 0
    for _, block in _blocks(text):
        if "place_on_specific_land_id" in block:
            text = text.replace(block, "\n")
            removed += 1
    return text, f"removed {removed} land-id-targeted block(s)"


CUTS = {
    "copse": (cut_copse,
              "island copse blocks - the prime suspect: present on Britain "
              "and Italy (both crashed), absent on Salish Sea (did not)"),
    "scatter": (cut_scatter, "island scattered-straggler blocks"),
    "trees": (cut_island_trees, "both kinds of island tree block"),
    "stragglers-inc": (cut_neutral_stragglers,
                       "the map-wide stragglers_neutral.inc include - "
                       "already unlikely, Salish Sea shipped it and lived"),
    "piles": (cut_per_island_piles, "per-island gold and stone"),
    "land-id": (cut_all_land_id,
                "everything aimed by place_on_specific_land_id, to test the "
                "clause rather than any one object"),
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="show the cuts, and what is in the slot now")
    ap.add_argument("--cut", choices=sorted(CUTS),
                    help="write a variant with this removed")
    ap.add_argument("--source", type=Path, default=None,
                    help="operate on this .rms instead of the installed slot")
    ap.add_argument("--install", action="store_true",
                    help="copy the variant into the slot, backing up first")
    ap.add_argument("--restore", action="store_true",
                    help="put the original back in the slot")
    args = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)

    if args.restore:
        if not BACKUP.exists():
            return print(f"no backup at {BACKUP}") or 1
        shutil.copyfile(BACKUP, SLOT)
        print(f"restored {BACKUP} -> {SLOT}")
        return 0

    source = args.source or SLOT
    if not source.exists():
        return print(f"no script at {source}") or 1
    text = source.read_text(encoding="utf-8", errors="replace")

    if args.list or not args.cut:
        print(f"slot: {source}")
        print(f"title: {text.splitlines()[0].strip()}")
        print(f"\ncreate_object blocks ({len(_blocks(text))}):")
        for name, block in _blocks(text):
            tags = [t for t in ("place_on_specific_land_id", "set_tight_grouping",
                                "set_loose_grouping") if t in block]
            n = re.search(r"number_of_objects (\d+)", block)
            g = re.search(r"number_of_groups (\d+)", block)
            print(f"  {name:20} objects={n.group(1) if n else '?':<5}"
                  f"groups={g.group(1) if g else '?':<6}{' '.join(tags)}")
        print("\ncuts:")
        for key, (_, why) in sorted(CUTS.items()):
            print(f"  --cut {key:16} {why}")
        return 0

    fn, why = CUTS[args.cut]
    out_text, note = fn(text)
    if out_text == text:
        print(f"WARNING: nothing changed - {note}")
    dest = WORK / f"{source.stem}__no_{args.cut}.rms"
    dest.write_text(out_text, encoding="utf-8")
    print(f"{note}\nwrote {dest}")

    if args.install:
        if not BACKUP.exists():
            shutil.copyfile(SLOT, BACKUP)
            print(f"backed up the original slot -> {BACKUP}")
        shutil.copyfile(dest, SLOT)
        print(f"installed -> {SLOT}\n\nNow click Generate Map by hand. "
              f"--restore puts the original back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
