#pragma once

#include "core/calculator.hpp"
#include "core/engineering.hpp"
#include "core/types.hpp"
#include <string>
#include <vector>

namespace tin {

// ---------- 1. 设计面-土方调配联合费用优化 ----------
struct GradeOptConstraints {
    double max_slope_x = 5.0; // 最大允许 X 坡度绝对值 (%)
    double max_slope_y = 5.0; // 最大允许 Y 坡度绝对值 (%)
    int haul_cols = 4;
    int haul_rows = 3;
    int max_evals = 120;
};

struct GradePlanSummary {
    std::string label;
    PlaneDesign plane;
    double cut_volume = 0.0;
    double fill_volume = 0.0;
    double haul_work_m3_m = 0.0;
    double export_surplus = 0.0;
    double borrow_deficit = 0.0;
    double total_cost = 0.0;
};

struct GradeOptimizationResult {
    GradePlanSummary flat_balance;
    GradePlanSummary best_fit;
    GradePlanSummary optimized;
    double savings_vs_flat = 0.0;
    double savings_pct_vs_flat = 0.0;
    double savings_vs_best_fit = 0.0;
    double savings_pct_vs_best_fit = 0.0;
    int evaluations = 0;
    double elapsed_ms = 0.0;
    bool converged = false;
};

GradeOptimizationResult optimize_grade_cost(
    const TINEarthworkCalculator& base_calc,
    const HaulCostParams& costs = HaulCostParams{},
    const GradeOptConstraints& constraints = GradeOptConstraints{});

} // namespace tin
