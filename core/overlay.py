"""
两个三角网的叠加（overlay）：两期土方对比用。

前期、后期各自是分片线性曲面，但折线（三角形边）位置不同。把两个三角网叠在一起，
每个前期三角形与每个相交的后期三角形求交，得到的凸多边形内两期地面都是线性的，
高差也就是线性的；再扇形剖分成三角形，交给现有的 TIN 方量计算，结果是精确的。
"""
import math
from collections import defaultdict
from typing import List, Sequence, Tuple

import numpy as np

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


def overlay_triangulations(
    tri_a: np.ndarray, tri_b: np.ndarray, area_tol: float
) -> List[Tuple[List[XY], int, int]]:
    """返回 [(交集多边形, 前期三角形序号, 后期三角形序号)]，面积不大于 area_tol 的碎片丢弃。"""
    tri_a = np.asarray(tri_a, dtype=float).reshape(-1, 3, 2)
    tri_b = np.asarray(tri_b, dtype=float).reshape(-1, 3, 2)
    if not len(tri_a) or not len(tri_b):
        return []

    b_min = tri_b.min(axis=1)
    b_max = tri_b.max(axis=1)
    size = np.median(np.maximum(b_max - b_min, 0).max(axis=1))
    cell = float(size) if size > 0 else 1.0

    # 均匀网格索引后期三角形，按包围盒查询候选
    buckets = defaultdict(list)
    lo = np.floor(b_min / cell).astype(np.int64)
    hi = np.floor(b_max / cell).astype(np.int64)
    for index in range(len(tri_b)):
        for i in range(lo[index, 0], hi[index, 0] + 1):
            for j in range(lo[index, 1], hi[index, 1] + 1):
                buckets[(i, j)].append(index)

    clip_b = [_ccw([tuple(p) for p in tri.tolist()]) for tri in tri_b]
    b_min_list = b_min.tolist()
    b_max_list = b_max.tolist()
    cells: List[Tuple[List[XY], int, int]] = []
    for ia, triangle in enumerate(tri_a):
        subject = _ccw([tuple(p) for p in triangle.tolist()])
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
