"""Reconstruct the preset registry from what is already on disk.

Nothing here invents a parameter set. Every preset is read out of a record
this project already wrote:

* **shipped** - ``MOD_REGIONS_AT_IMPORT`` below, the hand-edited list
  ``build_mod.MOD_REGIONS`` held until 2026-08-19. It really was the
  parameter record for the 10 shipped maps; what it lacked was a date, a
  commit and a link to the captures. Frozen here as an import fixture -
  ``update_mod.py`` reads ``status: shipped`` out of the registry now.
* **retired** - the argv the retired regions were actually captured with,
  read off ``results.jsonl`` (``update_mod.RETIRED_REGIONS`` names them but
  no longer carries their args).
* **candidate** - the condition sets: ``automation/candidate_set.py`` and
  every ``out/*_set.json`` a session left behind.
* **screened** - ``automation/window_candidates.py``, windows judged on
  paper and never built. Recorded so "when did we look at this window, and
  in which report" has an answer.

Then it attaches artifacts, by *provenance rather than by guessing*:

* a capture run's ``out/mod_capture/<run>/scripts/<region>/`` holds the
  exact script that run captured, so it attaches as a build of the preset
  that run's rows resolve to;
* ``out/mod_capture/<run>/<region>/raw/sample_NNN.aoe2scenario`` and the
  committed ``reports/*_data*/`` copies attach as that capture's scenarios;
* ``mod/<MOD_NAME>/.../RW <name>.rms`` attaches to the shipped preset.

Every build is content-addressed (sha256) and every path is a *last known
location*. Under this project's index-only storage policy the registry is
committed and the artifacts are not, so a path is a lead to check, never a
promise - and re-hashing is what turns "a script sits where the captured one
sat" into "this is the script that was captured".

Idempotent: run it again after a capture pass and it merges. Same-parameter
presets under different display names collapse into one record with an
``also_known_as`` list, which is what makes a candidate captured as "Scand
shift 10" and shipped as "Scandinavia" one map rather than two.

Usage:
    uv run python automation/preset_import.py            # write presets/
    uv run python automation/preset_import.py --dry-run  # say what it would do
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from rwmaps.presets import (Build, Capture, Preset, Registry,  # noqa: E402
                            sha256_file, utc_now)

CAPTURE_ROOT = REPO / "out" / "mod_capture"
LATENCY_ROOT = REPO / "out" / "gen_latency"
REPORTS = REPO / "reports"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def git_lines(*args: str) -> list[str]:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []


def file_history(path: Path) -> dict:
    """First and last commit to touch ``path``, with dates.

    The closest thing on record to "when was this script built and by which
    version of src/": the commit that last wrote the file. Not the same
    claim as a build stamp, so it is stored under its own name.
    """
    lines = git_lines("log", "--follow", "--date=short",
                      "--format=%h|%ad", "--", rel(path))
    if not lines:
        return {}
    last_h, last_d = lines[0].split("|")
    first_h, first_d = lines[-1].split("|")
    return {"last_commit": last_h, "last_commit_date": last_d,
            "first_commit": first_h, "first_commit_date": first_d}


def commit_at(iso_utc: str) -> str:
    """The commit HEAD was at on a given date - for runs that predate
    ``runlog`` (added 2026-08-17) and so recorded no commit of their own."""
    lines = git_lines("rev-list", "-1", f"--before={iso_utc}", "HEAD",
                      "--format=%h")
    for l in lines:
        if not l.startswith("commit "):
            return l.strip()
    return "unknown"


# --------------------------------------------------------------------------
# results.jsonl -> a compact per-sample record
# --------------------------------------------------------------------------

#: The resource kinds the balance model counts. Kept in this order because
#: it is the order every report in reports/ prints them in.
KINDS = ("gold", "stone", "forage", "sheep", "deer", "boar", "small_game")


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 2)


def summarize_row(row: dict) -> dict:
    """One capture sample, compressed to what has to outlive ``out/``.

    Under index-only storage the ``.aoe2scenario`` and the full per-player
    fairness profile stay in gitignored working data, so this carries the
    part a later decision actually reads: the geometry facts, and per kind
    the median and worst-off player within ``OWNERSHIP_RADIUS``. Medians and
    minima per player, not map totals - "the unit is a player, not a map".

    Facts only. No score, no verdict (``CLAUDE.md``).
    """
    aes = row.get("aesthetic") or {}
    place = row.get("placement") or {}
    fair = row.get("fairness") or {}
    per = fair.get("per_player") or {}

    out = {
        "sample_index": row.get("sample_index"),
        "iou_10m": round(aes["iou_10m"], 4) if "iou_10m" in aes else None,
        "preserved_fraction": aes.get("preserved_fraction"),
        "land_pct": row.get("land_pct"),
        "n_tcs": row.get("n_tcs"),
        "min_tc_separation": place.get("min_tc_separation"),
        "landmasses_with_a_player": place.get("n_landmasses_with_a_player"),
        "reachable_fraction": place.get("pairwise_land_reachable_fraction"),
        "ai_map_type": row.get("ai_map_type"),
    }
    if fair:
        counts = {k: [p["counts"].get(k, 0) for p in per.values()] for k in KINDS}
        # Absent, not zero. Per-player land landed in the model on
        # 2026-08-16, so every capture before that has no land figure at
        # all - and defaulting it to 0 put a hard "this map gives a player
        # no land" into the record for maps whose land was simply never
        # measured. It then dragged the median of a mixed-vintage preset to
        # zero, which is the one number a land comparison must not invent.
        land = [v for v in ((p.get("land") or {}).get("land_exclusive")
                            for p in per.values()) if v is not None]
        wood = [v for v in ((p.get("wood") or {}).get("forest_exclusive")
                            for p in per.values()) if v is not None]
        out["fairness"] = {
            "ownership_radius": fair.get("ownership_radius"),
            "median": {k: _median(v) for k, v in counts.items()},
            "min": {k: (min(v) if v else None) for k, v in counts.items()},
            "players_without_any": {
                k: (fair.get("spread", {}).get(k, {}) or {}).get("n_players_without_any")
                for k in KINDS},
            "land_exclusive_median": _median(land) if land else None,
            "land_exclusive_min": min(land) if land else None,
            "wood_exclusive_median": _median(wood) if wood else None,
            "neutral_total": fair.get("neutral_total"),
            "forest_share_of_land": (fair.get("forest") or {}).get("share_of_land"),
        }
    return out


def north_of(row: dict) -> float:
    """A row's orientation in screen space, whichever era wrote it."""
    from rwmaps.projection import north_from_legacy_rotate
    if row.get("north_deg") is not None:
        return float(row["north_deg"])
    return north_from_legacy_rotate(float(row.get("rotate") or 0.0))


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------


#: The hand-edited shipping list ``build_mod.MOD_REGIONS`` held until
#: 2026-08-19, frozen here as the fixture the registry was reconstructed
#: from. **Nothing reads it to decide what ships** - ``update_mod.py`` reads
#: ``status: shipped`` out of ``presets/`` now. It stays so that deleting
#: ``presets/`` and re-importing reproduces the same 10 shipped presets
#: rather than silently producing none, and so the exact list that was
#: shipping on the day of the switch is on the record.
#:
#: Verbatim, including the two constants it composed:
#: FOREST_SPLIT = --forest-clumps 36 --forest-alt PINE_FOREST --forest-spacing 3
#: NW = --north -45 ; NORTH_UP = --north 0
MOD_REGIONS_AT_IMPORT = [
    ("Salish Sea", ["--center=-122.9,48.15", "--span-km", "260", "--overlap",
                    "0.85", "--min-water-width", "5", "--min-land-width", "3",
                    "--north", "-45"]),
    ("Cramped Italy", ["--region", "italy", "--north", "-45"]),
    ("Italy", ["--region", "italy", "--spread-starts", "--north", "-45"]),
    ("Britain", ["--region", "britain", "--forest-clumps", "36",
                 "--forest-alt", "PINE_FOREST", "--forest-spacing", "3",
                 "--north", "-45"]),
    ("Greece", ["--region", "greece", "--forest-clumps", "36", "--forest-alt",
                "PINE_FOREST", "--forest-spacing", "3", "--forest-percent",
                "14", "--north", "-45"]),
    ("Chesapeake Bay", ["--region", "chesapeake", "--north", "-45"]),
    ("Black Sea", ["--region", "blacksea", "--north", "-45"]),
    ("Scandinavia", ["--region", "scandinavia", "--north", "-45"]),
    ("Michigan", ["--center=-85.0,44.5", "--span-km", "1200", "--overlap",
                  "0.85", "--min-water-width", "5", "--min-land-width", "3",
                  "--north", "0"]),
    ("Great Britain N", ["--center=-3.0,52.5", "--span-km", "1300",
                         "--forest-clumps", "36", "--forest-alt",
                         "PINE_FOREST", "--forest-spacing", "3",
                         "--north", "0"]),
]

#: Per-region notes transcribed from the comments MOD_REGIONS carried, so
#: the reasoning travels with the preset instead of with the list. Only
#: applied to a preset that has no note yet.
SHIPPED_NOTES = {
    "Salish Sea": "the original hand-verified data point. Overrides "
        "consolidation width to victoria_recenter's verified 5/3 (cell "
        "0a8509cf) rather than the 4/3 default.",
    "Italy": "--spread-starts, so players spread across Sardinia/Corsica/"
        "Tunisia and the peninsula instead of only the mainland's far "
        "corners - see MOD_STATUS.md for the crowding investigation.",
    "Cramped Italy": "all 8 players crowded onto the single connected "
        "mainland/France/Balkans landmass - the original, unmodified "
        "behaviour, shipped alongside Italy rather than replaced by it.",
    "Britain": "forest split across two terrain types so it stops fusing "
        "into one mass (FOREST_SPLIT). Britain pays no wood for it.",
    "Greece": "FOREST_SPLIT plus --forest-percent 14, because the second "
        "forest block under-places; nets out at 23% wood against 25%.",
    "Michigan": "\"GL Michigan-Huron\" in the 2026-08-16 candidate report, "
        "renamed on request. Salish Sea's consolidation widths (5/3). "
        "Measured there: 78.8% land, 8 TCs, IoU 0.892 - the highest IoU of "
        "the Great Lakes candidates. No N=10 pass yet.",
    "Great Britain N": "\"Britain northup France\" in the 2026-08-16 "
        "candidate report: the shipped Britain window at the same 1300 km "
        "span, centre 2 degrees south, trading open sea for enough "
        "continent to hold two TCs. Water north of Britain drops 45 -> 16 "
        "tiles while staying open; Ireland keeps 32 tiles of clearance; the "
        "Channel stays 8 wide; the continental patch grows 6,041 -> 10,809 "
        "tiles. Capture: 35.2% land, 8 TCs, IoU 0.848. No N=10 pass yet.",
}


def shipped_presets() -> list[Preset]:
    import update_mod
    out = []
    for name, extra in MOD_REGIONS_AT_IMPORT:
        rms = (REPO / "mod" / update_mod.MOD_NAME / "resources" / "_common"
               / "random-map-scripts" / update_mod.shipped_filename(name))
        origin = {"source": "automation/build_mod.py:MOD_REGIONS",
                  "shipped_filename": update_mod.shipped_filename(name)}
        origin.update(file_history(rms))
        p = Preset.create(name, name, extra, status="shipped", origin=origin,
                          note=SHIPPED_NOTES.get(name, ""))
        if rms.is_file():
            p.record_build(Build(
                sha256=sha256_file(rms), bytes=rms.stat().st_size,
                src_commit=origin.get("last_commit", "unknown"),
                built_utc=mtime_utc(rms), paths=[rel(rms)],
                command=f"uv run rwmaps {name!r} " + " ".join(extra),
                summary={"note": "the copy that ships, committed in mod/"}))
        out.append(p)
    return out


def retired_presets(rows_by_region: dict[str, list[tuple[str, dict]]]) -> list[Preset]:
    """The three regions dropped 2026-08-15, with the argv they were
    captured with - MOD_REGIONS no longer carries it."""
    import update_mod
    out = []
    for name in update_mod.RETIRED_REGIONS:
        rows = rows_by_region.get(name) or []
        if not rows:
            continue
        _run, row = rows[0]
        argv = list(row.get("extra_args") or [])
        legacy = row.get("north_deg") is None
        out.append(Preset.create(
            name, name, argv, status="retired", legacy_default_north=legacy,
            note="dropped from the shipped mod 2026-08-15 - see "
                 "update_mod.RETIRED_REGIONS for the supply and land numbers",
            origin={"source": "captured argv (update_mod.RETIRED_REGIONS keeps "
                              "only the names)"}))
    return out


def condition_set_presets() -> list[Preset]:
    """``out/*_set.json`` plus ``candidate_set.CANDIDATES``.

    The JSON files are what ``mod_capture --region-set`` was actually driven
    with, so they are the authoritative argv for those runs; the module is
    included because its list is documented and its JSON may have been
    cleaned up.

    No legacy orientation default is applied to either. Both were rewritten
    onto the screen-space convention when it landed (``d56001a``), so a
    missing ``--north`` here means 0 - today's meaning. Assuming the old
    default instead silently turned "Britain north-up" into the shipped
    north -45 window and merged the two records, which is the whole reason
    that guess has to be made per source rather than globally.
    """
    out = []
    seen_json = set()
    for path in sorted((REPO / "out").glob("*_set.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        seen_json.add(path.name)
        for name, argv in entries:
            out.append(Preset.create(
                name, name, list(argv),
                origin={"source": rel(path), "kind": "condition set",
                        "written": mtime_utc(path)}))
    try:
        import candidate_set
        for name, argv in candidate_set.CANDIDATES:
            out.append(Preset.create(
                name, name, list(argv),
                origin={"source": "automation/candidate_set.py:CANDIDATES",
                        "kind": "condition set"}))
    except Exception as e:
        print(f"  candidate_set.py: skipped ({e})")
    return out


def screened_presets() -> list[Preset]:
    """``window_candidates.CANDIDATES`` - windows drawn and judged on paper.

    A screened window has no build and no capture. It is in the registry so
    the *decision record* is complete: this window was looked at, on this
    date, in this report, alongside these others.
    """
    import window_candidates as wc
    report = latest_report("window_candidates")
    out = []
    for c in wc.CANDIDATES:
        argv = [f"--center={c.lon},{c.lat}", "--span-km", f"{c.span_km:g}",
                "--north", f"{c.north:g}"]
        if c.proj != "laea":
            argv += ["--proj", c.proj]
        for key, flag in (("min_water_width", "--min-water-width"),
                          ("min_land_width", "--min-land-width"),
                          ("min_island_tiles", "--min-island-tiles"),
                          ("resolution", "--resolution"),
                          ("overlap", "--overlap"),
                          ("max_radius", "--max-radius")):
            if key in c.overrides:
                v = c.overrides[key]
                argv += [flag, f"{v:g}" if isinstance(v, float) else str(v)]
        for name in c.presets:
            argv += ["--feature-preset", name]
        for spec in c.features:
            argv += ["--feature", spec]
        out.append(Preset.create(
            c.name, c.name, argv, status="screened", note=c.note,
            origin={"source": "automation/window_candidates.py:CANDIDATES",
                    "kind": "paper screen", "group": c.group,
                    "report": report}))
    return out


def latest_report(kind: str, run_id: str | None = None) -> str:
    """Newest ``reports/*<kind>*.html`` (optionally for one run-id)."""
    hits = [p for p in REPORTS.glob(f"*{kind}*.html")
            if run_id is None or p.stem.endswith(run_id)]
    return rel(max(hits, key=lambda p: p.name)) if hits else ""


# --------------------------------------------------------------------------
# attaching captures and builds
# --------------------------------------------------------------------------

def read_runs() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """``{run_id: [row, ...]}`` and ``{run_id: run metadata}``."""
    rows: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}
    for results in sorted(CAPTURE_ROOT.glob("*/results.jsonl")):
        run = results.parent.name
        rs = []
        for line in results.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if not rs:
            continue
        rows[run] = rs
        info = {"results": rel(results), "captured_utc": mtime_utc(results),
                "commit": "unknown", "commit_source": "unknown",
                "regen": {}}
        events = results.parent / "events.jsonl"
        if events.is_file():
            for line in events.open(encoding="utf-8"):
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("kind") == "run_start":
                    info["commit"] = ev.get("commit", "unknown")
                    info["commit_source"] = "run_start event"
                    info["started"] = ev.get("t", "")
                    info["command"] = ev.get("command", "")
                elif ev.get("kind") == "regen" and ev.get("region"):
                    info["regen"][ev["region"]] = {
                        "command": ev.get("command", ""),
                        "stdout": ev.get("stdout", ""),
                    }
        if info["commit"] == "unknown":
            info["commit"] = commit_at(info["captured_utc"])
            info["commit_source"] = "HEAD at the run's date (pre-runlog run)"
        meta[run] = info
    return rows, meta


_LAND_IOU = re.compile(r"land ([\d.]+)%\s+coastline IoU ([\d.]+)")
_STARTS = re.compile(r"(\d+) starts, min separation ([\d.]+), verdict (\S+)")


def parse_rwmaps_stdout(text: str) -> dict:
    """rwmaps' own report on the script it built: the Python-side prediction
    the engine capture is then measured against."""
    out = {}
    if m := _LAND_IOU.search(text or ""):
        out["land_pct"] = float(m.group(1))
        out["script_iou"] = float(m.group(2))
    if m := _STARTS.search(text or ""):
        out["starts"] = int(m.group(1))
        out["min_separation"] = float(m.group(2))
        out["verdict"] = m.group(3)
    return out


def scripts_for(run: str, region: str) -> list[Path]:
    d = CAPTURE_ROOT / run / "scripts" / region
    return sorted(d.rglob("*.rms")) if d.is_dir() else []


def scenarios_for(run: str, region: str) -> dict[int, Path]:
    """``{sample_index: path}`` for a run's archived captures, preferring a
    committed ``reports/*_data*/`` copy over the one in ``out/``."""
    found: dict[int, Path] = {}
    raw = CAPTURE_ROOT / run / region / "raw"
    if raw.is_dir():
        for p in sorted(raw.glob("sample_*.aoe2scenario")):
            m = re.search(r"sample_(\d+)", p.stem)
            if m:
                found[int(m.group(1))] = p
    for data_dir in REPORTS.glob(f"*_data*{run}"):
        region_dir = data_dir / region
        if not region_dir.is_dir():
            continue
        for p in sorted(region_dir.glob("*.aoe2scenario")):
            m = re.search(r"s(\d+)$", p.stem)
            if m:
                found[int(m.group(1))] = p     # committed copy wins
    return found


def report_for(run: str) -> str:
    hits = [p for p in REPORTS.glob("*.html") if p.stem.endswith(run)]
    return rel(max(hits, key=lambda p: p.name)) if hits else ""


def capture_presets(rows: dict[str, list[dict]], meta: dict[str, dict]
                    ) -> list[Preset]:
    """A preset per (run, region, argv) group, carrying that group's capture
    and the build it was captured from."""
    out = []
    for run, rs in rows.items():
        info = meta[run]
        groups: dict[tuple, list[dict]] = {}
        for row in rs:
            key = (row["region"], tuple(row.get("extra_args") or []),
                   north_of(row))
            groups.setdefault(key, []).append(row)
        for (region, argv, _north), grp in groups.items():
            legacy = grp[0].get("north_deg") is None
            preset = Preset.create(
                region, region, list(argv), legacy_default_north=legacy,
                origin={"source": f"{info['results']} (run {run})",
                        "kind": "capture"})
            scen = scenarios_for(run, region)
            cap = Capture(
                run_id=run, region=region, n_samples=len(grp),
                captured_utc=info["captured_utc"], commit=info["commit"],
                started_local=info.get("started", ""),
                results=info["results"], report=report_for(run),
                samples=[summarize_row(r) for r in
                         sorted(grp, key=lambda r: r.get("sample_index") or 0)],
                scenarios=[rel(scen[i]) for i in sorted(scen)
                           if i in {r.get("sample_index") for r in grp}],
                commit_source=info["commit_source"],
            )
            preset.record_capture(cap)
            for rms in scripts_for(run, region):
                regen = info["regen"].get(region, {})
                preset.record_build(Build(
                    sha256=sha256_file(rms), bytes=rms.stat().st_size,
                    src_commit=info["commit"], built_utc=mtime_utc(rms),
                    paths=[rel(rms)], command=regen.get("command", ""),
                    summary=parse_rwmaps_stdout(regen.get("stdout", ""))))
            for data_dir in REPORTS.glob(f"*_data*{run}"):
                if not (data_dir / region).is_dir():
                    continue
                for rms in (data_dir / region).glob("*.rms"):
                    preset.record_build(Build(
                        sha256=sha256_file(rms), bytes=rms.stat().st_size,
                        src_commit=info["commit"], built_utc=mtime_utc(rms),
                        paths=[rel(rms)],
                        summary={"note": "committed copy, archived beside the "
                                         "report that presented it"}))
            out.append(preset)
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def attach_cache(reg: Registry) -> int:
    """Attach anything sitting in ``out/rms_cache/<preset id>/``.

    update_mod's cache is named by preset id, so a rebuilt registry can find
    its builds again instead of regenerating them. The sha256 is recomputed
    here, not trusted from the name: the point of the cache is that the
    script it holds is the one that was measured.
    """
    cache = REPO / "out" / "rms_cache"
    if not cache.is_dir():
        return 0
    by_id = {p.id: p for p in reg.presets.values()}
    n = 0
    for d in sorted(cache.iterdir()):
        preset = by_id.get(d.name)
        if preset is None or not d.is_dir():
            continue
        for rms in sorted(d.glob("*.rms")):
            preset.record_build(Build(
                sha256=sha256_file(rms), bytes=rms.stat().st_size,
                built_utc=mtime_utc(rms), paths=[rel(rms)],
                summary={"note": "build cache (out/rms_cache), written by "
                                 "update_mod"}))
            n += 1
    return n


def attach_gen_latency(reg: Registry) -> int:
    """Attach ``out/gen_latency/<run>/`` passes to the presets they ran.

    A latency pass is a capture pass with a narrow question - it generates
    real maps in the real engine - so leaving it out of the registry is
    exactly the "folder nobody can join back to a parameter set" this
    reconstruction existed to end. It records no argv, though, and it can
    run scripts that are not in the mod (``--extra-dir``), so the join is by
    **provenance**: the plan event names the absolute path of every script
    the pass ran, and that file's sha256 is matched against the builds
    already on record. A script nothing recognises is skipped rather than
    guessed at.

    ``--keep-scenarios`` passes also carry the orientation each sample
    rolled, if ``rot_orientation.py`` has been run over them.
    """
    if not LATENCY_ROOT.is_dir():
        return 0
    by_sha: dict[str, object] = {}
    for preset in reg.presets.values():
        for build in preset.builds:
            by_sha.setdefault(build.sha256, preset)

    n = 0
    for run_dir in sorted(LATENCY_ROOT.iterdir()):
        results = run_dir / "results.jsonl"
        events = run_dir / "events.jsonl"
        if not results.is_file() or not events.is_file():
            continue
        plan = commit = started = None
        for line in events.open(encoding="utf-8"):
            ev = json.loads(line)
            if ev.get("kind") == "plan":
                plan = ev
            if ev.get("commit"):
                commit = commit or ev["commit"]
            if ev.get("kind") == "run_start":
                started = ev.get("local") or ev.get("started_local") or started
        if not plan:
            continue
        # map label -> the script that label actually ran, hashed now.
        script_sha: dict[str, str] = {}
        for m in plan.get("maps", []):
            q = Path(m["path"])
            if q.is_file():
                script_sha[m["map"]] = sha256_file(q)

        turns = {}
        orient = run_dir / "orientation.json"
        if orient.is_file():
            for row in json.loads(orient.read_text(encoding="utf-8")):
                turns[(row["map"], row["round"])] = row

        rows = [json.loads(l) for l in results.open(encoding="utf-8")]
        by_map: dict[str, list[dict]] = {}
        for row in rows:
            by_map.setdefault(row["map"], []).append(row)

        for label, samples in sorted(by_map.items()):
            preset = by_sha.get(script_sha.get(label, ""))
            if preset is None:
                continue
            scen = run_dir / "scenarios"
            out_samples = []
            paths = []
            for row in samples:
                rec = {"sample_index": row["round"],
                       "generate_s": row["generate_s"],
                       "save_s": row["save_s"],
                       "verified": row["verified"]}
                hit = turns.get((label, row["round"]))
                if hit:
                    rec["orientation_turn"] = hit["turn"]
                    rec["orientation_iou"] = hit["iou"]
                    rec["orientation_margin"] = hit["margin"]
                out_samples.append(rec)
                if row.get("scenario") and (scen / row["scenario"]).is_file():
                    paths.append(rel(scen / row["scenario"]))
            preset.record_capture(Capture(
                run_id=run_dir.name, n_samples=len(out_samples), region=label,
                captured_utc=mtime_utc(results), started_local=started or "",
                commit=commit or "unknown",
                commit_source="runlog" if commit else "unknown",
                results=rel(results), samples=out_samples, scenarios=paths))
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, write nothing")
    ap.add_argument("--no-screened", action="store_true",
                    help="skip window_candidates.py's paper screens")
    args = ap.parse_args()

    reg = Registry(REPO).load()
    before = len(reg.presets)
    rows, meta = read_runs()
    rows_by_region: dict[str, list[tuple[str, dict]]] = {}
    for run, rs in rows.items():
        for row in rs:
            rows_by_region.setdefault(row["region"], []).append((run, row))

    print(f"registry: {before} presets before")
    print(f"captures: {sum(len(v) for v in rows.values())} rows across "
          f"{len(rows)} runs")

    # Order matters. Shipped first, so a merged record keeps the shipped
    # name and status; screened last, because a paper screen that later got
    # built and captured should read as that build's record, not as a screen.
    batches = [("shipped", shipped_presets()),
               ("retired", retired_presets(rows_by_region)),
               ("captured", capture_presets(rows, meta)),
               ("condition sets", condition_set_presets())]
    if not args.no_screened:
        batches.append(("screened", screened_presets()))

    for label, batch in batches:
        merged = new = 0
        for p in batch:
            existing = reg.presets.get(p.label)
            hit = any(q.params_hash == p.params_hash for q in reg.presets.values())
            reg.add(p)
            if hit or existing:
                merged += 1
            else:
                new += 1
        print(f"  {label:14s} {len(batch):4d} records -> {new} new, {merged} merged")

    n_cache = attach_cache(reg)
    if n_cache:
        print(f"  build cache     {n_cache} scripts in out/rms_cache attached")

    n_lat = attach_gen_latency(reg)
    if n_lat:
        print(f"  gen_latency     {n_lat} (run, map) passes attached by "
              f"script sha256")

    n_built = sum(1 for p in reg.presets.values() if p.builds)
    n_cap = sum(1 for p in reg.presets.values() if p.captures)
    print(f"registry: {len(reg.presets)} presets "
          f"({n_built} with a build on record, {n_cap} captured)")
    for status in ("shipped", "retired", "candidate", "screened"):
        n = sum(1 for p in reg.presets.values() if p.status == status)
        print(f"  {status:10s} {n}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    for p in reg.presets.values():
        p.origin.setdefault("imported", utc_now())
    reg.save_all()
    print(f"\nwrote {len(reg.presets)} files to {rel(reg.dir)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
