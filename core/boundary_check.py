"""计算边界的检查：交叉、重复点、测点覆盖，以及把交叉的连线顺序理顺。"""
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from core.geometry import _orientation, clip_ring_halfplane, signed_area

XY = Tuple[float, float]


def find_crossings(ring: Sequence[XY]) -> List[Tuple[int, int]]:
    """互相交叉的边（i, j），i < j；第 i 条边连接第 i 点和下一点，最后一条回到起点。

    判定与 ring_self_intersects 相同：只算严格相交，相邻的边不算。
    """
    points = np.asarray(ring, dtype=float).reshape(-1, 2)
    count = len(points)
    crossings: List[Tuple[int, int]] = []
    if count < 4:
        return crossings
    starts = points
    ends = np.roll(points, -1, axis=0)
    for i in range(count - 2):
        others = np.arange(i + 2, count)
        if i == 0:
            others = others[others != count - 1]
        if not len(others):
            continue
        c, d = starts[others], ends[others]
        a, b = starts[i], ends[i]
        hit = (_orientation(a, b, c) * _orientation(a, b, d) < 0) & (_orientation(c, d, a) * _orientation(c, d, b) < 0)
        crossings.extend((i, int(j)) for j in others[hit])
    return crossings


def duplicate_vertices(ring: Sequence[XY]) -> List[int]:
    """与前面某个点重合的顶点序号（第二次及以后出现的）。"""
    seen = set()
    duplicates = []
    for index, point in enumerate(ring):
        key = (float(point[0]), float(point[1]))
        if key in seen:
            duplicates.append(index)
        seen.add(key)
    return duplicates


def untangle(ring: Sequence[XY]) -> List[XY]:
    """在不增删点的前提下调整连线顺序，直到没有交叉（2-opt）。

    每次把一对交叉的边换成不交叉的连法（中间一段倒过来），周长严格变短，所以一定会停下。
    起点不变；原来的顺序大体对、只有几处接错时，改动也只在出错的地方。
    """
    points = [(float(x), float(y)) for x, y in ring]
    count = len(points)
    if len(set(points)) != count:
        raise ValueError("边界里有重复的点，先删掉重复点再理顺")
    for _ in range(max(1, 4 * count * count)):
        crossings = find_crossings(points)
        if not crossings:
            return points
        i, j = crossings[0]
        points[i + 1:j + 1] = reversed(points[i + 1:j + 1])
    raise ValueError("连线顺序理不顺，请检查边界点")


def convex_hull(xy: Sequence[XY]) -> Optional[List[XY]]:
    """测点的凸包（逆时针）——三角网覆盖的范围；测点不足或共线时返回 None。"""
    points = np.unique(np.asarray(xy, dtype=float).reshape(-1, 2), axis=0)
    if len(points) < 3:
        return None
    origin = points.mean(axis=0)
    try:
        hull = ConvexHull(points - origin)
    except QhullError:
        return None
    return [(float(points[index, 0]), float(points[index, 1])) for index in hull.vertices]


def covered_area(ring: Sequence[XY], hull: Sequence[XY]) -> float:
    """边界内落在凸包（测点覆盖范围）里的面积。"""
    boundary = np.asarray(ring, dtype=float).reshape(-1, 2)
    region = np.asarray(hull, dtype=float).reshape(-1, 2)
    if len(boundary) < 3 or len(region) < 3:
        return 0.0
    origin = region.mean(axis=0)        # 以凸包中心为原点，大地坐标下也不丢精度
    clipped = boundary - origin
    region = region - origin
    if signed_area(region) < 0:
        region = region[::-1]
    for start, end in zip(region, np.roll(region, -1, axis=0)):
        # 逆时针凸包的每条边，保留左侧：a·x + b·y + c >= 0
        a, b = start[1] - end[1], end[0] - start[0]
        clipped = clip_ring_halfplane(clipped, a, b, -(a * start[0] + b * start[1]))
        if not len(clipped):
            return 0.0
    return abs(signed_area(clipped))


def edge_lengths(ring: Sequence[XY]) -> List[float]:
    """每个点到下一点的边长，最后一个是回到起点的闭合边。"""
    points = np.asarray(ring, dtype=float).reshape(-1, 2)
    if len(points) < 2:
        return []
    return [float(value) for value in np.hypot(*(np.roll(points, -1, axis=0) - points).T)]
