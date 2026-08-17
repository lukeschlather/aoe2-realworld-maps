"""The one Random Map list entry every capture harness overwrites.

Selecting a *different* entry in the editor's Random Map list reproducibly
crashes the game (see ``MOD_STATUS.md``), so nothing here ever changes the
selection: one fixed filename is selected once, by hand, and every script -
ours, stock, or a tuning variant - is captured by writing over it. That
makes the slot a shared resource rather than a detail of any one harness,
which is why it lives here.

``SLOT_PATH`` was already copy-pasted into six harnesses when this file was
added; ``put_slot`` had one copy, in ``stock_capture.py``, and its retry
loop is exactly the kind of hard-won detail that goes missing from copy
number two.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from rwmaps import install as install_mod  # noqa: E402

#: The entry the editor's Random Map selector is parked on. Named ``AA_`` so
#: it sorts first and the selection needs no click at all.
SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"

#: Where the editor writes a saved scenario.
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"


def put_slot(src: Path) -> None:
    """Copy a script into the slot, normalised to the ascii/LF form the
    engine's parser wants (same as ``rwmaps.rms.write_rms`` - a CRLF copy is
    a silent failure mode).

    Retries on PermissionError: the game holds the slot file open while it
    is generating, so a swap issued too soon after the previous sample dies
    with EACCES rather than anything descriptive.
    """
    data = src.read_bytes().replace(b"\r\n", b"\n")
    for _ in range(40):
        try:
            SLOT_PATH.write_bytes(data)
            return
        except PermissionError:
            time.sleep(0.5)
    raise RuntimeError(f"slot stayed locked by the game: {SLOT_PATH}")
