import math

import numpy as np
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator, extract_zero_contour_segments


def _grid_points(z_func, size=100.0, step=10.0, jitter=0.0, seed=0, origin=(0.0, 0.0)):
    rng = np.random.default_rng(seed)
    points = []
    for x in np.arange(0, size + step / 2, step):
        for y in np.arange(0, size + step / 2, step):
            px = float(x + rng.uniform(-jitter, jitter))
            py = float(y + rng.uniform(-jitter, jitter))
            points.append(SurveyPoint(f"P{len(points) + 1}", px + origin[0], py + origin[1], z_func(px, py)))
    return points


def _polygon_area(poly):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))) / 2


def test_add_points_preserves_imported_design_elevations():
    points = [
        SurveyPoint("A", 0, 0, 12, design_z=11, has_design_z=True),
        SurveyPoint("B", 1, 0, 8, design_z=9, has_design_z=True),
        SurveyPoint("C", 0, 1, 10, design_z=10, has_design_z=True),
    ]
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(points)
    by_id = {point.id: point for point in calculator.points}
    assert by_id["A"].design_z == 11
    assert by_id["B"].design_z == 9
    calculator.run_full_calculation()
    assert by_id["A"].design_z == 11


def test_requires_three_points():
    calculator = TINEarthworkCalculator(10)
    calculator.add_points([SurveyPoint("A", 0, 0, 10), SurveyPoint("B", 1, 0, 10)])
    with pytest.raises(ValueError, match="至少需要 3 个测量点"):
        calculator.run_full_calculation()


def test_mixed_triangle_preserves_area_and_cut_fill():
    points = [
        SurveyPoint("A", 0, 0, 1),
        SurveyPoint("B", 1, 0, -1),
        SurveyPoint("C", 0, 1, -1),
    ]
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(points)
    result = calculator.run_full_calculation()

    triangle = result.triangles[0]
    assert triangle.is_mixed
    assert math.isclose(triangle.area, 0.5, rel_tol=1e-9)
    assert math.isclose(triangle.cut_area + triangle.fill_area, triangle.area, rel_tol=1e-9)
    assert math.isclose(triangle.cut_area, 0.125, rel_tol=1e-9)
    assert math.isclose(triangle.cut_volume, 1 / 24, rel_tol=1e-9)
    assert math.isclose(triangle.fill_volume, 5 / 24, rel_tol=1e-9)
    assert math.isclose(triangle.volume, -1 / 6, rel_tol=1e-9)
    assert math.isclose(result.net_volume, triangle.volume, rel_tol=1e-9)


def test_partition_elevations_survive_run_full_calculation():
    points = [
        SurveyPoint("A", 0, 0, 12),
        SurveyPoint("B", 1, 0, 8),
        SurveyPoint("C", 0, 1, 10),
    ]
    calculator = TINEarthworkCalculator(10)
    calculator.add_points(points)
    calculator.set_design_elevations({"A": 11, "B": 9, "C": 10})

    result = calculator.run_full_calculation()

    by_id = {point.id: point for point in calculator.points}
    assert by_id["A"].design_z == 11
    assert by_id["B"].design_z == 9
    assert by_id["C"].design_z == 10
    assert math.isclose(by_id["A"].delta_z, 1)
    assert math.isclose(by_id["B"].delta_z, -1)
    assert result.triangle_count == 1


def test_partition_points_not_listed_use_given_default():
    """回归：分区只列部分点时，其余点应落到统一设计高程，而不是 0。"""
    points = [
        SurveyPoint("A", 0, 0, 15.5),
        SurveyPoint("B", 10, 0, 15.2),
        SurveyPoint("C", 0, 10, 14.8),
        SurveyPoint("D", 10, 10, 15.1),
    ]
    calculator = TINEarthworkCalculator()  # 与界面一致：构造时不传高程
    calculator.add_points(points)
    calculator.set_design_elevations({"A": 15.3}, default=15.0)
    result = calculator.run_full_calculation()

    by_id = {point.id: point.design_z for point in calculator.points}
    assert by_id == {"A": 15.3, "B": 15.0, "C": 15.0, "D": 15.0}
    assert result.total_cut < 20
    assert not result.uniform_design
    assert "14.800" not in result.design_text and "15.000 ~ 15.300" in result.design_text


def test_collinear_points_raise_clear_error():
    calculator = TINEarthworkCalculator(0)
    calculator.add_points([
        SurveyPoint("A", 0, 0, 1),
        SurveyPoint("B", 1, 0, 1),
        SurveyPoint("C", 2, 0, 1),
    ])
    with pytest.raises(ValueError, match="无法构建三角网"):
        calculator.run_full_calculation()


def test_small_boundary_inside_large_triangle_is_clipped_exactly():
    """回归：原先按质心整块取舍，边界落在三角形内部时方量为 0。"""
    points = [
        SurveyPoint("A", 0, 0, 10),
        SurveyPoint("B", 10, 0, 10),
        SurveyPoint("C", 0, 10, 10),
        SurveyPoint("D", 10, 10, 10),
    ]
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(points)
    calculator.set_boundary([(0, 0), (1, 0), (1, 1), (0, 1)])
    result = calculator.run_full_calculation()
    assert math.isclose(result.computed_area, 1.0, rel_tol=1e-9)
    assert math.isclose(result.total_cut, 10.0, rel_tol=1e-9)
    assert result.warnings == []


@pytest.mark.parametrize("step", [5.0, 10.0, 20.0])
def test_concave_boundary_volume_matches_area_times_height(step):
    """高差恒为 1 m 时，方量必须精确等于边界面积（与测点间距无关）。"""
    points = _grid_points(lambda x, y: 11.0, step=step, jitter=1.0)
    boundary = [(23.3, 17.1), (81.7, 21.9), (83.2, 62.4), (52.5, 48.8), (47.5, 88.8), (21.4, 71.2)]
    calculator = TINEarthworkCalculator(10.0)
    calculator.add_points(points)
    calculator.set_boundary(boundary)
    result = calculator.run_full_calculation()
    assert math.isclose(result.total_cut, _polygon_area(boundary), rel_tol=1e-9)
    assert math.isclose(result.computed_area, _polygon_area(boundary), rel_tol=1e-9)
    assert result.clipped_triangle_count > 0
    assert result.total_fill == 0


def test_planar_terrain_cut_fill_exact_with_geodetic_coordinates():
    """平面地形下 TIN 插值精确；大地坐标（百万米量级）也不应损失精度。"""
    origin = (3_456_000.0, 512_000.0)
    points = _grid_points(lambda x, y: 50 + 0.02 * x - 0.01 * y, step=7.0, jitter=2.0, seed=3, origin=origin)
    square = [(10, 10), (90, 10), (90, 90), (10, 90)]
    calculator = TINEarthworkCalculator(50.3)
    calculator.add_points(points)
    calculator.set_boundary([(x + origin[0], y + origin[1]) for x, y in square])
    result = calculator.run_full_calculation()

    # dz = 0.02x - 0.01y - 0.3，在 10..90 的正方形上积分
    def integral(x0, x1, y0, y1):
        return (0.01 * (x1**2 - x0**2) * (y1 - y0) - 0.005 * (y1**2 - y0**2) * (x1 - x0)
                - 0.3 * (x1 - x0) * (y1 - y0))

    assert math.isclose(result.net_volume, integral(10, 90, 10, 90), rel_tol=1e-9)
    assert result.total_cut > 0 and result.total_fill > 0


def test_boundary_larger_than_survey_area_warns():
    points = [SurveyPoint(f"P{i}", x, y, 11.0) for i, (x, y) in enumerate([(0, 0), (100, 0), (0, 100), (100, 100), (50, 50)])]
    calculator = TINEarthworkCalculator(10)
    calculator.add_points(points)
    calculator.set_boundary([(-50, -50), (150, -50), (150, 150), (-50, 150)])
    result = calculator.run_full_calculation()
    assert math.isclose(result.computed_area, 10000)
    assert math.isclose(result.coverage_ratio, 0.25)
    assert any("75.0%" in item for item in result.warnings)


def test_boundary_without_survey_points_hints_axis_swap():
    points = [SurveyPoint("A", 0, 0, 1), SurveyPoint("B", 10, 0, 1), SurveyPoint("C", 0, 10, 1)]
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(points)
    calculator.set_boundary([(100, 100), (110, 100), (110, 110)])
    result = calculator.run_full_calculation()
    assert result.total_cut == 0
    assert any("X/Y" in item for item in result.warnings)


def test_self_intersecting_boundary_is_rejected():
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(_grid_points(lambda x, y: 1.0))
    calculator.set_boundary([(0, 0), (10, 10), (10, 0), (0, 10)])
    with pytest.raises(ValueError, match="交叉"):
        calculator.run_full_calculation()


def test_missing_elevation_raises_clear_error():
    points = [SurveyPoint("A", 0, 0, 11), SurveyPoint("B", 10, 0, 12), SurveyPoint("C", 0, 10, float("nan"))]
    calculator = TINEarthworkCalculator(10)
    calculator.add_points(points)
    with pytest.raises(ValueError, match="C"):
        calculator.run_full_calculation()


def test_duplicate_xy_with_different_elevation_is_rejected():
    points = [
        SurveyPoint("A", 0, 0, 10), SurveyPoint("B", 10, 0, 10), SurveyPoint("C", 0, 10, 10),
        SurveyPoint("E", 5, 5, 20), SurveyPoint("E2", 5, 5, 0),
    ]
    calculator = TINEarthworkCalculator(10)
    calculator.add_points(points)
    with pytest.raises(ValueError, match="E2"):
        calculator.run_full_calculation()


def test_exact_duplicate_point_is_ignored_with_warning():
    points = [
        SurveyPoint("A", 0, 0, 11), SurveyPoint("B", 10, 0, 11), SurveyPoint("C", 0, 10, 11),
        SurveyPoint("C-copy", 0, 10, 11),
    ]
    calculator = TINEarthworkCalculator(10)
    calculator.add_points(points)
    result = calculator.run_full_calculation()
    assert math.isclose(result.total_cut, 50.0)
    assert any("C-copy" in item for item in result.warnings)


def test_zero_contour_has_single_segment_per_mixed_triangle():
    """回归：挖方或填方一侧的四边形对角线不应算作零填挖线。"""
    points = [SurveyPoint("A", 0, 0, 1), SurveyPoint("B", 1, 0, -1), SurveyPoint("C", 0, 1, -1)]
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(points)
    segments = extract_zero_contour_segments(calculator.run_full_calculation())
    assert len(segments) == 1
    assert {tuple(round(v, 9) for v in p) for p in segments[0]} == {(0.5, 0.0), (0.0, 0.5)}


def test_zero_contour_lies_on_design_level_and_inside_boundary():
    points = _grid_points(lambda x, y: 50 + 0.02 * x - 0.01 * y, step=6.0, jitter=1.5, seed=5)
    boundary = [(10, 10), (90, 10), (90, 90), (50, 50), (10, 90)]
    calculator = TINEarthworkCalculator(50.5)
    calculator.add_points(points)
    calculator.set_boundary(boundary)
    segments = extract_zero_contour_segments(calculator.run_full_calculation())
    assert segments
    for segment in segments:
        for x, y in segment:
            assert abs(0.02 * x - 0.01 * y - 0.5) < 1e-9
            assert 10 - 1e-9 <= x <= 90 + 1e-9 and 10 - 1e-9 <= y <= 90 + 1e-9


def test_recalculating_with_new_design_resets_mixed_state():
    points = [SurveyPoint("A", 0, 0, 1), SurveyPoint("B", 1, 0, -1), SurveyPoint("C", 0, 1, -1)]
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(points)
    calculator.run_full_calculation()
    calculator.set_design_elevation(-5)
    result = calculator.calculate_volumes()
    triangle = result.triangles[0]
    assert not triangle.is_mixed
    assert triangle.zero_segments == [] and triangle.cut_polygon == []
    assert result.mixed_triangle_count == 0
