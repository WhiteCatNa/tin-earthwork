"""
场地放坡：一个或多个设计平台（多边形 + 平台高程），平台外按挖方坡比、填方坡比自动放坡，直到与原地面相交。

平台外一点到最近平台的水平距离为 d，挖方坡面 Ds = H + d / m挖，填方坡面 Df = H − d / m填（平台内 d = 0，即 H）。
放坡后的设计面是“地面高于 Ds 处削到 Ds，低于 Df 处填到 Df，其余不动”，所以
    挖方 = ∫ max(地面 − Ds, 0)，填方 = ∫ max(Df − 地面, 0)，
两项各自是“地面三角网对设计三角网”的标准算法（与设计三角网模式相同，逐三角形精确叠加）。
Ds、Df 由平台边线加密点和规则格网点构成三角网：平台内、每条平台边对应的坡面条带内是平面，
三角网精确；只有平台转角外的锥面和两个平台坡面相接处是近似，误差随取样间距减小。
多个平台时，平台外每一点按最近的平台放坡。
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.calculator import (COMPARE_DESIGN, CalculationResult, SurveyPoint, TINEarthworkCalculator,
                             chain_segments, extract_zero_contour_segments)
from core.geometry import clip_segment_to_ring, normalize_ring, points_in_ring, ring_self_intersects, rings_overlap

XY = Tuple[float, float]
MAX_SAMPLES = 400_000


@dataclass
class Platform:
    name: str
    ring: List[XY]
    elevation: float
    cut_ratio: float = 1.0      # 挖方坡比 1 : m
    fill_ratio: float = 1.5     # 填方坡比 1 : n

    def validate(self):
        if len(self.ring) < 3:
            raise ValueError(f"平台“{self.name}”至少需要 3 个顶点")
        if ring_self_intersects(self.ring):
            raise ValueError(f"平台“{self.name}”的边线自相交")
        for label, value in (("高程", self.elevation), ("挖方坡比", self.cut_ratio), ("填方坡比", self.fill_ratio)):
            if not math.isfinite(value):
                raise ValueError(f"平台“{self.name}”的{label}不是有效数字")
        if self.cut_ratio <= 0 or self.fill_ratio <= 0:
            raise ValueError(f"平台“{self.name}”的坡比必须大于 0（1 : m 中的 m）")

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "elevation": self.elevation, "cut_ratio": self.cut_ratio,
                "fill_ratio": self.fill_ratio, "ring": [[float(x), float(y)] for x, y in self.ring]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Platform":
        return cls(str(data.get("name") or "平台"), [(float(x), float(y)) for x, y in data["ring"]],
                   float(data["elevation"]), float(data.get("cut_ratio", 1.0)), float(data.get("fill_ratio", 1.5)))


@dataclass
class SlopeResult:
    platforms: List[Platform]
    spacing: float
    total_cut: float = 0.0
    total_fill: float = 0.0
    platform_cut: float = 0.0
    platform_fill: float = 0.0
    cut_line: List[Tuple[XY, XY]] = field(default_factory=list)     # 坡顶线（挖方坡面与地面交线）
    fill_line: List[Tuple[XY, XY]] = field(default_factory=list)    # 坡脚线（填方坡面与地面交线）
    cut_polygons: List[List[XY]] = field(default_factory=list)      # 挖方范围（含平台内）
    fill_polygons: List[List[XY]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def slope_cut(self) -> float:
        return self.total_cut - self.platform_cut

    @property
    def slope_fill(self) -> float:
        return self.total_fill - self.platform_fill

    def rows(self) -> List[Tuple[str, float, float]]:
        return [("平台范围", self.platform_cut, self.platform_fill),
                ("边坡", self.slope_cut, self.slope_fill),
                ("合计", self.total_cut, self.total_fill)]


def check_platforms(platforms: Sequence[Platform]) -> None:
    if not platforms:
        raise ValueError("请先添加设计平台")
    for platform in platforms:
        platform.validate()
    rings = [normalize_ring(p.ring) for p in platforms]
    for i in range(len(rings)):
        for j in range(i + 1, len(rings)):
            if rings_overlap(rings[i], rings[j]):
                raise ValueError(f"平台“{platforms[i].name}”与“{platforms[j].name}”重叠")


def _distance_to_edges(xy: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """点到多边形边线的水平距离（不分内外）。"""
    best = np.full(len(xy), np.inf)
    for a, b in zip(ring, np.roll(ring, -1, axis=0)):
        edge = b - a
        length2 = float(edge @ edge)
        t = np.clip(((xy - a) @ edge) / length2, 0, 1) if length2 > 0 else np.zeros(len(xy))
        best = np.minimum(best, np.hypot(*(xy - (a + t[:, None] * edge)).T))
    return best


def _distance_to_ring(xy: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """点到多边形的水平距离，多边形内为 0。"""
    distance = _distance_to_edges(xy, ring)
    distance[points_in_ring(xy, ring)] = 0.0
    return distance


def slope_surfaces(platforms: Sequence[Platform], xy: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """各点的 (挖方坡面 Ds, 填方坡面 Df)，按最近平台。"""
    distances = np.array([_distance_to_ring(xy, np.asarray(p.ring, dtype=float)) for p in platforms])
    nearest = distances.argmin(axis=0)
    d = distances[nearest, np.arange(len(xy))]
    h = np.array([p.elevation for p in platforms])[nearest]
    m_cut = np.array([p.cut_ratio for p in platforms])[nearest]
    m_fill = np.array([p.fill_ratio for p in platforms])[nearest]
    return h + d / m_cut, h - d / m_fill


def _densify_ring(ring: Sequence[XY], step: float) -> np.ndarray:
    points = []
    closed = list(ring) + [ring[0]]
    for (x0, y0), (x1, y1) in zip(closed, closed[1:]):
        parts = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / step))
        for k in range(parts):
            t = k / parts
            points.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    return np.array(points)


def suggest_spacing(ground: Sequence[SurveyPoint]) -> float:
    xy = np.array([(p.x, p.y) for p in ground])
    span = float(np.ptp(xy, axis=0).max()) if len(xy) else 1.0
    raw = max(span / 150, 0.1)
    exponent = math.floor(math.log10(raw))
    for nice in (1, 2, 2.5, 5, 10):
        if nice * 10 ** exponent >= raw:
            return float(nice * 10 ** exponent)
    return float(10 ** (exponent + 1))


def _sample_points(platforms: Sequence[Platform], ground_xy: np.ndarray, spacing: float) -> np.ndarray:
    xmin, ymin = ground_xy.min(axis=0) - spacing
    xmax, ymax = ground_xy.max(axis=0) + spacing
    nx = int(math.ceil((xmax - xmin) / spacing)) + 1
    ny = int(math.ceil((ymax - ymin) / spacing)) + 1
    if nx * ny > MAX_SAMPLES:
        raise ValueError(f"放坡取样间距 {spacing:g} m 过小（{nx * ny} 个点），请加大")
    gx, gy = np.meshgrid(xmin + np.arange(nx) * spacing, ymin + np.arange(ny) * spacing)
    grid = np.column_stack([gx.ravel(), gy.ravel()])
    edges = [_densify_ring(p.ring, spacing / 2) for p in platforms]
    # 紧贴平台边线的格网点去掉，避免与边线点形成狭长三角形跨过折线
    near = np.zeros(len(grid), dtype=bool)
    for platform in platforms:
        near |= _distance_to_edges(grid, np.asarray(platform.ring, dtype=float)) < spacing * 0.3
    return np.concatenate([grid[~near]] + edges)


def _design_points(prefix: str, xy: np.ndarray, z: np.ndarray) -> List[SurveyPoint]:
    return [SurveyPoint(f"{prefix}{i}", float(x), float(y), float(h)) for i, ((x, y), h) in enumerate(zip(xy, z), 1)]


def _run_surface(ground: Sequence[SurveyPoint], design: List[SurveyPoint]) -> CalculationResult:
    calc = TINEarthworkCalculator()
    calc.add_points(list(ground))
    calc.set_compare_points(design, kind=COMPARE_DESIGN)
    return calc.run_full_calculation()


def _outside(segments, rings) -> List[Tuple[XY, XY]]:
    out = segments
    for ring in rings:
        clipped = []
        for a, b in out:
            clipped.extend(clip_segment_to_ring(a, b, ring, inside=False))
        out = clipped
    return out


def run_slope(ground: Sequence[SurveyPoint], platforms: Sequence[Platform], spacing: Optional[float] = None,
              lines: bool = True) -> SlopeResult:
    """放坡土方：平台 + 自动放坡的挖方、填方，坡顶线、坡脚线。"""
    check_platforms(platforms)
    if len(ground) < 3:
        raise ValueError("测点不足 3 个")
    spacing = suggest_spacing(ground) if spacing is None else float(spacing)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("放坡取样间距必须大于 0")
    ground_xy = np.array([(p.x, p.y) for p in ground])
    samples = _sample_points(platforms, ground_xy, spacing)
    ds, df = slope_surfaces(platforms, samples)
    cut_run = _run_surface(ground, _design_points("S", samples, ds))
    fill_run = _run_surface(ground, _design_points("F", samples, df))
    result = SlopeResult(list(platforms), spacing, cut_run.total_cut, fill_run.total_fill)

    for platform in platforms:
        calc = TINEarthworkCalculator(platform.elevation)
        calc.add_points(list(ground))
        calc.set_boundary(list(platform.ring))
        flat = calc.run_full_calculation()
        result.platform_cut += flat.total_cut
        result.platform_fill += flat.total_fill
        if flat.computed_area < abs(_ring_area(platform.ring)) * 0.995:
            result.warnings.append(f"平台“{platform.name}”有部分不在测点覆盖范围内，该部分不计方量")

    if lines:
        rings = [normalize_ring(p.ring) for p in platforms]
        result.cut_line = _outside(extract_zero_contour_segments(cut_run), rings)
        result.fill_line = _outside(extract_zero_contour_segments(fill_run), rings)
        result.cut_polygons = _region_polygons(cut_run, positive=True)
        result.fill_polygons = _region_polygons(fill_run, positive=False)

    # 测区边缘仍在挖、填：坡面到测区边上还没与地面相交
    hull_edge = _hull_samples(ground_xy, spacing)
    if len(hull_edge):
        natural = _ground_heights(ground, hull_edge)
        hull_ds, hull_df = slope_surfaces(platforms, hull_edge)
        tol = 1e-6
        if np.nanmax(natural - hull_ds) > tol or np.nanmax(hull_df - natural) > tol:
            result.warnings.append("部分边坡到测区边缘仍未与地面相交，坡顶线/坡脚线不闭合，测区外的边坡方量未计入")
    return result


def _ring_area(ring: Sequence[XY]) -> float:
    xy = np.asarray(ring, dtype=float)
    return 0.5 * float(np.dot(xy[:, 0], np.roll(xy[:, 1], -1)) - np.dot(xy[:, 1], np.roll(xy[:, 0], -1)))


def _region_polygons(result: CalculationResult, positive: bool) -> List[List[XY]]:
    polygons = []
    for tri in result.triangles:
        if tri.is_boundary:
            continue
        if tri.cut_polygon or tri.fill_polygon:
            polygon = tri.cut_polygon if positive else tri.fill_polygon
            if len(polygon) >= 3:
                polygons.append(polygon)
        elif (tri.volume > 0) == positive and tri.volume != 0:
            polygons.append([(p.x, p.y) for p in tri.vertex_points])
    return polygons


def _hull_samples(xy: np.ndarray, spacing: float) -> np.ndarray:
    from scipy.spatial import ConvexHull, QhullError
    try:
        hull = xy[ConvexHull(xy).vertices]
    except QhullError:
        return np.zeros((0, 2))
    centroid = hull.mean(axis=0)
    shrunk = centroid + (hull - centroid) * (1 - 1e-6)     # 稍向内收，保证在三角网内
    return _densify_ring([tuple(p) for p in shrunk], spacing)


def _ground_heights(ground: Sequence[SurveyPoint], xy: np.ndarray) -> np.ndarray:
    from scipy.interpolate import LinearNDInterpolator
    data = np.array([(p.x, p.y, p.z) for p in ground])
    origin = data[:, :2].mean(axis=0)
    return LinearNDInterpolator(data[:, :2] - origin, data[:, 2])(xy - origin)


def balance_platforms(ground: Sequence[SurveyPoint], platforms: Sequence[Platform], compact: float = 1.0,
                      spacing: Optional[float] = None) -> Tuple[float, SlopeResult]:
    """各平台同时升降同一高度，使放坡后挖方 = 填方 ÷ Ks'；返回 (升降量, 平衡后的结果)。"""
    from scipy.optimize import brentq

    check_platforms(platforms)
    if not math.isfinite(compact) or compact <= 0:
        raise ValueError("最终可松性系数 Ks' 必须大于 0")
    zs = [p.z for p in ground]
    heights = [p.elevation for p in platforms]
    low, high = min(zs) - max(heights), max(zs) - min(heights)
    spacing = suggest_spacing(ground) if spacing is None else spacing

    def shifted(delta: float) -> List[Platform]:
        return [Platform(p.name, p.ring, p.elevation + delta, p.cut_ratio, p.fill_ratio) for p in platforms]

    def gap(delta: float) -> float:
        # 搜索时取样加倍稀疏以节省时间，最后按原间距复算
        r = run_slope(ground, shifted(delta), spacing * 2, lines=False)
        return r.total_cut - r.total_fill / compact

    g_low, g_high = gap(low), gap(high)
    if g_low < 0 or g_high > 0:
        raise ValueError("在测点高程范围内找不到挖填平衡的平台高程")
    delta = brentq(gap, low, high, xtol=1e-4) if g_low * g_high < 0 else (low if g_low == 0 else high)
    delta = round(delta, 3)
    return delta, run_slope(ground, shifted(delta), spacing)


def export_slope_dxf(result: SlopeResult, filepath: str) -> None:
    """平台边线（带平台高程）、坡顶线、坡脚线，R12 DXF。"""
    from utils.data_handler import DataExporter

    parts = ["0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n", "0\nSECTION\n2\nENTITIES\n"]
    for platform in result.platforms:
        parts.append(DataExporter._dxf_polyline("PLATFORM", platform.ring, closed=True, elevation=platform.elevation))
    for layer, segments in (("SLOPE_TOP", result.cut_line), ("SLOPE_TOE", result.fill_line)):
        for polyline in chain_segments(segments):
            if len(polyline) >= 2:
                parts.append(DataExporter._dxf_polyline(layer, polyline, closed=False))
    parts.append("0\nENDSEC\n0\nEOF\n")
    with open(filepath, "w", encoding="utf-8") as handle:
        handle.write("".join(parts))
