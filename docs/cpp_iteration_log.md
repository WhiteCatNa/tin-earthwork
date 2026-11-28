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
