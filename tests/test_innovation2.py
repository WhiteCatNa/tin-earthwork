"""第二批创新功能：按精度目标规划补测、变化显著性、保精度抽稀、设计面费用优化。"""
import numpy as np
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.grade_opt import CostRates, optimize_grade
from core.significance import detect_changes
from core.survey_plan import plan_survey
from core.uncertainty import evaluate_uncertainty, extend_recommendations
from tests.test_uncertainty import SQUARE, SIZE, _calc, _points, hill
from utils.terrain_import import THIN_ADAPTIVE, LasCloud, adaptive_thin, cloud_points


def wavy(x, y):
    return 50 + 2 * np.sin(x / 12) * np.cos(y / 15) + 0.01 * x


# ---------- 按精度目标规划补测 ----------

def test_plan_survey_ranges_and_batch():
    calc, result = _calc(wavy, n=200, seed=3, noise=0.0)
    u = evaluate_uncertainty(calc, result, sigma_measure=0.01, recommend=10, seed=0)
    extend_recommendations(calc, result, u, 400)
    assert len(u.recommendations) == 400
    remaining = [r.remaining for r in u.recommendations]
    assert all(a >= b - 1e-12 for a, b in zip(remaining, remaining[1:]))
    current = max(1.96 * u.sigma[i] / abs(u.estimate[i]) for i in (0, 1))
    plan = plan_survey(u, len(calc.points), current / 2)
    assert not plan.reached
    assert plan.optimistic is not None and plan.conservative is not None
    assert 0 < plan.optimistic <= plan.conservative
    assert len(plan.batch) == min(plan.optimistic, plan.conservative)
    assert plan.curve[0][0] == 0 and plan.curve[0][1] == pytest.approx(current, rel=1e-3)
    assert "预计需补测" in plan.summary_text()

    done = plan_survey(u, len(calc.points), min(current * 1.5, 0.99))
    assert done.reached and not done.batch and "不需要补测" in done.summary_text()
    for bad in (0, 1, float("nan")):
        with pytest.raises(ValueError):
            plan_survey(u, len(calc.points), bad)


def test_plan_survey_measurement_floor():
    """测量误差远大于目标时，保守估计在 50 倍点数内达不到。"""
    calc, result = _calc(wavy, n=100, seed=4)
    u = evaluate_uncertainty(calc, result, sigma_measure=0.5, recommend=50, seed=0)
    plan = plan_survey(u, len(calc.points), 0.0001)
    assert plan.conservative is None and not plan.batch and "达不到" in plan.summary_text()


# ---------- 变化显著性 ----------

def _two_period(change, n=400, seed=0, noise=0.01):
    before = _points(wavy, n, seed, noise)
    rng = np.random.default_rng(seed + 100)
    xy = np.vstack([SQUARE, rng.uniform(0, SIZE, (n, 2))])
    z = wavy(xy[:, 0], xy[:, 1]) + change(xy[:, 0], xy[:, 1]) + rng.normal(0, noise, len(xy))
    after = [SurveyPoint(f"Q{i}", float(x), float(y), float(v)) for i, ((x, y), v) in enumerate(zip(xy, z))]
    calc = TINEarthworkCalculator(50.0)
    calc.add_points(before)
    calc.set_compare_points(after)
    calc.set_boundary(SQUARE)
    return calc, calc.run_full_calculation()


def test_no_change_has_few_false_positives():
    calc, result = _two_period(lambda x, y: 0 * x)
    u = evaluate_uncertainty(calc, result, sigma_measure=0.01, recommend=0, seed=0)
    sig = u.significance
    assert sig is not None and len(sig.factors) == 2 and all(f is not None for f in sig.factors)
    # TIN 法两期差值有几百方“假变化”，绝大部分判为不显著
    assert result.total_cut + result.total_fill > 50
    assert sig.uncertain_ratio > 0.95
    assert sig.significant_cut + sig.significant_fill < 0.3 * (result.total_cut + result.total_fill)
    assert sig.fixed_significant_area > (1 - sig.uncertain_ratio) * sig.area     # 常数检测限误判更多


def test_detects_pit():
    pit = lambda x, y: -1.0 * ((abs(x - 50) < 15) & (abs(y - 50) < 15))
    calc, result = _two_period(pit, n=800)
    u = evaluate_uncertainty(calc, result, sigma_measure=0.01, recommend=0, seed=0)
    sig = u.significance
    # 挖出 30×30×1 = 900 m³（前期 − 后期为挖方）
    assert sig.significant_cut == pytest.approx(900, rel=0.2)
    ny, nx = sig.classes.shape
    centre = sig.classes[ny // 2, nx // 2]
    assert centre == 1
    rows = dict((label, value) for label, value, _, _ in sig.rows())
    assert rows["显著挖方"] == sig.significant_cut
    with pytest.raises(ValueError):
        detect_changes(calc, result, u, confidence=1.2)


def test_design_mode_significance_and_reports(tmp_path):
    from utils.data_handler import DataExporter
    calc, result = _calc(hill, n=300, seed=2, noise=0.01)
    u = evaluate_uncertainty(calc, result, sigma_measure=0.01, recommend=3, seed=0)
    result.uncertainty = u
    sig = u.significance
    assert sig is not None and len(sig.factors) == 1
    assert sig.significant_cut > 0.8 * result.total_cut
    rows = [row[0] for row in DataExporter.summary_rows(result)]
    assert "变化显著性" in rows


# ---------- 保精度抽稀 ----------

def _cloud(n=40000, seed=0, noise=0.0):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 100, (n, 2))
    z = wavy(xy[:, 0], xy[:, 1]) + rng.normal(0, noise, n) * (noise > 0)
    return np.column_stack([xy, z])


def test_adaptive_thin_tolerance_guarantee():
    from scipy.interpolate import LinearNDInterpolator
    xyz = _cloud()
    keep = adaptive_thin(xyz, 0.05)
    assert 50 < len(keep) < 0.2 * len(xyz)
    sub = xyz[keep]
    estimate = LinearNDInterpolator(sub[:, :2], sub[:, 2])(xyz[:, :2])
    ok = ~np.isnan(estimate)
    assert ok.mean() > 0.999
    assert np.abs(estimate[ok] - xyz[ok, 2]).max() <= 0.05 + 1e-9
    looser = adaptive_thin(xyz, 0.2)
    assert len(looser) < len(keep)


def test_adaptive_thin_plane_and_errors():
    rng = np.random.default_rng(1)
    xy = rng.uniform(0, 100, (5000, 2))
    plane = np.column_stack([xy, 10 + 0.02 * xy[:, 0]])
    assert len(adaptive_thin(plane, 0.01)) < 30          # 平面只留凸包附近的点
    with pytest.raises(ValueError):
        adaptive_thin(plane, 0)
    with pytest.raises(ValueError):
        adaptive_thin(_cloud(), 1e-6, max_points=100)


def test_cloud_points_adaptive():
    xyz = _cloud(20000, noise=0.005)
    cloud = LasCloud(xyz, np.full(len(xyz), 2), "1.2", 1)
    points = cloud_points(cloud, 1.0, THIN_ADAPTIVE, tolerance=0.05)
    assert 50 < len(points) < 4000 and points[0].id.startswith("L")


# ---------- 设计面费用优化 ----------

def _grade_calc():
    calc, _ = _calc(lambda x, y: 50 + 0.03 * x - 0.02 * y + np.sin(x / 15), n=150, seed=5)
    return calc


def test_optimize_grade_not_worse_than_baselines():
    calc = _grade_calc()
    calls = []
    result = optimize_grade(calc, CostRates(), zone_size=25, progress=calls.append)
    assert result.best.cost <= min(b.cost for b in result.baselines) + 1e-6
    assert result.saving >= 0 and calls and result.evaluations == calls[-1]
    flat = result.baselines[0]
    assert flat.plane.slope_x == 0 and flat.plane.slope_y == 0
    assert result.best.cost < flat.cost            # 地面有坡，斜面比水平面省
    assert len(result.rows()) == 3
    assert calc.design_plane is None                # 不改动原计算器


def test_optimize_grade_respects_bounds():
    calc = _grade_calc()
    result = optimize_grade(calc, CostRates(haul=20), slope_x=(0.5, 1.0), slope_y=(-0.2, 0.2), zone_size=25)
    plane = result.best.plane
    assert 0.5 - 1e-6 <= plane.slope_x <= 1.0 + 1e-6 and -0.2 - 1e-6 <= plane.slope_y <= 0.2 + 1e-6
    assert "超出坡度范围" in result.baselines[0].label
    with pytest.raises(ValueError):
        optimize_grade(calc, CostRates(cut=-1))
    with pytest.raises(ValueError):
        optimize_grade(calc, slope_x=(2, 1))
