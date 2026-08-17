"""Two logs per run, because two different readers want opposite things.

* ``log.txt`` - **terse, for an agent or a person**. One short line per
  thing that happened, no timestamps, no durations, no ANSI, no library
  chatter. It answers "what happened, and did anything fail" in as few
  lines as possible, and two runs of the same pass produce near-identical
  files, so a diff between them is signal rather than noise. This is also
  what goes to stdout.
* ``events.jsonl`` - **verbose, for querying**. One JSON object per event
  with an absolute timestamp, seconds since the run started, and every
  duration and field the event carries. This is where "how long did save
  take across every capture of Britain" gets answered.

The split is deliberate and the reason is the terse log's readers: a time
is the single most common thing to differ between two otherwise identical
runs, so timings in the terse log would make every diff noisy and every
grep less exact - while an agent reading the file almost never wants
them. Nothing is lost, because every event lands in both and the JSON one
keeps everything.

Queries the JSON log is meant for::

    # every phase duration for one region, in order
    jq -r 'select(.region=="Britain") | [.kind, .duration_s] | @tsv' events.jsonl

    # median save time across a pass
    jq -s '[.[] | select(.kind=="capture") | .save_s] | sort | .[length/2|floor]'

    # what failed, and what the pass was doing at the time
    jq -c 'select(.ok==false)' events.jsonl
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).parent.parent


def git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except Exception:
        return "unknown"


class RunLog:
    """Writes both logs, appending, so a resumed run continues its own record.

    Appending rather than truncating because these passes are resumable:
    ``--run-id`` is reused to pick up where a crash left off, and a
    truncating log would destroy the record of the very failure that made
    the resume necessary.
    """

    def __init__(self, outroot: Path, run_id: str, command: list[str] | None = None,
                 echo: bool = True):
        outroot.mkdir(parents=True, exist_ok=True)
        self.terse_path = outroot / "log.txt"
        self.json_path = outroot / "events.jsonl"
        self.run_id = run_id
        self.echo = echo
        self._t0 = time.time()
        self._terse = self.terse_path.open("a", encoding="utf-8")
        self._json = self.json_path.open("a", encoding="utf-8")
        self.event("run_start", f"run {run_id}",
                   command=" ".join(command or sys.argv), commit=git_commit(),
                   run_id=run_id)

    # ------------------------------------------------------------- writing

    def event(self, kind: str, terse: str | None = None, **fields) -> None:
        """Record one event. ``terse`` is the agent-facing line, if any.

        An event with no ``terse`` is JSON-only - for the fine-grained
        timing that would bury the short log without telling an agent
        anything it did not already know from the line around it.
        """
        rec = {
            "t": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "elapsed_s": round(time.time() - self._t0, 3),
            "kind": kind,
            **fields,
        }
        self._json.write(json.dumps(rec, default=str) + "\n")
        self._json.flush()
        if terse is not None:
            self._terse.write(terse + "\n")
            self._terse.flush()
            if self.echo:
                print(terse, flush=True)

    def attach_editor(self, editor_module) -> None:
        """Send ``editor.py``'s narration into the JSON log, not stdout.

        Every harness that drives the editor wants this and none of them
        wants to think about it: the narration is per-click detail carrying
        pids and durations, which is what a post-mortem needs and exactly
        what the terse log excludes. Left unattached those lines reach
        stdout and no log at all, which is how the terse log ends up an
        incomplete account of its own run.
        """
        editor_module.SINK = lambda line: self.event("editor", None,
                                                    line=line.strip())

    def ok(self, kind: str, terse: str, **fields) -> None:
        self.event(kind, terse, ok=True, **fields)

    def fail(self, kind: str, terse: str, **fields) -> None:
        """A failure. Marked ``ok: false`` so one jq filter finds every one."""
        self.event(kind, terse, ok=False, **fields)

    def close(self, terse: str | None = None, **fields) -> None:
        self.event("run_end", terse, total_s=round(time.time() - self._t0, 2),
                   **fields)
        self._terse.close()
        self._json.close()

    # ------------------------------------------------------------- timing

    def timer(self, kind: str, **fields) -> "Timer":
        """Time a block and record its duration as its own JSON event.

        ``with log.timer("regen", region=name):`` - the duration lands in
        events.jsonl and nothing lands in the terse log, which is the usual
        want for a phase.

        **One event per phase occurrence.** Do not follow a timer with an
        explicit event of the same ``kind``: that writes the same duration
        twice, and any query that sums durations by kind then double-counts
        it. When a phase also needs a terse line, time it by hand and emit
        one event carrying ``duration_s`` - see how the harnesses handle
        preflight.
        """
        return Timer(self, kind, fields)


class Timer:
    def __init__(self, log: RunLog, kind: str, fields: dict):
        self.log, self.kind, self.fields = log, kind, fields
        self.seconds = 0.0

    def __enter__(self) -> "Timer":
        self._t = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.seconds = time.time() - self._t
        self.log.event(self.kind, None, duration_s=round(self.seconds, 3),
                       ok=exc_type is None,
                       **({"error": str(exc)} if exc else {}), **self.fields)
        return False
