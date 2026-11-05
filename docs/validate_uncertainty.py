"""不确定度评定与补测点推荐的仿真验证（docs/uncertainty_validation.md 的数据来源）。

用法：python docs/validate_uncertainty.py 400 random 30 40 [9000] [0.02]
      （测点数、布点方式 random/grid、地形组数、补测点数、随机种子起点、测量中误差 m）
      报告中的“标定组”用种子起点 5000，“检验组”用 9000（方法定型后才运行，未参与调参）。
地形 = 48 + 0.02x + 12 个随机高斯山包/洼地，200 m × 200 m，设计高程 50 m，测量中误差 0.02 m；
真值用 2000 × 2000 细网格数值积分。
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.calculator import SurveyPoint, TINEarthworkCalculator  # noqa: E402
from core.uncertainty import evaluate_uncertainty  # noqa: E402

SIZE = 200.0
DESIGN = 50.0
CORNERS = [(0.0, 0.0), (SIZE, 0.0), (SIZE, SIZE), (0.0, SIZE)]


def terrain(rng, bumps=12):
    centers = rng.uniform(0, SIZE, (bumps, 2))
    heights = rng.normal(0, 3, bumps)
    widths = rng.uniform(15, 50, bumps)

    def f(x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        return 48 + 0.02 * x + sum(h * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * w * w))
                                   for (cx, cy), h, w in zip(centers, heights, widths))
    return f


def true_volumes(f, m=2000):
    xs = (np.arange(m) + 0.5) * SIZE / m
    X, Y = np.meshgrid(xs, xs)
    d = f(X, Y) - DESIGN
    cell = (SIZE / m) ** 2
    cut, fill = d[d > 0].sum() * cell, -d[d < 0].sum() * cell
    return np.array([cut, fill, cut - fill])


def layout(rng, n, kind):
    if kind == "random":
        return rng.uniform(0, SIZE, (n, 2))
    k = int(math.sqrt(n))
    g = (np.arange(k) + 0.5) * SIZE / k
    X, Y = np.meshgrid(g, g)
    return np.column_stack([X.ravel(), Y.ravel()]) + rng.normal(0, SIZE / k * 0.15, (k * k, 2))


def volumes(xy, z):
    calc = TINEarthworkCalculator(DESIGN)
    calc.add_points([SurveyPoint(f"P{i}", float(x), float(y), float(v)) for i, ((x, y), v) in enumerate(zip(xy, z))])
    calc.set_boundary(CORNERS)
    result = calc.run_full_calculation()
    return calc, result, np.array([result.total_cut, result.total_fill, result.net_volume])


def run(n, kind, groups, extra, sigma=0.02, base=5000):
    z_scores, before, after, added = [], [], [], []
    for seed in range(groups):
        rng = np.random.default_rng(base + seed)
        f = terrain(rng)
        xy = np.vstack([CORNERS, layout(rng, n, kind)])
        z = f(xy[:, 0], xy[:, 1]) + rng.normal(0, sigma, len(xy))
        calc, result, tin = volumes(xy, z)
        u = evaluate_uncertainty(calc, result, sigma, recommend=extra, seed=seed)
        truth = true_volumes(f)
        z_scores.append((np.array(u.estimate) - truth) / np.array(u.sigma))
        before.append(tin - truth)
        after.append(np.array(u.estimate) - truth)
        if extra:
            errors = []
            for more in (np.array([(r.x, r.y) for r in u.recommendations]).reshape(-1, 2),
                         rng.uniform(0, SIZE, (extra, 2))):
                z_more = f(more[:, 0], more[:, 1]) + rng.normal(0, sigma, len(more))
                errors.append(volumes(np.vstack([xy, more]), np.r_[z, z_more])[2] - truth)
            added.append(errors)
    z_scores, before, after = np.array(z_scores), np.array(before), np.array(after)
    rms = lambda a: np.sqrt((a ** 2).mean(axis=0)).round(0)  # noqa: E731
    print(f"{n} 点 {kind}，{groups} 组，种子起点 {base}，σm = {sigma} m（挖方 / 填方 / 挖填差值）")
    print("  95% 区间覆盖率", (abs(z_scores) < 1.96).mean(axis=0).round(2), " z 均方根", rms(z_scores * 100) / 100)
    print("  均方根误差 TIN 法", rms(before), " 修正后", rms(after))
    if extra:
        added = np.array(added)
        print(f"  补测 {extra} 点后 TIN 法均方根误差：推荐位置", rms(added[:, 0]), " 随机位置", rms(added[:, 1]))


if __name__ == "__main__":
    n, kind, groups = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    extra = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    base = int(sys.argv[5]) if len(sys.argv) > 5 else 5000
    sigma = float(sys.argv[6]) if len(sys.argv) > 6 else 0.02
    run(n, kind, groups, extra, sigma, base)
