#include "core/delaunay.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <numeric>
#include <queue>
#include <stdexcept>
#include <tuple>

namespace tin {

namespace {

double orient2d(const XY& a, const XY& b, const XY& c) {
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
}

// a, b, c 逆时针时 > 0 表示 d 在外接圆内
double in_circle_ccw(const XY& a, const XY& b, const XY& c, const XY& d) {
    double adx = a.x - d.x, ady = a.y - d.y;
    double bdx = b.x - d.x, bdy = b.y - d.y;
    double cdx = c.x - d.x, cdy = c.y - d.y;
    double ad = adx * adx + ady * ady;
    double bd = bdx * bdx + bdy * bdy;
    double cd = cdx * cdx + cdy * cdy;
    return adx * (bdy * cd - bd * cdy)
         - ady * (bdx * cd - bd * cdx)
         + ad  * (bdx * cdy - bdy * cdx);
}

struct BWCircle {
    int a, b, c;
    double cx, cy, r2;
    bool valid = true;
};

BWCircle make_bw_tri(const std::vector<XY>& pts, int a, int b, int c) {
    if (orient2d(pts[a], pts[b], pts[c]) < 0.0) {
        std::swap(b, c);
    }
    const XY& pa = pts[a];
    const XY& pb = pts[b];
    const XY& pc = pts[c];
    double ax = pb.x - pa.x, ay = pb.y - pa.y;
    double bx = pc.x - pa.x, by = pc.y - pa.y;
    double d = 2.0 * (ax * by - ay * bx);
    BWCircle tri{a, b, c, 0.0, 0.0, 0.0, true};
    if (std::abs(d) < 1e-18) {
        tri.cx = (pa.x + pb.x + pc.x) / 3.0;
        tri.cy = (pa.y + pb.y + pc.y) / 3.0;
        tri.r2 = std::numeric_limits<double>::infinity();
        return tri;
    }
    double a2 = ax * ax + ay * ay;
    double b2 = bx * bx + by * by;
    double ux = (by * a2 - ay * b2) / d;
    double uy = (ax * b2 - bx * a2) / d;
    tri.cx = pa.x + ux;
    tri.cy = pa.y + uy;
    tri.r2 = ux * ux + uy * uy;
    return tri;
}

} // namespace

std::vector<TriIndex> build_delaunay(const std::vector<XY>& points) {
    int n = static_cast<int>(points.size());
    if (n < 3) {
        throw std::runtime_error("至少需要 3 个不重复的测量点才能构建三角网");
    }

    double xmin = points[0].x, xmax = points[0].x;
    double ymin = points[0].y, ymax = points[0].y;
    for (const auto& p : points) {
        if (!std::isfinite(p.x) || !std::isfinite(p.y)) {
            throw std::runtime_error("测量点坐标包含无效数值");
        }
        xmin = std::min(xmin, p.x);
        xmax = std::max(xmax, p.x);
        ymin = std::min(ymin, p.y);
        ymax = std::max(ymax, p.y);
    }
    double scale = std::max({xmax - xmin, ymax - ymin, 1.0});

    std::vector<XY> norm(n + 3);
    for (int i = 0; i < n; ++i) {
        norm[i] = {(points[i].x - xmin) / scale, (points[i].y - ymin) / scale};
    }
    // 超级三角形覆盖归一化空间
    int s0 = n, s1 = n + 1, s2 = n + 2;
    norm[s0] = {-100.0, -100.0};
    norm[s1] = {200.0, -100.0};
    norm[s2] = {50.0, 200.0};

    std::vector<int> order(n);
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int i, int j) {
        if (norm[i].x != norm[j].x) return norm[i].x < norm[j].x;
        return norm[i].y < norm[j].y;
    });

    std::vector<BWCircle> tris;
    tris.reserve(n * 2 + 8);
    tris.push_back(make_bw_tri(norm, s0, s1, s2));

    struct Edge {
        int u, v;
        bool operator==(const Edge& o) const { return u == o.u && v == o.v; }
    };

    for (int p : order) {
        const XY& pt = norm[p];
        std::vector<Edge> polygon;
        polygon.reserve(16);
        std::vector<int> bad_indices;
        bad_indices.reserve(16);

        for (int ti = 0; ti < static_cast<int>(tris.size()); ++ti) {
            const auto& t = tris[ti];
            if (!t.valid) continue;
            double dx = pt.x - t.cx;
            double dy = pt.y - t.cy;
            if (dx * dx + dy * dy <= t.r2 * (1.0 + 1e-12) + 1e-15) {
                bad_indices.push_back(ti);
            }
        }

        // 记录空腔所有有向边，反向边相互抵消即为边界环
        std::vector<Edge> edges;
        edges.reserve(bad_indices.size() * 3);
        for (int ti : bad_indices) {
            const auto& t = tris[ti];
            edges.push_back({t.a, t.b});
            edges.push_back({t.b, t.c});
            edges.push_back({t.c, t.a});
            tris[ti].valid = false;
        }

        for (size_t i = 0; i < edges.size(); ++i) {
            bool shared = false;
            for (size_t j = 0; j < edges.size(); ++j) {
                if (i == j) continue;
                if (edges[i].u == edges[j].v && edges[i].v == edges[j].u) {
                    shared = true;
                    break;
                }
            }
            if (!shared) {
                polygon.push_back(edges[i]);
            }
        }

        for (const auto& e : polygon) {
            if (std::abs(orient2d(norm[e.u], norm[e.v], pt)) <= 1e-16) {
                continue; // 避免退化零面积三角形
            }
            tris.push_back(make_bw_tri(norm, e.u, e.v, p));
        }
    }

    std::vector<TriIndex> result;
    result.reserve(tris.size());
    for (const auto& t : tris) {
        if (!t.valid) continue;
        if (t.a >= n || t.b >= n || t.c >= n) continue;
        if (std::abs(orient2d(points[t.a], points[t.b], points[t.c])) <= 1e-12 * scale * scale) {
            continue;
        }
        if (orient2d(points[t.a], points[t.b], points[t.c]) < 0.0) {
            result.push_back({t.a, t.c, t.b});
        } else {
            result.push_back({t.a, t.b, t.c});
        }
    }

    if (result.empty()) {
        throw std::runtime_error("无法构建三角网：测量点全部共线或重复");
    }
    return result;
}

namespace {

class CDTMesh {
public:
    CDTMesh(const std::vector<XY>& pts, const std::vector<TriIndex>& tris) : p_(pts) {
        for (const auto& tri : tris) {
            int a = tri.a, b = tri.b, c = tri.c;
            if (orient2d(p_[a], p_[b], p_[c]) < 0.0) {
                std::swap(b, c);
            }
            add_tri(a, b, c);
        }
    }

    bool has_edge(int a, int b) const {
        return half_.count({a, b}) || half_.count({b, a});
    }

    void insert(int a, int b, double tol) {
        if (has_edge(a, b)) return;
        const XY& pa = p_[a];
        const XY& pb = p_[b];
        int r = -1, l = -1;
        auto it = out_.find(a);
        if (it != out_.end()) {
            for (int v : it->second) {
                int w = half_[{a, v}];
                if (orient2d(pa, p_[v], pb) > 0.0 && orient2d(pa, p_[w], pb) < 0.0) {
                    r = v;
                    l = w;
                    break;
                }
            }
        }
        if (r < 0 || l < 0) return; // 端点与边未按标准穿过，安全跳过

        struct Tri3 { int a, b, c; };
        std::vector<Tri3> crossed;
        std::vector<int> right_chain = {r};
        std::vector<int> left_chain = {l};
        crossed.push_back({a, r, l});

        double len = std::hypot(pb.x - pa.x, pb.y - pa.y);
        int guard = static_cast<int>(half_.size()) + 10;
        while (guard-- > 0) {
            auto hit = half_.find({l, r});
            if (hit == half_.end()) return;
            int x = hit->second;
            crossed.push_back({l, r, x});
            if (x == b) break;
            double side = orient2d(pa, pb, p_[x]);
            if (std::abs(side) <= tol * len) {
                return;
            }
            if (side < 0.0) {
                r = x;
                right_chain.push_back(x);
            } else {
                l = x;
                left_chain.push_back(x);
            }
        }
        if (guard <= 0) return;

        for (const auto& t : crossed) {
            remove_tri(t.a, t.b, t.c);
        }
        std::reverse(left_chain.begin(), left_chain.end());
        fill_cavity(a, b, left_chain);
        fill_cavity(b, a, right_chain);
    }

    std::vector<TriIndex> simplices() const {
        std::vector<TriIndex> out;
        for (const auto& kv : half_) {
            int u = kv.first.first;
            int v = kv.first.second;
            int w = kv.second;
            if (u < v && u < w) {
                out.push_back({u, v, w});
            }
        }
        return out;
    }

private:
    void add_tri(int a, int b, int c) {
        half_[{a, b}] = c;
        half_[{b, c}] = a;
        half_[{c, a}] = b;
        out_[a].insert(b);
        out_[b].insert(c);
        out_[c].insert(a);
    }

    void remove_tri(int a, int b, int c) {
        half_.erase({a, b});
        half_.erase({b, c});
        half_.erase({c, a});
        out_[a].erase(b);
        out_[b].erase(c);
        out_[c].erase(a);
    }

    void fill_cavity(int u, int v, const std::vector<int>& chain) {
        struct Task { int u, v; std::vector<int> ch; };
        std::vector<Task> stack;
        stack.push_back({u, v, chain});
        while (!stack.empty()) {
            Task cur = std::move(stack.back());
            stack.pop_back();
            if (cur.ch.empty()) continue;
            int best = 0;
            for (int i = 1; i < static_cast<int>(cur.ch.size()); ++i) {
                if (in_circle_ccw(p_[cur.u], p_[cur.v], p_[cur.ch[best]], p_[cur.ch[i]]) > 0.0) {
                    best = i;
                }
            }
            int c = cur.ch[best];
            add_tri(cur.u, cur.v, c);
            std::vector<int> left(cur.ch.begin(), cur.ch.begin() + best);
            std::vector<int> right(cur.ch.begin() + best + 1, cur.ch.end());
            stack.push_back({c, cur.v, std::move(left)});
            stack.push_back({cur.u, c, std::move(right)});
        }
    }

    const std::vector<XY>& p_;
    std::map<std::pair<int, int>, int> half_;
    std::map<int, std::set<int>> out_;
};

} // namespace

ConstrainedDelaunayResult build_constrained_delaunay(
    const std::vector<SurveyPoint>& base_points,
    const std::vector<Breakline>& breaklines,
    double tol) {

    ConstrainedDelaunayResult out;
    out.all_points = base_points;
    out.base_point_count = static_cast<int>(base_points.size());

    auto find_or_add = [&](const SurveyPoint& bp) -> int {
        for (int i = 0; i < static_cast<int>(out.all_points.size()); ++i) {
            if (std::hypot(out.all_points[i].x - bp.x, out.all_points[i].y - bp.y) <= tol) {
                return i;
            }
        }
        SurveyPoint copy = bp;
        if (copy.id.empty()) {
            copy.id = "FL" + std::to_string(out.all_points.size() + 1);
        }
        out.all_points.push_back(copy);
        return static_cast<int>(out.all_points.size()) - 1;
    };

    std::vector<std::pair<int, int>> raw_segs;
    for (const auto& line : breaklines) {
        if (line.points.size() < 2) continue;
        std::vector<int> idx;
        idx.reserve(line.points.size());
        for (const auto& pt : line.points) {
            idx.push_back(find_or_add(pt));
        }
        for (size_t i = 0; i + 1 < idx.size(); ++i) {
            if (idx[i] != idx[i + 1]) raw_segs.emplace_back(idx[i], idx[i + 1]);
        }
        if (line.closed && idx.size() >= 3 && idx.back() != idx.front()) {
            raw_segs.emplace_back(idx.back(), idx.front());
        }
    }

    // 约束边求交并加交点
    for (size_t i = 0; i < raw_segs.size(); ++i) {
        for (size_t j = i + 1; j < raw_segs.size(); ++j) {
            int a1 = raw_segs[i].first, a2 = raw_segs[i].second;
            int b1 = raw_segs[j].first, b2 = raw_segs[j].second;
            if (a1 == b1 || a1 == b2 || a2 == b1 || a2 == b2) continue;
            const auto& p1 = out.all_points[a1];
            const auto& p2 = out.all_points[a2];
            const auto& q1 = out.all_points[b1];
            const auto& q2 = out.all_points[b2];
            double rx = p2.x - p1.x, ry = p2.y - p1.y;
            double sx = q2.x - q1.x, sy = q2.y - q1.y;
            double denom = rx * sy - ry * sx;
            if (std::abs(denom) < 1e-14) continue;
            double qpx = q1.x - p1.x, qpy = q1.y - p1.y;
            double t = (qpx * sy - qpy * sx) / denom;
            double u = (qpx * ry - qpy * rx) / denom;
            double lr = std::hypot(rx, ry), ls = std::hypot(sx, sy);
            if (t * lr > tol && (1.0 - t) * lr > tol && u * ls > tol && (1.0 - u) * ls > tol) {
                SurveyPoint cross;
                cross.x = p1.x + t * rx;
                cross.y = p1.y + t * ry;
                cross.z = p1.z + t * (p2.z - p1.z);
                cross.design_z = p1.design_z + t * (p2.design_z - p1.design_z);
                cross.has_design_z = p1.has_design_z && p2.has_design_z;
                find_or_add(cross);
            }
        }
    }

    // 沿约束线段经过的中间点拆开
    std::set<std::pair<int, int>> split_edges;
    for (auto [a, b] : raw_segs) {
        const auto& pa = out.all_points[a];
        const auto& pb = out.all_points[b];
        double dx = pb.x - pa.x, dy = pb.y - pa.y;
        double length = std::hypot(dx, dy);
        if (length <= tol) continue;
        double ux = dx / length, uy = dy / length;
        std::vector<std::pair<double, int>> chain;
        chain.emplace_back(0.0, a);
        chain.emplace_back(length, b);
        for (int k = 0; k < static_cast<int>(out.all_points.size()); ++k) {
            if (k == a || k == b) continue;
            double rx = out.all_points[k].x - pa.x;
            double ry = out.all_points[k].y - pa.y;
            double along = rx * ux + ry * uy;
            double across = std::abs(rx * uy - ry * ux);
            if (across <= tol && along > tol && along < length - tol) {
                chain.emplace_back(along, k);
            }
        }
        std::sort(chain.begin(), chain.end());
        for (size_t k = 0; k + 1 < chain.size(); ++k) {
            int u = chain[k].second, v = chain[k + 1].second;
            if (u != v) split_edges.insert({std::min(u, v), std::max(u, v)});
        }
    }

    std::vector<XY> xy(out.all_points.size());
    for (size_t i = 0; i < out.all_points.size(); ++i) {
        xy[i] = {out.all_points[i].x, out.all_points[i].y};
    }

    auto base_tris = build_delaunay(xy);
    if (split_edges.empty()) {
        out.simplices = std::move(base_tris);
        return out;
    }

    CDTMesh mesh(xy, base_tris);
    for (auto [u, v] : split_edges) {
        mesh.insert(u, v, tol);
    }
    out.simplices = mesh.simplices();
    out.constrained_edges = std::move(split_edges);
    return out;
}

std::vector<XY> convex_hull(const std::vector<XY>& points) {
    if (points.size() < 3) return points;
    std::vector<XY> pts = points;
    std::sort(pts.begin(), pts.end(), [](const XY& a, const XY& b) {
        if (a.x != b.x) return a.x < b.x;
        return a.y < b.y;
    });
    pts.erase(std::unique(pts.begin(), pts.end()), pts.end());
    if (pts.size() < 3) return pts;

    std::vector<XY> lower, upper;
    for (const auto& p : pts) {
        while (lower.size() >= 2 && orient2d(lower[lower.size() - 2], lower.back(), p) <= 0.0) {
            lower.pop_back();
        }
        lower.push_back(p);
    }
    for (int i = static_cast<int>(pts.size()) - 1; i >= 0; --i) {
        const auto& p = pts[i];
        while (upper.size() >= 2 && orient2d(upper[upper.size() - 2], upper.back(), p) <= 0.0) {
            upper.pop_back();
        }
        upper.push_back(p);
    }
    lower.pop_back();
    upper.pop_back();
    lower.insert(lower.end(), upper.begin(), upper.end());
    return lower;
}

std::pair<std::vector<XY>, double> outline_from_points(const std::vector<XY>& points, double max_edge) {
    std::vector<XY> uniq;
    uniq.reserve(points.size());
    for (const auto& p : points) {
        if (!std::isfinite(p.x) || !std::isfinite(p.y)) continue;
        bool dup = false;
        for (const auto& q : uniq) {
            if (std::hypot(p.x - q.x, p.y - q.y) <= 1e-6) {
                dup = true;
                break;
            }
        }
        if (!dup) uniq.push_back(p);
    }
    if (uniq.size() < 3) {
        throw std::runtime_error("至少需要 3 个位置不同的测点才能生成外轮廓");
    }

    auto tris = build_delaunay(uniq);
    int nt = static_cast<int>(tris.size());
    int nv = static_cast<int>(uniq.size());

    // 构建邻接信息 neighbors[t][k] 为第 k 个顶点对面的相邻三角形
    std::vector<std::array<int, 3>> vert(nt);
    std::vector<std::array<int, 3>> nbr(nt, {-1, -1, -1});
    std::map<std::pair<int, int>, std::pair<int, int>> edge_owner;
    std::vector<double> all_lengths;
    all_lengths.reserve(nt * 3);

    for (int t = 0; t < nt; ++t) {
        vert[t] = {tris[t].a, tris[t].b, tris[t].c};
        for (int k = 0; k < 3; ++k) {
            int u = vert[t][(k + 1) % 3];
            int v = vert[t][(k + 2) % 3];
            all_lengths.push_back(std::hypot(uniq[u].x - uniq[v].x, uniq[u].y - uniq[v].y));
            auto key = std::make_pair(std::min(u, v), std::max(u, v));
            auto it = edge_owner.find(key);
            if (it == edge_owner.end()) {
                edge_owner[key] = {t, k};
            } else {
                int ot = it->second.first, ok = it->second.second;
                nbr[t][k] = ot;
                nbr[ot][ok] = t;
            }
        }
    }

    if (max_edge < 0.0) {
        std::sort(all_lengths.begin(), all_lengths.end());
        size_t p95 = std::min(all_lengths.size() - 1, static_cast<size_t>(all_lengths.size() * 0.95));
        double raw = 1.4 * all_lengths[p95];
        if (raw > 0.0) {
            double step = std::pow(10.0, std::floor(std::log10(raw)) - 1.0);
            max_edge = std::ceil(raw / step - 1e-9) * step;
        }
    }

    auto edge_len = [&](int t, int k) {
        int a = vert[t][(k + 1) % 3], b = vert[t][(k + 2) % 3];
        return std::hypot(uniq[a].x - uniq[b].x, uniq[a].y - uniq[b].y);
    };

    std::vector<bool> alive(nt, true);
    std::vector<bool> on_outline(nv, false);

    struct HeapItem {
        double len;
        int t;
        int k;
        bool operator<(const HeapItem& o) const { return len < o.len; }
    };
    std::priority_queue<HeapItem> pq;

    for (int t = 0; t < nt; ++t) {
        for (int k = 0; k < 3; ++k) {
            if (nbr[t][k] == -1) {
                on_outline[vert[t][(k + 1) % 3]] = true;
                on_outline[vert[t][(k + 2) % 3]] = true;
                pq.push({edge_len(t, k), t, k});
            }
        }
    }

    if (max_edge > 0.0) {
        while (!pq.empty()) {
            auto item = pq.top();
            pq.pop();
            if (item.len <= max_edge) break;
            if (!alive[item.t] || nbr[item.t][item.k] != -1) continue;
            int apex = vert[item.t][item.k];
            if (on_outline[apex]) continue;
            alive[item.t] = false;
            on_outline[apex] = true;
            for (int other : {(item.k + 1) % 3, (item.k + 2) % 3}) {
                int inner = nbr[item.t][other];
                if (inner < 0 || !alive[inner]) continue;
                for (int s = 0; s < 3; ++s) {
                    if (nbr[inner][s] == item.t) {
                        nbr[inner][s] = -1;
                        pq.push({edge_len(inner, s), inner, s});
                        break;
                    }
                }
            }
        }
    }

    std::map<int, std::vector<int>> links;
    for (int t = 0; t < nt; ++t) {
        if (!alive[t]) continue;
        for (int k = 0; k < 3; ++k) {
            if (nbr[t][k] == -1) {
                int a = vert[t][(k + 1) % 3], b = vert[t][(k + 2) % 3];
                links[a].push_back(b);
                links[b].push_back(a);
            }
        }
    }
    if (links.empty()) {
        return {convex_hull(uniq), max_edge};
    }

    int start = links.begin()->first;
    std::vector<int> order = {start};
    int prev = -1, cur = start;
    for (size_t step = 0; step < links.size() + 5; ++step) {
        const auto& adj = links[cur];
        if (adj.size() < 2) break;
        int nxt = (adj[0] == prev) ? adj[1] : adj[0];
        if (nxt == start) break;
        order.push_back(nxt);
        prev = cur;
        cur = nxt;
    }

    std::vector<XY> ring;
    ring.reserve(order.size());
    for (int idx : order) ring.push_back(uniq[idx]);
    return {ring, max_edge};
}

// ---------------------------------------------------------------------------
// TinSurface
// ---------------------------------------------------------------------------

void TinSurface::build(const std::vector<XY>& pts,
                       const std::vector<TriIndex>& simplices,
                       const std::vector<double>& natural_z,
                       const std::vector<double>& design_z) {
    pts_ = pts;
    simplices_ = simplices;
    nat_z_ = natural_z;
    des_z_ = design_z;
    buckets_.clear();

    if (pts_.empty() || simplices_.empty()) return;

    xmin_ = xmax_ = pts_[0].x;
    ymin_ = ymax_ = pts_[0].y;
    for (const auto& p : pts_) {
        xmin_ = std::min(xmin_, p.x);
        xmax_ = std::max(xmax_, p.x);
        ymin_ = std::min(ymin_, p.y);
        ymax_ = std::max(ymax_, p.y);
    }

    grid_res_ = std::clamp(static_cast<int>(std::sqrt(static_cast<double>(simplices_.size()))), 4, 64);
    double span_x = std::max(xmax_ - xmin_, 1e-6);
    double span_y = std::max(ymax_ - ymin_, 1e-6);
    cell_w_ = span_x / grid_res_;
    cell_h_ = span_y / grid_res_;
    buckets_.assign(grid_res_ * grid_res_, {});

    for (int ti = 0; ti < static_cast<int>(simplices_.size()); ++ti) {
        const auto& t = simplices_[ti];
        const XY& a = pts_[t.a];
        const XY& b = pts_[t.b];
        const XY& c = pts_[t.c];
        double tx0 = std::min({a.x, b.x, c.x});
        double tx1 = std::max({a.x, b.x, c.x});
        double ty0 = std::min({a.y, b.y, c.y});
        double ty1 = std::max({a.y, b.y, c.y});
        int gx0 = std::clamp(static_cast<int>((tx0 - xmin_) / cell_w_), 0, grid_res_ - 1);
        int gx1 = std::clamp(static_cast<int>((tx1 - xmin_) / cell_w_), 0, grid_res_ - 1);
        int gy0 = std::clamp(static_cast<int>((ty0 - ymin_) / cell_h_), 0, grid_res_ - 1);
        int gy1 = std::clamp(static_cast<int>((ty1 - ymin_) / cell_h_), 0, grid_res_ - 1);
        for (int gy = gy0; gy <= gy1; ++gy) {
            for (int gx = gx0; gx <= gx1; ++gx) {
                buckets_[gy * grid_res_ + gx].push_back(ti);
            }
        }
    }
}

int TinSurface::find_simplex(double x, double y, double& w0, double& w1, double& w2) const {
    if (simplices_.empty()) return -1;
    if (x < xmin_ - 1e-6 || x > xmax_ + 1e-6 || y < ymin_ - 1e-6 || y > ymax_ + 1e-6) {
        return -1;
    }
    int gx = std::clamp(static_cast<int>((x - xmin_) / cell_w_), 0, grid_res_ - 1);
    int gy = std::clamp(static_cast<int>((y - ymin_) / cell_h_), 0, grid_res_ - 1);
    const auto& cand = buckets_[gy * grid_res_ + gx];
    for (int ti : cand) {
        const auto& t = simplices_[ti];
        const XY& a = pts_[t.a];
        const XY& b = pts_[t.b];
        const XY& c = pts_[t.c];
        double denom = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
        if (std::abs(denom) <= 1e-18) continue;
        double u = ((b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)) / denom;
        double v = ((c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)) / denom;
        double w = 1.0 - u - v;
        if (u >= -1e-9 && v >= -1e-9 && w >= -1e-9) {
            w0 = u;
            w1 = v;
            w2 = w;
            return ti;
        }
    }
    return -1;
}

std::pair<double, double> TinSurface::interpolate(double x, double y) const {
    double w0 = 0.0, w1 = 0.0, w2 = 0.0;
    int ti = find_simplex(x, y, w0, w1, w2);
    if (ti < 0) {
        double nan = std::numeric_limits<double>::quiet_NaN();
        return {nan, nan};
    }
    const auto& t = simplices_[ti];
    double nat = w0 * nat_z_[t.a] + w1 * nat_z_[t.b] + w2 * nat_z_[t.c];
    double des = w0 * des_z_[t.a] + w1 * des_z_[t.b] + w2 * des_z_[t.c];
    return {nat, des};
}

namespace {

// 最小二乘平面 z = a*(x-x0) + b*(y-y0) + c 拟合（带高斯消元）
double fit_local_plane(const std::vector<XY>& pts,
                       const std::vector<double>& vals,
                       const std::vector<int>& idxs,
                       double x0, double y0) {
    if (idxs.empty()) return std::numeric_limits<double>::quiet_NaN();
    double m[3][4] = {};
    for (int k : idxs) {
        double dx = pts[k].x - x0;
        double dy = pts[k].y - y0;
        double z = vals[k];
        m[0][0] += dx * dx; m[0][1] += dx * dy; m[0][2] += dx; m[0][3] += dx * z;
        m[1][0] += dx * dy; m[1][1] += dy * dy; m[1][2] += dy; m[1][3] += dy * z;
        m[2][0] += dx;      m[2][1] += dy;      m[2][2] += 1;  m[2][3] += z;
    }
    // 部分主元高斯消元
    for (int col = 0; col < 3; ++col) {
        int piv = col;
        for (int r = col + 1; r < 3; ++r) {
            if (std::abs(m[r][col]) > std::abs(m[piv][col])) piv = r;
        }
        if (std::abs(m[piv][col]) < 1e-12) {
            return vals[idxs.front()];
        }
        if (piv != col) {
            for (int j = col; j < 4; ++j) std::swap(m[piv][j], m[col][j]);
        }
        for (int r = col + 1; r < 3; ++r) {
            double f = m[r][col] / m[col][col];
            for (int j = col; j < 4; ++j) m[r][j] -= f * m[col][j];
        }
    }
    double c2 = m[2][3] / m[2][2];
    return std::isfinite(c2) ? c2 : vals[idxs.front()];
}

} // namespace

std::tuple<double, double, bool> TinSurface::extrapolate(double x, double y) const {
    auto [nat, des] = interpolate(x, y);
    if (std::isfinite(nat) && std::isfinite(des)) {
        return {nat, des, false};
    }
    if (pts_.empty()) {
        double nan = std::numeric_limits<double>::quiet_NaN();
        return {nan, nan, true};
    }
    std::vector<std::pair<double, int>> dists(pts_.size());
    for (int i = 0; i < static_cast<int>(pts_.size()); ++i) {
        double dx = pts_[i].x - x;
        double dy = pts_[i].y - y;
        dists[i] = {dx * dx + dy * dy, i};
    }
    size_t k = std::min<size_t>(8, pts_.size());
    std::partial_sort(dists.begin(), dists.begin() + k, dists.end());
    std::vector<int> nearest(k);
    for (size_t i = 0; i < k; ++i) nearest[i] = dists[i].second;

    double nat_ext = fit_local_plane(pts_, nat_z_, nearest, x, y);
    double des_ext = fit_local_plane(pts_, des_z_, nearest, x, y);
    return {nat_ext, des_ext, true};
}

std::vector<XY> TinSurface::hull() const {
    return convex_hull(pts_);
}

} // namespace tin
