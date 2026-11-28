#include "core/ai_assistant.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <sstream>

namespace tin {

namespace {

std::string trim_str(const std::string& s) {
    size_t a = 0;
    while (a < s.size() && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    size_t b = s.size();
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

std::string upper_str(std::string s) {
    for (char& ch : s) {
        ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
    }
    return s;
}

std::string lower_str(std::string s) {
    for (char& ch : s) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return s;
}

bool contains_any(const std::string& hay, std::initializer_list<const char*> needles) {
    for (const char* nd : needles) {
        if (hay.find(nd) != std::string::npos) return true;
    }
    return false;
}

std::vector<double> extract_numbers(const std::string& s) {
    std::vector<double> nums;
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        bool start_num = std::isdigit(c) ||
                         ((c == '-' || c == '+') && i + 1 < s.size() &&
                          (std::isdigit(static_cast<unsigned char>(s[i + 1])) || s[i + 1] == '.'));
        if (start_num) {
            // 避免把 "3D" / "2D" 当作业务参数
            size_t end = i + 1;
            bool has_dot = false;
            while (end < s.size()) {
                unsigned char ec = static_cast<unsigned char>(s[end]);
                if (std::isdigit(ec)) {
                    ++end;
                } else if (ec == '.' && !has_dot) {
                    has_dot = true;
                    ++end;
                } else {
                    break;
                }
            }
            if (end < s.size() && (s[end] == 'd' || s[end] == 'D')) {
                i = end + 1;
                continue;
            }
            try {
                nums.push_back(std::stod(s.substr(i, end - i)));
            } catch (...) {}
            i = end;
        } else {
            ++i;
        }
    }
    return nums;
}

} // namespace

AssistantIntent parse_engineering_intent(const std::string& raw_input) {
    AssistantIntent out;
    std::string cmd = trim_str(raw_input);
    if (cmd.empty()) return out;

    std::string up = upper_str(cmd);
    std::string lo = lower_str(cmd);

    // 1. 精确匹配经典 CAD 命令
    if (up == "HELP" || up == "?") {
        out.action = AssistantAction::Help;
        return out;
    }
    if (up == "AUTOB" || up == "_AUTOB" || up == "RECT") {
        out.action = AssistantAction::AutoRectBoundary;
        return out;
    }
    if (up == "OUTLINE" || up == "_OUTLINE") {
        out.action = AssistantAction::AutoOutline;
        return out;
    }
    if (up == "CLRBND" || up == "_CLRBND") {
        out.action = AssistantAction::ClearBoundary;
        return out;
    }
    if (up == "CALC" || up == "_CALC" || up == "RUN") {
        out.action = AssistantAction::Calculate;
        return out;
    }
    if (up == "BALANCE" || up == "_BALANCE" || up == "H0") {
        out.action = AssistantAction::BalanceH0;
        return out;
    }
    if (up == "FITPLANE" || up == "_FITPLANE") {
        out.action = AssistantAction::BestFitPlane;
        return out;
    }
    if (up == "BALPLANE" || up == "_BALPLANE") {
        out.action = AssistantAction::BalancePlane;
        return out;
    }
    if (up == "OPT" || up == "AIOPT" || up == "_OPT" || up == "GRADEOPT") {
        out.action = AssistantAction::OptimizeGradeCost;
        return out;
    }
    if (up == "SECTIONS" || up == "_SECTIONS") {
        out.action = AssistantAction::RunSections;
        return out;
    }
    if (up == "STRATA" || up == "_STRATA") {
        out.action = AssistantAction::ComputeStrata;
        return out;
    }
    if (up == "HAUL" || up == "_HAUL") {
        out.action = AssistantAction::ComputeHaul;
        return out;
    }
    if (up == "SLOPE" || up == "_SLOPE") {
        out.action = AssistantAction::ComputeSlope;
        return out;
    }
    if (up == "DIAG" || up == "ANOMALY" || up == "CHECK" || up == "AI") {
        out.action = AssistantAction::RunDiagnostics;
        return out;
    }
    if (up == "CLEAN" || up == "REPAIR") {
        out.action = AssistantAction::CleanAnomalies;
        return out;
    }
    if (up == "UNCERT" || up == "SURVEY" || up == "REC") {
        out.action = AssistantAction::RunUncertainty;
        return out;
    }
    if (up == "SIGNIF" || up == "LOD") {
        out.action = AssistantAction::AnalyzeSignificance;
        return out;
    }
    if (up == "THIN" || up == "DECIMATE") {
        out.action = AssistantAction::ThinPoints;
        return out;
    }
    if (up == "DRAIN" || up == "PONDING") {
        out.action = AssistantAction::CheckDrainage;
        return out;
    }
    if (up == "SENSITIVITY" || up == "CURVE") {
        out.action = AssistantAction::RunSensitivity;
        return out;
    }
    if (up == "BENCH" || up == "STEPPED" || up == "TERRACE") {
        out.action = AssistantAction::OptimizeStepped;
        return out;
    }
    if (up == "FLEET" || up == "SCHEDULE" || up == "MACHINES") {
        out.action = AssistantAction::EstimateFleet;
        return out;
    }
    if (up == "MEASURE" || up == "DIST" || up == "DI") {
        out.action = AssistantAction::MeasureDist;
        return out;
    }
    if (up == "AREA" || up == "AA") {
        out.action = AssistantAction::MeasureArea;
        return out;
    }
    if (up == "3D") {
        out.action = AssistantAction::SetView3D;
        return out;
    }
    if (up == "2D") {
        out.action = AssistantAction::SetView2D;
        return out;
    }
    if (up == "GRID" || up == "_GRID") {
        out.action = AssistantAction::ToggleGrid;
        return out;
    }
    if (up == "CONTOUR" || up == "_CONTOUR") {
        out.action = AssistantAction::ToggleContours;
        return out;
    }
    if (up == "PROFILE" || up == "_PROFILE") {
        out.action = AssistantAction::QuickProfile;
        return out;
    }
    if (up == "ZE" || up == "Z" || up == "ZOOM") {
        out.action = AssistantAction::ZoomExtents;
        return out;
    }
    if (up == "DARK") {
        out.action = AssistantAction::DarkMode;
        return out;
    }
    if (up == "LIGHT") {
        out.action = AssistantAction::LightMode;
        return out;
    }
    if (up == "SWAP") {
        out.action = AssistantAction::SwapCompare;
        return out;
    }
    if (up == "DAT") {
        out.action = AssistantAction::ExportDat;
        return out;
    }
    if (up == "DXF") {
        out.action = AssistantAction::ExportDxf;
        return out;
    }
    if (up == "HTMLRPT" || up == "REPORT") {
        out.action = AssistantAction::ExportHtmlReport;
        return out;
    }
    if (up == "EXP_REC" || up == "RECCSV") {
        out.action = AssistantAction::ExportSurveyRecCsv;
        return out;
    }
    if (up == "EXP_AIDXF" || up == "AIDXF") {
        out.action = AssistantAction::ExportAiDxf;
        return out;
    }
    if (up == "UNDO" || up == "_UNDO" || up == "U") {
        out.action = AssistantAction::Undo;
        return out;
    }
    if (up == "REDO" || up == "_REDO") {
        out.action = AssistantAction::Redo;
        return out;
    }
    if (up == "CLEAR" || up == "CLS") {
        out.action = AssistantAction::ClearConsole;
        return out;
    }
    if (up.rfind("H=", 0) == 0 || up.rfind("Z=", 0) == 0) {
        try {
            out.param1 = std::stod(cmd.substr(2));
            out.has_param1 = true;
            out.action = AssistantAction::SetElevation;
            return out;
        } catch (...) {
            out.reply = "参数格式错误，示例: H=50.5";
            return out;
        }
    }

    // 2. 中英双语自然语言工程意图解析 + 参数抽取
    auto nums = extract_numbers(cmd);

    if (contains_any(lo, {"帮助", "命令", "指令", "怎么用", "help", "commands"})) {
        out.action = AssistantAction::Help;
        return out;
    }
    if (contains_any(lo, {"清屏", "清空日志", "clear log", "clear console"})) {
        out.action = AssistantAction::ClearConsole;
        return out;
    }
    if (contains_any(lo, {"撤销", "回退", "undo"})) {
        out.action = AssistantAction::Undo;
        return out;
    }
    if (contains_any(lo, {"重做", "恢复上一步", "redo"})) {
        out.action = AssistantAction::Redo;
        return out;
    }
    if (contains_any(lo, {"修复", "清理粗差", "清洗异常", "clean anomaly", "repair outlier", "fix spike"})) {
        out.action = AssistantAction::CleanAnomalies;
        out.reply = "AI助手: 执行测点异常自动修复";
        return out;
    }
    if (contains_any(lo, {"异常", "粗差", "飞点", "体检", "诊断", "健康", "anomaly", "outlier", "diagnos", "health"})) {
        out.action = AssistantAction::RunDiagnostics;
        out.reply = "AI助手: 执行测点空间异常检测与场地健康诊断";
        return out;
    }
    if (contains_any(lo, {"显著性", "检测限", "lod", "conformal", "significance"})) {
        out.action = AssistantAction::AnalyzeSignificance;
        out.reply = "AI助手: 执行共形标定变化显著性 (LoD) 检验";
        return out;
    }
    if (contains_any(lo, {"抽稀", "精简点云", "thinning", "decimate"})) {
        out.action = AssistantAction::ThinPoints;
        if (!nums.empty()) {
            out.param1 = std::clamp(std::abs(nums[0]), 0.01, 2.0);
            out.has_param1 = true;
        }
        out.reply = "AI助手: 执行保精度点云抽稀";
        return out;
    }
    if (contains_any(lo, {"敏感性", "标高曲线", "sensitivity", "elevation curve"})) {
        out.action = AssistantAction::RunSensitivity;
        out.reply = "AI助手: 生成设计标高-方量/综合费用敏感性曲线";
        return out;
    }
    if (contains_any(lo, {"台阶", "阶梯", "二级平场", "双级", "stepped", "terrace", "bench"})) {
        out.action = AssistantAction::OptimizeStepped;
        if (!nums.empty()) {
            out.param1 = std::clamp(std::abs(nums[0]), 0.0, 2000.0);
            out.has_param1 = true;
        }
        out.reply = "AI助手: 执行双级台阶平场及挡土墙联合造价优化";
        return out;
    }
    if (contains_any(lo, {"机械", "挖机", "自卸车", "工期", "台班", "排班", "fleet", "schedule", "excavator", "truck"})) {
        out.action = AssistantAction::EstimateFleet;
        if (!nums.empty()) {
            out.param1 = std::clamp(std::abs(nums[0]), 1.0, 365.0);
            out.has_param1 = true;
        }
        out.reply = "AI助手: 执行土方施工机械配置与工期台班估算";
        return out;
    }
    if (contains_any(lo, {"积水", "排水", "drainage", "ponding"})) {
        out.action = AssistantAction::CheckDrainage;
        if (!nums.empty()) {
            out.param1 = std::clamp(std::abs(nums[0]), 0.05, 5.0);
            out.has_param1 = true;
        }
        out.reply = "AI助手: 执行场地排水坡度与积水风险检查";
        return out;
    }
    if (contains_any(lo, {"补测", "不确定度", "置信区间", "误差分布", "uncertainty", "recommend", "survey point"})) {
        out.action = AssistantAction::RunUncertainty;
        if (!nums.empty()) {
            out.count_param = std::clamp(static_cast<int>(std::lround(nums.back())), 1, 50);
        }
        out.reply = "AI助手: 执行不确定度评定与自适应补测点推荐";
        return out;
    }
    if (contains_any(lo, {"费用", "省钱", "成本", "联合优化", "最优方案", "优化总费用", "grade opt", "cost opt", "minimize cost", "optimize cost", "optimize total cost", "total cost"})) {
        out.action = AssistantAction::OptimizeGradeCost;
        if (!nums.empty()) {
            out.param1 = std::clamp(std::abs(nums[0]), 0.2, 25.0);
            out.has_param1 = true;
        }
        out.reply = "AI助手: 启动设计面-土方调配联合费用优化";
        return out;
    }
    if (contains_any(lo, {"最小二乘", "最优斜面", "best fit", "best-fit", "fit plane"})) {
        out.action = AssistantAction::BestFitPlane;
        out.reply = "AI助手: 拟合最小二乘最优斜面";
        return out;
    }
    if (contains_any(lo, {"保坡", "balance plane", "slope balance"})) {
        out.action = AssistantAction::BalancePlane;
        out.reply = "AI助手: 保坡求解零平衡基准标高";
        return out;
    }
    if (contains_any(lo, {"平衡标高", "零平衡", "挖填平衡", "balance elevation", "zero balance"})) {
        out.action = AssistantAction::BalanceH0;
        out.reply = "AI助手: 求解挖填零平衡设计标高 H0";
        return out;
    }
    if (contains_any(lo, {"平行断面", "横断面", "断面法", "桩号", "cross section", "sections"})) {
        out.action = AssistantAction::RunSections;
        if (!nums.empty()) {
            out.param1 = std::clamp(std::abs(nums[0]), 2.0, 200.0);
            out.has_param1 = true;
        }
        if (nums.size() >= 2) {
            out.param2 = nums[1];
            out.has_param2 = true;
        }
        out.reply = "AI助手: 生成平行横断面与平均断面法方量表";
        return out;
    }
    if (contains_any(lo, {"纵断面", "剖面", "profile"})) {
        out.action = AssistantAction::QuickProfile;
        return out;
    }
    if (contains_any(lo, {"分层", "表土", "岩层", "strata", "layers"})) {
        out.action = AssistantAction::ComputeStrata;
        return out;
    }
    if (contains_any(lo, {"调配", "运距", "haul", "transport"})) {
        out.action = AssistantAction::ComputeHaul;
        return out;
    }
    if (contains_any(lo, {"放坡", "边坡", "坡脚", "坡顶", "slope grading", "daylight"})) {
        out.action = AssistantAction::ComputeSlope;
        // 识别如 "1:1.2 1:1.5" 或 "1.2 1.5"
        if (nums.size() >= 4 && std::abs(nums[0] - 1.0) < 1e-6 && std::abs(nums[2] - 1.0) < 1e-6) {
            out.param1 = nums[1];
            out.param2 = nums[3];
            out.has_param1 = out.has_param2 = true;
        } else if (nums.size() >= 2) {
            out.param1 = nums[0];
            out.param2 = nums[1];
            out.has_param1 = out.has_param2 = true;
        } else if (nums.size() == 1) {
            out.param1 = out.param2 = nums[0];
            out.has_param1 = out.has_param2 = true;
        }
        return out;
    }
    if (contains_any(lo, {"测面积", "量面积", "多边形面积", "measure area", "polygon area"})) {
        out.action = AssistantAction::MeasureArea;
        return out;
    }
    if (contains_any(lo, {"测距", "量测", "距离", "坡度测量", "measure", "distance"})) {
        out.action = AssistantAction::MeasureDist;
        return out;
    }
    if (contains_any(lo, {"3d", "三维", "立体"})) {
        out.action = AssistantAction::SetView3D;
        return out;
    }
    if (contains_any(lo, {"2d", "二维", "平面视图"})) {
        out.action = AssistantAction::SetView2D;
        return out;
    }
    if (contains_any(lo, {"白底", "浅色", "出图", "打印模式", "light mode", "white background"})) {
        out.action = AssistantAction::LightMode;
        return out;
    }
    if (contains_any(lo, {"黑底", "深色", "模型空间", "dark mode", "dark background"})) {
        out.action = AssistantAction::DarkMode;
        return out;
    }
    if (contains_any(lo, {"全图", "居中", "缩放全部", "zoom extents", "fit view"})) {
        out.action = AssistantAction::ZoomExtents;
        return out;
    }
    if (contains_any(lo, {"外轮廓", "凹轮廓", "自动边界", "outline", "concave hull"})) {
        out.action = AssistantAction::AutoOutline;
        return out;
    }
    if (contains_any(lo, {"矩形边界", "rect boundary", "bounding box"})) {
        out.action = AssistantAction::AutoRectBoundary;
        return out;
    }
    if (contains_any(lo, {"清除边界", "删除边界", "clear boundary"})) {
        out.action = AssistantAction::ClearBoundary;
        return out;
    }
    if (contains_any(lo, {"方格网", "grid check"})) {
        out.action = AssistantAction::ToggleGrid;
        return out;
    }
    if (contains_any(lo, {"等高线", "contour"})) {
        out.action = AssistantAction::ToggleContours;
        return out;
    }
    if (contains_any(lo, {"html", "决策报告", "智能报告", "export report"})) {
        out.action = AssistantAction::ExportHtmlReport;
        return out;
    }
    if (contains_any(lo, {"导出补测", "补测点csv", "export survey"})) {
        out.action = AssistantAction::ExportSurveyRecCsv;
        return out;
    }
    if (contains_any(lo, {"导出dxf", "输出dxf", "export dxf", "save dxf"})) {
        out.action = AssistantAction::ExportDxf;
        return out;
    }
    if (contains_any(lo, {"导出dat", "cass坐标", "export dat"})) {
        out.action = AssistantAction::ExportDat;
        return out;
    }
    if (contains_any(lo, {"互换", "对调", "swap compare"})) {
        out.action = AssistantAction::SwapCompare;
        return out;
    }
    if (contains_any(lo, {"标高", "高程", "elevation", "design level", "flat level"}) && !nums.empty()) {
        out.param1 = nums.back();
        out.has_param1 = true;
        out.action = AssistantAction::SetElevation;
        return out;
    }
    if (contains_any(lo, {"计算", "算量", "构网", "运行", "calculate", "compute", "run tin"})) {
        out.action = AssistantAction::Calculate;
        return out;
    }

    out.reply = "未能识别指令 \"" + cmd + "\"，输入 HELP 查看命令清单，或尝试中文如“优化总费用”、“检查异常点”、“把标高设为50.5”。";
    return out;
}

std::vector<std::string> suggest_command_completions(const std::string& prefix) {
    static const char* kCommands[] = {
        "AI", "AIOPT", "ANOMALY", "AREA", "AUTOB", "BALANCE", "BALPLANE", "BENCH",
        "CALC", "CLEAN", "CLEAR", "CLRBND", "CONTOUR", "DARK", "DAT",
        "DIAG", "DIST", "DRAIN", "DXF", "EXP_AIDXF", "EXP_REC", "FITPLANE", "FLEET", "GRID", "H0", "HAUL",
        "HELP", "HTMLRPT", "LIGHT", "MEASURE", "OPT", "OUTLINE", "PROFILE",
        "REDO", "SECTIONS", "SENSITIVITY", "SIGNIF", "SLOPE", "STRATA", "SWAP", "THIN", "UNCERT",
        "UNDO", "ZE", "2D", "3D"
    };
    std::string up = upper_str(trim_str(prefix));
    std::vector<std::string> matches;
    for (const char* cmd : kCommands) {
        std::string s(cmd);
        if (up.empty() || s.rfind(up, 0) == 0) {
            matches.push_back(s);
        }
    }
    return matches;
}

DiagnosisSummary generate_site_diagnosis(
    const ProjectData& project,
    const CalculationResult* calc_res,
    const SiteHealthReport* health,
    const GradeOptimizationResult* grade_opt,
    const UncertaintyReport* uncertainty) {
    DiagnosisSummary out;
    out.health_score = health ? health->health_score : 100;

    if (project.points.size() < 3) {
        out.items.push_back({1, "缺少实测地形数据", "请先导入高程测点 (.xlsx / .csv / .dat) 或打开示范工程。"});
        out.headline = "等待导入测点数据";
        return out;
    }

    if (health) {
        if (health->anomalies.empty()) {
            out.items.push_back({0, "测点空间连续性良好", "未检出高程突刺、深坑或孤立坐标飞点。"});
        } else {
            std::ostringstream os;
            os << "检出 " << health->anomalies.size() << " 个疑似异常测点，首条: "
               << health->anomalies.front().point_id << " (" << health->anomalies.front().reason << ")。";
            out.items.push_back({1, "存在疑似测量粗差", os.str()});
        }
    }

    if (project.boundary.size() < 3) {
        out.items.push_back({2, "建议设置计算边界", "当前按测点凸包计算；若场地边界呈凹形或有红线要求，建议执行 OUTLINE 或手绘边界。"});
    }

    if (calc_res) {
        double vol_sum = calc_res->total_cut + calc_res->total_fill;
        if (vol_sum > 1e-6) {
            double imbal = std::abs(calc_res->net_volume) / vol_sum * 100.0;
            std::ostringstream os;
            os << std::fixed << std::setprecision(1)
               << "挖方 " << calc_res->total_cut << " m³，填方 " << calc_res->total_fill
               << " m³，净余缺 " << calc_res->net_volume << " m³（不平衡率 " << imbal << "%）。";
            if (imbal > 15.0 && !calc_res->is_compare) {
                os << " 建议求解零平衡标高 H0 或运行联合费用优化降低外运/借土成本。";
                out.items.push_back({2, "挖填不平衡度偏高", os.str()});
            } else {
                out.items.push_back({0, "土方平衡度可控", os.str()});
            }
        }
        if (calc_res->grid_check.has_value() && calc_res->total_cut > 1e-6) {
            double diff_pct = std::abs(calc_res->grid_check->total_cut - calc_res->total_cut) / calc_res->total_cut * 100.0;
            std::ostringstream os;
            os << std::fixed << std::setprecision(2)
               << "方格网法校核挖方相对偏差 " << diff_pct << "%（TIN 法精确剖分零线，方格网仅作复核参考）。";
            out.items.push_back({0, "双算法校核完成", os.str()});
        }
    }

    if (grade_opt && grade_opt->converged) {
        std::ostringstream os;
        os << std::fixed << std::setprecision(1)
           << "AI 联合费用最优斜面综合成本 ¥" << grade_opt->optimized.total_cost
           << "，较平场平衡节省 ¥" << grade_opt->savings_vs_flat
           << "（" << grade_opt->savings_pct_vs_flat << "%）。";
        out.items.push_back({grade_opt->savings_pct_vs_flat >= 5.0 ? 2 : 0, "设计面联合节费方案就绪", os.str()});
    }

    if (uncertainty) {
        std::ostringstream os;
        os << std::fixed << std::setprecision(1)
           << "95% 不确定度半宽：挖方 ±" << uncertainty->cut_rel_ci95_pct
           << "%，填方 ±" << uncertainty->fill_rel_ci95_pct << "%。";
        if (!uncertainty->recommendations.empty()) {
            os << " 已按曲率-面积误差尺度推荐 " << uncertainty->recommendations.size() << " 处高收益补测点。";
        }
        out.items.push_back({(uncertainty->cut_rel_ci95_pct > 5.0 || uncertainty->fill_rel_ci95_pct > 5.0) ? 2 : 0,
                             "精度不确定度与补测建议", os.str()});
    }

    out.headline = "场地健康分 " + std::to_string(out.health_score) + "/100 · 生成 " +
                   std::to_string(out.items.size()) + " 条工程诊断建议";
    return out;
}

} // namespace tin
