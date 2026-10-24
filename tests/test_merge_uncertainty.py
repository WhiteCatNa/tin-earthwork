"""Uncertainty thinning must retain the measured vertices of 2D breaklines."""
import numpy as np
import pytest

from core.breaklines import Breakline
from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.uncertainty import _sampling_mask, _subsample, evaluate_uncertainty


def _points(prefix, offset=0.0):
    return [SurveyPoint(f"{prefix}{x}-{y}", float(x), float(y), 10 + 0.01 * x + offset)
            for x in range(11) for y in range(11)]


def test_sampling_keeps_hull_and_2d_constraint_vertices():
    points = _points("P")
    lines = [Breakline([(0, 5, None), (5, 5, None), (10, 5, None)])]
    fixed = _sampling_mask(points, lines)
    for seed in range(3):
        subset = _subsample(points, fixed, 0.5, np.random.default_rng(seed))
        xy = {(p.x, p.y) for p in subset}
        assert {(0, 0), (10, 0), (10, 10), (0, 10), (0, 5), (5, 5), (10, 5)} <= xy
        assert len(subset) < len(points)


@pytest.mark.parametrize("compare", [False, True])
def test_uncertainty_resamples_with_2d_breaklines_in_each_period(compare):
    calc = TINEarthworkCalculator(9.0)
    calc.add_points(_points("P"))
    first = [Breakline([(0, 5, None), (5, 5, None), (10, 5, None)])]
    second = [Breakline([(0, 4, None), (5, 4, None), (10, 4, None)])]
    if compare:
        calc.set_compare_points(_points("Q", -1.0))
    calc.set_breaklines(first, second if compare else [])
    calc.set_boundary([(0, 0), (10, 0), (10, 10), (0, 10)])
    calc.set_holes([[(2, 2), (3, 2), (3, 3), (2, 3)]])
    calc.set_max_edge_length(20.0)
    result = calc.run_full_calculation()
    uncertainty = evaluate_uncertainty(calc, result, repeats=3, seed=0)
    assert uncertainty.repeats == 3
    assert not any("无法重新构网" in warning for warning in uncertainty.warnings)
    assert uncertainty.bias == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
    assert calc.run_full_calculation().total_cut == pytest.approx(result.total_cut)
