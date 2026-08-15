"""Render what a generated map actually looks like.

The important panel is the right-hand one: the union of the ``create_land``
discs the script really emits, in the orientation the game draws it. A preview
of the source projection alone is misleading, because the blobbiness and the 45
degree isometric rotation are exactly what make a coastline hard to recognise
in play.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import thumbnail

SEA = (32, 74, 150)
LAND = (78, 138, 62)
SPILL = (150, 150, 70)   # script builds land where the real map has sea
MISS = (122, 62, 58)     # real land the script fails to build
BG = (18, 18, 18)
INK = (255, 220, 90)


def _mask_rgb(mask: np.ndarray) -> np.ndarray:
    rgb = np.zeros(mask.shape + (3,), np.uint8)
    rgb[...] = SEA
    rgb[mask] = LAND
    return rgb


def _diff_rgb(approx: np.ndarray, truth: np.ndarray) -> np.ndarray:
    rgb = np.zeros(approx.shape + (3,), np.uint8)
    rgb[...] = SEA
    rgb[approx] = LAND
    rgb[approx & ~truth] = SPILL
    rgb[~approx & truth] = MISS
    return rgb


def _panel(
    rgb: np.ndarray,
    starts: list[tuple[int, int]] | None,
    px: int,
    isometric: bool,
    label: str,
) -> Image.Image:
    size = rgb.shape[0]
    scale = max(1, px // size)
    img = Image.fromarray(rgb).resize((size * scale, size * scale), Image.NEAREST)
    if starts:
        d = ImageDraw.Draw(img)
        for i, (y, x) in enumerate(starts, 1):
            cx, cy, r = x * scale, y * scale, 5 * scale
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 70, 70), width=3)
            d.text((cx + r + 2, cy - r - 2), str(i), fill=(255, 255, 255))
    if isometric:
        # The game draws the square grid rotated 45 degrees COUNTER-clockwise
        # - north ends up at the upper left. This said clockwise, and drew it
        # that way, until 2026-08-14; the stock real-world maps' own icons
        # settle it (see thumbnail.ICON_ROTATION for the measurement).
        img = img.rotate(thumbnail.ICON_ROTATION, expand=True,
                         resample=Image.NEAREST, fillcolor=BG)
    img = img.resize((px, px), Image.LANCZOS)
    out = Image.new("RGB", (px, px + 22), BG)
    out.paste(img, (0, 22))
    ImageDraw.Draw(out).text((4, 5), label, fill=INK)
    return out


def save_preview(
    truth: np.ndarray,
    approx: np.ndarray,
    starts: list[tuple[int, int]] | None,
    path: str | Path,
    title: str = "",
    px: int = 520,
) -> Path:
    """Three panels: the real coastline, what we build, and how it looks in game."""
    panels = [
        _panel(_mask_rgb(truth), starts, px, False, "real coastline (north up)"),
        _panel(_diff_rgb(approx, truth), starts, px, False,
               "generated (olive=spill, red=missed)"),
        _panel(_mask_rgb(approx), starts, px, True, "as the game draws it"),
    ]
    gap = 10
    sheet = Image.new(
        "RGB",
        (sum(p.width for p in panels) + gap * (len(panels) + 1),
         panels[0].height + 34 + gap),
        BG,
    )
    x = gap
    for p in panels:
        sheet.paste(p, (x, 30))
        x += p.width + gap
    ImageDraw.Draw(sheet).text((gap, 8), title, fill=(255, 255, 255))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path
