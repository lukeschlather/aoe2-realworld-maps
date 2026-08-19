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
import contextlib
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import controls  # noqa: E402
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


#: Where this module's narration goes. ``None`` means stdout, which is what
#: a person running ``editor.py`` directly wants.
#:
#: A harness with its own two logs replaces this with a callable, so lines
#: like "idle redness 100.6, pid 34948" land in the verbose JSON log instead
#: of stdout. They are useful - a pid and a redness are exactly what a
#: post-mortem needs - and they are also precisely what must stay out of a
#: terse log intended to diff cleanly between runs. See ``runlog.py``.
SINK = None


def _say(*args, **kwargs) -> None:
    if SINK is None:
        print(*args, **kwargs)
        return
    SINK(" ".join(str(a) for a in args))


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
    """Click (x, y), having actually arrived there from somewhere else.

    The nudge is not decoration. Clicking the pixel the cursor is already
    parked on can fail to register - it behaves like Windows merging the two
    presses into a double-click - and the old PowerShell driver carried the
    same jitter for the same reason. It matters here because every retry in
    this file re-clicks the *same* control after a failed confirmation, so
    without this the second and third attempts are the ones least likely to
    work, which is backwards.
    """
    _make_dpi_aware()
    u = ctypes.windll.user32
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    if abs(pt.x - x) <= 2 and abs(pt.y - y) <= 2:
        move(x - 9, y - 9, steps=4)
        time.sleep(0.05)
    move(x, y)
    time.sleep(settle)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(hold)
    u.mouse_event(0x0004, 0, 0, 0, 0)


#: A parser held open across a menu walk, so re-reading the screen between
#: clicks costs inference instead of a fresh model load. None means "parse
#: in a subprocess", which is correct but ~20s slower per call.
_SERVER = None


@contextlib.contextmanager
def parser_open():
    """Hold OmniParser's models open for the duration of a menu walk.

    Every click below is followed by a fresh read of the screen, which is
    the only way to know the click landed. That turns a model load per
    screen-read into the dominant cost of recovery, and it is exactly the
    cost ``omni.Server`` exists to remove. Failing to start one is not
    fatal - the calls fall back to a subprocess parse each.
    """
    global _SERVER
    from omni import Server  # noqa: PLC0415

    if _SERVER is not None:      # already inside one
        yield
        return
    try:
        _SERVER = Server()
    except Exception as e:       # noqa: BLE001 - a slow path is still a path
        _say(f"  (no parser server: {e} - parsing per call instead)", flush=True)
        yield
        return
    try:
        yield
    finally:
        srv, _SERVER = _SERVER, None
        srv.close()


def _parse(shot: Path) -> list[dict]:
    if _SERVER is not None:
        return _SERVER.parse(shot)
    from omni import parse_image  # noqa: PLC0415

    return parse_image(shot)


def locate(label: str, retries: int = 2) -> tuple[int, int] | None:
    """Where is the control whose text contains ``label``, right now?

    Reads the screen with OmniParser rather than trusting a constant. This
    is not optional for the main menu: its items move between launches -
    EDITORS measured at y=795 on one launch and y=834 on the next, and a
    click at the stale coordinate landed on MORE CONTENT instead. That is
    the whole class of bug this project has been calling "the agent was
    not clicking the actual button".

    "Right now" is load-bearing and the answer goes stale: see
    :func:`click_label`, which re-reads rather than reusing a coordinate.
    """
    from omni import find, grab_screen  # noqa: PLC0415

    # Read where the game actually is rather than assuming the primary
    # monitor. This box was hardcoded to (0,0,1920,1080), which is right
    # only while the window happens to sit at the desktop origin - on this
    # machine's two-monitor layout it need not, and a parse of the wrong
    # half of the desktop finds nothing and reports it as "the button is
    # missing". Coordinates come back relative to the crop, so the crop's
    # own origin has to be added before anything is clicked.
    box = find_window_rect("Age of Empires") or (0, 0, 1920, 1080)
    ox, oy = box[0], box[1]
    for attempt in range(retries):
        shot = grab_screen(REPO / "out" / "omni" / "locate.png", box)
        hits = find(_parse(shot), label)
        if hits:
            x, y = hits[0]["center"]
            x, y = x + ox, y + oy
            _say(f"  located {label!r} at ({x}, {y}) "
                  f"[{hits[0]['content']!r}]", flush=True)
            return x, y
        _say(f"  {label!r} not found (attempt {attempt + 1})", flush=True)
        time.sleep(2)
    return None


def click_label(label: str, confirm: str | None = None, tries: int = 3) -> bool:
    """Click ``label``, and prove it landed by finding ``confirm`` after.

    A single located-then-clicked coordinate is not enough on the main menu,
    which is where recovery starts. A pass died here: OmniParser put EDITORS
    at y=795, the click went to y=795, and the game stayed on the main menu
    - the following 'create scenario' lookups then failed three times and
    recovery gave up with nine regions still untaken. The coordinate was not
    wrong when it was read; this menu measurably moves between launches (the
    y=795 / y=834 note above), and a menu that has just faded in is still
    settling, so the button can leave before the cursor arrives.

    Both of those are invisible to a blind click and obvious to a second
    look, so every attempt re-reads the screen from scratch instead of
    retrying the same stale point, and the click is only believed once the
    screen it was supposed to open is actually there. ``confirm`` is a label
    that exists on the destination and not on the origin.
    """
    for attempt in range(1, tries + 1):
        where = locate(label)
        if where is None:
            return False
        click(*where)
        if confirm is None:
            return True
        # The destination fades in - and "Create Scenario" builds a whole
        # editor session, which is slow - so be patient before calling the
        # click a miss. Being impatient here is not free: the retry would
        # re-click, and a click that lands in an editor that *had* loaded is
        # a brush stroke on the map, which this project already suspects of
        # causing crashes. (Re-locating first is the other guard: once the
        # editor is up, the origin label is gone, so there is nothing to
        # click again.)
        time.sleep(3)
        if locate(confirm, retries=3) is not None:
            return True
        _say(f"  clicking {label!r} at {where} did not bring up {confirm!r} "
              f"- re-reading the screen (attempt {attempt}/{tries})", flush=True)
    return False


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


def generate(timeout: float = 300.0, poll: float = 0.5,
             start_grace: float = 5.0, click_tries: int = 3) -> GenResult:
    """Click Generate and watch the button until it comes back.

    The click is RETRIED if generation never starts, which is the whole
    reason this is not three lines. Measured: the button greys 1.1-1.4s
    after a click that registers, every time. So a click with nothing
    happening 5s later did not register - and the old version of this
    function waited out the entire 300s timeout before saying so. A pass
    died exactly that way: the click at 23:24:40 never landed, redness sat
    at idle 100.6 for five minutes, and the only reason it was noticed was
    a human remarking that the CPU was oddly flat.

    ``save()`` already knew this about the Menu click and re-clicked it -
    "the retired driver re-opened the menu and retried Save for exactly
    this reason" - and Generate was left with a single blind attempt.

    **Why a click gets swallowed.** The user's read, and it fits better than
    "the click was dropped": an editor panel such as **Terrain** was open, so
    the first click went to dismissing that rather than to Generate. Verified
    shape on the very next run - click 1 did nothing, click 2 generated in
    54.6s.

    Two blind spots make that invisible from here, and neither is fixed by
    this function:

    * ``is_foreground()`` only reads the *window title*, so it cannot tell
      which tab or panel the editor is showing. A game that is foreground and
      on the wrong panel looks identical to one ready to generate.
    * ``redness(GENERATE_BOX)`` samples a fixed rectangle. It cannot
      distinguish "the Generate button, idle" from "whatever else is reddish
      at those coordinates", so the idle reading is not proof the button is
      even on screen.

    The retry papers over both cheaply and provably. A real fix would verify
    the Generate control is present before clicking - ``controls.py`` and
    ``locate("generate map")`` can both do it - at the cost of a screen read
    per generation, which is why it is not done unconditionally here.

    Re-clicking is safe here precisely because it is gated on *not having
    started*: idle redness means no generation is in flight, so there is
    nothing to interrupt or double up.

    ``seconds`` is measured from the click that actually took effect, not
    from the first one, so a retry does not inflate the generation time the
    latency work reads. ``clicks`` says whether that happened.
    """
    pid = game_pid()
    if pid is None:
        return GenResult(False, 0.0, "game not running")
    idle = redness()
    _say(f"idle redness {idle:.1f}, pid {pid}", flush=True)

    t0 = time.time()
    click(*GENERATE_BTN)
    clicks, last_click = 1, time.time()
    started = False
    while time.time() - t0 < timeout:
        time.sleep(poll)
        if game_pid() != pid:
            return GenResult(False, time.time() - last_click,
                             f"GAME DIED {time.time()-t0:.1f}s in "
                             f"(generation had {'' if started else 'NOT '}started)")
        r = redness()
        if not started and r < idle - BUSY_DROP:
            started = True
            _say(f"  generating at {time.time()-last_click:.1f}s "
                  f"(redness {r:.1f}, click {clicks})", flush=True)
        elif started and r > idle - IDLE_RETURN:
            return GenResult(True, time.time() - last_click,
                             f"button returned to red"
                             f"{'' if clicks == 1 else f' after {clicks} clicks'}")
        elif not started and time.time() - last_click > start_grace:
            if clicks >= click_tries:
                return GenResult(
                    False, time.time() - t0,
                    f"generation never started after {clicks} clicks - the "
                    f"button stayed idle, so the clicks are not registering")
            _say(f"  nothing happening {time.time()-last_click:.1f}s after "
                  f"click {clicks} - re-clicking Generate", flush=True)
            click(*GENERATE_BTN)
            clicks, last_click = clicks + 1, time.time()
    return GenResult(False, time.time() - t0,
                     "timed out" + ("" if started else " - never started"))



def press(vk: int, hold: float = 0.05) -> None:
    """Tap a virtual key at the focused window."""
    u = ctypes.windll.user32
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(hold)
    u.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


VK_ESCAPE = 0x1B
VK_BACK = 0x08

#: The scenario the capture loop keeps overwriting. One fixed name so the
#: file browser is answered once per session and every later save is silent.
SAVE_NAME = "rw_capture_slot"

#: How many times ``save()`` will try to get the Menu overlay open. The Menu
#: click intermittently fails to register, and waiting longer does not fix a
#: click that never landed.
MENU_TRIES = 3

#: How long a save dialog gets to disappear after being answered before the
#: answer is assumed lost and re-clicked.
DIALOG_RECLICK_S = 1.5


def type_text(text: str, clear: int = 40, hold: float = 0.02) -> None:
    """Type into whatever has keyboard focus, clearing it first.

    ``VkKeyScanW`` rather than a scancode table so the shift state for
    each character comes from the active layout instead of being assumed.
    """
    u = ctypes.windll.user32
    for _ in range(clear):
        press(VK_BACK, hold)
    for ch in text:
        vk = u.VkKeyScanW(ord(ch))
        if vk == -1:
            continue
        shift = (vk >> 8) & 1
        if shift:
            u.keybd_event(0x10, 0, 0, 0)
        press(vk & 0xFF, hold)
        if shift:
            u.keybd_event(0x10, 0, 2, 0)


def wait_for_main_menu(timeout: float = 120.0, poll: float = 1.5) -> bool:
    """Skip the opening cinematic and wait until the main menu is really up.

    The game plays an intro on launch, and the previous code just slept 20
    seconds and hoped - which is both slower than needed when the intro is
    short and wrong when it is not. Escape skips it; the loop presses
    Escape and then *checks*, rather than assuming either the press or the
    duration.

    The check is the main menu's own template, so this returns when the
    menu is verifiably there - which is also exactly the precondition the
    first click needs.
    """
    reg = controls.load()
    menu = reg.get("editors_menu")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if menu is not None and controls.verify(menu).ok:
            _say(f"  main menu up after {time.time()-t0:.0f}s")
            return True
        # NEVER press Escape over the mods dialog. Escape dismisses it
        # without re-enabling anything, and with mods off the placeholder
        # script is not in the Random Map list at all - the editor silently
        # falls back to the first stock script and generates that instead.
        # Measured, and it cost a capture: the selector read "Acclivity"
        # and the result was a 240x240 map that was 100% land. This exact
        # bug was introduced by adding the cutscene skip.
        if redness(MODS_DIALOG_BOX) > MODS_DIALOG_RED:
            _say("  mods dialog is up - leaving it for recover(), "
                  "not escaping past it")
            return False
        if is_foreground():
            press(VK_ESCAPE)
        time.sleep(poll)
    _say(f"  main menu did not appear within {timeout:.0f}s")
    return menu is None  # no template learned yet: do not block the caller


def is_foreground() -> bool:
    """Is the game the window receiving input right now?"""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return "age of empires" in buf.value.lower()


def why_not_there(name: str, pid: str | None) -> str:
    """Why did a control fail to verify? Cheapest explanations first.

    A pixel mismatch has four causes and only one of them needs a model:

    1. **The game crashed.** A pid check, ~50ms, and it is the one cause
       where clicking anything at all is wrong.
    2. **Somebody is using the machine.** If the game is not foreground,
       the automation is looking at whatever is on top of it - and its
       clicks would land there too. Wait, do not click.
    3. **The UI has not settled yet.** By far the most common, and the
       remedy is simply to wait and look again, which is what the caller's
       retry loop does.
    4. **The screen is genuinely not what we expected.** Only this one is
       worth an OmniParser pass, and when it happens an annotated dump is
       exactly what a human wants to see anyway.

    Checking in this order matters because 1 and 2 are ~50ms each while 4
    is seconds, and because 1 and 2 have *different* correct responses -
    recover, and wait for the human to finish, respectively.
    """
    if game_pid() != pid:
        return "crashed"
    if not is_foreground():
        return "not-foreground"
    return "unsettled"


def wait_for_foreground(timeout: float = 180.0, poll: float = 3.0) -> bool:
    """Wait for the game to be the window receiving input.

    Its own separate function because "not foreground" is the one preflight
    failure that is not about the editor at all - it means somebody is using
    the machine, or that another window was raised over the game - and the
    correct response is to wait, not to touch anything.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_foreground():
            return True
        time.sleep(poll)
    return False


def click_when_ready(name: str, tries: int = 20, pause: float = 0.25,
                     pid: str | None = None) -> bool:
    """Verify, wait, verify again - and never click on a guess.

    Escalates to a full OmniParser pass with an annotated image only after
    the cheap explanations are exhausted, because that is the only case
    where a labelled picture of the screen tells anyone anything.
    """
    reg = controls.load()
    pid = pid or game_pid()
    for attempt in range(tries):
        try:
            controls.click(name, reg)
            return True
        except controls.NotThere:
            why = why_not_there(name, pid)
            if why == "crashed":
                _say(f"  {name}: the game is gone - not clicking")
                return False
            if why == "not-foreground":
                if attempt % 8 == 0:
                    _say(f"  {name}: the game is not foreground, waiting "
                          f"rather than clicking into whatever is")
                time.sleep(1.0)
                continue
            time.sleep(pause)
    # Cheap explanations exhausted: this is the case a picture is for.
    shot = REPO / "out" / "omni" / f"unexpected_{name}.png"
    _say(f"  {name}: still not there after {tries} tries - "
          f"parsing the screen for a look")
    try:
        from omni import find, grab_screen, parse_image  # noqa: PLC0415
        img = grab_screen(shot, (0, 0, 1920, 1080))
        els = parse_image(img, annotate=shot.with_name(f"{shot.stem}_boxed.png"))
        hits = find(els, controls.load()[name].label or name)
        _say(f"  {name}: OmniParser {'found it at ' + str(hits[0]['center']) if hits else 'cannot see it either'};"
              f" annotated screen at {shot.with_name(shot.stem + '_boxed.png')}")
    except Exception as e:
        _say(f"  {name}: escalation failed too ({e})")
    return False


MOD_TITLES = ("Real World Maps", "Real World Maps (Debug)")


def mod_status_path() -> Path:
    sys.path.insert(0, str(REPO / "src"))
    from rwmaps import install as install_mod  # noqa: PLC0415

    return install_mod.find_profile() / "mods" / "mod-status.json"


def mods_enabled() -> dict[str, bool]:
    """Which of our mods the game currently has switched on.

    ``mods/mod-status.json`` is the game's own record, so this is ground
    truth rather than a screen reading - instant, and it cannot be confused
    by OCR. Worth preferring over anything visual: the failure it detects
    is completely silent. With the mod off, the placeholder script is not
    in the Random Map list, the editor falls back to the first stock
    script, and the capture is of a different map that still has the right
    size and player count.
    """
    import json  # noqa: PLC0415

    p = mod_status_path()
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {m["Title"]: bool(m.get("Enabled"))
            for m in data.get("Mods", []) if m.get("Title") in MOD_TITLES}


def enable_mods(priority: int | None = None) -> bool:
    """Switch our mods back on. Only safe while the game is NOT running.

    A crash makes the game disable every mod, and that state *persists* -
    the "re-enable your mods?" prompt appears once, and if it is missed
    (pressing Escape past it does exactly that) the mods simply stay off
    with nothing to click. Editing the game's own record is the reliable
    fix, and it must happen with the game closed or the running process
    rewrites the file on exit.
    """
    import json  # noqa: PLC0415

    if game_pid():
        _say("  not touching mod-status.json while the game is running - "
              "it would be overwritten on exit")
        return False
    p = mod_status_path()
    if not p.exists():
        _say(f"  no {p}")
        return False
    data = json.loads(p.read_text(encoding="utf-8"))
    changed = 0
    for m in data.get("Mods", []):
        if m.get("Title") not in MOD_TITLES:
            continue
        if not m.get("Enabled"):
            m["Enabled"] = True
            changed += 1
        # Our mods sit at the bottom of the Mods list (priority 23 and 25
        # of ~25), so reaching them in the UI means scrolling a long list -
        # the interaction this project has the most history with. Raising
        # the priority puts them at the top, which is the same trick as
        # naming the slot script AA_ so it sorts first and needs no click
        # at all. Opt-in, because it is the user's mod ordering.
        if priority is not None and m.get("Priority") != priority:
            m["Priority"] = priority
            changed += 1
    if changed:
        p.write_text(json.dumps(data), encoding="utf-8")
        _say(f"  re-enabled {changed} mod(s) in mod-status.json")
    return True


def selector_is_placeholder() -> bool | None:
    """Is the Random Map list actually on our slot? None if not learnable.

    Worth its own check because the failure it catches is silent and
    expensive. With mods disabled the placeholder script is absent from
    the list and the editor falls back to the first *stock* script; the
    map still generates, still comes out 240x240 with 8 players, and is
    simply a different map. One such capture was recorded under the name
    "Britain" and was 100% land. The IoU guard caught it, but only after a
    full generate-and-save had been spent on it.

    Template rather than OCR: the selector's text is small and OmniParser
    read it as "placeholder" once and not at all the next time, which is
    not something to gate a pass on.
    """
    reg = controls.load()
    c = reg.get("slot_selector")
    if c is None:
        return None
    return controls.verify(c).ok


def preflight() -> tuple[bool, str]:
    """Is the editor actually going to generate OUR script?

    Two checks, cheapest first, because the failure they catch is silent
    and the whole pass depends on it. With the mod disabled the placeholder
    is not in the Random Map list and the editor generates the first stock
    script instead - right size, right player count, wrong map. One such
    capture was filed under "Britain" and was 100% land; only the coastline
    IoU caught it, after a full generate-and-save had been spent.

    Costs about a quarter of a second against the ~90s a wasted sample
    costs, and against a whole pass if it goes unnoticed.
    """
    off = [name for name, on in mods_enabled().items() if not on]
    if off:
        return False, (f"these mods are disabled: {off}. The placeholder "
                       f"script is not in the Random Map list while they "
                       f"are, so the editor would generate a stock map. "
                       f"Close the game and run editor.enable_mods().")
    on_slot = selector_is_placeholder()
    if on_slot is False:
        # Distinguish "wrong script" from "something is covering the
        # panel". An unanswered overwrite prompt sits over the map panel
        # and made this report a wrong selector when the selector was
        # simply hidden - a misleading message costs more than no message.
        reg = controls.load()
        if "overwrite_yes" in reg and controls.verify(reg["overwrite_yes"]).ok:
            return False, ("an unanswered 'overwrite?' prompt is covering "
                           "the panel - the selector was not readable, not "
                           "necessarily wrong")
        if not is_foreground():
            return False, ("the game is not foreground, so the panel could "
                           "not be read - somebody may be using the machine")
        return False, ("the Random Map selector is not on "
                       "AA_rw_placeholder_tester - the editor would "
                       "generate whatever it is on instead.")
    if on_slot is None:
        return True, ("slot_selector is not in the control registry, so the "
                      "selector was NOT checked - only the mod state was")
    return True, "mods enabled and the selector is on the placeholder slot"


def newest_scenario(scenario_dir: Path) -> Path | None:
    files = sorted(scenario_dir.glob("*.aoe2scenario"),
                   key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def save(scenario_dir: Path, timeout: float = 30.0) -> Path | None:
    """Menu -> Save, with the overlay confirmed present before Save is clicked.

    This is the sequence that was demonstrably clicking blind. It slept a
    fixed 200ms after opening the Menu and then clicked SAVE_BTN, which
    with no overlay up is the middle of the map - where a click in the
    Scenario Editor is a brush stroke, not a no-op. So a menu that had not
    finished laying out did not produce a harmless missed click; it edited
    the scenario silently, and is the leading suspect for the crashes.

    Two independent confirmations, because the project has been burned by
    each alone: the overlay's own control must be visible before Save is
    clicked, and a genuinely newer file must appear before the save is
    called done. The menu closing is not sufficient - that has been
    observed with no file written.
    """
    before = newest_scenario(scenario_dir)
    before_mtime = before.stat().st_mtime if before else 0.0

    reg = controls.load()
    # Opening the Menu has to be retried, not just waited on. The Menu click
    # is one of the two that intermittently fail to register - the retired
    # driver re-opened the menu and retried Save for exactly this reason, and
    # not carrying that over was caught on the very first pass after the
    # migration: capture 1 of a session saved, capture 2 reported "the Menu
    # overlay never appeared" with the editor in a perfectly clean state
    # afterwards (every panel control verified present, no dialog up), which
    # is what a dropped click looks like and not what a stuck editor looks
    # like. Re-clicking Menu is safe in a way that re-clicking Save is not:
    # if the overlay *is* up this closes it and the next attempt reopens it,
    # whereas Save's coordinate with no overlay up is the middle of the map.
    for attempt in range(1, MENU_TRIES + 1):
        try:
            controls.click("menu_button", reg)
        except controls.NotThere as e:
            _say(f"  save: {e}")
            return None
        if controls.wait_for("menu_save", timeout=8.0, controls=reg):
            break
        _say(f"  save: the Menu overlay never appeared "
              f"({attempt}/{MENU_TRIES}) - re-clicking Menu")
    else:
        _say("  save: could not get the Menu overlay up - not clicking "
              "where Save would be, because that coordinate is the map "
              "when the menu is closed")
        return None
    try:
        controls.click("menu_save", reg)
    except controls.NotThere as e:
        _say(f"  save: {e}")
        return None

    # Three things can follow the Save click and only two are dialogs:
    #
    # * the file simply appears - every save after the first, silently, to
    #   the same fixed name;
    # * the **Save Scenario file browser** opens - the first save of a
    #   session. The old pipeline knew only the silent form, which is why it
    #   could report the menu closing with no file on disk: it had left this
    #   dialog sitting open and unanswered;
    # * **"that file already exists, overwrite it?"** - unanswered this
    #   blocks the save AND covers the map panel, which then makes the next
    #   region's preflight report the selector as wrong when it is merely
    #   hidden. One unanswered modal, two misleading symptoms.
    #
    # Waiting for each in turn spent the *full* timeout on every one that
    # did not happen. Measured over 12 captures: save cost 10.1s every time,
    # with a variance of 0.3s, of which 8.0s was two dialog waits timing out
    # on dialogs that never come again after the first save - 2.2 hours of a
    # 1000-capture pass, spent watching for nothing. So watch for all three
    # at once and take whichever fires. The saved file is what makes leaving
    # early safe: if it is on disk, there is no dialog left pending.
    t0 = time.time()
    clicked_at: dict[str, float] = {}
    while time.time() - t0 < timeout:
        newest = newest_scenario(scenario_dir)
        if newest and newest.stat().st_mtime > before_mtime:
            _say(f"  saved {newest.name} after {time.time()-t0:.1f}s")
            return newest
        for dialog in ("save_dlg_confirm", "overwrite_yes"):
            # A dialog still on screen a moment after being answered was not
            # answered - the click did not register, which is documented as
            # happening to these clicks. Answering it once and then waiting
            # out the timeout is the same mistake the Menu click made.
            if dialog not in reg:
                continue
            if time.time() - clicked_at.get(dialog, 0.0) < DIALOG_RECLICK_S:
                continue
            if not controls.verify(reg[dialog]).ok:
                continue
            clicked_at[dialog] = time.time()
            try:
                if dialog == "save_dlg_confirm":
                    _say(f"  save: file browser opened - naming it {SAVE_NAME}")
                    controls.click("save_dlg_name", reg)
                    time.sleep(0.3)
                    type_text(SAVE_NAME)
                    time.sleep(0.3)
                controls.click(dialog, reg)
            except controls.NotThere as e:
                _say(f"  save: {e}")
                return None
        # Only needed while both dialogs are inside their re-click cooldown -
        # a verify is a screen grab and already paces the loop otherwise.
        time.sleep(0.1)
    _say(f"  save: no new scenario file after {timeout:.0f}s")
    return None


class CaptureFailed(RuntimeError):
    """One generate-and-save did not produce a scenario."""


@dataclass
class Capture:
    """Where one capture's wall-clock went, and what it produced.

    Timed because the goal scale is ~1000 generations, and at that scale the
    only useful question about a phase is how many hours of the pass it is.
    Every harness gets this for free rather than each printing its own total,
    which is all any of them had.
    """
    path: Path
    generate_s: float
    save_s: float

    @property
    def total_s(self) -> float:
        return self.generate_s + self.save_s


def generate_and_save(scenario_dir: Path) -> Capture:
    """One capture: generate a map, save it, return what appeared and when.

    This is the whole of what the PowerShell driver did for every capture
    harness in this directory - ``Click-GenerateMapVerified``, click Menu,
    ``Click-SaveVerified`` - and six harnesses each carried their own copy
    of that block, with their own stale coordinates. They now share this,
    so a fix to the click path reaches all of them.

    The callers used to pass a ``before_mtime`` baseline taken before the
    script was even copied into the slot. ``save()`` takes its own,
    immediately before clicking, which cannot be fooled by a file that
    landed in between.
    """
    result = generate()
    if not result.ok:
        raise CaptureFailed(f"generate: {result.detail}")
    t0 = time.time()
    saved = save(scenario_dir)
    save_s = time.time() - t0
    if saved is None:
        raise CaptureFailed("save produced no new scenario file")
    _say(f"  capture: generate {result.seconds:.1f}s + save {save_s:.1f}s")
    return Capture(saved, result.seconds, save_s)


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


def mods_off() -> list[str]:
    """Which of our mods the game currently has switched off."""
    return [name for name, on in mods_enabled().items() if not on]


def _launch() -> bool:
    """Start the game and wait for a pid."""
    sh(f"Start-Process '{STEAM_URL}'")
    for _ in range(30):
        time.sleep(3)
        if game_pid():
            return True
    return False


def _quit() -> None:
    sh(f"Stop-Process -Name {GAME} -Force -ErrorAction SilentlyContinue")
    time.sleep(8)


def recover() -> bool:
    """Clear crash dialogs, re-enable mods, and get back to the main menu."""
    if game_pid() and not mods_off():
        _say("game already running")
        return True
    if game_pid():
        # Not "nothing to recover", which is what this used to report. A
        # running game with our mods off is the silent wrong-map state: the
        # placeholder is absent from the Random Map list, so the editor
        # generates a stock script and the capture is filed under one of our
        # region names anyway. Only the loop at the end can help, since
        # mod-status.json is unwritable while this process lives.
        _say(f"game is running, but {mods_off()} disabled")
    else:
        titles = "".join(
            sh("Get-Process | ForEach-Object { $_.MainWindowTitle }").splitlines())
        # BugSplat holds Steam's "game is running" lock, so it has to go first.
        if "encountered a problem" in titles:
            _say("dismissing crash reporter")
            click(*BUGSPLAT_DONT_SEND)
            time.sleep(3)
        # The engine does not always crash into BugSplat. It also faults
        # straight to Windows' own "Application Error" box - seen reading
        # address 0x9, i.e. a null dereference - and that box sits on top of
        # the main menu, where it is not a crash reporter to dismiss but an
        # opaque window covering the button the walk is looking for. A setup
        # died exactly that way, reporting "'editors' not found" twice with
        # EDITORS simply hidden behind it. WerFault owns the box, so ending
        # WerFault is what clears it; nothing here needs the debug option it
        # offers.
        if "Application Error" in titles:
            _say("dismissing Windows' Application Error box (WerFault)")
            sh("Stop-Process -Name WerFault -Force -ErrorAction SilentlyContinue")
            time.sleep(2)
        # Do this before launching: the game reads mod-status.json at start
        # and rewrites it on exit, so it is only editable with the game down.
        enable_mods()
        _say("launching")
        if not _launch():
            _say("game did not start")
            return False
        # Give the dialog a moment to appear before deciding it is absent.
        time.sleep(12)

        # A crash makes the game disable every mod on the next launch and ask
        # whether to re-enable them. Miss this and the placeholder script is
        # simply not there - the editor would generate whatever else is
        # selected and the capture would be of the wrong map entirely.
        #
        # Detected by the YES button's colour rather than by clicking where it
        # usually is: measured 102 redness with the dialog up against 6 on the
        # bare main menu, which is not a marginal difference.
        if redness(MODS_DIALOG_BOX) > MODS_DIALOG_RED:
            _say("mods were disabled by the crash - re-enabling")
            click(*MODS_REENABLE_YES)
            time.sleep(3)
            click(*MODS_REENABLED_OK)
            time.sleep(3)
            # The game says outright that a restart may be needed for mods to
            # take effect, and a half-loaded mod is exactly the state that
            # silently generates the wrong script. Pay the restart.
            _say("restarting so the mod loads from launch")
            _quit()
            _launch()
            wait_for_main_menu()

    # Whatever the dialog did or did not do, ask the game's own record before
    # declaring recovery finished. The colour check above is a heuristic about
    # a modal that may never appear, and a recovery sailed straight past it
    # and came back with both mods off - the capture after that would have
    # been a stock map under Italy's name, and only the preflight in
    # mod_capture stopped it. mod-status.json is not a heuristic.
    for attempt in range(1, 3):
        off = mods_off()
        if not off:
            return game_pid() is not None
        _say(f"  the game has {off} disabled - closing to fix its own "
              f"record, then relaunching ({attempt}/2)")
        _quit()
        enable_mods()
        if not _launch():
            _say("  game did not come back")
            return False
        wait_for_main_menu()
    if mods_off():
        _say(f"  {mods_off()} will not stay enabled across a relaunch")
        return False
    return game_pid() is not None


def setup(players: int = 8) -> bool:
    """Main menu -> editor -> N players, Random Map, Huge [240]."""
    if not focus_game():
        _say("no game window")
        return False
    # Located, not assumed - the main menu moves between launches - and
    # confirmed, not hoped: each click has to produce the screen it was
    # aimed at before the walk goes on. One parser stays open across both,
    # since confirming doubles the number of screen reads.
    with parser_open():
        _say("editors")
        if not click_label("editors", confirm="create scenario"):
            _say("could not get from the main menu to the Editors menu")
            return False
        _say("create scenario (never load - loading one is what destabilises it)")
        if not click_label("create scenario", confirm="generate map"):
            _say("could not get from the Editors menu into a new scenario")
            return False

    _say(f"players -> {players}")
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

    _say("map -> random map")
    click(*MENU_TAB)
    time.sleep(3)
    click(*RANDOM_MAP_RADIO)
    time.sleep(2)

    _say("map size -> Huge [240]")
    click(*MAP_SIZE_COMBO)
    time.sleep(2)
    scroll_combo(MAP_SIZE_SCROLL_DOWN, 2)
    time.sleep(1)
    click(260, 1022)
    time.sleep(2)
    return True


def ensure_ready(players: int = 8, tries: int = 3) -> tuple[bool, str]:
    """Get the editor into a state a capture can use, and prove it did.

    Recovery has to be a loop, not a step, because the state it repairs is
    not observable when it runs: the game disables our mods on the launch
    after a crash and records it minutes later, so a rebuild can pass every
    check it can make and still be wrong by the time a capture starts. The
    only honest test is preflight, and the only useful response to preflight
    saying no is to rebuild and ask again.

    Callers used to each grow their own version of this - mod_capture's
    crash path, its per-region check, the A/B harness - and the ones that
    forgot simply aborted on a state that a second rebuild fixes. One
    function, so a pass cannot start against an editor that would generate
    a stock map under one of our region names.
    """
    why = "not checked"
    for attempt in range(1, tries + 1):
        if game_pid() is not None and not mods_off():
            ok, why = preflight()
            if ok:
                return True, why
            # A Menu overlay left open by a failed save sits over the whole
            # panel and eats every click aimed underneath it, so the editor
            # looks unusable while being perfectly fine one click away. Try
            # that click before paying for a relaunch: measured, the panel
            # underneath still had Random Map, the placeholder slot and Huge
            # [240] exactly as set up.
            with parser_open():
                where = locate("cancel", retries=1)
                if where is not None:
                    _say(f"  a dialog is covering the panel - closing it",
                          flush=True)
                    click(*where)
                    time.sleep(2)
                    ok, why = preflight()
                    if ok:
                        return True, why
        else:
            why = f"pid={game_pid()}, mods disabled={mods_off()}"

        # "Not foreground" is not a broken editor. It means somebody is using
        # the machine, or a window was raised over the game, and rebuilding is
        # actively the wrong answer: the walk it runs starts from the main
        # menu, so with the editor already up it looks for 'editors', does not
        # find it - because it genuinely is not on that screen - and aborts a
        # pass whose editor was fine the whole time. Measured: a 5-sample pass
        # died exactly this way with the editor idle at the Generate button
        # and every mod enabled, 90 minutes after the previous pass left it in
        # perfect shape. Try focusing it, then wait; only then rebuild.
        if "not foreground" in why:
            _say("  the game is not foreground - focusing and waiting rather "
                 "than rebuilding an editor that may be fine", flush=True)
            focus_game()
            if wait_for_foreground():
                ok, why = preflight()
                if ok:
                    return True, why
            continue
        _say(f"  editor not ready ({why}) - rebuilding ({attempt}/{tries})",
              flush=True)
        if not (recover() and setup(players)):
            return False, "could not rebuild the editor"
    return preflight()


def status() -> None:
    pid = game_pid()
    _say(f"game pid: {pid or 'not running'}")
    if pid:
        _say(f"window  : {find_window_rect('Age of Empires')}")
        _say(f"generate button redness: {redness():.1f} "
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
        _say(f"\n{'OK' if r.ok else 'FAILED'} after {r.seconds:.1f}s - {r.detail}")
        return 0 if r.ok else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
