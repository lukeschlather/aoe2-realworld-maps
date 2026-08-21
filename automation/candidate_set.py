"""The windows picked out of the window-candidate report, as a region set
``mod_capture.py --region-set`` can drive through the real engine.

Nothing here ships. These are candidates being taken from "the coastline
looks right on paper" to "here is what the engine actually built", which is
the only evidence this project accepts about how a map renders.

Parameter choices are copied from an existing shipped region rather than
tuned, because that is what was asked for:

* **Great Lakes** uses Salish Sea's set - ``--overlap 0.85
  --min-water-width 5 --min-land-width 3``. Only the water width differs
  from the defaults; the other two already are the defaults.
* **Britain** uses the shipped Britain's set, i.e. ``FOREST_SPLIT``.
* **Korea** and **Florida** use Italy's set, which is the plain defaults.
  Greece is the same plus forest knobs (``FOREST_SPLIT`` and
  ``--forest-percent 14``); those are deliberately NOT applied here, since
  picking between the two would be tuning.

Usage:
    uv run python automation/candidate_set.py            # write the JSON
    uv run python automation/mod_capture.py --run-id cands \\
        --region-set out/candidate_set.json --n-samples 2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from update_mod import FOREST_SPLIT  # noqa: E402

#: Salish Sea's raster settings, as MOD_REGIONS spells them.
PUGET = ["--overlap", "0.85", "--min-water-width", "5", "--min-land-width", "3"]


def window(lon: float, lat: float, span: float, north: float = 0.0) -> list[str]:
    """``north`` is screen space: 0 (the default) reads north-up in game."""
    args = [f"--center={lon},{lat}", "--span-km", f"{span:g}"]
    if north:
        args += ["--north", f"{north:g}"]
    return args


#: (name, rwmaps args). Names become directory names under
#: out/mod_capture/<run-id>/, so they stay filesystem-safe.
CANDIDATES: list[tuple[str, list[str]]] = [
    # --- the one asked for by name --------------------------------------
    # "GL Michigan-Huron" in the candidate report; renamed on request.
    ("Michigan", [*window(-85.0, 44.5, 1200), *PUGET]),

    # --- the rest of the Great Lakes set, to see how they render ---------
    ("GL current rotate0", [*window(-84.0, 44.5, 1600, -45), *PUGET]),
    ("GL northup 1600", [*window(-84.0, 44.5, 1600), *PUGET]),
    ("GL northup 1400", [*window(-84.0, 44.5, 1400), *PUGET]),
    ("GL northup 1200", [*window(-83.5, 44.5, 1200), *PUGET]),
    ("GL northup 1000", [*window(-83.0, 45.0, 1000), *PUGET]),
    ("GL five lakes 1800", [*window(-85.0, 45.5, 1800), *PUGET]),
    ("GL Huron-Erie", [*window(-82.0, 44.0, 1100), *PUGET]),
    ("GL Erie-Ontario", [*window(-79.5, 43.2, 900), *PUGET]),
    ("GL Superior", [*window(-87.5, 47.5, 1200), *PUGET]),

    # --- Britain -------------------------------------------------------
    # The shipped window, turned north-up. Norway is already out at this
    # rotation: at rotate 0 it is a 1,142-tile piece, at 45 it is gone.
    ("Britain northup", [*window(-3.0, 54.5, 1300), *FOREST_SPLIT]),
    # Same span, centre pushed south. Measured against the row above: the
    # water between northern Britain and the nearest map edge goes 45 -> 16
    # tiles (64% less, inside the 50-75% asked for) and stays open, so
    # Britain is still an island; Ireland keeps 32 tiles of clearance and
    # the Channel stays 8 tiles wide. The continental patch grows
    # 6,041 -> 10,809 tiles, which is the point - it has to hold two TCs.
    ("Britain northup France", [*window(-3.0, 52.5, 1300), *FOREST_SPLIT]),

    # --- proposed replacements ------------------------------------------
    ("Korea", window(127.0, 36.5, 1200)),
    ("Florida", window(-83.0, 27.5, 1700)),
]


def main() -> None:
    out = REPO / "out" / "candidate_set.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(CANDIDATES, indent=2), encoding="utf-8")
    for name, args in CANDIDATES:
        print(f"{name:26s} {' '.join(args)}")
    print(f"\n{len(CANDIDATES)} candidates -> {out}")


if __name__ == "__main__":
    main()
