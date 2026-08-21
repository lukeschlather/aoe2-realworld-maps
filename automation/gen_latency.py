"""How long the engine takes to generate one map, per script, with variance.

The question is narrow on purpose: **wall-clock from the Generate Map click
until the button comes back**, for our scripts, for stock scripts, and for
the hyper-random ones - nothing else. No script is rebuilt and no scenario
is analysed, because both are our time and neither moves the number being
asked about. That keeps a sample at roughly generate + a couple of seconds
of save, instead of the ~105s a full ``mod_capture`` sample costs, and it is
what makes an N large enough to talk about variance affordable at all.

What is already suspected, and what this exists to settle: a comment in
``stock_capture.py`` claims stock is far slower than ours ("Arabia ~82s
against ~3s"). The only per-sample timings this repo actually has say the
opposite - our four measured regions ran 46-57s, and no stock map has ever
been timed per-sample at all, because the stock benchmark pass predates the
two-log paradigm. So the comparison has never been measured; it has only
been asserted.

**Samples are interleaved, not blocked.** Round *r* takes one sample of
every map before any map takes its sample *r+1*. Running a map's ten
samples back to back would confound the map with the moment - the engine's
memory state, the machine warming up, whatever else is running - and a
per-map spread is exactly the thing that would absorb it and look like
variance of the script. Interleaving also makes an interrupted pass useful:
dying at round six leaves N=6 for every map rather than N=10 for the first
60% of them. Each record carries its ``round`` so a warm-up effect stays
visible instead of being averaged away.

**What counts as "it generated".** Two independent facts, because this
project has been burned by each alone. ``editor.generate()`` watches the
Generate button's colour, which marks the start as well as the end, so a
script that never began and one that never finished are distinguishable
from one that took a long time. Then the map is saved and a genuinely newer
file must appear on disk - a mtime, not a screen reading. Timing comes from
the first; the verdict needs both, and the two are recorded separately so
neither is charged to the other.

Usage:
    uv run python automation/gen_latency.py --run-id latency_v1
    uv run python automation/gen_latency.py --run-id latency_v1 --n-samples 3
    uv run python automation/gen_latency.py --run-id smoke --n-samples 1 \
        --maps "Arabia,RW Britain,Megarandom"

Reuse a ``--run-id`` to resume: the pass reads its own results.jsonl and
picks up at the first round that is not complete.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import editor  # noqa: E402
from runlog import RunLog  # noqa: E402
from slot import SCENARIO_DIR, put_slot  # noqa: E402

#: The live stock scripts. Per ``STOCK_MAP_INVENTORY.md`` this - not
#: ``random-map-scripts/``, and not the ``.backup.20201109`` snapshot - is
#: what the game actually runs.
STOCK_DIR = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\AoE2DE"
    r"\resources\_common\drs\gamedata_x2"
)

#: Our shipping scripts, read from the repo rather than from the installed
#: mod so the run is pinned to a commit.
OURS_DIR = REPO / "mod" / "Real World Maps" / "resources" / "_common" / "random-map-scripts"

SIZE = 240
PLAYERS = 8
N_SAMPLES = 10

#: The stock set is ``stock_capture.py``'s benchmark set, unchanged, so this
#: pass and the resource benchmarks are talking about the same nine maps.
#: They were chosen to span resource system, topology and placement mode -
#: axes which are not the ones that obviously drive generation *time*, but
#: they do happen to span land-only, heavy forest, fragmented land, coastal
#: and direct_placement, which are.
STOCK = [
    ("Arabia", "Arabia.rms"),
    ("Thames", "Thames.rms"),
    ("Yucatan", "Yucatan.rms"),
    ("City of Lakes", "cityoflakes.rms2"),
    ("Loch Ness", "Loch Ness.rms"),
    ("Team Islands", "Team_Islands.rms"),
    ("Coastal", "Coastal.rms"),
    ("Black Forest", "Black_Forest.rms"),
    ("Arena", "Arena.rms"),
]

#: The hyper-random ones. Megarandom re-rolls terrain, resources and layout
#: per generation; Blind Random picks a whole different stock script each
#: time. Both are here because a script whose *work* differs run to run is
#: the natural upper bound on variance, and having it makes the spread on
#: everything else readable - without it there is nothing to say whether our
#: maps' spread is large or small.
HYPERRANDOM = [
    ("Megarandom", "Megarandom.rms2"),
    ("Blind Random", "Blind_Random.rms"),
]


def build_map_list(extra_dir: Path | None = None) -> list[tuple[str, str, Path]]:
    """(label, group, path) for everything in the pass.

    ``extra_dir`` adds scripts that are not in the mod, under the group
    ``extra``. That is what makes an experimental variant measurable against
    its own shipped baseline **in one interleaved pass** - the only way the
    comparison is not confounded with the moment - without staging the
    variant into the mod first.
    """
    maps: list[tuple[str, str, Path]] = []
    if extra_dir:
        for q in sorted(extra_dir.glob("*.rms")):
            maps.append((q.stem, "extra", q))
    for p in sorted(OURS_DIR.glob("*.rms")):
        maps.append((p.stem, "ours", p))
    for label, filename in STOCK:
        maps.append((label, "stock", STOCK_DIR / filename))
    for label, filename in HYPERRANDOM:
        maps.append((label, "hyperrandom", STOCK_DIR / filename))
    return maps


def done_counts(results_path: Path) -> dict[str, int]:
    """How many samples each map already has in this run-id.

    Counts *attempts that produced a record*, successful or not, because the
    round loop below is keyed on rounds - a map that failed round 3 should
    move on to round 4 rather than retrying round 3 forever.
    """
    counts: dict[str, int] = defaultdict(int)
    if results_path.exists():
        for line in results_path.open(encoding="utf-8"):
            counts[json.loads(line)["map"]] += 1
    return counts


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True,
                   help="scopes output under out/gen_latency/<run-id>/ - reuse "
                        "the same run-id to resume a partially-completed pass")
    p.add_argument("--n-samples", type=int, default=N_SAMPLES,
                   help=f"generations per map (default {N_SAMPLES}). Variance "
                        f"is the point, so this wants to be 10+; anything "
                        f"smaller is a smoke test, not a measurement.")
    p.add_argument("--maps", default=None,
                   help="comma-separated subset of labels (default: all)")
    p.add_argument("--groups", default=None,
                   help="comma-separated subset of ours,stock,hyperrandom,extra")
    p.add_argument("--extra-dir", default=None,
                   help="a directory of extra .rms to include, group 'extra' - "
                        "for measuring a variant against its shipped baseline "
                        "without staging it into the mod")
    p.add_argument("--keep-scenarios", action="store_true",
                   help="copy each saved scenario into the run dir. The editor "
                        "saves to one fixed name and overwrites it, so without "
                        "this the only surviving fact about a generation is "
                        "its timing.")
    p.add_argument("--timeout", type=float, default=300.0,
                   help="seconds before a generation is called hung")
    p.add_argument("--no-save", action="store_true",
                   help="skip the save. Faster, but then the only evidence a "
                        "map generated is the button's colour - no file mtime "
                        "backs it up. For timing spot-checks, not for a pass "
                        "whose numbers get published.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    maps = build_map_list(Path(args.extra_dir) if args.extra_dir else None)
    if args.groups:
        want = {g.strip() for g in args.groups.split(",")}
        maps = [m for m in maps if m[1] in want]
    if args.maps:
        want = {m.strip() for m in args.maps.split(",")}
        maps = [m for m in maps if m[0] in want]
        missing = want - {m[0] for m in maps}
        if missing:
            raise SystemExit(f"unknown map(s): {sorted(missing)}")
    if not maps:
        raise SystemExit("no maps selected")

    missing = [(label, p) for label, _g, p in maps if not p.exists()]
    if missing:
        for label, p in missing:
            print(f"MISSING {label}: {p}")
        raise SystemExit("some scripts do not exist - fix the list first")

    outroot = REPO / "out" / "gen_latency" / args.run_id
    outroot.mkdir(parents=True, exist_ok=True)
    results_path = outroot / "results.jsonl"

    scenarios = outroot / "scenarios"
    if args.keep_scenarios:
        scenarios.mkdir(exist_ok=True)

    log = RunLog(outroot, args.run_id)
    log.attach_editor(editor)
    log.event("plan",
              f"{len(maps)} maps x {args.n_samples} samples, interleaved",
              maps=[{"map": m, "group": g, "path": str(p),
                     "script_bytes": p.stat().st_size} for m, g, p in maps],
              n_samples=args.n_samples, size=SIZE, players=PLAYERS,
              save=not args.no_save, timeout_s=args.timeout)

    # Timed by hand, so this is ONE event: a timer writes its own, and a
    # second explicit event of the same kind double-counts in any query that
    # sums durations by kind.
    t_pre = time.time()
    ok, why = editor.ensure_ready(PLAYERS)
    preflight_s = time.time() - t_pre
    if not ok:
        log.fail("preflight", f"ABORT editor unusable: {why}", why=why,
                 duration_s=round(preflight_s, 3))
        log.close("aborted")
        raise SystemExit(f"ABORTING: the editor is not usable: {why}")
    log.ok("preflight", "editor ready", why=why,
           duration_s=round(preflight_s, 3))

    counts = done_counts(results_path)
    t_start = time.time()
    attempted = succeeded = 0

    with results_path.open("a", encoding="utf-8") as fh:
        for rnd in range(args.n_samples):
            todo = [m for m in maps if counts[m[0]] <= rnd]
            if not todo:
                continue
            log.event("round", f"round {rnd + 1}/{args.n_samples}",
                      round=rnd, n_maps=len(todo))
            for label, group, src in todo:
                put_slot(src)
                attempted += 1

                # THE measurement. generate() clicks, watches the button go
                # grey, and returns when it comes back red - so its seconds
                # are click-to-normal, which is the number being asked for,
                # and nothing else in this loop is inside it.
                gen = editor.generate(timeout=args.timeout)

                saved = None
                save_s = 0.0
                if gen.ok and not args.no_save:
                    t0 = time.time()
                    saved = editor.save(SCENARIO_DIR)
                    save_s = time.time() - t0

                kept = None
                if saved is not None and args.keep_scenarios:
                    kept = scenarios / f"{label}_r{rnd}{saved.suffix}"
                    shutil.copy2(saved, kept)

                verified = gen.ok and (args.no_save or saved is not None)
                rec = {
                    "map": label, "group": group, "round": rnd,
                    "script": src.name, "script_bytes": src.stat().st_size,
                    "size": SIZE, "players": PLAYERS,
                    "generate_s": round(gen.seconds, 3),
                    "generate_ok": gen.ok, "generate_detail": gen.detail,
                    "save_s": round(save_s, 3),
                    "saved_bytes": saved.stat().st_size if saved else None,
                    "scenario": kept.name if kept else None,
                    "verified": verified,
                }
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                counts[label] += 1

                if verified:
                    succeeded += 1
                    # No duration in the terse line, by convention: a time is
                    # the single most common thing to differ between two runs
                    # of the same pass, and it is all in events.jsonl anyway.
                    log.ok("sample", f"  {label}: ok", map=label, group=group,
                           round=rnd, generate_s=round(gen.seconds, 3),
                           save_s=round(save_s, 3),
                           script_bytes=rec["script_bytes"])
                    continue

                log.fail("sample", f"  {label}: FAILED {gen.detail}",
                         map=label, group=group, round=rnd,
                         generate_s=round(gen.seconds, 3),
                         generate_ok=gen.ok, detail=gen.detail,
                         saved=bool(saved))

                # A dead game is the one failure that will not fix itself,
                # and at ~3h unattended it must not end the pass: recover and
                # keep going, with the failed sample already recorded so the
                # loss is visible rather than silently retried away.
                if editor.game_pid() is None or not gen.ok:
                    ok, why = editor.ensure_ready(PLAYERS)
                    if not ok:
                        log.fail("recover", f"ABORT could not rebuild the "
                                 f"editor: {why}", why=why)
                        log.close("aborted", attempted=attempted,
                                  succeeded=succeeded)
                        return 1
                    log.ok("recover", "  editor rebuilt", why=why)

    log.close(f"done {succeeded}/{attempted} verified",
              attempted=attempted, succeeded=succeeded,
              expected=len(maps) * args.n_samples,
              results=str(results_path),
              wall_s=round(time.time() - t_start, 1))
    print(f"logs: {log.terse_path}  {log.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
