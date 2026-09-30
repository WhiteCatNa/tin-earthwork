"""方格网校核：线性地形下与 TIN 法完全一致；起伏地形下差异随边长收敛。"""
import math

import numpy as np
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.grid_check import MAX_GRID_CELLS, run_grid_check, suggest_spacing
from core.surface import PlaneDesign


def _points(z_func, step=5.0, jitter=1.5, seed=0, lo=-5.0, hi=105.0, prefix="P"):
    rng = np.random.default_rng(seed)
    points = [SurveyPoint(f"{prefix}C{i}", x, y, z_func(x, y))
              for i, (x, y) in enumerate([(lo, lo), (hi, lo), (hi, hi), (lo, hi)])]
    for x in np.arange(0, 100 + step / 2, step):
        for y in np.arange(0, 100 + step / 2, step):
            px, py = float(x + rng.uniform(-jitter, jitter)), float(y + rng.uniform(-jitter, jitter))
            points.append(SurveyPoint(f"{prefix}{len(points)}", px, py, z_func(px, py)))
    return points


def _run(points, boundary=None, design=50.0, plane=None, compare=None, spacing=10.0):
    calculator = TINEarthworkCalculator(design)
    calculator.add_points(points)
    if plane is not None:
        calculator.set_design_plane(plane)
    if compare is not None:
        calculator.set_compare_points(compare)
    if boundary:
        calculator.set_boundary(boundary)
    tin = calculator.run_full_calculation()
    return tin, run_grid_check(calculator, tin, spacing)


CONCAVE = [(12.3, 8.1), (91.7, 15.9), (83.2, 72.4), (52.5, 48.8), (37.5, 93.8), (8.4, 61.2)]


@pytest.mark.parametrize("spacing", [7.0, 10.0, 20.0])
def test_linear_terrain_grid_equals_tin_exactly(spacing):
    terrain = lambda x, y: 50 + 0.02 * x - 0.01 * y
    tin, grid = _run(_points(terrain), CONCAVE, design=50.3, spacing=spacing)
    assert math.isclose(grid.total_cut, tin.total_cut, rel_tol=1e-9)
    assert math.isclose(grid.total_fill, tin.total_fill, rel_tol=1e-9)
    assert math.isclose(grid.computed_area, tin.computed_area, rel_tol=1e-9)
    assert grid.warnings == []
    for label, _, _, diff, relative in grid.comparison(tin):
        assert abs(relative) < 1e-6, label


def test_plane_design_and_two_period_grid_equal_tin_for_linear_surfaces():
    terrain = lambda x, y: 50 + 0.02 * x - 0.01 * y
    tin, grid = _run(_points(terrain), CONCAVE, plane=PlaneDesign(50, 50, 50.2, 1.0, 0.5))
    assert math.isclose(grid.net_volume, tin.net_volume, rel_tol=1e-9)
    assert math.isclose(grid.total_cut, tin.total_cut, rel_tol=1e-9)

    after = _points(lambda x, y: 49.5 + 0.01 * x + 0.005 * y, step=9.0, seed=4, prefix="Q")
    tin, grid = _run(_points(terrain, seed=2), CONCAVE, compare=after)
    assert tin.is_compare
    assert math.isclose(grid.total_cut, tin.total_cut, rel_tol=1e-9)
    assert math.isclose(grid.total_fill, tin.total_fill, rel_tol=1e-9)


def test_curved_terrain_grid_close_to_tin():
    terrain = lambda x, y: 50 + 2 * math.sin(x / 15) * math.cos(y / 12)
    tin, coarse = _run(_points(terrain, step=4.0), CONCAVE, spacing=20.0)
    _, fine = _run(_points(terrain, step=4.0), CONCAVE, spacing=4.0)
    fine_error = abs(fine.net_volume - tin.net_volume)
    assert abs(fine.total_cut - tin.total_cut) / tin.total_cut < 0.02
    assert fine_error < abs(coarse.net_volume - tin.net_volume) or fine_error < 1.0


def test_grid_layout_corners_and_cells():
    tin, grid = _run(_points(lambda x, y: 51.0), [(0, 0), (100, 0), (100, 100), (0, 100)], spacing=10.0)
    assert (grid.rows, grid.cols, grid.origin) == (10, 10, (0.0, 0.0))
    assert len(grid.corners) == 121 and len(grid.cells) == 100
    first = grid.cells[0]
    assert first.name == "1-1" and first.corners == ("1-1", "1-2", "2-2", "2-1")
    assert all(math.isclose(cell.area, 100) and math.isclose(cell.cut, 100) and not cell.clipped for cell in grid.cells)
    corner = next(c for c in grid.corners if c.name == "3-4")
    assert (corner.x, corner.y) == (30.0, 20.0) and math.isclose(corner.height, 1.0)
    assert math.isclose(grid.total_cut, tin.total_cut)


def test_grid_covers_same_region_as_tin_when_boundary_exceeds_survey():
    """边界取测点外接矩形时，边缘方格角点落在测区外：外推取值，只计测区内部分，与 TIN 范围一致。"""
    terrain = lambda x, y: 50 + 0.02 * x - 0.01 * y
    points = _points(terrain, lo=0.0, hi=100.0)
    xs, ys = [p.x for p in points], [p.y for p in points]
    bbox = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
    for boundary in (None, bbox):
        tin, grid = _run(points, boundary, design=50.3, spacing=15.0)
        assert any(corner.extrapolated for corner in grid.corners)
        assert grid.skipped_cells == 0 and grid.warnings == []
        assert math.isclose(grid.computed_area, tin.computed_area, rel_tol=1e-9)
        # 平面地形下外推也是精确的
        assert math.isclose(grid.total_cut, tin.total_cut, rel_tol=1e-9)
        assert math.isclose(grid.total_fill, tin.total_fill, rel_tol=1e-9)


def test_invalid_spacing_is_rejected():
    points = _points(lambda x, y: 51.0)
    with pytest.raises(ValueError, match="大于 0"):
        _run(points, spacing=0)
    with pytest.raises(ValueError, match="过小"):
        _run(points, spacing=100.0 / math.sqrt(MAX_GRID_CELLS) / 2)


def test_suggest_spacing():
    assert suggest_spacing(200, 150) == 5.0
    assert suggest_spacing(800, 600) == 20.0
    assert suggest_spacing(5000, 5000) == 100.0
