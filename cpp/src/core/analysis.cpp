#include "core/analysis.hpp"
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

constexpr int MAX_GRID_CELLS = 200000;
constexpr double GRID_COVERAGE_WARN_RATIO = 0.995;

std::vector<std::vector<XY>> chain_segments_to_polylines(const std::vector<Segment>& segs) {
    if (segs.empty()) return {};
    auto rk = [](const XY& p) {
        return std::make_pair(
            static_cast<long long>(std::llround(p.x * 1e6)),
            static_cast<long long>(std::llround(p.y * 1e6)));
    };
    using Key = std::pair<long long, long long>;
    std::map<Key, XY> coord_of;
    std::map<Key, std::vector<Key>> adj;
    for (const auto& s : segs) {
        Key ka = rk(s.first), kb = rk(s.second);
        if (ka == kb) continue;
        coord_of[ka] = s.first;
        coord_of[kb] = s.second;
        adj[ka].push_back(kb);
        adj[kb].push_back(ka);
    }

    auto make_edge = [](const Key& a, const Key& b) {
        return (a < b) ? std::make_pair(a, b) : std::make_pair(b, a);
    };
    std::set<std::pair<Key, Key>> visited;
    std::vector<Key> starts;
    for (const auto& kv : adj) {
        if (kv.second.size() == 1) starts.push_back(kv.first);
    }
    for (const auto& kv : adj) {
        if (kv.second.size() != 1) starts.push_back(kv.first);
    }

    std::vector<std::vector<XY>> polylines;
    for (const Key& st : starts) {
        for (const Key& nb : adj[st]) {
            auto edge = make_edge(st, nb);
            if (!visited.insert(edge).second) continue;
            std::vector<XY> path = {coord_of[st], coord_of[nb]};
            Key prev = st, cur = nb;
            while (true) {
                bool extended = false;
                for (const Key& nxt : adj[cur]) {
                    if (nxt == prev) continue;
                    auto e2 = make_edge(cur, nxt);
                    if (visited.insert(e2).second) {
                        path.push_back(coord_of[nxt]);
                        prev = cur;
                        cur = nxt;
                        extended = true;
                        break;
                    }
                }
                if (!extended) break;
            }
            if (path.size() >= 2) {
                polylines.push_back(std::move(path));
            }
        }
    }
    return polylines;
}

} // namespace

BalanceResult solve_balance_elevation(const TINEarthworkCalculator& calculator, const SoilFactors& factors) {
    if (calculator.is_compare()) {
        throw std::runtime_error("两期对比模式没有设计面，不能求零平衡标高");
    }
    if (calculator.points().size() < 3) {
        throw std::runtime_error("至少需要 3 个测量点才能求解平衡高程");
    }

    std::vector<SurveyPoint> clean;
    clean.reserve(calculator.points().size());
    double z_min = 1e30, z_max = -1e30, z_sum = 0.0;
    for (const auto& p : calculator.points()) {
        if (!std::isfinite(p.z)) {
            throw std::runtime_error("测点高程包含非数字值，无法求解平衡高程");
        }
        clean.push_back(SurveyPoint{p.id, p.x, p.y, p.z, 0.0, 0.0, false});
        z_min = std::min(z_min, p.z);
        z_max = std::max(z_max, p.z);
        z_sum += p.z;
    }
    double z_mean = z_sum / static_cast<double>(clean.size());

    TINEarthworkCalculator probe(z_mean);
    probe.add_points(clean);
    probe.set_breaklines(calculator.breaklines());
    probe.set_boundary(calculator.boundary());
    probe.set_holes(calculator.holes(), true);
    if (calculator.max_edge_length() > 0.0) {
        probe.set_max_edge_length(calculator.max_edge_length());
    }
    probe.build_tin();

    double h_balance = z_mean;
    if (std::abs(factors.compact - 1.0) < 1e-12) {
        // 统一设计高程下 V_net(H) 关于 H 严格线性，一步解析求得精确解
        auto ref = probe.calculate_volumes();
        if (ref.computed_area <= 0.0) {
            throw std::runtime_error("计算范围内有效面积为 0，无法求解平衡高程");
        }
        h_balance = z_mean + ref.net_volume / ref.computed_area;
    } else {
        auto gap = [&](double h) {
            probe.set_design_elevation(h);
            auto r = probe.calculate_volumes();
            if (r.computed_area <= 0.0) {
                throw std::runtime_error("计算范围内有效面积为 0，无法求解平衡高程");
            }
            return r.total_cut - r.total_fill / factors.compact;
        };
        double lo = z_min - 1.0, hi = z_max + 1.0;
        double glo = gap(lo), ghi = gap(hi);
        if (glo < 0.0 || ghi > 0.0) {
            throw std::runtime_error("无法在测点高程范围内求得平衡标高");
        }
        for (int iter = 0; iter < 80 && (hi - lo) > 1e-10; ++iter) {
            double mid = 0.5 * (lo + hi);
            double gmid = gap(mid);
            if (std::abs(gmid) < 1e-9) {
                lo = hi = mid;
                break;
            }
            if (gmid > 0.0) lo = mid;
            else hi = mid;
        }
        h_balance = 0.5 * (lo + hi);
    }

    probe.set_design_elevation(h_balance);
    auto final_res = probe.calculate_volumes();

    return BalanceResult{
        h_balance,
        final_res.total_cut,
        final_res.total_fill,
        final_res.net_volume,
        final_res.computed_area,
        z_min,
        z_max,
        z_mean
    };
}

double suggest_grid_spacing(double width, double height) {
    for (double s : {5.0, 10.0, 20.0, 50.0, 100.0}) {
        double cells = std::ceil(width / s) * std::ceil(height / s);
        if (cells <= 2500.0) return s;
    }
    return 200.0;
}

GridCheckResult run_grid_check(
    const TINEarthworkCalculator& calculator,
    const CalculationResult& tin_result,
    double spacing) {

    if (!std::isfinite(spacing) || spacing <= 0.0) {
        throw std::runtime_error("方格边长必须是大于 0 的数字");
    }
    auto region = calculator.coverage_polygon();
    if (calculator.boundary().size() >= 3) {
        auto b_norm = normalize_ring(calculator.boundary());
        if (ring_self_intersects(b_norm)) {
            throw std::runtime_error("计算边界自相交，无法铺设方格网");
        }
        // 使用计算边界（TIN 三角形仍会在边界与覆盖区内裁剪）
        region = b_norm;
    }
    if (region.size() < 3) {
        throw std::runtime_error("计算边界内没有测点覆盖，无法铺设方格网");
    }

    double xmin = region[0].x, xmax = region[0].x;
    double ymin = region[0].y, ymax = region[0].y;
    for (const auto& p : region) {
        xmin = std::min(xmin, p.x); xmax = std::max(xmax, p.x);
        ymin = std::min(ymin, p.y); ymax = std::max(ymax, p.y);
    }

    double x0 = std::floor(xmin / spacing + 1e-9) * spacing;
    double y0 = std::floor(ymin / spacing + 1e-9) * spacing;
    int cols = std::max(1, static_cast<int>(std::ceil((xmax - x0) / spacing - 1e-9)));
    int rows = std::max(1, static_cast<int>(std::ceil((ymax - y0) / spacing - 1e-9)));
    if (static_cast<long long>(rows) * cols > MAX_GRID_CELLS) {
        throw std::runtime_error("方格边长过小，生成方格数超过上限，请加大边长");
    }

    int nr = rows + 1;
    int nc = cols + 1;
    GridCheckResult out;
    out.spacing = spacing;
    out.origin = {x0, y0};
    out.rows = rows;
    out.cols = cols;
    out.corners.reserve(nr * nc);

    std::vector<int> vertex_index(nr * nc, -1);
    std::vector<SurveyPoint> vertex_points;
    vertex_points.reserve(nr * nc);

    for (int r = 0; r < nr; ++r) {
        double y = y0 + spacing * r;
        for (int c = 0; c < nc; ++c) {
            double x = x0 + spacing * c;
            auto [nat_in, des_in, _] = calculator.sample_surface(x, y, false);
            bool covered = std::isfinite(nat_in) && std::isfinite(des_in);
            double nat = nat_in, des = des_in;
            if (!covered) {
                auto [nat_ex, des_ex, _ext] = calculator.sample_surface(x, y, true);
                nat = nat_ex;
                des = des_ex;
            }
            std::string name = std::to_string(r + 1) + "-" + std::to_string(c + 1);
            GridCorner corner{name, r + 1, c + 1, x, y, nat, des, !covered};
            if (corner.valid()) {
                vertex_index[r * nc + c] = static_cast<int>(vertex_points.size());
                vertex_points.push_back(SurveyPoint{
                    name, x, y, nat, des, nat - des, true
                });
            }
            out.corners.push_back(std::move(corner));
        }
    }

    struct CellLoc { int r, c; };
    std::vector<CellLoc> valid_cells;
    std::vector<TriIndex> first_tris, second_tris;
    for (int r = 0; r < rows; ++r) {
        for (int c = 0; c < cols; ++c) {
            int i_ll = vertex_index[r * nc + c];
            int i_lr = vertex_index[r * nc + c + 1];
            int i_ur = vertex_index[(r + 1) * nc + c + 1];
            int i_ul = vertex_index[(r + 1) * nc + c];
            if (i_ll >= 0 && i_lr >= 0 && i_ur >= 0 && i_ul >= 0) {
                valid_cells.push_back({r, c});
                first_tris.push_back({i_ll, i_lr, i_ur});
                second_tris.push_back({i_ll, i_ur, i_ul});
            } else {
                ++out.skipped_cells;
            }
        }
    }
    if (valid_cells.empty()) {
        throw std::runtime_error("方格网内没有有效方格，请调整边长或检查计算范围");
    }

    std::vector<TriIndex> all_tris;
    all_tris.reserve(valid_cells.size() * 2);
    all_tris.insert(all_tris.end(), first_tris.begin(), first_tris.end());
    all_tris.insert(all_tris.end(), second_tris.begin(), second_tris.end());

    // 用测点覆盖凸包与边界相交区域的方格网网格计算精确方量
    auto cov = calculator.coverage_polygon();
    std::vector<XY> mesh_boundary = region;
    if (!calculator.boundary().empty() && cov.size() >= 3) {
        auto clipped = clip_convex_polygons(region, cov);
        if (clipped.size() >= 3 && std::abs(signed_area(clipped)) > 1e-6) {
            mesh_boundary = std::move(clipped);
        }
    }

    TINEarthworkCalculator mesh;
    mesh.set_boundary(mesh_boundary);
    if (!calculator.holes().empty()) {
        mesh.set_holes(calculator.holes(), false);
    }
    mesh.build_from_mesh(vertex_points, all_tris);
    auto mesh_result = mesh.calculate_volumes();

    int n_cells = static_cast<int>(valid_cells.size());
    for (int k = 0; k < n_cells; ++k) {
        const auto& t1 = mesh_result.triangles[k];
        const auto& t2 = mesh_result.triangles[k + n_cells];
        double area = (t1.is_boundary ? 0.0 : t1.area) + (t2.is_boundary ? 0.0 : t2.area);
        if (area <= 0.0) continue;
        double cut = (t1.is_boundary ? 0.0 : t1.cut_volume) + (t2.is_boundary ? 0.0 : t2.cut_volume);
        double fill = (t1.is_boundary ? 0.0 : t1.fill_volume) + (t2.is_boundary ? 0.0 : t2.fill_volume);
        bool clipped = t1.is_clipped || t2.is_clipped || t1.is_boundary || t2.is_boundary;
        int r = valid_cells[k].r, c = valid_cells[k].c;
        GridCell cell;
        cell.name = std::to_string(r + 1) + "-" + std::to_string(c + 1);
        cell.row = r + 1;
        cell.col = c + 1;
        cell.x0 = x0 + spacing * c;
        cell.y0 = y0 + spacing * r;
        cell.x1 = cell.x0 + spacing;
        cell.y1 = cell.y0 + spacing;
        cell.area = area;
        cell.cut = cut;
        cell.fill = fill;
        cell.clipped = clipped;
        out.cells.push_back(std::move(cell));
    }

    out.total_cut = mesh_result.total_cut;
    out.total_fill = mesh_result.total_fill;
    out.computed_area = mesh_result.computed_area;

    if (tin_result.computed_area > 0.0 && out.computed_area < tin_result.computed_area * GRID_COVERAGE_WARN_RATIO) {
        double missing = tin_result.computed_area - out.computed_area;
        std::ostringstream os;
        os << "方格网少计 " << std::fixed << std::setprecision(1) << missing << " m²（"
           << (missing / tin_result.computed_area * 100.0) << "%）：部分边角方格未完整计入";
        out.warnings.push_back(os.str());
    }
    return out;
}

double suggest_contour_interval(double z_min, double z_max) {
    double span = std::max(z_max - z_min, 1e-9);
    for (double step : {0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0}) {
        if (span / step <= 20.0) return step;
    }
    return 50.0;
}

ContourSet extract_elevation_contours(const CalculationResult& result, double interval, int major_step) {
    std::vector<const Triangle*> active;
    double z_min = 1e30, z_max = -1e30;
    for (const auto& tri : result.triangles) {
        if (tri.is_boundary) continue;
        active.push_back(&tri);
        for (const auto* p : {&tri.p0, &tri.p1, &tri.p2}) {
            z_min = std::min(z_min, p->z);
            z_max = std::max(z_max, p->z);
        }
    }
    ContourSet out;
    out.major_step = major_step;
    if (active.empty() || z_max < z_min) {
        out.interval = (interval > 0.0) ? interval : 1.0;
        return out;
    }
    if (interval <= 0.0) {
        interval = suggest_contour_interval(z_min, z_max);
    }
    out.interval = interval;

    int first_k = static_cast<int>(std::ceil(z_min / interval - 1e-9));
    int last_k = static_cast<int>(std::floor(z_max / interval + 1e-9));
    if (last_k - first_k + 1 > 400) {
        throw std::runtime_error("等高距过小，等高线条数超过 400 条限制");
    }

    std::vector<XY> ring;
    if (result.boundary_points.size() >= 3) {
        ring = normalize_ring(result.boundary_points);
    }
    std::vector<std::vector<XY>> holes;
    for (const auto& h : result.holes) {
        if (h.size() >= 3) holes.push_back(normalize_ring(h));
    }

    double nudge = interval * 1e-9;
    for (int k = first_k; k <= last_k; ++k) {
        double level = k * interval;
        bool is_major = (k % major_step == 0);
        std::vector<Segment> level_segs;

        for (const Triangle* tri : active) {
            double zs[3] = {tri->p0.z - level, tri->p1.z - level, tri->p2.z - level};
            if (std::min({zs[0], zs[1], zs[2]}) > 0.0 || std::max({zs[0], zs[1], zs[2]}) < 0.0) continue;
            XY pts[3] = {{tri->p0.x, tri->p0.y}, {tri->p1.x, tri->p1.y}, {tri->p2.x, tri->p2.y}};
            for (int i = 0; i < 3; ++i) {
                if (std::abs(zs[i]) <= nudge) zs[i] = nudge;
            }
            const int edges[3][2] = {{0, 1}, {1, 2}, {2, 0}};
            XY hits[3];
            int nhit = 0;
            for (int e = 0; e < 3; ++e) {
                int a = edges[e][0], b = edges[e][1];
                if ((zs[a] > 0.0) != (zs[b] > 0.0)) {
                    double t = zs[a] / (zs[a] - zs[b]);
                    hits[nhit++] = {
                        pts[a].x + t * (pts[b].x - pts[a].x),
                        pts[a].y + t * (pts[b].y - pts[a].y)
                    };
                }
            }
            if (nhit != 2 || hits[0] == hits[1]) continue;

            std::vector<Segment> pieces = {{hits[0], hits[1]}};
            if (!ring.empty() && tri->is_clipped) {
                pieces = clip_segment_to_ring(hits[0], hits[1], ring, true);
            }
            if (!holes.empty() && tri->is_clipped) {
                for (const auto& hole : holes) {
                    std::vector<Segment> next;
                    for (const auto& s : pieces) {
                        auto sub = clip_segment_to_ring(s.first, s.second, hole, false);
                        next.insert(next.end(), sub.begin(), sub.end());
                    }
                    pieces = std::move(next);
                }
            }
            for (const auto& s : pieces) {
                level_segs.push_back(s);
                if (is_major) out.major_segments.push_back(s);
                else out.minor_segments.push_back(s);
            }
        }

        for (auto& poly : chain_segments_to_polylines(level_segs)) {
            out.lines.push_back(ContourLine{level, is_major, std::move(poly)});
        }
    }
    return out;
}

ProfileResult sample_profile(
    TINEarthworkCalculator& calculator, const XY& start, const XY& end, int samples) {

    double dx = end.x - start.x, dy = end.y - start.y;
    double length = std::hypot(dx, dy);
    if (!std::isfinite(length) || length <= 1e-9) {
        throw std::runtime_error("剖面起点和终点重合，请选择不同的两点");
    }
    if (!calculator.has_tin()) {
        calculator.build_tin();
    }

    int count = std::max(2, samples);
    ProfileResult out;
    out.start = start;
    out.end = end;
    out.length = length;
    out.is_compare = calculator.is_compare();
    out.distances.resize(count);
    out.natural_z.resize(count);
    out.design_z.resize(count);
    out.delta_z.resize(count);

    double nan = std::numeric_limits<double>::quiet_NaN();
    for (int i = 0; i < count; ++i) {
        double t = static_cast<double>(i) / static_cast<double>(count - 1);
        double x = start.x + t * dx;
        double y = start.y + t * dy;
        out.distances[i] = t * length;
        auto [nat, des, _] = calculator.sample_surface(x, y, false);
        out.natural_z[i] = nat;
        out.design_z[i] = des;
        if (std::isfinite(nat) && std::isfinite(des)) {
            double dz = nat - des;
            out.delta_z[i] = dz;
            if (dz > out.max_cut_depth) out.max_cut_depth = dz;
            if (-dz > out.max_fill_depth) out.max_fill_depth = -dz;
        } else {
            out.delta_z[i] = nan;
        }
    }

    for (int i = 0; i + 1 < count; ++i) {
        double v0 = out.delta_z[i], v1 = out.delta_z[i + 1];
        if (!std::isfinite(v0) || !std::isfinite(v1)) continue;
        double ds = out.distances[i + 1] - out.distances[i];
        if ((v0 >= 0.0 && v1 >= 0.0) || (v0 <= 0.0 && v1 <= 0.0)) {
            double area = 0.5 * (std::abs(v0) + std::abs(v1)) * ds;
            if (v0 + v1 >= 0.0) out.cut_area += area;
            else out.fill_area += area;
        } else {
            double frac = std::abs(v0) / (std::abs(v0) + std::abs(v1));
            double s_cut = (v0 > 0.0) ? (ds * frac) : (ds * (1.0 - frac));
            double s_fill = ds - s_cut;
            out.cut_area += 0.5 * (v0 > 0.0 ? v0 : v1) * s_cut;
            out.fill_area += 0.5 * (v0 < 0.0 ? -v0 : -v1) * s_fill;
        }
    }
    return out;
}

} // namespace tin
