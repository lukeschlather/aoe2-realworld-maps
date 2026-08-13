"""Render a terrain thumbnail for every shipped map, plus a gallery page.

Cheap and engine-free: each thumbnail is drawn from the shipped ``.rms``
itself (see ``rwmaps.thumbnail``), so it always matches what is in
``mod/Real World Maps/`` and a full rebuild takes seconds. It shows land,
water, the shoreline band and the script's pinned player starts - not
forest, elevation or objects, which the engine rolls at generation time.

Usage:
    uv run python automation/build_thumbnails.py
    uv run python automation/build_thumbnails.py --isometric --px 256
    uv run python automation/build_thumbnails.py --outdir out/thumbs --no-gallery
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

MOD_SCRIPTS = REPO / "mod" / "Real World Maps" / "resources" / "_common" / "random-map-scripts"
DEFAULT_OUT = REPO / "reports" / "map_thumbnails_data"
GALLERY = REPO / "reports" / "map_thumbnails.html"


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
    p.add_argument("--isometric", action="store_true",
                   help="rotate 45 degrees, the orientation the engine draws "
                        "the grid in (default is north-up)")
    p.add_argument("--no-starts", action="store_true",
                   help="omit the player start dots")
    p.add_argument("--no-gallery", action="store_true",
                   help="write the PNGs only, no gallery page")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    scripts = sorted(args.scripts.glob("*.rms"))
    if not scripts:
        sys.exit(f"no .rms scripts in {args.scripts}")

    rendered: list[tuple[thumbnail.MapScript, Path]] = []
    for path in scripts:
        try:
            script = thumbnail.parse_script(path)
        except ValueError as exc:
            print(f"  skip: {exc}")
            continue
        png = args.outdir / f"{path.stem}.png"
        thumbnail.save_thumbnail(script, png, px=args.px, isometric=args.isometric,
                                 show_starts=not args.no_starts)
        land = 100.0 * script.land_mask.mean()
        print(f"  {png.relative_to(REPO)}  {script.size}x{script.size}  "
              f"{len(script.discs)} lands  {len(script.starts)} starts  land {land:.0f}%")
        rendered.append((script, png))

    if not args.no_gallery:
        write_gallery(rendered, args)
        print(f"\ngallery -> {GALLERY.relative_to(REPO)}")
    print(f"{len(rendered)}/{len(scripts)} thumbnails in {args.outdir.relative_to(REPO)}")
    return 0


def write_gallery(rendered, args) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    orientation = "isometric (as the engine draws it)" if args.isometric else "north-up"
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
</style>
<h1>Real World Maps &mdash; terrain thumbnails</h1>
<p class="meta">
  Land and water read back from each shipped <code>.rms</code>'s own
  <code>create_land</code> blocks, {orientation}; dots are the script's pinned
  player starts. Forest, elevation and objects are engine RNG at generation
  time and are not shown &mdash; this is the terrain the script asks for, not a
  render of a generated game.<br>
  Generated {stamp} &middot; commit {_commit()}
</p>
<div class="grid">{''.join(cards)}
</div>
""", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
