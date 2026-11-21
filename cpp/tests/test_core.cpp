#include "core/analysis.hpp"
#include "core/calculator.hpp"
#include "core/delaunay.hpp"
#include "core/geometry.hpp"
#include "io/io.hpp"

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

    std::cout << "=== Summary: PASSED " << g_passed << " / " << (g_passed + g_failed) << " ===\n";
    return g_failed == 0 ? 0 : 1;
}
