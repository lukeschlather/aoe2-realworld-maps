"""Download and load Natural Earth vector data.

Natural Earth is public domain, so the data can be cached locally and
redistributed freely. Only the physical layers we need are fetched.
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from functools import lru_cache
from pathlib import Path

import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

#: Mirror that serves the raw Natural Earth zips.
_BASE = "https://naciscdn.org/naturalearth/{res}/physical/{name}.zip"

#: Where downloaded shapefiles are cached. Overridable for tests.
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "naturalearth"


def _cache_dir(name: str, res: str) -> Path:
    return DATA_DIR / res / name


def ensure_layer(name: str, res: str = "10m") -> Path:
    """Download and extract a Natural Earth layer if not already cached.

    Returns the directory holding the extracted ``.shp`` and friends.
    """
    target = _cache_dir(name, res)
    if (target / f"{name}.shp").exists():
        return target

    url = _BASE.format(res=res, name=name)
    target.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "rwmaps/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = resp.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for member in zf.namelist():
            # Zips are sometimes flat and sometimes nested one level deep.
            stem = Path(member).name
            if not stem or member.endswith("/"):
                continue
            (target / stem).write_bytes(zf.read(member))
    if not (target / f"{name}.shp").exists():
        raise RuntimeError(f"{url} did not contain {name}.shp")
    return target


@lru_cache(maxsize=8)
def load_layer(name: str, res: str = "10m") -> BaseGeometry:
    """Load a Natural Earth layer as a single (multi)geometry in EPSG:4326".

    Parsing the shapefile and unioning it into one multi-geometry is the
    dominant cost of a single ``rwmaps`` invocation (~8s for 10m land, ~2s
    for 10m lakes measured directly) - and every invocation pays it fresh,
    since :func:`functools.lru_cache` only lives for one process, which is
    fatal for anything that shells out to ``rwmaps`` repeatedly (batch
    generation, seed sweeps). The underlying shapefiles are static, so the
    merged geometry is cached to disk as WKB after the first computation and
    reused directly on every later call, in this process or a new one.
    """
    directory = ensure_layer(name, res)
    cache_path = directory / f"{name}.merged.wkb"
    if cache_path.exists():
        return shapely.from_wkb(cache_path.read_bytes())

    import shapefile  # pyshp

    reader = shapefile.Reader(str(directory / name))
    geoms = [shape(s.__geo_interface__) for s in reader.shapes()]
    reader.close()
    merged = shapely.union_all([g for g in geoms if not g.is_empty])
    cache_path.write_bytes(shapely.to_wkb(merged))
    return merged


def land(res: str = "10m") -> BaseGeometry:
    """Global land polygons (continents + islands), EPSG:4326."""
    return load_layer("ne_10m_land" if res == "10m" else f"ne_{res}_land", res)


def lakes(res: str = "10m") -> BaseGeometry:
    """Inland water bodies, EPSG:4326."""
    return load_layer("ne_10m_lakes" if res == "10m" else f"ne_{res}_lakes", res)
