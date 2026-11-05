"""DXF 边界中的圆弧段（凸度，组码 42）：导入后面积、方量要与圆弧一致，而不是按弦算。"""
import math

import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from utils.dxf_io import ARC_SAGITTA_TOLERANCE, import_boundary_from_dxf, parse_dxf_polylines

QUARTER = math.tan(math.pi / 8)  # 90° 圆弧的凸度


def _signed_area(poly):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1])) / 2


def _lwpolyline(vertices, closed=True, layer="BOUNDARY"):
    body = "".join(
        f"10\n{x!r}\n20\n{y!r}\n" + (f"42\n{bulge!r}\n" if bulge else "")
        for x, y, bulge in vertices
    )
    return (
        "0\nSECTION\n2\nENTITIES\n"
        f"0\nLWPOLYLINE\n8\n{layer}\n90\n{len(vertices)}\n70\n{1 if closed else 0}\n{body}"
        "0\nENDSEC\n0\nEOF\n"
    )


def _polyline(vertices, closed=True, vertex_flags=0, layer="红线"):
    body = "".join(
        f"0\nVERTEX\n8\n{layer}\n10\n{x!r}\n20\n{y!r}\n30\n0.0\n"
        + (f"42\n{bulge!r}\n" if bulge else "")
        + f"70\n{vertex_flags}\n"
        for x, y, bulge in vertices
    )
    return (
        "0\nSECTION\n2\nENTITIES\n"
        f"0\nPOLYLINE\n8\n{layer}\n66\n1\n70\n{1 if closed else 0}\n10\n0.0\n20\n0.0\n30\n0.0\n{body}"
        "0\nSEQEND\n0\nENDSEC\n0\nEOF\n"
    )


def _write(tmp_path, text, name="boundary.dxf"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# 20×20 的方形、四角倒半径 5 的圆角，逆时针；最后一个顶点的凸度管闭合段
ROUNDED_SQUARE = [
    (5.0, 0.0, 0.0), (15.0, 0.0, QUARTER),
    (20.0, 5.0, 0.0), (20.0, 15.0, QUARTER),
    (15.0, 20.0, 0.0), (5.0, 20.0, QUARTER),
    (0.0, 15.0, 0.0), (0.0, 5.0, QUARTER),
]
ROUNDED_SQUARE_AREA = 400.0 - (4.0 - math.pi) * 25.0


def _distance_to_rounded_square(x, y):
    """点到圆角方形轮廓的距离（只用于轮廓附近的点）。"""
    dx, dy = abs(x - 10.0) - 5.0, abs(y - 10.0) - 5.0
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    return abs(outside + min(max(dx, dy), 0.0) - 5.0)


@pytest.mark.parametrize("make", [_lwpolyline, _polyline], ids=["lwpolyline", "polyline"])
def test_rounded_corners_keep_arc_area(tmp_path, make):
    ring = import_boundary_from_dxf(_write(tmp_path, make(ROUNDED_SQUARE)))

    assert _signed_area(ring) == pytest.approx(ROUNDED_SQUARE_AREA, rel=1e-12)
    # 原来的顶点都在，弧上插入的点偏离圆弧不超过容许弓高
    for x, y, _ in ROUNDED_SQUARE:
        assert (x, y) in ring
    assert len(ring) > len(ROUNDED_SQUARE)
    assert max(_distance_to_rounded_square(x, y) for x, y in ring) <= ARC_SAGITTA_TOLERANCE


def test_clockwise_polyline_uses_negative_bulge(tmp_path):
    reversed_vertices = []
    ordered = ROUNDED_SQUARE[::-1]
    for index, (x, y, _) in enumerate(ordered):
        # 反向后，每段的凸度来自原来的前一个顶点，并且变号
        _, _, bulge = ordered[(index + 1) % len(ordered)]
        reversed_vertices.append((x, y, -bulge))
    ring = import_boundary_from_dxf(_write(tmp_path, _lwpolyline(reversed_vertices)))
    assert _signed_area(ring) == pytest.approx(-ROUNDED_SQUARE_AREA, rel=1e-12)


def test_two_vertex_circle(tmp_path):
    # CAD 里"两个顶点 + 凸度 1"就是一个整圆
    circle = [(0.0, 0.0, 1.0), (10.0, 0.0, 1.0)]
    ring = import_boundary_from_dxf(_write(tmp_path, _lwpolyline(circle)))
    assert _signed_area(ring) == pytest.approx(math.pi * 25.0, rel=1e-12)
    assert all(abs(math.hypot(x - 5.0, y) - 5.0) <= ARC_SAGITTA_TOLERANCE for x, y in ring)
    # 正凸度为逆时针：从 (0,0) 到 (10,0) 的弧走下半圆
    assert min(y for _, y in ring) == pytest.approx(-5.0, abs=ARC_SAGITTA_TOLERANCE)


def test_inward_arc_reduces_area(tmp_path):
    # 逆时针方形的底边向内凹成半圆（顺时针弧）
    notch = [(0.0, 0.0, -1.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]
    ring = import_boundary_from_dxf(_write(tmp_path, _lwpolyline(notch)))
    assert _signed_area(ring) == pytest.approx(100.0 - math.pi * 25.0 / 2.0, rel=1e-12)


def test_open_polyline_closes_with_straight_edge(tmp_path):
    # 未闭合的多段线：末点的凸度不生效，首尾用直线相连
    open_vertices = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 1.0)]
    ring = import_boundary_from_dxf(_write(tmp_path, _lwpolyline(open_vertices, closed=False)))
    assert ring == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_vertex_flags_are_not_polyline_flags(tmp_path):
    # VERTEX 的 70 是顶点标志（1 = 曲线拟合插入的点），不能当成多段线的"闭合"
    open_vertices = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 1.0)]
    path = _write(tmp_path, _polyline(open_vertices, closed=False, vertex_flags=1))
    (polyline,) = parse_dxf_polylines(path)
    assert not polyline.closed
    assert import_boundary_from_dxf(path) == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_straight_polylines_are_untouched(tmp_path):
    square = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]
    path = _write(tmp_path, _lwpolyline(square))
    assert import_boundary_from_dxf(path) == [(x, y) for x, y, _ in square]
    assert parse_dxf_polylines(path)[0].arc_count == 0


def test_arc_count_is_reported(tmp_path):
    (polyline,) = parse_dxf_polylines(_write(tmp_path, _lwpolyline(ROUNDED_SQUARE)))
    assert polyline.arc_count == 4


def test_large_radius_arc_with_geodetic_coordinates(tmp_path):
    # 大地坐标下的大半径弧：半径 400 m、圆心角 60°
    ox, oy, radius = 3_456_000.0, 512_000.0, 400.0
    half = math.radians(30.0)
    start = (ox + radius * math.cos(half), oy - radius * math.sin(half))
    end = (ox + radius * math.cos(half), oy + radius * math.sin(half))
    vertices = [(ox, oy, 0.0), (start[0], start[1], math.tan(math.radians(60.0) / 4.0)), (end[0], end[1], 0.0)]
    ring = import_boundary_from_dxf(_write(tmp_path, _lwpolyline(vertices)))
    sector = radius * radius * math.radians(60.0) / 2.0
    assert abs(_signed_area([(x - ox, y - oy) for x, y in ring])) == pytest.approx(sector, rel=1e-9)
    assert all(
        abs(math.hypot(x - ox, y - oy) - radius) <= ARC_SAGITTA_TOLERANCE
        for x, y in ring if (x, y) != (ox, oy)
    )


def test_arcs_stay_within_tolerance_for_any_subdivision(tmp_path):
    # 扫一遍半径和圆心角（含只分成两三小段的短弧）：顶点和各小段中点都不超出容许偏差，面积仍然严格相等
    for case, (radius, sweep) in enumerate(
        (radius, 0.02 + 0.05 * step) for radius in (2.0, 13.9, 150.0, 2500.0) for step in range(60)
    ):
        end = (radius * math.cos(sweep), radius * math.sin(sweep))
        vertices = [(0.0, 0.0, 0.0), (radius, 0.0, math.tan(sweep / 4.0)), (end[0], end[1], 0.0)]
        ring = import_boundary_from_dxf(_write(tmp_path, _lwpolyline(vertices), f"arc{case}.dxf"))
        arc = ring[1:]
        samples = arc + [((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0) for a, b in zip(arc, arc[1:])]
        deviation = max(abs(math.hypot(x, y) - radius) for x, y in samples)
        assert deviation <= ARC_SAGITTA_TOLERANCE, (radius, sweep, len(ring))
        assert _signed_area(ring) == pytest.approx(radius * radius * sweep / 2.0, rel=1e-12), (radius, sweep)


def test_circular_boundary_volume_matches_analytic_solution(tmp_path):
    # 平面地形 z = 0.03x − 0.02y + 12，圆形边界（圆心 (50,50)、半径 30），设计高程 10：
    # 全部为挖方，挖方量 = 圆面积 ×（圆心处地面高程 − 设计高程）
    circle = [(20.0, 50.0, 1.0), (80.0, 50.0, 1.0)]
    boundary = import_boundary_from_dxf(_write(tmp_path, _lwpolyline(circle)))

    points = [
        SurveyPoint(f"P{ix * 11 + iy + 1}", ix * 10.0, iy * 10.0, 0.03 * ix * 10.0 - 0.02 * iy * 10.0 + 12.0)
        for ix in range(11) for iy in range(11)
    ]
    calculator = TINEarthworkCalculator(10.0)
    calculator.add_points(points)
    calculator.set_boundary(boundary)
    result = calculator.run_full_calculation()

    area = math.pi * 30.0 ** 2
    expected_cut = area * (0.03 * 50.0 - 0.02 * 50.0 + 12.0 - 10.0)
    assert result.computed_area == pytest.approx(area, rel=1e-9)
    assert result.total_cut == pytest.approx(expected_cut, rel=1e-9)
    assert result.total_fill == pytest.approx(0.0, abs=1e-9)
