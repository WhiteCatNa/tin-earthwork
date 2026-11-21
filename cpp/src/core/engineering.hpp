#pragma once

#include "core/calculator.hpp"
#include "core/types.hpp"
#include <string>
#include <vector>

namespace tin {

// ---------- 1. 分层土方统计（支持按绝对标高分层或按开挖深度分层） ----------
enum class StrataMode {
    ByElevation = 0, // 按绝对分界标高分层（如强风化岩顶标高）
    ByDepth          // 按距原地面深度分层（如 0~0.3m 表土清表，0.3~2.0m 普通土）
};

struct StratumLayerResult {
    std::string name;
    double lower_bound = 0.0;
    double upper_bound = 0.0;
    double cut_volume = 0.0;
    double fill_volume = 0.0;
    double cut_ratio = 0.0;
    double fill_ratio = 0.0;
};

struct StrataResult {
    StrataMode mode = StrataMode::ByDepth;
    std::vector<double> boundaries;
    std::vector<StratumLayerResult> layers;
    double total_cut = 0.0;
    double total_fill = 0.0;
};

StrataResult compute_strata_volumes(
    const CalculationResult& tin_result,
    StrataMode mode,
    const std::vector<double>& boundaries,
    const std::vector<std::string>& layer_names = {});

// ---------- 2. 土方调配分区与最小运距优化 ----------
struct HaulZone {
    int id = 0;
    int row = 0;
    int col = 0;
    std::string name;
    double cut_volume = 0.0;
    double fill_volume = 0.0;
    double internal_balance = 0.0; // 区内自平衡自然方
    double net_surplus = 0.0;      // >0 余土（挖方区），<0 缺土（填方区）
    XY cut_centroid;
    XY fill_centroid;
};

struct HaulFlow {
    int from_zone_id = 0;
    int to_zone_id = 0;
    std::string from_name;
    std::string to_name;
    XY from_xy;
    XY to_xy;
    double volume = 0.0;   // 调配自然方 (m³)
    double distance = 0.0; // 重心直线运距 (m)
};

struct HaulCostParams {
    double cut_unit_cost = 18.0;     // 挖方单价 (元/m³)
    double fill_unit_cost = 14.0;    // 填方压实单价 (元/m³)
    double haul_per_m3_100m = 3.5;   // 场内运距单价 (元/m³·100m)
    double export_unit_cost = 35.0;  // 余土外运单价 (元/m³)
    double borrow_unit_cost = 42.0;  // 缺土外借单价 (元/m³)
};

struct HaulPlanResult {
    int grid_rows = 0;
    int grid_cols = 0;
    std::vector<HaulZone> zones;
    std::vector<HaulFlow> flows;
    double total_cut = 0.0;
    double total_fill = 0.0;
    double internal_balanced = 0.0;
    double cross_zone_hauled = 0.0;
    double export_surplus = 0.0;
    double borrow_deficit = 0.0;
    double avg_haul_distance = 0.0; // 跨区加权平均运距 (m)
    double total_work_m3_m = 0.0;   // 总土方调配功 (m³·m)
    double estimated_cost = 0.0;    // 估算综合土方费用 (元)
};

HaulPlanResult compute_haul_plan(
    const CalculationResult& tin_result,
    int grid_cols = 4,
    int grid_rows = 4,
    const HaulCostParams& cost_params = HaulCostParams{});

// ---------- 3. 场地周边放坡设计（自动搜寻自然地面坡顶/坡脚交线） ----------
struct SlopeGradingResult {
    double cut_ratio = 1.0;   // 挖方放坡 1:m_cut
    double fill_ratio = 1.5;  // 填方放坡 1:m_fill
    double perimeter = 0.0;
    double slope_cut_volume = 0.0;
    double slope_fill_volume = 0.0;
    double slope_footprint_area = 0.0;
    double max_slope_width = 0.0;
    std::vector<XY> platform_ring;
    std::vector<XY> daylight_ring;    // 坡顶/坡脚闭合交线
    std::vector<Segment> slope_ticks; // 示坡线短线（便于 CAD 渲染）
};

SlopeGradingResult compute_slope_grading(
    const TINEarthworkCalculator& calculator,
    double cut_ratio = 1.0,
    double fill_ratio = 1.5,
    double sample_step = 4.0);

} // namespace tin
