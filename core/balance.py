"""
土方平衡：松散/压实系数换算、挖填平衡的设计标高、最小二乘最优斜面。

可松性系数按《土方与爆破工程施工及验收规范》的习惯：
  最初可松性系数 Ks  = 挖出后的松散体积 / 自然状态体积
  最终可松性系数 Ks' = 回填压实后的体积 / 自然状态体积
挖方按自然方计，填方按压实方计；填方需要的自然方 = 填方 / Ks'。
"""
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.surface import PlaneDesign

# 设计标高的求解精度 (m)
ELEVATION_TOLERANCE = 1e-5
# 斜面坡度的保留位数（%），求出后按此取整，再按取整后的坡度求平衡高程
SLOPE_DECIMALS = 4


@dataclass
class SoilFactors:
    """可松性系数。默认 1.0 表示不换算。"""
    loose: float = 1.0     # 最初可松性系数 Ks
    compact: float = 1.0   # 最终可松性系数 Ks'

    def validate(self) -> "SoilFactors":
        for value, name in ((self.loose, "最初可松性系数 Ks"), (self.compact, "最终可松性系数 Ks'")):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}必须是大于 0 的数字")
            if value > 3:
                raise ValueError(f"{name}一般在 1.0～1.5 之间，{value:g} 明显偏大，请核对")
        return self

    @property
    def is_default(self) -> bool:
        return self.loose == 1.0 and self.compact == 1.0

    def describe(self) -> str:
        return f"Ks = {self.loose:g}，Ks' = {self.compact:g}"

    def to_dict(self):
        return {"loose": self.loose, "compact": self.compact}

    @classmethod
    def from_dict(cls, data) -> "SoilFactors":
        data = data or {}
        return cls(float(data.get("loose", 1.0)), float(data.get("compact", 1.0)))


@dataclass
class SoilBalance:
    """场内土方平衡：挖方（自然方）回填后余多少、缺多少。"""
    cut: float
    fill: float
    factors: SoilFactors

    @property
    def cut_loose(self) -> float:
        """挖方的松方量（装车运输量）。"""
        return self.cut * self.factors.loose

    @property
    def fill_natural(self) -> float:
        """填方需要的自然方。"""
        return self.fill / self.factors.compact

    @property
    def surplus(self) -> float:
        """余土（自然方），负值为缺土。"""
        return self.cut - self.fill_natural

    @property
    def export_loose(self) -> float:
        """余土外运的松方量。"""
        return max(self.surplus, 0.0) * self.factors.loose

    @property
    def borrow_natural(self) -> float:
        """缺土时需外借的自然方。"""
        return max(-self.surplus, 0.0)

    def rows(self) -> List[List]:
        """汇总表各行：项目、数值、单位、说明。"""
        rows = [
            ['可松性系数', self.factors.describe(), '', '挖方按自然方、填方按压实方计'],
            ['挖方松方量', round(self.cut_loose, 3), 'm³', '挖方 × Ks，装车运输量'],
            ['填方所需自然方', round(self.fill_natural, 3), 'm³', "填方 ÷ Ks'"],
        ]
        if self.surplus >= 0:
            rows += [
                ['余土（自然方）', round(self.surplus, 3), 'm³', '挖方回填后剩余'],
                ['余土外运（松方）', round(self.export_loose, 3), 'm³', '余土 × Ks'],
            ]
        else:
            rows.append(['缺土外借（自然方）', round(self.borrow_natural, 3), 'm³', '挖方不够回填，需外借'])
        return rows


def soil_balance(cut: float, fill: float, factors: Optional[SoilFactors] = None) -> SoilBalance:
    return SoilBalance(float(cut), float(fill), (factors or SoilFactors()).validate())


@dataclass
class BalancedDesign:
    """挖填平衡的设计面及其方量。"""
    elevation: Optional[float]          # 统一设计高程；斜面时为 None
    plane: Optional[PlaneDesign]        # 斜面；统一高程时为 None
    cut: float
    fill: float
    balance: SoilBalance


def _design_copy(calculator: TINEarthworkCalculator) -> TINEarthworkCalculator:
    """复制测点、特征线、边界、挖空区和三角网清理设置，不影响界面上的计算器。"""
    if calculator.compare_points:
        raise ValueError("两期对比没有设计面，不能求平衡标高")
    if len(calculator.points) < 3:
        raise ValueError("至少需要 3 个测量点")
    copy = TINEarthworkCalculator()
    copy.add_points([SurveyPoint(p.id, p.x, p.y, p.z) for p in calculator.points])
    copy.set_breaklines(calculator.breaklines)
    copy.set_boundary(list(calculator.boundary_polygon))
    copy.set_holes(calculator.holes)
    copy.set_max_edge_length(calculator.max_edge_length)
    copy.build_tin()
    return copy


def _solve(copy: TINEarthworkCalculator, apply, low: float, high: float,
           factors: SoilFactors) -> Tuple[float, float, float]:
    """求设计面整体高低 h，使 挖方 = 填方 / Ks'。apply(h) 把设计面设到 copy 上。"""
    def gap(h: float) -> float:
        apply(h)
        result = copy.calculate_volumes()
        if result.computed_area <= 0:
            raise ValueError("计算范围内没有测点覆盖，无法求平衡标高")
        return result.total_cut - result.total_fill / factors.compact

    low_gap, high_gap = gap(low), gap(high)
    if low_gap < 0 or high_gap > 0:
        raise ValueError("无法求得挖填平衡的设计高程，请检查测点高程")
    h = low if low_gap == 0 else high if high_gap == 0 else brentq(gap, low, high, xtol=ELEVATION_TOLERANCE)
    apply(h)
    result = copy.calculate_volumes()
    return h, result.total_cut, result.total_fill


def balance_elevation(calculator: TINEarthworkCalculator, factors: Optional[SoilFactors] = None) -> BalancedDesign:
    """挖填平衡的统一设计高程（考虑可松性系数时，挖方自然方恰好够回填）。"""
    factors = (factors or SoilFactors()).validate()
    copy = _design_copy(calculator)
    zs = [p.z for p in copy.points + copy.breakline_points]
    h, cut, fill = _solve(copy, copy.set_design_elevation, min(zs) - 1.0, max(zs) + 1.0, factors)
    return BalancedDesign(h, None, cut, fill, soil_balance(cut, fill, factors))


def balance_plane(calculator: TINEarthworkCalculator, plane: PlaneDesign,
                  factors: Optional[SoilFactors] = None) -> BalancedDesign:
    """保持斜面的基准点和坡度不变，求挖填平衡的基准高程 H0。"""
    factors = (factors or SoilFactors()).validate()
    copy = _design_copy(calculator)
    offsets = [p.z - (plane.elevation(p.x, p.y) - plane.h0)
               for p in copy.points + copy.breakline_points]

    def apply(h: float):
        copy.set_design_plane(PlaneDesign(plane.x0, plane.y0, h, plane.slope_x, plane.slope_y))

    h, cut, fill = _solve(copy, apply, min(offsets) - 1.0, max(offsets) + 1.0, factors)
    solved = PlaneDesign(plane.x0, plane.y0, h, plane.slope_x, plane.slope_y)
    return BalancedDesign(None, solved, cut, fill, soil_balance(cut, fill, factors))


# 二次多项式在三角形上精确积分：三边中点，各取 1/3 面积
def _moments(pieces) -> np.ndarray:
    """计算范围上 ∫[1, x, y, z, x², xy, y², xz, yz] dA（局部坐标）。"""
    total = np.zeros(9)
    for polygon, heights, sign in pieces:
        if len(polygon) < 3:
            continue
        x0, y0, z0 = polygon[0, 0], polygon[0, 1], heights[0]
        for i in range(1, len(polygon) - 1):
            (x1, y1), (x2, y2) = polygon[i], polygon[i + 1]
            z1, z2 = heights[i], heights[i + 1]
            area = ((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) / 2
            if area == 0:
                continue
            mids = np.array([
                ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
                ((x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2),
                ((x2 + x0) / 2, (y2 + y0) / 2, (z2 + z0) / 2),
            ])
            x, y, z = mids[:, 0], mids[:, 1], mids[:, 2]
            values = np.array([np.ones(3), x, y, z, x * x, x * y, y * y, x * z, y * z]).sum(axis=1)
            total += sign * area / 3 * values
    # 扇形剖分按多边形方向带符号；逆时针为正，统一取正
    return total if total[0] >= 0 else -total


def best_fit_plane(calculator: TINEarthworkCalculator, factors: Optional[SoilFactors] = None) -> BalancedDesign:
    """最小二乘最优斜面：使 ∫(地面 − 设计面)² 最小（挖填总量最小的平面），再按可松性系数调到挖填平衡。

    基准点取计算范围的形心。
    """
    factors = (factors or SoilFactors()).validate()
    copy = _design_copy(calculator)
    m = _moments(copy.computed_pieces())
    area, sx, sy, sz, sxx, sxy, syy, sxz, syz = m
    if area <= 0:
        raise ValueError("计算范围内没有测点覆盖，无法求最优斜面")
    xc, yc = sx / area, sy / area
    # 以形心为原点的中心矩，数值更稳定
    cxx = sxx - sx * xc
    cxy = sxy - sx * yc
    cyy = syy - sy * yc
    cxz = sxz - sx * sz / area
    cyz = syz - sy * sz / area
    matrix = np.array([[cxx, cxy], [cxy, cyy]])
    if abs(np.linalg.det(matrix)) <= 1e-12 * max(cxx * cyy, 1e-30):
        raise ValueError("计算范围过于狭长，无法确定最优斜面的坡度")
    a, b = np.linalg.solve(matrix, [cxz, cyz])
    ox, oy = copy.origin
    plane = PlaneDesign(
        x0=round(float(xc + ox), 3), y0=round(float(yc + oy), 3), h0=float(sz / area),
        slope_x=round(float(a) * 100, SLOPE_DECIMALS), slope_y=round(float(b) * 100, SLOPE_DECIMALS),
    )
    return balance_plane(calculator, plane, factors)
