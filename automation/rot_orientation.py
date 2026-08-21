"""Which of the four orientations did the engine actually roll?

The script says the same coastline in four quarter turns and the *engine*
picks one per generation (``rms_land.rotation_roll``). That claim is only
worth anything if a capture can be shown to be a turned copy rather than a
different map, and if the four turns are seen to come up - so this reads a
captured ``.scx`` back and asks which turn of the script's own ROT_0
geometry it matches.

Method, and why it is not circular: the reference comes from the script's
**first** ``land_position`` per block, which is the ROT_0 branch, parsed by
``thumbnail.parse_script`` - the same parser that draws the map icon, so this
also demonstrates that the icon stays pinned to orientation 0. That
reference is rotated in *array* space with ``np.rot90``, entirely
independently of the percent-space arithmetic in ``rms_land``. If the two
agreed only by construction, a capture would match every turn equally;
instead one wins by a wide margin, and the margin is reported so the
identification is checkable rather than asserted.

IoU against a disc union is blobby in absolute terms (the engine grows lands
organically), so read the *ranking* and the gap, not the value.

    uv run python automation/rot_orientation.py \\
        --scripts out/rot4_scripts --run out/gen_latency/rot4_v1
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import scx_read, thumbnail  # noqa: E402
from rwmaps.rms_land import iou, rasterize_discs  # noqa: E402

#: ``np.rot90(m, k)`` for k=0..3, labelled by the script's own define. Which
#: k corresponds to which label is *measured* here, not assumed - see the
#: module docstring - so the labels are attached after the fact by the order
#: the turns come out, which is what makes a mismatch visible.
TURNS = (0, 1, 2, 3)


def reference_masks(script: Path) -> tuple[list[np.ndarray], int]:
    """The ROT_0 disc union and its three turns, as ``[y][x]`` bool."""
    parsed = thumbnail.parse_script(script)
    base = rasterize_discs(parsed.discs, parsed.size)
    return [np.rot90(base, k) for k in TURNS], parsed.size


def classify(capture: Path, refs: list[np.ndarray]) -> tuple[int, list[float]]:
    """(best turn, IoU against each turn) for one captured scenario."""
    got = scx_read.read_land_mask(capture)
    scores = [iou(got, r) for r in refs]
    return int(np.argmax(scores)), scores


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scripts", required=True,
                    help="directory of the .rms the pass ran")
    ap.add_argument("--run", required=True,
                    help="a gen_latency run dir with scenarios/ kept")
    ap.add_argument("--json", default=None, help="write the per-sample table here")
    args = ap.parse_args()

    run = Path(args.run)
    scen_dir = run / "scenarios"
    if not scen_dir.is_dir():
        raise SystemExit(f"no kept scenarios in {run} - the pass needs "
                         f"--keep-scenarios")

    records = [json.loads(l) for l in (run / "results.jsonl").open(encoding="utf-8")]
    refs: dict[str, list[np.ndarray]] = {}
    rows = []
    seen: dict[str, Counter] = defaultdict(Counter)

    for rec in records:
        if not rec.get("scenario"):
            continue
        script = Path(args.scripts) / rec["script"]
        if not script.exists():
            continue
        if rec["map"] not in refs:
            refs[rec["map"]], _ = reference_masks(script)
        turn, scores = classify(scen_dir / rec["scenario"], refs[rec["map"]])
        runner_up = sorted(scores, reverse=True)[1]
        rows.append({"map": rec["map"], "round": rec["round"], "turn": turn,
                     "iou": round(scores[turn], 4),
                     "margin": round(scores[turn] - runner_up, 4),
                     "all": [round(s, 4) for s in scores]})
        seen[rec["map"]][turn] += 1
        print(f"{rec['map']:28s} r{rec['round']:<2d} turn={turn} "
              f"iou={scores[turn]:.3f} (next {runner_up:.3f}, "
              f"margin {scores[turn] - runner_up:+.3f})")

    print()
    for m, c in seen.items():
        total = sum(c.values())
        spread = " ".join(f"turn{k}={c.get(k, 0)}" for k in TURNS)
        print(f"{m:28s} n={total:<3d} {spread}   distinct={len(c)}/4")

    if rows:
        margins = [r["margin"] for r in rows]
        print(f"\nweakest identification margin: {min(margins):+.3f} "
              f"(a turn that was not distinguishable would be near 0)")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
