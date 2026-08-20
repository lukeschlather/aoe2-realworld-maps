"""Alternate treatments for the diamond render, from a captured scenario.

Two jobs that pull in opposite directions, so they are separate treatments
rather than one compromise:

**Utility.** Can this map be played? That wants everything visible at once -
where the woods are, where the shore is, which resources belong to whom -
and does not care whether it is pretty. ``utility()`` is the existing
analysis render plus the one thing it never drew: forest. Wood is the
resource a start lives or dies by and it was the only one with no mark on
the picture.

**Aesthetics.** The map-selection screen. That wants the map to read as a
place, in the game's own visual language, and to survive being 420 px wide.
Four treatments, all showing Town Centres and trees and nothing else,
because a resource dot is information the player does not have yet.

Everything here reads a **captured** ``.aoe2scenario`` - the terrain the
engine actually produced, objects included - not the script that asked for
it. ``thumbnail.py`` renders the other way round, from the ``.rms``, and
says why: a thumbnail built from the script cannot drift from the map that
ships. The cost of that is it can only draw land and water, because forest,
elevation and every object are engine RNG. These treatments pay the
opposite cost: they need a capture, and the capture is one sample of many.

Terrain ids come from the game's own
``resources/_common/drs/gamedata_x2/includes/constants.inc`` (the
``TERRAIN_CONSTANTS`` block), read rather than guessed - see ``CLASS_IDS``.
Anything unlisted falls back to plain land and is counted, so a biome this
table has not met shows up as a number instead of as a silent mis-colour.

Every treatment ends turned by ``thumbnail.ICON_ROTATION``: the diamond is
the only orientation anybody sees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

from . import terrain as T
from . import thumbnail

#: Terrain id -> class, by name, from ``includes/constants.inc``. The class
#: is what a picture needs: how deep the water is, whether the ground is
#: green or dry, whether it is a wood. Ids not listed are drawn as ``grass``
#: and reported by ``Scene.unknown_ids``.
CLASS_IDS: dict[str, tuple[int, ...]] = {
    # water, by depth band - the engine paints these three deliberately and
    # they are what gives a coast its gradient.
    "shore": (1, 15, 57, 58, 95, 96, 114),        # WATER_SHALLOW + presets
    "mid": (23,),                                  # WATER_MEDIUM
    "deep": (22, 116),                             # WATER_DEEP
    # fordable: water to a boat, ground to a villager. Its own class because
    # it is the one terrain that is both.
    "ford": (4, 26, 28, 54, 59, 111, 115),
    "beach": (2, 37, 52, 79, 80, 81, 82, 107, 108, 109),
    "forest": (10, 13, 17, 18, 19, 20, 21, 48, 49, 50, 55, 56, 88, 89, 90,
               91, 92, 99, 104, 105, 106, 110, 112, 113, 128),
    "brush": (5, 39, 62, 71, 72, 75, 77),          # UNDERBRUSH_*, ROAD_FUNGUS
    "grass": (0, 9, 12, 16, 60, 83, 100, 122, 123),
    "dry": (41, 42, 101, 76, 44),                  # SAVANNAH, BOGLAND, mud
    "desert": (14, 13, 46, 102, 70, 45),           # DESERT, GRAVEL, cracked
    "dirt": (3, 6, 11, 24, 25, 43, 78, 103, 40),   # DIRT_*, ROAD_*, ROCK
    "snow": (32, 33, 34, 35, 36, 38, 73, 74, 124, 125, 126, 127),
    "farm": (7, 8, 29, 30, 31, 117, 118, 119, 120, 121),
    # PLACEHOLDER_TERRAIN_A..F. Ours, not the game's: rms.py paints player
    # lands with a placeholder and repaints them afterwards, and a tile that
    # survives that cleanup is a BLACK tile in the finished game (see the
    # repeated-cleanup fix, 2026-08-15). 40 such tiles survive across the 18
    # captures drawn here, so they are worth a colour of their own in the
    # utility view rather than being quietly drawn as grass.
    #
    # Only the six unambiguous ids. PLACEHOLDER_TERRAIN_G and H are 33 and
    # 34, which are also SNOW_DIRT and SNOW_GRASS - real terrain a snow
    # biome uses - so they stay classed as snow.
    "placeholder": (84, 85, 86, 87, 97, 98),
}

WATER_CLASSES = ("shore", "mid", "deep")
LAND_CLASSES = ("beach", "forest", "brush", "grass", "dry", "desert", "dirt",
                "snow", "farm", "placeholder")

#: Dark forest green, as asked for: the utility view's woods and trees. Two
#: tones so a wood reads as a mass and a straggler reads as a dot on it.
FOREST_GREEN = (24, 64, 34)
TREE_GREEN = (14, 44, 22)

#: Leftover PLACEHOLDER terrain, which ships as a black tile. Magenta
#: because nothing else on the picture is, and it is a defect rather than a
#: feature.
PLACEHOLDER_DEFECT = (226, 40, 200)

#: Off-map, behind the diamond. Matches the report background rather than
#: black, so a panel does not look like a hole in the page.
OFFMAP = (17, 21, 26)


def _class_grid(grid: np.ndarray) -> tuple[dict[str, np.ndarray], dict[int, int]]:
    masks = {}
    claimed = np.zeros(grid.shape, bool)
    for name, ids in CLASS_IDS.items():
        m = np.isin(grid, list(ids))
        masks[name] = m
        claimed |= m
    unknown: dict[int, int] = {}
    if (~claimed).any():
        vals, counts = np.unique(grid[~claimed], return_counts=True)
        unknown = {int(v): int(c) for v, c in zip(vals, counts)}
        masks["grass"] = masks["grass"] | ~claimed
    return masks, unknown


@dataclass
class Scene:
    """One captured map, in the terms a picture needs."""
    name: str
    size: int
    grid: np.ndarray
    cls: dict[str, np.ndarray]
    trees: list[tuple[float, float]]
    tcs: list[tuple[int, float, float]]
    resources: list[tuple[str, float, float]]
    source: str = ""
    unknown_ids: dict[int, int] = field(default_factory=dict)

    @property
    def land(self) -> np.ndarray:
        """Coastline land - shallows read as sea, as everywhere else here."""
        return ~np.isin(self.grid, list(T.WATER_IDS))

    @property
    def water(self) -> np.ndarray:
        return ~self.land

    @property
    def forest(self) -> np.ndarray:
        return self.cls["forest"]


def scene_from_scenario(path: str | Path, name: str = "") -> Scene:
    """One parse, everything a treatment needs.

    ``scx_read.read_capture`` exists exactly for this: the per-question
    readers each re-parse the file, and a parse is ~4.5s, so seven
    treatments off four readers would be half a minute per map spent
    reading the same bytes.
    """
    from . import scx_read

    path = Path(path)
    cap = scx_read.read_capture(path)
    cls, unknown = _class_grid(cap.terrain)
    return Scene(
        name=name or path.stem, size=cap.terrain.shape[0], grid=cap.terrain,
        cls=cls, trees=[(x, y) for _k, x, y in cap.trees],
        tcs=cap.town_centers, resources=cap.resources,
        source=str(path), unknown_ids=unknown,
    )


# --------------------------------------------------------------------------
# shared drawing machinery
# --------------------------------------------------------------------------

def _rng(scene: Scene) -> np.random.Generator:
    """Deterministic per map: a texture that reshuffles every rebuild makes
    two renders of the same map impossible to compare."""
    return np.random.default_rng(abs(hash(scene.name)) % (2 ** 32))


def paint(base: np.ndarray, mask: np.ndarray, color) -> None:
    base[mask] = color


def edge(mask: np.ndarray) -> np.ndarray:
    """The one-tile ring just outside ``mask``."""
    return ndimage.binary_dilation(mask, np.ones((3, 3), bool)) & ~mask


def coast_height(land: np.ndarray, smooth: float = 3.0) -> np.ndarray:
    """A plausible elevation field: far from the sea is high.

    Not the map's real elevation - the engine's elevation is not in the
    terrain grid at all. It is a *shading* field, and it is honest about
    what it encodes: distance inland. Every real coastline reads that way
    to the eye anyway, which is why hillshading it looks like terrain
    rather than like noise.
    """
    h = ndimage.distance_transform_edt(land).astype(np.float32)
    if smooth:
        h = ndimage.gaussian_filter(h, smooth)
    return h


def hillshade(height: np.ndarray, azimuth_deg: float = 315.0,
              altitude_deg: float = 45.0, z: float = 2.2) -> np.ndarray:
    """Lambertian shade of ``height`` in [0, 1], the usual relief formula."""
    dy, dx = np.gradient(height * z)
    slope = np.pi / 2 - np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az = math.radians(360.0 - azimuth_deg + 90.0)
    alt = math.radians(altitude_deg)
    shade = (math.sin(alt) * np.sin(slope)
             + math.cos(alt) * np.cos(slope) * np.cos(az - aspect))
    return np.clip((shade + 1) / 2, 0, 1)


def texture(base: np.ndarray, mask: np.ndarray, rng, amount: int = 7) -> None:
    """Per-tile grain, so a flat fill stops looking like a fill.

    Applied per *tile*, not per pixel: the render is supersampled and then
    resampled down, and pixel-level noise would simply blur to grey.
    """
    n = rng.integers(-amount, amount + 1, size=base.shape[:2] + (1,))
    base[mask] = np.clip(base[mask].astype(np.int16) + n[mask], 0, 255)


def upscale(base: np.ndarray, scale: int) -> Image.Image:
    return Image.fromarray(base).resize(
        (base.shape[1] * scale, base.shape[0] * scale), Image.NEAREST)


def turn(img: Image.Image, px: int, fill=OFFMAP,
         resample=Image.LANCZOS) -> Image.Image:
    """The diamond: turned by ICON_ROTATION, then resampled once."""
    if thumbnail.ICON_ROTATION % 360:
        img = img.rotate(thumbnail.ICON_ROTATION, expand=True,
                         resample=Image.BICUBIC, fillcolor=fill)
    return img.resize((px, px), resample)


def tc_diamonds(img: Image.Image, scene: Scene, scale: float, *,
                colors=None, half_px: int | None = None,
                outline=(12, 12, 12)) -> None:
    """Player markers as axis-aligned squares, pre-rotation.

    Squares before the turn are diamonds after it, sitting square to the
    finished picture - which is what the stock map icons do, and the reason
    ``thumbnail.render_icon`` draws them the same way.
    """
    colors = colors or thumbnail.AOE_PLAYER_COLORS
    d = ImageDraw.Draw(img)
    half = half_px if half_px is not None else max(3, round(img.width / 60))
    for player, x, y in scene.tcs:
        cx, cy = x * scale, y * scale
        c = colors[(player - 1) % len(colors)]
        d.rectangle([cx - half, cy - half, cx + half, cy + half],
                    fill=c, outline=outline, width=max(1, half // 4))


def draw_trees(img: Image.Image, scene: Scene, scale: float, color,
               radius: float = 1.0) -> None:
    """Tree *objects* - the stragglers and copses that are not forest
    terrain. Small, because one tree is one tile."""
    d = ImageDraw.Draw(img)
    r = max(1.0, radius * scale / 2)
    for x, y in scene.trees:
        cx, cy = x * scale, y * scale
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def diamond_frame(img: Image.Image, border, outline, width: int = 6) -> Image.Image:
    """The tan-over-dark edge the stock map icons carry."""
    out = img.convert("RGB")
    span = out.width - 1
    mid = span / 2
    poly = [(mid, 0), (span, mid), (mid, span), (0, mid)]
    d = ImageDraw.Draw(out)
    d.polygon(poly, outline=outline, width=width + 3)
    d.polygon(poly, outline=border, width=width)
    return out


def vignette(img: Image.Image, strength: float = 0.38) -> Image.Image:
    """Corners dimmed. Cheap, and it is most of why a flat fill starts to
    look lit rather than printed."""
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    k = np.clip(1 - strength * np.clip(r - 0.35, 0, None) ** 1.6, 0, 1)
    a = np.asarray(img.convert("RGB")).astype(np.float32) * k[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def sea_depth(land: np.ndarray, reach: float = 14.0) -> np.ndarray:
    """0 at the shore, 1 in the open sea - a smooth depth ramp.

    The engine's own water bands (WATER_SHALLOW / MEDIUM / DEEP) are in the
    capture and were used first, but they are painted in rectangular-ish
    lumps, so a stylised render came out with a flat pale slab in the middle
    of the ocean. Distance to the nearest land is what a coast actually looks
    like, and it is derived from the same geometry the coastline is, so it is
    no less true to the capture - just smooth. The bands are still drawn
    where they matter, as ``ford``.
    """
    d = ndimage.distance_transform_edt(~land).astype(np.float32)
    return np.clip(ndimage.gaussian_filter(d, 1.2) / reach, 0, 1)


def shade_water(base: np.ndarray, land: np.ndarray, shallow, deep,
                reach: float = 14.0) -> None:
    """Paint the sea as a shore-to-deep gradient, in place."""
    t = sea_depth(land, reach)[..., None]
    grad = (np.array(shallow, np.float32) * (1 - t)
            + np.array(deep, np.float32) * t)
    sea = ~land
    base[sea] = np.clip(grad, 0, 255).astype(np.uint8)[sea]


def coast_beach(scene: "Scene") -> np.ndarray:
    """Beach the engine painted, kept to the tiles actually on a shore.

    Unfiltered, beach is a bright ring round every islet and inlet, and once
    a treatment lights the land it stops reading as sand and starts reading
    as a glow outline drawn round the coast.
    """
    return scene.cls["beach"] & ndimage.binary_dilation(
        scene.water, np.ones((3, 3), bool))


# --------------------------------------------------------------------------
# utility treatment
# --------------------------------------------------------------------------

#: The analysis render's own palette, so the utility view reads as the same
#: picture with more in it rather than as a different picture.
U_SEA = (28, 46, 74)
U_MID = (24, 40, 66)
U_DEEP = (19, 33, 56)
U_FORD = (52, 96, 92)
U_LAND = (94, 122, 84)
U_BEACH = (150, 146, 108)
U_PLAYER = [(230, 80, 70), (90, 150, 230), (235, 190, 60), (80, 200, 130),
            (200, 110, 220), (230, 150, 60), (70, 210, 210), (235, 235, 235)]
U_UNCLAIMED = (150, 150, 150)


def final_scale(px: int, size: int, scale: int) -> float:
    """Internal pixels per one final pixel.

    A mark drawn before the turn is resampled twice - the rotation grows the
    canvas to ``size*scale*sqrt(2)``, then everything is resized to ``px`` -
    so a radius chosen in internal pixels is not the radius anybody sees. The
    resource dots were sized that way and came out at about 1.5 px in the
    finished picture, which is why they stopped being legible against the
    forest. Sizes go through here now: they are chosen in FINAL pixels and
    converted back.
    """
    return size * scale * math.sqrt(2) / px


def utility(scene: Scene, px: int = 720, scale: int = 3,
            resource_owner=None) -> Image.Image:
    """Everything at once: coast, depth, fords, **forest**, trees, resources
    dotted by owner, TC rings.

    The default is 720 px, not 360: this is the picture a fairness report is
    read from, and per-player resource dots have to survive being looked at
    closely. A report displays it smaller and lets it be zoomed.

    ``resource_owner`` is ``{(kind, x, y): player or None}`` if the caller
    has already walked the distances (``sample_analysis`` does). Without it
    the dots are drawn neutral rather than guessed at.
    """
    base = np.zeros(scene.grid.shape + (3,), np.uint8)
    base[...] = U_SEA
    paint(base, scene.cls["mid"], U_MID)
    paint(base, scene.cls["deep"], U_DEEP)
    paint(base, scene.land, U_LAND)
    paint(base, scene.cls["beach"], U_BEACH)
    paint(base, scene.cls["forest"], FOREST_GREEN)
    # A survivor of the placeholder cleanup is a black tile in game, so it
    # gets the one colour on this picture that means "look at this".
    paint(base, scene.cls["placeholder"], PLACEHOLDER_DEFECT)
    # Fords last: they are the one terrain that must not be read as either
    # land or sea, and they sit between the two.
    paint(base, scene.cls["ford"], U_FORD)

    img = upscale(base, scale)
    d = ImageDraw.Draw(img)
    k = final_scale(px, scene.size, scale)
    draw_trees(img, scene, scale, TREE_GREEN, radius=1.6 * k)

    # The old analysis render's dot and ring sizes, restored in the units
    # they are actually read in.
    dot = max(2.0, 2.6 * k)
    for kind, x, y in scene.resources:
        who = (resource_owner or {}).get((kind, x, y), "none")
        color = (U_PLAYER[(who - 1) % len(U_PLAYER)]
                 if isinstance(who, int) else U_UNCLAIMED)
        d.ellipse([x * scale - dot, y * scale - dot,
                   x * scale + dot, y * scale + dot],
                  fill=color, outline=(10, 10, 10))
    ring = max(4.0, 6.0 * k)
    for player, x, y in scene.tcs:
        cx, cy = x * scale, y * scale
        d.ellipse([cx - ring, cy - ring, cx + ring, cy + ring],
                  outline=(0, 0, 0), width=max(2, round(1.6 * k)))
        d.ellipse([cx - ring, cy - ring, cx + ring, cy + ring],
                  outline=U_PLAYER[(player - 1) % len(U_PLAYER)],
                  width=max(1, round(1.1 * k)))
    return turn(img, px)


# --------------------------------------------------------------------------
# aesthetic treatments - Town Centres and trees only
# --------------------------------------------------------------------------

def style_minimap(scene: Scene, px: int = 360, scale: int = 3) -> Image.Image:
    """**Minimap.** The game's own minimap language: flat, saturated, read at
    a glance, nothing lit and nothing textured. A dark coastline outline is
    what does the work - it is what makes an archipelago legible at 360 px.
    """
    P = {"ford": (96, 150, 178), "beach": (198, 180, 138),
         "grass": (100, 152, 68), "brush": (110, 144, 70),
         "dry": (152, 156, 84), "desert": (206, 186, 128),
         "dirt": (146, 122, 84), "snow": (232, 236, 240),
         "farm": (128, 146, 78), "forest": (28, 74, 38), "placeholder": (110, 140, 74)}
    base = np.zeros(scene.grid.shape + (3,), np.uint8)
    shade_water(base, scene.land, (56, 112, 180), (16, 40, 86), reach=12.0)
    for k in ("grass", "brush", "dry", "desert", "dirt", "snow", "farm",
              "placeholder", "forest"):
        paint(base, scene.cls[k], P[k])
    paint(base, coast_beach(scene), P["beach"])
    paint(base, scene.cls["ford"], P["ford"])
    paint(base, edge(scene.land) & scene.water, (10, 26, 58))

    img = upscale(base, scale)
    draw_trees(img, scene, scale, (20, 60, 30), radius=1.4)
    tc_diamonds(img, scene, scale,
                half_px=max(4, round(scene.size * scale / 105)))
    out = turn(img, px)
    return diamond_frame(out, thumbnail.ICON_BORDER, thumbnail.ICON_OUTLINE,
                         width=max(3, px // 70))


def atlas_base(scene: Scene, grain: bool = True) -> np.ndarray:
    """The Atlas treatment's tile-resolution RGB, without marks or frame.

    Shared by ``style_atlas`` (the report panel) and ``icon_atlas`` (what
    ships), so the icon on the map-selection screen cannot drift from the
    picture the treatment was chosen from.
    """
    land = scene.land
    d = ndimage.distance_transform_edt(land).astype(np.float32)
    h = ndimage.gaussian_filter(d, 9.0)
    hi = (np.clip(h / max(1e-6, float(np.percentile(h[land], 96))), 0, 1)
          if land.any() else h)

    LOW = np.array([146, 166, 108], np.float32)     # coastal lowland
    MID = np.array([116, 146, 82], np.float32)
    HIGH = np.array([92, 116, 70], np.float32)      # deep interior
    t = hi[..., None]
    ramp = np.where(t < 0.5, LOW + (MID - LOW) * (t / 0.5),
                    MID + (HIGH - MID) * ((t - 0.5) / 0.5))

    base = np.zeros(scene.grid.shape + (3,), np.uint8)
    shade_water(base, land, (150, 190, 214), (44, 92, 138), reach=20.0)
    base[land] = ramp.astype(np.uint8)[land]
    for k, c in (("forest", (58, 96, 56)), ("brush", (124, 148, 84)),
                 ("dry", (168, 164, 104)), ("desert", (216, 198, 148)),
                 ("snow", (238, 240, 244)), ("dirt", (162, 142, 104))):
        paint(base, scene.cls[k], c)
    b = coast_beach(scene)
    sand = np.array([214, 198, 158], np.float32)
    base[b] = (base[b].astype(np.float32) * 0.45 + sand * 0.55).astype(np.uint8)
    paint(base, scene.cls["ford"], (128, 176, 186))
    paint(base, edge(land) & scene.water, (36, 70, 100))

    shade = hillshade(h, azimuth_deg=225.0, altitude_deg=45.0, z=3.0)
    a = np.asarray(base).astype(np.float32)
    a = np.where(land[..., None], a * (0.82 + 0.36 * shade)[..., None], a)
    base = np.clip(a, 0, 255).astype(np.uint8)
    if grain:
        texture(base, np.ones(scene.grid.shape, bool), _rng(scene), 4)
    return base


def style_atlas(scene: Scene, px: int = 360, scale: int = 3) -> Image.Image:
    """**Atlas.** A printed map: hypsometric land tint, an offshore gradient,
    a thin ink coastline, and only as much shading as keeps it from looking
    flat.

    Everything here is derived from the capture's own geometry - the land
    tint and the sea gradient are both distance to the coast, smoothed. The
    treatment that started in this slot hillshaded that same field hard, and
    it did not work: distance-to-shore has a near-constant gradient inland,
    so shading it lights the coast and nothing else, which comes out as a rim
    glow rather than terrain. Faking hills would have meant inventing
    elevation the capture does not contain, so the answer was to stop
    pretending to relief and tint by height band instead, the way an atlas
    does.

    Beach is blended toward the ground it sits on. The engine paints a
    one-tile beach ring round every islet and inlet, and at full contrast
    that ring survives the turn and the downsample as a drawn outline -
    which is the single artefact that made three of these treatments look
    like each other.
    """
    base = atlas_base(scene)
    img = upscale(base, scale)
    draw_trees(img, scene, scale, (44, 82, 44), radius=1.2)
    tc_diamonds(img, scene, scale, outline=(28, 26, 22),
                half_px=max(4, round(scene.size * scale / 100)))
    return vignette(turn(img, px, fill=(226, 224, 214)), 0.22)


#: The stock map icons draw a player marker 34 px across on a 420 px icon
#: (see ``thumbnail.render_icon``). The shipped icon uses HALF that: at 34 px
#: the markers crowd an 8-player map and are the first thing the eye lands
#: on, ahead of the geography they are sitting on.
ICON_MARKER_PX = 17.0


def icon_atlas(scene: Scene, px: int = thumbnail.ICON_PX,
               supersample: int = 2) -> Image.Image:
    """The shipped map-selection icon: Atlas, no border, small markers.

    No border on purpose - the game draws its own frame round the icon on
    the Select Location screen, and ``thumbnail.render_icon``'s tan diamond
    sat inside it as a second one.

    Geometry follows ``render_icon`` exactly, because that is what makes the
    markers come out as diamonds square to the finished icon: the grid is
    drawn axis-aligned at ``px/sqrt(2)`` a side, marks go on before the
    turn, and the turn grows the canvas back to ``px``.
    """
    side = round(px / math.sqrt(2)) * supersample
    # No grain in the icon. Per-tile noise is invisible at 420 px and it
    # triples the PNG - 200 KB an icon against 70 - and 21 icons ship inside
    # the mod the game has to load.
    base = atlas_base(scene, grain=False)
    # RGBA, and it matters: outside the diamond has to stay transparent. The
    # stock icons are RGBA and the game composites them over its own frame,
    # so a flattened icon ships black triangles in all four corners.
    img = Image.fromarray(base).convert("RGBA").resize((side, side),
                                                       Image.NEAREST)

    tile = side / scene.size
    draw_trees(img, scene, tile, (44, 82, 44), radius=1.15)
    half = max(2.0, ICON_MARKER_PX / math.sqrt(2) / 2 * supersample)
    d = ImageDraw.Draw(img)
    for player, x, y in scene.tcs:
        cx, cy = x * tile, y * tile
        c = thumbnail.AOE_PLAYER_COLORS[(player - 1) % 8]
        d.rectangle([cx - half, cy - half, cx + half, cy + half],
                    fill=c, outline=(20, 18, 14), width=max(1, round(half / 3)))

    img = img.rotate(thumbnail.ICON_ROTATION, expand=True,
                     resample=Image.BILINEAR, fillcolor=(0, 0, 0, 0))
    return img.resize((px, px), Image.LANCZOS)


def style_parchment(scene: Scene, px: int = 360, scale: int = 3) -> Image.Image:
    """**Parchment.** The map as a chart: aged paper, ink coastline, the sea
    cross-hatched rather than filled, woods stippled in dark green. Reads as
    a document about a place, which is the one register the other three
    cannot reach.

    The markers are the hard part here. Ink on paper vanished at 360 px, so
    they are deep red with a cream keyline - the way a chart marks a
    settlement, and the only colour on the page that is not earth.
    """
    PAPER = (216, 198, 164)
    INK = (62, 48, 34)
    base = np.zeros(scene.grid.shape + (3,), np.uint8)
    # A pale wash that still deepens offshore, so a bay reads as a bay.
    shade_water(base, scene.land, (198, 202, 194), (150, 160, 158), reach=18.0)
    paint(base, scene.cls["ford"], (204, 204, 190))
    paint(base, scene.land, PAPER)
    paint(base, coast_beach(scene), (226, 212, 178))
    paint(base, scene.cls["dry"], (208, 192, 150))
    paint(base, scene.cls["desert"], (224, 208, 166))
    paint(base, scene.cls["snow"], (238, 234, 226))

    rng = _rng(scene)
    yy, xx = np.mgrid[0:scene.size, 0:scene.size]
    # Cross-hatch, both diagonals, sea only. Drawn in grid space, so the turn
    # puts it square to the frame the way chart hatching sits.
    hatch = (((xx + yy) % 6 == 0) | ((xx - yy) % 6 == 0)) & scene.water
    base[hatch] = np.clip(base[hatch].astype(np.int16) - 16, 0, 255)
    # Stipple, not fill: a wood on a chart is a texture, not a shape.
    base[scene.cls["forest"]] = (198, 190, 154)
    stipple = scene.cls["forest"] & (rng.random(scene.grid.shape) < 0.5)
    base[stipple] = (56, 92, 52)
    paint(base, edge(scene.land) & scene.water, INK)
    texture(base, np.ones(scene.grid.shape, bool), rng, 6)

    img = upscale(base, scale)
    draw_trees(img, scene, scale, (52, 84, 48), radius=1.2)
    tc_diamonds(img, scene, scale, colors=[(150, 46, 38)] * 8,
                outline=(238, 226, 198),
                half_px=max(4, round(scene.size * scale / 100)))
    out = turn(img, px, fill=(236, 226, 202))
    return vignette(diamond_frame(out, (122, 98, 68), (58, 44, 32),
                                 width=max(2, px // 110)), 0.36)


def style_illuminated(scene: Scene, px: int = 360, scale: int = 4) -> Image.Image:
    """**Illuminated.** The warm end of the game's palette, lit from the
    upper left, a soft bloom off the water, a gold edge. The most stylised of
    the four: trying to look like an icon somebody drew.

    Beach is painted *after* the light, and only on real shore tiles. Lit
    along with everything else it became a bright halo tracing every islet,
    which read as an outline effect rather than as sand.
    """
    P = {"ford": (112, 158, 162), "beach": (206, 182, 138),
         "grass": (126, 152, 78), "brush": (128, 146, 74),
         "dry": (172, 162, 92), "desert": (216, 194, 138),
         "dirt": (160, 136, 94), "snow": (236, 238, 242),
         "farm": (142, 150, 84), "forest": (46, 88, 48), "placeholder": (110, 140, 74)}
    base = np.zeros(scene.grid.shape + (3,), np.uint8)
    shade_water(base, scene.land, (64, 126, 168), (18, 44, 82), reach=15.0)
    for k in ("grass", "brush", "dry", "desert", "dirt", "snow", "farm",
              "placeholder", "forest"):
        paint(base, scene.cls[k], P[k])

    rng = _rng(scene)
    shade = hillshade(coast_height(scene.land, smooth=3.0), azimuth_deg=225.0,
                      altitude_deg=48.0, z=2.6)
    warm = np.array([1.07, 1.0, 0.90], np.float32)
    a = np.asarray(base).astype(np.float32)
    a = np.where(scene.land[..., None],
                 a * (0.72 + 0.52 * shade)[..., None] * warm, a)
    canopy = scene.cls["forest"]
    shadow = ndimage.shift(canopy.astype(np.uint8), (2, 2), order=0) > 0
    a[shadow & ~canopy & scene.land] *= 0.72
    base = np.clip(a, 0, 255).astype(np.uint8)
    paint(base, coast_beach(scene), P["beach"])
    paint(base, scene.cls["ford"], P["ford"])
    texture(base, scene.land, rng, 6)

    img = upscale(base, scale)
    # Bloom: blur everything, screen it back over the water only, so the sea
    # glows and the coastline stays sharp.
    blur = img.filter(ImageFilter.GaussianBlur(radius=scale * 1.5))
    arr = np.asarray(img).astype(np.float32)
    bl = np.asarray(blur).astype(np.float32)
    water_up = np.repeat(np.repeat(scene.water, scale, 0), scale, 1)
    arr[water_up] = np.clip(arr[water_up] * 0.74 + bl[water_up] * 0.40, 0, 255)
    img = Image.fromarray(arr.astype(np.uint8))

    draw_trees(img, scene, scale, (34, 72, 38), radius=1.2)
    tc_diamonds(img, scene, scale,
                half_px=max(4, round(scene.size * scale / 100)))
    out = turn(img, px)
    return vignette(diamond_frame(out, (208, 174, 110), (44, 34, 24),
                                 width=max(3, px // 80)), 0.42)


def mod_icon_from_scene(scene: Scene, px: int = 360) -> Image.Image:
    """The shipped map-selection icon's own treatment, fed by a capture.

    ``thumbnail.render_icon`` takes a parsed ``.rms``; it only ever reads
    ``size``, ``land_mask`` and ``starts``, so a capture can stand in for
    one. That is the point of putting it beside the others: same renderer,
    real terrain, so the comparison is between *treatments* and not between
    a script and a capture.
    """
    class _AsScript:
        size = scene.size
        land_mask = scene.land
        starts = [(p, y, x) for p, x, y in scene.tcs]

    return thumbnail.render_icon(_AsScript(), px=px).convert("RGB")


#: ``(key, title, one-line what-it-is-for, callable)``. The report renders
#: these in order; ``existing`` and ``mod-icon`` are supplied by the caller
#: because they belong to other modules.
AESTHETIC_TREATMENTS = [
    ("minimap", "Minimap",
     "the game's own minimap language - flat, saturated, read at a glance",
     style_minimap),
    ("atlas", "Atlas",
     "a printed map: land tinted by height band, an offshore gradient, a thin "
     "ink coastline", style_atlas),
    ("parchment", "Parchment",
     "the map as an aged chart: ink coastline, hatched sea, stippled woods",
     style_parchment),
    ("illuminated", "Illuminated",
     "warm palette, lit from the upper left, bloom off the water, gold edge",
     style_illuminated),
]
