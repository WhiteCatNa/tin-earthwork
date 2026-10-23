"""
设计标高与土方调配联合优化：在坡度范围内找一个斜面设计面 H = H0 + ix·(x − X0) + iy·(y − Y0)，
使总费用最小：

  费用 = 挖方单价 × 挖方 + 填方单价 × 填方 + 运输单价 × 场内运输量（m³·km）
       + 外运单价 × 余土 + 借土单价 × 借土

场内运输量来自土方调配（core.haul：按方格分区，运输问题线性规划），所以设计面的高低、坡向直接影响
运距和余缺。常规做法只求“挖填平衡”的标高或最小二乘斜面，再另行调配；这里把两者放在一起优化。

费用对 (H0, ix, iy) 不光滑（调配方案随设计面跳变），用带边界的 Powell 法从几个起点搜索，
起点取两个对照方案（挖填平衡的水平面、最小二乘最优斜面截到坡度范围内再调平衡）中费用较低且满足坡度范围的一个。

仿真（200 m 场地、默认单价）中，费用最小斜面比挖填平衡水平面省 25%~54%，比最小二乘斜面只省 0~1%：
单价以挖填方量为主时，最小二乘斜面已接近最优；运距单价高、坡度受排水要求限制、或外运/借土便宜时差别才明显。
"""
import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import numpy as np

from core.balance import SoilFactors, _design_copy, balance_elevation, best_fit_plane, balance_plane
from core.calculator import TINEarthworkCalculator
from core.haul import HaulResult, run_haul, suggest_zone_size
from core.surface import PlaneDesign

MAX_EVALUATIONS = 160


@dataclass
class CostRates:
    """单价（元）：挖、填按 m³，运输按 m³·km，外运、借土按 m³（自然方）。"""
    cut: float = 8.0
    fill: float = 10.0
    haul: float = 1.5
    waste: float = 30.0
    borrow: float = 40.0

    def validate(self) -> "CostRates":
        for name, value in (("挖方单价", self.cut), ("填方单价", self.fill), ("运输单价", self.haul),
                            ("外运单价", self.waste), ("借土单价", self.borrow)):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name}不能为负数")
        return self

    def to_dict(self):
        return {"cut": self.cut, "fill": self.fill, "haul": self.haul, "waste": self.waste, "borrow": self.borrow}


@dataclass
class GradeOption:
    label: str
    plane: PlaneDesign
    cut: float
    fill: float
    work: float            # 场内运输量 (m³·m)
    waste: float
    borrow: float
    cost: float

    def cost_parts(self, rates: CostRates) -> List[Tuple[str, float]]:
        return [("挖方", rates.cut * self.cut), ("填方", rates.fill * self.fill),
                ("场内运输", rates.haul * self.work / 1000), ("余土外运", rates.waste * self.waste),
                ("借土", rates.borrow * self.borrow)]


@dataclass
class GradeResult:
    rates: CostRates
    slope_x: Tuple[float, float]
    slope_y: Tuple[float, float]
    zone_size: float
    best: GradeOption
    baselines: List[GradeOption] = field(default_factory=list)
    evaluations: int = 0

    @property
    def saving(self) -> float:
        """与基准方案中最便宜者相比节省的费用。"""
        return min(b.cost for b in self.baselines) - self.best.cost if self.baselines else 0.0

    def rows(self) -> List[list]:
        out = []
        for option in [self.best] + self.baselines:
            p = option.plane
            out.append([option.label, round(p.h0, 3), round(p.slope_x, 3), round(p.slope_y, 3), round(option.cut, 1),
                        round(option.fill, 1), round(option.work / 1000, 1), round(option.waste, 1),
                        round(option.borrow, 1), round(option.cost, 0)])
        return out


HEADERS = ["方案", "基准高程 H0", "X 坡度 %", "Y 坡度 %", "挖方 m³", "填方 m³", "运输量 m³·km", "余土 m³", "借土 m³",
           "费用 元"]


def optimize_grade(calculator: TINEarthworkCalculator, rates: Optional[CostRates] = None,
                   slope_x: Tuple[float, float] = (-2.0, 2.0), slope_y: Tuple[float, float] = (-2.0, 2.0),
                   zone_size: Optional[float] = None, factors: Optional[SoilFactors] = None,
                   progress: Optional[Callable[[int], None]] = None) -> GradeResult:
    """calculator 须为与设计面比较的模式（不支持两期对比、设计三角网）。坡度范围单位 %。"""
    from scipy.optimize import minimize

    rates = (rates or CostRates()).validate()
    factors = (factors or SoilFactors()).validate()
    for name, (low, high) in (("X 坡度", slope_x), ("Y 坡度", slope_y)):
        if not (math.isfinite(low) and math.isfinite(high)) or low > high:
            raise ValueError(f"{name}范围无效：下限应不大于上限")
        if max(abs(low), abs(high)) > 50:
            raise ValueError(f"{name}范围超过 ±50%，请核对")
    copy = _design_copy(calculator)
    zone_size = float(zone_size) if zone_size else suggest_zone_size(copy)
    balanced_flat = balance_elevation(calculator, factors)
    fitted = best_fit_plane(calculator, factors).plane
    x0, y0 = fitted.x0, fitted.y0
    clipped = PlaneDesign(x0, y0, fitted.h0, float(np.clip(fitted.slope_x, *slope_x)),
                          float(np.clip(fitted.slope_y, *slope_y)))
    clipped = balance_plane(calculator, clipped, factors).plane
    zs = np.array([p.z for p in copy.points])
    span = max(float(zs.max() - zs.min()), 1.0)
    count = [0]

    def evaluate(h0: float, sx: float, sy: float) -> Tuple[float, HaulResult]:
        copy.set_design_plane(PlaneDesign(x0, y0, h0, sx, sy))
        haul = run_haul(copy, zone_size, factors.compact)
        cut = sum(z.cut for z in haul.zones)
        fill = sum(z.fill for z in haul.zones)
        cost = (rates.cut * cut + rates.fill * fill + rates.haul * haul.work / 1000 + rates.waste * haul.waste
                + rates.borrow * haul.borrow)
        count[0] += 1
        if progress is not None:
            progress(count[0])
        return cost, haul

    def option(label: str, plane: PlaneDesign) -> GradeOption:
        cost, haul = evaluate(plane.h0, plane.slope_x, plane.slope_y)
        return GradeOption(label, PlaneDesign(x0, y0, plane.h0, plane.slope_x, plane.slope_y),
                           sum(z.cut for z in haul.zones), sum(z.fill for z in haul.zones), haul.work,
                           haul.waste, haul.borrow, cost)

    # 水平面的基准点换到同一个 (X0, Y0)，高程不变
    flat = PlaneDesign(x0, y0, balanced_flat.elevation, 0.0, 0.0)
    flat_ok = slope_x[0] <= 0.0 <= slope_x[1] and slope_y[0] <= 0.0 <= slope_y[1]
    baselines = [option("挖填平衡水平面" if flat_ok else "挖填平衡水平面（超出坡度范围，仅供对照）", flat),
                 option("最小二乘斜面（挖填平衡）", clipped)]
    feasible = [b for b, ok in zip(baselines, (flat_ok, True)) if ok]
    start = min(feasible, key=lambda b: b.cost)

    bounds = [(float(zs.min()) - span, float(zs.max()) + span), tuple(slope_x), tuple(slope_y)]
    found = minimize(lambda v: evaluate(*v)[0], np.array([start.plane.h0, start.plane.slope_x, start.plane.slope_y]),
                     method="Powell", bounds=bounds,
                     options={"maxfev": MAX_EVALUATIONS, "xtol": 1e-3, "ftol": 1e-6})
    best = option("费用最小斜面", PlaneDesign(x0, y0, float(found.x[0]), float(found.x[1]), float(found.x[2])))
    if start.cost < best.cost:        # 优化结果不应比可行的起点差
        best = GradeOption("费用最小斜面", start.plane, start.cut, start.fill, start.work, start.waste, start.borrow,
                           start.cost)
    return GradeResult(rates, tuple(slope_x), tuple(slope_y), zone_size, best, baselines, count[0])


def summary_text(result: GradeResult) -> str:
    best = result.best
    reference = min(result.baselines, key=lambda b: b.cost) if result.baselines else None
    text = (f"费用最小斜面：H0 = {best.plane.h0:.3f} m，X 向 {best.plane.slope_x:+.3f}%，Y 向 {best.plane.slope_y:+.3f}%，"
            f"总费用 {best.cost:,.0f} 元")
    if reference is not None and reference.cost > 0:
        text += f"，比“{reference.label}”省 {result.saving:,.0f} 元（{result.saving / reference.cost:.1%}）"
    return text
