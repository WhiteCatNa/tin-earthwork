"""
分层土方：把挖方、填方按高程分层（如不同土层分界标高），或按深度分层（如表土 0~0.3 m）。

三角形内地面 N 与设计面 D 都是线性的：
- 高程 h 以上的挖方 = ∫ max(min(N − D, N − h), 0)，高程 h 以上的填方 = ∫ max(min(D − N, D − h), 0)；
- 原地面以下深度 s 以下的挖方 = ∫ max(N − D − s, 0)，填方同理（原地面以上 s 以上的部分）。
被积函数是两个线性函数取小再取正，按半平面精确裁剪后在每块上积分，结果是精确值；
相邻两个分界之差就是该层的方量，各层之和等于总挖方、总填方。计算范围与 TIN 法一致。
"""
import math
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Sequence, Tuple

import numpy as np

from core.calculator import CalculationResult, TINEarthworkCalculator
from core.geometry import clip_ring_halfplane, linear_plane, polygon_integral

Plane = Tuple[float, float, float]      # v = a·x + b·y + c（局部坐标）

LAYER_ELEVATION = "elevation"
LAYER_DEPTH = "depth"
LAYER_MODES = (LAYER_ELEVATION, LAYER_DEPTH)
MAX_LAYERS = 50


@dataclass
class LayerRow:
    lower: Optional[float]      # 高程分层：下界高程；深度分层：上界深度（较浅）。None 为不限
    upper: Optional[float]
    cut: float = 0.0
    fill: float = 0.0

    def label(self, mode: str) -> str:
        if mode == LAYER_DEPTH:
            if self.upper is None:
                return f"{self.lower:g} m 以下" if self.lower else "全部"
            return f"{self.lower or 0:g} ~ {self.upper:g} m"
        if self.lower is None and self.upper is None:
            return "全部"
        if self.lower is None:
            return f"{self.upper:.3f} m 以下"
        if self.upper is None:
            return f"{self.lower:.3f} m 以上"
        return f"{self.lower:.3f} ~ {self.upper:.3f} m"


@dataclass
class LayerResult:
    mode: str
    values: List[float]
    rows: List[LayerRow] = field(default_factory=list)

    @property
    def title(self) -> str:
        return "按高程分层" if self.mode == LAYER_ELEVATION else "按深度分层（挖方自原地面向下、填方自原地面向上）"

    @property
    def total_cut(self) -> float:
        return sum(row.cut for row in self.rows)

    @property
    def total_fill(self) -> float:
        return sum(row.fill for row in self.rows)


def parse_layer_values(text: str, mode: str) -> List[float]:
    """“48, 50.5 52” → 升序去重的分界值；深度须大于 0。"""
    if mode not in LAYER_MODES:
        raise ValueError(f"未知的分层方式: {mode}")
    parts = [part for part in text.replace("，", ",").replace("；", ",").replace(";", ",").replace(",", " ").split()]
    if not parts:
        raise ValueError("请填写分层的分界高程或深度，多个值用逗号或空格分开")
    values = []
    for part in parts:
        try:
            value = float(part)
        except ValueError:
            raise ValueError(f"分层分界值“{part}”不是数字") from None
        if not math.isfinite(value):
            raise ValueError(f"分层分界值“{part}”不是有效数字")
        if mode == LAYER_DEPTH and value <= 0:
            raise ValueError("分层深度必须大于 0")
        values.append(value)
    values = sorted(set(values))
    if len(values) > MAX_LAYERS:
        raise ValueError(f"分界值最多 {MAX_LAYERS} 个")
    return values


def _evaluate(plane: Plane, polygon: np.ndarray) -> np.ndarray:
    a, b, c = plane
    return polygon[:, 0] * a + polygon[:, 1] * b + c


def _clip(polygon: np.ndarray, plane: Plane) -> np.ndarray:
    return clip_ring_halfplane(polygon, *plane) if len(polygon) >= 3 else polygon


def positive_min_integral(polygon: np.ndarray, first: Plane, second: Optional[Plane] = None) -> float:
    """∫ max(min(f1, f2), 0) dA，f1、f2 为线性函数；second 为 None 时即 ∫ max(f1, 0)。"""
    polygon = _clip(polygon, first)
    if second is not None:
        polygon = _clip(polygon, second)
    if len(polygon) < 3:
        return 0.0
    if second is None:
        return polygon_integral(polygon, _evaluate(first, polygon))[1]
    difference = tuple(p - q for p, q in zip(first, second))       # f1 − f2
    total = 0.0
    for keep, plane in (((-difference[0], -difference[1], -difference[2]), first), (difference, second)):
        part = _clip(polygon, keep)                                  # f1 ≤ f2 处取 f1，否则取 f2
        if len(part) >= 3:
            total += polygon_integral(part, _evaluate(plane, part))[1]
    return total


def surface_pieces(calculator: TINEarthworkCalculator) -> Iterator[Tuple[np.ndarray, Plane, Plane, float]]:
    """计算范围拆成多边形：(局部坐标顶点, 地面线性函数, 设计面线性函数, 符号)；挖空区部分符号为 −1。"""
    ox, oy = calculator.origin
    eps = calculator._eps * calculator._eps
    for tri in calculator.triangles:
        if tri.is_boundary:
            continue
        corners = np.array([(p.x - ox, p.y - oy) for p in tri.vertex_points])
        planes = []
        for key in ("z", "design_z"):
            values = [getattr(p, key) for p in tri.vertex_points]
            plane = linear_plane(*map(tuple, corners), *values, eps=eps)
            planes.append(plane if plane is not None else (0.0, 0.0, sum(values) / 3))
        region = calculator._regions.get(tri.id)
        yield (corners if region is None else np.asarray(region, dtype=float)), planes[0], planes[1], 1.0
        for hole in calculator._hole_regions.get(tri.id, []):
            yield np.asarray(hole, dtype=float), planes[0], planes[1], -1.0


def run_layers(calculator: TINEarthworkCalculator, mode: str, values: Sequence[float]) -> LayerResult:
    """在已计算的三角网上分层统计挖方、填方（calculator 须已完成计算）。"""
    if mode not in LAYER_MODES:
        raise ValueError(f"未知的分层方式: {mode}")
    values = sorted(set(float(v) for v in values))
    if not values:
        raise ValueError("请至少给出一个分界值")
    pieces = list(surface_pieces(calculator))
    if not pieces:
        raise ValueError("计算范围内没有三角形，无法分层")

    def accumulate(level: Optional[float]) -> Tuple[float, float]:
        """level 以上（高程）或以下（深度）的挖方、填方；None 为全部。"""
        cut = fill = 0.0
        for polygon, natural, design, sign in pieces:
            n_minus_d = tuple(p - q for p, q in zip(natural, design))
            d_minus_n = tuple(-v for v in n_minus_d)
            if level is None:
                cut_first, cut_second, fill_first, fill_second = n_minus_d, None, d_minus_n, None
            elif mode == LAYER_ELEVATION:
                cut_first, cut_second = n_minus_d, (natural[0], natural[1], natural[2] - level)
                fill_first, fill_second = d_minus_n, (design[0], design[1], design[2] - level)
            else:
                cut_first = (n_minus_d[0], n_minus_d[1], n_minus_d[2] - level)
                fill_first = (d_minus_n[0], d_minus_n[1], d_minus_n[2] - level)
                cut_second = fill_second = None
            cut += sign * positive_min_integral(polygon, cut_first, cut_second)
            fill += sign * positive_min_integral(polygon, fill_first, fill_second)
        return cut, fill

    # 各分界“以上/以下”的累计量，相邻相减即为每层方量
    cumulative = [accumulate(None)] + [accumulate(level) for level in values]
    result = LayerResult(mode=mode, values=values)
    bounds = [None] + values + [None]
    if mode == LAYER_DEPTH:
        bounds[0] = 0.0
    for index in range(len(values) + 1):
        upper_cum = cumulative[index + 1] if index < len(values) else (0.0, 0.0)
        lower_cum = cumulative[index]
        row = LayerRow(lower=bounds[index], upper=bounds[index + 1],
                       cut=max(lower_cum[0] - upper_cum[0], 0.0), fill=max(lower_cum[1] - upper_cum[1], 0.0))
        result.rows.append(row)
    return result


def layer_summary(result: CalculationResult) -> List[Tuple[str, float, float]]:
    """[(层, 挖方, 填方)]，用于报告。"""
    layers = getattr(result, "layers", None)
    if layers is None:
        return []
    return [(row.label(layers.mode), row.cut, row.fill) for row in layers.rows]
