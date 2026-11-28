#include "ui/viewport_3d.hpp"
#include "ui/theme.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

namespace tin::ui {

namespace {

constexpr float kPiF = 3.14159265358979323846f;

struct Vec3 {
    float x = 0.0f, y = 0.0f, z = 0.0f;
};

Vec3 operator-(const Vec3& a, const Vec3& b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }

Vec3 cross3(const Vec3& a, const Vec3& b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}

float dot3(const Vec3& a, const Vec3& b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

Vec3 normalize3(const Vec3& v) {
    float len = std::sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
    if (len < 1e-8f) return {0.0f, 0.0f, 1.0f};
    return {v.x / len, v.y / len, v.z / len};
}

ImU32 shade_rgb(int r, int g, int b, float light, int a = 255) {
    auto clamp8 = [](int v) -> unsigned char {
        return static_cast<unsigned char>(std::clamp(v, 0, 255));
    };
    return IM_COL32(
        clamp8(static_cast<int>(r * light)),
        clamp8(static_cast<int>(g * light)),
        clamp8(static_cast<int>(b * light)),
        clamp8(a));
}

void hypsometric_rgb(float t, int& r, int& g, int& b) {
    t = std::clamp(t, 0.0f, 1.0f);
    struct Stop { float t; int r, g, b; };
    static const Stop stops[] = {
        {0.00f, 0x22, 0x62, 0xA8},
        {0.25f, 0x26, 0xA6, 0x9A},
        {0.50f, 0x68, 0xB8, 0x5C},
        {0.75f, 0xE2, 0xB8, 0x45},
        {1.00f, 0xC9, 0x5B, 0x3C},
    };
    for (int i = 0; i < 4; ++i) {
        if (t <= stops[i + 1].t) {
            float u = (t - stops[i].t) / (stops[i + 1].t - stops[i].t);
            r = static_cast<int>(stops[i].r + u * (stops[i + 1].r - stops[i].r));
            g = static_cast<int>(stops[i].g + u * (stops[i + 1].g - stops[i].g));
            b = static_cast<int>(stops[i].b + u * (stops[i + 1].b - stops[i].b));
            return;
        }
    }
    r = 0xC9; g = 0x5B; b = 0x3C;
}

} // namespace

void Viewport3D::reset_isometric() {
    yaw_deg = -42.0f;
    pitch_deg = 34.0f;
    zoom = 1.0f;
    pan_px_ = {0.0f, 0.0f};
}

void Viewport3D::set_top_view() {
    yaw_deg = 0.0f;
    pitch_deg = 86.0f;
    zoom = 1.05f;
    pan_px_ = {0.0f, 0.0f};
}

void Viewport3D::set_side_view() {
    yaw_deg = -12.0f;
    pitch_deg = 14.0f;
    zoom = 1.08f;
    pan_px_ = {0.0f, 0.0f};
}

void Viewport3D::render(const ProjectData& project,
                        const CalculationResult* result,
                        const SiteHealthReport* health,
                        const UncertaintyReport* uncertainty,
                        const MultiBenchResult* multi_bench) {
    ImVec2 avail = ImGui::GetContentRegionAvail();
    if (avail.x < 40.0f || avail.y < 40.0f) return;

    // 顶部 3D 视口控制栏
    ImGui::PushStyleColor(ImGuiCol_ChildBg, rgba(0x16, 0x1C, 0x24));
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(6.0f, 2.0f));
    ImGui::BeginChild("##vp3d_bar", ImVec2(0.0f, 28.0f), ImGuiChildFlags_Borders,
                      ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    ImGui::AlignTextToFramePadding();
    ImGui::TextColored(rgba(0x7E, 0xB8, 0xF7), "3D地形:");
    ImGui::SameLine();
    int cm = static_cast<int>(color_mode);
    if (ImGui::RadioButton("挖填着色", &cm, 0)) color_mode = ColorMode3D::CutFill;
    ImGui::SameLine();
    if (ImGui::RadioButton("高程色谱", &cm, 1)) color_mode = ColorMode3D::Elevation;
    ImGui::SameLine();
    if (ImGui::RadioButton("线框", &cm, 2)) color_mode = ColorMode3D::Wireframe;
    ImGui::SameLine();
    if (ImGui::RadioButton("排水坡度", &cm, 3)) color_mode = ColorMode3D::SlopeDrainage;
    ImGui::SameLine();
    ImGui::Checkbox("网格线", &show_wireframe);
    ImGui::SameLine();
    ImGui::Checkbox("设计面", &show_design_plane);
    ImGui::SameLine();
    ImGui::SetNextItemWidth(92.0f);
    ImGui::SliderFloat("高程夸张", &z_exaggeration, 1.0f, 15.0f, "%.1fx");
    ImGui::SameLine();
    if (ImGui::SmallButton("等轴测")) reset_isometric();
    ImGui::SameLine();
    if (ImGui::SmallButton("俯视")) set_top_view();
    ImGui::SameLine();
    if (ImGui::SmallButton("侧视")) set_side_view();

    ImGui::EndChild();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();

    ImVec2 canvas_p0 = ImGui::GetCursorScreenPos();
    ImVec2 canvas_sz = ImGui::GetContentRegionAvail();
    if (canvas_sz.x < 40.0f || canvas_sz.y < 40.0f) return;
    ImVec2 canvas_p1{canvas_p0.x + canvas_sz.x, canvas_p0.y + canvas_sz.y};

    ImGui::InvisibleButton("##vp3d_canvas", canvas_sz,
                           ImGuiButtonFlags_MouseButtonLeft | ImGuiButtonFlags_MouseButtonRight | ImGuiButtonFlags_MouseButtonMiddle);
    const bool hovered = ImGui::IsItemHovered();
    const bool active = ImGui::IsItemActive();

    if (active && ImGui::IsMouseDragging(ImGuiMouseButton_Left)) {
        ImVec2 d = ImGui::GetIO().MouseDelta;
        yaw_deg += d.x * 0.45f;
        pitch_deg = std::clamp(pitch_deg + d.y * 0.35f, 4.0f, 89.0f);
    }
    if (active && (ImGui::IsMouseDragging(ImGuiMouseButton_Right) || ImGui::IsMouseDragging(ImGuiMouseButton_Middle))) {
        ImVec2 d = ImGui::GetIO().MouseDelta;
        pan_px_.x += d.x;
        pan_px_.y += d.y;
    }
    if (hovered) {
        float wheel = ImGui::GetIO().MouseWheel;
        if (std::abs(wheel) > 1e-4f) {
            zoom = std::clamp(zoom * std::pow(1.12f, wheel), 0.35f, 4.5f);
        }
        if (ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left)) {
            reset_isometric();
        }
    }

    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->PushClipRect(canvas_p0, canvas_p1, true);

    // 渐变背景（模拟专业 GIS/CAD 三维视口天空地平线）
    ImU32 bg_top = dark_mode ? IM_COL32(0x0A, 0x0E, 0x16, 255) : IM_COL32(0xE8, 0xF0, 0xFA, 255);
    ImU32 bg_bot = dark_mode ? IM_COL32(0x13, 0x1A, 0x26, 255) : IM_COL32(0xF8, 0xFA, 0xFC, 255);
    dl->AddRectFilledMultiColor(canvas_p0, canvas_p1, bg_top, bg_top, bg_bot, bg_bot);
    dl->AddRect(canvas_p0, canvas_p1, IM_COL32(0x35, 0x3D, 0x4B, 255));

    if (!result || result->triangles.empty()) {
        const char* msg = "请先执行 TIN 算量以生成三维实景地形网格";
        ImVec2 ts = ImGui::CalcTextSize(msg);
        dl->AddText(ImVec2(canvas_p0.x + (canvas_sz.x - ts.x) * 0.5f,
                           canvas_p0.y + (canvas_sz.y - ts.y) * 0.5f),
                    IM_COL32(0x94, 0xA3, 0xB8, 255), msg);
        dl->PopClipRect();
        return;
    }

    // 计算包围盒并归一化到 [-1, 1]
    double xmin = 1e30, xmax = -1e30, ymin = 1e30, ymax = -1e30;
    double zmin = 1e30, zmax = -1e30, dmin = 1e30, dmax = -1e30;
    for (const auto& t : result->triangles) {
        if (t.is_boundary) continue;
        for (const auto* p : {&t.p0, &t.p1, &t.p2}) {
            xmin = std::min(xmin, p->x); xmax = std::max(xmax, p->x);
            ymin = std::min(ymin, p->y); ymax = std::max(ymax, p->y);
            zmin = std::min(zmin, p->z); zmax = std::max(zmax, p->z);
            dmin = std::min(dmin, p->design_z); dmax = std::max(dmax, p->design_z);
        }
    }
    double cx = 0.5 * (xmin + xmax);
    double cy = 0.5 * (ymin + ymax);
    double cz = 0.5 * (std::min(zmin, dmin) + std::max(zmax, dmax));
    double xy_span = std::max({xmax - xmin, ymax - ymin, 1e-3});
    double z_span = std::max(zmax - zmin, 1e-3);
    float inv_xy = static_cast<float>(1.65 / xy_span);

    // 相机旋转矩阵（绕 Z 偏航 yaw，再绕 X 俯仰 pitch）
    float yaw = yaw_deg * kPiF / 180.0f;
    float pitch = pitch_deg * kPiF / 180.0f;
    float cyaw = std::cos(yaw), syaw = std::sin(yaw);
    float cpitch = std::cos(pitch), spitch = std::sin(pitch);

    float scale = std::min(canvas_sz.x, canvas_sz.y) * 0.44f * zoom;
    ImVec2 center{canvas_p0.x + canvas_sz.x * 0.5f + pan_px_.x,
                  canvas_p0.y + canvas_sz.y * 0.52f + pan_px_.y};

    auto to_norm = [&](double wx, double wy, double wz) -> Vec3 {
        return {
            static_cast<float>((wx - cx) * inv_xy),
            static_cast<float>((wy - cy) * inv_xy),
            static_cast<float>((wz - cz) * inv_xy * z_exaggeration)
        };
    };

    auto project_pt = [&](const Vec3& p, float& out_depth) -> ImVec2 {
        // 先绕 Z 轴旋转 yaw
        float x1 = p.x * cyaw - p.y * syaw;
        float y1 = p.x * syaw + p.y * cyaw;
        float z1 = p.z;
        // 再绕 X 轴俯仰 pitch（屏幕 +X 向右，+Y 向上，depth 向观察者为正）
        float y_cam = y1 * spitch + z1 * cpitch;
        float depth = -y1 * cpitch + z1 * spitch;
        // 轻量透视感（保留工程测量可读性）
        float persp = 3.6f / std::max(1.1f, 3.6f - depth * 0.45f);
        out_depth = depth;
        return ImVec2(center.x + x1 * scale * persp, center.y - y_cam * scale * persp);
    };

    Vec3 light_dir = normalize3({0.45f, -0.55f, 0.72f});

    struct DrawTri {
        ImVec2 s0, s1, s2;
        float depth = 0.0f;
        ImU32 fill_col = 0;
        ImU32 line_col = 0;
        bool is_design = false;
    };
    std::vector<DrawTri> draw_list;
    draw_list.reserve(result->triangles.size() + 128);

    // 1. 实测地形 TIN 三角面片
    for (const auto& t : result->triangles) {
        if (t.is_boundary || t.area <= 0.0) continue;
        Vec3 v0 = to_norm(t.p0.x, t.p0.y, t.p0.z);
        Vec3 v1 = to_norm(t.p1.x, t.p1.y, t.p1.z);
        Vec3 v2 = to_norm(t.p2.x, t.p2.y, t.p2.z);

        Vec3 n = normalize3(cross3(v1 - v0, v2 - v0));
        if (n.z < 0.0f) { n.x = -n.x; n.y = -n.y; n.z = -n.z; }
        float lambert = 0.46f + 0.54f * std::max(0.0f, dot3(n, light_dir));

        float d0, d1, d2;
        DrawTri dt;
        dt.s0 = project_pt(v0, d0);
        dt.s1 = project_pt(v1, d1);
        dt.s2 = project_pt(v2, d2);
        dt.depth = (d0 + d1 + d2) / 3.0f;

        if (color_mode == ColorMode3D::CutFill) {
            if (t.is_mixed) {
                dt.fill_col = shade_rgb(0xD9, 0x9B, 0x32, lambert, 240);
            } else if (t.cut_volume >= t.fill_volume) {
                dt.fill_col = shade_rgb(0xDC, 0x5B, 0x4E, lambert, 242);
            } else {
                dt.fill_col = shade_rgb(0x2D, 0x9D, 0xA8, lambert, 242);
            }
            dt.line_col = dark_mode ? IM_COL32(0x14, 0x1A, 0x24, 110) : IM_COL32(0x30, 0x3A, 0x48, 95);
        } else if (color_mode == ColorMode3D::Elevation) {
            float avg_z = static_cast<float>((t.p0.z + t.p1.z + t.p2.z) / 3.0);
            float frac = static_cast<float>((avg_z - zmin) / z_span);
            int r = 0, g = 0, b = 0;
            hypsometric_rgb(frac, r, g, b);
            dt.fill_col = shade_rgb(r, g, b, lambert, 245);
            dt.line_col = IM_COL32(0x10, 0x15, 0x1D, 85);
        } else if (color_mode == ColorMode3D::SlopeDrainage) {
            double denom = (t.p1.y - t.p2.y) * (t.p0.x - t.p2.x) +
                           (t.p2.x - t.p1.x) * (t.p0.y - t.p2.y);
            double slope_pct = 0.0;
            if (std::abs(denom) > 1e-18) {
                double dx1 = t.p1.x - t.p0.x, dy1 = t.p1.y - t.p0.y, dz1 = t.p1.z - t.p0.z;
                double dx2 = t.p2.x - t.p0.x, dy2 = t.p2.y - t.p0.y, dz2 = t.p2.z - t.p0.z;
                double gx = (dz1 * dy2 - dz2 * dy1) / denom;
                double gy = (dx1 * dz2 - dx2 * dz1) / denom;
                slope_pct = std::hypot(gx, gy) * 100.0;
            }
            if (slope_pct < 0.3) {
                dt.fill_col = shade_rgb(0x38, 0xBD, 0xF8, lambert, 245); // 平缓易积水区
            } else if (slope_pct < 5.0) {
                dt.fill_col = shade_rgb(0x22, 0xC5, 0x5E, lambert, 242); // 良好排水缓坡
            } else if (slope_pct < 15.0) {
                dt.fill_col = shade_rgb(0xF5, 0x9E, 0x0B, lambert, 242); // 中陡坡
            } else {
                dt.fill_col = shade_rgb(0xEF, 0x44, 0x44, lambert, 245); // 陡坎/冲刷风险区
            }
            dt.line_col = IM_COL32(0x10, 0x15, 0x1D, 90);
        } else {
            dt.fill_col = 0;
            dt.line_col = dark_mode ? IM_COL32(0x38, 0xE5, 0x8F, 185) : IM_COL32(0x0E, 0x7A, 0x54, 195);
        }
        draw_list.push_back(dt);
    }

    // 2. 半透明设计基准面（仅平场/斜面模式渲染平滑格网）
    if (show_design_plane && !result->is_compare) {
        const int gx = 10, gy = 10;
        auto design_z_at = [&](double x, double y) -> double {
            if (project.settings.design_mode == DesignMode::Plane) {
                return project.settings.design_plane.elevation(x, y);
            }
            return project.settings.design_elevation;
        };
        for (int iy = 0; iy < gy; ++iy) {
            double y0 = ymin + (ymax - ymin) * iy / gy;
            double y1 = ymin + (ymax - ymin) * (iy + 1) / gy;
            for (int ix = 0; ix < gx; ++ix) {
                double x0 = xmin + (xmax - xmin) * ix / gx;
                double x1 = xmin + (xmax - xmin) * (ix + 1) / gx;
                Vec3 p00 = to_norm(x0, y0, design_z_at(x0, y0));
                Vec3 p10 = to_norm(x1, y0, design_z_at(x1, y0));
                Vec3 p11 = to_norm(x1, y1, design_z_at(x1, y1));
                Vec3 p01 = to_norm(x0, y1, design_z_at(x0, y1));
                float d00, d10, d11, d01;
                ImVec2 s00 = project_pt(p00, d00);
                ImVec2 s10 = project_pt(p10, d10);
                ImVec2 s11 = project_pt(p11, d11);
                ImVec2 s01 = project_pt(p01, d01);

                ImU32 dfill = dark_mode ? IM_COL32(0x38, 0xBD, 0xF8, 48) : IM_COL32(0x02, 0x84, 0xC7, 45);
                ImU32 dline = dark_mode ? IM_COL32(0x7D, 0xD3, 0xFC, 130) : IM_COL32(0x03, 0x69, 0xA1, 140);
                draw_list.push_back(DrawTri{s00, s10, s11, (d00 + d10 + d11) / 3.0f, dfill, dline, true});
                draw_list.push_back(DrawTri{s00, s11, s01, (d00 + d11 + d01) / 3.0f, dfill, dline, true});
            }
        }
    }

    // 画家算法由远及近排序渲染
    std::sort(draw_list.begin(), draw_list.end(), [](const DrawTri& a, const DrawTri& b) {
        return a.depth < b.depth;
    });

    for (const auto& dt : draw_list) {
        if (dt.fill_col != 0) {
            dl->AddTriangleFilled(dt.s0, dt.s1, dt.s2, dt.fill_col);
        }
        if (dt.is_design) {
            dl->AddLine(dt.s0, dt.s1, dt.line_col, 1.0f);
            dl->AddLine(dt.s1, dt.s2, dt.line_col, 1.0f);
        } else if (show_wireframe || color_mode == ColorMode3D::Wireframe) {
            dl->AddTriangle(dt.s0, dt.s1, dt.s2, dt.line_col, 1.0f);
        }
    }

    // 3. 三维坐标包络轴框与高程标尺
    if (show_axes) {
        double zb = std::min(zmin, dmin);
        double zt = std::max(zmax, dmax);
        float dtmp = 0.0f;
        ImVec2 o = project_pt(to_norm(xmin, ymin, zb), dtmp);
        ImVec2 ax = project_pt(to_norm(xmax, ymin, zb), dtmp);
        ImVec2 ay = project_pt(to_norm(xmin, ymax, zb), dtmp);
        ImVec2 az = project_pt(to_norm(xmin, ymin, zt), dtmp);

        dl->AddLine(o, ax, IM_COL32(0xF8, 0x71, 0x71, 220), 1.8f);
        dl->AddLine(o, ay, IM_COL32(0x4A, 0xDE, 0x80, 220), 1.8f);
        dl->AddLine(o, az, IM_COL32(0x60, 0xA5, 0xFA, 220), 1.8f);
        dl->AddText(ImVec2(ax.x + 4, ax.y - 8), IM_COL32(0xF8, 0x71, 0x71, 240), "X(东)");
        dl->AddText(ImVec2(ay.x + 4, ay.y - 8), IM_COL32(0x4A, 0xDE, 0x80, 240), "Y(北)");
        char zbuf[48];
        std::snprintf(zbuf, sizeof(zbuf), "Z(%.1f~%.1fm)", zmin, zmax);
        dl->AddText(ImVec2(az.x + 4, az.y - 12), IM_COL32(0x60, 0xA5, 0xFA, 240), zbuf);
    }

    // 4. 三维异常点与自适应补测推荐竖向靶标针
    double pin_lift = std::max(0.5, z_span * 0.14);
    if (health && !health->anomalies.empty()) {
        ImU32 acol = IM_COL32(0xEF, 0x44, 0x44, 245);
        for (const auto& a : health->anomalies) {
            float d0 = 0.0f, d1 = 0.0f;
            ImVec2 base = project_pt(to_norm(a.x, a.y, a.z), d0);
            ImVec2 tip = project_pt(to_norm(a.x, a.y, a.z + pin_lift), d1);
            dl->AddLine(base, tip, acol, 2.0f);
            dl->AddCircleFilled(tip, 4.5f, acol);
            dl->AddText(ImVec2(tip.x + 6, tip.y - 7), acol, a.point_id.c_str());
        }
    }
    if (uncertainty && !uncertainty->recommendations.empty()) {
        ImU32 rcol = IM_COL32(0xE8, 0x79, 0xF9, 245);
        for (const auto& r : uncertainty->recommendations) {
            float d0 = 0.0f, d1 = 0.0f;
            ImVec2 base = project_pt(to_norm(r.x, r.y, r.estimated_z), d0);
            ImVec2 tip = project_pt(to_norm(r.x, r.y, r.estimated_z + pin_lift), d1);
            dl->AddLine(base, tip, rcol, 1.8f);
            dl->AddCircle(tip, 4.5f, rcol, 12, 1.8f);
            char rb[16];
            std::snprintf(rb, sizeof(rb), "#%d", r.rank);
            dl->AddText(ImVec2(tip.x + 6, tip.y - 7), rcol, rb);
        }
    }
    if (multi_bench && multi_bench->bench_count >= 2 && !multi_bench->dividers.empty()) {
        ImU32 scol = IM_COL32(0xFA, 0xCC, 0x15, 245);
        for (size_t i = 0; i < multi_bench->dividers.size(); ++i) {
            const auto& dv = multi_bench->dividers[i];
            double zref = 0.5 * (multi_bench->benches[i].elevation + multi_bench->benches[i + 1].elevation);
            float d0 = 0.0f, d1 = 0.0f;
            ImVec2 p0 = project_pt(to_norm(dv.start.x, dv.start.y, zref), d0);
            ImVec2 p1 = project_pt(to_norm(dv.end.x, dv.end.y, zref), d1);
            dl->AddLine(p0, p1, scol, 2.8f);
        }
    }

    // 5. 右上角高程/挖填/排水坡度三维色带图例
    {
        float lx = canvas_p1.x - 122.0f, ly = canvas_p0.y + 44.0f;
        dl->AddRectFilled(ImVec2(lx, ly), ImVec2(lx + 110.0f, ly + 74.0f),
                          dark_mode ? IM_COL32(0x12, 0x17, 0x20, 210) : IM_COL32(0xFF, 0xFF, 0xFF, 220), 4.0f);
        if (color_mode == ColorMode3D::Elevation) {
            for (int k = 0; k < 20; ++k) {
                float frac = 1.0f - static_cast<float>(k) / 19.0f;
                int rr = 0, gg = 0, bb = 0;
                hypsometric_rgb(frac, rr, gg, bb);
                dl->AddRectFilled(ImVec2(lx + 8, ly + 8 + k * 2.8f), ImVec2(lx + 22, ly + 11 + k * 2.8f),
                                  IM_COL32(rr, gg, bb, 255));
            }
            char bmax[24], bmin[24];
            std::snprintf(bmax, sizeof(bmax), "%.1fm", zmax);
            std::snprintf(bmin, sizeof(bmin), "%.1fm", zmin);
            ImU32 tc = dark_mode ? IM_COL32(0xE2, 0xE8, 0xF0, 255) : IM_COL32(0x0F, 0x17, 0x2A, 255);
            dl->AddText(ImVec2(lx + 28, ly + 6), tc, bmax);
            dl->AddText(ImVec2(lx + 28, ly + 50), tc, bmin);
        } else if (color_mode == ColorMode3D::SlopeDrainage) {
            ImU32 tc = dark_mode ? IM_COL32(0xE2, 0xE8, 0xF0, 255) : IM_COL32(0x0F, 0x17, 0x2A, 255);
            dl->AddRectFilled(ImVec2(lx + 8, ly + 7), ImVec2(lx + 20, ly + 17), IM_COL32(0x38, 0xBD, 0xF8, 255));
            dl->AddText(ImVec2(lx + 26, ly + 4), tc, "<0.3% 积水");
            dl->AddRectFilled(ImVec2(lx + 8, ly + 23), ImVec2(lx + 20, ly + 33), IM_COL32(0x22, 0xC5, 0x5E, 255));
            dl->AddText(ImVec2(lx + 26, ly + 20), tc, "0.3~5% 顺畅");
            dl->AddRectFilled(ImVec2(lx + 8, ly + 39), ImVec2(lx + 20, ly + 49), IM_COL32(0xF5, 0x9E, 0x0B, 255));
            dl->AddText(ImVec2(lx + 26, ly + 36), tc, "5~15% 较陡");
            dl->AddRectFilled(ImVec2(lx + 8, ly + 55), ImVec2(lx + 20, ly + 65), IM_COL32(0xEF, 0x44, 0x44, 255));
            dl->AddText(ImVec2(lx + 26, ly + 52), tc, ">15% 陡坎");
        } else {
            ImU32 tc = dark_mode ? IM_COL32(0xE2, 0xE8, 0xF0, 255) : IM_COL32(0x0F, 0x17, 0x2A, 255);
            dl->AddRectFilled(ImVec2(lx + 8, ly + 10), ImVec2(lx + 20, ly + 22), IM_COL32(0xDC, 0x5B, 0x4E, 255));
            dl->AddText(ImVec2(lx + 26, ly + 8), tc, "挖方(Cut)");
            dl->AddRectFilled(ImVec2(lx + 8, ly + 30), ImVec2(lx + 20, ly + 42), IM_COL32(0x2D, 0x9D, 0xA8, 255));
            dl->AddText(ImVec2(lx + 26, ly + 28), tc, "填方(Fill)");
            dl->AddRectFilled(ImVec2(lx + 8, ly + 50), ImVec2(lx + 20, ly + 62), IM_COL32(0xD9, 0x9B, 0x32, 255));
            dl->AddText(ImVec2(lx + 26, ly + 48), tc, "挖填混合");
        }
    }

    // 左下角操作提示胶囊
    char hud[128];
    std::snprintf(hud, sizeof(hud), "左键拖动旋转 | 滚轮缩放 | 右键平移 | 双击重置  [偏航 %.0f° 俯仰 %.0f° 夸张 %.1fx]",
                  yaw_deg, pitch_deg, z_exaggeration);
    ImVec2 hs = ImGui::CalcTextSize(hud);
    ImVec2 hp{canvas_p0.x + 10.0f, canvas_p1.y - hs.y - 12.0f};
    dl->AddRectFilled(ImVec2(hp.x - 6, hp.y - 3), ImVec2(hp.x + hs.x + 6, hp.y + hs.y + 3),
                      dark_mode ? IM_COL32(0x10, 0x15, 0x1E, 210) : IM_COL32(0xFF, 0xFF, 0xFF, 220), 3.0f);
    dl->AddText(hp, dark_mode ? IM_COL32(0xC4, 0xD1, 0xE2, 245) : IM_COL32(0x1E, 0x29, 0x3B, 245), hud);

    dl->PopClipRect();
}

} // namespace tin::ui
