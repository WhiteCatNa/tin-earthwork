"""DXF 边界读写：解析 LWPOLYLINE / POLYLINE，选出计算边界。"""
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


@dataclass
class DxfPolyline:
    layer: str = "0"
    closed: bool = False
    points: List[Tuple[float, float]] = field(default_factory=list)


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


def parse_dxf_polylines(filepath: str) -> List[DxfPolyline]:
    """解析 DXF 中的 LWPOLYLINE 与 POLYLINE（VERTEX/SEQEND）。"""
    text = read_text_with_encodings(filepath)
    polylines: List[DxfPolyline] = []
    current: Optional[DxfPolyline] = None
    in_polyline = False
    collect_xy = False
    pending_x: Optional[float] = None

    def finish_current():
        nonlocal current, in_polyline, collect_xy, pending_x
        if current is not None and current.points:
            polylines.append(current)
        current = None
        in_polyline = False
        collect_xy = False
        pending_x = None

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
            try:
                flags = int(float(value))
            except ValueError:
                flags = 0
            if flags & 1:
                current.closed = True
        elif code == 10:
            if collect_xy:
                pending_x = _as_float(value)
        elif code == 20:
            if not collect_xy:
                continue
            y_value = _as_float(value)
            if pending_x is not None and y_value is not None:
                current.points.append((pending_x, y_value))
            pending_x = None

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
