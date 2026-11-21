#include "core/engineering.hpp"
#include "core/geometry.hpp"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>

namespace tin {

namespace {

std::vector<double> eval_plane_on_poly(
    const std::vector<XY>& poly,
    const XY& p0, const XY& p1, const XY& p2,
    double v0, double v1, double v2) {
    std::vector<double> vals(poly.size(), 0.0);
    auto pl = linear_plane(p0, p1, p2, v0, v1, v2, 1e-18);
    if (!pl.has_value()) {
        double avg = (v0 + v1 + v2) / 3.0;
        std::fill(vals.begin(), vals.end(), avg);
        return vals;
    }
    auto [a, b, c] = *pl;
    for (size_t i = 0; i < poly.size(); ++i) {
        vals[i] = a * poly[i].x + b * poly[i].y + c;
    }
    return vals;
}

double integrate_above(const std::vector<XY>& poly, const std::vector<double>& vals, double threshold) {
    if (poly.size() < 3) return 0.0;
    std::vector<double> shifted(vals.size());
    bool any_pos = false;
    for (size_t i = 0; i < vals.size(); ++i) {
        shifted[i] = vals[i] - threshold;
        if (shifted[i] > 0.0) any_pos = true;
    }
    if (!any_pos) return 0.0;
    auto [clipped, cvals] = clip_by_sign(poly, shifted, true);
    if (clipped.size() < 3) return 0.0;
    auto [area, vol] = polygon_integral(clipped, cvals);
    (void)area;
    return std::max(0.0, vol);
}

XY polygon_centroid(const std::vector<XY>& poly) {
    if (poly.empty()) return {0.0, 0.0};
    double sx = 0.0, sy = 0.0;
    for (const auto& p : poly) {
        sx += p.x;
        sy += p.y;
    }
    double inv = 1.0 / static_cast<double>(poly.size());
    return {sx * inv, sy * inv};
}

std::string fmt1(double v) {
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(1) << v;
    return oss.str();
}

} // namespace

StrataResult compute_strata_volumes(
    const CalculationResult& tin_result,
    StrataMode mode,
    const std::vector<double>& boundaries_in,
    const std::vector<std::string>& layer_names) {

    std::vector<double> bnds;
    for (double b : boundaries_in) {
        if (!std::isfinite(b)) continue;
        if (mode == StrataMode::ByDepth && b <= 1e-9) continue;
        bnds.push_back(b);
    }
    std::sort(bnds.begin(), bnds.end());
    bnds.erase(std::unique(bnds.begin(), bnds.end(), [](double a, double b) {
        return std::abs(a - b) < 1e-9;
    }), bnds.end());

    const size_t n_layers = bnds.size() + 1;
    const double inf = std::numeric_limits<double>::infinity();

    StrataResult out;
    out.mode = mode;
    out.boundaries = bnds;
    out.total_cut = tin_result.total_cut;
    out.total_fill = tin_result.total_fill;
    out.layers.resize(n_layers);

    for (size_t i = 0; i < n_layers; ++i) {
        auto& lr = out.layers[i];
        if (mode == StrataMode::ByDepth) {
            lr.lower_bound = (i == 0) ? 0.0 : bnds[i - 1];
            lr.upper_bound = (i + 1 == n_layers) ? inf : bnds[i];
            if (i < layer_names.size() && !layer_names[i].empty()) {
                lr.name = layer_names[i];
            } else if (i + 1 == n_layers) {
                lr.name = "深层 > " + fmt1(lr.lower_bound) + " m";
            } else {
                lr.name = "深度 " + fmt1(lr.lower_bound) + " ~ " + fmt1(lr.upper_bound) + " m";
            }
        } else {
            lr.lower_bound = (i == 0) ? -inf : bnds[i - 1];
            lr.upper_bound = (i + 1 == n_layers) ? inf : bnds[i];
            if (i < layer_names.size() && !layer_names[i].empty()) {
                lr.name = layer_names[i];
            } else if (i == 0) {
                lr.name = "标高 < " + fmt1(lr.upper_bound) + " m";
            } else if (i + 1 == n_layers) {
                lr.name = "标高 >= " + fmt1(lr.lower_bound) + " m";
            } else {
                lr.name = "标高 " + fmt1(lr.lower_bound) + " ~ " + fmt1(lr.upper_bound) + " m";
            }
        }
    }

    for (const auto& t : tin_result.triangles) {
        if (t.is_boundary || t.area <= 0.0) continue;
        XY p0{t.p0.x, t.p0.y};
        XY p1{t.p1.x, t.p1.y};
        XY p2{t.p2.x, t.p2.y};
        std::vector<XY> full_tri = {p0, p1, p2};

        auto accumulate_part = [&](const std::vector<XY>& poly, double target_vol, bool is_cut) {
            if (target_vol <= 1e-12 || poly.size() < 3) return;
            auto z_nat = eval_plane_on_poly(poly, p0, p1, p2, t.p0.z, t.p1.z, t.p2.z);
            auto z_des = eval_plane_on_poly(poly, p0, p1, p2, t.p0.design_z, t.p1.design_z, t.p2.design_z);

            std::vector<double> layer_vols(n_layers, 0.0);
            if (mode == StrataMode::ByDepth) {
                std::vector<double> depth(poly.size());
                for (size_t k = 0; k < poly.size(); ++k) {
                    depth[k] = is_cut ? (z_nat[k] - z_des[k]) : (z_des[k] - z_nat[k]);
                }
                std::vector<double> above(bnds.size() + 1, 0.0);
                above[0] = integrate_above(poly, depth, 0.0);
                for (size_t b = 0; b < bnds.size(); ++b) {
                    above[b + 1] = integrate_above(poly, depth, bnds[b]);
                }
                for (size_t i = 0; i < n_layers; ++i) {
                    double hi_v = (i + 1 < above.size()) ? above[i + 1] : 0.0;
                    layer_vols[i] = std::max(0.0, above[i] - hi_v);
                }
            } else {
                // 精确伸缩恒等式：在挖方多边形(z_nat >= z_des)上，每高程带体积 = ∫(F(z_nat) - F(z_des)) dA
                const auto& z_hi_surf = is_cut ? z_nat : z_des;
                const auto& z_lo_surf = is_cut ? z_des : z_nat;
                std::vector<double> diff_total(poly.size());
                for (size_t k = 0; k < poly.size(); ++k) {
                    diff_total[k] = std::max(0.0, z_hi_surf[k] - z_lo_surf[k]);
                }
                double v_tot = integrate_above(poly, diff_total, 0.0);
                std::vector<double> above_b(bnds.size(), 0.0);
                for (size_t b = 0; b < bnds.size(); ++b) {
                    double a_hi = integrate_above(poly, z_hi_surf, bnds[b]);
                    double a_lo = integrate_above(poly, z_lo_surf, bnds[b]);
                    above_b[b] = std::max(0.0, a_hi - a_lo);
                }
                for (size_t i = 0; i < n_layers; ++i) {
                    double v_ge_lo = (i == 0) ? v_tot : above_b[i - 1];
                    double v_ge_hi = (i + 1 == n_layers) ? 0.0 : above_b[i];
                    layer_vols[i] = std::max(0.0, v_ge_lo - v_ge_hi);
                }
            }

            double sum_lv = 0.0;
            for (double v : layer_vols) sum_lv += v;
            double scale = (sum_lv > 1e-12) ? (target_vol / sum_lv) : 0.0;
            for (size_t i = 0; i < n_layers; ++i) {
                if (is_cut) out.layers[i].cut_volume += layer_vols[i] * scale;
                else out.layers[i].fill_volume += layer_vols[i] * scale;
            }
        };

        if (t.cut_volume > 1e-12) {
            const auto& cpoly = !t.cut_polygon.empty() ? t.cut_polygon : full_tri;
            accumulate_part(cpoly, t.cut_volume, true);
        }
        if (t.fill_volume > 1e-12) {
            const auto& fpoly = !t.fill_polygon.empty() ? t.fill_polygon : full_tri;
            accumulate_part(fpoly, t.fill_volume, false);
        }
    }

    for (auto& lr : out.layers) {
        lr.cut_ratio = (out.total_cut > 1e-9) ? (lr.cut_volume / out.total_cut) : 0.0;
        lr.fill_ratio = (out.total_fill > 1e-9) ? (lr.fill_volume / out.total_fill) : 0.0;
    }
    return out;
}

HaulPlanResult compute_haul_plan(
    const CalculationResult& tin_result,
    int grid_cols,
    int grid_rows,
    const HaulCostParams& cost_params) {

    grid_cols = std::clamp(grid_cols, 1, 12);
    grid_rows = std::clamp(grid_rows, 1, 12);

    HaulPlanResult out;
    out.grid_cols = grid_cols;
    out.grid_rows = grid_rows;
    out.total_cut = tin_result.total_cut;
    out.total_fill = tin_result.total_fill;

    double xmin = 1e30, xmax = -1e30, ymin = 1e30, ymax = -1e30;
    bool any_tri = false;
    for (const auto& t : tin_result.triangles) {
        if (t.is_boundary || t.area <= 0.0) continue;
        any_tri = true;
        for (const auto* p : {&t.p0, &t.p1, &t.p2}) {
            xmin = std::min(xmin, p->x);
            xmax = std::max(xmax, p->x);
            ymin = std::min(ymin, p->y);
            ymax = std::max(ymax, p->y);
        }
    }
    if (!any_tri) return out;

    double dx = std::max(xmax - xmin, 1e-6);
    double dy = std::max(ymax - ymin, 1e-6);
    double compact = std::max(tin_result.soil_balance.factors.compact, 1e-6);

    struct ZoneAcc {
        double cut = 0.0, fill = 0.0;
        double cx = 0.0, cy = 0.0;
        double fx = 0.0, fy = 0.0;
    };
    std::vector<ZoneAcc> acc(static_cast<size_t>(grid_rows * grid_cols));

    auto locate_zone = [&](const XY& pt) -> int {
        int c = static_cast<int>(std::floor((pt.x - xmin) / dx * grid_cols));
        int r = static_cast<int>(std::floor((pt.y - ymin) / dy * grid_rows));
        c = std::clamp(c, 0, grid_cols - 1);
        r = std::clamp(r, 0, grid_rows - 1);
        return r * grid_cols + c;
    };

    for (const auto& t : tin_result.triangles) {
        if (t.is_boundary || t.area <= 0.0) continue;
        XY tri_c{(t.p0.x + t.p1.x + t.p2.x) / 3.0, (t.p0.y + t.p1.y + t.p2.y) / 3.0};
        if (t.cut_volume > 1e-12) {
            XY c = !t.cut_polygon.empty() ? polygon_centroid(t.cut_polygon) : tri_c;
            int idx = locate_zone(c);
            acc[idx].cut += t.cut_volume;
            acc[idx].cx += t.cut_volume * c.x;
            acc[idx].cy += t.cut_volume * c.y;
        }
        if (t.fill_volume > 1e-12) {
            XY c = !t.fill_polygon.empty() ? polygon_centroid(t.fill_polygon) : tri_c;
            int idx = locate_zone(c);
            acc[idx].fill += t.fill_volume;
            acc[idx].fx += t.fill_volume * c.x;
            acc[idx].fy += t.fill_volume * c.y;
        }
    }

    for (int r = 0; r < grid_rows; ++r) {
        for (int c = 0; c < grid_cols; ++c) {
            int idx = r * grid_cols + c;
            const auto& a = acc[idx];
            if (a.cut <= 1e-9 && a.fill <= 1e-9) continue;
            HaulZone z;
            z.id = static_cast<int>(out.zones.size());
            z.row = r + 1;
            z.col = c + 1;
            z.name = "Z" + std::to_string(r + 1) + "-" + std::to_string(c + 1);
            z.cut_volume = a.cut;
            z.fill_volume = a.fill;
            double fill_nat = a.fill / compact;
            z.internal_balance = std::min(a.cut, fill_nat);
            z.net_surplus = a.cut - fill_nat;
            z.cut_centroid = (a.cut > 1e-9) ? XY{a.cx / a.cut, a.cy / a.cut}
                                            : XY{xmin + (c + 0.5) * dx / grid_cols, ymin + (r + 0.5) * dy / grid_rows};
            z.fill_centroid = (a.fill > 1e-9) ? XY{a.fx / a.fill, a.fy / a.fill}
                                              : XY{xmin + (c + 0.5) * dx / grid_cols, ymin + (r + 0.5) * dy / grid_rows};
            out.internal_balanced += z.internal_balance;
            out.zones.push_back(std::move(z));
        }
    }

    // 供需分区列表（自然方）
    std::vector<int> sup_ids, dem_ids;
    std::vector<double> sup_rem, dem_rem;
    for (const auto& z : out.zones) {
        if (z.net_surplus > 1e-6) {
            sup_ids.push_back(z.id);
            sup_rem.push_back(z.net_surplus);
        } else if (z.net_surplus < -1e-6) {
            dem_ids.push_back(z.id);
            dem_rem.push_back(-z.net_surplus);
        }
    }

    const size_t ns = sup_ids.size();
    const size_t nd = dem_ids.size();
    std::vector<std::vector<double>> dist(ns, std::vector<double>(nd, 0.0));
    std::vector<std::vector<double>> alloc(ns, std::vector<double>(nd, 0.0));

    struct PairDist { size_t s, d; double dist; };
    std::vector<PairDist> pairs;
    pairs.reserve(ns * nd);
    for (size_t s = 0; s < ns; ++s) {
        const auto& zs = out.zones[sup_ids[s]];
        for (size_t d = 0; d < nd; ++d) {
            const auto& zd = out.zones[dem_ids[d]];
            double dd = std::hypot(zs.cut_centroid.x - zd.fill_centroid.x,
                                   zs.cut_centroid.y - zd.fill_centroid.y);
            dist[s][d] = dd;
            pairs.push_back({s, d, dd});
        }
    }
    std::sort(pairs.begin(), pairs.end(), [](const PairDist& a, const PairDist& b) {
        return a.dist < b.dist;
    });

    // 第一阶段：最近运距贪心初始可行解
    for (const auto& pd : pairs) {
        double v = std::min(sup_rem[pd.s], dem_rem[pd.d]);
        if (v > 1e-9) {
            alloc[pd.s][pd.d] += v;
            sup_rem[pd.s] -= v;
            dem_rem[pd.d] -= v;
        }
    }

    // 第二阶段：2-opt 交叉路线优化（严格单调降低总运距功 ∑ V_ij * d_ij）
    for (int iter = 0; iter < 64; ++iter) {
        bool improved = false;
        for (size_t s1 = 0; s1 < ns; ++s1) {
            for (size_t d1 = 0; d1 < nd; ++d1) {
                if (alloc[s1][d1] <= 1e-9) continue;
                for (size_t s2 = s1 + 1; s2 < ns; ++s2) {
                    for (size_t d2 = 0; d2 < nd; ++d2) {
                        if (d2 == d1 || alloc[s2][d2] <= 1e-9) continue;
                        double cur_cost = dist[s1][d1] + dist[s2][d2];
                        double alt_cost = dist[s1][d2] + dist[s2][d1];
                        if (alt_cost + 1e-7 < cur_cost) {
                            double mv = std::min(alloc[s1][d1], alloc[s2][d2]);
                            alloc[s1][d1] -= mv;
                            alloc[s2][d2] -= mv;
                            alloc[s1][d2] += mv;
                            alloc[s2][d1] += mv;
                            improved = true;
                        }
                    }
                }
            }
        }
        if (!improved) break;
    }

    for (size_t s = 0; s < ns; ++s) {
        out.export_surplus += sup_rem[s];
        const auto& zs = out.zones[sup_ids[s]];
        for (size_t d = 0; d < nd; ++d) {
            if (alloc[s][d] <= 1e-6) continue;
            const auto& zd = out.zones[dem_ids[d]];
            HaulFlow fl;
            fl.from_zone_id = zs.id;
            fl.to_zone_id = zd.id;
            fl.from_name = zs.name;
            fl.to_name = zd.name;
            fl.from_xy = zs.cut_centroid;
            fl.to_xy = zd.fill_centroid;
            fl.volume = alloc[s][d];
            fl.distance = dist[s][d];
            out.cross_zone_hauled += fl.volume;
            out.total_work_m3_m += fl.volume * fl.distance;
            out.flows.push_back(std::move(fl));
        }
    }
    for (size_t d = 0; d < nd; ++d) {
        out.borrow_deficit += dem_rem[d];
    }

    std::sort(out.flows.begin(), out.flows.end(), [](const HaulFlow& a, const HaulFlow& b) {
        return a.volume > b.volume;
    });

    if (out.cross_zone_hauled > 1e-9) {
        out.avg_haul_distance = out.total_work_m3_m / out.cross_zone_hauled;
    }

    double loose = tin_result.soil_balance.factors.loose;
    out.estimated_cost =
        out.total_cut * cost_params.cut_unit_cost +
        out.total_fill * cost_params.fill_unit_cost +
        (out.total_work_m3_m / 100.0) * cost_params.haul_per_m3_100m +
        (out.export_surplus * loose) * cost_params.export_unit_cost +
        out.borrow_deficit * cost_params.borrow_unit_cost;

    return out;
}

SlopeGradingResult compute_slope_grading(
    const TINEarthworkCalculator& calculator,
    double cut_ratio,
    double fill_ratio,
    double sample_step) {

    if (cut_ratio <= 0.0 || fill_ratio <= 0.0) {
        throw std::runtime_error("挖方/填方放坡坡比必须大于 0");
    }
    if (!calculator.has_tin()) {
        throw std::runtime_error("请先构建 TIN 三角网再执行放坡设计");
    }
    sample_step = std::max(0.5, sample_step);

    std::vector<XY> ring = calculator.boundary().size() >= 3
                               ? normalize_ring(calculator.boundary())
                               : normalize_ring(calculator.coverage_polygon());
    if (ring.size() < 3) {
        throw std::runtime_error("缺少有效平台边界，无法进行周边放坡");
    }
    // 确保逆时针方向（内部在左侧，外法向量为 (dy, -dx)）
    if (signed_area(ring) < 0.0) {
        std::reverse(ring.begin(), ring.end());
    }

    SlopeGradingResult out;
    out.cut_ratio = cut_ratio;
    out.fill_ratio = fill_ratio;
    out.platform_ring = ring;

    struct EdgeSample {
        XY base;
        XY daylight;
        double width = 0.0;
        double height = 0.0; // >0 挖方边坡, <0 填方边坡
    };

    const size_t n = ring.size();
    int tick_counter = 0;

    for (size_t i = 0; i < n; ++i) {
        XY a = ring[i];
        XY b = ring[(i + 1) % n];
        double ex = b.x - a.x;
        double ey = b.y - a.y;
        double elen = std::hypot(ex, ey);
        if (elen <= 1e-6) continue;
        out.perimeter += elen;

        XY normal{ey / elen, -ex / elen}; // 指向平台外侧
        int segs = std::max(1, static_cast<int>(std::ceil(elen / sample_step)));
        std::vector<EdgeSample> samples(static_cast<size_t>(segs + 1));

        for (int k = 0; k <= segs; ++k) {
            double t = static_cast<double>(k) / static_cast<double>(segs);
            XY p{a.x + t * ex, a.y + t * ey};
            auto [zn0, zd0, _ok0] = calculator.sample_surface(p.x, p.y, true);
            (void)_ok0;
            if (!std::isfinite(zn0) || !std::isfinite(zd0)) {
                samples[k] = {p, p, 0.0, 0.0};
                continue;
            }
            double h0 = zn0 - zd0;
            bool is_cut = (h0 >= 0.0);
            double m = is_cut ? cut_ratio : fill_ratio;
            double w = std::abs(h0) * m;
            double h_eff = h0;

            // 沿外法向迭代求解坡面与自然地面的交线（最多 6 次阻尼不动点迭代）
            for (int it = 0; it < 6 && w > 1e-4; ++it) {
                XY q{p.x + normal.x * w, p.y + normal.y * w};
                auto [zn_q, _zd_q, _ok_q] = calculator.sample_surface(q.x, q.y, true);
                (void)_zd_q;
                (void)_ok_q;
                if (!std::isfinite(zn_q)) break;
                double dh = is_cut ? (zn_q - zd0) : (zd0 - zn_q);
                double w_next = std::clamp(std::max(0.0, dh) * m, 0.0, 200.0);
                w = 0.5 * w + 0.5 * w_next;
                h_eff = is_cut ? std::max(0.0, dh) : -std::max(0.0, dh);
            }

            XY dl{p.x + normal.x * w, p.y + normal.y * w};
            samples[k] = {p, dl, w, h_eff};
            out.max_slope_width = std::max(out.max_slope_width, w);
        }

        for (int k = 0; k < segs; ++k) {
            const auto& s0 = samples[k];
            const auto& s1 = samples[k + 1];
            double ds = elen / static_cast<double>(segs);
            out.slope_footprint_area += 0.5 * (s0.width + s1.width) * ds;

            double a0 = 0.5 * s0.width * std::abs(s0.height);
            double a1 = 0.5 * s1.width * std::abs(s1.height);
            double seg_vol = 0.5 * (a0 + a1) * ds;
            if (s0.height + s1.height >= 0.0) out.slope_cut_volume += seg_vol;
            else out.slope_fill_volume += seg_vol;

            out.daylight_ring.push_back(s0.daylight);
            if (s0.width > 0.2) {
                // 经典 CASS 示坡线：长短交替垂直短线
                double ratio = (tick_counter % 2 == 0) ? 0.92 : 0.48;
                XY tip{s0.base.x + (s0.daylight.x - s0.base.x) * ratio,
                       s0.base.y + (s0.daylight.y - s0.base.y) * ratio};
                out.slope_ticks.push_back({s0.base, tip});
                ++tick_counter;
            }
        }
    }

    return out;
}

} // namespace tin
