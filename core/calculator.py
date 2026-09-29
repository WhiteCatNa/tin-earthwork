"""
核心计算模块：TIN 三角网构建、按计算边界精确裁剪、挖填方计算
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.spatial import Delaunay, QhullError

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

    @property
    def uniform_design(self) -> bool:
        return abs(self.design_max - self.design_min) < 1e-9

    @property
    def design_text(self) -> str:
        """报告中的设计高程说明。"""
        if self.uniform_design:
            return f"{self.design_min:.3f} m"
        return f"按点变化 {self.design_min:.3f} ~ {self.design_max:.3f} m（分区/逐点设计高程）"

    @property
    def coverage_ratio(self) -> Optional[float]:
        if self.boundary_area <= 0:
            return None
        return self.computed_area / self.boundary_area


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
        self.points: List[SurveyPoint] = []
        self.boundary_polygon: List[XY] = []
        self.triangles: List[Triangle] = []
        self.delaunay = None
        self.warnings: List[str] = []
        self._origin: Tuple[float, float] = (0.0, 0.0)
        self._ring: Optional[np.ndarray] = None       # 局部坐标下的逆时针边界
        self._regions: Dict[int, np.ndarray] = {}     # 被裁剪三角形在边界内的部分（局部坐标）
        self._boundary_area = 0.0
        self._eps = 1e-9

    def set_design_elevation(self, elevation: float):
        """设置统一设计高程"""
        self.design_elevation = elevation
        for pt in self.points:
            pt.design_z = elevation
            pt.delta_z = pt.z - elevation

    def set_design_elevations(self, elevations: Dict[str, float], default: Optional[float] = None):
        """设置分区设计高程 (点号 -> 高程)。

        未列出的点使用 default；不传 default 时沿用当前的统一设计高程。
        """
        if default is not None:
            self.design_elevation = float(default)
        for pt in self.points:
            pt.design_z = float(elevations.get(pt.id, self.design_elevation))
            pt.delta_z = pt.z - pt.design_z

    def add_points(self, points: List[SurveyPoint]):
        """添加测量点。

        若测点已带逐点设计高程，则保留并刷新高差；否则套用统一设计高程。
        """
        self.points = points
        if any(pt.has_design_z for pt in points):
            for pt in self.points:
                pt.delta_z = pt.z - pt.design_z
            unique = {round(pt.design_z, 6) for pt in points if pt.has_design_z}
            if len(unique) == 1:
                self.design_elevation = next(iter(unique))
        else:
            self.set_design_elevation(self.design_elevation)

    def set_boundary(self, boundary: List[XY]):
        """设置计算边界"""
        self.boundary_polygon = boundary

    # ------------------------------------------------------------------
    # 构网
    # ------------------------------------------------------------------

    def _check_finite(self):
        bad = [
            str(pt.id) for pt in self.points
            if not all(math.isfinite(value) for value in (pt.x, pt.y, pt.z, pt.design_z))
        ]
        if bad:
            raise ValueError(f"以下测点的坐标或高程为空或不是数字，请修正后再计算：{format_id_list(bad)}")

    def _unique_point_indices(self, coords: np.ndarray) -> np.ndarray:
        """合并完全重复的测点；平面坐标相同但高程不同时报错，避免随机丢点。"""
        keys = np.round(coords / DUPLICATE_TOLERANCE).astype(np.int64)
        _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        inverse = inverse.ravel()
        if len(first) == len(coords):
            return np.arange(len(coords))

        conflicts: List[str] = []
        ignored: List[str] = []
        for group in np.nonzero(np.bincount(inverse) > 1)[0]:
            members = [self.points[i] for i in np.nonzero(inverse == group)[0]]
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
                "以下测点平面坐标相同但高程不同，无法确定该用哪个，"
                f"请在数据表中删除或修正后再计算：\n{listed}{more}"
            )
        self.warnings.append(f"有 {len(ignored)} 个测点与其他点完全重复，已忽略：{format_id_list(ignored)}")
        return np.sort(first)

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
        """构建 TIN 三角网，并按计算边界把三角形分为 边界内 / 跨边界 / 边界外。"""
        if len(self.points) < 3:
            raise ValueError("至少需要 3 个测量点才能构建三角网")
        self.warnings = []
        self._regions = {}
        self._check_finite()

        coords = np.array([[p.x, p.y] for p in self.points], dtype=float)
        unique = self._unique_point_indices(coords)
        # 以左下角为局部原点，避免大地坐标（百万米量级）带来的舍入误差
        origin = coords[unique].min(axis=0)
        self._origin = (float(origin[0]), float(origin[1]))
        local = coords - origin
        try:
            self.delaunay = Delaunay(local[unique])
        except QhullError as exc:
            raise ValueError(
                "无法构建三角网：测量点共线、重复或分布不足以构成三角形，请检查坐标"
            ) from exc
        if len(self.delaunay.coplanar):
            dropped = [str(self.points[unique[i]].id) for i in self.delaunay.coplanar[:, 0]]
            self.warnings.append(f"有 {len(dropped)} 个测点与相邻点距离过近，未参与构网：{format_id_list(dropped)}")

        simplices = unique[self.delaunay.simplices]
        tri_xy = local[simplices]
        extent = float(np.ptp(local[unique], axis=0).max()) or 1.0
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
                vertex_points=(self.points[v0], self.points[v1], self.points[v2]),
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
        result.warnings = list(self.warnings) + self._coverage_warnings(result)
        return result

    def _coverage_warnings(self, result: CalculationResult) -> List[str]:
        ratio = result.coverage_ratio
        if ratio is None or ratio >= COVERAGE_WARN_RATIO:
            return []
        missing = result.boundary_area - result.computed_area
        if result.computed_area <= 0:
            return [
                f"计算边界（{result.boundary_area:.1f} m²）内没有测点覆盖，方量为 0。"
                "请检查边界是否画错位置，或测点的 X/Y 方向是否与边界一致（可在数据导入页使用“X/Y 互换”）。"
            ]
        return [
            f"计算边界面积 {result.boundary_area:.1f} m²，其中 {missing:.1f} m²"
            f"（{(1 - ratio) * 100:.1f}%）不在测点覆盖范围内，这部分没有计入方量。"
            "请补测边界附近的点，或把边界收进测点范围。"
        ]

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
