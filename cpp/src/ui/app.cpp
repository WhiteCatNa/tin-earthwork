#include "ui/app.hpp"

#include "core/geometry.hpp"
#include "io/io.hpp"
#include "imgui.h"

#ifdef _WIN32
#include <commdlg.h>
#endif

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
    case CadToolMode::MeasureDist: return "交互测距/坡度";
    case CadToolMode::MeasureArea: return "测面积/局部方量";
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
    struct HistCtx {
        std::vector<std::string>* list;
        int* pos;
        std::function<void(const std::string&)> on_log;
    };
    auto* ctx = static_cast<HistCtx*>(data->UserData);
    if (!ctx) return 0;

    if (data->EventFlag == ImGuiInputTextFlags_CallbackCompletion) {
        std::string cur(data->Buf, static_cast<size_t>(data->BufTextLen));
        auto cands = suggest_command_completions(cur);
        if (cands.size() == 1) {
            data->DeleteChars(0, data->BufTextLen);
            data->InsertChars(0, cands[0].c_str());
        } else if (cands.size() > 1) {
            // 计算最长公共前缀并输出候选清单
            std::string lcp = cands[0];
            for (size_t i = 1; i < cands.size(); ++i) {
                size_t k = 0;
                while (k < lcp.size() && k < cands[i].size() && lcp[k] == cands[i][k]) ++k;
                lcp.resize(k);
            }
            if (lcp.size() > cur.size()) {
                data->DeleteChars(0, data->BufTextLen);
                data->InsertChars(0, lcp.c_str());
            } else if (ctx->on_log) {
                std::string hint = "补全候选: ";
                for (size_t i = 0; i < std::min<size_t>(cands.size(), 10); ++i) {
                    if (i > 0) hint += " | ";
                    hint += cands[i];
                }
                ctx->on_log(hint);
            }
        }
        return 0;
    }

    if (data->EventFlag == ImGuiInputTextFlags_CallbackHistory && ctx->list && !ctx->list->empty()) {
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

CassApp::CassApp(NativeWindowHandle hwnd, const ThemeFonts& fonts)
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
        project_.boundary, project_.holes, project_.breaklines, project_.settings, std::nullopt
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
    EditSnapshot prev = std::move(undo_stack_.back());
    undo_stack_.pop_back();
    std::optional<std::vector<SurveyPoint>> cur_pts = prev.points.has_value()
        ? std::optional<std::vector<SurveyPoint>>(project_.points)
        : std::nullopt;
    redo_stack_.push_back(EditSnapshot{
        project_.boundary, project_.holes, project_.breaklines, project_.settings, std::move(cur_pts)
    });
    project_.boundary = std::move(prev.boundary);
    project_.holes = std::move(prev.holes);
    project_.breaklines = std::move(prev.breaklines);
    project_.settings = std::move(prev.settings);
    if (prev.points.has_value()) {
        project_.points = std::move(*prev.points);
        update_point_stats();
    }
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
    EditSnapshot next = std::move(redo_stack_.back());
    redo_stack_.pop_back();
    std::optional<std::vector<SurveyPoint>> cur_pts = next.points.has_value()
        ? std::optional<std::vector<SurveyPoint>>(project_.points)
        : std::nullopt;
    undo_stack_.push_back(EditSnapshot{
        project_.boundary, project_.holes, project_.breaklines, project_.settings, std::move(cur_pts)
    });
    project_.boundary = std::move(next.boundary);
    project_.holes = std::move(next.holes);
    project_.breaklines = std::move(next.breaklines);
    project_.settings = std::move(next.settings);
    if (next.points.has_value()) {
        project_.points = std::move(*next.points);
        update_point_stats();
    }
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

bool CassApp::compute_strata_action() {
    if (!result_) {
        if (!run_calculation()) return false;
    }
    std::vector<double> bounds;
    std::string raw(strata_bounds_buf_);
    for (char& ch : raw) {
        if (ch == ',' || ch == ';' || ch == ' ') ch = ' ';
    }
    std::istringstream iss(raw);
    double v = 0.0;
    while (iss >> v) bounds.push_back(v);
    if (bounds.empty()) {
        bounds = (strata_mode_idx_ == 1) ? std::vector<double>{0.2, 0.4}
                                         : std::vector<double>{avg_nat_z_};
    }
    try {
        StrataMode mode = (strata_mode_idx_ == 0) ? StrataMode::ByElevation : StrataMode::ByDepth;
        strata_result_ = compute_strata_volumes(*result_, mode, bounds);
        std::ostringstream oss;
        oss << "分层土方统计完成（" << strata_result_->layers.size() << " 层，总挖 "
            << format_num(strata_result_->total_cut, 2) << " m³ / 总填 "
            << format_num(strata_result_->total_fill, 2) << " m³）";
        status_text_ = oss.str();
        log_cmd("命令: _STRATA -> " + oss.str());
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("分层土方计算失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::compute_haul_action() {
    if (!result_) {
        if (!run_calculation()) return false;
    }
    try {
        haul_result_ = compute_haul_plan(*result_, haul_cols_, haul_rows_);
        canvas_.layers.haul = true;
        std::ostringstream oss;
        oss << "土方调配优化完成（" << haul_result_->zones.size() << " 区，"
            << haul_result_->flows.size() << " 条流向）：场内调配 "
            << format_num(haul_result_->cross_zone_hauled, 1) << " m³，平均运距 "
            << format_num(haul_result_->avg_haul_distance, 1) << " m，估算造价 "
            << format_num(haul_result_->estimated_cost / 10000.0, 2) << " 万元";
        status_text_ = oss.str();
        log_cmd("命令: _HAUL -> " + oss.str());
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("土方调配计算失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::compute_slope_action() {
    if (project_.points.size() < 3) {
        status_text_ = "测点不足，无法进行场地放坡设计";
        return false;
    }
    try {
        if (!result_) {
            if (!run_calculation()) return false;
        }
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        slope_result_ = compute_slope_grading(calc, slope_cut_ratio_, slope_fill_ratio_, 4.0);
        canvas_.layers.slope = true;
        std::ostringstream oss;
        oss << "场地周边放坡完成（挖 1:" << format_num(slope_cut_ratio_, 1)
            << " / 填 1:" << format_num(slope_fill_ratio_, 1)
            << "）：边坡挖方 " << format_num(slope_result_->slope_cut_volume, 1)
            << " m³，填方 " << format_num(slope_result_->slope_fill_volume, 1)
            << " m³，最大坡宽 " << format_num(slope_result_->max_slope_width, 2) << " m";
        status_text_ = oss.str();
        log_cmd("命令: _SLOPE -> " + oss.str());
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("场地放坡计算失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

void CassApp::set_3d_view(bool enabled) {
    show_3d_view_ = enabled;
    viewport_3d_.dark_mode = canvas_.dark_mode;
    status_text_ = enabled ? "已切换至 3D 实景地形与设计面透视视口" : "已返回 2D CAD 平面视口";
}

void CassApp::set_3d_color_mode(ColorMode3D mode) {
    viewport_3d_.color_mode = mode;
}

void CassApp::set_tool_mode(CadToolMode mode) {
    canvas_.clear_draft();
    canvas_.tool = mode;
    status_text_ = std::string("已切换到工具: ") + tool_name_cn(mode);
}

bool CassApp::run_grade_optimization_action() {
    if (project_.points.size() < 3) {
        status_text_ = "测点不足，无法执行联合费用优化";
        return false;
    }
    try {
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        grade_opt_result_ = optimize_grade_cost(calc, cost_params_, opt_constraints_);
        const auto& opt = grade_opt_result_->optimized;
        std::ostringstream oss;
        oss << "AI 联合费用优化完成（评估 " << grade_opt_result_->evaluations << " 次，"
            << format_num(grade_opt_result_->elapsed_ms, 1) << " ms）：最优总成本 ¥"
            << format_num(opt.total_cost, 0) << "（较平场平衡省 "
            << format_num(grade_opt_result_->savings_pct_vs_flat, 1) << "%）";
        status_text_ = oss.str();
        log_cmd("命令: _AIOPT -> " + oss.str());
        diagnosis_summary_ = generate_site_diagnosis(
            project_, result_ ? &(*result_) : nullptr,
            health_report_ ? &(*health_report_) : nullptr,
            &(*grade_opt_result_),
            uncertainty_report_ ? &(*uncertainty_report_) : nullptr);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("联合费用优化失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::apply_optimized_plane_action() {
    if (!grade_opt_result_) return false;
    push_undo_snapshot();
    project_.settings.design_mode = DesignMode::Plane;
    project_.settings.design_plane = grade_opt_result_->optimized.plane;
    bool ok = run_calculation();
    if (ok) compute_haul_action();
    status_text_ = "已将 AI 费用最优斜面写入工程参数并更新方量与调配";
    log_cmd("命令: _APPLY_OPT -> " + status_text_);
    return ok;
}

bool CassApp::run_anomaly_diagnosis_action() {
    if (project_.points.empty()) {
        status_text_ = "请先导入测点数据";
        return false;
    }
    try {
        health_report_ = detect_survey_anomalies(project_.points);
        canvas_.layers.anomalies = true;
        status_text_ = health_report_->summary;
        log_cmd("命令: _DIAG -> " + health_report_->summary);
        diagnosis_summary_ = generate_site_diagnosis(
            project_, result_ ? &(*result_) : nullptr,
            &(*health_report_),
            grade_opt_result_ ? &(*grade_opt_result_) : nullptr,
            uncertainty_report_ ? &(*uncertainty_report_) : nullptr);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("测点异常诊断失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::clean_anomalies_action() {
    if (!health_report_) {
        if (!run_anomaly_diagnosis_action()) return false;
    }
    if (health_report_->anomalies.empty()) {
        status_text_ = "未检出需要修复的异常点";
        return true;
    }
    undo_stack_.push_back(EditSnapshot{
        project_.boundary, project_.holes, project_.breaklines, project_.settings, project_.points
    });
    if (undo_stack_.size() > 50) undo_stack_.erase(undo_stack_.begin());
    redo_stack_.clear();

    auto cleaned = clean_survey_anomalies(project_.points, *health_report_, true);
    project_.points = std::move(cleaned.points);
    update_point_stats();
    if (result_) run_calculation();
    health_report_ = detect_survey_anomalies(project_.points);
    std::ostringstream oss;
    oss << "已局部插值修复 " << cleaned.repaired_count << " 个高程粗差、剔除 "
        << cleaned.removed_count << " 个飞点/冲突点（健康分 -> " << health_report_->health_score << "/100）";
    status_text_ = oss.str();
    log_cmd("命令: _CLEAN -> " + status_text_);
    return true;
}

bool CassApp::run_uncertainty_action() {
    if (project_.points.size() < 4) {
        status_text_ = "至少需要 4 个测点才能评定不确定度";
        return false;
    }
    try {
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        uncertainty_report_ = analyze_uncertainty_and_recommend(calc, measure_sigma_, recommend_count_, 8);
        canvas_.layers.survey_recs = true;
        status_text_ = uncertainty_report_->summary;
        log_cmd("命令: _UNCERT -> " + uncertainty_report_->summary);
        diagnosis_summary_ = generate_site_diagnosis(
            project_, result_ ? &(*result_) : nullptr,
            health_report_ ? &(*health_report_) : nullptr,
            grade_opt_result_ ? &(*grade_opt_result_) : nullptr,
            &(*uncertainty_report_));
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("不确定度评定失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::run_significance_action() {
    if (project_.points.size() < 4) {
        status_text_ = "至少需要 4 个测点才能分析变化显著性";
        return false;
    }
    try {
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        significance_report_ = analyze_change_significance(calc, measure_sigma_);
        status_text_ = significance_report_->summary;
        log_cmd("命令: _SIGNIF -> " + significance_report_->summary);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("显著性分析失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::run_thinning_action(double tol_m) {
    if (project_.points.size() < 5) {
        status_text_ = "测点太少，无需抽稀";
        return false;
    }
    try {
        thin_tol_m_ = std::max(0.01, tol_m);
        thinning_result_ = thin_points_preserve_accuracy(project_.points, thin_tol_m_);
        status_text_ = thinning_result_->summary;
        log_cmd("命令: _THIN -> " + thinning_result_->summary);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("保精度抽稀失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::run_drainage_action(double min_slope_pct) {
    if (!result_) {
        if (!run_calculation()) return false;
    }
    drain_min_slope_ = std::max(0.05, min_slope_pct);
    bool use_design = (project_.settings.design_mode == DesignMode::Plane);
    drainage_report_ = analyze_drainage_and_ponding(*result_, drain_min_slope_, use_design);
    status_text_ = drainage_report_->summary;
    log_cmd("命令: _DRAIN -> " + drainage_report_->summary);
    return true;
}

bool CassApp::run_sensitivity_action() {
    if (project_.points.size() < 3) {
        status_text_ = "测点不足，无法计算标高敏感性曲线";
        return false;
    }
    try {
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        sensitivity_result_ = run_elevation_sensitivity_curve(calc, cost_params_, 17, 0.0);
        status_text_ = sensitivity_result_->summary;
        log_cmd("命令: _SENSITIVITY -> " + sensitivity_result_->summary);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("敏感性曲线计算失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::run_pareto_action() {
    if (project_.points.size() < 3) {
        status_text_ = "测点不足，无法计算 Pareto 多目标前沿";
        return false;
    }
    try {
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        pareto_result_ = compute_grade_pareto_frontier(calc, cost_params_, opt_constraints_);
        status_text_ = pareto_result_->summary;
        log_cmd("命令: _PARETO -> " + pareto_result_->summary);
        return !pareto_result_->plans.empty();
    } catch (const std::exception& e) {
        status_text_ = std::string("Pareto 多目标优化失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::apply_pareto_plan_action(size_t idx) {
    if (!pareto_result_ || idx >= pareto_result_->plans.size()) return false;
    push_undo_snapshot();
    const auto& chosen = pareto_result_->plans[idx];
    project_.settings.design_mode = DesignMode::Plane;
    project_.settings.use_partition = false;
    project_.settings.design_plane = chosen.summary.plane;
    bool ok = run_calculation();
    if (ok) compute_haul_action();
    status_text_ = "已应用 Pareto 方案「" + chosen.tag + "」";
    log_cmd("命令: _APPLY_PARETO -> " + status_text_);
    return ok;
}

bool CassApp::run_multi_bench_action(int max_benches) {
    if (project_.points.size() < 4) {
        status_text_ = "测点不足，无法执行主坡向多级台阶优化";
        return false;
    }
    try {
        if (max_benches >= 2 && max_benches <= 4) {
            multi_bench_max_k_ = max_benches;
        }
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        multi_bench_result_ = optimize_multi_bench_terraces(calc, cost_params_, multi_bench_max_k_, wall_cost_per_m2_);
        canvas_.layers.stepped = true;
        status_text_ = multi_bench_result_->summary;
        log_cmd("命令: _MULTIBENCH -> " + multi_bench_result_->summary);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("多级台阶 DP 优化失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::apply_multi_bench_action() {
    if (!multi_bench_result_ || multi_bench_result_->benches.empty()) {
        if (!run_multi_bench_action()) return false;
    }
    const auto& mb = *multi_bench_result_;
    push_undo_snapshot();
    project_.settings.design_mode = DesignMode::Flat;
    project_.settings.use_partition = true;
    project_.settings.design_elevation = mb.benches.front().elevation;
    project_.settings.partition.clear();
    for (const auto& pt : project_.points) {
        double s = (pt.x - mb.origin_x) * mb.dir_x + (pt.y - mb.origin_y) * mb.dir_y;
        size_t zone_idx = 0;
        while (zone_idx < mb.dividers.size() && s > mb.dividers[zone_idx].s_coord) {
            ++zone_idx;
        }
        zone_idx = std::min(zone_idx, mb.benches.size() - 1);
        project_.settings.partition[pt.id] = mb.benches[zone_idx].elevation;
    }
    bool ok = run_calculation();
    if (ok) compute_haul_action();
    status_text_ = "已将 " + std::to_string(mb.bench_count) + " 级台阶标高写入分区设计面并完成算量";
    log_cmd("命令: _APPLY_BENCH -> " + status_text_);
    return ok;
}

bool CassApp::run_stepped_platform_action(double wall_cost_per_m2) {
    if (project_.points.size() < 4) {
        status_text_ = "测点不足，无法执行双级台阶平场优化";
        return false;
    }
    try {
        if (wall_cost_per_m2 >= 0.0) {
            wall_cost_per_m2_ = wall_cost_per_m2;
        }
        TINEarthworkCalculator calc(project_.settings.design_elevation);
        configure_calculator(calc);
        calc.build_tin();
        stepped_result_ = optimize_stepped_platform(calc, cost_params_, wall_cost_per_m2_);
        status_text_ = stepped_result_->summary;
        log_cmd("命令: _BENCH -> " + stepped_result_->summary);
        return true;
    } catch (const std::exception& e) {
        status_text_ = std::string("双级台阶优化失败: ") + e.what();
        log_cmd("错误: " + status_text_);
        return false;
    }
}

bool CassApp::run_fleet_estimate_action(double target_days) {
    if (!result_) {
        if (!run_calculation()) return false;
    }
    if (!haul_result_) {
        compute_haul_action();
    }
    if (target_days > 0.5) {
        fleet_params_.target_days = target_days;
    }
    fleet_result_ = estimate_earthwork_fleet(
        *result_, haul_result_ ? &(*haul_result_) : nullptr, fleet_params_);
    status_text_ = fleet_result_->summary;
    log_cmd("命令: _FLEET -> " + fleet_result_->summary);
    return true;
}

bool CassApp::run_ai_full_workflow_action() {
    if (project_.points.size() < 4) {
        status_text_ = "请先导入不少于 4 个测点再运行一键 AI 全流程诊断";
        return false;
    }
    if (!result_) run_calculation();
    bool ok1 = run_anomaly_diagnosis_action();
    bool ok2 = run_grade_optimization_action();
    bool ok3 = run_uncertainty_action();
    run_significance_action();
    run_drainage_action(drain_min_slope_);
    diagnosis_summary_ = generate_site_diagnosis(
        project_, result_ ? &(*result_) : nullptr,
        health_report_ ? &(*health_report_) : nullptr,
        grade_opt_result_ ? &(*grade_opt_result_) : nullptr,
        uncertainty_report_ ? &(*uncertainty_report_) : nullptr);
    if (diagnosis_summary_) {
        status_text_ = diagnosis_summary_->headline;
        log_cmd("AI 全流程诊断完成 -> " + diagnosis_summary_->headline);
    }
    set_inspector_tab(6);
    return ok1 && ok2 && ok3;
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
    pending_tab_ = std::clamp(idx, 0, 6);
}

void CassApp::set_dark_mode(bool dark) {
    canvas_.dark_mode = dark;
    viewport_3d_.dark_mode = dark;
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

    AssistantIntent intent = parse_engineering_intent(cmd);
    if (!intent.reply.empty()) {
        log_cmd(intent.reply);
    }

    switch (intent.action) {
    case AssistantAction::Help:
        log_cmd("CAD 经典指令: AUTOB | OUTLINE | CLRBND | CALC(F5) | BALANCE/H0 | FITPLANE | BALPLANE | SECTIONS");
        log_cmd("专题与绘图   : STRATA | HAUL | SLOPE | GRID | CONTOUR | PROFILE | MEASURE | AREA | 3D | 2D | ZE");
        log_cmd("AI 智能指令  : OPT/AIOPT(联合费用优化) | DIAG/ANOMALY(异常体检) | CLEAN(修复粗差) | UNCERT(补测推荐)");
        log_cmd("实用分析指令 : SIGNIF(共形LoD) | THIN(抽稀) | DRAIN(排水) | SENSITIVITY(敏感性) | PARETO(多目标) | MULTIBENCH(多级台阶) | APPLY_BENCH | BENCH | FLEET");
        log_cmd("智能成果导出 : HTMLRPT(决策报告) | EXP_REC(补测点CSV) | EXP_AIDXF(AI图层) | EXP_BOQ(工程量报价清单CSV)");
        log_cmd("自然语言示例 : \"把标高设为50.8\" | \"优化总费用\" | \"检查测点异常\" | \"推荐10个补测点\" | \"放坡 1:1.2 1:1.5\"");
        log_cmd("视图与编辑   : DARK | LIGHT | SWAP | UNDO | REDO | DAT | DXF | CLEAR (支持 Tab 自动补全)");
        break;
    case AssistantAction::AutoRectBoundary:
        generate_rect_boundary();
        break;
    case AssistantAction::AutoOutline:
        generate_auto_outline();
        break;
    case AssistantAction::ClearBoundary:
        clear_boundary();
        break;
    case AssistantAction::Calculate:
        run_calculation();
        break;
    case AssistantAction::BalanceH0:
        solve_balance_h0();
        break;
    case AssistantAction::BestFitPlane:
        solve_best_fit_plane_action();
        break;
    case AssistantAction::BalancePlane:
        solve_balance_plane_action();
        break;
    case AssistantAction::OptimizeGradeCost:
        if (intent.has_param1) {
            opt_constraints_.max_slope_x = intent.param1;
            opt_constraints_.max_slope_y = intent.param1;
        }
        run_grade_optimization_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::RunSections:
        if (intent.has_param1) section_spacing_ = intent.param1;
        if (intent.has_param2) section_azimuth_ = intent.param2;
        run_sections_action(section_spacing_, section_azimuth_);
        set_inspector_tab(3);
        break;
    case AssistantAction::ComputeStrata:
        compute_strata_action();
        set_inspector_tab(5);
        break;
    case AssistantAction::ComputeHaul:
        compute_haul_action();
        set_inspector_tab(5);
        break;
    case AssistantAction::ComputeSlope:
        if (intent.has_param1) slope_cut_ratio_ = std::max(0.1, intent.param1);
        if (intent.has_param2) slope_fill_ratio_ = std::max(0.1, intent.param2);
        compute_slope_action();
        set_inspector_tab(5);
        break;
    case AssistantAction::RunDiagnostics:
        run_anomaly_diagnosis_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::CleanAnomalies:
        clean_anomalies_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::RunUncertainty:
        recommend_count_ = intent.count_param;
        run_uncertainty_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::AnalyzeSignificance:
        run_significance_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::ThinPoints:
        run_thinning_action(intent.has_param1 ? intent.param1 : thin_tol_m_);
        set_inspector_tab(6);
        break;
    case AssistantAction::CheckDrainage:
        run_drainage_action(intent.has_param1 ? intent.param1 : drain_min_slope_);
        set_inspector_tab(6);
        break;
    case AssistantAction::RunSensitivity:
        run_sensitivity_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::RunPareto:
        run_pareto_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::OptimizeStepped:
        run_stepped_platform_action(intent.has_param1 ? intent.param1 : -1.0);
        set_inspector_tab(6);
        break;
    case AssistantAction::OptimizeMultiBench:
        run_multi_bench_action(intent.count_param);
        set_inspector_tab(6);
        break;
    case AssistantAction::ApplyMultiBench:
        apply_multi_bench_action();
        set_inspector_tab(6);
        break;
    case AssistantAction::EstimateFleet:
        run_fleet_estimate_action(intent.has_param1 ? intent.param1 : -1.0);
        set_inspector_tab(6);
        break;
    case AssistantAction::MeasureDist:
        set_tool_mode(CadToolMode::MeasureDist);
        break;
    case AssistantAction::MeasureArea:
        set_tool_mode(CadToolMode::MeasureArea);
        break;
    case AssistantAction::SetElevation:
        set_design_elevation(intent.param1);
        run_calculation();
        break;
    case AssistantAction::SetView3D:
        set_3d_view(true);
        break;
    case AssistantAction::SetView2D:
        set_3d_view(false);
        break;
    case AssistantAction::ToggleGrid:
        enable_grid_check(!project_.settings.grid_enabled, project_.settings.grid_spacing);
        run_calculation();
        break;
    case AssistantAction::ToggleContours:
        enable_contours(!project_.settings.contours_enabled, project_.settings.contour_interval);
        run_calculation();
        break;
    case AssistantAction::QuickProfile:
        generate_quick_profile(true);
        set_inspector_tab(3);
        break;
    case AssistantAction::ZoomExtents:
        zoom_extents();
        log_cmd("视图已全图居中 (ZE)");
        break;
    case AssistantAction::DarkMode:
        set_dark_mode(true);
        break;
    case AssistantAction::LightMode:
        set_dark_mode(false);
        break;
    case AssistantAction::SwapCompare:
        if (!project_.compare_points.empty()) {
            std::swap(project_.points, project_.compare_points);
            compare_invert_ = 1 - compare_invert_;
            update_point_stats();
            run_calculation();
        } else {
            log_cmd("提示: 当前未加载对比期测点");
        }
        break;
    case AssistantAction::ExportDat:
        action_export_cass_dat();
        break;
    case AssistantAction::ExportDxf:
        action_export_dxf();
        break;
    case AssistantAction::ExportHtmlReport:
        action_export_ai_report_html();
        break;
    case AssistantAction::ExportSurveyRecCsv:
        action_export_survey_rec_csv();
        break;
    case AssistantAction::ExportAiDxf:
        action_export_ai_dxf();
        break;
    case AssistantAction::ExportBoqCsv:
        action_export_boq_csv();
        break;
    case AssistantAction::Undo:
        undo();
        break;
    case AssistantAction::Redo:
        redo();
        break;
    case AssistantAction::ClearConsole:
        console_lines_.clear();
        log_cmd("命令行历史记录已清空");
        break;
    case AssistantAction::None:
        break;
    }
}

std::optional<std::string> CassApp::open_file_dialog(const wchar_t* title, const wchar_t* filter) const {
#ifdef _WIN32
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
#else
    (void)title;
    (void)filter;
#endif
    return std::nullopt;
}

std::optional<std::string> CassApp::save_file_dialog(const wchar_t* title, const wchar_t* filter,
                                                     const wchar_t* default_ext,
                                                     const std::string& default_name) const {
#ifdef _WIN32
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
#else
    (void)title;
    (void)filter;
    (void)default_ext;
    (void)default_name;
#endif
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

void CassApp::action_export_ai_report_html() {
    static const wchar_t filter[] = L"HTML 决策报告 (*.html)\0*.html\0\0";
    if (auto p = save_file_dialog(L"导出 AI 智能工程决策报告 (HTML)", filter, L"html", project_.project_name + "_AI决策报告.html")) {
        try {
            export_ai_report_html(
                project_,
                result_ ? &(*result_) : nullptr,
                grade_opt_result_ ? &(*grade_opt_result_) : nullptr,
                health_report_ ? &(*health_report_) : nullptr,
                uncertainty_report_ ? &(*uncertainty_report_) : nullptr,
                significance_report_ ? &(*significance_report_) : nullptr,
                drainage_report_ ? &(*drainage_report_) : nullptr,
                diagnosis_summary_ ? &(*diagnosis_summary_) : nullptr,
                *p,
                stepped_result_ ? &(*stepped_result_) : nullptr,
                fleet_result_ ? &(*fleet_result_) : nullptr,
                pareto_result_ ? &(*pareto_result_) : nullptr,
                multi_bench_result_ ? &(*multi_bench_result_) : nullptr);
            status_text_ = "已导出 HTML 智能决策报告: " + *p;
            log_cmd("命令: HTMLRPT -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出 HTML 报告失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_survey_rec_csv() {
    if (!uncertainty_report_ || uncertainty_report_->recommendations.empty()) {
        status_text_ = "请先执行不确定度评定与补测点推荐";
        return;
    }
    static const wchar_t filter[] = L"CSV 文件 (*.csv)\0*.csv\0\0";
    if (auto p = save_file_dialog(L"导出补测推荐点位 (CSV)", filter, L"csv", project_.project_name + "_补测点推荐.csv")) {
        try {
            export_survey_recommendations_csv(*uncertainty_report_, *p);
            status_text_ = "已导出补测推荐表: " + *p;
            log_cmd("命令: EXP_REC -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出补测点失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_ai_dxf() {
    static const wchar_t filter[] = L"AutoCAD R12 DXF (*.dxf)\0*.dxf\0\0";
    if (auto p = save_file_dialog(L"导出 AI 专题 DXF 图层", filter, L"dxf", project_.project_name + "_AI专题图层.dxf")) {
        try {
            export_ai_overlays_dxf(
                result_ ? &(*result_) : nullptr,
                health_report_ ? &(*health_report_) : nullptr,
                uncertainty_report_ ? &(*uncertainty_report_) : nullptr,
                haul_result_ ? &(*haul_result_) : nullptr,
                slope_result_ ? &(*slope_result_) : nullptr,
                *p);
            status_text_ = "已导出 AI 专题 DXF: " + *p;
            log_cmd("命令: EXP_AIDXF -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出 AI DXF 失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_boq_csv() {
    if (!result_) {
        if (!run_calculation()) return;
    }
    static const wchar_t filter[] = L"CSV 文件 (*.csv)\0*.csv\0\0";
    if (auto p = save_file_dialog(L"导出工程量清单报价表 (BOQ CSV)", filter, L"csv", project_.project_name + "_工程量报价清单.csv")) {
        try {
            export_boq_csv(
                project_,
                *result_,
                haul_result_ ? &(*haul_result_) : nullptr,
                cost_params_,
                slope_result_ ? &(*slope_result_) : nullptr,
                stepped_result_ ? &(*stepped_result_) : nullptr,
                fleet_result_ ? &(*fleet_result_) : nullptr,
                *p);
            status_text_ = "已导出工程量清单报价表: " + *p;
            log_cmd("命令: EXP_BOQ -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出工程量报价表失败: ") + e.what();
            log_cmd("错误: " + status_text_);
        }
    }
}

void CassApp::action_export_pareto_csv() {
    if (!pareto_result_ && !run_pareto_action()) return;
    static const wchar_t filter[] = L"CSV 文件 (*.csv)\0*.csv\0\0";
    if (auto p = save_file_dialog(L"导出 Pareto 多目标方案对比表 (CSV)", filter, L"csv", project_.project_name + "_Pareto方案.csv")) {
        try {
            export_pareto_csv(*pareto_result_, *p);
            status_text_ = "已导出 Pareto 多目标方案表: " + *p;
            log_cmd("命令: EXP_PARETO -> " + status_text_);
        } catch (const std::exception& e) {
            status_text_ = std::string("导出 Pareto 方案失败: ") + e.what();
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
#ifdef _WIN32
            if (hwnd_) PostMessageW(hwnd_, WM_CLOSE, 0, 0);
#endif
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
        ImGui::Separator();
        if (ImGui::MenuItem("导出 AI 智能决策报告 (HTML)...")) action_export_ai_report_html();
        if (ImGui::MenuItem("导出补测推荐点表 (CSV)...")) action_export_survey_rec_csv();
        if (ImGui::MenuItem("导出 AI 专题图层 (DXF)...")) action_export_ai_dxf();
        if (ImGui::MenuItem("导出工程量清单报价表 (BOQ CSV)...")) action_export_boq_csv();
        if (ImGui::MenuItem("导出多目标 Pareto 方案对比 (CSV)...")) action_export_pareto_csv();
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
    ImGui::SameLine();
    tool_btn("测距", CadToolMode::MeasureDist);
    ImGui::SameLine();
    tool_btn("测面积", CadToolMode::MeasureArea);

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
    if (colored_button("AI优化", rgba(0x6D, 0x28, 0xD9), rgba(0x7C, 0x3A, 0xED), rgba(0x5B, 0x21, 0xB6))) {
        run_ai_full_workflow_action();
    }
    ImGui::SameLine();
    if (ImGui::Button("ZE全图")) zoom_extents();
    ImGui::SameLine();
    if (show_3d_view_) {
        if (colored_button("2D平面", rgba(0x23, 0x5C, 0x9F), rgba(0x2E, 0x73, 0xC4), rgba(0x1C, 0x4B, 0x82))) {
            set_3d_view(false);
        }
    } else {
        if (colored_button("3D地形", rgba(0x4C, 0x2F, 0x7A), rgba(0x63, 0x3E, 0x9E), rgba(0x3B, 0x24, 0x60))) {
            set_3d_view(true);
        }
    }
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
    ImGui::SameLine(); ImGui::Checkbox("调配", &canvas_.layers.haul);
    ImGui::SameLine(); ImGui::Checkbox("放坡", &canvas_.layers.slope);
    ImGui::SameLine(); ImGui::Checkbox("异常点", &canvas_.layers.anomalies);
    ImGui::SameLine(); ImGui::Checkbox("补测点", &canvas_.layers.survey_recs);
    ImGui::SameLine(); ImGui::Checkbox("台阶", &canvas_.layers.stepped);
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
    const SiteHealthReport* health_ptr = health_report_ ? &(*health_report_) : nullptr;
    const UncertaintyReport* uncert_ptr = uncertainty_report_ ? &(*uncertainty_report_) : nullptr;
    const MultiBenchResult* mb_ptr = multi_bench_result_ ? &(*multi_bench_result_) : nullptr;
    if (show_3d_view_) {
        viewport_3d_.dark_mode = canvas_.dark_mode;
        viewport_3d_.render(project_, res_ptr, health_ptr, uncert_ptr, mb_ptr);
    } else {
        const HaulPlanResult* haul_ptr = haul_result_ ? &(*haul_result_) : nullptr;
        const SlopeGradingResult* slope_ptr = slope_result_ ? &(*slope_result_) : nullptr;
        const SteppedPlatformResult* stepped_ptr = stepped_result_ ? &(*stepped_result_) : nullptr;
        canvas_.render(project_, res_ptr, profile_line_, selected_tri_id_, callbacks,
                       haul_ptr, slope_ptr, health_ptr, uncert_ptr, stepped_ptr, mb_ptr);
    }

    ImGui::EndChild();
}

void CassApp::render_right_inspector(float width, float height) {
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x1B, 0x20, 0x28));
    ImGui::BeginChild("##right_inspector", ImVec2(width, height), ImGuiChildFlags_Borders);

    if (ImGui::BeginTabBar("##inspector_tabs", ImGuiTabBarFlags_None)) {
        auto tab_flags = [&](int idx) -> ImGuiTabItemFlags {
            return (pending_tab_ == idx) ? ImGuiTabItemFlags_SetSelected : ImGuiTabItemFlags_None;
        };

        if (ImGui::BeginTabItem("参数", nullptr, tab_flags(0))) {
            render_tab_params();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("测点", nullptr, tab_flags(1))) {
            render_tab_points();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("汇总", nullptr, tab_flags(2))) {
            render_tab_summary();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("断面", nullptr, tab_flags(3))) {
            render_tab_profile();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("边界", nullptr, tab_flags(4))) {
            render_tab_boundary();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("专题", nullptr, tab_flags(5))) {
            render_tab_engineering();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("AI智能", nullptr, tab_flags(6))) {
            render_tab_ai();
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

void CassApp::render_tab_engineering() {
    ImGui::BeginChild("##tab_eng_scroll", ImVec2(0, 0), ImGuiChildFlags_None);

    draw_section_bar("分层土方统计 (Strata)");
    ImGui::RadioButton("按绝对标高", &strata_mode_idx_, 0);
    ImGui::SameLine();
    ImGui::RadioButton("按开挖深度", &strata_mode_idx_, 1);
    ImGui::InputText("分界值(逗号分隔)", strata_bounds_buf_, sizeof(strata_bounds_buf_));
    if (colored_button("计算分层土方", rgba(0x2B, 0x4E, 0x7A), rgba(0x36, 0x62, 0x99), rgba(0x21, 0x3D, 0x60), ImVec2(-1, 0))) {
        compute_strata_action();
    }
    if (strata_result_) {
        for (const auto& l : strata_result_->layers) {
            ImGui::BulletText("%s: 挖 %s m³ (%.1f%%) | 填 %s m³ (%.1f%%)",
                              l.name.c_str(),
                              format_num(l.cut_volume, 1).c_str(), l.cut_ratio * 100.0,
                              format_num(l.fill_volume, 1).c_str(), l.fill_ratio * 100.0);
        }
    }

    ImGui::Spacing();
    draw_section_bar("土方调配优化 (Haul)");
    ImGui::SliderInt("横向分区数", &haul_cols_, 2, 8);
    ImGui::SliderInt("纵向分区数", &haul_rows_, 2, 8);
    if (colored_button("计算最优土方调配", rgba(0x16, 0x7A, 0x55), rgba(0x1D, 0x99, 0x6B), rgba(0x12, 0x63, 0x45), ImVec2(-1, 0))) {
        compute_haul_action();
    }
    if (haul_result_) {
        ImGui::Text("区内平衡 %s m³ | 跨区调配 %s m³",
                    format_num(haul_result_->internal_balanced, 1).c_str(),
                    format_num(haul_result_->cross_zone_hauled, 1).c_str());
        ImGui::Text("加权运距 %.2f m | 估算总费用 ¥%s",
                    haul_result_->avg_haul_distance,
                    format_num(haul_result_->estimated_cost, 0).c_str());
    }

    ImGui::Spacing();
    draw_section_bar("场地周边放坡设计 (Slope)");
    ImGui::InputDouble("挖方坡比 1:m", &slope_cut_ratio_, 0.1, 0.5, "%.2f");
    ImGui::InputDouble("填方坡比 1:m", &slope_fill_ratio_, 0.1, 0.5, "%.2f");
    if (colored_button("生成周边放坡与示坡线", rgba(0x4C, 0x2F, 0x7A), rgba(0x63, 0x3E, 0x9E), rgba(0x3B, 0x24, 0x60), ImVec2(-1, 0))) {
        compute_slope_action();
    }
    if (slope_result_) {
        ImGui::Text("周长 %.1f m | 最大坡宽 %.2f m | 边坡挖 %s / 填 %s m³",
                    slope_result_->perimeter, slope_result_->max_slope_width,
                    format_num(slope_result_->slope_cut_volume, 1).c_str(),
                    format_num(slope_result_->slope_fill_volume, 1).c_str());
    }

    ImGui::EndChild();
}

void CassApp::render_tab_ai() {
    ImGui::BeginChild("##tab_ai_scroll", ImVec2(0, 0), ImGuiChildFlags_None);

    if (colored_button("一键 AI 全流程诊断与优化",
                       rgba(0x6D, 0x28, 0xD9), rgba(0x7C, 0x3A, 0xED), rgba(0x5B, 0x21, 0xB6),
                       ImVec2(-1, 30.0f))) {
        run_ai_full_workflow_action();
    }

    if (health_report_) {
        float frac = static_cast<float>(health_report_->health_score) / 100.0f;
        ImVec4 bar_col = (health_report_->health_score >= 90) ? rgba(0x22, 0xC5, 0x5E)
                       : (health_report_->health_score >= 70) ? rgba(0xF5, 0x9E, 0x0B)
                                                              : rgba(0xEF, 0x44, 0x44);
        ImGui::PushStyleColor(ImGuiCol_PlotHistogram, bar_col);
        char hbuf[64];
        std::snprintf(hbuf, sizeof(hbuf), "场地数据健康分: %d / 100", health_report_->health_score);
        ImGui::ProgressBar(frac, ImVec2(-1, 18.0f), hbuf);
        ImGui::PopStyleColor();
    }

    ImGui::Spacing();
    draw_section_bar("1. 设计面-土方调配联合费用优化");
    ImGui::InputDouble("挖方单价(元/m³)", &cost_params_.cut_unit_cost, 1.0, 5.0, "%.1f");
    ImGui::InputDouble("填方单价(元/m³)", &cost_params_.fill_unit_cost, 1.0, 5.0, "%.1f");
    ImGui::InputDouble("运距单价(元/100m)", &cost_params_.haul_per_m3_100m, 0.5, 2.0, "%.1f");
    ImGui::InputDouble("外运单价(元/m³)", &cost_params_.export_unit_cost, 1.0, 5.0, "%.1f");
    ImGui::InputDouble("借土单价(元/m³)", &cost_params_.borrow_unit_cost, 1.0, 5.0, "%.1f");
    ImGui::InputDouble("最大坡度约束(%)", &opt_constraints_.max_slope_x, 0.5, 1.0, "%.1f");
    opt_constraints_.max_slope_y = opt_constraints_.max_slope_x;
    ImGui::InputDouble("最小排水坡(%)", &opt_constraints_.min_drain_slope_pct, 0.1, 0.5, "%.2f");

    if (colored_button("开始联合费用优化", rgba(0x16, 0x7A, 0x55), rgba(0x1D, 0x99, 0x6B), rgba(0x12, 0x63, 0x45), ImVec2(168, 0))) {
        run_grade_optimization_action();
    }
    ImGui::SameLine();
    if (ImGui::Button("多目标 Pareto", ImVec2(-1, 0))) {
        run_pareto_action();
    }
    if (grade_opt_result_) {
        const auto& g = *grade_opt_result_;
        ImGuiTableFlags tf = ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg | ImGuiTableFlags_SizingStretchProp;
        if (ImGui::BeginTable("##grade_cmp_tbl", 4, tf)) {
            ImGui::TableSetupColumn("方案", ImGuiTableColumnFlags_WidthStretch);
            ImGui::TableSetupColumn("总费用(元)", ImGuiTableColumnFlags_WidthFixed, 74.0f);
            ImGui::TableSetupColumn("坡度ix/iy%", ImGuiTableColumnFlags_WidthFixed, 72.0f);
            ImGui::TableSetupColumn("节费", ImGuiTableColumnFlags_WidthFixed, 46.0f);
            ImGui::TableHeadersRow();
            auto row = [&](const GradePlanSummary& s, const char* sav_str, bool hl) {
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0);
                if (hl) ImGui::TextColored(rgba(0x4A, 0xDE, 0x80), "%s", s.label.c_str());
                else ImGui::Text("%s", s.label.c_str());
                ImGui::TableSetColumnIndex(1); ImGui::Text("%s", format_num(s.total_cost, 0).c_str());
                ImGui::TableSetColumnIndex(2); ImGui::Text("%.2f/%.2f", s.plane.slope_x, s.plane.slope_y);
                ImGui::TableSetColumnIndex(3); ImGui::Text("%s", sav_str);
            };
            row(g.flat_balance, "--", false);
            row(g.best_fit, "--", false);
            char sbuf[24];
            std::snprintf(sbuf, sizeof(sbuf), "%.1f%%", g.savings_pct_vs_flat);
            row(g.optimized, sbuf, true);
            ImGui::EndTable();
        }
        if (colored_button("一键采纳 AI 最优斜面并重算", rgba(0x23, 0x5C, 0x9F), rgba(0x2E, 0x73, 0xC4), rgba(0x1C, 0x4B, 0x82), ImVec2(-1, 0))) {
            apply_optimized_plane_action();
        }
    }
    if (pareto_result_ && !pareto_result_->plans.empty()) {
        ImGui::TextWrapped("%s (点击行一键采纳)", pareto_result_->summary.c_str());
        ImGuiTableFlags ptf = ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg | ImGuiTableFlags_SizingStretchProp;
        if (ImGui::BeginTable("##pareto_tbl", 4, ptf)) {
            ImGui::TableSetupColumn("策略", ImGuiTableColumnFlags_WidthStretch);
            ImGui::TableSetupColumn("总费用", ImGuiTableColumnFlags_WidthFixed, 68.0f);
            ImGui::TableSetupColumn("挖填方m³", ImGuiTableColumnFlags_WidthFixed, 68.0f);
            ImGui::TableSetupColumn("坡度%", ImGuiTableColumnFlags_WidthFixed, 44.0f);
            ImGui::TableHeadersRow();
            for (size_t i = 0; i < pareto_result_->plans.size(); ++i) {
                const auto& p = pareto_result_->plans[i];
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0);
                std::string lbl = p.tag + "##par" + std::to_string(i);
                if (ImGui::Selectable(lbl.c_str(), false, ImGuiSelectableFlags_SpanAllColumns)) {
                    apply_pareto_plan_action(i);
                }
                ImGui::TableSetColumnIndex(1); ImGui::Text("%s", format_num(p.summary.total_cost, 0).c_str());
                ImGui::TableSetColumnIndex(2); ImGui::Text("%s", format_num(p.total_moved_volume, 0).c_str());
                ImGui::TableSetColumnIndex(3); ImGui::Text("%.2f", p.slope_pct);
            }
            ImGui::EndTable();
        }
        if (ImGui::Button("导出 Pareto 方案 CSV", ImVec2(-1, 0))) {
            action_export_pareto_csv();
        }
    }

    ImGui::Spacing();
    draw_section_bar("2. 测点空间异常诊断 (点击定位)");
    if (ImGui::Button("检测异常测点", ImVec2(148, 0))) run_anomaly_diagnosis_action();
    ImGui::SameLine();
    if (ImGui::Button("一键局部修复", ImVec2(-1, 0))) clean_anomalies_action();
    if (health_report_) {
        ImGui::TextWrapped("%s", health_report_->summary.c_str());
        if (!health_report_->anomalies.empty()) {
            ImGuiTableFlags tf = ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg |
                                 ImGuiTableFlags_ScrollY | ImGuiTableFlags_SizingStretchProp;
            if (ImGui::BeginTable("##anom_tbl", 3, tf, ImVec2(0, 96.0f))) {
                ImGui::TableSetupScrollFreeze(0, 1);
                ImGui::TableSetupColumn("点号", ImGuiTableColumnFlags_WidthFixed, 60.0f);
                ImGui::TableSetupColumn("残差(m)", ImGuiTableColumnFlags_WidthFixed, 60.0f);
                ImGui::TableSetupColumn("诊断说明", ImGuiTableColumnFlags_WidthStretch);
                ImGui::TableHeadersRow();
                for (int i = 0; i < static_cast<int>(health_report_->anomalies.size()); ++i) {
                    const auto& a = health_report_->anomalies[i];
                    ImGui::TableNextRow();
                    ImGui::TableSetColumnIndex(0);
                    std::string lbl = a.point_id + "##anom" + std::to_string(i);
                    if (ImGui::Selectable(lbl.c_str(), false, ImGuiSelectableFlags_SpanAllColumns)) {
                        canvas_.focus_point(a.x, a.y, 40.0);
                        status_text_ = "已定位异常点 " + a.point_id + ": " + a.reason;
                    }
                    ImGui::TableSetColumnIndex(1);
                    ImGui::TextColored(rgba(0xF8, 0x71, 0x71), "%+.2f", a.residual);
                    ImGui::TableSetColumnIndex(2);
                    ImGui::TextUnformatted(a.reason.c_str());
                }
                ImGui::EndTable();
            }
        }
    }

    ImGui::Spacing();
    draw_section_bar("3. 不确定度评定 & 补测点推荐");
    ImGui::InputDouble("测量中误差 σm(m)", &measure_sigma_, 0.01, 0.05, "%.3f");
    ImGui::SliderInt("推荐补测点数", &recommend_count_, 3, 20);
    if (ImGui::Button("评定不确定度并推荐补测点", ImVec2(-1, 0))) run_uncertainty_action();
    if (uncertainty_report_) {
        const auto& u = *uncertainty_report_;
        ImGui::Text("挖方 95%%区间: %s ± %s m³ (±%.1f%%)",
                    format_num(u.cut_corrected, 1).c_str(), format_num(u.cut_ci95, 1).c_str(), u.cut_rel_ci95_pct);
        ImGui::Text("填方 95%%区间: %s ± %s m³ (±%.1f%%)",
                    format_num(u.fill_corrected, 1).c_str(), format_num(u.fill_ci95, 1).c_str(), u.fill_rel_ci95_pct);
        if (!u.recommendations.empty()) {
            ImGuiTableFlags tf = ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg |
                                 ImGuiTableFlags_ScrollY | ImGuiTableFlags_SizingStretchProp;
            if (ImGui::BeginTable("##rec_tbl", 4, tf, ImVec2(0, 102.0f))) {
                ImGui::TableSetupScrollFreeze(0, 1);
                ImGui::TableSetupColumn("#", ImGuiTableColumnFlags_WidthFixed, 28.0f);
                ImGui::TableSetupColumn("X", ImGuiTableColumnFlags_WidthStretch);
                ImGui::TableSetupColumn("Y", ImGuiTableColumnFlags_WidthStretch);
                ImGui::TableSetupColumn("估Z", ImGuiTableColumnFlags_WidthFixed, 54.0f);
                ImGui::TableHeadersRow();
                for (const auto& r : u.recommendations) {
                    ImGui::TableNextRow();
                    ImGui::TableSetColumnIndex(0);
                    std::string lbl = "#" + std::to_string(r.rank);
                    if (ImGui::Selectable(lbl.c_str(), false, ImGuiSelectableFlags_SpanAllColumns)) {
                        canvas_.focus_point(r.x, r.y, 40.0);
                        status_text_ = "已定位建议补测 #" + std::to_string(r.rank);
                    }
                    ImGui::TableSetColumnIndex(1); ImGui::Text("%.1f", r.x);
                    ImGui::TableSetColumnIndex(2); ImGui::Text("%.1f", r.y);
                    ImGui::TableSetColumnIndex(3); ImGui::Text("%.2f", r.estimated_z);
                }
                ImGui::EndTable();
            }
        }
    }

    if (diagnosis_summary_ && !diagnosis_summary_->items.empty()) {
        ImGui::Spacing();
        draw_section_bar("4. AI 工程综合建议清单");
        for (const auto& it : diagnosis_summary_->items) {
            ImVec4 col = (it.level == 1) ? rgba(0xF8, 0x71, 0x71)
                       : (it.level == 2) ? rgba(0xFB, 0xBF, 0x24)
                                         : rgba(0x4A, 0xDE, 0x80);
            ImGui::TextColored(col, "• [%s]", it.title.c_str());
            ImGui::TextWrapped("  %s", it.detail.c_str());
        }
    }

    ImGui::Spacing();
    draw_section_bar("5. 变化显著性 / 保精度抽稀 / 排水积水");
    if (ImGui::Button("显著性LoD", ImVec2(96, 0))) run_significance_action();
    ImGui::SameLine();
    if (ImGui::Button("保精度抽稀", ImVec2(96, 0))) run_thinning_action(thin_tol_m_);
    ImGui::SameLine();
    if (ImGui::Button("排水积水", ImVec2(-1, 0))) run_drainage_action(drain_min_slope_);

    ImGui::InputDouble("抽稀容差(m)", &thin_tol_m_, 0.02, 0.1, "%.2f");
    ImGui::InputDouble("最小排水坡(%)", &drain_min_slope_, 0.1, 0.5, "%.2f");

    if (significance_report_) {
        ImGui::TextWrapped("%s", significance_report_->summary.c_str());
    }
    if (thinning_result_) {
        ImGui::TextWrapped("%s", thinning_result_->summary.c_str());
        if (thinning_result_->retained_count < thinning_result_->original_count &&
            ImGui::Button("应用抽稀结果到当前工程 (可Ctrl+Z撤销)", ImVec2(-1, 0))) {
            undo_stack_.push_back(EditSnapshot{
                project_.boundary, project_.holes, project_.breaklines, project_.settings, project_.points
            });
            project_.points = thinning_result_->points;
            update_point_stats();
            if (result_) run_calculation();
        }
    }
    if (drainage_report_) {
        ImGui::TextWrapped("%s", drainage_report_->summary.c_str());
    }

    ImGui::Spacing();
    draw_section_bar("6. 智能决策报告与专题导出");
    if (ImGui::Button("导出 HTML 决策报告", ImVec2(148, 0))) action_export_ai_report_html();
    ImGui::SameLine();
    if (ImGui::Button("导出补测 CSV", ImVec2(-1, 0))) action_export_survey_rec_csv();
    if (ImGui::Button("导出 AI 专题 DXF 图层 (异常/补测/调配/放坡)", ImVec2(-1, 0))) {
        action_export_ai_dxf();
    }
    if (ImGui::Button("导出工程量报价表 CSV (BOQ)", ImVec2(-1, 0))) {
        action_export_boq_csv();
    }

    ImGui::Spacing();
    draw_section_bar("7. 标高-方量/综合费用敏感性曲线");
    if (ImGui::Button("生成标高敏感性曲线 (17档)", ImVec2(-1, 0))) {
        run_sensitivity_action();
    }
    if (sensitivity_result_ && sensitivity_result_->points.size() >= 2) {
        const auto& pts = sensitivity_result_->points;
        ImGui::TextWrapped("%s", sensitivity_result_->summary.c_str());

        ImVec2 avail = ImGui::GetContentRegionAvail();
        float cw = std::max(180.0f, avail.x);
        float ch = 128.0f;
        ImVec2 p0 = ImGui::GetCursorScreenPos();
        ImVec2 p1(p0.x + cw, p0.y + ch);
        ImDrawList* dl = ImGui::GetWindowDrawList();
        dl->AddRectFilled(p0, p1, IM_COL32(0x11, 0x15, 0x1C, 255), 3.0f);
        dl->AddRect(p0, p1, IM_COL32(0x33, 0x3D, 0x4E, 255), 3.0f);

        double max_vol = 1.0, max_cost = 1.0, min_cost = pts[0].total_cost;
        for (const auto& sp : pts) {
            max_vol = std::max({max_vol, sp.cut_volume, sp.fill_volume});
            max_cost = std::max(max_cost, sp.total_cost);
            min_cost = std::min(min_cost, sp.total_cost);
        }
        double cost_span = std::max(1.0, max_cost - min_cost);
        double h0 = pts.front().elevation;
        double h1 = std::max(h0 + 1e-6, pts.back().elevation);
        auto map_x = [&](double h) {
            return p0.x + 8.0f + static_cast<float>((h - h0) / (h1 - h0)) * (cw - 16.0f);
        };
        auto map_yv = [&](double v) {
            return p1.y - 10.0f - static_cast<float>(v / max_vol) * (ch - 28.0f);
        };
        auto map_yc = [&](double c) {
            return p1.y - 10.0f - static_cast<float>((c - min_cost) / cost_span) * (ch - 28.0f);
        };

        for (size_t i = 0; i + 1 < pts.size(); ++i) {
            float x0s = map_x(pts[i].elevation), x1s = map_x(pts[i + 1].elevation);
            dl->AddLine(ImVec2(x0s, map_yv(pts[i].cut_volume)), ImVec2(x1s, map_yv(pts[i + 1].cut_volume)),
                        IM_COL32(0xF8, 0x71, 0x71, 230), 1.8f);
            dl->AddLine(ImVec2(x0s, map_yv(pts[i].fill_volume)), ImVec2(x1s, map_yv(pts[i + 1].fill_volume)),
                        IM_COL32(0x38, 0xBD, 0xF8, 230), 1.8f);
            dl->AddLine(ImVec2(x0s, map_yc(pts[i].total_cost)), ImVec2(x1s, map_yc(pts[i + 1].total_cost)),
                        IM_COL32(0x4A, 0xDE, 0x80, 240), 2.2f);
        }
        float bx = map_x(sensitivity_result_->best_cost_elevation);
        dl->AddLine(ImVec2(bx, p0.y + 6), ImVec2(bx, p1.y - 6), IM_COL32(0xFA, 0xCC, 0x15, 200), 1.4f);
        dl->AddText(ImVec2(p0.x + 8, p0.y + 4), IM_COL32(0xF8, 0x71, 0x71, 240), "挖方");
        dl->AddText(ImVec2(p0.x + 42, p0.y + 4), IM_COL32(0x38, 0xBD, 0xF8, 240), "填方");
        dl->AddText(ImVec2(p0.x + 76, p0.y + 4), IM_COL32(0x4A, 0xDE, 0x80, 240), "总费用");
        ImGui::Dummy(ImVec2(cw, ch + 4.0f));
    }

    ImGui::Spacing();
    draw_section_bar("8. 多级台阶自适应优化 & 机械排班工期估算");
    ImGui::InputDouble("挡墙护坡(元/m2)", &wall_cost_per_m2_, 10.0, 50.0, "%.0f");
    ImGui::SliderInt("最大台阶级数", &multi_bench_max_k_, 2, 4);
    if (ImGui::Button("正交两级台阶", ImVec2(142, 0))) {
        run_stepped_platform_action();
    }
    ImGui::SameLine();
    if (ImGui::Button("主坡向多级台阶(DP)", ImVec2(-1, 0))) {
        run_multi_bench_action();
    }
    if (stepped_result_) {
        ImGui::TextWrapped("%s", stepped_result_->summary.c_str());
    }
    if (multi_bench_result_) {
        ImGui::TextWrapped("%s", multi_bench_result_->summary.c_str());
        if (multi_bench_result_->bench_count >= 2 &&
            colored_button("一键应用多级台阶到当前工程(可Ctrl+Z)", rgba(0x23, 0x5C, 0x9F), rgba(0x2E, 0x73, 0xC4), rgba(0x1C, 0x4B, 0x82), ImVec2(-1, 0))) {
            apply_multi_bench_action();
        }
    }

    ImGui::InputDouble("目标工期(天)", &fleet_params_.target_days, 1.0, 5.0, "%.0f");
    ImGui::InputDouble("自卸车速(km/h)", &fleet_params_.truck_speed_kmh, 2.0, 5.0, "%.0f");
    if (ImGui::Button("机械配置与工期估算", ImVec2(-1, 0))) {
        run_fleet_estimate_action();
    }
    if (fleet_result_) {
        ImGui::TextWrapped("%s", fleet_result_->summary.c_str());
    }

    ImGui::EndChild();
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
        std::function<void(const std::string&)> on_log;
    } hctx{&cmd_history_, &history_pos_, [this](const std::string& m) { log_cmd(m); }};

    if (fonts_.mono) ImGui::PushFont(fonts_.mono);
    ImGui::SetNextItemWidth(-1.0f);
    ImGuiInputTextFlags cflags = ImGuiInputTextFlags_EnterReturnsTrue |
                                 ImGuiInputTextFlags_CallbackHistory |
                                 ImGuiInputTextFlags_CallbackCompletion;
    if (ImGui::InputTextWithHint("##cad_cmd_input",
                                 "输入 CAD 指令或中文如 \"优化总费用\" / \"把标高设为50.5\" (Tab自动补全, HELP查看清单)...",
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
    if (health_report_) {
        right_ss << " | 健康:" << health_report_->health_score << "分";
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
