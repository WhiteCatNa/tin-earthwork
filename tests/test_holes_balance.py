"""挖空区、三角网清理、可松性系数与挖填平衡设计面（2.1）。

地面取平面或分片线性，TIN 对其精确，方量可与解析解逐位比较。
"""
import math

import numpy as np
import pytest

from core.balance import (
    SoilFactors, balance_elevation, balance_plane, best_fit_plane, soil_balance,
)
from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.grid_check import run_grid_check
from core.surface import PlaneDesign
from utils.data_handler import DataExporter, load_project, save_project

SQUARE = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]


def grid_points(func, step=10.0, size=100.0):
    points = []
    for i, x in enumerate(np.arange(0, size + step / 2, step)):
        for j, y in enumerate(np.arange(0, size + step / 2, step)):
            points.append(SurveyPoint(f"P{i}-{j}", float(x), float(y), float(func(x, y))))
    return points


def calculator(func, design=10.0, boundary=SQUARE, holes=None):
    calc = TINEarthworkCalculator(design)
    calc.add_points(grid_points(func))
    calc.set_boundary(list(boundary))
    calc.set_holes(holes or [])
    return calc


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ----------------------------------------------------------------------------
# 挖空区
# ----------------------------------------------------------------------------

def test_hole_removes_its_volume_exactly():
    # 高差 h = 0.1·x，边界 100×100 内 ∫h = 0.1·50·10000 = 50000；挖空区 [20,40]×[30,60] 内 ∫h = 0.1·30·600 = 1800
    calc = calculator(lambda x, y: 10 + 0.1 * x, holes=[rect(20, 30, 40, 60)])
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(50000 - 1800, abs=1e-6)
    assert result.total_fill == pytest.approx(0, abs=1e-9)
    assert result.computed_area == pytest.approx(10000 - 600, abs=1e-6)
    assert result.boundary_area == pytest.approx(10000 - 600, abs=1e-6)
    assert result.hole_area == pytest.approx(600)
    assert not [w for w in result.warnings if "覆盖" in w]


def test_hole_across_zero_line_splits_cut_and_fill():
    # h = 0.1·(x − 50)：x > 50 挖，x < 50 填。挖空区 [40,70]×[0,20] 跨零线，
    # 挖方部分 ∫_{50}^{70} 0.1(x−50)·20 dx = 400，填方部分 ∫_{40}^{50} 0.1(50−x)·20 dx = 100
    calc = calculator(lambda x, y: 10 + 0.1 * (x - 50), holes=[rect(40, 0, 70, 20)])
    result = calc.run_full_calculation()
    full = 0.1 * 50 * 50 / 2 * 100   # 半边的挖方（= 填方）= 12500
    assert result.total_cut == pytest.approx(full - 400, abs=1e-6)
    assert result.total_fill == pytest.approx(full - 100, abs=1e-6)
    # 挖空区内没有零填挖线
    for tri in result.triangles:
        for a, b in tri.zero_segments:
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            assert not (40 < mid[0] < 70 and 0 < mid[1] < 20)


def test_concave_hole_and_several_holes_without_boundary():
    l_shape = [(10, 10), (50, 10), (50, 30), (30, 30), (30, 50), (10, 50)]   # 面积 1200，∫x dA = 24000 + 8000
    calc = calculator(lambda x, y: 10 + 0.1 * x, boundary=[], holes=[l_shape, rect(60, 60, 80, 70)])
    result = calc.run_full_calculation()
    # 第二个挖空区面积 200，∫x dA = 70·200 = 14000
    expected = 0.1 * (50 * 10000 - 32000 - 14000)
    assert result.total_cut == pytest.approx(expected, abs=1e-6)
    assert result.computed_area == pytest.approx(10000 - 1200 - 200, abs=1e-6)


def test_hole_covering_whole_triangles_and_matching_grid_check():
    calc = calculator(lambda x, y: 10 + 0.05 * x + 0.02 * y, holes=[rect(0, 0, 50, 50)])
    result = calc.run_full_calculation()
    # 平面地形下方格网法与 TIN 法都精确
    grid = run_grid_check(calc, result, 7.0)
    assert grid.total_cut == pytest.approx(result.total_cut, rel=1e-9)
    assert grid.computed_area == pytest.approx(result.computed_area, rel=1e-9)
    assert any(tri.is_boundary for tri in result.triangles)


@pytest.mark.parametrize("hole, message", [
    (rect(90, 90, 120, 120), "超出了计算边界"),
    ([(10, 10), (20, 20), (20, 10), (10, 20)], "交叉"),
    ([(10, 10), (20, 10)], "至少需要 3 个"),
])
def test_invalid_hole_is_rejected(hole, message):
    calc = calculator(lambda x, y: 10 + 0.1 * x, holes=[hole])
    with pytest.raises(ValueError, match=message):
        calc.run_full_calculation()


def test_overlapping_holes_are_rejected():
    calc = calculator(lambda x, y: 10, holes=[rect(10, 10, 30, 30), rect(20, 20, 40, 40)])
    with pytest.raises(ValueError, match="重叠"):
        calc.run_full_calculation()


def test_hole_in_compare_mode():
    before = grid_points(lambda x, y: 12.0)
    after = grid_points(lambda x, y: 10.0, step=12.5)
    calc = TINEarthworkCalculator()
    calc.add_points(before)
    calc.set_compare_points(after)
    calc.set_boundary(SQUARE)
    calc.set_holes([rect(20, 20, 60, 40)])
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(2 * (10000 - 800), abs=1e-6)


def test_hole_rows_and_dxf(tmp_path):
    calc = calculator(lambda x, y: 10 + 0.1 * x, holes=[rect(20, 30, 40, 60)])
    result = calc.run_full_calculation()
    labels = [row[0] for row in DataExporter.summary_rows(result)]
    assert "挖空区" in labels
    path = tmp_path / "out.dxf"
    DataExporter("测试").export_boundary_dxf(result, str(path))
    assert "\nHOLE\n" in path.read_text(encoding="utf-8")
    assert "挖空区" in DataExporter("测试").generate_report_text(result, 121)


# ----------------------------------------------------------------------------
# 三角网清理
# ----------------------------------------------------------------------------

def test_long_triangles_are_trimmed():
    points = grid_points(lambda x, y: 10 + 0.1 * x)
    points.append(SurveyPoint("远点", 300.0, 50.0, 40.0))   # 与测区之间形成一圈狭长三角形
    calc = TINEarthworkCalculator(10.0)
    calc.add_points(points)
    calc.set_max_edge_length(15)
    result = calc.run_full_calculation()
    assert result.trimmed_triangles
    assert result.computed_area == pytest.approx(10000, abs=1e-6)
    assert result.total_cut == pytest.approx(50000, abs=1e-6)
    assert any("剔除" in w for w in result.warnings)
    assert any(row[0] == "剔除长边三角形" for row in DataExporter.summary_rows(result))

    calc.set_max_edge_length(None)
    assert calc.run_full_calculation().computed_area > 10000


def test_trimmed_area_excluded_from_grid_check():
    points = grid_points(lambda x, y: 10 + 0.1 * x)
    points.append(SurveyPoint("远点", 300.0, 50.0, 40.0))
    calc = TINEarthworkCalculator(10.0)
    calc.add_points(points)
    calc.set_boundary([(0, 0), (200, 0), (200, 100), (0, 100)])
    calc.set_max_edge_length(15)
    result = calc.run_full_calculation()
    grid = run_grid_check(calc, result, 5.0)
    assert grid.computed_area == pytest.approx(result.computed_area, rel=1e-9)
    assert grid.total_cut == pytest.approx(result.total_cut, rel=1e-9)


def test_trimming_everything_is_an_error():
    calc = calculator(lambda x, y: 10)
    calc.set_max_edge_length(1)
    with pytest.raises(ValueError, match="没有剩余三角形"):
        calc.run_full_calculation()


def test_trim_in_compare_mode():
    before = grid_points(lambda x, y: 12.0) + [SurveyPoint("远点", 300.0, 50.0, 12.0)]
    after = grid_points(lambda x, y: 10.0)
    calc = TINEarthworkCalculator()
    calc.add_points(before)
    calc.set_compare_points(after)
    calc.set_max_edge_length(15)
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(20000, abs=1e-6)


@pytest.mark.parametrize("value", [-1, float("nan")])
def test_bad_max_edge_length(value):
    with pytest.raises(ValueError):
        TINEarthworkCalculator().set_max_edge_length(value)


# ----------------------------------------------------------------------------
# 可松性系数与平衡
# ----------------------------------------------------------------------------

def test_soil_balance_numbers():
    balance = soil_balance(1000, 800, SoilFactors(1.25, 1.05))
    assert balance.cut_loose == pytest.approx(1250)
    assert balance.fill_natural == pytest.approx(800 / 1.05)
    assert balance.surplus == pytest.approx(1000 - 800 / 1.05)
    assert balance.export_loose == pytest.approx((1000 - 800 / 1.05) * 1.25)
    assert balance.borrow_natural == 0
    short = soil_balance(500, 800, SoilFactors(1.25, 1.05))
    assert short.borrow_natural == pytest.approx(800 / 1.05 - 500)
    assert short.export_loose == 0
    assert any(row[0] == "缺土外借（自然方）" for row in short.rows())


@pytest.mark.parametrize("factors", [SoilFactors(0, 1), SoilFactors(1, -1), SoilFactors(5, 1),
                                     SoilFactors(float("nan"), 1)])
def test_bad_soil_factors(factors):
    with pytest.raises(ValueError):
        factors.validate()


def test_balance_elevation_is_mean_height_without_factors():
    # 地面 z = 10 + 0.04x + 0.02y 在 [0,100]² 上的平均高程 = 10 + 2 + 1 = 13
    calc = calculator(lambda x, y: 10 + 0.04 * x + 0.02 * y)
    design = balance_elevation(calc)
    assert design.elevation == pytest.approx(13, abs=1e-4)
    assert design.cut == pytest.approx(design.fill, rel=1e-4)
    # 不改动界面上的计算器
    assert all(p.design_z == 10 for p in calc.points)


def test_balance_elevation_with_compaction_and_hole():
    calc = calculator(lambda x, y: 10 + 0.04 * x + 0.02 * y, holes=[rect(10, 10, 30, 30)])
    factors = SoilFactors(1.2, 1.1)
    design = balance_elevation(calc, factors)
    assert design.cut == pytest.approx(design.fill / 1.1, rel=1e-4)
    assert design.balance.surplus == pytest.approx(0, abs=0.05)
    calc.set_design_elevation(design.elevation)
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(design.cut, rel=1e-6)


def test_balance_plane_keeps_slopes():
    calc = calculator(lambda x, y: 10 + 0.04 * x + 0.02 * y)
    plane = PlaneDesign(50, 50, 0, 1.0, 0.0)
    design = balance_plane(calc, plane)
    assert (design.plane.x0, design.plane.y0, design.plane.slope_x, design.plane.slope_y) == (50, 50, 1.0, 0.0)
    # 平面地面与平面设计面：平均高差为 0 时挖填平衡，H0 = 13（基准点在中心）
    assert design.plane.h0 == pytest.approx(13, abs=1e-4)


def test_best_fit_plane_recovers_planar_ground():
    calc = calculator(lambda x, y: 10 + 0.04 * x - 0.02 * y)
    design = best_fit_plane(calc)
    assert design.plane.slope_x == pytest.approx(4, abs=1e-4)
    assert design.plane.slope_y == pytest.approx(-2, abs=1e-4)
    assert design.cut == pytest.approx(0, abs=1)
    assert design.fill == pytest.approx(0, abs=1)


def test_best_fit_plane_minimises_earthwork():
    rng = np.random.default_rng(3)
    bumps = rng.normal(0, 0.5, (11, 11))
    calc = calculator(lambda x, y: 10 + 0.03 * x + 0.01 * y + bumps[int(x // 10), int(y // 10)])
    best = best_fit_plane(calc)
    total = best.cut + best.fill
    for dx, dy in ((0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3)):
        other = balance_plane(calc, PlaneDesign(best.plane.x0, best.plane.y0, 0,
                                                best.plane.slope_x + dx, best.plane.slope_y + dy))
        assert other.cut + other.fill > total * 0.999
    assert best.cut == pytest.approx(best.fill, rel=1e-3)


def test_balance_rejects_compare_mode():
    calc = calculator(lambda x, y: 10)
    calc.set_compare_points(grid_points(lambda x, y: 9))
    with pytest.raises(ValueError, match="两期对比"):
        balance_elevation(calc)


def test_balance_rows_in_reports():
    calc = calculator(lambda x, y: 10 + 0.1 * x)
    result = calc.run_full_calculation()
    result.soil_balance = soil_balance(result.total_cut, result.total_fill, SoilFactors(1.3, 1.05))
    labels = [row[0] for row in DataExporter.summary_rows(result)]
    assert "余土外运（松方）" in labels
    assert "土方平衡" in DataExporter("测试").generate_report_text(result, 121)


def test_project_round_trip_with_v21_settings(tmp_path):
    path = tmp_path / "p.tinproj.json"
    save_project(str(path), grid_points(lambda x, y: 10), SQUARE, holes=[rect(10, 10, 20, 20)],
                 max_edge_length=25, soil_factors={"loose": 1.2, "compact": 1.05})
    data = load_project(str(path))
    assert data["holes"] == [rect(10, 10, 20, 20)]
    assert data["max_edge_length"] == 25
    assert SoilFactors.from_dict(data["soil_factors"]) == SoilFactors(1.2, 1.05)
    # 旧工程文件没有这些键
    save_project(str(path), grid_points(lambda x, y: 10), SQUARE)
    data = load_project(str(path))
    assert data["holes"] == [] and data["max_edge_length"] is None and data["soil_factors"] is None
