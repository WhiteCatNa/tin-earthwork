#pragma once

#include "core/ai_optimizer.hpp"
#include "core/engineering.hpp"
#include "core/types.hpp"
#include "imgui.h"
#include <functional>
#include <optional>
#include <vector>

namespace tin::ui {

enum class CadToolMode {
    Select = 0,
    DrawBoundary,
    DrawHole,
    DrawBreakline,
    PickProfile,
    MeasureDist,
    MeasureArea
};

struct LayerVisibility {
    bool cut_fill = true;
    bool tin = true;
    bool zero_contour = true;
    bool contours = true;
    bool grid = true;
    bool points = true;
    bool point_labels = false;
    bool boundary = true;
    bool holes = true;
    bool breaklines = true;
    bool profile = true;
    bool sections = true;
    bool haul = true;
    bool slope = true;
    bool anomalies = true;
    bool survey_recs = true;
};

struct CadCanvasCallbacks {
    std::function<void(int tri_id)> on_triangle_selected;
    std::function<void(const XY& a, const XY& b)> on_profile_picked;
    std::function<void(const std::vector<XY>& pts, CadToolMode tool)> on_draft_committed;
    std::function<void(const std::string& msg)> on_status_hint;
};

class CadCanvas {
public:
    LayerVisibility layers;
    CadToolMode tool = CadToolMode::Select;
    bool dark_mode = true;
    bool osnap_enabled = true;

    void fit_view(const std::vector<SurveyPoint>& points,
                  const std::vector<XY>& boundary,
                  const ImVec2& view_size = ImVec2(900.0f, 650.0f));

    void focus_triangle(const Triangle& tri);
    void focus_point(double x, double y, double target_span_m = 45.0);

    void render(const ProjectData& project,
                const CalculationResult* result,
                const std::optional<Segment>& profile_line,
                int& selected_tri_id,
                const CadCanvasCallbacks& callbacks,
                const HaulPlanResult* haul = nullptr,
                const SlopeGradingResult* slope = nullptr,
                const SiteHealthReport* health = nullptr,
                const UncertaintyReport* uncertainty = nullptr);

    // 交互绘制草稿控制
    void clear_draft();
    void commit_draft(const CadCanvasCallbacks& callbacks);
    const std::vector<XY>& draft_points() const { return draft_; }
    const std::vector<XY>& measure_points() const { return measure_pts_; }

    bool has_hover() const { return hover_valid_; }
    XY hover_world() const { return hover_world_; }
    double pixels_per_meter() const { return px_per_m_; }

private:
    ImVec2 world_to_screen(double wx, double wy, const ImVec2& origin, const ImVec2& size) const;
    XY screen_to_world(const ImVec2& sp, const ImVec2& origin, const ImVec2& size) const;
    double nice_step(double target_world) const;
    std::optional<SurveyPoint> find_snap_point(
        const ImVec2& mouse, const std::vector<SurveyPoint>& pts,
        const ImVec2& origin, const ImVec2& size, float radius_px = 14.0f) const;

    double center_x_ = 0.0;
    double center_y_ = 0.0;
    double px_per_m_ = 4.0;
    bool view_initialized_ = false;

    bool hover_valid_ = false;
    XY hover_world_{0.0, 0.0};

    std::vector<XY> draft_;
    std::vector<XY> measure_pts_;
    std::optional<XY> profile_first_;

    bool panning_ = false;
    bool pan_moved_ = false;
    ImVec2 pan_start_mouse_{0.0f, 0.0f};
    double pan_start_cx_ = 0.0;
    double pan_start_cy_ = 0.0;
};

// 渲染右侧「纵断面图」独立 GPU 交互图窗
void render_profile_chart(const ProfileResult* profile, bool dark_mode, float height = 280.0f);

} // namespace tin::ui
