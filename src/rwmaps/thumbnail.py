"""Cheap terrain thumbnails, rendered from a shipped ``.rms`` script itself.

No engine time and no geodata: the script's own ``create_land`` blocks are
parsed back into discs and unioned, which is the same construction
``rms_land.rasterize_discs`` previews before generation. Reading the shipped
script rather than re-running the generator means a thumbnail cannot drift
away from the map that actually ships, and it costs nothing to rebuild.

What it is NOT: a picture of a generated map. Forest, elevation and every
object are engine RNG at generation time, and nothing here guesses at them -
only land/water (plus the shoreline shading the engine paints as beach) and
the deliberate player start positions the script pins down. For real
engine-produced output see ``real_preview``/``RENDER_PIPELINE.md``.

``land_position`` is integer percent, so disc centres quantise to about one
percent of the grid (~2.4 tiles at 240) - that quantisation is what the
engine gets too.

Radii need one inference. A block records ``number_of_tiles``, and
``build_land_generation`` scales those tile budgets down by a constant
factor so the heavily-overlapping discs sum to the real land area rather
than flooding the map, so ``sqrt(tiles/pi)`` is every disc's radius shrunk
by the same constant - which opens cracks between discs that in fact
overlap. ``fit_scale`` recovers that constant by growing the discs until
their union is the land area the script budgets for. The engine reaches the
same place from the other side: it grows each land to exactly its tile
budget, and lands cannot overlap, so they spread into the gaps.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .rms_land import Disc, rasterize_discs

#: The placeholder terrain each player's own starting land is painted with
#: (see ``rms.PLAYER_SPAWN_PLACEHOLDER``); the engine repaints it as ordinary
#: land, so for a thumbnail it is land like any other - it only tells us
#: which blocks are the deliberate starts.
DEEP = (24, 54, 104)
SHALLOW = (42, 96, 158)
BEACH = (203, 180, 126)
LAND = (86, 126, 58)
BG = (18, 18, 18)

#: One distinct colour per player, matching ``real_preview.PLAYER_COLORS`` so
#: a thumbnail and a real render can be read side by side.
PLAYER_COLORS = [
    (230, 60, 60), (60, 140, 230), (240, 200, 40), (60, 200, 120),
    (200, 100, 230), (240, 140, 40), (60, 220, 220), (230, 230, 230),
]

#: The game's own player colours, in player order - blue, red, green, yellow,
#: cyan, purple, grey, orange. Used only for the in-game map icon, which
#: should look like the stock ones rather than like this project's previews.
AOE_PLAYER_COLORS = [
    (42, 95, 232), (224, 48, 48), (38, 212, 38), (255, 232, 0),
    (0, 232, 232), (232, 40, 232), (156, 156, 156), (255, 128, 0),
]

#: The map-selection icon the game looks for beside a script, measured off
#: the stock ``mapicons/rm_arabia.png`` and the two subscribed mods that ship
#: their own (Legacy ES Maps, Zetnus HyperRandom): 420x420 RGBA, the map a
#: full-bleed diamond with its corners on the edge midpoints, edged with a
#: tan border over a thin dark outline.
ICON_PX = 420
ICON_BORDER = (215, 182, 151)
ICON_OUTLINE = (38, 30, 22)

#: Degrees to rotate the north-up grid by, PIL's sense (positive is
#: counter-clockwise). COUNTER-clockwise, measured off the stock real-world
#: maps' own icons - the three that show recognisable geography all agree:
#: rwm_iberia puts Africa (south) at the lower RIGHT and the Balearics
#: (east) at the upper right; rwm_britain puts Scotland (north) at the upper
#: left with Ireland (west) below it; rwm_italy puts the Alps (north) upper
#: left and Sardinia (west) lower left. So north lands at the upper left and
#: the sequence round the diamond is N, E, S, W clockwise from there.
#:
#: This was clockwise until 2026-08-14 and was wrong: 45 degrees the wrong
#: way is a 90 degree error, and every map shipped an icon that did not
#: match the map. Do not "simplify" the sign without re-measuring against
#: those stock icons.
ICON_ROTATION = 45

#: Tiles of water next to the shore drawn as shallows, and tiles of land next
#: to the water drawn as beach - one ring each, as the engine paints beach.
#: A wider beach band looks fine on a chunky landmass but eats the narrow
#: ones whole: at two rings, Japan's coastal chains rendered as all sand.
SHALLOW_TILES = 3
BEACH_TILES = 1

#: Enclosed water pockets up to this many tiles are filled in before drawing.
#: They are interstitial slivers between neighbouring discs (see
#: ``rms_land.cover_mask``'s ``overlap`` note) sharpened by ``land_position``
#: quantisation, not lakes - and unfilled they ring the interior of a solid
#: landmass with beach, which is exactly the "speckled so badly you cannot
#: read the landmass" failure that note is about. It is a small correction
#: once ``fit_scale`` has the discs at their true size: 35-190 tiles a map,
#: under 1% of the land. Water touching the map edge is sea and is never
#: filled, whatever its size, and nothing here narrows a channel - a strait
#: stays a strait.
MAX_POCKET_TILES = 12

_BLOCK = re.compile(r"create_land\s*\{(.*?)\}", re.S)
_SIZE = re.compile(r"projection\s+\S+,\s*(\d+)x\d+\s*tiles")


def _field(body: str, name: str) -> str | None:
    m = re.search(rf"^\s*{name}\s+(.+?)\s*$", body, re.M)
    return m.group(1) if m else None


def fit_scale(discs: list[Disc], size: int, land_tiles: int,
              steps: int = 10, max_scale: float = 3.0) -> float:
    """Factor the recorded radii were shrunk by, recovered from the budget.

    Bisects on the one number the script does state exactly: the tiles of
    land it asks the engine for. The union grows monotonically with the
    factor, so bisection is safe; ``steps`` of 10 pins it to ~0.2%, well
    under a tile at any grid size here.
    """
    if not discs or land_tiles <= 0:
        return 1.0
    area = lambda k: int(rasterize_discs(  # noqa: E731
        [Disc(d.y, d.x, d.radius * k) for d in discs], size).sum())
    if area(1.0) >= land_tiles:
        return 1.0
    lo, hi = 1.0, max_scale
    for _ in range(steps):
        mid = (lo + hi) / 2
        if area(mid) < land_tiles:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass(frozen=True)
class MapScript:
    """The land geometry of one ``.rms``, recovered from the script text."""

    name: str
    size: int
    #: Disc centres exact to ``land_position``'s percent; radii carry the
    #: uniform shrink described in the module docstring, which ``land_mask``
    #: undoes with ``fit_scale``.
    discs: list[Disc]
    #: Player number per entry of ``discs``, ``None`` for the coastline discs.
    #: A player's own land is land like any other on the thumbnail; this only
    #: says which blocks are the deliberate starts.
    owners: list[int | None]
    #: Tiles of land the script budgets for, summed over every block - the
    #: real land area the coastline was cut from.
    land_tiles: int
    path: Path

    @property
    def coastline(self) -> list[Disc]:
        return [d for d, p in zip(self.discs, self.owners) if p is None]

    @property
    def starts(self) -> list[tuple[int, int, int]]:
        """(player number, y, x), lowest player first."""
        return sorted((p, d.y, d.x) for d, p in zip(self.discs, self.owners)
                      if p is not None)

    @cached_property
    def land_mask(self) -> np.ndarray:
        """The land the script builds, at the discs' true size."""
        scale = fit_scale(self.discs, self.size, self.land_tiles)
        grown = [Disc(d.y, d.x, d.radius * scale) for d in self.discs]
        return rasterize_discs(grown, self.size)


def parse_script(path: str | Path) -> MapScript:
    """Recover discs and start positions from a generated ``.rms``."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _SIZE.search(text)
    if not m:
        raise ValueError(f"{path.name}: no 'NxN tiles' in the rwmaps header - "
                         "not a script this renders")
    size = int(m.group(1))

    discs: list[Disc] = []
    owners: list[int | None] = []
    land_tiles = 0
    for body in _BLOCK.findall(text):
        pos, tiles = _field(body, "land_position"), _field(body, "number_of_tiles")
        if pos is None or tiles is None:
            continue
        land_tiles += int(float(tiles))
        px, py = (float(v) for v in pos.split()[:2])
        x = int(round(px / 100.0 * (size - 1)))
        y = int(round(py / 100.0 * (size - 1)))
        # Blocks record a tile budget, not a radius; the generator sized that
        # budget as the disc's area (rms_land._land_block), so invert it.
        radius = math.sqrt(max(1.0, float(tiles)) / math.pi)
        discs.append(Disc(y, x, radius))
        player = _field(body, "assign_to_player")
        owners.append(int(player) if player is not None else None)

    if not discs:
        raise ValueError(f"{path.name}: no create_land blocks")
    return MapScript(path.stem, size, discs, owners, land_tiles, path)


def fill_pockets(mask: np.ndarray, max_tiles: int = MAX_POCKET_TILES) -> np.ndarray:
    """Fill enclosed water pockets up to ``max_tiles`` - see MAX_POCKET_TILES."""
    labels, count = ndimage.label(~mask)
    if not count:
        return mask
    edge = set(labels[0].tolist()) | set(labels[-1].tolist()) \
        | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    sizes = np.bincount(labels.ravel())
    fill = np.zeros(sizes.size, bool)
    fill[1:] = sizes[1:] <= max_tiles
    for label in edge:
        fill[label] = False
    return mask | fill[labels]


def terrain_rgb(mask: np.ndarray) -> np.ndarray:
    """Land/water painted the way a minimap reads: shallows, beach, land."""
    mask = fill_pockets(mask)
    water = ~mask
    # Distance in tiles from every water tile to the nearest land, and from
    # every land tile to the nearest water - the two shoreline bands.
    to_land = ndimage.distance_transform_edt(water)
    to_water = ndimage.distance_transform_edt(mask)

    rgb = np.zeros(mask.shape + (3,), np.uint8)
    rgb[...] = DEEP
    rgb[water & (to_land <= SHALLOW_TILES)] = SHALLOW
    rgb[mask] = LAND
    rgb[mask & (to_water <= BEACH_TILES)] = BEACH
    return rgb


def render(
    script: MapScript,
    px: int = 320,
    isometric: bool = False,
    show_starts: bool = True,
    label: str | None = None,
) -> Image.Image:
    """One square thumbnail of ``script``'s terrain.

    ``isometric`` rotates by ICON_ROTATION, the orientation the game draws
    the grid in; the default leaves it north-up, which is how the real place
    is recognisable.
    """
    size = script.size
    # Draw the marks at whole-tile resolution first, then resample once, so
    # dots stay round and the coastline does not get a staircase edge.
    scale = max(1, math.ceil(px * (2 if isometric else 1) / size))
    img = Image.fromarray(terrain_rgb(script.land_mask))
    img = img.resize((size * scale, size * scale), Image.NEAREST)

    if show_starts and script.starts:
        d = ImageDraw.Draw(img)
        r = max(3, round(size * scale / 90))
        for player, y, x in script.starts:
            cx, cy = x * scale, y * scale
            color = PLAYER_COLORS[(player - 1) % len(PLAYER_COLORS)]
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color,
                      outline=(20, 20, 20), width=max(1, r // 3))

    if isometric:
        img = img.rotate(ICON_ROTATION, expand=True, resample=Image.BICUBIC,
                         fillcolor=DEEP)
    img = img.resize((px, px), Image.LANCZOS)

    if label:
        out = Image.new("RGB", (px, px + 20), BG)
        out.paste(img, (0, 0))
        ImageDraw.Draw(out).text((4, px + 5), label, fill=(235, 235, 235))
        return out
    return img


def render_icon(script: MapScript, px: int = ICON_PX, supersample: int = 2) -> Image.Image:
    """The map-selection icon, in the game's own format - see ICON_PX.

    The grid is a square rotated 45 degrees, so the diamond's diagonal is the
    icon's width and the square drawn before rotation is ``px/sqrt(2)`` on a
    side. Start markers are drawn as axis-aligned squares *before* that
    rotation, which is what makes them come out as diamonds sitting square to
    the finished icon, exactly like the stock ones.

    The rotation is COUNTER-clockwise - see ICON_ROTATION.
    """
    side = round(px / math.sqrt(2)) * supersample
    img = Image.fromarray(terrain_rgb(script.land_mask)).convert("RGBA")
    img = img.resize((side, side), Image.NEAREST)

    if script.starts:
        d = ImageDraw.Draw(img)
        # 34px across on a 420 icon, as the stock icons draw them; a square
        # of that diagonal before the rotation.
        half = max(2, round(34 / math.sqrt(2) / 2 * side / (px / math.sqrt(2))))
        for player, y, x in script.starts:
            cx = x * side / script.size
            cy = y * side / script.size
            color = AOE_PLAYER_COLORS[(player - 1) % len(AOE_PLAYER_COLORS)]
            d.rectangle([cx - half, cy - half, cx + half, cy + half],
                        fill=color, outline=(0, 0, 0), width=max(2, half // 5))

    img = img.rotate(ICON_ROTATION, expand=True, resample=Image.BILINEAR,
                     fillcolor=(0, 0, 0, 0))
    span = img.width - 1
    mid = span / 2
    diamond = [(mid, 0), (span, mid), (mid, span), (0, mid)]
    frame = ImageDraw.Draw(img)
    frame.polygon(diamond, outline=ICON_OUTLINE, width=round(9 * supersample))
    frame.polygon(diamond, outline=ICON_BORDER, width=round(6 * supersample))
    return img.resize((px, px), Image.LANCZOS)


def save_icon(script: MapScript, path: str | Path, px: int = ICON_PX) -> Path:
    """Write the icon the game shows for ``script`` on the map-selection screen."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    render_icon(script, px=px).save(path)
    return path


def save_thumbnail(script: MapScript, path: str | Path, **kwargs) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    render(script, **kwargs).save(path)
    return path
