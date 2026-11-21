#pragma once

#include "core/types.hpp"
#include "imgui.h"

namespace tin::ui {

enum class ColorMode3D {
    CutFill = 0,     // 挖填着色（红挖青填 + 光照）
    Elevation = 1,   // 测绘高程色谱（蓝绿黄棕 + 光照）
    Wireframe = 2    // 经典 CAD 绿线框三角网
};

// 原生 Direct3D 11 / ImDrawList 交互式 3D 地形与挖填透视视口
class Viewport3D {
public:
    ColorMode3D color_mode = ColorMode3D::CutFill;
    float yaw_deg = -42.0f;
    float pitch_deg = 34.0f;
    float zoom = 1.0f;
    float z_exaggeration = 6.0f;
    bool show_wireframe = true;
    bool show_design_plane = true;
    bool show_axes = true;
    bool dark_mode = true;

    void reset_isometric();
    void set_top_view();
    void set_side_view();

    void render(const ProjectData& project, const CalculationResult* result);

private:
    ImVec2 pan_px_{0.0f, 0.0f};
};

} // namespace tin::ui
