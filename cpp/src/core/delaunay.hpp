#pragma once

#include "core/types.hpp"
#include <array>
#include <set>
#include <utility>
#include <vector>

namespace tin {

struct TriIndex {
    int a = 0, b = 0, c = 0;
};

struct ConstrainedDelaunayResult {
    std::vector<SurveyPoint> all_points; // 原有唯一点 + 特征线交点/内部点
    std::vector<TriIndex> simplices;     // 逆时针三角形顶点索引
    std::set<std::pair<int, int>> constrained_edges;
    int base_point_count = 0;
};

// 普通 Delaunay 三角剖分（Bowyer-Watson + 局部归一化坐标，数值稳定）
std::vector<TriIndex> build_delaunay(const std::vector<XY>& points);

// 带特征线约束的 Delaunay 三角剖分（CDT）
ConstrainedDelaunayResult build_constrained_delaunay(
    const std::vector<SurveyPoint>& base_points,
    const std::vector<Breakline>& breaklines,
    double tol = 1e-3);

// 凸包（逆时针顶点）
std::vector<XY> convex_hull(const std::vector<XY>& points);

// 沿测点外轮廓自动生成边界（χ-shape：max_edge < 0 自动估算，== 0 返回凸包）
std::pair<std::vector<XY>, double> outline_from_points(
    const std::vector<XY>& points, double max_edge = -1.0);

// 网格空间加速的 TIN 曲面线性插值与网外最小二乘外推
class TinSurface {
public:
    void build(const std::vector<XY>& pts,
               const std::vector<TriIndex>& simplices,
               const std::vector<double>& natural_z,
               const std::vector<double>& design_z);

    // 返回 {natural, design}，网外返回 {NaN, NaN}
    std::pair<double, double> interpolate(double x, double y) const;
    // 返回 {natural, design, extrapolated}
    std::tuple<double, double, bool> extrapolate(double x, double y) const;
    std::vector<XY> hull() const;

    bool empty() const { return simplices_.empty(); }

private:
    int find_simplex(double x, double y, double& w0, double& w1, double& w2) const;

    std::vector<XY> pts_;
    std::vector<TriIndex> simplices_;
    std::vector<double> nat_z_;
    std::vector<double> des_z_;

    double xmin_ = 0.0, ymin_ = 0.0, xmax_ = 0.0, ymax_ = 0.0;
    double cell_w_ = 1.0, cell_h_ = 1.0;
    int grid_res_ = 1;
    std::vector<std::vector<int>> buckets_;
};

} // namespace tin
