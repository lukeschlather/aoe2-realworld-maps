"""Turn one captured .aoe2scenario into a JSON-serializable playability
summary plus a preview image, so this runs ONCE right after capture (where
it belongs) instead of being redone by a report builder later.

All of it is computed from the engine's *actual* placement (real TC tiles,
real resource positions via scx_read), not intended pre-generation
positions - this is the still-open verification README's "What's missing:
town centre placement" item asked for: does the engine's own placement
land somewhere fair, not just the analyzer's chosen tile.

Deliberately does NOT compute a pass/fail "verdict". An earlier version of
this file folded `analysis.evaluate()`'s land-path connectivity check (can
every player walk to every other player?) into an "unfair" label - that's
wrong for a real-world archipelago-style map, where being on a separate
landmass from another player is completely normal and says nothing about
whether *that player individually* has enough resources to play. The only
thing reported as an unambiguous problem is a player having literally zero
of some resource kind - everything else (connectivity, land-within-radius
spread, TC separation) is surfaced as a plain fact for a human to judge,
not pre-judged here.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from rwmaps import scx_read  # noqa: E402
from rwmaps.analysis import components, land_path_distance, resource_ownership  # noqa: E402

SEA = (28, 61, 92)
LAND = (94, 122, 84)
UNCLAIMED_DOT = (150, 150, 150)

#: one distinct colour per player (1-8), also used for TC rings so a
#: resource dot's colour visibly matches its owner's ring.
PLAYER_COLORS = [
    (230, 80, 70), (90, 150, 230), (235, 190, 60), (80, 200, 130),
    (200, 110, 220), (230, 150, 60), (70, 210, 210), (235, 235, 235),
]

RESOURCE_KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]
RESOURCE_LABELS = {"gold": "G", "stone": "S", "forage": "F", "sheep": "Sh", "deer": "D", "boar": "B"}


def _font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _render(
    mask: np.ndarray,
    tcs: list[tuple[int, float, float]],
    resources: list[tuple[str, float, float]],
    px: int = 720,
) -> str:
    """Real coastline + every land resource dotted by who can actually walk
    to it first + every player's real TC ring - so start placement AND
    resource coverage are both visible in the one image, not split across
    a table a reader has to cross-reference by hand.
    """
    size = mask.shape[0]
    scale = max(2, px // size)
    rgb = np.zeros(mask.shape + (3,), np.uint8)
    rgb[...] = SEA
    rgb[mask] = LAND
    img = Image.fromarray(rgb).resize((size * scale, size * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    f = _font(max(9, int(2.2 * scale)))

    player_color = {p: PLAYER_COLORS[(p - 1) % len(PLAYER_COLORS)] for p, _, _ in tcs}
    paths = {p: land_path_distance(mask, (int(y), int(x))) for p, x, y in tcs}
    max_distance = 30.0

    for kind, x, y in resources:
        xi, yi = int(x), int(y)
        best_p, best_d = None, float("inf")
        for p, dist in paths.items():
            dd = dist[yi, xi]
            if dd < best_d:
                best_d, best_p = dd, p
        color = player_color[best_p] if (best_p is not None and best_d <= max_distance) else UNCLAIMED_DOT
        cx, cy, r = x * scale, y * scale, max(2, int(1.4 * scale))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=(0, 0, 0))

    for player, x, y in tcs:
        cx, cy, r = x * scale, y * scale, max(4, int(3.2 * scale))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 0, 0), width=max(2, scale // 2 + 2))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=player_color[player], width=max(1, scale // 2))
        d.text((cx + r + 2, cy - r - 2), str(player), fill="white", font=f,
               stroke_width=2, stroke_fill=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def analyze_capture(path: str | Path, size: int) -> dict:
    """Read one .aoe2scenario and return a plain-dict playability summary."""
    mask = scx_read.read_land_mask(path)
    tcs = scx_read.read_town_centers(path)
    resources = scx_read.read_resources(path)

    starts_yx = [(int(y), int(x)) for _, x, y in sorted(tcs)]
    n = len(starts_yx)

    # Neutral facts about placement geometry - no pass/fail judgment here.
    min_separation = None
    if n >= 2:
        seps = [float(np.hypot(a[0] - b[0], a[1] - b[1]))
                for i, a in enumerate(starts_yx) for b in starts_yx[i + 1:]]
        min_separation = round(min(seps), 1)

    paths = [land_path_distance(mask, s) for s in starts_yx]
    reachable_pairs = 0
    total_pairs = 0
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            total_pairs += 1
            if np.isfinite(paths[i][starts_yx[j]]):
                reachable_pairs += 1

    labels, _ = components(mask)
    n_landmasses_with_a_player = len({int(labels[y, x]) for y, x in starts_yx}) if starts_yx else 0

    per_player, unclaimed = resource_ownership(mask, tcs, resources)
    zero_kinds_by_player = {
        p: [k for k in RESOURCE_KINDS if counts.get(k, 0) == 0]
        for p, counts in per_player.items()
    }
    any_zero = any(zero_kinds_by_player.values())

    return {
        "n_tcs": n,
        "land_pct": round(100 * float(mask.mean()), 1),
        "placement": {
            "min_tc_separation": min_separation,
            "n_landmasses_with_a_player": n_landmasses_with_a_player,
            # fraction of player-pairs that can walk to each other at all -
            # a fact about geography (separate islands, etc.), NOT a
            # fairness verdict. 1.0 means every player can reach every
            # other player by land.
            "pairwise_land_reachable_fraction":
                round(reachable_pairs / total_pairs, 2) if total_pairs else None,
        },
        # NOT the current supply model. Nearest-TC ownership, straight-line
        # distances, ties broken by player index - superseded by
        # rwmaps.fairness (exclusive/contested, walked distances). Kept, and
        # named for what it is, so pre-2026-08 runs stay comparable and
        # nobody reports from it by reaching for the obvious key.
        "legacy_resources_nearest_tc": {
            "per_player": {str(p): c for p, c in per_player.items()},
            "unclaimed": unclaimed,
            # the one thing treated as an unambiguous problem: a player
            # with literally zero of some resource kind has nothing to
            # judge - everything else here is a fact, not a verdict.
            "any_player_zero_of_a_kind": any_zero,
            "zero_kinds_by_player": {str(p): ks for p, ks in zero_kinds_by_player.items() if ks},
        },
        "preview_png_b64": _render(mask, tcs, resources),
    }
