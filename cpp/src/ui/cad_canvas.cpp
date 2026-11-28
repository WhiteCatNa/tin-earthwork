#include "ui/cad_canvas.hpp"
#include "core/geometry.hpp"
#include "ui/theme.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <unordered_set>

namespace tin::ui {

namespace {

// 颜色映射：terrain 风格高程散点（蓝 -> 青绿 -> 黄 -> 褐白）
ImU32 elevation_ramp_color(double t, bool dark_mode) {
    t = std::clamp(t, 0.0, 1.0);
    unsigned char r = 0, g = 0, b = 0;
    if (t < 0.25) {
        double u = t / 0.25;
        r = static_cast<unsigned char>(50 + u * 10);
        g = static_cast<unsigned char>(120 + u * 90);
        b = static_cast<unsigned char>(235 - u * 45);
    } else if (t < 0.5) {
        double u = (t - 0.25) / 0.25;
        r = static_cast<unsigned char>(60 + u * 140);
        g = static_cast<unsigned char>(210 + u * 20);
        b = static_cast<unsigned char>(190 - u * 110);
    } else if (t < 0.75) {
        double u = (t - 0.5) / 0.25;
        r = static_cast<unsigned char>(200 + u * 35);
        g = static_cast<unsigned char>(230 - u * 75);
        b = static_cast<unsigned char>(80 - u * 20);
    } else {
        double u = (t - 0.75) / 0.25;
        r = static_cast<unsigned char>(235 + (dark_mode ? 20 : -30) * u);
        g = static_cast<unsigned char>(155 + (dark_mode ? 90 : -35) * u);
        b = static_cast<unsigned char>(60 + (dark_mode ? 180 : 20) * u);
    }
    return IM_COL32(r, g, b, 240);
}

// 简单耳切法支持任意凸/凹多边形填充（解决凹边界裁剪后单个多边形为凹的问题）
void draw_polygon_filled(ImDrawList* dl, std::vector<ImVec2> pts, ImU32 color) {
    if (pts.size() < 3) return;
    if (pts.size() == 3) {
        dl->AddTriangleFilled(pts[0], pts[1], pts[2], color);
        return;
    }
    // 计算屏幕坐标系有向面积
    double area = 0.0;
    for (size_t i = 0; i < pts.size(); ++i) {
        size_t j = (i + 1) % pts.size();
        area += static_cast<double>(pts[i].x) * pts[j].y - static_cast<double>(pts[j].x) * pts[i].y;
    }
    if (std::abs(area) < 0.05) return;
    if (area < 0.0) std::reverse(pts.begin(), pts.end());

    auto cross2 = [](const ImVec2& a, const ImVec2& b, const ImVec2& c) {
        return static_cast<double>(b.x - a.x) * (c.y - a.y) -
               static_cast<double>(b.y - a.y) * (c.x - a.x);
    };
    auto in_tri = [](const ImVec2& p, const ImVec2& a, const ImVec2& b, const ImVec2& c) {
        double c1 = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
        double c2 = (c.x - b.x) * (p.y - b.y) - (c.y - b.y) * (p.x - b.x);
        double c3 = (a.x - c.x) * (p.y - c.y) - (a.y - c.y) * (p.x - c.x);
        return c1 >= -1e-4 && c2 >= -1e-4 && c3 >= -1e-4;
    };

    int guard = static_cast<int>(pts.size() * pts.size()) + 10;
    while (pts.size() > 3 && guard-- > 0) {
        bool clipped = false;
        size_t n = pts.size();
        for (size_t i = 0; i < n; ++i) {
            size_t prev = (i + n - 1) % n;
            size_t next = (i + 1) % n;
            if (cross2(pts[prev], pts[i], pts[next]) <= 1e-4) continue;
            bool has_inside = false;
            for (size_t k = 0; k < n; ++k) {
                if (k == prev || k == i || k == next) continue;
                if (in_tri(pts[k], pts[prev], pts[i], pts[next])) {
                    has_inside = true;
                    break;
                }
            }
            if (!has_inside) {
                dl->AddTriangleFilled(pts[prev], pts[i], pts[next], color);
                pts.erase(pts.begin() + static_cast<ptrdiff_t>(i));
                clipped = true;
                break;
            }
        }
        if (!clipped) break;
    }
    if (pts.size() == 3) {
        dl->AddTriangleFilled(pts[0], pts[1], pts[2], color);
    }
}

void draw_dashed_line(ImDrawList* dl, ImVec2 a, ImVec2 b, ImU32 color, float thickness, float dash = 7.0f, float gap = 5.0f) {
    float dx = b.x - a.x, dy = b.y - a.y;
    float len = std::hypot(dx, dy);
    if (len < 1.0f) return;
    float ux = dx / len, uy = dy / len;
    float cur = 0.0f;
    while (cur < len) {
        float nxt = std::min(len, cur + dash);
        dl->AddLine(ImVec2(a.x + ux * cur, a.y + uy * cur),
                    ImVec2(a.x + ux * nxt, a.y + uy * nxt), color, thickness);
        cur += dash + gap;
    }
}

bool point_in_tri_xy(double x, double y, const Triangle& tri) {
    const auto &p0 = tri.p0, &p1 = tri.p1, &p2 = tri.p2;
    double d = (p1.y - p2.y) * (p0.x - p2.x) + (p2.x - p1.x) * (p0.y - p2.y);
    if (std::abs(d) < 1e-15) return false;
    double a = ((p1.y - p2.y) * (x - p2.x) + (p2.x - p1.x) * (y - p2.y)) / d;
    double b = ((p2.y - p0.y) * (x - p2.x) + (p0.x - p2.x) * (y - p2.y)) / d;
    double c = 1.0 - a - b;
    return a >= 0.0 && b >= 0.0 && c >= 0.0;
}

} // namespace

ImVec2 CadCanvas::world_to_screen(double wx, double wy, const ImVec2& origin, const ImVec2& size) const {
    float sx = origin.x + size.x * 0.5f + static_cast<float>((wx - center_x_) * px_per_m_);
    float sy = origin.y + size.y * 0.5f - static_cast<float>((wy - center_y_) * px_per_m_);
    return ImVec2(sx, sy);
}

XY CadCanvas::screen_to_world(const ImVec2& sp, const ImVec2& origin, const ImVec2& size) const {
    double wx = center_x_ + (sp.x - (origin.x + size.x * 0.5f)) / px_per_m_;
    double wy = center_y_ - (sp.y - (origin.y + size.y * 0.5f)) / px_per_m_;
    return XY{wx, wy};
}

double CadCanvas::nice_step(double target_world) const {
    if (target_world <= 0.0 || !std::isfinite(target_world)) return 10.0;
    double p = std::pow(10.0, std::floor(std::log10(target_world)));
    double norm = target_world / p;
    if (norm < 1.5) return 1.0 * p;
    if (norm < 3.5) return 2.0 * p;
    if (norm < 7.5) return 5.0 * p;
    return 10.0 * p;
}

void CadCanvas::fit_view(const std::vector<SurveyPoint>& points,
                         const std::vector<XY>& boundary,
                         const ImVec2& view_size) {
    double xmin = 1e30, xmax = -1e30, ymin = 1e30, ymax = -1e30;
    bool any = false;
    for (const auto& p : points) {
        xmin = std::min(xmin, p.x); xmax = std::max(xmax, p.x);
        ymin = std::min(ymin, p.y); ymax = std::max(ymax, p.y);
        any = true;
    }
    for (const auto& b : boundary) {
        xmin = std::min(xmin, b.x); xmax = std::max(xmax, b.x);
        ymin = std::min(ymin, b.y); ymax = std::max(ymax, b.y);
        any = true;
    }
    if (!any) {
        center_x_ = 50.0;
        center_y_ = 50.0;
        px_per_m_ = 4.0;
        view_initialized_ = true;
        return;
    }
    center_x_ = 0.5 * (xmin + xmax);
    center_y_ = 0.5 * (ymin + ymax);
    double dx = std::max(xmax - xmin, 1.0);
    double dy = std::max(ymax - ymin, 1.0);
    float avail_w = std::max(view_size.x - 100.0f, 120.0f);
    float avail_h = std::max(view_size.y - 80.0f, 120.0f);
    px_per_m_ = std::min(avail_w / dx, avail_h / dy) * 0.88;
    px_per_m_ = std::clamp(px_per_m_, 1e-4, 5000.0);
    view_initialized_ = true;
}

void CadCanvas::focus_triangle(const Triangle& tri) {
    center_x_ = (tri.p0.x + tri.p1.x + tri.p2.x) / 3.0;
    center_y_ = (tri.p0.y + tri.p1.y + tri.p2.y) / 3.0;
}

void CadCanvas::focus_point(double x, double y, double target_span_m) {
    center_x_ = x;
    center_y_ = y;
    px_per_m_ = std::max(px_per_m_, 600.0 / std::max(5.0, target_span_m));
    view_initialized_ = true;
}

std::optional<SurveyPoint> CadCanvas::find_snap_point(
    const ImVec2& mouse, const std::vector<SurveyPoint>& pts,
    const ImVec2& origin, const ImVec2& size, float radius_px) const {
    if (!osnap_enabled || pts.empty()) return std::nullopt;
    float best_d2 = radius_px * radius_px;
    std::optional<SurveyPoint> best;
    for (const auto& p : pts) {
        ImVec2 sp = world_to_screen(p.x, p.y, origin, size);
        float dx = sp.x - mouse.x, dy = sp.y - mouse.y;
        float d2 = dx * dx + dy * dy;
        if (d2 < best_d2) {
            best_d2 = d2;
            best = p;
        }
    }
    return best;
}

void CadCanvas::clear_draft() {
    draft_.clear();
    measure_pts_.clear();
    profile_first_.reset();
}

void CadCanvas::commit_draft(const CadCanvasCallbacks& callbacks) {
    if (draft_.empty()) return;
    if (callbacks.on_draft_committed) {
        callbacks.on_draft_committed(draft_, tool);
    }
    draft_.clear();
}

void CadCanvas::render(const ProjectData& project,
                       const CalculationResult* result,
                       const std::optional<Segment>& profile_line,
                       int& selected_tri_id,
                       const CadCanvasCallbacks& callbacks,
                       const HaulPlanResult* haul,
                       const SlopeGradingResult* slope,
                       const SiteHealthReport* health,
                       const UncertaintyReport* uncertainty,
                       const SteppedPlatformResult* stepped,
                       const MultiBenchResult* multi_bench) {

    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0.0f, 0.0f));
    ImGui::BeginChild("CadViewportHost", ImVec2(-1, -1), true,
                      ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::PopStyleVar();

    ImVec2 avail = ImGui::GetContentRegionAvail();
    if (avail.x < 40.0f || avail.y < 40.0f) {
        ImGui::EndChild();
        return;
    }

    if (!view_initialized_ && (!project.points.empty() || !project.boundary.empty())) {
        fit_view(project.points, project.boundary, avail);
    }

    ImVec2 canvas_p0 = ImGui::GetCursorScreenPos();
    ImVec2 canvas_p1 = ImVec2(canvas_p0.x + avail.x, canvas_p0.y + avail.y);

    // 预留左侧与顶部 CAD 坐标标尺条
    const float ruler_l = 56.0f;
    const float ruler_t = 22.0f;
    ImVec2 view_origin = ImVec2(canvas_p0.x + ruler_l, canvas_p0.y + ruler_t);
    ImVec2 view_size = ImVec2(std::max(20.0f, avail.x - ruler_l), std::max(20.0f, avail.y - ruler_t));
    ImVec2 view_end = ImVec2(view_origin.x + view_size.x, view_origin.y + view_size.y);

    ImGui::InvisibleButton("##cad_canvas_interact", avail,
                           ImGuiButtonFlags_MouseButtonLeft | ImGuiButtonFlags_MouseButtonRight |
                           ImGuiButtonFlags_MouseButtonMiddle);
    bool hovered = ImGui::IsItemHovered();
    ImGuiIO& io = ImGui::GetIO();
    ImVec2 mouse = io.MousePos;

    // 计算当前高亮/捕捉点与鼠标交互
    std::optional<SurveyPoint> snapped;
    if (hovered && mouse.x >= view_origin.x && mouse.y >= view_origin.y) {
        hover_valid_ = true;
        hover_world_ = screen_to_world(mouse, view_origin, view_size);
        snapped = find_snap_point(mouse, project.points, view_origin, view_size);
        if (snapped.has_value()) {
            hover_world_ = XY{snapped->x, snapped->y};
        }

        // 滚轮以鼠标位置为中心无级缩放
        if (io.MouseWheel != 0.0f) {
            XY before = screen_to_world(mouse, view_origin, view_size);
            double factor = std::pow(1.18, io.MouseWheel);
            px_per_m_ = std::clamp(px_per_m_ * factor, 1e-4, 5000.0);
            XY after = screen_to_world(mouse, view_origin, view_size);
            center_x_ += (before.x - after.x);
            center_y_ += (before.y - after.y);
        }

        // 双击左键在 Select 模式下复位视图
        if (tool == CadToolMode::Select && ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left)) {
            fit_view(project.points, project.boundary, view_size);
        }

        // 平移检测：中键直接平移；Select 模式下左键拖动平移；非绘图模式右键拖动平移
        bool start_pan = ImGui::IsMouseClicked(ImGuiMouseButton_Middle) ||
                         (tool == CadToolMode::Select && ImGui::IsMouseClicked(ImGuiMouseButton_Left)) ||
                         (tool == CadToolMode::Select && ImGui::IsMouseClicked(ImGuiMouseButton_Right));
        if (start_pan) {
            panning_ = true;
            pan_moved_ = false;
            pan_start_mouse_ = mouse;
            pan_start_cx_ = center_x_;
            pan_start_cy_ = center_y_;
        }
    } else {
        hover_valid_ = false;
    }

    if (panning_) {
        bool down = ImGui::IsMouseDown(ImGuiMouseButton_Middle) ||
                    ImGui::IsMouseDown(ImGuiMouseButton_Left) ||
                    ImGui::IsMouseDown(ImGuiMouseButton_Right);
        if (down) {
            float dx = mouse.x - pan_start_mouse_.x;
            float dy = mouse.y - pan_start_mouse_.y;
            if (std::hypot(dx, dy) > 4.0f) pan_moved_ = true;
            if (pan_moved_) {
                center_x_ = pan_start_cx_ - dx / px_per_m_;
                center_y_ = pan_start_cy_ + dy / px_per_m_;
            }
        } else {
            // 左键未拖动则算点击
            if (!pan_moved_ && tool == CadToolMode::Select && hovered && result != nullptr) {
                XY click_w = screen_to_world(mouse, view_origin, view_size);
                for (const auto& tri : result->triangles) {
                    if (tri.is_boundary) continue;
                    if (point_in_tri_xy(click_w.x, click_w.y, tri)) {
                        selected_tri_id = tri.id;
                        if (callbacks.on_triangle_selected) {
                            callbacks.on_triangle_selected(tri.id);
                        }
                        break;
                    }
                }
            }
            panning_ = false;
        }
    }

    // 绘图/剖面拾取/量测左右键事件
    if (hovered && mouse.x >= view_origin.x && mouse.y >= view_origin.y && tool != CadToolMode::Select) {
        if (ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
            XY pt = snapped.has_value() ? XY{snapped->x, snapped->y} : screen_to_world(mouse, view_origin, view_size);
            if (tool == CadToolMode::PickProfile) {
                if (!profile_first_.has_value()) {
                    profile_first_ = pt;
                    if (callbacks.on_status_hint) {
                        callbacks.on_status_hint("剖面拾取: 已定点 A，请左键点击终点 A'");
                    }
                } else {
                    XY a = *profile_first_;
                    profile_first_.reset();
                    if (std::hypot(pt.x - a.x, pt.y - a.y) > 1e-6 && callbacks.on_profile_picked) {
                        callbacks.on_profile_picked(a, pt);
                    }
                }
            } else if (tool == CadToolMode::MeasureDist || tool == CadToolMode::MeasureArea) {
                measure_pts_.push_back(pt);
                if (callbacks.on_status_hint) {
                    callbacks.on_status_hint(
                        (tool == CadToolMode::MeasureDist)
                            ? "测距模式: 左键续点，右键撤销/清空"
                            : "测面积模式: 左键加顶点（≥3点自动统计闭合面积与区域方量），右键清空");
                }
            } else {
                draft_.push_back(pt);
            }
        } else if (ImGui::IsMouseClicked(ImGuiMouseButton_Right)) {
            if (tool == CadToolMode::PickProfile) {
                profile_first_.reset();
            } else if (tool == CadToolMode::MeasureDist || tool == CadToolMode::MeasureArea) {
                if (!measure_pts_.empty()) measure_pts_.pop_back();
            } else if (!draft_.empty()) {
                draft_.pop_back();
            }
        }
    }

    ImDrawList* dl = ImGui::GetWindowDrawList();

    // 配色板：黑底模型空间 vs 白底打印图纸
    ImU32 bg_col = dark_mode ? col32(0x0E, 0x12, 0x17) : col32(0xF8, 0xFA, 0xFC);
    ImU32 ruler_bg = dark_mode ? col32(0x16, 0x1A, 0x20) : col32(0xE2, 0xE8, 0xF0);
    ImU32 ruler_txt = dark_mode ? col32(0x8B, 0x96, 0xA5) : col32(0x33, 0x41, 0x55);
    ImU32 grid_col = dark_mode ? col32(0x1E, 0x29, 0x38) : col32(0xCB, 0xD5, 0xE1);
    ImU32 tin_col = dark_mode ? col32(0x4C, 0x6B, 0x84, 150) : col32(0x94, 0xA3, 0xB8, 170);
    ImU32 cut_fill_col = dark_mode ? col32(0xF0, 0x5A, 0x50, 130) : col32(0xDC, 0x38, 0x30, 155);
    ImU32 fill_fill_col = dark_mode ? col32(0x26, 0xB5, 0xA8, 130) : col32(0x0D, 0x94, 0x88, 155);
    ImU32 zero_col = dark_mode ? col32(0xF5, 0xB8, 0x2E, 255) : col32(0xD9, 0x77, 0x06, 255);
    ImU32 c_minor_col = dark_mode ? col32(0x9C, 0x72, 0x35, 195) : col32(0xB4, 0x7B, 0x2B, 175);
    ImU32 c_major_col = dark_mode ? col32(0xE8, 0xA8, 0x38, 240) : col32(0x85, 0x4D, 0x0E, 235);
    ImU32 grid_line_col = dark_mode ? col32(0x38, 0xBD, 0xF8, 175) : col32(0x02, 0x84, 0xC7, 175);
    ImU32 grid_txt_col = dark_mode ? col32(0xBA, 0xE6, 0xFD, 230) : col32(0x03, 0x69, 0xA1, 240);
    ImU32 bnd_col = dark_mode ? col32(0x38, 0xBD, 0xF8, 255) : col32(0x1E, 0x29, 0x3B, 255);
    ImU32 hole_col = col32(0xF0, 0x5A, 0x50, 235);
    ImU32 bl_col = dark_mode ? col32(0x81, 0x8C, 0xF8, 240) : col32(0x4F, 0x46, 0xE5, 240);
    ImU32 pt_label_col = dark_mode ? col32(0xFD, 0xE6, 0x8A, 225) : col32(0x0F, 0x17, 0x2A, 225);
    ImU32 prof_col = dark_mode ? col32(0xF4, 0x72, 0xB6, 255) : col32(0xDB, 0x27, 0x77, 255);
    ImU32 sel_col = col32(0xFF, 0x4D, 0x6D, 255);

    // 填充视口与标尺底色
    dl->AddRectFilled(view_origin, view_end, bg_col);
    dl->AddRectFilled(canvas_p0, ImVec2(canvas_p1.x, view_origin.y), ruler_bg);
    dl->AddRectFilled(canvas_p0, ImVec2(view_origin.x, canvas_p1.y), ruler_bg);

    // 裁剪主图形区
    dl->PushClipRect(view_origin, view_end, true);

    XY w_min = screen_to_world(ImVec2(view_origin.x, view_end.y), view_origin, view_size);
    XY w_max = screen_to_world(ImVec2(view_end.x, view_origin.y), view_origin, view_size);
    double gstep = nice_step(95.0 / px_per_m_);
    double gx0 = std::floor(w_min.x / gstep) * gstep;
    double gy0 = std::floor(w_min.y / gstep) * gstep;

    for (double x = gx0; x <= w_max.x + gstep * 0.5; x += gstep) {
        ImVec2 s0 = world_to_screen(x, w_min.y, view_origin, view_size);
        dl->AddLine(ImVec2(s0.x, view_origin.y), ImVec2(s0.x, view_end.y), grid_col, 1.0f);
    }
    for (double y = gy0; y <= w_max.y + gstep * 0.5; y += gstep) {
        ImVec2 s0 = world_to_screen(w_min.x, y, view_origin, view_size);
        dl->AddLine(ImVec2(view_origin.x, s0.y), ImVec2(view_end.x, s0.y), grid_col, 1.0f);
    }

    // 1. 挖填多边形填色
    if (result && layers.cut_fill) {
        for (const auto& tri : result->triangles) {
            if (tri.is_boundary) continue;
            if (!tri.cut_polygon.empty() || !tri.fill_polygon.empty()) {
                if (tri.cut_polygon.size() >= 3) {
                    std::vector<ImVec2> spts;
                    spts.reserve(tri.cut_polygon.size());
                    for (const auto& p : tri.cut_polygon) {
                        spts.push_back(world_to_screen(p.x, p.y, view_origin, view_size));
                    }
                    draw_polygon_filled(dl, std::move(spts), cut_fill_col);
                }
                if (tri.fill_polygon.size() >= 3) {
                    std::vector<ImVec2> spts;
                    spts.reserve(tri.fill_polygon.size());
                    for (const auto& p : tri.fill_polygon) {
                        spts.push_back(world_to_screen(p.x, p.y, view_origin, view_size));
                    }
                    draw_polygon_filled(dl, std::move(spts), fill_fill_col);
                }
            } else {
                ImVec2 a = world_to_screen(tri.p0.x, tri.p0.y, view_origin, view_size);
                ImVec2 b = world_to_screen(tri.p1.x, tri.p1.y, view_origin, view_size);
                ImVec2 c = world_to_screen(tri.p2.x, tri.p2.y, view_origin, view_size);
                dl->AddTriangleFilled(a, b, c, tri.volume >= 0.0 ? cut_fill_col : fill_fill_col);
            }
        }
    }

    // 1b. 挖空区底色遮罩
    if (layers.holes) {
        for (const auto& hole : project.holes) {
            if (hole.size() < 3) continue;
            std::vector<ImVec2> spts;
            spts.reserve(hole.size());
            for (const auto& p : hole) spts.push_back(world_to_screen(p.x, p.y, view_origin, view_size));
            draw_polygon_filled(dl, spts, bg_col);
            draw_polygon_filled(dl, spts, col32(0xF0, 0x5A, 0x50, 36));
        }
    }

    // 2. TIN 三角网
    if (result && layers.tin) {
        for (const auto& tri : result->triangles) {
            if (tri.is_boundary) continue;
            ImVec2 a = world_to_screen(tri.p0.x, tri.p0.y, view_origin, view_size);
            ImVec2 b = world_to_screen(tri.p1.x, tri.p1.y, view_origin, view_size);
            ImVec2 c = world_to_screen(tri.p2.x, tri.p2.y, view_origin, view_size);
            dl->AddTriangle(a, b, c, tin_col, 1.0f);
        }
    }

    // 3. 等高线 (DGX)
    if (result && layers.contours && result->contours.has_value()) {
        const auto& cs = *result->contours;
        for (const auto& seg : cs.minor_segments) {
            dl->AddLine(
                world_to_screen(seg.first.x, seg.first.y, view_origin, view_size),
                world_to_screen(seg.second.x, seg.second.y, view_origin, view_size),
                c_minor_col, 1.0f);
        }
        for (const auto& seg : cs.major_segments) {
            dl->AddLine(
                world_to_screen(seg.first.x, seg.first.y, view_origin, view_size),
                world_to_screen(seg.second.x, seg.second.y, view_origin, view_size),
                c_major_col, 1.7f);
        }
        // 计曲线中段标高注记（避免重叠）
        if (px_per_m_ > 1.2) {
            for (size_t i = 0; i < cs.lines.size(); ++i) {
                const auto& line = cs.lines[i];
                if (!line.major || line.polyline.size() < 4) continue;
                const XY& mid = line.polyline[line.polyline.size() / 2];
                ImVec2 mp = world_to_screen(mid.x, mid.y, view_origin, view_size);
                char buf[24];
                std::snprintf(buf, sizeof(buf), "%.1f", line.level);
                dl->AddText(ImVec2(mp.x + 2.0f, mp.y - 7.0f), c_major_col, buf);
            }
        }
    }

    // 4. 方格网 (FGW)
    if (result && layers.grid && result->grid_check.has_value()) {
        const auto& grid = *result->grid_check;
        float cell_px = static_cast<float>(grid.spacing * px_per_m_);
        for (const auto& cell : grid.cells) {
            ImVec2 p0 = world_to_screen(cell.x0, cell.y1, view_origin, view_size);
            ImVec2 p1 = world_to_screen(cell.x1, cell.y0, view_origin, view_size);
            dl->AddRect(p0, p1, grid_line_col, 0.0f, 0, 1.1f);
            if (cell_px >= 78.0f && grid.cells.size() <= 400) {
                char buf[48];
                std::snprintf(buf, sizeof(buf), "+%.0f/-%.0f", cell.cut, cell.fill);
                ImVec2 tsz = ImGui::CalcTextSize(buf);
                if (tsz.x < cell_px - 6.0f) {
                    ImVec2 tp(0.5f * (p0.x + p1.x - tsz.x), 0.5f * (p0.y + p1.y - tsz.y));
                    dl->AddRectFilled(
                        ImVec2(tp.x - 3.0f, tp.y - 1.0f),
                        ImVec2(tp.x + tsz.x + 3.0f, tp.y + tsz.y + 1.0f),
                        dark_mode ? col32(0x0B, 0x0F, 0x16, 175) : col32(0xFF, 0xFF, 0xFF, 195),
                        2.0f);
                    dl->AddText(tp, grid_txt_col, buf);
                }
            }
        }
        if (cell_px >= 86.0f && grid.corners.size() <= 600) {
            for (const auto& corner : grid.corners) {
                if (!corner.valid() || corner.extrapolated) continue;
                ImVec2 cp = world_to_screen(corner.x, corner.y, view_origin, view_size);
                char buf[24];
                std::snprintf(buf, sizeof(buf), "%+.2f", corner.height());
                ImU32 hcol = corner.height() >= 0.0 ? col32(0xFF, 0x9C, 0x94, 220) : col32(0x6E, 0xE7, 0xDC, 220);
                dl->AddText(ImVec2(cp.x + 3.0f, cp.y - 13.0f), hcol, buf);
            }
        }
    }

    // 5. 零填挖线
    if (result && layers.zero_contour) {
        for (const auto& tri : result->triangles) {
            if (tri.is_boundary) continue;
            for (const auto& seg : tri.zero_segments) {
                dl->AddLine(
                    world_to_screen(seg.first.x, seg.first.y, view_origin, view_size),
                    world_to_screen(seg.second.x, seg.second.y, view_origin, view_size),
                    zero_col, 2.2f);
            }
        }
    }

    // 6. 测点散点与高程注记
    if (layers.points && !project.points.empty()) {
        double zmin = 1e30, zmax = -1e30;
        for (const auto& p : project.points) {
            zmin = std::min(zmin, p.z);
            zmax = std::max(zmax, p.z);
        }
        double zspan = std::max(zmax - zmin, 1e-6);

        // 空间网格抽稀，避免密集测点注记重叠成团
        std::unordered_set<long long> used_label_cells;
        for (const auto& p : project.points) {
            ImVec2 sp = world_to_screen(p.x, p.y, view_origin, view_size);
            if (sp.x < view_origin.x - 10.0f || sp.x > view_end.x + 10.0f ||
                sp.y < view_origin.y - 10.0f || sp.y > view_end.y + 10.0f) {
                continue;
            }
            double t = (p.z - zmin) / zspan;
            ImU32 pcol = elevation_ramp_color(t, dark_mode);
            dl->AddCircleFilled(sp, 3.2f, pcol);
            dl->AddCircle(sp, 3.4f, dark_mode ? col32(0x0E, 0x12, 0x17) : col32(0xFF, 0xFF, 0xFF), 8, 0.8f);

            if (layers.point_labels) {
                int lx = static_cast<int>(std::floor(sp.x / 68.0f));
                int ly = static_cast<int>(std::floor(sp.y / 22.0f));
                long long key = (static_cast<long long>(lx) << 32) ^ static_cast<unsigned int>(ly);
                if (used_label_cells.insert(key).second) {
                    char buf[32];
                    std::snprintf(buf, sizeof(buf), "%.2f", p.z);
                    dl->AddText(ImVec2(sp.x + 5.0f, sp.y - 7.0f), pt_label_col, buf);
                }
            }
        }
    }

    // 7. 特征线 (CDT)
    if (layers.breaklines) {
        for (const auto& bl : project.breaklines) {
            if (bl.points.size() < 2) continue;
            for (size_t i = 0; i + 1 < bl.points.size(); ++i) {
                ImVec2 a = world_to_screen(bl.points[i].x, bl.points[i].y, view_origin, view_size);
                ImVec2 b = world_to_screen(bl.points[i + 1].x, bl.points[i + 1].y, view_origin, view_size);
                dl->AddLine(a, b, bl_col, 2.0f);
            }
            if (bl.closed && bl.points.size() >= 3) {
                ImVec2 a = world_to_screen(bl.points.back().x, bl.points.back().y, view_origin, view_size);
                ImVec2 b = world_to_screen(bl.points.front().x, bl.points.front().y, view_origin, view_size);
                dl->AddLine(a, b, bl_col, 2.0f);
            }
            for (const auto& pt : bl.points) {
                ImVec2 sp = world_to_screen(pt.x, pt.y, view_origin, view_size);
                dl->AddQuadFilled(ImVec2(sp.x, sp.y - 4), ImVec2(sp.x + 4, sp.y),
                                  ImVec2(sp.x, sp.y + 4), ImVec2(sp.x - 4, sp.y), bl_col);
            }
        }
    }

    // 8. 挖空区边线
    if (layers.holes) {
        for (const auto& hole : project.holes) {
            if (hole.size() < 3) continue;
            for (size_t i = 0; i < hole.size(); ++i) {
                ImVec2 a = world_to_screen(hole[i].x, hole[i].y, view_origin, view_size);
                ImVec2 b = world_to_screen(hole[(i + 1) % hole.size()].x, hole[(i + 1) % hole.size()].y, view_origin, view_size);
                draw_dashed_line(dl, a, b, hole_col, 1.8f);
            }
        }
    }

    // 9. 计算边界 (JZX) + CAD 方形夹点
    if (layers.boundary && !project.boundary.empty()) {
        for (size_t i = 0; i + 1 < project.boundary.size(); ++i) {
            ImVec2 a = world_to_screen(project.boundary[i].x, project.boundary[i].y, view_origin, view_size);
            ImVec2 b = world_to_screen(project.boundary[i + 1].x, project.boundary[i + 1].y, view_origin, view_size);
            dl->AddLine(a, b, bnd_col, 2.2f);
        }
        if (project.boundary.size() >= 3) {
            ImVec2 a = world_to_screen(project.boundary.back().x, project.boundary.back().y, view_origin, view_size);
            ImVec2 b = world_to_screen(project.boundary.front().x, project.boundary.front().y, view_origin, view_size);
            dl->AddLine(a, b, bnd_col, 2.2f);
        }
        for (const auto& p : project.boundary) {
            ImVec2 sp = world_to_screen(p.x, p.y, view_origin, view_size);
            dl->AddRectFilled(ImVec2(sp.x - 3.5f, sp.y - 3.5f), ImVec2(sp.x + 3.5f, sp.y + 3.5f), col32(0xFF, 0xFF, 0xFF));
            dl->AddRect(ImVec2(sp.x - 3.5f, sp.y - 3.5f), ImVec2(sp.x + 3.5f, sp.y + 3.5f), bnd_col, 0.0f, 0, 1.5f);
        }
    }

    // 10a. 平行断面法桩号线
    if (result && layers.sections && result->sections.has_value()) {
        ImU32 sec_col = dark_mode ? col32(0xC0, 0x84, 0xFC, 195) : col32(0x7E, 0x22, 0xCE, 200);
        ImU32 badge_bg = dark_mode ? col32(0x0E, 0x13, 0x1C, 215) : col32(0xFF, 0xFF, 0xFF, 225);
        for (const auto& st : result->sections->stations) {
            ImVec2 sa = world_to_screen(st.start.x, st.start.y, view_origin, view_size);
            ImVec2 sb = world_to_screen(st.end.x, st.end.y, view_origin, view_size);
            draw_dashed_line(dl, sa, sb, sec_col, 1.3f, 6.0f, 4.0f);
            ImVec2 tsz = ImGui::CalcTextSize(st.name.c_str());
            ImVec2 tp(sa.x - tsz.x * 0.5f, sa.y + 6.0f);
            dl->AddRectFilled(ImVec2(tp.x - 3.0f, tp.y - 1.0f), ImVec2(tp.x + tsz.x + 3.0f, tp.y + tsz.y + 1.0f), badge_bg, 2.0f);
            dl->AddText(tp, sec_col, st.name.c_str());
        }
    }

    // 10b. 场地放坡坡脚/坡顶线与 CASS 示坡线
    if (layers.slope && slope && slope->daylight_ring.size() >= 3) {
        ImU32 sl_col = dark_mode ? col32(0x4A, 0xDE, 0x80, 225) : col32(0x15, 0x80, 0x3D, 230);
        for (size_t i = 0; i < slope->daylight_ring.size(); ++i) {
            const auto& a_w = slope->daylight_ring[i];
            const auto& b_w = slope->daylight_ring[(i + 1) % slope->daylight_ring.size()];
            dl->AddLine(world_to_screen(a_w.x, a_w.y, view_origin, view_size),
                        world_to_screen(b_w.x, b_w.y, view_origin, view_size), sl_col, 2.0f);
        }
        for (const auto& tk : slope->slope_ticks) {
            dl->AddLine(world_to_screen(tk.first.x, tk.first.y, view_origin, view_size),
                        world_to_screen(tk.second.x, tk.second.y, view_origin, view_size), sl_col, 1.2f);
        }
    }

    // 10c. 土方调配流向箭头与方量注记
    if (layers.haul && haul && !haul->flows.empty()) {
        ImU32 arr_col = dark_mode ? col32(0xFB, 0xBF, 0x24, 235) : col32(0xD9, 0x77, 0x06, 235);
        ImU32 tag_bg = dark_mode ? col32(0x12, 0x17, 0x20, 225) : col32(0xFF, 0xFB, 0xEB, 235);
        double max_vol = haul->flows.front().volume;
        size_t draw_cnt = std::min<size_t>(haul->flows.size(), 16);
        for (size_t i = 0; i < draw_cnt; ++i) {
            const auto& fl = haul->flows[i];
            ImVec2 p0 = world_to_screen(fl.from_xy.x, fl.from_xy.y, view_origin, view_size);
            ImVec2 p1 = world_to_screen(fl.to_xy.x, fl.to_xy.y, view_origin, view_size);
            float dx = p1.x - p0.x, dy = p1.y - p0.y;
            float len = std::hypot(dx, dy);
            if (len < 12.0f) continue;
            float thick = 1.8f + 2.6f * static_cast<float>(fl.volume / std::max(max_vol, 1.0));
            dl->AddLine(p0, p1, arr_col, thick);
            dl->AddCircleFilled(p0, 4.0f, col32(0xF8, 0x71, 0x71, 240));
            float ux = dx / len, uy = dy / len;
            ImVec2 tip = p1;
            ImVec2 left{tip.x - ux * 11.0f - uy * 5.0f, tip.y - uy * 11.0f + ux * 5.0f};
            ImVec2 right{tip.x - ux * 11.0f + uy * 5.0f, tip.y - uy * 11.0f - ux * 5.0f};
            dl->AddTriangleFilled(tip, left, right, arr_col);
            if (i < 8) {
                char buf[48];
                std::snprintf(buf, sizeof(buf), "%.0fm³/%.0fm", fl.volume, fl.distance);
                ImVec2 ts = ImGui::CalcTextSize(buf);
                ImVec2 mid{0.5f * (p0.x + p1.x) - ts.x * 0.5f, 0.5f * (p0.y + p1.y) - ts.y * 0.5f};
                dl->AddRectFilled(ImVec2(mid.x - 3, mid.y - 1), ImVec2(mid.x + ts.x + 3, mid.y + ts.y + 1), tag_bg, 2.0f);
                dl->AddRect(ImVec2(mid.x - 3, mid.y - 1), ImVec2(mid.x + ts.x + 3, mid.y + ts.y + 1), arr_col, 2.0f);
                dl->AddText(mid, arr_col, buf);
            }
        }
    }

    // 10. 剖面线 A——A'
    if (layers.profile && profile_line.has_value()) {
        ImVec2 a = world_to_screen(profile_line->first.x, profile_line->first.y, view_origin, view_size);
        ImVec2 b = world_to_screen(profile_line->second.x, profile_line->second.y, view_origin, view_size);
        draw_dashed_line(dl, a, b, prof_col, 2.4f, 10.0f, 5.0f);
        for (auto [pt, lbl] : {std::make_pair(a, "A"), std::make_pair(b, "A'")}) {
            dl->AddCircleFilled(pt, 5.0f, prof_col);
            dl->AddRectFilled(ImVec2(pt.x + 6, pt.y - 18), ImVec2(pt.x + 28, pt.y - 2), col32(0x18, 0x1C, 0x22, 230), 2.0f);
            dl->AddRect(ImVec2(pt.x + 6, pt.y - 18), ImVec2(pt.x + 28, pt.y - 2), prof_col, 2.0f);
            dl->AddText(ImVec2(pt.x + 10, pt.y - 17), col32(0xFF, 0xFF, 0xFF), lbl);
        }
    }

    // 10d. 空间异常测点醒目告警叠层
    if (layers.anomalies && health != nullptr && !health->anomalies.empty()) {
        ImU32 anom_col = col32(0xEF, 0x44, 0x44, 245);
        ImU32 warn_col = col32(0xF5, 0x9E, 0x0B, 240);
        for (const auto& a : health->anomalies) {
            ImVec2 sp = world_to_screen(a.x, a.y, view_origin, view_size);
            if (sp.x < view_origin.x - 16 || sp.x > view_end.x + 16 ||
                sp.y < view_origin.y - 16 || sp.y > view_end.y + 16) continue;
            ImU32 col = (a.severity >= 2) ? anom_col : warn_col;
            dl->AddCircle(sp, 9.0f, col, 16, 2.2f);
            dl->AddLine(ImVec2(sp.x - 5, sp.y - 5), ImVec2(sp.x + 5, sp.y + 5), col, 1.8f);
            dl->AddLine(ImVec2(sp.x - 5, sp.y + 5), ImVec2(sp.x + 5, sp.y - 5), col, 1.8f);
            dl->AddText(ImVec2(sp.x + 10.0f, sp.y - 12.0f), col, a.point_id.c_str());
            if (hovered && std::hypot(mouse.x - sp.x, mouse.y - sp.y) <= 12.0f) {
                ImGui::SetTooltip("异常点 %s\n实测 Z: %.2f m | 预测 Z: %.2f m\n残差: %+.2f m\n%s",
                                  a.point_id.c_str(), a.z, a.expected_z, a.residual, a.reason.c_str());
            }
        }
    }

    // 10e. 自适应补测推荐靶标叠层
    if (layers.survey_recs && uncertainty != nullptr && !uncertainty->recommendations.empty()) {
        ImU32 rec_col = dark_mode ? col32(0xE8, 0x79, 0xF9, 245) : col32(0x93, 0x33, 0xEA, 245);
        for (const auto& r : uncertainty->recommendations) {
            ImVec2 sp = world_to_screen(r.x, r.y, view_origin, view_size);
            if (sp.x < view_origin.x - 16 || sp.x > view_end.x + 16 ||
                sp.y < view_origin.y - 16 || sp.y > view_end.y + 16) continue;
            dl->AddCircle(sp, 8.0f, rec_col, 16, 1.8f);
            dl->AddLine(ImVec2(sp.x - 10, sp.y), ImVec2(sp.x + 10, sp.y), rec_col, 1.4f);
            dl->AddLine(ImVec2(sp.x, sp.y - 10), ImVec2(sp.x, sp.y + 10), rec_col, 1.4f);
            char rtag[16];
            std::snprintf(rtag, sizeof(rtag), "#%d", r.rank);
            dl->AddText(ImVec2(sp.x + 9.0f, sp.y + 4.0f), rec_col, rtag);
            if (hovered && std::hypot(mouse.x - sp.x, mouse.y - sp.y) <= 12.0f) {
                ImGui::SetTooltip("建议补测 #%d\nX: %.2f  Y: %.2f\n预估 Z: %.2f m\n%s",
                                  r.rank, r.x, r.y, r.estimated_z, r.reason.c_str());
            }
        }
    }

    // 10f. 台阶分界线及级差标高徽标（优先绘制主坡向 DP 多级台阶，其次兼容正交两级台阶）
    if (layers.stepped && multi_bench != nullptr && multi_bench->bench_count >= 2 && !multi_bench->dividers.empty()) {
        ImU32 step_col = dark_mode ? col32(0xFA, 0xCC, 0x15, 245) : col32(0xD9, 0x77, 0x06, 245);
        ImU32 badge_bg = dark_mode ? col32(0x14, 0x18, 0x1F, 225) : col32(0xFF, 0xFF, 0xFF, 235);
        for (const auto& dv : multi_bench->dividers) {
            ImVec2 a = world_to_screen(dv.start.x, dv.start.y, view_origin, view_size);
            ImVec2 b = world_to_screen(dv.end.x, dv.end.y, view_origin, view_size);
            draw_dashed_line(dl, a, b, step_col, 2.6f, 12.0f, 6.0f);
            ImVec2 mid_ab(0.5f * (a.x + b.x), 0.5f * (a.y + b.y));
            char dbuf[48];
            std::snprintf(dbuf, sizeof(dbuf), "ΔH=%.2fm", dv.step_height);
            ImVec2 tsd = ImGui::CalcTextSize(dbuf);
            dl->AddRectFilled(ImVec2(mid_ab.x - tsd.x * 0.5f - 4, mid_ab.y - 10),
                              ImVec2(mid_ab.x + tsd.x * 0.5f + 4, mid_ab.y + 8), badge_bg, 3.0f);
            dl->AddRect(ImVec2(mid_ab.x - tsd.x * 0.5f - 4, mid_ab.y - 10),
                        ImVec2(mid_ab.x + tsd.x * 0.5f + 4, mid_ab.y + 8), step_col, 3.0f);
            dl->AddText(ImVec2(mid_ab.x - tsd.x * 0.5f, mid_ab.y - 8), step_col, dbuf);
        }
        for (const auto& bz : multi_bench->benches) {
            double s_mid = 0.5 * (bz.s_min + bz.s_max);
            double wx = multi_bench->origin_x + s_mid * multi_bench->dir_x;
            double wy = multi_bench->origin_y + s_mid * multi_bench->dir_y;
            ImVec2 sp = world_to_screen(wx, wy, view_origin, view_size);
            char bbuf[48];
            std::snprintf(bbuf, sizeof(bbuf), "台%d H=%.2fm", bz.index, bz.elevation);
            ImVec2 tsb = ImGui::CalcTextSize(bbuf);
            dl->AddRectFilled(ImVec2(sp.x - tsb.x * 0.5f - 4, sp.y - 10),
                              ImVec2(sp.x + tsb.x * 0.5f + 4, sp.y + 8), badge_bg, 3.0f);
            dl->AddRect(ImVec2(sp.x - tsb.x * 0.5f - 4, sp.y - 10),
                        ImVec2(sp.x + tsb.x * 0.5f + 4, sp.y + 8), step_col, 3.0f);
            dl->AddText(ImVec2(sp.x - tsb.x * 0.5f, sp.y - 8), step_col, bbuf);
        }
    } else if (layers.stepped && stepped != nullptr && stepped->step_height > 1e-3 && !project.points.empty()) {
        double bx0 = project.points[0].x, bx1 = bx0;
        double by0 = project.points[0].y, by1 = by0;
        for (const auto& p : project.points) {
            bx0 = std::min(bx0, p.x); bx1 = std::max(bx1, p.x);
            by0 = std::min(by0, p.y); by1 = std::max(by1, p.y);
        }
        ImVec2 a{}, b{}, b1p{}, b2p{};
        if (stepped->split_axis == 0) {
            a = world_to_screen(stepped->split_coord, by0, view_origin, view_size);
            b = world_to_screen(stepped->split_coord, by1, view_origin, view_size);
            b1p = world_to_screen(0.5 * (bx0 + stepped->split_coord), 0.5 * (by0 + by1), view_origin, view_size);
            b2p = world_to_screen(0.5 * (stepped->split_coord + bx1), 0.5 * (by0 + by1), view_origin, view_size);
        } else {
            a = world_to_screen(bx0, stepped->split_coord, view_origin, view_size);
            b = world_to_screen(bx1, stepped->split_coord, view_origin, view_size);
            b1p = world_to_screen(0.5 * (bx0 + bx1), 0.5 * (by0 + stepped->split_coord), view_origin, view_size);
            b2p = world_to_screen(0.5 * (bx0 + bx1), 0.5 * (stepped->split_coord + by1), view_origin, view_size);
        }
        ImU32 step_col = dark_mode ? col32(0xFA, 0xCC, 0x15, 245) : col32(0xD9, 0x77, 0x06, 245);
        ImU32 badge_bg = dark_mode ? col32(0x14, 0x18, 0x1F, 225) : col32(0xFF, 0xFF, 0xFF, 235);
        draw_dashed_line(dl, a, b, step_col, 2.6f, 12.0f, 6.0f);

        char tbuf[64];
        std::snprintf(tbuf, sizeof(tbuf), "台阶1 H1=%.2fm", stepped->bench1_elevation);
        ImVec2 ts1 = ImGui::CalcTextSize(tbuf);
        dl->AddRectFilled(ImVec2(b1p.x - ts1.x * 0.5f - 4, b1p.y - 10), ImVec2(b1p.x + ts1.x * 0.5f + 4, b1p.y + 8), badge_bg, 3.0f);
        dl->AddRect(ImVec2(b1p.x - ts1.x * 0.5f - 4, b1p.y - 10), ImVec2(b1p.x + ts1.x * 0.5f + 4, b1p.y + 8), step_col, 3.0f);
        dl->AddText(ImVec2(b1p.x - ts1.x * 0.5f, b1p.y - 8), step_col, tbuf);

        std::snprintf(tbuf, sizeof(tbuf), "台阶2 H2=%.2fm", stepped->bench2_elevation);
        ImVec2 ts2 = ImGui::CalcTextSize(tbuf);
        dl->AddRectFilled(ImVec2(b2p.x - ts2.x * 0.5f - 4, b2p.y - 10), ImVec2(b2p.x + ts2.x * 0.5f + 4, b2p.y + 8), badge_bg, 3.0f);
        dl->AddRect(ImVec2(b2p.x - ts2.x * 0.5f - 4, b2p.y - 10), ImVec2(b2p.x + ts2.x * 0.5f + 4, b2p.y + 8), step_col, 3.0f);
        dl->AddText(ImVec2(b2p.x - ts2.x * 0.5f, b2p.y - 8), step_col, tbuf);

        ImVec2 mid_ab(0.5f * (a.x + b.x), 0.5f * (a.y + b.y));
        std::snprintf(tbuf, sizeof(tbuf), "台阶分界 ΔH=%.2fm", stepped->step_height);
        ImVec2 tsm = ImGui::CalcTextSize(tbuf);
        dl->AddRectFilled(ImVec2(mid_ab.x - tsm.x * 0.5f - 4, mid_ab.y - 10), ImVec2(mid_ab.x + tsm.x * 0.5f + 4, mid_ab.y + 8), badge_bg, 3.0f);
        dl->AddRect(ImVec2(mid_ab.x - tsm.x * 0.5f - 4, mid_ab.y - 10), ImVec2(mid_ab.x + tsm.x * 0.5f + 4, mid_ab.y + 8), step_col, 3.0f);
        dl->AddText(ImVec2(mid_ab.x - tsm.x * 0.5f, mid_ab.y - 8), step_col, tbuf);
    }

    // 11b. 交互式 CAD 测距 / 测面积叠层
    if (!measure_pts_.empty() && (tool == CadToolMode::MeasureDist || tool == CadToolMode::MeasureArea)) {
        ImU32 mcol = dark_mode ? col32(0xFA, 0xCC, 0x15, 245) : col32(0xB4, 0x53, 0x09, 245);
        std::vector<XY> pts = measure_pts_;
        if (hover_valid_) pts.push_back(hover_world_);

        if (tool == CadToolMode::MeasureArea && pts.size() >= 3) {
            std::vector<ImVec2> spts;
            spts.reserve(pts.size());
            for (const auto& q : pts) spts.push_back(world_to_screen(q.x, q.y, view_origin, view_size));
            draw_polygon_filled(dl, spts, dark_mode ? col32(0xFA, 0xCC, 0x15, 42) : col32(0xD9, 0x77, 0x06, 42));
        }

        double total_len = 0.0;
        for (size_t i = 0; i + 1 < pts.size(); ++i) {
            ImVec2 a = world_to_screen(pts[i].x, pts[i].y, view_origin, view_size);
            ImVec2 b = world_to_screen(pts[i + 1].x, pts[i + 1].y, view_origin, view_size);
            draw_dashed_line(dl, a, b, mcol, 2.0f, 6.0f, 4.0f);
            total_len += std::hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y);
        }
        if (tool == CadToolMode::MeasureArea && pts.size() >= 3) {
            ImVec2 a = world_to_screen(pts.back().x, pts.back().y, view_origin, view_size);
            ImVec2 b = world_to_screen(pts.front().x, pts.front().y, view_origin, view_size);
            draw_dashed_line(dl, a, b, mcol, 1.8f, 6.0f, 4.0f);
            total_len += std::hypot(pts.front().x - pts.back().x, pts.front().y - pts.back().y);
        }
        for (const auto& q : measure_pts_) {
            dl->AddCircleFilled(world_to_screen(q.x, q.y, view_origin, view_size), 4.0f, mcol);
        }

        auto sample_z_quick = [&](const XY& q, double& out_z) -> bool {
            if (result) {
                for (const auto& t : result->triangles) {
                    if (t.is_boundary) continue;
                    if (point_in_tri_xy(q.x, q.y, t)) {
                        double det = (t.p1.y - t.p2.y) * (t.p0.x - t.p2.x) + (t.p2.x - t.p1.x) * (t.p0.y - t.p2.y);
                        if (std::abs(det) > 1e-12) {
                            double l0 = ((t.p1.y - t.p2.y) * (q.x - t.p2.x) + (t.p2.x - t.p1.x) * (q.y - t.p2.y)) / det;
                            double l1 = ((t.p2.y - t.p0.y) * (q.x - t.p2.x) + (t.p0.x - t.p2.x) * (q.y - t.p2.y)) / det;
                            double l2 = 1.0 - l0 - l1;
                            out_z = l0 * t.p0.z + l1 * t.p1.z + l2 * t.p2.z;
                            return true;
                        }
                    }
                }
            }
            return false;
        };

        char mbuf[160]{};
        if (tool == CadToolMode::MeasureDist && pts.size() >= 2) {
            double za = 0.0, zb = 0.0;
            bool ok_a = sample_z_quick(pts.front(), za);
            bool ok_b = sample_z_quick(pts.back(), zb);
            double seg = std::hypot(pts.back().x - pts[pts.size() - 2].x, pts.back().y - pts[pts.size() - 2].y);
            if (ok_a && ok_b && total_len > 1e-6) {
                double dz = zb - za;
                double slope_pct = dz / total_len * 100.0;
                std::snprintf(mbuf, sizeof(mbuf), "总长 %.2f m (末段 %.2f m) | ΔZ=%+.2f m | 坡度 %+.2f%%",
                              total_len, seg, dz, slope_pct);
            } else {
                std::snprintf(mbuf, sizeof(mbuf), "水平总长 %.2f m (末段 %.2f m)", total_len, seg);
            }
        } else if (tool == CadToolMode::MeasureArea && pts.size() >= 3) {
            double area = std::abs(signed_area(pts));
            double local_cut = 0.0, local_fill = 0.0;
            if (result) {
                for (const auto& t : result->triangles) {
                    if (t.is_boundary) continue;
                    XY tc{(t.p0.x + t.p1.x + t.p2.x) / 3.0, (t.p0.y + t.p1.y + t.p2.y) / 3.0};
                    if (point_in_ring(tc, pts)) {
                        local_cut += t.cut_volume;
                        local_fill += t.fill_volume;
                    }
                }
            }
            std::snprintf(mbuf, sizeof(mbuf), "面积 %.1f m² (%.2f 亩) | 周长 %.1f m | 圈内挖 %.0f / 填 %.0f m³",
                          area, area / 666.6667, total_len, local_cut, local_fill);
        }
        if (mbuf[0] != '\0') {
            ImVec2 anchor = world_to_screen(pts.back().x, pts.back().y, view_origin, view_size);
            ImVec2 tsz = ImGui::CalcTextSize(mbuf);
            ImVec2 bx0(anchor.x + 12, anchor.y - 26);
            ImVec2 bx1(bx0.x + tsz.x + 12, bx0.y + tsz.y + 8);
            dl->AddRectFilled(bx0, bx1, col32(0x14, 0x18, 0x1F, 235), 3.0f);
            dl->AddRect(bx0, bx1, mcol, 3.0f, 0, 1.4f);
            dl->AddText(ImVec2(bx0.x + 6, bx0.y + 4), col32(0xFF, 0xFF, 0xFF), mbuf);
        }
    }

    // 11. 交互绘制草稿与橡皮筋预览
    if (!draft_.empty()) {
        ImU32 dcol = (tool == CadToolMode::DrawHole) ? hole_col : (tool == CadToolMode::DrawBreakline ? bl_col : bnd_col);
        for (size_t i = 0; i + 1 < draft_.size(); ++i) {
            dl->AddLine(world_to_screen(draft_[i].x, draft_[i].y, view_origin, view_size),
                        world_to_screen(draft_[i + 1].x, draft_[i + 1].y, view_origin, view_size), dcol, 2.0f);
        }
        if (hover_valid_) {
            ImVec2 last_s = world_to_screen(draft_.back().x, draft_.back().y, view_origin, view_size);
            ImVec2 cur_s = world_to_screen(hover_world_.x, hover_world_.y, view_origin, view_size);
            draw_dashed_line(dl, last_s, cur_s, col32(0xF5, 0xB8, 0x2E, 230), 1.5f);
            if (draft_.size() >= 2 && tool != CadToolMode::DrawBreakline) {
                ImVec2 first_s = world_to_screen(draft_.front().x, draft_.front().y, view_origin, view_size);
                draw_dashed_line(dl, cur_s, first_s, col32(0x38, 0xBD, 0xF8, 120), 1.0f);
            }
        }
        for (const auto& dp : draft_) {
            ImVec2 sp = world_to_screen(dp.x, dp.y, view_origin, view_size);
            dl->AddRectFilled(ImVec2(sp.x - 3.5f, sp.y - 3.5f), ImVec2(sp.x + 3.5f, sp.y + 3.5f), col32(0xFF, 0xFF, 0xFF));
            dl->AddRect(ImVec2(sp.x - 3.5f, sp.y - 3.5f), ImVec2(sp.x + 3.5f, sp.y + 3.5f), dcol, 0.0f, 0, 1.5f);
        }
    }
    if (tool == CadToolMode::PickProfile && profile_first_.has_value() && hover_valid_) {
        ImVec2 s0 = world_to_screen(profile_first_->x, profile_first_->y, view_origin, view_size);
        ImVec2 s1 = world_to_screen(hover_world_.x, hover_world_.y, view_origin, view_size);
        draw_dashed_line(dl, s0, s1, prof_col, 2.0f);
        dl->AddCircleFilled(s0, 5.0f, prof_col);
    }

    // 12. 选中三角形高亮 + 浮标
    if (result && selected_tri_id >= 0 && selected_tri_id < static_cast<int>(result->triangles.size())) {
        const auto& tri = result->triangles[selected_tri_id];
        if (!tri.is_boundary) {
            ImVec2 a = world_to_screen(tri.p0.x, tri.p0.y, view_origin, view_size);
            ImVec2 b = world_to_screen(tri.p1.x, tri.p1.y, view_origin, view_size);
            ImVec2 c = world_to_screen(tri.p2.x, tri.p2.y, view_origin, view_size);
            dl->AddTriangle(a, b, c, sel_col, 3.0f);

            ImVec2 cent((a.x + b.x + c.x) / 3.0f, (a.y + b.y + c.y) / 3.0f);
            char tip[128];
            std::snprintf(tip, sizeof(tip), "Δ%d  面积:%.1fm²\n挖:%.1f  填:%.1f",
                          tri.id, tri.area, tri.cut_volume, tri.fill_volume);
            ImVec2 tsz = ImGui::CalcTextSize(tip);
            ImVec2 bx0(cent.x - tsz.x * 0.5f - 6.0f, cent.y - tsz.y * 0.5f - 4.0f);
            ImVec2 bx1(cent.x + tsz.x * 0.5f + 6.0f, cent.y + tsz.y * 0.5f + 4.0f);
            dl->AddRectFilled(bx0, bx1, col32(0x14, 0x18, 0x1F, 235), 3.0f);
            dl->AddRect(bx0, bx1, sel_col, 3.0f, 0, 1.4f);
            dl->AddText(ImVec2(bx0.x + 6.0f, bx0.y + 4.0f), col32(0xFF, 0xFF, 0xFF), tip);
        }
    }

    // 13. 全屏 CAD 十字丝光标 + 拾取方框 + OSNAP 绿圆捕捉提示
    if (hover_valid_ && mouse.x >= view_origin.x && mouse.y >= view_origin.y) {
        ImU32 cross_col = dark_mode ? col32(0xCB, 0xD5, 0xE1, 95) : col32(0x1E, 0x29, 0x3B, 95);
        dl->AddLine(ImVec2(view_origin.x, mouse.y), ImVec2(view_end.x, mouse.y), cross_col, 1.0f);
        dl->AddLine(ImVec2(mouse.x, view_origin.y), ImVec2(mouse.x, view_end.y), cross_col, 1.0f);
        if (tool == CadToolMode::Select) {
            dl->AddRect(ImVec2(mouse.x - 5.0f, mouse.y - 5.0f), ImVec2(mouse.x + 5.0f, mouse.y + 5.0f),
                        dark_mode ? col32(0xE6, 0xED, 0xF3, 180) : col32(0x0F, 0x17, 0x2A, 180));
        }
        if (snapped.has_value()) {
            ImVec2 sp = world_to_screen(snapped->x, snapped->y, view_origin, view_size);
            dl->AddCircle(sp, 8.0f, col32(0x22, 0xC5, 0x5E, 255), 16, 2.0f);
            char sb[64];
            std::snprintf(sb, sizeof(sb), "%s (%.2f)", snapped->id.c_str(), snapped->z);
            dl->AddText(ImVec2(sp.x + 11.0f, sp.y - 8.0f), col32(0x4A, 0xDE, 0x80, 255), sb);
        }
    }

    // 14. 右上角紧凑图例浮窗 & 左下角动态比例尺
    {
        struct Leg { const char* name; ImU32 col; bool show; };
        Leg items[] = {
            {"挖方区", col32(0xF0, 0x5A, 0x50), layers.cut_fill && result != nullptr},
            {"填方区", col32(0x26, 0xB5, 0xA8), layers.cut_fill && result != nullptr},
            {"零填挖线", zero_col, layers.zero_contour && result != nullptr},
            {"等高线", c_major_col, layers.contours && result != nullptr},
            {"方格网", grid_line_col, layers.grid && result != nullptr && result->grid_check.has_value()},
            {"计算边界", bnd_col, layers.boundary && !project.boundary.empty()},
            {"异常测点", col32(0xEF, 0x44, 0x44), layers.anomalies && health != nullptr && !health->anomalies.empty()},
            {"补测推荐", col32(0xE8, 0x79, 0xF9), layers.survey_recs && uncertainty != nullptr && !uncertainty->recommendations.empty()},
            {"台阶分界", col32(0xFA, 0xCC, 0x15),
             layers.stepped && ((multi_bench != nullptr && multi_bench->bench_count >= 2) ||
                                (stepped != nullptr && stepped->step_height > 1e-3))},
        };
        int active_cnt = 0;
        for (const auto& it : items) if (it.show) ++active_cnt;
        if (active_cnt > 0) {
            float lw = 104.0f;
            float lh = 12.0f + active_cnt * 19.0f;
            ImVec2 lp0(view_end.x - lw - 10.0f, view_origin.y + 10.0f);
            ImVec2 lp1(view_end.x - 10.0f, view_origin.y + 10.0f + lh);
            dl->AddRectFilled(lp0, lp1, dark_mode ? col32(0x14, 0x18, 0x1F, 215) : col32(0xFF, 0xFF, 0xFF, 225), 3.0f);
            dl->AddRect(lp0, lp1, dark_mode ? col32(0x35, 0x3D, 0x4B) : col32(0xCB, 0xD5, 0xE1), 3.0f);
            float y = lp0.y + 7.0f;
            for (const auto& it : items) {
                if (!it.show) continue;
                dl->AddRectFilled(ImVec2(lp0.x + 8.0f, y + 3.0f), ImVec2(lp0.x + 20.0f, y + 13.0f), it.col, 1.0f);
                dl->AddText(ImVec2(lp0.x + 26.0f, y), dark_mode ? col32(0xE6, 0xED, 0xF3) : col32(0x0F, 0x17, 0x2A), it.name);
                y += 19.0f;
            }
        }

        // 左下角动态比例尺
        double bar_world = nice_step(85.0 / px_per_m_);
        float bar_px = static_cast<float>(bar_world * px_per_m_);
        if (bar_px >= 25.0f && bar_px <= 220.0f) {
            ImVec2 b0(view_origin.x + 14.0f, view_end.y - 18.0f);
            ImVec2 b1(b0.x + bar_px, b0.y);
            ImU32 scol = dark_mode ? col32(0xC2, 0xCB, 0xD6) : col32(0x33, 0x41, 0x55);
            dl->AddLine(b0, b1, scol, 2.0f);
            dl->AddLine(ImVec2(b0.x, b0.y - 5.0f), ImVec2(b0.x, b0.y + 3.0f), scol, 1.5f);
            dl->AddLine(ImVec2(b1.x, b1.y - 5.0f), ImVec2(b1.x, b1.y + 3.0f), scol, 1.5f);
            char sbuf[32];
            std::snprintf(sbuf, sizeof(sbuf), "%.4g m", bar_world);
            dl->AddText(ImVec2(b0.x, b0.y - 20.0f), scol, sbuf);
        }

        // 左上角 CAD 指北针 (North Arrow)
        {
            ImVec2 nc(view_origin.x + 26.0f, view_origin.y + 34.0f);
            ImU32 ncol = dark_mode ? col32(0x94, 0xA3, 0xB8, 220) : col32(0x33, 0x41, 0x55, 220);
            ImU32 nfill = dark_mode ? col32(0x38, 0xBD, 0xF8, 220) : col32(0x02, 0x84, 0xC7, 220);
            dl->AddCircle(nc, 14.0f, ncol, 20, 1.2f);
            dl->AddTriangleFilled(ImVec2(nc.x, nc.y - 13.0f),
                                  ImVec2(nc.x - 5.0f, nc.y + 4.0f),
                                  ImVec2(nc.x, nc.y), nfill);
            dl->AddTriangle(ImVec2(nc.x, nc.y - 13.0f),
                            ImVec2(nc.x, nc.y),
                            ImVec2(nc.x + 5.0f, nc.y + 4.0f), ncol, 1.2f);
            dl->AddText(ImVec2(nc.x - 4.0f, nc.y - 28.0f), nfill, "N");
        }

        // 右下角实时鼠标悬停工程遥测 HUD（地面高程 / 设计标高 / 挖填高差 / 局部坡度与坡向）
        if (hover_valid_ && result != nullptr && !result->triangles.empty()) {
            const double hx = hover_world_.x;
            const double hy = hover_world_.y;
            for (const auto& t : result->triangles) {
                if (t.is_boundary || t.area <= 1e-12) continue;
                double xmin_t = std::min({t.p0.x, t.p1.x, t.p2.x});
                double xmax_t = std::max({t.p0.x, t.p1.x, t.p2.x});
                if (hx < xmin_t - 1e-6 || hx > xmax_t + 1e-6) continue;
                double ymin_t = std::min({t.p0.y, t.p1.y, t.p2.y});
                double ymax_t = std::max({t.p0.y, t.p1.y, t.p2.y});
                if (hy < ymin_t - 1e-6 || hy > ymax_t + 1e-6) continue;

                double denom = (t.p1.y - t.p2.y) * (t.p0.x - t.p2.x) +
                               (t.p2.x - t.p1.x) * (t.p0.y - t.p2.y);
                if (std::abs(denom) <= 1e-18) continue;
                double u = ((t.p1.y - t.p2.y) * (hx - t.p2.x) + (t.p2.x - t.p1.x) * (hy - t.p2.y)) / denom;
                double v = ((t.p2.y - t.p0.y) * (hx - t.p2.x) + (t.p0.x - t.p2.x) * (hy - t.p2.y)) / denom;
                double w = 1.0 - u - v;
                if (u < -1e-4 || v < -1e-4 || w < -1e-4) continue;

                double nat_z = u * t.p0.z + v * t.p1.z + w * t.p2.z;
                double des_z = u * t.p0.design_z + v * t.p1.design_z + w * t.p2.design_z;
                double dz = nat_z - des_z;

                double dx1 = t.p1.x - t.p0.x, dy1 = t.p1.y - t.p0.y, dz1 = t.p1.z - t.p0.z;
                double dx2 = t.p2.x - t.p0.x, dy2 = t.p2.y - t.p0.y, dz2 = t.p2.z - t.p0.z;
                double gx = (dz1 * dy2 - dz2 * dy1) / denom;
                double gy = (dx1 * dz2 - dx2 * dz1) / denom;
                double slope_pct = std::hypot(gx, gy) * 100.0;
                double aspect = std::atan2(-gx, -gy) * (180.0 / 3.14159265358979323846);
                if (aspect < 0.0) aspect += 360.0;

                char hbuf[160];
                std::snprintf(hbuf, sizeof(hbuf),
                              "地面Z=%.2fm | 设计H=%.2fm | %s%.2fm | 坡度%.1f%% (%.0f°)",
                              nat_z, des_z, (dz >= 0.0 ? "挖+" : "填"), dz, slope_pct, aspect);
                ImVec2 ts = ImGui::CalcTextSize(hbuf);
                ImVec2 hp(view_end.x - ts.x - 18.0f, view_end.y - ts.y - 16.0f);
                ImU32 hbg = dark_mode ? col32(0x14, 0x18, 0x1F, 225) : col32(0xFF, 0xFF, 0xFF, 235);
                ImU32 hbd = (dz >= 0.0) ? col32(0xF8, 0x71, 0x71, 220) : col32(0x38, 0xBD, 0xF8, 220);
                dl->AddRectFilled(ImVec2(hp.x - 7, hp.y - 4), ImVec2(hp.x + ts.x + 7, hp.y + ts.y + 4), hbg, 3.0f);
                dl->AddRect(ImVec2(hp.x - 7, hp.y - 4), ImVec2(hp.x + ts.x + 7, hp.y + ts.y + 4), hbd, 3.0f);
                dl->AddText(hp, dark_mode ? col32(0xE6, 0xED, 0xF3) : col32(0x0F, 0x17, 0x2A), hbuf);
                break;
            }
        }
    }

    dl->PopClipRect();

    // 绘制顶部与左侧 CAD 标尺刻度
    dl->PushClipRect(canvas_p0, canvas_p1, true);
    dl->AddLine(ImVec2(view_origin.x, canvas_p0.y), ImVec2(view_origin.x, canvas_p1.y), col32(0x35, 0x3D, 0x4B));
    dl->AddLine(ImVec2(canvas_p0.x, view_origin.y), ImVec2(canvas_p1.x, view_origin.y), col32(0x35, 0x3D, 0x4B));
    dl->AddText(ImVec2(canvas_p0.x + 8.0f, canvas_p0.y + 3.0f), ruler_txt, "Y/X");

    for (double x = gx0; x <= w_max.x + gstep * 0.5; x += gstep) {
        ImVec2 s0 = world_to_screen(x, w_min.y, view_origin, view_size);
        if (s0.x < view_origin.x + 4.0f || s0.x > view_end.x - 28.0f) continue;
        char buf[24];
        std::snprintf(buf, sizeof(buf), "%.0f", x);
        dl->AddLine(ImVec2(s0.x, view_origin.y - 4.0f), ImVec2(s0.x, view_origin.y), ruler_txt);
        dl->AddText(ImVec2(s0.x + 3.0f, canvas_p0.y + 3.0f), ruler_txt, buf);
    }
    for (double y = gy0; y <= w_max.y + gstep * 0.5; y += gstep) {
        ImVec2 s0 = world_to_screen(w_min.x, y, view_origin, view_size);
        if (s0.y < view_origin.y + 4.0f || s0.y > view_end.y - 16.0f) continue;
        char buf[24];
        std::snprintf(buf, sizeof(buf), "%.0f", y);
        dl->AddLine(ImVec2(view_origin.x - 4.0f, s0.y), ImVec2(view_origin.x, s0.y), ruler_txt);
        dl->AddText(ImVec2(canvas_p0.x + 4.0f, s0.y - 7.0f), ruler_txt, buf);
    }
    dl->PopClipRect();

    ImGui::EndChild();
}

void render_profile_chart(const ProfileResult* profile, bool dark_mode, float height) {
    ImGui::BeginChild("ProfileChart", ImVec2(-1, height), true, ImGuiWindowFlags_NoScrollbar);
    ImVec2 p0 = ImGui::GetCursorScreenPos();
    ImVec2 sz = ImGui::GetContentRegionAvail();
    if (sz.x < 80.0f || sz.y < 80.0f) {
        ImGui::EndChild();
        return;
    }
    ImVec2 p1(p0.x + sz.x, p0.y + sz.y);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    ImU32 bg = dark_mode ? col32(0x0E, 0x12, 0x17) : col32(0xF8, 0xFA, 0xFC);
    ImU32 grid_c = dark_mode ? col32(0x22, 0x2E, 0x3D) : col32(0xCB, 0xD5, 0xE1);
    ImU32 txt_c = dark_mode ? col32(0x9A, 0xA8, 0xB8) : col32(0x33, 0x41, 0x55);

    dl->AddRectFilled(p0, p1, bg);
    if (!profile || profile->distances.empty()) {
        const char* hint = "点击上方「水平中线 / 垂直中线 / 对角线」或在图上拾取两点生成纵断面";
        ImVec2 tsz = ImGui::CalcTextSize(hint);
        dl->AddText(ImVec2(p0.x + (sz.x - tsz.x) * 0.5f, p0.y + (sz.y - tsz.y) * 0.5f), txt_c, hint);
        ImGui::EndChild();
        return;
    }

    double zmin = 1e30, zmax = -1e30;
    for (size_t i = 0; i < profile->distances.size(); ++i) {
        if (std::isfinite(profile->natural_z[i])) {
            zmin = std::min(zmin, profile->natural_z[i]);
            zmax = std::max(zmax, profile->natural_z[i]);
        }
        if (std::isfinite(profile->design_z[i])) {
            zmin = std::min(zmin, profile->design_z[i]);
            zmax = std::max(zmax, profile->design_z[i]);
        }
    }
    if (zmax < zmin) {
        dl->AddText(ImVec2(p0.x + 20.0f, p0.y + 20.0f), txt_c, "剖面线未穿过测量三角网区域");
        ImGui::EndChild();
        return;
    }
    double zpad = std::max((zmax - zmin) * 0.15, 0.5);
    zmin -= zpad;
    zmax += zpad;

    const float ml = 46.0f, mr = 14.0f, mt = 26.0f, mb = 24.0f;
    ImVec2 c0(p0.x + ml, p0.y + mt);
    ImVec2 c1(p1.x - mr, p1.y - mb);
    float cw = std::max(10.0f, c1.x - c0.x);
    float ch = std::max(10.0f, c1.y - c0.y);

    auto map_pt = [&](double d, double z) -> ImVec2 {
        float sx = c0.x + static_cast<float>(d / std::max(profile->length, 1e-6)) * cw;
        float sy = c1.y - static_cast<float>((z - zmin) / std::max(zmax - zmin, 1e-6)) * ch;
        return ImVec2(sx, sy);
    };

    dl->AddRect(c0, c1, grid_c);
    for (int g = 0; g <= 4; ++g) {
        float t = g / 4.0f;
        float gy = c1.y - t * ch;
        dl->AddLine(ImVec2(c0.x, gy), ImVec2(c1.x, gy), grid_c);
        char zb[24];
        std::snprintf(zb, sizeof(zb), "%.2f", zmin + t * (zmax - zmin));
        dl->AddText(ImVec2(p0.x + 4.0f, gy - 7.0f), txt_c, zb);

        float gx = c0.x + t * cw;
        dl->AddLine(ImVec2(gx, c0.y), ImVec2(gx, c1.y), grid_c);
        char db[24];
        std::snprintf(db, sizeof(db), "%.0fm", t * profile->length);
        dl->AddText(ImVec2(gx - 12.0f, c1.y + 4.0f), txt_c, db);
    }

    // 挖填区域填充
    for (size_t i = 0; i + 1 < profile->distances.size(); ++i) {
        double n0 = profile->natural_z[i], n1 = profile->natural_z[i + 1];
        double d0 = profile->design_z[i], d1 = profile->design_z[i + 1];
        if (!std::isfinite(n0) || !std::isfinite(n1) || !std::isfinite(d0) || !std::isfinite(d1)) continue;
        ImVec2 sn0 = map_pt(profile->distances[i], n0);
        ImVec2 sn1 = map_pt(profile->distances[i + 1], n1);
        ImVec2 sd0 = map_pt(profile->distances[i], d0);
        ImVec2 sd1 = map_pt(profile->distances[i + 1], d1);
        double mid_dz = 0.5 * ((n0 - d0) + (n1 - d1));
        ImU32 fcol = (mid_dz >= 0.0) ? col32(0xF0, 0x5A, 0x50, 90) : col32(0x26, 0xB5, 0xA8, 90);
        dl->AddQuadFilled(sn0, sn1, sd1, sd0, fcol);
    }

    // 原地面高程线 & 设计高程线
    ImU32 nat_col = col32(0x38, 0xBD, 0xF8, 255);
    ImU32 des_col = col32(0xF5, 0xB8, 0x2E, 255);
    for (size_t i = 0; i + 1 < profile->distances.size(); ++i) {
        if (std::isfinite(profile->natural_z[i]) && std::isfinite(profile->natural_z[i + 1])) {
            dl->AddLine(map_pt(profile->distances[i], profile->natural_z[i]),
                        map_pt(profile->distances[i + 1], profile->natural_z[i + 1]), nat_col, 2.0f);
        }
        if (std::isfinite(profile->design_z[i]) && std::isfinite(profile->design_z[i + 1])) {
            draw_dashed_line(dl, map_pt(profile->distances[i], profile->design_z[i]),
                             map_pt(profile->distances[i + 1], profile->design_z[i + 1]), des_col, 1.8f, 6.0f, 3.0f);
        }
    }

    // 顶部简洁图例
    dl->AddLine(ImVec2(c0.x + 4, p0.y + 12), ImVec2(c0.x + 24, p0.y + 12), nat_col, 2.0f);
    dl->AddText(ImVec2(c0.x + 28, p0.y + 4), txt_c, profile->is_compare ? "前期高程" : "原地面线");
    dl->AddLine(ImVec2(c0.x + 100, p0.y + 12), ImVec2(c0.x + 120, p0.y + 12), des_col, 2.0f);
    dl->AddText(ImVec2(c0.x + 124, p0.y + 4), txt_c, profile->is_compare ? "后期高程" : "设计高程");

    ImGui::EndChild();
}

} // namespace tin::ui
