"""
边界设置页面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Tuple, Callable, Optional
from utils.plotter import EarthworkPlotter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from core.breaklines import Breakline
from core.calculator import DUPLICATE_TOLERANCE, SurveyPoint
from core.geometry import ring_self_intersects
import numpy as np

TARGET_BOUNDARY = "boundary"
TARGET_BREAKLINE = "breakline"
BOUNDARY_TITLE = '边界编辑模式 - 左键添加点，右键结束'
BREAKLINE_TITLE = '特征线绘制 - 左键点测点添加顶点，右键结束这一条'


class BoundaryFrame(ttk.Frame):
    """计算边界设置页面"""
    
    def __init__(self, parent, points: List[SurveyPoint], on_boundary_set: Callable[[List[Tuple[float, float]]], None],
                 on_breaklines_changed: Optional[Callable[[List[Breakline]], None]] = None):
        super().__init__(parent)
        self.points = points
        self.on_boundary_set = on_boundary_set
        self.on_breaklines_changed = on_breaklines_changed
        self.boundary: List[Tuple[float, float]] = []
        self.breaklines: List[Breakline] = []
        self.drawing: List[Tuple[float, float]] = []  # 正在绘制的特征线顶点
        self.edit_mode = True
        self.current_point: Optional[Tuple[float, float]] = None
        self._point_xy = np.empty((0, 2), dtype=float)
        
        self._create_widgets()
        self._refresh_plot()
        
    def _create_widgets(self):
        # 左侧：绘图区
        plot_frame = ttk.LabelFrame(self, text="边界编辑 (左键添加点，右键结束/删除最后一点)", padding=8)
        plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 6), pady=10)
        
        self.plotter = EarthworkPlotter(figsize=(8, 6))
        self.canvas = FigureCanvasTkAgg(self.plotter.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.plotter.bind_canvas(self.canvas, enable_hover=False)

        # 绑定鼠标事件
        self._canvas_connection_ids = [
            self.canvas.mpl_connect('button_press_event', self._on_canvas_click),
            self.canvas.mpl_connect('motion_notify_event', self._on_canvas_motion),
        ]
        
        # 右侧：控制面板。before=plot_frame 让面板先按自身宽度占位，绘图区用剩下的宽度；
        # 面板内用 grid 且只给坐标列表权重，窗口偏矮时先缩列表，“确认边界”等按钮始终可见
        ctrl_frame = ttk.LabelFrame(self, text="边界控制", padding=12)
        ctrl_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 10), pady=10, before=plot_frame)
        ctrl_frame.grid_columnconfigure(0, weight=1)
        ctrl_frame.grid_rowconfigure(2, weight=1)

        # 模式选择
        mode_frame = ttk.Frame(ctrl_frame)
        mode_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.edit_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode_frame, text="编辑模式（左键添加）", variable=self.edit_var,
                       command=self._toggle_edit_mode).pack(anchor=tk.W)
        target_row = ttk.Frame(mode_frame)
        target_row.pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(target_row, text="左键绘制：").pack(side=tk.LEFT)
        self.target_var = tk.StringVar(value=TARGET_BOUNDARY)
        for value, text in ((TARGET_BOUNDARY, "计算边界"), (TARGET_BREAKLINE, "特征线")):
            ttk.Radiobutton(target_row, text=text, value=value, variable=self.target_var,
                            command=self._on_target_change).pack(side=tk.LEFT, padx=(0, 8))

        # 边界点列表
        ttk.Label(ctrl_frame, text="边界点坐标:", style="Title.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))

        list_frame = ttk.Frame(ctrl_frame)
        list_frame.grid(row=2, column=0, sticky="nsew", pady=8)

        self.tree_boundary = ttk.Treeview(list_frame, columns=('idx', 'x', 'y'), show='headings', height=8)
        self.tree_boundary.heading('idx', text='序号')
        self.tree_boundary.heading('x', text='X坐标')
        self.tree_boundary.heading('y', text='Y坐标')
        self.tree_boundary.column('idx', width=50, anchor='center')
        self.tree_boundary.column('x', width=100, anchor='center')
        self.tree_boundary.column('y', width=100, anchor='center')
        self.tree_boundary.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree_boundary.yview)
        self.tree_boundary.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 编辑按钮（上行 2 个、下行 3 个，省出高度给坐标列表）
        btn_frame = ttk.Frame(ctrl_frame)
        btn_frame.grid(row=3, column=0, sticky="ew", pady=4)
        btn_frame.grid_columnconfigure(tuple(range(6)), weight=1, uniform="boundary_buttons")
        button_rows = (
            (("自动生成数据范围边界", self._create_auto_boundary), ("导入边界文件", self._import_boundary)),
            (("撤销上一点", self._undo_last_point), ("删除选中点", self._delete_selected),
             ("清空边界", self._clear_boundary)),
        )
        for row, buttons in enumerate(button_rows):
            span = 6 // len(buttons)
            for index, (text, command) in enumerate(buttons):
                ttk.Button(btn_frame, text=text, command=command).grid(
                    row=row, column=index * span, columnspan=span, sticky="ew",
                    padx=(0 if index == 0 else 2, 0 if index == len(buttons) - 1 else 2), pady=2,
                )
        ttk.Separator(btn_frame, orient='horizontal').grid(row=2, column=0, columnspan=6, sticky="ew", pady=8)
        ttk.Button(btn_frame, text="确认边界", command=self._confirm_boundary,
                  style='Accent.TButton').grid(row=3, column=0, columnspan=6, sticky="ew", pady=2)

        # 特征线：沟边、坎边、坡顶、坡脚等，构网时三角形不跨越
        line_frame = ttk.LabelFrame(ctrl_frame, text="特征线（可选）", padding=6)
        line_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        line_frame.grid_columnconfigure((0, 1), weight=1, uniform="breakline_buttons")
        self.breakline_status_var = tk.StringVar()
        ttk.Label(line_frame, textvariable=self.breakline_status_var, style="Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        for index, (text, command) in enumerate((
            ("导入 DXF 特征线…", self.import_breaklines_dxf), ("结束当前线", self._finish_drawing),
            ("删除最后一条", self._delete_last_breakline), ("清空特征线", self._clear_breaklines),
        )):
            ttk.Button(line_frame, text=text, command=command).grid(
                row=1 + index // 2, column=index % 2, sticky="ew", padx=(0 if index % 2 == 0 else 2, 0), pady=2)

        self.coord_var = tk.StringVar(value="X: --  Y: --")
        ttk.Label(ctrl_frame, textvariable=self.coord_var, style="Muted.TLabel").grid(
            row=5, column=0, sticky="w", pady=(10, 2)
        )
        self.boundary_status_var = tk.StringVar(value="待添加边界点")
        ttk.Label(ctrl_frame, textvariable=self.boundary_status_var, style="Accent.TLabel").grid(
            row=6, column=0, sticky="w"
        )
        self._update_boundary_status()

        # 信息提示
        info_text = ("操作说明:\n"
                    "1. 不知道边界时，先点“自动生成数据范围边界”\n"
                    "2. 手工边界按顺时针或逆时针逐点左键点击\n"
                    "3. 点会自动吸附到附近测量点；右键或“撤销”可回退\n"
                    "4. 至少 3 个点，系统会自动闭合；确认前检查状态提示\n"
                    "5. 也可导入 DXF 多段线或 CSV/TXT（前两列为 X、Y）\n"
                    "6. 有沟、坎、坡时选“特征线”，沿坡顶、坡脚点测点")
        self.info_label = ttk.Label(ctrl_frame, text=info_text, justify=tk.LEFT, style="Hint.TLabel")
        self.info_label.grid(row=7, column=0, sticky="w", pady=(10, 0))
        self.ctrl_frame, self.list_frame = ctrl_frame, list_frame
        ctrl_frame.bind("<Configure>", self._adapt_hint)

    # 坐标列表至少保留的高度（表头 + 约 4 行）
    LIST_MIN_HEIGHT = 140

    def _adapt_hint(self, _event=None):
        """面板偏矮时隐藏操作说明，把高度留给坐标列表；变高后再显示。"""
        shown = bool(self.info_label.winfo_manager())
        info_height = self.info_label.winfo_reqheight() + 10
        without_info = self.ctrl_frame.winfo_reqheight() - (info_height if shown else 0)
        needed = without_info - self.list_frame.winfo_reqheight() + self.LIST_MIN_HEIGHT + info_height
        if self.ctrl_frame.winfo_height() >= needed:
            if not shown:
                self.info_label.grid()
        elif shown:
            self.info_label.grid_remove()
        
    def _toggle_edit_mode(self):
        self.edit_mode = self.edit_var.get()
        self._refresh_plot()
        
    def _on_canvas_click(self, event):
        if not self.edit_mode or event.inaxes != self.plotter.ax:
            return
        if self.target_var.get() == TARGET_BREAKLINE:
            if event.button == 1 and event.xdata is not None and event.ydata is not None:
                self.add_drawing_vertex(event.xdata, event.ydata)
            elif event.button == 3:
                if len(self.drawing) >= 2:
                    self._finish_drawing()
                elif self.drawing:
                    self.drawing.pop()
                    self._refresh_plot()
            return

        if event.button == 1:  # 左键添加点
            if event.xdata is not None and event.ydata is not None:
                x, y = self._snap_to_point(event.xdata, event.ydata)
                self.boundary.append((x, y))
                self.current_point = None
                self._refresh_plot()
                self._update_boundary_list()
        elif event.button == 3 and self.boundary:  # 右键删除最后一点
            self.boundary.pop()
            self.current_point = None
            self._refresh_plot()
            self._update_boundary_list()
                
    def _on_canvas_motion(self, event):
        if event.inaxes == self.plotter.ax and event.xdata is not None and event.ydata is not None:
            self.coord_var.set(f"X: {event.xdata:.3f}  Y: {event.ydata:.3f}")
            drawing_line = self.target_var.get() == TARGET_BREAKLINE
            anchor = self.drawing if drawing_line else self.boundary
            if self.edit_mode and anchor:
                self.current_point = (event.xdata, event.ydata)
                self.plotter.update_boundary_preview(anchor, self.current_point)

    def _snap_to_point(self, x: float, y: float, threshold: float = 5.0) -> Tuple[float, float]:
        """吸附到最近的测量点。"""
        snapped = self._nearest_point(x, y, threshold)
        return snapped if snapped is not None else (x, y)

    def _nearest_point(self, x: float, y: float, threshold: float) -> Optional[Tuple[float, float]]:
        """threshold 以内最近测点的坐标，没有则为 None。"""
        if not self.points:
            return None
        if self._point_xy is None or len(self._point_xy) != len(self.points):
            self._point_xy = np.array([(point.x, point.y) for point in self.points], dtype=float)
        distances = np.sum((self._point_xy - (x, y)) ** 2, axis=1)
        point_index = int(np.argmin(distances))
        if distances[point_index] <= threshold ** 2:
            return tuple(float(v) for v in self._point_xy[point_index])
        return None

    def _refresh_plot(self):
        drawing_line = getattr(self, "target_var", None) is not None and self.target_var.get() == TARGET_BREAKLINE
        self.plotter.plot_boundary_edit(
            self.points, self.boundary, self.current_point, breaklines=self.breaklines, drawing=self.drawing,
            title=BREAKLINE_TITLE if drawing_line else BOUNDARY_TITLE,
        )
        self._update_boundary_status()
        self._update_breakline_status()

    # ------------------------------------------------------------------
    # 特征线
    # ------------------------------------------------------------------

    def _on_target_change(self):
        self.current_point = None
        self._refresh_plot()

    def _update_breakline_status(self, message: Optional[str] = None):
        if not hasattr(self, "breakline_status_var"):
            return
        if message is None:
            message = f"已有 {len(self.breaklines)} 条特征线" if self.breaklines else "未设置特征线"
            if self.drawing:
                message += f"；正在绘制，已点 {len(self.drawing)} 个顶点"
        self.breakline_status_var.set(message)

    def add_drawing_vertex(self, x: float, y: float) -> bool:
        """在正在绘制的特征线上加一个顶点；须点在测点上（吸附范围内），否则提示并忽略。"""
        snapped = self._nearest_point(x, y, 5.0)
        if snapped is None:
            self._update_breakline_status("特征线顶点须点在测点上：附近 5 m 内没有测点")
            return False
        if not self.drawing or self.drawing[-1] != snapped:
            self.drawing.append(snapped)
        self.current_point = None
        self._refresh_plot()
        return True

    def _finish_drawing(self):
        if len(self.drawing) >= 2:
            self.breaklines.append(Breakline([(x, y, None) for x, y in self.drawing],
                                             name=f"特征线{len(self.breaklines) + 1}"))
            self._notify_breaklines()
        self.drawing = []
        self.current_point = None
        self._refresh_plot()

    def _delete_last_breakline(self):
        if self.drawing:
            self.drawing = []
        elif self.breaklines:
            self.breaklines.pop()
            self._notify_breaklines()
        self._refresh_plot()

    def _clear_breaklines(self):
        self.drawing = []
        if self.breaklines:
            self.breaklines = []
            self._notify_breaklines()
        self._refresh_plot()

    def set_breaklines(self, lines: List[Breakline]):
        """外部设置特征线（打开工程时）。"""
        self.breaklines = list(lines)
        self.drawing = []
        try:
            self._refresh_plot()
        except tk.TclError:
            pass

    def _notify_breaklines(self):
        if self.on_breaklines_changed is not None:
            self.on_breaklines_changed(list(self.breaklines))

    def offsite_vertices(self, lines: List[Breakline]) -> int:
        """不带高程、又不在任何测点上的顶点数（这样的特征线计算时会报错）。"""
        flat = [(x, y) for line in lines for x, y, z in line.vertices if z is None]
        if not flat or not self.points:
            return len(flat)
        from scipy.spatial import cKDTree
        tree = cKDTree([(p.x, p.y) for p in self.points])
        distances, _ = tree.query(flat)
        return int(np.count_nonzero(distances > DUPLICATE_TOLERANCE))

    def import_breaklines_dxf(self, filepath: Optional[str] = None, layers: Optional[List[str]] = None) -> bool:
        """从 DXF 导入特征线；图层多于一个时让用户选择图层。"""
        from utils.dxf_io import import_breaklines_from_dxf, list_dxf_layers
        if filepath is None:
            filepath = filedialog.askopenfilename(title="导入特征线（DXF）",
                                                  filetypes=[("DXF图形", "*.dxf"), ("所有文件", "*.*")])
            if not filepath:
                return False
        try:
            if layers is None:
                available = list_dxf_layers(filepath)
                if not available:
                    messagebox.showwarning("提示", "DXF 中没有多段线或直线")
                    return False
                layers = list(available) if len(available) == 1 else ask_layers(self, available)
                if not layers:
                    return False
            lines = import_breaklines_from_dxf(filepath, layers)
        except Exception as error:
            messagebox.showerror("错误", f"导入特征线失败: {error}")
            return False
        start = len(self.breaklines)
        for offset, line in enumerate(lines, 1):
            line.name = f"特征线{start + offset}（{line.name.rsplit('-', 1)[0]}）"
        self.breaklines.extend(lines)
        self._notify_breaklines()
        self._refresh_plot()
        message = f"导入特征线 {len(lines)} 条（图层：{'、'.join(layers)}）"
        missing = self.offsite_vertices(lines)
        if missing:
            messagebox.showwarning(
                "特征线已导入，请注意",
                message + f"\n\n有 {missing} 个不带高程的顶点不在测点上，计算时会报错。"
                "请在 CAD 中把顶点捕捉到测点，或改用带高程的三维多段线。",
            )
        else:
            messagebox.showinfo("成功", message)
        return True

    def _update_boundary_status(self):
        if not hasattr(self, 'boundary_status_var'):
            return
        count = len(self.boundary)
        if count < 3:
            self.boundary_status_var.set(f"当前 {count} 个点；至少还需 {3 - count} 个点")
        elif self._boundary_is_valid():
            self.boundary_status_var.set(f"当前 {count} 个点；边界有效，确认后可计算")
        else:
            self.boundary_status_var.set("边界无效：请撤销或删除重复/交叉点")

    def _boundary_is_valid(self) -> bool:
        if len(self.boundary) < 3:
            return False
        if len(set(self.boundary)) != len(self.boundary):
            return False
        return not ring_self_intersects(self.boundary)

    def _overlaps_points(self) -> bool:
        """边界外包矩形是否与测点范围重叠。"""
        if not self.points or not self.boundary:
            return True
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        bxs = [x for x, _ in self.boundary]
        bys = [y for _, y in self.boundary]
        return not (max(bxs) < min(xs) or min(bxs) > max(xs) or max(bys) < min(ys) or min(bys) > max(ys))

    def set_points(self, points: List[SurveyPoint]):
        """更新测点（使吸附坐标缓存失效）并重绘。"""
        self.points = points
        self._point_xy = None
        try:
            self._refresh_plot()
        except tk.TclError:
            pass

    def transform_boundary(self, func: Callable[[float, float], Tuple[float, float]]):
        """对边界和特征线的每个顶点做同一变换（平移、X/Y 互换）。"""
        self.boundary = [func(x, y) for x, y in self.boundary]
        self.drawing = [func(x, y) for x, y in self.drawing]
        if self.breaklines:
            self.breaklines = [Breakline([(*func(x, y), z) for x, y, z in line.vertices], name=line.name)
                               for line in self.breaklines]
            self._notify_breaklines()
        self.current_point = None
        self._update_boundary_list()
        try:
            self._refresh_plot()
        except tk.TclError:
            pass

    def shutdown(self):
        """断开画布事件并释放嵌入式绘图资源。"""
        for connection_id in getattr(self, "_canvas_connection_ids", []):
            self.canvas.mpl_disconnect(connection_id)
        self._canvas_connection_ids = []
        self.plotter.close()
        try:
            self.canvas.get_tk_widget().destroy()
        except tk.TclError:
            pass

    def _create_auto_boundary(self):
        if not self.points:
            messagebox.showwarning("提示", "请先导入测量点")
            return
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        self.boundary = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
        self.current_point = None
        self._refresh_plot()
        self._update_boundary_list()

    def _undo_last_point(self):
        if self.boundary:
            self.boundary.pop()
            self.current_point = None
            self._refresh_plot()
            self._update_boundary_list()
        
    def _update_boundary_list(self):
        self.tree_boundary.delete(*self.tree_boundary.get_children())
        for i, (x, y) in enumerate(self.boundary, 1):
            self.tree_boundary.insert('', 'end', values=(i, f"{x:.3f}", f"{y:.3f}"))
            
    def _import_boundary(self):
        filepath = filedialog.askopenfilename(
            title="导入边界文件",
            filetypes=[
                ("DXF图形", "*.dxf"),
                ("CSV文件", "*.csv"),
                ("文本文件", "*.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if not filepath:
            return

        try:
            if filepath.lower().endswith(".dxf"):
                from utils.dxf_io import import_boundary_from_dxf
                self.boundary = import_boundary_from_dxf(filepath)
            else:
                import pandas as pd
                df = pd.read_csv(filepath, sep=None, engine="python", header=None)
                if df.shape[1] < 2:
                    messagebox.showwarning("提示", "文件列数不足，需要至少X、Y两列")
                    return
                boundary = []
                for _, row in df.iterrows():
                    try:
                        boundary.append((float(row[0]), float(row[1])))
                    except (TypeError, ValueError):
                        continue  # 表头或说明行
                self.boundary = boundary
            if len(self.boundary) < 3:
                messagebox.showwarning("提示", "边界至少需要 3 个点")
                return
            self.current_point = None
            self._refresh_plot()
            self._update_boundary_list()
            messagebox.showinfo("成功", f"导入边界点 {len(self.boundary)} 个")
        except Exception as error:
            messagebox.showerror("错误", f"导入失败: {error}")
            
    def _clear_boundary(self):
        self.boundary = []
        self.current_point = None
        self._refresh_plot()
        self._update_boundary_list()
        
    def _delete_selected(self):
        selection = self.tree_boundary.selection()
        if selection:
            idx = int(self.tree_boundary.item(selection[0])['values'][0]) - 1
            if 0 <= idx < len(self.boundary):
                self.boundary.pop(idx)
                self._refresh_plot()
                self._update_boundary_list()
                
    def _confirm_boundary(self):
        if not self._boundary_is_valid():
            messagebox.showwarning("提示", "边界至少需要 3 个不重复点，且边线不能交叉。可使用“撤销上一点”修正。")
            return
        if not self._overlaps_points() and not messagebox.askokcancel(
            "边界与测点不重叠",
            "计算边界与测点范围完全不重叠，计算结果将为 0。\n"
            "常见原因是测点的 X/Y 与图纸方向相反（测量坐标 X 为北向），"
            "可在数据导入页点击“X/Y 互换”。\n\n仍要使用这个边界吗？",
        ):
            return
        self._update_boundary_list()
        self.on_boundary_set(list(self.boundary))
        messagebox.showinfo("成功", f"边界已设置，共 {len(self.boundary)} 个点")

    def set_boundary(self, boundary: List[Tuple[float, float]]):
        """外部设置边界"""
        self.boundary = list(boundary)
        self._refresh_plot()
        self._update_boundary_list()


def ask_layers(parent, layers: dict) -> Optional[List[str]]:
    """选择作为特征线的 DXF 图层（多选），取消时返回 None。"""
    dialog = tk.Toplevel(parent)
    dialog.title("选择特征线图层")
    dialog.transient(parent.winfo_toplevel())
    ttk.Label(dialog, text="选择作为特征线的图层（可多选）：", padding=8).pack(anchor=tk.W)
    listbox = tk.Listbox(dialog, selectmode=tk.MULTIPLE, height=min(12, max(4, len(layers))), exportselection=False)
    names = list(layers)
    for name in names:
        listbox.insert(tk.END, f"{name}（{layers[name]} 条线）")
    listbox.pack(fill=tk.BOTH, expand=True, padx=8)
    chosen: List[str] = []

    def accept():
        chosen.extend(names[i] for i in listbox.curselection())
        dialog.destroy()

    buttons = ttk.Frame(dialog, padding=8)
    buttons.pack(fill=tk.X)
    ttk.Button(buttons, text="确定", command=accept, style="Accent.TButton").pack(side=tk.RIGHT)
    ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=6)
    dialog.grab_set()
    parent.wait_window(dialog)
    return chosen or None
