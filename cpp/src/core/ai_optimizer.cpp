#include "core/ai_optimizer.hpp"
#include "core/analysis.hpp"
#include "core/geometry.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <limits>
#include <numeric>
#include <queue>
#include <random>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

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
        for (const auto& t : tris) {
            if (t.is_boundary || t.area <= 0.0) continue;
            if (t.is_clipped) has_clipped_ = true;
            Item it;
            it.p0 = t.p0;
            it.p1 = t.p1;
            it.p2 = t.p2;
            it.area = std::abs(
                (t.p0.x * (t.p1.y - t.p2.y) +
                 t.p1.x * (t.p2.y - t.p0.y) +
                 t.p2.x * (t.p0.y - t.p1.y)) * 0.5);
            items_.push_back(it);
        }
    }

    double eval(const PlaneDesign& pl) {
        if (has_clipped_) {
            calc_.set_design_plane(pl);
            CalculationResult r = calc_.calculate_volumes();
            return compute_haul_plan(r, cols_, rows_, costs_).estimated_cost;
        }
        if (items_.empty()) return 0.0;

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
    };
    TINEarthworkCalculator& calc_;
    HaulCostParams costs_;
    int cols_, rows_;
    std::vector<Item> items_;
    bool has_clipped_ = false;
};

double median_of(std::vector<double> v) {
    if (v.empty()) return 0.0;
    size_t mid = v.size() / 2;
    std::nth_element(v.begin(), v.begin() + static_cast<ptrdiff_t>(mid), v.end());
    double m = v[mid];
    if (v.size() % 2 == 0) {
        auto it = std::max_element(v.begin(), v.begin() + static_cast<ptrdiff_t>(mid));
        m = 0.5 * (m + *it);
    }
    return m;
}

double mad_sigma(const std::vector<double>& v, double med) {
    if (v.empty()) return 0.0;
    std::vector<double> dev(v.size());
    for (size_t i = 0; i < v.size(); ++i) dev[i] = std::abs(v[i] - med);
    return 1.4826 * median_of(std::move(dev));
}

// 解 3x3 对称线性方程组 A*x = b（高斯全主元消去）
bool solve_3x3(double A[3][3], double b[3], double x[3]) {
    for (int col = 0; col < 3; ++col) {
        int piv = col;
        double best = std::abs(A[col][col]);
        for (int r = col + 1; r < 3; ++r) {
            if (std::abs(A[r][col]) > best) {
                best = std::abs(A[r][col]);
                piv = r;
            }
        }
        if (best < 1e-12) return false;
        if (piv != col) {
            for (int c = col; c < 3; ++c) std::swap(A[col][c], A[piv][c]);
            std::swap(b[col], b[piv]);
        }
        for (int r = col + 1; r < 3; ++r) {
            double f = A[r][col] / A[col][col];
            for (int c = col; c < 3; ++c) A[r][c] -= f * A[col][c];
            b[r] -= f * b[col];
        }
    }
    for (int r = 2; r >= 0; --r) {
        double s = b[r];
        for (int c = r + 1; c < 3; ++c) s -= A[r][c] * x[c];
        x[r] = s / A[r][r];
    }
    return std::isfinite(x[0]) && std::isfinite(x[1]) && std::isfinite(x[2]);
}

// 鲁棒局部平面预测（2-pass IRLS 抗粗差裁剪，消除斜坡与边界偏差并防止邻域粗差污染）
double predict_local_elevation_robust(
    const std::vector<SurveyPoint>& pts,
    size_t target_idx,
    const std::vector<std::pair<double, size_t>>& knn) {
    if (knn.empty()) return pts[target_idx].z;
    const double x0 = pts[target_idx].x;
    const double y0 = pts[target_idx].y;

    std::vector<double> nz;
    nz.reserve(knn.size());
    for (const auto& kv : knn) nz.push_back(pts[kv.second].z);
    double med_z = median_of(nz);
    double sig_z = std::max(mad_sigma(nz, med_z), 0.25);

    std::vector<std::pair<double, size_t>> clean_knn;
    clean_knn.reserve(knn.size());
    for (const auto& kv : knn) {
        if (std::abs(pts[kv.second].z - med_z) <= 3.5 * sig_z) {
            clean_knn.push_back(kv);
        }
    }
    if (clean_knn.size() < 3) clean_knn = knn;

    // 邻域截尾 IDW 回退值
    double w_sum = 0.0, wz_sum = 0.0;
    double scale = std::max(1.0, std::sqrt(clean_knn.back().first));
    for (const auto& kv : clean_knn) {
        double d2 = kv.first + 1e-4 * scale * scale;
        double w = 1.0 / d2;
        w_sum += w;
        wz_sum += w * pts[kv.second].z;
    }
    double idw_z = (w_sum > 0.0) ? (wz_sum / w_sum) : pts[target_idx].z;
    if (clean_knn.size() < 4) return idw_z;

    auto fit_plane = [&](const std::vector<double>& extra_w, double out_sol[3]) -> bool {
        double M[3][3]{};
        double rhs[3]{};
        for (size_t k = 0; k < clean_knn.size(); ++k) {
            const auto& kv = clean_knn[k];
            const auto& q = pts[kv.second];
            double dx = (q.x - x0) / scale;
            double dy = (q.y - y0) / scale;
            double w = extra_w[k] / (kv.first / (scale * scale) + 0.05);
            double basis[3] = {1.0, dx, dy};
            for (int r = 0; r < 3; ++r) {
                rhs[r] += w * basis[r] * q.z;
                for (int c = 0; c < 3; ++c) {
                    M[r][c] += w * basis[r] * basis[c];
                }
            }
        }
        M[1][1] += 1e-4;
        M[2][2] += 1e-4;
        return solve_3x3(M, rhs, out_sol);
    };

    std::vector<double> w1(clean_knn.size(), 1.0);
    double sol1[3]{};
    if (!fit_plane(w1, sol1)) return idw_z;

    // 第二轮 IRLS：按第一轮平面残差剔除污染邻域的隐蔽粗差点
    std::vector<double> e1(clean_knn.size());
    for (size_t k = 0; k < clean_knn.size(); ++k) {
        const auto& q = pts[clean_knn[k].second];
        double pred = sol1[0] + sol1[1] * ((q.x - x0) / scale) + sol1[2] * ((q.y - y0) / scale);
        e1[k] = q.z - pred;
    }
    double med_e = median_of(e1);
    double mad_e = std::max(mad_sigma(e1, med_e), 0.12);
    std::vector<double> w2(clean_knn.size(), 1.0);
    int kept = 0;
    for (size_t k = 0; k < clean_knn.size(); ++k) {
        if (std::abs(e1[k] - med_e) > 3.0 * mad_e) {
            w2[k] = 0.0;
        } else {
            ++kept;
        }
    }
    double sol[3]{sol1[0], sol1[1], sol1[2]};
    if (kept >= 4) {
        double sol2[3]{};
        if (fit_plane(w2, sol2)) {
            sol[0] = sol2[0];
        }
    }

    double min_nz = pts[clean_knn[0].second].z, max_nz = min_nz;
    for (size_t k = 0; k < clean_knn.size(); ++k) {
        if (w2[k] <= 0.0 && kept >= 3) continue;
        min_nz = std::min(min_nz, pts[clean_knn[k].second].z);
        max_nz = std::max(max_nz, pts[clean_knn[k].second].z);
    }
    double margin = std::max(1.0, 0.75 * (max_nz - min_nz));
    if (sol[0] < min_nz - margin || sol[0] > max_nz + margin) {
        return idw_z;
    }
    return sol[0];
}

// Andrew 单调链二维凸包索引（逆时针）
std::vector<int> convex_hull_indices(const std::vector<SurveyPoint>& pts) {
    const int n = static_cast<int>(pts.size());
    if (n <= 3) {
        std::vector<int> idx(n);
        std::iota(idx.begin(), idx.end(), 0);
        return idx;
    }
    std::vector<int> order(n);
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        if (pts[a].x != pts[b].x) return pts[a].x < pts[b].x;
        return pts[a].y < pts[b].y;
    });

    auto cross = [&](int o, int a, int b) {
        return (pts[a].x - pts[o].x) * (pts[b].y - pts[o].y) -
               (pts[a].y - pts[o].y) * (pts[b].x - pts[o].x);
    };

    std::vector<int> hull;
    hull.reserve(n);
    for (int i : order) {
        while (hull.size() >= 2 && cross(hull[hull.size() - 2], hull.back(), i) <= 0.0) {
            hull.pop_back();
        }
        hull.push_back(i);
    }
    size_t lower_sz = hull.size();
    for (int k = n - 2; k >= 0; --k) {
        int i = order[k];
        while (hull.size() > lower_sz && cross(hull[hull.size() - 2], hull.back(), i) <= 0.0) {
            hull.pop_back();
        }
        hull.push_back(i);
    }
    if (!hull.empty()) hull.pop_back();
    return hull;
}

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

SiteHealthReport detect_survey_anomalies(const std::vector<SurveyPoint>& points) {
    SiteHealthReport rep;
    const size_t n = points.size();
    rep.total_points = static_cast<int>(n);
    if (n == 0) {
        rep.health_score = 100;
        rep.summary = "当前暂无测点数据";
        return rep;
    }

    double zmin = points[0].z, zmax = points[0].z, zsum = 0.0;
    for (const auto& p : points) {
        if (!std::isfinite(p.z)) continue;
        zmin = std::min(zmin, p.z);
        zmax = std::max(zmax, p.z);
        zsum += p.z;
    }
    double zmean = zsum / static_cast<double>(n);
    double zvar = 0.0;
    for (const auto& p : points) {
        double d = p.z - zmean;
        zvar += d * d;
    }
    rep.z_min = zmin;
    rep.z_max = zmax;
    rep.z_std = std::sqrt(zvar / static_cast<double>(n));

    if (n < 5) {
        rep.summary = "测点数少于 5 个，跳过局部邻域异常诊断";
        return rep;
    }

    const size_t K = std::min<size_t>(8, n - 1);
    std::vector<double> nn_dist(n, 0.0);
    std::vector<double> expected_z(n, 0.0);
    std::vector<double> residuals(n, 0.0);
    std::vector<bool> dup_conflict(n, false);
    std::vector<double> dup_ref_z(n, 0.0);

    std::vector<std::pair<double, size_t>> buf;
    buf.reserve(n);

    for (size_t i = 0; i < n; ++i) {
        const auto& pi = points[i];
        buf.clear();
        double min_nonzero_d2 = std::numeric_limits<double>::infinity();
        for (size_t j = 0; j < n; ++j) {
            if (i == j) continue;
            double dx = points[j].x - pi.x;
            double dy = points[j].y - pi.y;
            double d2 = dx * dx + dy * dy;
            if (d2 < 1e-6) {
                if (std::abs(points[j].z - pi.z) > 0.05 && j < i) {
                    dup_conflict[i] = true;
                    dup_ref_z[i] = points[j].z;
                }
                continue;
            }
            min_nonzero_d2 = std::min(min_nonzero_d2, d2);
            buf.push_back({d2, j});
        }
        nn_dist[i] = std::isfinite(min_nonzero_d2) ? std::sqrt(min_nonzero_d2) : 0.0;

        if (buf.empty()) {
            expected_z[i] = pi.z;
            residuals[i] = 0.0;
            continue;
        }
        size_t take = std::min(K, buf.size());
        std::partial_sort(buf.begin(), buf.begin() + static_cast<ptrdiff_t>(take), buf.end(),
                          [](const auto& a, const auto& b) { return a.first < b.first; });
        buf.resize(take);

        expected_z[i] = predict_local_elevation_robust(points, i, buf);
        residuals[i] = pi.z - expected_z[i];
    }

    rep.median_spacing = median_of(nn_dist);
    double nn_mad = std::max(mad_sigma(nn_dist, rep.median_spacing), 0.1 * rep.median_spacing);

    double med_res = median_of(residuals);
    double res_mad = std::max(mad_sigma(residuals, med_res), 0.04);

    // 结合整体高差尺度计算合理的物理显著门槛，避免极平坦或光滑波形地形把局部曲率误判成粗差
    double abs_thresh = std::max({0.45, 4.5 * res_mad});

    for (size_t i = 0; i < n; ++i) {
        const auto& p = points[i];
        if (dup_conflict[i]) {
            PointAnomaly a;
            a.point_index = static_cast<int>(i);
            a.point_id = p.id;
            a.x = p.x;
            a.y = p.y;
            a.z = p.z;
            a.expected_z = dup_ref_z[i];
            a.residual = p.z - dup_ref_z[i];
            a.z_score = std::abs(a.residual) / res_mad;
            a.type = AnomalyType::NearDuplicate;
            a.severity = 2;
            a.reason = "同坐标重合点高程冲突（差值 " + std::to_string(a.residual).substr(0, 6) + " m）";
            rep.anomalies.push_back(std::move(a));
            continue;
        }

        if (rep.median_spacing > 1e-6 &&
            nn_dist[i] > std::max(6.0 * rep.median_spacing, rep.median_spacing + 10.0 * nn_mad)) {
            PointAnomaly a;
            a.point_index = static_cast<int>(i);
            a.point_id = p.id;
            a.x = p.x;
            a.y = p.y;
            a.z = p.z;
            a.expected_z = expected_z[i];
            a.residual = residuals[i];
            a.z_score = (nn_dist[i] - rep.median_spacing) / std::max(nn_mad, 1e-6);
            a.type = AnomalyType::CoordOutlier;
            a.severity = 2;
            std::ostringstream oss;
            oss << "孤立飞点：最近邻距 " << std::fixed << std::setprecision(1) << nn_dist[i]
                << " m（中位间距 " << rep.median_spacing << " m）";
            a.reason = oss.str();
            rep.anomalies.push_back(std::move(a));
            continue;
        }

        double dev = residuals[i] - med_res;
        double zs = std::abs(dev) / res_mad;
        if (zs > 5.0 && std::abs(residuals[i]) > abs_thresh) {
            PointAnomaly a;
            a.point_index = static_cast<int>(i);
            a.point_id = p.id;
            a.x = p.x;
            a.y = p.y;
            a.z = p.z;
            a.expected_z = expected_z[i];
            a.residual = residuals[i];
            a.z_score = zs;
            a.type = (residuals[i] > 0.0) ? AnomalyType::ElevationSpike : AnomalyType::ElevationPit;
            a.severity = (zs > 8.0) ? 2 : 1;
            std::ostringstream oss;
            oss << ((residuals[i] > 0.0) ? "高程突刺 +" : "高程深坑 ")
                << std::fixed << std::setprecision(2) << residuals[i]
                << " m（邻域预测 " << expected_z[i] << " m，Z=" << std::setprecision(1) << zs << "）";
            a.reason = oss.str();
            rep.anomalies.push_back(std::move(a));
        }
    }

    std::sort(rep.anomalies.begin(), rep.anomalies.end(), [](const PointAnomaly& a, const PointAnomaly& b) {
        return a.z_score > b.z_score;
    });

    int penalty = 0;
    for (const auto& a : rep.anomalies) {
        penalty += (a.severity >= 2) ? 6 : 3;
    }
    double ratio_pen = 120.0 * (static_cast<double>(rep.anomalies.size()) / static_cast<double>(n));
    rep.health_score = std::clamp(100 - std::min(75, std::max(penalty / 2, static_cast<int>(std::lround(ratio_pen)))), 10, 100);

    std::ostringstream sum;
    if (rep.anomalies.empty()) {
        sum << "测点质量优良（健康分 " << rep.health_score << "/100），未发现高程粗差或坐标飞点。";
    } else {
        sum << "检出 " << rep.anomalies.size() << " 个疑似异常测点（健康分 " << rep.health_score
            << "/100），建议在画布核查或一键局部插值修复。";
    }
    rep.summary = sum.str();
    return rep;
}

CleanPointsResult clean_survey_anomalies(
    const std::vector<SurveyPoint>& points,
    const SiteHealthReport& report,
    bool interpolate_z) {
    CleanPointsResult out;
    out.points = points;
    if (report.anomalies.empty()) return out;

    std::vector<int> action(points.size(), 0); // 0=keep, 1=repair_z, 2=drop
    std::vector<double> new_z(points.size(), 0.0);
    for (const auto& a : report.anomalies) {
        if (a.point_index < 0 || a.point_index >= static_cast<int>(points.size())) continue;
        if (a.type == AnomalyType::CoordOutlier || a.type == AnomalyType::NearDuplicate || !interpolate_z) {
            action[a.point_index] = 2;
        } else {
            action[a.point_index] = 1;
            new_z[a.point_index] = a.expected_z;
        }
    }

    std::vector<SurveyPoint> filtered;
    filtered.reserve(points.size());
    for (size_t i = 0; i < points.size(); ++i) {
        if (action[i] == 2) {
            ++out.removed_count;
            continue;
        }
        SurveyPoint p = points[i];
        if (action[i] == 1) {
            p.z = new_z[i];
            p.delta_z = p.z - p.design_z;
            ++out.repaired_count;
        }
        filtered.push_back(std::move(p));
    }
    out.points = std::move(filtered);
    return out;
}

UncertaintyReport analyze_uncertainty_and_recommend(
    const TINEarthworkCalculator& base_calc,
    double measure_sigma,
    int recommend_count,
    int subsample_runs) {
    auto t0 = std::chrono::steady_clock::now();

    if (base_calc.points().size() < 4) {
        throw std::runtime_error("至少需要 4 个测点才能评定不确定度与推荐补测点");
    }

    TINEarthworkCalculator full_calc = base_calc;
    CalculationResult base_res = full_calc.has_tin()
                                     ? full_calc.calculate_volumes()
                                     : full_calc.run_full_calculation();

    UncertaintyReport rep;
    rep.cut_raw = base_res.total_cut;
    rep.fill_raw = base_res.total_fill;
    rep.net_raw = base_res.net_volume;
    rep.measure_sigma = std::max(0.0, measure_sigma);

    const auto& pts = full_calc.points();
    const size_t n = pts.size();

    // 1. 识别凸包顶点（抽稀时必须保留以保持计算覆盖范围不变）
    std::vector<int> hull = convex_hull_indices(pts);
    std::vector<bool> is_hull(n, false);
    for (int idx : hull) {
        if (idx >= 0 && idx < static_cast<int>(n)) is_hull[idx] = true;
    }
    std::vector<int> interior;
    interior.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        if (!is_hull[i]) interior.push_back(static_cast<int>(i));
    }

    // 2. Richardson 抽稀外推估计 TIN 线性内插系统偏差与随机离散
    int K = std::clamp(subsample_runs, 4, 16);
    if (interior.size() < 4) K = 0;
    rep.subsample_runs = K;

    std::vector<double> d_cut, d_fill, d_net;
    d_cut.reserve(K);
    d_fill.reserve(K);
    d_net.reserve(K);

    // 构造与 base_calc 相同配置的模板（不含点）
    TINEarthworkCalculator sub_tmpl = full_calc;
    sub_tmpl.set_breaklines({}); // 抽稀点子集时忽略非点特征线，避免缺顶点失败

    for (int k = 0; k < K; ++k) {
        std::mt19937_64 rng(20261128ULL + static_cast<unsigned long long>(k) * 1013ULL);
        std::vector<int> perm = interior;
        std::shuffle(perm.begin(), perm.end(), rng);
        size_t keep_int = std::max<size_t>(1, perm.size() / 2);

        std::vector<SurveyPoint> sub_pts;
        sub_pts.reserve(hull.size() + keep_int);
        for (int idx : hull) sub_pts.push_back(pts[idx]);
        for (size_t j = 0; j < keep_int; ++j) sub_pts.push_back(pts[perm[j]]);

        try {
            TINEarthworkCalculator sc = sub_tmpl;
            sc.add_points(sub_pts);
            CalculationResult sr = sc.run_full_calculation();
            // 内部点保留比例 f_int ≈ keep_int / interior = 0.5（凸包点全保留仅用于维持计算边界）
            // 线性内插偏差与 h^2 成正比，B = (Vk - V) / (1 / f_int - 1)
            double f_int = static_cast<double>(keep_int) / static_cast<double>(interior.size());
            double denom = std::max(0.5, (1.0 / std::clamp(f_int, 0.25, 0.75)) - 1.0);
            d_cut.push_back((sr.total_cut - rep.cut_raw) / denom);
            d_fill.push_back((sr.total_fill - rep.fill_raw) / denom);
            d_net.push_back((sr.net_volume - rep.net_raw) / denom);
        } catch (...) {
            // 若极少数随机子集退化则跳过
        }
    }

    auto mean_sd = [](const std::vector<double>& v) -> std::pair<double, double> {
        if (v.empty()) return {0.0, 0.0};
        double m = 0.0;
        for (double x : v) m += x;
        m /= static_cast<double>(v.size());
        if (v.size() == 1) return {m, 0.0};
        double s2 = 0.0;
        for (double x : v) {
            double d = x - m;
            s2 += d * d;
        }
        return {m, std::sqrt(s2 / static_cast<double>(v.size() - 1))};
    };

    auto [b_cut, sd_cut] = mean_sd(d_cut);
    auto [b_fill, sd_fill] = mean_sd(d_fill);
    auto [b_net, sd_net] = mean_sd(d_net);

    // 测量随机误差按顶点分担面积权重传播：Var_meas = sigma_m^2 * sum(w_i^2)
    std::unordered_map<long long, double> area_share;
    area_share.reserve(n * 2);
    auto pt_key = [](double x, double y) -> long long {
        long long kx = static_cast<long long>(std::llround(x * 1000.0));
        long long ky = static_cast<long long>(std::llround(y * 1000.0));
        return (kx * 73856093LL) ^ (ky * 19349663LL);
    };
    std::vector<double> nn_d(n, 0.0);
    for (const auto& t : base_res.triangles) {
        if (t.is_boundary || t.area <= 0.0) continue;
        double third = t.area / 3.0;
        area_share[pt_key(t.p0.x, t.p0.y)] += third;
        area_share[pt_key(t.p1.x, t.p1.y)] += third;
        area_share[pt_key(t.p2.x, t.p2.y)] += third;
    }
    double sum_w2 = 0.0;
    for (const auto& kv : area_share) {
        sum_w2 += kv.second * kv.second;
    }
    double var_meas = rep.measure_sigma * rep.measure_sigma * sum_w2;

    // 规则布点 vs 不规则布点偏差不确定度因子 c
    for (size_t i = 0; i < n; ++i) {
        double best = std::numeric_limits<double>::infinity();
        for (size_t j = 0; j < n; ++j) {
            if (i == j) continue;
            double dx = pts[j].x - pts[i].x;
            double dy = pts[j].y - pts[i].y;
            double d2 = dx * dx + dy * dy;
            if (d2 > 1e-8) best = std::min(best, d2);
        }
        nn_d[i] = std::isfinite(best) ? std::sqrt(best) : 0.0;
    }
    auto [nn_mean, nn_sd] = mean_sd(nn_d);
    double nn_cv = (nn_mean > 1e-9) ? (nn_sd / nn_mean) : 1.0;
    double c_bias = (nn_cv < 0.3) ? 1.0 : 0.5;
    double k_eff = std::max(1.0, static_cast<double>(d_cut.size()));

    double var_cut = sd_cut * sd_cut * (1.0 + 1.0 / k_eff) + (c_bias * b_cut) * (c_bias * b_cut) + var_meas;
    double var_fill = sd_fill * sd_fill * (1.0 + 1.0 / k_eff) + (c_bias * b_fill) * (c_bias * b_fill) + var_meas;
    double var_net = sd_net * sd_net * (1.0 + 1.0 / k_eff) +
                     c_bias * c_bias * (b_cut * b_cut + b_fill * b_fill) + var_meas;

    constexpr double Z95 = 1.959964;
    rep.cut_corrected = std::max(0.0, rep.cut_raw - b_cut);
    rep.fill_corrected = std::max(0.0, rep.fill_raw - b_fill);
    rep.net_corrected = rep.net_raw - b_net;
    rep.cut_ci95 = Z95 * std::sqrt(std::max(0.0, var_cut));
    rep.fill_ci95 = Z95 * std::sqrt(std::max(0.0, var_fill));
    rep.net_ci95 = Z95 * std::sqrt(std::max(0.0, var_net));

    rep.cut_rel_ci95_pct = (rep.cut_corrected > 1e-6) ? (rep.cut_ci95 / rep.cut_corrected * 100.0) : 0.0;
    rep.fill_rel_ci95_pct = (rep.fill_corrected > 1e-6) ? (rep.fill_ci95 / rep.fill_corrected * 100.0) : 0.0;

    // 3. 局部弯曲程度 κ 估计 + 贪心最大误差缩减补测点推荐
    std::unordered_map<long long, double> vertex_kappa;
    vertex_kappa.reserve(n * 2);
    std::vector<std::pair<double, size_t>> knn_buf;
    knn_buf.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        knn_buf.clear();
        for (size_t j = 0; j < n; ++j) {
            if (i == j) continue;
            double dx = pts[j].x - pts[i].x;
            double dy = pts[j].y - pts[i].y;
            double d2 = dx * dx + dy * dy;
            if (d2 > 1e-6) knn_buf.push_back({d2, j});
        }
        size_t take = std::min<size_t>(6, knn_buf.size());
        if (take == 0) {
            vertex_kappa[pt_key(pts[i].x, pts[i].y)] = 1e-4;
            continue;
        }
        std::partial_sort(knn_buf.begin(), knn_buf.begin() + static_cast<ptrdiff_t>(take), knn_buf.end(),
                          [](const auto& a, const auto& b) { return a.first < b.first; });
        knn_buf.resize(take);
        double pred = predict_local_elevation_robust(pts, i, knn_buf);
        double mean_l2 = 0.0;
        for (const auto& kv : knn_buf) mean_l2 += kv.first;
        mean_l2 /= static_cast<double>(take);
        double kappa = std::abs(pts[i].z - pred) / std::max(mean_l2, 1.0);
        vertex_kappa[pt_key(pts[i].x, pts[i].y)] = std::max(kappa, 1e-5);
    }

    struct CandTri {
        double score;
        XY a, b, c;
        double za, zb, zc;
        double kappa;
        bool operator<(const CandTri& o) const { return score < o.score; }
    };
    auto make_cand = [](const XY& a, const XY& b, const XY& c,
                        double za, double zb, double zc, double kappa) -> CandTri {
        double area = std::abs((a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y)) * 0.5);
        double l2_sum =
            (b.x - a.x) * (b.x - a.x) + (b.y - a.y) * (b.y - a.y) +
            (c.x - b.x) * (c.x - b.x) + (c.y - b.y) * (c.y - b.y) +
            (a.x - c.x) * (a.x - c.x) + (a.y - c.y) * (a.y - c.y);
        double score = kappa * area * (l2_sum / 12.0);
        return CandTri{score, a, b, c, za, zb, zc, kappa};
    };

    std::priority_queue<CandTri> pq;
    for (const auto& t : base_res.triangles) {
        if (t.is_boundary || t.area <= 1e-6) continue;
        double k0 = vertex_kappa[pt_key(t.p0.x, t.p0.y)];
        double k1 = vertex_kappa[pt_key(t.p1.x, t.p1.y)];
        double k2 = vertex_kappa[pt_key(t.p2.x, t.p2.y)];
        double kt = std::max({k0, k1, k2, 1e-5});
        pq.push(make_cand(
            {t.p0.x, t.p0.y}, {t.p1.x, t.p1.y}, {t.p2.x, t.p2.y},
            t.p0.z, t.p1.z, t.p2.z, kt));
    }

    const int n_rec = std::clamp(recommend_count, 0, 200);
    for (int r = 1; r <= n_rec && !pq.empty(); ++r) {
        CandTri top = pq.top();
        pq.pop();

        XY cen{(top.a.x + top.b.x + top.c.x) / 3.0, (top.a.y + top.b.y + top.c.y) / 3.0};
        double cz = (top.za + top.zb + top.zc) / 3.0;

        CandTri c1 = make_cand(top.a, top.b, cen, top.za, top.zb, cz, top.kappa);
        CandTri c2 = make_cand(top.b, top.c, cen, top.zb, top.zc, cz, top.kappa);
        CandTri c3 = make_cand(top.c, top.a, cen, top.zc, top.za, cz, top.kappa);
        double drop = std::max(0.0, top.score - (c1.score + c2.score + c3.score));

        SurveyRecommendation rec;
        rec.rank = r;
        rec.x = cen.x;
        rec.y = cen.y;
        rec.estimated_z = cz;
        rec.error_scale = top.score;
        rec.expected_error_drop = drop;
        std::ostringstream rs;
        rs << "高曲率/大跨度三角区形心（预计削减局部误差尺度 "
           << std::fixed << std::setprecision(2) << drop << " m³）";
        rec.reason = rs.str();
        rep.recommendations.push_back(std::move(rec));

        pq.push(c1);
        pq.push(c2);
        pq.push(c3);
    }

    auto t1 = std::chrono::steady_clock::now();
    rep.elapsed_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

    std::ostringstream sum;
    sum << std::fixed << std::setprecision(1)
        << "挖方修正 " << rep.cut_corrected << "±" << rep.cut_ci95 << " m³ (" << rep.cut_rel_ci95_pct
        << "%) | 填方修正 " << rep.fill_corrected << "±" << rep.fill_ci95 << " m³ (" << rep.fill_rel_ci95_pct << "%)";
    rep.summary = sum.str();
    return rep;
}

} // namespace tin
