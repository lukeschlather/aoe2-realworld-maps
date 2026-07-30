"""End-to-end real-render loop: generate an .rms, swap it into the fixed
editor slot, click Generate Map -> Menu -> Save, then rename the resulting
.aoe2scenario to a unique name. Requires the one-time manual editor setup in
EDITOR_WORKFLOW.md to already be done (Map panel open, Random Map checked,
size + slot script + players already selected).

Usage:
    uv run python scratch_compare/gen_loop.py <name> --region <region> [rwmaps args...]
"""

import argparse
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
from rwmaps import install as install_mod  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "rw_anatolia_220.rms"  # TODO: switch back to
# AA_rw_placeholder_tester.rms once that's selected in-game (see EDITOR_WORKFLOW.md)
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"
UI_DRIVER = Path(__file__).parent / "ui_driver.ps1"

# Screen coordinates (physical pixels, per-monitor-v2 DPI aware) found by hand
# on this machine's current resolution/layout - see EDITOR_WORKFLOW.md.
GENERATE_BTN = (256, 1028)
MENU_BTN = (1834, 23)
SAVE_BTN = (960, 436)
CANCEL_BTN = (960, 737)  # Main Menu's "Cancel" - only clicked if a prior
# failed/partial run left the Menu stuck open (detected via Test-MenuOpen).


def click_sequence(before_mtime: float):
    ps = f"""
. "{UI_DRIVER}"
Reset-IfMenuStuck {CANCEL_BTN[0]} {CANCEL_BTN[1]}
$ok = Click-GenerateMapVerified {GENERATE_BTN[0]} {GENERATE_BTN[1]}
if (-not $ok) {{ exit 1 }}
Click-At {MENU_BTN[0]} {MENU_BTN[1]}
Start-Sleep -Milliseconds 200
$beforeTime = [DateTimeOffset]::FromUnixTimeMilliseconds({math.ceil(before_mtime * 1000) + 200}).LocalDateTime
$ok = Click-SaveVerified {SAVE_BTN[0]} {SAVE_BTN[1]} {MENU_BTN[0]} {MENU_BTN[1]} "{SCENARIO_DIR}" $beforeTime
if (-not $ok) {{ exit 2 }}
"""
    # ui_driver.ps1 needs WinRT OCR, which only projects correctly under
    # Windows PowerShell 5.1 (powershell.exe), not PowerShell 7 (pwsh).
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode == 1:
        raise RuntimeError(f"Generate Map never registered a seed change:\n{result.stdout}\n{result.stderr}")
    if result.returncode == 2:
        raise RuntimeError(f"Save never closed the Menu:\n{result.stdout}\n{result.stderr}")
    if result.returncode != 0:
        raise RuntimeError(f"click sequence failed unexpectedly:\n{result.stdout}\n{result.stderr}")


def newest_scenario():
    files = sorted(SCENARIO_DIR.glob("*.aoe2scenario"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--region", required=True)
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--outdir", default="out")
    args, extra = ap.parse_known_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / args.outdir / f"loop-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    before = newest_scenario()
    before_mtime = before.stat().st_mtime if before else 0

    gen_cmd = [
        "uv", "run", "rwmaps", args.name,
        "--region", args.region, "--size", str(args.size),
        "--players", str(args.players), "--outdir", str(outdir), "--no-preview",
        *extra,
    ]
    print(f"[gen_loop] {' '.join(gen_cmd)}")
    subprocess.run(gen_cmd, cwd=REPO, check=True)

    rms_files = list(outdir.rglob("*.rms"))
    if len(rms_files) != 1:
        raise RuntimeError(f"expected exactly one .rms in {outdir}, found {rms_files}")
    rms_path = rms_files[0]

    print(f"[gen_loop] swapping {rms_path.name} into slot {SLOT_PATH}")
    shutil.copyfile(rms_path, SLOT_PATH)

    print("[gen_loop] driving Generate Map -> Menu -> Save")
    click_sequence(before_mtime)

    after = newest_scenario()
    if after is None or after.stat().st_mtime <= before_mtime:
        raise RuntimeError("no new .aoe2scenario appeared after Save - check screenshots")

    dest = outdir / f"{rms_path.stem}.aoe2scenario"
    shutil.copyfile(after, dest)
    print(f"[gen_loop] captured render: {dest}")
    return dest


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
