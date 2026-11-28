#pragma once

#include "core/ai_assistant.hpp"
#include "core/ai_optimizer.hpp"
#include "core/engineering.hpp"
#include "core/types.hpp"
#include <cstdint>
#include <string>
#include <vector>

namespace tin {

std::wstring utf8_to_wide(const std::string& s);
std::string wide_to_utf8(const std::wstring& ws);

struct ImportPointsResult {
    std::vector<SurveyPoint> points;
    ValidationIssues issues;
    std::string detected_format;
};

// 数据导入与质量检查
ImportPointsResult import_points_file(const std::string& utf8_path);
ValidationIssues validate_points(const std::vector<SurveyPoint>& points);

// 边界与特征线导入（支持 DXF / CSV / TXT / DAT）
std::vector<XY> import_boundary_file(const std::string& utf8_path);
std::vector<Breakline> import_breaklines_dxf(const std::string& utf8_path);

// 工程文件存取（兼容 Python 版 .tinproj.json v2/v3）
ProjectData load_project_json(const std::string& utf8_path);
void save_project_json(const std::string& utf8_path, const ProjectData& project);

// 成果导出
void export_summary_csv(const CalculationResult& result, const std::string& utf8_path);
void export_detail_csv(const CalculationResult& result, const std::string& utf8_path);
void export_report_txt(const CalculationResult& result, int point_count,
                       const std::string& project_name, const std::string& utf8_path);
void export_dxf_r12(const CalculationResult& result, const std::string& utf8_path);
void export_cass_dat(const std::vector<SurveyPoint>& points, const std::string& utf8_path,
                     const std::string& code = "");
void export_sections_csv(const SectionMethodResult& sections, const std::string& utf8_path);
void export_survey_recommendations_csv(const UncertaintyReport& uncertainty, const std::string& utf8_path);
void export_ai_overlays_dxf(
    const CalculationResult* result,
    const SiteHealthReport* health,
    const UncertaintyReport* uncertainty,
    const HaulPlanResult* haul,
    const SlopeGradingResult* slope,
    const std::string& utf8_path);
void export_ai_report_html(
    const ProjectData& project,
    const CalculationResult* result,
    const GradeOptimizationResult* grade_opt,
    const SiteHealthReport* health,
    const UncertaintyReport* uncertainty,
    const SignificanceReport* significance,
    const DrainageReport* drainage,
    const DiagnosisSummary* diagnosis,
    const std::string& utf8_path);

// 高清 PNG 保存（用于成果图导出与实机截图）
void save_rgba_png(const std::uint8_t* rgba, int width, int height, const std::string& utf8_path);

} // namespace tin
