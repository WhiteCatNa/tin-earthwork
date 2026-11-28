#include "ui/theme.hpp"
#ifdef _WIN32
#include <dwmapi.h>
#endif
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

namespace tin::ui {

void configure_win32_dark_titlebar(NativeWindowHandle hwnd) {
#ifdef _WIN32
    if (!hwnd) return;
    BOOL dark = TRUE;
    DwmSetWindowAttribute(hwnd, 20 /* DWMWA_USE_IMMERSIVE_DARK_MODE */, &dark, sizeof(dark));
    COLORREF caption = RGB(0x18, 0x1C, 0x22);
    DwmSetWindowAttribute(hwnd, 35 /* DWMWA_CAPTION_COLOR */, &caption, sizeof(caption));
#else
    (void)hwnd;
#endif
}

namespace {

bool file_exists(const char* path) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return false;
    std::fclose(f);
    return true;
}

double linearize_srgb(double c) {
    return (c <= 0.03928) ? (c / 12.92) : std::pow((c + 0.055) / 1.055, 2.4);
}

double relative_luminance(const ImVec4& col) {
    return 0.2126 * linearize_srgb(col.x) +
           0.7152 * linearize_srgb(col.y) +
           0.0722 * linearize_srgb(col.z);
}

} // namespace

double contrast_ratio(const ImVec4& fg, const ImVec4& bg) {
    double l1 = relative_luminance(fg);
    double l2 = relative_luminance(bg);
    if (l1 < l2) std::swap(l1, l2);
    return (l1 + 0.05) / (l2 + 0.05);
}

void apply_cass_style(bool dark_mode, float dpi_scale) {
    (void)dark_mode; // 工作区外壳保持南方CASS深灰工程工作站主题，画布在模型黑底/图纸白底间切换
    ImGuiStyle& st = ImGui::GetStyle();
    st.WindowRounding = 0.0f;
    st.ChildRounding = 2.0f;
    st.FrameRounding = 2.0f;
    st.PopupRounding = 2.0f;
    st.ScrollbarRounding = 2.0f;
    st.GrabRounding = 2.0f;
    st.TabRounding = 2.0f;

    st.WindowBorderSize = 1.0f;
    st.ChildBorderSize = 1.0f;
    st.PopupBorderSize = 1.0f;
    st.FrameBorderSize = 1.0f;
    st.TabBorderSize = 1.0f;

    st.WindowPadding = ImVec2(8.0f, 8.0f);
    st.FramePadding = ImVec2(8.0f, 4.0f);
    st.ItemSpacing = ImVec2(7.0f, 5.0f);
    st.ItemInnerSpacing = ImVec2(5.0f, 4.0f);
    st.ScrollbarSize = 11.0f;

    ImVec4* c = st.Colors;
    c[ImGuiCol_Text] = rgba(0xE6, 0xED, 0xF3);
    c[ImGuiCol_TextDisabled] = rgba(0x76, 0x83, 0x93);
    c[ImGuiCol_WindowBg] = rgba(0x1E, 0x22, 0x28);
    c[ImGuiCol_ChildBg] = rgba(0x23, 0x28, 0x31);
    c[ImGuiCol_PopupBg] = rgba(0x22, 0x27, 0x30, 0xFA);
    c[ImGuiCol_Border] = rgba(0x35, 0x3D, 0x4B);
    c[ImGuiCol_BorderShadow] = rgba(0, 0, 0, 0);

    c[ImGuiCol_FrameBg] = rgba(0x14, 0x18, 0x1F);
    c[ImGuiCol_FrameBgHovered] = rgba(0x1E, 0x25, 0x30);
    c[ImGuiCol_FrameBgActive] = rgba(0x24, 0x30, 0x42);

    c[ImGuiCol_TitleBg] = rgba(0x18, 0x1C, 0x22);
    c[ImGuiCol_TitleBgActive] = rgba(0x23, 0x2C, 0x3B);
    c[ImGuiCol_TitleBgCollapsed] = rgba(0x18, 0x1C, 0x22);
    c[ImGuiCol_MenuBarBg] = rgba(0x18, 0x1C, 0x22);

    c[ImGuiCol_ScrollbarBg] = rgba(0x16, 0x1A, 0x20);
    c[ImGuiCol_ScrollbarGrab] = rgba(0x3B, 0x44, 0x54);
    c[ImGuiCol_ScrollbarGrabHovered] = rgba(0x4E, 0x5A, 0x6E);
    c[ImGuiCol_ScrollbarGrabActive] = rgba(0x2F, 0x81, 0xF7);

    c[ImGuiCol_CheckMark] = rgba(0x60, 0xA5, 0xFA);
    c[ImGuiCol_SliderGrab] = rgba(0x2F, 0x81, 0xF7);
    c[ImGuiCol_SliderGrabActive] = rgba(0x58, 0xA6, 0xFF);

    c[ImGuiCol_Button] = rgba(0x2E, 0x35, 0x40);
    c[ImGuiCol_ButtonHovered] = rgba(0x3B, 0x45, 0x54);
    c[ImGuiCol_ButtonActive] = rgba(0x2F, 0x81, 0xF7);

    c[ImGuiCol_Header] = rgba(0x27, 0x34, 0x47);
    c[ImGuiCol_HeaderHovered] = rgba(0x31, 0x43, 0x5C);
    c[ImGuiCol_HeaderActive] = rgba(0x2F, 0x81, 0xF7);

    c[ImGuiCol_Separator] = rgba(0x35, 0x3D, 0x4B);
    c[ImGuiCol_SeparatorHovered] = rgba(0x2F, 0x81, 0xF7);
    c[ImGuiCol_SeparatorActive] = rgba(0x58, 0xA6, 0xFF);

    c[ImGuiCol_Tab] = rgba(0x18, 0x1C, 0x22);
    c[ImGuiCol_TabHovered] = rgba(0x2C, 0x35, 0x44);
    c[ImGuiCol_TabSelected] = rgba(0x28, 0x33, 0x45);
    c[ImGuiCol_TabSelectedOverline] = rgba(0x2F, 0x81, 0xF7);

    c[ImGuiCol_TableHeaderBg] = rgba(0x26, 0x2E, 0x3B);
    c[ImGuiCol_TableBorderStrong] = rgba(0x38, 0x41, 0x50);
    c[ImGuiCol_TableBorderLight] = rgba(0x2D, 0x34, 0x41);
    c[ImGuiCol_TableRowBg] = rgba(0x1C, 0x21, 0x28);
    c[ImGuiCol_TableRowBgAlt] = rgba(0x21, 0x27, 0x30);

    c[ImGuiCol_TextSelectedBg] = rgba(0x2F, 0x81, 0xF7, 0x88);
    c[ImGuiCol_ModalWindowDimBg] = rgba(0x0A, 0x0D, 0x12, 0xAA);

    if (dpi_scale != 1.0f) {
        st.ScaleAllSizes(dpi_scale);
    }
}

ThemeFonts setup_imgui_fonts_and_style(float dpi_scale) {
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr; // 每次启动保持整洁的南方CASS布局

    // 构建汉字全集 + 工程上标/符号字形表
    static ImVector<ImWchar> ranges;
    if (ranges.empty()) {
        ImFontGlyphRangesBuilder builder;
        builder.AddRanges(io.Fonts->GetGlyphRangesChineseFull());
        static const ImWchar extra_ranges[] = {
            0x00A0, 0x00FF, // ° ± ² ³ × ÷ 等
            0x0391, 0x03C9, // Δ Σ α β 等希腊字母
            0x2000, 0x26FF, // — • ✓ ✕ ▲ ▼ ● ○ □ ■ ★ 等几何符号
            0,
        };
        builder.AddRanges(extra_ranges);
        builder.BuildRanges(&ranges);
    }

    const char* ui_font_candidates[] = {
#ifdef _WIN32
        "C:\\Windows\\Fonts\\msyh.ttc",
        "C:\\Windows\\Fonts\\msyh.ttf",
        "C:\\Windows\\Fonts\\simhei.ttf",
        "C:\\Windows\\Fonts\\Deng.ttf",
        "C:\\Windows\\Fonts\\simsun.ttc",
#elif defined(__APPLE__)
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
#else
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
#endif
    };
    const char* chosen_ui = nullptr;
    for (const char* p : ui_font_candidates) {
        if (file_exists(p)) {
            chosen_ui = p;
            break;
        }
    }

    const char* mono_candidates[] = {
#ifdef _WIN32
        "C:\\Windows\\Fonts\\consola.ttf",
        "C:\\Windows\\Fonts\\simhei.ttf",
#elif defined(__APPLE__)
        "/System/Library/Fonts/Menlo.ttc",
#else
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
#endif
    };
    const char* chosen_mono = nullptr;
    for (const char* p : mono_candidates) {
        if (file_exists(p)) {
            chosen_mono = p;
            break;
        }
    }

    ThemeFonts fonts;
    float base_px = 16.0f * dpi_scale;
    float kpi_px = 21.0f * dpi_scale;

    if (chosen_ui) {
        fonts.regular = io.Fonts->AddFontFromFileTTF(chosen_ui, base_px, nullptr, ranges.Data);
        fonts.kpi = io.Fonts->AddFontFromFileTTF(chosen_ui, kpi_px, nullptr, ranges.Data);
    }
    if (!fonts.regular) {
        fonts.regular = io.Fonts->AddFontDefault();
        fonts.kpi = fonts.regular;
    }

    if (chosen_mono) {
        ImFontConfig cfg{};
        cfg.OversampleH = 2;
        fonts.mono = io.Fonts->AddFontFromFileTTF(
            chosen_mono, 15.0f * dpi_scale, &cfg, io.Fonts->GetGlyphRangesDefault());
        if (chosen_ui && fonts.mono) {
            ImFontConfig merge_cfg{};
            merge_cfg.MergeMode = true;
            merge_cfg.PixelSnapH = true;
            io.Fonts->AddFontFromFileTTF(chosen_ui, 15.0f * dpi_scale, &merge_cfg, ranges.Data);
        }
    }
    if (!fonts.mono) {
        fonts.mono = fonts.regular;
    }

    apply_cass_style(true, dpi_scale);
    return fonts;
}

} // namespace tin::ui
