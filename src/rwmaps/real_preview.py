"""Render real, engine-produced output: the true coastline the engine grew
plus each player's actual Town Centre placement, both read back from a saved
``.aoe2scenario`` via ``scx_read`` - no Python approximation of either.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SEA = (32, 74, 150)
LAND = (78, 138, 62)
BG = (18, 18, 18)
INK = (255, 220, 90)
TC = (255, 70, 70)
UNCLAIMED = (110, 110, 110)

#: One distinct colour per player (1-8), used to show which TC a resource
#: was assigned to.
PLAYER_COLORS = [
    (230, 60, 60), (60, 140, 230), (240, 200, 40), (60, 200, 120),
    (200, 100, 230), (240, 140, 40), (60, 220, 220), (230, 230, 230),
]

#: Short label per resource kind, drawn next to each dot.
RESOURCE_LABELS = {
    "gold": "G", "stone": "S", "forage": "F",
    "sheep": "Sh", "deer": "D", "boar": "B",
}


def save_real_render(
    mask: np.ndarray,
    town_centers: list[tuple[int, float, float]],
    path: str | Path,
    title: str = "",
    px: int = 700,
) -> Path:
    """North-up coastline outline with every player's real TC tile marked."""
    size = mask.shape[0]
    scale = max(1, px // size)

    rgb = np.zeros(mask.shape + (3,), np.uint8)
    rgb[...] = SEA
    rgb[mask] = LAND

    img = Image.fromarray(rgb).resize((size * scale, size * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    for player, x, y in town_centers:
        cx, cy, r = x * scale, y * scale, 6 * scale
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=TC, width=3)
        d.text((cx + r + 2, cy - r - 2), str(player), fill=(255, 255, 255))

    out = Image.new("RGB", (img.width, img.height + 26), BG)
    out.paste(img, (0, 26))
    ImageDraw.Draw(out).text((4, 6), title, fill=INK)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    return path


def save_resource_map(
    mask: np.ndarray,
    town_centers: list[tuple[int, float, float]],
    resources: list[tuple[str, float, float]],
    ownership: dict[int, dict[str, int]],
    unclaimed: dict[str, int],
    path: str | Path,
    title: str = "",
    px: int = 900,
) -> Path:
    """Coastline + TCs + every land resource, dot-coloured by which TC it
    was actually assigned to (``analysis.resource_ownership``) - grey if it's
    unreachable by any TC within the walking-distance cap."""
    size = mask.shape[0]
    scale = max(1, px // size)
    player_color = {p: PLAYER_COLORS[(p - 1) % len(PLAYER_COLORS)] for p, _, _ in town_centers}

    # Recompute per-resource-instance ownership (the aggregate `ownership`
    # dict only has counts) by redoing the same nearest-reachable-TC lookup,
    # so dots and the counts they came from can never drift out of sync.
    from .analysis import land_path_distance
    paths = {p: land_path_distance(mask, (int(y), int(x))) for p, x, y in town_centers}

    rgb = np.zeros(mask.shape + (3,), np.uint8)
    rgb[...] = SEA
    rgb[mask] = LAND
    img = Image.fromarray(rgb).resize((size * scale, size * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)

    max_distance = 30.0
    for kind, x, y in resources:
        xi, yi = int(x), int(y)
        best_p, best_d = None, float("inf")
        for p, dist in paths.items():
            dd = dist[yi, xi]
            if dd < best_d:
                best_d, best_p = dd, p
        color = player_color[best_p] if (best_p is not None and best_d <= max_distance) else UNCLAIMED
        cx, cy, r = x * scale, y * scale, 3 * scale
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=(0, 0, 0))
        d.text((cx + r + 1, cy - r - 1), RESOURCE_LABELS.get(kind, "?"), fill=(255, 255, 255))

    for player, x, y in town_centers:
        cx, cy, r = x * scale, y * scale, 7 * scale
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 0, 0), width=5)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=player_color[player], width=3)
        d.text((cx + r + 2, cy - r - 2), str(player), fill=(255, 255, 255))

    legend_h = 26
    out = Image.new("RGB", (img.width, img.height + 26 + legend_h), BG)
    out.paste(img, (0, 26))
    dl = ImageDraw.Draw(out)
    dl.text((4, 6), title, fill=INK)
    lx = 4
    for player in sorted(player_color):
        dl.ellipse([lx, img.height + 30, lx + 12, img.height + 42], fill=player_color[player])
        dl.text((lx + 16, img.height + 29), f"P{player}", fill=(230, 230, 230))
        lx += 55
    dl.ellipse([lx, img.height + 30, lx + 12, img.height + 42], fill=UNCLAIMED)
    dl.text((lx + 16, img.height + 29), "unclaimed", fill=(230, 230, 230))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    return path
