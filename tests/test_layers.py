"""分层土方：解析解与细网格数值积分对照。"""
import numpy as np
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.layers import (LAYER_DEPTH, LAYER_ELEVATION, parse_layer_values, positive_min_integral,
                         run_layers)
from core.surface import PlaneDesign

SQUARE = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]


def _calc(func=lambda x, y: 10 + 0.1 * x, holes=None, boundary=SQUARE, step=2.5):
    calc = TINEarthworkCalculator()
    n = int(20 / step) + 1
    calc.add_points([SurveyPoint(f"P{i}-{j}", i * step, j * step, func(i * step, j * step))
                     for i in range(n) for j in range(n)])
    calc.set_boundary(boundary)
    if holes:
        calc.set_holes(holes)
    calc.set_design_elevation(11.0)
    return calc, calc.run_full_calculation()


def test_elevation_layers_exact():
    calc, result = _calc()
    layers = run_layers(calc, LAYER_ELEVATION, [11.5, 10.5])
    rows = [(row.label(layers.mode), row.cut, row.fill) for row in layers.rows]
    assert [r[0] for r in rows] == ["10.500 m 以下", "10.500 ~ 11.500 m", "11.500 m 以上"]
    assert [r[1] for r in rows] == pytest.approx([0, 75, 25], abs=1e-9)
    assert [r[2] for r in rows] == pytest.approx([25, 75, 0], abs=1e-9)
    assert layers.total_cut == pytest.approx(result.total_cut, abs=1e-9)
    assert layers.total_fill == pytest.approx(result.total_fill, abs=1e-9)


def test_depth_layers_exact():
    calc, _ = _calc()
    layers = run_layers(calc, LAYER_DEPTH, [0.5])
    assert [row.label(LAYER_DEPTH) for row in layers.rows] == ["0 ~ 0.5 m", "0.5 m 以下"]
    assert [row.cut for row in layers.rows] == pytest.approx([75, 25], abs=1e-9)
    assert [row.fill for row in layers.rows] == pytest.approx([75, 25], abs=1e-9)


def _numeric(func, design, inside, level, mode, n=1200):
    """细网格中点积分：level 以上（高程）/ 以下（深度）的挖方、填方。"""
    xs = (np.arange(n) + 0.5) * 20 / n
    x, y = np.meshgrid(xs, xs)
    N, D = func(x, y), design(x, y)
    mask = inside(x, y)
    if mode == LAYER_ELEVATION:
        cut = np.clip(N - np.maximum(D, level), 0, None)
        fill = np.clip(D - np.maximum(N, level), 0, None)
    else:
        cut = np.clip(N - D - level, 0, None)
        fill = np.clip(D - N - level, 0, None)
    cell = (20 / n) ** 2
    return float((cut * mask).sum() * cell), float((fill * mask).sum() * cell)


@pytest.mark.parametrize("mode,values", [(LAYER_ELEVATION, [10.6, 11.2]), (LAYER_DEPTH, [0.2, 0.7])])
def test_layers_match_numeric_with_plane_design_and_hole(mode, values):
    func = lambda x, y: 10 + 0.08 * x + 0.05 * y          # noqa: E731  地面为平面，TIN 精确
    hole = [(4.0, 4.0), (9.0, 5.0), (7.0, 9.0)]
    calc, result = _calc(func, holes=[hole])
    plane = PlaneDesign(x0=10, y0=10, h0=11.2, slope_x=-1.0, slope_y=2.0)
    calc.set_design_plane(plane)
    result = calc.run_full_calculation()
    layers = run_layers(calc, mode, values)
    design = lambda x, y: 11.2 - 0.01 * (x - 10) + 0.02 * (y - 10)   # noqa: E731

    from matplotlib.path import Path
    path = Path(hole)

    def inside(x, y):
        return ~path.contains_points(np.column_stack([x.ravel(), y.ravel()])).reshape(x.shape)

    for index, level in enumerate(values):
        cut, fill = _numeric(func, design, inside, level, mode)
        # 分界以上（高程）/ 以下（深度）= 该分界之后各层之和
        got_cut = sum(row.cut for row in layers.rows[index + 1:])
        got_fill = sum(row.fill for row in layers.rows[index + 1:])
        assert got_cut == pytest.approx(cut, rel=2e-3, abs=0.05)
        assert got_fill == pytest.approx(fill, rel=2e-3, abs=0.05)
    assert layers.total_cut == pytest.approx(result.total_cut, rel=1e-9)
    assert layers.total_fill == pytest.approx(result.total_fill, rel=1e-9)


def test_positive_min_integral_unit_square():
    square = np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=float)
    # ∫ max(min(x, 1 − x), 0) = 1/4
    assert positive_min_integral(square, (1, 0, 0), (-1, 0, 1)) == pytest.approx(0.25)
    assert positive_min_integral(square, (1, 0, -0.5)) == pytest.approx(0.125)
    assert positive_min_integral(square, (0, 0, -1)) == 0


def test_parse_layer_values():
    assert parse_layer_values("50， 48;49 50", LAYER_ELEVATION) == [48, 49, 50]
    with pytest.raises(ValueError, match="大于 0"):
        parse_layer_values("0.3 0", LAYER_DEPTH)
    with pytest.raises(ValueError, match="不是数字"):
        parse_layer_values("a", LAYER_ELEVATION)
    with pytest.raises(ValueError, match="请填写"):
        parse_layer_values(" ", LAYER_ELEVATION)
