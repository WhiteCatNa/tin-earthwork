"""斜面设计面与两期土方对比：与解析解、独立算法对比。"""
import math

import numpy as np
import pytest

from core.calculator import (
    COMPARE_ID_PREFIX,
    CROSSING_ID_PREFIX,
    MODE_COMPARE,
    SurveyPoint,
    TINEarthworkCalculator,
)
from core.geometry import clip_by_sign, polygon_integral
from core.surface import PlaneDesign

SQUARE = [(10.0, 10.0), (90.0, 10.0), (90.0, 90.0), (10.0, 90.0)]


def _grid(z_func, step=7.0, jitter=2.0, seed=0, size=100.0, prefix="P", origin=(0.0, 0.0)):
    rng = np.random.default_rng(seed)
    points = []
    for x in np.arange(0, size + step / 2, step):
        for y in np.arange(0, size + step / 2, step):
            px = float(x + rng.uniform(-jitter, jitter))
            py = float(y + rng.uniform(-jitter, jitter))
            points.append(SurveyPoint(f"{prefix}{len(points) + 1}", px + origin[0], py + origin[1], z_func(px, py)))
    return points


def _corners(lo=-5.0, hi=105.0, z_func=None, prefix="C", origin=(0.0, 0.0)):
    """四个角点：默认放在抖动范围之外，保证测区覆盖整个 0..100 正方形。"""
    return [
        SurveyPoint(f"{prefix}{i}", x + origin[0], y + origin[1], z_func(x, y))
        for i, (x, y) in enumerate([(lo, lo), (hi, lo), (hi, hi), (lo, hi)], 1)
    ]


def _linear_cut_fill(polygon, dz_func):
    """线性高差在多边形上的 (挖方, 填方)：直接按零线裁剪积分，作为独立对照。"""
    values = [dz_func(x, y) for x, y in polygon]
    cut_poly, cut_values = clip_by_sign(polygon, values, positive=True)
    fill_poly, fill_values = clip_by_sign(polygon, values, positive=False)
    return polygon_integral(cut_poly, cut_values)[1], -polygon_integral(fill_poly, fill_values)[1]


# ---------------------------------------------------------------------------
# 斜面设计面
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [(0.0, 0.0), (3_456_000.0, 512_000.0)])
def test_plane_design_on_planar_terrain_is_exact(origin):
    terrain = lambda x, y: 50 + 0.02 * x - 0.01 * y
    plane = PlaneDesign(x0=50 + origin[0], y0=50 + origin[1], h0=50.2, slope_x=1.0, slope_y=0.5)
    calculator = TINEarthworkCalculator()
    calculator.add_points(_grid(terrain, seed=3, origin=origin) + _corners(z_func=terrain, origin=origin))
    calculator.set_design_plane(plane)
    calculator.set_boundary([(x + origin[0], y + origin[1]) for x, y in SQUARE])
    result = calculator.run_full_calculation()

    dz = lambda x, y: terrain(x, y) - plane.elevation(x + origin[0], y + origin[1])
    cut, fill = _linear_cut_fill(SQUARE, dz)
    assert math.isclose(result.net_volume, 6400 * dz(50, 50), rel_tol=1e-9)
    assert math.isclose(result.total_cut, cut, rel_tol=1e-9)
    assert math.isclose(result.total_fill, fill, rel_tol=1e-9)
    assert result.total_cut > 0 and result.total_fill > 0
    assert "斜面" in result.design_text and "+1.000%" in result.design_text


def test_plane_design_zero_line_lies_on_the_plane():
    terrain = lambda x, y: 50 + 0.02 * x - 0.01 * y
    plane = PlaneDesign(0, 0, 50.1, slope_x=1.5, slope_y=-0.5)
    calculator = TINEarthworkCalculator()
    calculator.add_points(_grid(terrain, seed=5))
    calculator.set_design_plane(plane)
    result = calculator.run_full_calculation()
    segments = [segment for tri in result.triangles for segment in tri.zero_segments]
    assert segments
    for segment in segments:
        for x, y in segment:
            assert abs(terrain(x, y) - plane.elevation(x, y)) < 1e-9


def test_plane_design_with_overrides_and_reapply_after_add_points():
    points = [SurveyPoint("A", 0, 0, 10), SurveyPoint("B", 10, 0, 10), SurveyPoint("C", 0, 10, 10)]
    calculator = TINEarthworkCalculator()
    calculator.add_points(points)
    calculator.set_design_plane(PlaneDesign(0, 0, 9.0, slope_x=10.0), overrides={"C": 8.0})
    assert [p.design_z for p in points] == [9.0, 10.0, 8.0]
    # 测点更新后（如坐标平移）仍按斜面重新计算设计高程
    moved = [SurveyPoint(p.id, p.x + 10, p.y, p.z) for p in points]
    calculator.add_points(moved)
    assert [p.design_z for p in moved] == [10.0, 11.0, 8.0]
    result = calculator.run_full_calculation()
    assert "单独指定" in result.design_text
    # 回到统一高程时清掉斜面与分区
    calculator.set_design_elevation(9.5)
    assert all(p.design_z == 9.5 for p in moved)


# ---------------------------------------------------------------------------
# 两期对比
# ---------------------------------------------------------------------------

def _compare(before, after, boundary=None):
    calculator = TINEarthworkCalculator()
    calculator.add_points(before)
    calculator.set_compare_points(after)
    if boundary:
        calculator.set_boundary(boundary)
    return calculator, calculator.run_full_calculation()


def test_compare_planar_surfaces_on_different_points_is_exact():
    before_z = lambda x, y: 50 + 0.02 * x - 0.01 * y
    after_z = lambda x, y: 49.5 + 0.01 * x + 0.005 * y
    before = _grid(before_z, step=7.0, seed=1) + _corners(z_func=before_z)
    after = _grid(after_z, step=9.0, jitter=3.0, seed=2, prefix="Q") + _corners(z_func=after_z, prefix="D")
    _, result = _compare(before, after, SQUARE)

    dz = lambda x, y: before_z(x, y) - after_z(x, y)
    cut, fill = _linear_cut_fill(SQUARE, dz)
    assert result.mode == MODE_COMPARE
    assert math.isclose(result.computed_area, 6400, rel_tol=1e-9)
    assert math.isclose(result.total_cut, cut, rel_tol=1e-9)
    assert math.isclose(result.total_fill, fill, rel_tol=1e-9)
    assert result.warnings == []
    assert "两期对比" in result.design_text


def _surface_integral(points, boundary):
    """单个 TIN 曲面在边界内的积分（与 0 比较的净方量），用作两期对比的独立对照。"""
    calculator = TINEarthworkCalculator(0.0)
    calculator.add_points([SurveyPoint(p.id, p.x, p.y, p.z) for p in points])
    calculator.set_boundary(boundary)
    return calculator.run_full_calculation().net_volume


def test_compare_bumpy_surfaces_net_equals_difference_of_integrals():
    """两期都是起伏地形、测点位置不同：净方量必须等于两个曲面积分之差。"""
    rng = np.random.default_rng(7)
    before = _grid(lambda x, y: 50 + rng.normal(0, 0.8), step=6.0, seed=3) + _corners(z_func=lambda x, y: 50.0)
    after = _grid(lambda x, y: 49.8 + rng.normal(0, 0.8), step=8.0, jitter=3.0, seed=4, prefix="Q")
    after += _corners(z_func=lambda x, y: 49.0, prefix="D")
    boundary = [(12.3, 8.1), (91.7, 15.9), (83.2, 72.4), (52.5, 48.8), (37.5, 93.8), (8.4, 61.2)]
    _, result = _compare(before, after, boundary)

    expected = _surface_integral(before, boundary) - _surface_integral(after, boundary)
    assert math.isclose(result.net_volume, expected, rel_tol=1e-9, abs_tol=1e-6)
    assert result.total_cut > 0 and result.total_fill > 0
    assert result.mixed_triangle_count > 0 and result.clipped_triangle_count > 0


def test_compare_identical_surveys_gives_zero_and_keeps_point_ids():
    terrain = lambda x, y: 50 + math.sin(x / 13) + math.cos(y / 9)
    before = _grid(terrain, seed=8)
    after = [SurveyPoint(p.id, p.x, p.y, p.z) for p in before]
    _, result = _compare(before, after)
    assert abs(result.total_cut) < 1e-9 and abs(result.total_fill) < 1e-9
    ids = {p.id for tri in result.triangles for p in tri.vertex_points}
    assert ids <= {p.id for p in before}


def test_compare_names_second_period_points_and_crossings():
    before = [SurveyPoint("A", 0, 0, 10), SurveyPoint("B", 10, 0, 10), SurveyPoint("C", 0, 10, 10),
              SurveyPoint("D", 10, 10, 10)]
    after = [SurveyPoint("A", 0, 0, 9), SurveyPoint("B", 10, 0, 9), SurveyPoint("C", 0, 10, 9),
             SurveyPoint("D", 10, 10, 9), SurveyPoint("M", 4, 5, 8)]
    _, result = _compare(before, after)
    assert math.isclose(result.computed_area, 100, rel_tol=1e-12)
    ids = {p.id for tri in result.triangles for p in tri.vertex_points}
    assert {"A", "B", "C", "D", f"{COMPARE_ID_PREFIX}M"} <= ids
    assert any(name.startswith(CROSSING_ID_PREFIX) for name in ids)
    # 前期是 10 m 的平面；后期在 M 处凹下，挖方 = 1 m × 100 m² + 锥体
    cone = 1 / 3 * 100 * 1.0
    assert math.isclose(result.total_cut, 100 + cone, rel_tol=1e-9)


def _left_half(z):
    return [SurveyPoint(f"Q{i}", x, y, z) for i, (x, y) in enumerate([(0, 0), (50, 0), (50, 100), (0, 100)])]


def test_compare_partial_overlap_warns_without_boundary():
    before = _corners(0, 100, z_func=lambda x, y: 10.0)
    _, result = _compare(before, _left_half(9.0))
    assert math.isclose(result.computed_area, 5000, rel_tol=1e-9)
    assert math.isclose(result.total_cut, 5000, rel_tol=1e-9)
    assert any("50.0%" in item for item in result.warnings)


def test_compare_with_boundary_reports_uncovered_part():
    before = _corners(0, 100, z_func=lambda x, y: 10.0)
    _, result = _compare(before, _left_half(9.0), [(0, 0), (100, 0), (100, 100), (0, 100)])
    assert any("两期测量共同覆盖" in item for item in result.warnings)


def test_compare_without_overlap_raises():
    before = _corners(0, 100, z_func=lambda x, y: 10.0)
    after = _corners(0, 100, z_func=lambda x, y: 9.0, origin=(500.0, 500.0), prefix="Q")
    with pytest.raises(ValueError, match="没有重叠"):
        _compare(before, after)


def test_compare_rejects_conflicting_duplicates_in_second_period():
    before = _corners(0, 100, z_func=lambda x, y: 10.0)
    after = _corners(0, 100, z_func=lambda x, y: 9.0, prefix="Q") + [SurveyPoint("Q9", 0, 0, 5.0)]
    with pytest.raises(ValueError, match="后期"):
        _compare(before, after)


def test_sample_surfaces_matches_tin_interpolation():
    before_z = lambda x, y: 50 + 0.02 * x - 0.01 * y
    after_z = lambda x, y: 49 + 0.01 * y
    calculator, _ = _compare(_grid(before_z, seed=2) + _corners(z_func=before_z),
                             _grid(after_z, step=11.0, seed=3, prefix="Q") + _corners(z_func=after_z, prefix="D"))
    xy = np.array([[25.0, 30.0], [70.0, 55.5], [150.0, 150.0]])
    natural, compare = calculator.sample_surfaces(xy)
    assert np.allclose(natural[:2], [before_z(*p) for p in xy[:2]])
    assert np.allclose(compare[:2], [after_z(*p) for p in xy[:2]])
    assert np.isnan(natural[2]) and np.isnan(compare[2])
