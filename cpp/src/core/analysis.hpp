#pragma once

#include "core/calculator.hpp"
#include "core/types.hpp"
#include <vector>

namespace tin {

// 土方零平衡高程解析求解（支持可松性压实系数 Ks'）
BalanceResult solve_balance_elevation(
    const TINEarthworkCalculator& calculator,
    const SoilFactors& factors = SoilFactors{});

// 保持指定 X/Y 排水坡度，求解挖填平衡斜面基准高程 H0
PlaneBalanceResult solve_balance_plane(
    const TINEarthworkCalculator& calculator,
    double x0, double y0, double slope_x, double slope_y,
    const SoilFactors& factors = SoilFactors{});

// 面积加权最小二乘最优斜面（最小化总挖填量，并自动调平至挖填平衡）
PlaneBalanceResult solve_best_fit_plane(
    const TINEarthworkCalculator& calculator,
    const SoilFactors& factors = SoilFactors{});

// 平行横断面法批量土方计算（平均断面法，支持任意推进方位角）
SectionMethodResult run_sections(
    TINEarthworkCalculator& calculator,
    double spacing = 20.0,
    double azimuth_deg = 0.0,
    int samples_per_section = 180);

// 方格网自动建议边长（控制格数 <= 2500）
double suggest_grid_spacing(double width, double height);

// 方格网法校核（角点由 TIN 线性插值，每格按两三角形精确裁剪）
GridCheckResult run_grid_check(
    const TINEarthworkCalculator& calculator,
    const CalculationResult& tin_result,
    double spacing);

// TIN 等高线自动推荐等高距与追踪（含首曲线/计曲线分类与多段线连接）
double suggest_contour_interval(double z_min, double z_max);
ContourSet extract_elevation_contours(
    const CalculationResult& result,
    double interval = 0.0,
    int major_step = 5);

// 任意起终点纵断面高程采样与断面挖填面积积分
ProfileResult sample_profile(
    TINEarthworkCalculator& calculator,
    const XY& start,
    const XY& end,
    int samples = 240);

} // namespace tin
