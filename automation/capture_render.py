"""The one way this project draws a captured scenario for a report.

Every report that shows a sample used to embed ``preview_png_b64``, the
render ``sample_analysis`` stores at capture time: coastline, resource dots
coloured by who can walk to each first, TC rings. That picture is missing
the two things a reader asks about most - **forest and tree objects**, the
resource a start lives or dies by, and **fish**, which is the whole economy
on a coastline map. It was retired as a report visual on 2026-08-20.

What ships instead is ``render_styles.utility``: the same palette and the
same dots, plus forest, trees, fords, and water food as white dots. It needs
the **scenario**, not the stored render, because the stored render is a
picture and the fish were never in it. So the rule is "whenever there is a
scenario on disk, draw it this way", and the stored preview is a fallback
for a row whose capture has been cleaned up.

Two shapes, because reports need both:

``data_uri``
    A finished diamond, in the in-game orientation, ready for an ``<img>``.
``grid_image``
    The same picture still square to the tile grid, for a caller that
    follows tiles through the rotation itself - the strait crops in
    ``build_feature_report`` do, and they need the tile scale to survive.

A parse is ~4.5s and the ownership walk another second or two, so renders
are cached on disk under ``out/utility_cache`` and keyed by the scenario
path, the size and the orientation. The render is a pure function of the
capture, so the cache is only invalid when the file is newer than it.
"""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from PIL import Image  # noqa: E402

CACHE = REPO / "out" / "utility_cache"

#: Walked tiles from a Town Centre, past which a resource is nobody's. Same
#: number as ``fairness.OWNERSHIP_RADIUS`` - dots are coloured on the same
#: rule the tables are counted on, or the picture and the numbers disagree.
OWNERSHIP_RADIUS = 30.0


def _resource_owner(scene) -> dict:
    """``{(kind, x, y): player or None}`` by walked distance.

    Fish are deliberately absent: this is a walk, and no walk reaches a
    fish. ``render_styles.draw_water_food`` draws them unowned.
    """
    import sample_analysis

    if not scene.tcs:
        return {}
    walks = {pl: sample_analysis.land_path_distance(scene.land, (int(y), int(x)))
             for pl, x, y in scene.tcs}
    owner = {}
    for kind, x, y in scene.resources:
        best_p, best_d = None, float("inf")
        for pl, dist in walks.items():
            dd = dist[int(y), int(x)]
            if dd < best_d:
                best_d, best_p = dd, pl
        owner[(kind, x, y)] = best_p if best_d <= OWNERSHIP_RADIUS else None
    return owner


def _render(path: Path, px: int, turned: bool) -> Path:
    from rwmaps import render_styles

    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(
        f"{path.resolve()}|{px}|{'turn' if turned else 'grid'}".encode()
    ).hexdigest()[:16]
    cached = CACHE / f"utility-{key}.png"
    if not (cached.is_file() and cached.stat().st_mtime >= path.stat().st_mtime):
        scene = render_styles.scene_from_scenario(path)
        render_styles.utility(scene, px=px, resource_owner=_resource_owner(scene),
                              turned=turned).save(cached)
    return cached


def _resolve(scenario) -> Path | None:
    if not scenario:
        return None
    p = Path(scenario)
    p = p if p.is_absolute() else REPO / p
    return p if p.is_file() else None


def grid_image(scenario, px: int = 720) -> Image.Image | None:
    """The utility render, still square to the tile grid. None if no capture.

    ``px`` sets the internal scale rather than the finished size: the image
    comes back at ``size * scale`` and the caller resizes after its own
    transform, exactly as the stored preview was used.
    """
    path = _resolve(scenario)
    if path is None:
        return None
    return Image.open(_render(path, px, turned=False)).convert("RGB")


def data_uri(scenario, px: int = 720) -> str:
    """The utility render as a finished, in-game-oriented data URI.

    Returns "" when the scenario is not on disk, so a caller can fall back
    to whatever the row carries.
    """
    path = _resolve(scenario)
    if path is None:
        return ""
    return ("data:image/png;base64,"
            + base64.b64encode(_render(path, px, turned=True).read_bytes()).decode())
