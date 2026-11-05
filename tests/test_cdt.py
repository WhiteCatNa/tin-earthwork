"""约束 Delaunay 三角剖分（特征线构网）"""
import numpy as np
import pytest
from scipy.spatial import ConvexHull, Delaunay

from core.cdt import constrained_delaunay, prepare_segments
from core.surface import TinSurface


def _edges(simplices):
    edges = set()
    for tri in simplices.tolist():
        for i in range(3):
            u, v = tri[i], tri[(i + 1) % 3]
            edges.add((min(u, v), max(u, v)))
    return edges


def _signed_areas(points, simplices):
    t = points[simplices]
    return 0.5 * ((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1])
                  - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0]))


def _assert_valid(result):
    areas = _signed_areas(result.points, result.simplices)
    assert (areas > 0).all(), "三角形应全部为逆时针、面积为正"
    hull = ConvexHull(result.points).volume
    assert areas.sum() == pytest.approx(hull, rel=1e-9), "三角形应无重叠地铺满凸包"
    assert result.constrained_edges <= _edges(result.simplices), "每条约束边都应是三角网的边"


def test_without_constraints_matches_scipy():
    xy = np.random.default_rng(0).random((200, 2)) * 100
    result = constrained_delaunay(xy)
    np.testing.assert_array_equal(result.simplices, Delaunay(xy).simplices)
    assert not result.added


@pytest.mark.parametrize("seed", range(40))
def test_random_constraints_are_kept(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(20, 300))
    xy = rng.random((n, 2)) * 100
    segments = [tuple(rng.choice(n, 2, replace=False)) for _ in range(int(rng.integers(1, 15)))]
    _assert_valid(constrained_delaunay(xy, segments, tol=1e-6))


def test_long_constraint_crossing_many_triangles():
    rng = np.random.default_rng(7)
    xy = np.vstack([[[0, 0], [1000, 1000]], rng.random((3000, 2)) * 1000])
    result = constrained_delaunay(xy, [(0, 1)], tol=1e-3)
    _assert_valid(result)
    assert (0, 1) in result.constrained_edges


def test_constraint_split_at_point_on_it():
    xy = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [5, 5], [2, 8]], dtype=float)
    points, edges, added = prepare_segments(xy, [(0, 2)], tol=1e-6)
    assert edges == [(0, 4), (2, 4)]
    assert not added
    _assert_valid(constrained_delaunay(xy, [(0, 2)], tol=1e-6))


def test_crossing_constraints_add_vertex_with_parents():
    xy = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [2, 7], [8, 3]], dtype=float)
    result = constrained_delaunay(xy, [(0, 2), (1, 3)], tol=1e-6)
    _assert_valid(result)
    assert len(result.added) == 1
    vertex = result.added[0]
    np.testing.assert_allclose(result.points[vertex.index], [5, 5])
    assert sorted(vertex.parents) == [(0, pytest.approx(0.5)), (1, pytest.approx(0.5))]
    k = vertex.index
    assert result.constrained_edges == {(0, k), (2, k), (1, k), (3, k)}


def test_t_junction_and_shared_crossing():
    # 第三条线的端点落在第一条线上（T 形）；三条线交于同一点
    xy = np.array([[0, 0], [10, 10], [10, 0], [0, 10], [5, 0], [5, 10], [3, 3], [0, 6]], dtype=float)
    result = constrained_delaunay(xy, [(0, 1), (2, 3), (4, 5), (7, 6)], tol=1e-6)
    _assert_valid(result)
    assert len(result.added) == 1
    assert {k for k, _ in result.added[0].parents} == {0, 1, 2}
    assert (0, 6) in result.constrained_edges and (1, 6) not in result.constrained_edges


def test_constraint_collinear_with_hull_edge_is_noop():
    xy = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [4, 6]], dtype=float)
    result = constrained_delaunay(xy, [(0, 1)], tol=1e-6)
    as_set = lambda simplices: {tuple(sorted(tri)) for tri in simplices.tolist()}
    assert as_set(result.simplices) == as_set(Delaunay(xy).simplices)


def test_bad_index_rejected():
    with pytest.raises(ValueError, match="越界"):
        constrained_delaunay(np.zeros((3, 2)) + np.eye(3)[:, :2], [(0, 5)])


def _v_ditch():
    """沿 x = 50 的 V 形沟：z = 0.5·|x − 50|。沟底只有两头两个测点，两侧各一排测点。"""
    ys = np.arange(5.0, 100.0, 10.0)
    xy = np.vstack([[[50.0, 0.0], [50.0, 100.0]],
                    np.column_stack([np.full_like(ys, 40.0), ys]),
                    np.column_stack([np.full_like(ys, 60.0), ys])])
    z = 0.5 * np.abs(xy[:, 0] - 50.0)
    return xy, z


def test_breakline_makes_v_ditch_exact():
    xy, z = _v_ditch()
    rng = np.random.default_rng(1)
    query = np.column_stack([40 + rng.random(4000) * 20, 5 + rng.random(4000) * 90])
    exact = 0.5 * np.abs(query[:, 0] - 50.0)

    plain = Delaunay(xy)
    error_plain = np.abs(TinSurface(xy, plain.simplices, {"z": z}).interpolate(query, "z") - exact)
    assert np.nanmax(error_plain) > 1.0, "不加特征线时三角形横跨沟底，应有明显偏差"

    result = constrained_delaunay(xy, [(0, 1)], tol=1e-6)
    _assert_valid(result)
    surface = TinSurface(result.points, result.simplices, {"z": z})
    values = surface.interpolate(query, "z")
    assert not np.isnan(values).any()
    np.testing.assert_allclose(values, exact, atol=1e-9)


def test_tin_surface_matches_scipy_point_location():
    rng = np.random.default_rng(2)
    xy = rng.random((500, 2)) * 100
    z = np.sin(xy[:, 0] / 9) + xy[:, 1] / 30
    delaunay = Delaunay(xy)
    surface = TinSurface(xy, delaunay.simplices, {"z": z})
    query = rng.random((3000, 2)) * 110 - 5
    values = surface.interpolate(query, "z")
    simplex = delaunay.find_simplex(query)
    np.testing.assert_array_equal(np.isnan(values), simplex < 0)
    inside = simplex >= 0
    bary = np.einsum("ijk,ik->ij", delaunay.transform[simplex[inside], :2],
                     query[inside] - delaunay.transform[simplex[inside], 2])
    weights = np.column_stack([bary, 1 - bary.sum(axis=1)])
    expected = np.einsum("ij,ij->i", weights, z[delaunay.simplices[simplex[inside]]])
    np.testing.assert_allclose(values[inside], expected, atol=1e-12)
