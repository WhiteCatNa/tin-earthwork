#include "core/analysis.hpp"
#include "core/calculator.hpp"
#include "core/engineering.hpp"
#include "io/io.hpp"
#include "ui/app.hpp"
#include "ui/theme.hpp"
#include "imgui.h"

#include <chrono>
#include <cmath>
#include <filesystem>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <queue>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::string find_test_xlsx(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--json") continue;
        if (std::filesystem::exists(a)) return a;
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

std::vector<tin::SurveyPoint> make_grid_points(
    int nx, int ny, double step, const std::function<double(double, double)>& z_fn) {
    std::vector<tin::SurveyPoint> pts;
    pts.reserve(static_cast<size_t>(nx * ny));
    int id = 1;
    for (int iy = 0; iy < ny; ++iy) {
        for (int ix = 0; ix < nx; ++ix) {
            double x = ix * step;
            double y = iy * step;
            pts.push_back(tin::SurveyPoint{
                "P" + std::to_string(id++), x, y, z_fn(x, y), 0.0, 0.0, false
            });
        }
    }
    return pts;
}

// 检验土方调配残余图是否存在负权增广圈（LP 最优性充要条件）
bool haul_plan_has_negative_cycle(const tin::HaulPlanResult& plan, double compact = 1.0) {
    std::vector<int> sup_ids, dem_ids;
    std::vector<double> sup_cap, dem_cap;
    for (size_t i = 0; i < plan.zones.size(); ++i) {
        const auto& z = plan.zones[i];
        if (z.net_surplus > 1e-6) {
            sup_ids.push_back(z.id);
            sup_cap.push_back(z.net_surplus);
        } else if (z.net_surplus < -1e-6) {
            dem_ids.push_back(z.id);
            dem_cap.push_back(-z.net_surplus);
        }
    }
    (void)compact;
    const size_t ns = sup_ids.size();
    const size_t nd = dem_ids.size();
    if (ns <= 1 || nd <= 1) return false;

    std::vector<std::vector<double>> flow(ns, std::vector<double>(nd, 0.0));
    std::vector<std::vector<double>> cost(ns, std::vector<double>(nd, 0.0));
    std::vector<double> sup_used(ns, 0.0), dem_used(nd, 0.0);

    for (size_t s = 0; s < ns; ++s) {
        const auto& zs = plan.zones[sup_ids[s]];
        for (size_t d = 0; d < nd; ++d) {
            const auto& zd = plan.zones[dem_ids[d]];
            cost[s][d] = std::hypot(zs.cut_centroid.x - zd.fill_centroid.x,
                                    zs.cut_centroid.y - zd.fill_centroid.y);
        }
    }
    for (const auto& f : plan.flows) {
        int si = -1, di = -1;
        for (size_t s = 0; s < ns; ++s) if (sup_ids[s] == f.from_zone_id) { si = static_cast<int>(s); break; }
        for (size_t d = 0; d < nd; ++d) if (dem_ids[d] == f.to_zone_id) { di = static_cast<int>(d); break; }
        if (si >= 0 && di >= 0) {
            flow[si][di] += f.volume;
            sup_used[si] += f.volume;
            dem_used[di] += f.volume;
        }
    }

    // 构造二分残余网络 (0..ns-1 为挖方区, ns..ns+nd-1 为填方区, ns+nd 为虚拟源汇外运借土平衡点)
    const int V = static_cast<int>(ns + nd + 1);
    const int W = static_cast<int>(ns + nd);
    struct Edge { int u, v; double w; };
    std::vector<Edge> edges;
    for (size_t s = 0; s < ns; ++s) {
        for (size_t d = 0; d < nd; ++d) {
            edges.push_back({static_cast<int>(s), static_cast<int>(ns + d), cost[s][d]});
            if (flow[s][d] > 1e-6) {
                edges.push_back({static_cast<int>(ns + d), static_cast<int>(s), -cost[s][d]});
            }
        }
        // 未调出余土 <-> 虚拟节点 W (cost 0)
        if (sup_cap[s] - sup_used[s] > 1e-5) {
            edges.push_back({static_cast<int>(s), W, 0.0});
        }
        if (sup_used[s] > 1e-5) {
            edges.push_back({W, static_cast<int>(s), 0.0});
        }
    }
    for (size_t d = 0; d < nd; ++d) {
        if (dem_cap[d] - dem_used[d] > 1e-5) {
            edges.push_back({W, static_cast<int>(ns + d), 0.0});
        }
        if (dem_used[d] > 1e-5) {
            edges.push_back({static_cast<int>(ns + d), W, 0.0});
        }
    }

    // Bellman-Ford 检测是否存在严格负环
    std::vector<double> dist(V, 0.0);
    for (int it = 0; it < V; ++it) {
        bool changed = false;
        for (const auto& e : edges) {
            if (dist[e.u] + e.w < dist[e.v] - 1e-5) {
                dist[e.v] = dist[e.u] + e.w;
                changed = true;
                if (it == V - 1) return true;
            }
        }
        if (!changed) break;
    }
    return false;
}

} // namespace

int main(int argc, char** argv) {
    bool json_mode = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--json") json_mode = true;
    }

    std::string xlsx = find_test_xlsx(argc, argv);
    if (xlsx.empty()) {
        std::cerr << "Missing test_data_1000.xlsx\n";
        return 1;
    }

    auto imp = tin::import_points_file(xlsx);
    tin::TINEarthworkCalculator calc(50.0);
    calc.add_points(imp.points);

    auto t0 = std::chrono::steady_clock::now();
    auto res = calc.run_full_calculation();
    auto t1 = std::chrono::steady_clock::now();
    double tin_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

    bool parity_ok = std::abs(res.total_cut - 3569.3644) < 0.5 &&
                     std::abs(res.total_fill - 3876.4035) < 0.5;

    // 调配基准测试（含真实数据集 + 4 组非平凡多供需方格）
    auto h0 = std::chrono::steady_clock::now();
    auto haul_real = tin::compute_haul_plan(res, 4, 3);
    auto h1 = std::chrono::steady_clock::now();
    double haul_ms = std::chrono::duration<double, std::milli>(h1 - h0).count();

    int haul_optimal_cases = 0;
    const int haul_total_cases = 5;
    if (!haul_plan_has_negative_cycle(haul_real)) ++haul_optimal_cases;
    for (int seed = 1; seed <= 4; ++seed) {
        auto pts = make_grid_points(9, 9, 15.0, [seed](double x, double y) {
            return 20.0 + 3.5 * std::sin(0.055 * x + seed) * std::cos(0.048 * y - seed * 0.7)
                        + 0.02 * (x - 60.0) - 0.015 * (y - 60.0);
        });
        tin::TINEarthworkCalculator hc(20.0);
        hc.add_points(pts);
        auto hres = hc.run_full_calculation();
        auto hplan = tin::compute_haul_plan(hres, 5, 5);
        if (!haul_plan_has_negative_cycle(hplan)) ++haul_optimal_cases;
    }

    // 边界鲁棒性检查
    int robust_passed = 0;
    const int robust_total = 4;
    // 1. 3点极简地形
    try {
        tin::TINEarthworkCalculator c3(0.0);
        c3.add_points({{"1", 0, 0, 1, 0, 0, false}, {"2", 10, 0, 2, 0, 0, false}, {"3", 0, 10, 3, 0, 0, false}});
        auto r3 = c3.run_full_calculation();
        if (r3.computed_area > 49.0 && std::isfinite(r3.total_cut)) ++robust_passed;
    } catch (...) {}
    // 2. 共线点拒绝异常
    try {
        tin::TINEarthworkCalculator ccol(0.0);
        ccol.add_points({{"1", 0, 0, 1, 0, 0, false}, {"2", 10, 0, 2, 0, 0, false}, {"3", 20, 0, 3, 0, 0, false}});
        ccol.run_full_calculation();
    } catch (const std::exception&) {
        ++robust_passed;
    }
    // 3. 空调配防御
    try {
        tin::CalculationResult empty_res;
        auto hp = tin::compute_haul_plan(empty_res, 4, 3);
        if (hp.zones.empty() && hp.estimated_cost == 0.0) ++robust_passed;
    } catch (...) {}
    // 4. 负单价/退化系数安全
    try {
        auto hp = tin::compute_haul_plan(res, 1, 1);
        if (std::isfinite(hp.estimated_cost)) ++robust_passed;
    } catch (...) {}

    // 无头 UI 基础检查
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.DisplaySize = ImVec2(1440.0f, 900.0f);
    io.DeltaTime = 1.0f / 60.0f;
    tin::ui::ThemeFonts fonts = tin::ui::setup_imgui_fonts_and_style(1.0f);
    unsigned char* px = nullptr;
    int tw = 0, th = 0;
    io.Fonts->GetTexDataAsRGBA32(&px, &tw, &th);
    tin::ui::CassApp app(nullptr, fonts);
    app.load_points_from_path(xlsx);
    app.generate_auto_outline();
    app.set_design_elevation(50.0);
    app.run_calculation();
    io.DisplaySize = ImVec2(1440.0f, 900.0f);
    io.DeltaTime = 1.0f / 60.0f;
    ImGui::NewFrame();
    app.render();
    ImGui::Render();
    bool ui_ok = ImGui::GetDrawData() != nullptr && ImGui::GetDrawData()->CmdListsCount > 0;
    double contrast = tin::ui::contrast_ratio(
        ImGui::GetStyle().Colors[ImGuiCol_Text],
        ImGui::GetStyle().Colors[ImGuiCol_WindowBg]);
    ImGui::DestroyContext();

    if (json_mode) {
        std::cout << std::fixed << std::setprecision(4)
                  << "{\n"
                  << "  \"parity_ok\": " << (parity_ok ? "true" : "false") << ",\n"
                  << "  \"tin_ms\": " << tin_ms << ",\n"
                  << "  \"haul_ms\": " << haul_ms << ",\n"
                  << "  \"haul_optimal_cases\": " << haul_optimal_cases << ",\n"
                  << "  \"haul_total_cases\": " << haul_total_cases << ",\n"
                  << "  \"has_grade_optimizer\": false,\n"
                  << "  \"grade_savings_pct\": 0.0,\n"
                  << "  \"grade_ms\": 0.0,\n"
                  << "  \"has_uncertainty\": false,\n"
                  << "  \"uncertainty_coverage_ok\": false,\n"
                  << "  \"survey_rec_ratio\": 0.0,\n"
                  << "  \"uncertainty_ms\": 0.0,\n"
                  << "  \"has_anomaly_detector\": false,\n"
                  << "  \"anomaly_recall\": 0.0,\n"
                  << "  \"anomaly_precision\": 0.0,\n"
                  << "  \"has_ai_assistant\": false,\n"
                  << "  \"nl_intent_acc\": 0.0,\n"
                  << "  \"robust_passed\": " << robust_passed << ",\n"
                  << "  \"robust_total\": " << robust_total << ",\n"
                  << "  \"ui_tabs\": 6,\n"
                  << "  \"ui_ok\": " << (ui_ok ? "true" : "false") << ",\n"
                  << "  \"ui_has_ai_tab\": false,\n"
                  << "  \"ui_has_measure_tool\": false,\n"
                  << "  \"ui_has_tab_completion\": false,\n"
                  << "  \"ui_contrast\": " << contrast << "\n"
                  << "}\n";
    } else {
        std::cout << "[BENCH] parity=" << (parity_ok ? "OK" : "FAIL")
                  << " tin_ms=" << tin_ms
                  << " haul_ms=" << haul_ms
                  << " haul_opt=" << haul_optimal_cases << "/" << haul_total_cases
                  << " robust=" << robust_passed << "/" << robust_total
                  << "\n";
    }
    return parity_ok && ui_ok ? 0 : 1;
}
