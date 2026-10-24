"""沿测点外轮廓生成计算边界。

从测点三角网的凸包出发，反复去掉“外边过长”的边缘三角形（χ-shape 算法），
得到一个贴着测点的单连通多边形：顶点全是测点，内部处处有三角网覆盖。
"""
import heapq
import math
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import Delaunay, QhullError

XY = Tuple[float, float]

# 默认“最长边”= 三角网边长的 95% 分位数 × 该倍数：规则测网上约为 2 倍点距，
# 凹角处会留下一个对角三角形；想完全贴合可把“最长边”改到略大于点距
DEFAULT_EDGE_FACTOR = 1.4
DEFAULT_EDGE_PERCENTILE = 95.0
# 三点叉积与两边长乘积之比小于该值时视为共线
COLLINEAR_TOLERANCE = 1e-10


def _triangulate(xy: Sequence[XY]) -> Tuple[np.ndarray, Delaunay]:
    """去重后构网，返回（去重后的原始坐标, 以形心为原点的三角网）。"""
    points = np.asarray(xy, dtype=float).reshape(-1, 2)
    if len(points) and not np.isfinite(points).all():
        raise ValueError("测点坐标中有空值或非数字")
    unique = np.unique(points, axis=0)
    if len(unique) < 3:
        raise ValueError("至少需要 3 个位置不同的测点才能生成外轮廓")
    try:
        return unique, Delaunay(unique - unique.mean(axis=0))
    except QhullError as exc:
        raise ValueError("测点全部在一条直线上，无法生成外轮廓") from exc


def _edge_lengths(delaunay: Delaunay) -> np.ndarray:
    simplices = delaunay.simplices
    edges = np.sort(np.concatenate([simplices[:, [0, 1]], simplices[:, [1, 2]], simplices[:, [2, 0]]]), axis=1)
    edges = np.unique(edges, axis=0)
    ends = delaunay.points[edges]
    return np.hypot(*(ends[:, 0] - ends[:, 1]).T)


def _round_up(value: float) -> float:
    """向上取成两位有效数字，界面上好读、好改。"""
    if value <= 0:
        return 0.0
    step = 10.0 ** (math.floor(math.log10(value)) - 1)
    return float(f"{math.ceil(value / step - 1e-9) * step:.10g}")


def _suggested_edge(delaunay: Delaunay) -> float:
    lengths = _edge_lengths(delaunay)
    return _round_up(DEFAULT_EDGE_FACTOR * float(np.percentile(lengths, DEFAULT_EDGE_PERCENTILE)))


def default_outline_edge(xy: Sequence[XY]) -> float:
    """按测点疏密给出“最长边”的建议值（米）。"""
    return _suggested_edge(_triangulate(xy)[1])


def _drop_collinear(ring: List[XY]) -> List[XY]:
    """去掉落在相邻两点连线上的中间点（不改变面积）。"""
    kept: List[XY] = []
    count = len(ring)
    for index, point in enumerate(ring):
        before, after = ring[index - 1], ring[(index + 1) % count]
        ax, ay = point[0] - before[0], point[1] - before[1]
        bx, by = after[0] - point[0], after[1] - point[1]
        cross = ax * by - ay * bx
        along = ax * bx + ay * by
        if abs(cross) > COLLINEAR_TOLERANCE * math.hypot(ax, ay) * math.hypot(bx, by) or along <= 0:
            kept.append(point)
    return kept if len(kept) >= 3 else ring


def outline_from_points(xy: Sequence[XY], max_edge: Optional[float] = None) -> List[XY]:
    """沿测点外轮廓生成边界多边形，参数见 outline_with_edge()。"""
    return outline_with_edge(xy, max_edge)[0]


def outline_with_edge(xy: Sequence[XY], max_edge: Optional[float] = None) -> Tuple[List[XY], float]:
    """沿测点外轮廓生成边界多边形，返回（多边形, 实际采用的最长边）。

    max_edge：轮廓上相邻两点的连线不超过这个长度（米），除非再去掉就会把区域切断。
    None 取 default_outline_edge() 的建议值；0 或负数不收缩，返回凸包。
    """
    unique, delaunay = _triangulate(xy)
    if max_edge is None:
        max_edge = _suggested_edge(delaunay)

    local = delaunay.points
    simplices = delaunay.simplices
    neighbors = delaunay.neighbors.copy()   # neighbors[t, k]：顶点 k 对面那条边另一侧的三角形，-1 为没有
    alive = np.ones(len(simplices), dtype=bool)
    on_outline = np.zeros(len(local), dtype=bool)

    def edge_length(tri: int, k: int) -> float:
        a, b = simplices[tri, (k + 1) % 3], simplices[tri, (k + 2) % 3]
        return float(math.hypot(*(local[a] - local[b])))

    heap: List[Tuple[float, int, int]] = []
    for tri, k in zip(*np.nonzero(neighbors == -1)):
        tri, k = int(tri), int(k)
        on_outline[simplices[tri, (k + 1) % 3]] = on_outline[simplices[tri, (k + 2) % 3]] = True
        heapq.heappush(heap, (-edge_length(tri, k), tri, k))

    if max_edge > 0:
        while heap:
            negative_length, tri, k = heapq.heappop(heap)
            if -negative_length <= max_edge:
                break
            apex = simplices[tri, k]
            if on_outline[apex]:
                continue   # 对面的顶点已在轮廓上：去掉这个三角形会把区域切断或挤成一个点相连
            alive[tri] = False
            on_outline[apex] = True
            for other in ((k + 1) % 3, (k + 2) % 3):
                inner = int(neighbors[tri, other])
                side = int(np.nonzero(neighbors[inner] == tri)[0][0])
                neighbors[inner, side] = -1
                heapq.heappush(heap, (-edge_length(inner, side), inner, side))

    # 把剩下的外边首尾相接连成环
    links = {}
    for tri, k in zip(*np.nonzero((neighbors == -1) & alive[:, None])):
        a, b = int(simplices[tri, (k + 1) % 3]), int(simplices[tri, (k + 2) % 3])
        links.setdefault(a, []).append(b)
        links.setdefault(b, []).append(a)
    start = min(links)
    order = [start]
    previous, current = None, start
    while True:
        first, second = links[current]
        following = second if first == previous else first
        if following == start:
            break
        order.append(following)
        previous, current = current, following

    ring = [(float(unique[index, 0]), float(unique[index, 1])) for index in order]
    return _drop_collinear(ring), float(max_edge)
