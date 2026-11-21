#pragma once

#include "core/calculator.hpp"
#include "core/types.hpp"
#include <vector>

namespace tin {

// 土方零平衡高程解析求解（支持可松性压实系数 Ks'）
BalanceResult solve_balance_elevation(
    const TINEarthworkCalculator& calculator,
    const SoilFactors& factors = SoilFactors{});

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
