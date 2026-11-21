"""沿测点外轮廓生成边界。"""
import math

import numpy as np
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.geometry import ring_self_intersects
from core.outline import default_outline_edge, outline_from_points


def _area(ring):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def _grid(keep, size=10, step=10.0):
    return [(ix * step, iy * step) for ix in range(size + 1) for iy in range(size + 1) if keep(ix, iy)]


def _l_shape(ix, iy):
    return not (ix > 5 and iy > 5)   # 100×100 的方形缺掉右上角 50×50


def test_square_grid_gives_its_four_corners():
    ring = outline_from_points(_grid(lambda ix, iy: True))
    assert sorted(ring) == [(0.0, 0.0), (0.0, 100.0), (100.0, 0.0), (100.0, 100.0)]


def test_l_shaped_site_follows_the_notch():
    points = _grid(_l_shape)
    ring = outline_from_points(points)
    # 默认的“最长边”约 2 倍点距：沿缺口走，凹角处只留下一个对角三角形（半格）
    assert _area(ring) == pytest.approx(7500.0 + 50.0)
    assert (50.0, 60.0) in ring and (60.0, 50.0) in ring
    assert not ring_self_intersects(ring)
    assert set(ring) <= set(points)   # 顶点都是测点


def test_zero_max_edge_returns_convex_hull():
    points = _grid(_l_shape)
    hull = outline_from_points(points, max_edge=0)
    assert _area(hull) == pytest.approx(100 * 100 - 50 * 50 / 2)   # 凸包把缺角斜着封上
    assert (50.0, 50.0) not in hull


def test_max_edge_controls_how_far_the_outline_cuts_in():
    points = _grid(_l_shape)
    # 比凸包上最长的边（70.7）还大：不收缩
    assert _area(outline_from_points(points, max_edge=80)) == pytest.approx(_area(outline_from_points(points, max_edge=0)))
    # 略大于点距：连凹角也完全贴合
    exact = outline_from_points(points, max_edge=12)
    assert _area(exact) == pytest.approx(7500.0)
    assert len(exact) == 6 and (50.0, 50.0) in exact


def test_default_edge_is_about_two_point_spacings():
    assert default_outline_edge(_grid(_l_shape)) == 20.0


def test_outline_is_a_simple_ring_for_scattered_points():
    rng = np.random.default_rng(7)
    xy = rng.uniform(0, 300, size=(3000, 2))
    xy = xy[~((xy[:, 0] > 150) & (xy[:, 1] > 120))]           # 挖掉一角
    xy = xy[np.hypot(xy[:, 0] - 60, xy[:, 1] - 300) > 70]      # 再从上边咬掉一块半圆
    ring = outline_from_points([tuple(row) for row in xy])
    assert len(set(ring)) == len(ring)
    assert not ring_self_intersects(ring)
    assert set(ring) <= {tuple(row) for row in xy}
    expected = 300 * 300 - 150 * 180 - math.pi * 70 ** 2 / 2
    assert _area(ring) == pytest.approx(expected, rel=0.05)
    assert _area(ring) < _area(outline_from_points([tuple(row) for row in xy], max_edge=0))


def test_outline_with_geodetic_coordinates_and_duplicates():
    ox, oy = 3_456_000.0, 512_000.0
    points = [(x + ox, y + oy) for x, y in _grid(_l_shape)]
    ring = outline_from_points(points + points[:20], max_edge=12)
    assert _area([(x - ox, y - oy) for x, y in ring]) == pytest.approx(7500.0)


def test_outline_boundary_is_fully_covered_by_the_tin():
    rng = np.random.default_rng(3)
    xy = rng.uniform(0, 200, size=(800, 2))
    xy = xy[~((xy[:, 0] > 120) & (xy[:, 1] < 90))]
    points = [SurveyPoint(f"P{i}", float(x), float(y), float(0.02 * x - 0.01 * y + 20)) for i, (x, y) in enumerate(xy)]
    ring = outline_from_points([(p.x, p.y) for p in points])

    calculator = TINEarthworkCalculator(10.0)
    calculator.add_points(points)
    calculator.set_boundary(ring)
    result = calculator.run_full_calculation()
    # 轮廓内处处有三角网：计算面积等于边界面积，没有“未覆盖”提示
    assert result.computed_area == pytest.approx(_area(ring), rel=1e-9)
    assert not any("覆盖" in warning for warning in calculator.warnings)


@pytest.mark.parametrize("points, message", [
    ([(0, 0), (1, 1)], "至少需要 3 个"),
    ([(0, 0), (1, 1), (1, 1), (0, 0)], "至少需要 3 个"),
    ([(0, 0), (1, 1), (2, 2), (3, 3)], "一条直线"),
])
def test_outline_rejects_degenerate_input(points, message):
    with pytest.raises(ValueError, match=message):
        outline_from_points(points)
