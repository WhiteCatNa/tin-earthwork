#include "core/calculator.hpp"
#include "core/geometry.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

namespace tin {

namespace {

constexpr double DUPLICATE_TOL = 1e-3;
constexpr double ELEVATION_TOL = 1e-3;
constexpr double COVERAGE_WARN_RATIO = 0.995;

void check_finite_points(const std::vector<SurveyPoint>& pts, const std::string& label) {
    for (const auto& p : pts) {
        if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z) || !std::isfinite(p.design_z)) {
            throw std::runtime_error("测点 " + p.id + "(" + label + ") 坐标或高程包含空值或非数字");
        }
    }
}

std::string fmt3(double v) {
    std::ostringstream os;
    os << std::fixed << std::setprecision(3) << v;
    return os.str();
}

std::string fmt1(double v) {
    std::ostringstream os;
    os << std::fixed << std::setprecision(1) << v;
    return os.str();
}

struct PlaneCoeff {
    double a = 0.0, b = 0.0, c = 0.0;
};

PlaneCoeff compute_tri_plane(const XY& p0, const XY& p1, const XY& p2, double z0, double z1, double z2) {
    double x1 = p1.x - p0.x, y1 = p1.y - p0.y;
    double x2 = p2.x - p0.x, y2 = p2.y - p0.y;
    double dz1 = z1 - z0, dz2 = z2 - z0;
    double det = x1 * y2 - x2 * y1;
    if (std::abs(det) <= 1e-18) {
        return {0.0, 0.0, (z0 + z1 + z2) / 3.0};
    }
    double a = (dz1 * y2 - dz2 * y1) / det;
    double b = (dz2 * x1 - dz1 * x2) / det;
    double c = z0 - a * p0.x - b * p0.y;
    return {a, b, c};
}

} // namespace

TINEarthworkCalculator::TINEarthworkCalculator(double design_elevation)
    : design_elevation_(design_elevation) {}

void TINEarthworkCalculator::apply_design() {
    auto update = [&](SurveyPoint& pt, bool check_override) {
        if (check_override) {
            auto it = design_overrides_.find(pt.id);
            if (it != design_overrides_.end()) {
                pt.design_z = it->second;
                pt.delta_z = pt.z - pt.design_z;
                return;
            }
        }
        if (design_plane_.has_value()) {
            pt.design_z = design_plane_->elevation(pt.x, pt.y);
        } else {
            pt.design_z = design_elevation_;
        }
        pt.delta_z = pt.z - pt.design_z;
    };
    for (auto& pt : points_) update(pt, true);
    for (auto& pt : extra_points_) update(pt, false);
    if (compare_points_.empty()) {
        for (auto& tri : triangles_) {
            update(tri.p0, true);
            update(tri.p1, true);
            update(tri.p2, true);
        }
    }
}

void TINEarthworkCalculator::add_points(const std::vector<SurveyPoint>& points) {
    points_ = points;
    bool any_design = false;
    std::set<long long> uniq_z;
    for (const auto& pt : points_) {
        if (pt.has_design_z) {
            any_design = true;
            uniq_z.insert(static_cast<long long>(std::llround(pt.design_z * 1e6)));
        }
    }
    if (any_design) {
        for (auto& pt : points_) {
            pt.delta_z = pt.z - pt.design_z;
        }
        if (uniq_z.size() == 1) {
            for (const auto& pt : points_) {
                if (pt.has_design_z) {
                    design_elevation_ = pt.design_z;
                    break;
                }
            }
        }
    } else {
        apply_design();
    }
}

void TINEarthworkCalculator::set_design_elevation(double elevation) {
    design_elevation_ = elevation;
    design_plane_.reset();
    design_overrides_.clear();
    apply_design();
}

void TINEarthworkCalculator::set_design_elevations(const std::map<std::string, double>& overrides, double default_elev) {
    design_elevation_ = default_elev;
    design_plane_.reset();
    design_overrides_ = overrides;
    apply_design();
}

void TINEarthworkCalculator::set_design_plane(const PlaneDesign& plane, const std::map<std::string, double>& overrides) {
    design_plane_ = plane;
    design_overrides_ = overrides;
    apply_design();
}

void TINEarthworkCalculator::set_compare_points(const std::vector<SurveyPoint>& points) {
    compare_points_ = points;
}

void TINEarthworkCalculator::set_boundary(const std::vector<XY>& boundary) {
    boundary_ = boundary;
}

void TINEarthworkCalculator::set_holes(const std::vector<std::vector<XY>>& holes, bool validate) {
    holes_.clear();
    for (const auto& h : holes) {
        if (h.size() >= 3) holes_.push_back(h);
    }
    validate_holes_ = validate;
}

void TINEarthworkCalculator::set_breaklines(const std::vector<Breakline>& breaklines) {
    breaklines_ = breaklines;
}

void TINEarthworkCalculator::set_max_edge_length(double length) {
    if (!std::isfinite(length) || length < 0.0) {
        throw std::runtime_error("最大边长必须是不小于 0 的数字");
    }
    max_edge_length_ = length;
}

void TINEarthworkCalculator::set_soil_factors(const SoilFactors& factors) {
    if (factors.loose <= 0.0 || factors.compact <= 0.0 || !std::isfinite(factors.loose) || !std::isfinite(factors.compact)) {
        throw std::runtime_error("可松性系数必须大于 0");
    }
    soil_factors_ = factors;
}

std::vector<int> TINEarthworkCalculator::unique_point_indices(const std::vector<SurveyPoint>& pts, const std::string& label) {
    struct KeyIdx {
        long long kx;
        long long ky;
        int idx;
    };
    std::vector<KeyIdx> items;
    items.reserve(pts.size());
    for (int i = 0; i < static_cast<int>(pts.size()); ++i) {
        items.push_back({
            static_cast<long long>(std::llround(pts[i].x / DUPLICATE_TOL)),
            static_cast<long long>(std::llround(pts[i].y / DUPLICATE_TOL)),
            i
        });
    }
    std::sort(items.begin(), items.end(), [](const KeyIdx& a, const KeyIdx& b) {
        if (a.kx != b.kx) return a.kx < b.kx;
        if (a.ky != b.ky) return a.ky < b.ky;
        return a.idx < b.idx;
    });

    std::vector<int> keep;
    keep.reserve(items.size());
    int ignored_count = 0;
    size_t i = 0;
    while (i < items.size()) {
        size_t j = i + 1;
        while (j < items.size() && items[j].kx == items[i].kx && items[j].ky == items[i].ky) {
            ++j;
        }
        int first_idx = items[i].idx;
        keep.push_back(first_idx);
        const auto& first = pts[first_idx];
        for (size_t k = i + 1; k < j; ++k) {
            const auto& other = pts[items[k].idx];
            if (std::abs(other.z - first.z) > ELEVATION_TOL ||
                std::abs(other.design_z - first.design_z) > ELEVATION_TOL) {
                throw std::runtime_error(
                    label + "测点坐标重复但高程不同: " + first.id + "(" + fmt3(first.z) + ") 与 " +
                    other.id + "(" + fmt3(other.z) + ") @ (" + fmt3(first.x) + ", " + fmt3(first.y) + ")");
            }
            ++ignored_count;
        }
        i = j;
    }
    std::sort(keep.begin(), keep.end());
    if (ignored_count > 0) {
        warnings_.push_back("已忽略 " + std::to_string(ignored_count) + " 个完全重复的" + label + "测点");
    }
    return keep;
}

void TINEarthworkCalculator::triangulate_design(
    std::vector<SurveyPoint>& out_pts, std::vector<XY>& out_local, std::vector<TriIndex>& out_tris) {

    auto uniq = unique_point_indices(points_, "");
    if (uniq.size() < 3) {
        throw std::runtime_error("至少需要 3 个不重复的测量点才能构建三角网");
    }

    double ox = points_[uniq[0]].x, oy = points_[uniq[0]].y;
    for (int idx : uniq) {
        ox = std::min(ox, points_[idx].x);
        oy = std::min(oy, points_[idx].y);
    }
    origin_ = {ox, oy};

    std::vector<SurveyPoint> base;
    base.reserve(uniq.size());
    for (int idx : uniq) base.push_back(points_[idx]);

    std::vector<Breakline> local_bl;
    breakline_polylines_.clear();
    for (const auto& bl : breaklines_) {
        if (bl.points.size() < 2) continue;
        Breakline copy = bl;
        std::vector<XY> poly;
        for (auto& pt : copy.points) {
            poly.push_back({pt.x, pt.y});
            pt.x -= ox;
            pt.y -= oy;
            if (!pt.has_design_z) {
                pt.design_z = design_plane_.has_value() ? design_plane_->elevation(pt.x + ox, pt.y + oy) : design_elevation_;
                pt.delta_z = pt.z - pt.design_z;
            }
        }
        breakline_polylines_.push_back(std::move(poly));
        local_bl.push_back(std::move(copy));
    }

    std::vector<SurveyPoint> base_local = base;
    for (auto& pt : base_local) {
        pt.x -= ox;
        pt.y -= oy;
    }

    auto cdt = build_constrained_delaunay(base_local, local_bl, DUPLICATE_TOL);
    out_pts.clear();
    out_pts.reserve(cdt.all_points.size());
    out_local.clear();
    out_local.reserve(cdt.all_points.size());
    extra_points_.clear();

    std::vector<double> nat_z(cdt.all_points.size()), des_z(cdt.all_points.size());
    for (size_t i = 0; i < cdt.all_points.size(); ++i) {
        SurveyPoint gp = cdt.all_points[i];
        out_local.push_back({gp.x, gp.y});
        gp.x += ox;
        gp.y += oy;
        if (static_cast<int>(i) >= cdt.base_point_count) {
            if (!gp.has_design_z) {
                gp.design_z = design_plane_.has_value() ? design_plane_->elevation(gp.x, gp.y) : design_elevation_;
                gp.delta_z = gp.z - gp.design_z;
            }
            extra_points_.push_back(gp);
        }
        nat_z[i] = gp.z;
        des_z[i] = gp.design_z;
        out_pts.push_back(std::move(gp));
    }

    // 过滤超长外缘三角形
    out_tris.clear();
    for (const auto& t : cdt.simplices) {
        if (max_edge_length_ > 0.0) {
            const XY& a = out_local[t.a];
            const XY& b = out_local[t.b];
            const XY& c = out_local[t.c];
            double l0 = std::hypot(a.x - b.x, a.y - b.y);
            double l1 = std::hypot(b.x - c.x, b.y - c.y);
            double l2 = std::hypot(c.x - a.x, c.y - a.y);
            if (std::max({l0, l1, l2}) > max_edge_length_) {
                trimmed_triangles_.push_back({to_global(a), to_global(b), to_global(c)});
                continue;
            }
        }
        out_tris.push_back(t);
    }

    if (out_tris.empty()) {
        throw std::runtime_error("所有三角形均因最大边长限制被剔除，请增大最大边长阈值");
    }

    tin_a_.build(out_local, out_tris, nat_z, des_z);
    tin_b_ = TinSurface{};
    overlap_ratio_.reset();
}

void TINEarthworkCalculator::triangulate_compare(
    std::vector<SurveyPoint>& out_pts, std::vector<XY>& out_local, std::vector<TriIndex>& out_tris) {

    if (compare_points_.size() < 3) {
        throw std::runtime_error("两期对比时后期测量至少需要 3 个测点");
    }
    check_finite_points(compare_points_, "后期");

    auto uniq_a = unique_point_indices(points_, "前期");
    auto uniq_b = unique_point_indices(compare_points_, "后期");

    double ox = points_[uniq_a[0]].x, oy = points_[uniq_a[0]].y;
    for (int i : uniq_a) { ox = std::min(ox, points_[i].x); oy = std::min(oy, points_[i].y); }
    for (int i : uniq_b) { ox = std::min(ox, compare_points_[i].x); oy = std::min(oy, compare_points_[i].y); }
    origin_ = {ox, oy};

    std::vector<XY> local_a(uniq_a.size()), local_b(uniq_b.size());
    std::vector<double> za(uniq_a.size()), zb(uniq_b.size());
    double minx = 1e30, maxx = -1e30, miny = 1e30, maxy = -1e30;
    for (size_t k = 0; k < uniq_a.size(); ++k) {
        const auto& p = points_[uniq_a[k]];
        local_a[k] = {p.x - ox, p.y - oy};
        za[k] = p.z;
        minx = std::min(minx, local_a[k].x); maxx = std::max(maxx, local_a[k].x);
        miny = std::min(miny, local_a[k].y); maxy = std::max(maxy, local_a[k].y);
    }
    for (size_t k = 0; k < uniq_b.size(); ++k) {
        const auto& p = compare_points_[uniq_b[k]];
        local_b[k] = {p.x - ox, p.y - oy};
        zb[k] = p.z;
        minx = std::min(minx, local_b[k].x); maxx = std::max(maxx, local_b[k].x);
        miny = std::min(miny, local_b[k].y); maxy = std::max(maxy, local_b[k].y);
    }

    auto raw_a = build_delaunay(local_a);
    auto raw_b = build_delaunay(local_b);

    std::vector<TriIndex> simp_a, simp_b;
    for (const auto& t : raw_a) {
        if (max_edge_length_ > 0.0) {
            double mx = std::max({
                std::hypot(local_a[t.a].x - local_a[t.b].x, local_a[t.a].y - local_a[t.b].y),
                std::hypot(local_a[t.b].x - local_a[t.c].x, local_a[t.b].y - local_a[t.c].y),
                std::hypot(local_a[t.c].x - local_a[t.a].x, local_a[t.c].y - local_a[t.a].y)
            });
            if (mx > max_edge_length_) {
                trimmed_triangles_.push_back({to_global(local_a[t.a]), to_global(local_a[t.b]), to_global(local_a[t.c])});
                continue;
            }
        }
        simp_a.push_back(t);
    }
    for (const auto& t : raw_b) {
        if (max_edge_length_ > 0.0) {
            double mx = std::max({
                std::hypot(local_b[t.a].x - local_b[t.b].x, local_b[t.a].y - local_b[t.b].y),
                std::hypot(local_b[t.b].x - local_b[t.c].x, local_b[t.b].y - local_b[t.c].y),
                std::hypot(local_b[t.c].x - local_b[t.a].x, local_b[t.c].y - local_b[t.a].y)
            });
            if (mx > max_edge_length_) {
                trimmed_triangles_.push_back({to_global(local_b[t.a]), to_global(local_b[t.b]), to_global(local_b[t.c])});
                continue;
            }
        }
        simp_b.push_back(t);
    }

    tin_a_.build(local_a, simp_a, za, za);
    tin_b_.build(local_b, simp_b, zb, zb);

    double extent = std::max({maxx - minx, maxy - miny, 1.0});
    double area_tol = (extent * 1e-9) * extent * 1e-3;
    double key_tol = extent * 1e-9;

    // 计算两个三角网的精确交集细胞单元
    struct Box { double x0, y0, x1, y1; };
    std::vector<Box> box_a(simp_a.size()), box_b(simp_b.size());
    std::vector<PlaneCoeff> planes_a(simp_a.size()), planes_b(simp_b.size());
    double hull_a_area = 0.0;

    for (size_t i = 0; i < simp_a.size(); ++i) {
        const auto& t = simp_a[i];
        const XY &p0 = local_a[t.a], &p1 = local_a[t.b], &p2 = local_a[t.c];
        box_a[i] = {std::min({p0.x, p1.x, p2.x}), std::min({p0.y, p1.y, p2.y}),
                    std::max({p0.x, p1.x, p2.x}), std::max({p0.y, p1.y, p2.y})};
        planes_a[i] = compute_tri_plane(p0, p1, p2, za[t.a], za[t.b], za[t.c]);
        hull_a_area += std::abs(signed_area({p0, p1, p2}));
    }
    for (size_t j = 0; j < simp_b.size(); ++j) {
        const auto& t = simp_b[j];
        const XY &p0 = local_b[t.a], &p1 = local_b[t.b], &p2 = local_b[t.c];
        box_b[j] = {std::min({p0.x, p1.x, p2.x}), std::min({p0.y, p1.y, p2.y}),
                    std::max({p0.x, p1.x, p2.x}), std::max({p0.y, p1.y, p2.y})};
        planes_b[j] = compute_tri_plane(p0, p1, p2, zb[t.a], zb[t.b], zb[t.c]);
    }

    auto make_key = [&](const XY& p) {
        return std::make_pair(
            static_cast<long long>(std::llround(p.x / key_tol)),
            static_cast<long long>(std::llround(p.y / key_tol)));
    };

    std::map<std::pair<long long, long long>, std::string> names;
    for (size_t k = 0; k < uniq_b.size(); ++k) {
        names[make_key(local_b[k])] = "后期-" + compare_points_[uniq_b[k]].id;
    }
    for (size_t k = 0; k < uniq_a.size(); ++k) {
        names[make_key(local_a[k])] = points_[uniq_a[k]].id;
    }

    std::map<std::pair<long long, long long>, int> vert_map;
    out_pts.clear();
    out_local.clear();
    out_tris.clear();
    int crossing_id = 0;
    double overlap_area = 0.0;

    for (size_t ia = 0; ia < simp_a.size(); ++ia) {
        const auto& ta = simp_a[ia];
        std::vector<XY> poly_a = {local_a[ta.a], local_a[ta.b], local_a[ta.c]};
        for (size_t jb = 0; jb < simp_b.size(); ++jb) {
            if (box_a[ia].x1 < box_b[jb].x0 || box_a[ia].x0 > box_b[jb].x1 ||
                box_a[ia].y1 < box_b[jb].y0 || box_a[ia].y0 > box_b[jb].y1) {
                continue;
            }
            const auto& tb = simp_b[jb];
            std::vector<XY> poly_b = {local_b[tb.a], local_b[tb.b], local_b[tb.c]};
            auto inter = clip_convex_polygons(poly_a, poly_b);
            if (inter.size() < 3) continue;
            double cell_area = std::abs(signed_area(inter));
            if (cell_area <= area_tol) continue;
            overlap_area += cell_area;

            std::vector<int> ring_idx;
            ring_idx.reserve(inter.size());
            const auto& pa = planes_a[ia];
            const auto& pb = planes_b[jb];
            for (const auto& v : inter) {
                auto key = make_key(v);
                auto it = vert_map.find(key);
                int idx = -1;
                if (it != vert_map.end()) {
                    idx = it->second;
                } else {
                    idx = static_cast<int>(out_pts.size());
                    vert_map[key] = idx;
                    double z1 = pa.a * v.x + pa.b * v.y + pa.c;
                    double z2 = pb.a * v.x + pb.b * v.y + pb.c;
                    auto nit = names.find(key);
                    std::string pid = (nit != names.end()) ? nit->second : ("交点" + std::to_string(++crossing_id));
                    out_local.push_back(v);
                    out_pts.push_back(SurveyPoint{pid, v.x + ox, v.y + oy, z1, z2, z1 - z2, true});
                }
                if (ring_idx.empty() || ring_idx.back() != idx) {
                    ring_idx.push_back(idx);
                }
            }
            if (ring_idx.size() >= 2 && ring_idx.front() == ring_idx.back()) {
                ring_idx.pop_back();
            }
            for (size_t k = 1; k + 1 < ring_idx.size(); ++k) {
                int i0 = ring_idx[0], i1 = ring_idx[k], i2 = ring_idx[k + 1];
                double a3 = std::abs(signed_area({out_local[i0], out_local[i1], out_local[i2]}));
                if (a3 > area_tol) {
                    out_tris.push_back({i0, i1, i2});
                }
            }
        }
    }

    if (out_tris.empty()) {
        throw std::runtime_error("前期和后期测量的范围没有重叠，无法对比。请检查坐标系与 X/Y 方向是否一致");
    }
    if (hull_a_area > 0.0) {
        overlap_ratio_ = overlap_area / hull_a_area;
    }
}

std::vector<XY> TINEarthworkCalculator::prepare_boundary() const {
    if (boundary_.empty()) return {};
    auto ring = normalize_ring(boundary_);
    if (ring.size() < 3) {
        throw std::runtime_error("计算边界无效：至少需要 3 个不重复的顶点");
    }
    for (const auto& p : ring) {
        if (!std::isfinite(p.x) || !std::isfinite(p.y)) {
            throw std::runtime_error("计算边界含有非数字坐标");
        }
    }
    if (ring_self_intersects(ring)) {
        throw std::runtime_error("计算边界的边线相互交叉，请在边界页修正后再计算");
    }
    if (std::abs(signed_area(ring)) <= 0.0) {
        throw std::runtime_error("计算边界无效：顶点共线，围不成面积");
    }
    for (auto& p : ring) {
        p.x -= origin_.x;
        p.y -= origin_.y;
    }
    return ring;
}

std::vector<std::vector<XY>> TINEarthworkCalculator::prepare_holes(const std::vector<XY>& ring) const {
    std::vector<std::vector<XY>> out;
    for (const auto& raw : holes_) {
        auto h = normalize_ring(raw);
        if (h.size() < 3 || std::abs(signed_area(h)) <= 0.0) {
            throw std::runtime_error("挖空区无效：至少需要 3 个不共线顶点");
        }
        if (ring_self_intersects(h)) {
            throw std::runtime_error("挖空区的边线相互交叉，请修正后再计算");
        }
        for (auto& p : h) {
            p.x -= origin_.x;
            p.y -= origin_.y;
        }
        if (validate_holes_ && !ring.empty()) {
            for (const auto& pt : h) {
                if (!point_in_ring(pt, ring)) {
                    throw std::runtime_error("挖空区必须位于计算边界内部");
                }
            }
        }
        out.push_back(std::move(h));
    }
    return out;
}

void TINEarthworkCalculator::classify(
    const std::vector<SurveyPoint>& pts, const std::vector<XY>& local, const std::vector<TriIndex>& simplices) {

    regions_.clear();
    hole_regions_.clear();

    double xmin = 1e30, xmax = -1e30, ymin = 1e30, ymax = -1e30;
    for (const auto& t : simplices) {
        for (int vi : {t.a, t.b, t.c}) {
            xmin = std::min(xmin, local[vi].x);
            xmax = std::max(xmax, local[vi].x);
            ymin = std::min(ymin, local[vi].y);
            ymax = std::max(ymax, local[vi].y);
        }
    }
    double extent = std::max({xmax - xmin, ymax - ymin, 1.0});
    eps_ = extent * 1e-9;
    double area_tol = eps_ * extent * 1e-3;

    ring_ = prepare_boundary();
    boundary_area_ = ring_.empty() ? 0.0 : std::abs(signed_area(ring_));
    hole_rings_ = prepare_holes(ring_);
    hole_area_ = 0.0;
    for (const auto& h : hole_rings_) {
        hole_area_ += std::abs(signed_area(h));
    }
    if (!ring_.empty()) {
        boundary_area_ = std::max(0.0, boundary_area_ - hole_area_);
    }

    int nt = static_cast<int>(simplices.size());
    std::vector<bool> outside(nt, false);

    if (!ring_.empty()) {
        for (int i = 0; i < nt; ++i) {
            const auto& t = simplices[i];
            const XY &p0 = local[t.a], &p1 = local[t.b], &p2 = local[t.c];
            if (!triangle_touches_ring(p0, p1, p2, ring_, eps_)) {
                XY centroid{(p0.x + p1.x + p2.x) / 3.0, (p0.y + p1.y + p2.y) / 3.0};
                if (!point_in_ring(centroid, ring_)) {
                    outside[i] = true;
                }
            } else {
                auto region = clip_ring_to_triangle(ring_, p0, p1, p2);
                double region_area = std::abs(signed_area(region));
                double full_area = std::abs(signed_area({p0, p1, p2}));
                if (region_area <= area_tol) {
                    outside[i] = true;
                } else if (full_area - region_area > area_tol + full_area * 1e-9) {
                    regions_[i] = std::move(region);
                }
            }
        }
    }

    for (const auto& hole : hole_rings_) {
        for (int i = 0; i < nt; ++i) {
            if (outside[i]) continue;
            const auto& t = simplices[i];
            const XY &p0 = local[t.a], &p1 = local[t.b], &p2 = local[t.c];
            if (!triangle_touches_ring(p0, p1, p2, hole, eps_)) {
                XY centroid{(p0.x + p1.x + p2.x) / 3.0, (p0.y + p1.y + p2.y) / 3.0};
                if (point_in_ring(centroid, hole)) {
                    outside[i] = true;
                }
            } else {
                auto piece = clip_ring_to_triangle(hole, p0, p1, p2);
                double piece_area = std::abs(signed_area(piece));
                if (piece_area <= area_tol) continue;
                auto it = regions_.find(i);
                double region_area = (it != regions_.end())
                    ? std::abs(signed_area(it->second))
                    : std::abs(signed_area({p0, p1, p2}));
                auto& pieces = hole_regions_[i];
                pieces.push_back(std::move(piece));
                double holes_sum = 0.0;
                for (const auto& hp : pieces) holes_sum += std::abs(signed_area(hp));
                if (region_area - holes_sum <= area_tol + region_area * 1e-9) {
                    outside[i] = true;
                }
            }
        }
    }

    for (int i = 0; i < nt; ++i) {
        if (outside[i]) {
            hole_regions_.erase(i);
            regions_.erase(i);
        }
    }

    triangles_.clear();
    triangles_.reserve(nt);
    for (int i = 0; i < nt; ++i) {
        const auto& t = simplices[i];
        Triangle tri;
        tri.id = i;
        tri.v0 = t.a;
        tri.v1 = t.b;
        tri.v2 = t.c;
        tri.p0 = pts[t.a];
        tri.p1 = pts[t.b];
        tri.p2 = pts[t.c];
        tri.area = std::abs(
            (tri.p0.x * (tri.p1.y - tri.p2.y) +
             tri.p1.x * (tri.p2.y - tri.p0.y) +
             tri.p2.x * (tri.p0.y - tri.p1.y)) * 0.5);
        tri.is_boundary = outside[i];
        tri.is_clipped = (regions_.count(i) > 0) || (hole_regions_.count(i) > 0);
        triangles_.push_back(std::move(tri));
    }
}

void TINEarthworkCalculator::build_tin() {
    if (points_.size() < 3) {
        throw std::runtime_error("至少需要 3 个测量点才能构建三角网");
    }
    warnings_.clear();
    trimmed_triangles_.clear();
    check_finite_points(points_, "");

    std::vector<SurveyPoint> pts;
    std::vector<XY> local;
    std::vector<TriIndex> simplices;
    if (!compare_points_.empty()) {
        triangulate_compare(pts, local, simplices);
    } else {
        triangulate_design(pts, local, simplices);
    }
    classify(pts, local, simplices);
}

void TINEarthworkCalculator::build_from_mesh(
    const std::vector<SurveyPoint>& vertex_points, const std::vector<TriIndex>& simplices) {
    warnings_.clear();
    trimmed_triangles_.clear();
    check_finite_points(vertex_points, "");
    if (vertex_points.empty() || simplices.empty()) {
        throw std::runtime_error("三角网顶点或单元为空");
    }
    double ox = vertex_points[0].x, oy = vertex_points[0].y;
    for (const auto& p : vertex_points) {
        ox = std::min(ox, p.x);
        oy = std::min(oy, p.y);
    }
    origin_ = {ox, oy};
    std::vector<XY> local(vertex_points.size());
    for (size_t i = 0; i < vertex_points.size(); ++i) {
        local[i] = {vertex_points[i].x - ox, vertex_points[i].y - oy};
    }
    tin_a_ = TinSurface{};
    tin_b_ = TinSurface{};
    classify(vertex_points, local, simplices);
}

std::vector<Segment> TINEarthworkCalculator::clip_to_region(int tri_id, const XY& start, const XY& end) const {
    XY ls{start.x - origin_.x, start.y - origin_.y};
    XY le{end.x - origin_.x, end.y - origin_.y};
    std::vector<Segment> segs = {{ls, le}};
    if (regions_.count(tri_id) > 0 && !ring_.empty()) {
        segs = clip_segment_to_ring(ls, le, ring_, true);
    }
    if (hole_regions_.count(tri_id) > 0) {
        for (const auto& hole : hole_rings_) {
            std::vector<Segment> next;
            for (const auto& s : segs) {
                auto parts = clip_segment_to_ring(s.first, s.second, hole, false);
                next.insert(next.end(), parts.begin(), parts.end());
            }
            segs = std::move(next);
        }
    }
    std::vector<Segment> out;
    out.reserve(segs.size());
    for (const auto& s : segs) {
        out.push_back({to_global(s.first), to_global(s.second)});
    }
    return out;
}

void TINEarthworkCalculator::subtract_holes(Triangle& tri) {
    auto hit = hole_regions_.find(tri.id);
    if (hit == hole_regions_.end()) return;

    std::vector<XY> verts = {
        {tri.p0.x - origin_.x, tri.p0.y - origin_.y},
        {tri.p1.x - origin_.x, tri.p1.y - origin_.y},
        {tri.p2.x - origin_.x, tri.p2.y - origin_.y}
    };
    double dz[3] = {tri.p0.delta_z, tri.p1.delta_z, tri.p2.delta_z};
    auto plane = linear_plane(verts[0], verts[1], verts[2], dz[0], dz[1], dz[2], eps_ * eps_);

    for (const auto& piece : hit->second) {
        std::vector<double> vals(piece.size());
        if (!plane.has_value()) {
            double avg = (dz[0] + dz[1] + dz[2]) / 3.0;
            std::fill(vals.begin(), vals.end(), avg);
        } else {
            auto [a, b, c] = *plane;
            for (size_t i = 0; i < piece.size(); ++i) {
                vals[i] = a * piece[i].x + b * piece[i].y + c;
            }
        }
        auto [area, _] = polygon_integral(piece, vals);
        auto [cut_poly, cut_vals] = clip_by_sign(piece, vals, true);
        auto [cut_a, cut_v] = polygon_integral(cut_poly, cut_vals);
        auto [fill_poly, fill_vals] = clip_by_sign(piece, vals, false);
        auto [fill_a, fill_v] = polygon_integral(fill_poly, fill_vals);

        tri.area -= area;
        if (cut_v > 0.0) {
            tri.cut_volume -= cut_v;
            tri.cut_area -= cut_a;
        }
        if (fill_v < 0.0) {
            tri.fill_volume += fill_v;
            tri.fill_area -= fill_a;
        }
    }
    tri.area = std::max(0.0, tri.area);
    tri.cut_area = std::max(0.0, tri.cut_area);
    tri.fill_area = std::max(0.0, tri.fill_area);
    tri.cut_volume = std::max(0.0, tri.cut_volume);
    tri.fill_volume = std::max(0.0, tri.fill_volume);
    tri.volume = tri.cut_volume - tri.fill_volume;
    tri.avg_delta_z = (tri.area > 0.0) ? (tri.volume / tri.area) : 0.0;
}

void TINEarthworkCalculator::calculate_triangle(Triangle& tri) {
    const double dz0 = tri.p0.delta_z;
    const double dz1 = tri.p1.delta_z;
    const double dz2 = tri.p2.delta_z;

    const bool has_regions = !regions_.empty();
    auto reg_it = has_regions ? regions_.find(tri.id) : regions_.end();
    if (reg_it == regions_.end()) {
        const bool has_cut = (dz0 > 0.0 || dz1 > 0.0 || dz2 > 0.0);
        const bool has_fill = (dz0 < 0.0 || dz1 < 0.0 || dz2 < 0.0);
        if (!(has_cut && has_fill)) {
            tri.area = std::abs(
                (tri.p0.x * (tri.p1.y - tri.p2.y) +
                 tri.p1.x * (tri.p2.y - tri.p0.y) +
                 tri.p2.x * (tri.p0.y - tri.p1.y)) * 0.5);
            tri.volume = tri.area * (dz0 + dz1 + dz2) / 3.0;
            if (tri.volume > 0.0) {
                tri.cut_volume = tri.volume;
                tri.cut_area = tri.area;
            } else if (tri.volume < 0.0) {
                tri.fill_volume = -tri.volume;
                tri.fill_area = tri.area;
            }
            tri.avg_delta_z = (tri.area > 0.0) ? (tri.volume / tri.area) : 0.0;
            if (!hole_regions_.empty()) {
                subtract_holes(tri);
            }
            return;
        }
    }

    std::vector<XY> verts = {
        {tri.p0.x - origin_.x, tri.p0.y - origin_.y},
        {tri.p1.x - origin_.x, tri.p1.y - origin_.y},
        {tri.p2.x - origin_.x, tri.p2.y - origin_.y}
    };
    std::vector<double> dz = {dz0, dz1, dz2};

    std::vector<XY> poly;
    std::vector<double> values;
    if (reg_it != regions_.end()) {
        poly = reg_it->second;
        values.resize(poly.size());
        auto plane = linear_plane(verts[0], verts[1], verts[2], dz[0], dz[1], dz[2], eps_ * eps_);
        if (!plane.has_value()) {
            double avg = (dz[0] + dz[1] + dz[2]) / 3.0;
            std::fill(values.begin(), values.end(), avg);
        } else {
            auto [a, b, c] = *plane;
            for (size_t i = 0; i < poly.size(); ++i) {
                values[i] = a * poly[i].x + b * poly[i].y + c;
            }
        }
    } else {
        poly = verts;
        values = dz;
    }

    bool has_cut = false, has_fill = false;
    for (double v : values) {
        if (v > 0.0) has_cut = true;
        if (v < 0.0) has_fill = true;
    }

    if (!(has_cut && has_fill)) {
        if (reg_it != regions_.end()) {
            auto [area, vol] = polygon_integral(poly, values);
            tri.area = area;
            tri.volume = vol;
        } else {
            tri.area = std::abs(
                (tri.p0.x * (tri.p1.y - tri.p2.y) +
                 tri.p1.x * (tri.p2.y - tri.p0.y) +
                 tri.p2.x * (tri.p0.y - tri.p1.y)) * 0.5);
            tri.volume = tri.area * (dz[0] + dz[1] + dz[2]) / 3.0;
        }
        if (tri.volume > 0.0) {
            tri.cut_volume = tri.volume;
            tri.cut_area = tri.area;
        } else if (tri.volume < 0.0) {
            tri.fill_volume = -tri.volume;
            tri.fill_area = tri.area;
        }
        if (reg_it != regions_.end()) {
            auto& target = (tri.volume >= 0.0) ? tri.cut_polygon : tri.fill_polygon;
            target.reserve(poly.size());
            for (const auto& p : poly) target.push_back(to_global(p));
        }
        tri.avg_delta_z = (tri.area > 0.0) ? (tri.volume / tri.area) : 0.0;
        if (!hole_regions_.empty()) subtract_holes(tri);
        return;
    }

    tri.is_mixed = true;
    auto [cut_p, cut_v] = clip_by_sign(poly, values, true);
    auto [fill_p, fill_v] = clip_by_sign(poly, values, false);
    auto [ca, cv] = polygon_integral(cut_p, cut_v);
    auto [fa, fv] = polygon_integral(fill_p, fill_v);

    tri.cut_area = ca;
    tri.fill_area = fa;
    tri.cut_volume = std::max(cv, 0.0);
    tri.fill_volume = std::max(-fv, 0.0);
    tri.volume = tri.cut_volume - tri.fill_volume;
    tri.area = tri.cut_area + tri.fill_area;
    tri.avg_delta_z = (tri.area > 0.0) ? (tri.volume / tri.area) : 0.0;

    tri.cut_polygon.reserve(cut_p.size());
    for (const auto& p : cut_p) tri.cut_polygon.push_back(to_global(p));
    tri.fill_polygon.reserve(fill_p.size());
    for (const auto& p : fill_p) tri.fill_polygon.push_back(to_global(p));

    auto zero = zero_crossing_points(verts, dz);
    if (zero.size() == 2) {
        tri.zero_segments = clip_to_region(tri.id, to_global(zero[0]), to_global(zero[1]));
    }
    if (!hole_regions_.empty()) subtract_holes(tri);
}

CalculationResult TINEarthworkCalculator::calculate_volumes() {
    auto t0 = std::chrono::steady_clock::now();
    if (triangles_.empty()) {
        build_tin();
    }

    CalculationResult result;
    result.boundary_points = boundary_;
    result.holes = holes_;
    result.breakline_polylines = breakline_polylines_;
    result.trimmed_triangles = trimmed_triangles_;
    result.boundary_area = boundary_area_;
    result.hole_area = hole_area_;
    result.is_compare = !compare_points_.empty();
    result.compare_point_count = static_cast<int>(compare_points_.size());

    for (auto& tri : triangles_) {
        tri.volume = tri.cut_volume = tri.fill_volume = 0.0;
        tri.cut_area = tri.fill_area = tri.avg_delta_z = 0.0;
        tri.is_mixed = false;
        tri.cut_polygon.clear();
        tri.fill_polygon.clear();
        tri.zero_segments.clear();
        if (tri.is_boundary) continue;

        calculate_triangle(tri);
        result.total_cut += tri.cut_volume;
        result.total_fill += tri.fill_volume;
        result.computed_area += tri.area;
        result.triangle_count += 1;
        if (tri.is_mixed) result.mixed_triangle_count += 1;
        if (tri.is_clipped) result.clipped_triangle_count += 1;
    }

    result.net_volume = result.total_cut - result.total_fill;
    result.triangles = triangles_;
    result.soil_balance = SoilBalance{result.total_cut, result.total_fill, soil_factors_};

    if (!points_.empty()) {
        double dmin = 1e30, dmax = -1e30;
        for (const auto& p : points_) {
            dmin = std::min(dmin, p.design_z);
            dmax = std::max(dmax, p.design_z);
        }
        result.design_min = dmin;
        result.design_max = dmax;
    }

    if (!compare_points_.empty()) {
        result.design_description = "两期对比：以后期测量（" + std::to_string(compare_points_.size()) + " 个点）为比较面，高差 = 前期 − 后期";
    } else if (design_plane_.has_value()) {
        result.design_description =
            "斜面：基准点 (" + fmt3(design_plane_->x0) + ", " + fmt3(design_plane_->y0) +
            ") 高程 " + fmt3(design_plane_->h0) + " m，X向坡度 " +
            fmt3(design_plane_->slope_x) + "%，Y向坡度 " + fmt3(design_plane_->slope_y) + "%";
    } else if (result.uniform_design()) {
        result.design_description = fmt3(result.design_min) + " m";
    } else {
        result.design_description = "分区/逐点设计高程 " + fmt3(result.design_min) + " ~ " + fmt3(result.design_max) + " m";
    }

    result.warnings = warnings_;
    if (result.boundary_area > 0.0) {
        double ratio = result.computed_area / result.boundary_area;
        if (ratio < COVERAGE_WARN_RATIO) {
            double missing = result.boundary_area - result.computed_area;
            if (result.computed_area <= 0.0) {
                result.warnings.push_back(
                    "计算边界（" + fmt1(result.boundary_area) + " m²）内没有测点覆盖，方量为 0。"
                    "请检查边界位置或 X/Y 方向是否一致。");
            } else {
                result.warnings.push_back(
                    "计算边界面积 " + fmt1(result.boundary_area) + " m²，其中 " + fmt1(missing) +
                    " m²（" + fmt1((1.0 - ratio) * 100.0) + "%）不在测点覆盖范围内，未计入方量。");
            }
        }
    } else if (result.is_compare && overlap_ratio_.has_value() && *overlap_ratio_ < COVERAGE_WARN_RATIO) {
        result.warnings.push_back(
            "后期测量只覆盖了前期范围的 " + fmt1(*overlap_ratio_ * 100.0) + "%，只计算两期共同覆盖部分。");
    }

    auto t1 = std::chrono::steady_clock::now();
    result.elapsed_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    return result;
}

CalculationResult TINEarthworkCalculator::run_full_calculation(std::optional<double> design_elevation) {
    if (design_elevation.has_value()) {
        set_design_elevation(*design_elevation);
    }
    build_tin();
    return calculate_volumes();
}

std::tuple<double, double, bool> TINEarthworkCalculator::sample_surface(double x, double y, bool extrapolate) const {
    if (tin_a_.empty()) {
        double nan = std::numeric_limits<double>::quiet_NaN();
        return {nan, nan, false};
    }
    double lx = x - origin_.x;
    double ly = y - origin_.y;
    if (tin_b_.empty()) {
        if (extrapolate) return tin_a_.extrapolate(lx, ly);
        auto [nat, des] = tin_a_.interpolate(lx, ly);
        return {nat, des, false};
    }
    if (extrapolate) {
        auto [na, _1, ea] = tin_a_.extrapolate(lx, ly);
        auto [nb, _2, eb] = tin_b_.extrapolate(lx, ly);
        return {na, nb, ea || eb};
    }
    auto [na, _1] = tin_a_.interpolate(lx, ly);
    auto [nb, _2] = tin_b_.interpolate(lx, ly);
    return {na, nb, false};
}

std::vector<XY> TINEarthworkCalculator::coverage_polygon() const {
    if (tin_a_.empty()) return {};
    auto region = tin_a_.hull();
    if (!tin_b_.empty()) {
        region = clip_convex_polygons(region, tin_b_.hull());
    }
    for (auto& p : region) p = to_global(p);
    return region;
}

std::vector<Segment> TINEarthworkCalculator::level_segments(bool use_design, double level) const {
    std::vector<Segment> segs;
    std::set<std::pair<std::pair<long long, long long>, std::pair<long long, long long>>> seen;
    auto rk = [](const XY& p) {
        return std::make_pair(
            static_cast<long long>(std::llround(p.x * 1e6)),
            static_cast<long long>(std::llround(p.y * 1e6)));
    };
    for (const auto& tri : triangles_) {
        if (tri.is_boundary) continue;
        double v0 = (use_design ? tri.p0.design_z : tri.p0.z) - level;
        double v1 = (use_design ? tri.p1.design_z : tri.p1.z) - level;
        double v2 = (use_design ? tri.p2.design_z : tri.p2.z) - level;
        if (std::min({v0, v1, v2}) > 0.0 || std::max({v0, v1, v2}) < 0.0) continue;
        auto cross = zero_crossing_points(
            {{tri.p0.x, tri.p0.y}, {tri.p1.x, tri.p1.y}, {tri.p2.x, tri.p2.y}},
            {v0, v1, v2});
        if (cross.size() != 2) continue;
        for (const auto& s : clip_to_region(tri.id, cross[0], cross[1])) {
            auto ka = rk(s.first), kb = rk(s.second);
            if (ka == kb) continue;
            if (kb < ka) std::swap(ka, kb);
            if (seen.insert({ka, kb}).second) {
                segs.push_back(s);
            }
        }
    }
    return segs;
}

} // namespace tin
