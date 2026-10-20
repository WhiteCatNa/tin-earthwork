"""三维视图：裁剪到计算范围、扣除挖空区后的三维面。"""
import numpy as np
import pytest
from matplotlib.figure import Figure

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.geometry import signed_area
from utils.view3d import draw_3d, exaggeration_value, surface_faces


def _calc(holes=None):
    calc = TINEarthworkCalculator()
    calc.add_points([SurveyPoint(f"P{i}-{j}", i * 5.0, j * 5.0, 10 + 0.1 * i * 5.0)
                     for i in range(5) for j in range(5)])
    calc.set_boundary([(2, 2), (18, 2), (18, 18), (2, 18)])
    if holes:
        calc.set_holes(holes)
    calc.set_design_elevation(10.5)
    return calc, calc.run_full_calculation()


def _area(faces):
    return sum(abs(signed_area(face[:, :2])) for face in faces)


def test_faces_follow_boundary_and_surfaces():
    calc, result = _calc()
    natural, design = surface_faces(calc)
    assert len(natural) == len(design)
    assert _area(natural) == pytest.approx(result.computed_area)
    ox, oy = calc.origin
    for face in natural:
        assert np.allclose(face[:, 2], 10 + 0.1 * (face[:, 0] + ox))
    assert all(np.allclose(face[:, 2], 10.5) for face in design)


def test_hole_is_removed_from_faces():
    hole = [(6.2, 6.2), (11.7, 6.2), (11.7, 11.3), (6.2, 11.3)]
    calc, result = _calc([hole])
    natural, _ = surface_faces(calc)
    ox, oy = calc.origin
    centers = np.array([face[:, :2].mean(axis=0) + (ox, oy) for face in natural])
    inside = ((centers[:, 0] > 6.2) & (centers[:, 0] < 11.7) & (centers[:, 1] > 6.2) & (centers[:, 1] < 11.3))
    assert not inside.any()
    # 细分近似：显示面积与精确计算面积相差很小
    assert _area(natural) == pytest.approx(result.computed_area, rel=0.03)


def test_draw_3d_and_exaggeration():
    calc, result = _calc()
    natural, design = surface_faces(calc)
    ax = Figure().add_subplot(projection="3d")
    assert draw_3d(ax, natural, design, result, 5) is not None
    assert draw_3d(ax, natural, design, result, 1, show_natural=False) is None
    assert draw_3d(ax, natural, design, result, 1, False, False) is None
    assert exaggeration_value("2.5") == 2.5
    assert exaggeration_value("0") is None and exaggeration_value("abc") is None
