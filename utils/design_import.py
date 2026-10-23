"""
设计面数据导入：从 DXF 中取出带高程的设计点，构成设计三角网。

读取 ENTITIES 段中的：
- POINT（高程点）、3DFACE（设计面三角形/四边形的顶点）——总是读入；
- INSERT（CASS 等软件的高程点块，插入点 Z 为高程）、LINE、2D/3D 多段线顶点——Z 不为 0 时读入，
  可用于设计等高线（多段线标高）、带高程的设计特征线。
平面位置相同且高程相同的点只保留一个。
"""
from typing import Dict, List, Optional, Tuple

from core.calculator import SurveyPoint
from utils.dxf_io import _as_float, _iter_group_pairs, read_text_with_encodings

XYZ = Tuple[float, float, float]
ROUND = 1e-3   # 判定重复点的平面、高程容差 (m)


def _xyz(groups: Dict[int, List[str]], xc: int, index: int = 0) -> Optional[XYZ]:
    try:
        x = _as_float(groups[xc][index])
        y = _as_float(groups[xc + 10][index])
    except (KeyError, IndexError):
        return None
    z_values = groups.get(xc + 20, [])
    z = _as_float(z_values[index]) if index < len(z_values) else 0.0
    if x is None or y is None or z is None:
        return None
    return x, y, z


def read_design_points(filepath: str) -> List[XYZ]:
    """DXF 中所有带高程的设计点（去重前）。"""
    text = read_text_with_encodings(filepath)
    points: List[XYZ] = []
    section = ""
    expect_section_name = False
    entity: Optional[str] = None
    groups: Dict[int, List[str]] = {}
    polyline: Optional[Dict[str, float]] = None   # 当前 POLYLINE 的 {flags, elevation}

    def finish():
        nonlocal polyline
        if entity is None or section != "ENTITIES":
            return
        if entity in ("POINT", "3DFACE"):
            for code in ((10,) if entity == "POINT" else (10, 11, 12, 13)):
                point = _xyz(groups, code)
                if point is not None:
                    points.append(point)
        elif entity in ("INSERT", "LINE"):
            for code in ((10,) if entity == "INSERT" else (10, 11)):
                point = _xyz(groups, code)
                if point is not None and point[2] != 0:
                    points.append(point)
        elif entity == "LWPOLYLINE":
            elevation = _as_float((groups.get(38) or ["0"])[0]) or 0.0
            if elevation != 0:
                for index in range(min(len(groups.get(10, [])), len(groups.get(20, [])))):
                    x, y = _as_float(groups[10][index]), _as_float(groups[20][index])
                    if x is not None and y is not None:
                        points.append((x, y, elevation))
        elif entity == "POLYLINE":
            flags = int(_as_float((groups.get(70) or ["0"])[0]) or 0)
            polyline = {"flags": flags, "elevation": _as_float((groups.get(30) or ["0"])[0]) or 0.0}
        elif entity == "VERTEX" and polyline is not None:
            point = _xyz(groups, 10)
            if point is not None:
                x, y, z = point
                if not polyline["flags"] & 8:          # 2D 多段线：顶点高程取多段线标高
                    z = polyline["elevation"]
                if z != 0:
                    points.append((x, y, z))
        elif entity == "SEQEND":
            polyline = None

    for code, value in _iter_group_pairs(text):
        if code == 0:
            finish()
            entity, groups = value.upper(), {}
            if entity == "SECTION":
                expect_section_name = True
            elif entity == "ENDSEC":
                section = ""
            continue
        if code == 2 and expect_section_name:
            section = value.upper()
            expect_section_name = False
            continue
        groups.setdefault(code, []).append(value)
    finish()
    return points


def import_design_points_from_dxf(filepath: str) -> List[SurveyPoint]:
    """读入设计点（去掉重复点），点号依次为 D1、D2……"""
    unique: Dict[Tuple[int, int, int], XYZ] = {}
    for x, y, z in read_design_points(filepath):
        unique.setdefault((round(x / ROUND), round(y / ROUND), round(z / ROUND)), (x, y, z))
    if len(unique) < 3:
        raise ValueError("DXF 中带高程的设计点不足 3 个。设计点可以是高程点（POINT/块）、"
                         "3DFACE，或标高不为 0 的设计等高线、3D 多段线")
    return [SurveyPoint(f"D{i}", x, y, z) for i, (x, y, z) in enumerate(unique.values(), 1)]
