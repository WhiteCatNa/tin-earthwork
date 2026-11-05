"""
变化显著性：逐处判断高差（实测 − 设计，两期对比为前期 − 后期）是真实的挖填，还是测量误差与内插误差。

每期测量在 p 处的误差尺度 s(p) = √(σm² + e(p)²)，e(p) 为该期三角网的内插误差尺度
（core.uncertainty.error_map：留一交叉验证估计的局部弯曲程度 κ × 所在三角形的 Σℓ²/12）。

内插误差是重尾的（山顶、沟底处远大于平均），按正态分布取 1.96 倍会严重偏窄（仿真中误判率 17%）。
所以检测限的倍数用“分裂共形预测”标定：随机留出 10% 的测点（重复 3 次），用其余测点构网内插留出点，
取 |残差| / s 的 95% 分位数 q。只要留出点与场内各处可交换，|误差| ≤ q·s 的概率就不低于 95%。
检测限 LoD(p) = √(Σ_每期 (q·s(p))²)；设计面（统一高程、斜面、设计三角网）没有误差。
|高差| > LoD 的地方判为显著挖方或显著填方，其余为“不显著”（变化可能只是误差）。

与地貌学“DEM 差值 + 检测限”（DoD/LoD）的做法相同，但检测限随地面起伏和点距逐处变化，
而不是全场一个只含测量误差的常数：起伏大、点稀的地方内插误差大，常数检测限会把这些地方的
内插误差误判成挖填。
"""
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from core.calculator import CalculationResult, TINEarthworkCalculator

Z95 = 1.959964
TARGET_CELLS = 40000
HOLDOUT = 0.1          # 共形标定时留出的测点比例
HOLDOUT_REPEATS = 3
MIN_HOLDOUT = 30       # 留出残差少于此数时不标定，按正态分布取倍数


@dataclass
class SignificanceResult:
    confidence: float
    cell: float
    origin: tuple                   # 格网左下角（全局坐标）
    dz: np.ndarray                  # [ny, nx] 高差，范围外为 NaN
    lod: np.ndarray                 # [ny, nx] 检测限
    classes: np.ndarray             # [ny, nx] 1 显著挖方、−1 显著填方、0 不显著、NaN 范围外
    significant_cut: float = 0.0    # 显著挖方区内的挖方 (m³)
    significant_fill: float = 0.0
    uncertain_cut: float = 0.0      # 不显著区内的挖方 (m³)
    uncertain_fill: float = 0.0
    uncertain_area: float = 0.0     # 不显著区面积 (m²)
    area: float = 0.0               # 计入的总面积 (m²)
    fixed_lod: float = 0.0          # 只计测量误差的常数检测限 (m)，供对照
    fixed_significant_area: float = 0.0   # 按常数检测限判为显著的面积
    factors: Optional[list] = None  # 各期共形标定的倍数 q（None 为测点太少，按正态分布）

    @property
    def uncertain_ratio(self) -> float:
        return self.uncertain_area / self.area if self.area > 0 else 0.0

    def summary_text(self) -> str:
        return (f"显著挖方 {self.significant_cut:,.1f} m³、显著填方 {self.significant_fill:,.1f} m³；"
                f"{self.uncertain_ratio:.0%} 的面积高差不显著（其中挖 {self.uncertain_cut:,.1f}、填 {self.uncertain_fill:,.1f} m³）")

    def rows(self):
        return [
            ("显著挖方", self.significant_cut, "m³", f"|高差| > 检测限（{self.confidence:.0%}）的挖方"),
            ("显著填方", self.significant_fill, "m³", "|高差| > 检测限的填方"),
            ("不显著挖方", self.uncertain_cut, "m³", "高差小于检测限，可能只是测量与内插误差"),
            ("不显著填方", self.uncertain_fill, "m³", ""),
            ("不显著面积", self.uncertain_area, "m²", f"占 {self.uncertain_ratio:.1%}"),
        ]


def _nice(value: float) -> float:
    exponent = math.floor(math.log10(value))
    for nice in (1, 2, 2.5, 5, 10):
        if nice * 10 ** exponent >= value:
            return float(nice * 10 ** exponent)
    return float(10 ** (exponent + 1))


def conformal_factor(points, sigma_m: float, confidence: float, rng: np.random.Generator) -> Optional[float]:
    """分裂共形预测：|留出点残差| / s 的 confidence 分位数（有限样本修正）。测点太少时返回 None。"""
    from scipy.interpolate import LinearNDInterpolator

    from core.uncertainty import _hull_mask, error_map

    xy = np.array([(p.x, p.y) for p in points], dtype=float)
    z = np.array([p.z for p in points], dtype=float)
    interior = np.nonzero(~_hull_mask(points))[0]
    count = int(len(interior) * HOLDOUT)
    if count * HOLDOUT_REPEATS < MIN_HOLDOUT:
        return None
    scores = []
    for _ in range(HOLDOUT_REPEATS):
        hold = np.zeros(len(xy), dtype=bool)
        hold[rng.choice(interior, count, replace=False)] = True
        kept = [p for p, h in zip(points, hold) if not h]
        emap = error_map(kept, sigma_m, rng)
        if emap is None:
            return None
        center = xy[~hold].mean(axis=0)
        residual = LinearNDInterpolator(xy[~hold] - center, z[~hold])(xy[hold] - center) - z[hold]
        scale = np.sqrt(np.nan_to_num(emap.at(xy[hold])) ** 2 + sigma_m ** 2)
        ok = ~np.isnan(residual) & (scale > 0)
        scores.append(np.abs(residual[ok]) / scale[ok])
    scores = np.sort(np.concatenate(scores))
    if len(scores) < MIN_HOLDOUT:
        return None
    rank = min(int(math.ceil((len(scores) + 1) * confidence)) - 1, len(scores) - 1)
    return float(scores[rank])


def detect_changes(calculator: TINEarthworkCalculator, result: CalculationResult, uncertainty,
                   confidence: float = 0.95, cell: Optional[float] = None, seed: int = 0) -> SignificanceResult:
    """uncertainty 为 evaluate_uncertainty 的结果（提供测量中误差与各期内插误差分布）。"""
    from scipy.stats import norm

    from core.uncertainty import _inside

    if not 0.5 < confidence < 1:
        raise ValueError("置信水平应在 50%~100% 之间")
    if result.computed_area <= 0:
        raise ValueError("计算面积为 0")
    z = float(norm.ppf(0.5 + confidence / 2))
    cell = float(cell) if cell else _nice(math.sqrt(result.computed_area / TARGET_CELLS))
    ring = np.asarray(calculator.boundary_polygon or calculator.coverage_polygon(), dtype=float)
    low, high = ring.min(axis=0), ring.max(axis=0)
    nx = max(int(math.ceil((high[0] - low[0]) / cell)), 1)
    ny = max(int(math.ceil((high[1] - low[1]) / cell)), 1)
    gx, gy = np.meshgrid(low[0] + (np.arange(nx) + 0.5) * cell, low[1] + (np.arange(ny) + 0.5) * cell)
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    natural, compare = calculator.sample_surfaces(xy)
    dz = natural - compare
    dz[~_inside(calculator, xy)] = np.nan

    sigma_m = uncertainty.sigma_measure
    period = result.is_compare and not result.is_design_tin
    rng = np.random.default_rng(seed)
    surveys = [(calculator.points, uncertainty.natural_map)]
    if period:
        surveys.append((calculator.compare_points, uncertainty.compare_map))
    variance = np.zeros(len(xy))
    factors = []
    for points, emap in surveys:
        factor = conformal_factor(points, sigma_m, confidence, rng)
        factors.append(factor)
        scale2 = sigma_m ** 2 + (np.nan_to_num(emap.at(xy)) ** 2 if emap is not None else 0.0)
        variance += (factor if factor is not None else z) ** 2 * scale2
    lod = np.sqrt(variance)
    lod[np.isnan(dz)] = np.nan
    classes = np.where(np.isnan(dz), np.nan, np.where(dz > lod, 1.0, np.where(dz < -lod, -1.0, 0.0)))

    a = cell * cell
    valid = ~np.isnan(dz)
    out = SignificanceResult(confidence, cell, (float(low[0]), float(low[1])), dz.reshape(ny, nx),
                             lod.reshape(ny, nx), classes.reshape(ny, nx))
    out.factors = factors
    d = np.nan_to_num(dz)
    out.significant_cut = float(d[classes == 1].sum() * a)
    out.significant_fill = float(-d[classes == -1].sum() * a)
    uncertain = classes == 0
    out.uncertain_cut = float(np.clip(d[uncertain], 0, None).sum() * a)
    out.uncertain_fill = float(-np.clip(d[uncertain], None, 0).sum() * a)
    out.uncertain_area = float(uncertain.sum() * a)
    out.area = float(valid.sum() * a)
    out.fixed_lod = z * sigma_m * math.sqrt(2 if period else 1)
    out.fixed_significant_area = float((np.abs(d[valid]) > out.fixed_lod).sum() * a)
    return out
