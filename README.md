# TIN 土方自动算量系统

基于 TIN（不规则三角网）的土方工程量自动计算桌面应用，适用于施工现场测量数据的土方挖填量精确计算。

## 功能简介

- **数据导入**：支持 Excel（.xlsx/.xls）、CSV 格式的测量点数据导入，可自定义列映射
- **边界设置**：在可视化地图上点击绘制计算边界多边形，支持吸附至测量点
- **TIN 构网**：基于 Delaunay 三角剖分自动构建不规则三角网
- **土方计算**：自动识别挖方/填方区域，混合三角形按零轮廓线精确分割
- **结果输出**：生成汇总报告（Excel/TXT）及逐点明细（CSV），含三维可视化图

## 目录结构

```
tin_earthwork/
├── main.py                    # 程序入口
├── __init__.py
├── README.md
├── .gitignore
├── requirements.txt           # 运行依赖
├── requirements-dev.txt       # 开发依赖（pytest 等）
├── tin_earthwork.spec         # PyInstaller 打包配置
├── build_app.sh               # 一键打包脚本（macOS）
├── run_full_test.py           # 端到端全流程验证脚本
├── test_data_1000.xlsx        # 示例测量数据（1000 点）
├── 运行流程图.html             # 系统运行流程示意图
├── core/
│   └── calculator.py          # TIN 构建与土方计算核心算法
├── gui/
│   ├── data_import_frame.py   # 数据导入界面
│   ├── boundary_frame.py      # 边界设置界面
│   └── calculation_frame.py   # 计算与结果界面
├── utils/
│   ├── data_handler.py        # 数据读写（Excel/CSV/DXF）
│   └── plotter.py             # Matplotlib 可视化组件
└── tests/
    ├── test_calculator.py         # 计算核心单元测试
    ├── test_data_handler.py       # 数据读写单元测试
    ├── test_boundary.py           # 边界几何单元测试
    ├── test_plotter.py            # 可视化单元测试
    ├── test_calculation_frame.py  # 计算界面单元测试
    └── gui_stress_test.py         # 界面自动化点击压力测试
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

1. **导入数据** — 点击"导入文件"选择 Excel 或 CSV，映射 X/Y/Z 列后确认
2. **设置边界** — 在点云图上点击添加边界顶点，右键或双击闭合边界
3. **计算土方** — 输入设计标高，点击"开始计算"，等待结果

计算完成后可在结果页查看挖填方量汇总、三维 TIN 模型图，并导出报告。

## 打包为可执行程序

```bash
# macOS（需先安装 PyInstaller）
pip install pyinstaller
bash build_app.sh
```

打包产物输出至 `dist/` 目录。

## 运行测试

```bash
pip install -r requirements-dev.txt

# 单元测试
pytest tests/ -v

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
| `matplotlib` | TIN 三维可视化（TkAgg 后端） |
| `tkinter` | 桌面 GUI 框架 |
| `Pillow` / `tkcalendar` | UI 组件增强 |

## 开发说明

- **新增导出格式**：扩展 `utils/data_handler.py` 中的 `DataExporter` 类
- **修改计算逻辑**：编辑 `core/calculator.py` 中的 `TINEarthworkCalculator` 类
- **调整界面流程**：修改 `gui/` 下各 Frame 类

---

上海上铁建筑工程（集团）有限公司
