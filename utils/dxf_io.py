"""DXF 边界读写：解析 LWPOLYLINE / POLYLINE（含圆弧段），选出计算边界。"""
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


BOUNDARY_LAYER_HINTS = (
    "boundary",
    "计算边界",
    "用地边界",
    "用地界",
    "红线",
    "边界",
    "kz",
)

# 圆弧段折线化：插入的点偏离圆弧不超过该值（米），每小段圆心角不超过 ARC_MAX_STEP
ARC_SAGITTA_TOLERANCE = 0.005
ARC_MAX_STEP = math.pi / 8
# 单段弧的小段数上限，只为防异常数据；半径 10 km 的整圆约需 3100 段
ARC_MAX_SEGMENTS = 4096
# 弓高小于该值（米）的弧段按直线处理
ARC_MIN_SAGITTA = 1e-6


@dataclass
class DxfPolyline:
    layer: str = "0"
    closed: bool = False
    # 圆弧段已折线化
    points: List[Tuple[float, float]] = field(default_factory=list)
    arc_count: int = 0


def read_text_with_encodings(filepath: str) -> str:
    data = Path(filepath).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _iter_group_pairs(text: str) -> Iterable[Tuple[int, str]]:
    lines = text.splitlines()
    index = 0
    while index + 1 < len(lines):
        code_text = lines[index].strip()
        value = lines[index + 1].strip()
        try:
            yield int(code_text), value
        except ValueError:
            index += 1
            continue
        index += 2


def _as_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ring_points(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if len(points) >= 2 and points[0] == points[-1]:
        return points[:-1]
    return list(points)


def _bbox_area(points: List[Tuple[float, float]]) -> float:
    ring = _ring_points(points)
    if len(ring) < 2:
        return 0.0
    xs = [point[0] for point in ring]
    ys = [point[1] for point in ring]
    return max(0.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))


def _area_scale(sweep: float, count: int) -> float:
    """圆弧等分成 count 小段时，中间点所在同心圆与圆弧的半径比 r'/r，使折线与圆弧面积相等。

    扇形面积 r²·|θ|/2 = 两端三角形 r·r'·sinφ/2 × 2 + 中间三角形 r'²·sinφ/2 × (count − 2)，φ = |θ| / count。
    """
    sin_step = math.sin(abs(sweep) / count)
    if count == 2:
        return abs(sweep) / (2.0 * sin_step)
    a, b = (count - 2) * sin_step, 2.0 * sin_step
    return (math.sqrt(b * b + 4.0 * a * abs(sweep)) - b) / (2.0 * a)


def _arc_points(start: Tuple[float, float], end: Tuple[float, float], bulge: float) -> List[Tuple[float, float]]:
    """带凸度线段上插入的中间点（不含两端）。凸度 = tan(圆心角 / 4)，正值为逆时针。

    中间点取在比圆弧半径略大的同心圆上，使折线围出的面积与圆弧严格相等
    （点全落在圆弧上时折线内接，面积总是偏小）；折线偏离圆弧不超过 ARC_SAGITTA_TOLERANCE。
    """
    (x1, y1), (x2, y2) = start, end
    chord = math.hypot(x2 - x1, y2 - y1)
    if abs(bulge) * chord / 2.0 < ARC_MIN_SAGITTA:
        return []

    sweep = 4.0 * math.atan(bulge)
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    offset = (1.0 - bulge * bulge) / (4.0 * bulge)
    cx = (x1 + x2) / 2.0 - (y2 - y1) * offset
    cy = (y1 + y2) / 2.0 + (x2 - x1) * offset

    # 先按内接折线的弓高定小段数，保证折线向内不超限
    step = ARC_MAX_STEP
    if radius > ARC_SAGITTA_TOLERANCE:
        step = min(step, 2.0 * math.acos(1.0 - ARC_SAGITTA_TOLERANCE / radius))
    count = min(max(2, math.ceil(abs(sweep) / step)), ARC_MAX_SEGMENTS)
    # 小段数很少时中间点外移较多，再加密到向外也不超限
    scale = _area_scale(sweep, count)
    while radius * (scale - 1.0) > ARC_SAGITTA_TOLERANCE and count < ARC_MAX_SEGMENTS:
        count += 1
        scale = _area_scale(sweep, count)

    start_angle = math.atan2(y1 - cy, x1 - cx)
    return [
        (
            cx + radius * scale * math.cos(start_angle + sweep * index / count),
            cy + radius * scale * math.sin(start_angle + sweep * index / count),
        )
        for index in range(1, count)
    ]


def _flatten_arcs(polyline: DxfPolyline, bulges: List[float]) -> None:
    """把带凸度的线段换成折线。末点的凸度只在多段线闭合时生效（管回到首点的那一段）。"""
    vertices = polyline.points
    flattened: List[Tuple[float, float]] = []
    for index, vertex in enumerate(vertices):
        flattened.append(vertex)
        if index + 1 < len(vertices):
            following = vertices[index + 1]
        elif polyline.closed:
            following = vertices[0]
        else:
            break
        inserted = _arc_points(vertex, following, bulges[index])
        if inserted:
            polyline.arc_count += 1
            flattened.extend(inserted)
    polyline.points = flattened


def parse_dxf_polylines(filepath: str) -> List[DxfPolyline]:
    """解析 DXF 中的 LWPOLYLINE 与 POLYLINE（VERTEX/SEQEND），圆弧段折线化。"""
    text = read_text_with_encodings(filepath)
    polylines: List[DxfPolyline] = []
    current: Optional[DxfPolyline] = None
    bulges: List[float] = []          # 与 current.points 一一对应
    in_polyline = False
    collect_xy = False
    pending_x: Optional[float] = None
    pending_bulge = 0.0               # 凸度先于坐标出现时暂存
    vertex_has_point = False          # 当前顶点的坐标是否已读到

    def finish_current():
        nonlocal current, bulges, in_polyline, collect_xy, pending_x, pending_bulge, vertex_has_point
        if current is not None and current.points:
            if any(bulges):
                _flatten_arcs(current, bulges)
            polylines.append(current)
        current = None
        bulges = []
        in_polyline = False
        collect_xy = False
        pending_x = None
        pending_bulge = 0.0
        vertex_has_point = False

    for code, value in _iter_group_pairs(text):
        if code == 0:
            entity = value.upper()
            if entity == "LWPOLYLINE":
                finish_current()
                current = DxfPolyline()
                collect_xy = True
            elif entity == "POLYLINE":
                finish_current()
                current = DxfPolyline()
                in_polyline = True
                collect_xy = False
            elif entity == "VERTEX":
                if current is None:
                    current = DxfPolyline()
                    in_polyline = True
                collect_xy = True
                pending_x = None
                pending_bulge = 0.0
                vertex_has_point = False
            elif entity == "SEQEND":
                finish_current()
            elif entity in {"ENDSEC", "EOF", "SECTION"}:
                if current is not None and not in_polyline:
                    finish_current()
                elif entity == "EOF":
                    finish_current()
            else:
                if current is not None and not in_polyline:
                    finish_current()
            continue

        if current is None:
            continue
        if code == 8:
            if not in_polyline or not current.layer or current.layer == "0":
                current.layer = value
        elif code == 70:
            if in_polyline and collect_xy:
                continue  # VERTEX 的 70 是顶点标志，不是多段线的闭合标志
            try:
                flags = int(float(value))
            except ValueError:
                flags = 0
            if flags & 1:
                current.closed = True
        elif code == 10:
            if collect_xy:
                pending_x = _as_float(value)
                if not in_polyline:
                    # LWPOLYLINE 的每个 10 开始一个新顶点
                    pending_bulge = 0.0
                    vertex_has_point = False
        elif code == 20:
            if not collect_xy:
                continue
            y_value = _as_float(value)
            if pending_x is not None and y_value is not None:
                current.points.append((pending_x, y_value))
                bulges.append(pending_bulge)
                pending_bulge = 0.0
                vertex_has_point = True
            pending_x = None
        elif code == 42:
            if not collect_xy:
                continue
            bulge = _as_float(value) or 0.0
            if vertex_has_point:
                bulges[-1] = bulge
            else:
                pending_bulge = bulge

    finish_current()
    return polylines


def select_boundary_polyline(polylines: List[DxfPolyline]) -> DxfPolyline:
    """优先 BOUNDARY/红线等图层，其次闭合、范围最大的多段线。"""
    usable = [item for item in polylines if len(_ring_points(item.points)) >= 3]
    if not usable:
        raise ValueError("DXF 中未找到可用的计算边界（需要至少 3 个顶点的多段线）")

    def score(item: DxfPolyline) -> Tuple[int, int, float, int]:
        layer = (item.layer or "").lower()
        preference = 0
        for rank, hint in enumerate(BOUNDARY_LAYER_HINTS):
            if hint.lower() in layer:
                preference = 100 - rank
                break
        closed = 1 if (item.closed or len(item.points) >= 2 and item.points[0] == item.points[-1]) else 0
        return (preference, closed, _bbox_area(item.points), len(item.points))

    return max(usable, key=score)


def import_boundary_from_dxf(filepath: str) -> List[Tuple[float, float]]:
    """从 DXF 取出一条计算边界多边形。"""
    chosen = select_boundary_polyline(parse_dxf_polylines(filepath))
    ring = _ring_points(chosen.points)
    if len(ring) < 3:
        raise ValueError("DXF 边界顶点不足 3 个")
    return ring
