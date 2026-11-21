#pragma once

#include "core/types.hpp"
#include <optional>
#include <tuple>
#include <vector>

namespace tin {

// 平面多边形与几何裁剪工具（全面支持凹多边形、挖空区与精确线性积分）
double signed_area(const std::vector<XY>& ring);
std::vector<XY> normalize_ring(const std::vector<XY>& points);
bool segments_cross(const XY& a, const XY& b, const XY& c, const XY& d);
bool ring_self_intersects(const std::vector<XY>& ring);
bool point_in_ring(const XY& pt, const std::vector<XY>& ring);
std::vector<bool> points_in_ring(const std::vector<XY>& pts, const std::vector<XY>& ring);

std::vector<XY> clip_ring_halfplane(const std::vector<XY>& poly, double a, double b, double c);
std::vector<XY> clip_ring_to_triangle(const std::vector<XY>& ring, const XY& p0, const XY& p1, const XY& p2);
std::vector<XY> clip_convex_polygons(const std::vector<XY>& subject, const std::vector<XY>& clipper);

std::pair<std::vector<XY>, std::vector<double>> clip_by_sign(
    const std::vector<XY>& poly, const std::vector<double>& values, bool positive);

std::pair<double, double> polygon_integral(const std::vector<XY>& poly, const std::vector<double>& values);

std::optional<std::tuple<double, double, double>> linear_plane(
    const XY& p0, const XY& p1, const XY& p2, double v0, double v1, double v2, double eps);

std::vector<XY> zero_crossing_points(const std::vector<XY>& poly, const std::vector<double>& values);

std::vector<Segment> clip_segment_to_ring(const XY& start, const XY& end, const std::vector<XY>& ring, bool inside = true);

bool triangle_touches_ring(const XY& p0, const XY& p1, const XY& p2, const std::vector<XY>& ring, double eps);

} // namespace tin
