"""
等高线：在参与计算的三角网上按等高距追踪等高线，只取计算范围（边界以内、挖空区以外）。

三角网内高程线性变化，等高线在每个三角形内是直线段，结果与 TIN 精确一致。
每 5 条取一条计曲线（高程是 5 倍等高距的整数倍）。
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.calculator import TINEarthworkCalculator, chain_segments

XY = Tuple[float, float]

# 一张图最多追踪的等高线条数，防止等高距填得过小
MAX_LEVELS = 400
MAJOR_EVERY = 5
NICE_STEPS = (0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0)


@dataclass
class Contour:
    level: float
    major: bool
    polyline: List[XY]


@dataclass
class ContourSet:
    interval: float
    natural: List[Contour] = field(default_factory=list)   # 实测（两期对比时为前期）地面
    design: List[Contour] = field(default_factory=list)    # 设计面（两期对比时为后期地面）


def suggest_interval(zmin: float, zmax: float) -> float:
    """按高差选常用等高距，约 10～25 条。"""
    span = max(zmax - zmin, 1e-9)
    for step in NICE_STEPS:
        if span / step <= 25:
            return step
    return NICE_STEPS[-1]


def levels(zmin: float, zmax: float, interval: float) -> List[float]:
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("等高距必须大于 0")
    first = math.ceil(zmin / interval - 1e-9)
    last = math.floor(zmax / interval + 1e-9)
    if last - first + 1 > MAX_LEVELS:
        raise ValueError(f"等高距 {interval:g} m 过小：需要 {last - first + 1} 条等高线，请加大等高距")
    return [round(k * interval, 6) for k in range(first, last + 1)]


def is_major(level: float, interval: float) -> bool:
    ratio = level / (interval * MAJOR_EVERY)
    return abs(ratio - round(ratio)) < 1e-6


def _trace(calculator: TINEarthworkCalculator, key: str, interval: float) -> List[Contour]:
    values = [getattr(p, key) for tri in calculator.triangles if not tri.is_boundary for p in tri.vertex_points]
    if not values:
        return []
    contours = []
    for level in levels(min(values), max(values), interval):
        for polyline in chain_segments(calculator.level_segments(key, level)):
            if len(polyline) >= 2:
                contours.append(Contour(level, is_major(level, interval), polyline))
    return contours


def trace_contours(calculator: TINEarthworkCalculator, interval: Optional[float] = None,
                   design: bool = True) -> ContourSet:
    """在已计算的三角网上追踪地面（及设计面）等高线。interval 为空时自动选取。"""
    if not calculator.triangles:
        raise ValueError("请先计算")
    if interval is None:
        zs = [p.z for tri in calculator.triangles if not tri.is_boundary for p in tri.vertex_points]
        if not zs:
            return ContourSet(1.0)
        interval = suggest_interval(min(zs), max(zs))
    result = ContourSet(float(interval), natural=_trace(calculator, "z", interval))
    if design:
        result.design = _trace(calculator, "design_z", interval)
    return result

