"""
土方量不确定度评定与补测点推荐。

TIN 法方量的误差有两部分：
1. 内插误差：三角形内用平面代替真实地面。它不是纯随机的——山包被“削平”、沟谷被“填高”，
   挖方、填方都系统性偏小，测点越稀偏得越多。
2. 测量误差：每个测点高程的随机误差 σm（及可选的系统误差 σb）。方量对测点高程是线性的，
   Var = σm²·Σwi² + σb²·A²，wi 为测点 i 分担的面积。

内插误差用“抽稀外推”（Richardson 外推）从测点自身估计，不需要额外数据：
   随机保留一半内部测点（凸包上的点全部保留，计算范围不变）重算方量 Vk。三角形边长 h 放大 √2 倍，
   线性内插的误差与 h² 成正比，偏差翻倍，于是 TIN 法方量的偏差估计为
       B = mean(Vk − V) / (1/f − 1)        （f 为保留比例，取 0.5）
   修正后估计 V − B；内插部分的不确定度取
       σ² = sd²·(1 + 1/K) + (c·B)²
   sd 为各次 (Vk − V)/(1/f − 1) 的标准差（随机抽稀造成的离散），c·B 计入“偏差估计本身不准”（c = 1）。
   测点接近规则格网时，抽稀后的点不再规则，偏差会被高估或低估，评定中会提示。

补测点推荐：线性内插在三角形内的平均误差约为 κ·Σℓ²/12（ℓ 为边长，κ 为当地地面弯曲程度，1/m），
三角形的方量误差尺度为 κ·g，g = A·Σℓ²/12。κ 由留一交叉验证（逐个去掉内部测点、用相邻点内插、看残差）
按邻域估计。在三角形形心补一点把它分成三个小三角形，g 变小；按“误差尺度减少最多”贪心选点，
被分出的小三角形继续参与竞选。
"""
import heapq
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from core.calculator import CalculationResult, SurveyPoint, TINEarthworkCalculator
from core.geometry import points_in_ring

XY = Tuple[float, float]
Z95 = 1.959964
FRACTION = 0.5            # 抽稀时保留的内部测点比例
REPEATS = 8               # 抽稀次数
BIAS_INFLATION = 1.0      # c：偏差估计本身的不确定度按 c·B 计
MIN_INTERIOR = 20         # 内部测点少于此数时不做抽稀外推
REGULAR_CV = 0.3          # 最近邻距离的变异系数低于此值视为接近规则格网
MAX_LOO_POINTS = 5000
SHRINK = 5.0              # 局部 κ 向全局值收缩的强度（相当于多少个“虚拟邻点”）
LABELS = ("挖方", "填方", "挖填差值")


@dataclass
class LooStats:
    count: int
    rmse: float               # 留一残差的均方根 (m)
    kappa: float              # 全局曲率尺度 κ (1/m)


@dataclass
class Recommendation:
    x: float
    y: float
    reduction: float          # 该点使内插误差指标 Σκ·g 减少 (m³)
    remaining: float          # 补测到这个点为止，内插误差指标剩余的比例


@dataclass
class UncertaintyResult:
    sigma_measure: float
    sigma_bias: float
    repeats: int = 0                                   # 实际完成的抽稀次数
    tin: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # TIN 法 挖方、填方、挖填差值
    bias: Tuple[float, float, float] = (0.0, 0.0, 0.0)   # 估计的 TIN 法偏差（TIN − 真值）
    sigma: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # 修正后估计的 1σ
    interp_sigma: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    measure_sigma: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    loo: Optional[LooStats] = None
    triangle_sigma: List[Tuple[List[XY], float]] = field(default_factory=list)   # 三角形平均内插误差尺度 (m)
    indicator: float = 0.0                              # 内插误差指标 Σκ·g (m³)
    recommendations: List[Recommendation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def estimate(self) -> Tuple[float, float, float]:
        return tuple(v - b for v, b in zip(self.tin, self.bias))

    def rows(self) -> List[Tuple[str, float, float, float, Optional[float]]]:
        """[(项目, TIN 法方量, 修正后估计, 95% 区间半宽, 半宽占估计值 %)]"""
        out = []
        for label, value, estimate, sigma in zip(LABELS, self.tin, self.estimate, self.sigma):
            half = Z95 * sigma
            out.append((label, value, estimate, half, half / abs(estimate) * 100 if abs(estimate) > 1e-9 else None))
        return out

    def summary_text(self) -> str:
        cut, fill, _ = self.rows()[:3]
        text = (f"不确定度：挖方 {cut[2]:,.1f} ± {cut[3]:,.1f} m³，填方 {fill[2]:,.1f} ± {fill[3]:,.1f} m³"
                f"（修正后估计 ± 95% 区间半宽）")
        if self.recommendations:
            text += f"；推荐补测 {len(self.recommendations)} 点，内插误差指标可降至 {self.recommendations[-1].remaining:.0%}"
        return text


# ---------------------------------------------------------------------------
# 抽稀外推
# ---------------------------------------------------------------------------

def _copy_points(points: Sequence[SurveyPoint]) -> List[SurveyPoint]:
    return [SurveyPoint(p.id, p.x, p.y, p.z, p.design_z, p.delta_z, p.has_design_z) for p in points]


def _hull_mask(points: Sequence[SurveyPoint]) -> np.ndarray:
    from scipy.spatial import ConvexHull, QhullError

    xy = np.array([(p.x, p.y) for p in points], dtype=float)
    mask = np.zeros(len(xy), dtype=bool)
    if len(xy) < 3:
        mask[:] = True
        return mask
    try:
        mask[ConvexHull(xy - xy.mean(axis=0)).vertices] = True
    except QhullError:
        mask[:] = True
    return mask


def _subsample(points: Sequence[SurveyPoint], hull: np.ndarray, fraction: float,
               rng: np.random.Generator) -> List[SurveyPoint]:
    interior = np.nonzero(~hull)[0]
    keep = rng.choice(interior, int(round(len(interior) * fraction)), replace=False)
    chosen = np.sort(np.concatenate([np.nonzero(hull)[0], keep]))
    return [points[i] for i in chosen.tolist()]


def _clone(calculator: TINEarthworkCalculator, points: Sequence[SurveyPoint],
           compare: Optional[Sequence[SurveyPoint]], fraction: float) -> TINEarthworkCalculator:
    """按 calculator 的设置（设计面、边界、挖空区、特征线）用另一组测点建一个计算器。"""
    calc = TINEarthworkCalculator(calculator.design_elevation)
    calc.design_plane = calculator.design_plane
    calc.design_overrides = dict(calculator.design_overrides)
    calc.add_points(_copy_points(points))
    if compare:
        calc.set_compare_points(_copy_points(compare), calculator.compare_kind)
    calc.set_boundary(list(calculator.boundary_polygon or []))
    calc.set_holes(calculator.holes, validate=False)
    if calculator.max_edge_length:
        # 抽稀后边长约放大 1/√f 倍，剔除阈值同步放大，否则凸包边缘会多剔掉三角形
        calc.set_max_edge_length(calculator.max_edge_length / math.sqrt(fraction))
    if hasattr(calculator, "set_breaklines"):
        calc.set_breaklines(getattr(calculator, "breaklines", None), getattr(calculator, "compare_breaklines", None))
    return calc


def _volumes(result: CalculationResult) -> np.ndarray:
    return np.array([result.total_cut, result.total_fill, result.net_volume], dtype=float)


def _nearest_cv(points: Sequence[SurveyPoint]) -> float:
    """最近邻距离的变异系数：随机布点约 0.5，规则格网接近 0。"""
    from scipy.spatial import cKDTree

    xy = np.array([(p.x, p.y) for p in points], dtype=float)
    if len(xy) < 10:
        return 1.0
    distance, _ = cKDTree(xy).query(xy, k=2)
    nearest = distance[:, 1]
    mean = float(nearest.mean())
    return float(nearest.std() / mean) if mean > 0 else 0.0


# ---------------------------------------------------------------------------
# 测量误差
# ---------------------------------------------------------------------------

def _area_weights2(points: Sequence[SurveyPoint], region_area: float) -> float:
    """Σwi²：wi 为测点分担的面积（含该点的三角形面积 / 3），按实际计算面积缩放。"""
    from scipy.spatial import Delaunay, QhullError

    xy = np.array([(p.x, p.y) for p in points], dtype=float)
    if len(xy) < 3:
        return region_area ** 2
    try:
        tri = Delaunay(xy - xy.mean(axis=0))
    except QhullError:
        return region_area ** 2
    corners = tri.points[tri.simplices]
    u, v = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    area = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]) / 2
    total = float(area.sum())
    if total <= 0:
        return region_area ** 2
    weights = np.bincount(tri.simplices.ravel(), weights=np.repeat(area / 3, 3), minlength=len(xy))
    return float((weights ** 2).sum()) * (region_area / total) ** 2


# ---------------------------------------------------------------------------
# 留一交叉验证与局部曲率（补测点推荐、误差分布图）
# ---------------------------------------------------------------------------

def _loo(xy: np.ndarray, z: np.ndarray, rng: np.random.Generator):
    """返回 (点下标, 残差 r, q, Σλ²)，只含内部测点（凸包上的点去掉后是外推，不参与）。

    r 为去掉该点后由相邻点内插的残差，q = Σλi|p − vi|²（λ 为重心坐标），对二阶光滑地面 r ≈ κ·ξ·q。
    """
    from scipy.spatial import Delaunay, QhullError

    tri = Delaunay(xy)
    indptr, indices = tri.vertex_neighbor_vertices
    hull = np.zeros(len(xy), dtype=bool)
    hull[np.unique(tri.convex_hull)] = True
    candidates = np.nonzero(~hull)[0]
    if len(candidates) > MAX_LOO_POINTS:
        candidates = np.sort(rng.choice(candidates, MAX_LOO_POINTS, replace=False))
    rows = []
    for i in candidates.tolist():
        neighbors = indices[indptr[i]:indptr[i + 1]]
        if len(neighbors) < 3:
            continue
        try:
            sub = Delaunay(xy[neighbors])
        except QhullError:
            continue
        simplex = int(sub.find_simplex(xy[i]))
        if simplex < 0:
            continue
        transform = sub.transform[simplex]
        b = transform[:2] @ (xy[i] - transform[2])
        lam = np.array([b[0], b[1], 1 - b.sum()])
        corners = neighbors[sub.simplices[simplex]]
        q = float(lam @ ((xy[i] - xy[corners]) ** 2).sum(axis=1))
        rows.append((i, z[i] - float(lam @ z[corners]), q, float(lam @ lam)))
    if not rows:
        return None
    index, residual, q, lam2 = (np.array(column) for column in zip(*rows))
    return index.astype(int), residual, q, lam2


def _kappa2(residual: np.ndarray, q: np.ndarray, lam2: np.ndarray, sigma_m: float) -> float:
    """κ² 的加权最小二乘估计：E[r²] = κ²q² + σm²(1 + Σλ²)。"""
    denominator = float((q ** 4).sum())
    if denominator <= 0:
        return 0.0
    excess = residual ** 2 - sigma_m ** 2 * (1 + lam2)
    return max(float((excess * q ** 2).sum()) / denominator, 0.0)


def _local_kappa2(n: int, neighbors: Tuple[np.ndarray, np.ndarray], loo, sigma_m: float,
                  kappa2: float) -> np.ndarray:
    """每个测点处的局部 κ²：本点及相邻点的留一残差加权估计，向全局值收缩。"""
    indptr, indices = neighbors
    num = np.zeros(n)
    den = np.zeros(n)
    index, residual, q, lam2 = loo
    num[index] = (residual ** 2 - sigma_m ** 2 * (1 + lam2)) * q ** 2
    den[index] = q ** 4
    owner = np.repeat(np.arange(n), np.diff(indptr))
    sum_num = num + np.bincount(owner, weights=num[indices], minlength=n)
    sum_den = den + np.bincount(owner, weights=den[indices], minlength=n)
    prior = SHRINK * float(den[index].mean())
    return np.clip((sum_num + prior * kappa2) / np.maximum(sum_den + prior, 1e-300), 0.0, None)


def _triangle_g(vertices: np.ndarray) -> Tuple[float, float]:
    """(面积, Σℓ²)。"""
    a, b, c = vertices
    area = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
    edges = float(((a - b) ** 2).sum() + ((b - c) ** 2).sum() + ((c - a) ** 2).sum())
    return area, edges


def _split_g(vertices: np.ndarray, point: np.ndarray) -> float:
    """在 point 处补点后，三个小三角形的 A·Σℓ²/12 之和。"""
    total = 0.0
    for k in range(3):
        child = vertices.copy()
        child[k] = point
        area, edges = _triangle_g(child)
        total += area * edges / 12
    return total


def _inside(calculator: TINEarthworkCalculator, xy: np.ndarray) -> np.ndarray:
    """全局坐标的点是否在计算范围内（边界内且不在挖空区内）。未设边界时为测点凸包。"""
    ring = calculator.boundary_polygon or calculator.coverage_polygon()
    mask = points_in_ring(xy, np.asarray(ring, dtype=float)) if len(ring) >= 3 else np.ones(len(xy), dtype=bool)
    for hole in calculator.holes:
        if len(hole) >= 3:
            mask &= ~points_in_ring(xy, np.asarray(hole, dtype=float))
    return mask


def _roughness(calculator: TINEarthworkCalculator, points: Sequence[SurveyPoint], sigma_m: float,
               out: UncertaintyResult, recommend: int, rng: np.random.Generator) -> None:
    """留一交叉验证估计局部曲率，生成误差分布图，并贪心推荐补测点。"""
    from scipy.spatial import Delaunay, QhullError

    xy_global = np.array([(p.x, p.y) for p in points], dtype=float)
    center = xy_global.mean(axis=0)
    xy = xy_global - center
    z = np.array([p.z for p in points], dtype=float)
    try:
        tri = Delaunay(xy)
    except QhullError:
        return
    loo = _loo(xy, z, rng)
    if loo is None or len(loo[0]) < 10:
        out.warnings.append("内部测点太少，无法用留一交叉验证估计误差分布，不推荐补测点")
        return
    kappa2 = _kappa2(loo[1], loo[2], loo[3], sigma_m)
    out.loo = LooStats(len(loo[0]), float(np.sqrt((loo[1] ** 2).mean())), math.sqrt(kappa2))
    if kappa2 == 0:
        out.warnings.append("留一残差不大于测量中误差，看不出内插误差，不推荐补测点")
        return
    kappa = np.sqrt(_local_kappa2(len(xy), tri.vertex_neighbor_vertices, loo, sigma_m, kappa2))

    simplices = tri.simplices
    vertices = xy[simplices]
    keep = _inside(calculator, vertices.mean(axis=1) + center)
    if calculator.max_edge_length:
        lengths = np.linalg.norm(vertices - np.roll(vertices, -1, axis=1), axis=2)
        keep &= lengths.max(axis=1) <= calculator.max_edge_length
    heap = []
    for k in np.nonzero(keep)[0].tolist():
        corners = vertices[k]
        area, edges = _triangle_g(corners)
        if area <= 0:
            continue
        k_tri = float(kappa[simplices[k]].mean())
        g = area * edges / 12
        out.indicator += k_tri * g
        out.triangle_sigma.append(([tuple(v) for v in (corners + center).tolist()], k_tri * edges / 12))
        point = corners.mean(axis=0)
        heap.append((-k_tri * (g - _split_g(corners, point)), k, corners, point, k_tri))
    if not recommend or out.indicator <= 0:
        return
    heapq.heapify(heap)
    remaining = out.indicator
    serial = len(simplices)
    while heap and len(out.recommendations) < recommend:
        negative, _, corners, point, k_tri = heapq.heappop(heap)
        gain = -negative
        if gain <= 0:
            break
        remaining -= gain
        out.recommendations.append(Recommendation(float(point[0] + center[0]), float(point[1] + center[1]), gain,
                                                  max(remaining, 0.0) / out.indicator))
        # 补点后分出的三个小三角形各自继续竞选
        for j in range(3):
            child = corners.copy()
            child[j] = point
            area, edges = _triangle_g(child)
            centroid = child.mean(axis=0)
            if area <= 0 or not _inside(calculator, (centroid + center)[None])[0]:
                continue
            serial += 1
            heapq.heappush(heap, (-k_tri * (area * edges / 12 - _split_g(child, centroid)), serial, child,
                                  centroid, k_tri))


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def evaluate_uncertainty(calculator: TINEarthworkCalculator, result: CalculationResult, sigma_measure: float = 0.02,
                         sigma_bias: float = 0.0, recommend: int = 0, seed: int = 0,
                         repeats: int = REPEATS) -> UncertaintyResult:
    """评定 result 的挖方、填方、挖填差值不确定度，并推荐 recommend 个补测点（calculator 须已完成计算）。

    比较面为统一高程、斜面、分区或设计三角网时，只有实测地面有误差；两期对比时前期、后期两次测量都计入，
    补测点推荐针对后期（现状）地面。
    """
    for label, value in (("测量中误差", sigma_measure), ("系统误差", sigma_bias)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{label}不能为负数")
    recommend = int(recommend)
    if recommend < 0 or recommend > 1000:
        raise ValueError("推荐补测点数应在 0~1000 之间")
    rng = np.random.default_rng(seed)
    out = UncertaintyResult(sigma_measure, sigma_bias)
    tin = _volumes(result)
    out.tin = tuple(float(v) for v in tin)

    period = result.is_compare and not result.is_design_tin
    surveys = [list(calculator.points)] + ([list(calculator.compare_points)] if period else [])
    hulls = [_hull_mask(points) for points in surveys]
    interior = min(int((~hull).sum()) for hull in hulls)

    bias = np.zeros(3)
    interp_var = np.zeros(3)
    if interior < MIN_INTERIOR:
        out.warnings.append(f"内部测点少于 {MIN_INTERIOR} 个，无法用抽稀外推估计内插误差，只计测量误差")
    else:
        if min(_nearest_cv(points) for points in surveys) < REGULAR_CV:
            out.warnings.append("测点接近规则格网，抽稀外推对内插偏差的估计可能偏小，95% 区间宜适当放宽")
        design = list(calculator.compare_points) if result.is_design_tin else None
        scale = 1 / FRACTION - 1
        samples = []
        for _ in range(max(int(repeats), 2)):
            subsets = [_subsample(points, hull, FRACTION, rng) for points, hull in zip(surveys, hulls)]
            compare = subsets[1] if period else design
            try:
                half = _clone(calculator, subsets[0], compare, FRACTION).run_full_calculation()
            except ValueError:
                continue
            samples.append((_volumes(half) - tin) / scale)
        if len(samples) >= 2:
            samples = np.array(samples)
            out.repeats = len(samples)
            bias = samples.mean(axis=0)
            spread = samples.std(axis=0, ddof=1)
            interp_var = spread ** 2 * (1 + 1 / len(samples)) + (BIAS_INFLATION * bias) ** 2
        else:
            out.warnings.append("抽稀后无法重新构网，只计测量误差")

    areas = np.array([sum(t.cut_area for t in result.triangles if not t.is_boundary),
                      sum(t.fill_area for t in result.triangles if not t.is_boundary),
                      result.computed_area], dtype=float)
    shares = areas / areas[2] if areas[2] > 0 else np.array([0.0, 0.0, 1.0])
    measure_var = np.zeros(3)
    for points in surveys:
        # 挖方区、填方区按面积比例分摊随机测量误差；系统误差对整个区域同向
        measure_var += sigma_measure ** 2 * _area_weights2(points, areas[2]) * shares
        measure_var += (sigma_bias * areas) ** 2
    out.bias = tuple(float(v) for v in bias)
    out.interp_sigma = tuple(float(math.sqrt(v)) for v in interp_var)
    out.measure_sigma = tuple(float(math.sqrt(v)) for v in measure_var)
    out.sigma = tuple(float(math.sqrt(a + b)) for a, b in zip(interp_var, measure_var))

    _roughness(calculator, surveys[-1], sigma_measure, out, recommend, rng)
    return out


def recommendation_points(result: UncertaintyResult) -> List[XY]:
    return [(r.x, r.y) for r in result.recommendations]
