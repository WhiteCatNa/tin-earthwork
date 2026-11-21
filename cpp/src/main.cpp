#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>
#include <d3d11.h>

#include "imgui.h"
#include "backends/imgui_impl_dx11.h"
#include "backends/imgui_impl_win32.h"

#include "io/io.hpp"
#include "ui/app.hpp"
#include "ui/theme.hpp"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(
    HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

namespace {

ID3D11Device* g_pd3dDevice = nullptr;
ID3D11DeviceContext* g_pd3dDeviceContext = nullptr;
IDXGISwapChain* g_pSwapChain = nullptr;
ID3D11RenderTargetView* g_mainRenderTargetView = nullptr;
UINT g_ResizeWidth = 0;
UINT g_ResizeHeight = 0;

void CreateRenderTarget() {
    ID3D11Texture2D* pBackBuffer = nullptr;
    if (SUCCEEDED(g_pSwapChain->GetBuffer(0, IID_PPV_ARGS(&pBackBuffer))) && pBackBuffer) {
        g_pd3dDevice->CreateRenderTargetView(pBackBuffer, nullptr, &g_mainRenderTargetView);
        pBackBuffer->Release();
    }
}

void CleanupRenderTarget() {
    if (g_mainRenderTargetView) {
        g_mainRenderTargetView->Release();
        g_mainRenderTargetView = nullptr;
    }
}

bool CreateDeviceD3D(HWND hWnd) {
    DXGI_SWAP_CHAIN_DESC sd{};
    sd.BufferCount = 2;
    sd.BufferDesc.Width = 0;
    sd.BufferDesc.Height = 0;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.BufferDesc.RefreshRate.Numerator = 60;
    sd.BufferDesc.RefreshRate.Denominator = 1;
    sd.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_MODE_SWITCH;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow = hWnd;
    sd.SampleDesc.Count = 1;
    sd.SampleDesc.Quality = 0;
    sd.Windowed = TRUE;
    sd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    UINT createDeviceFlags = 0;
    D3D_FEATURE_LEVEL featureLevel{};
    const D3D_FEATURE_LEVEL featureLevelArray[2] = {
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_0,
    };

    HRESULT res = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, createDeviceFlags,
        featureLevelArray, 2, D3D11_SDK_VERSION, &sd, &g_pSwapChain,
        &g_pd3dDevice, &featureLevel, &g_pd3dDeviceContext);
    if (res != S_OK) {
        res = D3D11CreateDeviceAndSwapChain(
            nullptr, D3D_DRIVER_TYPE_WARP, nullptr, createDeviceFlags,
            featureLevelArray, 2, D3D11_SDK_VERSION, &sd, &g_pSwapChain,
            &g_pd3dDevice, &featureLevel, &g_pd3dDeviceContext);
    }
    if (res != S_OK) {
        return false;
    }

    CreateRenderTarget();
    return true;
}

void CleanupDeviceD3D() {
    CleanupRenderTarget();
    if (g_pSwapChain) { g_pSwapChain->Release(); g_pSwapChain = nullptr; }
    if (g_pd3dDeviceContext) { g_pd3dDeviceContext->Release(); g_pd3dDeviceContext = nullptr; }
    if (g_pd3dDevice) { g_pd3dDevice->Release(); g_pd3dDevice = nullptr; }
}

bool capture_backbuffer_png(const std::string& utf8_path) {
    if (!g_pSwapChain || !g_pd3dDevice || !g_pd3dDeviceContext) return false;
    ID3D11Texture2D* back_buf = nullptr;
    if (FAILED(g_pSwapChain->GetBuffer(0, IID_PPV_ARGS(&back_buf))) || !back_buf) {
        return false;
    }

    D3D11_TEXTURE2D_DESC desc{};
    back_buf->GetDesc(&desc);
    D3D11_TEXTURE2D_DESC staging_desc = desc;
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staging_desc.MiscFlags = 0;
    staging_desc.SampleDesc.Count = 1;
    staging_desc.SampleDesc.Quality = 0;

    ID3D11Texture2D* staging = nullptr;
    HRESULT hr = g_pd3dDevice->CreateTexture2D(&staging_desc, nullptr, &staging);
    if (FAILED(hr) || !staging) {
        back_buf->Release();
        return false;
    }

    g_pd3dDeviceContext->CopyResource(staging, back_buf);
    back_buf->Release();

    D3D11_MAPPED_SUBRESOURCE mapped{};
    hr = g_pd3dDeviceContext->Map(staging, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) {
        staging->Release();
        return false;
    }

    const int w = static_cast<int>(desc.Width);
    const int h = static_cast<int>(desc.Height);
    std::vector<std::uint8_t> rgba(static_cast<size_t>(w) * static_cast<size_t>(h) * 4u);
    const auto* src = static_cast<const std::uint8_t*>(mapped.pData);
    for (int y = 0; y < h; ++y) {
        const std::uint8_t* row_src = src + static_cast<size_t>(y) * mapped.RowPitch;
        std::uint8_t* row_dst = rgba.data() + static_cast<size_t>(y) * static_cast<size_t>(w) * 4u;
        std::memcpy(row_dst, row_src, static_cast<size_t>(w) * 4u);
        for (int x = 0; x < w; ++x) {
            row_dst[x * 4 + 3] = 255;
        }
    }

    g_pd3dDeviceContext->Unmap(staging, 0);
    staging->Release();

    try {
        tin::save_rgba_png(rgba.data(), w, h, utf8_path);
        return true;
    } catch (...) {
        return false;
    }
}

LRESULT WINAPI WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (ImGui_ImplWin32_WndProcHandler(hWnd, msg, wParam, lParam)) {
        return 1;
    }
    switch (msg) {
    case WM_SIZE:
        if (wParam == SIZE_MINIMIZED) return 0;
        g_ResizeWidth = LOWORD(lParam);
        g_ResizeHeight = HIWORD(lParam);
        return 0;
    case WM_SYSCOMMAND:
        if ((wParam & 0xfff0) == SC_KEYMENU) return 0;
        break;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        break;
    }
    return DefWindowProcW(hWnd, msg, wParam, lParam);
}

void enable_dpi_awareness() {
    HMODULE u32 = GetModuleHandleW(L"user32.dll");
    if (u32) {
        using Fn = BOOL(WINAPI*)(HANDLE);
        auto fn = reinterpret_cast<Fn>(GetProcAddress(u32, "SetProcessDpiAwarenessContext"));
        if (fn && fn(reinterpret_cast<HANDLE>(-4) /* DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 */)) {
            return;
        }
    }
    SetProcessDPIAware();
}

} // namespace

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE, PWSTR, int nCmdShow) {
    enable_dpi_awareness();

    int argc = 0;
    LPWSTR* argv_w = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::vector<std::string> args;
    if (argv_w) {
        args.reserve(static_cast<size_t>(argc));
        for (int i = 0; i < argc; ++i) {
            args.push_back(tin::wide_to_utf8(argv_w[i]));
        }
        LocalFree(argv_w);
    }

    std::string demo_file;
    std::string capture_dir;
    std::string startup_file;
    for (size_t i = 1; i < args.size(); ++i) {
        if (args[i] == "--demo" && i + 1 < args.size()) {
            demo_file = args[++i];
        } else if (args[i] == "--capture-dir" && i + 1 < args.size()) {
            capture_dir = args[++i];
        } else if (args[i].rfind("--", 0) != 0 && startup_file.empty()) {
            startup_file = args[i];
        }
    }
    const bool demo_mode = !demo_file.empty();

    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.style = CS_CLASSDC;
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"TinEarthworkCassWndClass";
    RegisterClassExW(&wc);

    RECT wr{0, 0, 1600, 940};
    AdjustWindowRectEx(&wr, WS_OVERLAPPEDWINDOW, FALSE, 0);
    HWND hwnd = CreateWindowExW(
        0, wc.lpszClassName,
        L"TIN 土方自动算量工作站 — 南方CASS CAD 版 (C++20 / Direct3D 11)",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT,
        wr.right - wr.left, wr.bottom - wr.top,
        nullptr, nullptr, wc.hInstance, nullptr);

    if (!hwnd) {
        UnregisterClassW(wc.lpszClassName, wc.hInstance);
        return 1;
    }

    tin::ui::configure_win32_dark_titlebar(hwnd);

    if (!CreateDeviceD3D(hwnd)) {
        CleanupDeviceD3D();
        DestroyWindow(hwnd);
        UnregisterClassW(wc.lpszClassName, wc.hInstance);
        return 2;
    }

    ShowWindow(hwnd, demo_mode ? SW_SHOWNOACTIVATE : nCmdShow);
    UpdateWindow(hwnd);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    io.IniFilename = nullptr;

    tin::ui::ThemeFonts fonts = tin::ui::setup_imgui_fonts_and_style(1.0f);

    ImGui_ImplWin32_Init(hwnd);
    ImGui_ImplDX11_Init(g_pd3dDevice, g_pd3dDeviceContext);

    tin::ui::CassApp app(hwnd, fonts);

    auto render_frame = [&](bool vsync) {
        if (g_ResizeWidth != 0 && g_ResizeHeight != 0) {
            CleanupRenderTarget();
            g_pSwapChain->ResizeBuffers(0, g_ResizeWidth, g_ResizeHeight, DXGI_FORMAT_UNKNOWN, 0);
            g_ResizeWidth = g_ResizeHeight = 0;
            CreateRenderTarget();
        }
        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();

        app.render();

        ImGui::Render();
        const float clear_col[4] = {0x18 / 255.0f, 0x1C / 255.0f, 0x22 / 255.0f, 1.0f};
        g_pd3dDeviceContext->OMSetRenderTargets(1, &g_mainRenderTargetView, nullptr);
        g_pd3dDeviceContext->ClearRenderTargetView(g_mainRenderTargetView, clear_col);
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
        g_pSwapChain->Present(vsync ? 1 : 0, 0);
    };

    auto pump_messages = []() {
        MSG msg{};
        while (PeekMessageW(&msg, nullptr, 0U, 0U, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    };

    int exit_code = 0;
    if (demo_mode) {
        if (!capture_dir.empty()) {
            std::filesystem::create_directories(std::filesystem::path(tin::utf8_to_wide(capture_dir)));
        }
        if (!app.load_points_from_path(demo_file)) {
            exit_code = 3;
        } else {
            double target_h = 50.0;
            if (!(app.min_z() < target_h && target_h < app.max_z())) {
                target_h = std::round(app.avg_z() * 100.0) / 100.0;
            }
            app.set_design_elevation(target_h);
            app.generate_rect_boundary(0.0);
            app.enable_grid_check(true, 15.0);
            app.enable_contours(true, 0.0);
            app.run_calculation();
            app.generate_quick_profile(true);
            int mix_id = app.first_mixed_triangle_id();
            if (mix_id >= 0) app.set_selected_triangle(mix_id);
            app.zoom_extents();

            auto save_shot = [&](const std::string& filename) {
                for (int k = 0; k < 4; ++k) {
                    pump_messages();
                    render_frame(false);
                }
                if (!capture_dir.empty()) {
                    std::filesystem::path outp = std::filesystem::path(tin::utf8_to_wide(capture_dir)) /
                                                 tin::utf8_to_wide(filename);
                    if (!capture_backbuffer_png(tin::wide_to_utf8(outp.wstring()))) {
                        exit_code = 4;
                    }
                }
            };

            app.set_dark_mode(true);
            app.set_inspector_tab(2);
            app.zoom_extents();
            save_shot("cass_01_main_dark.png");

            app.set_inspector_tab(3);
            save_shot("cass_02_profile.png");

            app.set_inspector_tab(1);
            save_shot("cass_03_points.png");

            app.set_dark_mode(false);
            app.set_inspector_tab(0);
            save_shot("cass_04_light_layout.png");
        }
    } else {
        if (!startup_file.empty()) {
            std::string lower = startup_file;
            for (char& ch : lower) {
                if (ch >= 'A' && ch <= 'Z') ch = static_cast<char>(ch - 'A' + 'a');
            }
            if (lower.size() >= 5 && lower.substr(lower.size() - 5) == ".json") {
                app.load_project_from_path(startup_file);
            } else {
                app.load_points_from_path(startup_file);
            }
        }

        bool done = false;
        while (!done) {
            MSG msg{};
            while (PeekMessageW(&msg, nullptr, 0U, 0U, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
                if (msg.message == WM_QUIT) done = true;
            }
            if (done) break;
            if (IsIconic(hwnd)) {
                Sleep(16);
                continue;
            }
            render_frame(true);
        }
    }

    ImGui_ImplDX11_Shutdown();
    ImGui_ImplWin32_Shutdown();
    ImGui::DestroyContext();

    CleanupDeviceD3D();
    DestroyWindow(hwnd);
    UnregisterClassW(wc.lpszClassName, wc.hInstance);
    return exit_code;
}
