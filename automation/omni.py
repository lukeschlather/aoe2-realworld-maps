"""Find the Scenario Editor's controls with a vision model instead of by hand.

Why this exists, in the two failures it addresses:

* **Hand-found coordinates cannot say what they are looking at.** An agent
  spent a session clicking something that was not the Generate button and
  had no way to notice; the coordinates were simply wrong and everything
  downstream reported "the script would not generate". A detector that
  returns *labelled* boxes makes that self-evident, and the annotated image
  it draws makes it self-evident to a human too.
* **The editor's state is invisible to us.** It crashed on 2026-08-10 and
  came back at Blank Map / Small [144]. Nothing in the pipeline could tell,
  because nothing read the panel - and map size is not cosmetic here, land
  areas are absolute tile counts. ``--check-editor`` reads the panel.

**The models live in the vmauto project, not here.** ``vmauto``'s venv
already carries torch, easyocr and transformers at about 1.1 GB, and this
machine has run down to 11 GB free, so duplicating that into this project
would be the most expensive thing in it. This module is a thin bridge: it
re-invokes itself under vmauto's interpreter, which does the parsing, and
hands back plain JSON. Point ``RWMAPS_VMAUTO`` at the checkout to move it.

Measured on a real editor screenshot (2026-08-10, ``out/state_check_bf.png``
cropped to the game's monitor): the fast path - detection plus OCR, no
Florence-2 captioning, which is not installed - found 42 elements including
``Generate Map`` at (303, 1024), the map-size box reading ``240] Huge``, the
script selector reading ``placeholder``, and the seed value. Its ``Menu``
box centred within 2 px of the hand-found ``MENU_BTN``, which is the check
that says its coordinates are in the same physical pixel space the clicker
uses.

Usage:
    uv run python automation/omni.py --screen --annotate out/omni.png
    uv run python automation/omni.py --image out/state_check_bf.png --find "generate map"
    uv run python automation/omni.py --screen --check-editor
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent

#: Where the vendored OmniParser and its (large) venv live.
VMAUTO = Path(os.environ.get(
    "RWMAPS_VMAUTO", r"C:\Users\luke.schlather\setup\hyperv\vmauto"))

#: The game sits on the left monitor; the parse is much cleaner without a
#: second screen of unrelated text in frame. Set --no-crop to disable.
GAME_MONITOR = (0, 0, 1920, 1080)

#: Are we looking at the editor's map panel at all? Presence checks, and
#: they are only good for that - "Random Map" is a label that is on screen
#: whether or not it is the selected mode, so it can tell you the panel is
#: up and nothing more. Kept apart from the checks below on purpose: a
#: check that cannot fail is worse than no check, because it reads like
#: reassurance.
EDITOR_PRESENT = {
    "the map panel is up": ("map size",),
    "a Generate Map button": ("generate map",),
}

#: Is it configured the way a capture needs? These read *values* out of the
#: combo boxes, so they can and do fail - verified against the screenshot
#: taken right after the 2026-08-10 crash, where both come back MISSING.
#:
#: Substring, and lenient, because the OCR mangles reading order on the
#: combo boxes: "Huge [240]" comes back as "240] Huge". Matching the whole
#: string would fail on a perfectly good screen, which is the worst kind of
#: check to have in an abort path.
EDITOR_CONFIGURED = {
    "map size Huge [240]": ("240",),
    "the placeholder slot selected": ("placeholder",),
}

EDITOR_EXPECTED = {**EDITOR_PRESENT, **EDITOR_CONFIGURED}


# ------------------------------------------------------------------ worker


def _serve() -> int:
    """Hold the models open and parse whatever paths arrive on stdin.

    Measured on this machine: importing torch/easyocr costs 12.8s and
    constructing the models another 7.1s, against 16.5s of actual inference
    per full frame. A subprocess per call pays that 20s every time, which is
    more than half the wall clock of a cold parse and all of it avoidable.

    Protocol is one JSON request per line, one JSON response per line, so a
    caller can keep this open for a whole capture pass.
    """
    import contextlib  # noqa: PLC0415

    sys.path.insert(0, str(VMAUTO))
    os.chdir(VMAUTO)
    from PIL import Image

    from omni import ScreenParser  # noqa: PLC0415

    # The vendored Omniparser prints "Omniparser initialized!!!" to stdout,
    # and stdout is the protocol here - the first read came back with that
    # instead of the ready line and the client gave up. Anything the models
    # want to say goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        parser = ScreenParser()
    print(json.dumps({"ready": True}), flush=True)
    # readline(), not `for line in sys.stdin`: iterating a pipe read-ahead
    # buffers, so the worker sat waiting for a full buffer and never saw a
    # single request line. It looked exactly like a hang.
    while True:
        raw = sys.stdin.readline()
        if not raw:
            return 0
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("stop"):
                return 0
            img = Image.open(req["image"]).convert("RGB")
            if req.get("box"):
                img = img.crop(tuple(req["box"]))
            elif img.width > GAME_MONITOR[2]:
                img = img.crop(GAME_MONITOR)
            # The vendored parser prints during inference too, not only at
            # construction, and stdout is the protocol - one stray line and
            # the client's json.loads fails on the reply it never got.
            with contextlib.redirect_stdout(sys.stderr):
                els = parser.parse_screen_fast(img, debug_path=req.get("annotate"))
            ox, oy = (req["box"][0], req["box"][1]) if req.get("box") else (0, 0)
            print(json.dumps({"elements": [
                {"type": e.type, "content": (e.content or "").strip(),
                 "interactive": e.interactivity,
                 "bbox": [e.bbox_px[0] + ox, e.bbox_px[1] + oy,
                          e.bbox_px[2] + ox, e.bbox_px[3] + oy],
                 "center": [e.center_px[0] + ox, e.center_px[1] + oy]}
                for e in els]}), flush=True)
        except Exception as e:  # a bad request must not kill the worker
            print(json.dumps({"error": str(e)}), flush=True)
    return 0


def _worker(image_path: str, annotate: str | None) -> int:
    """Runs under vmauto's interpreter. Prints elements as JSON."""
    # Absolute before the chdir, or every relative path the caller passed
    # starts resolving against the vmauto checkout.
    image_path = str(Path(image_path).resolve())
    annotate = str(Path(annotate).resolve()) if annotate else None
    sys.path.insert(0, str(VMAUTO))
    os.chdir(VMAUTO)  # the vendored package resolves weights/ against cwd
    from PIL import Image

    from omni import ScreenParser  # noqa: PLC0415 - only exists in that venv

    img = Image.open(image_path).convert("RGB")
    # A saved full-desktop capture carries a second monitor's worth of
    # unrelated text, which the OCR half happily reads and which then has
    # to be filtered back out. The game is on the left monitor.
    if img.width > GAME_MONITOR[2]:
        img = img.crop(GAME_MONITOR)
    parser = ScreenParser()
    els = parser.parse_screen_fast(img, debug_path=annotate)
    print("<<<JSON>>>")
    print(json.dumps([
        {"type": e.type, "content": (e.content or "").strip(),
         "interactive": e.interactivity, "bbox": list(e.bbox_px),
         "center": list(e.center_px)}
        for e in els
    ]))
    return 0


# ------------------------------------------------------------------ bridge


def _vmauto_python() -> Path:
    exe = VMAUTO / ".venv" / "Scripts" / "python.exe"
    if not exe.exists():
        raise SystemExit(
            f"no vmauto interpreter at {exe}. That checkout owns the models "
            f"(torch/easyocr, ~1.1 GB) so this project does not have to; set "
            f"RWMAPS_VMAUTO if it lives somewhere else."
        )
    return exe


class Server:
    """A long-lived parser process, so the model load is paid once.

    Use it as a context manager around a whole capture pass. Falls back to
    nothing clever: if the worker dies, ``parse`` raises and the caller can
    go back to :func:`parse_image`.
    """

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [str(_vmauto_python()), str(Path(__file__).resolve()), "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
            env={**os.environ, "RWMAPS_VMAUTO": str(VMAUTO)})
        # Skip anything chatty the model stack emits before the handshake -
        # belt and braces, since the worker already redirects its own
        # construction noise to stderr.
        for _ in range(20):
            line = self.proc.stdout.readline()
            if not line:
                raise SystemExit("omniparser worker exited during startup")
            if '"ready"' in line:
                break
        else:
            raise SystemExit(f"omniparser worker never signalled ready: {line!r}")

    def parse(self, image: Path, box: tuple[int, int, int, int] | None = None,
              annotate: Path | None = None) -> list[dict]:
        req = {"image": str(Path(image).resolve())}
        if box:
            req["box"] = list(box)
        if annotate:
            req["annotate"] = str(Path(annotate).resolve())
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        # Skip anything that is not the reply. Defensive rather than
        # decorative: chatty inference code has now polluted this stream
        # twice, and a JSONDecodeError here reads as "the parser failed"
        # when the parse actually succeeded.
        for _ in range(50):
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("omniparser worker exited")
            line = line.strip()
            if not line.startswith("{"):
                continue
            reply = json.loads(line)
            if "error" in reply:
                raise RuntimeError(reply["error"])
            return reply["elements"]
        raise RuntimeError("omniparser worker sent no parseable reply")

    def close(self) -> None:
        try:
            self.proc.stdin.write('{"stop":true}\n')
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()

    def __enter__(self) -> "Server":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def parse_image(image: Path, annotate: Path | None = None,
                timeout: float = 900) -> list[dict]:
    """Elements in ``image``, via the vmauto interpreter. Pixel coordinates."""
    cmd = [str(_vmauto_python()), str(Path(__file__).resolve()),
           "--worker", "--image", str(image)]
    if annotate:
        annotate.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--annotate", str(annotate)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "RWMAPS_VMAUTO": str(VMAUTO)})
    if "<<<JSON>>>" not in r.stdout:
        raise SystemExit(f"omniparser worker failed (rc={r.returncode}):\n"
                         f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return json.loads(r.stdout.split("<<<JSON>>>", 1)[1].strip())


def grab_screen(dest: Path, crop: tuple[int, int, int, int] | None) -> Path:
    """Screenshot for parsing. Same GDI path frame_server.py uses."""
    sys.path.insert(0, str(Path(__file__).parent))
    from frame_server import _make_dpi_aware  # noqa: PLC0415

    from PIL import ImageGrab  # noqa: PLC0415

    _make_dpi_aware()
    img = ImageGrab.grab(all_screens=True)
    if crop:
        img = img.crop(crop)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def find(elements: list[dict], text: str,
         near: tuple[int, int] | None = None) -> list[dict]:
    """Elements matching ``text``, best first.

    Plain "contains" is not good enough and picked the wrong control three
    times in one pass: "map" matched *Generate Map* rather than the Map
    tab, "seed" matched *Seed Map* rather than the Seed box, "random map"
    matched *Random Map location* rather than the Random Map radio. The
    user's own notes record the same class of failure from substring OCR
    matching - two candidates averaged into a coordinate that hit neither.

    So: exact match wins, then the shortest containing text, because a
    longer string containing the query is usually a *different* control
    that merely mentions it. ``near`` breaks remaining ties by distance,
    for the genuinely ambiguous cases.
    """
    q = text.lower().strip()
    hits = [e for e in elements if q in e["content"].lower()]

    def rank(e):
        content = e["content"].lower().strip()
        dist = 0.0
        if near is not None:
            dx, dy = e["center"][0] - near[0], e["center"][1] - near[1]
            dist = (dx * dx + dy * dy) ** 0.5
        return (content != q,            # exact first
                not e["interactive"],    # then clickable
                len(content),            # then the least padded match
                dist)

    return sorted(hits, key=rank)


def check_editor(elements: list[dict]) -> dict[str, bool]:
    return {
        label: any(find(elements, want) for want in wants)
        for label, wants in EDITOR_EXPECTED.items()
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--serve", action="store_true",
                    help="hold the models open and answer JSON requests on "
                         "stdin, so a pass pays the 20s model load once")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--image", type=Path, help="parse this file")
    src.add_argument("--screen", action="store_true", help="parse a screenshot")
    ap.add_argument("--annotate", type=Path,
                    help="write the labelled debug image here - this is the "
                         "artifact that makes a wrong click obvious")
    ap.add_argument("--find", metavar="TEXT",
                    help="print the click coordinate of the matching element")
    ap.add_argument("--check-editor", action="store_true",
                    help="is the editor in a state a capture can use")
    ap.add_argument("--no-crop", action="store_true",
                    help=f"parse the whole desktop, not just {GAME_MONITOR}")
    ap.add_argument("--json", type=Path, help="write all elements here")
    args = ap.parse_args()

    if args.serve:
        return _serve()
    if args.worker:
        return _worker(str(args.image), str(args.annotate) if args.annotate else None)

    if args.screen:
        image = grab_screen(REPO / "out" / "omni" / "screen.png",
                            None if args.no_crop else GAME_MONITOR)
    elif args.image:
        image = args.image
    else:
        return ap.error("need --image or --screen")

    elements = parse_image(image, args.annotate)
    print(f"{len(elements)} elements from {image}")
    if args.annotate:
        print(f"annotated -> {args.annotate}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(elements, indent=1), encoding="utf-8")

    if args.find:
        hits = find(elements, args.find)
        if not hits:
            print(f"no element matching {args.find!r}")
            return 1
        for e in hits[:5]:
            print(f"  {e['center'][0]},{e['center'][1]}  "
                  f"{'interactive' if e['interactive'] else 'text'}  "
                  f"{e['content']!r}")

    if args.check_editor:
        results = check_editor(elements)
        print()
        for group, title in ((EDITOR_PRESENT, "is this the editor"),
                             (EDITOR_CONFIGURED, "is it set up for a capture")):
            print(f"  {title}:")
            for label in group:
                print(f"    {'ok  ' if results[label] else 'MISSING'}  {label}")
        if not all(results.values()):
            print("\nThe editor is not in a state a capture can use. After a "
                  "crash it comes back at Blank Map / Small [144], and map "
                  "size is not cosmetic - land areas here are absolute tile "
                  "counts, so the wrong size breaks the map rather than "
                  "shrinking it.")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
