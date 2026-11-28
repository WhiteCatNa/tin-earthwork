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

// ---------- 2. 空间测点智能异常诊断与局部自动修复 ----------
enum class AnomalyType {
    ElevationSpike = 0, // 高程突刺（正粗差）
    ElevationPit,       // 高程深坑（负粗差）
    CoordOutlier,       // 孤立飞点（坐标离群）
    NearDuplicate       // 重合点高程冲突
};

struct PointAnomaly {
    int point_index = -1;
    std::string point_id;
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
    double expected_z = 0.0;
    double residual = 0.0;
    double z_score = 0.0;
    AnomalyType type = AnomalyType::ElevationSpike;
    int severity = 1; // 1=一般警示, 2=严重粗差
    std::string reason;
};

struct SiteHealthReport {
    int health_score = 100; // 0 ~ 100
    int total_points = 0;
    double median_spacing = 0.0;
    double z_min = 0.0;
    double z_max = 0.0;
    double z_std = 0.0;
    std::vector<PointAnomaly> anomalies;
    std::string summary;
};

struct CleanPointsResult {
    std::vector<SurveyPoint> points;
    int repaired_count = 0;
    int removed_count = 0;
};

SiteHealthReport detect_survey_anomalies(const std::vector<SurveyPoint>& points);

CleanPointsResult clean_survey_anomalies(
    const std::vector<SurveyPoint>& points,
    const SiteHealthReport& report,
    bool interpolate_z = true);

// ---------- 3. 抽稀外推不确定度评定与自适应补测点推荐 ----------
struct SurveyRecommendation {
    int rank = 0;
    double x = 0.0;
    double y = 0.0;
    double estimated_z = 0.0;
    double error_scale = 0.0;
    double expected_error_drop = 0.0;
    std::string reason;
};

struct UncertaintyReport {
    double cut_raw = 0.0;
    double cut_corrected = 0.0;
    double cut_ci95 = 0.0;
    double fill_raw = 0.0;
    double fill_corrected = 0.0;
    double fill_ci95 = 0.0;
    double net_raw = 0.0;
    double net_corrected = 0.0;
    double net_ci95 = 0.0;
    double cut_rel_ci95_pct = 0.0;
    double fill_rel_ci95_pct = 0.0;
    double measure_sigma = 0.03;
    int subsample_runs = 0;
    double elapsed_ms = 0.0;
    std::vector<SurveyRecommendation> recommendations;
    std::string summary;
};

UncertaintyReport analyze_uncertainty_and_recommend(
    const TINEarthworkCalculator& base_calc,
    double measure_sigma = 0.03,
    int recommend_count = 8,
    int subsample_runs = 8);

// ---------- 4. 共形标定变化显著性 (Spatially Varying LoD) ----------
struct SignificanceReport {
    double confidence = 0.95;
    double conformal_q = 1.959964;
    double significant_cut = 0.0;
    double significant_fill = 0.0;
    double uncertain_cut = 0.0;
    double uncertain_fill = 0.0;
    double uncertain_area = 0.0;
    double total_area = 0.0;
    double uncertain_ratio = 0.0;
    std::string summary;
};

SignificanceReport analyze_change_significance(
    const TINEarthworkCalculator& base_calc,
    double measure_sigma = 0.03);

// ---------- 5. 保精度点云抽稀 (Tolerance-Bounded TIN Decimation) ----------
struct ThinningResult {
    std::vector<SurveyPoint> points;
    int original_count = 0;
    int retained_count = 0;
    double reduction_pct = 0.0;
    double max_error_m = 0.0;
    double tolerance_m = 0.15;
    std::string summary;
};

ThinningResult thin_points_preserve_accuracy(
    const std::vector<SurveyPoint>& points,
    double tolerance_m = 0.15);

// ---------- 6. 场地排水坡度与低洼积水风险诊断 ----------
struct DrainageReport {
    double min_required_slope_pct = 0.3;
    double mean_slope_pct = 0.0;
    double min_slope_pct = 0.0;
    double max_slope_pct = 0.0;
    double ponding_area = 0.0;
    double total_area = 0.0;
    double ponding_ratio_pct = 0.0;
    double dominant_runoff_azimuth_deg = 0.0;
    std::vector<int> ponding_triangle_ids;
    std::string summary;
};

DrainageReport analyze_drainage_and_ponding(
    const CalculationResult& result,
    double min_slope_pct = 0.3,
    bool use_design_surface = true);

// ---------- 7. 设计标高-方量/综合费用敏感性曲线 ----------
struct SensitivityPoint {
    double elevation = 0.0;
    double cut_volume = 0.0;
    double fill_volume = 0.0;
    double net_volume = 0.0;
    double total_cost = 0.0;
};

struct SensitivityCurveResult {
    double center_elevation = 0.0;
    double best_cost_elevation = 0.0;
    double min_total_cost = 0.0;
    std::vector<SensitivityPoint> points;
    std::string summary;
};

SensitivityCurveResult run_elevation_sensitivity_curve(
    const TINEarthworkCalculator& base_calc,
    const HaulCostParams& costs = HaulCostParams{},
    int steps = 17,
    double half_span_m = 0.0);

} // namespace tin
