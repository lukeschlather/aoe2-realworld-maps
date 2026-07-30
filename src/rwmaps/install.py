"""Install generated scripts into AoE2 DE's local mod folder.

A ``.rms`` dropped here shows up under Map Style *Custom*, and the engine sends
it to other players when you host a multiplayer game - no game files are
touched and nothing needs to be published.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MOD_NAME = "Real World Projections"
_PROFILE_ROOT = Path(os.environ.get("USERPROFILE", Path.home())) / "Games" / "Age of Empires 2 DE"


def find_profile() -> Path:
    """Locate the AoE2 DE user profile directory (the numeric Steam-id folder)."""
    if not _PROFILE_ROOT.is_dir():
        raise FileNotFoundError(f"AoE2 DE user folder not found at {_PROFILE_ROOT}")
    numeric = [p for p in _PROFILE_ROOT.iterdir() if p.is_dir() and p.name.isdigit()]
    real = [p for p in numeric if p.name != "0"] or numeric
    if not real:
        raise FileNotFoundError(f"no profile folder under {_PROFILE_ROOT}")
    return max(real, key=lambda p: p.stat().st_mtime)


def mod_dir(mod_name: str = MOD_NAME) -> Path:
    return find_profile() / "mods" / "local" / mod_name


def scripts_dir(mod_name: str = MOD_NAME) -> Path:
    return mod_dir(mod_name) / "resources" / "_common" / "random-map-scripts"


def ensure_mod(mod_name: str = MOD_NAME, description: str = "") -> Path:
    """Create the local mod skeleton if missing and return its scripts folder."""
    scripts = scripts_dir(mod_name)
    scripts.mkdir(parents=True, exist_ok=True)
    info = mod_dir(mod_name) / "info.json"
    if not info.exists():
        info.write_text(
            json.dumps({
                "Author": "rwmaps",
                "CacheStatus": 0,
                "Description": description or "Random maps with real-world coastlines.",
                "Title": mod_name,
            }),
            encoding="utf-8",
        )
    return scripts
