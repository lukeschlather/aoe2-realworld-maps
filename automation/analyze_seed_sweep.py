"""Summarize a seed_sweep.py results.jsonl: distribution of per-player
resource counts across all sampled seeds, how often each floor (boar>=2,
sheep>=4, no zeros) is met, and how many seeds are fully clean.

Usage:
    uv run python automation/analyze_seed_sweep.py out/seedsweep-italy_240_v2/results.jsonl
"""

import json
import sys

KINDS = ["gold", "stone", "forage", "sheep", "deer", "boar"]


def main():
    path = sys.argv[1]
    records = [json.loads(line) for line in open(path, encoding="utf-8")]
    print(f"{len(records)} seeds loaded from {path}")

    samples = {k: [] for k in KINDS}  # every (seed, player) count, flattened
    zero_counts = {k: 0 for k in KINDS}
    total_player_seed = 0

    clean_seeds = 0          # no player has a zero in any kind
    boar_floor_seeds = 0     # every player has boar >= 2
    sheep_floor_seeds = 0    # every player has sheep >= 4
    both_floors_seeds = 0

    for rec in records:
        per_player = rec["per_player"]
        seed_clean = True
        seed_boar_ok = True
        seed_sheep_ok = True
        for p, counts in per_player.items():
            total_player_seed += 1
            for k in KINDS:
                n = counts.get(k, 0)
                samples[k].append(n)
                if n == 0:
                    zero_counts[k] += 1
                    seed_clean = False
            if counts.get("boar", 0) < 2:
                seed_boar_ok = False
            if counts.get("sheep", 0) < 4:
                seed_sheep_ok = False
        clean_seeds += seed_clean
        boar_floor_seeds += seed_boar_ok
        sheep_floor_seeds += seed_sheep_ok
        both_floors_seeds += seed_boar_ok and seed_sheep_ok

    print(f"\n{'kind':8s} {'mean':>6s} {'min':>4s} {'p10':>4s} {'median':>7s} {'max':>4s} {'%zero':>7s}")
    for k in KINDS:
        vals = sorted(samples[k])
        n = len(vals)
        mean = sum(vals) / n
        p10 = vals[int(0.10 * n)]
        median = vals[n // 2]
        pct_zero = 100 * zero_counts[k] / total_player_seed
        print(f"{k:8s} {mean:6.1f} {vals[0]:4d} {p10:4d} {median:7d} {vals[-1]:4d} {pct_zero:6.1f}%")

    n = len(records)
    print(f"\n{clean_seeds}/{n} seeds ({100*clean_seeds/n:.0f}%) have zero shortfalls "
          f"of any kind for any player")
    print(f"{boar_floor_seeds}/{n} seeds ({100*boar_floor_seeds/n:.0f}%) have boar>=2 "
          f"for every player")
    print(f"{sheep_floor_seeds}/{n} seeds ({100*sheep_floor_seeds/n:.0f}%) have sheep>=4 "
          f"for every player")
    print(f"{both_floors_seeds}/{n} seeds ({100*both_floors_seeds/n:.0f}%) meet both floors "
          f"for every player")


if __name__ == "__main__":
    main()
