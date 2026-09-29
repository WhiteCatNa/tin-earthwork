"""
平面几何工具：多边形面积与积分、半平面裁剪、点在多边形内、边界相交检测。

大多边形（计算边界）用 numpy 向量化处理；三角形级的小多边形用纯 Python，
避免 numpy 小数组的调用开销。
"""
from typing import List, Sequence, Tuple

import numpy as np

XY = Tuple[float, float]


# ---------------------------------------------------------------------------
# 小多边形（纯 Python）
# ---------------------------------------------------------------------------

def polygon_integral(poly: Sequence[XY], values: Sequence[float]) -> Tuple[float, float]:
    """返回多边形的 (面积, ∫v dA)，v 为顶点上给定、在多边形内线性变化的函数。

    以第一个顶点作扇形剖分并保留有向面积，对凹多边形和 Sutherland–Hodgman
    产生的退化"桥边"同样精确；结果按整体方向取正。
    """
    if len(poly) < 3:
        return 0.0, 0.0
    x0, y0 = poly[0]
    v0 = values[0]
    area = 0.0
    integral = 0.0
    for i in range(1, len(poly) - 1):
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        part = ((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) / 2
        area += part
        integral += part * (v0 + values[i] + values[i + 1]) / 3
    if area < 0:
        return -area, -integral
    return area, integral


def clip_by_sign(
    poly: Sequence[XY], values: Sequence[float], positive: bool
) -> Tuple[List[XY], List[float]]:
    """按线性函数的正负裁剪多边形，保留 v>=0（positive）或 v<=0 的部分。"""
    out_points: List[XY] = []
    out_values: List[float] = []
    count = len(poly)
    for i in range(count):
        j = (i + 1) % count
        vi, vj = values[i], values[j]
        in_i = vi >= 0 if positive else vi <= 0
        in_j = vj >= 0 if positive else vj <= 0
        if in_i:
            out_points.append(poly[i])
            out_values.append(vi)
        # 端点恰为 0 时交点就是端点本身，已经（或即将）加入
        if in_i != in_j and vi != 0 and vj != 0:
            t = vi / (vi - vj)
            (xi, yi), (xj, yj) = poly[i], poly[j]
            out_points.append((xi + t * (xj - xi), yi + t * (yj - yi)))
            out_values.append(0.0)
    return out_points, out_values


def zero_crossing_points(poly: Sequence[XY], values: Sequence[float]) -> List[XY]:
    """凸多边形边上线性函数为 0 的点（去重）；对挖填混合的三角形恰好两个。"""
    points: List[XY] = []
    count = len(poly)
    for i in range(count):
        j = (i + 1) % count
        vi, vj = values[i], values[j]
        if vi == 0:
            candidate = poly[i]
        elif (vi > 0 > vj) or (vi < 0 < vj):
            t = vi / (vi - vj)
            (xi, yi), (xj, yj) = poly[i], poly[j]
            candidate = (xi + t * (xj - xi), yi + t * (yj - yi))
        else:
            continue
        if candidate not in points:
            points.append(candidate)
    return points


def linear_plane(p0: XY, p1: XY, p2: XY, v0: float, v1: float, v2: float, eps: float):
    """由三角形三顶点求线性函数 v = a*x + b*y + c；三角形退化时返回 None。"""
    x1, y1 = p1[0] - p0[0], p1[1] - p0[1]
    x2, y2 = p2[0] - p0[0], p2[1] - p0[1]
    det = x1 * y2 - x2 * y1
    if abs(det) <= eps:
        return None
    a = ((v1 - v0) * y2 - (v2 - v0) * y1) / det
    b = ((v2 - v0) * x1 - (v1 - v0) * x2) / det
    return a, b, v0 - a * p0[0] - b * p0[1]


# ---------------------------------------------------------------------------
# 计算边界（numpy）
# ---------------------------------------------------------------------------

def signed_area(ring: np.ndarray) -> float:
    """多边形有向面积，逆时针为正。"""
    if len(ring) < 3:
        return 0.0
    x = ring[:, 0]
    y = ring[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) / 2


def normalize_ring(points: Sequence[XY]) -> np.ndarray:
    """去掉首尾重复与相邻重复顶点，并统一为逆时针方向。"""
    ring = [tuple(map(float, point)) for point in points]
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    cleaned: List[XY] = []
    for point in ring:
        if not cleaned or cleaned[-1] != point:
            cleaned.append(point)
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1]:
        cleaned.pop()
    array = np.array(cleaned, dtype=float).reshape(-1, 2)
    if signed_area(array) < 0:
        array = array[::-1].copy()
    return array


def _orientation(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> np.ndarray:
    value = (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
    return np.sign(value)


def segments_cross(a: XY, b: XY, c: XY, d: XY) -> bool:
    """线段 ab 与 cd 是否严格相交（仅端点接触不算）。"""
    a, b, c, d = (np.asarray(point, dtype=float) for point in (a, b, c, d))
    return bool(
        _orientation(a, b, c) * _orientation(a, b, d) < 0
        and _orientation(c, d, a) * _orientation(c, d, b) < 0
    )


def ring_self_intersects(points: Sequence[XY]) -> bool:
    """闭合多边形是否有不相邻的边严格相交。"""
    ring = np.asarray(points, dtype=float).reshape(-1, 2)
    count = len(ring)
    if count < 4:
        return False
    starts = ring
    ends = np.roll(ring, -1, axis=0)
    for i in range(count - 2):
        others = np.arange(i + 2, count)
        if i == 0:
            others = others[others != count - 1]
        if not len(others):
            continue
        c, d = starts[others], ends[others]
        a, b = starts[i], ends[i]
        crossing = (_orientation(a, b, c) * _orientation(a, b, d) < 0) & (
            _orientation(c, d, a) * _orientation(c, d, b) < 0
        )
        if crossing.any():
            return True
    return False


def points_in_ring(xy: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """射线法批量判断点是否在多边形内。

    点按 y 排序后，每条边只处理 y 落在该边纵向范围内的点，
    总工作量约为 点数 × 水平线穿过边界的次数。
    """
    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    if not len(xy) or len(ring) < 3:
        return np.zeros(len(xy), dtype=bool)
    order = np.argsort(xy[:, 1], kind="stable")
    xs = xy[order, 0]
    ys = xy[order, 1]
    inside_sorted = np.zeros(len(xy), dtype=bool)
    for (x1, y1), (x2, y2) in zip(ring, np.roll(ring, -1, axis=0)):
        if y1 == y2:
            continue
        low, high = (y1, y2) if y1 < y2 else (y2, y1)
        start = np.searchsorted(ys, low, side="left")
        stop = np.searchsorted(ys, high, side="left")
        if start >= stop:
            continue
        x_cross = (x2 - x1) * (ys[start:stop] - y1) / (y2 - y1) + x1
        inside_sorted[start:stop] ^= xs[start:stop] < x_cross
    inside = np.empty_like(inside_sorted)
    inside[order] = inside_sorted
    return inside


def clip_ring_halfplane(poly: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    """Sutherland–Hodgman：保留 a*x + b*y + c >= 0 的部分（向量化）。"""
    if not len(poly):
        return poly
    side = poly[:, 0] * a + poly[:, 1] * b + c
    inside = side >= 0
    if inside.all():
        return poly
    if not inside.any():
        return poly[:0]
    following = np.roll(np.arange(len(poly)), -1)
    crossing = inside != inside[following]
    t = np.zeros(len(poly))
    t[crossing] = side[crossing] / (side[crossing] - side[following][crossing])
    intersections = poly + t[:, None] * (poly[following] - poly)
    stacked = np.stack([poly, intersections], axis=1).reshape(-1, 2)
    keep = np.stack([inside, crossing], axis=1).reshape(-1)
    return stacked[keep]


def clip_ring_to_triangle(ring: np.ndarray, triangle: Sequence[XY]) -> np.ndarray:
    """计算边界（可为凹多边形）与三角形的交。

    结果可能含零宽度的退化边，但面积和线性函数积分精确。
    """
    p0, p1, p2 = (np.asarray(point, dtype=float) for point in triangle)
    if (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0]) < 0:
        p1, p2 = p2, p1
    poly = ring
    for start, end in ((p0, p1), (p1, p2), (p2, p0)):
        ex, ey = end - start
        poly = clip_ring_halfplane(poly, -ey, ex, ey * start[0] - ex * start[1])
        if not len(poly):
            break
    return poly


def _segment_hits_triangles(start: np.ndarray, end: np.ndarray, triangles: np.ndarray, eps: float) -> np.ndarray:
    """分离轴法判断线段与一批三角形是否接触（含边界，放宽 eps）。"""
    hit = np.ones(len(triangles), dtype=bool)
    segment = np.stack([start, end])
    direction = end - start
    for axis in (np.array([-direction[1], direction[0]]), direction):
        length = float(np.hypot(*axis))
        if length == 0:
            continue
        axis = axis / length
        seg_proj = segment @ axis
        tri_proj = triangles @ axis
        hit &= (tri_proj.max(axis=1) >= seg_proj.min() - eps) & (tri_proj.min(axis=1) <= seg_proj.max() + eps)
    for i in range(3):
        edge = triangles[:, (i + 1) % 3] - triangles[:, i]
        normal = np.stack([-edge[:, 1], edge[:, 0]], axis=1)
        length = np.hypot(normal[:, 0], normal[:, 1])
        length[length == 0] = 1.0
        normal /= length[:, None]
        tri_proj = np.einsum("kij,kj->ki", triangles, normal)
        seg_proj = segment @ normal.T
        hit &= (tri_proj.max(axis=1) >= seg_proj.min(axis=0) - eps) & (
            tri_proj.min(axis=1) <= seg_proj.max(axis=0) + eps
        )
    return hit


def triangles_touching_ring(triangles: np.ndarray, ring: np.ndarray, eps: float) -> np.ndarray:
    """找出与边界线接触的三角形（保守：宁多勿漏，多出的只会多做一次精确裁剪）。

    三角形按 xmin 排序，每条边界边只检查 x 方向滑窗内的三角形；
    个别特别宽的三角形（凸包边缘的狭长三角形）单独全量检查。
    """
    count = len(triangles)
    touched = np.zeros(count, dtype=bool)
    if not count or len(ring) < 2:
        return touched
    xmin = triangles[:, :, 0].min(axis=1)
    xmax = triangles[:, :, 0].max(axis=1)
    ymin = triangles[:, :, 1].min(axis=1)
    ymax = triangles[:, :, 1].max(axis=1)
    width = xmax - xmin
    limit = float(np.percentile(width, 99))
    wide = np.nonzero(width > limit)[0]
    narrow = np.nonzero(width <= limit)[0]
    order = narrow[np.argsort(xmin[narrow], kind="stable")]
    sorted_xmin = xmin[order]

    for start, end in zip(ring, np.roll(ring, -1, axis=0)):
        exmin, exmax = min(start[0], end[0]) - eps, max(start[0], end[0]) + eps
        eymin, eymax = min(start[1], end[1]) - eps, max(start[1], end[1]) + eps
        lo = np.searchsorted(sorted_xmin, exmin - limit, side="left")
        hi = np.searchsorted(sorted_xmin, exmax, side="right")
        candidates = np.concatenate([order[lo:hi], wide])
        overlap = (
            (xmax[candidates] >= exmin)
            & (xmin[candidates] <= exmax)
            & (ymax[candidates] >= eymin)
            & (ymin[candidates] <= eymax)
            & ~touched[candidates]
        )
        candidates = candidates[overlap]
        if not len(candidates):
            continue
        hit = _segment_hits_triangles(start, end, triangles[candidates], eps)
        touched[candidates[hit]] = True
    return touched


def clip_segment_to_ring(start: XY, end: XY, ring: np.ndarray) -> List[Tuple[XY, XY]]:
    """把线段裁剪到多边形内部，返回位于多边形内的子线段。"""
    p = np.asarray(start, dtype=float)
    q = np.asarray(end, dtype=float)
    direction = q - p
    a = ring
    b = np.roll(ring, -1, axis=0)
    edge = b - a
    denom = direction[0] * edge[:, 1] - direction[1] * edge[:, 0]
    diff = a - p
    params = [0.0, 1.0]
    valid = denom != 0
    if valid.any():
        t = (diff[valid, 0] * edge[valid, 1] - diff[valid, 1] * edge[valid, 0]) / denom[valid]
        u = (diff[valid, 0] * direction[1] - diff[valid, 1] * direction[0]) / denom[valid]
        inside = (t > 0) & (t < 1) & (u >= 0) & (u <= 1)
        params.extend(t[inside].tolist())
    params = sorted(set(params))
    mids = np.array([p + direction * (t0 + t1) / 2 for t0, t1 in zip(params, params[1:])])
    if not len(mids):
        return []
    keep = points_in_ring(mids, ring)
    pieces: List[Tuple[XY, XY]] = []
    for (t0, t1), inside_piece in zip(zip(params, params[1:]), keep):
        if not inside_piece:
            continue
        a_point = p + direction * t0
        b_point = p + direction * t1
        piece = ((float(a_point[0]), float(a_point[1])), (float(b_point[0]), float(b_point[1])))
        if pieces and pieces[-1][1] == piece[0]:
            pieces[-1] = (pieces[-1][0], piece[1])
        else:
            pieces.append(piece)
    return pieces
