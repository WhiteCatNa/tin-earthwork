#pragma once

#include "imgui.h"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace tin::ui {

#ifdef _WIN32
using NativeWindowHandle = HWND;
#else
using NativeWindowHandle = void*;
#endif

struct ThemeFonts {
    ImFont* regular = nullptr;
    ImFont* kpi = nullptr;
    ImFont* mono = nullptr;
};

// 在 Windows 10/11 上设置深色沉浸标题栏（#181C22）；非 Windows 平台为空操作
void configure_win32_dark_titlebar(NativeWindowHandle hwnd);

// 应用南方CASS深色模型/浅色出图风格（供初始化和模式切换复用）
void apply_cass_style(bool dark_mode = true, float dpi_scale = 1.0f);

// 计算 WCAG 2.1 相对亮度对比度（>= 4.5 满足正文 AA 级标准）
double contrast_ratio(const ImVec4& fg, const ImVec4& bg);

// 加载系统清晰中文字体与工程符号，配置南方CASS深色CAD样式
ThemeFonts setup_imgui_fonts_and_style(float dpi_scale = 1.0f);

// 工程语义色（ImU32 ABGR / ImVec4）
inline ImVec4 rgba(unsigned char r, unsigned char g, unsigned char b, unsigned char a = 255) {
    return ImVec4(r / 255.0f, g / 255.0f, b / 255.0f, a / 255.0f);
}

inline ImU32 col32(unsigned char r, unsigned char g, unsigned char b, unsigned char a = 255) {
    return IM_COL32(r, g, b, a);
}

} // namespace tin::ui
