"""End-to-end real-render loop: generate an .rms, swap it into the fixed
editor slot, generate and save in the editor, then rename the resulting
.aoe2scenario to a unique name.

The editor no longer has to be set up by hand: ``editor.ensure_ready()``
walks it there from whatever state it is in, and every click it makes is
verified against the screen first (see EDITOR_AUTOMATION.md).

Usage:
    uv run python automation/gen_loop.py <name> --region <region> [rwmaps args...]
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import editor  # noqa: E402
from rwmaps import install as install_mod  # noqa: E402

SLOT_PATH = install_mod.scripts_dir() / "AA_rw_placeholder_tester.rms"
SCENARIO_DIR = install_mod.find_profile() / "resources" / "_common" / "scenario"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--region")  # or pass --center/--span-km through `extra`
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--outdir", default="out")
    args, extra = ap.parse_known_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / args.outdir / f"loop-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    gen_cmd = [
        "uv", "run", "rwmaps", args.name,
        *(["--region", args.region] if args.region else []),
        "--size", str(args.size),
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

    # Check the editor is on our slot with the mods on before spending a
    # generation on it - with the mods off the editor silently generates a
    # stock map under this run's name instead.
    ok, why = editor.ensure_ready(args.players)
    if not ok:
        raise RuntimeError(f"the editor is not usable: {why}")

    print(f"[gen_loop] generating and saving in the editor ({why})")
    after = editor.generate_and_save(SCENARIO_DIR).path

    dest = outdir / f"{rms_path.stem}.aoe2scenario"
    shutil.copyfile(after, dest)
    print(f"[gen_loop] captured render: {dest}")
    return dest


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
