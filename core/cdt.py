"""
约束 Delaunay 三角剖分（特征线 / 地性线构网）。

先用 scipy 得到普通 Delaunay，再逐条插入约束边：删除被约束边穿过的三角形，
约束边两侧的空腔各自按 Delaunay 准则重新三角化（Anglada 1997 的伪多边形法）。
结果中每条约束边都是三角网的边，不受约束的部分仍是 Delaunay 三角网。

插入前先整理约束边：
- 恰好经过某个测点（距离不超过容差）的约束边在该点处拆开；
- 两条约束边在内部相交时，在交点处新增顶点并把两条边都拆开。
新增顶点记录它位于哪条（输入）约束边的什么位置，调用方据此给出高程。

所有坐标应为局部坐标（已减去原点），容差为绝对长度。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.spatial import Delaunay, QhullError

Edge = Tuple[int, int]


@dataclass
class AddedVertex:
    """约束边相交处新增的顶点：parents 为 [(输入约束边序号, 沿该边的参数 t ∈ (0, 1)), ...]。"""
    index: int
    parents: List[Tuple[int, float]]


@dataclass
class ConstrainedTriangulation:
    points: np.ndarray                    # (n + k, 2)：输入点 + 新增交点
    simplices: np.ndarray                 # (m, 3)，逆时针
    constrained_edges: Set[Edge] = field(default_factory=set)  # (小序号, 大序号)
    added: List[AddedVertex] = field(default_factory=list)
    coplanar: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))  # 未参与构网的点


def _orient(p, q, r) -> float:
    """>0：r 在 p→q 左侧。"""
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _in_circle(a, b, c, d) -> float:
    """a、b、c 逆时针时，>0 表示 d 在其外接圆内。"""
    adx, ady = a[0] - d[0], a[1] - d[1]
    bdx, bdy = b[0] - d[0], b[1] - d[1]
    cdx, cdy = c[0] - d[0], c[1] - d[1]
    ad = adx * adx + ady * ady
    bd = bdx * bdx + bdy * bdy
    cd = cdx * cdx + cdy * cdy
    return (adx * (bdy * cd - bd * cdy)
            - ady * (bdx * cd - bd * cdx)
            + ad * (bdx * cdy - bdy * cdx))


def _segment_crossing(p1, p2, q1, q2, tol: float) -> Optional[Tuple[float, float]]:
    """两线段在双方内部（离端点超过 tol）真正相交时返回 (t, u)，否则 None。"""
    r = p2 - p1
    s = q2 - q1
    denom = r[0] * s[1] - r[1] * s[0]
    if denom == 0.0:
        return None
    qp = q1 - p1
    t = (qp[0] * s[1] - qp[1] * s[0]) / denom
    u = (qp[0] * r[1] - qp[1] * r[0]) / denom
    len_r = float(np.hypot(*r))
    len_s = float(np.hypot(*s))
    if t * len_r <= tol or (1 - t) * len_r <= tol or u * len_s <= tol or (1 - u) * len_s <= tol:
        return None
    return t, u


def _nearby(cells: Dict[Tuple[int, int], List[int]], points: List[np.ndarray], point: np.ndarray,
            tol: float) -> Optional[int]:
    cx, cy = int(point[0] // tol), int(point[1] // tol)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for k in cells.get((cx + dx, cy + dy), ()):
                if np.hypot(*(points[k] - point)) <= tol:
                    return k
    return None


def prepare_segments(
    xy: np.ndarray, segments: Sequence[Edge], tol: float
) -> Tuple[np.ndarray, List[Edge], List[AddedVertex]]:
    """整理约束边：在交点处加点，在经过的测点处拆开。

    返回 (扩充后的点, 拆分后互不交叉、内部不含任何点的约束边, 新增顶点)。
    """
    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    segs: List[Edge] = []
    source: List[int] = []  # segs[k] 对应的输入约束边序号
    for k, (a, b) in enumerate(segments):
        a, b = int(a), int(b)
        if not (0 <= a < len(xy) and 0 <= b < len(xy)):
            raise ValueError(f"约束边端点序号越界：({a}, {b})")
        if a != b and np.hypot(*(xy[b] - xy[a])) > tol:
            segs.append((a, b))
            source.append(k)

    # 1. 约束边两两求交，交点作为新顶点（相同位置的交点合并）
    new_points: List[np.ndarray] = []
    added: List[AddedVertex] = []
    cells: Dict[Tuple[int, int], List[int]] = {}  # 按容差分格，查找已有的同位置交点
    for i in range(len(segs)):
        p1, p2 = xy[segs[i][0]], xy[segs[i][1]]
        lo_i = np.minimum(p1, p2) - tol
        hi_i = np.maximum(p1, p2) + tol
        for j in range(i + 1, len(segs)):
            q1, q2 = xy[segs[j][0]], xy[segs[j][1]]
            if np.any(np.maximum(q1, q2) < lo_i) or np.any(np.minimum(q1, q2) > hi_i):
                continue
            hit = _segment_crossing(p1, p2, q1, q2, tol)
            if hit is None:
                continue
            t, u = hit
            point = p1 + t * (p2 - p1)
            parents = [(source[i], t), (source[j], u)]
            vertex = _nearby(cells, new_points, point, tol)
            if vertex is not None:
                known = {k for k, _ in added[vertex].parents}
                added[vertex].parents.extend(item for item in parents if item[0] not in known)
            else:
                cell = (int(point[0] // tol), int(point[1] // tol))
                cells.setdefault(cell, []).append(len(new_points))
                added.append(AddedVertex(index=len(xy) + len(new_points), parents=parents))
                new_points.append(point)
    points = np.vstack([xy] + [p.reshape(1, 2) for p in new_points]) if new_points else xy.copy()

    # 2. 在约束边内部经过的点（测点、交点、其他约束边的端点）处拆开
    result: Set[Edge] = set()
    for a, b in segs:
        pa, pb = points[a], points[b]
        direction = pb - pa
        length = float(np.hypot(*direction))
        unit = direction / length
        rel = points - pa
        along = rel @ unit
        across = np.abs(rel[:, 0] * unit[1] - rel[:, 1] * unit[0])
        on = np.nonzero((across <= tol) & (along > tol) & (along < length - tol))[0]
        chain = [a] + [int(k) for k in on[np.argsort(along[on])]] + [b]
        for u, v in zip(chain, chain[1:]):
            if u != v:
                result.add((min(u, v), max(u, v)))
    return points, sorted(result), added


class _Mesh:
    """以有向边表示的三角网：half[(u, v)] = w 表示存在逆时针三角形 (u, v, w)。"""

    def __init__(self, points: np.ndarray, simplices: np.ndarray):
        self.p = points
        self.half: Dict[Edge, int] = {}
        self.out: Dict[int, Set[int]] = {}
        for tri in simplices.tolist():
            a, b, c = tri
            if _orient(points[a], points[b], points[c]) < 0:
                b, c = c, b
            self._add(a, b, c)

    def _add(self, a: int, b: int, c: int):
        for u, v, w in ((a, b, c), (b, c, a), (c, a, b)):
            self.half[(u, v)] = w
            self.out.setdefault(u, set()).add(v)

    def _remove(self, a: int, b: int, c: int):
        for u, v in ((a, b), (b, c), (c, a)):
            del self.half[(u, v)]
            self.out[u].discard(v)

    def has_edge(self, a: int, b: int) -> bool:
        return (a, b) in self.half or (b, a) in self.half

    def insert(self, a: int, b: int, tol: float):
        if self.has_edge(a, b):
            return
        p = self.p
        pa, pb = p[a], p[b]
        # 找出从 a 出发、被 a→b 穿过的第一个三角形 (a, r, l)
        start = None
        for v in self.out.get(a, ()):
            w = self.half[(a, v)]
            if _orient(pa, p[v], pb) > 0 and _orient(pa, p[w], pb) < 0:
                start = (v, w)
                break
        if start is None:
            raise ValueError("约束边无法插入三角网：端点不在网中，或约束边与测点共线")
        r, l = start
        crossed = [(a, r, l)]
        right = [r]
        left = [l]
        while True:
            x = self.half.get((l, r))
            if x is None:
                raise ValueError("约束边超出三角网范围")
            crossed.append((l, r, x))
            if x == b:
                break
            side = _orient(pa, pb, p[x])
            length = float(np.hypot(*(pb - pa)))
            if abs(side) <= tol * length:
                raise ValueError("约束边经过一个未拆分的测点（共线），请检查特征线")
            if side < 0:
                r = x
                right.append(x)
            else:
                l = x
                left.append(x)
        for tri in crossed:
            self._remove(*tri)
        self._fill(a, b, left[::-1])
        self._fill(b, a, right)

    def _fill(self, u: int, v: int, chain: List[int]):
        """逆时针多边形 u→v→chain[0]→…→chain[-1]→u 的约束 Delaunay 三角化（用栈代替递归）。"""
        p = self.p
        stack = [(u, v, chain)]
        while stack:
            u, v, chain = stack.pop()
            if not chain:
                continue
            best = 0
            for i in range(1, len(chain)):
                if _in_circle(p[u], p[v], p[chain[best]], p[chain[i]]) > 0:
                    best = i
            c = chain[best]
            self._add(u, v, c)
            stack.append((c, v, chain[:best]))
            stack.append((u, c, chain[best + 1:]))

    def simplices(self) -> np.ndarray:
        """每个三角形只取一次（以最小序号顶点开头），保持逆时针。"""
        tris = [(u, v, w) for (u, v), w in self.half.items() if u < v and u < w]
        return np.array(sorted(tris), dtype=np.int64).reshape(-1, 3)


def constrained_delaunay(
    xy: np.ndarray, segments: Sequence[Edge] = (), tol: float = 1e-3
) -> ConstrainedTriangulation:
    """对点集 xy 做约束 Delaunay 三角剖分，segments 为必须保留为三角网边的点对。

    没有约束边时结果与 scipy.spatial.Delaunay 完全相同。
    """
    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    points, edges, added = prepare_segments(xy, segments, tol)
    try:
        delaunay = Delaunay(points)
    except QhullError as exc:
        raise ValueError("测量点共线、重复或分布不足以构成三角形，请检查坐标") from exc
    coplanar = delaunay.coplanar[:, 0].astype(np.int64) if len(delaunay.coplanar) else np.zeros(0, dtype=np.int64)
    if not edges:
        return ConstrainedTriangulation(points, delaunay.simplices.astype(np.int64), set(), added, coplanar)
    skipped = set(coplanar.tolist())
    bad = sorted({k for edge in edges for k in edge if k in skipped})
    if bad:
        raise ValueError(f"特征线端点与相邻测点距离过近，未参与构网（点序号 {bad[:10]}）")
    mesh = _Mesh(points, delaunay.simplices)
    for a, b in edges:
        mesh.insert(a, b, tol)
    return ConstrainedTriangulation(points, mesh.simplices(), set(edges), added, coplanar)
