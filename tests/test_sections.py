"""断面法：线性变化的断面面积下平均断面法精确，与 TIN 法逐位比较。"""
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.sections import _positive_integral, run_sections


def grid(func, step=10.0, size=100.0):
    n = int(size / step) + 1
    return [SurveyPoint(f"P{i}-{j}", i * step, j * step, func(i * step, j * step)) for i in range(n) for j in range(n)]


def calc_for(func, boundary=None, holes=None, design=10.0):
    calc = TINEarthworkCalculator(design)
    calc.add_points(grid(func))
    if boundary:
        calc.set_boundary(boundary)
    calc.set_holes(holes or [])
    return calc, calc.run_full_calculation()


def test_positive_integral():
    assert _positive_integral(1, 3, 2) == (4, 0)
    assert _positive_integral(-1, -3, 2) == (0, 4)
    cut, fill = _positive_integral(2, -2, 4)
    assert cut == pytest.approx(2) and fill == pytest.approx(2)


@pytest.mark.parametrize("angle", [0, 90, 180])
def test_linear_section_area_is_exact(angle):
    calc, tin = calc_for(lambda x, y: 10 + 0.1 * x)
    sections = run_sections(calc, tin, 10, angle)
    assert len(sections.sections) == 11
    assert sections.total_cut == pytest.approx(tin.total_cut, rel=1e-9)
    assert sections.total_fill == pytest.approx(0, abs=1e-9)
    if angle == 90:
        assert all(s.cut_area == pytest.approx(500) for s in sections.sections)
        assert all(s.width == pytest.approx(100) for s in sections.sections)


def test_mixed_cut_fill_and_short_last_interval():
    # 高差 0.1(x − 50)：每个横断面（沿 y）挖填面积随 x 线性变化，断面法与 TIN 法一致
    calc, tin = calc_for(lambda x, y: 10 + 0.1 * (x - 50))
    sections = run_sections(calc, tin, 30, 90)       # 里程 0,30,60,90,100：最后一段 10 m
    assert [round(s.station, 6) for s in sections.sections] == [0, 30, 60, 90, 100]
    assert sections.total_cut == pytest.approx(tin.total_cut, rel=1e-9)
    assert sections.total_fill == pytest.approx(tin.total_fill, rel=1e-9)


def test_sections_clip_to_boundary_and_holes():
    boundary = [(0, 0), (100, 0), (100, 100), (0, 100)]
    hole = [(20, 20), (40, 20), (40, 40), (20, 40)]
    calc, tin = calc_for(lambda x, y: 10 + 0.1 * x, boundary=boundary, holes=[hole])
    sections = run_sections(calc, tin, 5, 0)
    at = {round(s.station): s for s in sections.sections}
    assert at[30].width == pytest.approx(80)
    assert at[30].cut_area == pytest.approx(0.1 * 30 * 80)
    # 挖空区边缘处面积跳变，平均断面法有误差，但不大
    assert sections.total_cut == pytest.approx(tin.total_cut, rel=0.01)


def test_rotated_sections_close_to_tin():
    calc, tin = calc_for(lambda x, y: 10 + 0.05 * x + 0.02 * y + 0.0004 * (x - 50) ** 2)
    sections = run_sections(calc, tin, 2, 30)
    assert sections.total_cut == pytest.approx(tin.total_cut, rel=0.01)
    comparison = sections.comparison(tin)
    assert comparison[0][0] == "挖方"


def test_compare_mode_sections():
    before = grid(lambda x, y: 12.0)
    after = grid(lambda x, y: 10.0, step=12.5)
    calc = TINEarthworkCalculator()
    calc.add_points(before)
    calc.set_compare_points(after)
    tin = calc.run_full_calculation()
    sections = run_sections(calc, tin, 10, 0)
    assert sections.total_cut == pytest.approx(20000, rel=1e-9)


@pytest.mark.parametrize("spacing", [0, -1, float("nan"), 0.001])
def test_bad_spacing(spacing):
    calc, tin = calc_for(lambda x, y: 10)
    with pytest.raises(ValueError):
        run_sections(calc, tin, spacing)
