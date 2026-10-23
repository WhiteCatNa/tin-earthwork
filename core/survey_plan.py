"""
按精度目标规划补测：要求挖方、填方的 95% 区间半宽不超过方量的 τ（如 ±2%），估计还需补测多少点，
给出第一批补测位置；补测后重新评定，不达标就再规划下一批（测 → 评 → 补 → 达标）。

补测 k 个点后的区间半宽用两条曲线夹住：
- 乐观：按补测点推荐的内插误差指标 Σκ·g 剩余比例 r(k) 缩小，σ内插(k) = σ内插 · r(k)。
  推荐点放在误差最大的地方，指标下降很快，但实际偏差下降得没有这么快（仿真中约为指标的一半到同量级）。
- 保守：按整体加密估计。线性内插偏差与边长平方成正比、即与点数成反比，σ内插(k) = σ内插 · n/(n + k)。
  这相当于随机补点，推荐补点通常好于它。
测量误差部分两条曲线都按 σ测量 · √(n/(n + k)) 缩小（Σw² 与点数成反比）。

第一批按乐观估计的点数补测（不会多测），再评定；保守估计给出最多还要测多少点。
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from core.uncertainty import Z95, Recommendation, UncertaintyResult

MAX_CONSERVATIVE_FACTOR = 50     # 保守估计最多算到现有点数的 50 倍
ITEMS = {"cut_fill": (0, 1), "cut": (0,), "fill": (1,), "net": (2,)}
ITEM_LABELS = {"cut_fill": "挖方和填方", "cut": "挖方", "fill": "填方", "net": "挖填差值"}


@dataclass
class SurveyPlan:
    target: float                      # 相对半宽目标（0.02 表示 ±2%）
    item: str
    point_count: int                   # 现有测点数
    current: float                     # 当前最大相对半宽
    optimistic: Optional[int]          # 乐观估计还需补测的点数；None 表示推荐点数不够、估不出来
    conservative: Optional[int]        # 保守估计；None 表示测量误差本身已超过目标，加密也达不到
    batch: List[Recommendation] = field(default_factory=list)   # 第一批补测点
    curve: List[Tuple[int, float, float]] = field(default_factory=list)   # (补测点数, 乐观半宽, 保守半宽)
    warnings: List[str] = field(default_factory=list)

    @property
    def reached(self) -> bool:
        return self.current <= self.target

    def summary_text(self) -> str:
        label = ITEM_LABELS[self.item]
        if self.reached:
            return f"{label}的 95% 区间半宽 ±{self.current:.1%}，已达到 ±{self.target:.1%} 的要求，不需要补测"
        if self.conservative is None:
            return (f"{label}目前 ±{self.current:.1%}，按保守估计补测到现有点数的 {MAX_CONSERVATIVE_FACTOR} 倍也达不到 "
                    f"±{self.target:.1%}，请放宽目标（方量很小的项目相对精度难以提高）或提高测量精度")
        if not self.batch:
            return (f"{label}目前 ±{self.current:.1%}，要达到 ±{self.target:.1%}：按全场均匀加密估计需补测约 "
                    f"{self.conservative} 点（没有可推荐的位置，区间主要来自测量误差或整体偏差），补测后重新评定")
        need = (f"{len(self.batch)} 点以上（保守估计 {self.conservative} 点）" if self.optimistic is None
                else f"{self.optimistic} ~ {self.conservative} 点")
        return (f"{label}目前 ±{self.current:.1%}，要达到 ±{self.target:.1%}：预计需补测 {need}。"
                f"先按推荐位置补测第一批 {len(self.batch)} 点，导入后重新评定")


def _half_widths(u: UncertaintyResult, indices, interp_factor: np.ndarray, measure_factor: np.ndarray) -> np.ndarray:
    """各补测点数下所选项目的最大相对半宽。"""
    worst = np.zeros(len(interp_factor))
    for i in indices:
        value = abs(u.estimate[i])
        if value <= 1e-9:
            continue
        sigma = np.sqrt((u.interp_sigma[i] * interp_factor) ** 2 + (u.measure_sigma[i] * measure_factor) ** 2)
        worst = np.maximum(worst, Z95 * sigma / value)
    return worst


def plan_survey(u: UncertaintyResult, point_count: int, target: float, item: str = "cut_fill") -> SurveyPlan:
    """u 为 evaluate_uncertainty 的结果（recommend 宜取得大一些，如 1000），point_count 为现有测点数。"""
    if not math.isfinite(target) or not 0 < target < 1:
        raise ValueError("精度目标应在 0~100% 之间（不含 0）")
    if item not in ITEMS:
        raise ValueError(f"未知的项目：{item}")
    if point_count < 3:
        raise ValueError("测点不足 3 个")
    indices = ITEMS[item]
    n = float(point_count)
    current = float(_half_widths(u, indices, np.ones(1), np.ones(1))[0])
    plan = SurveyPlan(target, item, point_count, current, 0, 0)
    if plan.reached:
        return plan
    if all(abs(u.estimate[i]) <= 1e-9 for i in indices):
        plan.optimistic = plan.conservative = None
        plan.warnings.append(f"{ITEM_LABELS[item]}为 0，无法按相对精度规划")
        return plan

    # 测量误差的下限：点数无穷多时内插误差为 0，测量误差也趋于 0；但每个点的测量误差不变，
    # 实际中补测点数有限，按保守曲线最多算到 50 倍点数
    ks = np.unique(np.r_[0, np.round(np.geomspace(1, n * MAX_CONSERVATIVE_FACTOR, 400))]).astype(int)
    conservative = _half_widths(u, indices, n / (n + ks), np.sqrt(n / (n + ks)))
    hit = np.nonzero(conservative <= target)[0]
    plan.conservative = int(ks[hit[0]]) if len(hit) else None

    remaining = np.r_[1.0, [r.remaining for r in u.recommendations]]
    k_rec = np.arange(len(remaining))
    optimistic = _half_widths(u, indices, remaining, np.sqrt(n / (n + k_rec)))
    hit = np.nonzero(optimistic <= target)[0]
    plan.optimistic = int(k_rec[hit[0]]) if len(hit) else None
    if plan.conservative is None:
        return plan
    if not u.recommendations:
        plan.optimistic = None
        return plan
    if plan.optimistic is None:
        plan.warnings.append(f"推荐的 {len(u.recommendations)} 个补测点全部补完仍未达到目标，可先补完这些点再评定")
    batch = plan.optimistic if plan.optimistic is not None else len(u.recommendations)
    if plan.conservative is not None:
        batch = min(batch, plan.conservative)
    plan.batch = list(u.recommendations[:max(batch, 1)])
    sample = np.unique(np.r_[0, np.round(np.linspace(0, len(remaining) - 1, 40))]).astype(int)
    plan.curve = [(int(k), float(optimistic[k]), float(_half_widths(u, indices, np.array([n / (n + k)]),
                                                                    np.array([math.sqrt(n / (n + k))]))[0]))
                  for k in sample]
    return plan
