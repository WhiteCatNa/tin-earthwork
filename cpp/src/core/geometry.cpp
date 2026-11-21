#include "core/geometry.hpp"
#include <algorithm>
#include <cmath>

namespace tin {

namespace {

int orient_sign(const XY& p, const XY& q, const XY& r) {
    double v = (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
    if (v > 0.0) return 1;
    if (v < 0.0) return -1;
    return 0;
}

} // namespace

double signed_area(const std::vector<XY>& ring) {
    if (ring.size() < 3) return 0.0;
    double sum = 0.0;
    size_t n = ring.size();
    for (size_t i = 0; i < n; ++i) {
        size_t j = (i + 1) % n;
        sum += ring[i].x * ring[j].y - ring[j].x * ring[i].y;
    }
    return sum * 0.5;
}

std::vector<XY> normalize_ring(const std::vector<XY>& points) {
    std::vector<XY> ring = points;
    if (ring.size() >= 2 && ring.front() == ring.back()) {
        ring.pop_back();
    }
    std::vector<XY> cleaned;
    cleaned.reserve(ring.size());
    for (const auto& pt : ring) {
        if (cleaned.empty() || cleaned.back() != pt) {
            cleaned.push_back(pt);
        }
    }
    if (cleaned.size() >= 2 && cleaned.front() == cleaned.back()) {
        cleaned.pop_back();
    }
    if (signed_area(cleaned) < 0.0) {
        std::reverse(cleaned.begin(), cleaned.end());
    }
    return cleaned;
}

bool segments_cross(const XY& a, const XY& b, const XY& c, const XY& d) {
    return (orient_sign(a, b, c) * orient_sign(a, b, d) < 0) &&
           (orient_sign(c, d, a) * orient_sign(c, d, b) < 0);
}

bool ring_self_intersects(const std::vector<XY>& ring) {
    size_t n = ring.size();
    if (n < 4) return false;
    for (size_t i = 0; i + 2 < n; ++i) {
        size_t j_end = (i == 0) ? (n - 1) : n;
        for (size_t j = i + 2; j < j_end; ++j) {
            const XY& a = ring[i];
            const XY& b = ring[(i + 1) % n];
            const XY& c = ring[j];
            const XY& d = ring[(j + 1) % n];
            if (segments_cross(a, b, c, d)) {
                return true;
            }
        }
    }
    return false;
}

bool point_in_ring(const XY& pt, const std::vector<XY>& ring) {
    size_t n = ring.size();
    if (n < 3) return false;
    bool inside = false;
    for (size_t i = 0; i < n; ++i) {
        const XY& a = ring[i];
        const XY& b = ring[(i + 1) % n];
        if (a.y == b.y) continue;
        double low = std::min(a.y, b.y);
        double high = std::max(a.y, b.y);
        if (pt.y >= low && pt.y < high) {
            double x_cross = (b.x - a.x) * (pt.y - a.y) / (b.y - a.y) + a.x;
            if (pt.x < x_cross) {
                inside = !inside;
            }
        }
    }
    return inside;
}

std::vector<bool> points_in_ring(const std::vector<XY>& pts, const std::vector<XY>& ring) {
    std::vector<bool> out(pts.size(), false);
    for (size_t i = 0; i < pts.size(); ++i) {
        out[i] = point_in_ring(pts[i], ring);
    }
    return out;
}

std::vector<XY> clip_ring_halfplane(const std::vector<XY>& poly, double a, double b, double c) {
    if (poly.empty()) return {};
    size_t n = poly.size();
    std::vector<double> side(n);
    std::vector<bool> inside(n);
    bool all_in = true;
    bool any_in = false;
    for (size_t i = 0; i < n; ++i) {
        side[i] = poly[i].x * a + poly[i].y * b + c;
        inside[i] = side[i] >= 0.0;
        all_in = all_in && inside[i];
        any_in = any_in || inside[i];
    }
    if (all_in) return poly;
    if (!any_in) return {};

    std::vector<XY> out;
    out.reserve(n + 4);
    for (size_t i = 0; i < n; ++i) {
        size_t j = (i + 1) % n;
        if (inside[i]) {
            out.push_back(poly[i]);
        }
        if (inside[i] != inside[j]) {
            double denom = side[i] - side[j];
            double t = (denom != 0.0) ? (side[i] / denom) : 0.0;
            out.push_back({
                poly[i].x + t * (poly[j].x - poly[i].x),
                poly[i].y + t * (poly[j].y - poly[i].y)
            });
        }
    }
    return out;
}

std::vector<XY> clip_ring_to_triangle(const std::vector<XY>& ring, const XY& p0, const XY& p1, const XY& p2) {
    XY q0 = p0, q1 = p1, q2 = p2;
    if ((q1.x - q0.x) * (q2.y - q0.y) - (q1.y - q0.y) * (q2.x - q0.x) < 0.0) {
        std::swap(q1, q2);
    }
    std::vector<XY> poly = ring;
    const XY tri[3] = {q0, q1, q2};
    for (int i = 0; i < 3; ++i) {
        const XY& s = tri[i];
        const XY& e = tri[(i + 1) % 3];
        double ex = e.x - s.x;
        double ey = e.y - s.y;
        poly = clip_ring_halfplane(poly, -ey, ex, ey * s.x - ex * s.y);
        if (poly.empty()) break;
    }
    return poly;
}

std::vector<XY> clip_convex_polygons(const std::vector<XY>& subject, const std::vector<XY>& clipper) {
    if (subject.size() < 3 || clipper.size() < 3) return {};
    std::vector<XY> clip = normalize_ring(clipper);
    std::vector<XY> poly = normalize_ring(subject);
    size_t n = clip.size();
    for (size_t i = 0; i < n; ++i) {
        const XY& s = clip[i];
        const XY& e = clip[(i + 1) % n];
        double ex = e.x - s.x;
        double ey = e.y - s.y;
        poly = clip_ring_halfplane(poly, -ey, ex, ey * s.x - ex * s.y);
        if (poly.empty()) break;
    }
    return poly;
}

std::pair<std::vector<XY>, std::vector<double>> clip_by_sign(
    const std::vector<XY>& poly, const std::vector<double>& values, bool positive) {
    std::vector<XY> out_pts;
    std::vector<double> out_vals;
    size_t n = poly.size();
    if (n == 0) return {out_pts, out_vals};
    out_pts.reserve(n + 2);
    out_vals.reserve(n + 2);

    for (size_t i = 0; i < n; ++i) {
        size_t j = (i + 1) % n;
        double vi = values[i];
        double vj = values[j];
        bool in_i = positive ? (vi >= 0.0) : (vi <= 0.0);
        bool in_j = positive ? (vj >= 0.0) : (vj <= 0.0);
        if (in_i) {
            out_pts.push_back(poly[i]);
            out_vals.push_back(vi);
        }
        if (in_i != in_j && vi != 0.0 && vj != 0.0) {
            double t = vi / (vi - vj);
            out_pts.push_back({
                poly[i].x + t * (poly[j].x - poly[i].x),
                poly[i].y + t * (poly[j].y - poly[i].y)
            });
            out_vals.push_back(0.0);
        }
    }
    return {out_pts, out_vals};
}

std::pair<double, double> polygon_integral(const std::vector<XY>& poly, const std::vector<double>& values) {
    if (poly.size() < 3) return {0.0, 0.0};
    double x0 = poly[0].x, y0 = poly[0].y, v0 = values[0];
    double area = 0.0;
    double integral = 0.0;
    for (size_t i = 1; i + 1 < poly.size(); ++i) {
        double x1 = poly[i].x, y1 = poly[i].y;
        double x2 = poly[i + 1].x, y2 = poly[i + 1].y;
        double part = ((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) * 0.5;
        area += part;
        integral += part * (v0 + values[i] + values[i + 1]) / 3.0;
    }
    if (area < 0.0) return {-area, -integral};
    return {area, integral};
}

std::optional<std::tuple<double, double, double>> linear_plane(
    const XY& p0, const XY& p1, const XY& p2, double v0, double v1, double v2, double eps) {
    double x1 = p1.x - p0.x, y1 = p1.y - p0.y;
    double x2 = p2.x - p0.x, y2 = p2.y - p0.y;
    double det = x1 * y2 - x2 * y1;
    if (std::abs(det) <= eps) return std::nullopt;
    double a = ((v1 - v0) * y2 - (v2 - v0) * y1) / det;
    double b = ((v2 - v0) * x1 - (v1 - v0) * x2) / det;
    double c = v0 - a * p0.x - b * p0.y;
    return std::make_tuple(a, b, c);
}

std::vector<XY> zero_crossing_points(const std::vector<XY>& poly, const std::vector<double>& values) {
    std::vector<XY> points;
    size_t n = poly.size();
    for (size_t i = 0; i < n; ++i) {
        size_t j = (i + 1) % n;
        double vi = values[i];
        double vj = values[j];
        XY cand;
        if (vi == 0.0) {
            cand = poly[i];
        } else if ((vi > 0.0 && vj < 0.0) || (vi < 0.0 && vj > 0.0)) {
            double t = vi / (vi - vj);
            cand = {
                poly[i].x + t * (poly[j].x - poly[i].x),
                poly[i].y + t * (poly[j].y - poly[i].y)
            };
        } else {
            continue;
        }
        bool dup = false;
        for (const auto& existing : points) {
            if (existing == cand) {
                dup = true;
                break;
            }
        }
        if (!dup) points.push_back(cand);
    }
    return points;
}

std::vector<Segment> clip_segment_to_ring(const XY& start, const XY& end, const std::vector<XY>& ring, bool inside) {
    if (ring.size() < 3) return inside ? std::vector<Segment>{} : std::vector<Segment>{{start, end}};
    double dx = end.x - start.x;
    double dy = end.y - start.y;
    std::vector<double> params = {0.0, 1.0};
    size_t n = ring.size();
    for (size_t i = 0; i < n; ++i) {
        const XY& a = ring[i];
        const XY& b = ring[(i + 1) % n];
        double ex = b.x - a.x;
        double ey = b.y - a.y;
        double denom = dx * ey - dy * ex;
        if (denom == 0.0) continue;
        double diff_x = a.x - start.x;
        double diff_y = a.y - start.y;
        double t = (diff_x * ey - diff_y * ex) / denom;
        double u = (diff_x * dy - diff_y * dx) / denom;
        if (t > 0.0 && t < 1.0 && u >= 0.0 && u <= 1.0) {
            params.push_back(t);
        }
    }
    std::sort(params.begin(), params.end());
    params.erase(std::unique(params.begin(), params.end()), params.end());

    std::vector<Segment> pieces;
    for (size_t i = 0; i + 1 < params.size(); ++i) {
        double t0 = params[i], t1 = params[i + 1];
        if (t1 <= t0) continue;
        double tm = 0.5 * (t0 + t1);
        XY mid{start.x + dx * tm, start.y + dy * tm};
        bool keep = (point_in_ring(mid, ring) == inside);
        if (!keep) continue;
        Segment piece{
            {start.x + dx * t0, start.y + dy * t0},
            {start.x + dx * t1, start.y + dy * t1}
        };
        if (!pieces.empty() && pieces.back().second == piece.first) {
            pieces.back().second = piece.second;
        } else {
            pieces.push_back(piece);
        }
    }
    return pieces;
}

bool triangle_touches_ring(const XY& p0, const XY& p1, const XY& p2, const std::vector<XY>& ring, double eps) {
    if (ring.size() < 2) return false;
    double txmin = std::min({p0.x, p1.x, p2.x}) - eps;
    double txmax = std::max({p0.x, p1.x, p2.x}) + eps;
    double tymin = std::min({p0.y, p1.y, p2.y}) - eps;
    double tymax = std::max({p0.y, p1.y, p2.y}) + eps;

    const XY tri[3] = {p0, p1, p2};
    size_t n = ring.size();
    for (size_t k = 0; k < n; ++k) {
        const XY& s = ring[k];
        const XY& e = ring[(k + 1) % n];
        if (std::max(s.x, e.x) < txmin || std::min(s.x, e.x) > txmax ||
            std::max(s.y, e.y) < tymin || std::min(s.y, e.y) > tymax) {
            continue;
        }
        // 分离轴测试（边法向 + 线段自身方向）
        double dir_x = e.x - s.x;
        double dir_y = e.y - s.y;
        double len = std::hypot(dir_x, dir_y);
        bool hit = true;
        if (len > 0.0) {
            XY axes[2] = {{-dir_y / len, dir_x / len}, {dir_x / len, dir_y / len}};
            for (const auto& ax : axes) {
                double sp0 = s.x * ax.x + s.y * ax.y;
                double sp1 = e.x * ax.x + e.y * ax.y;
                double smin = std::min(sp0, sp1), smax = std::max(sp0, sp1);
                double tp0 = p0.x * ax.x + p0.y * ax.y;
                double tp1 = p1.x * ax.x + p1.y * ax.y;
                double tp2 = p2.x * ax.x + p2.y * ax.y;
                double tmin = std::min({tp0, tp1, tp2}), tmax = std::max({tp0, tp1, tp2});
                if (tmax < smin - eps || tmin > smax + eps) {
                    hit = false;
                    break;
                }
            }
        }
        if (!hit) continue;

        for (int i = 0; i < 3; ++i) {
            const XY& a = tri[i];
            const XY& b = tri[(i + 1) % 3];
            double nx = -(b.y - a.y), ny = (b.x - a.x);
            double nlen = std::hypot(nx, ny);
            if (nlen <= 0.0) continue;
            nx /= nlen;
            ny /= nlen;
            double sp0 = s.x * nx + s.y * ny;
            double sp1 = e.x * nx + e.y * ny;
            double smin = std::min(sp0, sp1), smax = std::max(sp0, sp1);
            double tp0 = p0.x * nx + p0.y * ny;
            double tp1 = p1.x * nx + p1.y * ny;
            double tp2 = p2.x * nx + p2.y * ny;
            double tmin = std::min({tp0, tp1, tp2}), tmax = std::max({tp0, tp1, tp2});
            if (tmax < smin - eps || tmin > smax + eps) {
                hit = false;
                break;
            }
        }
        if (hit) return true;
    }
    return false;
}

} // namespace tin
