#pragma once

#include "core/ai_assistant.hpp"
#include "core/ai_optimizer.hpp"
#include "core/analysis.hpp"
#include "core/calculator.hpp"
#include "core/engineering.hpp"
#include "core/types.hpp"
#include "ui/cad_canvas.hpp"
#include "ui/theme.hpp"
#include "ui/viewport_3d.hpp"

#include <optional>
#include <string>
#include <vector>

namespace tin::ui {

// 南方CASS 10/11 风格全功能 TIN 土方算量桌面工作站主控类
class CassApp {
public:
    CassApp(NativeWindowHandle hwnd, const ThemeFonts& fonts);

    // 每帧渲染（覆盖全窗口工作区：菜单栏 + 工具栏 + 左侧屏幕菜单 + 中央CAD视口 + 右侧属性页 + 命令行 + 状态栏）
    void render();

    // 数据与工程控制（同时供交互界面与 --demo / CLI 自动化调用）
    bool load_points_from_path(const std::string& utf8_path);
    bool load_compare_from_path(const std::string& utf8_path);
    bool load_project_from_path(const std::string& utf8_path);
    bool save_project_to_path(const std::string& utf8_path);

    void generate_rect_boundary(double margin = 0.0);
    void generate_auto_outline();
    void clear_boundary();
    bool undo();
    bool redo();
    void enable_grid_check(bool enabled, double spacing = 15.0);
    void enable_contours(bool enabled, double interval = 0.0);
    void set_design_elevation(double elev);

    bool run_calculation();
    bool solve_balance_h0();
    bool solve_best_fit_plane_action();
    bool solve_balance_plane_action();
    bool run_sections_action(double spacing = 20.0, double azimuth_deg = 0.0);
    bool compute_strata_action();
    bool compute_haul_action();
    bool compute_slope_action();
    bool run_grade_optimization_action();
    bool apply_optimized_plane_action();
    bool run_anomaly_diagnosis_action();
    bool clean_anomalies_action();
    bool run_uncertainty_action();
    bool run_significance_action();
    bool run_thinning_action(double tol_m = 0.15);
    bool run_drainage_action(double min_slope_pct = 0.3);
    bool run_ai_full_workflow_action();
    void set_3d_view(bool enabled);
    void set_tool_mode(CadToolMode mode);
    bool generate_quick_profile(bool horizontal = true);

    void set_inspector_tab(int idx);
    void set_dark_mode(bool dark);
    void set_selected_triangle(int tri_id);
    int first_mixed_triangle_id() const;
    void zoom_extents();

    const CalculationResult* result() const { return result_ ? &(*result_) : nullptr; }
    const GradeOptimizationResult* grade_opt_result() const { return grade_opt_result_ ? &(*grade_opt_result_) : nullptr; }
    const SiteHealthReport* health_report() const { return health_report_ ? &(*health_report_) : nullptr; }
    const UncertaintyReport* uncertainty_report() const { return uncertainty_report_ ? &(*uncertainty_report_) : nullptr; }
    const SignificanceReport* significance_report() const { return significance_report_ ? &(*significance_report_) : nullptr; }
    const ThinningResult* thinning_result() const { return thinning_result_ ? &(*thinning_result_) : nullptr; }
    const DrainageReport* drainage_report() const { return drainage_report_ ? &(*drainage_report_) : nullptr; }
    const DiagnosisSummary* diagnosis_summary() const { return diagnosis_summary_ ? &(*diagnosis_summary_) : nullptr; }
    const ProjectData& project() const { return project_; }
    double min_z() const { return min_nat_z_; }
    double max_z() const { return max_nat_z_; }
    double avg_z() const { return avg_nat_z_; }

    // CAD 控制台输出
    void log_cmd(const std::string& line);
    void execute_command(const std::string& raw_cmd);

private:
    void render_menu_bar();
    void render_quick_toolbar();
    void render_left_screen_menu(float width, float height);
    void render_center_viewport(float width, float height);
    void render_kpi_strip();
    void render_layer_strip();
    void render_right_inspector(float width, float height);
    void render_tab_params();
    void render_tab_points();
    void render_tab_summary();
    void render_tab_profile();
    void render_tab_boundary();
    void render_tab_engineering();
    void render_tab_ai();
    void render_command_console(float height);
    void render_status_bar(float height);
    void render_modals();

    void configure_calculator(TINEarthworkCalculator& calc) const;
    void update_point_stats();
    void sync_project_name_buffer();
    void push_undo_snapshot();
    void handle_draft_committed(const std::vector<XY>& pts, CadToolMode tool);
    void handle_profile_picked(const XY& a, const XY& b);

    // 原生 Windows UTF-16 文件对话框
    std::optional<std::string> open_file_dialog(const wchar_t* title, const wchar_t* filter) const;
    std::optional<std::string> save_file_dialog(const wchar_t* title, const wchar_t* filter,
                                                const wchar_t* default_ext,
                                                const std::string& default_name) const;

    // 交互文件动作
    void action_open_project();
    void action_save_project(bool save_as = false);
    void action_import_points();
    void action_import_compare();
    void action_import_boundary();
    void action_import_breaklines_dxf();
    void action_export_summary_csv();
    void action_export_detail_csv();
    void action_export_report_txt();
    void action_export_dxf();
    void action_export_cass_dat();
    void action_export_sections_csv();

    [[maybe_unused]] NativeWindowHandle hwnd_ = nullptr;
    ThemeFonts fonts_;

    ProjectData project_;
    ValidationIssues issues_;
    std::optional<CalculationResult> result_;
    std::optional<ProfileResult> profile_result_;
    std::optional<Segment> profile_line_;
    std::optional<BalanceResult> last_balance_;
    std::optional<StrataResult> strata_result_;
    std::optional<HaulPlanResult> haul_result_;
    std::optional<SlopeGradingResult> slope_result_;
    std::optional<GradeOptimizationResult> grade_opt_result_;
    std::optional<SiteHealthReport> health_report_;
    std::optional<UncertaintyReport> uncertainty_report_;
    std::optional<SignificanceReport> significance_report_;
    std::optional<ThinningResult> thinning_result_;
    std::optional<DrainageReport> drainage_report_;
    std::optional<DiagnosisSummary> diagnosis_summary_;
    std::optional<std::string> current_project_path_;

    CadCanvas canvas_;
    Viewport3D viewport_3d_;
    bool show_3d_view_ = false;
    int selected_tri_id_ = -1;
    int pending_tab_ = -1;
    bool show_screen_menu_ = true;
    bool show_kpi_strip_ = true;
    bool show_about_modal_ = false;
    bool show_help_modal_ = false;
    bool focus_cmd_input_ = false;

    double min_nat_z_ = 0.0;
    double max_nat_z_ = 0.0;
    double avg_nat_z_ = 0.0;
    double min_x_ = 0.0;
    double max_x_ = 0.0;
    double min_y_ = 0.0;
    double max_y_ = 0.0;

    std::string status_text_;
    std::vector<std::string> console_lines_;
    std::vector<std::string> cmd_history_;
    int history_pos_ = -1;
    char cmd_buf_[512]{};
    char proj_name_buf_[256]{};
    char point_filter_buf_[64]{};
    int tri_filter_mode_ = 0; // 0=全部 1=挖方 2=填方 3=挖填混合 4=边界裁剪
    int compare_invert_ = 0;
    double section_spacing_ = 20.0;
    double section_azimuth_ = 0.0;
    int selected_section_idx_ = -1;

    int strata_mode_idx_ = 1; // 0=标高分层 1=深度分层
    char strata_bounds_buf_[128] = "0.2, 0.4";
    int haul_cols_ = 4;
    int haul_rows_ = 3;
    double slope_cut_ratio_ = 1.0;
    double slope_fill_ratio_ = 1.5;

    HaulCostParams cost_params_;
    GradeOptConstraints opt_constraints_;
    int recommend_count_ = 8;
    double measure_sigma_ = 0.03;
    double thin_tol_m_ = 0.15;
    double drain_min_slope_ = 0.3;

    struct EditSnapshot {
        std::vector<XY> boundary;
        std::vector<std::vector<XY>> holes;
        std::vector<Breakline> breaklines;
        CalculationSettings settings;
        std::optional<std::vector<SurveyPoint>> points;
    };
    std::vector<EditSnapshot> undo_stack_;
    std::vector<EditSnapshot> redo_stack_;
};

} // namespace tin::ui
