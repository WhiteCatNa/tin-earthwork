"""
两个三角网的叠加（overlay）：两期土方对比用。

前期、后期各自是分片线性曲面，但折线（三角形边）位置不同。把两个三角网叠在一起，
每个前期三角形与每个相交的后期三角形求交，得到的凸多边形内两期地面都是线性的，
高差也就是线性的；再扇形剖分成三角形，交给现有的 TIN 方量计算，结果是精确的。
"""
import math
from collections import defaultdict
from typing import List, NamedTuple, Sequence, Tuple

import numpy as np

from core import parallel

XY = Tuple[float, float]


def _ccw(triangle: Sequence[XY]) -> List[XY]:
    (x0, y0), (x1, y1), (x2, y2) = triangle
    if (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) < 0:
        return [(x0, y0), (x2, y2), (x1, y1)]
    return [(x0, y0), (x1, y1), (x2, y2)]


def clip_convex(subject: List[XY], clip: List[XY]) -> List[XY]:
    """Sutherland–Hodgman：凸多边形 subject 与逆时针凸多边形 clip 的交。"""
    output = subject
    count = len(clip)
    for i in range(count):
        if not output:
            break
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % count]
        ex, ey = bx - ax, by - ay
        points = output
        output = []
        px, py = points[-1]
        prev_side = ex * (py - ay) - ey * (px - ax)
        for cx, cy in points:
            side = ex * (cy - ay) - ey * (cx - ax)
            if side >= 0:
                if prev_side < 0 and side > 0:
                    t = prev_side / (prev_side - side)
                    output.append((px + t * (cx - px), py + t * (cy - py)))
                output.append((cx, cy))
            elif prev_side > 0:
                t = prev_side / (prev_side - side)
                output.append((px + t * (cx - px), py + t * (cy - py)))
            px, py, prev_side = cx, cy, side
    return output


def _polygon_area(poly: Sequence[XY]) -> float:
    total = 0.0
    count = len(poly)
    for i in range(count):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2


class _TriangleIndex:
    """后期三角形的均匀网格索引，按包围盒查询候选。"""

    def __init__(self, tri_b: np.ndarray):
        b_min = tri_b.min(axis=1)
        b_max = tri_b.max(axis=1)
        size = np.median(np.maximum(b_max - b_min, 0).max(axis=1))
        self.cell = float(size) if size > 0 else 1.0
        self.buckets = defaultdict(list)
        lo = np.floor(b_min / self.cell).astype(np.int64)
        hi = np.floor(b_max / self.cell).astype(np.int64)
        for index in range(len(tri_b)):
            for i in range(lo[index, 0], hi[index, 0] + 1):
                for j in range(lo[index, 1], hi[index, 1] + 1):
                    self.buckets[(i, j)].append(index)
        self.clip_b = [_ccw([tuple(p) for p in tri.tolist()]) for tri in tri_b]
        self.b_min = b_min.tolist()
        self.b_max = b_max.tolist()


def _overlay_range(tri_a: np.ndarray, rows: range, index: _TriangleIndex,
                   area_tol: float) -> List[Tuple[List[XY], int, int]]:
    cell, buckets, clip_b = index.cell, index.buckets, index.clip_b
    b_min_list, b_max_list = index.b_min, index.b_max
    cells: List[Tuple[List[XY], int, int]] = []
    for ia in rows:
        subject = _ccw([tuple(p) for p in tri_a[ia].tolist()])
        xs = [p[0] for p in subject]
        ys = [p[1] for p in subject]
        axmin, axmax, aymin, aymax = min(xs), max(xs), min(ys), max(ys)
        candidates = set()
        for i in range(math.floor(axmin / cell), math.floor(axmax / cell) + 1):
            for j in range(math.floor(aymin / cell), math.floor(aymax / cell) + 1):
                candidates.update(buckets.get((i, j), ()))
        for ib in sorted(candidates):
            (bxmin, bymin), (bxmax, bymax) = b_min_list[ib], b_max_list[ib]
            if bxmax < axmin or bxmin > axmax or bymax < aymin or bymin > aymax:
                continue
            polygon = clip_convex(subject, clip_b[ib])
            if len(polygon) >= 3 and _polygon_area(polygon) > area_tol:
                cells.append((polygon, ia, ib))
    return cells


class OverlayCells(NamedTuple):
    """叠加结果，按 (前期三角形序号, 后期三角形序号) 排序。

    第 k 个交集多边形的顶点是 points[offsets[k]:offsets[k] + sizes[k]]。
    """
    points: np.ndarray   # (顶点总数, 2)
    sizes: np.ndarray    # 每个多边形的顶点数
    tri_a: np.ndarray    # 所在前期三角形序号
    tri_b: np.ndarray    # 所在后期三角形序号

    @property
    def count(self) -> int:
        return len(self.sizes)


def _pack(cells: List[Tuple[List[XY], int, int]]) -> OverlayCells:
    """多边形列表转为紧凑数组（浮点值不变），便于进程间传输和批量处理。"""
    count = len(cells)
    return OverlayCells(
        points=np.array([point for polygon, _, _ in cells for point in polygon], dtype=float).reshape(-1, 2),
        sizes=np.fromiter((len(polygon) for polygon, _, _ in cells), dtype=np.int64, count=count),
        tri_a=np.fromiter((ia for _, ia, _ in cells), dtype=np.int64, count=count),
        tri_b=np.fromiter((ib for _, _, ib in cells), dtype=np.int64, count=count),
    )


def _overlay_task(task) -> OverlayCells:
    """子进程任务：前期三角形的一个连续区段与全部后期三角形求交。"""
    tri_a, offset, tri_b, area_tol = task
    cells = _pack(_overlay_range(tri_a, range(len(tri_a)), _TriangleIndex(tri_b), area_tol))
    return cells._replace(tri_a=cells.tri_a + offset)


def overlay_triangulations(tri_a: np.ndarray, tri_b: np.ndarray, area_tol: float) -> OverlayCells:
    """前期、后期三角网求交，面积不大于 area_tol 的碎片丢弃。

    三角形多时按前期三角形分段多进程计算，拼接后与串行结果逐位一致。
    """
    tri_a = np.asarray(tri_a, dtype=float).reshape(-1, 3, 2)
    tri_b = np.asarray(tri_b, dtype=float).reshape(-1, 3, 2)
    if not len(tri_a) or not len(tri_b):
        return _pack([])

    if parallel.use_processes(len(tri_a) + len(tri_b)):
        # 每个进程只分一段：每段都要重建后期三角形索引
        tasks = [(tri_a[rows.start:rows.stop], rows.start, tri_b, area_tol)
                 for rows in parallel.chunk_bounds(len(tri_a), parallel.worker_count())]
        parts = parallel.process_map(_overlay_task, tasks)
        return OverlayCells(*(np.concatenate(column) for column in zip(*parts)))
    return _pack(_overlay_range(tri_a, range(len(tri_a)), _TriangleIndex(tri_b), area_tol))
