#include "ui/app.hpp"

#include "core/geometry.hpp"
#include "io/io.hpp"
#include "imgui.h"

#include <commdlg.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace tin::ui {

namespace {

std::string format_num(double v, int decimals = 2) {
    if (!std::isfinite(v)) return "--";
    bool neg = v < 0.0;
    double abs_v = std::abs(v);
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(decimals) << abs_v;
    std::string raw = oss.str();

    size_t dot = raw.find('.');
    std::string int_part = (dot == std::string::npos) ? raw : raw.substr(0, dot);
    std::string frac_part = (dot == std::string::npos) ? "" : raw.substr(dot);

    std::string grouped;
    grouped.reserve(int_part.size() + int_part.size() / 3 + 2);
    int cnt = 0;
    for (auto it = int_part.rbegin(); it != int_part.rend(); ++it) {
        if (cnt > 0 && cnt % 3 == 0) grouped.push_back(',');
        grouped.push_back(*it);
        ++cnt;
    }
    std::reverse(grouped.begin(), grouped.end());
    if (neg && abs_v >= 0.5 * std::pow(10.0, -decimals)) {
        grouped.insert(grouped.begin(), '-');
    }
    return grouped + frac_part;
}

std::string trim_copy(const std::string& s) {
    size_t a = 0;
    while (a < s.size() && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    size_t b = s.size();
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

std::string upper_ascii(std::string s) {
    for (char& ch : s) {
        ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
    }
    return s;
}

const char* tool_name_cn(CadToolMode mode) {
    switch (mode) {
    case CadToolMode::Select: return "选择/查询";
    case CadToolMode::DrawBoundary: return "绘制计算边界";
    case CadToolMode::DrawHole: return "绘制挖空区";
    case CadToolMode::DrawBreakline: return "绘制特征线";
    case CadToolMode::PickProfile: return "拾取纵断面";
    }
    return "选择/查询";
}

bool colored_button(const char* label, const ImVec4& bg, const ImVec4& hover, const ImVec4& active,
                    const ImVec2& size = ImVec2(0, 0)) {
    ImGui::PushStyleColor(ImGuiCol_Button, bg);
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, hover);
    ImGui::PushStyleColor(ImGuiCol_ButtonActive, active);
    bool clicked = ImGui::Button(label, size);
    ImGui::PopStyleColor(3);
    return clicked;
}

void draw_section_bar(const char* title) {
    ImDrawList* dl = ImGui::GetWindowDrawList();
    ImVec2 p = ImGui::GetCursorScreenPos();
    float w = ImGui::GetContentRegionAvail().x;
    float h = ImGui::GetTextLineHeight() + 6.0f;
    dl->AddRectFilled(p, ImVec2(p.x + w, p.y + h), IM_COL32(0x26, 0x35, 0x48, 255), 2.0f);
    dl->AddRectFilled(p, ImVec2(p.x + 3.0f, p.y + h), IM_COL32(0x38, 0x98, 0xF8, 255));
    dl->AddText(ImVec2(p.x + 8.0f, p.y + 3.0f), IM_COL32(0xD8, 0xE6, 0xF5, 255), title);
    ImGui::Dummy(ImVec2(w, h + 2.0f));
}

int command_history_callback(ImGuiInputTextCallbackData* data) {
    auto* hist = static_cast<std::vector<std::string>*>(data->UserData);
    // Callback receives struct { vector*, int* pos }
    struct HistCtx {
        std::vector<std::string>* list;
        int* pos;
    };
    auto* ctx = static_cast<HistCtx*>(data->UserData);
    (void)hist;
    if (!ctx || !ctx->list || ctx->list->empty()) return 0;

    if (data->EventFlag == ImGuiInputTextFlags_CallbackHistory) {
        int n = static_cast<int>(ctx->list->size());
        int prev = *ctx->pos;
        if (data->EventKey == ImGuiKey_UpArrow) {
            if (*ctx->pos == -1) *ctx->pos = n - 1;
            else if (*ctx->pos > 0) --(*ctx->pos);
        } else if (data->EventKey == ImGuiKey_DownArrow) {
            if (*ctx->pos != -1) {
                ++(*ctx->pos);
                if (*ctx->pos >= n) *ctx->pos = -1;
            }
        }
        if (prev != *ctx->pos) {
            const char* text = (*ctx->pos >= 0 && *ctx->pos < n) ? (*ctx->list)[*ctx->pos].c_str() : "";
            data->DeleteChars(0, data->BufTextLen);
            data->InsertChars(0, text);
        }
    }
    return 0;
}

} // namespace

CassApp::CassApp(HWND hwnd, const ThemeFonts& fonts)
    : hwnd_(hwnd), fonts_(fonts) {
    status_text_ = "就绪 — 请导入高程测点 (.xlsx/.csv/.dat) 或打开工程 (.tinproj.json)";
    sync_project_name_buffer();
    log_cmd("南方CASS TIN土方自动算量工作站 v2.1 (C++20 / Direct3D 11 硬件加速)");
    log_cmd("输入 HELP 查看可用 CAD 快捷指令，按 F5 立即执行 TIN 算量，鼠标滚轮缩放，中键/左键拖拽平移。");
}

void CassApp::log_cmd(const std::string& line) {
    console_lines_.push_back(line);
    if (console_lines_.size() > 300) {
        console_lines_.erase(console_lines_.begin(), console_lines_.begin() + 100);
    }
}

void CassApp::sync_project_name_buffer() {
    std::snprintf(proj_name_buf_, sizeof(proj_name_buf_), "%s", project_.project_name.c_str());
}

void CassApp::update_point_stats() {
    if (project_.points.empty()) {
        min_nat_z_ = max_nat_z_ = avg_nat_z_ = 0.0;
        min_x_ = max_x_ = min_y_ = max_y_ = 0.0;
        issues_ = {};
        return;
    }
    min_nat_z_ = max_nat_z_ = project_.points[0].z;
    min_x_ = max_x_ = project_.points[0].x;
    min_y_ = max_y_ = project_.points[0].y;
    double sum_z = 0.0;
    for (const auto& p : project_.points) {
        min_nat_z_ = std::min(min_nat_z_, p.z);
        max_nat_z_ = std::max(max_nat_z_, p.z);
        min_x_ = std::min(min_x_, p.x);
        max_x_ = std::max(max_x_, p.x);
        min_y_ = std::min(min_y_, p.y);
        max_y_ = std::max(max_y_, p.y);
        sum_z += p.z;
    }
    avg_nat_z_ = sum_z / static_cast<double>(project_.points.size());
    issues_ = validate_points(project_.points);
}

bool CassApp::load_points_from_path(const std::string& utf8_path) {
    try {
        auto imp = import_points_file(utf8_path);
        project_.points = std::move(imp.points);
        issues_ = std::move(imp.issues);
        result_.reset();
        profile_result_.reset();
        profile_line_.reset();
        last_balance_.reset();
        selected_tri_id_ = -1;
        update_point_stats();

        // 若默认高程尚未配置或为0且实测高程明显不为0，设置合理初值
        if (!project_.points.empty()) {
            double mid_z = std::round(((min_nat_z_ + max_nat_z_) * 0.5) * 100.0) / 100.0;
            if (std::abs(project_.settings.design_elevation) < 1e-9 &&
                (min_nat_z_ > 1.0 || max_nat_z_ < -1.0)) {
                project_.settings.design_elevation = mid_z;
            }
            project_.settings.design_plane.x0 = (min_x_ + max_x_) * 0.5;
            project_.settings.design_plane.y0 = (min_y_ + max_y_) * 0.5;
            project_.settings.design_plane.h0 = mid_z;
        }

        std::filesystem::path fp = std::filesystem::path(utf8_to_wide(utf8_path));
        if (project_.project_name == "TIN土方计算项目" || project_.project_name.empty()) {
            project_.project_name = wide_to_utf8(fp.stem().wstring());
            sync_project_name_buffer();
        }
        zoom_extents();

        std::ostringstream oss;
        oss << "已导入测点 " << project_.points.size() << " 个 [" << imp.detected_format
            << "]，高程范围 " << format_num(min_nat_z_, 2) << " ~ " << format_num(max_nat_z_, 2)
            << " m，质量告警 " << issues_.total_count() << " 项";
        status_text_ = oss.str();
        log_cmd("命令: IMPORT_PTS -> " + oss.str());
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("导入测点失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::load_compare_from_path(const std::string& utf8_path) {
    try {
        auto imp = import_points_file(utf8_path);
        project_.compare_points = std::move(imp.points);
        project_.compare_source = utf8_path;
        project_.settings.design_mode = DesignMode::Compare;
        result_.reset();
        std::ostringstream oss;
        oss << "已加载对比期测点 " << project_.compare_points.size() << " 个，已切换至两期对比模式";
        status_text_ = oss.str();
        log_cmd("命令: IMPORT_COMPARE -> " + oss.str());
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("导入对比期测点失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::load_project_from_path(const std::string& utf8_path) {
    try {
        ProjectData loaded = load_project_json(utf8_path);
        project_ = std::move(loaded);
        current_project_path_ = utf8_path;
        sync_project_name_buffer();
        update_point_stats();
        canvas_.layers.contours = project_.settings.contours_enabled;
        canvas_.layers.grid = project_.settings.grid_enabled;
        result_.reset();
        profile_result_.reset();
        profile_line_.reset();
        last_balance_.reset();
        selected_tri_id_ = -1;
        zoom_extents();

        std::ostringstream oss;
        oss << "已打开工程 [" << project_.project_name << "]：" << project_.points.size()
            << " 个测点，边界顶点 " << project_.boundary.size()
            << " 个，挖空区 " << project_.holes.size()
            << " 个，特征线 " << project_.breaklines.size() << " 条";
        status_text_ = oss.str();
        log_cmd("命令: OPENPROJ -> " + oss.str());
        if (project_.points.size() >= 3) {
            run_calculation();
        }
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("打开工程失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::save_project_to_path(const std::string& utf8_path) {
    try {
        save_project_json(utf8_path, project_);
        current_project_path_ = utf8_path;
        status_text_ = "已保存工程文件: " + utf8_path;
        log_cmd("命令: SAVEPROJ -> " + status_text_);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("保存工程失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

void CassApp::push_undo_snapshot() {
    undo_stack_.push_back(EditSnapshot{
        project_.boundary, project_.holes, project_.breaklines, project_.settings
    });
    if (undo_stack_.size() > 50) {
        undo_stack_.erase(undo_stack_.begin());
    }
    redo_stack_.clear();
}

bool CassApp::undo() {
    if (undo_stack_.empty()) {
        status_text_ = "没有可撤销的操作";
        return false;
    }
    redo_stack_.push_back(EditSnapshot{
        project_.boundary, project_.holes, project_.breaklines, project_.settings
    });
    EditSnapshot prev = std::move(undo_stack_.back());
    undo_stack_.pop_back();
    project_.boundary = std::move(prev.boundary);
    project_.holes = std::move(prev.holes);
    project_.breaklines = std::move(prev.breaklines);
    project_.settings = std::move(prev.settings);
    status_text_ = "已撤销上一步编辑 (Ctrl+Z)";
    log_cmd("命令: _UNDO -> " + status_text_);
    if (result_) run_calculation();
    return true;
}

bool CassApp::redo() {
    if (redo_stack_.empty()) {
        status_text_ = "没有可重做的操作";
        return false;
    }
    undo_stack_.push_back(EditSnapshot{
        project_.boundary, project_.holes, project_.breaklines, project_.settings
    });
    EditSnapshot next = std::move(redo_stack_.back());
    redo_stack_.pop_back();
    project_.boundary = std::move(next.boundary);
    project_.holes = std::move(next.holes);
    project_.breaklines = std::move(next.breaklines);
    project_.settings = std::move(next.settings);
    status_text_ = "已重做编辑 (Ctrl+Y)";
    log_cmd("命令: _REDO -> " + status_text_);
    if (result_) run_calculation();
    return true;
}

void CassApp::generate_rect_boundary(double margin) {
    if (project_.points.size() < 3) {
        status_text_ = "测点不足 3 个，无法生成外接矩形边界";
        log_cmd("提示: " + status_text_);
        return;
    }
    push_undo_snapshot();
    update_point_stats();
    project_.boundary = {
        {min_x_ - margin, min_y_ - margin},
        {max_x_ + margin, min_y_ - margin},
        {max_x_ + margin, max_y_ + margin},
        {min_x_ - margin, max_y_ + margin},
    };
    double area = std::abs(signed_area(project_.boundary));
    std::ostringstream oss;
    oss << "已生成外接矩形计算边界（面积 " << format_num(area, 2) << " m²）";
    status_text_ = oss.str();
    log_cmd("命令: _AUTOB -> " + oss.str());
}

void CassApp::generate_auto_outline() {
    if (project_.points.size() < 3) {
        status_text_ = "测点不足 3 个，无法提取自动轮廓边界";
        log_cmd("提示: " + status_text_);
        return;
    }
    try {
        std::vector<XY> pts;
        pts.reserve(project_.points.size());
        for (const auto& p : project_.points) pts.push_back({p.x, p.y});
        auto [ring, used_edge] = outline_from_points(pts, -1.0);
        if (ring.size() < 3) {
            status_text_ = "自动轮廓提取失败：有效边界顶点不足 3 个";
            log_cmd("警告: " + status_text_);
            return;
        }
        push_undo_snapshot();
        project_.boundary = std::move(ring);
        double area = std::abs(signed_area(project_.boundary));
        std::ostringstream oss;
        oss << "已自动提取凹轮廓边界：" << project_.boundary.size()
            << " 顶点（阈值边长 " << format_num(used_edge, 2)
            << " m，面积 " << format_num(area, 2) << " m²）";
        status_text_ = oss.str();
        log_cmd("命令: _OUTLINE -> " + oss.str());
    } catch (const std::exception& e) {
        status_text_ = std::string("自动轮廓提取失败: ") + e.what();
        log_cmd("错误: " + status_text_);
    }
}

void CassApp::clear_boundary() {
    if (!project_.boundary.empty()) push_undo_snapshot();
    project_.boundary.clear();
    status_text_ = "已清除计算边界（将按全部测点凸包统计）";
    log_cmd("命令: _CLRBND -> 已清除计算边界");
}

void CassApp::enable_grid_check(bool enabled, double spacing) {
    project_.settings.grid_enabled = enabled;
    if (spacing > 0.0) {
        project_.settings.grid_spacing = spacing;
    }
    canvas_.layers.grid = enabled;
}

void CassApp::enable_contours(bool enabled, double interval) {
    project_.settings.contours_enabled = enabled;
    project_.settings.contour_interval = interval;
    canvas_.layers.contours = enabled;
}

void CassApp::set_design_elevation(double elev) {
    project_.settings.design_mode = DesignMode::Flat;
    project_.settings.design_elevation = elev;
}

void CassApp::configure_calculator(TINEarthworkCalculator& calc) const {
    calc.add_points(project_.points);
    if (project_.settings.design_mode == DesignMode::Flat) {
        if (project_.settings.use_partition && !project_.settings.partition.empty()) {
            calc.set_design_elevations(project_.settings.partition, project_.settings.design_elevation);
        } else {
            calc.set_design_elevation(project_.settings.design_elevation);
        }
    } else if (project_.settings.design_mode == DesignMode::Plane) {
        calc.set_design_plane(
            project_.settings.design_plane,
            project_.settings.use_partition ? project_.settings.partition : std::map<std::string, double>{});
    } else if (project_.settings.design_mode == DesignMode::Compare) {
        calc.set_compare_points(project_.compare_points);
    }

    if (!project_.boundary.empty()) {
        calc.set_boundary(project_.boundary);
    }
    if (!project_.holes.empty()) {
        calc.set_holes(project_.holes, true);
    }
    if (!project_.breaklines.empty()) {
        calc.set_breaklines(project_.breaklines);
    }
    if (project_.settings.max_edge_length > 0.0) {
        calc.set_max_edge_length(project_.settings.max_edge_length);
    }
    calc.set_soil_factors(project_.settings.soil_factors);
}

bool CassApp::run_calculation() {
    if (project_.points.size() < 3) {
        status_text_ = "测点少于 3 个，无法构建 TIN 三角网";
        log_cmd("警告: " + status_text_);
        return false;
    }
    if (project_.settings.design_mode == DesignMode::Compare && project_.compare_points.size() < 3) {
        status_text_ = "两期对比模式下需先导入至少 3 个对比期测点";
        log_cmd("警告: " + status_text_);
        return false;
    }

    try {
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);

        CalculationResult res = calc.run_full_calculation();
        if (project_.settings.contours_enabled) {
            res.contours = extract_elevation_contours(res, project_.settings.contour_interval, 5);
        }
        if (project_.settings.grid_enabled) {
            double sp = project_.settings.grid_spacing;
            if (sp <= 0.0) {
                sp = suggest_grid_spacing(std::max(max_x_ - min_x_, 10.0), std::max(max_y_ - min_y_, 10.0));
                project_.settings.grid_spacing = sp;
            }
            res.grid_check = run_grid_check(calc, res, sp);
        }
        if (profile_line_) {
            profile_result_ = sample_profile(calc, profile_line_->first, profile_line_->second, 240);
        }

        result_ = std::move(res);
        if (selected_tri_id_ >= result_->triangle_count) {
            selected_tri_id_ = -1;
        }

        std::ostringstream oss;
        oss << "TIN 算量完成：挖方 " << format_num(result_->total_cut, 2)
            << " m3 | 填方 " << format_num(result_->total_fill, 2)
            << " m3 | 净方 " << format_num(result_->net_volume, 2)
            << " m3 | 有效面积 " << format_num(result_->computed_area, 2)
            << " m2 | 三角形 " << result_->triangle_count
            << " 个（耗时 " << format_num(result_->elapsed_ms, 1) << " ms）";
        status_text_ = oss.str();
        log_cmd("命令: _CALC -> " + oss.str());
        for (const auto& w : result_->warnings) {
            log_cmd("  [告警] " + w);
        }
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("算量执行失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::solve_balance_h0() {
    if (project_.points.size() < 3) {
        status_text_ = "测点少于 3 个，无法求解挖填平衡标高";
        log_cmd("警告: " + status_text_);
        return false;
    }
    try {
        project_.settings.design_mode = DesignMode::Flat;
        project_.settings.use_partition = false;
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        BalanceResult bal = solve_balance_elevation(calc, project_.settings.soil_factors);
        last_balance_ = bal;
        project_.settings.design_elevation = bal.elevation;

        std::ostringstream oss;
        oss << "零平衡高程 H0 = " << format_num(bal.elevation, 4)
            << " m（平衡挖方 " << format_num(bal.cut_volume, 2)
            << " m3，填方 " << format_num(bal.fill_volume, 2)
            << " m3，实测高程均值 " << format_num(bal.natural_mean, 3) << " m）";
        log_cmd("命令: _BALANCE -> " + oss.str());
        return run_calculation();
    } catch (const std::exception& e) {
        status_text_ = std::string("求解平衡标高失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::solve_best_fit_plane_action() {
    if (project_.points.size() < 3) {
        status_text_ = "测点少于 3 个，无法求解最小二乘最优斜面";
        return false;
    }
    try {
        push_undo_snapshot();
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        auto res = solve_best_fit_plane(calc, project_.settings.soil_factors);
        project_.settings.design_mode = DesignMode::Plane;
        project_.settings.design_plane = res.plane;
        std::ostringstream oss;
        oss << "最优斜面求解完成：基准(" << format_num(res.plane.x0, 2) << "," << format_num(res.plane.y0, 2)
            << ") H0=" << format_num(res.plane.h0, 4) << "m，坡度 X=" << format_num(res.plane.slope_x, 3)
            << "% Y=" << format_num(res.plane.slope_y, 3) << "%";
        log_cmd("命令: _FITPLANE -> " + oss.str());
        return run_calculation();
    } catch (const std::exception& e) {
        status_text_ = std::string("求解最优斜面失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::solve_balance_plane_action() {
    if (project_.points.size() < 3) {
        status_text_ = "测点少于 3 个，无法求解保坡平衡斜面";
        return false;
    }
    try {
        push_undo_snapshot();
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        const auto& pl = project_.settings.design_plane;
        auto res = solve_balance_plane(calc, pl.x0, pl.y0, pl.slope_x, pl.slope_y, project_.settings.soil_factors);
        project_.settings.design_mode = DesignMode::Plane;
        project_.settings.design_plane = res.plane;
        std::ostringstream oss;
        oss << "保坡平衡斜面 H0=" << format_num(res.plane.h0, 4)
            << "m（X=" << format_num(res.plane.slope_x, 3) << "% Y=" << format_num(res.plane.slope_y, 3) << "%）";
        log_cmd("命令: _BALPLANE -> " + oss.str());
        return run_calculation();
    } catch (const std::exception& e) {
        status_text_ = std::string("保坡平衡斜面求解失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::run_sections_action(double spacing, double azimuth_deg) {
    if (project_.points.size() < 3) {
        status_text_ = "测点少于 3 个，无法执行平行断面法";
        return false;
    }
    try {
        if (!result_) {
            if (!run_calculation()) return false;
        }
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        SectionMethodResult sec = run_sections(calc, spacing, azimuth_deg, 180);
        section_spacing_ = spacing;
        section_azimuth_ = azimuth_deg;
        selected_section_idx_ = sec.stations.empty() ? -1 : static_cast<int>(sec.stations.size() / 2);
        if (selected_section_idx_ >= 0) {
            const auto& st = sec.stations[selected_section_idx_];
            profile_line_ = Segment{st.start, st.end};
            profile_result_ = st.profile;
        }
        std::ostringstream oss;
        oss << "平行断面法完成（" << sec.stations.size() << " 条断面，间距 "
            << format_num(spacing, 1) << " m）：挖方 " << format_num(sec.total_cut, 2)
            << " m³ | 填方 " << format_num(sec.total_fill, 2) << " m³";
        result_->sections = std::move(sec);
        canvas_.layers.sections = true;
        status_text_ = oss.str();
        log_cmd("命令: _SECTIONS -> " + oss.str());
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("平行断面法执行失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::generate_quick_profile(bool horizontal) {
    if (project_.points.size() < 3) {
        status_text_ = "测点不足，无法生成纵断面";
        return false;
    }
    update_point_stats();
    XY a{}, b{};
    if (horizontal) {
        double ym = (min_y_ + max_y_) * 0.5;
        a = {min_x_, ym};
        b = {max_x_, ym};
    } else {
        double xm = (min_x_ + max_x_) * 0.5;
        a = {xm, min_y_};
        b = {xm, max_y_};
    }
    handle_profile_picked(a, b);
    return profile_result_.has_value();
}

void CassApp::handle_profile_picked(const XY& a, const XY& b) {
    if (project_.points.size() < 3) return;
    try {
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        profile_line_ = Segment{a, b};
        profile_result_ = sample_profile(calc, a, b, 240);
        canvas_.layers.profile = true;
        std::ostringstream oss;
        oss << "已生成纵断面（总长 " << format_num(profile_result_->length, 2)
            << " m，最大挖深 " << format_num(profile_result_->max_cut_depth, 2)
            << " m，最大填高 " << format_num(profile_result_->max_fill_depth, 2) << " m）";
        status_text_ = oss.str();
        log_cmd("命令: _PROFILE -> " + oss.str());
    } catch (const std::exception& e) {
        status_text_ = std::string("生成纵断面失败: ") + e.what();
        log_cmd("错误: " + status_text_);
    }
}

void CassApp::handle_draft_committed(const std::vector<XY>& pts, CadToolMode tool) {
    if (tool == CadToolMode::DrawBoundary) {
        if (pts.size() < 3) {
            status_text_ = "边界至少需要 3 个顶点";
            return;
        }
        push_undo_snapshot();
        project_.boundary = normalize_ring(pts);
        status_text_ = "已设置手绘计算边界（" + std::to_string(project_.boundary.size()) + " 顶点）";
        log_cmd("命令: _DRAWBND -> " + status_text_);
    } else if (tool == CadToolMode::DrawHole) {
        if (pts.size() < 3) {
            status_text_ = "挖空区至少需要 3 个顶点";
            return;
        }
        push_undo_snapshot();
        project_.holes.push_back(normalize_ring(pts));
        status_text_ = "已添加挖空岛屿 #" + std::to_string(project_.holes.size());
        log_cmd("命令: _ADDHOLE -> " + status_text_);
    } else if (tool == CadToolMode::DrawBreakline) {
        if (pts.size() < 2) {
            status_text_ = "特征线至少需要 2 个顶点";
            return;
        }
        push_undo_snapshot();
        Breakline bl;
        bl.name = "BL-" + std::to_string(project_.breaklines.size() + 1);
        bl.closed = false;
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        bool can_sample = false;
        if (project_.points.size() >= 3) {
            try {
                configure_calculator(calc);
                calc.build_tin();
                can_sample = true;
            } catch (...) {
                can_sample = false;
            }
        }
        for (size_t i = 0; i < pts.size(); ++i) {
            double z_val = avg_nat_z_;
            if (can_sample) {
                auto [zn, _, ok] = calc.sample_surface(pts[i].x, pts[i].y, true);
                if (std::isfinite(zn)) z_val = zn;
                (void)ok;
            }
            SurveyPoint sp;
            sp.id = bl.name + "_" + std::to_string(i + 1);
            sp.x = pts[i].x;
            sp.y = pts[i].y;
            sp.z = z_val;
            bl.points.push_back(sp);
        }
        project_.breaklines.push_back(std::move(bl));
        status_text_ = "已添加地形特征线（" + std::to_string(pts.size()) + " 点）";
        log_cmd("命令: _ADDBL -> " + status_text_);
    }
    canvas_.tool = CadToolMode::Select;
}

void CassApp::set_inspector_tab(int idx) {
    pending_tab_ = std::clamp(idx, 0, 4);
}

void CassApp::set_dark_mode(bool dark) {
    canvas_.dark_mode = dark;
    status_text_ = dark ? "已切换至 CAD 深色模型空间 (#0B0E14)" : "已切换至 CAD 白底出图布局 (#F8FAFC)";
}

void CassApp::set_selected_triangle(int tri_id) {
    selected_tri_id_ = tri_id;
    if (result_ && tri_id >= 0 && tri_id < static_cast<int>(result_->triangles.size())) {
        canvas_.focus_triangle(result_->triangles[tri_id]);
    }
}

int CassApp::first_mixed_triangle_id() const {
    if (!result_) return -1;
    for (const auto& t : result_->triangles) {
        if (!t.is_boundary && t.is_mixed) return t.id;
    }
    for (const auto& t : result_->triangles) {
        if (!t.is_boundary) return t.id;
    }
    return -1;
}

void CassApp::zoom_extents() {
    canvas_.fit_view(project_.points, project_.boundary, ImVec2(900.0f, 620.0f));
}

void CassApp::execute_command(const std::string& raw_cmd) {
    std::string cmd = trim_copy(raw_cmd);
    if (cmd.empty()) return;
    cmd_history_.push_back(cmd);
    history_pos_ = -1;
    log_cmd("命令: " + cmd);

    std::string up = upper_ascii(cmd);
    if (up == "HELP" || up == "?") {
        log_cmd("可用指令: AUTOB(矩形边界) | OUTLINE(自动轮廓) | CALC(开始计算) | BALANCE/H0(平衡标高)");
        log_cmd("         GRID(方格网开关) | CONTOUR(等高线开关) | PROFILE(水平断面) | ZE(全图缩放)");
        log_cmd("         DARK(黑底) | LIGHT(白底) | SWAP(互换对比两期) | DAT/DXF(快捷导出) | CLEAR(清屏)");
    } else if (up == "AUTOB" || up == "_AUTOB" || up == "RECT") {
        generate_rect_boundary();
    } else if (up == "OUTLINE" || up == "_OUTLINE") {
        generate_auto_outline();
    } else if (up == "CLRBND" || up == "_CLRBND") {
        clear_boundary();
    } else if (up == "CALC" || up == "_CALC" || up == "RUN") {
        run_calculation();
    } else if (up == "BALANCE" || up == "_BALANCE" || up == "H0") {
        solve_balance_h0();
    } else if (up == "FITPLANE" || up == "_FITPLANE") {
        solve_best_fit_plane_action();
    } else if (up == "BALPLANE" || up == "_BALPLANE") {
        solve_balance_plane_action();
    } else if (up == "SECTIONS" || up == "_SECTIONS") {
        run_sections_action(section_spacing_, section_azimuth_);
        set_inspector_tab(3);
    } else if (up == "GRID" || up == "_GRID") {
        enable_grid_check(!project_.settings.grid_enabled, project_.settings.grid_spacing);
        run_calculation();
    } else if (up == "CONTOUR" || up == "_CONTOUR") {
        enable_contours(!project_.settings.contours_enabled, project_.settings.contour_interval);
        run_calculation();
    } else if (up == "PROFILE" || up == "_PROFILE") {
        generate_quick_profile(true);
        set_inspector_tab(3);
    } else if (up == "ZE" || up == "Z" || up == "ZOOM") {
        zoom_extents();
        log_cmd("视图已全图居中 (ZE)");
    } else if (up == "DARK") {
        set_dark_mode(true);
    } else if (up == "LIGHT") {
        set_dark_mode(false);
    } else if (up == "SWAP") {
        if (!project_.compare_points.empty()) {
            std::swap(project_.points, project_.compare_points);
            compare_invert_ = 1 - compare_invert_;
            update_point_stats();
            run_calculation();
        } else {
            log_cmd("提示: 当前未加载对比期测点");
        }
    } else if (up == "DAT") {
        action_export_cass_dat();
    } else if (up == "DXF") {
        action_export_dxf();
    } else if (up == "UNDO" || up == "_UNDO" || up == "U") {
        undo();
    } else if (up == "REDO" || up == "_REDO") {
        redo();
    } else if (up == "CLEAR" || up == "CLS") {
        console_lines_.clear();
        log_cmd("命令行历史记录已清空");
    } else if (up.rfind("H=", 0) == 0 || up.rfind("Z=", 0) == 0) {
        try {
            double h = std::stod(cmd.substr(2));
            set_design_elevation(h);
            run_calculation();
        } catch (...) {
            log_cmd("参数格式错误，示例: H=50.5");
        }
    } else {
        log_cmd("未知指令 \"" + cmd + "\"，输入 HELP 查看命令清单");
    }
}

std::optional<std::string> CassApp::open_file_dialog(const wchar_t* title, const wchar_t* filter) const {
    wchar_t buf[1024] = {0};
    OPENFILENAMEW ofn{};
    ofn.lStructSize = sizeof(ofn);
    ofn.hwndOwner = hwnd_;
    ofn.lpstrFilter = filter;
    ofn.lpstrFile = buf;
    ofn.nMaxFile = static_cast<DWORD>(std::size(buf));
    ofn.lpstrTitle = title;
    ofn.Flags = OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST;
    if (GetOpenFileNameW(&ofn)) {
        return wide_to_utf8(buf);
    }
    return std::nullopt;
}

std::optional<std::string> CassApp::save_file_dialog(const wchar_t* title, const wchar_t* filter,
                                                     const wchar_t* default_ext,
                                                     const std::string& default_name) const {
    wchar_t buf[1024] = {0};
    std::wstring wdef = utf8_to_wide(default_name);
    if (!wdef.empty() && wdef.size() < std::size(buf) - 1) {
        std::copy(wdef.begin(), wdef.end(), buf);
    }
    OPENFILENAMEW ofn{};
    ofn.lStructSize = sizeof(ofn);
    ofn.hwndOwner = hwnd_;
    ofn.lpstrFilter = filter;
    ofn.lpstrFile = buf;
    ofn.nMaxFile = static_cast<DWORD>(std::size(buf));
    ofn.lpstrTitle = title;
    ofn.lpstrDefExt = default_ext;
    ofn.Flags = OFN_NOCHANGEDIR | OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST;
    if (GetSaveFileNameW(&ofn)) {
        return wide_to_utf8(buf);
    }
    return std::nullopt;
}

void CassApp::action_open_project() {
    static const wchar_t filter[] = L"TIN 工程文件 (*.tinproj.json;*.json)\0*.tinproj.json;*.json\0所有文件 (*.*)\0*.*\0\0";
    if (auto p = open_file_dialog(L"打开 TIN 土方工程", filter)) {
        load_project_from_path(*p);
    }
}

void CassApp::action_save_project(bool save_as) {
    if (!save_as && current_project_path_) {
        save_project_to_path(*current_project_path_);
        return;
    }
    static const wchar_t filter[] = L"TIN 工程文件 (*.tinproj.json)\0*.tinproj.json\0JSON 文件 (*.json)\0*.json\0\0";
    std::string def = project_.project_name + ".tinproj.json";
    if (auto p = save_file_dialog(L"保存 TIN 土方工程", filter, L"json", def)) {
        save_project_to_path(*p);
    }
}

void CassApp::action_import_points() {
    static const wchar_t filter[] =
        L"高程测点文件 (*.xlsx;*.csv;*.txt;*.dat)\0*.xlsx;*.csv;*.txt;*.dat\0"
        L"Excel 工作簿 (*.xlsx)\0*.xlsx\0"
        L"南方CASS 坐标文件 (*.dat)\0*.dat\0"
        L"文本/CSV (*.csv;*.txt)\0*.csv;*.txt\0\0";
    if (auto p = open_file_dialog(L"导入高程测点数据", filter)) {
        load_points_from_path(*p);
    }
}

void CassApp::action_import_compare() {
    static const wchar_t filter[] =
        L"高程测点文件 (*.xlsx;*.csv;*.txt;*.dat)\0*.xlsx;*.csv;*.txt;*.dat\0所有文件 (*.*)\0*.*\0\0";
    if (auto p = open_file_dialog(L"导入对比期高程数据", filter)) {
        load_compare_from_path(*p);
    }
}

void CassApp::action_import_boundary() {
    static const wchar_t filter[] = L"边界文件 (*.dxf;*.csv;*.txt;*.dat)\0*.dxf;*.csv;*.txt;*.dat\0\0";
    if (auto p = open_file_dialog(L"导入计算边界多边形", filter)) {
        try {
            auto ring = import_boundary_file(*p);
            if (ring.size() < 3) throw std::runtime_error("边界顶点少于 3 个");
            project_.boundary = std::move(ring);
            status_text_ = "已导入计算边界（" + std::to_string(project_.boundary.size()) + " 顶点）";
            log_cmd("命令: IMPORT_BND -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导入边界失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_import_breaklines_dxf() {
    static const wchar_t filter[] = L"CAD 交换文件 (*.dxf)\0*.dxf\0\0";
    if (auto p = open_file_dialog(L"导入特征线 (DXF)", filter)) {
        try {
            auto bls = import_breaklines_dxf(*p);
            size_t added = bls.size();
            project_.breaklines.insert(project_.breaklines.end(),
                                       std::make_move_iterator(bls.begin()),
                                       std::make_move_iterator(bls.end()));
            status_text_ = "已从 DXF 导入特征线 " + std::to_string(added) + " 条";
            log_cmd("命令: IMPORT_BL -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导入特征线失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_summary_csv() {
    if (!result_) { status_text_ = "请先执行 TIN 土方算量"; return; }
    static const wchar_t filter[] = L"CSV 文件 (*.csv)\0*.csv\0\0";
    if (auto p = save_file_dialog(L"导出方量汇总表 (CSV)", filter, L"csv", project_.project_name + "_汇总.csv")) {
        try {
            export_summary_csv(*result_, *p);
            status_text_ = "已导出汇总 CSV: " + *p;
            log_cmd("命令: EXP_SUM -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出汇总失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_detail_csv() {
    if (!result_) { status_text_ = "请先执行 TIN 土方算量"; return; }
    static const wchar_t filter[] = L"CSV 文件 (*.csv)\0*.csv\0\0";
    if (auto p = save_file_dialog(L"导出逐三角形明细 (CSV)", filter, L"csv", project_.project_name + "_明细.csv")) {
        try {
            export_detail_csv(*result_, *p);
            status_text_ = "已导出逐三角形明细 CSV: " + *p;
            log_cmd("命令: EXP_DETAIL -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出明细失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_report_txt() {
    if (!result_) { status_text_ = "请先执行 TIN 土方算量"; return; }
    static const wchar_t filter[] = L"工程报告 (*.txt)\0*.txt\0\0";
    if (auto p = save_file_dialog(L"导出工程计算报告 (TXT)", filter, L"txt", project_.project_name + "_报告.txt")) {
        try {
            export_report_txt(*result_, static_cast<int>(project_.points.size()), project_.project_name, *p);
            status_text_ = "已导出工程计算报告: " + *p;
            log_cmd("命令: EXP_RPT -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出报告失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_dxf() {
    if (!result_) { status_text_ = "请先执行 TIN 土方算量"; return; }
    static const wchar_t filter[] = L"AutoCAD R12 DXF (*.dxf)\0*.dxf\0\0";
    if (auto p = save_file_dialog(L"导出 CASS / AutoCAD R12 DXF", filter, L"dxf", project_.project_name + "_TIN.dxf")) {
        try {
            export_dxf_r12(*result_, *p);
            status_text_ = "已导出 R12 DXF 成果图: " + *p;
            log_cmd("命令: EXP_DXF -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出 DXF 失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_cass_dat() {
    if (project_.points.empty()) { status_text_ = "当前没有可导出的测点数据"; return; }
    static const wchar_t filter[] = L"南方CASS 坐标文件 (*.dat)\0*.dat\0\0";
    if (auto p = save_file_dialog(L"导出南方CASS DAT 文件", filter, L"dat", project_.project_name + ".dat")) {
        try {
            export_cass_dat(project_.points, *p, "");
            status_text_ = "已导出南方CASS DAT: " + *p;
            log_cmd("命令: EXP_DAT -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出 DAT 失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_sections_csv() {
    if (!result_ || !result_->sections || result_->sections->stations.empty()) {
        status_text_ = "请先执行平行断面法批量算量";
        return;
    }
    static const wchar_t filter[] = L"CSV 文件 (*.csv)\0*.csv\0\0";
    if (auto p = save_file_dialog(L"导出平行断面法汇总表 (CSV)", filter, L"csv", project_.project_name + "_断面法.csv")) {
        try {
            export_sections_csv(*result_->sections, *p);
            status_text_ = "已导出平行断面法表: " + *p;
            log_cmd("命令: EXP_SEC -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出断面表失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::render() {
    // 全局快捷键处理
    if (!ImGui::GetIO().WantTextInput) {
        const bool ctrl = ImGui::GetIO().KeyCtrl;
        if (ctrl && ImGui::IsKeyPressed(ImGuiKey_Z)) undo();
        if (ctrl && ImGui::IsKeyPressed(ImGuiKey_Y)) redo();
        if (ctrl && ImGui::IsKeyPressed(ImGuiKey_O)) action_open_project();
        if (ctrl && ImGui::IsKeyPressed(ImGuiKey_S)) action_save_project(false);
        if (ImGui::IsKeyPressed(ImGuiKey_F5)) {
            run_calculation();
        }
        if (ImGui::IsKeyPressed(ImGuiKey_Escape)) {
            if (!canvas_.draft_points().empty()) {
                canvas_.clear_draft();
                status_text_ = "已取消当前绘制草稿";
            } else if (canvas_.tool != CadToolMode::Select) {
                canvas_.tool = CadToolMode::Select;
                status_text_ = "已返回选择/查询模式";
            }
        }
        if (ImGui::IsKeyPressed(ImGuiKey_Enter) && !canvas_.draft_points().empty()) {
            CadCanvasCallbacks cbs;
            cbs.on_draft_committed = [this](const std::vector<XY>& pts, CadToolMode t) {
                handle_draft_committed(pts, t);
            };
            canvas_.commit_draft(cbs);
        }
    }

    render_menu_bar();

    ImGuiViewport* vp = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(vp->WorkPos);
    ImGui::SetNextWindowSize(vp->WorkSize);

    ImGuiWindowFlags host_flags =
        ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
        ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoBringToFrontOnFocus |
        ImGuiWindowFlags_NoNavFocus | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse;

    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(6.0f, 4.0f));
    ImGui::Begin("##CASS_WORKSTATION_HOST", nullptr, host_flags);
    ImGui::PopStyleVar();

    render_quick_toolbar();

    const float console_h = 100.0f;
    const float status_h = 25.0f;
    const float avail_h = ImGui::GetContentRegionAvail().y;
    const float mid_h = std::max(240.0f, avail_h - console_h - status_h - 10.0f);
    const float avail_w = ImGui::GetContentRegionAvail().x;
    const float left_w = show_screen_menu_ ? 166.0f : 0.0f;
    const float right_w = std::clamp(avail_w * 0.25f, 345.0f, 400.0f);
    const float spacing_x = ImGui::GetStyle().ItemSpacing.x;
    const float center_w = std::max(320.0f, avail_w - left_w - right_w - (show_screen_menu_ ? spacing_x * 2.0f : spacing_x));

    if (show_screen_menu_) {
        render_left_screen_menu(left_w, mid_h);
        ImGui::SameLine();
    }

    render_center_viewport(center_w, mid_h);
    ImGui::SameLine();
    render_right_inspector(right_w, mid_h);

    render_command_console(console_h);
    render_status_bar(status_h);
    render_modals();

    ImGui::End();
}

void CassApp::render_menu_bar() {
    if (!ImGui::BeginMainMenuBar()) return;

    if (ImGui::BeginMenu("文件(F)")) {
        if (ImGui::MenuItem("打开工程 (.tinproj.json)...", "Ctrl+O")) action_open_project();
        if (ImGui::MenuItem("保存工程", "Ctrl+S")) action_save_project(false);
        if (ImGui::MenuItem("工程另存为...")) action_save_project(true);
        ImGui::Separator();
        if (ImGui::MenuItem("退出工作站", "Alt+F4")) {
            if (hwnd_) PostMessageW(hwnd_, WM_CLOSE, 0, 0);
        }
        ImGui::EndMenu();
    }

    if (ImGui::BeginMenu("数据(D)")) {
        if (ImGui::MenuItem("导入高程测点 (.xlsx/.csv/.dat)...")) action_import_points();
        if (ImGui::MenuItem("导入对比期测点（两期土方）...")) action_import_compare();
        if (ImGui::MenuItem("检查测点质量与重号")) set_inspector_tab(1);
        ImGui::EndMenu();
    }

    if (ImGui::BeginMenu("工程应用(E)")) {
        if (ImGui::MenuItem("执行 TIN 土方算量", "F5")) run_calculation();
        if (ImGui::MenuItem("求解零平衡设计标高 H0")) solve_balance_h0();
        ImGui::Separator();
        bool grid_on = project_.settings.grid_enabled;
        if (ImGui::MenuItem("方格网法同步校核", nullptr, &grid_on)) {
            enable_grid_check(grid_on, project_.settings.grid_spacing);
            if (result_) run_calculation();
        }
        bool cont_on = project_.settings.contours_enabled;
        if (ImGui::MenuItem("自动追踪等高线", nullptr, &cont_on)) {
            enable_contours(cont_on, project_.settings.contour_interval);
            if (result_) run_calculation();
        }
        if (ImGui::MenuItem("生成水平中线纵断面")) {
            generate_quick_profile(true);
            set_inspector_tab(3);
        }
        ImGui::EndMenu();
    }

    if (ImGui::BeginMenu("边界与特征(B)")) {
        if (ImGui::MenuItem("生成外接矩形边界")) generate_rect_boundary();
        if (ImGui::MenuItem("自动提取凹轮廓边界")) generate_auto_outline();
        if (ImGui::MenuItem("导入计算边界 (.dxf/.csv/.dat)...")) action_import_boundary();
        if (ImGui::MenuItem("导入特征线 (.dxf)...")) action_import_breaklines_dxf();
        ImGui::Separator();
        if (ImGui::MenuItem("在图形区手绘边界")) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawBoundary; }
        if (ImGui::MenuItem("在图形区手绘挖空岛屿")) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawHole; }
        if (ImGui::MenuItem("在图形区手绘特征线")) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawBreakline; }
        if (ImGui::MenuItem("清除计算边界")) clear_boundary();
        ImGui::EndMenu();
    }

    if (ImGui::BeginMenu("视图(V)")) {
        if (ImGui::MenuItem("全图居中显示 (ZE)")) zoom_extents();
        if (ImGui::MenuItem("CAD 深色模型空间 (#0B0E14)", nullptr, canvas_.dark_mode)) set_dark_mode(true);
        if (ImGui::MenuItem("CAD 白底出图布局 (#F8FAFC)", nullptr, !canvas_.dark_mode)) set_dark_mode(false);
        ImGui::Separator();
        ImGui::MenuItem("显示南方CASS屏幕菜单", nullptr, &show_screen_menu_);
        ImGui::MenuItem("显示顶部汇总KPI卡片", nullptr, &show_kpi_strip_);
        ImGui::MenuItem("对象捕捉 (OSNAP)", nullptr, &canvas_.osnap_enabled);
        ImGui::EndMenu();
    }

    if (ImGui::BeginMenu("成果输出(O)")) {
        if (ImGui::MenuItem("导出 AutoCAD R12 DXF 成果图...")) action_export_dxf();
        if (ImGui::MenuItem("导出方量汇总表 (CSV)...")) action_export_summary_csv();
        if (ImGui::MenuItem("导出逐三角形明细 (CSV)...")) action_export_detail_csv();
        if (ImGui::MenuItem("导出工程计算报告 (TXT)...")) action_export_report_txt();
        if (ImGui::MenuItem("导出平行断面汇总表 (CSV)...")) action_export_sections_csv();
        if (ImGui::MenuItem("导出南方CASS 坐标点文件 (.dat)...")) action_export_cass_dat();
        ImGui::EndMenu();
    }

    if (ImGui::BeginMenu("帮助(H)")) {
        if (ImGui::MenuItem("CAD 命令与快捷键速查")) show_help_modal_ = true;
        if (ImGui::MenuItem("关于 TIN 土方自动算量工作站")) show_about_modal_ = true;
        ImGui::EndMenu();
    }

    ImGui::EndMainMenuBar();
}

void CassApp::render_quick_toolbar() {
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x18, 0x1D, 0x24));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(6.0f, 4.0f));
    ImGui::BeginChild("##quick_toolbar", ImVec2(0.0f, 36.0f), ImGuiChildFlags_Borders,
                      ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(8.0f, 3.0f));
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(5.0f, 4.0f));

    if (ImGui::Button("打开工程")) action_open_project();
    ImGui::SameLine();
    if (ImGui::Button("保存工程")) action_save_project(false);
    ImGui::SameLine();
    if (ImGui::Button("导入测点")) action_import_points();
    ImGui::SameLine();
    ImGui::TextDisabled("|");
    ImGui::SameLine();

    if (ImGui::Button("矩形边界")) generate_rect_boundary();
    ImGui::SameLine();
    if (ImGui::Button("自动轮廓")) generate_auto_outline();
    ImGui::SameLine();
    ImGui::TextDisabled("|");
    ImGui::SameLine();

    auto tool_btn = [&](const char* label, CadToolMode mode) {
        bool active = (canvas_.tool == mode);
        if (active) {
            if (colored_button(label, rgba(0x23, 0x5C, 0x9F), rgba(0x2E, 0x73, 0xC4), rgba(0x1C, 0x4B, 0x82))) {
                canvas_.tool = CadToolMode::Select;
            }
        } else if (ImGui::Button(label)) {
            canvas_.clear_draft();
            canvas_.tool = mode;
            status_text_ = std::string("已切换到工具: ") + tool_name_cn(mode);
        }
    };

    tool_btn("选择", CadToolMode::Select);
    ImGui::SameLine();
    tool_btn("绘边界", CadToolMode::DrawBoundary);
    ImGui::SameLine();
    tool_btn("绘挖空", CadToolMode::DrawHole);
    ImGui::SameLine();
    tool_btn("特征线", CadToolMode::DrawBreakline);
    ImGui::SameLine();
    tool_btn("切断面", CadToolMode::PickProfile);

    if (!canvas_.draft_points().empty()) {
        ImGui::SameLine();
        if (colored_button("确认闭合", rgba(0x1E, 0x6B, 0x4E), rgba(0x26, 0x85, 0x62), rgba(0x17, 0x54, 0x3D))) {
            CadCanvasCallbacks cbs;
            cbs.on_draft_committed = [this](const std::vector<XY>& pts, CadToolMode t) {
                handle_draft_committed(pts, t);
            };
            canvas_.commit_draft(cbs);
        }
        ImGui::SameLine();
        if (ImGui::Button("取消草稿")) {
            canvas_.clear_draft();
            status_text_ = "已取消绘制草稿";
        }
    }

    ImGui::SameLine();
    ImGui::TextDisabled("|");
    ImGui::SameLine();

    if (colored_button("开始计算 (F5)", rgba(0x16, 0x7A, 0x55), rgba(0x1D, 0x99, 0x6B), rgba(0x12, 0x63, 0x45))) {
        run_calculation();
    }
    ImGui::SameLine();
    if (colored_button("平衡H0", rgba(0x2D, 0x4C, 0x73), rgba(0x3A, 0x62, 0x94), rgba(0x23, 0x3C, 0x5B))) {
        solve_balance_h0();
    }
    ImGui::SameLine();
    if (ImGui::Button("ZE全图")) zoom_extents();
    ImGui::SameLine();
    if (ImGui::Button(canvas_.dark_mode ? "白底布局" : "黑底模型")) {
        set_dark_mode(!canvas_.dark_mode);
    }
    ImGui::SameLine();
    if (ImGui::Button(show_screen_menu_ ? "隐藏菜单" : "屏幕菜单")) {
        show_screen_menu_ = !show_screen_menu_;
    }

    // 右侧项目名输入框
    float right_rem = ImGui::GetContentRegionAvail().x;
    if (right_rem > 170.0f) {
        ImGui::SameLine();
        ImGui::SetNextItemWidth(std::min(210.0f, right_rem - 62.0f));
        ImGui::TextDisabled("项目:");
        ImGui::SameLine();
        if (ImGui::InputText("##proj_name_tb", proj_name_buf_, sizeof(proj_name_buf_))) {
            project_.project_name = proj_name_buf_;
        }
    }

    ImGui::PopStyleVar(2);
    ImGui::EndChild();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();
}

void CassApp::render_left_screen_menu(float width, float height) {
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x19, 0x1E, 0x26));
    ImGui::BeginChild("##screen_menu", ImVec2(width, height), ImGuiChildFlags_Borders);

    // CASS 顶部蓝灰标志条
    ImDrawList* dl = ImGui::GetWindowDrawList();
    ImVec2 p = ImGui::GetCursorScreenPos();
    float cw = ImGui::GetContentRegionAvail().x;
    dl->AddRectFilled(p, ImVec2(p.x + cw, p.y + 24.0f), IM_COL32(0x1F, 0x3A, 0x58, 255), 2.0f);
    dl->AddText(ImVec2(p.x + 8.0f, p.y + 4.0f), IM_COL32(0xEA, 0xF2, 0xFB, 255), "南方CASS 屏幕菜单");
    ImGui::Dummy(ImVec2(cw, 28.0f));

    const float bw = -1.0f;

    draw_section_bar("文件与数据");
    if (ImGui::Button("导入高程测点", ImVec2(bw, 0))) action_import_points();
    if (ImGui::Button("导入对比期数据", ImVec2(bw, 0))) action_import_compare();
    if (ImGui::Button("打开 TIN 工程", ImVec2(bw, 0))) action_open_project();
    if (ImGui::Button("保存工程 (.json)", ImVec2(bw, 0))) action_save_project(false);

    ImGui::Spacing();
    draw_section_bar("边界与特征");
    if (ImGui::Button("外接矩形边界", ImVec2(bw, 0))) generate_rect_boundary();
    if (ImGui::Button("自动轮廓边界", ImVec2(bw, 0))) generate_auto_outline();
    if (ImGui::Button("手绘边界区域", ImVec2(bw, 0))) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawBoundary; }
    if (ImGui::Button("手绘挖空岛屿", ImVec2(bw, 0))) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawHole; }
    if (ImGui::Button("绘制地形特征线", ImVec2(bw, 0))) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawBreakline; }
    if (ImGui::Button("导入 DXF 特征线", ImVec2(bw, 0))) action_import_breaklines_dxf();

    ImGui::Spacing();
    draw_section_bar("方量与分析");
    if (colored_button("构网与土方算量", rgba(0x16, 0x7A, 0x55), rgba(0x1D, 0x99, 0x6B), rgba(0x12, 0x63, 0x45), ImVec2(bw, 0))) {
        run_calculation();
    }
    if (ImGui::Button("求解平衡标高 H0", ImVec2(bw, 0))) solve_balance_h0();
    if (ImGui::Button("方格网法同步校核", ImVec2(bw, 0))) {
        enable_grid_check(true, project_.settings.grid_spacing);
        run_calculation();
    }
    if (ImGui::Button("自动追踪等高线", ImVec2(bw, 0))) {
        enable_contours(true, project_.settings.contour_interval);
        run_calculation();
    }
    if (ImGui::Button("交互拾取纵断面", ImVec2(bw, 0))) {
        canvas_.clear_draft();
        canvas_.tool = CadToolMode::PickProfile;
        set_inspector_tab(3);
    }
    if (ImGui::Button("水平中线纵断面", ImVec2(bw, 0))) {
        generate_quick_profile(true);
        set_inspector_tab(3);
    }

    ImGui::Spacing();
    draw_section_bar("图形与输出");
    if (ImGui::Button("导出 R12 DXF 图", ImVec2(bw, 0))) action_export_dxf();
    if (ImGui::Button("导出 CASS DAT 点", ImVec2(bw, 0))) action_export_cass_dat();
    if (ImGui::Button("导出汇总表 CSV", ImVec2(bw, 0))) action_export_summary_csv();
    if (ImGui::Button("导出三角形明细", ImVec2(bw, 0))) action_export_detail_csv();
    if (ImGui::Button("导出工程报告 TXT", ImVec2(bw, 0))) action_export_report_txt();

    ImGui::EndChild();
    ImGui::PopStyleColor();
}

void CassApp::render_kpi_strip() {
    if (!show_kpi_strip_) return;

    const bool has_res = result_.has_value();
    const double cut_v = has_res ? result_->total_cut : 0.0;
    const double fill_v = has_res ? result_->total_fill : 0.0;
    const double net_v = has_res ? result_->net_volume : 0.0;
    const double area_v = has_res ? result_->computed_area : 0.0;

    if (ImGui::BeginTable("##kpi_strip", 4, ImGuiTableFlags_SizingStretchSame)) {
        auto draw_card = [&](int idx, const char* title, const std::string& val_str,
                             const std::string& sub_str, ImU32 accent_col, ImVec4 val_col) {
            ImGui::TableSetColumnIndex(idx);
            ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x16, 0x1B, 0x22));
            ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(10.0f, 6.0f));
            std::string cid = "##kpi_card_" + std::to_string(idx);
            ImGui::BeginChild(cid.c_str(), ImVec2(0.0f, 64.0f), ImGuiChildFlags_Borders,
                              ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

            ImDrawList* dl = ImGui::GetWindowDrawList();
            ImVec2 wp = ImGui::GetWindowPos();
            ImVec2 ws = ImGui::GetWindowSize();
            dl->AddRectFilled(wp, ImVec2(wp.x + ws.x, wp.y + 3.0f), accent_col);

            ImGui::TextColored(rgba(0x9A, 0xA8, 0xBC), "%s", title);
            if (fonts_.kpi) ImGui::PushFont(fonts_.kpi);
            ImGui::TextColored(val_col, "%s", val_str.c_str());
            if (fonts_.kpi) ImGui::PopFont();
            ImGui::SameLine();
            ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 4.0f);
            ImGui::TextColored(rgba(0x7D, 0x8B, 0x9E), "%s", sub_str.c_str());

            ImGui::EndChild();
            ImGui::PopStyleVar();
            ImGui::PopStyleColor();
        };

        ImGui::TableNextRow();
        draw_card(0, "总挖方量 (Cut)",
                  has_res ? (format_num(cut_v, 2) + " m³") : "--",
                  has_res && !result_->soil_balance.factors.is_default()
                      ? ("松散 " + format_num(result_->soil_balance.cut_loose(), 1))
                      : "高差 ΔZ > 0",
                  IM_COL32(0xE0, 0x62, 0x52, 255),
                  rgba(0xF4, 0x83, 0x74));

        draw_card(1, "总填方量 (Fill)",
                  has_res ? (format_num(fill_v, 2) + " m³") : "--",
                  has_res && !result_->soil_balance.factors.is_default()
                      ? ("天然需土 " + format_num(result_->soil_balance.fill_natural(), 1))
                      : "高差 ΔZ < 0",
                  IM_COL32(0x3B, 0x9A, 0xEC, 255),
                  rgba(0x63, 0xB8, 0xFF));

        const char* net_tag = !has_res ? "等待计算" : (net_v >= 0.0 ? "余土外运" : "缺土外借");
        ImU32 net_accent = !has_res ? IM_COL32(0x64, 0x74, 0x8B, 255)
                         : (std::abs(net_v) < 1e-2 ? IM_COL32(0x2E, 0xCC, 0x71, 255)
                         : (net_v >= 0.0 ? IM_COL32(0xF5, 0x9E, 0x0B, 255) : IM_COL32(0x38, 0xBD, 0xF8, 255)));
        draw_card(2, "挖填净方 (Net = Cut - Fill)",
                  has_res ? (format_num(net_v, 2) + " m³") : "--",
                  net_tag,
                  net_accent,
                  !has_res ? rgba(0xC0, 0xCA, 0xD8)
                           : (std::abs(net_v) < 1e-2 ? rgba(0x4A, 0xDE, 0x80) : rgba(0xFB, 0xBF, 0x24)));

        draw_card(3, "有效算量面积 (Area)",
                  has_res ? (format_num(area_v, 2) + " m²") : "--",
                  has_res ? (std::to_string(result_->triangle_count) + " 三角形 | " + format_num(area_v / 666.6667, 2) + " 亩")
                          : ("测点 " + std::to_string(project_.points.size()) + " 个"),
                  IM_COL32(0x26, 0xA6, 0x7A, 255),
                  rgba(0xE2, 0xEA, 0xF2));

        ImGui::EndTable();
    }
}

void CassApp::render_layer_strip() {
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x18, 0x1D, 0x25));
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(5.0f, 2.0f));
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(6.0f, 2.0f));
    ImGui::BeginChild("##layer_strip", ImVec2(0.0f, 26.0f), ImGuiChildFlags_Borders,
                      ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    ImGui::AlignTextToFramePadding();
    ImGui::TextColored(rgba(0x88, 0x99, 0xAD), "图层:");
    ImGui::SameLine(); ImGui::Checkbox("挖填填色", &canvas_.layers.cut_fill);
    ImGui::SameLine(); ImGui::Checkbox("TIN网", &canvas_.layers.tin);
    ImGui::SameLine(); ImGui::Checkbox("零线", &canvas_.layers.zero_contour);
    ImGui::SameLine(); ImGui::Checkbox("等高线", &canvas_.layers.contours);
    ImGui::SameLine(); ImGui::Checkbox("方格网", &canvas_.layers.grid);
    ImGui::SameLine(); ImGui::Checkbox("测点", &canvas_.layers.points);
    ImGui::SameLine(); ImGui::Checkbox("点号", &canvas_.layers.point_labels);
    ImGui::SameLine(); ImGui::Checkbox("边界", &canvas_.layers.boundary);
    ImGui::SameLine(); ImGui::Checkbox("挖空", &canvas_.layers.holes);
    ImGui::SameLine(); ImGui::Checkbox("特征线", &canvas_.layers.breaklines);
    ImGui::SameLine(); ImGui::Checkbox("断面", &canvas_.layers.profile);
    ImGui::SameLine(); ImGui::Checkbox("断面网", &canvas_.layers.sections);
    ImGui::SameLine(); ImGui::Checkbox("捕捉", &canvas_.osnap_enabled);

    ImGui::EndChild();
    ImGui::PopStyleVar(2);
    ImGui::PopStyleColor();
}

void CassApp::render_center_viewport(float width, float height) {
    ImGui::BeginChild("##center_panel", ImVec2(width, height), ImGuiChildFlags_None,
                      ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    render_kpi_strip();
    render_layer_strip();

    CadCanvasCallbacks callbacks;
    callbacks.on_triangle_selected = [this](int tri_id) {
        selected_tri_id_ = tri_id;
        if (result_ && tri_id >= 0 && tri_id < static_cast<int>(result_->triangles.size())) {
            const auto& t = result_->triangles[tri_id];
            std::ostringstream oss;
            oss << "已选中三角形 #" << t.id << "：面积 " << format_num(t.area, 2)
                << " m2，挖方 " << format_num(t.cut_volume, 2)
                << " m3，填方 " << format_num(t.fill_volume, 2) << " m3";
            status_text_ = oss.str();
        }
    };
    callbacks.on_profile_picked = [this](const XY& a, const XY& b) {
        handle_profile_picked(a, b);
        set_inspector_tab(3);
    };
    callbacks.on_draft_committed = [this](const std::vector<XY>& pts, CadToolMode tool) {
        handle_draft_committed(pts, tool);
    };
    callbacks.on_status_hint = [this](const std::string& msg) {
        status_text_ = msg;
    };

    const CalculationResult* res_ptr = result_ ? &(*result_) : nullptr;
    canvas_.render(project_, res_ptr, profile_line_, selected_tri_id_, callbacks);

    ImGui::EndChild();
}

void CassApp::render_right_inspector(float width, float height) {
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x1B, 0x20, 0x28));
    ImGui::BeginChild("##right_inspector", ImVec2(width, height), ImGuiChildFlags_Borders);

    if (ImGui::BeginTabBar("##inspector_tabs", ImGuiTabBarFlags_None)) {
        auto tab_flags = [&](int idx) -> ImGuiTabItemFlags {
            return (pending_tab_ == idx) ? ImGuiTabItemFlags_SetSelected : ImGuiTabItemFlags_None;
        };

        if (ImGui::BeginTabItem("算量参数", nullptr, tab_flags(0))) {
            render_tab_params();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("测点检查", nullptr, tab_flags(1))) {
            render_tab_points();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("汇总明细", nullptr, tab_flags(2))) {
            render_tab_summary();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("纵断面图", nullptr, tab_flags(3))) {
            render_tab_profile();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("边界特征", nullptr, tab_flags(4))) {
            render_tab_boundary();
            ImGui::EndTabItem();
        }
        pending_tab_ = -1;
        ImGui::EndTabBar();
    }

    ImGui::EndChild();
    ImGui::PopStyleColor();
}

void CassApp::render_tab_params() {
    ImGui::BeginChild("##tab_params_scroll", ImVec2(0, 0), ImGuiChildFlags_None);

    draw_section_bar("设计高程模式");
    int mode_idx = static_cast<int>(project_.settings.design_mode);
    if (ImGui::RadioButton("平场设计标高", &mode_idx, 0)) project_.settings.design_mode = DesignMode::Flat;
    ImGui::SameLine();
    if (ImGui::RadioButton("倾斜设计面", &mode_idx, 1)) project_.settings.design_mode = DesignMode::Plane;
    ImGui::SameLine();
    if (ImGui::RadioButton("两期对比", &mode_idx, 2)) project_.settings.design_mode = DesignMode::Compare;

    ImGui::Spacing();
    if (project_.settings.design_mode == DesignMode::Flat) {
        ImGui::TextColored(rgba(0xB8, 0xC4, 0xD4), "平场设计标高 (m)");
        ImGui::SetNextItemWidth(-96.0f);
        ImGui::InputDouble("##flat_elev", &project_.settings.design_elevation, 0.5, 2.0, "%.3f");
        ImGui::SameLine();
        if (colored_button("求平衡H0", rgba(0x2B, 0x4E, 0x7A), rgba(0x36, 0x62, 0x99), rgba(0x21, 0x3D, 0x60), ImVec2(-1, 0))) {
            solve_balance_h0();
        }
        if (!project_.points.empty()) {
            ImGui::TextColored(rgba(0x88, 0x99, 0xAE),
                               "实测高程区间: %.2f ~ %.2f m（均值 %.2f m）",
                               min_nat_z_, max_nat_z_, avg_nat_z_);
        }
    } else if (project_.settings.design_mode == DesignMode::Plane) {
        auto& pl = project_.settings.design_plane;
        ImGui::InputDouble("基准点 X0", &pl.x0, 1.0, 10.0, "%.3f");
        ImGui::InputDouble("基准点 Y0", &pl.y0, 1.0, 10.0, "%.3f");
        ImGui::InputDouble("基准高程 H0", &pl.h0, 0.5, 2.0, "%.3f");
        ImGui::InputDouble("X 方向坡度(%)", &pl.slope_x, 0.1, 1.0, "%.3f");
        ImGui::InputDouble("Y 方向坡度(%)", &pl.slope_y, 0.1, 1.0, "%.3f");
        if (colored_button("最小二乘最优斜面", rgba(0x2B, 0x4E, 0x7A), rgba(0x36, 0x62, 0x99), rgba(0x21, 0x3D, 0x60), ImVec2(162.0f, 0))) {
            solve_best_fit_plane_action();
        }
        ImGui::SameLine();
        if (ImGui::Button("保坡求平衡H0", ImVec2(-1, 0))) {
            solve_balance_plane_action();
        }
    } else {
        ImGui::Text("对比期测点数: %zu 个", project_.compare_points.size());
        if (ImGui::Button("导入对比期测点...", ImVec2(-1, 0))) action_import_compare();
        if (!project_.compare_points.empty() && ImGui::Button("互换前后两期数据", ImVec2(-1, 0))) {
            std::swap(project_.points, project_.compare_points);
            compare_invert_ = 1 - compare_invert_;
            update_point_stats();
            if (result_) run_calculation();
        }
        if (compare_invert_ != 0) {
            ImGui::TextColored(rgba(0xF5, 0x9E, 0x0B), "提示: 已互换前期/后期测点顺序");
        }
    }

    ImGui::Spacing();
    draw_section_bar("构网约束与土方系数");
    ImGui::InputDouble("最大允许边长(m)", &project_.settings.max_edge_length, 5.0, 20.0, "%.1f");
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("设为 0 不剔除超长边界外围畸形三角形；建议取测点平均间距的 2.5~4 倍");
    ImGui::InputDouble("最初可松性 Ks", &project_.settings.soil_factors.loose, 0.02, 0.1, "%.3f");
    ImGui::InputDouble("最终压实系数 Ks'", &project_.settings.soil_factors.compact, 0.02, 0.1, "%.3f");

    ImGui::Spacing();
    draw_section_bar("等高线与方格网校核");
    if (ImGui::Checkbox("计算后自动提取等高线", &project_.settings.contours_enabled)) {
        canvas_.layers.contours = project_.settings.contours_enabled;
    }
    if (project_.settings.contours_enabled) {
        ImGui::InputDouble("等高距(0=自动)", &project_.settings.contour_interval, 0.5, 1.0, "%.2f");
    }
    if (ImGui::Checkbox("启用方格网法同步校核", &project_.settings.grid_enabled)) {
        canvas_.layers.grid = project_.settings.grid_enabled;
    }
    if (project_.settings.grid_enabled) {
        ImGui::TextColored(rgba(0xB8, 0xC4, 0xD4), "方格网边长 (m)");
        ImGui::SetNextItemWidth(-96.0f);
        ImGui::InputDouble("##grid_spacing", &project_.settings.grid_spacing, 1.0, 5.0, "%.1f");
        ImGui::SameLine();
        if (ImGui::Button("自动建议", ImVec2(-1, 0))) {
            project_.settings.grid_spacing = suggest_grid_spacing(
                std::max(max_x_ - min_x_, 10.0), std::max(max_y_ - min_y_, 10.0));
        }
    }

    ImGui::Spacing();
    if (colored_button("执行 TIN 构网与方量计算 (F5)", rgba(0x16, 0x7A, 0x55), rgba(0x1D, 0x99, 0x6B), rgba(0x12, 0x63, 0x45), ImVec2(-1, 32.0f))) {
        run_calculation();
    }

    if (last_balance_) {
        ImGui::Spacing();
        draw_section_bar("零平衡标高结果");
        ImGui::Text("平衡标高 H0: %s m", format_num(last_balance_->elevation, 4).c_str());
        ImGui::Text("平衡挖方/填方: %s / %s m3",
                    format_num(last_balance_->cut_volume, 2).c_str(),
                    format_num(last_balance_->fill_volume, 2).c_str());
    }

    if (result_ && !result_->warnings.empty()) {
        ImGui::Spacing();
        draw_section_bar("构网与算量告警");
        for (const auto& w : result_->warnings) {
            ImGui::TextWrapped("[!] %s", w.c_str());
        }
    }

    ImGui::EndChild();
}

void CassApp::render_tab_points() {
    draw_section_bar("数据质量检查");
    ImGui::Text("测点总数: %zu  |  质量问题合计: %d 项", project_.points.size(), issues_.total_count());
    ImGui::TextColored(issues_.duplicate_coords.empty() ? rgba(0x70, 0xC9, 0x95) : rgba(0xF5, 0x9E, 0x0B),
                       "重复坐标: %zu   重号: %zu   高程异常: %zu",
                       issues_.duplicate_coords.size(),
                       issues_.duplicate_ids.size(),
                       issues_.elevation_outliers.size());
    if (!project_.points.empty()) {
        ImGui::TextColored(rgba(0x94, 0xA3, 0xB8),
                           "坐标范围 X:[%.2f, %.2f]  Y:[%.2f, %.2f]  Z:[%.2f, %.2f]",
                           min_x_, max_x_, min_y_, max_y_, min_nat_z_, max_nat_z_);
    }

    ImGui::Spacing();
    ImGui::SetNextItemWidth(-1);
    ImGui::InputTextWithHint("##pt_filter", "按点号过滤检索...", point_filter_buf_, sizeof(point_filter_buf_));

    std::string needle = upper_ascii(trim_copy(point_filter_buf_));
    std::vector<int> filtered;
    filtered.reserve(project_.points.size());
    for (int i = 0; i < static_cast<int>(project_.points.size()); ++i) {
        if (needle.empty() || upper_ascii(project_.points[i].id).find(needle) != std::string::npos) {
            filtered.push_back(i);
        }
    }

    ImGuiTableFlags tf = ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg |
                         ImGuiTableFlags_ScrollY | ImGuiTableFlags_SizingStretchProp;
    if (ImGui::BeginTable("##points_table", 5, tf, ImVec2(0, 0))) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("点号", ImGuiTableColumnFlags_WidthFixed, 62.0f);
        ImGui::TableSetupColumn("东坐标 X", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("北坐标 Y", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("实测 Z", ImGuiTableColumnFlags_WidthFixed, 64.0f);
        ImGui::TableSetupColumn("高差 ΔZ", ImGuiTableColumnFlags_WidthFixed, 62.0f);
        ImGui::TableHeadersRow();

        ImGuiListClipper clipper;
        clipper.Begin(static_cast<int>(filtered.size()));
        while (clipper.Step()) {
            for (int r = clipper.DisplayStart; r < clipper.DisplayEnd; ++r) {
                const auto& p = project_.points[filtered[r]];
                double dz = p.z - project_.settings.design_elevation;
                if (project_.settings.design_mode == DesignMode::Plane) {
                    dz = p.z - project_.settings.design_plane.elevation(p.x, p.y);
                }
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0); ImGui::TextUnformatted(p.id.c_str());
                ImGui::TableSetColumnIndex(1); ImGui::Text("%.3f", p.x);
                ImGui::TableSetColumnIndex(2); ImGui::Text("%.3f", p.y);
                ImGui::TableSetColumnIndex(3); ImGui::Text("%.3f", p.z);
                ImGui::TableSetColumnIndex(4);
                ImGui::TextColored(dz >= 0.0 ? rgba(0xF4, 0x83, 0x74) : rgba(0x63, 0xB8, 0xFF), "%+.3f", dz);
            }
        }
        ImGui::EndTable();
    }
}

void CassApp::render_tab_summary() {
    if (!result_) {
        ImGui::TextWrapped("尚未执行土方计算。请先导入高程测点并点击“开始计算 (F5)”。");
        return;
    }

    draw_section_bar("TIN 算量汇总指标");
    if (ImGui::BeginTable("##summary_kv", 2, ImGuiTableFlags_BordersInnerH | ImGuiTableFlags_SizingStretchProp)) {
        auto kv = [](const char* k, const std::string& v, ImVec4 vc = rgba(0xE6, 0xED, 0xF3)) {
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0); ImGui::TextColored(rgba(0x94, 0xA3, 0xB8), "%s", k);
            ImGui::TableSetColumnIndex(1); ImGui::TextColored(vc, "%s", v.c_str());
        };
        kv("设计方式", result_->design_description);
        kv("总挖方量", format_num(result_->total_cut, 2) + " m³", rgba(0xF4, 0x83, 0x74));
        kv("总填方量", format_num(result_->total_fill, 2) + " m³", rgba(0x63, 0xB8, 0xFF));
        kv("挖填净方", format_num(result_->net_volume, 2) + " m³", rgba(0xFB, 0xBF, 0x24));
        kv("有效面积", format_num(result_->computed_area, 2) + " m² (" + format_num(result_->computed_area / 666.6667, 3) + " 亩)");
        kv("三角网规模", std::to_string(result_->triangle_count) + " 个 (混合零线 " + std::to_string(result_->mixed_triangle_count) + " / 边界裁剪 " + std::to_string(result_->clipped_triangle_count) + ")");
        if (!result_->soil_balance.factors.is_default()) {
            kv("松散挖方(运出)", format_num(result_->soil_balance.cut_loose(), 2) + " m³");
            kv("压实需自然方", format_num(result_->soil_balance.fill_natural(), 2) + " m³");
        }
        if (result_->grid_check) {
            const auto& gc = *result_->grid_check;
            double diff_pct = (std::abs(result_->total_cut) > 1e-6)
                ? ((gc.total_cut - result_->total_cut) / result_->total_cut * 100.0) : 0.0;
            kv("方格网校核(挖/填)", format_num(gc.total_cut, 1) + " / " + format_num(gc.total_fill, 1) + " m³");
            kv("格网与TIN挖方偏差", format_num(diff_pct, 2) + " %");
        }
        ImGui::EndTable();
    }

    ImGui::Spacing();
    draw_section_bar("逐三角形土方明细 (点击联动定位)");
    const char* filters[] = {"全部", "纯挖", "纯填", "混合", "边界裁剪"};
    ImGui::SetNextItemWidth(-1);
    ImGui::Combo("##tri_filter", &tri_filter_mode_, filters, IM_ARRAYSIZE(filters));

    std::vector<int> vis_tris;
    vis_tris.reserve(result_->triangles.size());
    for (int i = 0; i < static_cast<int>(result_->triangles.size()); ++i) {
        const auto& t = result_->triangles[i];
        if (t.is_boundary) continue;
        if (tri_filter_mode_ == 1 && (t.is_mixed || t.cut_volume <= 0.0)) continue;
        if (tri_filter_mode_ == 2 && (t.is_mixed || t.fill_volume <= 0.0)) continue;
        if (tri_filter_mode_ == 3 && !t.is_mixed) continue;
        if (tri_filter_mode_ == 4 && !t.is_clipped) continue;
        vis_tris.push_back(i);
    }

    ImGuiTableFlags tf = ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg |
                         ImGuiTableFlags_ScrollY | ImGuiTableFlags_SizingStretchProp;
    if (ImGui::BeginTable("##tri_detail_table", 5, tf, ImVec2(0, 0))) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("#ID", ImGuiTableColumnFlags_WidthFixed, 48.0f);
        ImGui::TableSetupColumn("面积(m2)", ImGuiTableColumnFlags_WidthFixed, 66.0f);
        ImGui::TableSetupColumn("挖方(m3)", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("填方(m3)", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("类型", ImGuiTableColumnFlags_WidthFixed, 48.0f);
        ImGui::TableHeadersRow();

        ImGuiListClipper clipper;
        clipper.Begin(static_cast<int>(vis_tris.size()));
        while (clipper.Step()) {
            for (int r = clipper.DisplayStart; r < clipper.DisplayEnd; ++r) {
                const auto& t = result_->triangles[vis_tris[r]];
                bool is_sel = (selected_tri_id_ == t.id);
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0);
                std::string lbl = std::to_string(t.id) + "##tr" + std::to_string(t.id);
                if (ImGui::Selectable(lbl.c_str(), is_sel, ImGuiSelectableFlags_SpanAllColumns)) {
                    set_selected_triangle(t.id);
                }
                ImGui::TableSetColumnIndex(1); ImGui::Text("%.2f", t.area);
                ImGui::TableSetColumnIndex(2); ImGui::TextColored(rgba(0xF4, 0x83, 0x74), "%.2f", t.cut_volume);
                ImGui::TableSetColumnIndex(3); ImGui::TextColored(rgba(0x63, 0xB8, 0xFF), "%.2f", t.fill_volume);
                ImGui::TableSetColumnIndex(4);
                ImGui::TextUnformatted(t.is_mixed ? "混合" : (t.cut_volume > t.fill_volume ? "挖" : "填"));
            }
        }
        ImGui::EndTable();
    }
}

void CassApp::render_tab_profile() {
    draw_section_bar("单线纵断面剖切");
    if (ImGui::Button("水平中线", ImVec2(96.0f, 0))) generate_quick_profile(true);
    ImGui::SameLine();
    if (ImGui::Button("垂直中线", ImVec2(96.0f, 0))) generate_quick_profile(false);
    ImGui::SameLine();
    if (colored_button("图上拾取", rgba(0x2B, 0x4E, 0x7A), rgba(0x36, 0x62, 0x99), rgba(0x21, 0x3D, 0x60), ImVec2(-1, 0))) {
        canvas_.clear_draft();
        canvas_.tool = CadToolMode::PickProfile;
        status_text_ = "请在画布上依次左键点击断面起点与终点";
    }

    ImGui::Spacing();
    draw_section_bar("平行横断面法批量算量");
    ImGui::SetNextItemWidth(100.0f);
    ImGui::InputDouble("间距(m)##sec_sp", &section_spacing_, 5.0, 10.0, "%.1f");
    ImGui::SameLine();
    ImGui::SetNextItemWidth(85.0f);
    ImGui::InputDouble("方位(°)##sec_az", &section_azimuth_, 15.0, 45.0, "%.0f");
    if (colored_button("执行断面法算量", rgba(0x16, 0x7A, 0x55), rgba(0x1D, 0x99, 0x6B), rgba(0x12, 0x63, 0x45), ImVec2(170.0f, 0))) {
        run_sections_action(std::max(1.0, section_spacing_), section_azimuth_);
    }
    ImGui::SameLine();
    if (ImGui::Button("导出断面表 CSV", ImVec2(-1, 0))) {
        action_export_sections_csv();
    }

    if (result_ && result_->sections && !result_->sections->stations.empty()) {
        const auto& sec = *result_->sections;
        double cut_diff = (std::abs(result_->total_cut) > 1e-6)
            ? ((sec.total_cut - result_->total_cut) / result_->total_cut * 100.0) : 0.0;
        ImGui::TextColored(rgba(0xD8, 0xB4, 0xFE),
                           "断面法 (%zu条): 挖 %s / 填 %s m³ (挖偏差 %+.2f%%)",
                           sec.stations.size(),
                           format_num(sec.total_cut, 1).c_str(),
                           format_num(sec.total_fill, 1).c_str(),
                           cut_diff);

        ImGuiTableFlags tf = ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg |
                             ImGuiTableFlags_ScrollY | ImGuiTableFlags_SizingStretchProp;
        if (ImGui::BeginTable("##sec_stations", 4, tf, ImVec2(0, 118.0f))) {
            ImGui::TableSetupScrollFreeze(0, 1);
            ImGui::TableSetupColumn("桩号", ImGuiTableColumnFlags_WidthFixed, 68.0f);
            ImGui::TableSetupColumn("挖面积", ImGuiTableColumnFlags_WidthStretch);
            ImGui::TableSetupColumn("填面积", ImGuiTableColumnFlags_WidthStretch);
            ImGui::TableSetupColumn("段挖方", ImGuiTableColumnFlags_WidthStretch);
            ImGui::TableHeadersRow();
            for (int i = 0; i < static_cast<int>(sec.stations.size()); ++i) {
                const auto& st = sec.stations[i];
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0);
                std::string lbl = st.name + "##sec" + std::to_string(i);
                if (ImGui::Selectable(lbl.c_str(), selected_section_idx_ == i, ImGuiSelectableFlags_SpanAllColumns)) {
                    selected_section_idx_ = i;
                    profile_line_ = Segment{st.start, st.end};
                    profile_result_ = st.profile;
                }
                ImGui::TableSetColumnIndex(1); ImGui::TextColored(rgba(0xF4, 0x83, 0x74), "%.2f", st.cut_area);
                ImGui::TableSetColumnIndex(2); ImGui::TextColored(rgba(0x63, 0xB8, 0xFF), "%.2f", st.fill_area);
                ImGui::TableSetColumnIndex(3); ImGui::Text("%.1f", st.seg_cut_vol);
            }
            ImGui::EndTable();
        }
    }

    if (profile_result_) {
        ImGui::Spacing();
        ImGui::Text("长 %s m | 挖深 %s m | 填高 %s m | 挖/填面积 %s / %s m²",
                    format_num(profile_result_->length, 1).c_str(),
                    format_num(profile_result_->max_cut_depth, 2).c_str(),
                    format_num(profile_result_->max_fill_depth, 2).c_str(),
                    format_num(profile_result_->cut_area, 2).c_str(),
                    format_num(profile_result_->fill_area, 2).c_str());
    }

    ImGui::Spacing();
    const ProfileResult* prof_ptr = profile_result_ ? &(*profile_result_) : nullptr;
    float rem_h = std::max(210.0f, ImGui::GetContentRegionAvail().y - 4.0f);
    render_profile_chart(prof_ptr, canvas_.dark_mode, rem_h);
}

void CassApp::render_tab_boundary() {
    draw_section_bar("外部计算边界");
    double b_area = (project_.boundary.size() >= 3) ? std::abs(signed_area(project_.boundary)) : 0.0;
    ImGui::Text("当前边界顶点数: %zu 个  (闭合面积 %s m2)", project_.boundary.size(), format_num(b_area, 2).c_str());
    if (ImGui::Button("外接矩形边界", ImVec2(106, 0))) generate_rect_boundary();
    ImGui::SameLine();
    if (ImGui::Button("自动轮廓边界", ImVec2(106, 0))) generate_auto_outline();
    ImGui::SameLine();
    if (ImGui::Button("清除边界", ImVec2(-1, 0))) clear_boundary();
    if (ImGui::Button("图上手绘边界", ImVec2(166, 0))) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawBoundary; }
    ImGui::SameLine();
    if (ImGui::Button("导入边界文件...", ImVec2(-1, 0))) action_import_boundary();

    ImGui::Spacing();
    draw_section_bar("内部挖空岛屿 (扣除面积)");
    ImGui::Text("已定义挖空区: %zu 个", project_.holes.size());
    if (ImGui::Button("图上手绘挖空区", ImVec2(166, 0))) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawHole; }
    ImGui::SameLine();
    if (ImGui::Button("清空全部挖空", ImVec2(-1, 0))) project_.holes.clear();

    for (size_t i = 0; i < project_.holes.size(); ++i) {
        double ha = (project_.holes[i].size() >= 3) ? std::abs(signed_area(project_.holes[i])) : 0.0;
        ImGui::BulletText("挖空 #%zu: %zu 顶点 (面积 %.2f m2)", i + 1, project_.holes[i].size(), ha);
        ImGui::SameLine();
        std::string del_lbl = "删除##h" + std::to_string(i);
        if (ImGui::SmallButton(del_lbl.c_str())) {
            project_.holes.erase(project_.holes.begin() + static_cast<ptrdiff_t>(i));
            break;
        }
    }

    ImGui::Spacing();
    draw_section_bar("约束特征线 (Breaklines)");
    ImGui::Text("已加载特征线: %zu 条", project_.breaklines.size());
    if (ImGui::Button("图上绘制特征线", ImVec2(166, 0))) { canvas_.clear_draft(); canvas_.tool = CadToolMode::DrawBreakline; }
    ImGui::SameLine();
    if (ImGui::Button("导入 DXF 特征线", ImVec2(-1, 0))) action_import_breaklines_dxf();

    for (size_t i = 0; i < project_.breaklines.size(); ++i) {
        ImGui::BulletText("%s: %zu 顶点%s",
                          project_.breaklines[i].name.c_str(),
                          project_.breaklines[i].points.size(),
                          project_.breaklines[i].closed ? " (闭合)" : "");
        ImGui::SameLine();
        std::string del_lbl = "删除##bl" + std::to_string(i);
        if (ImGui::SmallButton(del_lbl.c_str())) {
            project_.breaklines.erase(project_.breaklines.begin() + static_cast<ptrdiff_t>(i));
            break;
        }
    }
}

void CassApp::render_command_console(float height) {
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x11, 0x15, 0x1B));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(8.0f, 5.0f));
    ImGui::BeginChild("##cmd_console", ImVec2(0.0f, height), ImGuiChildFlags_Borders,
                      ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    float log_h = std::max(36.0f, height - 38.0f);
    ImGui::BeginChild("##cmd_log_scroll", ImVec2(0.0f, log_h), ImGuiChildFlags_None);
    if (fonts_.mono) ImGui::PushFont(fonts_.mono);
    for (const auto& line : console_lines_) {
        ImVec4 col = rgba(0xB8, 0xC4, 0xD4);
        if (line.rfind("错误:", 0) == 0) col = rgba(0xF8, 0x71, 0x71);
        else if (line.rfind("警告:", 0) == 0 || line.find("[告警]") != std::string::npos) col = rgba(0xFB, 0xBF, 0x24);
        else if (line.rfind("命令:", 0) == 0) col = rgba(0x63, 0xB3, 0xED);
        ImGui::TextColored(col, "%s", line.c_str());
    }
    if (ImGui::GetScrollY() >= ImGui::GetScrollMaxY() - 8.0f) {
        ImGui::SetScrollHereY(1.0f);
    }
    if (fonts_.mono) ImGui::PopFont();
    ImGui::EndChild();

    ImGui::Separator();
    ImGui::AlignTextToFramePadding();
    ImGui::TextColored(rgba(0x58, 0xA6, 0xFF), "命令:");
    ImGui::SameLine();

    if (focus_cmd_input_) {
        ImGui::SetKeyboardFocusHere();
        focus_cmd_input_ = false;
    }

    struct HistCtx {
        std::vector<std::string>* list;
        int* pos;
    } hctx{&cmd_history_, &history_pos_};

    if (fonts_.mono) ImGui::PushFont(fonts_.mono);
    ImGui::SetNextItemWidth(-1.0f);
    ImGuiInputTextFlags cflags = ImGuiInputTextFlags_EnterReturnsTrue | ImGuiInputTextFlags_CallbackHistory;
    if (ImGui::InputTextWithHint("##cad_cmd_input", "输入 CAD 指令 (HELP / AUTOB / OUTLINE / CALC / H0 / ZE / DARK / LIGHT)...",
                                 cmd_buf_, sizeof(cmd_buf_), cflags, command_history_callback, &hctx)) {
        std::string entered = cmd_buf_;
        cmd_buf_[0] = '\0';
        execute_command(entered);
        focus_cmd_input_ = true;
    }
    if (fonts_.mono) ImGui::PopFont();

    ImGui::EndChild();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();
}

void CassApp::render_status_bar(float height) {
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x16, 0x1A, 0x21));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(8.0f, 3.0f));
    ImGui::BeginChild("##status_bar", ImVec2(0.0f, height), ImGuiChildFlags_Borders,
                      ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    std::ostringstream coord_ss;
    if (canvas_.has_hover()) {
        XY hw = canvas_.hover_world();
        coord_ss << "X=" << std::fixed << std::setprecision(2) << hw.x
                 << "  Y=" << std::fixed << std::setprecision(2) << hw.y;
    } else {
        coord_ss << "X=--  Y=--";
    }

    std::ostringstream right_ss;
    right_ss << coord_ss.str()
             << "  |  捕捉:" << (canvas_.osnap_enabled ? "开" : "关")
             << "  |  工具:" << tool_name_cn(canvas_.tool)
             << "  |  " << (canvas_.dark_mode ? "模型(黑底)" : "布局(白底)")
             << "  |  测点:" << project_.points.size();
    if (result_) {
        right_ss << " / 三角:" << result_->triangle_count;
    }

    std::string right_str = right_ss.str();
    float rw = ImGui::CalcTextSize(right_str.c_str()).x + 12.0f;
    float avail_w = ImGui::GetContentRegionAvail().x;

    ImGui::TextColored(rgba(0xB4, 0xC2, 0xD4), "%s", status_text_.c_str());
    if (avail_w > rw + 180.0f) {
        ImGui::SameLine(avail_w - rw);
        ImGui::TextColored(rgba(0x7E, 0xB8, 0xF7), "%s", right_str.c_str());
    }

    ImGui::EndChild();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();
}

void CassApp::render_modals() {
    if (show_help_modal_) {
        ImGui::OpenPopup("CAD 命令与快捷键速查");
        show_help_modal_ = false;
    }
    if (ImGui::BeginPopupModal("CAD 命令与快捷键速查", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::Text("全局快捷键:");
        ImGui::BulletText("F5: 立即执行 TIN 构网与方量计算");
        ImGui::BulletText("Esc: 取消绘制草稿 / 返回选择工具");
        ImGui::BulletText("Enter: 闭合确认当前手绘边界/挖空区/特征线");
        ImGui::BulletText("鼠标滚轮: 以光标为中心缩放画布; 右键或滚轮点击触发平移");
        ImGui::Spacing();
        ImGui::Text("命令行常用指令:");
        ImGui::BulletText("AUTOB / OUTLINE: 自动生成矩形或凹轮廓边界");
        ImGui::BulletText("CALC / H0: 执行算量 / 求解零平衡标高");
        ImGui::BulletText("H=50.5: 直接修改平场标高并重算");
        ImGui::BulletText("ZE / DARK / LIGHT: 全图缩放 / 切换黑底白底");
        ImGui::Spacing();
        if (ImGui::Button("关闭", ImVec2(120, 0))) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }

    if (show_about_modal_) {
        ImGui::OpenPopup("关于 TIN 土方自动算量工作站");
        show_about_modal_ = false;
    }
    if (ImGui::BeginPopupModal("关于 TIN 土方自动算量工作站", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::Text("TIN 土方自动算量工作站 — 南方CASS CAD 原生 C++20 版");
        ImGui::Separator();
        ImGui::Text("架构栈: C++20 + Win32 + Direct3D 11 + Dear ImGui + 约束 Delaunay CDT");
        ImGui::Text("支持能力: 平场/倾斜面/两期对比、零线剖分、凹边界裁剪、挖空岛屿、特征线、");
        ImGui::Text("          零平衡标高 H0、方格网同期校核、等高线追踪、任意纵断面与 R12 DXF 导出");
        ImGui::Spacing();
        if (ImGui::Button("确定", ImVec2(120, 0))) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }
}

} // namespace tin::ui
