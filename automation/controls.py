"""Never click a control without first confirming it is there.

The working hypothesis, and the reason this exists: **clicking before the
control is actually in place is what crashes the editor.** Every crash from
the PowerShell era is consistent with it, and one blind click was found
outright - the save sequence slept a fixed 200ms after opening the Menu and
then clicked (960, 436), which with no overlay up is the middle of the map,
where a click is a brush stroke rather than a no-op.

Verification is only worth having if it is cheap enough to run before
*every* click, so it is tiered:

1. **Template match at the expected box, with a small local search** - a
   mean absolute difference against a stored crop. No model.
2. **An OmniParser pass over the control's own region** - seconds, for when
   the template has genuinely moved.
3. **A full OmniParser pass** - when the screen is not what was expected at
   all, which is exactly the case where a labelled screen dump is what you
   want anyway.

Measured on this machine:

| check | cost |
|---|---|
| template match (tier 1+2) | **~130ms**, exact (diff 0.0) |
| OmniParser, 160x50 region | 2.9s |
| OmniParser, 420x160 region | 4.0s |
| OmniParser, full 1920x1080 | 17.8s |

Tier 1 is 130ms rather than the ~1ms the arithmetic suggests, and the
reason is worth knowing: almost all of it is the **screen grab**, not the
comparison. Verifying several controls from a single grab is therefore
nearly free, and grabbing per control is what costs. Either way 130ms
before every click is affordable, which is the point - blind clicking is
the suspected cause of the editor's crashes, so the budget question is
only whether verification is cheap enough to never skip. It is.

Note also that OmniParser's OCR gets *worse* on small crops - less
surrounding context - and read "Map Size" as "MAp Size" on a 420x160
region. Tier 2 is for relocating a control, not for reading values.

Regions are **machine-specific** - they are physical pixel boxes at this
display layout, the same assumption ``tuning_matrix.py``'s button
coordinates already make. Re-learn them if the resolution changes.

Usage:
    uv run python automation/controls.py --learn generate="generate map"
    uv run python automation/controls.py --verify-all
    uv run python automation/controls.py --show generate
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

REPO = Path(__file__).parent.parent
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from frame_server import _make_dpi_aware  # noqa: E402

REGISTRY = HERE / "regions.json"
TEMPLATES = HERE / "templates"

#: Mean absolute pixel difference below which a crop is "the same control".
#: The editor's panels are static art, so a match is normally near zero; the
#: threshold has room for JPEG-free but still slightly dithered redraws.
MATCH_TOL = 12.0

#: How far to slide the template when the exact box does not match, in
#: pixels. Covers a panel that shifted slightly without covering half the
#: screen - the main menu's items move by tens of pixels between launches,
#: which is a re-learn, not a nudge.
SEARCH_RADIUS = 6


@dataclass
class Control:
    name: str
    #: Screen box (left, top, right, bottom) in physical pixels.
    box: tuple[int, int, int, int]
    #: Where to click - the box centre unless overridden.
    click: tuple[int, int]
    #: What OmniParser called it when this was learned, for re-learning.
    label: str = ""

    @property
    def template(self) -> Path:
        return TEMPLATES / f"{self.name}.png"


def load() -> dict[str, Control]:
    if not REGISTRY.exists():
        return {}
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {k: Control(name=k, box=tuple(v["box"]), click=tuple(v["click"]),
                       label=v.get("label", "")) for k, v in raw.items()}


def save(controls: dict[str, Control]) -> None:
    REGISTRY.write_text(json.dumps(
        {c.name: {"box": list(c.box), "click": list(c.click), "label": c.label}
         for c in controls.values()}, indent=1), encoding="utf-8")


def grab(box) -> np.ndarray:
    _make_dpi_aware()
    return np.asarray(ImageGrab.grab(bbox=box, all_screens=True).convert("RGB"),
                      dtype=np.int16)


@dataclass
class Check:
    ok: bool
    diff: float
    offset: tuple[int, int]
    millis: float

    def __str__(self) -> str:
        where = "" if self.offset == (0, 0) else f" shifted by {self.offset}"
        return (f"{'ok' if self.ok else 'MISMATCH'} diff={self.diff:.1f}"
                f"{where} in {self.millis:.1f}ms")


def verify(c: Control, tol: float = MATCH_TOL,
           search: int = SEARCH_RADIUS) -> Check:
    """Is ``c`` where the registry says it is, right now?

    Returns the offset it was found at, so a caller can click the control
    where it actually is rather than where it was learned.
    """
    t0 = time.perf_counter()
    if not c.template.exists():
        return Check(False, float("inf"), (0, 0),
                     (time.perf_counter() - t0) * 1000)
    tpl = np.asarray(Image.open(c.template).convert("RGB"), dtype=np.int16)
    h, w = tpl.shape[:2]
    l, t, _, _ = c.box
    # One grab covering the whole search window, then slide in numpy - far
    # cheaper than a screen capture per candidate offset.
    live = grab((l - search, t - search, l + w + search, t + h + search))
    best, best_off = float("inf"), (0, 0)
    for dy in range(0, 2 * search + 1):
        for dx in range(0, 2 * search + 1):
            patch = live[dy:dy + h, dx:dx + w]
            if patch.shape[:2] != (h, w):
                continue
            d = float(np.abs(patch - tpl).mean())
            if d < best:
                best, best_off = d, (dx - search, dy - search)
            if best == 0.0:
                break
    return Check(best <= tol, best, best_off, (time.perf_counter() - t0) * 1000)


class NotThere(RuntimeError):
    """A control was not where it should be, so nothing was clicked."""


def click(name: str, controls: dict[str, Control] | None = None,
          settle: float = 0.35, hold: float = 0.09, tol: float = MATCH_TOL):
    """Verify, then click - or raise, having clicked nothing.

    This is the whole point of the module. The suspicion is that clicking a
    control that is not yet in place is what crashes the editor, and there
    is at least one proven instance: the save sequence used to sleep 200ms
    and then click a coordinate that, without the menu overlay up, is the
    middle of the map.

    Clicks where the control *is*, not where it was learned, so a panel
    that shifted a few pixels is followed rather than missed.
    """
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    controls = controls if controls is not None else load()
    c = controls.get(name)
    if c is None:
        raise NotThere(f"{name!r} is not in the registry - learn it first")
    check = verify(c, tol=tol)
    if not check.ok:
        raise NotThere(f"{name!r} is not on screen ({check}) - refusing to "
                       f"click {c.click}, which may be something else entirely")
    x, y = c.click[0] + check.offset[0], c.click[1] + check.offset[1]

    _make_dpi_aware()
    u = ctypes.windll.user32
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    for i in range(1, 13):
        u.SetCursorPos(int(pt.x + (x - pt.x) * i / 12),
                       int(pt.y + (y - pt.y) * i / 12))
        time.sleep(0.008)
    u.SetCursorPos(x, y)
    time.sleep(settle)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(hold)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    return check


def wait_for(name: str, timeout: float = 10.0, poll: float = 0.15,
             controls: dict[str, Control] | None = None) -> bool:
    """Block until a control is actually on screen.

    Replaces every fixed sleep between an action and the click that follows
    it. A sleep guesses; this looks.
    """
    controls = controls if controls is not None else load()
    c = controls.get(name)
    if c is None:
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        if verify(c).ok:
            return True
        time.sleep(poll)
    return False


def learn(name: str, label: str, pad: int = 4) -> Control | None:
    """Find ``label`` with a full OmniParser pass and remember where it is."""
    from omni import find, grab_screen, parse_image  # noqa: PLC0415

    shot = grab_screen(REPO / "out" / "omni" / "learn.png", (0, 0, 1920, 1080))
    hits = find(parse_image(shot), label)
    if not hits:
        print(f"  {label!r} not on screen")
        return None
    hit = hits[0]
    x1, y1, x2, y2 = hit["bbox"]
    box = (max(0, x1 - pad), max(0, y1 - pad), x2 + pad, y2 + pad)
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    Image.fromarray(grab(box).astype(np.uint8)).save(TEMPLATES / f"{name}.png")
    c = Control(name, box, tuple(hit["center"]), hit["content"])
    print(f"  learned {name}: {hit['content']!r} box={box} click={c.click}")
    return c


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--learn", nargs="+", metavar="NAME=LABEL",
                    help="locate each label with OmniParser and store it")
    ap.add_argument("--verify", nargs="+", metavar="NAME")
    ap.add_argument("--verify-all", action="store_true")
    ap.add_argument("--show", metavar="NAME")
    args = ap.parse_args()

    controls = load()

    if args.learn:
        for spec in args.learn:
            name, _, label = spec.partition("=")
            c = learn(name, label or name)
            if c:
                controls[name] = c
        save(controls)
        print(f"wrote {REGISTRY}")

    names = args.verify or (sorted(controls) if args.verify_all else [])
    for name in names:
        c = controls.get(name)
        print(f"{name:20} {verify(c) if c else 'not in the registry'}")

    if args.show:
        c = controls.get(args.show)
        print(json.dumps(asdict(c), indent=1) if c else "not in the registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
