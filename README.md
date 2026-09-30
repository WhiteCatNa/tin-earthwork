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
  - 比较面可选：
    - **统一设计高程**；可加**分区设计高程**（个别点单独指定），文件自带的逐点设计高程同样支持
    - **斜面设计面**：基准点 (X0, Y0)、基准高程 H0、X/Y 双向坡度（%），场平排水坡常用；平面在三角形内线性，结果仍是精确值
    - **两期土方对比**：导入后期测量数据（开挖后、回填后等），前期、后期两个三角网精确叠加后计算两期之间的挖填量；两期测点位置、数量可以完全不同
  - 边界内有区域没有测点覆盖时给出提示（覆盖率低于 99.5%）；两期对比时只计两期都有测点的部分，并提示覆盖比例
- **方格网法校核**（可选）：按给定边长铺方格网，角点高程由三角网插值，每格分成两个三角形计算（三角棱柱体法），计算范围与 TIN 法一致；给出与 TIN 法的差值和相对差，并输出角点表、方格表和方格网 DXF
- **结果输出**：Excel 报告（汇总、三角形明细、异常数据检查；启用方格网时另含方格网校核、角点、方格三页）、CSV 明细、DXF（计算边界 + 零填挖线；方格网线、角点施工高度与高程、方格方量，R12 格式）、PDF 计算书、高清图片、文本报告；导出在后台进行，界面不卡顿
- **工程存取**：保存/打开工程文件（测点、边界、比较面设置、后期测点、方格网设置、项目名称），最近工程列表；1.x 的工程文件可直接打开
- **小屏适配**：窗口初始尺寸不超过屏幕；1366×768 笔记本上页头压成一行、列映射可收起、计算设置可滚动，各页主要按钮始终可见

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
│   ├── calculator.py          # TIN 构建、边界裁剪与土方计算（统一/分区/斜面/两期对比）
│   ├── geometry.py            # 平面几何：多边形裁剪、积分、点在多边形内
│   ├── surface.py             # 斜面设计面、TIN 曲面插值与外推
│   ├── overlay.py             # 两个三角网的精确叠加（两期对比）
│   └── grid_check.py          # 方格网法校核
├── gui/
│   ├── theme.py               # 界面配色与字体
│   ├── widgets.py             # 通用部件（可滚动面板）
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
    ├── test_layout.py             # 窗口排版（默认尺寸与 1366×768 下按钮不被挤出窗口）
    ├── test_design_surfaces.py    # 斜面设计面、两期对比（解析解与独立算法对照）
    ├── test_grid_check.py         # 方格网校核
    ├── test_reports_v2.py         # 2.0 报告、DXF、工程文件 v2
    ├── test_gui_v20.py            # 2.0 界面流程
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
3. **计算土方** — 选择比较面并填写参数，点击“开始计算”：
   - 统一高程：输入设计高程（可点“从数据估算”），需要时勾选“分区设计高程”单独指定个别点
   - 斜面：填写基准点、基准高程和 X/Y 向坡度（沿 +X/+Y 升高为正）；首次切换时自动填入场地中心和高程中位数
   - 两期对比：数据导入页的测点作为前期，点“导入后期测量数据”选择后期文件（格式同数据导入，自动识别列）；高差 = 前期 − 后期，正为挖除、负为填筑
   - 需要校核时勾选“方格网法校核”并填写边长（默认按场地大小建议 5/10/20 m）

计算完成后可在结果页查看挖填分色平面图、汇总表、三角形明细，点击三角形查看详情，并导出报告。
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

## 版本说明

**2.0.0**
- 新增斜面设计面、两期土方对比、方格网法校核（见“功能简介”）
- 工程文件升级为第 2 版格式，保存比较面设置、后期测点和方格网设置；1.x 工程文件可直接打开，2.0 保存的工程不能再用 1.x 打开
- 界面：页头压成一行，列映射可收起，计算设置可滚动；显示开关移到结果图上方；默认窗口下被挤出窗口的“确认无误，进入下一步”“确认边界”和导出按钮恢复可见
- 与 1.3 相比，跨越计算边界的三角形改为按边界精确裁剪（原为按质心整块取舍），老工程重新计算时方量会略有差别

## 开发说明

- **新增导出格式**：扩展 `utils/data_handler.py` 中的 `DataExporter` 类
- **修改计算逻辑**：编辑 `core/calculator.py` 中的 `TINEarthworkCalculator` 类；几何算法在 `core/geometry.py`。各种比较面最终都落到“三角形顶点高差 = 实测 − 比较面”：斜面、分区直接给测点赋设计高程，两期对比由 `core/overlay.py` 叠加出新的三角网，方格网校核通过 `build_from_mesh()` 复用同一套计算
- **调整界面流程**：修改 `gui/` 下各 Frame 类，页面之间通过公开方法（如 `set_points`、`update_inputs`）同步
- **版本号**：只改 `version.py`

---

上海上铁建筑工程（集团）有限公司
