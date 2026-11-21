#pragma once

#include "core/delaunay.hpp"
#include "core/types.hpp"
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace tin {

class TINEarthworkCalculator {
public:
    explicit TINEarthworkCalculator(double design_elevation = 0.0);

    void add_points(const std::vector<SurveyPoint>& points);
    void set_design_elevation(double elevation);
    void set_design_elevations(const std::map<std::string, double>& overrides, double default_elev);
    void set_design_plane(const PlaneDesign& plane, const std::map<std::string, double>& overrides = {});
    void set_compare_points(const std::vector<SurveyPoint>& points);
    void set_boundary(const std::vector<XY>& boundary);
    void set_holes(const std::vector<std::vector<XY>>& holes, bool validate = true);
    void set_breaklines(const std::vector<Breakline>& breaklines);
    void set_max_edge_length(double length);
    void set_soil_factors(const SoilFactors& factors);

    void build_tin();
    void build_from_mesh(const std::vector<SurveyPoint>& vertex_points, const std::vector<TriIndex>& simplices);
    CalculationResult calculate_volumes();
    CalculationResult run_full_calculation(std::optional<double> design_elevation = std::nullopt);

    // 曲面插值与裁剪辅助（供方格网、等高线、纵断面复用）
    std::tuple<double, double, bool> sample_surface(double x, double y, bool extrapolate = false) const;
    std::vector<XY> coverage_polygon() const;
    std::vector<Segment> clip_to_region(int tri_id, const XY& start, const XY& end) const;
    std::vector<Segment> level_segments(bool use_design, double level) const;

    const std::vector<SurveyPoint>& points() const { return points_; }
    const std::vector<SurveyPoint>& compare_points() const { return compare_points_; }
    const std::vector<XY>& boundary() const { return boundary_; }
    const std::vector<std::vector<XY>>& holes() const { return holes_; }
    const std::vector<Breakline>& breaklines() const { return breaklines_; }
    const std::vector<Triangle>& triangles() const { return triangles_; }
    double max_edge_length() const { return max_edge_length_; }
    const SoilFactors& soil_factors() const { return soil_factors_; }
    bool is_compare() const { return !compare_points_.empty(); }
    bool has_tin() const { return !tin_a_.empty(); }

private:
    void apply_design();
    std::vector<int> unique_point_indices(const std::vector<SurveyPoint>& pts, const std::string& label);
    void triangulate_design(std::vector<SurveyPoint>& out_pts, std::vector<XY>& out_local, std::vector<TriIndex>& out_tris);
    void triangulate_compare(std::vector<SurveyPoint>& out_pts, std::vector<XY>& out_local, std::vector<TriIndex>& out_tris);
    void classify(const std::vector<SurveyPoint>& pts, const std::vector<XY>& local, const std::vector<TriIndex>& simplices);
    void calculate_triangle(Triangle& tri);
    void subtract_holes(Triangle& tri);
    std::vector<XY> prepare_boundary() const;
    std::vector<std::vector<XY>> prepare_holes(const std::vector<XY>& ring) const;
    XY to_global(const XY& p) const { return {p.x + origin_.x, p.y + origin_.y}; }

    double design_elevation_ = 0.0;
    std::optional<PlaneDesign> design_plane_;
    std::map<std::string, double> design_overrides_;
    std::vector<SurveyPoint> points_;
    std::vector<SurveyPoint> compare_points_;
    std::vector<XY> boundary_;
    std::vector<std::vector<XY>> holes_;
    bool validate_holes_ = true;
    std::vector<Breakline> breaklines_;
    double max_edge_length_ = 0.0;
    SoilFactors soil_factors_;

    XY origin_{0.0, 0.0};
    double eps_ = 1e-9;
    double boundary_area_ = 0.0;
    double hole_area_ = 0.0;
    std::optional<double> overlap_ratio_;
    std::vector<XY> ring_;
    std::vector<std::vector<XY>> hole_rings_;
    std::map<int, std::vector<XY>> regions_;
    std::map<int, std::vector<std::vector<XY>>> hole_regions_;

    std::vector<SurveyPoint> extra_points_;
    std::vector<Triangle> triangles_;
    std::vector<std::vector<XY>> trimmed_triangles_;
    std::vector<std::vector<XY>> breakline_polylines_;
    std::vector<std::string> warnings_;

    TinSurface tin_a_;
    TinSurface tin_b_;
};

} // namespace tin
