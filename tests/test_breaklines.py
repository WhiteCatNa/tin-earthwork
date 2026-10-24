"""特征线接入计算：统一高程、斜面、分区、两期对比、方格网校核（与解析解对照）"""
import numpy as np
import pytest

from core.breaklines import Breakline
from core.calculator import BREAKLINE_ID_PREFIX, SurveyPoint, TINEarthworkCalculator
from core.grid_check import run_grid_check
from core.surface import PlaneDesign

# 场地 100 × 60 m，地面 z = 10 + 0.01x；沿 y = 24 有一条梯形沟：底宽 4 m、深 2 m、边坡 1:1（顶宽 8 m）
SITE = (100.0, 60.0)
EDGES = (20.0, 22.0, 26.0, 28.0)  # 坡顶、坡脚、坡脚、坡顶的 y
SECTION = 12.0                    # 沟断面面积 (m²)


def ground(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    depth = np.clip(2.0 - np.clip(np.abs(y - 24.0) - 2.0, 0, None), 0, 2.0)
    return 10 + 0.01 * x - depth


def flat(x, y):
    return 10 + 0.01 * np.asarray(x, dtype=float)


def _scatter(seed=3, count=300):
    rng = np.random.default_rng(seed)
    xy = rng.random((count, 2)) * SITE
    near = np.min(np.abs(xy[:, 1:2] - np.array(EDGES)), axis=1) < 0.3
    corners = np.array([[0, 0], [SITE[0], 0], [SITE[0], SITE[1]], [0, SITE[1]]], dtype=float)
    return np.vstack([corners, xy[~near]])


def _edge_xy():
    """四条边线上的点：两端在场地边上，中间点错开，使普通构网的三角形横跨沟。"""
    lines = []
    for k, y in enumerate(EDGES):
        xs = np.concatenate([[0.0], np.arange(7.0 + 3 * k, 100.0, 15.0), [SITE[0]]])
        lines.append(np.column_stack([xs, np.full_like(xs, y)]))
    return lines


def _points(xy, surface=ground, prefix="P"):
    z = surface(xy[:, 0], xy[:, 1])
    return [SurveyPoint(id=f"{prefix}{i}", x=float(x), y=float(y), z=float(h)) for i, ((x, y), h) in enumerate(zip(xy, z))]


def _survey(with_edge_points=True):
    xy = _scatter()
    lines = _edge_xy()
    if with_edge_points:
        xy = np.vstack([xy] + lines)
    return _points(xy), lines


def _calc(points, design=5.0, lines=None):
    calc = TINEarthworkCalculator(design_elevation=design)
    calc.add_points(points)
    calc.set_boundary([(0, 0), (SITE[0], 0), (SITE[0], SITE[1]), (0, SITE[1])])
    if lines:
        calc.set_breaklines(lines)
    return calc


EXACT_CUT = SITE[1] * (5.0 * SITE[0] + 0.01 * SITE[0] ** 2 / 2) - SECTION * SITE[0]  # 设计高程 5 m，全为挖方


def test_2d_breaklines_on_survey_points_give_exact_volume():
    points, lines = _survey()
    plain = _calc(points).run_full_calculation()
    assert abs(plain.total_cut - EXACT_CUT) > 20, "不加特征线时应有明显偏差"

    calc = _calc(points, lines=[Breakline([(x, y, None) for x, y in line]) for line in lines])
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(EXACT_CUT, abs=1e-6)
    assert result.total_fill == pytest.approx(0.0, abs=1e-9)
    assert not calc.breakline_points
    assert not result.warnings


def test_breakline_surface_matches_terrain_everywhere():
    points, lines = _survey()
    calc = _calc(points, design=9.5, lines=[Breakline([(x, y, None) for x, y in line]) for line in lines])
    calc.run_full_calculation()
    rng = np.random.default_rng(5)
    query = rng.random((5000, 2)) * SITE
    natural, design = calc.sample_surfaces(query)
    np.testing.assert_allclose(natural, ground(query[:, 0], query[:, 1]), atol=1e-9)
    np.testing.assert_allclose(design, 9.5, atol=1e-12)


def test_3d_breaklines_add_vertices():
    points, lines = _survey(with_edge_points=False)
    breaklines = [Breakline([(x, y, float(ground(x, y))) for x, y in line], name=f"沟边{k + 1}")
                  for k, line in enumerate(lines)]
    calc = _calc(points, lines=breaklines)
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(EXACT_CUT, abs=1e-6)
    assert len(calc.breakline_points) == sum(len(line) for line in lines)
    assert all(str(p.id).startswith(BREAKLINE_ID_PREFIX) for p in calc.breakline_points)
    assert all(p.design_z == 5.0 for p in calc.breakline_points)


def test_plane_design_and_zones_on_breakline_vertices():
    points, lines = _survey(with_edge_points=False)
    breaklines = [Breakline([(x, y, float(ground(x, y))) for x, y in line]) for line in lines]
    plane = PlaneDesign(x0=50, y0=30, h0=9.0, slope_x=0.5, slope_y=-0.2)
    calc = _calc(points, lines=breaklines)
    calc.set_design_plane(plane)
    calc.run_full_calculation()
    for p in calc.breakline_points:
        assert p.design_z == pytest.approx(plane.elevation(p.x, p.y), abs=1e-12)
        assert p.delta_z == pytest.approx(p.z - p.design_z)
    rng = np.random.default_rng(8)
    query = rng.random((2000, 2)) * SITE
    _, design = calc.sample_surfaces(query)
    np.testing.assert_allclose(design, plane.elevation(query[:, 0], query[:, 1]), atol=1e-9)

    # 分区：新增顶点按测点设计高程插值，不会报错，且落在相邻测点设计高程之间
    calc.set_design_elevations({points[0].id: 7.0}, default=9.0)
    calc.run_full_calculation()
    designs = [p.design_z for p in calc.breakline_points]
    assert min(designs) >= 7.0 - 1e-9 and max(designs) <= 9.0 + 1e-9


def test_compare_with_breaklines_in_before_survey():
    before, lines = _survey()
    rng = np.random.default_rng(9)
    after = _points(rng.random((250, 2)) * SITE, surface=flat, prefix="Q")
    after += _points(np.array([[0, 0], [SITE[0], 0], [SITE[0], SITE[1]], [0, SITE[1]]], dtype=float), flat, "QC")

    plain = _calc(before)
    plain.set_compare_points(after)
    plain_result = plain.run_full_calculation()

    calc = _calc(before, lines=[Breakline([(x, y, None) for x, y in line]) for line in lines])
    calc.set_compare_points(after)
    result = calc.run_full_calculation()
    # 前期有沟、后期填平：填方 = 断面 × 长度
    assert result.total_fill == pytest.approx(SECTION * SITE[0], abs=1e-6)
    assert result.total_cut == pytest.approx(0.0, abs=1e-6)
    assert abs(plain_result.total_fill - SECTION * SITE[0]) > 10


def test_compare_with_breaklines_in_after_survey():
    rng = np.random.default_rng(10)
    before = _points(rng.random((250, 2)) * SITE, surface=flat, prefix="Q")
    before += _points(np.array([[0, 0], [SITE[0], 0], [SITE[0], SITE[1]], [0, SITE[1]]], dtype=float), flat, "QC")
    after, lines = _survey(with_edge_points=False)
    calc = _calc(before)
    calc.set_breaklines([], [Breakline([(x, y, float(ground(x, y))) for x, y in line]) for line in lines])
    calc.set_compare_points(after)
    result = calc.run_full_calculation()
    # 后期挖出沟：挖方 = 断面 × 长度
    assert result.total_cut == pytest.approx(SECTION * SITE[0], abs=1e-6)
    assert result.total_fill == pytest.approx(0.0, abs=1e-6)
    assert all(str(p.id).startswith("后期-" + BREAKLINE_ID_PREFIX) for p in calc.breakline_points)


def test_grid_check_uses_breakline_surface():
    points, lines = _survey()
    plain = _calc(points)
    plain_result = plain.run_full_calculation()
    plain_grid = run_grid_check(plain, plain_result, 1.0)

    calc = _calc(points, lines=[Breakline([(x, y, None) for x, y in line]) for line in lines])
    result = calc.run_full_calculation()
    grid = run_grid_check(calc, result, 1.0)
    # 方格角点都在边线上（y = 20/22/26/28 是整数），方格网法也是精确的
    assert grid.net_volume == pytest.approx(EXACT_CUT, abs=1e-6)
    assert abs(plain_grid.net_volume - EXACT_CUT) > 10


def test_2d_vertex_off_survey_point_rejected():
    points, lines = _survey()
    calc = _calc(points, lines=[Breakline([(0.0, 20.0, None), (50.5, 20.0, None)])])
    with pytest.raises(ValueError, match="不在任何测点上"):
        calc.run_full_calculation()


def test_3d_vertex_on_survey_point_with_other_elevation_rejected():
    points, lines = _survey()
    x, y = lines[0][0]
    calc = _calc(points, lines=[Breakline([(x, y, 99.0), (lines[0][1][0], y, None)], name="坡顶")])
    with pytest.raises(ValueError, match="坡顶.*高程不同"):
        calc.run_full_calculation()


def test_crossing_breaklines():
    points, _ = _survey(with_edge_points=False)
    a = Breakline([(10.0, 10.0, 10.0), (90.0, 50.0, 11.0)], name="甲")
    consistent = Breakline([(10.0, 50.0, 10.0), (90.0, 10.0, 11.0)], name="乙")
    calc = _calc(points, lines=[a, consistent])
    calc.run_full_calculation()
    crossing = [p for p in calc.breakline_points if "交点" in str(p.id)]
    assert len(crossing) == 1
    assert (crossing[0].x, crossing[0].y) == (pytest.approx(50.0), pytest.approx(30.0))
    assert crossing[0].z == pytest.approx(10.5)
    assert not any("相交" in w for w in calc.warnings)

    inconsistent = Breakline([(10.0, 50.0, 12.0), (90.0, 10.0, 12.0)], name="乙")
    calc = _calc(points, lines=[a, inconsistent])
    calc.run_full_calculation()
    crossing = [p for p in calc.breakline_points if "交点" in str(p.id)]
    assert crossing[0].z == pytest.approx((10.5 + 12.0) / 2)
    assert any("甲" in w and "乙" in w and "相交" in w for w in calc.warnings)


def test_breakline_roundtrip_dict():
    line = Breakline([(1, 2, None), (3, 4, 5.5)], name="坡脚")
    again = Breakline.from_dict(line.to_dict())
    assert again == line
    assert again.vertices[0][2] is None and again.vertices[1][2] == 5.5
