"""Every automation entry point must at least import.

The test suite exercises src/rwmaps and never imports automation/, so a
syntax error there passed 39 green tests and only surfaced when a capture
pass died on the first line. An import is the cheapest possible check and
it would have caught it.
"""
import importlib
import sys
from pathlib import Path

import pytest

AUTOMATION = Path(__file__).parent.parent / "automation"
sys.path.insert(0, str(AUTOMATION))


@pytest.mark.parametrize("name", [
    "controls", "crash_bisect", "editor", "frame_server", "omni",
    "build_mod", "build_thumbnails", "mod_capture", "stock_capture",
])
def test_module_imports(name):
    importlib.import_module(name)
