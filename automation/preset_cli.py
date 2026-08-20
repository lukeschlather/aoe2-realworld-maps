"""Read and edit the preset registry - the one place a map's window,
parameters, builds and captures are joined up.

    uv run python automation/preset_cli.py list
    uv run python automation/preset_cli.py list --status shipped
    uv run python automation/preset_cli.py show scand-shift-10
    uv run python automation/preset_cli.py window scand-shift-10
    uv run python automation/preset_cli.py audit
    uv run python automation/preset_cli.py new scand-south "Scandinavia" -- \\
        --center=21.58,63.0 --span-km 2000 --min-water-width 2 --north -45
    uv run python automation/preset_cli.py region-set scand-shift-10 scand-shallows \\
        -o out/scand_pick.json
    uv run python automation/preset_cli.py promote scand-shift-10 --name Scandinavia

``promote`` is the whole point of the registry: it flips a status, and
``build_mod.py`` then ships that preset from a build it already has -
byte-identical to the script the engine was measured on, if one is still on
disk. Nothing regenerates and no annealing runs.

``audit`` answers the question the reconstruction raised and no report had
been asking: is the script that ships the script that was captured? It is a
hash comparison, not a judgement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from rwmaps.presets import Preset, Registry, sha256_file, utc_now  # noqa: E402

STATUS_ORDER = {"shipped": 0, "candidate": 1, "screened": 2, "retired": 3}


def load() -> Registry:
    return Registry(REPO).load()


def _exists(path_str: str) -> Path | None:
    p = Path(path_str)
    p = p if p.is_absolute() else REPO / p
    return p if p.is_file() else None


def body_hash(path: Path) -> str:
    """sha256 of a script with its header title line removed.

    The map name reaches a script only as the first line of its header
    comment, so two scripts that differ there and nowhere else are the same
    map: shipped "Great Britain N" is exactly the candidate captured as
    "Britain northup France" with that one line rewritten (diffed
    2026-08-19). Without this the audit reports "not the captured script"
    for a rename, which is a true statement about bytes and a false one
    about evidence.
    """
    import hashlib
    text = path.read_text(encoding="ascii", errors="replace").splitlines(True)
    return hashlib.sha256("".join(text[1:]).encode()).hexdigest()


def last_capture(p: Preset) -> str:
    dates = [c.captured_utc[:10] for c in p.captures if c.captured_utc]
    return max(dates) if dates else "-"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_list(args) -> int:
    reg = load()
    presets = reg.select(args.presets or None, status=args.status)
    if args.window:
        presets = [p for p in presets if p.window_hash.startswith(args.window)]
    if args.captured:
        presets = [p for p in presets if p.captures]
    presets.sort(key=lambda p: (STATUS_ORDER.get(p.status, 9), p.label))
    print(f"{'label':30s} {'status':9s} {'window':44s} {'caps':>4s} "
          f"{'runs':>4s} {'blds':>4s}  last")
    for p in presets:
        print(f"{p.label:30s} {p.status:9s} {p.describe_window():44s} "
              f"{p.n_captured:4d} {len(p.captures):4d} {len(p.builds):4d}  "
              f"{last_capture(p)}")
    print(f"\n{len(presets)} presets")
    return 0


def cmd_show(args) -> int:
    reg = load()
    for key in args.presets:
        p = reg.get(key)
        print("=" * 78)
        print(f"{p.id}   [{p.status}]")
        print(f"  name        {p.name}"
              + (f"   (also: {', '.join(p.origin['also_known_as'])})"
                 if p.origin.get("also_known_as") else ""))
        print(f"  window      {p.describe_window()}")
        print(f"  window_hash {p.window_hash[:12]}   params_hash {p.params_hash[:12]}")
        print(f"  command     {p.command}")
        if p.note:
            print(f"  note        {p.note}")
        for k, v in sorted(p.origin.items()):
            print(f"  origin.{k:<12s} {v}")
        for line in p.legacy_notes:
            print(f"  legacy      {line}")

        # The complete resolved parameter set, defaults included - CLAUDE.md
        # asks every report for this, and a preset is where it comes from.
        print("\n  resolved parameters")
        for k, v in sorted(p.params.items()):
            if k.startswith("_"):
                continue
            print(f"    {k:26s} {v}")

        print(f"\n  builds ({len(p.builds)})")
        for b in p.builds:
            print(f"    {b.sha256[:12]} {b.bytes:7d} B  built {b.built_utc[:10]} "
                  f"at {b.src_commit}")
            for path in b.paths:
                print(f"      {'ok ' if _exists(path) else 'GONE'} {path}")
            if b.summary:
                print(f"      summary {b.summary}")

        print(f"\n  captures ({p.n_captured} samples in {len(p.captures)} runs)")
        for c in sorted(p.captures, key=lambda c: c.captured_utc):
            print(f"    {c.run_id:24s} n={c.n_samples:<3d} {c.captured_utc[:10]} "
                  f"commit {c.commit} ({c.commit_source})")
            if c.report:
                print(f"      report    {c.report}")
            print(f"      results   {c.results}")
            live = sum(1 for s in c.scenarios if _exists(s))
            print(f"      scenarios {live}/{len(c.scenarios)} still on disk")
            for s in c.samples:
                f = s.get("fairness") or {}
                med = f.get("median") or {}
                print(f"      s{s['sample_index']:<3d} iou {s['iou_10m']!s:<7.7s} "
                      f"land {s['land_pct']!s:<5.5s}% TCs {s['n_tcs']} "
                      f"sep {s['min_tc_separation']!s:<5.5s} "
                      f"masses {s['landmasses_with_a_player']} "
                      f"reach {s['reachable_fraction']}"
                      + (f"  median/player gold {med.get('gold')} "
                         f"stone {med.get('stone')} food(forage/sheep/deer/boar) "
                         f"{med.get('forage')}/{med.get('sheep')}/"
                         f"{med.get('deer')}/{med.get('boar')} "
                         f"land {f.get('land_exclusive_median')} "
                         f"@r{f.get('ownership_radius')}" if med else ""))
    return 0


def cmd_window(args) -> int:
    """Every preset sharing a preset's window - "same place, other knobs"."""
    reg = load()
    p = reg.get(args.preset)
    print(f"window {p.describe_window()}  ({p.window_hash[:12]})")
    for q in reg.by_window(p.window_hash):
        mark = "*" if q.label == p.label else " "
        print(f" {mark} {q.label:30s} {q.status:9s} caps {q.n_captured:3d}  "
              f"{' '.join(q.argv)}")
    return 0


def cmd_audit(args) -> int:
    """Facts about the registry's artifacts. No verdicts.

    Three questions, each a hash or an existence check:
      * does a shipped preset ship a script the engine was measured on?
      * which recorded builds have no surviving copy?
      * which presets have a build but no capture, or neither?
    """
    reg = load()
    import build_mod

    print("shipped: is the script that ships the script that was captured?")
    print(f"{'preset':22s} {'caps':>4s} {'runs':>4s}  ships a captured build?")
    for p in sorted(reg.select(status="shipped"), key=lambda p: p.label):
        shipped_build = None
        for b in p.builds:
            if any(x.startswith("mod/") for x in b.paths):
                shipped_build = b
        note = "no build recorded in mod/"
        if shipped_build:
            capture_paths = [x for x in shipped_build.paths
                             if x.startswith("out/mod_capture/")
                             or x.startswith("reports/")]
            if capture_paths:
                runs = sorted({x.split("/")[2] for x in capture_paths
                               if x.startswith("out/mod_capture/")})
                note = "YES - same sha256 as " + (", ".join(runs) or "an archived copy")
            else:
                other = [b for b in p.builds if b is not shipped_build]
                note = (f"no - {len(other)} other build(s) on record, none matching"
                        if other else "no - nothing else on record")
                # A rename is not a different map. Compare bodies before
                # saying the engine never saw this script.
                ship_path = next((_exists(x) for x in shipped_build.paths
                                  if x.startswith("mod/") and _exists(x)), None)
                if ship_path:
                    want = body_hash(ship_path)
                    for b in other:
                        for x in b.paths:
                            q = _exists(x)
                            if q and body_hash(q) == want:
                                run = (x.split("/")[2]
                                       if x.startswith("out/mod_capture/") else x)
                                note = ("YES apart from the header comment "
                                        f"(renamed) - {run}")
                                break
                        else:
                            continue
                        break
        print(f"{p.label:22s} {p.n_captured:4d} {len(p.captures):4d}  {note}")

    print("\nbuilds whose every recorded copy is gone")
    gone = 0
    for p in sorted(reg.presets.values(), key=lambda p: p.label):
        for b in p.builds:
            if b.paths and not any(_exists(x) for x in b.paths):
                gone += 1
                print(f"  {p.label:26s} {b.sha256[:12]} built {b.built_utc[:10]} "
                      f"at {b.src_commit}")
    if not gone:
        print("  none - every recorded build still has a copy on disk")

    print("\npromotable now: a build on disk, hash-verified, not shipped")
    for p in sorted(reg.presets.values(), key=lambda p: p.label):
        if p.status == "shipped":
            continue
        hit = p.find_build(REPO)
        if hit:
            build, path = hit
            print(f"  {p.label:26s} caps {p.n_captured:3d}  {build.sha256[:10]}  {path.name}")

    print("\nno capture on record")
    for status in ("shipped", "candidate"):
        names = [p.label for p in reg.select(status=status) if not p.captures]
        print(f"  {status:10s} {', '.join(names) if names else 'none'}")
    print(f"  screened   {len(reg.select(status='screened'))} paper screens, by "
          f"definition never built")
    retired = ", ".join(build_mod.RETIRED_REGIONS)
    print(f"\nretired regions (kept for their evidence): {retired}")
    return 0


def cmd_new(args) -> int:
    """Define a preset directly. Replaces the throwaway condition-set module.

    The window and every parameter are resolved and hashed here, so a
    preset exists - and can be found again - before any engine time is
    spent on it.
    """
    reg = load()
    argv = list(args.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    p = Preset.create(args.label, args.name, argv, status=args.status,
                      note=args.note,
                      origin={"source": "automation/preset_cli.py new",
                              "created": utc_now()})
    same = [q for q in reg.presets.values() if q.params_hash == p.params_hash]
    if same:
        print(f"identical parameters to {same[0].label} ({same[0].status}) - "
              f"nothing written. That preset already carries "
              f"{same[0].n_captured} captures.")
        return 1
    reg.add(p)
    path = reg.save(p)
    print(f"{p.id}\n  {p.describe_window()}\n  {p.command}\n  -> {path}")
    for line in p.legacy_notes:
        print(f"  legacy: {line}")
    return 0


def cmd_region_set(args) -> int:
    """Emit a ``mod_capture --region-set`` file from chosen presets.

    ``mod_capture.py --presets`` reads the registry directly; this exists for
    the harnesses that still take a region-set file, and because writing the
    file makes the exact set of a pass reviewable before it is started.
    """
    reg = load()
    presets = reg.select(args.presets)
    entries = [[p.name if args.by_name else p.label, p.argv] for p in presets]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    for name, argv in entries:
        print(f"{name:28s} {' '.join(argv)}")
    print(f"\n{len(entries)} conditions -> {out}")
    return 0


def cmd_promote(args) -> int:
    reg = load()
    p = reg.get(args.preset)
    if args.name:
        if args.name != p.name:
            aka = p.origin.setdefault("also_known_as", [])
            if p.name not in aka:
                aka.append(p.name)
            p.name = args.name
    p.status = "shipped"
    p.origin["promoted"] = utc_now()
    if args.why:
        p.note = args.why
    reg.save(p)
    hit = p.find_build(REPO)
    print(f"{p.label} -> shipped as {p.name!r}")
    print(f"  {p.describe_window()}")
    print(f"  {p.n_captured} captures on record across {len(p.captures)} runs")
    if hit:
        build, path = hit
        print(f"  build on disk, hash-verified: {build.sha256[:12]} {path}")
        print(f"  build_mod will ship that script as-is - no regeneration")
    else:
        print("  no build on disk - build_mod will generate one (~70s of "
              "annealing) and record it")
    print("\nnext: uv run python automation/build_mod.py "
          f"--presets {p.label}")
    return 0


def cmd_retire(args) -> int:
    reg = load()
    p = reg.get(args.preset)
    p.status = "retired"
    p.origin["retired"] = utc_now()
    if args.why:
        p.note = args.why
    reg.save(p)
    print(f"{p.label} -> retired. {p.note}")
    return 0


def cmd_note(args) -> int:
    reg = load()
    p = reg.get(args.preset)
    p.note = args.text
    reg.save(p)
    print(f"{p.label}: {p.note}")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="one line per preset")
    p.add_argument("presets", nargs="*")
    p.add_argument("--status", choices=sorted(STATUS_ORDER))
    p.add_argument("--window", metavar="HASH_PREFIX")
    p.add_argument("--captured", action="store_true",
                   help="only presets with a capture on record")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="everything about one preset")
    p.add_argument("presets", nargs="+")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("window", help="presets sharing this one's window")
    p.add_argument("preset")
    p.set_defaults(fn=cmd_window)

    p = sub.add_parser("audit", help="artifact facts: shipped vs captured, "
                                     "missing copies, promotable now")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("new", help="define a preset")
    p.add_argument("label")
    p.add_argument("name", help="in-game map name")
    p.add_argument("--status", default="candidate", choices=sorted(STATUS_ORDER))
    p.add_argument("--note", default="")
    p.add_argument("argv", nargs=argparse.REMAINDER,
                   help="rwmaps flags, after a literal --")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("region-set", help="write a mod_capture --region-set file")
    p.add_argument("presets", nargs="+")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--by-name", action="store_true",
                   help="key the file by display name instead of preset label "
                        "(names collide; labels do not)")
    p.set_defaults(fn=cmd_region_set)

    p = sub.add_parser("promote", help="mark a preset shipped")
    p.add_argument("preset")
    p.add_argument("--name", help="in-game map name to ship it under")
    p.add_argument("--why", help="what decided it - stored as the preset's note")
    p.set_defaults(fn=cmd_promote)

    p = sub.add_parser("retire", help="withdraw a preset from the mod")
    p.add_argument("preset")
    p.add_argument("--why", required=True)
    p.set_defaults(fn=cmd_retire)

    p = sub.add_parser("note", help="set a preset's note")
    p.add_argument("preset")
    p.add_argument("text")
    p.set_defaults(fn=cmd_note)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except KeyError as e:
        print(f"error: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
