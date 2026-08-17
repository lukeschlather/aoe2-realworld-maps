"""Render every shipped map's thumbnail: the in-game icon, and a gallery.

Cheap and engine-free: each image is drawn from the shipped ``.rms`` itself
(see ``rwmaps.thumbnail``), so it always matches what is in
``mod/Real World Maps/`` and a full rebuild takes seconds. They show land,
water, the shoreline band and the script's pinned player starts - not
forest, elevation or objects, which the engine rolls at generation time.

Two outputs, because they are read in different places:

* **Icons**, written into each mod next to the script as ``<stem>.png``.
  That is the game's own convention for a mod-supplied map image - stock
  ``mapicons/rm_arabia.png`` and the two subscribed mods that ship their
  own (Legacy ES Maps, Zetnus HyperRandom) all use the same 420x420 RGBA
  diamond - so these are isometric, the orientation the map-selection
  screen displays.  ``build_mod.py`` calls this at the end of a build,
  since a full build wipes the mod directories.
* **A gallery** under ``reports/``, north-up, for judging how recognisable
  a coastline is without the 45-degree rotation in the way.

Usage:
    uv run python automation/build_thumbnails.py
    uv run python automation/build_thumbnails.py --no-gallery
    uv run python automation/build_thumbnails.py --no-icons --px 256
"""

from __future__ import annotations

import argparse
import base64
import html
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import thumbnail  # noqa: E402

SCRIPTS_SUBDIR = Path("resources") / "_common" / "random-map-scripts"
#: Both mod roots build_mod.py maintains; the debug one carries the extra
#: placeholder slot, which gets an icon like any other script.
MOD_ROOTS = [REPO / "mod" / "Real World Maps", REPO / "mod" / "Real World Maps (Debug)"]
MOD_SCRIPTS = MOD_ROOTS[0] / SCRIPTS_SUBDIR
DEFAULT_OUT = REPO / "reports" / "map_thumbnails_data"
GALLERY = REPO / "reports" / "map_thumbnails.html"
#: Real captures embedded in the gallery when present: the Select Location
#: screen showing these icons, and the Scenario Editor's minimap of a
#: generated Britain, which is what settles the rotation. Captured by
#: hand-driving the UI automation; nothing regenerates them, so they are
#: evidence rather than output - recapture if the icons change.
#:
#: NOTE the game caches these images at startup. After rebuilding icons,
#: restart the game before checking them, or you will be looking at the
#: previous set - which happened, and briefly looked like the fix had not
#: worked.
INGAME_PROOF = DEFAULT_OUT / "ingame_select_location.png"
MINIMAP_PROOF = DEFAULT_OUT / "ingame_minimap_britain.png"


def write_icons(roots=MOD_ROOTS, quiet: bool = False) -> int:
    """Write ``<stem>.png`` beside every script in each mod root.

    Called by ``build_mod.py`` after a build, since a full build wipes the
    mod directories - an icon left behind for a renamed script would show up
    against nothing, and a script with no icon gets the game's generic one.
    """
    written = 0
    for root in roots:
        scripts = root / SCRIPTS_SUBDIR
        if not scripts.is_dir():
            continue
        for path in sorted(scripts.glob("*.rms")):
            try:
                script = thumbnail.parse_script(path)
            except ValueError as exc:
                print(f"  skip: {exc}")
                continue
            thumbnail.save_icon(script, path.with_suffix(".png"))
            written += 1
        # Icons for scripts that no longer exist are dead weight in the mod.
        for stale in sorted(scripts.glob("*.png")):
            if not stale.with_suffix(".rms").exists():
                stale.unlink()
                print(f"  removed stale icon {stale.name}")
    if not quiet:
        print(f"  {written} in-game icons written beside their scripts")
    return written


def _commit() -> str:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                       capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def _parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scripts", type=Path, default=MOD_SCRIPTS,
                   help="directory of .rms scripts to render (default: the "
                        "shipped mod)")
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--px", type=int, default=320, help="thumbnail size in pixels")
    p.add_argument("--as-grid", action="store_true",
                   help="render the raw grid instead of the way the engine "
                        "draws it (debugging only - nobody sees this view)")
    p.add_argument("--no-starts", action="store_true",
                   help="omit the player start dots")
    p.add_argument("--no-gallery", action="store_true",
                   help="skip the reports/ gallery and its north-up PNGs")
    p.add_argument("--no-icons", action="store_true",
                   help="skip the in-game icons written into the mod roots")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    scripts = sorted(args.scripts.glob("*.rms"))
    if not scripts:
        sys.exit(f"no .rms scripts in {args.scripts}")

    if not args.no_icons:
        write_icons()
    if args.no_gallery:
        return 0

    rendered: list[tuple[thumbnail.MapScript, Path]] = []
    for path in scripts:
        try:
            script = thumbnail.parse_script(path)
        except ValueError as exc:
            print(f"  skip: {exc}")
            continue
        png = args.outdir / f"{path.stem}.png"
        thumbnail.save_thumbnail(script, png, px=args.px, as_grid=args.as_grid,
                                 show_starts=not args.no_starts)
        land = 100.0 * script.land_mask.mean()
        print(f"  {png.relative_to(REPO)}  {script.size}x{script.size}  "
              f"{len(script.discs)} lands  {len(script.starts)} starts  land {land:.0f}%")
        rendered.append((script, png))

    write_gallery(rendered, args)
    print(f"\ngallery -> {GALLERY.relative_to(REPO)}")
    print(f"{len(rendered)}/{len(scripts)} thumbnails in {args.outdir.relative_to(REPO)}")
    return 0


def write_gallery(rendered, args) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    orientation = ("raw grid (debug)" if args.as_grid
                   else "as the engine draws it")
    cards = []
    for script, png in rendered:
        b64 = base64.b64encode(png.read_bytes()).decode("ascii")
        # Every shipped script's name has spaces in it, so the href needs
        # escaping as a URL, not just as HTML.
        rms_rel = quote("../" + script.path.relative_to(REPO).as_posix())
        land = 100.0 * script.land_mask.mean()
        cards.append(f"""
    <figure>
      <img src="data:image/png;base64,{b64}" alt="{html.escape(script.name)}">
      <figcaption>
        <b>{html.escape(script.name)}</b>
        <span>{script.size}&times;{script.size} &middot; {len(script.discs)} lands
          &middot; {len(script.starts)} starts &middot; land {land:.0f}%</span>
        <a href="{rms_rel}">{html.escape(script.path.name)}</a>
      </figcaption>
    </figure>""")

    proof = ""
    if INGAME_PROOF.exists():
        b64 = base64.b64encode(INGAME_PROOF.read_bytes()).decode("ascii")
        proof += f"""
<figure class="proof">
  <img src="data:image/png;base64,{b64}" alt="the game's Select Location screen">
  <figcaption>The icons as the game actually draws them, captured off the
    Select Location screen. <code>LJFS_real_world_spain</code> is an
    unrelated script with no image of its own, so the generic "?" it still
    shows is the control: the game is reading ours from beside the
    scripts.</figcaption>
</figure>"""
    if MINIMAP_PROOF.exists():
        b64 = base64.b64encode(MINIMAP_PROOF.read_bytes()).decode("ascii")
        proof += f"""
<figure class="proof">
  <img src="data:image/png;base64,{b64}" alt="the editor's minimap of a generated Britain">
  <figcaption>What fixes the rotation: the Scenario Editor's own minimap of
    a generated <code>RW Britain</code>. Unwarping this diamond back to a
    square scores IoU 0.60 against the script's land when north is put at
    the upper left, against 0.25 for the clockwise alternative the icons
    shipped with until 2026-08-14 - a 90&deg; error. The stock real-world
    maps' icons agree independently: <code>rwm_iberia</code> puts Africa at
    the lower right, <code>rwm_britain</code> puts Scotland at the upper
    left.</figcaption>
</figure>"""

    GALLERY.parent.mkdir(parents=True, exist_ok=True)
    GALLERY.write_text(f"""<!doctype html>
<meta charset="utf-8">
<title>Real World Maps - terrain thumbnails</title>
<style>
  body {{ background:#121212; color:#e8e8e8; font:14px/1.5 system-ui, sans-serif;
         margin:2rem; }}
  h1 {{ font-size:1.3rem; margin:0 0 .2rem; }}
  p.meta {{ color:#9a9a9a; margin:0 0 1.5rem; }}
  .grid {{ display:grid; gap:1.2rem;
           grid-template-columns:repeat(auto-fill,minmax({args.px}px,1fr)); }}
  figure {{ margin:0; }}
  img {{ width:100%; display:block; border-radius:4px; }}
  figcaption {{ display:flex; flex-direction:column; padding-top:.4rem; }}
  figcaption span {{ color:#9a9a9a; font-size:12px; }}
  a {{ color:#7fb3ff; font-size:12px; }}
  figure.proof {{ margin:0 0 2rem; max-width:1000px; }}
  figure.proof figcaption {{ color:#9a9a9a; font-size:12px; padding-top:.5rem; }}
</style>
<h1>Real World Maps &mdash; terrain thumbnails</h1>
<p class="meta">
  Land and water read back from each shipped <code>.rms</code>'s own
  <code>create_land</code> blocks, {orientation}; dots are the script's pinned
  player starts. Forest, elevation and objects are engine RNG at generation
  time and are not shown &mdash; this is the terrain the script asks for, not a
  render of a generated game.<br>
  The mod additionally ships each map an isometric 420&times;420 icon beside
  its script, which is what the game's map-selection screen displays.<br>
  Generated {stamp} &middot; commit {_commit()}
</p>{proof}
<div class="grid">{''.join(cards)}
</div>
""", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
