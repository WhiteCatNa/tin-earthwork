#pragma once

#include "imgui.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

namespace tin::ui {

struct ThemeFonts {
    ImFont* regular = nullptr;
    ImFont* kpi = nullptr;
    ImFont* mono = nullptr;
};

// 在 Windows 10/11 上设置深色沉浸标题栏（#181C22）
void configure_win32_dark_titlebar(HWND hwnd);

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
