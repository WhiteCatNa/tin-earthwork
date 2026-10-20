"""
计算设置与结果页面
"""
import math
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, List, Callable, Optional, Dict, Tuple
from core.balance import SoilFactors, balance_elevation, balance_plane, best_fit_plane, soil_balance
from core.calculator import COMPARE_DESIGN, SurveyPoint, CalculationResult, TINEarthworkCalculator, format_id_list
from core.contours import trace_contours
from core.sections import run_sections
from core.layers import LAYER_DEPTH, LAYER_ELEVATION, LAYER_MODES, parse_layer_values, run_layers
from core.grid_check import run_grid_check, suggest_spacing
from core.surface import PlaneDesign
from utils.view3d import surface_faces
from gui.view3d_window import View3DWindow, cancel_idle_draw
from utils.plotter import EarthworkPlotter, create_standalone_figure, draw_section
from matplotlib.figure import Figure
from utils.data_handler import ISSUE_NAMES, DataExporter, DataImporter, DataValidator, report_labels
from gui.theme import COLORS, style_text_widget
from gui.widgets import ScrollableFrame, WrappingButtonRow
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import queue

DEFAULT_PROJECT_NAME = "TIN土方计算项目"
# 比较面：统一高程 / 斜面 / 两期对比 / 设计三角网（导入设计点、设计等高线）
MODE_LABELS = (("flat", "统一高程"), ("plane", "斜面"), ("compare", "两期对比"), ("design_tin", "设计三角网"))
PLANE_FIELDS = (("x0", "基准点 X0"), ("y0", "Y0"), ("h0", "基准高程 H0"), ("slope_x", "X向坡度%"), ("slope_y", "Y向坡度%"))


class CalculationFrame(ttk.Frame):
    """计算设置与结果页面"""
    
    def __init__(self, parent, points: List[SurveyPoint], boundary: List[Tuple[float, float]],
                 project_name_getter: Optional[Callable[[], str]] = None,
                 holes: Optional[List[List[Tuple[float, float]]]] = None):
        super().__init__(parent)
        self.project_name_getter = project_name_getter
        self.points = points
        self.boundary = boundary
        self.holes = list(holes or [])
        self.calculator = TINEarthworkCalculator()
        self.calculator.add_points(points)
        self.calculator.set_boundary(boundary)
        self.calculator.set_holes(self.holes)
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
        self.design_tin_points: List[SurveyPoint] = []
        self.design_tin_source = ""
        self.grid_enabled_var = tk.BooleanVar(value=False)
        self.grid_spacing_var = tk.StringVar(value=f"{self._suggested_spacing():g}")
        self.trim_enabled_var = tk.BooleanVar(value=False)
        self.max_edge_var = tk.StringVar(value=f"{self._suggested_max_edge():g}")
        self.contours_enabled_var = tk.BooleanVar(value=True)
        self.contour_interval_var = tk.StringVar(value="")   # 空为自动
        self.sections_enabled_var = tk.BooleanVar(value=False)
        self.section_spacing_var = tk.StringVar(value=f"{self._suggested_spacing():g}")
        self.section_angle_var = tk.StringVar(value="0")
        self.layers_enabled_var = tk.BooleanVar(value=False)
        self.layer_mode_var = tk.StringVar(value=LAYER_ELEVATION)
        self.layer_values_var = tk.StringVar(value="")
        self.loose_var = tk.StringVar(value="1.0")
        self.compact_var = tk.StringVar(value="1.0")
        self._balance_thread = None
        self._balance_events = queue.Queue()
        self._balance_poll_after_id = None
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
        self.view3d = None

        self._create_widgets()

    def _project_name(self) -> str:
        name = self.project_name_getter() if self.project_name_getter else ""
        return (name or "").strip() or DEFAULT_PROJECT_NAME

    def design_elevation_value(self) -> float:
        """读取统一设计高程；输入框不是数字时给出明确提示。"""
        try:
            return float(self.design_elevation_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("统一设计高程必须是数字") from None

    def update_inputs(self, points: List[SurveyPoint], boundary: List[Tuple[float, float]],
                      holes: Optional[List[List[Tuple[float, float]]]] = None):
        """测点、边界或挖空区变化后调用：同步计算器并作废旧结果。"""
        self.points = points
        self.boundary = boundary
        self.holes = list(holes or [])
        self.calculator.add_points(points)
        self.calculator.set_boundary(boundary)
        self.calculator.set_holes(self.holes)
        self.result = None
        self._refresh_plot()

    def apply_design_settings(self, design_elevation: float, use_partition: bool, partition: Dict[str, float],
                              design_mode: str = "flat", design_plane: Optional[Dict[str, float]] = None,
                              compare_points: Optional[List[SurveyPoint]] = None, compare_source: str = "",
                              design_points: Optional[List[SurveyPoint]] = None, design_source: str = "",
                              grid_enabled: bool = False, grid_spacing: Optional[float] = None,
                              max_edge_length: Optional[float] = None,
                              soil_factors: Optional[Dict[str, float]] = None,
                              contours_enabled: bool = True, contour_interval: Optional[float] = None,
                              sections_enabled: bool = False, section_spacing: Optional[float] = None,
                              section_angle: float = 0.0, layers: Optional[Dict[str, Any]] = None):
        """恢复工程文件中的比较面、分区、后期测点、方格网、三角网清理和可松性系数设置。"""
        self.design_elevation_var.set(design_elevation)
        self.use_partition_var.set(bool(use_partition))
        self.partition_data = dict(partition or {})
        self._toggle_partition()
        if design_plane:
            plane = PlaneDesign.from_dict(design_plane)
            for key, _ in PLANE_FIELDS:
                self.plane_vars[key].set(f"{getattr(plane, key):g}")
        self.set_compare_points(list(compare_points or []), compare_source)
        self.set_design_tin_points(list(design_points or []), design_source)
        self.grid_enabled_var.set(bool(grid_enabled))
        if grid_spacing:
            self.grid_spacing_var.set(f"{grid_spacing:g}")
        self.trim_enabled_var.set(bool(max_edge_length))
        if max_edge_length:
            self.max_edge_var.set(f"{max_edge_length:g}")
        self.contours_enabled_var.set(bool(contours_enabled))
        self.contour_interval_var.set(f"{contour_interval:g}" if contour_interval else "")
        self.sections_enabled_var.set(bool(sections_enabled))
        if section_spacing:
            self.section_spacing_var.set(f"{section_spacing:g}")
        self.section_angle_var.set(f"{section_angle or 0:g}")
        layers = layers or {}
        self.layers_enabled_var.set(bool(layers.get("enabled")))
        self.layer_mode_var.set(layers.get("mode") if layers.get("mode") in LAYER_MODES else LAYER_ELEVATION)
        self.layer_values_var.set(str(layers.get("values") or ""))
        factors = SoilFactors.from_dict(soil_factors)
        self.loose_var.set(f"{factors.loose:g}")
        self.compact_var.set(f"{factors.compact:g}")
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
        self.result = None
        self._refresh_plot()

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
        max_edge = self.max_edge_value() if self.trim_enabled_var.get() else None
        factors = self.soil_factors_value()
        return {
            "design_mode": mode,
            "design_elevation": self.design_elevation_value(),
            "design_plane": plane,
            "use_partition": bool(self.use_partition_var.get()),
            "partition": dict(self.partition_data),
            "compare_points": list(self.compare_points),
            "compare_source": self.compare_source,
            "design_points": list(self.design_tin_points),
            "design_source": self.design_tin_source,
            "grid_enabled": bool(self.grid_enabled_var.get()),
            "grid_spacing": spacing,
            "max_edge_length": max_edge,
            "soil_factors": None if factors.is_default else factors.to_dict(),
            "contours_enabled": bool(self.contours_enabled_var.get()),
            "contour_interval": self.contour_interval_value(),
            **self._section_settings(),
            **self._layer_settings(),
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

    def max_edge_value(self) -> float:
        try:
            length = float(self.max_edge_var.get())
        except ValueError:
            raise ValueError("最大边长必须是数字") from None
        if not math.isfinite(length) or length <= 0:
            raise ValueError("最大边长必须大于 0")
        return length

    def section_values(self) -> Tuple[float, float]:
        """(断面间距, 推进方向角)。"""
        try:
            spacing = float(self.section_spacing_var.get())
            angle = float(self.section_angle_var.get() or 0)
        except ValueError:
            raise ValueError("断面间距和方向角必须是数字") from None
        if not math.isfinite(spacing) or spacing <= 0:
            raise ValueError("断面间距必须大于 0")
        if not math.isfinite(angle):
            raise ValueError("断面方向角必须是数字")
        return spacing, angle

    def _section_settings(self) -> Dict[str, Any]:
        enabled = bool(self.sections_enabled_var.get())
        try:
            spacing, angle = self.section_values()
        except ValueError:
            if enabled:
                raise
            spacing, angle = None, 0.0
        return {"sections_enabled": enabled, "section_spacing": spacing, "section_angle": angle}

    def layer_values(self) -> Tuple[str, List[float]]:
        """(分层方式, 分界值)。"""
        mode = self.layer_mode_var.get()
        return mode, parse_layer_values(self.layer_values_var.get(), mode)

    def _layer_settings(self) -> Dict[str, Any]:
        enabled = bool(self.layers_enabled_var.get())
        text = self.layer_values_var.get().strip()
        if enabled:
            self.layer_values()
        if not enabled and not text:
            return {"layers": None}
        return {"layers": {"enabled": enabled, "mode": self.layer_mode_var.get(), "values": text}}

    def contour_interval_value(self) -> Optional[float]:
        """等高距；空为自动。"""
        text = self.contour_interval_var.get().strip()
        if not text:
            return None
        try:
            interval = float(text)
        except ValueError:
            raise ValueError("等高距必须是数字，留空为自动") from None
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("等高距必须大于 0")
        return interval

    def soil_factors_value(self) -> SoilFactors:
        values = []
        for var, name in ((self.loose_var, "最初可松性系数 Ks"), (self.compact_var, "最终可松性系数 Ks'")):
            try:
                values.append(float(var.get()))
            except ValueError:
                raise ValueError(f"{name}必须是数字") from None
        return SoilFactors(*values).validate()

    def _suggested_max_edge(self) -> float:
        """建议的最大边长：测点平均间距的 5 倍，取整到 5 m。"""
        extent = self._site_extent()
        if extent is None or len(self.points) < 3:
            return 50.0
        xmin, ymin, xmax, ymax = extent
        spacing = math.sqrt(max((xmax - xmin) * (ymax - ymin), 1e-9) / len(self.points))
        return max(5.0, round(spacing * 5 / 5) * 5)

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
        self.result = None
        if hasattr(self, "compare_label"):
            if self.compare_points:
                name = os.path.basename(source) if source else "工程文件"
                self.compare_label.config(text=f"已导入 {len(self.compare_points)} 个后期测点（{name}）",
                                          foreground=COLORS["ink"])
            else:
                self.compare_label.config(text="未导入后期测量数据", foreground=COLORS["dim"])

    def set_design_tin_points(self, points: List[SurveyPoint], source: str = ""):
        """设置设计三角网的设计点（导入文件或恢复工程时调用）。"""
        self.design_tin_points = list(points)
        self.design_tin_source = source
        self.result = None
        if hasattr(self, "design_tin_label"):
            if self.design_tin_points:
                name = os.path.basename(source) if source else "工程文件"
                self.design_tin_label.config(text=f"已导入 {len(self.design_tin_points)} 个设计点（{name}）",
                                             foreground=COLORS["ink"])
            else:
                self.design_tin_label.config(text="未导入设计点", foreground=COLORS["dim"])

    def _import_design_tin(self):
        filepath = filedialog.askopenfilename(
            title="导入设计面数据",
            filetypes=[("设计面数据", "*.dxf *.xlsx *.xls *.csv *.txt *.dat"), ("所有文件", "*.*")],
        )
        if filepath:
            self.load_design_tin_file(filepath)

    def load_design_tin_file(self, filepath: str) -> bool:
        """导入设计点：DXF 取高程点、3DFACE、设计等高线等；表格/文本按测点格式读取。"""
        try:
            if filepath.lower().endswith(".dxf"):
                from utils.design_import import import_design_points_from_dxf
                points = import_design_points_from_dxf(filepath)
            else:
                points, _, _ = DataImporter.import_file(filepath)
        except Exception as error:
            messagebox.showerror("导入失败", str(error))
            return False
        if len(points) < 3:
            messagebox.showerror("导入失败", "设计面至少需要 3 个设计点")
            return False
        zs = [p.z for p in points]
        if not messagebox.askokcancel(
            "确认设计面数据",
            f"识别到 {len(points)} 个设计点，设计高程 {min(zs):.3f} ~ {max(zs):.3f} m。\n\n使用这批点构建设计三角网？",
        ):
            return False
        self.set_design_tin_points(points, filepath)
        return True

    def _create_widgets(self):
        # 左侧：设置面板。“开始计算”和进度固定在底部；设置项放在可滚动区域，窗口偏矮时滚动查看
        left_frame = ttk.LabelFrame(self, text="计算设置", padding=12)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 6), pady=10)

        bottom = ttk.Frame(left_frame)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Separator(bottom, orient='horizontal').pack(fill=tk.X, pady=(4, 8))
        self.calculate_button = ttk.Button(
            bottom,
            text="开始计算",
            command=self._run_calculation,
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
        ttk.Label(self.flat_frame, text="设计高程 (m)").pack(side=tk.LEFT)
        ttk.Entry(self.flat_frame, textvariable=self.design_elevation_var, width=10).pack(side=tk.LEFT, padx=6)
        ttk.Button(self.flat_frame, text="从数据估算", command=self._estimate_elevation).pack(side=tk.LEFT)
        ttk.Button(self.flat_frame, text="挖填平衡", command=self._balance_flat).pack(side=tk.LEFT, padx=(6, 0))

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
        ttk.Button(self.plane_frame, text="按坡度求平衡 H0", command=self._balance_plane).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0), padx=(0, 4))
        ttk.Button(self.plane_frame, text="最小二乘最优斜面", command=self._best_fit_plane).grid(
            row=4, column=2, columnspan=2, sticky="ew", pady=(4, 0))

        self.compare_frame = ttk.Frame(self.mode_container)
        compare_buttons = ttk.Frame(self.compare_frame)
        compare_buttons.pack(fill=tk.X)
        ttk.Button(compare_buttons, text="导入后期测量数据…", command=self._import_compare_points).pack(side=tk.LEFT)
        ttk.Button(compare_buttons, text="后期 X/Y 互换", command=self._swap_compare_xy).pack(side=tk.LEFT, padx=6)
        self.compare_label = ttk.Label(self.compare_frame, style="Muted.TLabel", text="未导入后期测量数据")
        self.compare_label.pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(self.compare_frame, text="前期 = 数据导入页的测点；高差 = 前期 − 后期",
                  style="Muted.TLabel").pack(anchor=tk.W)

        self.design_tin_frame = ttk.Frame(self.mode_container)
        ttk.Button(self.design_tin_frame, text="导入设计点 / 设计等高线…", command=self._import_design_tin).pack(anchor=tk.W)
        self.design_tin_label = ttk.Label(self.design_tin_frame, style="Muted.TLabel", text="未导入设计点")
        self.design_tin_label.pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(self.design_tin_frame, text="DXF 读高程点、3DFACE、带标高的等高线\n高差 = 实测 − 设计",
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
        widest = max(frame.winfo_reqwidth() for frame in (self.flat_frame, self.plane_frame, self.compare_frame,
                                                          self.design_tin_frame))
        ttk.Frame(self.mode_container, width=widest, height=1).pack(side=tk.BOTTOM)
        self._on_mode_change()

        # 土方平衡：可松性系数
        ttk.Separator(body, orient='horizontal').pack(fill=tk.X, pady=8)
        ttk.Label(body, text="土方平衡", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 4))
        factor_row = ttk.Frame(body)
        factor_row.pack(fill=tk.X)
        ttk.Label(factor_row, text="Ks").pack(side=tk.LEFT)
        ttk.Entry(factor_row, textvariable=self.loose_var, width=6).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(factor_row, text="Ks'").pack(side=tk.LEFT)
        ttk.Entry(factor_row, textvariable=self.compact_var, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(body, text="Ks = 松方/自然方，Ks' = 压实方/自然方\n都填 1 表示不换算",
                  style="Muted.TLabel").pack(anchor=tk.W)

        # 等高线
        ttk.Separator(body, orient='horizontal').pack(fill=tk.X, pady=8)
        contour_row = ttk.Frame(body)
        contour_row.pack(fill=tk.X)
        ttk.Checkbutton(contour_row, text="等高线", variable=self.contours_enabled_var).pack(side=tk.LEFT)
        ttk.Label(contour_row, text="等高距 (m)").pack(side=tk.LEFT, padx=(10, 4))
        ttk.Entry(contour_row, textvariable=self.contour_interval_var, width=6).pack(side=tk.LEFT)
        ttk.Label(contour_row, text="空为自动", style="Muted.TLabel").pack(side=tk.LEFT, padx=4)

        # 断面法
        ttk.Separator(body, orient='horizontal').pack(fill=tk.X, pady=8)
        section_row = ttk.Frame(body)
        section_row.pack(fill=tk.X)
        ttk.Checkbutton(section_row, text="断面法校核", variable=self.sections_enabled_var).pack(side=tk.LEFT)
        ttk.Label(section_row, text="间距 (m)").pack(side=tk.LEFT, padx=(10, 4))
        ttk.Entry(section_row, textvariable=self.section_spacing_var, width=6).pack(side=tk.LEFT)
        angle_row = ttk.Frame(body)
        angle_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(angle_row, text="推进方向 (°)").pack(side=tk.LEFT)
        ttk.Entry(angle_row, textvariable=self.section_angle_var, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(angle_row, text="0 沿 X，90 沿 Y", style="Muted.TLabel").pack(side=tk.LEFT)

        # 分层土方
        ttk.Separator(body, orient='horizontal').pack(fill=tk.X, pady=8)
        layer_row = ttk.Frame(body)
        layer_row.pack(fill=tk.X)
        ttk.Checkbutton(layer_row, text="分层土方", variable=self.layers_enabled_var).pack(side=tk.LEFT)
        for value, text in ((LAYER_ELEVATION, "按高程"), (LAYER_DEPTH, "按深度")):
            ttk.Radiobutton(layer_row, text=text, value=value, variable=self.layer_mode_var).pack(
                side=tk.LEFT, padx=(8, 0))
        layer_values = ttk.Frame(body)
        layer_values.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(layer_values, text="分界").pack(side=tk.LEFT)
        ttk.Entry(layer_values, textvariable=self.layer_values_var, width=16).pack(side=tk.LEFT, padx=4)
        ttk.Label(layer_values, text="高程或深度 (m)，逗号分隔", style="Muted.TLabel").pack(side=tk.LEFT)

        # 三角网清理
        ttk.Separator(body, orient='horizontal').pack(fill=tk.X, pady=8)
        trim_row = ttk.Frame(body)
        trim_row.pack(fill=tk.X)
        ttk.Checkbutton(trim_row, text="剔除长边三角形", variable=self.trim_enabled_var).pack(side=tk.LEFT)
        ttk.Label(trim_row, text="边长 > (m)").pack(side=tk.LEFT, padx=(10, 4))
        ttk.Entry(trim_row, textvariable=self.max_edge_var, width=6).pack(side=tk.LEFT)

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
        self.show_levels_var = tk.BooleanVar(value=True)

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

        # 结果选项卡
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # 选项卡1：图形显示（显示开关放在图上方，左栏留给计算设置）
        self.plot_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.plot_frame, text="计算结果图")
        toggles = ttk.Frame(self.plot_frame)
        toggles.pack(fill=tk.X, padx=4, pady=(4, 0))
        for text, variable in (("TIN网格", self.show_tin_var), ("零填挖线", self.show_contour_var),
                               ("等高线", self.show_levels_var), ("测点", self.show_points_var),
                               ("边界", self.show_boundary_var)):
            ttk.Checkbutton(toggles, text=text, variable=variable, command=self._refresh_plot).pack(
                side=tk.LEFT, padx=(0, 10))

        self.plotter = EarthworkPlotter(figsize=(10, 7))
        self.canvas = FigureCanvasTkAgg(self.plotter.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.plotter.bind_canvas(self.canvas, enable_navigation=True)
        # 图上滚轮缩放、拖动平移、双击复位；显示开关一栏在小屏已排满，复位按钮浮在图的左下角
        corner = ttk.Frame(self.canvas.get_tk_widget())
        corner.place(relx=0, rely=1, x=6, y=-6, anchor=tk.SW)
        ttk.Button(corner, text="重置视图", command=self._reset_plot_view).pack(side=tk.LEFT)
        ttk.Button(corner, text="三维视图…", command=self._open_3d_view).pack(side=tk.LEFT, padx=(6, 0))
        self.plotter.on_triangle_click = self._on_triangle_selected
        
        # 选项卡2：汇总表
        self.summary_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.summary_frame, text="汇总表")
        self._create_summary_table()
        
        # 选项卡3：明细表
        self.detail_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.detail_frame, text="计算明细")
        self._create_detail_table()
        
        # 选项卡：断面图（启用断面法后可选断面查看）
        self.section_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.section_frame, text="断面图")
        self._create_section_view()

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
        
    def _create_section_view(self):
        top = ttk.Frame(self.section_frame)
        top.pack(fill=tk.X, padx=8, pady=(6, 0))
        ttk.Label(top, text="断面：").pack(side=tk.LEFT)
        self.section_choice_var = tk.StringVar()
        self.section_combo = ttk.Combobox(top, textvariable=self.section_choice_var, state="readonly", width=36)
        self.section_combo.pack(side=tk.LEFT)
        self.section_combo.bind("<<ComboboxSelected>>", lambda _event: self._draw_selected_section())
        for text, step in (("上一个", -1), ("下一个", 1)):
            ttk.Button(top, text=text, command=lambda step=step: self._step_section(step)).pack(side=tk.LEFT, padx=(6, 0))
        self.section_figure = Figure(figsize=(8, 4), dpi=100, constrained_layout=True)
        self.section_axes = self.section_figure.add_subplot(111)
        self.section_canvas = FigureCanvasTkAgg(self.section_figure, master=self.section_frame)
        self.section_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._update_section_view()

    def _update_section_view(self):
        sections = self.result.sections.sections if self.result is not None and self.result.sections else []
        self.section_combo["values"] = [
            f"断面 {s.index}  里程 {s.station:.3f} m  挖 {s.cut_area:.2f} m²  填 {s.fill_area:.2f} m²" for s in sections
        ]
        if sections:
            middle = len(sections) // 2
            self.section_combo.current(middle)
        else:
            self.section_choice_var.set("")
        self._draw_selected_section()

    def _step_section(self, step: int):
        count = len(self.section_combo["values"])
        if not count:
            return
        index = min(max(self.section_combo.current() + step, 0), count - 1)
        self.section_combo.current(index)
        self._draw_selected_section()

    def _draw_selected_section(self):
        self.section_axes.clear()
        index = self.section_combo.current()
        if self.result is None or self.result.sections is None or index < 0:
            self.section_axes.text(0.5, 0.5, "勾选“断面法校核”并计算后，在这里查看断面图",
                                   ha="center", va="center", transform=self.section_axes.transAxes)
            self.section_axes.set_axis_off()
        else:
            self.section_axes.set_axis_on()
            draw_section(self.section_axes, self.result.sections.sections[index], self.result)
        self.section_canvas.draw_idle()

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
        messagebox.showinfo("估算结果", f"建议设计高程 (中位数): {median_z:.3f} m")
        
    def _on_mode_change(self):
        """切换比较面：只显示对应的输入；两期对比时分区高程不适用。"""
        mode = self.design_mode_var.get()
        for frame, value in ((self.flat_frame, "flat"), (self.plane_frame, "plane"), (self.compare_frame, "compare"),
                             (self.design_tin_frame, "design_tin")):
            if value == mode:
                frame.pack(fill=tk.X)
            else:
                frame.pack_forget()
        if mode == "plane" and all(var.get() in ("", "0") for key, var in self.plane_vars.items()
                                   if key in ("x0", "y0", "h0")):
            self._fill_plane_defaults()
        compare = mode in ("compare", "design_tin")
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

    # ------------------------------------------------------------------
    # 挖填平衡的设计标高、最优斜面（后台计算，完成后填入输入框）
    # ------------------------------------------------------------------

    def _balance_inputs(self):
        """读取平衡计算需要的设置并同步到计算器；输入有误时提示并返回 None。"""
        if not self.points:
            messagebox.showwarning("提示", "没有可计算的测量点")
            return None
        if self._balance_thread is not None:
            messagebox.showinfo("提示", "上一次平衡计算还在进行，请稍候")
            return None
        try:
            factors = self.soil_factors_value()
            max_edge = self.max_edge_value() if self.trim_enabled_var.get() else None
        except ValueError as error:
            messagebox.showerror("输入错误", str(error))
            return None
        self.calculator.set_compare_points(None)
        self.calculator.set_max_edge_length(max_edge)
        return factors

    def _run_balance(self, label: str, work: Callable[[], Any], on_done: Callable[[Any], None]):
        self.status_var.set(f"正在{label}...")
        events = queue.Queue()
        self._balance_events = events

        def worker():
            try:
                events.put(("done", work()))
            except Exception as error:
                events.put(("error", error))

        self._balance_thread = threading.Thread(target=worker, daemon=True)
        self._balance_thread.start()

        def poll():
            self._balance_poll_after_id = None
            if self._closing:
                return
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                self._balance_poll_after_id = self.after(30, poll)
                return
            self._balance_thread = None
            if kind == "error":
                self.status_var.set(f"{label}失败")
                messagebox.showerror(f"{label}失败", str(value))
                return
            self.status_var.set(f"{label}完成，可点“开始计算”")
            on_done(value)

        self._balance_poll_after_id = self.after(30, poll)

    @staticmethod
    def _balance_message(design) -> str:
        balance = design.balance
        text = f"挖方 {design.cut:.2f} m³，填方 {design.fill:.2f} m³"
        if not balance.factors.is_default:
            text += f"\n按 {balance.factors.describe()}，填方需自然方 {balance.fill_natural:.2f} m³，挖填平衡"
        return text

    def _balance_flat(self):
        factors = self._balance_inputs()
        if factors is None:
            return
        calculator = self.calculator

        def done(design):
            self.design_elevation_var.set(round(design.elevation, 4))
            self.result = None
            messagebox.showinfo("挖填平衡", f"挖填平衡的设计高程：{design.elevation:.4f} m\n"
                                            f"{self._balance_message(design)}\n\n已填入设计高程。")

        self._run_balance("求平衡标高", lambda: balance_elevation(calculator, factors), done)

    def _set_plane(self, plane: PlaneDesign):
        for key, _ in PLANE_FIELDS:
            value = getattr(plane, key)
            self.plane_vars[key].set(f"{value:.4f}" if key in ("h0", "slope_x", "slope_y") else f"{value:.3f}")
        self.result = None

    def _balance_plane(self):
        try:
            plane = self.plane_value()
        except ValueError as error:
            messagebox.showerror("输入错误", str(error))
            return
        factors = self._balance_inputs()
        if factors is None:
            return
        calculator = self.calculator

        def done(design):
            self._set_plane(design.plane)
            messagebox.showinfo("挖填平衡", f"保持坡度不变，挖填平衡的基准高程 H0 = {design.plane.h0:.4f} m\n"
                                            f"{self._balance_message(design)}")

        self._run_balance("求平衡 H0", lambda: balance_plane(calculator, plane, factors), done)

    def _best_fit_plane(self):
        factors = self._balance_inputs()
        if factors is None:
            return
        calculator = self.calculator

        def done(design):
            self._set_plane(design.plane)
            messagebox.showinfo(
                "最小二乘最优斜面",
                f"与地面最贴合（挖填总量最小）且挖填平衡的斜面：\n"
                f"X 向坡度 {design.plane.slope_x:+.4f}%，Y 向坡度 {design.plane.slope_y:+.4f}%\n"
                f"基准点取计算范围形心 ({design.plane.x0:.3f}, {design.plane.y0:.3f})，H0 = {design.plane.h0:.4f} m\n"
                f"{self._balance_message(design)}\n\n排水需要特定坡度时，可改坡度后点“按坡度求平衡 H0”。",
            )

        self._run_balance("求最优斜面", lambda: best_fit_plane(calculator, factors), done)

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
            dialog.destroy()
            
        ttk.Button(dialog, text="保存", command=save, style="Accent.TButton").pack(pady=10)
        
    def _run_calculation(self):
        """运行计算 (在后台线程)"""
        if not self.points:
            messagebox.showwarning("提示", "没有可计算的测量点")
            return

        # Tkinter 控件只能由主线程访问；在线程启动前读取所需配置。
        mode = self.design_mode_var.get()
        use_partition = self.use_partition_var.get() and mode not in ("compare", "design_tin")
        partition_data = self.partition_data.copy() if use_partition else {}
        compare_points = list(self.compare_points)
        design_tin_points = list(self.design_tin_points)
        try:
            design_elevation = self.design_elevation_value()
            plane = self.plane_value() if mode == "plane" else None
            grid_spacing = self.grid_spacing_value() if self.grid_enabled_var.get() else None
            max_edge = self.max_edge_value() if self.trim_enabled_var.get() else None
            factors = self.soil_factors_value()
            contour_interval = self.contour_interval_value()
            section = self.section_values() if self.sections_enabled_var.get() else None
            layer_spec = self.layer_values() if self.layers_enabled_var.get() else None
        except ValueError as error:
            messagebox.showerror("输入错误", str(error))
            return
        contours_enabled = self.contours_enabled_var.get()
        if mode == "design_tin" and not design_tin_points:
            messagebox.showwarning("提示", "设计三角网需要先导入设计点")
            return
        if mode == "compare" and not compare_points:
            messagebox.showwarning("提示", "两期对比需要先导入后期测量数据")
            return

        self.status_var.set("正在计算...")
        self.progress_var.set(0)
        self.calculate_button.config(state=tk.DISABLED)
        self.update_idletasks()

        def calc_thread():
            try:
                calculator = self.calculator
                if mode == "design_tin":
                    calculator.set_compare_points(design_tin_points, kind=COMPARE_DESIGN)
                else:
                    calculator.set_compare_points(compare_points if mode == "compare" else None)
                calculator.set_max_edge_length(max_edge)
                if plane is not None:
                    calculator.set_design_plane(plane, partition_data)
                elif partition_data:
                    # 未列入分区的点使用界面上的统一设计高程
                    calculator.set_design_elevations(partition_data, default=design_elevation)
                else:
                    calculator.set_design_elevation(design_elevation)

                stage = "叠加前期、后期三角网..." if mode == "compare" else "构建 TIN 三角网..."
                self._worker_events.put(("progress", 30, stage))
                result = calculator.run_full_calculation()
                result.soil_balance = soil_balance(result.total_cut, result.total_fill, factors)
                if partition_data:
                    unlisted = [str(p.id) for p in calculator.points if p.id not in partition_data]
                    if unlisted:
                        base = "斜面" if plane is not None else f"统一设计高程 {design_elevation:.3f} m"
                        result.warnings.insert(0, (
                            f"有 {len(unlisted)} 个测点不在分区高程表中，按{base} 计算：{format_id_list(unlisted)}"
                        ))
                if contours_enabled:
                    self._worker_events.put(("progress", 60, "追踪等高线..."))
                    try:
                        result.contours = trace_contours(calculator, contour_interval)
                    except ValueError as error:
                        result.warnings.append(f"等高线未生成：{error}")
                if section is not None:
                    self._worker_events.put(("progress", 70, "断面法..."))
                    try:
                        result.sections = run_sections(calculator, result, *section)
                    except ValueError as error:
                        result.warnings.append(f"断面法未完成：{error}")
                if layer_spec is not None:
                    self._worker_events.put(("progress", 72, "分层土方..."))
                    try:
                        result.layers = run_layers(calculator, *layer_spec)
                    except ValueError as error:
                        result.warnings.append(f"分层土方未完成：{error}")
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
        self._refresh_plot()
        self._update_summary_table()
        self._update_section_view()
        self._start_detail_table_load()
        summary = (
            f"总挖方量: {self.result.total_cut:.2f} m³\n"
            f"总填方量: {self.result.total_fill:.2f} m³\n"
            f"挖填差值: {self.result.net_volume:.2f} m³\n"
            f"计算面积: {self.result.computed_area:.2f} m²\n"
            f"三角形数: {self.result.triangle_count}\n"
            f"混合三角形: {self.result.mixed_triangle_count}"
        )
        balance = self.result.soil_balance
        if balance is not None:
            if balance.surplus >= 0:
                summary += f"\n余土 {balance.surplus:.2f} m³（自然方），外运松方 {balance.export_loose:.2f} m³"
            else:
                summary += f"\n缺土 {balance.borrow_natural:.2f} m³（自然方），需外借"
        warnings = list(self.result.warnings)
        sections = self.result.sections
        if sections is not None:
            summary += f"\n\n断面法（间距 {sections.spacing:g} m，{len(sections.sections)} 个断面）:"
            for label, _, value, diff, relative in sections.comparison(self.result)[:2]:
                percent = "" if relative is None else f"，{relative:+.2f}%"
                summary += f"\n  {label} {value:.2f} m³（与 TIN 法相差 {diff:+.2f}{percent}）"
            warnings += sections.warnings
        layers = self.result.layers
        if layers is not None:
            summary += f"\n\n分层土方（{'按高程' if layers.mode == LAYER_ELEVATION else '按深度'}）:"
            for row in layers.rows:
                summary += f"\n  {row.label(layers.mode)}: 挖 {row.cut:.2f}，填 {row.fill:.2f} m³"
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
        else:
            messagebox.showinfo("计算完成", summary)
            
    def _on_calculation_error(self, error):
        self.status_var.set("计算失败")
        self.progress_var.set(0)
        self.calculate_button.config(state=tk.NORMAL)
        messagebox.showerror("计算错误", str(error))
        
    def _refresh_plot(self):
        if self.result:
            self.plotter.plot_result(
                self.result, self.points,
                show_tin=self.show_tin_var.get(),
                show_contour=self.show_contour_var.get(),
                show_points=self.show_points_var.get(),
                show_boundary=self.show_boundary_var.get(),
                show_levels=self.show_levels_var.get(),
            )
        else:
            self.plotter.plot_points_only(self.points)

    def _open_3d_view(self):
        """三维视图窗口：地面与设计面（后期面），可旋转、调高程夸张。"""
        if self.result is None:
            messagebox.showinfo("三维视图", "请先完成计算")
            return
        try:
            natural, design = surface_faces(self.calculator)
        except Exception as error:
            messagebox.showerror("三维视图", f"无法生成三维面：{error}")
            return
        if self.view3d is not None and self.view3d.winfo_exists():
            self.view3d.destroy()
        self.view3d = View3DWindow(self, self.result, natural, design, self.calculator.origin,
                                   self._project_name())

    def _reset_plot_view(self):
        self.plotter.reset_view()

    def _update_summary_table(self):
        """立即更新体积汇总；大明细表由后台事件循环分批填充。"""
        if not self.result:
            return

        self.tree_summary.delete(*self.tree_summary.get_children())
        for row in DataExporter.summary_rows(self.result):
            self.tree_summary.insert('', 'end', values=row)
        grid_warnings = self.result.grid_check.warnings if self.result.grid_check is not None else []
        for item in list(self.result.warnings) + list(grid_warnings):
            self.tree_summary.insert('', 'end', values=('提示', '', '', item), tags=('warn',))
        self.tree_summary.tag_configure('warn', foreground=COLORS["cut"])

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

        for attr_name in ("_worker_poll_after_id", "_detail_load_after_id", "_export_poll_after_id",
                          "_balance_poll_after_id"):
            after_id = getattr(self, attr_name, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr_name, None)

        if self.view3d is not None:
            try:
                self.view3d.destroy()
            except tk.TclError:
                pass
            self.view3d = None
        if hasattr(self, "plotter"):
            self.plotter.close()
        for name in ("canvas", "section_canvas"):
            if hasattr(self, name):
                cancel_idle_draw(getattr(self, name))

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
        return filedialog.asksaveasfilename(
            title=title, defaultextension=extension, filetypes=filetypes,
            initialfile=f"{self._project_name()}{extension}",
        ) or None

    def _run_export(self, label: str, work: Callable[[], None], success: str):
        """在后台线程执行导出，完成后在主线程提示结果。"""
        for button in self._export_buttons:
            button.config(state=tk.DISABLED)
        self.status_var.set(f"正在{label}...")
        events = queue.Queue()
        self._export_events = events

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
            self.status_var.set(message)
            messagebox.showinfo("成功", message)
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
