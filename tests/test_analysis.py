"""Checks for the fairness analysis."""

from __future__ import annotations

import numpy as np
import pytest

from rwmaps.analysis import (
    analyze,
    choose_starts,
    components,
    evaluate,
    land_mask_from_terrain,
    land_path_distance,
    start_quality,
)
from rwmaps import terrain as T


def _two_islands(size=80, gap=10):
    mask = np.zeros((size, size), dtype=bool)
    mask[10:35, 10:70] = True
    mask[35 + gap : 70, 10:70] = True
    return mask


def test_land_mask_from_terrain():
    grid = np.array([[T.GRASS, T.WATER], [T.DEEP_WATER, T.DIRT]], dtype=np.uint8)
    np.testing.assert_array_equal(land_mask_from_terrain(grid), [[True, False], [False, True]])


def test_components_counts_separate_islands():
    _, sizes = components(_two_islands())
    assert (sizes[1:] > 0).sum() == 2


def test_land_path_distance_is_infinite_across_water():
    mask = _two_islands()
    dist = land_path_distance(mask, (20, 20))
    assert np.isfinite(dist[20, 60])
    assert not np.isfinite(dist[60, 20]), "the other island must be unreachable on foot"


def test_start_quality_peaks_inland():
    mask = np.zeros((80, 80), dtype=bool)
    mask[20:60, 20:60] = True
    q = start_quality(mask, radius=8)
    assert q[40, 40] == pytest.approx(1.0)
    assert q[20, 20] < 0.6, "a corner tile should score far worse than the middle"


def test_choose_starts_prefers_room_over_spread():
    """A tiny peninsula is farther away, but a start there is worse."""
    mask = np.zeros((120, 120), dtype=bool)
    mask[30:90, 30:90] = True  # the mainland
    mask[58:62, 90:118] = True  # a thin spit reaching east
    starts = choose_starts(mask, players=2, radius=12, edge_margin=4)
    assert len(starts) == 2
    assert all(x < 95 for _, x in starts), f"a start was placed on the spit: {starts}"


def test_choose_starts_respects_player_count_and_land():
    mask = np.zeros((150, 150), dtype=bool)
    mask[20:130, 20:130] = True
    starts = choose_starts(mask, players=4, radius=15)
    assert len(starts) == 4
    assert len(set(starts)) == 4
    for y, x in starts:
        assert mask[y, x]


def test_symmetric_map_scores_as_fair():
    mask = np.zeros((160, 160), dtype=bool)
    mask[20:140, 20:140] = True
    report = analyze(mask, players=4, radius=15)
    assert report.all_connected
    assert report.land_within_spread < 0.1
    assert report.verdict == "playable"


def test_split_map_is_flagged_when_starts_may_span_islands():
    mask = _two_islands(size=100, gap=12)
    starts = choose_starts(mask, players=4, radius=8, same_component=False, edge_margin=4)
    report = evaluate(mask, starts, radius=8)
    if not report.all_connected:
        assert report.verdict == "unfair"
        assert any("by land" in w for w in report.warnings)


def test_ai_type_is_coastal_when_starts_sit_on_lakes():
    """Great Lakes is ~85% land but every start is on a lake: fishing matters,
    so it must not be labelled ARABIA (a map with no fish at all)."""
    from rwmaps.analysis import choose_ai_map_type

    mask = np.ones((200, 200), dtype=bool)
    # two big lakes, with a start beside each
    yy, xx = np.ogrid[:200, :200]
    mask[((yy - 60) ** 2 + (xx - 60) ** 2) <= 30**2] = False
    mask[((yy - 140) ** 2 + (xx - 140) ** 2) <= 30**2] = False
    starts = [(60, 100), (100, 60), (140, 100), (100, 140)]
    assert choose_ai_map_type(mask, starts) == "COASTAL"


def test_ai_type_is_arabia_when_there_is_no_water_worth_fishing():
    from rwmaps.analysis import choose_ai_map_type

    mask = np.ones((200, 200), dtype=bool)
    starts = [(60, 60), (60, 140), (140, 60), (140, 140)]
    assert choose_ai_map_type(mask, starts) == "ARABIA"


def test_ai_type_is_naval_when_starts_cannot_walk_to_each_other():
    from rwmaps.analysis import choose_ai_map_type

    mask = _two_islands(size=100, gap=14)
    starts = [(20, 30), (20, 55), (60, 30), (60, 55)]
    assert choose_ai_map_type(mask, starts) in {"ISLANDS", "ARCHIPELAGO"}


def test_cramped_map_is_warned_about():
    """Four players on a small island in a big ocean is a real fairness problem."""
    mask = np.zeros((220, 220), dtype=bool)
    mask[95:125, 95:125] = True
    report = analyze(mask, players=4, radius=10)
    assert any("cramped" in w for w in report.warnings)
    assert report.verdict == "unfair"
