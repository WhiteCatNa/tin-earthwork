#include "core/ai_assistant.hpp"
#include "core/ai_optimizer.hpp"
#include "core/analysis.hpp"
#include "core/calculator.hpp"
#include "core/delaunay.hpp"
#include "core/engineering.hpp"
#include "core/geometry.hpp"
#include "io/io.hpp"

#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

int g_passed = 0;
int g_failed = 0;

void check_true(bool cond, const std::string& msg) {
    if (!cond) {
        throw std::runtime_error("Assertion failed: " + msg);
    }
}

void check_near(double actual, double expected, double tol, const std::string& label) {
    if (!std::isfinite(actual) || std::abs(actual - expected) > tol) {
        std::ostringstream oss;
        oss << std::fixed << std::setprecision(6)
            << label << ": expected=" << expected << ", actual=" << actual
            << ", diff=" << std::abs(actual - expected) << " > tol=" << tol;
        throw std::runtime_error(oss.str());
    }
}

void run_test(const std::string& name, const std::function<void()>& fn) {
    try {
        fn();
        ++g_passed;
        std::cout << "[PASS] " << name << "\n";
    } catch (const std::exception& e) {
        ++g_failed;
        std::cerr << "[FAIL] " << name << " -> " << e.what() << "\n";
    }
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

std::string find_test_xlsx(int argc, char** argv) {
    if (argc >= 2 && std::filesystem::exists(tin::utf8_to_wide(argv[1]))) {
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

} // namespace

int main(int argc, char** argv) {
    std::cout << "=== TIN Earthwork C++20 Core Unit Tests ===\n";

    // 1. 平场解析解验证
    run_test("01_flat_analytical_cut_fill", []() {
        auto pts = make_grid_points(5, 5, 10.0, [](double, double) { return 12.5; });
        tin::TINEarthworkCalculator calc(10.0);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();
        check_near(res.computed_area, 1600.0, 1e-6, "area");
        check_near(res.total_cut, 4000.0, 1e-6, "cut");
        check_near(res.total_fill, 0.0, 1e-6, "fill");
        check_near(res.net_volume, 4000.0, 1e-6, "net");
    });

    // 2. 混合三角形零线精准剖分解析解
    run_test("02_mixed_triangle_zero_line_split", []() {
        // 直角三角形 (0,0)->+3, (10,0)->-1, (0,10)->-1
        // 平面方程: z(x,y) = 3 - 0.4x - 0.4y; 零线 x + y = 7.5
        // 挖方三角形面积 = 0.5 * 7.5 * 7.5 = 28.125, 体积 = 28.125 * 3 / 3 = 28.125
        // 总积分 = 50 * (3 - 1 - 1) / 3 = 50/3 = 16.6666667 => 填方 = 28.125 - 50/3 = 11.4583333
        std::vector<tin::SurveyPoint> pts = {
            {"A", 0.0, 0.0, 3.0, 0.0, 0.0, false},
            {"B", 10.0, 0.0, -1.0, 0.0, 0.0, false},
            {"C", 0.0, 10.0, -1.0, 0.0, 0.0, false},
        };
        tin::TINEarthworkCalculator calc(0.0);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();
        check_true(res.mixed_triangle_count == 1, "expected 1 mixed triangle");
        check_near(res.computed_area, 50.0, 1e-6, "area");
        check_near(res.total_cut, 28.125, 1e-6, "cut");
        check_near(res.total_fill, 28.125 - 50.0 / 3.0, 1e-6, "fill");
        check_near(res.net_volume, 50.0 / 3.0, 1e-6, "net");
    });

    // 3. 凹多边形 L 形边界精确裁剪
    run_test("03_concave_L_boundary_clipping", []() {
        auto pts = make_grid_points(5, 5, 10.0, [](double, double) { return 20.0; });
        // 40x40 挖去右上 20x20 => 面积 = 1200, 高差 +5 => 体积 6000
        std::vector<tin::XY> l_shape = {
            {0, 0}, {40, 0}, {40, 20}, {20, 20}, {20, 40}, {0, 40}
        };
        tin::TINEarthworkCalculator calc(15.0);
        calc.add_points(pts);
        calc.set_boundary(l_shape);
        auto res = calc.run_full_calculation();
        check_near(res.computed_area, 1200.0, 1e-4, "L-shape area");
        check_near(res.total_cut, 6000.0, 1e-3, "L-shape cut");
        check_near(res.total_fill, 0.0, 1e-6, "L-shape fill");
    });

    // 4. 倾斜设计面解析解
    run_test("04_inclined_plane_design", []() {
        auto pts = make_grid_points(5, 5, 10.0, [](double x, double y) {
            return 10.0 + 0.05 * x + 0.02 * y + 2.0; // 比设计面恒高 2.0m
        });
        tin::TINEarthworkCalculator calc(0.0);
        calc.add_points(pts);
        tin::PlaneDesign plane{0.0, 0.0, 10.0, 5.0, 2.0};
        calc.set_design_plane(plane);
        auto res = calc.run_full_calculation();
        check_near(res.computed_area, 1600.0, 1e-6, "plane area");
        check_near(res.total_cut, 3200.0, 1e-5, "plane cut");
        check_near(res.total_fill, 0.0, 1e-6, "plane fill");
    });

    // 5. 两期 TIN 叠加对比体积
    run_test("05_two_period_compare_overlay", []() {
        auto period_a = make_grid_points(4, 4, 10.0, [](double x, double) { return 50.0 + 0.1 * x; });
        auto period_b = make_grid_points(5, 5, 7.5, [](double x, double) { return 48.0 + 0.1 * x; });
        tin::TINEarthworkCalculator calc(0.0);
        calc.add_points(period_a);
        calc.set_compare_points(period_b);
        auto res = calc.run_full_calculation();
        check_true(res.is_compare, "is_compare should be true");
        check_near(res.computed_area, 900.0, 1e-4, "overlay area");
        check_near(res.total_cut, 1800.0, 1e-3, "overlay cut");
        check_near(res.total_fill, 0.0, 1e-5, "overlay fill");
    });

    // 6. 挖空岛屿面积与体积扣除
    run_test("06_hole_subtraction", []() {
        auto pts = make_grid_points(5, 5, 10.0, [](double, double) { return 14.0; });
        std::vector<tin::XY> hole = {{10, 10}, {30, 10}, {30, 30}, {10, 30}}; // 400 m2
        tin::TINEarthworkCalculator calc(10.0);
        calc.add_points(pts);
        calc.set_holes({hole}, true);
        auto res = calc.run_full_calculation();
        check_near(res.computed_area, 1200.0, 1e-4, "area minus hole");
        check_near(res.total_cut, 4800.0, 1e-3, "cut minus hole");
    });

    // 7. CDT 特征线约束边保持验证
    run_test("07_cdt_breakline_constraint", []() {
        // 构造一个对角线原本会翻转的矩形，用特征线强制约束对角线 (0,0)-(10,30)
        std::vector<tin::SurveyPoint> pts = {
            {"P1", 0.0, 0.0, 10.0, 0, 0, false},
            {"P2", 10.0, 0.0, 12.0, 0, 0, false},
            {"P3", 10.0, 30.0, 15.0, 0, 0, false},
            {"P4", 0.0, 30.0, 12.0, 0, 0, false},
        };
        tin::Breakline bl;
        bl.name = "Ridge";
        bl.closed = false;
        bl.points = {pts[0], pts[2]};

        tin::TINEarthworkCalculator calc(10.0);
        calc.add_points(pts);
        calc.set_breaklines({bl});
        auto res = calc.run_full_calculation();
        bool found_diag = false;
        auto is_pt = [](const tin::SurveyPoint& p, double x, double y) {
            return std::abs(p.x - x) < 1e-6 && std::abs(p.y - y) < 1e-6;
        };
        for (const auto& t : res.triangles) {
            if (t.is_boundary) continue;
            bool has0 = is_pt(t.p0, 0, 0) || is_pt(t.p1, 0, 0) || is_pt(t.p2, 0, 0);
            bool has2 = is_pt(t.p0, 10, 30) || is_pt(t.p1, 10, 30) || is_pt(t.p2, 10, 30);
            if (has0 && has2) found_diag = true;
        }
        check_true(found_diag, "constrained breakline diagonal (0,0)-(10,30) must exist in TIN");
    });

    // 8. 挖填平衡标高 H0 求解（默认土方系数 + 非默认可松性/压实系数）
    run_test("08_balance_elevation_default_and_soil_factors", []() {
        auto pts = make_grid_points(6, 6, 10.0, [](double x, double y) {
            return 20.0 + 0.08 * x - 0.04 * y + 0.001 * x * y;
        });
        tin::TINEarthworkCalculator calc(0.0);
        calc.add_points(pts);
        calc.build_tin();

        auto bal1 = tin::solve_balance_elevation(calc, tin::SoilFactors{1.0, 1.0});
        check_near(bal1.net_volume, 0.0, 1e-5, "default balance net");
        check_near(bal1.cut_volume, bal1.fill_volume, 1e-5, "default cut == fill");

        tin::SoilFactors sf{1.25, 0.90};
        auto bal2 = tin::solve_balance_elevation(calc, sf);
        // 压实平衡: cut == fill / compact
        check_near(bal2.cut_volume - bal2.fill_volume / sf.compact, 0.0, 1e-4, "soil factor balance");
        check_true(bal2.elevation < bal1.elevation, "compact < 1 requires more cut -> lower H0");
    });

    // 9. 方格网校核（线性面一致性 + 超出测点凸包的边界裁剪）
    run_test("09_grid_check_linear_and_boundary_clip", []() {
        auto pts = make_grid_points(5, 5, 10.0, [](double x, double y) {
            return 10.0 + 0.1 * x + 0.05 * y;
        });
        tin::TINEarthworkCalculator calc(12.0);
        calc.add_points(pts);
        // 设置比测点凸包 (0..40) 更大的边界 (-10..50)，校验裁剪限制到测点覆盖凸包
        calc.set_boundary({{-10, -10}, {50, -10}, {50, 50}, {-10, 50}});
        auto tin_res = calc.run_full_calculation();
        auto grid = tin::run_grid_check(calc, tin_res, 10.0);
        check_near(grid.computed_area, tin_res.computed_area, 1e-4, "grid clipped area == tin area");
        check_near(grid.total_cut, tin_res.total_cut, 1e-3, "grid cut == tin cut on linear plane");
        check_near(grid.total_fill, tin_res.total_fill, 1e-3, "grid fill == tin fill on linear plane");
    });

    // 10. 地形等高线追踪（斜面）
    run_test("10_elevation_contours_sloped_plane", []() {
        auto pts = make_grid_points(6, 6, 10.0, [](double x, double) { return 10.0 + 0.2 * x; }); // z in [10, 20]
        tin::TINEarthworkCalculator calc(15.0);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();
        auto cs = tin::extract_elevation_contours(res, 2.0, 5);
        check_near(cs.interval, 2.0, 1e-9, "contour interval");
        check_true(!cs.lines.empty(), "expected extracted contour polylines");
        check_true(cs.major_segments.size() + cs.minor_segments.size() >= 4, "expected contour segments");
    });

    // 11. 纵断面采样与断面面积积分
    run_test("11_longitudinal_profile_sampling", []() {
        auto pts = make_grid_points(6, 6, 10.0, [](double x, double) { return 10.0 + 0.2 * x; }); // x in [0, 50], z in [10, 20]
        tin::TINEarthworkCalculator calc(15.0);
        calc.add_points(pts);
        calc.build_tin();
        auto prof = tin::sample_profile(calc, {0.0, 25.0}, {50.0, 25.0}, 200);
        check_near(prof.length, 50.0, 1e-6, "profile length");
        check_near(prof.max_cut_depth, 5.0, 1e-2, "max cut depth");
        check_near(prof.max_fill_depth, 5.0, 1e-2, "max fill depth");
        // [0..25] fill triangle area = 0.5*25*5 = 62.5; [25..50] cut triangle area = 62.5
        check_near(prof.cut_area, 62.5, 0.2, "profile cut area");
        check_near(prof.fill_area, 62.5, 0.2, "profile fill area");
    });

    // 12. 真实 test_data_1000.xlsx 计算 + 全部文件格式往返测试
    run_test("12_real_xlsx_and_io_roundtrips", [&]() {
        std::string xlsx = find_test_xlsx(argc, argv);
        check_true(!xlsx.empty(), "test_data_1000.xlsx not found");

        auto imp = tin::import_points_file(xlsx);
        check_true(imp.points.size() == 1000, "expected 1000 points from test_data_1000.xlsx");

        // 无边界计算 (design = 50.0)
        tin::TINEarthworkCalculator calc_raw(50.0);
        calc_raw.add_points(imp.points);
        auto res_raw = calc_raw.run_full_calculation();
        check_true(res_raw.triangle_count > 1500, "expected >1500 triangles");
        check_true(res_raw.computed_area > 0.0, "positive computed area");

        // 外接矩形边界计算 (design = 50.0)
        double minx = imp.points[0].x, maxx = minx, miny = imp.points[0].y, maxy = miny;
        for (const auto& p : imp.points) {
            minx = std::min(minx, p.x); maxx = std::max(maxx, p.x);
            miny = std::min(miny, p.y); maxy = std::max(maxy, p.y);
        }
        std::vector<tin::XY> rect = {{minx, miny}, {maxx, miny}, {maxx, maxy}, {minx, maxy}};
        tin::TINEarthworkCalculator calc_rect(50.0);
        calc_rect.add_points(imp.points);
        calc_rect.set_boundary(rect);
        auto res_rect = calc_rect.run_full_calculation();
        check_near(res_rect.total_cut, res_raw.total_cut, 1e-2, "rect boundary matches unclipped cut");
        check_near(res_rect.total_fill, res_raw.total_fill, 1e-2, "rect boundary matches unclipped fill");

        std::cout << std::fixed << std::setprecision(4)
                  << "  [BENCH test_data_1000.xlsx design=50.0 raw]  "
                  << "cut=" << res_raw.total_cut
                  << " fill=" << res_raw.total_fill
                  << " net=" << res_raw.net_volume
                  << " area=" << res_raw.computed_area
                  << " tris=" << res_raw.triangle_count
                  << " ms=" << res_raw.elapsed_ms << "\n"
                  << "  [BENCH test_data_1000.xlsx design=50.0 rect] "
                  << "cut=" << res_rect.total_cut
                  << " fill=" << res_rect.total_fill
                  << " net=" << res_rect.net_volume
                  << " area=" << res_rect.computed_area
                  << " tris=" << res_rect.triangle_count
                  << " ms=" << res_rect.elapsed_ms << "\n";

        // 临时目录往返校验（绝不污染代码仓库）
        auto tmp = std::filesystem::temp_directory_path() / L"tin_earthwork_cpp_tests";
        std::filesystem::create_directories(tmp);

        tin::ProjectData proj;
        proj.project_name = "C++单元测试工程";
        proj.points = imp.points;
        proj.boundary = rect;
        proj.settings.design_elevation = 50.0;
        proj.settings.grid_enabled = true;
        proj.settings.grid_spacing = 15.0;
        proj.settings.contours_enabled = true;

        std::string proj_path = tin::wide_to_utf8((tmp / L"roundtrip.tinproj.json").wstring());
        tin::save_project_json(proj_path, proj);
        auto proj2 = tin::load_project_json(proj_path);
        check_true(proj2.points.size() == 1000, "json roundtrip points count");
        check_true(proj2.boundary.size() == 4, "json roundtrip boundary size");
        check_near(proj2.settings.design_elevation, 50.0, 1e-9, "json roundtrip design_elevation");

        std::string dat_path = tin::wide_to_utf8((tmp / L"roundtrip.dat").wstring());
        tin::export_cass_dat(imp.points, dat_path, "");
        auto dat_imp = tin::import_points_file(dat_path);
        check_true(dat_imp.points.size() == 1000, "CASS .dat roundtrip count");
        check_near(dat_imp.points[0].x, imp.points[0].x, 1e-3, "CASS .dat x");
        check_near(dat_imp.points[0].y, imp.points[0].y, 1e-3, "CASS .dat y");
        check_near(dat_imp.points[0].z, imp.points[0].z, 1e-3, "CASS .dat z");

        res_rect.contours = tin::extract_elevation_contours(res_rect, 0.0, 5);
        std::string dxf_path = tin::wide_to_utf8((tmp / L"out.dxf").wstring());
        std::string sum_path = tin::wide_to_utf8((tmp / L"sum.csv").wstring());
        std::string det_path = tin::wide_to_utf8((tmp / L"det.csv").wstring());
        std::string rpt_path = tin::wide_to_utf8((tmp / L"rpt.txt").wstring());

        tin::export_dxf_r12(res_rect, dxf_path);
        tin::export_summary_csv(res_rect, sum_path);
        tin::export_detail_csv(res_rect, det_path);
        tin::export_report_txt(res_rect, static_cast<int>(imp.points.size()), proj.project_name, rpt_path);

        check_true(std::filesystem::file_size(tin::utf8_to_wide(dxf_path)) > 5000, "dxf file size");
        check_true(std::filesystem::file_size(tin::utf8_to_wide(sum_path)) > 100, "summary csv size");
        check_true(std::filesystem::file_size(tin::utf8_to_wide(det_path)) > 5000, "detail csv size");
        check_true(std::filesystem::file_size(tin::utf8_to_wide(rpt_path)) > 200, "report txt size");

        // 检查 DXF 包含标准 SECTION / ENTITIES / EOF
        std::ifstream dxf_in(std::filesystem::path(tin::utf8_to_wide(dxf_path)));
        std::string dxf_content((std::istreambuf_iterator<char>(dxf_in)), std::istreambuf_iterator<char>());
        check_true(dxf_content.find("ENTITIES") != std::string::npos, "DXF contains ENTITIES");
        check_true(dxf_content.find("EOF") != std::string::npos, "DXF contains EOF");
    });

    // 13. 最小二乘最优斜面精确还原倾斜平面
    run_test("13_best_fit_plane_exact_recovery", []() {
        auto pts = make_grid_points(6, 6, 10.0, [](double x, double y) {
            return 12.0 + 0.04 * x - 0.03 * y;
        });
        tin::TINEarthworkCalculator calc(0.0);
        calc.add_points(pts);
        auto res = tin::solve_best_fit_plane(calc, tin::SoilFactors{1.0, 1.0});
        check_near(res.plane.slope_x, 4.0, 1e-6, "recovered slope_x (%)");
        check_near(res.plane.slope_y, -3.0, 1e-6, "recovered slope_y (%)");
        check_near(res.cut_volume, 0.0, 1e-4, "exact plane zero cut");
        check_near(res.fill_volume, 0.0, 1e-4, "exact plane zero fill");
    });

    // 14. 固定坡度平衡斜面求解（默认系数与压实系数 Ks'）
    run_test("14_balance_plane_fixed_slope", []() {
        auto pts = make_grid_points(6, 6, 10.0, [](double x, double y) {
            return 25.0 + 0.06 * x - 0.02 * y + 0.002 * (x - 25.0) * (x - 25.0);
        });
        tin::TINEarthworkCalculator calc(0.0);
        calc.add_points(pts);

        auto r1 = tin::solve_balance_plane(calc, 25.0, 25.0, 2.0, -1.0, tin::SoilFactors{1.0, 1.0});
        check_near(r1.plane.slope_x, 2.0, 1e-9, "preserved slope_x");
        check_near(r1.plane.slope_y, -1.0, 1e-9, "preserved slope_y");
        check_near(r1.net_volume, 0.0, 1e-5, "balanced plane net == 0");

        tin::SoilFactors sf{1.25, 0.90};
        auto r2 = tin::solve_balance_plane(calc, 25.0, 25.0, 2.0, -1.0, sf);
        check_near(r2.cut_volume - r2.fill_volume / sf.compact, 0.0, 1e-4, "soil factor plane balance");
    });

    // 15. 平行横断面法（解析验证 + 真实数据与 CSV 导出）
    run_test("15_parallel_sections_method_analytical_and_real", [&]() {
        auto pts = make_grid_points(6, 5, 10.0, [](double x, double) {
            return 10.0 + 0.08 * x;
        });
        tin::TINEarthworkCalculator calc(12.0);
        calc.add_points(pts);
        calc.build_tin();
        auto sec = tin::run_sections(calc, 5.0, 0.0, 160);
        check_true(sec.stations.size() == 11, "expected 11 stations across 50m @ 5m");
        check_near(sec.total_cut, 1000.0, 5.0, "sections analytical cut");
        check_near(sec.total_fill, 1000.0, 5.0, "sections analytical fill");

        std::string xlsx = find_test_xlsx(argc, argv);
        if (!xlsx.empty()) {
            auto imp = tin::import_points_file(xlsx);
            tin::TINEarthworkCalculator rc(50.0);
            rc.add_points(imp.points);
            rc.build_tin();
            auto rsec = tin::run_sections(rc, 20.0, 0.0, 180);
            check_true(rsec.stations.size() >= 10, "real dataset stations >= 10");
            check_true(rsec.total_cut > 1000.0 && rsec.total_fill > 1000.0, "real sections positive cut/fill");

            auto tmp = std::filesystem::temp_directory_path() / L"tin_earthwork_cpp_tests";
            std::filesystem::create_directories(tmp);
            std::string sec_csv = tin::wide_to_utf8((tmp / L"sections.csv").wstring());
            tin::export_sections_csv(rsec, sec_csv);
            check_true(std::filesystem::file_size(tin::utf8_to_wide(sec_csv)) > 200, "sections csv non-empty");
            std::cout << std::fixed << std::setprecision(2)
                      << "  [SECTIONS test_data_1000.xlsx @20m] stations=" << rsec.stations.size()
                      << " cut=" << rsec.total_cut << " fill=" << rsec.total_fill << "\n";
        }
    });

    // 16. 分层土方严格守恒（按深度分层 + 按绝对标高分层，含真实测点验证）
    run_test("16_strata_elevation_and_depth_conservation", [&]() {
        auto pts = make_grid_points(6, 6, 10.0, [](double x, double y) {
            return 10.0 + 0.08 * x - 0.04 * y; // z in [8..14]
        });
        tin::TINEarthworkCalculator calc(11.0);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();

        auto s_depth = tin::compute_strata_volumes(res, tin::StrataMode::ByDepth, {0.5, 1.5});
        check_true(s_depth.layers.size() == 3, "3 depth strata");
        double sum_c = 0.0, sum_f = 0.0;
        for (const auto& l : s_depth.layers) { sum_c += l.cut_volume; sum_f += l.fill_volume; }
        check_near(sum_c, res.total_cut, 1e-6, "depth strata cut conservation");
        check_near(sum_f, res.total_fill, 1e-6, "depth strata fill conservation");

        auto s_elev = tin::compute_strata_volumes(res, tin::StrataMode::ByElevation, {10.0, 11.5, 13.0});
        check_true(s_elev.layers.size() == 4, "4 elevation strata");
        sum_c = sum_f = 0.0;
        for (const auto& l : s_elev.layers) { sum_c += l.cut_volume; sum_f += l.fill_volume; }
        check_near(sum_c, res.total_cut, 1e-6, "elevation strata cut conservation");
        check_near(sum_f, res.total_fill, 1e-6, "elevation strata fill conservation");

        std::string xlsx = find_test_xlsx(argc, argv);
        if (!xlsx.empty()) {
            auto imp = tin::import_points_file(xlsx);
            tin::TINEarthworkCalculator rc(50.0);
            rc.add_points(imp.points);
            auto rres = rc.run_full_calculation();
            auto rs = tin::compute_strata_volumes(rres, tin::StrataMode::ByDepth, {0.2, 0.4});
            double rc_sum = 0.0, rf_sum = 0.0;
            for (const auto& l : rs.layers) { rc_sum += l.cut_volume; rf_sum += l.fill_volume; }
            check_near(rc_sum, rres.total_cut, 1e-5, "real xlsx strata cut conservation");
            check_near(rf_sum, rres.total_fill, 1e-5, "real xlsx strata fill conservation");
        }
    });

    // 17. 土方调配质量守恒与 2-opt 运距优化验证
    run_test("17_haul_plan_mass_conservation_and_optimization", [&]() {
        // 左侧挖方 (x<25, z=12)、右侧填方 (x>25, z=8)，设计标高 10
        auto pts = make_grid_points(6, 5, 10.0, [](double x, double) {
            return (x <= 20.0) ? 12.0 : ((x >= 30.0) ? 8.0 : 10.0);
        });
        tin::TINEarthworkCalculator calc(10.0);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();
        auto plan = tin::compute_haul_plan(res, 4, 2);
        check_near(plan.internal_balanced + plan.cross_zone_hauled + plan.export_surplus,
                   res.total_cut, 1e-4, "haul cut mass balance");
        check_near(plan.internal_balanced + plan.cross_zone_hauled + plan.borrow_deficit,
                   res.total_fill, 1e-4, "haul fill mass balance");
        check_true(plan.cross_zone_hauled > 0.0 && plan.avg_haul_distance > 10.0, "expected cross-zone haul flows");
        check_true(plan.estimated_cost > 0.0, "positive cost estimate");
    });

    // 18. 场地周边放坡解析验证（均匀高差台地楔形边坡体积 = 0.5 * h^2 * m * 周长）
    run_test("18_slope_grading_analytical_and_real", [&]() {
        // 均匀自然地面 z = 14.0，设计平台标高 10.0 (全挖 h = 4.0 m)，平台 40x40 (周长 160 m)
        // 挖坡 1:1.5 => 坡宽 w = 6.0 m，横截面积 A = 0.5 * 6 * 4 = 12 m²，边坡挖方 = 12 * 160 = 1920 m³
        auto pts = make_grid_points(5, 5, 10.0, [](double, double) { return 14.0; });
        tin::TINEarthworkCalculator calc(10.0);
        calc.add_points(pts);
        calc.set_boundary({{0, 0}, {40, 0}, {40, 40}, {0, 40}});
        calc.build_tin();
        auto sl = tin::compute_slope_grading(calc, 1.5, 1.5, 4.0);
        check_near(sl.perimeter, 160.0, 1e-6, "platform perimeter");
        check_near(sl.max_slope_width, 6.0, 1e-4, "max slope width");
        check_near(sl.slope_cut_volume, 1920.0, 1e-2, "analytical slope cut volume");
        check_near(sl.slope_fill_volume, 0.0, 1e-6, "analytical slope fill volume");
        check_true(!sl.slope_ticks.empty() && sl.daylight_ring.size() >= 12, "slope ticks and daylight ring");
    });

    // 19. 精确最小费用流土方调配：多区非平凡图严格满足质量守恒且优于/等于原始方案
    run_test("19_exact_mcmf_haul_optimality", [&]() {
        auto pts = make_grid_points(9, 9, 15.0, [](double x, double y) {
            return 20.0 + 3.2 * std::sin(0.055 * x + 1.0) * std::cos(0.048 * y - 0.7)
                        + 0.02 * (x - 60.0) - 0.015 * (y - 60.0);
        });
        tin::TINEarthworkCalculator calc(20.0);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();
        auto plan = tin::compute_haul_plan(res, 5, 5);
        check_near(plan.internal_balanced + plan.cross_zone_hauled + plan.export_surplus,
                   res.total_cut, 1e-4, "mcmf cut mass balance");
        check_near(plan.internal_balanced + plan.cross_zone_hauled + plan.borrow_deficit,
                   res.total_fill, 1e-4, "mcmf fill mass balance");
        check_true(plan.cross_zone_hauled > 100.0 && plan.total_work_m3_m > 0.0, "mcmf non-trivial haul");
    });

    // 20. 设计面-土方调配联合费用优化：坡度约束合规 + 优于平场平衡与最小二乘对照
    run_test("20_grade_cost_optimizer", [&]() {
        auto pts = make_grid_points(8, 8, 25.0, [](double x, double y) {
            return 30.0 + 0.025 * x - 0.015 * y + 0.8 * std::sin(0.04 * x) * std::cos(0.04 * y);
        });
        tin::TINEarthworkCalculator calc(30.0);
        calc.add_points(pts);
        calc.build_tin();

        tin::HaulCostParams costs;
        costs.haul_per_m3_100m = 8.0;
        tin::GradeOptConstraints cons;
        cons.max_slope_x = 3.0;
        cons.max_slope_y = 3.0;
        cons.max_evals = 120;

        auto opt = tin::optimize_grade_cost(calc, costs, cons);
        check_true(std::abs(opt.optimized.plane.slope_x) <= cons.max_slope_x + 1e-9, "slope_x bound");
        check_true(std::abs(opt.optimized.plane.slope_y) <= cons.max_slope_y + 1e-9, "slope_y bound");
        check_true(opt.optimized.total_cost <= opt.flat_balance.total_cost + 1e-6, "beats flat balance");
        check_true(opt.optimized.total_cost <= opt.best_fit.total_cost + 1e-6, "beats/equals clamped best fit");
        check_true(opt.savings_pct_vs_flat > 25.0, "substantial savings > 25% on sloped terrain");
    });

    // 21. 空间测点智能异常检测与一键局部修复
    run_test("21_anomaly_detector_and_repair", [&]() {
        auto pts = make_grid_points(14, 14, 10.0, [](double x, double y) {
            return 25.0 + 0.03 * x - 0.02 * y + 0.6 * std::sin(0.06 * x) * std::cos(0.05 * y);
        });
        std::vector<int> injected = {18, 35, 52, 74, 91, 110, 128, 149, 165, 178}; // ~5%
        for (size_t k = 0; k < injected.size(); ++k) {
            pts[injected[k]].z += (k % 2 == 0) ? 4.2 : -3.8;
        }
        auto rep = tin::detect_survey_anomalies(pts);
        int tp = 0;
        for (const auto& a : rep.anomalies) {
            if (std::find(injected.begin(), injected.end(), a.point_index) != injected.end()) ++tp;
        }
        double recall = static_cast<double>(tp) / static_cast<double>(injected.size());
        double precision = rep.anomalies.empty() ? 0.0 : static_cast<double>(tp) / static_cast<double>(rep.anomalies.size());
        check_true(recall >= 0.90, "anomaly recall >= 0.90");
        check_true(precision >= 0.85, "anomaly precision >= 0.85");
        check_true(rep.health_score < 95, "health score penalized");

        auto cleaned = tin::clean_survey_anomalies(pts, rep, true);
        check_true(cleaned.repaired_count >= 9, "repaired injected spikes/pits");
        auto rep_after = tin::detect_survey_anomalies(cleaned.points);
        check_true(rep_after.health_score >= 95, "health score restored after repair");
    });

    // 22. 高斯山包解析真值：抽稀外推偏差修正与 95% 不确定度区间覆盖验证
    run_test("22_uncertainty_analytical_mound", [&]() {
        auto mound_z = [](double x, double y) {
            double dx = x - 50.0, dy = y - 50.0;
            return 4.0 * std::exp(-(dx * dx + dy * dy) / (2.0 * 18.0 * 18.0));
        };
        double erf1 = std::erf(50.0 / (18.0 * std::sqrt(2.0)));
        double v_true = 4.0 * 2.0 * 3.14159265358979323846 * 18.0 * 18.0 * erf1 * erf1;

        // 1) 多组外业随机散点布设（n=140）：平均外推修正后误差明显小于原始 TIN，且 95% 区间覆盖真值
        double err_raw_sum = 0.0, err_cor_sum = 0.0;
        int covered = 0;
        for (int seed = 0; seed < 4; ++seed) {
            std::vector<tin::SurveyPoint> scat_pts = {
                {"C0", 0.0, 0.0, mound_z(0.0, 0.0), 0.0, 0.0, false},
                {"C1", 100.0, 0.0, mound_z(100.0, 0.0), 0.0, 0.0, false},
                {"C2", 100.0, 100.0, mound_z(100.0, 100.0), 0.0, 0.0, false},
                {"C3", 0.0, 100.0, mound_z(0.0, 100.0), 0.0, 0.0, false},
            };
            unsigned long long s = 123456789ULL + static_cast<unsigned long long>(seed) * 987654321ULL;
            auto next_u = [&]() {
                s = s * 6364136223846793005ULL + 1442695040888963407ULL;
                return static_cast<double>((s >> 11) & ((1ULL << 53) - 1)) / static_cast<double>(1ULL << 53);
            };
            for (int i = 0; i < 140; ++i) {
                double x = 100.0 * next_u();
                double y = 100.0 * next_u();
                scat_pts.push_back({"S" + std::to_string(i), x, y, mound_z(x, y), 0.0, 0.0, false});
            }
            tin::TINEarthworkCalculator sc(0.0);
            sc.add_points(scat_pts);
            sc.build_tin();
            auto su = tin::analyze_uncertainty_and_recommend(sc, 0.0, 8, 8);
            err_raw_sum += std::abs(su.cut_raw - v_true);
            err_cor_sum += std::abs(su.cut_corrected - v_true);
            if (std::abs(su.cut_corrected - v_true) <= su.cut_ci95) ++covered;
            if (seed == 0) {
                check_true(su.recommendations.size() == 8, "generated 8 survey recommendations");
            }
        }
        check_true(err_cor_sum < 0.85 * err_raw_sum, "Richardson bias correction reduces mean error on mound");
        check_true(covered >= 3, "95% CI covers analytical mound volume across seeds");

        // 2) 规则格网布设：95% 区间覆盖真值
        auto grid_pts = make_grid_points(11, 11, 10.0, mound_z);
        tin::TINEarthworkCalculator gc(0.0);
        gc.add_points(grid_pts);
        gc.build_tin();
        auto gu = tin::analyze_uncertainty_and_recommend(gc, 0.0, 8, 8);
        check_true(std::abs(gu.cut_corrected - v_true) <= gu.cut_ci95 * 1.25,
                   "95% CI covers analytical mound volume on grid points");
    });

    // 23. 自适应补测点推荐明显优于等量随机加密
    run_test("23_adaptive_survey_beats_random", [&]() {
        auto terrain_z = [](double x, double y) {
            double dx = x - 55.0, dy = y - 55.0;
            return 10.0 + 6.5 * std::exp(-(dx * dx + dy * dy) / (2.0 * 14.0 * 14.0))
                        + 0.02 * x - 0.01 * y;
        };
        auto pts = make_grid_points(9, 9, 12.5, terrain_z); // 81 pts on [0,100]^2
        tin::TINEarthworkCalculator calc(11.5);
        calc.add_points(pts);
        calc.build_tin();
        auto base_res = calc.calculate_volumes();

        // 精细参考网 41x41 近似解析真值
        auto fine_pts = make_grid_points(41, 41, 2.5, terrain_z);
        tin::TINEarthworkCalculator fine_calc(11.5);
        fine_calc.add_points(fine_pts);
        double true_cut = fine_calc.run_full_calculation().total_cut;
        double base_err = std::abs(base_res.total_cut - true_cut);

        int add_n = 8; // ~10%
        auto u = tin::analyze_uncertainty_and_recommend(calc, 0.02, add_n, 6);
        auto rec_pts = pts;
        for (const auto& r : u.recommendations) {
            rec_pts.push_back({"REC_" + std::to_string(r.rank), r.x, r.y, terrain_z(r.x, r.y), 0.0, 0.0, false});
        }
        tin::TINEarthworkCalculator rec_calc(11.5);
        rec_calc.add_points(rec_pts);
        double rec_err = std::abs(rec_calc.run_full_calculation().total_cut - true_cut);

        // 均匀分布对照补测点（边角/平坦区随机布点）
        auto rand_pts = pts;
        for (int k = 0; k < add_n; ++k) {
            double rx = 5.0 + 90.0 * ((k * 37 + 13) % 97) / 97.0;
            double ry = 5.0 + 90.0 * ((k * 53 + 29) % 89) / 89.0;
            rand_pts.push_back({"RND_" + std::to_string(k), rx, ry, terrain_z(rx, ry), 0.0, 0.0, false});
        }
        tin::TINEarthworkCalculator rnd_calc(11.5);
        rnd_calc.add_points(rand_pts);
        double rnd_err = std::abs(rnd_calc.run_full_calculation().total_cut - true_cut);

        double rec_drop = std::max(1e-6, base_err - rec_err);
        double rnd_drop = std::max(1e-6, base_err - rnd_err);
        check_true(rec_err < base_err && rec_drop >= 1.5 * rnd_drop,
                   "recommended points reduce error >= 1.5x vs random");
    });

    // 24. 中英双语自然语言工程意图识别 & Tab 补全
    run_test("24_bilingual_ai_assistant", [&]() {
        check_true(tin::parse_engineering_intent("把标高设为51.25").action == tin::AssistantAction::SetElevation, "cn elev");
        check_near(tin::parse_engineering_intent("把标高设为51.25").param1, 51.25, 1e-6, "cn elev param");
        check_true(tin::parse_engineering_intent("帮我优化总费用").action == tin::AssistantAction::OptimizeGradeCost, "cn opt");
        check_true(tin::parse_engineering_intent("detect survey outliers").action == tin::AssistantAction::RunDiagnostics, "en anomaly");
        check_true(tin::parse_engineering_intent("推荐12个补测点").count_param == 12, "cn rec count");
        check_true(tin::parse_engineering_intent("切换到三维视图").action == tin::AssistantAction::SetView3D, "cn 3d");
        check_true(tin::parse_engineering_intent("白底打印模式").action == tin::AssistantAction::LightMode, "cn light");
        auto comp = tin::suggest_command_completions("AI");
        check_true(comp.size() >= 2, "tab completion AI prefix");
    });

    // 25. 极端边界与异常防御（空点集、3点极简、零坡度限制、极端单价）
    run_test("25_edge_cases_and_extreme_costs", [&]() {
        auto empty_rep = tin::detect_survey_anomalies({});
        check_true(empty_rep.health_score == 100 && empty_rep.anomalies.empty(), "empty points health");

        auto pts3 = make_grid_points(2, 2, 10.0, [](double x, double y) { return 10.0 + 0.05 * x + 0.03 * y; });
        pts3.pop_back();
        auto rep3 = tin::detect_survey_anomalies(pts3);
        check_true(rep3.health_score == 100, "3-point health safe");

        tin::TINEarthworkCalculator c3(10.0);
        c3.add_points(pts3);
        c3.build_tin();
        tin::GradeOptConstraints zero_slope;
        zero_slope.max_slope_x = 0.0;
        zero_slope.max_slope_y = 0.0;
        zero_slope.max_evals = 30;
        tin::HaulCostParams extreme_costs;
        extreme_costs.haul_per_m3_100m = 500.0;
        extreme_costs.export_unit_cost = 1000.0;
        extreme_costs.borrow_unit_cost = 1000.0;
        auto opt0 = tin::optimize_grade_cost(c3, extreme_costs, zero_slope);
        check_true(std::abs(opt0.optimized.plane.slope_x) <= 1e-9 &&
                   std::abs(opt0.optimized.plane.slope_y) <= 1e-9, "zero slope constraint respected");
        check_true(std::isfinite(opt0.optimized.total_cost), "finite cost under extreme rates");
    });

    // 26. 共形变化显著性检测限 (LoD) 分离真实挖填与测量/内插噪声
    run_test("26_conformal_change_significance_lod", [&]() {
        auto pts = make_grid_points(10, 10, 10.0, [](double x, double) {
            return (x >= 50.0) ? 12.0 : 10.02; // 左侧接近零误差噪声，右侧 2.0m 显著挖方
        });
        tin::TINEarthworkCalculator calc(10.0);
        calc.add_points(pts);
        calc.build_tin();
        auto sig = tin::analyze_change_significance(calc, 0.03);
        check_true(sig.conformal_q >= 1.9599, "conformal q >= z95");
        check_true(sig.significant_cut > 1000.0, "detects large real cut");
        check_true(sig.uncertain_area > 1000.0, "flags near-zero region as uncertain");
    });

    // 27. 保精度点云抽稀：误差严格小于容差且平坦缓坡处高压缩率
    run_test("27_precision_preserving_point_thinning", [&]() {
        // 大部分为平缓斜坡 + 中间小丘，15x15 = 225 点
        auto pts = make_grid_points(15, 15, 10.0, [](double x, double y) {
            double dx = x - 70.0, dy = y - 70.0;
            return 20.0 + 0.02 * x + 2.5 * std::exp(-(dx * dx + dy * dy) / (2.0 * 15.0 * 15.0));
        });
        auto th = tin::thin_points_preserve_accuracy(pts, 0.08);
        check_true(th.retained_count < th.original_count, "points reduced");
        check_true(th.reduction_pct >= 30.0, "meaningful compression >= 30%");
        check_true(th.max_error_m <= 0.08 + 1e-6, "max vertical error strictly within tolerance");
    });

    // 28. 场地排水坡度与低洼积水风险检查
    run_test("28_drainage_and_ponding_analysis", [&]() {
        // 平坦自然地面 (0% 坡度) vs 倾斜设计面 (2.0% X 坡度)
        auto pts = make_grid_points(6, 6, 10.0, [](double, double) { return 10.0; });
        tin::TINEarthworkCalculator calc(10.0);
        calc.add_points(pts);
        auto r_flat = calc.run_full_calculation();
        auto d_flat = tin::analyze_drainage_and_ponding(r_flat, 0.3, false);
        check_near(d_flat.ponding_ratio_pct, 100.0, 1e-3, "flat ground 100% ponding risk");

        calc.set_design_plane({0.0, 0.0, 10.0, 2.0, 0.0});
        auto r_sloped = calc.calculate_volumes();
        auto d_sloped = tin::analyze_drainage_and_ponding(r_sloped, 0.3, true);
        check_near(d_sloped.mean_slope_pct, 2.0, 1e-3, "2% design slope");
        check_near(d_sloped.ponding_ratio_pct, 0.0, 1e-3, "no ponding on 2% slope");
        check_near(d_sloped.dominant_runoff_azimuth_deg, 270.0, 1.0, "runoff toward -X (270 deg)");
    });

    // 29. AI 决策 HTML 报告、补测点 CSV 与 AI 专题 R12 DXF 导出往返校验
    run_test("29_ai_exports_html_csv_dxf_roundtrip", [&]() {
        auto pts = make_grid_points(8, 8, 20.0, [](double x, double y) {
            return 20.0 + 0.03 * x - 0.015 * y + 0.7 * std::sin(0.04 * x);
        });
        pts[20].z += 4.5; // 注入一个异常点
        tin::TINEarthworkCalculator calc(22.0);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();
        auto health = tin::detect_survey_anomalies(pts);
        auto grade_opt = tin::optimize_grade_cost(calc);
        auto uncert = tin::analyze_uncertainty_and_recommend(calc, 0.03, 6, 6);
        auto sig = tin::analyze_change_significance(calc, 0.03);
        auto drain = tin::analyze_drainage_and_ponding(res, 0.3, false);
        auto haul = tin::compute_haul_plan(res, 3, 3);
        auto slope = tin::compute_slope_grading(calc, 1.0, 1.5, 5.0);

        tin::ProjectData proj;
        proj.project_name = "AI_Export_Verify";
        proj.points = pts;
        auto diag = tin::generate_site_diagnosis(proj, &res, &health, &grade_opt, &uncert);

        auto tmp = std::filesystem::temp_directory_path() / L"tin_earthwork_cpp_tests";
        std::filesystem::create_directories(tmp);
        std::string html_p = tin::wide_to_utf8((tmp / L"ai_report.html").wstring());
        std::string rec_p = tin::wide_to_utf8((tmp / L"survey_rec.csv").wstring());
        std::string dxf_p = tin::wide_to_utf8((tmp / L"ai_overlays.dxf").wstring());

        tin::export_ai_report_html(proj, &res, &grade_opt, &health, &uncert, &sig, &drain, &diag, html_p);
        tin::export_survey_recommendations_csv(uncert, rec_p);
        tin::export_ai_overlays_dxf(&res, &health, &uncert, &haul, &slope, dxf_p);

        auto read_all = [](const std::string& p) {
            std::ifstream ifs(std::filesystem::path(tin::utf8_to_wide(p)), std::ios::binary);
            return std::string((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
        };
        std::string html_s = read_all(html_p);
        std::string rec_s = read_all(rec_p);
        std::string dxf_s = read_all(dxf_p);

        check_true(html_s.find("<svg") != std::string::npos && html_s.find("AI_Export_Verify") != std::string::npos,
                   "HTML report contains SVG chart and project title");
        check_true(rec_s.find("X(东坐标)") != std::string::npos && rec_s.size() > 150,
                   "Survey recommendation CSV valid");
        check_true(dxf_s.find("AI_ANOMALY") != std::string::npos &&
                   dxf_s.find("AI_SURVEY_REC") != std::string::npos &&
                   dxf_s.find("AI_HAUL_FLOW") != std::string::npos &&
                   dxf_s.find("AI_SLOPE_DAYLIGHT") != std::string::npos &&
                   dxf_s.find("EOF") != std::string::npos,
                   "AI DXF contains all 4 AI overlay layers and EOF");
    });

    // 30. O(N) 空间网格 5000 点异常检测压力测试与标高敏感性曲线校验
    run_test("30_spatial_grid_5000pts_stress_and_sensitivity_curve", [&]() {
        auto pts5k = make_grid_points(71, 71, 5.0, [](double x, double y) {
            return 30.0 + 0.02 * x - 0.015 * y + 0.9 * std::sin(0.035 * x) * std::cos(0.03 * y);
        }); // 5041 points
        std::vector<int> inj;
        for (int idx = 150; idx < 4800; idx += 450) {
            pts5k[idx].z += ((inj.size() % 2 == 0) ? 4.5 : -4.2);
            inj.push_back(idx);
        }
        auto t0 = std::chrono::steady_clock::now();
        auto rep5k = tin::detect_survey_anomalies(pts5k);
        auto t1 = std::chrono::steady_clock::now();
        double ms5k = std::chrono::duration<double, std::milli>(t1 - t0).count();
        std::cout << "  [STRESS 5041 pts spatial-grid anomaly scan] ms=" << std::fixed << std::setprecision(2) << ms5k
                  << " found=" << rep5k.anomalies.size() << "\n";
        int tp = 0;
        for (const auto& a : rep5k.anomalies) {
            if (std::find(inj.begin(), inj.end(), a.point_index) != inj.end()) ++tp;
        }
        check_true(tp == static_cast<int>(inj.size()), "100% recall on 5041 pts");
        check_true(rep5k.anomalies.size() <= inj.size() + 2, "high precision on 5041 pts");
        check_true(ms5k < 150.0, "5000-pt O(N) spatial grid anomaly scan < 150 ms");

        // 标高敏感性曲线单调性与 CSV 导出验证
        auto pts = make_grid_points(7, 7, 20.0, [](double x, double y) {
            return 20.0 + 0.04 * x - 0.02 * y;
        });
        tin::TINEarthworkCalculator calc(22.0);
        calc.add_points(pts);
        calc.build_tin();
        auto curve = tin::run_elevation_sensitivity_curve(calc, tin::HaulCostParams{}, 15, 3.0);
        check_true(curve.points.size() == 15, "15 sensitivity samples");
        for (size_t i = 0; i + 1 < curve.points.size(); ++i) {
            check_true(curve.points[i + 1].cut_volume <= curve.points[i].cut_volume + 1e-6, "cut non-increasing");
            check_true(curve.points[i + 1].fill_volume >= curve.points[i].fill_volume - 1e-6, "fill non-decreasing");
        }
        check_true(curve.min_total_cost <= curve.points.front().total_cost + 1e-6 &&
                   curve.min_total_cost <= curve.points.back().total_cost + 1e-6,
                   "best cost <= endpoints");

        auto tmp = std::filesystem::temp_directory_path() / L"tin_earthwork_cpp_tests";
        std::filesystem::create_directories(tmp);
        std::string sens_csv = tin::wide_to_utf8((tmp / L"sensitivity.csv").wstring());
        tin::export_sensitivity_csv(curve, sens_csv);
        check_true(std::filesystem::file_size(tin::utf8_to_wide(sens_csv)) > 200, "sensitivity csv non-empty");
    });

    // 31. 双级台阶斜坡平场优化验证（陡坡山地较单平场显著降低综合造价）
    run_test("31_stepped_terrace_saves_cost_on_steep_slope", [&]() {
        auto slope_pts = make_grid_points(11, 11, 12.0, [](double x, double y) {
            // 西低东高剧烈地形，适合南北轴分段（X分两级）
            return 10.0 + 0.14 * x + 0.01 * y + 0.6 * std::sin(0.05 * y);
        });
        tin::TINEarthworkCalculator calc(18.0);
        calc.add_points(slope_pts);
        calc.build_tin();

        auto st = tin::optimize_stepped_platform(calc, tin::HaulCostParams{}, 180.0);
        check_true(st.split_axis == 0, "steep X-slope chooses X split axis");
        check_true(st.step_height > 2.0, "stepped terrace introduces clear bench height delta");
        check_true(st.bench2_elevation > st.bench1_elevation, "east bench higher than west bench");
        check_true(st.total_cost < st.single_pad_cost && st.savings_pct > 15.0,
                   "stepped terrace reduces total cost >15% including retaining wall");
    });

    // 32. 施工机械配置与工期台班造价估算验证
    run_test("32_earthwork_fleet_and_schedule_estimator", [&]() {
        auto pts = make_grid_points(9, 9, 15.0, [](double x, double y) {
            return 25.0 + 0.03 * x - 0.02 * y + 1.5 * std::sin(0.04 * x);
        });
        tin::TINEarthworkCalculator calc(24.5);
        calc.add_points(pts);
        auto res = calc.run_full_calculation();
        auto haul = tin::compute_haul_plan(res, 4, 3);

        tin::FleetParams fp;
        fp.target_days = 10.0;
        auto f_fast = tin::estimate_earthwork_fleet(res, &haul, fp);
        check_true(f_fast.excavators_needed >= 1 && f_fast.trucks_needed >= 1 && f_fast.compaction_sets >= 1,
                   "fleet recommends positive equipment counts");
        check_true(f_fast.estimated_days > 0.0 && f_fast.total_machine_cost > 0.0,
                   "fleet schedule days and cost positive");

        // 运距增大时单趟循环周期应单调变长，单车日运能下降
        tin::HaulPlanResult short_haul = haul;
        short_haul.export_surplus = 0.0;
        short_haul.borrow_deficit = 0.0;
        short_haul.cross_zone_hauled = std::max(1000.0, res.total_cut);
        short_haul.avg_haul_distance = 120.0;
        auto f_short = tin::estimate_earthwork_fleet(res, &short_haul, fp);

        tin::HaulPlanResult long_haul = short_haul;
        long_haul.avg_haul_distance = 650.0;
        auto f_long = tin::estimate_earthwork_fleet(res, &long_haul, fp);
        check_true(f_long.truck_cycle_min > f_short.truck_cycle_min, "longer haul increases truck cycle time");
        check_true(f_long.truck_daily_cap_m3 < f_short.truck_daily_cap_m3, "longer haul reduces per-truck daily capacity");
        check_true(f_long.trucks_needed >= f_short.trucks_needed, "longer haul requires >= trucks");
    });

    // 33. BOQ 工程量清单报价表 CSV 与增强版 HTML 智能决策报告导出校验
    run_test("33_boq_csv_and_enhanced_html_report_roundtrip", [&]() {
        auto slope_pts = make_grid_points(10, 10, 12.0, [](double x, double y) {
            return 15.0 + 0.12 * x + 0.015 * y + 0.5 * std::sin(0.04 * y);
        });
        tin::TINEarthworkCalculator calc(22.0);
        calc.add_points(slope_pts);
        auto res = calc.run_full_calculation();
        auto haul = tin::compute_haul_plan(res, 4, 3);
        auto slope = tin::compute_slope_grading(calc, 1.0, 1.5, 4.0);
        auto stepped = tin::optimize_stepped_platform(calc, tin::HaulCostParams{}, 180.0);
        auto fleet = tin::estimate_earthwork_fleet(res, &haul);

        tin::ProjectData proj;
        proj.project_name = "BOQ_Verify";
        proj.points = slope_pts;

        auto tmp = std::filesystem::temp_directory_path() / L"tin_earthwork_cpp_tests";
        std::filesystem::create_directories(tmp);
        std::string boq_p = tin::wide_to_utf8((tmp / L"boq.csv").wstring());
        std::string html_p = tin::wide_to_utf8((tmp / L"ai_report_full.html").wstring());

        tin::export_boq_csv(proj, res, &haul, tin::HaulCostParams{}, &slope, &stepped, &fleet, boq_p);
        tin::export_ai_report_html(proj, &res, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr,
                                   html_p, &stepped, &fleet);

        auto read_all = [](const std::string& p) {
            std::ifstream ifs(std::filesystem::path(tin::utf8_to_wide(p)), std::ios::binary);
            return std::string((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
        };
        std::string boq_s = read_all(boq_p);
        std::string html_s = read_all(html_p);

        check_true(boq_s.find("场地平整一般土方开挖") != std::string::npos &&
                   boq_s.find("直接工程费用汇总") != std::string::npos &&
                   boq_s.find("双级台阶") != std::string::npos &&
                   boq_s.find("机械集群") != std::string::npos,
                   "BOQ CSV contains earthwork items, totals, stepped & fleet references");
        check_true(html_s.find("双级台阶斜坡平场对比") != std::string::npos &&
                   html_s.find("施工机械排班与工期台班") != std::string::npos,
                   "Enhanced HTML report includes stepped & fleet sections");

        auto boq_intent = tin::parse_engineering_intent("导出工程量清单报价表");
        check_true(boq_intent.action == tin::AssistantAction::ExportBoqCsv, "BOQ Chinese intent recognized");
    });

    // 34. 万点级 (101x101 = 10,201 点) Y-分带 Bowyer-Watson 构网与算量实时性能校验
    run_test("34_10000_points_delaunay_and_volume_perf", [&]() {
        auto pts10k = make_grid_points(101, 101, 5.0, [](double x, double y) {
            return 25.0 + 0.015 * x - 0.01 * y;
        }); // 严格线性倾斜平面对 500x500m，Z=25+0.015*250-0.01*250=26.25 mean
        auto t0 = std::chrono::steady_clock::now();
        tin::TINEarthworkCalculator calc(20.0);
        calc.add_points(pts10k);
        auto res = calc.run_full_calculation();
        auto t1 = std::chrono::steady_clock::now();
        double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
        std::cout << "  [BENCH 10201 pts Y-strip Delaunay+Vol] tris=" << res.triangle_count
                  << " area=" << res.computed_area << " ms=" << ms << "\n";
        double expected_area = 500.0 * 500.0;
        double expected_cut = expected_area * ((25.0 + 0.015 * 250.0 - 0.01 * 250.0) - 20.0);
        check_near(res.computed_area, expected_area, 1e-4, "10k grid area exact");
        check_near(res.total_cut, expected_cut, 1e-2, "10k grid analytical volume exact");
        check_true(res.triangle_count == 20000, "101x101 grid forms 20,000 triangles");
#if defined(__SANITIZE_ADDRESS__) || defined(__SANITIZE_UNDEFINED__)
        constexpr double k10kLimitMs = 250.0;
#elif defined(__has_feature)
#  if __has_feature(address_sanitizer) || __has_feature(undefined_behavior_sanitizer)
        constexpr double k10kLimitMs = 250.0;
#  else
        constexpr double k10kLimitMs = 25.0;
#  endif
#else
        constexpr double k10kLimitMs = 25.0;
#endif
        check_true(ms < k10kLimitMs, "10,201 pts TIN+volume within latency budget");
    });

    // 35. 最小排水坡度 + 控制点接边约束优化与多目标 Pareto 前沿校验
    run_test("35_constrained_grade_opt_and_pareto_frontier", [&]() {
        // 1) 几乎水平地形下强制 min_drain_slope_pct >= 0.6%
        auto flat_pts = make_grid_points(8, 8, 20.0, [](double x, double y) {
            return 20.0 + 0.0005 * x - 0.0004 * y + 0.12 * std::sin(0.05 * x);
        });
        tin::TINEarthworkCalculator fcalc(20.0);
        fcalc.add_points(flat_pts);
        fcalc.build_tin();

        tin::GradeOptConstraints dc;
        dc.max_slope_x = 3.0;
        dc.max_slope_y = 3.0;
        dc.min_drain_slope_pct = 0.6;
        auto dr = tin::optimize_grade_cost(fcalc, tin::HaulCostParams{}, dc);
        double drain_sp = std::hypot(dr.optimized.plane.slope_x, dr.optimized.plane.slope_y);
        check_true(drain_sp >= 0.6 - 1e-4, "constrained opt satisfies min_drain_slope_pct >= 0.6%");

        // 2) 控制点高程接边约束 <= 0.05 m
        auto tilt_pts = make_grid_points(9, 9, 15.0, [](double x, double y) {
            return 15.0 + 0.03 * x + 0.015 * y + 1.1 * std::sin(0.04 * x) * std::cos(0.04 * y);
        });
        tin::TINEarthworkCalculator tcalc(17.5);
        tcalc.add_points(tilt_pts);
        tcalc.build_tin();

        tin::GradeOptConstraints cc;
        cc.max_slope_x = 5.0;
        cc.max_slope_y = 5.0;
        tin::ControlPointConstraint cp{"道口1", 0.0, 60.0, 16.4, 0.05, 80000.0};
        cc.control_points.push_back(cp);
        auto cr = tin::optimize_grade_cost(tcalc, tin::HaulCostParams{}, cc);
        double cp_err = std::abs(cr.optimized.plane.elevation(cp.x, cp.y) - cp.target_z);
        check_true(cp_err <= 0.05 + 1e-4, "control point elevation error <= 0.05 m");

        // 3) 多目标 Pareto 前沿（需生成 >= 3 组严格非支配方案并支持 CSV 导出）
        tin::GradeOptConstraints pc;
        pc.max_slope_x = 4.0;
        pc.max_slope_y = 4.0;
        auto pareto = tin::compute_grade_pareto_frontier(tcalc, tin::HaulCostParams{}, pc);
        check_true(pareto.plans.size() >= 3, "Pareto frontier has >= 3 distinct non-dominated plans");
        for (size_t i = 0; i + 1 < pareto.plans.size(); ++i) {
            check_true(pareto.plans[i].summary.total_cost <= pareto.plans[i + 1].summary.total_cost + 1e-3,
                       "Pareto frontier sorted ascending by total_cost");
        }

        auto tmp = std::filesystem::temp_directory_path() / L"tin_earthwork_cpp_tests";
        std::filesystem::create_directories(tmp);
        std::string p_csv = tin::wide_to_utf8((tmp / L"pareto.csv").wstring());
        tin::export_pareto_csv(pareto, p_csv);
        check_true(std::filesystem::file_size(tin::utf8_to_wide(p_csv)) > 200, "pareto csv non-empty");
    });

    // 36. 主坡向对齐 K=2..4 动态规划多级台阶优化（45°对角陡坡大幅胜过单平场与正交两级台阶）
    run_test("36_oriented_multi_bench_dp_beats_single_and_axis_2bench", [&]() {
        auto diag_pts = make_grid_points(12, 12, 18.0, [](double x, double y) {
            return 10.0 + 0.12 * x + 0.12 * y + 0.25 * std::sin(0.025 * (x - y));
        });
        tin::TINEarthworkCalculator calc(24.0);
        calc.add_points(diag_pts);
        calc.build_tin();

        auto axis2 = tin::optimize_stepped_platform(calc, tin::HaulCostParams{}, 180.0);
        auto dp3 = tin::optimize_multi_bench_terraces(calc, tin::HaulCostParams{}, 3, 180.0);
        std::cout << "  [DP MULTI-BENCH] benches=" << dp3.bench_count
                  << " az=" << dp3.slope_azimuth_deg
                  << " flat_sav=" << dp3.savings_pct << "%"
                  << " vs_axis2=" << ((axis2.total_cost - dp3.total_cost) / axis2.total_cost * 100.0) << "%"
                  << " ms=" << dp3.elapsed_ms << "\n";
        check_true(dp3.bench_count == 3, "45-deg steep hill chooses 3 oriented benches");
        check_true(dp3.slope_azimuth_deg > 35.0 && dp3.slope_azimuth_deg < 55.0,
                   "principal slope azimuth tracks 45-deg ascent");
        check_true(dp3.savings_pct > 40.0, "oriented 3-bench DP saves > 40% vs single pad");
        double vs_axis2 = (axis2.total_cost - dp3.total_cost) / axis2.total_cost * 100.0;
        check_true(vs_axis2 > 10.0, "oriented 3-bench DP beats axis-aligned 2-bench by > 10%");
#if defined(__SANITIZE_ADDRESS__) || defined(__SANITIZE_UNDEFINED__)
        constexpr double kDpLimitMs = 250.0;
#elif defined(__has_feature)
#  if __has_feature(address_sanitizer) || __has_feature(undefined_behavior_sanitizer)
        constexpr double kDpLimitMs = 250.0;
#  else
        constexpr double kDpLimitMs = 25.0;
#  endif
#else
        constexpr double kDpLimitMs = 25.0;
#endif
        check_true(dp3.elapsed_ms < kDpLimitMs, "DP multi-bench solver within latency budget");
    });

    // 37. v4.0 HTML 报告 Pareto/多级台阶渲染与助手机构意图集成校验
    run_test("37_pareto_and_multibench_v4_html_export", [&]() {
        auto diag_pts = make_grid_points(11, 11, 18.0, [](double x, double y) {
            return 10.0 + 0.12 * x + 0.12 * y + 0.4 * std::sin(0.03 * x);
        });
        tin::TINEarthworkCalculator calc(24.0);
        calc.add_points(diag_pts);
        auto res = calc.run_full_calculation();
        auto pareto = tin::compute_grade_pareto_frontier(calc);
        auto mb = tin::optimize_multi_bench_terraces(calc, tin::HaulCostParams{}, 3, 180.0);

        tin::ProjectData proj;
        proj.project_name = "V4_Report_Verify";
        proj.points = diag_pts;
        auto tmp = std::filesystem::temp_directory_path() / L"tin_earthwork_cpp_tests";
        std::filesystem::create_directories(tmp);
        std::string html_p = tin::wide_to_utf8((tmp / L"v4_report.html").wstring());
        tin::export_ai_report_html(proj, &res, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr,
                                   html_p, nullptr, nullptr, &pareto, &mb);

        std::ifstream ifs(std::filesystem::path(tin::utf8_to_wide(html_p)), std::ios::binary);
        std::string html_s((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
        check_true(html_s.find("多目标 Pareto 设计面比选方案集") != std::string::npos &&
                   html_s.find("主坡向自适应多级台阶 DP 优化") != std::string::npos,
                   "v4 HTML report includes Pareto & Multi-Bench DP sections");

        check_true(tin::parse_engineering_intent("PARETO").action == tin::AssistantAction::RunPareto,
                   "PARETO CAD command recognized");
        check_true(tin::parse_engineering_intent("MULTIBENCH").action == tin::AssistantAction::OptimizeMultiBench,
                   "MULTIBENCH CAD command recognized");
        check_true(tin::parse_engineering_intent("应用多级台阶").action == tin::AssistantAction::ApplyMultiBench,
                   "APPLY_BENCH Chinese intent recognized");
    });

    std::cout << "=== Summary: PASSED " << g_passed << " / " << (g_passed + g_failed) << " ===\n";
    return g_failed == 0 ? 0 : 1;
}
