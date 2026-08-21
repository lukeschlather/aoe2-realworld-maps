"""Sync a repo-built mod/ folder into the AoE2 DE local mods folder, replacing
the by-hand delete+copy that MOD_STATUS.md previously called out as manual.

Defaults to the debug variant (the one the tuning/capture automation and
day-to-day playtesting actually use, since it carries the placeholder slot
too). Pass --mod to target the non-debug "Real World Maps" instead, or
--all to sync both.

Usage:
    uv run python automation/install_mod.py            # debug mod only
    uv run python automation/install_mod.py --mod "Real World Maps"
    uv run python automation/install_mod.py --all
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import install as install_mod  # noqa: E402
from update_mod import DEBUG_MOD_NAME, MOD_NAME  # noqa: E402


def sync(mod_name: str) -> None:
    src = REPO / "mod" / mod_name
    if not src.is_dir():
        raise FileNotFoundError(f"{src} doesn't exist - run automation/update_mod.py first")
    dest = install_mod.find_profile() / "mods" / "local" / mod_name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"synced {src} -> {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mod", choices=[MOD_NAME, DEBUG_MOD_NAME], default=DEBUG_MOD_NAME)
    parser.add_argument("--all", action="store_true", help="sync both mod variants")
    args = parser.parse_args()

    names = [MOD_NAME, DEBUG_MOD_NAME] if args.all else [args.mod]
    for name in names:
        sync(name)


if __name__ == "__main__":
    main()
