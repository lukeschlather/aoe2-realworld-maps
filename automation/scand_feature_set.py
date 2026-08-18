"""The Scandinavia feature conditions, as a region set for ``mod_capture``.

Three conditions on one window, so the only thing that differs between them
is the ``features`` layer. The question they exist to answer is the one no
Python preview can: **what does SHALLOWS actually look like, and does the
Copenhagen area read as two islands you can sail through?**

The window is ``21.5, 63.8`` at 2000 km with ``--min-water-width 2``, chosen
off the candidate report and then narrowed by measurement:

* ``--min-water-width 2`` rather than the shipped 4 is what unseals the
  Danish straits. At 4 the Baltic is an enclosed 5,351-tile lake and the
  navigable width from the Baltic to the Atlantic is 0.00 tiles - there is
  no route at all.
* ``63.8`` is the best latitude in the viable band, not the extreme of it.
  Asked for "64 or 65", measured: 63.5 and 63.8 give a 1.41-tile bottleneck,
  63.6/63.7/63.9 give 1.00, **64.0 severs the route entirely**, and by 65.0
  Denmark has left the window altogether and all four Danish features are
  skipped. So further in the direction asked for is better right up until it
  is catastrophic, which is exactly why it was measured before being
  captured.

The three conditions:

``plain``
    No features. The control - and the honest baseline for "was the feature
    layer needed at all".
``shallows`` (``zealand-funen``)
    Zealand and Funen forced to land, the three belts drawn as channels of
    SHALLOWS. Passable by boats *and* fordable by land units, so no land
    route that exists in ``plain`` is removed.
``cut`` (``zealand-funen-cut``)
    The same two islands, but the belts cut as real water. Genuine islands
    that need a boat. This is the one that looked best on paper; whether it
    survives the engine is the point of capturing it.

Note the mask-stage numbers are identical across all three (land 55.6%, void
100.5) because shallows never enter the land mask - which is precisely why
this needs an engine render rather than another table.

Usage:
    uv run python automation/scand_feature_set.py
    uv run python automation/mod_capture.py --run-id scand_feat \\
        --region-set out/scand_feature_set.json --n-samples 2
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).parent.parent

#: Everything the three conditions share. Orientation is stated out loud:
#: -45 is the engine's uncorrected view, north toward the upper left, which
#: is what the other shipped regions use and what these were judged in.
BASE = ["--center=21.5,63.8", "--span-km", "2000",
        "--min-water-width", "2", "--north", "-45"]

CONDITIONS: list[tuple[str, list[str]]] = [
    ("Scand plain", [*BASE]),
    ("Scand shallows", [*BASE, "--feature-preset", "zealand-funen"]),
    ("Scand cut", [*BASE, "--feature-preset", "zealand-funen-cut"]),
]


def main() -> None:
    out = REPO / "out" / "scand_feature_set.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(CONDITIONS, indent=2), encoding="utf-8")
    for name, args in CONDITIONS:
        print(f"{name:18s} {' '.join(args)}")
    print(f"\n{len(CONDITIONS)} conditions -> {out}")


if __name__ == "__main__":
    main()
