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

// 二维均匀空间网格加速索引（将 k-NN 与最近邻查询从 O(N²) 降至均摊 O(N)）
class SpatialGrid2D {
public:
    explicit SpatialGrid2D(const std::vector<SurveyPoint>& pts) : pts_(pts) {
        const size_t n = pts.size();
        if (n == 0) return;
        xmin_ = xmax_ = pts[0].x;
        ymin_ = ymax_ = pts[0].y;
        for (const auto& p : pts) {
            xmin_ = std::min(xmin_, p.x);
            xmax_ = std::max(xmax_, p.x);
            ymin_ = std::min(ymin_, p.y);
            ymax_ = std::max(ymax_, p.y);
        }
        int dim = static_cast<int>(std::clamp(std::sqrt(static_cast<double>(n) / 4.0), 4.0, 96.0));
        gx_ = dim;
        gy_ = dim;
        double span_x = std::max(xmax_ - xmin_, 1.0);
        double span_y = std::max(ymax_ - ymin_, 1.0);
        cw_ = span_x / static_cast<double>(gx_);
        ch_ = span_y / static_cast<double>(gy_);
        min_cell_ = std::min(cw_, ch_);
        cells_.resize(static_cast<size_t>(gx_ * gy_));
        for (size_t i = 0; i < n; ++i) {
            int cx = std::clamp(static_cast<int>((pts[i].x - xmin_) / cw_), 0, gx_ - 1);
            int cy = std::clamp(static_cast<int>((pts[i].y - ymin_) / ch_), 0, gy_ - 1);
            cells_[static_cast<size_t>(cy * gx_ + cx)].push_back(i);
        }
    }

    void query_knn(
        size_t target_idx,
        size_t k,
        std::vector<std::pair<double, size_t>>& out_knn,
        double& out_nn_dist,
        bool* out_dup_conflict = nullptr,
        double* out_dup_ref_z = nullptr) const {
        out_knn.clear();
        const auto& pi = pts_[target_idx];
        double min_nonzero_d2 = std::numeric_limits<double>::infinity();
        if (out_dup_conflict) *out_dup_conflict = false;

        int cx0 = std::clamp(static_cast<int>((pi.x - xmin_) / cw_), 0, gx_ - 1);
        int cy0 = std::clamp(static_cast<int>((pi.y - ymin_) / ch_), 0, gy_ - 1);
        int max_r = std::max({cx0, gx_ - 1 - cx0, cy0, gy_ - 1 - cy0});

        auto scan_cell = [&](int cx, int cy) {
            for (size_t j : cells_[static_cast<size_t>(cy * gx_ + cx)]) {
                if (j == target_idx) continue;
                double dx = pts_[j].x - pi.x;
                double dy = pts_[j].y - pi.y;
                double d2 = dx * dx + dy * dy;
                if (d2 < 1e-6) {
                    if (out_dup_conflict && out_dup_ref_z &&
                        std::abs(pts_[j].z - pi.z) > 0.05 && j < target_idx) {
                        *out_dup_conflict = true;
                        *out_dup_ref_z = pts_[j].z;
                    }
                    continue;
                }
                min_nonzero_d2 = std::min(min_nonzero_d2, d2);
                out_knn.push_back({d2, j});
            }
        };

        for (int r = 0; r <= max_r; ++r) {
            if (r == 0) {
                scan_cell(cx0, cy0);
            } else {
                int x_lo = std::max(0, cx0 - r);
                int x_hi = std::min(gx_ - 1, cx0 + r);
                int y_lo = std::max(0, cy0 - r);
                int y_hi = std::min(gy_ - 1, cy0 + r);
                for (int cx = x_lo; cx <= x_hi; ++cx) {
                    if (cy0 - r >= 0) scan_cell(cx, cy0 - r);
                    if (cy0 + r <= gy_ - 1) scan_cell(cx, cy0 + r);
                }
                for (int cy = y_lo + 1; cy <= y_hi - 1; ++cy) {
                    if (cx0 - r >= 0) scan_cell(cx0 - r, cy);
                    if (cx0 + r <= gx_ - 1) scan_cell(cx0 + r, cy);
                }
            }
            if (r >= 1 && out_knn.size() >= k && std::isfinite(min_nonzero_d2)) {
                std::nth_element(out_knn.begin(),
                                 out_knn.begin() + static_cast<ptrdiff_t>(k - 1),
                                 out_knn.end(),
                                 [](const auto& a, const auto& b) { return a.first < b.first; });
                double kth_d2 = out_knn[k - 1].first;
                double next_min_d = static_cast<double>(r) * min_cell_;
                if (next_min_d * next_min_d > kth_d2 + 1e-9) {
                    break;
                }
            }
        }

        out_nn_dist = std::isfinite(min_nonzero_d2) ? std::sqrt(min_nonzero_d2) : 0.0;
        if (out_knn.size() > k) {
            std::partial_sort(out_knn.begin(), out_knn.begin() + static_cast<ptrdiff_t>(k), out_knn.end(),
                              [](const auto& a, const auto& b) { return a.first < b.first; });
            out_knn.resize(k);
        } else {
            std::sort(out_knn.begin(), out_knn.end(),
                      [](const auto& a, const auto& b) { return a.first < b.first; });
        }
    }

private:
    const std::vector<SurveyPoint>& pts_;
    double xmin_ = 0.0, xmax_ = 0.0, ymin_ = 0.0, ymax_ = 0.0;
    double cw_ = 1.0, ch_ = 1.0, min_cell_ = 1.0;
    int gx_ = 1, gy_ = 1;
    std::vector<std::vector<size_t>> cells_;
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
    const double min_drain = (constraints.min_drain_slope_pct > 1e-6)
                                 ? std::min(std::abs(constraints.min_drain_slope_pct), std::hypot(max_sx, max_sy))
                                 : 0.0;
    const bool has_hard_constraints = (min_drain > 1e-6) || !constraints.control_points.empty();
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

    auto constraint_penalty = [&](const PlaneDesign& pl) -> double {
        double pen = 0.0;
        if (min_drain > 1e-6) {
            double sp = std::hypot(pl.slope_x, pl.slope_y);
            if (sp < min_drain) {
                double def = min_drain - sp;
                pen += 1e7 * def * def + 2e5 * def;
            }
        }
        for (const auto& cp : constraints.control_points) {
            double err = std::abs(pl.elevation(cp.x, cp.y) - cp.target_z);
            double tol = std::max(0.0, cp.max_dev_m);
            if (err > tol) {
                double ex = err - tol;
                double eff_w = std::max({cp.weight, 5e5, flat_summary.total_cost * 2.0});
                pen += eff_w * (20.0 * ex * ex + 2.0 * ex);
            }
        }
        return pen;
    };

    int evals = 0;
    GradePlanSummary best_summary = (bf_summary.total_cost <= flat_summary.total_cost)
                                      ? bf_summary
                                      : flat_summary;
    best_summary.label = "AI 联合费用最优斜面";

    FastPlaneCostEvaluator fast_eval(work_calc, costs, cols, rows);
    Vec3Param best_vec{best_summary.plane.h0, best_summary.plane.slope_x, best_summary.plane.slope_y};
    double best_fast_cost = has_hard_constraints ? std::numeric_limits<double>::infinity() : best_summary.total_cost;

    auto eval_vec = [&](const Vec3Param& raw_v) -> double {
        Vec3Param v = clamp_param(raw_v);
        PlaneDesign pl{x0, y0, v.h0, v.sx, v.sy};
        double c = fast_eval.eval(pl) + constraint_penalty(pl);
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
    if (min_drain > 1e-6) {
        double h_ref = bf_clamped.plane.h0;
        if (max_sx >= min_drain) {
            starts.push_back({h_ref, min_drain, 0.0});
            starts.push_back({h_ref, -min_drain, 0.0});
        }
        if (max_sy >= min_drain) {
            starts.push_back({h_ref, 0.0, min_drain});
            starts.push_back({h_ref, 0.0, -min_drain});
        }
        double bf_norm = std::hypot(bf_sx, bf_sy);
        if (bf_norm > 1e-6) {
            double sc = min_drain / bf_norm;
            starts.push_back({h_ref, bf_sx * sc, bf_sy * sc});
        } else if (max_sx > 1e-6 || max_sy > 1e-6) {
            double diag = min_drain / std::sqrt(2.0);
            starts.push_back({h_ref, std::min(diag, max_sx), std::min(diag, max_sy)});
        }
    }
    if (!constraints.control_points.empty()) {
        const auto& cp0 = constraints.control_points.front();
        double h_cp_bf = cp0.target_z - (bf_sx / 100.0) * (cp0.x - x0) - (bf_sy / 100.0) * (cp0.y - y0);
        starts.push_back({h_cp_bf, bf_sx, bf_sy});
        starts.push_back({cp0.target_z, 0.0, 0.0});
        if (min_drain > 1e-6 && max_sx >= min_drain) {
            double h_cp_dx = cp0.target_z - (min_drain / 100.0) * (cp0.x - x0);
            starts.push_back({h_cp_dx, min_drain, 0.0});
        }
    }

    Vec3Param cur{best_vec.h0, best_vec.sx, best_vec.sy};
    double cur_cost = std::numeric_limits<double>::infinity();
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

    if (min_drain > 1e-6 && std::hypot(best_vec.sx, best_vec.sy) < min_drain) {
        double sp = std::hypot(best_vec.sx, best_vec.sy);
        if (sp > 1e-6) {
            double sc = (min_drain + 1e-4) / sp;
            best_vec = clamp_param({best_vec.h0, best_vec.sx * sc, best_vec.sy * sc});
        } else if (max_sx >= min_drain) {
            best_vec.sx = min_drain + 1e-4;
        } else if (max_sy >= min_drain) {
            best_vec.sy = min_drain + 1e-4;
        }
    }
    if (constraints.control_points.size() == 1) {
        const auto& cp0 = constraints.control_points.front();
        PlaneDesign tmp{x0, y0, best_vec.h0, best_vec.sx, best_vec.sy};
        double diff = tmp.elevation(cp0.x, cp0.y) - cp0.target_z;
        double tol = std::max(0.0, cp0.max_dev_m);
        if (std::abs(diff) > tol) {
            double shift = std::copysign(std::abs(diff) - tol * 0.95, diff);
            best_vec = clamp_param({best_vec.h0 - shift, best_vec.sx, best_vec.sy});
        }
    }

    GradePlanSummary final_opt = evaluate_plane_plan(
        work_calc,
        PlaneDesign{x0, y0, best_vec.h0, best_vec.sx, best_vec.sy},
        costs, cols, rows, "AI 联合费用最优斜面");
    if (has_hard_constraints || final_opt.total_cost <= best_summary.total_cost) {
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

    SpatialGrid2D grid(points);
    std::vector<std::pair<double, size_t>> buf;
    buf.reserve(32);

    for (size_t i = 0; i < n; ++i) {
        const auto& pi = points[i];
        bool dc = false;
        double dref = 0.0;
        grid.query_knn(i, K, buf, nn_dist[i], &dc, &dref);
        if (dc) {
            dup_conflict[i] = true;
            dup_ref_z[i] = dref;
        }
        if (buf.empty()) {
            expected_z[i] = pi.z;
            residuals[i] = 0.0;
            continue;
        }
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

    // 规则布点 vs 不规则布点偏差不确定度因子 c，并复用 O(N) 空间网格估计局部曲率 κ
    SpatialGrid2D grid(pts);
    std::unordered_map<long long, double> vertex_kappa;
    vertex_kappa.reserve(n * 2);
    std::vector<std::pair<double, size_t>> knn_buf;
    knn_buf.reserve(16);

    for (size_t i = 0; i < n; ++i) {
        grid.query_knn(i, 6, knn_buf, nn_d[i]);
        if (knn_buf.empty()) {
            vertex_kappa[pt_key(pts[i].x, pts[i].y)] = 1e-4;
            continue;
        }
        double pred = predict_local_elevation_robust(pts, i, knn_buf);
        double mean_l2 = 0.0;
        for (const auto& kv : knn_buf) mean_l2 += kv.first;
        mean_l2 /= static_cast<double>(knn_buf.size());
        double kappa = std::abs(pts[i].z - pred) / std::max(mean_l2, 1.0);
        vertex_kappa[pt_key(pts[i].x, pts[i].y)] = std::max(kappa, 1e-5);
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

SignificanceReport analyze_change_significance(
    const TINEarthworkCalculator& base_calc,
    double measure_sigma) {
    if (base_calc.points().size() < 4) {
        throw std::runtime_error("至少需要 4 个测点才能执行变化显著性分析");
    }

    TINEarthworkCalculator full_calc = base_calc;
    CalculationResult res = full_calc.has_tin()
                                ? full_calc.calculate_volumes()
                                : full_calc.run_full_calculation();

    const auto& pts = full_calc.points();
    const size_t n = pts.size();
    std::vector<int> hull = convex_hull_indices(pts);
    std::vector<bool> is_hull(n, false);
    for (int idx : hull) {
        if (idx >= 0 && idx < static_cast<int>(n)) is_hull[idx] = true;
    }
    std::vector<int> interior;
    for (size_t i = 0; i < n; ++i) {
        if (!is_hull[i]) interior.push_back(static_cast<int>(i));
    }

    // 1. 分裂共形预测（3 次 10% 内部点留出交叉检验）标定检测限倍数 q
    std::vector<double> norm_res;
    TINEarthworkCalculator tmpl = full_calc;
    tmpl.set_breaklines({});
    for (int rep_i = 0; rep_i < 3 && interior.size() >= 10; ++rep_i) {
        std::mt19937_64 rng(9911ULL + static_cast<unsigned long long>(rep_i) * 313ULL);
        std::vector<int> perm = interior;
        std::shuffle(perm.begin(), perm.end(), rng);
        size_t hold = std::max<size_t>(1, perm.size() / 10);

        std::vector<SurveyPoint> train;
        train.reserve(n - hold);
        for (int idx : hull) train.push_back(pts[idx]);
        for (size_t j = hold; j < perm.size(); ++j) train.push_back(pts[perm[j]]);

        try {
            TINEarthworkCalculator tc = tmpl;
            tc.add_points(train);
            tc.build_tin();
            for (size_t j = 0; j < hold; ++j) {
                const auto& hp = pts[perm[j]];
                auto [zn, _zd, _ok] = tc.sample_surface(hp.x, hp.y, false);
                (void)_zd;
                (void)_ok;
                if (std::isfinite(zn)) {
                    double scale = std::max(0.02, measure_sigma);
                    norm_res.push_back(std::abs(hp.z - zn) / scale);
                }
            }
        } catch (...) {}
    }

    double q = 1.959964;
    if (norm_res.size() >= 20) {
        std::sort(norm_res.begin(), norm_res.end());
        size_t idx95 = static_cast<size_t>(std::ceil(0.95 * static_cast<double>(norm_res.size()))) - 1;
        idx95 = std::clamp<size_t>(idx95, 0, norm_res.size() - 1);
        q = std::clamp(norm_res[idx95], 1.959964, 6.0);
    }

    SignificanceReport out;
    out.conformal_q = q;

    // 2. 估计局部弯曲度用于空间变化检测限 LoD
    std::unordered_map<long long, double> v_kappa;
    auto pt_key = [](double x, double y) -> long long {
        long long kx = static_cast<long long>(std::llround(x * 1000.0));
        long long ky = static_cast<long long>(std::llround(y * 1000.0));
        return (kx * 73856093LL) ^ (ky * 19349663LL);
    };
    std::vector<std::pair<double, size_t>> knn;
    for (size_t i = 0; i < n; ++i) {
        knn.clear();
        for (size_t j = 0; j < n; ++j) {
            if (i == j) continue;
            double dx = pts[j].x - pts[i].x;
            double dy = pts[j].y - pts[i].y;
            double d2 = dx * dx + dy * dy;
            if (d2 > 1e-6) knn.push_back({d2, j});
        }
        size_t take = std::min<size_t>(6, knn.size());
        if (take == 0) continue;
        std::partial_sort(knn.begin(), knn.begin() + static_cast<ptrdiff_t>(take), knn.end(),
                          [](const auto& a, const auto& b) { return a.first < b.first; });
        knn.resize(take);
        double pred = predict_local_elevation_robust(pts, i, knn);
        double ml2 = 0.0;
        for (const auto& kv : knn) ml2 += kv.first;
        ml2 /= static_cast<double>(take);
        v_kappa[pt_key(pts[i].x, pts[i].y)] = std::abs(pts[i].z - pred) / std::max(ml2, 1.0);
    }

    for (const auto& t : res.triangles) {
        if (t.is_boundary || t.area <= 0.0) continue;
        out.total_area += t.area;
        double l2 =
            (t.p1.x - t.p0.x) * (t.p1.x - t.p0.x) + (t.p1.y - t.p0.y) * (t.p1.y - t.p0.y) +
            (t.p2.x - t.p1.x) * (t.p2.x - t.p1.x) + (t.p2.y - t.p1.y) * (t.p2.y - t.p1.y) +
            (t.p0.x - t.p2.x) * (t.p0.x - t.p2.x) + (t.p0.y - t.p2.y) * (t.p0.y - t.p2.y);
        double kmax = std::max({
            v_kappa[pt_key(t.p0.x, t.p0.y)],
            v_kappa[pt_key(t.p1.x, t.p1.y)],
            v_kappa[pt_key(t.p2.x, t.p2.y)],
        });
        double interp_s = kmax * (l2 / 12.0);
        double s_tot = std::sqrt(measure_sigma * measure_sigma + interp_s * interp_s);
        double lod = q * s_tot;

        if (std::abs(t.avg_delta_z) > lod) {
            out.significant_cut += t.cut_volume;
            out.significant_fill += t.fill_volume;
        } else {
            out.uncertain_cut += t.cut_volume;
            out.uncertain_fill += t.fill_volume;
            out.uncertain_area += t.area;
        }
    }
    out.uncertain_ratio = (out.total_area > 1e-9) ? (out.uncertain_area / out.total_area) : 0.0;

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(1)
        << "显著挖方 " << out.significant_cut << " m³ / 显著填方 " << out.significant_fill
        << " m³（" << (out.uncertain_ratio * 100.0) << "% 面积高差处于检测限 LoD 内，q="
        << std::setprecision(2) << out.conformal_q << "）";
    out.summary = oss.str();
    return out;
}

ThinningResult thin_points_preserve_accuracy(
    const std::vector<SurveyPoint>& points,
    double tolerance_m) {
    ThinningResult out;
    out.original_count = static_cast<int>(points.size());
    out.tolerance_m = std::max(1e-4, tolerance_m);
    if (points.size() <= 4) {
        out.points = points;
        out.retained_count = static_cast<int>(points.size());
        out.summary = "测点数 <= 4，全部保留";
        return out;
    }

    // 凸包顶点作为初始骨架，保证测区覆盖范围不萎缩
    std::vector<int> hull = convex_hull_indices(points);
    std::vector<bool> kept(points.size(), false);
    for (int idx : hull) {
        if (idx >= 0 && idx < static_cast<int>(points.size())) kept[idx] = true;
    }

    auto build_kept_vec = [&]() {
        std::vector<SurveyPoint> v;
        v.reserve(points.size());
        for (size_t i = 0; i < points.size(); ++i) {
            if (kept[i]) v.push_back(points[i]);
        }
        return v;
    };

    double worst_err = 0.0;
    for (int pass = 0; pass < 18; ++pass) {
        auto cur_pts = build_kept_vec();
        if (cur_pts.size() >= points.size()) {
            worst_err = 0.0;
            break;
        }
        TINEarthworkCalculator calc(0.0);
        calc.add_points(cur_pts);
        calc.build_tin();

        // 在当前每个超限三角形内最多挑 1 个误差最大点插入（兼顾并行收敛与不扎堆）
        const auto& tris = calc.triangles();
        std::vector<int> best_pt_for_tri(tris.size(), -1);
        std::vector<double> best_err_for_tri(tris.size(), out.tolerance_m);

        worst_err = 0.0;
        int global_worst_idx = -1;
        double global_worst_err = 0.0;

        for (size_t i = 0; i < points.size(); ++i) {
            if (kept[i]) continue;
            auto [zn, _zd, _ok] = calc.sample_surface(points[i].x, points[i].y, true);
            (void)_zd;
            (void)_ok;
            if (!std::isfinite(zn)) continue;
            double err = std::abs(points[i].z - zn);
            worst_err = std::max(worst_err, err);
            if (err > global_worst_err) {
                global_worst_err = err;
                global_worst_idx = static_cast<int>(i);
            }
            if (err > out.tolerance_m) {
                // 粗定位至最先包含该点的有效三角形（按三角网索引）
                for (size_t ti = 0; ti < tris.size(); ++ti) {
                    const auto& t = tris[ti];
                    double a = (t.p1.x - t.p0.x) * (points[i].y - t.p0.y) - (t.p1.y - t.p0.y) * (points[i].x - t.p0.x);
                    double b = (t.p2.x - t.p1.x) * (points[i].y - t.p1.y) - (t.p2.y - t.p1.y) * (points[i].x - t.p1.x);
                    double c = (t.p0.x - t.p2.x) * (points[i].y - t.p2.y) - (t.p0.y - t.p2.y) * (points[i].x - t.p2.x);
                    if ((a >= -1e-6 && b >= -1e-6 && c >= -1e-6) || (a <= 1e-6 && b <= 1e-6 && c <= 1e-6)) {
                        if (err > best_err_for_tri[ti]) {
                            best_err_for_tri[ti] = err;
                            best_pt_for_tri[ti] = static_cast<int>(i);
                        }
                        break;
                    }
                }
            }
        }

        if (worst_err <= out.tolerance_m) break;

        int added = 0;
        for (int idx : best_pt_for_tri) {
            if (idx >= 0 && !kept[idx]) {
                kept[idx] = true;
                ++added;
            }
        }
        if (added == 0 && global_worst_idx >= 0) {
            kept[global_worst_idx] = true;
            ++added;
        }
        if (added == 0) break;
    }

    out.points = build_kept_vec();
    out.retained_count = static_cast<int>(out.points.size());
    out.reduction_pct = (out.original_count > 0)
        ? (100.0 * static_cast<double>(out.original_count - out.retained_count) / static_cast<double>(out.original_count))
        : 0.0;

    // 最终实测最大残差校验
    if (out.retained_count < out.original_count) {
        TINEarthworkCalculator fc(0.0);
        fc.add_points(out.points);
        fc.build_tin();
        double final_max = 0.0;
        for (size_t i = 0; i < points.size(); ++i) {
            if (kept[i]) continue;
            auto [zn, _zd, _ok] = fc.sample_surface(points[i].x, points[i].y, true);
            (void)_zd;
            (void)_ok;
            if (std::isfinite(zn)) {
                final_max = std::max(final_max, std::abs(points[i].z - zn));
            }
        }
        out.max_error_m = final_max;
    } else {
        out.max_error_m = 0.0;
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(1)
        << "保精度抽稀完成（容差 " << std::setprecision(3) << out.tolerance_m
        << " m）：保留 " << out.retained_count << "/" << out.original_count
        << " 点（压缩 " << std::setprecision(1) << out.reduction_pct
        << "%），实测最大高程误差 " << std::setprecision(3) << out.max_error_m << " m";
    out.summary = oss.str();
    return out;
}

DrainageReport analyze_drainage_and_ponding(
    const CalculationResult& result,
    double min_slope_pct,
    bool use_design_surface) {
    DrainageReport out;
    out.min_required_slope_pct = std::max(0.0, min_slope_pct);
    out.min_slope_pct = std::numeric_limits<double>::infinity();

    double weighted_slope_sum = 0.0;
    double runoff_x = 0.0, runoff_y = 0.0;

    for (const auto& t : result.triangles) {
        if (t.is_boundary || t.area <= 1e-9) continue;
        double z0 = use_design_surface ? t.p0.design_z : t.p0.z;
        double z1 = use_design_surface ? t.p1.design_z : t.p1.z;
        double z2 = use_design_surface ? t.p2.design_z : t.p2.z;
        auto pl = linear_plane(
            {t.p0.x, t.p0.y}, {t.p1.x, t.p1.y}, {t.p2.x, t.p2.y},
            z0, z1, z2, 1e-18);
        double gx = 0.0, gy = 0.0;
        if (pl.has_value()) {
            gx = std::get<0>(*pl);
            gy = std::get<1>(*pl);
        }
        double slope_pct = std::hypot(gx, gy) * 100.0;
        out.total_area += t.area;
        weighted_slope_sum += slope_pct * t.area;
        out.min_slope_pct = std::min(out.min_slope_pct, slope_pct);
        out.max_slope_pct = std::max(out.max_slope_pct, slope_pct);

        // 下坡汇流方向为负梯度方向 (-gx, -gy)
        runoff_x += (-gx) * t.area;
        runoff_y += (-gy) * t.area;

        if (slope_pct + 1e-9 < out.min_required_slope_pct) {
            out.ponding_area += t.area;
            out.ponding_triangle_ids.push_back(t.id);
        }
    }

    if (out.total_area > 1e-9) {
        out.mean_slope_pct = weighted_slope_sum / out.total_area;
        out.ponding_ratio_pct = out.ponding_area / out.total_area * 100.0;
        if (std::hypot(runoff_x, runoff_y) > 1e-12) {
            // 测量方位角：正北(Y+)为 0°，顺时针正东(X+)为 90°
            double az = std::atan2(runoff_x, runoff_y) * 180.0 / 3.14159265358979323846;
            if (az < 0.0) az += 360.0;
            out.dominant_runoff_azimuth_deg = az;
        }
    } else {
        out.min_slope_pct = 0.0;
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(2)
        << "平均坡度 " << out.mean_slope_pct << "%，主排水方向 "
        << std::setprecision(1) << out.dominant_runoff_azimuth_deg
        << "°；低于 " << std::setprecision(2) << out.min_required_slope_pct
        << "% 积水风险区占比 " << std::setprecision(1) << out.ponding_ratio_pct
        << "%（" << out.ponding_triangle_ids.size() << " 个三角形）";
    out.summary = oss.str();
    return out;
}

SensitivityCurveResult run_elevation_sensitivity_curve(
    const TINEarthworkCalculator& base_calc,
    const HaulCostParams& costs,
    int steps,
    double half_span_m) {
    if (base_calc.points().size() < 3) {
        throw std::runtime_error("至少需要 3 个测量点才能生成标高敏感性曲线");
    }

    TINEarthworkCalculator work = base_calc;
    if (!work.has_tin()) work.build_tin();

    BalanceResult bal = solve_balance_elevation(work, work.soil_factors());
    double z_span = std::max(1.0, bal.natural_max - bal.natural_min);
    double span = (half_span_m > 1e-6) ? half_span_m : std::max(1.0, 0.55 * z_span);
    int n_steps = std::clamp(steps, 5, 41);

    SensitivityCurveResult out;
    out.center_elevation = bal.elevation;
    out.min_total_cost = std::numeric_limits<double>::infinity();
    out.points.reserve(static_cast<size_t>(n_steps));

    for (int i = 0; i < n_steps; ++i) {
        double frac = static_cast<double>(i) / static_cast<double>(n_steps - 1);
        double h = (bal.elevation - span) + frac * (2.0 * span);
        work.set_design_elevation(h);
        CalculationResult res = work.calculate_volumes();
        HaulPlanResult haul = compute_haul_plan(res, 4, 3, costs);

        SensitivityPoint pt{h, res.total_cut, res.total_fill, res.net_volume, haul.estimated_cost};
        if (pt.total_cost < out.min_total_cost) {
            out.min_total_cost = pt.total_cost;
            out.best_cost_elevation = h;
        }
        out.points.push_back(pt);
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(2)
        << "平场经济最优标高 " << out.best_cost_elevation << " m（综合成本 ¥"
        << std::setprecision(0) << out.min_total_cost << "，平衡标高 H0="
        << std::setprecision(2) << out.center_elevation << " m）";
    out.summary = oss.str();
    return out;
}

SteppedPlatformResult optimize_stepped_platform(
    const TINEarthworkCalculator& base_calc,
    const HaulCostParams& costs,
    double wall_cost_per_m2) {
    if (base_calc.points().size() < 4) {
        throw std::runtime_error("至少需要 4 个测量点才能执行双级台阶平场优化");
    }

    TINEarthworkCalculator work = base_calc;
    if (!work.has_tin()) work.build_tin();

    const SoilFactors sf = work.soil_factors();
    BalanceResult bal = solve_balance_elevation(work, sf);
    work.set_design_elevation(bal.elevation);
    CalculationResult single_res = work.calculate_volumes();
    HaulPlanResult single_haul = compute_haul_plan(single_res, 4, 3, costs);

    SteppedPlatformResult best;
    best.wall_cost_per_m2 = std::max(0.0, wall_cost_per_m2);
    best.single_pad_elevation = bal.elevation;
    best.single_pad_cut = single_res.total_cut;
    best.single_pad_fill = single_res.total_fill;
    best.single_pad_cost = single_haul.estimated_cost;
    best.bench1_elevation = bal.elevation;
    best.bench2_elevation = bal.elevation;
    best.bench1_cut = single_res.total_cut;
    best.bench1_fill = single_res.total_fill;
    best.earthwork_cost = single_haul.estimated_cost;
    best.total_cost = single_haul.estimated_cost;

    struct TriItem {
        SurveyPoint p0, p1, p2;
        double area = 0.0;
        double cx = 0.0, cy = 0.0;
        double zmin = 0.0, zmax = 0.0;
    };
    std::vector<TriItem> items;
    items.reserve(single_res.triangles.size());
    double total_area = 0.0;
    double xmin = 1e30, xmax = -1e30, ymin = 1e30, ymax = -1e30;
    for (const auto& t : single_res.triangles) {
        if (t.is_boundary || t.area <= 1e-12) continue;
        TriItem it;
        it.p0 = t.p0;
        it.p1 = t.p1;
        it.p2 = t.p2;
        it.area = t.area;
        it.cx = (t.p0.x + t.p1.x + t.p2.x) / 3.0;
        it.cy = (t.p0.y + t.p1.y + t.p2.y) / 3.0;
        it.zmin = std::min({t.p0.z, t.p1.z, t.p2.z});
        it.zmax = std::max({t.p0.z, t.p1.z, t.p2.z});
        xmin = std::min(xmin, it.cx);
        xmax = std::max(xmax, it.cx);
        ymin = std::min(ymin, it.cy);
        ymax = std::max(ymax, it.cy);
        total_area += it.area;
        items.push_back(it);
    }

    if (items.size() < 6 || total_area <= 1e-9) {
        best.summary = "有效三角网数量不足，维持单平场平衡标高方案";
        return best;
    }

    auto eval_subset_vol = [&](const std::vector<size_t>& idxs, double h,
                               double& out_cut, double& out_fill,
                               std::vector<Triangle>* out_tris) {
        out_cut = 0.0;
        out_fill = 0.0;
        for (size_t idx : idxs) {
            const auto& it = items[idx];
            double dz0 = it.p0.z - h;
            double dz1 = it.p1.z - h;
            double dz2 = it.p2.z - h;

            double raw_area = std::abs(
                (it.p0.x * (it.p1.y - it.p2.y) +
                 it.p1.x * (it.p2.y - it.p0.y) +
                 it.p2.x * (it.p0.y - it.p1.y)) * 0.5);
            double scale = (raw_area > 1e-12) ? std::clamp(it.area / raw_area, 0.0, 1.0) : 1.0;

            double t_cut = 0.0, t_fill = 0.0;
            Triangle tri;
            tri.p0 = it.p0; tri.p1 = it.p1; tri.p2 = it.p2;
            tri.p0.design_z = h; tri.p1.design_z = h; tri.p2.design_z = h;
            tri.p0.delta_z = dz0; tri.p1.delta_z = dz1; tri.p2.delta_z = dz2;
            tri.area = it.area;

            const bool pos = (dz0 > 0.0 || dz1 > 0.0 || dz2 > 0.0);
            const bool neg = (dz0 < 0.0 || dz1 < 0.0 || dz2 < 0.0);
            if (!(pos && neg)) {
                double vol = it.area * (dz0 + dz1 + dz2) / 3.0;
                if (vol >= 0.0) t_cut = vol;
                else t_fill = -vol;
            } else {
                std::vector<XY> poly = {{it.p0.x, it.p0.y}, {it.p1.x, it.p1.y}, {it.p2.x, it.p2.y}};
                std::vector<double> vals = {dz0, dz1, dz2};
                auto [cp, cv] = clip_by_sign(poly, vals, true);
                auto [fp, fv] = clip_by_sign(poly, vals, false);
                auto [_ca, cvol] = polygon_integral(cp, cv);
                auto [_fa, fvol] = polygon_integral(fp, fv);
                (void)_ca; (void)_fa;
                t_cut = std::max(0.0, cvol) * scale;
                t_fill = std::max(0.0, -fvol) * scale;
                if (out_tris) {
                    tri.cut_polygon = std::move(cp);
                    tri.fill_polygon = std::move(fp);
                }
            }
            tri.cut_volume = t_cut;
            tri.fill_volume = t_fill;
            out_cut += t_cut;
            out_fill += t_fill;
            if (out_tris) out_tris->push_back(std::move(tri));
        }
    };

    auto solve_subset_balance = [&](const std::vector<size_t>& idxs,
                                    double& out_h, double& out_cut, double& out_fill) {
        double lo = 1e30, hi = -1e30;
        for (size_t idx : idxs) {
            lo = std::min(lo, items[idx].zmin);
            hi = std::max(hi, items[idx].zmax);
        }
        if (hi - lo < 1e-6) {
            out_h = lo;
            eval_subset_vol(idxs, out_h, out_cut, out_fill, nullptr);
            return;
        }
        double compact = std::max(0.5, sf.compact);
        for (int it = 0; it < 28; ++it) {
            double mid = 0.5 * (lo + hi);
            double c = 0.0, f = 0.0;
            eval_subset_vol(idxs, mid, c, f, nullptr);
            double surplus = c - f / compact;
            if (surplus > 0.0) lo = mid;
            else hi = mid;
        }
        out_h = 0.5 * (lo + hi);
        eval_subset_vol(idxs, out_h, out_cut, out_fill, nullptr);
    };

    bool found_better = false;
    double best_score_cost = std::numeric_limits<double>::infinity();
    std::vector<size_t> side1, side2;
    side1.reserve(items.size());
    side2.reserve(items.size());

    for (int axis = 0; axis < 2; ++axis) {
        double umin = (axis == 0) ? xmin : ymin;
        double umax = (axis == 0) ? xmax : ymax;
        double perp_span = (axis == 0) ? (ymax - ymin) : (xmax - xmin);
        if (umax - umin < 1.0 || perp_span < 1.0) continue;

        for (int s = 0; s < 9; ++s) {
            double frac = 0.30 + 0.05 * static_cast<double>(s);
            double split = umin + frac * (umax - umin);
            side1.clear();
            side2.clear();
            double area1 = 0.0, area2 = 0.0;
            double band = 0.12 * (umax - umin);
            double p_lo = 1e30, p_hi = -1e30;

            for (size_t i = 0; i < items.size(); ++i) {
                double u = (axis == 0) ? items[i].cx : items[i].cy;
                double v = (axis == 0) ? items[i].cy : items[i].cx;
                if (std::abs(u - split) <= band) {
                    p_lo = std::min(p_lo, v);
                    p_hi = std::max(p_hi, v);
                }
                if (u <= split) {
                    side1.push_back(i);
                    area1 += items[i].area;
                } else {
                    side2.push_back(i);
                    area2 += items[i].area;
                }
            }
            if (side1.size() < 3 || side2.size() < 3) continue;
            if (area1 < 0.15 * total_area || area2 < 0.15 * total_area) continue;

            double split_len = (p_hi > p_lo) ? std::min(perp_span, (p_hi - p_lo)) : 0.85 * perp_span;
            split_len = std::max(1.0, split_len);

            double h1 = 0.0, c1 = 0.0, f1 = 0.0;
            double h2 = 0.0, c2 = 0.0, f2 = 0.0;
            solve_subset_balance(side1, h1, c1, f1);
            solve_subset_balance(side2, h2, c2, f2);

            CalculationResult step_res;
            step_res.soil_balance.factors = sf;
            step_res.triangles.reserve(items.size());
            double check_c1 = 0.0, check_f1 = 0.0, check_c2 = 0.0, check_f2 = 0.0;
            eval_subset_vol(side1, h1, check_c1, check_f1, &step_res.triangles);
            eval_subset_vol(side2, h2, check_c2, check_f2, &step_res.triangles);
            step_res.total_cut = check_c1 + check_c2;
            step_res.total_fill = check_f1 + check_f2;
            step_res.net_volume = step_res.total_cut - step_res.total_fill;
            step_res.soil_balance = SoilBalance{step_res.total_cut, step_res.total_fill, sf};

            HaulPlanResult haul_step = compute_haul_plan(step_res, 4, 3, costs);
            double dh = std::abs(h1 - h2);
            double wall_cost = split_len * dh * best.wall_cost_per_m2;
            double total_c = haul_step.estimated_cost + wall_cost;

            if (total_c < best_score_cost) {
                best_score_cost = total_c;
                found_better = true;
                best.split_axis = axis;
                best.split_coord = split;
                best.split_length = split_len;
                best.bench1_elevation = h1;
                best.bench2_elevation = h2;
                best.bench1_cut = check_c1;
                best.bench1_fill = check_f1;
                best.bench2_cut = check_c2;
                best.bench2_fill = check_f2;
                best.step_height = dh;
                best.step_wall_cost = wall_cost;
                best.earthwork_cost = haul_step.estimated_cost;
                best.total_cost = total_c;
            }
        }
    }

    if (found_better) {
        best.savings_vs_single = best.single_pad_cost - best.total_cost;
        best.savings_pct = (best.single_pad_cost > 1e-6)
                               ? (best.savings_vs_single / best.single_pad_cost * 100.0)
                               : 0.0;
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(2);
    if (!found_better || best.savings_vs_single <= 0.0) {
        oss << "推荐维持单平场 (H0=" << best.single_pad_elevation << " m，造价 ¥"
            << std::setprecision(0) << best.single_pad_cost
            << "；双级台阶加支护成本反而高出 ¥" << std::max(0.0, -best.savings_vs_single) << ")";
    } else {
        oss << "建议沿" << (best.split_axis == 0 ? "X=" : "Y=") << best.split_coord
            << "m 分两级台阶（H1=" << best.bench1_elevation << " m, H2=" << best.bench2_elevation
            << " m, 级差 " << best.step_height << " m），含挡墙总造价 ¥"
            << std::setprecision(0) << best.total_cost
            << "，较单平场节省 ¥" << best.savings_vs_single
            << " (" << std::setprecision(1) << best.savings_pct << "%)";
    }
    best.summary = oss.str();
    return best;
}

FleetEstimateResult estimate_earthwork_fleet(
    const CalculationResult& calc_res,
    const HaulPlanResult* haul,
    const FleetParams& params) {
    FleetEstimateResult out;
    double loose = std::max(1.0, calc_res.soil_balance.factors.loose);
    out.loose_cut_volume = calc_res.total_cut * loose;
    out.compact_fill_volume = calc_res.total_fill;

    double equiv_nat = std::max(calc_res.total_cut, 0.35 * calc_res.total_fill);
    if (equiv_nat <= 1e-6) {
        out.summary = "当前挖填方量为 0，无需机械排班";
        return out;
    }

    double avg_dist = 120.0;
    if (haul) {
        double moved = haul->cross_zone_hauled + haul->export_surplus + haul->borrow_deficit;
        if (moved > 1e-6) {
            avg_dist = (haul->cross_zone_hauled * haul->avg_haul_distance +
                        (haul->export_surplus + haul->borrow_deficit) * 800.0) / moved;
        } else if (haul->internal_balanced > 1e-6) {
            avg_dist = 45.0;
        }
    }
    out.avg_haul_dist_m = std::clamp(avg_dist, 25.0, 5000.0);

    double speed_m_min = std::max(50.0, params.truck_speed_kmh * 1000.0 / 60.0);
    double work_h = std::max(1.0, params.work_hours_per_day);
    double truck_cap = std::max(2.0, params.truck_capacity_m3);
    out.truck_cycle_min = std::max(2.0, params.load_dump_wait_min + (2.0 * out.avg_haul_dist_m) / speed_m_min);
    out.truck_daily_cap_m3 = ((work_h * 60.0) / out.truck_cycle_min) * truck_cap;

    double exc_daily_nat = std::max(10.0, params.exc_rate_m3_per_h) * work_h;
    double target_d = std::max(1.0, params.target_days);
    int max_exc = std::max(1, params.max_excavators);

    out.excavators_needed = std::clamp(
        static_cast<int>(std::ceil(equiv_nat / (exc_daily_nat * target_d))),
        1, max_exc);

    double exc_days = std::max(0.5, equiv_nat / (static_cast<double>(out.excavators_needed) * exc_daily_nat));
    double loose_need_per_day = (equiv_nat * loose) / exc_days;
    out.trucks_needed = std::max(1, static_cast<int>(std::ceil(loose_need_per_day / out.truck_daily_cap_m3)));
    out.compaction_sets = std::max(1, static_cast<int>(std::ceil((calc_res.total_fill / exc_days) / 1800.0)));

    double raw_days = std::max(
        equiv_nat / (static_cast<double>(out.excavators_needed) * exc_daily_nat),
        (equiv_nat * loose) / (static_cast<double>(out.trucks_needed) * out.truck_daily_cap_m3));
    out.estimated_days = std::max(0.5, std::ceil(raw_days * 10.0) / 10.0);
    out.daily_peak_m3 = (calc_res.total_cut + calc_res.total_fill) / out.estimated_days;
    out.total_machine_cost = out.estimated_days *
                             (out.excavators_needed * params.exc_daily_rent +
                              out.trucks_needed * params.truck_daily_rent +
                              out.compaction_sets * params.dozer_daily_rent);

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(1)
        << "建议配置 " << out.excavators_needed << " 台挖机 + " << out.trucks_needed
        << " 辆自卸车 + " << out.compaction_sets << " 套压实机组，综合运距 "
        << std::setprecision(0) << out.avg_haul_dist_m << " m，预计工期 "
        << std::setprecision(1) << out.estimated_days << " 天，机械台班费约 ¥"
        << std::setprecision(0) << out.total_machine_cost;
    out.summary = oss.str();
    return out;
}

GradeParetoResult compute_grade_pareto_frontier(
    const TINEarthworkCalculator& base_calc,
    const HaulCostParams& costs,
    const GradeOptConstraints& constraints) {
    if (base_calc.points().size() < 3) {
        throw std::runtime_error("至少需要 3 个测量点才能生成多目标 Pareto 前沿");
    }

    TINEarthworkCalculator work = base_calc;
    if (!work.has_tin()) work.build_tin();

    GradeOptConstraints sub_cons = constraints;
    sub_cons.max_evals = std::clamp(constraints.max_evals, 40, 80);
    const int cols = std::clamp(constraints.haul_cols, 1, 8);
    const int rows = std::clamp(constraints.haul_rows, 1, 8);

    std::vector<std::pair<std::string, PlaneDesign>> cands;

    // 1. 成本最优
    auto r_cost = optimize_grade_cost(work, costs, sub_cons);
    cands.push_back({"成本最优", r_cost.optimized.plane});

    // 2. 最小总方量（弱化运距与外运偏差，专注削减挖填总量）
    HaulCostParams vol_costs{100.0, 100.0, 0.0, 0.0, 0.0};
    auto r_vol = optimize_grade_cost(work, vol_costs, sub_cons);
    cands.push_back({"最小总方量", r_vol.optimized.plane});

    // 3. 零外运/借土严格自平衡
    HaulCostParams bal_costs = costs;
    bal_costs.export_unit_cost = std::max(costs.export_unit_cost * 12.0, 450.0);
    bal_costs.borrow_unit_cost = std::max(costs.borrow_unit_cost * 12.0, 500.0);
    auto r_bal = optimize_grade_cost(work, bal_costs, sub_cons);
    cands.push_back({"严格自平衡", r_bal.optimized.plane});

    // 4. 最短场内运距优先
    HaulCostParams haul_costs = costs;
    haul_costs.haul_per_m3_100m = std::max(costs.haul_per_m3_100m * 15.0, 60.0);
    auto r_haul = optimize_grade_cost(work, haul_costs, sub_cons);
    cands.push_back({"短距低运功", r_haul.optimized.plane});

    // 5. 平场平衡 & 最小二乘基线（以及折中插值候选，丰富前沿权衡点）
    cands.push_back({"最小二乘斜面", r_cost.best_fit.plane});
    cands.push_back({"平场零平衡", r_cost.flat_balance.plane});

    const PlaneDesign& pc = r_cost.optimized.plane;
    const PlaneDesign& pv = r_vol.optimized.plane;
    for (double a : {0.25, 0.50, 0.75}) {
        PlaneDesign blend{
            pc.x0, pc.y0,
            (1.0 - a) * pc.h0 + a * pv.h0,
            (1.0 - a) * pc.slope_x + a * pv.slope_x,
            (1.0 - a) * pc.slope_y + a * pv.slope_y
        };
        cands.push_back({"均衡过渡", blend});
    }
    const PlaneDesign& pb = r_bal.optimized.plane;
    for (double a : {0.35, 0.70}) {
        PlaneDesign blend{
            pc.x0, pc.y0,
            (1.0 - a) * pc.h0 + a * pb.h0,
            (1.0 - a) * pc.slope_x + a * pb.slope_x,
            (1.0 - a) * pc.slope_y + a * pb.slope_y
        };
        cands.push_back({"费用平衡折中", blend});
    }

    auto is_feasible = [&](const PlaneDesign& pl) -> bool {
        if (std::abs(pl.slope_x) > std::abs(constraints.max_slope_x) + 1e-4 ||
            std::abs(pl.slope_y) > std::abs(constraints.max_slope_y) + 1e-4) {
            return false;
        }
        if (constraints.min_drain_slope_pct > 1e-6 &&
            std::hypot(pl.slope_x, pl.slope_y) + 1e-4 < constraints.min_drain_slope_pct) {
            return false;
        }
        for (const auto& cp : constraints.control_points) {
            if (std::abs(pl.elevation(cp.x, cp.y) - cp.target_z) > cp.max_dev_m + 1e-3) {
                return false;
            }
        }
        return true;
    };

    std::vector<ParetoPlan> evaled;
    evaled.reserve(cands.size());
    for (const auto& [tag, pl] : cands) {
        if (!is_feasible(pl)) continue;
        work.set_design_plane(pl);
        CalculationResult res = work.calculate_volumes();
        HaulPlanResult haul = compute_haul_plan(res, cols, rows, costs);

        double max_c = 0.0, max_f = 0.0;
        for (const auto& t : res.triangles) {
            if (t.is_boundary) continue;
            max_c = std::max({max_c, t.p0.delta_z, t.p1.delta_z, t.p2.delta_z});
            max_f = std::max({max_f, -t.p0.delta_z, -t.p1.delta_z, -t.p2.delta_z});
        }

        ParetoPlan pp;
        pp.tag = tag;
        pp.summary.label = tag;
        pp.summary.plane = pl;
        pp.summary.cut_volume = res.total_cut;
        pp.summary.fill_volume = res.total_fill;
        pp.summary.haul_work_m3_m = haul.total_work_m3_m;
        pp.summary.export_surplus = haul.export_surplus;
        pp.summary.borrow_deficit = haul.borrow_deficit;
        pp.summary.total_cost = haul.estimated_cost;
        pp.total_moved_volume = res.total_cut + res.total_fill;
        pp.max_cut_depth = max_c;
        pp.max_fill_depth = max_f;
        pp.slope_pct = std::hypot(pl.slope_x, pl.slope_y);
        evaled.push_back(std::move(pp));
    }

    // 在三维目标 (total_cost, total_moved_volume, max(max_cut_depth, max_fill_depth)) 上筛选非支配解
    std::vector<ParetoPlan> frontier;
    for (size_t i = 0; i < evaled.size(); ++i) {
        bool dominated = false;
        double dmax_i = std::max(evaled[i].max_cut_depth, evaled[i].max_fill_depth);
        for (size_t j = 0; j < evaled.size(); ++j) {
            if (i == j) continue;
            double dmax_j = std::max(evaled[j].max_cut_depth, evaled[j].max_fill_depth);
            bool le = (evaled[j].summary.total_cost <= evaled[i].summary.total_cost + 1e-6) &&
                      (evaled[j].total_moved_volume <= evaled[i].total_moved_volume + 1e-6) &&
                      (dmax_j <= dmax_i + 1e-6);
            bool lt = (evaled[j].summary.total_cost < evaled[i].summary.total_cost - 1e-3) ||
                      (evaled[j].total_moved_volume < evaled[i].total_moved_volume - 1e-3) ||
                      (dmax_j < dmax_i - 1e-4);
            if (le && lt) {
                dominated = true;
                break;
            }
        }
        if (!dominated) {
            bool dup = false;
            for (const auto& ex : frontier) {
                if (std::abs(ex.summary.total_cost - evaled[i].summary.total_cost) < 1e-2 &&
                    std::abs(ex.total_moved_volume - evaled[i].total_moved_volume) < 1e-2) {
                    dup = true;
                    break;
                }
            }
            if (!dup) frontier.push_back(evaled[i]);
        }
    }

    std::sort(frontier.begin(), frontier.end(), [](const ParetoPlan& a, const ParetoPlan& b) {
        if (std::abs(a.summary.total_cost - b.summary.total_cost) > 1e-3) {
            return a.summary.total_cost < b.summary.total_cost;
        }
        return a.total_moved_volume < b.total_moved_volume;
    });

    GradeParetoResult out;
    out.plans = std::move(frontier);
    std::ostringstream oss;
    oss << "已生成 " << out.plans.size() << " 组多目标非支配 Pareto 设计方案";
    if (!out.plans.empty()) {
        oss << std::fixed << std::setprecision(0)
            << "（最低造价 ¥" << out.plans.front().summary.total_cost
            << " / 最小总方量 " << std::min_element(out.plans.begin(), out.plans.end(),
                   [](const ParetoPlan& a, const ParetoPlan& b) {
                       return a.total_moved_volume < b.total_moved_volume;
                   })->total_moved_volume << " m³）";
    }
    out.summary = oss.str();
    return out;
}

} // namespace tin
