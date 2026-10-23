"""
地形数据导入：
- DXF 地形图：高程点（POINT、带高程的块）、高程注记（数字 TEXT/MTEXT）、等高线（带标高的多段线）。
  按图层、类型分组，由用户选择；等高线按给定间距加密取点。
- LAS 点云（1.0~1.4，未压缩）：可只取地面点（分类 2），按格网抽稀（每格取最低点或最接近格心的点）。
"""
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from core.calculator import SurveyPoint
from utils.dxf_io import _as_float, _iter_group_pairs, read_text_with_encodings

XYZ = Tuple[float, float, float]

KIND_POINT = "高程点"
KIND_TEXT = "高程注记"
KIND_CONTOUR = "等高线"
KIND_LINE3D = "三维线"       # 高程逐点变化的 3D 多段线（地形特征线）
LINE_KINDS = (KIND_CONTOUR, KIND_LINE3D)
DEDUPE = 1e-3               # 平面距离小于 1 mm 视为同一点
MAX_CLOUD_POINTS = 2_000_000


@dataclass
class TerrainSource:
    """DXF 中的一组地形数据：同一图层、同一类型。"""
    layer: str
    kind: str
    points: List[XYZ] = field(default_factory=list)          # 高程点、高程注记
    lines: List[List[XYZ]] = field(default_factory=list)     # 等高线（每条为顶点序列）

    @property
    def count(self) -> int:
        return len(self.lines) if self.kind in LINE_KINDS else len(self.points)

    @property
    def description(self) -> str:
        if self.kind in LINE_KINDS:
            levels = sorted({z for line in self.lines for _, _, z in line})
            span = f"{levels[0]:g} ~ {levels[-1]:g} m" if levels else ""
            return f"{len(self.lines)} 条，高程 {span}"
        zs = [p[2] for p in self.points]
        return f"{len(self.points)} 个，高程 {min(zs):.3f} ~ {max(zs):.3f} m" if zs else "0 个"


NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")


def _text_value(raw: str) -> Optional[float]:
    """高程注记文字 → 数值；带格式码的 MTEXT 先去掉格式。不是纯数字时返回 None。"""
    text = re.sub(r"\\[A-Za-z][^;\\]*;", "", raw)       # \fArial|b0;、\H2.5; 等
    text = text.replace("\\P", " ").replace("{", "").replace("}", "").strip()
    if not NUMBER.match(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def scan_dxf_terrain(filepath: str) -> List[TerrainSource]:
    """扫描 DXF 中的地形数据，按 (图层, 类型) 分组；高程为 0 的点、线不算。"""
    text = read_text_with_encodings(filepath)
    groups_by_key: Dict[Tuple[str, str], TerrainSource] = {}

    def source(layer: str, kind: str) -> TerrainSource:
        key = (layer, kind)
        if key not in groups_by_key:
            groups_by_key[key] = TerrainSource(layer, kind)
        return groups_by_key[key]

    section = ""
    expect_name = False
    entity: Optional[str] = None
    groups: Dict[int, List[str]] = {}
    polyline: Optional[dict] = None

    def first(code: int, default: Optional[str] = None) -> Optional[str]:
        values = groups.get(code)
        return values[0] if values else default

    def xyz(index: int = 0, xc: int = 10) -> Optional[XYZ]:
        try:
            x, y = _as_float(groups[xc][index]), _as_float(groups[xc + 10][index])
        except (KeyError, IndexError):
            return None
        zs = groups.get(xc + 20, [])
        z = _as_float(zs[index]) if index < len(zs) else 0.0
        if x is None or y is None or z is None:
            return None
        return x, y, z

    def finish():
        nonlocal polyline
        if entity is None or section != "ENTITIES":
            return
        layer = first(8, "0") or "0"
        if entity in ("POINT", "INSERT"):
            point = xyz()
            if point is not None and point[2] != 0:
                source(layer, KIND_POINT).points.append(point)
        elif entity in ("TEXT", "MTEXT"):
            raw = "".join(groups.get(3, [])) + (first(1, "") or "")
            value = _text_value(raw)
            point = xyz()
            if value is not None and point is not None:
                source(layer, KIND_TEXT).points.append((point[0], point[1], value))
        elif entity == "LWPOLYLINE":
            elevation = _as_float(first(38, "0") or "0") or 0.0
            count = min(len(groups.get(10, [])), len(groups.get(20, [])))
            vertices = [(_as_float(groups[10][i]), _as_float(groups[20][i])) for i in range(count)]
            vertices = [(x, y, elevation) for x, y in vertices if x is not None and y is not None]
            if elevation != 0 and len(vertices) >= 2:
                if int(_as_float(first(70, "0") or "0") or 0) & 1:
                    vertices.append(vertices[0])
                source(layer, KIND_CONTOUR).lines.append(vertices)
        elif entity == "POLYLINE":
            flags = int(_as_float(first(70, "0") or "0") or 0)
            polyline = None
            if not flags & (16 | 64):        # 16、64：网格面、多面网格，不是线
                polyline = {"layer": layer, "flags": flags, "vertices": [],
                            "elevation": _as_float(first(30, "0") or "0") or 0.0}
        elif entity == "VERTEX" and polyline is not None:
            flags = int(_as_float(first(70, "0") or "0") or 0)
            point = xyz()
            if point is not None and not flags & 16:          # 16：样条框架控制点，不在曲线上
                x, y, z = point
                if not polyline["flags"] & 8:
                    z = polyline["elevation"]
                polyline["vertices"].append((x, y, z))
        elif entity == "SEQEND" and polyline is not None:
            vertices = polyline["vertices"]
            if len(vertices) >= 2 and all(v[2] != 0 for v in vertices):
                if polyline["flags"] & 1:
                    vertices.append(vertices[0])
                kind = KIND_CONTOUR if len({round(v[2], 6) for v in vertices}) == 1 else KIND_LINE3D
                source(polyline["layer"], kind).lines.append(vertices)
            polyline = None

    for code, value in _iter_group_pairs(text):
        if code == 0:
            finish()
            entity, groups = value.upper(), {}
            if entity == "SECTION":
                expect_name = True
            elif entity == "ENDSEC":
                section = ""
            continue
        if code == 2 and expect_name:
            section = value.upper()
            expect_name = False
            continue
        groups.setdefault(code, []).append(value)
    finish()
    order = {KIND_POINT: 0, KIND_TEXT: 1, KIND_CONTOUR: 2, KIND_LINE3D: 3}
    return sorted((s for s in groups_by_key.values() if s.count), key=lambda s: (order[s.kind], s.layer))


def default_selection(sources: Sequence[TerrainSource]) -> List[bool]:
    """默认选择：全部高程点和等高线；高程注记只在没有高程点时选上（注记常与高程点重复）。"""
    has_points = any(s.kind == KIND_POINT for s in sources)
    return [s.kind != KIND_TEXT or not has_points for s in sources]


def densify(line: Sequence[XYZ], spacing: float) -> List[XYZ]:
    """等高线按间距加密：保留原顶点，长于 spacing 的线段等分插点。"""
    out: List[XYZ] = [line[0]]
    for (x0, y0, z0), (x1, y1, z1) in zip(line, line[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        parts = max(1, math.ceil(length / spacing - 1e-9)) if spacing > 0 else 1
        for k in range(1, parts + 1):
            t = k / parts
            out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0), z0 + t * (z1 - z0)))
    return out


@dataclass
class TerrainPoints:
    points: List[SurveyPoint]
    conflicts: int = 0          # 平面位置相同而高程不同、被舍弃的点
    flat_triangles: int = 0     # 三个顶点在同一条等高线上的三角形
    triangle_count: int = 0

    @property
    def flat_ratio(self) -> float:
        return self.flat_triangles / self.triangle_count if self.triangle_count else 0.0


def build_terrain_points(sources: Sequence[TerrainSource], selected: Sequence[bool],
                         spacing: float = 5.0) -> TerrainPoints:
    """把选中的地形数据合成测点；等高线点号为 C{线号}-{序号}，其他为 G{序号}。"""
    if not any(selected):
        raise ValueError("请至少选择一组地形数据")
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("等高线取点间距必须大于 0")
    seen: Dict[Tuple[int, int], float] = {}
    points: List[SurveyPoint] = []
    line_of: List[int] = []           # 每个点所属等高线编号，非等高线点为 −1
    conflicts = set()

    def add(point_id: str, x: float, y: float, z: float, line: int):
        key = (round(x / DEDUPE), round(y / DEDUPE))
        if key in seen:
            if abs(seen[key] - z) > DEDUPE:
                conflicts.add(key)
            return
        seen[key] = z
        points.append(SurveyPoint(point_id, x, y, z))
        line_of.append(line)

    counter = line_counter = 0
    for source, use in zip(sources, selected):
        if not use or source.kind in LINE_KINDS:
            continue
        for x, y, z in source.points:
            counter += 1
            add(f"G{counter}", x, y, z, -1)
    for source, use in zip(sources, selected):
        if not use or source.kind not in LINE_KINDS:
            continue
        for line in source.lines:
            line_counter += 1
            contour = line_counter if source.kind == KIND_CONTOUR else -1   # 只有等高线会产生平三角形
            for k, (x, y, z) in enumerate(densify(line, spacing), 1):
                add(f"C{line_counter}-{k}", x, y, z, contour)
    if len(points) < 3:
        raise ValueError("选中的地形数据不足 3 个点")
    if len(points) > MAX_CLOUD_POINTS:
        raise ValueError(f"取点过多（{len(points)} 个），请加大等高线取点间距")

    result = TerrainPoints(points, len(conflicts))
    if line_counter:
        from scipy.spatial import Delaunay, QhullError
        try:
            tri = Delaunay(np.array([(p.x, p.y) for p in points]))
        except QhullError:
            return result
        lines = np.array(line_of)[tri.simplices]
        result.triangle_count = len(tri.simplices)
        result.flat_triangles = int(((lines[:, 0] >= 0) & (lines[:, 0] == lines[:, 1])
                                     & (lines[:, 1] == lines[:, 2])).sum())
    return result


# ---------------------------------------------------------------------------
# LAS 点云
# ---------------------------------------------------------------------------

@dataclass
class LasCloud:
    xyz: np.ndarray                   # [n, 3]
    classification: np.ndarray        # [n]
    version: str
    point_format: int

    @property
    def count(self) -> int:
        return len(self.xyz)

    @property
    def has_ground_class(self) -> bool:
        return bool((self.classification == 2).any())


def read_las(filepath: str) -> LasCloud:
    """读取未压缩的 LAS 1.0~1.4 点云（点格式 0~10）。"""
    with open(filepath, "rb") as handle:
        header = handle.read(375)
    if len(header) < 227 or header[:4] != b"LASF":
        raise ValueError("不是 LAS 点云文件")
    major, minor = header[24], header[25]
    offset = struct.unpack_from("<I", header, 96)[0]
    raw_format = header[104]
    if raw_format & 0xC0:
        raise ValueError("这是压缩的 LAZ 点云，请先用 LAStools / CloudCompare 等转为 LAS")
    point_format = raw_format & 0x3F
    if point_format > 10:
        raise ValueError(f"不支持的 LAS 点格式 {point_format}")
    record_length = struct.unpack_from("<H", header, 105)[0]
    count = struct.unpack_from("<I", header, 107)[0]
    if (major, minor) >= (1, 4) and len(header) >= 255 and count == 0:
        count = struct.unpack_from("<Q", header, 247)[0]
    scale = struct.unpack_from("<3d", header, 131)
    origin = struct.unpack_from("<3d", header, 155)
    if count == 0:
        raise ValueError("点云中没有点")
    class_offset = 16 if point_format >= 6 else 15
    dtype = np.dtype({"names": ["X", "Y", "Z", "cls"], "formats": ["<i4", "<i4", "<i4", "u1"],
                      "offsets": [0, 4, 8, class_offset], "itemsize": record_length})
    records = np.memmap(filepath, dtype=dtype, mode="r", offset=offset, shape=(count,))
    try:
        xyz = np.column_stack([records["X"] * scale[0] + origin[0],
                               records["Y"] * scale[1] + origin[1],
                               records["Z"] * scale[2] + origin[2]])
        classification = np.array(records["cls"])
    finally:
        del records
    if point_format < 6:
        classification = classification & 0x1F
    return LasCloud(xyz, classification, f"{major}.{minor}", point_format)


THIN_LOWEST = "lowest"
THIN_CENTER = "center"
THIN_ADAPTIVE = "adaptive"      # 保精度抽稀：保证每个原始点到抽稀后三角网的高程差不超过给定值
DEFAULT_TOLERANCE = 0.05
MAX_ADAPTIVE_CANDIDATES = 2_000_000


def suggest_cell(xyz: np.ndarray, target: int = 50_000) -> float:
    """抽稀格网边长：使保留点数约为 target（取 0.1/0.2/0.5×10ⁿ 的整齐值）。"""
    span = np.ptp(xyz[:, :2], axis=0)
    area = max(float(span[0] * span[1]), 1e-6)
    raw = math.sqrt(area / max(target, 1))
    exponent = math.floor(math.log10(raw)) if raw > 0 else 0
    for nice in (1, 2, 5, 10):
        cell = nice * 10 ** exponent
        if cell >= raw:
            return float(cell)
    return float(10 ** (exponent + 1))


def thin_cloud(xyz: np.ndarray, cell: float, method: str = THIN_LOWEST) -> np.ndarray:
    """格网抽稀：每格保留一个点（最低点，或离格心最近的点），返回保留点的下标。"""
    if not math.isfinite(cell) or cell <= 0:
        raise ValueError("抽稀格网边长必须大于 0")
    if method not in (THIN_LOWEST, THIN_CENTER):
        raise ValueError(f"未知的抽稀方式: {method}")

    if not len(xyz):
        return np.zeros(0, dtype=int)
    base = xyz[:, :2].min(axis=0)
    cells = np.floor((xyz[:, :2] - base) / cell).astype(np.int64)
    key = cells[:, 0] * (int(cells[:, 1].max()) + 1) + cells[:, 1]
    if method == THIN_LOWEST:
        score = xyz[:, 2]
    else:
        center = base + (cells + 0.5) * cell
        score = np.hypot(*(xyz[:, :2] - center).T)
    order = np.lexsort((score, key))
    first = np.ones(len(order), dtype=bool)
    first[1:] = key[order][1:] != key[order][:-1]
    return order[first]


def adaptive_thin(xyz: np.ndarray, tolerance: float, max_points: int = MAX_CLOUD_POINTS,
                  rounds: int = 60) -> np.ndarray:
    """保精度抽稀（批量贪心插入）：从凸包顶点开始构网，每轮在每个三角形里加入高程差最大、且超过
    tolerance 的那个点，直到所有点到三角网的高程差都不超过 tolerance。返回保留点的下标。

    与格网抽稀相比，平坦处只留很少的点，起伏、坡折处自动加密；保证抽稀后的三角网与全部点的
    高程差处处（在原始点上）不超过 tolerance，挖填方与用全部点构网相差约不超过 tolerance × 面积，
    实际通常小得多（正负相抵）。
    """
    from scipy.spatial import ConvexHull, Delaunay, QhullError

    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("高程容差必须大于 0")
    n = len(xyz)
    if n <= 3:
        return np.arange(n)
    xy = xyz[:, :2] - xyz[:, :2].mean(axis=0)
    z = xyz[:, 2]
    try:
        selected = np.zeros(n, dtype=bool)
        selected[ConvexHull(xy).vertices] = True
    except QhullError:
        raise ValueError("点云的点共线，无法构网") from None
    for _ in range(rounds):
        index = np.nonzero(selected)[0]
        tri = Delaunay(xy[index])
        rest = np.nonzero(~selected)[0]
        if not len(rest):
            break
        simplex = tri.find_simplex(xy[rest])
        inside = simplex >= 0
        rest, simplex = rest[inside], simplex[inside]
        transform = tri.transform[simplex]
        b = np.einsum("nij,nj->ni", transform[:, :2], xy[rest] - transform[:, 2])
        lam = np.column_stack([b, 1 - b.sum(axis=1)])
        estimate = (lam * z[index][tri.simplices[simplex]]).sum(axis=1)
        error = np.abs(z[rest] - estimate)
        bad = error > tolerance
        if not bad.any():
            break
        # 每个三角形里取误差最大的点
        order = np.lexsort((-error[bad], simplex[bad]))
        tri_sorted = simplex[bad][order]
        first = np.ones(len(order), dtype=bool)
        first[1:] = tri_sorted[1:] != tri_sorted[:-1]
        selected[rest[bad][order][first]] = True
        if selected.sum() > max_points:
            raise ValueError(f"保精度抽稀后超过 {max_points} 个点，请加大高程容差")
    return np.nonzero(selected)[0]


def cloud_points(cloud: LasCloud, cell: float, method: str = THIN_LOWEST, ground_only: bool = True,
                 tolerance: float = DEFAULT_TOLERANCE, prefix: str = "L") -> List[SurveyPoint]:
    """点云 → 测点：可只取地面点，再按格网抽稀或保精度抽稀。

    保精度抽稀时 cell 只用于预筛：点数超过 200 万时先按 cell 格网取最低点。
    """
    xyz = cloud.xyz
    if ground_only and cloud.has_ground_class:
        xyz = xyz[cloud.classification == 2]
    if method == THIN_ADAPTIVE:
        if len(xyz) > MAX_ADAPTIVE_CANDIDATES:
            xyz = xyz[thin_cloud(xyz, cell, THIN_LOWEST)]
        keep = adaptive_thin(xyz, tolerance)
    else:
        keep = thin_cloud(xyz, cell, method)
    if len(keep) > MAX_CLOUD_POINTS:
        raise ValueError(f"抽稀后仍有 {len(keep)} 个点，请加大格网边长")
    if len(keep) < 3:
        raise ValueError("抽稀后不足 3 个点")
    kept = xyz[np.sort(keep)]
    return [SurveyPoint(f"{prefix}{i}", float(x), float(y), float(z)) for i, (x, y, z) in enumerate(kept, 1)]


def points_frame(points: Sequence[SurveyPoint]) -> pd.DataFrame:
    """测点 → 预览用 DataFrame（与表格导入的列名一致，便于复用列映射显示）。"""
    return pd.DataFrame({"点号": [p.id for p in points], "X": [p.x for p in points],
                         "Y": [p.y for p in points], "Z": [p.z for p in points]})
