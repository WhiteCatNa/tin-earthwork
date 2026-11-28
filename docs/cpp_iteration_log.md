# C++20 南方CASS 工作站递归自迭代日志

> 目标文件：[cpp/ITERATION_GOAL.md](file:///Users/jiangzhiwei/Desktop/code/tin-earthwork/cpp/ITERATION_GOAL.md)（100 分制，北极星目标 ≥ **95 / 100**）

_以下各轮记录均由 `python3 tools/eval_cpp_goal.py` 真实编译、运行 CTest 与基准套件测得。_

## Iteration 0 — Baseline (`v3.0.0-alpha`)
- **版本说明**：完成跨平台 C++20 构建适配（macOS/Linux + Win32/D3D11 双兼容）、补全 `render_tab_engineering()` 链接缺失、建立无头 Dear ImGui 测试与 100 分制量化评测体系。
- **实测结果（`python3 tools/eval_cpp_goal.py`）**：
  - 1. 算法最优性与算量精度：`8.00 / 30.00`（18 项核心单测全过，`test_data_1000.xlsx` 对齐精度达标；但贪心+2-opt 调配在 5/5 测试集上均存在负权增广圈 `0/5`，且尚无设计面联合费用优化）
  - 2. AI 工程智能：`0.00 / 25.00`（待实现不确定度评定、自适应补测推荐、异常点检测与双语 AI 助手）
  - 3. 性能与边界鲁棒性：`8.00 / 15.00`（`tin_ms = 6.74 ms`，`haul_ms = 0.03 ms`，边界防御 `4/4`）
  - 4. 南方CASS UI/UX：`9.00 / 20.00`（6 标签页无头渲染通过，对比度 `13.52 >= 4.5`；待扩展 `AI智能` 页签、量测工具、Tab 补全与自然语言执行）
  - 5. 工程质量与迭代实证：`7.00 / 10.00`（0 条编译警告，跨平台完整，CTest 3/3 PASS）
  - **总分：`32.00 / 100.00`**
- **下一步瓶颈诊断**：优先攻克 1) 精确最小费用流土方调配（消除全部负权环，+10 分）与 2) 快速设计面-调配联合费用优化（+12 分 + 2 分性能），同时消除 1 条编译警告。

## Iteration 1 — 核心算法最优性与性能突破 (`v3.1.0`)
- **版本升级**：
  1. `cpp/src/core/delaunay.cpp`：Bowyer-Watson 构网引入 X 排序扫描剪枝淘汰机制（`active_tris` + `finished_tris`）。
  2. `cpp/src/core/calculator.cpp`：非裁剪单号三角形启用零堆分配栈快路径。
  3. `cpp/src/core/engineering.cpp`：`compute_haul_plan` 升级为 **SPFA 精确最小费用最大流（Min-Cost Max-Flow）**，保证二分运输问题全局 LP 最优且零负权增广圈。
  4. `cpp/src/core/ai_optimizer.hpp/.cpp`：新增 `optimize_grade_cost` 设计面-土方调配联合费用优化器（复用已构三角网的轻量评估器 + 多起点热启动 + 箱式约束坐标模式搜索 + Nelder-Mead 单纯形精修）。
- **实测结果（`python3 tools/eval_cpp_goal.py`）**：
  - 1. 算法最优性与算量精度：**`30.00 / 30.00`**（满分达成！精确调配 `5/5` 零负权环，联合优化节费 `79.94%` 且严格优于平场平衡与最小二乘斜面）
  - 2. AI 工程智能：`0.00 / 25.00`（下一轮重点攻克）
  - 3. 性能与边界鲁棒性：**`13.00 / 15.00`**（1000 点算量 `6.85 ms -> 2.68 ms`，调配 `0.04 ms`，联合优化 `190 ms -> 45.96 ms`，边界防御 `4/4`）
  - 4. 南方CASS UI/UX：`9.00 / 20.00`
  - 5. 工程质量与迭代实证：`9.00 / 10.00`（0 警告，CTest 3/3 PASS）
  - **总分：`60.00 -> 61.00 / 100.00`**
- **下一步瓶颈诊断**：当前最大失分项为「2. AI 工程智能」（`0/25`）。Iteration 2 (`v3.2.0`) 将落地空间异常点检测与自动修复、抽稀外推不确定度评定、自适应补测点推荐及中英双语 AI 助手。

## Iteration 2 — AI 工程智能全面落地 (`v3.2.0`)
- **版本升级**：
  1. `cpp/src/core/ai_optimizer.hpp/.cpp`：
     - 新增空间异常点检测 `detect_survey_anomalies`（2-pass IRLS 局部平面拟合抗粗差 + 稳健 MAD 准则，检出高程突刺/深坑、孤立飞点、重合点高程冲突并评定 0~100 场地健康分）与实用一键修复 `clean_survey_anomalies`。
     - 新增抽稀外推不确定度评定与自适应补测点推荐 `analyze_uncertainty_and_recommend`（$K=8$ 保留凸包内部 50% 抽稀 Richardson 外推 + 面积加权测量误差 + 曲率-面积误差尺度大顶堆贪心推荐）。
  2. `cpp/src/core/ai_assistant.hpp/.cpp`：新增中英双语自然语言/CAD 指令意图解析器 `parse_engineering_intent`、Tab 补全候选生成器 `suggest_command_completions` 与综合工程诊断报告 `generate_site_diagnosis`。
  3. `cpp/src/core/calculator.cpp`：`unique_point_indices` 改用扁平向量排序替代 `std::map`，消除千点级堆分配。
- **实测结果（`python3 tools/eval_cpp_goal.py`）**：
  - 1. 算法最优性与算量精度：`30.00 / 30.00`
  - 2. AI 工程智能：**`25.00 / 25.00`**（满分达成！异常检测 Recall `100%` / Precision `90.91%`，双语 30 用例意图识别 `100%`，95% 区间覆盖高斯山包解析真值，10% 推荐补测远超随机加密）
  - 3. 性能与边界鲁棒性：**`15.00 / 15.00`**（满分达成！`tin_ms = 2.26 ms`，`haul_ms = 0.04 ms`，`grade_ms = 52.47 ms`，`uncertainty_ms = 15.75 ms`，边界防御 `4/4`）
  - 4. 南方CASS UI/UX：`9.00 / 20.00`
  - 5. 工程质量与迭代实证：**`10.00 / 10.00`**（0 警告，CTest 24/24 + UI + Benchmark 全绿）
  - **总分：`88.00 -> 89.00 / 100.00`**
- **下一步瓶颈诊断**：当前唯一未达标维度为「4. 南方CASS UI/UX」（`9/20`）。Iteration 3 (`v3.3.0`) 将把全部 AI 能力深度融入南方CASS 四区工作站界面并补齐交互式测距/测面积工具与智能命令行。

## Iteration 3 — 南方CASS UI/UX 全面升级 (`v3.3.0`)
- **版本升级**：
  1. `cpp/src/ui/cad_canvas.hpp/.cpp`：新增交互式 `MeasureDist`（距离/ΔZ/坡度%）与 `MeasureArea`（闭合面积/周长/圈内挖填方量实时探针）、`focus_point` 点击定位、异常点告警标记与悬浮详情、自适应补测推荐靶标、CAD 指北针与更新图例。
  2. `cpp/src/ui/app.hpp/.cpp`：新增右侧第 7 页签「**AI智能**」（一键全流程诊断优化、健康分进度条、5 项单价联合费用优化对照表与一键采纳、可点击定位异常点表、一键局部修复并支持 Ctrl+Z 撤销、不确定度 95% 区间与补测点推荐表、工程建议清单）；快捷工具栏新增 `测距` / `测面积` / `AI优化`；命令行新增 `Tab` 自动补全与中英双语自然语言执行。
  3. `cpp/tests/test_ui_headless.cpp` & `cpp/tests/test_ai_benchmark.cpp`：无头驱动全 7 页签、深浅色模式、2D/3D、量测工具、Tab 补全与双语命令行。
- **实测结果（`python3 tools/eval_cpp_goal.py`）**：
  - 1. 算法最优性与算量精度：`30.00 / 30.00`
  - 2. AI 工程智能：`25.00 / 25.00`
  - 3. 性能与边界鲁棒性：`15.00 / 15.00`（`tin_ms = 1.78 ms`，`grade_ms = 40.14 ms`，`uncertainty_ms = 11.35 ms`）
  - 4. 南方CASS UI/UX：**`20.00 / 20.00`**（7 标签页全覆盖、对比度 `13.52 >= 4.5`、双语指令与 Tab 补全全通）
  - 5. 工程质量与迭代实证：`10.00 / 10.00`（0 警告，CTest 3/3 PASS）
  - **总分：`100.00 / 100.00`（超越 ≥95 分北极星目标）**
- **下一步持续迭代方向**：进入 `v3.4.0` ASan+UBSan 内存安全审计与多随机种子严苛基准复核，随后迭代 `v3.5.0`~`v3.7.0` 实用工程新功能（共形显著性 LoD、保精度抽稀、积水排水坡度检查、HTML 决策报告、3D 图例靶标与 $O(N)$ 空间网格加速）。

## Iteration 4 — ASan+UBSan 内存安全审计与严谨基准复核 (`v3.4.0`)
- **版本升级**：
  1. `cpp/tests/test_core.cpp`：补充 Test 25（空点集/3点极简/零坡度约束/极端单价防御）。
  2. `cpp/tests/test_ai_benchmark.cpp`：自适应补测效率对比升级为 3 组随机种子均值对照，确保 `survey_rec_ratio` 客观稳定。
  3. 完整执行 `AddressSanitizer + UndefinedBehaviorSanitizer (-fsanitize=address,undefined)` 编译并全量通过 CTest 3/3 套件（零内存越界/泄漏/UB）。
- **实测结果（`python3 tools/eval_cpp_goal.py` + `build_asan`）**：
  - Release 评分：**`100.00 / 100.00`**（0 编译警告，CTest 3/3 PASS）
  - ASan + UBSan 审计：**3/3 全量通过（10.26s）**
- **下一步持续迭代方向**：进入 `v3.5.0` 实用工程新功能开发（共形变化显著性 LoD、保精度点云抽稀与场地排水/积水风险检查）。

## Iteration 5 — 工程实用新功能拓展 (`v3.5.0`)
- **版本升级**：
  1. **共形标定变化显著性检测限 (`analyze_change_significance`)**：基于 3 次 10% 留出分裂共形预测标定 $q$，结合局部曲率与测量误差生成空间自适应 LoD，区分真实挖填方量与测绘/内插噪声。
  2. **保精度点云抽稀 (`thin_points_preserve_accuracy`)**：保留凸包骨架并按逐三角形最大残差贪心精化，在平坦缓坡区实现高压缩率的同时严格保证任意被删点高程误差 $\le$ 容差（并支持界面一键应用与 `Ctrl+Z` 撤销）。
  3. **场地排水坡度与积水风险检查 (`analyze_drainage_and_ponding`)**：计算设计面/原地面加权平均坡度、主排水汇流方位角与低于最小排水坡度（如 0.3%）的积水风险区面积占比。
  4. 同步接入双语命令行（`SIGNIF` / `THIN` / `DRAIN`）、Tab 补全与「AI智能」页签第 5 板块，并新增 Test 26~28 解析与对照单测。
- **实测结果（`python3 tools/eval_cpp_goal.py`）**：
  - 综合评分：**`100.00 / 100.00`**（0 编译警告，CTest 3/3 + 28/28 核心单测全过，`tin_ms = 1.83 ms`）
- **下一步持续迭代方向**：进入 `v3.6.0`（可打印 HTML 智能工程决策报告、AI 专题 R12 DXF 图层与 3D 视口高程图例/异常靶标升级）。

## Iteration 6 — 智能决策报告与 3D 视口升级 (`v3.6.0`)
- **版本升级**：
  1. `cpp/src/io/io.hpp/.cpp`：新增 `export_ai_report_html`（内置 SVG 3 方案费用对比图、KPI 卡片、异常点/不确定度/显著性/排水与 AI 建议表，支持打印排版）、`export_survey_recommendations_csv` 与 `export_ai_overlays_dxf`（输出 `AI_ANOMALY`、`AI_SURVEY_REC`、`AI_HAUL_FLOW`、`AI_SLOPE_DAYLIGHT` 独立 CAD 图层）。
  2. `cpp/src/ui/viewport_3d.hpp/.cpp`：新增 3D 高程/挖填色带图例浮窗，以及三维异常点与补测推荐点竖向靶标针。
  3. 同步接入成果输出菜单、「AI智能」页签导出区与命令行（`HTMLRPT` / `EXP_REC` / `EXP_AIDXF`），并通过 Test 29 导出往返校验。
- **实测结果（`python3 tools/eval_cpp_goal.py`）**：
  - 综合评分：**`100.00 / 100.00`**（0 编译警告，CTest 3/3 + 29/29 核心单测全过）
- **下一步持续迭代方向**：进入 `v3.7.0`（$O(N)$ 二维均匀空间网格加速、5000 点大规模压力测试与标高-方量/费用敏感性曲线）。

---

## Iteration 7 — O(N) 空间网格加速与标高敏感性曲线 (v3.7.0)

### 改动清单
1. **`O(N)` 二维均匀空间网格 k-NN 加速** (`cpp/src/core/ai_optimizer.cpp`)
   - 新增内部 `SpatialGrid2D` 空间哈希网格（自适应网格单元宽度，螺旋圈层邻域扩展搜索 k-NN），替代异常点检测 (`detect_survey_anomalies`) 与不确定性重采样推荐 (`analyze_uncertainty_and_recommend`) 中的 $O(N^2)$ 全量距离扫描。
2. **标高-方量/费用敏感性曲线分析** (`cpp/src/core/ai_optimizer.hpp/.cpp`, `cpp/src/io/io.hpp/.cpp`)
   - 新增 `SensitivityPoint` / `SensitivityCurveResult` 与 `run_elevation_sensitivity_curve(...)`，围绕平场平衡标高扫描多个候选设计标高，联合计算挖方量、填方量、总土方调配成本、最大/平均运距及平场经济最优标高。
   - 新增 `export_sensitivity_csv(...)` 导出多标高方案敏感性表。
3. **UI 与 AI 助手联动** (`cpp/src/core/ai_assistant.hpp/.cpp`, `cpp/src/ui/app.hpp/.cpp`)
   - `AI智能` 标签页新增敏感性分析按钮与 ImGui DrawList 实时双曲线/成本折线图可视化；命令行支持 `SENSITIVITY` / `敏感性` 指令及导出。
4. **测试验证** (`cpp/tests/test_core.cpp`, `cpp/tests/test_ui_headless.cpp`)
   - 新增 Test 30 (`30_spatial_grid_5000pts_stress_and_sensitivity_curve`) 验证 5,041 点大规模场地加速与标高敏感性曲线单调性；无头 UI 测试新增 `SENSITIVITY` 命令校验。

### 门禁验证结果
- `python3 tools/eval_cpp_goal.py`：**100.00 / 100.00**（`tin_ms=1.85ms`, `haul_ms=0.04ms`, `grade_ms=41.78ms`, `uncertainty_ms=10.56ms`, `anomaly_recall=1.00`, `anomaly_precision=0.9091`, `survey_rec_ratio=208.55x`, `nl_intent_acc=1.00`）
- Compiler warnings (project `src/` & `tests/`): **0**
- CTest: **3/3 PASS**

- **下一步持续迭代方向**：进入 `v3.8.0`（双级台阶斜坡平场联合优化 + 土方机械配置与工期造价估算）。

---

## Iteration 8 — 双级台阶斜坡平场联合优化与施工机械工期估算 (v3.8.0)

### 改动清单
1. **双级台阶斜坡平场联合优化** (`cpp/src/core/ai_optimizer.hpp/.cpp`)
   - 新增 `SteppedPlatformResult` 与 `optimize_stepped_platform(...)`：围绕 X 轴（左右台阶）及 Y 轴（南北台阶）扫描 9 档面积分割界线（0.30..0.70），按各分区三角网面积与松散/压实系数二分精确求解两级台阶零平衡标高 $(H_1, H_2)$，结合挡土墙/护坡接触线造价及跨区土方调配成本与单平场 $H_0$ 对比，自动优选总造价最低的台阶平场方案。
2. **施工机械配置与工期台班估算** (`cpp/src/core/ai_optimizer.hpp/.cpp`)
   - 新增 `FleetParams` / `FleetEstimateResult` 与 `estimate_earthwork_fleet(...)`：结合松方系数、跨区加权运距、自卸车单趟循环周期（装卸+往返运距/车速）及目标工期约束，推荐挖掘机台数、自卸车数量、推平压实机组套数、预计完工天数与机械台班总造价。
3. **助手语义路由与 CASS 工作台集成** (`cpp/src/core/ai_assistant.hpp/.cpp`, `cpp/src/ui/app.hpp/.cpp`)
   - 新增 CAD 命令 `BENCH` / `FLEET` 与中英文自然语言意图（“双级台阶”、“挡土墙”、“机械排班”、“预计工期”），并加入 Tab 自动补全与 `HELP` 清单；`AI智能` 标签页新增第 8 节参数设置与一键测算面板。
4. **单元测试与无头 UI 验证** (`cpp/tests/test_core.cpp`, `cpp/tests/test_ui_headless.cpp`)
   - 新增 Test 31 (`31_stepped_terrace_saves_cost_on_steep_slope`) 验证陡坡山地双级台阶含挡墙总造价较单平场降低 > 15%；新增 Test 32 (`32_earthwork_fleet_and_schedule_estimator`) 验证机械配置与运距-循环周期单调性。

### 门禁验证结果
- `python3 tools/eval_cpp_goal.py`：**100.00 / 100.00**（`tin_ms=1.87ms`, `haul_ms=0.07ms`, `grade_ms=44.40ms`, `uncertainty_ms=9.81ms`, `anomaly_recall=1.00`, `anomaly_precision=0.9091`, `survey_rec_ratio=208.55x`, `nl_intent_acc=1.00`, `ui_contrast=13.52`）
- Compiler warnings (project `src/` & `tests/`): **0**
- CTest: **3/3 PASS** (Core 32/32 PASS)

- **下一步持续迭代方向**：进入 `v3.9.0`（2D CAD 台阶分界可视化 + 工程量清单报价表 BOQ CSV 导出 + HTML 决策报告升级）。

---

## Iteration 9 — 台阶 CAD 可视化、BOQ 工程量清单导出与 HTML 报告升级 (v3.9.0)

### 改动清单
1. **2D CAD 台阶分界与高程徽标叠加图层** (`cpp/src/ui/cad_canvas.hpp/.cpp`, `cpp/src/ui/app.cpp`)
   - `LayerVisibility` 新增 `stepped` 图层及图层条“台阶”复选开关；在 2D CAD 模型空间按最优分割轴绘出金色虚线分界线，标注 `台阶1 H1=...m`、`台阶2 H2=...m` 及 `台阶分界 ΔH=...m` 徽标与图例项。
2. **BOQ 工程量清单报价表导出** (`cpp/src/io/io.hpp/.cpp`)
   - 新增 `export_boq_csv(...)`，基于计算结果、跨区土方调配、周边放坡、双级台阶挡墙与机械台班参数导出带 UTF-8 BOM 的分部分项工程量清单与直接工程费用汇总表 CSV。
3. **HTML 智能决策报告升级** (`cpp/src/io/io.hpp/.cpp`)
   - `export_ai_report_html(...)` 新增第 6 节（双级台阶斜坡平场对比表）与第 7 节（施工机械排班与工期台班表）。
4. **助手语义路由与测试验证** (`cpp/src/core/ai_assistant.hpp/.cpp`, `cpp/src/ui/app.hpp/.cpp`, `cpp/tests/test_core.cpp`, `cpp/tests/test_ui_headless.cpp`)
   - 新增 `EXP_BOQ` / `BOQ` 指令及“工程量清单/报价表”自然语言意图识别、菜单项与 `AI智能` 标签页导出按钮；新增 Test 33 (`33_boq_csv_and_enhanced_html_report_roundtrip`) 与无头 CAD 台阶叠层渲染校验。

### 门禁验证结果
- `python3 tools/eval_cpp_goal.py`：**100.00 / 100.00**（`tin_ms=1.91ms`, `haul_ms=0.04ms`, `grade_ms=43.58ms`, `uncertainty_ms=10.10ms`, `anomaly_recall=1.00`, `anomaly_precision=0.9091`, `survey_rec_ratio=208.55x`, `nl_intent_acc=1.00`, `ui_contrast=13.52`）
- Compiler warnings (project `src/` & `tests/`): **0**
- CTest: **3/3 PASS** (Core 33/33 PASS)

- **下一步持续迭代方向**：进入 `v3.10.0`（全量 ASan + UBSan 内存/未定义行为审计、Windows 跨平台构建配置复核、`README.md` 与 `v3.0.0-alpha → v3.10.0` 总览收尾）。
