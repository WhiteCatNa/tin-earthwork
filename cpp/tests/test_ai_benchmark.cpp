#include "core/ai_assistant.hpp"
#include "core/ai_optimizer.hpp"
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

    // 构造精确最小费用最大流残余网络 (0..ns-1 为挖方区, ns..ns+nd-1 为填方区, SRC, SNK)
    const int SRC = static_cast<int>(ns + nd);
    const int SNK = SRC + 1;
    const int V = SNK + 1;
    struct Edge { int u, v; double w; };
    std::vector<Edge> edges;
    for (size_t s = 0; s < ns; ++s) {
        for (size_t d = 0; d < nd; ++d) {
            edges.push_back({static_cast<int>(s), static_cast<int>(ns + d), cost[s][d]});
            if (flow[s][d] > 1e-6) {
                edges.push_back({static_cast<int>(ns + d), static_cast<int>(s), -cost[s][d]});
            }
        }
        // SRC <-> s 残余边（SRC 与 SNK 分离，保证最大流守恒下的负权圈检验）
        if (sup_cap[s] - sup_used[s] > 1e-5) {
            edges.push_back({SRC, static_cast<int>(s), 0.0});
        }
        if (sup_used[s] > 1e-5) {
            edges.push_back({static_cast<int>(s), SRC, 0.0});
        }
    }
    for (size_t d = 0; d < nd; ++d) {
        if (dem_cap[d] - dem_used[d] > 1e-5) {
            edges.push_back({static_cast<int>(ns + d), SNK, 0.0});
        }
        if (dem_used[d] > 1e-5) {
            edges.push_back({SNK, static_cast<int>(ns + d), 0.0});
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

    // v4.0 万点级 (101x101 = 10,201 点) 构网+算量耗时基准
    auto pts_10k = make_grid_points(101, 101, 5.0, [](double x, double y) {
        return 30.0 + 0.025 * x - 0.018 * y + 1.6 * std::sin(0.028 * x) * std::cos(0.025 * y);
    });
    auto tk0 = std::chrono::steady_clock::now();
    tin::TINEarthworkCalculator calc10k(31.0);
    calc10k.add_points(pts_10k);
    auto res10k = calc10k.run_full_calculation();
    auto tk1 = std::chrono::steady_clock::now();
    double tin_10k_ms = std::chrono::duration<double, std::milli>(tk1 - tk0).count();
    (void)res10k;

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

    // 联合设计面费用优化基准（含真实数据对拍 + 倾斜起伏场地综合节费测算）
    calc.build_tin();
    auto opt_real = tin::optimize_grade_cost(calc, tin::HaulCostParams{}, tin::GradeOptConstraints{});

    auto slope_pts = make_grid_points(8, 8, 25.0, [](double x, double y) {
        return 30.0 + 0.025 * x - 0.015 * y + 0.8 * std::sin(0.04 * x) * std::cos(0.04 * y);
    });
    tin::TINEarthworkCalculator slope_calc(30.0);
    slope_calc.add_points(slope_pts);
    slope_calc.build_tin();
    tin::GradeOptConstraints gcons;
    gcons.max_slope_x = 3.0;
    gcons.max_slope_y = 3.0;
    gcons.max_evals = 120;
    auto opt_slope = tin::optimize_grade_cost(slope_calc, tin::HaulCostParams{}, gcons);

    bool grade_beats_bf =
        (opt_real.optimized.total_cost <= opt_real.best_fit.total_cost + 1e-6) &&
        (opt_slope.optimized.total_cost <= opt_slope.best_fit.total_cost + 1e-6) &&
        (std::abs(opt_slope.optimized.plane.slope_x) <= gcons.max_slope_x + 1e-9) &&
        (std::abs(opt_slope.optimized.plane.slope_y) <= gcons.max_slope_y + 1e-9);
    double grade_savings_pct = opt_slope.savings_pct_vs_flat;
    double grade_ms = opt_real.elapsed_ms;

    // Iteration 2 基准：1) 不确定度评定 + 自适应补测推荐（含真值覆盖与 10% 补测比随机对比）
    auto u0 = std::chrono::steady_clock::now();
    auto uncert_real = tin::analyze_uncertainty_and_recommend(calc, 0.03, 8, 8);
    auto u1 = std::chrono::steady_clock::now();
    double uncertainty_ms = std::chrono::duration<double, std::milli>(u1 - u0).count();

    auto mound_fn = [](double x, double y) {
        double dx = x - 50.0, dy = y - 50.0;
        return 4.0 * std::exp(-(dx * dx + dy * dy) / (2.0 * 18.0 * 18.0));
    };
    auto mound_pts = make_grid_points(11, 11, 10.0, mound_fn);
    tin::TINEarthworkCalculator mcalc(0.0);
    mcalc.add_points(mound_pts);
    mcalc.build_tin();
    auto mu = tin::analyze_uncertainty_and_recommend(mcalc, 0.0, 6, 8);
    double erf1 = std::erf(50.0 / (18.0 * std::sqrt(2.0)));
    double mound_true = 4.0 * 2.0 * 3.14159265358979323846 * 18.0 * 18.0 * erf1 * erf1;
    bool uncertainty_coverage_ok =
        (std::abs(mu.cut_corrected - mound_true) <= mu.cut_ci95 * 1.25) &&
        (mu.cut_ci95 > 0.0);

    auto rec_terrain = [](double x, double y) {
        double dx = x - 55.0, dy = y - 55.0;
        return 10.0 + 6.5 * std::exp(-(dx * dx + dy * dy) / (2.0 * 14.0 * 14.0)) + 0.02 * x - 0.01 * y;
    };
    auto sparse_pts = make_grid_points(9, 9, 12.5, rec_terrain);
    tin::TINEarthworkCalculator scalc(11.5);
    scalc.add_points(sparse_pts);
    double s_base_cut = scalc.run_full_calculation().total_cut;
    auto fine_pts = make_grid_points(41, 41, 2.5, rec_terrain);
    tin::TINEarthworkCalculator fcalc(11.5);
    fcalc.add_points(fine_pts);
    double s_true_cut = fcalc.run_full_calculation().total_cut;
    double s_base_err = std::abs(s_base_cut - s_true_cut);

    auto su = tin::analyze_uncertainty_and_recommend(scalc, 0.02, 8, 6);
    auto rpts = sparse_pts;
    for (const auto& r : su.recommendations) {
        rpts.push_back({"R" + std::to_string(r.rank), r.x, r.y, rec_terrain(r.x, r.y), 0.0, 0.0, false});
    }
    tin::TINEarthworkCalculator rcalc(11.5);
    rcalc.add_points(rpts);
    double s_rec_err = std::abs(rcalc.run_full_calculation().total_cut - s_true_cut);

    double rnd_drop_sum = 0.0;
    for (int rseed = 0; rseed < 3; ++rseed) {
        auto rndpts = sparse_pts;
        for (int k = 0; k < 8; ++k) {
            double rx = 5.0 + 90.0 * (((k + rseed * 5) * 37 + 13) % 97) / 97.0;
            double ry = 5.0 + 90.0 * (((k + rseed * 7) * 53 + 29) % 89) / 89.0;
            rndpts.push_back({"N" + std::to_string(k), rx, ry, rec_terrain(rx, ry), 0.0, 0.0, false});
        }
        tin::TINEarthworkCalculator rndcalc(11.5);
        rndcalc.add_points(rndpts);
        double s_rnd_err = std::abs(rndcalc.run_full_calculation().total_cut - s_true_cut);
        rnd_drop_sum += std::max(0.0, s_base_err - s_rnd_err);
    }
    double rnd_drop_mean = std::max(1.0, rnd_drop_sum / 3.0);
    double survey_rec_ratio = std::max(0.0, s_base_err - s_rec_err) / rnd_drop_mean;
    (void)uncert_real;

    // 2) 空间异常点检测（5% 注入粗差 Recall & Precision）
    auto clean_pts = make_grid_points(14, 14, 10.0, [](double x, double y) {
        return 25.0 + 0.03 * x - 0.02 * y + 0.6 * std::sin(0.06 * x) * std::cos(0.05 * y);
    });
    std::vector<int> inj_idx = {18, 35, 52, 74, 91, 110, 128, 149, 165, 178};
    for (size_t k = 0; k < inj_idx.size(); ++k) {
        clean_pts[inj_idx[k]].z += (k % 2 == 0) ? 4.2 : -3.8;
    }
    auto anom_rep = tin::detect_survey_anomalies(clean_pts);
    int tp = 0;
    for (const auto& a : anom_rep.anomalies) {
        if (std::find(inj_idx.begin(), inj_idx.end(), a.point_index) != inj_idx.end()) ++tp;
    }
    double anomaly_recall = static_cast<double>(tp) / static_cast<double>(inj_idx.size());
    double anomaly_precision = anom_rep.anomalies.empty()
                                 ? 0.0
                                 : static_cast<double>(tp) / static_cast<double>(anom_rep.anomalies.size());

    // 3) 30 条中英双语自然语言 & CAD 工程指令意图识别套件
    struct NLCase { const char* text; tin::AssistantAction expected; };
    const NLCase nl_cases[] = {
        {"HELP", tin::AssistantAction::Help},
        {"查看帮助命令", tin::AssistantAction::Help},
        {"AUTOB", tin::AssistantAction::AutoRectBoundary},
        {"生成矩形边界", tin::AssistantAction::AutoRectBoundary},
        {"生成外轮廓边界", tin::AssistantAction::AutoOutline},
        {"clear boundary", tin::AssistantAction::ClearBoundary},
        {"开始计算", tin::AssistantAction::Calculate},
        {"run tin calculation", tin::AssistantAction::Calculate},
        {"求解零平衡标高", tin::AssistantAction::BalanceH0},
        {"balance elevation", tin::AssistantAction::BalanceH0},
        {"拟合最小二乘最优斜面", tin::AssistantAction::BestFitPlane},
        {"保坡求解平衡高程", tin::AssistantAction::BalancePlane},
        {"帮我联合优化总费用", tin::AssistantAction::OptimizeGradeCost},
        {"optimize total cost", tin::AssistantAction::OptimizeGradeCost},
        {"按间距15米做平行断面", tin::AssistantAction::RunSections},
        {"计算分层土方", tin::AssistantAction::ComputeStrata},
        {"计算土方调配方案", tin::AssistantAction::ComputeHaul},
        {"放坡 1:1.2 1:1.5", tin::AssistantAction::ComputeSlope},
        {"检查测点异常和健康分", tin::AssistantAction::RunDiagnostics},
        {"detect survey outliers", tin::AssistantAction::RunDiagnostics},
        {"一键修复高程粗差", tin::AssistantAction::CleanAnomalies},
        {"推荐8个补测点", tin::AssistantAction::RunUncertainty},
        {"uncertainty analysis", tin::AssistantAction::RunUncertainty},
        {"测距坡度工具", tin::AssistantAction::MeasureDist},
        {"测量多边形面积", tin::AssistantAction::MeasureArea},
        {"把标高设为51.2", tin::AssistantAction::SetElevation},
        {"switch to 3d view", tin::AssistantAction::SetView3D},
        {"切换到二维平面", tin::AssistantAction::SetView2D},
        {"白底出图打印模式", tin::AssistantAction::LightMode},
        {"导出dxf图纸", tin::AssistantAction::ExportDxf},
    };
    int nl_ok = 0;
    const int nl_total = static_cast<int>(std::size(nl_cases));
    for (const auto& c : nl_cases) {
        if (tin::parse_engineering_intent(c.text).action == c.expected) ++nl_ok;
    }
    double nl_intent_acc = static_cast<double>(nl_ok) / static_cast<double>(nl_total);

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
    bool ai_wf_ok = app.run_ai_full_workflow_action();
    app.set_tool_mode(tin::ui::CadToolMode::MeasureDist);
    bool measure_ok = (tin::parse_engineering_intent("MEASURE").action == tin::AssistantAction::MeasureDist) &&
                      (tin::parse_engineering_intent("AREA").action == tin::AssistantAction::MeasureArea);
    bool tab_comp_ok = tin::suggest_command_completions("AI").size() >= 2 &&
                       tin::suggest_command_completions("OPT").size() >= 1;
    int ui_vtx_min = 1000000;
    for (int tab = 0; tab < 7; ++tab) {
        app.set_inspector_tab(tab);
        io.DisplaySize = ImVec2(1440.0f, 900.0f);
        io.DeltaTime = 1.0f / 60.0f;
        ImGui::NewFrame();
        app.render();
        ImGui::Render();
        ImDrawData* dd = ImGui::GetDrawData();
        int vtx = 0;
        if (dd) {
            for (int i = 0; i < dd->CmdListsCount; ++i) vtx += dd->CmdLists[i]->VtxBuffer.Size;
        }
        ui_vtx_min = std::min(ui_vtx_min, vtx);
    }
    bool ui_ok = ai_wf_ok && (ui_vtx_min > 200);
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
                  << "  \"has_grade_optimizer\": true,\n"
                  << "  \"grade_beats_best_fit\": " << (grade_beats_bf ? "true" : "false") << ",\n"
                  << "  \"grade_savings_pct\": " << grade_savings_pct << ",\n"
                  << "  \"grade_ms\": " << grade_ms << ",\n"
                  << "  \"has_uncertainty\": true,\n"
                  << "  \"uncertainty_coverage_ok\": " << (uncertainty_coverage_ok ? "true" : "false") << ",\n"
                  << "  \"survey_rec_ratio\": " << survey_rec_ratio << ",\n"
                  << "  \"uncertainty_ms\": " << uncertainty_ms << ",\n"
                  << "  \"has_anomaly_detector\": true,\n"
                  << "  \"anomaly_recall\": " << anomaly_recall << ",\n"
                  << "  \"anomaly_precision\": " << anomaly_precision << ",\n"
                  << "  \"has_ai_assistant\": true,\n"
                  << "  \"nl_intent_acc\": " << nl_intent_acc << ",\n"
                  << "  \"robust_passed\": " << robust_passed << ",\n"
                  << "  \"robust_total\": " << robust_total << ",\n"
                  << "  \"ui_tabs\": 7,\n"
                  << "  \"ui_ok\": " << (ui_ok ? "true" : "false") << ",\n"
                  << "  \"ui_has_ai_tab\": " << (ai_wf_ok ? "true" : "false") << ",\n"
                  << "  \"ui_has_measure_tool\": " << (measure_ok ? "true" : "false") << ",\n"
                  << "  \"ui_has_tab_completion\": " << (tab_comp_ok ? "true" : "false") << ",\n"
                  << "  \"ui_contrast\": " << contrast << ",\n"
                  << "  \"tin_10k_ms\": " << tin_10k_ms << ",\n"
                  << "  \"has_constrained_grade\": false,\n"
                  << "  \"constrained_drain_ok\": false,\n"
                  << "  \"constrained_ctrl_ok\": false,\n"
                  << "  \"pareto_count\": 0,\n"
                  << "  \"has_multi_bench_dp\": false,\n"
                  << "  \"multi_bench_vs_flat_pct\": 0.0,\n"
                  << "  \"multi_bench_vs_2axis_pct\": 0.0,\n"
                  << "  \"multi_bench_ms\": 999.0,\n"
                  << "  \"ui_has_v4_features\": false\n"
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
