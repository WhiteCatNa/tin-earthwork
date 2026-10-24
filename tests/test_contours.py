"""等高线：平面地形上的等高线是已知直线，逐条核对位置与长度。"""
import math

import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.contours import is_major, levels, suggest_interval, trace_contours
from core.surface import PlaneDesign
from utils.data_handler import DataExporter


def grid(func, step=10.0, size=100.0):
    n = int(size / step) + 1
    return [SurveyPoint(f"P{i}-{j}", i * step, j * step, func(i * step, j * step)) for i in range(n) for j in range(n)]


def length(polyline):
    return sum(math.dist(a, b) for a, b in zip(polyline, polyline[1:]))


def calc_for(func, boundary=None, holes=None, design=10.0):
    calc = TINEarthworkCalculator(design)
    calc.add_points(grid(func))
    if boundary:
        calc.set_boundary(boundary)
    calc.set_holes(holes or [])
    calc.run_full_calculation()
    return calc


def test_planar_ground_contours_are_straight_lines():
    calc = calc_for(lambda x, y: 10 + 0.1 * x)
    contours = trace_contours(calc, 1.0)
    assert [c.level for c in contours.natural] == [float(v) for v in range(10, 21)]
    for contour in contours.natural:
        x = (contour.level - 10) * 10
        assert all(abs(px - x) < 1e-9 for px, _ in contour.polyline)
        assert length(contour.polyline) == pytest.approx(100)
    assert [c.level for c in contours.natural if c.major] == [10.0, 15.0, 20.0]
    # 统一设计高程没有设计面等高线（整面都在同一高程上）
    assert contours.design == []


def test_contours_clipped_to_boundary_and_holes():
    boundary = [(20, 0), (80, 0), (80, 100), (20, 100)]
    hole = [(40, 40), (60, 40), (60, 60), (40, 60)]
    calc = calc_for(lambda x, y: 10 + 0.1 * x, boundary=boundary, holes=[hole])
    contours = trace_contours(calc, 1.0)
    assert sorted({c.level for c in contours.natural}) == [float(v) for v in range(12, 19)]
    by_level = {}
    for c in contours.natural:
        by_level.setdefault(c.level, 0.0)
        by_level[c.level] += length(c.polyline)
    assert by_level[12.0] == pytest.approx(100)
    assert by_level[15.0] == pytest.approx(80)     # x = 50 穿过挖空区，少 20 m


def test_design_plane_contours():
    calc = TINEarthworkCalculator()
    calc.add_points(grid(lambda x, y: 10 + 0.01 * x * y / 10))
    calc.set_design_plane(PlaneDesign(0, 0, 10, 0, 2))   # 设计面 10 + 0.02y
    calc.run_full_calculation()
    contours = trace_contours(calc, 0.5)
    assert [c.level for c in contours.design] == [10 + 0.5 * k for k in range(5)]
    for contour in contours.design:
        y = (contour.level - 10) / 0.02
        assert all(abs(py - y) < 1e-6 for _, py in contour.polyline)


def test_auto_interval_and_levels():
    assert suggest_interval(10, 20) == 0.5
    assert suggest_interval(0, 0.5) == 0.1
    assert levels(10.05, 11.0, 0.25) == [10.25, 10.5, 10.75, 11.0]
    assert is_major(15.0, 1.0) and not is_major(16.0, 1.0) and is_major(12.5, 0.5)
    with pytest.raises(ValueError, match="过小"):
        levels(0, 1000, 0.1)
    calc = calc_for(lambda x, y: 10 + 0.1 * x)
    assert trace_contours(calc).interval == 0.5


def test_contours_in_dxf_and_summary(tmp_path):
    calc = calc_for(lambda x, y: 10 + 0.1 * x)
    result = calc.calculate_volumes()
    result.contours = trace_contours(calc, 1.0)
    path = tmp_path / "c.dxf"
    DataExporter("测试").export_boundary_dxf(result, str(path))
    text = path.read_text(encoding="utf-8")
    assert "\nCONTOUR_MAJOR\n" in text and "\nCONTOUR\n" in text and "\nCONTOUR_LABEL\n" in text
    assert "\n30\n15.0\n" in text      # 多段线标高 = 等高线高程
    assert any(row[0] == "等高线" for row in DataExporter.summary_rows(result))
