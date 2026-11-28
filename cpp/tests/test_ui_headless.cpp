#include "core/calculator.hpp"
#include "io/io.hpp"
#include "ui/app.hpp"
#include "ui/theme.hpp"
#include "imgui.h"

#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

std::string find_test_xlsx(int argc, char** argv) {
    if (argc >= 2 && std::filesystem::exists(argv[1])) {
        return argv[1];
    }
    const char* candidates[] = {
        "test_data_1000.xlsx",
        "../test_data_1000.xlsx",
        "../../test_data_1000.xlsx",
    };
    for (const char* c : candidates) {
        if (std::filesystem::exists(c)) return c;
    }
    return "";
}

void check_true(bool cond, const std::string& msg) {
    if (!cond) {
        throw std::runtime_error("UI test assertion failed: " + msg);
    }
}

int render_one_frame(tin::ui::CassApp& app) {
    ImGuiIO& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(1440.0f, 900.0f);
    io.DeltaTime = 1.0f / 60.0f;
    ImGui::NewFrame();
    app.render();
    ImGui::Render();
    ImDrawData* dd = ImGui::GetDrawData();
    if (!dd) return 0;
    int total_vtx = 0;
    for (int i = 0; i < dd->CmdListsCount; ++i) {
        total_vtx += dd->CmdLists[i]->VtxBuffer.Size;
    }
    return total_vtx;
}

} // namespace

int main(int argc, char** argv) {
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();

    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.DisplaySize = ImVec2(1440.0f, 900.0f);
    io.DeltaTime = 1.0f / 60.0f;

    tin::ui::ThemeFonts fonts = tin::ui::setup_imgui_fonts_and_style(1.0f);
    unsigned char* tex_pixels = nullptr;
    int tex_w = 0, tex_h = 0;
    io.Fonts->GetTexDataAsRGBA32(&tex_pixels, &tex_w, &tex_h);
    check_true(tex_pixels != nullptr && tex_w > 0 && tex_h > 0, "ImGui font atlas built");

    tin::ui::CassApp app(nullptr, fonts);

    std::string xlsx = find_test_xlsx(argc, argv);
    check_true(!xlsx.empty(), "test_data_1000.xlsx found");
    check_true(app.load_points_from_path(xlsx), "load test_data_1000.xlsx");
    app.generate_auto_outline();
    app.set_design_elevation(50.0);
    app.enable_grid_check(true, 15.0);
    app.enable_contours(true, 0.0);
    check_true(app.run_calculation(), "run TIN calculation");

    // 渲染所有 6 个基础标签页 + 深浅色模式 + 3D 视口
    int min_vtx = 1000000;
    for (int tab = 0; tab < 6; ++tab) {
        app.set_inspector_tab(tab);
        int vtx = render_one_frame(app);
        min_vtx = std::min(min_vtx, vtx);
        check_true(vtx > 200, "non-empty draw list on tab " + std::to_string(tab));
    }

    app.set_dark_mode(false);
    check_true(render_one_frame(app) > 200, "light mode frame renders");
    app.set_dark_mode(true);

    app.set_3d_view(true);
    check_true(render_one_frame(app) > 200, "3D viewport frame renders");
    app.set_3d_view(false);

    app.execute_command("HELP");
    app.execute_command("H=50.2");
    check_true(app.result() != nullptr, "result valid after H=50.2 command");

    // WCAG 2.1 正文对比度校核
    const ImGuiStyle& st = ImGui::GetStyle();
    double cr_main = tin::ui::contrast_ratio(st.Colors[ImGuiCol_Text], st.Colors[ImGuiCol_WindowBg]);
    double cr_child = tin::ui::contrast_ratio(st.Colors[ImGuiCol_Text], st.Colors[ImGuiCol_ChildBg]);
    check_true(cr_main >= 4.5 && cr_child >= 4.5, "WCAG AA primary text contrast >= 4.5");

    std::cout << "[PASS] headless_ui_baseline (min_vtx=" << min_vtx
              << ", contrast=" << cr_main << ")\n";

    ImGui::DestroyContext();
    return 0;
}
