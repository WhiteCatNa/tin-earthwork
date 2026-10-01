"""计算边界的检查：交叉、重复点、测点覆盖、理顺连线顺序。"""
import math

import numpy as np
import pytest

from core.boundary_check import (
    convex_hull, covered_area, duplicate_vertices, edge_lengths, find_crossings, untangle,
)
from core.geometry import ring_self_intersects


def _area(ring):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_find_crossings_names_the_crossing_edges():
    bow_tie = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]
    assert find_crossings(bow_tie) == [(0, 2)]          # 第 1→2 点的边与第 3→4 点的边交叉
    assert find_crossings(SQUARE) == []
    # 与 ring_self_intersects 的结论一致
    rng = np.random.default_rng(4)
    for _ in range(200):
        ring = [tuple(p) for p in rng.uniform(0, 10, size=(int(rng.integers(3, 9)), 2))]
        assert bool(find_crossings(ring)) == ring_self_intersects(ring)


def test_duplicate_vertices():
    assert duplicate_vertices([(0, 0), (1, 0), (0, 0), (1, 1), (1, 0)]) == [2, 4]
    assert duplicate_vertices(SQUARE) == []


def test_untangle_fixes_a_swapped_pair_and_keeps_the_points():
    # 现场点号顺序接错了两个点：正确顺序是 0,1,2,3,4,5（逆时针的六边形）
    hexagon = [(math.cos(k * math.pi / 3) * 10, math.sin(k * math.pi / 3) * 10) for k in range(6)]
    wrong = [hexagon[0], hexagon[1], hexagon[3], hexagon[2], hexagon[4], hexagon[5]]
    assert find_crossings(wrong)
    fixed = untangle(wrong)
    assert fixed == [hexagon[i] for i in range(6)]
    assert untangle(hexagon) == hexagon                  # 本来就没有交叉：原样返回


def test_untangle_shuffled_points_gives_a_simple_ring():
    rng = np.random.default_rng(11)
    for size in (5, 12, 40):
        angles = np.sort(rng.uniform(0, 2 * np.pi, size))
        radii = rng.uniform(5, 10, size)
        ring = [(float(r * np.cos(a)), float(r * np.sin(a))) for r, a in zip(radii, angles)]
        shuffled = [ring[i] for i in rng.permutation(size)]
        fixed = untangle(shuffled)
        assert sorted(fixed) == sorted(shuffled)
        assert not ring_self_intersects(fixed)
        assert fixed[0] == shuffled[0]


def test_untangle_refuses_duplicates():
    with pytest.raises(ValueError, match="重复"):
        untangle([(0, 0), (1, 0), (1, 1), (0, 0)])


def test_coverage_of_a_boundary_partly_outside_the_points():
    points = [(x, y) for x in range(0, 101, 10) for y in range(0, 101, 10)]
    hull = convex_hull(points)
    assert _area(hull) == pytest.approx(10000.0)
    assert covered_area(SQUARE, hull) == pytest.approx(100.0)
    # 边界右边伸出测点范围 20 m
    wide = [(0.0, 0.0), (120.0, 0.0), (120.0, 100.0), (0.0, 100.0)]
    assert covered_area(wide, hull) == pytest.approx(10000.0)
    # 凹边界、顺时针也行
    notch = [(0.0, 0.0), (0.0, 100.0), (150.0, 100.0), (150.0, 50.0), (50.0, 50.0), (50.0, 0.0)]
    assert covered_area(notch, hull) == pytest.approx(50 * 100 + 50 * 50)
    # 完全在外面
    assert covered_area([(200, 200), (210, 200), (210, 210)], hull) == 0.0


def test_coverage_with_geodetic_coordinates():
    ox, oy = 3_456_000.0, 512_000.0
    points = [(x + ox, y + oy) for x in range(0, 101, 10) for y in range(0, 101, 10)]
    ring = [(ox - 10.0, oy - 10.0), (ox + 50.0, oy - 10.0), (ox + 50.0, oy + 50.0), (ox - 10.0, oy + 50.0)]
    assert covered_area(ring, convex_hull(points)) == pytest.approx(2500.0, rel=1e-9)


def test_convex_hull_of_degenerate_points_is_none():
    assert convex_hull([(0, 0), (1, 1)]) is None
    assert convex_hull([(0, 0), (1, 1), (2, 2)]) is None


def test_edge_lengths_include_the_closing_edge():
    assert edge_lengths([(0, 0), (3, 0), (3, 4)]) == pytest.approx([3.0, 4.0, 5.0])
