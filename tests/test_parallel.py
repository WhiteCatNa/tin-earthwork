"""多进程计算与串行计算结果逐位一致。"""
import gc
import math

import numpy as np
import pytest

from core import parallel
from core.calculator import SurveyPoint, TINEarthworkCalculator, _fan_triangles
from core.grid_check import run_grid_check


def _points(count, seed, prefix="P"):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 200, (count, 2)) + [500000, 3000000]
    z = 10 + 2 * np.sin(xy[:, 0] / 20) + np.cos(xy[:, 1] / 30) + rng.normal(0, 0.2, count)
    return [SurveyPoint(f"{prefix}{i}", float(x), float(y), float(h)) for i, ((x, y), h) in enumerate(zip(xy, z))]


def _boundary():
    angles = np.linspace(0, 2 * math.pi, 60, endpoint=False)
    radius = 80 + 12 * np.sin(5 * angles)
    return [(500100 + r * math.cos(a), 3000100 + r * math.sin(a)) for a, r in zip(angles, radius)]


def _snapshot(result):
    return (
        result.total_cut, result.total_fill, result.computed_area, result.triangle_count,
        result.mixed_triangle_count, result.clipped_triangle_count, result.warnings,
        [(t.vertex_ids, t.area, t.volume, t.cut_volume, t.fill_volume, t.cut_area, t.fill_area,
          t.avg_delta_z, t.is_mixed, t.is_clipped, t.is_boundary, t.cut_polygon, t.fill_polygon,
          t.zero_segments) for t in result.triangles],
    )


def _run(compare):
    calculator = TINEarthworkCalculator(10.0)
    calculator.add_points(_points(1500, 1))
    if compare:
        calculator.set_compare_points(_points(1500, 2, "B"))
    calculator.set_boundary(_boundary())
    result = calculator.run_full_calculation()
    grid = run_grid_check(calculator, result, 4.0)
    return _snapshot(result), _snapshot(grid.mesh_result), [
        (vertex.id, vertex.x, vertex.y, vertex.z, vertex.design_z) for vertex in calculator.triangles[0].vertex_points
    ]


@pytest.mark.parametrize("compare", [False, True])
def test_process_pool_matches_serial(monkeypatch, compare):
    monkeypatch.setenv(parallel.WORKERS_ENV, "1")
    serial = _run(compare)

    # 降低阈值，让小数据也走进程池
    monkeypatch.setenv(parallel.WORKERS_ENV, "2")
    monkeypatch.setattr(parallel, "MIN_PARALLEL_TRIANGLES", 1)
    monkeypatch.setattr(parallel, "MIN_PARALLEL_CLIPS", 1)
    calls = []
    original = parallel.process_map
    monkeypatch.setattr(parallel, "process_map", lambda func, tasks: calls.append(func) or original(func, tasks))
    try:
        assert _run(compare) == serial
    finally:
        parallel.shutdown()
    assert calls, "应当走了进程池"
    assert not parallel._process_disabled


def test_process_map_falls_back_to_serial(monkeypatch):
    class Broken:
        def map(self, *args, **kwargs):
            raise OSError("no processes here")

    monkeypatch.setattr(parallel, "_get_executor", lambda workers: Broken())
    monkeypatch.setattr(parallel, "_process_disabled", False)
    assert parallel.process_map(abs, [-1, -2, 3]) == [1, 2, 3]
    assert parallel._process_disabled
    assert not parallel.use_processes(10 ** 9)


def test_worker_count_env(monkeypatch):
    monkeypatch.setenv(parallel.WORKERS_ENV, "3")
    assert parallel.worker_count() == 3
    monkeypatch.setenv(parallel.WORKERS_ENV, "abc")
    assert parallel.worker_count() == 1
    monkeypatch.setenv(parallel.WORKERS_ENV, "1")
    assert not parallel.use_processes(10 ** 9)


def test_chunk_bounds_cover_range():
    chunks = parallel.chunk_bounds(10, 4)
    assert [i for rows in chunks for i in rows] == list(range(10))
    assert parallel.chunk_bounds(2, 8) == [range(0, 1), range(1, 2)]


def test_gc_paused_nests_and_restores():
    assert gc.isenabled()
    with parallel.gc_paused():
        with parallel.gc_paused():
            assert not gc.isenabled()
        assert not gc.isenabled()
    assert gc.isenabled()


def test_fan_triangles_matches_ring_loop():
    rng = np.random.default_rng(3)
    sizes = rng.integers(3, 7, 200)
    index = rng.integers(0, 6, int(sizes.sum()))
    expected = []
    start = 0
    for size in sizes.tolist():
        ring = []
        for value in index[start:start + size].tolist():
            if not ring or ring[-1] != value:
                ring.append(value)
        start += size
        if len(ring) >= 2 and ring[0] == ring[-1]:
            ring.pop()
        expected.extend((ring[0], ring[i], ring[i + 1]) for i in range(1, len(ring) - 1))
    assert _fan_triangles(index, sizes).tolist() == [list(t) for t in expected]
