"""
核心计算模块：TIN 三角网构建、按计算边界精确裁剪、挖填方计算

比较面（“设计面”）可以是统一高程、分区/逐点高程、斜面，或另一期测量的 TIN（两期土方对比）。
无论哪种，最终都落到“每个三角形顶点上的高差 = 实测 − 比较面”，三角形内线性变化，
由同一套边界裁剪与零线分割精确计算。
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import Delaunay, QhullError

from core.overlay import clip_convex, overlay_triangulations
from core.surface import PlaneDesign, TinSurface
from core.geometry import (
    clip_by_sign,
    clip_ring_to_triangle,
    clip_segment_to_ring,
    linear_plane,
    normalize_ring,
    points_in_ring,
    polygon_integral,
    ring_self_intersects,
    signed_area,
    triangles_touching_ring,
    zero_crossing_points,
)

XY = Tuple[float, float]

# 平面坐标重复判定容差 (m)，与导入时的数据检查一致
DUPLICATE_TOLERANCE = 1e-3
# 高程差在该范围内视为同一个点的重复记录 (m)
ELEVATION_TOLERANCE = 1e-3
# 边界内测点覆盖面积低于该比例时提示
COVERAGE_WARN_RATIO = 0.995
# 提示信息里最多列出的点号数量
MAX_LISTED = 10

MODE_DESIGN = "design"
MODE_COMPARE = "compare"
# 两期对比时，后期测点在明细中的点号前缀；叠加产生的交点命名前缀
COMPARE_ID_PREFIX = "后期-"
CROSSING_ID_PREFIX = "交点"


@dataclass
class SurveyPoint:
    """测量点"""
    id: str
    x: float
    y: float
    z: float  # 实测高程
    design_z: float = 0.0  # 设计高程
    delta_z: float = 0.0   # 高差 (实测 - 设计)
    has_design_z: bool = False  # 是否从文件带来逐点设计高程


@dataclass
class Triangle:
    """三角形单元。

    面积、方量均只统计计算边界以内的部分；跨边界的三角形按边界精确裁剪。
    """
    id: int
    vertex_ids: Tuple[int, int, int]  # 顶点索引
    vertex_points: Tuple[SurveyPoint, SurveyPoint, SurveyPoint]
    area: float = 0.0          # 边界内的水平投影面积
    avg_delta_z: float = 0.0   # 边界内的平均高差 (体积 / 面积)
    volume: float = 0.0
    cut_volume: float = 0.0
    fill_volume: float = 0.0
    cut_area: float = 0.0
    fill_area: float = 0.0
    is_boundary: bool = False  # 完全位于计算边界外，不参与计算
    is_clipped: bool = False   # 部分位于边界内，已按边界裁剪
    is_mixed: bool = False     # 边界内部分同时有挖有填
    # 混合或被裁剪时，挖方/填方区域的平面多边形（用于绘图）
    cut_polygon: List[XY] = field(default_factory=list)
    fill_polygon: List[XY] = field(default_factory=list)
    # 边界内的零填挖线段
    zero_segments: List[Tuple[XY, XY]] = field(default_factory=list)


@dataclass
class CalculationResult:
    """计算结果汇总"""
    total_cut: float = 0.0
    total_fill: float = 0.0
    net_volume: float = 0.0
    triangle_count: int = 0
    mixed_triangle_count: int = 0
    clipped_triangle_count: int = 0
    computed_area: float = 0.0   # 实际参与计算的水平面积
    boundary_area: float = 0.0   # 计算边界面积，未设边界时为 0
    design_min: float = 0.0
    design_max: float = 0.0
    triangles: List[Triangle] = field(default_factory=list)
    boundary_points: List[XY] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    mode: str = MODE_DESIGN               # MODE_DESIGN：与设计面比较；MODE_COMPARE：两期对比
    design_description: str = ""          # 斜面、两期对比时的比较面说明
    compare_point_count: int = 0          # 两期对比时后期测点数
    grid_check: Optional[Any] = None      # 方格网校核结果（core.grid_check.GridCheckResult）

    @property
    def is_compare(self) -> bool:
        return self.mode == MODE_COMPARE

    @property
    def uniform_design(self) -> bool:
        return abs(self.design_max - self.design_min) < 1e-9

    @property
    def design_text(self) -> str:
        """报告中的设计高程（比较面）说明。"""
        if self.design_description:
            return self.design_description
        if self.uniform_design:
            return f"{self.design_min:.3f} m"
        return f"按点变化 {self.design_min:.3f} ~ {self.design_max:.3f} m（分区/逐点设计高程）"

    @property
    def coverage_ratio(self) -> Optional[float]:
        if self.boundary_area <= 0:
            return None
        return self.computed_area / self.boundary_area


def _signed_areas(tri_xy: np.ndarray) -> np.ndarray:
    p0, p1, p2 = tri_xy[:, 0], tri_xy[:, 1], tri_xy[:, 2]
    return ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1])) / 2


def _triangle_planes(tri_xy: np.ndarray, tri_z: np.ndarray) -> List[Tuple[float, float, float]]:
    """每个三角形上 z = a·x + b·y + c 的系数；退化三角形取三点平均高程。"""
    x0, y0 = tri_xy[:, 0, 0], tri_xy[:, 0, 1]
    x1, y1 = tri_xy[:, 1, 0] - x0, tri_xy[:, 1, 1] - y0
    x2, y2 = tri_xy[:, 2, 0] - x0, tri_xy[:, 2, 1] - y0
    z0, z1, z2 = tri_z[:, 0], tri_z[:, 1] - tri_z[:, 0], tri_z[:, 2] - tri_z[:, 0]
    det = x1 * y2 - x2 * y1
    good = np.abs(det) > 0
    safe = np.where(good, det, 1.0)
    a = np.where(good, (z1 * y2 - z2 * y1) / safe, 0.0)
    b = np.where(good, (z2 * x1 - z1 * x2) / safe, 0.0)
    c = np.where(good, z0 - a * x0 - b * y0, tri_z.mean(axis=1))
    return list(zip(a.tolist(), b.tolist(), c.tolist()))


def format_id_list(ids: List[str]) -> str:
    """提示信息中列出点号，过多时截断。"""
    text = "、".join(ids[:MAX_LISTED])
    if len(ids) > MAX_LISTED:
        text += f" 等共 {len(ids)} 个"
    return text


class TINEarthworkCalculator:
    """TIN 土方计算核心类"""

    def __init__(self, design_elevation: float = 0.0):
        self.design_elevation = design_elevation
        self.design_plane: Optional[PlaneDesign] = None   # 斜面设计面；None 为统一高程
        self.design_overrides: Dict[str, float] = {}      # 分区：单独指定设计高程的点
        self.points: List[SurveyPoint] = []
        self.compare_points: List[SurveyPoint] = []       # 两期对比的后期测点；空表示与设计面比较
        self.boundary_polygon: List[XY] = []
        self.triangles: List[Triangle] = []
        self.delaunay = None
        self.warnings: List[str] = []
        self._origin: Tuple[float, float] = (0.0, 0.0)
        self._ring: Optional[np.ndarray] = None       # 局部坐标下的逆时针边界
        self._regions: Dict[int, np.ndarray] = {}     # 被裁剪三角形在边界内的部分（局部坐标）
        self._boundary_area = 0.0
        self._eps = 1e-9
        # 构网时的前期/后期 Delaunay 与参与构网的点序号，供任意位置插值（方格网校核）
        self._tin_a: Optional[Tuple[Delaunay, np.ndarray]] = None
        self._tin_b: Optional[Tuple[Delaunay, np.ndarray]] = None
        self._overlap_ratio: Optional[float] = None   # 两期对比：共同覆盖面积 / 前期范围面积

    @property
    def mode(self) -> str:
        return MODE_COMPARE if self.compare_points else MODE_DESIGN

    def _apply_design(self):
        """按当前设计面（统一高程或斜面）和分区覆盖刷新每个测点的设计高程与高差。"""
        for pt in self.points:
            if pt.id in self.design_overrides:
                pt.design_z = float(self.design_overrides[pt.id])
            elif self.design_plane is not None:
                pt.design_z = float(self.design_plane.elevation(pt.x, pt.y))
            else:
                pt.design_z = self.design_elevation
            pt.delta_z = pt.z - pt.design_z

    def set_design_elevation(self, elevation: float):
        """设置统一设计高程"""
        self.design_elevation = elevation
        self.design_plane = None
        self.design_overrides = {}
        self._apply_design()

    def set_design_elevations(self, elevations: Dict[str, float], default: Optional[float] = None):
        """设置分区设计高程 (点号 -> 高程)。

        未列出的点使用 default；不传 default 时沿用当前的统一设计高程。
        """
        if default is not None:
            self.design_elevation = float(default)
        self.design_plane = None
        self.design_overrides = {str(key): float(value) for key, value in elevations.items()}
        self._apply_design()

    def set_design_plane(self, plane: PlaneDesign, overrides: Optional[Dict[str, float]] = None):
        """设置斜面设计面；overrides 中的点单独指定设计高程（分区）。"""
        self.design_plane = plane
        self.design_overrides = {str(key): float(value) for key, value in (overrides or {}).items()}
        self._apply_design()

    def set_compare_points(self, points: Optional[Sequence[SurveyPoint]]):
        """设置两期对比的后期测点；传空则回到与设计面比较。"""
        self.compare_points = list(points or [])

    def add_points(self, points: List[SurveyPoint]):
        """添加测量点。

        若测点已带逐点设计高程，则保留并刷新高差；否则套用当前设计面。
        """
        self.points = points
        if any(pt.has_design_z for pt in points):
            for pt in self.points:
                pt.delta_z = pt.z - pt.design_z
            unique = {round(pt.design_z, 6) for pt in points if pt.has_design_z}
            if len(unique) == 1:
                self.design_elevation = next(iter(unique))
        else:
            self._apply_design()

    def set_boundary(self, boundary: List[XY]):
        """设置计算边界"""
        self.boundary_polygon = boundary

    # ------------------------------------------------------------------
    # 构网
    # ------------------------------------------------------------------

    @staticmethod
    def _check_finite(points: Sequence[SurveyPoint], label: str = ""):
        bad = [
            str(pt.id) for pt in points
            if not all(math.isfinite(value) for value in (pt.x, pt.y, pt.z, pt.design_z))
        ]
        if bad:
            raise ValueError(f"以下{label}测点的坐标或高程为空或不是数字，请修正后再计算：{format_id_list(bad)}")

    def _unique_point_indices(self, points: Sequence[SurveyPoint], coords: np.ndarray, label: str = "") -> np.ndarray:
        """合并完全重复的测点；平面坐标相同但高程不同时报错，避免随机丢点。"""
        keys = np.round(coords / DUPLICATE_TOLERANCE).astype(np.int64)
        _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        inverse = inverse.ravel()
        if len(first) == len(coords):
            return np.arange(len(coords))

        conflicts: List[str] = []
        ignored: List[str] = []
        for group in np.nonzero(np.bincount(inverse) > 1)[0]:
            members = [points[i] for i in np.nonzero(inverse == group)[0]]
            keep = members[0]
            same = all(
                abs(pt.z - keep.z) <= ELEVATION_TOLERANCE
                and abs(pt.design_z - keep.design_z) <= ELEVATION_TOLERANCE
                for pt in members[1:]
            )
            if same:
                ignored.extend(str(pt.id) for pt in members[1:])
            else:
                conflicts.append(
                    "、".join(f"{pt.id}(高程 {pt.z:.3f})" for pt in members)
                    + f" 位于 ({keep.x:.3f}, {keep.y:.3f})"
                )
        if conflicts:
            listed = "\n".join(conflicts[:MAX_LISTED])
            more = f"\n……共 {len(conflicts)} 组" if len(conflicts) > MAX_LISTED else ""
            raise ValueError(
                f"以下{label}测点平面坐标相同但高程不同，无法确定该用哪个，"
                f"请在数据表中删除或修正后再计算：\n{listed}{more}"
            )
        self.warnings.append(f"有 {len(ignored)} 个{label}测点与其他点完全重复，已忽略：{format_id_list(ignored)}")
        return np.sort(first)

    def _delaunay(self, local_unique: np.ndarray, points: Sequence[SurveyPoint], unique: np.ndarray,
                  label: str = "") -> Delaunay:
        try:
            delaunay = Delaunay(local_unique)
        except QhullError as exc:
            raise ValueError(
                f"无法构建{label}三角网：测量点共线、重复或分布不足以构成三角形，请检查坐标"
            ) from exc
        if len(delaunay.coplanar):
            dropped = [str(points[unique[i]].id) for i in delaunay.coplanar[:, 0]]
            self.warnings.append(
                f"有 {len(dropped)} 个{label}测点与相邻点距离过近，未参与构网：{format_id_list(dropped)}"
            )
        return delaunay

    def _prepare_boundary(self) -> Optional[np.ndarray]:
        if not self.boundary_polygon:
            return None
        ring = normalize_ring(self.boundary_polygon)
        if len(ring) < 3:
            raise ValueError("计算边界无效：至少需要 3 个不重复的顶点")
        if not np.all(np.isfinite(ring)):
            raise ValueError("计算边界含有非数字坐标")
        if ring_self_intersects(ring):
            raise ValueError("计算边界的边线相互交叉，请在边界页修正后再计算")
        if abs(signed_area(ring)) <= 0:
            raise ValueError("计算边界无效：顶点共线，围不成面积")
        return ring - np.asarray(self._origin)

    def build_tin(self) -> List[Triangle]:
        """构建三角网，并按计算边界把三角形分为 边界内 / 跨边界 / 边界外。

        两期对比时，三角网是前期、后期两个 TIN 的叠加，每个三角形内两期地面都是线性的。
        """
        if len(self.points) < 3:
            raise ValueError("至少需要 3 个测量点才能构建三角网")
        self.warnings = []
        self._check_finite(self.points)
        if self.compare_points:
            vertex_points, local, simplices = self._triangulate_compare()
        else:
            vertex_points, local, simplices = self._triangulate_design()
        return self._classify(vertex_points, local, simplices)

    def build_from_mesh(self, vertex_points: List[SurveyPoint], simplices: np.ndarray) -> List[Triangle]:
        """用给定的三角剖分（如方格网）代替 Delaunay，其余计算完全相同。"""
        self.warnings = []
        self._check_finite(vertex_points)
        coords = np.array([[p.x, p.y] for p in vertex_points], dtype=float).reshape(-1, 2)
        origin = coords.min(axis=0)
        self._origin = (float(origin[0]), float(origin[1]))
        self._tin_a = self._tin_b = None
        return self._classify(vertex_points, coords - origin, np.asarray(simplices, dtype=np.int64).reshape(-1, 3))

    def _triangulate_design(self):
        coords = np.array([[p.x, p.y] for p in self.points], dtype=float)
        unique = self._unique_point_indices(self.points, coords)
        # 以左下角为局部原点，避免大地坐标（百万米量级）带来的舍入误差
        origin = coords[unique].min(axis=0)
        self._origin = (float(origin[0]), float(origin[1]))
        local = coords - origin
        self.delaunay = self._delaunay(local[unique], self.points, unique)
        self._tin_a = (self.delaunay, unique)
        self._tin_b = None
        self._overlap_ratio = None
        return self.points, local, unique[self.delaunay.simplices]

    def _triangulate_compare(self):
        """前期、后期各自构网后叠加；叠加后的顶点 z 为前期高程，design_z 为后期高程。"""
        before, after = self.points, self.compare_points
        if len(after) < 3:
            raise ValueError("后期测量至少需要 3 个测点")
        self._check_finite(after, "后期")
        coords_a = np.array([[p.x, p.y] for p in before], dtype=float)
        coords_b = np.array([[p.x, p.y] for p in after], dtype=float)
        unique_a = self._unique_point_indices(before, coords_a, "前期")
        unique_b = self._unique_point_indices(after, coords_b, "后期")
        origin = np.minimum(coords_a[unique_a].min(axis=0), coords_b[unique_b].min(axis=0))
        self._origin = (float(origin[0]), float(origin[1]))
        local_a = coords_a - origin
        local_b = coords_b - origin
        tin_a = self._delaunay(local_a[unique_a], before, unique_a, "前期")
        tin_b = self._delaunay(local_b[unique_b], after, unique_b, "后期")
        self.delaunay = tin_a
        self._tin_a = (tin_a, unique_a)
        self._tin_b = (tin_b, unique_b)

        tri_a = local_a[unique_a][tin_a.simplices]
        tri_b = local_b[unique_b][tin_b.simplices]
        extent = float(max(np.ptp(local_a[unique_a], axis=0).max(), np.ptp(local_b[unique_b], axis=0).max())) or 1.0
        area_tol = (extent * 1e-9) * extent * 1e-3
        cells = overlay_triangulations(tri_a, tri_b, area_tol)
        if not cells:
            raise ValueError(
                "前期和后期测量的范围没有重叠，无法对比。请检查两期坐标是否同一坐标系、X/Y 方向是否一致。"
            )
        planes_a = _triangle_planes(tri_a, np.array([before[i].z for i in unique_a])[tin_a.simplices])
        planes_b = _triangle_planes(tri_b, np.array([after[i].z for i in unique_b])[tin_b.simplices])

        # 所有交集多边形的顶点摊平后按坐标去重（批量处理，避免逐点 Python 调用）
        sizes = np.fromiter((len(polygon) for polygon, _, _ in cells), dtype=np.int64, count=len(cells))
        flat = np.array([point for polygon, _, _ in cells for point in polygon], dtype=float)
        cell_of = np.repeat(np.arange(len(cells)), sizes)
        key_tol = extent * 1e-9
        keys = np.round(flat / key_tol).astype(np.int64)
        _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        inverse = inverse.ravel()
        vertex_xy = flat[first]
        # 每个顶点的两期高程取自它首次出现的那个交集多边形（其内两期地面都是线性的）
        owner = cell_of[first]
        pa = np.array([planes_a[cells[i][1]] for i in owner]).reshape(-1, 3)
        pb = np.array([planes_b[cells[i][2]] for i in owner]).reshape(-1, 3)
        z_before = pa[:, 0] * vertex_xy[:, 0] + pa[:, 1] * vertex_xy[:, 1] + pa[:, 2]
        z_after = pb[:, 0] * vertex_xy[:, 0] + pb[:, 1] * vertex_xy[:, 1] + pb[:, 2]

        simplices: List[Tuple[int, int, int]] = []
        start = 0
        for size in sizes.tolist():
            ring: List[int] = []
            for index in inverse[start:start + size].tolist():
                if not ring or ring[-1] != index:
                    ring.append(index)
            start += size
            if len(ring) >= 2 and ring[0] == ring[-1]:
                ring.pop()
            simplices.extend((ring[0], ring[i], ring[i + 1]) for i in range(1, len(ring) - 1))
        simplices_array = np.array(simplices, dtype=np.int64).reshape(-1, 3)
        areas = np.abs(_signed_areas(vertex_xy[simplices_array])) if len(simplices_array) else np.zeros(0)
        simplices_array = simplices_array[areas > area_tol]

        # 与原测点重合的顶点沿用原点号，便于在明细中核对；其余为两期三角网边线的交点
        names: Dict[Tuple[int, int], str] = {}
        for index, row in zip(unique_b, np.round(local_b[unique_b] / key_tol).astype(np.int64).tolist()):
            names[tuple(row)] = f"{COMPARE_ID_PREFIX}{after[index].id}"
        for index, row in zip(unique_a, np.round(local_a[unique_a] / key_tol).astype(np.int64).tolist()):
            names[tuple(row)] = str(before[index].id)
        ox, oy = self._origin
        crossing = 0
        vertex_points: List[SurveyPoint] = []
        for (x, y), row, zb, za in zip(vertex_xy.tolist(), keys[first].tolist(), z_after.tolist(), z_before.tolist()):
            name = names.get(tuple(row))
            if name is None:
                crossing += 1
                name = f"{CROSSING_ID_PREFIX}{crossing}"
            vertex_points.append(SurveyPoint(
                id=name, x=x + ox, y=y + oy, z=za, design_z=zb, delta_z=za - zb, has_design_z=True,
            ))

        hull_a = float(np.abs(_signed_areas(tri_a)).sum())
        self._overlap_ratio = float(areas[areas > area_tol].sum()) / hull_a if hull_a > 0 else None
        return vertex_points, vertex_xy, simplices_array

    def _classify(self, vertex_points: List[SurveyPoint], local: np.ndarray, simplices: np.ndarray) -> List[Triangle]:
        self._regions = {}
        tri_xy = local[simplices]
        used = local[np.unique(simplices)] if len(simplices) else local
        extent = float(np.ptp(used, axis=0).max()) if len(used) else 1.0
        extent = extent or 1.0
        self._eps = extent * 1e-9

        self._ring = self._prepare_boundary()
        self._boundary_area = abs(signed_area(self._ring)) if self._ring is not None else 0.0
        outside = np.zeros(len(simplices), dtype=bool)
        if self._ring is not None:
            touching = triangles_touching_ring(tri_xy, self._ring, self._eps)
            clear = np.nonzero(~touching)[0]
            outside[clear] = ~points_in_ring(tri_xy[clear].mean(axis=1), self._ring)
            area_tol = self._eps * extent * 1e-3
            for index in np.nonzero(touching)[0]:
                region = clip_ring_to_triangle(self._ring, tri_xy[index])
                region_area = abs(signed_area(region))
                full_area = abs(signed_area(tri_xy[index]))
                if region_area <= area_tol:
                    outside[index] = True
                elif full_area - region_area > area_tol + full_area * 1e-9:
                    self._regions[int(index)] = region

        self.triangles = []
        for i, (v0, v1, v2) in enumerate(simplices):
            tri = Triangle(
                id=i,
                vertex_ids=(int(v0), int(v1), int(v2)),
                vertex_points=(vertex_points[v0], vertex_points[v1], vertex_points[v2]),
                is_boundary=bool(outside[i]),
                is_clipped=i in self._regions,
            )
            tri.area = self._triangle_area(tri.vertex_points)
            self.triangles.append(tri)
        return self.triangles

    def _triangle_area(self, points: Tuple[SurveyPoint, SurveyPoint, SurveyPoint]) -> float:
        """计算三角形的水平投影面积。"""
        p0, p1, p2 = points
        return abs(
            (p0.x * (p1.y - p2.y) + p1.x * (p2.y - p0.y) + p2.x * (p0.y - p1.y)) / 2
        )

    # ------------------------------------------------------------------
    # 方量
    # ------------------------------------------------------------------

    def _to_global(self, polygon) -> List[XY]:
        ox, oy = self._origin
        return [(float(x) + ox, float(y) + oy) for x, y in polygon]

    def _calculate_triangle(self, tri: Triangle):
        """计算单个三角形在边界内部分的挖填方。"""
        p0, p1, p2 = tri.vertex_points
        ox, oy = self._origin
        vertices = [(p0.x - ox, p0.y - oy), (p1.x - ox, p1.y - oy), (p2.x - ox, p2.y - oy)]
        dz = [p0.delta_z, p1.delta_z, p2.delta_z]

        if tri.is_clipped:
            region = self._regions[tri.id]
            plane = linear_plane(*vertices, *dz, eps=self._eps * self._eps)
            polygon = [(float(x), float(y)) for x, y in region]
            if plane is None:
                values = [sum(dz) / 3] * len(polygon)
            else:
                a, b, c = plane
                values = [a * x + b * y + c for x, y in polygon]
        else:
            polygon, values = vertices, dz

        has_cut = any(v > 0 for v in values)
        has_fill = any(v < 0 for v in values)

        if not (has_cut and has_fill):
            if tri.is_clipped:
                tri.area, tri.volume = polygon_integral(polygon, values)
            else:
                tri.area = self._triangle_area(tri.vertex_points)
                tri.volume = tri.area * sum(dz) / 3
            if tri.volume > 0:
                tri.cut_volume, tri.cut_area = tri.volume, tri.area
            elif tri.volume < 0:
                tri.fill_volume, tri.fill_area = -tri.volume, tri.area
            if tri.is_clipped:
                target = tri.cut_polygon if tri.volume > 0 else tri.fill_polygon
                target.extend(self._to_global(polygon))
            tri.avg_delta_z = tri.volume / tri.area if tri.area else 0.0
            return

        tri.is_mixed = True
        cut_poly, cut_values = clip_by_sign(polygon, values, positive=True)
        fill_poly, fill_values = clip_by_sign(polygon, values, positive=False)
        tri.cut_area, cut = polygon_integral(cut_poly, cut_values)
        tri.fill_area, fill = polygon_integral(fill_poly, fill_values)
        tri.cut_volume = max(cut, 0.0)
        tri.fill_volume = max(-fill, 0.0)
        tri.volume = tri.cut_volume - tri.fill_volume
        tri.area = tri.cut_area + tri.fill_area
        tri.avg_delta_z = tri.volume / tri.area if tri.area else 0.0
        tri.cut_polygon = self._to_global(cut_poly)
        tri.fill_polygon = self._to_global(fill_poly)

        zero = zero_crossing_points(vertices, dz)
        if len(zero) == 2:
            if tri.is_clipped:
                segments = clip_segment_to_ring(zero[0], zero[1], self._ring)
            else:
                segments = [(zero[0], zero[1])]
            tri.zero_segments = [tuple(self._to_global(segment)) for segment in segments]

    def calculate_volumes(self) -> CalculationResult:
        """计算挖填方量"""
        if not self.triangles:
            self.build_tin()

        result = CalculationResult()
        result.boundary_points = self.boundary_polygon
        result.boundary_area = self._boundary_area

        for tri in self.triangles:
            tri.volume = tri.cut_volume = tri.fill_volume = 0.0
            tri.cut_area = tri.fill_area = tri.avg_delta_z = 0.0
            tri.is_mixed = False
            tri.cut_polygon, tri.fill_polygon, tri.zero_segments = [], [], []
            if tri.is_boundary:
                continue
            self._calculate_triangle(tri)
            result.total_cut += tri.cut_volume
            result.total_fill += tri.fill_volume
            result.computed_area += tri.area
            result.triangle_count += 1
            result.mixed_triangle_count += tri.is_mixed
            result.clipped_triangle_count += tri.is_clipped

        result.net_volume = result.total_cut - result.total_fill
        result.triangles = self.triangles
        if self.points:
            designs = [pt.design_z for pt in self.points]
            result.design_min, result.design_max = min(designs), max(designs)
        result.mode = self.mode
        result.design_description = self._design_description()
        if self.compare_points:
            result.compare_point_count = len(self.compare_points)
        result.warnings = list(self.warnings) + self._coverage_warnings(result)
        return result

    def _design_description(self) -> str:
        if self.compare_points:
            return f"两期对比：以后期测量（{len(self.compare_points)} 个点）为比较面，高差 = 前期 − 后期"
        if self.design_plane is not None:
            text = self.design_plane.describe()
            listed = sum(1 for pt in self.points if pt.id in self.design_overrides)
            if listed:
                text += f"；另有 {listed} 个点单独指定设计高程"
            return text
        return ""

    def _coverage_warnings(self, result: CalculationResult) -> List[str]:
        ratio = result.coverage_ratio
        compare = self.mode == MODE_COMPARE
        covered = "两期测量共同覆盖的范围" if compare else "测点覆盖范围"
        if ratio is None:
            if compare and self._overlap_ratio is not None and self._overlap_ratio < COVERAGE_WARN_RATIO:
                return [
                    f"后期测量只覆盖了前期测量范围的 {self._overlap_ratio * 100:.1f}%，"
                    "只计算两期都有测点的部分。需要对比整个场地时，请补测或设置计算边界。"
                ]
            return []
        if ratio >= COVERAGE_WARN_RATIO:
            return []
        missing = result.boundary_area - result.computed_area
        if result.computed_area <= 0:
            return [
                f"计算边界（{result.boundary_area:.1f} m²）内没有{covered}，方量为 0。"
                "请检查边界是否画错位置，或测点的 X/Y 方向是否与边界一致（可在数据导入页使用“X/Y 互换”）。"
            ]
        return [
            f"计算边界面积 {result.boundary_area:.1f} m²，其中 {missing:.1f} m²"
            f"（{(1 - ratio) * 100:.1f}%）不在{covered}内，这部分没有计入方量。"
            "请补测边界附近的点，或把边界收进测点范围。"
        ]

    def _surfaces(self) -> Tuple[TinSurface, str, TinSurface, str]:
        """(地面曲面, 取值键, 比较面曲面, 取值键)，局部坐标。须在 build_tin() 之后调用。"""
        if self._tin_a is None:
            raise ValueError("请先构建三角网")
        tin_a, unique_a = self._tin_a
        surface_a = TinSurface(tin_a, {
            "z": np.array([self.points[i].z for i in unique_a]),
            "design_z": np.array([self.points[i].design_z for i in unique_a]),
        })
        if self._tin_b is None:
            return surface_a, "z", surface_a, "design_z"
        tin_b, unique_b = self._tin_b
        surface_b = TinSurface(tin_b, {"z": np.array([self.compare_points[i].z for i in unique_b])})
        return surface_a, "z", surface_b, "z"

    def sample_surfaces(self, xy: np.ndarray, extrapolate: bool = False) -> Tuple[np.ndarray, np.ndarray]:
        """在任意平面位置取 (实测/前期地面高程, 设计/后期高程)。

        设计高程在前期三角网上按顶点设计高程线性插值——与 TIN 法对设计面的处理一致，
        对统一高程和斜面是精确值。网外为 NaN；extrapolate=True 时按最近三角形平面外推。
        """
        local = np.asarray(xy, dtype=float).reshape(-1, 2) - np.asarray(self._origin)
        natural, natural_key, compare, compare_key = self._surfaces()
        sample = (lambda s, k: s.extrapolate(local, k)) if extrapolate else (lambda s, k: s.interpolate(local, k))
        return sample(natural, natural_key), sample(compare, compare_key)

    def coverage_polygon(self) -> List[XY]:
        """参与计算的测点覆盖范围（全局坐标，逆时针）：前期凸包；两期对比时为两期凸包的交。"""
        natural, _, compare, _ = self._surfaces()
        region = [tuple(p) for p in natural.hull().tolist()]
        if compare is not natural:
            region = clip_convex(region, [tuple(p) for p in compare.hull().tolist()])
        ox, oy = self._origin
        return [(x + ox, y + oy) for x, y in region]

    def run_full_calculation(self, design_elevation: float = None) -> CalculationResult:
        """运行完整计算流程。

        仅在显式传入 design_elevation 时覆盖点上的设计高程，
        避免把 set_design_elevations() 的分区高程冲掉。
        """
        if design_elevation is not None:
            self.set_design_elevation(design_elevation)
        self.build_tin()
        return self.calculate_volumes()


def extract_zero_contour_segments(result: CalculationResult) -> List[Tuple[XY, XY]]:
    """收集边界内所有零填挖线段（去重）。"""
    segments: List[Tuple[XY, XY]] = []
    seen = set()
    for tri in result.triangles:
        if tri.is_boundary:
            continue
        for a, b in tri.zero_segments:
            key = tuple(sorted(((round(a[0], 8), round(a[1], 8)), (round(b[0], 8), round(b[1], 8)))))
            if key[0] == key[1] or key in seen:
                continue
            seen.add(key)
            segments.append((a, b))
    return segments


def chain_segments(
    segments: List[Tuple[XY, XY]],
) -> List[List[XY]]:
    """把端点相接的线段连成多段线。"""
    if not segments:
        return []

    def rk(point: XY) -> XY:
        return (round(float(point[0]), 8), round(float(point[1]), 8))

    adj: Dict[XY, List[XY]] = {}
    for start, end in segments:
        a, b = rk(start), rk(end)
        if a == b:
            continue
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    visited = set()

    def walk(origin: XY, first: XY) -> List[XY]:
        path = [origin, first]
        visited.add(tuple(sorted((origin, first))))
        while True:
            current = path[-1]
            prev = path[-2]
            nxt = None
            for cand in adj.get(current, []):
                edge = tuple(sorted((current, cand)))
                if edge in visited:
                    continue
                if cand == prev:
                    continue
                nxt = cand
                break
            if nxt is None:
                break
            visited.add(tuple(sorted((current, nxt))))
            path.append(nxt)
        return path

    polylines: List[List[XY]] = []
    endpoints = [node for node, nbrs in adj.items() if len(nbrs) == 1]
    ordered_starts = endpoints + [node for node in adj if node not in endpoints]
    for node in ordered_starts:
        for nbr in adj.get(node, []):
            edge = tuple(sorted((node, nbr)))
            if edge in visited:
                continue
            polylines.append(walk(node, nbr))
    return polylines


def create_sample_data() -> List[SurveyPoint]:
    """创建示例测试数据"""
    np.random.seed(42)
    points = []
    for i in range(50):
        x = np.random.uniform(0, 100)
        y = np.random.uniform(0, 100)
        z = np.random.uniform(10, 20)  # 实测高程
        points.append(SurveyPoint(id=f"P{i+1}", x=x, y=y, z=z))
    return points


if __name__ == "__main__":
    # 简单测试
    calc = TINEarthworkCalculator(design_elevation=15.0)
    points = create_sample_data()
    calc.add_points(points)

    # 设置一个矩形边界
    calc.set_boundary([(0, 0), (100, 0), (100, 100), (0, 100)])

    result = calc.run_full_calculation()
    print(f"总挖方量: {result.total_cut:.2f} m³")
    print(f"总填方量: {result.total_fill:.2f} m³")
    print(f"净挖填方: {result.net_volume:.2f} m³")
    print(f"三角形数: {result.triangle_count}")
    print(f"混合三角形: {result.mixed_triangle_count}")
