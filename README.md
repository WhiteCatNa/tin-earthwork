# TIN 土方自动算量系统

基于 TIN（不规则三角网）的土方工程量自动计算桌面应用，适用于施工现场测量数据的土方挖填量精确计算。

## 功能简介

- **数据导入**：支持 Excel（.xlsx/.xls）、CSV、TXT、CASS .dat 格式；自动识别列映射，也可双击“源列名”手动改选；空值、非数字行会被拒绝并列出行号
- **测点改错**：在测点表中双击修改、增删测点，支持坐标平移和 X/Y 互换（测量坐标 X 为北向时使用）
- **边界设置**：在平面图上点击绘制计算边界（吸附到测点），或自动生成数据范围边界，或从 DXF / CSV / TXT 导入
- **TIN 构网**：基于 Delaunay 三角剖分；平面坐标重复但高程不同的点会报错，要求先处理，避免随机丢点
- **土方计算**：
  - 跨越计算边界的三角形按边界**精确裁剪**，只计边界以内部分（凹边界同样精确）
  - 混合挖填三角形按零填挖线分割，避免正负高差抵消
  - 支持统一设计高程、分区设计高程（未列入分区的点用统一设计高程）和文件自带的逐点设计高程
  - **挖填平衡**：一键求出挖方 = 填方的统一设计高程（精确解，取整到 mm）并按它计算
  - 边界内有区域没有测点覆盖时给出提示（覆盖率低于 99.5%）
- **结果输出**：Excel 报告（汇总、三角形明细、异常数据检查）、CSV 明细、DXF（计算边界 + 零填挖线，R12 格式）、PDF 计算书、高清图片、文本报告；导出在后台进行，界面不卡顿
- **工程存取**：保存/打开工程文件（测点、边界、设计高程、项目名称），最近工程列表；有未保存的修改时标题栏带“*”，退出或打开其他工程前会提醒保存

## 目录结构

```
tin_earthwork/
├── main.py                    # 程序入口（主窗口、工程存取）
├── version.py                 # 版本号与署名的唯一来源
├── requirements.txt           # 运行依赖
├── requirements-dev.txt       # 开发依赖（pytest、PyInstaller）
├── pytest.ini                 # 测试配置
├── tin_earthwork.spec         # PyInstaller 打包配置（macOS/Windows 共用）
├── build_app.sh               # 一键打包脚本（macOS）
├── run_full_test.py           # 端到端全流程验证脚本
├── test_data_1000.xlsx        # 示例测量数据（1000 点）
├── 运行流程图.html             # 系统运行流程示意图
├── .github/workflows/build.yml  # CI：测试通过后打包 Windows 程序
├── core/
│   ├── calculator.py          # TIN 构建、边界裁剪与土方计算
│   └── geometry.py            # 平面几何：多边形裁剪、积分、点在多边形内
├── gui/
│   ├── theme.py               # 界面配色与字体
│   ├── data_import_frame.py   # 数据导入与测点改错
│   ├── boundary_frame.py      # 边界设置
│   └── calculation_frame.py   # 计算与结果、导出
├── utils/
│   ├── data_handler.py        # 数据导入、检查与各类报告导出
│   ├── dxf_io.py              # DXF 边界读取
│   ├── plotter.py             # Matplotlib 绘图（界面图与导出图共用）
│   ├── survey_edit.py         # 测点增删改、平移、X/Y 互换
│   ├── recent_projects.py     # 最近工程列表
│   └── fileio.py              # 原子写文件
└── tests/
    ├── test_calculator.py         # 计算核心（含解析解对比）
    ├── test_geometry.py           # 几何算法
    ├── test_data_handler.py       # 导入、导出、工程文件
    ├── test_boundary.py           # 边界校验
    ├── test_plotter.py            # 绘图
    ├── test_calculation_frame.py  # 计算页
    ├── test_gui_flow.py           # 界面流程回归
    ├── test_project.py            # 工程文件恢复
    ├── test_v13.py                # 测点改错、平移、最近工程
    └── gui_stress_test.py         # 界面自动化点击压力测试（独立脚本）
```

## 环境要求

- **Python 3.13**（推荐）
- macOS / Windows / Linux，需系统提供 Tcl/Tk 支持

> macOS 14 及以上系统请使用 Python 3.13。较早版本 Python 附带的 Tcl/Tk 与新系统存在兼容问题，会导致图形界面启动后异常退出。

## 安装与运行

```bash
# 1. 克隆或解压项目
cd tin_earthwork

# 2. 创建虚拟环境（推荐）
python3.13 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动程序
python main.py
```

## 使用流程

1. **导入数据** — 选择文件后点“导入”，核对列映射（可双击改选）和数据质量检查结果，确认进入下一步
2. **设置边界** — 左键逐点添加边界顶点（右键撤销上一点），系统自动闭合；也可自动生成或从 DXF 导入
3. **计算土方** — 输入设计高程（或启用分区设计高程），点击“开始计算”；不确定设计高程时可点“挖填平衡”

计算完成后，左侧卡片直接显示总挖方、总填方、净方量和计算面积；结果页可查看挖填分色平面图、汇总表、三角形明细，点击三角形查看详情，并导出报告。
操作成功的反馈显示在底部状态栏，不再弹窗；只有错误和需要注意的提示才会弹窗。

快捷键：`Ctrl+O`（macOS 为 `⌘O`）打开工程，`Ctrl+S`（`⌘S`）保存工程，`F5` 或在设计高程框里按回车开始计算。
顶部“项目名称”会出现在所有报告的抬头；保存工程时若未起名，会用文件名作为项目名称。

## 打包为可执行程序

```bash
# macOS
pip install -r requirements-dev.txt
bash build_app.sh
```

打包产物输出至 `dist/` 目录。Windows 版本由 GitHub Actions 在测试通过后自动打包（见 `.github/workflows/build.yml`），也可在 Windows 上执行 `pyinstaller --noconfirm --clean tin_earthwork.spec`。

## 运行测试

```bash
pip install -r requirements-dev.txt

# 单元测试
pytest

# 端到端全流程验证（无需界面，自动生成数据并导出报告）
python run_full_test.py --output-dir ./验证输出

# 界面自动化点击压力测试（模拟鼠标操作遍历全流程并逐步计时）
python tests/gui_stress_test.py
```

## 技术栈

| 模块 | 用途 |
|------|------|
| `numpy` / `scipy` | Delaunay 三角剖分、数值计算 |
| `pandas` / `openpyxl` | Excel/CSV 数据读写 |
| `matplotlib` | 平面成果图（TkAgg 后端）、PDF 计算书 |
| `tkinter` | 桌面 GUI 框架 |
| `Pillow` | Matplotlib 在 Tk 中显示图像所需 |

## 开发说明

- **新增导出格式**：扩展 `utils/data_handler.py` 中的 `DataExporter` 类
- **修改计算逻辑**：编辑 `core/calculator.py` 中的 `TINEarthworkCalculator` 类；几何算法在 `core/geometry.py`
- **调整界面流程**：修改 `gui/` 下各 Frame 类，页面之间通过公开方法（如 `set_points`、`update_inputs`）同步
- **版本号**：只改 `version.py`

---

上海上铁建筑工程（集团）有限公司
