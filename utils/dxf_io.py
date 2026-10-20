"""DXF 读取：解析 LWPOLYLINE / POLYLINE / LINE，选出计算边界，或按图层取出特征线。"""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.breaklines import Breakline


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
    z: List[Optional[float]] = field(default_factory=list)  # 逐点高程（3D 多段线、直线）
    elevation: Optional[float] = None  # 2D 多段线的整体标高（LWPOLYLINE 38 组码、POLYLINE 头部 30 组码）
    is_3d: bool = False
    kind: str = "POLYLINE"

    def vertex_elevations(self) -> List[Optional[float]]:
        """每个顶点的高程；没有高程（2D 且标高为 0 或缺省）时为 None。"""
        if self.is_3d:
            return [None if value is None else float(value) for value in self.z]
        if self.elevation:
            return [float(self.elevation)] * len(self.points)
        return [None] * len(self.points)


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
    pending_x2: Optional[float] = None

    def finish_current():
        nonlocal current, in_polyline, collect_xy, pending_x, pending_x2
        if current is not None and current.points:
            if current.kind == "LINE" and any(current.z):
                current.is_3d = True
            polylines.append(current)
        current = None
        in_polyline = False
        collect_xy = False
        pending_x = pending_x2 = None

    for code, value in _iter_group_pairs(text):
        if code == 0:
            entity = value.upper()
            if entity == "LWPOLYLINE":
                finish_current()
                current = DxfPolyline(kind="LWPOLYLINE")
                collect_xy = True
            elif entity == "LINE":
                finish_current()
                current = DxfPolyline(kind="LINE")
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
            if current.kind == "LINE":
                continue
            if flags & 1 and not (in_polyline and collect_xy):
                current.closed = True
            if (flags & 8 and in_polyline and not collect_xy) or (flags & 32 and in_polyline and collect_xy):
                current.is_3d = True
        elif code == 10:
            if collect_xy:
                pending_x = _as_float(value)
        elif code == 20:
            if not collect_xy:
                continue
            y_value = _as_float(value)
            if pending_x is not None and y_value is not None:
                current.points.append((pending_x, y_value))
                current.z.append(None)
            pending_x = None
        elif code == 30:
            if in_polyline and not collect_xy:
                current.elevation = _as_float(value)
            elif collect_xy and current.points and current.kind != "LWPOLYLINE":
                current.z[-1] = _as_float(value)
        elif code == 38 and current.kind == "LWPOLYLINE":
            current.elevation = _as_float(value)
        elif current.kind == "LINE" and code in (11, 21, 31):
            if code == 11:
                pending_x2 = _as_float(value)
            elif code == 21:
                y_value = _as_float(value)
                if pending_x2 is not None and y_value is not None and len(current.points) == 1:
                    current.points.append((pending_x2, y_value))
                    current.z.append(None)
                pending_x2 = None
            elif len(current.points) == 2:
                current.z[-1] = _as_float(value)

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


def list_dxf_layers(filepath: str) -> Dict[str, int]:
    """DXF 中各图层的线条数（多段线与直线），供选择特征线图层。"""
    counts = Counter(item.layer or "0" for item in parse_dxf_polylines(filepath) if len(item.points) >= 2)
    return dict(sorted(counts.items()))


def import_breaklines_from_dxf(filepath: str, layers: Optional[Sequence[str]] = None) -> List[Breakline]:
    """把 DXF 中的多段线、直线作为特征线读入；layers 为空时读取全部图层。

    3D 多段线、带高程的直线按顶点高程读入；2D 线（标高为 0 或缺省）不带高程，顶点须落在测点上。
    闭合多段线首尾相连。
    """
    wanted = None if layers is None else {str(layer) for layer in layers}
    lines: List[Breakline] = []
    for item in parse_dxf_polylines(filepath):
        layer = item.layer or "0"
        if wanted is not None and layer not in wanted:
            continue
        vertices = [(x, y, z) for (x, y), z in zip(item.points, item.vertex_elevations())]
        if item.closed and len(vertices) >= 3 and vertices[0][:2] != vertices[-1][:2]:
            vertices.append(vertices[0])
        if len(vertices) < 2:
            continue
        lines.append(Breakline(vertices=vertices, name=f"{layer}-{len(lines) + 1}"))
    if not lines:
        raise ValueError("DXF 所选图层中没有多段线或直线")
    return lines
