"""A **preset** is one map, completely specified: the window it samples, the
full resolved generation parameter set, and the identity of both.

Before this existed, a map's specification lived in three places that could
not be joined up: ``build_mod.MOD_REGIONS`` held the shipping list,
throwaway modules like ``automation/scand_shift_set.py`` wrote condition
JSON into gitignored ``out/``, and ``out/mod_capture/<run-id>/results.jsonl``
recorded what the engine did - keyed by a *display name* that gets reused
and by an argv list that drifts. So "which parameters produced this
scenario" was answerable only by reading a docstring, and "give me a report
over these four windows" was not answerable at all, because every report
builder is keyed to one run-id.

A preset fixes that by being the join key:

* ``window_hash`` - sha256 over ``(proj, lon, lat, span_km, north_deg, size,
  players)``. The geographic window and nothing else, so "same place,
  different knobs" is a query.
* ``params_hash`` - sha256 over the **complete resolved parameter set**,
  argparse defaults included (``CLAUDE.md``: a report shows the resolved
  set, not the diff from a baseline). This is what a built ``.rms`` is a
  deterministic function of, so it is the cache key.
* ``id`` - ``<label>-<params_hash[:8]>``.

``name`` is deliberately **not** in ``params_hash``. The map name reaches
the script only as a comment in its header (``rms._HEADER``); the filename
the game lists comes from ``build_mod.shipped_filename``. So a candidate
captured as "Scand shift 10" and shipped as "Scandinavia" is the same
script, and promoting it must not force a 70-second re-anneal to change a
comment.

Generation is deterministic given the parameters and ``src/``:
``analysis.choose_starts`` anneals with a fixed RNG seed (12345). That is
what makes a cached ``.rms`` reusable rather than merely similar - and what
makes ``sha256`` the honest way to say whether the script we are about to
ship is the one that was captured.

Legacy argv
-----------
Reconstructing history means parsing argv written against older CLIs, and
two flags no longer exist. ``normalize_argv`` translates them forward and
says so, rather than letting them fail or - worse - silently resolve to a
different window:

* ``--rotate D`` (grid space, pre-2026-08-16) -> ``--north D-45``. An old
  ``--rotate 45`` is today's ``--north 0``; an old record with *no*
  orientation flag is ``--north -45``, because that was the default then.
  Feeding a grid-space value in as ``north_deg`` builds a truth mask 45
  degrees off and corrupts IoU silently.
* ``--spread-islands`` -> ``--spread-starts`` (renamed 2026-08-01).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .projection import SCREEN_TURN

#: Preset JSONs live here, committed. The artifacts they point at (built
#: scripts, captured scenarios) do not: they are reproducible working data
#: under gitignored ``out/``, so this directory is the durable record and
#: every path in it is a *last known location*, checked before it is used.
PRESETS_DIR = "presets"

#: Argparse destinations that describe where output goes, not what is
#: generated. Excluded from ``params_hash``: a preset built with a different
#: ``--outdir`` is the same preset.
OUTPUT_ONLY = frozenset({"name", "outdir", "install", "mod_name",
                         "no_preview", "quiet"})

#: The *inputs* that locate the window, as opposed to the window they
#: resolve to. Also excluded from ``params_hash``, because
#: ``--region scandinavia`` and ``--center=16.0,62.0 --span-km 2000`` are
#: the same map and must hash alike - otherwise editing a preset into its
#: explicit form, which reconstruction does constantly, reads as a new map
#: and throws away a cached build that is byte-identical. The resolved
#: values are hashed instead, under ``_window``.
LOCATION_INPUTS = frozenset({"region", "center", "span_km", "size"})

#: The orientation default before 2026-08-16 (``d56001a``), in today's
#: screen-space terms. An argv from before then that says nothing about
#: orientation meant this, not 0.
LEGACY_NORTH = -SCREEN_TURN


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str) -> str:
    """kebab-case, safe as a filename and as a CLI token."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


# --------------------------------------------------------------------------
# argv normalization
# --------------------------------------------------------------------------

def normalize_argv(argv: list[str], *, legacy_default_north: bool = False
                   ) -> tuple[list[str], list[str]]:
    """Translate a historical argv onto today's CLI.

    Returns ``(argv, notes)``; ``notes`` names every substitution so a
    reconstructed preset can say how it was derived instead of pretending it
    was always spelled this way.

    ``legacy_default_north`` is for argv written before 2026-08-16 that
    carries no orientation flag at all: those maps generated at north -45,
    and defaulting them to today's 0 would silently rotate the window.
    Callers that know the record's vintage (a capture row storing ``rotate``
    rather than ``north_deg``, for instance) pass True.
    """
    out: list[str] = []
    notes: list[str] = []
    said_orientation = False
    it = iter(argv)
    for tok in it:
        if tok == "--rotate" or tok.startswith("--rotate="):
            raw = tok.split("=", 1)[1] if "=" in tok else next(it)
            north = float(raw) - SCREEN_TURN
            out += ["--north", f"{north:g}"]
            notes.append(f"--rotate {raw} (grid space) -> --north {north:g}")
            said_orientation = True
        elif tok == "--spread-islands":
            out.append("--spread-starts")
            notes.append("--spread-islands -> --spread-starts (renamed 2026-08-01)")
        else:
            if tok == "--north" or tok.startswith("--north="):
                said_orientation = True
            out.append(tok)
    if legacy_default_north and not said_orientation:
        out += ["--north", f"{LEGACY_NORTH:g}"]
        notes.append(f"no orientation flag in a pre-2026-08-16 record -> "
                     f"--north {LEGACY_NORTH:g} (the default then)")
    return out, notes


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def resolve(name: str, argv: list[str]) -> tuple[dict, dict]:
    """``(window, params)`` for ``rwmaps <name> <argv...>``.

    Resolved by the CLI's own parser, so the parameter set is exactly what a
    generation would use - including every default - and cannot drift from
    it. Imported lazily: ``cli`` pulls in the whole generation stack, and
    this module is also used by tooling that only wants to read the
    registry.
    """
    from . import cli

    args = cli.build_parser().parse_args([name, *argv])

    if args.region:
        lon, lat, span = cli.REGIONS[args.region]
    else:
        if not args.center or args.span_km is None:
            raise ValueError("need --region, or both --center and --span-km: "
                             f"{argv}")
        lon = lat = span = None
    if args.center:
        lon, lat = (float(v) for v in args.center.split(","))
    if args.span_km is not None:
        span = args.span_km
    size, lobby = cli.size_for_players(args.players)
    if args.size:
        size = args.size
        lobby = cli.LOBBY_SIZES.get(size, f"NOT SELECTABLE ({size})")

    window = {
        "proj": args.proj,
        "lon": round(float(lon), 6),
        "lat": round(float(lat), 6),
        "span_km": float(span),
        "north_deg": float(args.north),
        "size": int(size),
        "players": int(args.players),
    }
    params = {k: (str(v) if isinstance(v, Path) else v)
              for k, v in sorted(vars(args).items())
              if k not in OUTPUT_ONLY and k not in LOCATION_INPUTS}
    params.update({"_window": window, "_lobby_size": lobby})
    return window, params


# --------------------------------------------------------------------------
# the preset record
# --------------------------------------------------------------------------

@dataclass
class Build:
    """A built ``.rms``, content-addressed.

    ``paths`` is every place a copy was last seen - the build cache, the
    shipped mod, a capture run's ``scripts/`` dir, a committed report data
    dir. They are checked and re-hashed before use: under an index-only
    storage policy the registry cannot promise a path still exists, only
    that a file with this ``sha256`` is what was captured.
    """
    sha256: str
    bytes: int
    src_commit: str = "unknown"
    built_utc: str = ""
    paths: list[str] = field(default_factory=list)
    command: str = ""
    #: rwmaps' own report on the script it built: land %, coastline IoU,
    #: starts placed, ai_info_map_type.
    summary: dict = field(default_factory=dict)


@dataclass
class Capture:
    """One engine capture of a build: N samples under one run-id."""
    run_id: str
    n_samples: int
    #: The display name this run filed the samples under. Not always the
    #: preset's current name: a run captures under whatever name the
    #: condition set used, and promotion renames. results.jsonl is keyed by
    #: it, so a reader needs it to find the rows again.
    region: str = ""
    captured_utc: str = ""
    #: When the run started, in the local time it was started in (from the
    #: run_start event). Kept beside the UTC stamp because a session that
    #: ran late in the evening Pacific lands on the next UTC day, and a
    #: history that files it a day later than the work does not match
    #: anything a person remembers.
    started_local: str = ""
    commit: str = "unknown"
    #: How ``commit`` was established. Runs before ``runlog`` (2026-08-17)
    #: recorded none of their own, so theirs is HEAD-at-the-date - a weaker
    #: claim, and one that has to say it is weaker.
    commit_source: str = "unknown"
    results: str = ""          # results.jsonl this was read from
    report: str = ""           # reports/ HTML that presented it, if any
    #: Per-sample summary, copied out of results.jsonl rather than pointed
    #: at: the scenarios live in gitignored out/ and the fairness numbers
    #: are the part that must outlive them.
    samples: list[dict] = field(default_factory=list)
    #: Last known .aoe2scenario paths, index-only - same caveat as Build.paths.
    scenarios: list[str] = field(default_factory=list)


@dataclass
class Preset:
    label: str
    name: str
    argv: list[str]
    window: dict
    params: dict
    window_hash: str
    params_hash: str
    #: candidate - captured or not, not shipped. shipped - in the mod.
    #: retired - shipped once, withdrawn; kept because the *reason* is
    #: evidence (see build_mod's retired notes).
    status: str = "candidate"
    note: str = ""
    origin: dict = field(default_factory=dict)
    builds: list[Build] = field(default_factory=list)
    captures: list[Capture] = field(default_factory=list)
    legacy_notes: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.label}-{self.params_hash[:8]}"

    @property
    def n_captured(self) -> int:
        return sum(c.n_samples for c in self.captures)

    @property
    def command(self) -> str:
        return " ".join(["uv run rwmaps", f'"{self.name}"', *self.argv])

    def describe_window(self) -> str:
        w = self.window
        return (f"{w['lat']:.4g},{w['lon']:.4g} span {w['span_km']:g} km "
                f"north {w['north_deg']:g} {w['size']}x{w['size']} "
                f"{w['players']}p {w['proj']}")

    # -- persistence -------------------------------------------------------

    def to_json(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Preset":
        d = dict(d)
        d.pop("id", None)
        d["builds"] = [Build(**b) for b in d.get("builds", [])]
        d["captures"] = [Capture(**c) for c in d.get("captures", [])]
        return cls(**d)

    @classmethod
    def create(cls, label: str, name: str, argv: list[str], *,
               legacy_default_north: bool = False, **kw) -> "Preset":
        argv, notes = normalize_argv(list(argv),
                                     legacy_default_north=legacy_default_north)
        window, params = resolve(name, argv)
        return cls(label=slug(label), name=name, argv=argv, window=window,
                   params=params, window_hash=_hash(window),
                   params_hash=_hash(params), legacy_notes=notes, **kw)

    # -- artifacts ---------------------------------------------------------

    def find_build(self, repo: Path) -> tuple[Build, Path] | None:
        """The first recorded build with a copy still on disk that still
        hashes to what was recorded, or None.

        Re-hashing is the point. A path under ``out/`` can be overwritten by
        the next run of the harness that wrote it, and shipping a script
        that merely *sits where the captured one sat* is how a report ends
        up describing one map under another's name.
        """
        for build in self.builds:
            for rel in build.paths:
                p = (repo / rel) if not Path(rel).is_absolute() else Path(rel)
                if p.is_file() and sha256_file(p) == build.sha256:
                    return build, p
        return None

    def record_build(self, build: Build) -> Build:
        """Merge ``build`` into ``builds`` by sha256, unioning paths."""
        for existing in self.builds:
            if existing.sha256 == build.sha256:
                for p in build.paths:
                    if p not in existing.paths:
                        existing.paths.append(p)
                existing.src_commit = existing.src_commit if existing.src_commit != "unknown" else build.src_commit
                existing.built_utc = existing.built_utc or build.built_utc
                existing.command = existing.command or build.command
                existing.summary = existing.summary or build.summary
                return existing
        self.builds.append(build)
        return build

    def record_capture(self, capture: Capture) -> Capture:
        """Merge by run-id: a resumed run appends samples to its own record."""
        for existing in self.captures:
            if existing.run_id == capture.run_id:
                have = {s.get("sample_index") for s in existing.samples}
                for s in capture.samples:
                    if s.get("sample_index") not in have:
                        existing.samples.append(s)
                existing.samples.sort(key=lambda s: s.get("sample_index", 0))
                existing.n_samples = len(existing.samples)
                for p in capture.scenarios:
                    if p not in existing.scenarios:
                        existing.scenarios.append(p)
                existing.captured_utc = max(existing.captured_utc,
                                            capture.captured_utc)
                existing.report = existing.report or capture.report
                existing.region = existing.region or capture.region
                existing.started_local = (existing.started_local
                                          or capture.started_local)
                if existing.commit_source == "unknown":
                    existing.commit_source = capture.commit_source
                return existing
        self.captures.append(capture)
        return capture


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

class Registry:
    """``presets/*.json``, one file per preset, keyed by label."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / PRESETS_DIR
        self.presets: dict[str, Preset] = {}

    def load(self) -> "Registry":
        self.presets = {}
        if self.dir.is_dir():
            for path in sorted(self.dir.glob("*.json")):
                p = Preset.from_json(json.loads(path.read_text(encoding="utf-8")))
                self.presets[p.label] = p
        return self

    def path_for(self, preset: Preset) -> Path:
        return self.dir / f"{preset.label}.json"

    def save(self, preset: Preset) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(preset)
        path.write_text(json.dumps(preset.to_json(), indent=1) + "\n",
                        encoding="utf-8")
        self.presets[preset.label] = preset
        return path

    def save_all(self) -> None:
        for p in list(self.presets.values()):
            self.save(p)

    # -- lookup ------------------------------------------------------------

    def add(self, preset: Preset) -> Preset:
        """Insert, or merge into an existing preset with the same params.

        Same ``params_hash`` under a different label is the *same map*
        (reconstruction hits this constantly: a window screened as "Scand
        shallows" and shipped as "Scandinavia"). Merging keeps one record
        with both names rather than two records that have to be noticed.
        """
        for existing in self.presets.values():
            if existing.params_hash == preset.params_hash:
                for b in preset.builds:
                    existing.record_build(b)
                for c in preset.captures:
                    existing.record_capture(c)
                aka = existing.origin.setdefault("also_known_as", [])
                for alias in [preset.name, *preset.origin.get("also_known_as", [])]:
                    if alias != existing.name and alias not in aka:
                        aka.append(alias)
                existing.note = existing.note or preset.note
                return existing
        if preset.label in self.presets:
            preset.label = f"{preset.label}-{preset.params_hash[:4]}"
        self.presets[preset.label] = preset
        return preset

    def get(self, key: str) -> Preset:
        """By label, id, ``name``, or an unambiguous prefix of any of those."""
        by = self.presets
        if key in by:
            return by[key]
        k = key.lower()
        exact = [p for p in by.values()
                 if k in (p.id.lower(), p.name.lower(), p.label.lower())]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise KeyError(f"{key!r} matches {len(exact)} presets: "
                           + ", ".join(p.label for p in exact))
        pre = [p for p in by.values()
               if p.label.lower().startswith(k) or p.id.lower().startswith(k)]
        if len(pre) == 1:
            return pre[0]
        if not pre:
            raise KeyError(f"no preset matches {key!r}")
        raise KeyError(f"{key!r} is ambiguous: " + ", ".join(p.label for p in pre))

    def select(self, keys: list[str] | None = None, *, status: str | None = None
               ) -> list[Preset]:
        if keys:
            return [self.get(k) for k in keys]
        out = [p for p in self.presets.values()
               if status is None or p.status == status]
        return sorted(out, key=lambda p: p.label)

    def by_window(self, window_hash: str) -> list[Preset]:
        return sorted((p for p in self.presets.values()
                       if p.window_hash == window_hash),
                      key=lambda p: p.label)
