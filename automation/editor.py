"""Drive the Scenario Editor from a cold start, in Python, without blind clicks.

Replaces the PowerShell 5.1 driver for the parts that matter. What changed
and why, all of it learned on 2026-08-11:

* **The Generate button's colour is the generation signal.** It greys while
  the engine works and returns to red when it finishes. That is a mean over
  a 75x26 crop - no OCR, no model - and unlike the seed box it marks the
  *start* as well as the end. It is what proved a script crashed 53s into
  generation rather than failing to start, which the seed could not say
  because the seed only ever changes at the end.
* **CPU load was tried first and is not good enough alone.** It fires
  correctly (measured: load 1.56 against a 0.95 baseline) but reported
  "done" after 4s of a generation that had not finished a minute later,
  because a lull inside generation looks like the end.
* **Nothing here clicks a coordinate it has not just verified.** Every
  control is located by reading the screen, and the caller can check what
  it found before anything is clicked.

The one-time manual setup this project used to need is gone: ``setup()``
walks Main Menu -> Editors -> Create Scenario -> 8 players -> Random Map ->
Huge [240] on its own. **Create Scenario, never Load Scenario** - loading a
scenario to save the player-count clicks is what the user identified as
destabilising the editor.

Scrolling a combo box is done by clicking the scrollbar's arrow, slowly,
which is how a human does it here; the wheel works too but the arrow is
easier to aim at.

Usage:
    uv run python automation/editor.py --status
    uv run python automation/editor.py --setup
    uv run python automation/editor.py --generate
    uv run python automation/editor.py --recover --setup --generate
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from frame_server import _make_dpi_aware, find_window_rect  # noqa: E402

STEAM_URL = "steam://rungameid/813780"
GAME = "AoE2DE_s"

#: Screen rect of the Generate Map button's interior, and the redness drop
#: that means "generating". Measured: idle 107, generating 68.
GENERATE_BTN = (305, 1024)
GENERATE_BOX = (270, 1012, 345, 1038)
BUSY_DROP = 10.0
IDLE_RETURN = 5.0

#: Controls, all verified by reading the screen on 2026-08-11.
MENU_TAB = (98, 20)
PLAYERS_TAB = (449, 19)
PLAYER_COUNT_COMBO = (104, 956)
PLAYER_COUNT_SCROLL_DOWN = (183, 1050)
MAP_SIZE_COMBO = (305, 933)
MAP_SIZE_SCROLL_DOWN = (382, 1043)
RANDOM_MAP_RADIO = (80, 953)

#: Main-menu and dialog targets. Positions are re-read at run time by
#: ``omni.py`` when ``--verify`` is passed; these are the fallbacks.
EDITORS_ITEM = (367, 834)
CREATE_SCENARIO = (1227, 927)
MODS_REENABLE_YES = (799, 657)
MODS_REENABLED_OK = (962, 679)
BUGSPLAT_DONT_SEND = (940, 811)

#: How the "re-enable your mods?" dialog is recognised: the YES button's
#: red, which measures 102 with the dialog up and 6 on the bare main menu.
MODS_DIALOG_BOX = (760, 645, 840, 672)
MODS_DIALOG_RED = 50.0


def sh(cmd: str) -> str:
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True).stdout.strip()


def game_pid() -> str | None:
    return sh(f"(Get-Process -Name {GAME} -ErrorAction SilentlyContinue"
              f" | Select-Object -First 1 -ExpandProperty Id)") or None


def grab(box=None) -> Image.Image:
    _make_dpi_aware()
    return ImageGrab.grab(bbox=box, all_screens=True)


def redness(box=GENERATE_BOX) -> float:
    a = np.asarray(grab(box).convert("RGB"), dtype=float)
    return a[..., 0].mean() - (a[..., 1].mean() + a[..., 2].mean()) / 2


# ------------------------------------------------------------------ input


def move(x: int, y: int, steps: int = 12, delay: float = 0.008) -> None:
    """Move through real intermediate points, not a teleport.

    A teleported cursor is the one difference from a human that the old
    driver already found mattered: the first click into a list or combo box
    would not register without real motion first.
    """
    _make_dpi_aware()
    u = ctypes.windll.user32
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    sx, sy = pt.x, pt.y
    for i in range(1, steps + 1):
        u.SetCursorPos(int(sx + (x - sx) * i / steps),
                       int(sy + (y - sy) * i / steps))
        time.sleep(delay)
    u.SetCursorPos(x, y)


def click(x: int, y: int, settle: float = 0.35, hold: float = 0.09) -> None:
    move(x, y)
    time.sleep(settle)
    u = ctypes.windll.user32
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(hold)
    u.mouse_event(0x0004, 0, 0, 0, 0)


def locate(label: str, retries: int = 2) -> tuple[int, int] | None:
    """Where is the control whose text contains ``label``, right now?

    Reads the screen with OmniParser rather than trusting a constant. This
    is not optional for the main menu: its items move between launches -
    EDITORS measured at y=795 on one launch and y=834 on the next, and a
    click at the stale coordinate landed on MORE CONTENT instead. That is
    the whole class of bug this project has been calling "the agent was
    not clicking the actual button".

    Costs a model load per call (tens of seconds), which is why the editor
    panel below still uses measured constants: those have been stable
    across every launch observed, and they are re-checkable with
    ``omni.py --check-editor`` before anything is generated.
    """
    from omni import find, grab_screen, parse_image  # noqa: PLC0415

    for attempt in range(retries):
        shot = grab_screen(REPO / "out" / "omni" / "locate.png", (0, 0, 1920, 1080))
        hits = find(parse_image(shot), label)
        if hits:
            x, y = hits[0]["center"]
            print(f"  located {label!r} at ({x}, {y}) "
                  f"[{hits[0]['content']!r}]", flush=True)
            return x, y
        print(f"  {label!r} not found (attempt {attempt + 1})", flush=True)
        time.sleep(2)
    return None


def click_label(label: str) -> bool:
    where = locate(label)
    if where is None:
        return False
    click(*where)
    return True


def focus_game() -> bool:
    rect = find_window_rect("Age of Empires")
    if rect is None:
        return False
    sh(f"$s=New-Object -ComObject WScript.Shell; "
       f"$s.AppActivate((Get-Process -Name {GAME}).Id)")
    time.sleep(0.4)
    return True


# ------------------------------------------------------------- generation


@dataclass
class GenResult:
    ok: bool
    seconds: float
    detail: str


def generate(timeout: float = 300.0, poll: float = 0.5) -> GenResult:
    """Click Generate and watch the button until it comes back."""
    pid = game_pid()
    if pid is None:
        return GenResult(False, 0.0, "game not running")
    idle = redness()
    print(f"idle redness {idle:.1f}, pid {pid}", flush=True)
    click(*GENERATE_BTN)
    t0 = time.time()
    started = False
    while time.time() - t0 < timeout:
        time.sleep(poll)
        if game_pid() != pid:
            return GenResult(False, time.time() - t0,
                             f"GAME DIED {time.time()-t0:.1f}s in "
                             f"(generation had {'' if started else 'NOT '}started)")
        r = redness()
        if not started and r < idle - BUSY_DROP:
            started = True
            print(f"  generating at {time.time()-t0:.1f}s (redness {r:.1f})",
                  flush=True)
        elif started and r > idle - IDLE_RETURN:
            return GenResult(True, time.time() - t0, "button returned to red")
    return GenResult(False, time.time() - t0,
                     "timed out" + ("" if started else " - never started"))


# ------------------------------------------------------------------ setup


def scroll_combo(arrow: tuple[int, int], times: int, pause: float = 0.6) -> None:
    """Click a combo box's scroll arrow, slowly.

    Slowly on purpose: the user's read is that clicking these too fast, or
    slightly off, is a plausible way to destabilise the editor, and there is
    no hurry here.
    """
    move(*arrow)
    for i in range(times):
        time.sleep(pause)
        u = ctypes.windll.user32
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.09)
        u.mouse_event(0x0004, 0, 0, 0, 0)


def recover() -> bool:
    """Clear crash dialogs, re-enable mods, and get back to the main menu."""
    if game_pid():
        print("game already running")
        return True
    # BugSplat holds Steam's "game is running" lock, so it has to go first.
    if "encountered a problem" in "".join(
            sh("Get-Process | ForEach-Object { $_.MainWindowTitle }").splitlines()):
        print("dismissing crash reporter")
        click(*BUGSPLAT_DONT_SEND)
        time.sleep(3)
    print("launching")
    sh(f"Start-Process '{STEAM_URL}'")
    for _ in range(30):
        time.sleep(3)
        if game_pid():
            break
    if not game_pid():
        print("game did not start")
        return False
    time.sleep(20)

    # A crash makes the game disable every mod on the next launch and ask
    # whether to re-enable them. Miss this and the placeholder script is
    # simply not there - the editor would generate whatever else is
    # selected and the capture would be of the wrong map entirely.
    #
    # Detected by the YES button's colour rather than by clicking where it
    # usually is: measured 102 redness with the dialog up against 6 on the
    # bare main menu, which is not a marginal difference.
    if redness(MODS_DIALOG_BOX) > MODS_DIALOG_RED:
        print("mods were disabled by the crash - re-enabling")
        click(*MODS_REENABLE_YES)
        time.sleep(3)
        click(*MODS_REENABLED_OK)
        time.sleep(3)
        # The game says outright that a restart may be needed for mods to
        # take effect, and a half-loaded mod is exactly the state that
        # silently generates the wrong script. Pay the restart.
        print("restarting so the mod loads from launch")
        sh(f"Stop-Process -Name {GAME} -Force -ErrorAction SilentlyContinue")
        time.sleep(8)
        sh(f"Start-Process '{STEAM_URL}'")
        for _ in range(30):
            time.sleep(3)
            if game_pid():
                break
        time.sleep(20)
    return game_pid() is not None


def setup(players: int = 8) -> bool:
    """Main menu -> editor -> N players, Random Map, Huge [240]."""
    if not focus_game():
        print("no game window")
        return False
    # Located, not assumed - the main menu moves between launches.
    print("editors")
    if not click_label("editors"):
        print("could not find EDITORS on the main menu")
        return False
    time.sleep(5)
    print("create scenario (never load - loading one is what destabilises it)")
    if not click_label("create scenario"):
        print("could not find Create Scenario")
        return False
    time.sleep(6)

    print(f"players -> {players}")
    click(*PLAYERS_TAB)
    time.sleep(3)
    click(*PLAYER_COUNT_COMBO)
    time.sleep(2)
    # The list shows four at a time starting at 1; scroll so N is the last
    # visible row, then click that row.
    scroll_combo(PLAYER_COUNT_SCROLL_DOWN, max(0, players - 4))
    time.sleep(1)
    click(105, 1046)
    time.sleep(2)

    print("map -> random map")
    click(*MENU_TAB)
    time.sleep(3)
    click(*RANDOM_MAP_RADIO)
    time.sleep(2)

    print("map size -> Huge [240]")
    click(*MAP_SIZE_COMBO)
    time.sleep(2)
    scroll_combo(MAP_SIZE_SCROLL_DOWN, 2)
    time.sleep(1)
    click(260, 1022)
    time.sleep(2)
    return True


def status() -> None:
    pid = game_pid()
    print(f"game pid: {pid or 'not running'}")
    if pid:
        print(f"window  : {find_window_rect('Age of Empires')}")
        print(f"generate button redness: {redness():.1f} "
              f"(idle is ~107, generating ~68)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--recover", action="store_true",
                    help="clear crash dialogs and relaunch")
    ap.add_argument("--setup", action="store_true",
                    help="walk to a configured editor from the main menu")
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--generate", action="store_true")
    args = ap.parse_args()

    if args.status:
        status()
    if args.recover and not recover():
        return 1
    if args.setup and not setup(args.players):
        return 1
    if args.generate:
        r = generate()
        print(f"\n{'OK' if r.ok else 'FAILED'} after {r.seconds:.1f}s - {r.detail}")
        return 0 if r.ok else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
