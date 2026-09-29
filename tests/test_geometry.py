import math

import numpy as np

from core.geometry import (
    clip_by_sign,
    clip_ring_to_triangle,
    clip_segment_to_ring,
    normalize_ring,
    points_in_ring,
    polygon_integral,
    ring_self_intersects,
    signed_area,
    triangles_touching_ring,
    zero_crossing_points,
)

L_SHAPE = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]


def test_normalize_ring_drops_closing_duplicate_and_orients_ccw():
    ring = normalize_ring([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])
    assert len(ring) == 4
    assert signed_area(ring) > 0


def test_points_in_concave_ring():
    ring = normalize_ring(L_SHAPE)
    inside = points_in_ring(np.array([[0.5, 0.5], [3, 0.5], [0.5, 3], [3, 3], [5, 5]]), ring)
    assert inside.tolist() == [True, True, True, False, False]


def test_ring_self_intersection():
    assert ring_self_intersects([(0, 0), (2, 2), (2, 0), (0, 2)])
    assert not ring_self_intersects(L_SHAPE)


def test_concave_ring_clipped_by_triangle_keeps_exact_area():
    ring = normalize_ring(L_SHAPE)
    region = clip_ring_to_triangle(ring, [(0, 0), (4, 0), (0, 4)])
    # L 形面积 7，被 x+y<=4 切掉两个 0.5 的角
    assert math.isclose(abs(signed_area(region)), 6.0, rel_tol=1e-12)


def test_polygon_integral_of_linear_function():
    square = [(0, 0), (2, 0), (2, 2), (0, 2)]
    values = [x + y for x, y in square]
    area, integral = polygon_integral(square, values)
    assert math.isclose(area, 4)
    assert math.isclose(integral, 8)  # ∫∫(x+y) over [0,2]^2


def test_clip_by_sign_and_zero_crossings():
    triangle = [(0, 0), (1, 0), (0, 1)]
    values = [1.0, -1.0, -1.0]
    positive, positive_values = clip_by_sign(triangle, values, positive=True)
    negative, _ = clip_by_sign(triangle, values, positive=False)
    assert len(positive) == 3 and len(negative) == 4
    assert all(value >= 0 for value in positive_values)
    assert sorted(zero_crossing_points(triangle, values)) == [(0.0, 0.5), (0.5, 0.0)]


def test_triangles_touching_ring_catches_slot_without_vertex_inside():
    # 细长缺口穿过三角形，三角形顶点都在边界内、也没有边界顶点落在三角形里
    ring = normalize_ring([(0, 0), (10, 0), (10, 10), (5.1, 10), (5.1, 1), (4.9, 1), (4.9, 10), (0, 10)])
    triangles = np.array([[(2, 2), (8, 2), (4, 8)], [(0.5, 0.5), (1.5, 0.5), (1, 1.5)]], dtype=float)
    touched = triangles_touching_ring(triangles, ring, 1e-9)
    assert touched.tolist() == [True, False]


def test_clip_segment_to_concave_ring():
    ring = normalize_ring(L_SHAPE)
    pieces = clip_segment_to_ring((0.5, 3), (3, 0.5), ring)
    total = sum(math.dist(a, b) for a, b in pieces)
    assert len(pieces) == 2
    assert math.isclose(total, 2 * math.dist((0.5, 3), (1, 2.5)), rel_tol=1e-9)
