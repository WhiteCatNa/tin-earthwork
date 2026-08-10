"""
核心计算模块：TIN 三角网构建、挖填方计算、混合三角形分割
"""
import numpy as np
from scipy.spatial import Delaunay
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any
import math


@dataclass
class SurveyPoint:
    """测量点"""
    id: str
    x: float
    y: float
    z: float  # 实测高程
    design_z: float = 0.0  # 设计高程
    delta_z: float = 0.0   # 高差 (实测 - 设计)


@dataclass
class Triangle:
    """三角形单元"""
    id: int
    vertex_ids: Tuple[int, int, int]  # 顶点索引
    vertex_points: Tuple[SurveyPoint, SurveyPoint, SurveyPoint]
    area: float = 0.0
    avg_delta_z: float = 0.0
    volume: float = 0.0
    cut_volume: float = 0.0
    fill_volume: float = 0.0
    is_boundary: bool = False
    is_mixed: bool = False  # 是否为混合挖填三角形
    sub_triangles: List['Triangle'] = None  # 分割后的子三角形
    
    def __post_init__(self):
        if self.sub_triangles is None:
            self.sub_triangles = []


@dataclass
class CalculationResult:
    """计算结果汇总"""
    total_cut: float = 0.0
    total_fill: float = 0.0
    net_volume: float = 0.0
    triangle_count: int = 0
    mixed_triangle_count: int = 0
    triangles: List[Triangle] = None
    boundary_points: List[Tuple[float, float]] = None
    
    def __post_init__(self):
        if self.triangles is None:
            self.triangles = []
        if self.boundary_points is None:
            self.boundary_points = []


class TINEarthworkCalculator:
    """TIN 土方计算核心类"""
    
    def __init__(self, design_elevation: float = 0.0):
        self.design_elevation = design_elevation
        self.points: List[SurveyPoint] = []
        self.boundary_polygon: List[Tuple[float, float]] = []
        self.triangles: List[Triangle] = []
        self.delaunay = None
        
    def set_design_elevation(self, elevation: float):
        """设置统一设计高程"""
        self.design_elevation = elevation
        for pt in self.points:
            pt.design_z = elevation
            pt.delta_z = pt.z - elevation
            
    def set_design_elevations(self, elevations: Dict[str, float]):
        """设置分区设计高程 (点号 -> 高程)"""
        for pt in self.points:
            if pt.id in elevations:
                pt.design_z = elevations[pt.id]
            else:
                pt.design_z = self.design_elevation
            pt.delta_z = pt.z - pt.design_z
    
    def add_points(self, points: List[SurveyPoint]):
        """添加测量点，并初始化统一设计高程下的高差。"""
        self.points = points
        self.set_design_elevation(self.design_elevation)
        
    def set_boundary(self, boundary: List[Tuple[float, float]]):
        """设置计算边界"""
        self.boundary_polygon = boundary
        
    def _point_in_polygon(self, x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
        """射线法判断点是否在多边形内"""
        if not polygon or len(polygon) < 3:
            return True
        inside = False
        n = len(polygon)
        for i in range(n):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % n]
            if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
                inside = not inside
        return inside
    
    def _triangle_centroid_in_boundary(self, tri: Triangle) -> bool:
        """判断三角形质心是否在边界内"""
        cx = sum(p.x for p in tri.vertex_points) / 3
        cy = sum(p.y for p in tri.vertex_points) / 3
        return self._point_in_polygon(cx, cy, self.boundary_polygon)
    
    def build_tin(self) -> List[Triangle]:
        """构建 TIN 三角网"""
        if len(self.points) < 3:
            raise ValueError("至少需要 3 个测量点才能构建三角网")
            
        coords = np.array([[p.x, p.y] for p in self.points])
        self.delaunay = Delaunay(coords)
        
        self.triangles = []
        for i, simplex in enumerate(self.delaunay.simplices):
            v0, v1, v2 = simplex
            tri = Triangle(
                id=i,
                vertex_ids=(v0, v1, v2),
                vertex_points=(self.points[v0], self.points[v1], self.points[v2])
            )
            # 计算水平投影面积
            tri.area = self._triangle_area(tri.vertex_points)
            # 判断是否在边界内
            if self.boundary_polygon:
                tri.is_boundary = not self._triangle_centroid_in_boundary(tri)
            self.triangles.append(tri)
            
        return self.triangles
    
    def _interpolate_zero_point(self, p1: SurveyPoint, p2: SurveyPoint) -> SurveyPoint:
        """在两点之间插值零填挖点 (高差为0的点)"""
        dz1 = p1.delta_z
        dz2 = p2.delta_z
        
        if dz1 == 0:
            return p1
        if dz2 == 0:
            return p2
            
        # 线性插值
        t = dz1 / (dz1 - dz2)  # dz1 和 dz2 异号，t 在 (0,1) 之间
        x = p1.x + t * (p2.x - p1.x)
        y = p1.y + t * (p2.y - p1.y)
        z = p1.z + t * (p2.z - p1.z)
        design_z = p1.design_z + t * (p2.design_z - p1.design_z)
        
        return SurveyPoint(
            id=f"{p1.id}_{p2.id}_zero",
            x=x, y=y, z=z,
            design_z=design_z,
            delta_z=0.0
        )
    
    def _triangle_area(self, points: Tuple[SurveyPoint, SurveyPoint, SurveyPoint]) -> float:
        """计算三角形的水平投影面积。"""
        p0, p1, p2 = points
        return abs(
            (p0.x * (p1.y - p2.y) + p1.x * (p2.y - p0.y) + p2.x * (p0.y - p1.y)) / 2
        )

    def _calculate_triangle_volume(self, tri: Triangle):
        """计算一个符号一致的三角形体积。"""
        tri.area = self._triangle_area(tri.vertex_points)
        tri.avg_delta_z = sum(point.delta_z for point in tri.vertex_points) / 3
        tri.volume = tri.area * tri.avg_delta_z
        tri.cut_volume = max(tri.volume, 0.0)
        tri.fill_volume = max(-tri.volume, 0.0)

    def _clip_triangle_by_delta(self, vertices: List[SurveyPoint], keep_cut: bool) -> List[SurveyPoint]:
        """用零填挖线裁剪三角形，保留挖方或填方一侧。"""
        clipped = []
        for current, following in zip(vertices, vertices[1:] + vertices[:1]):
            current_inside = current.delta_z >= 0 if keep_cut else current.delta_z <= 0
            following_inside = following.delta_z >= 0 if keep_cut else following.delta_z <= 0
            if current_inside:
                clipped.append(current)
            if current_inside != following_inside:
                clipped.append(self._interpolate_zero_point(current, following))
        return clipped

    def _triangulate_clipped_polygon(self, tri: Triangle, vertices: List[SurveyPoint], suffix: str) -> List[Triangle]:
        """将裁剪后的三角形/四边形扇形拆分并计算体积。"""
        if len(vertices) < 3:
            return []

        sub_triangles = []
        for index in range(1, len(vertices) - 1):
            sub_triangle = Triangle(
                id=f"{tri.id}_{suffix}{index}",
                vertex_ids=(-1, -1, -1),
                vertex_points=(vertices[0], vertices[index], vertices[index + 1]),
                is_boundary=tri.is_boundary,
            )
            self._calculate_triangle_volume(sub_triangle)
            sub_triangles.append(sub_triangle)
        return sub_triangles

    def _split_mixed_triangle(self, tri: Triangle) -> List[Triangle]:
        """按零填挖线裁剪混合三角形，并计算每个子三角形体积。"""
        vertices = list(tri.vertex_points)
        tri.is_mixed = True
        cut_vertices = self._clip_triangle_by_delta(vertices, keep_cut=True)
        fill_vertices = self._clip_triangle_by_delta(vertices, keep_cut=False)
        sub_triangles = (
            self._triangulate_clipped_polygon(tri, cut_vertices, "cut")
            + self._triangulate_clipped_polygon(tri, fill_vertices, "fill")
        )
        tri.sub_triangles = sub_triangles
        return sub_triangles
    
    def calculate_volumes(self) -> CalculationResult:
        """计算挖填方量"""
        if not self.triangles:
            self.build_tin()
            
        result = CalculationResult()
        result.boundary_points = self.boundary_polygon
        
        for tri in self.triangles:
            if tri.is_boundary:
                continue
                
            p0, p1, p2 = tri.vertex_points
            dz = [p0.delta_z, p1.delta_z, p2.delta_z]
            
            # 判断类型
            has_cut = any(d > 0 for d in dz)
            has_fill = any(d < 0 for d in dz)
            
            if has_cut and has_fill:
                # 混合挖填三角形，需要分割
                sub_tris = self._split_mixed_triangle(tri)
                tri.cut_volume = sum(t.cut_volume for t in sub_tris)
                tri.fill_volume = sum(t.fill_volume for t in sub_tris)
                tri.volume = tri.cut_volume - tri.fill_volume
                tri.area = sum(t.area for t in sub_tris)
                tri.avg_delta_z = tri.volume / tri.area if tri.area else 0.0
                result.mixed_triangle_count += 1
            else:
                # 纯挖或纯填
                tri.avg_delta_z = sum(dz) / 3
                tri.volume = tri.area * tri.avg_delta_z
                if tri.volume > 0:
                    tri.cut_volume = tri.volume
                    tri.fill_volume = 0
                else:
                    tri.cut_volume = 0
                    tri.fill_volume = -tri.volume
                    
            result.total_cut += tri.cut_volume
            result.total_fill += tri.fill_volume
            result.triangle_count += 1
            
        result.net_volume = result.total_cut - result.total_fill
        result.triangles = self.triangles
        
        return result
    
    def run_full_calculation(self, design_elevation: float = None) -> CalculationResult:
        """运行完整计算流程"""
        if design_elevation is not None:
            self.set_design_elevation(design_elevation)
        elif self.design_elevation != 0.0:
            self.set_design_elevation(self.design_elevation)
        self.build_tin()
        return self.calculate_volumes()


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