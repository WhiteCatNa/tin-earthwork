#pragma once

#include "core/calculator.hpp"
#include "core/engineering.hpp"
#include "core/types.hpp"
#include <string>
#include <vector>

namespace tin {

// ---------- 1. 设计面-土方调配联合费用优化与多约束多目标 Pareto ----------
struct ControlPointConstraint {
    std::string label;
    double x = 0.0;
    double y = 0.0;
    double target_z = 0.0;
    double max_dev_m = 0.10;   // 允许接边最大高差 (m)
    double weight = 80000.0;   // 超限惩罚系数 (元/m)
};

struct GradeOptConstraints {
    double max_slope_x = 5.0;        // 最大允许 X 坡度绝对值 (%)
    double max_slope_y = 5.0;        // 最大允许 Y 坡度绝对值 (%)
    double min_drain_slope_pct = 0.0; // 最小合成排水坡度 (%)，0 表示不限
    int haul_cols = 4;
    int haul_rows = 3;
    int max_evals = 120;
    std::vector<ControlPointConstraint> control_points;
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

struct ParetoPlan {
    std::string tag;
    GradePlanSummary summary;
    double total_moved_volume = 0.0; // 总挖填体积 cut + fill (m³)
    double max_cut_depth = 0.0;      // 最大挖深 (m)
    double max_fill_depth = 0.0;     // 最大填高 (m)
    double slope_pct = 0.0;          // 合成坡度 (%)
};

struct GradeParetoResult {
    std::vector<ParetoPlan> plans;   // 按总造价升序排列的非支配方案集
    std::string summary;
};

GradeParetoResult compute_grade_pareto_frontier(
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

// ---------- 8. 双级台阶斜坡平场联合优化（减少大高差场地挖填成本） ----------
struct SteppedPlatformResult {
    int split_axis = 0;            // 0=沿X坐标分段(西/东台阶), 1=沿Y坐标分段(南/北台阶)
    double split_coord = 0.0;      // 分段界线坐标 (m)
    double split_length = 0.0;     // 台阶挡墙/护坡接触线长度 (m)
    double bench1_elevation = 0.0; // 分界线低坐标侧设计标高 (m)
    double bench2_elevation = 0.0; // 分界线高坐标侧设计标高 (m)
    double bench1_cut = 0.0;
    double bench1_fill = 0.0;
    double bench2_cut = 0.0;
    double bench2_fill = 0.0;
    double step_height = 0.0;      // 两级台阶级差 |H1 - H2| (m)
    double wall_cost_per_m2 = 220.0;
    double step_wall_cost = 0.0;   // 挡土墙/边坡支护造价 (元)
    double earthwork_cost = 0.0;   // 两级台阶土方挖填及调配造价 (元)
    double total_cost = 0.0;       // 两级台阶综合总造价 (元)
    double single_pad_elevation = 0.0;
    double single_pad_cut = 0.0;
    double single_pad_fill = 0.0;
    double single_pad_cost = 0.0;
    double savings_vs_single = 0.0;
    double savings_pct = 0.0;
    std::string summary;
};

SteppedPlatformResult optimize_stepped_platform(
    const TINEarthworkCalculator& base_calc,
    const HaulCostParams& costs = HaulCostParams{},
    double wall_cost_per_m2 = 220.0);

// ---------- 9. 施工机械配置与工期台班造价估算 ----------
struct FleetParams {
    double exc_rate_m3_per_h = 110.0;  // 挖掘机小时产能 (自然方 m³/h)
    double truck_capacity_m3 = 18.0;   // 自卸车松方斗容 (m³/车)
    double truck_speed_kmh = 22.0;     // 场内施工道路平均车速 (km/h)
    double load_dump_wait_min = 6.5;   // 装卸等候单趟辅助时间 (min)
    double work_hours_per_day = 9.0;   // 日有效台班作业时长 (h/d)
    double exc_daily_rent = 3600.0;    // 挖掘机台班日租金 (元/台·天)
    double truck_daily_rent = 1800.0;  // 自卸车日租金 (元/辆·天)
    double dozer_daily_rent = 2600.0;  // 推土/平地/压实机组日租金 (元/套·天)
    double target_days = 15.0;         // 目标工期天数 (天)
    int max_excavators = 8;            // 最大可展开挖机工作面数
};

struct FleetEstimateResult {
    double loose_cut_volume = 0.0;
    double compact_fill_volume = 0.0;
    double avg_haul_dist_m = 0.0;
    double truck_cycle_min = 0.0;
    double truck_daily_cap_m3 = 0.0;
    int excavators_needed = 0;
    int trucks_needed = 0;
    int compaction_sets = 0;
    double estimated_days = 0.0;
    double daily_peak_m3 = 0.0;
    double total_machine_cost = 0.0;
    std::string summary;
};

FleetEstimateResult estimate_earthwork_fleet(
    const CalculationResult& calc_res,
    const HaulPlanResult* haul = nullptr,
    const FleetParams& params = FleetParams{});

} // namespace tin
