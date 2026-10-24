"""
断面法土方：沿推进方向按固定间距切一组平行横断面，算每个断面的挖方、填方面积，
相邻断面按平均断面法（(A1 + A2) / 2 × 间距）求方量。

断面线与三角网逐个三角形求交，三角形内地面和设计面都是线性的，断面面积是精确值；
断面法与 TIN 法的差异只来自“断面之间按线性过渡”的假设，用来校核，也用来出断面图。
计算范围与 TIN 法一致（边界以内、挖空区以外、有测点覆盖）。
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from core.calculator import CalculationResult, TINEarthworkCalculator
from core.geometry import zero_crossing_points

XY = Tuple[float, float]

MAX_SECTIONS = 2000


@dataclass
class SectionPiece:
    """断面上连续的一段：偏距 s0→s1，两端的地面高程与设计（后期）高程，段内线性。"""
    s0: float
    s1: float
    natural0: float
    natural1: float
    design0: float
    design1: float


@dataclass
class Section:
    index: int
    station: float              # 里程：沿推进方向到第一个断面的距离 (m)
    start: XY                   # 断面线两端（全局坐标），偏距 0 在 start
    end: XY
    pieces: List[SectionPiece] = field(default_factory=list)
    cut_area: float = 0.0       # 地面高于设计面部分的断面面积 (m²)
    fill_area: float = 0.0

    @property
    def width(self) -> float:
        return sum(p.s1 - p.s0 for p in self.pieces)


@dataclass
class SectionResult:
    spacing: float
    angle: float                # 推进方向与 +X 轴的夹角（度，逆时针）
    sections: List[Section] = field(default_factory=list)
    volumes: List[Tuple[float, float, float]] = field(default_factory=list)  # 相邻断面之间 (距离, 挖方, 填方)
    total_cut: float = 0.0
    total_fill: float = 0.0
    warnings: List[str] = field(default_factory=list)

    @property
    def net_volume(self) -> float:
        return self.total_cut - self.total_fill

    def comparison(self, tin: CalculationResult) -> List[Tuple[str, float, float, float, Optional[float]]]:
        """[(项目, TIN 法, 断面法, 差值, 相对差 %)]"""
        rows = []
        for label, tin_value, value in (
            ("挖方", tin.total_cut, self.total_cut),
            ("填方", tin.total_fill, self.total_fill),
            ("挖填差值", tin.net_volume, self.net_volume),
        ):
            diff = value - tin_value
            relative = diff / abs(tin_value) * 100 if abs(tin_value) > 1e-6 else None
            rows.append((label, tin_value, value, diff, relative))
        return rows


def _positive_integral(a: float, b: float, length: float) -> Tuple[float, float]:
    """线性函数从 a 变到 b，长度 length 上 ∫max(v,0) 与 ∫max(−v,0)。"""
    if a >= 0 and b >= 0:
        return (a + b) / 2 * length, 0.0
    if a <= 0 and b <= 0:
        return 0.0, -(a + b) / 2 * length
    t = a / (a - b)
    first, second = t * length, (1 - t) * length
    if a > 0:
        return a * first / 2, -b * second / 2
    return b * second / 2, -a * first / 2


def _plane(tri, key: str, origin: XY):
    """三角形上 key 的线性函数 v = a·x + b·y + c（局部坐标）；退化时取平均值。"""
    ox, oy = origin
    pts = tri.vertex_points
    (x0, y0), (x1, y1), (x2, y2) = [(p.x - ox, p.y - oy) for p in pts]
    v0, v1, v2 = [getattr(p, key) for p in pts]
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if det == 0:
        mean = (v0 + v1 + v2) / 3
        return lambda x, y: mean
    a = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det
    b = ((v2 - v0) * (x1 - x0) - (v1 - v0) * (x2 - x0)) / det
    return lambda x, y: v0 + a * (x - x0) + b * (y - y0)


def run_sections(calculator: TINEarthworkCalculator, tin: CalculationResult, spacing: float,
                 angle: float = 0.0) -> SectionResult:
    """在已计算的三角网上切断面（calculator 须已完成 calculate_volumes）。

    angle 为推进方向（里程增加方向）与 +X 轴的夹角，断面线与推进方向垂直。
    """
    spacing = float(spacing)
    angle = float(angle)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("断面间距必须是大于 0 的数字")
    if not math.isfinite(angle):
        raise ValueError("断面方向角必须是数字")
    triangles = [tri for tri in tin.triangles if not tri.is_boundary and tri.area > 0]
    if not triangles:
        raise ValueError("计算范围内没有三角形，无法切断面")

    ox, oy = calculator.origin
    u = np.round([math.cos(math.radians(angle)), math.sin(math.radians(angle))], 15)
    v = np.array([-u[1], u[0]])
    xy = np.array([[(p.x - ox, p.y - oy) for p in tri.vertex_points] for tri in triangles])   # [n, 3, 2]
    along = xy @ u                      # 每个顶点的里程坐标
    # 断面范围取实际计算范围（三角形按边界裁剪后的部分），首末断面落在计算范围两端
    region = np.concatenate([polygon for polygon, _, sign in calculator.computed_pieces() if sign > 0])
    umin, umax = float((region @ u).min()), float((region @ u).max())
    vmin, vmax = float((region @ v).min()), float((region @ v).max())
    count = math.floor((umax - umin) / spacing + 1e-9) + 1
    stations = [umin + k * spacing for k in range(count)]
    if umax - stations[-1] > spacing * 1e-6:
        stations.append(umax)
    if len(stations) > MAX_SECTIONS:
        raise ValueError(f"断面间距 {spacing:g} m 过小：需要 {len(stations)} 个断面，请加大间距")

    tri_min, tri_max = along.min(axis=1), along.max(axis=1)
    eps = max(umax - umin, vmax - vmin, 1.0) * 1e-9
    result = SectionResult(spacing=spacing, angle=angle)
    for index, station in enumerate(stations, 1):
        base = u * station + v * vmin      # 偏距 0 处（局部坐标）
        section = Section(
            index=index, station=station - umin,
            start=(float(base[0] + ox), float(base[1] + oy)),
            end=(float(base[0] + v[0] * (vmax - vmin) + ox), float(base[1] + v[1] * (vmax - vmin) + oy)),
        )
        seen = set()
        for k in np.nonzero((tri_min <= station + eps) & (tri_max >= station - eps))[0].tolist():
            tri = triangles[k]
            corners = [tuple(point) for point in xy[k]]
            offsets = along[k] - station
            offsets[np.abs(offsets) <= eps] = 0.0     # 断面恰好经过顶点或沿三角形边时不丢段
            crossing = zero_crossing_points(corners, offsets.tolist())
            if len(crossing) != 2:
                continue
            natural = _plane(tri, "z", (ox, oy))
            design = _plane(tri, "design_z", (ox, oy))
            for a, b in calculator.clip_to_region(tri, crossing[0], crossing[1]):
                a = (a[0] - ox, a[1] - oy)
                b = (b[0] - ox, b[1] - oy)
                s0 = float(np.dot(a, v) - vmin)
                s1 = float(np.dot(b, v) - vmin)
                if s0 > s1:
                    a, b, s0, s1 = b, a, s1, s0
                key = (round(s0, 6), round(s1, 6))
                if s1 - s0 <= eps or key in seen:
                    continue
                seen.add(key)
                piece = SectionPiece(s0, s1, natural(*a), natural(*b), design(*a), design(*b))
                section.pieces.append(piece)
                cut, fill = _positive_integral(piece.natural0 - piece.design0, piece.natural1 - piece.design1, s1 - s0)
                section.cut_area += cut
                section.fill_area += fill
        section.pieces.sort(key=lambda p: p.s0)
        result.sections.append(section)

    for first, second in zip(result.sections, result.sections[1:]):
        distance = second.station - first.station
        cut = (first.cut_area + second.cut_area) / 2 * distance
        fill = (first.fill_area + second.fill_area) / 2 * distance
        result.volumes.append((distance, cut, fill))
        result.total_cut += cut
        result.total_fill += fill
    if len(result.sections) < 3:
        result.warnings.append("断面少于 3 个，断面法结果误差较大，请减小断面间距")
    return result
