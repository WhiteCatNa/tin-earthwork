#pragma once

#include <cmath>
#include <limits>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace tin {

struct XY {
    double x = 0.0;
    double y = 0.0;

    bool operator==(const XY& other) const {
        return x == other.x && y == other.y;
    }
    bool operator!=(const XY& other) const {
        return !(*this == other);
    }
};

using Segment = std::pair<XY, XY>;

struct SurveyPoint {
    std::string id;
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;         // 实测高程（或前期高程）
    double design_z = 0.0;  // 设计高程（或后期高程）
    double delta_z = 0.0;   // 高差 = z - design_z（正为挖、负为填）
    bool has_design_z = false;
};

struct PlaneDesign {
    double x0 = 0.0;
    double y0 = 0.0;
    double h0 = 0.0;
    double slope_x = 0.0;   // %，沿 +X 升高为正
    double slope_y = 0.0;   // %，沿 +Y 升高为正

    double elevation(double x, double y) const {
        return h0 + (slope_x / 100.0) * (x - x0) + (slope_y / 100.0) * (y - y0);
    }
};

struct Breakline {
    std::string name;
    std::vector<SurveyPoint> points; // 每个顶点含 x, y, z
    bool closed = false;
};

struct SoilFactors {
    double loose = 1.0;   // 最初可松性系数 Ks
    double compact = 1.0; // 最终可松性系数 Ks'

    bool is_default() const {
        return std::abs(loose - 1.0) < 1e-12 && std::abs(compact - 1.0) < 1e-12;
    }
};

struct SoilBalance {
    double cut = 0.0;
    double fill = 0.0;
    SoilFactors factors;

    double cut_loose() const { return cut * factors.loose; }
    double fill_natural() const { return fill / factors.compact; }
    double surplus() const { return cut - fill_natural(); }
    double export_loose() const { return std::max(surplus(), 0.0) * factors.loose; }
    double borrow_natural() const { return std::max(-surplus(), 0.0); }
};

struct Triangle {
    int id = 0;
    int v0 = 0, v1 = 0, v2 = 0;
    SurveyPoint p0, p1, p2;
    double area = 0.0;
    double avg_delta_z = 0.0;
    double volume = 0.0;
    double cut_volume = 0.0;
    double fill_volume = 0.0;
    double cut_area = 0.0;
    double fill_area = 0.0;
    bool is_boundary = false;
    bool is_clipped = false;
    bool is_mixed = false;
    std::vector<XY> cut_polygon;
    std::vector<XY> fill_polygon;
    std::vector<Segment> zero_segments;
};

struct GridCorner {
    std::string name;
    int row = 0;
    int col = 0;
    double x = 0.0;
    double y = 0.0;
    double natural = std::numeric_limits<double>::quiet_NaN();
    double design = std::numeric_limits<double>::quiet_NaN();
    bool extrapolated = false;

    bool valid() const { return std::isfinite(natural) && std::isfinite(design); }
    double height() const { return natural - design; }
};

struct GridCell {
    std::string name;
    int row = 0;
    int col = 0;
    double x0 = 0.0, y0 = 0.0, x1 = 0.0, y1 = 0.0;
    double area = 0.0;
    double cut = 0.0;
    double fill = 0.0;
    bool clipped = false;
    double net() const { return cut - fill; }
};

struct GridCheckResult {
    double spacing = 10.0;
    XY origin;
    int rows = 0;
    int cols = 0;
    std::vector<GridCorner> corners;
    std::vector<GridCell> cells;
    double total_cut = 0.0;
    double total_fill = 0.0;
    double computed_area = 0.0;
    int skipped_cells = 0;
    std::vector<std::string> warnings;

    double net_volume() const { return total_cut - total_fill; }
};

struct ContourLine {
    double level = 0.0;
    bool major = false;
    std::vector<XY> polyline;
};

struct ContourSet {
    double interval = 1.0;
    int major_step = 5;
    std::vector<Segment> minor_segments;
    std::vector<Segment> major_segments;
    std::vector<ContourLine> lines;
};

struct BalanceResult {
    double elevation = 0.0;
    double cut_volume = 0.0;
    double fill_volume = 0.0;
    double net_volume = 0.0;
    double computed_area = 0.0;
    double natural_min = 0.0;
    double natural_max = 0.0;
    double natural_mean = 0.0;
};

struct ProfileResult {
    XY start;
    XY end;
    std::vector<double> distances;
    std::vector<double> natural_z;
    std::vector<double> design_z;
    std::vector<double> delta_z;
    double length = 0.0;
    double max_cut_depth = 0.0;
    double max_fill_depth = 0.0;
    double cut_area = 0.0;
    double fill_area = 0.0;
    bool is_compare = false;
};

struct ValidationIssues {
    std::vector<std::string> missing_values;
    std::vector<std::string> duplicate_coords;
    std::vector<std::string> duplicate_ids;
    std::vector<std::string> coord_outliers;
    std::vector<std::string> elevation_outliers;
    std::vector<std::string> format_errors;

    int total_count() const {
        return static_cast<int>(missing_values.size() + duplicate_coords.size() +
                                duplicate_ids.size() + coord_outliers.size() +
                                elevation_outliers.size() + format_errors.size());
    }
};

enum class DesignMode {
    Flat,
    Plane,
    Compare
};

struct CalculationSettings {
    DesignMode design_mode = DesignMode::Flat;
    double design_elevation = 0.0;
    PlaneDesign design_plane;
    bool use_partition = false;
    std::map<std::string, double> partition;
    bool grid_enabled = false;
    double grid_spacing = 10.0;
    bool contours_enabled = true;
    double contour_interval = 0.0; // <= 0 自动计算
    double max_edge_length = 0.0;  // <= 0 不剔除
    SoilFactors soil_factors;
};

struct CalculationResult {
    double total_cut = 0.0;
    double total_fill = 0.0;
    double net_volume = 0.0;
    int triangle_count = 0;
    int mixed_triangle_count = 0;
    int clipped_triangle_count = 0;
    double computed_area = 0.0;
    double boundary_area = 0.0;
    double hole_area = 0.0;
    double design_min = 0.0;
    double design_max = 0.0;
    double elapsed_ms = 0.0;
    bool is_compare = false;
    std::string design_description;
    int compare_point_count = 0;
    std::vector<Triangle> triangles;
    std::vector<XY> boundary_points;
    std::vector<std::vector<XY>> holes;
    std::vector<std::vector<XY>> breakline_polylines;
    std::vector<std::vector<XY>> trimmed_triangles;
    std::vector<std::string> warnings;
    std::optional<GridCheckResult> grid_check;
    std::optional<ContourSet> contours;
    SoilBalance soil_balance;

    bool uniform_design() const { return std::abs(design_max - design_min) < 1e-9; }
};

struct ProjectData {
    std::string project_name = "TIN土方计算项目";
    std::vector<SurveyPoint> points;
    std::vector<XY> boundary;
    std::vector<std::vector<XY>> holes;
    std::vector<Breakline> breaklines;
    std::vector<SurveyPoint> compare_points;
    std::string compare_source;
    CalculationSettings settings;
};

} // namespace tin
