import math

import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator


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
    assert math.isclose(sum(child.area for child in triangle.sub_triangles), triangle.area, rel_tol=1e-9)
    assert math.isclose(triangle.cut_volume, 1 / 24, rel_tol=1e-9)
    assert math.isclose(triangle.fill_volume, 5 / 24, rel_tol=1e-9)
    assert math.isclose(triangle.volume, -1 / 6, rel_tol=1e-9)
    assert math.isclose(result.net_volume, triangle.volume, rel_tol=1e-9)


def test_boundary_filters_triangle_by_centroid():
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
    assert result.triangle_count == 0
    assert result.total_cut == 0
