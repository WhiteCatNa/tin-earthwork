#pragma once

#include "core/ai_optimizer.hpp"
#include "core/types.hpp"
#include <optional>
#include <string>
#include <vector>

namespace tin {

enum class AssistantAction {
    None = 0,
    Help,
    AutoRectBoundary,
    AutoOutline,
    ClearBoundary,
    Calculate,
    BalanceH0,
    BestFitPlane,
    BalancePlane,
    OptimizeGradeCost,
    RunSections,
    ComputeStrata,
    ComputeHaul,
    ComputeSlope,
    RunDiagnostics,
    CleanAnomalies,
    RunUncertainty,
    AnalyzeSignificance,
    ThinPoints,
    CheckDrainage,
    RunSensitivity,
    OptimizeStepped,
    EstimateFleet,
    MeasureDist,
    MeasureArea,
    SetElevation,
    SetView3D,
    SetView2D,
    ToggleGrid,
    ToggleContours,
    QuickProfile,
    ZoomExtents,
    DarkMode,
    LightMode,
    SwapCompare,
    ExportDat,
    ExportDxf,
    ExportHtmlReport,
    ExportSurveyRecCsv,
    ExportAiDxf,
    Undo,
    Redo,
    ClearConsole
};

struct AssistantIntent {
    AssistantAction action = AssistantAction::None;
    double param1 = 0.0;
    double param2 = 0.0;
    int count_param = 8;
    bool has_param1 = false;
    bool has_param2 = false;
    std::string reply;
};

// 解析经典 CAD 指令及中英文自然语言工程指令
AssistantIntent parse_engineering_intent(const std::string& raw_input);

// Tab 自动补全候选列表
std::vector<std::string> suggest_command_completions(const std::string& prefix);

struct DiagnosisItem {
    int level = 0; // 0=信息, 1=警示, 2=建议动作
    std::string title;
    std::string detail;
};

struct DiagnosisSummary {
    int health_score = 100;
    std::vector<DiagnosisItem> items;
    std::string headline;
};

// 生成综合工程 AI 诊断建议报告
DiagnosisSummary generate_site_diagnosis(
    const ProjectData& project,
    const CalculationResult* calc_res,
    const SiteHealthReport* health,
    const GradeOptimizationResult* grade_opt,
    const UncertaintyReport* uncertainty);

} // namespace tin
