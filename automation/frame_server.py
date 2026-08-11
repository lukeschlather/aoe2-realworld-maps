"""A slow, read-only window into what the game is doing, plus the frames
leading up to whatever went wrong.

Two jobs, and the second is the one that pays for this:

1. **Watch a run without owning the machine.** A capture pass takes the
   desktop for as long as it runs, so nobody can see progress or say
   anything while it does. This serves a frame every second or so over
   HTTP, so a run can be watched from another machine - or from a browser
   on this one - without touching the game.
2. **Leave evidence when the editor crashes.** It crashed mid-pass on
   2026-08-10 and left *nothing at all* behind: no record of what was
   clicked, no picture of the state it was in. Crashes stay anecdotes
   until they leave a trail. This keeps a rolling ring of the last N
   frames on disk and can freeze a copy of it on demand, which turns "it
   crashed again" into a sequence of frames an OmniParser trace can be run
   over afterwards.

**Read-only, on purpose.** There is no input path in this file. It cannot
click, focus, or move anything, so it can be left running during a pass
without becoming a suspect when something breaks.

Capture is Pillow's ``ImageGrab``, which is the GDI path - the same one
``ui_driver.ps1``'s ``Save-Screenshot`` uses, and which has been capturing
this game's window all along, so it is known to work here rather than
assumed to. It grabs the virtual desktop, so a virtual display added later
is captured the same way with no change: it is just another region of the
same coordinate space.

Usage:
    uv run python automation/frame_server.py                 # whole desktop
    uv run python automation/frame_server.py --window        # just the game
    uv run python automation/frame_server.py --fps 0.5 --ring 900

    # then open http://127.0.0.1:8765/ - or, to watch from another
    # machine, --host 0.0.0.0 (off by default: it puts a live picture of
    # this screen on the network)

    curl http://127.0.0.1:8765/snapshot   # freeze the ring for forensics
"""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import shutil
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import ImageGrab

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
FRAME_DIR = REPO / "out" / "frames"
SNAPSHOT_DIR = FRAME_DIR / "snapshots"

#: Default title substring for --window. The editor and the game share one
#: top-level window, so this matches in both.
GAME_TITLE = "Age of Empires"


# --------------------------------------------------------------- capture


def _make_dpi_aware() -> None:
    """Per-monitor-v2, so window rects come back in physical pixels.

    The argument types are not optional. Called as plain
    ``SetProcessDpiAwarenessContext(-4)`` ctypes marshals the handle as a
    32-bit int, the call fails, and nothing says so - window rects then
    come back in *logical* pixels (3072x864 on this machine's 3840x1080 at
    125%), which is the same silent coordinate-space mismatch
    ``RENDER_PIPELINE.md`` records as having already cost this project once.
    """
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    # Older Windows, and harmless to call when the above already succeeded
    # in a previous process - awareness is per-process and set-once.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()


def find_window_rect(title_contains: str) -> tuple[int, int, int, int] | None:
    """Screen rect of the first visible top-level window whose title matches.

    ctypes rather than a PowerShell round trip: this is called once per
    frame while following a window, and spawning a shell for it would be
    the slowest thing in the loop.
    """
    user32 = ctypes.windll.user32
    _make_dpi_aware()
    found: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_contains.lower() not in buf.value.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right > rect.left and rect.bottom > rect.top:
            found.append((rect.left, rect.top, rect.right, rect.bottom))
        return False

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


class Ring:
    """Fixed-size on-disk ring of JPEG frames.

    On disk rather than in memory so the frames survive *this* process
    dying too, which is the case that matters if a crash takes more than
    the game with it. Slots are overwritten in place, so the footprint is
    bounded and known up front - this machine has run down to 11 GB free,
    so an unbounded frame dump is not an acceptable default.
    """

    def __init__(self, root: Path, size: int):
        self.root = root
        self.size = size
        self.lock = threading.Lock()
        self.next_slot = 0
        self.count = 0
        self.stamps: dict[int, float] = {}
        root.mkdir(parents=True, exist_ok=True)

    def put(self, jpeg: bytes) -> None:
        with self.lock:
            slot = self.next_slot
            self.next_slot = (slot + 1) % self.size
            self.count = min(self.count + 1, self.size)
            self.stamps[slot] = time.time()
        (self.root / f"frame_{slot:05d}.jpg").write_bytes(jpeg)
        (self.root / "latest.jpg").write_bytes(jpeg)

    def ordered(self) -> list[tuple[int, float]]:
        """Slots oldest-first, which is the order a trace wants to read."""
        with self.lock:
            items = sorted(self.stamps.items(), key=lambda kv: kv[1])
        return items

    def snapshot(self) -> Path:
        """Freeze a copy of the ring, oldest-first, renumbered.

        Renumbered because the ring's slot numbers wrap, and a directory
        whose filenames do not sort into playback order is a trap for
        whatever reads it later.
        """
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = SNAPSHOT_DIR / stamp
        dest.mkdir(parents=True, exist_ok=True)
        manifest = []
        for i, (slot, when) in enumerate(self.ordered()):
            src = self.root / f"frame_{slot:05d}.jpg"
            if not src.exists():
                continue
            name = f"{i:05d}.jpg"
            shutil.copyfile(src, dest / name)
            manifest.append({"file": name, "unix_time": round(when, 3),
                             "clock": datetime.fromtimestamp(when).isoformat()})
        (dest / "manifest.json").write_text(
            json.dumps({"frames": manifest}, indent=1), encoding="utf-8")
        return dest


def capture_loop(ring: Ring, region: tuple[int, int, int, int] | None,
                 width: int, quality: int, interval: float,
                 follow_window: str | None, stop: threading.Event) -> None:
    misses = 0
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            if follow_window:
                # Re-resolve every frame: the window moves when it is sent to
                # another display, and a stale rect silently captures the
                # wrong part of the desktop rather than failing.
                rect = find_window_rect(follow_window)
                if rect is None:
                    misses += 1
                    if misses in (1, 30) or misses % 300 == 0:
                        print(f"no window matching {follow_window!r} "
                              f"({misses} frames)", flush=True)
                    stop.wait(interval)
                    continue
                misses = 0
                region = rect
            img = ImageGrab.grab(bbox=region, all_screens=True)
            if width and img.width > width:
                img = img.resize((width, round(img.height * width / img.width)))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=quality)
            ring.put(buf.getvalue())
        except Exception as e:  # a viewer must never take the run down
            print(f"capture failed: {e}", flush=True)
        stop.wait(max(0.0, interval - (time.monotonic() - t0)))


# ------------------------------------------------------------------ http


_PAGE = """<!doctype html><meta charset="utf-8">
<title>rwmaps frame viewer</title>
<style>
 :root{color-scheme:dark light}
 body{margin:0;background:#111;color:#ddd;font:14px system-ui,sans-serif}
 header{display:flex;gap:1rem;align-items:center;padding:.5rem .75rem}
 img{display:block;max-width:100%;height:auto}
 button,input{font:inherit}
 #age.stale{color:#e66}
</style>
<header>
  <strong>rwmaps</strong>
  <span id="age">-</span>
  <label>every <input id="every" type="number" min="0.2" step="0.2"
    value="1" style="width:4rem"> s</label>
  <button id="snap">snapshot ring</button>
  <span id="msg"></span>
</header>
<img id="f" alt="latest frame">
<script>
const img=document.getElementById('f'),age=document.getElementById('age');
let timer=null;
function tick(){
  img.src='/latest.jpg?t='+Date.now();
  fetch('/ring.json').then(r=>r.json()).then(d=>{
    const s=d.latest_age_seconds;
    age.textContent=(s==null?'no frames yet':s.toFixed(1)+'s old, '+d.count+' in ring');
    age.className=(s!=null&&s>10)?'stale':'';
  }).catch(()=>{});
}
function schedule(){
  if(timer)clearInterval(timer);
  timer=setInterval(tick,Math.max(200,
    parseFloat(document.getElementById('every').value||1)*1000));
}
document.getElementById('every').onchange=schedule;
document.getElementById('snap').onclick=()=>{
  fetch('/snapshot').then(r=>r.json()).then(d=>{
    document.getElementById('msg').textContent='wrote '+d.frames+' -> '+d.path;});
};
tick();schedule();
</script>
"""


def make_handler(ring: Ring):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path == "/":
                    self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/latest.jpg":
                    f = ring.root / "latest.jpg"
                    if not f.exists():
                        self._send(404, b"no frame yet", "text/plain")
                    else:
                        self._send(200, f.read_bytes(), "image/jpeg")
                elif path == "/ring.json":
                    items = ring.ordered()
                    newest = items[-1][1] if items else None
                    self._send(200, json.dumps({
                        "count": len(items),
                        "size": ring.size,
                        "latest_age_seconds": (
                            None if newest is None else time.time() - newest),
                        "frames": [{"slot": s, "unix_time": round(t, 3)}
                                   for s, t in items],
                    }).encode(), "application/json")
                elif path == "/snapshot":
                    dest = ring.snapshot()
                    n = len(list(dest.glob("*.jpg")))
                    print(f"snapshot: {n} frames -> {dest}", flush=True)
                    self._send(200, json.dumps(
                        {"path": str(dest), "frames": n}).encode(),
                        "application/json")
                elif path.startswith("/frame/"):
                    f = ring.root / f"frame_{int(path.rsplit('/', 1)[1]):05d}.jpg"
                    if not f.exists():
                        self._send(404, b"no such frame", "text/plain")
                    else:
                        self._send(200, f.read_bytes(), "image/jpeg")
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception as e:
                self._send(500, str(e).encode(), "text/plain")

        def log_message(self, *a):  # the access log is noise here
            pass

    return Handler


def snapshot_ring(port: int = 8765, timeout: float = 5.0) -> str | None:
    """Ask a running frame server to freeze its ring; None if none is up.

    Importable so a capture pass can call it the moment it decides
    something went wrong, while the frames that explain it are still in
    the ring. Best effort by design - a viewer being down must never turn
    into a failed capture pass.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/snapshot", timeout=timeout) as r:
            return json.loads(r.read()).get("path")
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fps", type=float, default=1.0,
                    help="frames per second (default 1; fractional is fine)")
    ap.add_argument("--ring", type=int, default=600,
                    help="frames kept on disk, oldest overwritten (default 600)")
    ap.add_argument("--width", type=int, default=960,
                    help="downscale to this width, 0 to keep full size")
    ap.add_argument("--quality", type=int, default=60, help="JPEG quality")
    ap.add_argument("--window", nargs="?", const=GAME_TITLE, default=None,
                    metavar="TITLE",
                    help=f"capture just this window, re-resolved every frame "
                         f"(default title match {GAME_TITLE!r})")
    ap.add_argument("--region", default=None, metavar="X,Y,W,H",
                    help="capture a fixed screen rect instead")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 to watch from another machine - which does "
                         "put a live picture of this screen on the network")
    ap.add_argument("--dir", type=Path, default=FRAME_DIR / "ring")
    args = ap.parse_args()

    _make_dpi_aware()
    region = None
    if args.region:
        x, y, w, h = (int(v) for v in args.region.split(","))
        region = (x, y, x + w, y + h)
    if args.window and not args.region:
        rect = find_window_rect(args.window)
        print(f"window {args.window!r}: "
              f"{'not found yet, will keep looking' if rect is None else rect}")

    ring = Ring(args.dir, args.ring)
    # Say the cost out loud. Free space on this machine has been down to
    # 11 GB, and a frame ring is exactly the kind of thing that quietly
    # eats the rest of it.
    est_mb = args.ring * (args.width or 1920) * 0.00006
    print(f"ring: {args.ring} frames in {args.dir} (~{est_mb:.0f} MB at "
          f"{args.width or 'full'}px/q{args.quality}), "
          f"{args.ring / args.fps / 60:.1f} min of history at {args.fps} fps")

    stop = threading.Event()
    t = threading.Thread(target=capture_loop, daemon=True, args=(
        ring, region, args.width, args.quality, 1.0 / args.fps,
        args.window if not args.region else None, stop))
    t.start()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(ring))
    print(f"viewing on http://{args.host}:{args.port}/   (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
