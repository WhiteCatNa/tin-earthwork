"""
计算设置与结果页面
"""
import math
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, List, Callable, Optional, Dict, Tuple
from core.calculator import SurveyPoint, CalculationResult, TINEarthworkCalculator, format_id_list
from core.grid_check import run_grid_check, suggest_spacing
from core.surface import PlaneDesign
from utils.plotter import EarthworkPlotter, create_standalone_figure
from utils.data_handler import ISSUE_NAMES, DataExporter, DataImporter, DataValidator, report_labels
from gui.theme import COLORS, style_text_widget
from gui.widgets import ScrollableFrame, WrappingButtonRow
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import queue

DEFAULT_PROJECT_NAME = "TIN土方计算项目"
# 比较面：统一高程 / 斜面 / 两期对比
MODE_LABELS = (("flat", "统一高程"), ("plane", "斜面"), ("compare", "两期对比"))
PLANE_FIELDS = (("x0", "基准点 X0"), ("y0", "Y0"), ("h0", "基准高程 H0"), ("slope_x", "X向坡度%"), ("slope_y", "Y向坡度%"))
NET_TITLE = "净方量（挖 − 填）"


class CalculationFrame(ttk.Frame):
    """计算设置与结果页面"""
    
    def __init__(self, parent, points: List[SurveyPoint], boundary: List[Tuple[float, float]],
                 project_name_getter: Optional[Callable[[], str]] = None,
                 notify: Optional[Callable[[str], None]] = None,
                 on_settings_changed: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.project_name_getter = project_name_getter
        self.notify = notify or (lambda message: messagebox.showinfo("提示", message))
        self.on_settings_changed = on_settings_changed or (lambda: None)
        self.points = points
        self.boundary = boundary
        self.calculator = TINEarthworkCalculator()
        self.calculator.add_points(points)
        self.calculator.set_boundary(boundary)
        self.result: Optional[CalculationResult] = None
        self.design_elevation_var = tk.DoubleVar(value=self.calculator.design_elevation)
        imported_design = [point for point in points if point.has_design_z]
        unique_design = {round(point.design_z, 6) for point in imported_design}
        self.use_partition_var = tk.BooleanVar(value=len(unique_design) > 1)
        self.partition_data: Dict[str, float] = (
            {point.id: point.design_z for point in imported_design} if len(unique_design) > 1 else {}
        )
        self.design_mode_var = tk.StringVar(value="flat")
        self.plane_vars = {key: tk.StringVar(value="0") for key, _ in PLANE_FIELDS}
        self.compare_points: List[SurveyPoint] = []
        self.compare_source = ""
        self.grid_enabled_var = tk.BooleanVar(value=False)
        self.grid_spacing_var = tk.StringVar(value=f"{self._suggested_spacing():g}")
        self._detail_rows = []
        self._detail_load_after_id = None
        self._detail_batch_size = 200
        self._detail_load_token = 0
        self._worker_events = queue.Queue()
        self._worker_thread = None
        self._worker_poll_after_id = None
        self._export_events = queue.Queue()
        self._export_thread = None
        self._export_poll_after_id = None
        self._export_buttons: List[ttk.Button] = []
        self._closing = False
        self._pending_export_path = ""
        self._balanced_level: Optional[float] = None

        self._create_widgets()
        # 控件建好后再挂监听：初始化时的赋值不算“用户改了设置”
        for variable in (self.design_elevation_var, self.use_partition_var, self.design_mode_var,
                         self.grid_enabled_var, self.grid_spacing_var, *self.plane_vars.values()):
            variable.trace_add("write", lambda *_: self.on_settings_changed())

    def _project_name(self) -> str:
        name = self.project_name_getter() if self.project_name_getter else ""
        return (name or "").strip() or DEFAULT_PROJECT_NAME

    def design_elevation_value(self) -> float:
        """读取统一设计高程；输入框不是数字时给出明确提示。"""
        try:
            return float(self.design_elevation_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("统一设计高程必须是数字") from None

    def update_inputs(self, points: List[SurveyPoint], boundary: List[Tuple[float, float]]):
        """测点或边界变化后调用：同步计算器并作废旧结果。"""
        self.points = points
        self.boundary = boundary
        self.calculator.add_points(points)
        self.calculator.set_boundary(boundary)
        self._invalidate_result()

    def _invalidate_result(self):
        """输入变了，旧结果作废：结果卡片标记为需重新计算，并重画测点图。"""
        had_result = self.result is not None
        self.result = None
        self._show_result_cards(None, stale=had_result)
        self._refresh_plot()

    def apply_design_settings(self, design_elevation: float, use_partition: bool, partition: Dict[str, float],
                              design_mode: str = "flat", design_plane: Optional[Dict[str, float]] = None,
                              compare_points: Optional[List[SurveyPoint]] = None, compare_source: str = "",
                              grid_enabled: bool = False, grid_spacing: Optional[float] = None):
        """恢复工程文件中的比较面、分区、后期测点和方格网设置。"""
        self.design_elevation_var.set(design_elevation)
        self.use_partition_var.set(bool(use_partition))
        self.partition_data = dict(partition or {})
        self._toggle_partition()
        if design_plane:
            plane = PlaneDesign.from_dict(design_plane)
            for key, _ in PLANE_FIELDS:
                self.plane_vars[key].set(f"{getattr(plane, key):g}")
        self.set_compare_points(list(compare_points or []), compare_source)
        self.grid_enabled_var.set(bool(grid_enabled))
        if grid_spacing:
            self.grid_spacing_var.set(f"{grid_spacing:g}")
        self.design_mode_var.set(design_mode if design_mode in dict(MODE_LABELS) else "flat")
        self._on_mode_change()
        overrides = self.partition_data if self.use_partition_var.get() else {}
        if self.partition_data and self.use_partition_var.get():
            self.partition_label.config(
                text=f"已设置 {len(self.partition_data)} 个分区高程", foreground=COLORS["ink"]
            )
        if self.design_mode_var.get() == "plane" and design_plane:
            self.calculator.set_design_plane(PlaneDesign.from_dict(design_plane), overrides)
        elif overrides:
            self.calculator.set_design_elevations(overrides, default=design_elevation)
        else:
            self.calculator.set_design_elevation(design_elevation)
        self._invalidate_result()

    def design_settings(self) -> Dict[str, Any]:
        """当前的比较面与方格网设置，供保存工程。输入框不是数字时抛出 ValueError。"""
        mode = self.design_mode_var.get()
        plane = None
        try:
            plane = self.plane_value().to_dict()
        except ValueError:
            if mode == "plane":
                raise
        spacing = None
        try:
            spacing = self.grid_spacing_value()
        except ValueError:
            if self.grid_enabled_var.get():
                raise
        return {
            "design_mode": mode,
            "design_elevation": self.design_elevation_value(),
            "design_plane": plane,
            "use_partition": bool(self.use_partition_var.get()),
            "partition": dict(self.partition_data),
            "compare_points": list(self.compare_points),
            "compare_source": self.compare_source,
            "grid_enabled": bool(self.grid_enabled_var.get()),
            "grid_spacing": spacing,
        }

    def plane_value(self) -> PlaneDesign:
        values = {}
        for key, label in PLANE_FIELDS:
            try:
                values[key] = float(self.plane_vars[key].get())
            except ValueError:
                raise ValueError(f"斜面参数“{label}”必须是数字") from None
            if not math.isfinite(values[key]):
                raise ValueError(f"斜面参数“{label}”必须是有限数字")
        return PlaneDesign(**values)

    def grid_spacing_value(self) -> float:
        try:
            spacing = float(self.grid_spacing_var.get())
        except ValueError:
            raise ValueError("方格边长必须是数字") from None
        if not math.isfinite(spacing) or spacing <= 0:
            raise ValueError("方格边长必须大于 0")
        return spacing

    def _site_extent(self) -> Optional[Tuple[float, float, float, float]]:
        coords = list(self.boundary) or [(p.x, p.y) for p in self.points]
        if not coords:
            return None
        xs = [float(x) for x, _ in coords]
        ys = [float(y) for _, y in coords]
        return min(xs), min(ys), max(xs), max(ys)

    def _suggested_spacing(self) -> float:
        extent = self._site_extent()
        if extent is None:
            return 10.0
        xmin, ymin, xmax, ymax = extent
        return suggest_spacing(xmax - xmin, ymax - ymin)

    def _fill_plane_defaults(self, include_height: bool = True):
        """基准点取场地中心；include_height 时基准高程取测点高程中位数。坡度保持不变。"""
        extent = self._site_extent()
        if extent is None:
            return
        xmin, ymin, xmax, ymax = extent
        self.plane_vars["x0"].set(f"{(xmin + xmax) / 2:.3f}")
        self.plane_vars["y0"].set(f"{(ymin + ymax) / 2:.3f}")
        if include_height and self.points:
            zs = sorted(p.z for p in self.points)
            middle = len(zs) // 2
            median = zs[middle] if len(zs) % 2 else (zs[middle - 1] + zs[middle]) / 2
            self.plane_vars["h0"].set(f"{median:.3f}")

    def set_compare_points(self, points: List[SurveyPoint], source: str = ""):
        """设置两期对比的后期测点（导入文件或恢复工程时调用）。"""
        self.compare_points = list(points)
        self.compare_source = source
        had_result = self.result is not None
        self.result = None
        self._show_result_cards(None, stale=had_result)
        self.on_settings_changed()
        if hasattr(self, "compare_label"):
            if self.compare_points:
                name = os.path.basename(source) if source else "工程文件"
                self.compare_label.config(text=f"已导入 {len(self.compare_points)} 个后期测点（{name}）",
                                          foreground=COLORS["ink"])
            else:
                self.compare_label.config(text="未导入后期测量数据", foreground=COLORS["dim"])

    def _create_widgets(self):
        # 左侧：设置面板。“开始计算”和进度固定在底部；设置项放在可滚动区域，窗口偏矮时滚动查看
        left_frame = ttk.LabelFrame(self, text="计算设置", padding=12)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 6), pady=10)

        bottom = ttk.Frame(left_frame)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Separator(bottom, orient='horizontal').pack(fill=tk.X, pady=(4, 8))
        self.calculate_button = ttk.Button(
            bottom,
            text="开始计算（F5）",
            command=self.start_calculation,
            style='Accent.TButton'
        )
        self.calculate_button.pack(fill=tk.X, ipady=5)
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(bottom, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=8)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self.status_var, style="Muted.TLabel").pack(anchor=tk.W)

        self.settings = ScrollableFrame(left_frame)
        self.settings.pack(fill=tk.BOTH, expand=True)
        body = self.settings.body

        # 比较面：统一高程 / 斜面 / 两期对比
        ttk.Label(body, text="比较面", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 4))
        mode_row = ttk.Frame(body)
        mode_row.pack(fill=tk.X)
        for value, label in MODE_LABELS:
            ttk.Radiobutton(mode_row, text=label, value=value, variable=self.design_mode_var,
                            command=self._on_mode_change).pack(side=tk.LEFT, padx=(0, 10))

        self.mode_container = ttk.Frame(body)
        self.mode_container.pack(fill=tk.X, pady=(6, 0))

        self.flat_frame = ttk.Frame(self.mode_container)
        flat_row = ttk.Frame(self.flat_frame)
        flat_row.pack(fill=tk.X)
        ttk.Label(flat_row, text="设计高程 (m)").pack(side=tk.LEFT)
        elevation_entry = ttk.Entry(flat_row, textvariable=self.design_elevation_var, width=10)
        elevation_entry.pack(side=tk.LEFT, padx=6)
        elevation_entry.bind("<Return>", lambda _event: self.start_calculation())
        ttk.Button(flat_row, text="从数据估算", command=self._estimate_elevation).pack(side=tk.LEFT)
        balance_row = ttk.Frame(self.flat_frame)
        balance_row.pack(fill=tk.X, pady=(6, 0))
        self.balance_button = ttk.Button(balance_row, text="挖填平衡", command=self._balance_elevation)
        self.balance_button.pack(side=tk.LEFT)
        ttk.Label(balance_row, text="求挖方 = 填方的设计高程并计算", style="Muted.TLabel").pack(
            side=tk.LEFT, padx=6)

        self.plane_frame = ttk.Frame(self.mode_container)
        positions = {"x0": (0, 0), "y0": (0, 1), "h0": (1, 0), "slope_x": (2, 0), "slope_y": (2, 1)}
        for key, label in PLANE_FIELDS:
            row, column = positions[key]
            ttk.Label(self.plane_frame, text=label).grid(row=row, column=column * 2, sticky="w", pady=2)
            ttk.Entry(self.plane_frame, textvariable=self.plane_vars[key], width=10).grid(
                row=row, column=column * 2 + 1, sticky="w", padx=(4, 8), pady=2)
        ttk.Button(self.plane_frame, text="基准点取场地中心",
                   command=lambda: self._fill_plane_defaults(include_height=False)).grid(
            row=1, column=2, columnspan=2, sticky="ew", pady=2)
        ttk.Label(self.plane_frame, text="坡度沿 +X / +Y 方向升高为正", style="Muted.TLabel").grid(
            row=3, column=0, columnspan=4, sticky="w")

        self.compare_frame = ttk.Frame(self.mode_container)
        compare_buttons = ttk.Frame(self.compare_frame)
        compare_buttons.pack(fill=tk.X)
        ttk.Button(compare_buttons, text="导入后期测量数据…", command=self._import_compare_points).pack(side=tk.LEFT)
        ttk.Button(compare_buttons, text="后期 X/Y 互换", command=self._swap_compare_xy).pack(side=tk.LEFT, padx=6)
        self.compare_label = ttk.Label(self.compare_frame, style="Muted.TLabel", text="未导入后期测量数据")
        self.compare_label.pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(self.compare_frame, text="前期 = 数据导入页的测点；高差 = 前期 − 后期",
                  style="Muted.TLabel").pack(anchor=tk.W)

        # 分区设计高程（统一高程、斜面时可用）
        self.partition_check = ttk.Checkbutton(body, text="分区设计高程（个别点单独指定）",
                                               variable=self.use_partition_var, command=self._toggle_partition)
        self.partition_check.pack(anchor=tk.W, pady=(8, 0))

        self.partition_frame = ttk.Frame(body)
        self.partition_frame.pack(fill=tk.X, pady=6)
        ttk.Button(self.partition_frame, text="编辑分区高程", command=self._edit_partition).pack(fill=tk.X)
        self.partition_label = ttk.Label(self.partition_frame, text="未设置", style="Muted.TLabel")
        self.partition_label.pack(anchor=tk.W, pady=6)
        self._toggle_partition()
        if self.partition_data:
            self.partition_label.config(
                text=f"已从文件导入 {len(self.partition_data)} 个分区高程",
                foreground=COLORS["ink"],
            )
        self.update_idletasks()
        widest = max(frame.winfo_reqwidth() for frame in (self.flat_frame, self.plane_frame, self.compare_frame))
        ttk.Frame(self.mode_container, width=widest, height=1).pack(side=tk.BOTTOM)
        self._on_mode_change()

        # 方格网校核
        ttk.Separator(body, orient='horizontal').pack(fill=tk.X, pady=8)
        grid_row = ttk.Frame(body)
        grid_row.pack(fill=tk.X)
        ttk.Checkbutton(grid_row, text="方格网法校核", variable=self.grid_enabled_var).pack(side=tk.LEFT)
        ttk.Label(grid_row, text="边长 (m)").pack(side=tk.LEFT, padx=(10, 4))
        ttk.Entry(grid_row, textvariable=self.grid_spacing_var, width=6).pack(side=tk.LEFT)

        self.show_tin_var = tk.BooleanVar(value=True)
        self.show_contour_var = tk.BooleanVar(value=True)
        self.show_points_var = tk.BooleanVar(value=True)
        self.show_boundary_var = tk.BooleanVar(value=True)

        # 右侧：结果显示
        right_frame = ttk.Frame(self)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 10), pady=10)

        # 底部导出按钮：先于结果选项卡 pack（side=BOTTOM），窗口偏矮时由图形区收缩，按钮不被挤掉
        # 窗口较窄（如 1024×768 屏幕）时自动折成两行，按钮不会被挤掉
        export_frame = WrappingButtonRow(right_frame)
        export_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        for text, command in (
            ("导出 Excel 报告", self._export_excel),
            ("导出 CSV 明细", self._export_csv),
            ("导出 DXF", self._export_dxf),
            ("导出 PDF 计算书", self._export_pdf),
            ("导出高清图片", self._export_image),
            ("生成文本报告", self._export_text),
        ):
            self._export_buttons.append(export_frame.add(text, command))

        # 结果卡片：计算完成后直接显示主要数字，不必每次看弹窗。
        # 横排放在结果选项卡上方，左栏留给计算设置（小屏上左栏高度紧张）
        cards = ttk.Frame(right_frame)
        cards.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self._cards = {}
        for column, (key, title, kind, unit) in enumerate((
            ("cut", "总挖方", "Cut", "m³"),
            ("fill", "总填方", "Fill", "m³"),
            ("net", NET_TITLE, "Net", "m³"),
            ("area", "计算面积", "Net", "m²"),
        )):
            cards.grid_columnconfigure(column, weight=1, uniform="cards")
            card = ttk.Frame(cards, style=f"{kind}Card.TFrame", padding=(10, 3))
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
            title_label = ttk.Label(card, text=title, style=f"{kind}CardTitle.TLabel")
            title_label.pack(anchor=tk.W)
            value_label = ttk.Label(card, text="—", style=f"{kind}CardValue.TLabel")
            value_label.pack(anchor=tk.W)
            self._cards[key] = (title_label, value_label, title, unit)

        # 结果选项卡
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # 选项卡1：图形显示（显示开关放在图上方，左栏留给计算设置）
        self.plot_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.plot_frame, text="计算结果图")
        toggles = ttk.Frame(self.plot_frame)
        toggles.pack(fill=tk.X, padx=4, pady=(4, 0))
        ttk.Label(toggles, text="显示：", style="Muted.TLabel").pack(side=tk.LEFT)
        for text, variable in (("TIN网格", self.show_tin_var), ("零填挖线", self.show_contour_var),
                               ("测量点", self.show_points_var), ("计算边界", self.show_boundary_var)):
            ttk.Checkbutton(toggles, text=text, variable=variable, command=self._refresh_plot).pack(
                side=tk.LEFT, padx=(0, 10))

        self.plotter = EarthworkPlotter(figsize=(10, 7))
        self.canvas = FigureCanvasTkAgg(self.plotter.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.plotter.bind_canvas(self.canvas)
        self.plotter.on_triangle_click = self._on_triangle_selected
        
        # 选项卡2：汇总表
        self.summary_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.summary_frame, text="汇总表")
        self._create_summary_table()
        
        # 选项卡3：明细表
        self.detail_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.detail_frame, text="计算明细")
        self._create_detail_table()
        
        # 选项卡4：三角形信息 (点击三角形后显示)
        self.info_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.info_frame, text="三角形详情")
        self._create_triangle_info()

    def _create_summary_table(self):
        """创建汇总表"""
        columns = ('项目', '数值', '单位', '说明')
        self.tree_summary = ttk.Treeview(self.summary_frame, columns=columns, show='headings', height=10)
        # 比较面说明、提示等文字较长：数值列放宽，说明列靠左并随窗口伸展
        for col, width, anchor, stretch in (('项目', 130, 'center', False), ('数值', 220, 'center', False),
                                            ('单位', 50, 'center', False), ('说明', 360, 'w', True)):
            self.tree_summary.heading(col, text=col)
            self.tree_summary.column(col, width=width, anchor=anchor, stretch=stretch)
        # 提示往往是长句，放在表下方自动换行显示，不在表格里被截断
        self.summary_warnings_var = tk.StringVar(value="")
        self.summary_warnings = ttk.Label(
            self.summary_frame, textvariable=self.summary_warnings_var, foreground=COLORS["cut"],
            justify=tk.LEFT, wraplength=600,
        )
        self.summary_warnings.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 10))
        self.summary_warnings.bind(
            "<Configure>", lambda event: self.summary_warnings.config(wraplength=max(event.width - 10, 200))
        )
        self.tree_summary.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        
    def _create_detail_table(self):
        """创建明细表"""
        columns = ('三角形', '顶点1', '顶点2', '顶点3', '边界内面积(m²)', '平均高差(m)',
                  '挖方量(m³)', '填方量(m³)', '净体积(m³)', '类型')
        self.tree_detail = ttk.Treeview(self.detail_frame, columns=columns, show='headings', height=20)
        for col in columns:
            self.tree_detail.heading(col, text=col)
            self.tree_detail.column(col, width=100, anchor='center')
            
        vsb = ttk.Scrollbar(self.detail_frame, orient="vertical", command=self.tree_detail.yview)
        hsb = ttk.Scrollbar(self.detail_frame, orient="horizontal", command=self.tree_detail.xview)
        self.tree_detail.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree_detail.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        self.detail_frame.grid_rowconfigure(0, weight=1)
        self.detail_frame.grid_columnconfigure(0, weight=1)
        
    def _create_triangle_info(self):
        """创建三角形详情面板"""
        self.info_text = tk.Text(self.info_frame, wrap=tk.WORD)
        style_text_widget(self.info_text)
        self.info_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.info_text.insert('1.0', '点击左侧图形中的三角形查看详细信息...')
        self.info_text.config(state=tk.DISABLED)
        
    def _estimate_elevation(self):
        """从数据估算设计高程 (中位数)"""
        import numpy as np
        zs = [p.z for p in self.points]
        median_z = np.median(zs)
        self.design_elevation_var.set(round(median_z, 3))
        self.notify(f"设计高程已设为实测高程中位数 {median_z:.3f} m")
        
    def _on_mode_change(self):
        """切换比较面：只显示对应的输入；两期对比时分区高程不适用。"""
        mode = self.design_mode_var.get()
        for frame, value in ((self.flat_frame, "flat"), (self.plane_frame, "plane"), (self.compare_frame, "compare")):
            if value == mode:
                frame.pack(fill=tk.X)
            else:
                frame.pack_forget()
        if mode == "plane" and all(var.get() in ("", "0") for key, var in self.plane_vars.items()
                                   if key in ("x0", "y0", "h0")):
            self._fill_plane_defaults()
        compare = mode == "compare"
        self.partition_check.config(state=tk.DISABLED if compare else tk.NORMAL)
        if compare:
            self.partition_frame.pack_forget()
        else:
            self._toggle_partition()

    def _import_compare_points(self):
        filepath = filedialog.askopenfilename(
            title="导入后期测量数据",
            filetypes=[("测量数据", "*.xlsx *.xls *.csv *.txt *.dat"), ("所有文件", "*.*")],
        )
        if filepath:
            self.load_compare_file(filepath)

    def load_compare_file(self, filepath: str) -> bool:
        """导入后期测点：自动识别列，显示识别结果与数据检查，确认后生效。"""
        try:
            points, issues, df = DataImporter.import_file(filepath)
        except Exception as error:
            messagebox.showerror("导入失败", str(error))
            return False
        if len(points) < 3:
            messagebox.showerror("导入失败", "后期测量至少需要 3 个有效测点")
            return False
        mapping = DataImporter.complete_column_mapping(df, DataValidator.auto_detect_columns(df))
        names = {"id": "点号", "x": "X", "y": "Y", "z": "高程"}
        mapping_text = "，".join(f"{names[key]}={mapping[key]}" for key in names if key in mapping)
        problems = [f"{ISSUE_NAMES[key]} {len(items)} 条" for key, items in issues.items() if items]
        message = f"识别到 {len(points)} 个后期测点。\n列映射：{mapping_text}"
        if problems:
            message += "\n\n数据检查：" + "；".join(problems) + "\n（坐标重复且高程不同的点会在计算时报错）"
        if not messagebox.askokcancel("确认后期测量数据", message + "\n\n使用这批数据作为后期测量？"):
            return False
        self.set_compare_points(points, filepath)
        return True

    def _swap_compare_xy(self):
        if not self.compare_points:
            messagebox.showinfo("提示", "请先导入后期测量数据")
            return
        swapped = [SurveyPoint(p.id, p.y, p.x, p.z) for p in self.compare_points]
        self.set_compare_points(swapped, self.compare_source)
        self.compare_label.config(text=self.compare_label.cget("text") + "，已互换 X/Y")

    def _toggle_partition(self):
        """切换分区高程编辑状态"""
        if self.use_partition_var.get():
            # after= 保证重新显示时仍紧跟在复选框下面，而不是排到面板最底部
            self.partition_frame.pack(fill=tk.X, pady=5, after=self.partition_check)
        else:
            self.partition_frame.pack_forget()
            
    def _edit_partition(self):
        """编辑分区设计高程对话框"""
        dialog = tk.Toplevel(self)
        dialog.title("分区设计高程设置")
        dialog.geometry("500x400")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        
        ttk.Label(dialog, text="为选定点号设置不同的设计高程 (点号,高程 每行一条):").pack(anchor=tk.W, padx=12, pady=8)
        
        text_frame = ttk.Frame(dialog)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        
        text = tk.Text(text_frame)
        style_text_widget(text)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 加载现有数据
        for pid, elev in self.partition_data.items():
            text.insert('end', f"{pid},{elev}\n")
            
        def save():
            data, bad_lines = parse_partition_text(text.get('1.0', 'end-1c'))
            known_ids = {point.id for point in self.points}
            unknown = [pid for pid in data if pid not in known_ids]
            problems = []
            if bad_lines:
                problems.append("以下行格式不对（应为 点号,高程），将被忽略：\n" + "\n".join(bad_lines[:10]))
            if unknown:
                problems.append("以下点号在测点表中不存在：" + "、".join(unknown[:10])
                                + (f" 等共 {len(unknown)} 个" if len(unknown) > 10 else ""))
            if problems and not messagebox.askokcancel(
                "请核对分区高程", "\n\n".join(problems) + "\n\n仍要保存吗？", parent=dialog
            ):
                return
            self.partition_data = data
            self.partition_label.config(text=f"已设置 {len(self.partition_data)} 个分区高程", foreground=COLORS["ink"])
            self.on_settings_changed()
            dialog.destroy()
            
        ttk.Button(dialog, text="保存", command=save, style="Accent.TButton").pack(pady=10)
        
    def start_calculation(self):
        """开始计算（按钮、回车和 F5 共用）；计算进行中时忽略。"""
        if self._worker_thread is None:
            self._run_calculation()

    def _balance_elevation(self):
        """求挖填平衡的统一设计高程，填入输入框并按它计算。只适用于统一高程、未启用分区。"""
        if self.design_mode_var.get() != "flat":
            messagebox.showwarning("提示", "挖填平衡只适用于统一设计高程，请先把比较面切换到“统一高程”")
            return
        if self.use_partition_var.get() and self.partition_data:
            messagebox.showwarning("提示", "挖填平衡只适用于统一设计高程，请先取消“分区设计高程”")
            return
        if self._worker_thread is None:
            self._run_calculation(balance=True)

    def _run_calculation(self, balance: bool = False):
        """运行计算 (在后台线程)；balance=True 时先求挖填平衡高程再按它计算。"""
        if not self.points:
            messagebox.showwarning("提示", "没有可计算的测量点")
            return

        # Tkinter 控件只能由主线程访问；在线程启动前读取所需配置。
        mode = "flat" if balance else self.design_mode_var.get()
        use_partition = self.use_partition_var.get() and mode != "compare" and not balance
        partition_data = self.partition_data.copy() if use_partition else {}
        compare_points = list(self.compare_points)
        try:
            try:
                design_elevation = self.design_elevation_value()
            except ValueError:
                if not balance:
                    raise
                design_elevation = 0.0  # 求平衡高程与起始值无关
            plane = self.plane_value() if mode == "plane" else None
            grid_spacing = self.grid_spacing_value() if self.grid_enabled_var.get() else None
        except ValueError as error:
            messagebox.showerror("输入错误", str(error))
            return
        if mode == "compare" and not compare_points:
            messagebox.showwarning("提示", "两期对比需要先导入后期测量数据")
            return

        self.status_var.set("正在计算...")
        self.progress_var.set(0)
        self._balanced_level = None
        self.calculate_button.config(state=tk.DISABLED)
        self.balance_button.config(state=tk.DISABLED)
        self.update_idletasks()

        def calc_thread():
            try:
                calculator = self.calculator
                calculator.set_compare_points(compare_points if mode == "compare" else None)
                if balance:
                    self._worker_events.put(("progress", 20, "求挖填平衡高程..."))
                    calculator.set_design_elevation(design_elevation)
                    level = round(calculator.balanced_design_elevation(), 3)
                    self._worker_events.put(("balanced", level))
                    calculator.set_design_elevation(level)
                elif plane is not None:
                    calculator.set_design_plane(plane, partition_data)
                elif partition_data:
                    # 未列入分区的点使用界面上的统一设计高程
                    calculator.set_design_elevations(partition_data, default=design_elevation)
                else:
                    calculator.set_design_elevation(design_elevation)

                stage = "叠加前期、后期三角网..." if mode == "compare" else "构建 TIN 三角网..."
                self._worker_events.put(("progress", 30, stage))
                result = calculator.run_full_calculation()
                if partition_data:
                    unlisted = [str(p.id) for p in calculator.points if p.id not in partition_data]
                    if unlisted:
                        base = "斜面" if plane is not None else f"统一设计高程 {design_elevation:.3f} m"
                        result.warnings.insert(0, (
                            f"有 {len(unlisted)} 个测点不在分区高程表中，按{base} 计算：{format_id_list(unlisted)}"
                        ))
                if grid_spacing is not None:
                    self._worker_events.put(("progress", 75, "方格网校核..."))
                    try:
                        result.grid_check = run_grid_check(calculator, result, grid_spacing)
                    except ValueError as error:
                        result.warnings.append(f"方格网校核未完成：{error}")
                self._worker_events.put(("progress", 90, "更新显示..."))
                self._worker_events.put(("done", result))
            except Exception as error:
                self._worker_events.put(("error", error))

        self._worker_events = queue.Queue()
        self._worker_thread = threading.Thread(target=calc_thread, daemon=True)
        self._worker_thread.start()
        self._schedule_worker_poll()

    def _schedule_worker_poll(self):
        """在 Tk 主线程轮询后台计算事件。"""
        if self._closing or self._worker_poll_after_id is not None:
            return
        self._worker_poll_after_id = self.after(20, self._poll_worker_events)

    def _poll_worker_events(self):
        """处理后台线程生成的纯 Python 事件。"""
        self._worker_poll_after_id = None
        if self._closing:
            return

        terminal_event = False
        while True:
            try:
                event = self._worker_events.get_nowait()
            except queue.Empty:
                break

            if event[0] == "progress":
                self._update_calculation_progress(event[1], event[2])
            elif event[0] == "balanced":
                self.use_partition_var.set(False)
                self._toggle_partition()
                self.design_elevation_var.set(event[1])
                self._balanced_level = event[1]
            elif event[0] == "done":
                self._worker_thread = None
                self.result = event[1]
                self._on_calculation_done()
                terminal_event = True
            elif event[0] == "error":
                self._worker_thread = None
                self._on_calculation_error(event[1])
                terminal_event = True

        if not terminal_event and self._worker_thread is not None:
            self._schedule_worker_poll()

    def _update_calculation_progress(self, progress: float, status: str):
        """在主线程更新计算进度。"""
        self.progress_var.set(progress)
        self.status_var.set(status)
        
    def _on_calculation_done(self):
        self.progress_var.set(100)
        self.status_var.set("计算完成")
        self.calculate_button.config(state=tk.NORMAL)
        self.balance_button.config(state=tk.NORMAL)
        self._refresh_plot()
        self._update_summary_table()
        self._start_detail_table_load()
        self._show_result_cards(self.result)
        summary = (
            f"总挖方量: {self.result.total_cut:.2f} m³\n"
            f"总填方量: {self.result.total_fill:.2f} m³\n"
            f"挖填差值: {self.result.net_volume:.2f} m³\n"
            f"计算面积: {self.result.computed_area:.2f} m²\n"
            f"三角形数: {self.result.triangle_count}\n"
            f"混合三角形: {self.result.mixed_triangle_count}"
        )
        warnings = list(self.result.warnings)
        grid = self.result.grid_check
        if grid is not None:
            summary += f"\n\n方格网校核（边长 {grid.spacing:g} m）:"
            for label, _, grid_value, diff, relative in grid.comparison(self.result)[:3]:
                percent = "" if relative is None else f"，{relative:+.2f}%"
                summary += f"\n  {label} {grid_value:.2f} m³（与 TIN 法相差 {diff:+.2f}{percent}）"
            warnings += grid.warnings
        if warnings:
            self.status_var.set("计算完成（有提示，请查看）")
            messagebox.showwarning(
                "计算完成，请注意",
                summary + "\n\n" + "\n\n".join(f"· {item}" for item in warnings),
            )
            return
        # 没有需要注意的提示：结果已在卡片和汇总表里，状态栏给一句反馈即可，不再弹窗
        brief = (
            f"挖方 {self.result.total_cut:,.2f} m³，填方 {self.result.total_fill:,.2f} m³，"
            f"净方量 {self.result.net_volume:+,.2f} m³"
        )
        if grid is not None:
            brief += "；方格网校核结果见汇总表"
        if self._balanced_level is not None:
            self.notify(f"挖填平衡设计高程 {self._balanced_level:.3f} m（取整到 mm）：{brief}")
        else:
            self.notify(f"计算完成：{brief}")

    def _show_result_cards(self, result: Optional[CalculationResult], stale: bool = False):
        """刷新结果卡片；result 为 None 时显示占位（stale 表示旧结果已作废）。"""
        if not hasattr(self, "_cards"):
            return
        if result is None:
            for title_label, value_label, title, _unit in self._cards.values():
                title_label.config(text=title)
                value_label.config(text="需重新计算" if stale else "—")
            return
        net = result.net_volume
        values = {
            "cut": result.total_cut,
            "fill": result.total_fill,
            "net": net,
            "area": result.computed_area,
        }
        for key, (title_label, value_label, title, unit) in self._cards.items():
            number = f"{values[key]:+,.2f}" if key == "net" else f"{values[key]:,.2f}"
            value_label.config(text=f"{number} {unit}")
            title_label.config(text=title)
        net_title = self._cards["net"][0]
        # 设计高程取到 mm，平衡时净方量最多剩 0.0005 m × 计算面积
        if abs(net) <= 0.0005 * result.computed_area + 0.005:
            net_title.config(text=f"{NET_TITLE}· 基本平衡")
        else:
            net_title.config(text=f"{NET_TITLE}· {'余方外运' if net > 0 else '缺方借土'}")

    def _on_calculation_error(self, error):
        self.status_var.set("计算失败")
        self.progress_var.set(0)
        self.calculate_button.config(state=tk.NORMAL)
        self.balance_button.config(state=tk.NORMAL)
        messagebox.showerror("计算错误", str(error))
        
    def _refresh_plot(self):
        if self.result:
            self.plotter.plot_result(
                self.result, self.points,
                show_tin=self.show_tin_var.get(),
                show_contour=self.show_contour_var.get(),
                show_points=self.show_points_var.get(),
                show_boundary=self.show_boundary_var.get()
            )
        else:
            self.plotter.plot_points_only(self.points)
            
    def _update_summary_table(self):
        """立即更新体积汇总；大明细表由后台事件循环分批填充。"""
        if not self.result:
            return

        self.tree_summary.delete(*self.tree_summary.get_children())
        for row in DataExporter.summary_rows(self.result):
            self.tree_summary.insert('', 'end', values=row)
        grid_warnings = self.result.grid_check.warnings if self.result.grid_check is not None else []
        self.summary_warnings_var.set(
            "\n\n".join(f"提示：{item}" for item in list(self.result.warnings) + list(grid_warnings))
        )

    def _start_detail_table_load(self):
        """分批写入明细，避免大量 Treeview 行阻塞主界面。"""
        self._detail_load_token += 1
        if self._detail_load_after_id is not None:
            self.after_cancel(self._detail_load_after_id)
            self._detail_load_after_id = None
        self.tree_detail.delete(*self.tree_detail.get_children())
        if not self.result:
            self._detail_rows = []
            return
        self._detail_rows = [tri for tri in self.result.triangles if not tri.is_boundary]
        self._load_detail_batch(0, self._detail_load_token)

    def _load_detail_batch(self, start: int, token: int):
        """写入一批明细行，并把其余工作让回 Tk 事件循环。"""
        if token != self._detail_load_token:
            return
        end = min(start + self._detail_batch_size, len(self._detail_rows))
        for tri in self._detail_rows[start:end]:
            p0, p1, p2 = tri.vertex_points
            tri_type = "混合" if tri.is_mixed else ("挖方" if tri.volume > 0 else "填方")
            if tri.is_clipped:
                tri_type += "（边界裁剪）"
            self.tree_detail.insert('', 'end', values=(
                tri.id,
                f"{p0.id}({p0.delta_z:+.3f})",
                f"{p1.id}({p1.delta_z:+.3f})",
                f"{p2.id}({p2.delta_z:+.3f})",
                f"{tri.area:.2f}",
                f"{tri.avg_delta_z:.3f}" if not tri.is_mixed else "分割计算",
                f"{tri.cut_volume:.3f}",
                f"{tri.fill_volume:.3f}",
                f"{tri.volume:.3f}",
                tri_type
            ))
        if end < len(self._detail_rows):
            self._detail_load_after_id = self.after(1, self._load_detail_batch, end, token)
        else:
            self._detail_load_after_id = None

    def _on_triangle_selected(self, tri):
        """三角形被点击时显示详情"""
        self.notebook.select(self.info_frame)
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete('1.0', 'end')
        
        labels = report_labels(self.result) if self.result is not None else report_labels(CalculationResult())
        natural, design = labels["natural"][:2], labels["design"][:2]
        vertex_lines = "\n".join(
            f"  顶点{index}: {p.id}  X={p.x:.3f}  Y={p.y:.3f}  {natural}={p.z:.3f}  {design}={p.design_z:.3f}"
            f"  高差={p.delta_z:+.3f}"
            for index, p in enumerate(tri.vertex_points, 1)
        )
        info = f"""三角形详细信息
{'='*50}
三角形编号: {tri.id}
是否被边界裁剪: {'是（只计边界内部分）' if tri.is_clipped else '否'}
是否混合挖填: {'是' if tri.is_mixed else '否'}
边界内水平面积: {tri.area:.3f} m²

顶点信息:
{vertex_lines}

计算结果:
  平均高差: {tri.avg_delta_z:.3f} m
  挖方量: {tri.cut_volume:.3f} m³
  填方量: {tri.fill_volume:.3f} m³
  净体积: {tri.volume:.3f} m³
"""
        if tri.is_mixed:
            info += (
                "\n按零填挖线分割:\n"
                f"  挖方部分: 面积={tri.cut_area:.3f} m²  体积={tri.cut_volume:.3f} m³\n"
                f"  填方部分: 面积={tri.fill_area:.3f} m²  体积={tri.fill_volume:.3f} m³\n"
            )

        self.info_text.insert('1.0', info)
        self.info_text.config(state=tk.DISABLED)
        
    def shutdown(self):
        """停止 Tk 回调并释放嵌入式绘图资源。"""
        if self._closing:
            return
        self._closing = True
        self._detail_load_token += 1

        for attr_name in ("_worker_poll_after_id", "_detail_load_after_id", "_export_poll_after_id"):
            after_id = getattr(self, attr_name, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr_name, None)

        if hasattr(self, "plotter"):
            self.plotter.close()
        if hasattr(self, "canvas"):
            try:
                self.canvas.get_tk_widget().destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # 导出（后台线程执行，避免大数据量时界面无响应）
    # ------------------------------------------------------------------

    def _ask_export_path(self, title: str, extension: str, filetypes) -> Optional[str]:
        if not self.result:
            messagebox.showwarning("提示", "请先进行计算")
            return None
        if self._export_thread is not None:
            messagebox.showinfo("提示", "上一个导出任务还在进行，请稍候")
            return None
        path = filedialog.asksaveasfilename(
            title=title, defaultextension=extension, filetypes=filetypes,
            initialfile=f"{self._project_name()}{extension}",
        ) or None
        self._pending_export_path = path or ""
        return path

    def _run_export(self, label: str, work: Callable[[], None], success: str):
        """在后台线程执行导出，完成后在主线程提示结果。"""
        for button in self._export_buttons:
            button.config(state=tk.DISABLED)
        self.status_var.set(f"正在{label}...")
        events = queue.Queue()
        self._export_events = events
        if self._pending_export_path:
            success = f"{success}：{self._pending_export_path}"

        def worker():
            try:
                work()
                events.put(("done", success))
            except Exception as error:  # 把具体原因带回界面
                hint = "\n文件可能正被 Excel 等程序打开，请关闭后重试。" if isinstance(error, PermissionError) else ""
                events.put(("error", f"{label}失败: {error}{hint}"))

        self._export_thread = threading.Thread(target=worker, daemon=True)
        self._export_thread.start()
        self._schedule_export_poll()

    def _schedule_export_poll(self):
        if self._closing or self._export_poll_after_id is not None:
            return
        self._export_poll_after_id = self.after(50, self._poll_export_events)

    def _poll_export_events(self):
        self._export_poll_after_id = None
        if self._closing:
            return
        try:
            kind, message = self._export_events.get_nowait()
        except queue.Empty:
            self._schedule_export_poll()
            return
        self._export_thread = None
        for button in self._export_buttons:
            button.config(state=tk.NORMAL)
        if kind == "done":
            self.status_var.set("导出完成")
            self.notify(message)
        else:
            self.status_var.set("导出失败")
            messagebox.showerror("错误", message)

    def _export_excel(self):
        filepath = self._ask_export_path("导出 Excel 报告", ".xlsx", [("Excel文件", "*.xlsx")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result, points = self.result, list(self.points)
        self._run_export(
            "导出 Excel 报告",
            lambda: exporter.export_summary_excel(result, filepath, points),
            "Excel 报告导出成功",
        )

    def _export_csv(self):
        filepath = self._ask_export_path("导出 CSV 明细", ".csv", [("CSV文件", "*.csv")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result = self.result
        self._run_export("导出 CSV 明细", lambda: exporter.export_triangles_csv(result, filepath), "CSV 明细导出成功")

    def _export_dxf(self):
        filepath = self._ask_export_path("导出 DXF", ".dxf", [("DXF图形", "*.dxf")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result = self.result
        self._run_export(
            "导出 DXF",
            lambda: exporter.export_boundary_dxf(result, filepath),
            "DXF 已导出（BOUNDARY 边界 / ZERO_CONTOUR 零填挖线）",
        )

    def _export_pdf(self):
        filepath = self._ask_export_path("导出 PDF 计算书", ".pdf", [("PDF文件", "*.pdf")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result, points = self.result, list(self.points)
        self._run_export(
            "导出 PDF 计算书",
            lambda: exporter.export_pdf_report(result, len(points), points, result.boundary_points, filepath),
            "PDF 计算书已导出（汇总页 + 成果图）",
        )

    def _export_image(self):
        filepath = self._ask_export_path(
            "导出高清图片", ".png",
            [("PNG图片", "*.png"), ("PDF文件", "*.pdf"), ("SVG矢量图", "*.svg")],
        )
        if not filepath:
            return
        result, points, name = self.result, list(self.points), self._project_name()

        def work():
            figure = create_standalone_figure(result, points, result.boundary_points, name)
            figure.savefig(filepath, dpi=300, bbox_inches='tight')

        self._run_export("导出高清图片", work, "高清图片导出成功")

    def _export_text(self):
        filepath = self._ask_export_path("生成文本报告", ".txt", [("文本文件", "*.txt")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        report = exporter.generate_report_text(self.result, len(self.points))

        def work():
            with open(filepath, 'w', encoding='utf-8') as handle:
                handle.write(report)

        self._run_export("生成文本报告", work, "文本报告生成成功")


def parse_partition_text(content: str) -> Tuple[Dict[str, float], List[str]]:
    """解析“点号,高程”文本（逗号、中文逗号、制表符或空格分隔），返回 (数据, 无法解析的行)。"""
    data: Dict[str, float] = {}
    bad_lines: List[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [part for part in line.replace("，", ",").replace("\t", ",").replace(" ", ",").split(",") if part]
        try:
            if len(parts) != 2:
                raise ValueError
            data[parts[0].strip()] = float(parts[1])
        except ValueError:
            bad_lines.append(line)
    return data, bad_lines
