#include "core/ai_optimizer.hpp"
#include "core/analysis.hpp"
#include "core/geometry.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace tin {

namespace {

GradePlanSummary evaluate_plane_plan(
    TINEarthworkCalculator& work_calc,
    const PlaneDesign& plane,
    const HaulCostParams& costs,
    int haul_cols,
    int haul_rows,
    const std::string& label) {
    work_calc.set_design_plane(plane);
    CalculationResult res = work_calc.calculate_volumes();
    HaulPlanResult haul = compute_haul_plan(res, haul_cols, haul_rows, costs);

    GradePlanSummary out;
    out.label = label;
    out.plane = plane;
    out.cut_volume = res.total_cut;
    out.fill_volume = res.total_fill;
    out.haul_work_m3_m = haul.total_work_m3_m;
    out.export_surplus = haul.export_surplus;
    out.borrow_deficit = haul.borrow_deficit;
    out.total_cost = haul.estimated_cost;
    return out;
}

// 搜索阶段的零堆分配快速费用估计器（复用已构好的三角网与边界裁剪状态）
class FastPlaneCostEvaluator {
public:
    FastPlaneCostEvaluator(
        TINEarthworkCalculator& calc,
        const HaulCostParams& costs,
        int haul_cols,
        int haul_rows)
        : calc_(calc), costs_(costs), cols_(haul_cols), rows_(haul_rows) {
        const auto& tris = calc_.triangles();
        items_.reserve(tris.size());
        bool any = false;
        for (const auto& t : tris) {
            if (t.is_boundary || t.area <= 0.0) continue;
            if (t.is_clipped) {
                has_clipped_ = true;
            }
            Item it;
            it.p0 = t.p0;
            it.p1 = t.p1;
            it.p2 = t.p2;
            it.area = std::abs(
                (t.p0.x * (t.p1.y - t.p2.y) +
                 t.p1.x * (t.p2.y - t.p0.y) +
                 t.p2.x * (t.p0.y - t.p1.y)) * 0.5);
            it.cx = (t.p0.x + t.p1.x + t.p2.x) / 3.0;
            it.cy = (t.p0.y + t.p1.y + t.p2.y) / 3.0;
            items_.push_back(it);
            for (const XY& q : {XY{t.p0.x, t.p0.y}, XY{t.p1.x, t.p1.y}, XY{t.p2.x, t.p2.y}}) {
                if (!any) {
                    xmin_ = xmax_ = q.x;
                    ymin_ = ymax_ = q.y;
                    any = true;
                } else {
                    xmin_ = std::min(xmin_, q.x);
                    xmax_ = std::max(xmax_, q.x);
                    ymin_ = std::min(ymin_, q.y);
                    ymax_ = std::max(ymax_, q.y);
                }
            }
        }
        compact_ = std::max(calc_.soil_factors().compact, 1e-6);
        loose_ = calc_.soil_factors().loose;
    }

    double eval(const PlaneDesign& pl) {
        if (has_clipped_) {
            calc_.set_design_plane(pl);
            CalculationResult r = calc_.calculate_volumes();
            return compute_haul_plan(r, cols_, rows_, costs_).estimated_cost;
        }
        if (items_.empty()) return 0.0;

        // 构造轻量级 CalculationResult 仅填入所需三角形与汇总，避免拷贝 polygon 容器
        CalculationResult fast_res;
        fast_res.soil_balance.factors = calc_.soil_factors();
        fast_res.triangles.resize(items_.size());
        double total_cut = 0.0, total_fill = 0.0;

        for (size_t i = 0; i < items_.size(); ++i) {
            const auto& it = items_[i];
            double dz0 = it.p0.z - pl.elevation(it.p0.x, it.p0.y);
            double dz1 = it.p1.z - pl.elevation(it.p1.x, it.p1.y);
            double dz2 = it.p2.z - pl.elevation(it.p2.x, it.p2.y);

            auto& tri = fast_res.triangles[i];
            tri.p0 = it.p0;
            tri.p1 = it.p1;
            tri.p2 = it.p2;
            tri.area = it.area;

            const bool pos = (dz0 > 0.0 || dz1 > 0.0 || dz2 > 0.0);
            const bool neg = (dz0 < 0.0 || dz1 < 0.0 || dz2 < 0.0);
            if (!(pos && neg)) {
                double vol = it.area * (dz0 + dz1 + dz2) / 3.0;
                if (vol >= 0.0) {
                    tri.cut_volume = vol;
                    total_cut += vol;
                } else {
                    tri.fill_volume = -vol;
                    total_fill += -vol;
                }
            } else {
                // 3 顶点异号时利用栈定长数组进行精确零线剖分
                std::vector<XY> poly = {{it.p0.x, it.p0.y}, {it.p1.x, it.p1.y}, {it.p2.x, it.p2.y}};
                std::vector<double> vals = {dz0, dz1, dz2};
                auto [cp, cv] = clip_by_sign(poly, vals, true);
                auto [fp, fv] = clip_by_sign(poly, vals, false);
                auto [_ca, cvol] = polygon_integral(cp, cv);
                auto [_fa, fvol] = polygon_integral(fp, fv);
                (void)_ca;
                (void)_fa;
                tri.cut_volume = std::max(0.0, cvol);
                tri.fill_volume = std::max(0.0, -fvol);
                tri.cut_polygon = std::move(cp);
                tri.fill_polygon = std::move(fp);
                total_cut += tri.cut_volume;
                total_fill += tri.fill_volume;
            }
        }
        fast_res.total_cut = total_cut;
        fast_res.total_fill = total_fill;
        fast_res.net_volume = total_cut - total_fill;
        fast_res.soil_balance = SoilBalance{total_cut, total_fill, calc_.soil_factors()};
        return compute_haul_plan(fast_res, cols_, rows_, costs_).estimated_cost;
    }

private:
    struct Item {
        SurveyPoint p0, p1, p2;
        double area = 0.0;
        double cx = 0.0, cy = 0.0;
    };
    TINEarthworkCalculator& calc_;
    HaulCostParams costs_;
    int cols_, rows_;
    std::vector<Item> items_;
    bool has_clipped_ = false;
    double xmin_ = 0.0, xmax_ = 0.0, ymin_ = 0.0, ymax_ = 0.0;
    double compact_ = 1.0, loose_ = 1.0;
};

} // namespace

GradeOptimizationResult optimize_grade_cost(
    const TINEarthworkCalculator& base_calc,
    const HaulCostParams& costs,
    const GradeOptConstraints& constraints) {
    auto t0 = std::chrono::steady_clock::now();

    if (base_calc.points().size() < 3) {
        throw std::runtime_error("至少需要 3 个测量点才能执行设计面费用优化");
    }

    TINEarthworkCalculator work_calc = base_calc;
    if (!work_calc.has_tin()) {
        work_calc.build_tin();
    }

    const double max_sx = std::max(0.0, std::abs(constraints.max_slope_x));
    const double max_sy = std::max(0.0, std::abs(constraints.max_slope_y));
    const int cols = std::clamp(constraints.haul_cols, 1, 8);
    const int rows = std::clamp(constraints.haul_rows, 1, 8);
    const int budget = std::max(24, constraints.max_evals);

    // 1. 基准对照一：平场零平衡标高
    BalanceResult bal = solve_balance_elevation(work_calc, work_calc.soil_factors());
    double cx = 0.0, cy = 0.0;
    for (const auto& p : work_calc.points()) {
        cx += p.x;
        cy += p.y;
    }
    cx /= static_cast<double>(work_calc.points().size());
    cy /= static_cast<double>(work_calc.points().size());

    PlaneDesign flat_plane{cx, cy, bal.elevation, 0.0, 0.0};
    GradePlanSummary flat_summary = evaluate_plane_plan(
        work_calc, flat_plane, costs, cols, rows, "平场平衡水平面");

    // 2. 基准对照二：最小二乘最优斜面（裁剪到允许排水坡度范围内并重新调平 H0）
    PlaneBalanceResult bf_raw = solve_best_fit_plane(work_calc, work_calc.soil_factors());
    double bf_sx = std::clamp(bf_raw.plane.slope_x, -max_sx, max_sx);
    double bf_sy = std::clamp(bf_raw.plane.slope_y, -max_sy, max_sy);
    PlaneBalanceResult bf_clamped = solve_balance_plane(
        work_calc, bf_raw.plane.x0, bf_raw.plane.y0, bf_sx, bf_sy, work_calc.soil_factors());
    GradePlanSummary bf_summary = evaluate_plane_plan(
        work_calc, bf_clamped.plane, costs, cols, rows, "最小二乘最优斜面");

    const double x0 = bf_clamped.plane.x0;
    const double y0 = bf_clamped.plane.y0;
    double z_min = bal.natural_min;
    double z_max = bal.natural_max;
    double h_span = std::max(1.0, z_max - z_min);

    struct Vec3Param {
        double h0, sx, sy;
    };

    auto clamp_param = [&](Vec3Param v) -> Vec3Param {
        v.h0 = std::clamp(v.h0, z_min - 0.5 * h_span, z_max + 0.5 * h_span);
        v.sx = std::clamp(v.sx, -max_sx, max_sx);
        v.sy = std::clamp(v.sy, -max_sy, max_sy);
        return v;
    };

    int evals = 0;
    GradePlanSummary best_summary = (bf_summary.total_cost <= flat_summary.total_cost)
                                      ? bf_summary
                                      : flat_summary;
    best_summary.label = "AI 联合费用最优斜面";

    FastPlaneCostEvaluator fast_eval(work_calc, costs, cols, rows);
    Vec3Param best_vec{best_summary.plane.h0, best_summary.plane.slope_x, best_summary.plane.slope_y};
    double best_fast_cost = best_summary.total_cost;

    auto eval_vec = [&](const Vec3Param& raw_v) -> double {
        Vec3Param v = clamp_param(raw_v);
        PlaneDesign pl{x0, y0, v.h0, v.sx, v.sy};
        double c = fast_eval.eval(pl);
        ++evals;
        if (c < best_fast_cost) {
            best_fast_cost = c;
            best_vec = v;
        }
        return c;
    };

    // 3. 多起点热启动探测（包含非平衡原始最小二乘斜面与上下标高微偏探索）
    std::vector<Vec3Param> starts = {
        {bf_clamped.plane.h0, bf_sx, bf_sy},
        {bf_raw.plane.h0, bf_sx, bf_sy},
        {bal.elevation, 0.0, 0.0},
        {bf_clamped.plane.h0 + 0.08 * h_span, bf_sx, bf_sy},
        {bf_clamped.plane.h0 - 0.08 * h_span, bf_sx, bf_sy},
    };
    Vec3Param cur{best_summary.plane.h0, best_summary.plane.slope_x, best_summary.plane.slope_y};
    double cur_cost = best_summary.total_cost;
    for (const auto& st : starts) {
        if (evals >= budget) break;
        double c = eval_vec(st);
        if (c < cur_cost) {
            cur_cost = c;
            cur = clamp_param(st);
        }
    }

    // 4. 坐标模式搜索（适应调配分段非线性与箱式坡度约束）
    double step_h = std::max(0.15, 0.15 * h_span);
    double step_s = std::max(0.10, 0.25 * std::max(max_sx, max_sy));
    if (step_s <= 1e-6) step_s = 0.0;

    for (int round = 0; round < 6 && evals + 6 <= budget; ++round) {
        bool improved = false;
        const std::array<Vec3Param, 6> dirs = {{
            {step_h, 0.0, 0.0},
            {-step_h, 0.0, 0.0},
            {0.0, step_s, 0.0},
            {0.0, -step_s, 0.0},
            {0.0, 0.0, step_s},
            {0.0, 0.0, -step_s},
        }};
        for (const auto& d : dirs) {
            if (d.sx == 0.0 && d.sy == 0.0 && d.h0 == 0.0) continue;
            if (evals >= budget) break;
            Vec3Param cand = clamp_param({cur.h0 + d.h0, cur.sx + d.sx, cur.sy + d.sy});
            double c = eval_vec(cand);
            if (c + 1e-6 < cur_cost) {
                cur = cand;
                cur_cost = c;
                improved = true;
            }
        }
        if (!improved) {
            step_h *= 0.45;
            step_s *= 0.45;
            if (step_h < 0.01 && step_s < 0.01) break;
        }
    }

    // 5. Nelder-Mead 单纯形精修（联动调整 H0 与 ix, iy 联合谷底）
    if (evals + 12 <= budget) {
        struct Vertex {
            Vec3Param p;
            double f;
        };
        std::array<Vertex, 4> simp;
        simp[0] = {cur, cur_cost};
        double dh = std::max(0.05, step_h);
        double dsx = (max_sx > 1e-6) ? std::max(0.03, std::min(step_s, max_sx * 0.25)) : 0.0;
        double dsy = (max_sy > 1e-6) ? std::max(0.03, std::min(step_s, max_sy * 0.25)) : 0.0;

        Vec3Param p1 = clamp_param({cur.h0 + dh, cur.sx, cur.sy});
        if (std::abs(p1.h0 - cur.h0) < 1e-6) p1 = clamp_param({cur.h0 - dh, cur.sx, cur.sy});
        Vec3Param p2 = clamp_param({cur.h0, cur.sx + dsx, cur.sy});
        if (dsx > 0.0 && std::abs(p2.sx - cur.sx) < 1e-6) p2 = clamp_param({cur.h0, cur.sx - dsx, cur.sy});
        Vec3Param p3 = clamp_param({cur.h0, cur.sx, cur.sy + dsy});
        if (dsy > 0.0 && std::abs(p3.sy - cur.sy) < 1e-6) p3 = clamp_param({cur.h0, cur.sx, cur.sy - dsy});

        simp[1] = {p1, eval_vec(p1)};
        simp[2] = {p2, eval_vec(p2)};
        simp[3] = {p3, eval_vec(p3)};

        while (evals + 4 <= budget) {
            std::sort(simp.begin(), simp.end(), [](const Vertex& a, const Vertex& b) {
                return a.f < b.f;
            });
            if (simp[3].f - simp[0].f < 1e-2) break;

            Vec3Param cen{
                (simp[0].p.h0 + simp[1].p.h0 + simp[2].p.h0) / 3.0,
                (simp[0].p.sx + simp[1].p.sx + simp[2].p.sx) / 3.0,
                (simp[0].p.sy + simp[1].p.sy + simp[2].p.sy) / 3.0,
            };
            Vec3Param xr = clamp_param({
                2.0 * cen.h0 - simp[3].p.h0,
                2.0 * cen.sx - simp[3].p.sx,
                2.0 * cen.sy - simp[3].p.sy,
            });
            double fr = eval_vec(xr);
            if (fr < simp[0].f) {
                Vec3Param xe = clamp_param({
                    cen.h0 + 2.0 * (xr.h0 - cen.h0),
                    cen.sx + 2.0 * (xr.sx - cen.sx),
                    cen.sy + 2.0 * (xr.sy - cen.sy),
                });
                double fe = eval_vec(xe);
                simp[3] = (fe < fr) ? Vertex{xe, fe} : Vertex{xr, fr};
            } else if (fr < simp[2].f) {
                simp[3] = {xr, fr};
            } else {
                Vec3Param xc = clamp_param({
                    cen.h0 + 0.5 * (simp[3].p.h0 - cen.h0),
                    cen.sx + 0.5 * (simp[3].p.sx - cen.sx),
                    cen.sy + 0.5 * (simp[3].p.sy - cen.sy),
                });
                double fc = eval_vec(xc);
                if (fc < simp[3].f) {
                    simp[3] = {xc, fc};
                } else {
                    for (int i = 1; i < 4 && evals < budget; ++i) {
                        simp[i].p = clamp_param({
                            0.5 * (simp[0].p.h0 + simp[i].p.h0),
                            0.5 * (simp[0].p.sx + simp[i].p.sx),
                            0.5 * (simp[0].p.sy + simp[i].p.sy),
                        });
                        simp[i].f = eval_vec(simp[i].p);
                    }
                }
            }
        }
    }

    // 对最优参数做一次完整精确评估并兜底比较基准方案
    GradePlanSummary final_opt = evaluate_plane_plan(
        work_calc,
        PlaneDesign{x0, y0, best_vec.h0, best_vec.sx, best_vec.sy},
        costs, cols, rows, "AI 联合费用最优斜面");
    if (final_opt.total_cost <= best_summary.total_cost) {
        best_summary = final_opt;
    }

    auto t1 = std::chrono::steady_clock::now();

    GradeOptimizationResult out;
    out.flat_balance = flat_summary;
    out.best_fit = bf_summary;
    out.optimized = best_summary;
    out.savings_vs_flat = flat_summary.total_cost - best_summary.total_cost;
    out.savings_pct_vs_flat = (flat_summary.total_cost > 1e-6)
                                ? (out.savings_vs_flat / flat_summary.total_cost * 100.0)
                                : 0.0;
    out.savings_vs_best_fit = bf_summary.total_cost - best_summary.total_cost;
    out.savings_pct_vs_best_fit = (bf_summary.total_cost > 1e-6)
                                    ? (out.savings_vs_best_fit / bf_summary.total_cost * 100.0)
                                    : 0.0;
    out.evaluations = evals;
    out.elapsed_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    out.converged = true;
    return out;
}

} // namespace tin
