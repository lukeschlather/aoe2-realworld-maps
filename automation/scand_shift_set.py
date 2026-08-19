"""Scandinavia shifted 10/15/20 tiles for less ocean, as a region set.

Asked for: move the centre 10, 15 and 20 tiles "toward the top left side of
the map" so there is less ocean. Those two halves of the request point in
opposite directions, which was worth measuring rather than guessing.

**Screen up-left is geographic north.** Measured, not reasoned: the pictures
are the grid turned counter-clockwise by ``thumbnail.ICON_ROTATION``, and
under that turn grid ``-row`` lands up-and-left on screen, while grid ``-row``
is north (30 tiles that way takes the centre from 63.76N to 66.01N).

And north is the wrong way for this goal, on every measure:

| shift from 21.5,63.8   | land  | ocean | nav width | Denmark          |
|------------------------|-------|-------|-----------|------------------|
| baseline               | 55.6% | 44.4% | 1.00      | intact           |
| 10 up-left  (north)    | 53.5% | 46.5% | 0.00      | 1 feature skipped|
| 15 up-left  (north)    | 52.1% | 47.9% | 0.00      | 5 skipped        |
| 20 up-left  (north)    | 50.8% | 49.2% | 0.00      | 5 skipped        |
| 10 down-right (south)  | 59.5% | 40.5% | 1.41      | intact           |
| 15 down-right (south)  | 61.6% | 38.4% | 0.00      | intact           |
| 20 down-right (south)  | 63.7% | 36.3% | 0.00      | intact           |

North *adds* ocean, raises the void radius (100 -> 111 tiles), severs the
Baltic-to-Atlantic route, and by 15 tiles has pushed Denmark off the window
so the whole shallows layer silently stops applying. South does the opposite
on all of those except the route. So these are the southward shifts.

The 0.00 on the 15- and 20-tile rows is not a reason to skip them: that
number is measured on the land mask, which does not contain shallows, and at
the baseline it read 1.00 while the real captures measured 2.00-2.83 because
in a capture the shallows are navigable. Whether the shallows reopen the
route at 15 and 20 tiles is exactly the question a generation answers and a
table cannot.

Centres are read off the baseline window's own ``tile_lonlat`` at
``(120 - n, 120)`` rather than by adding degrees by hand, so "10 tiles" means
ten of that window's tiles.

Usage:
    uv run python automation/scand_shift_set.py
    uv run python automation/mod_capture.py --run-id scand_shift \\
        --region-set out/scand_shift_set.json --n-samples 2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps.projection import MapWindow  # noqa: E402

BASE_LON, BASE_LAT, SPAN, SIZE, NORTH = 21.5, 63.8, 2000.0, 240, -45.0

#: Tile shifts to try, all down-right on screen (south).
SHIFTS = (10, 15, 20)

COMMON = ["--span-km", f"{SPAN:g}", "--min-water-width", "2",
          "--north", f"{NORTH:g}", "--feature-preset", "zealand-funen"]


def centres() -> list[tuple[str, float, float]]:
    """(label, lon, lat) for each shift, in the window's own tile units."""
    w = MapWindow.from_center("laea", BASE_LON, BASE_LAT, SPAN, SIZE, NORTH)
    lon, lat = w.tile_lonlat()
    mid = SIZE // 2
    out = []
    for n in SHIFTS:
        # +row is down-right on screen, i.e. south.
        out.append((f"Scand shift {n}", float(lon[mid + n, mid]),
                    float(lat[mid + n, mid])))
    return out


def conditions() -> list[tuple[str, list[str]]]:
    return [(name, [f"--center={lo:.4f},{la:.4f}", *COMMON])
            for name, lo, la in centres()]


def main() -> None:
    conds = conditions()
    out = REPO / "out" / "scand_shift_set.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(conds, indent=2), encoding="utf-8")
    for name, extra in conds:
        print(f"{name:16s} {' '.join(extra)}")
    print()
    print(f"{len(conds)} conditions -> {out}")


if __name__ == "__main__":
    main()
