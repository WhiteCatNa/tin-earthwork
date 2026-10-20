"""
边界设置页面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Tuple, Callable, Optional
from utils.plotter import EarthworkPlotter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from core.calculator import SurveyPoint, check_holes
from core.geometry import ring_self_intersects
import numpy as np


class BoundaryFrame(ttk.Frame):
    """计算边界设置页面"""
    
    def __init__(self, parent, points: List[SurveyPoint],
                 on_boundary_set: Callable[[List[Tuple[float, float]], List[List[Tuple[float, float]]]], None]):
        super().__init__(parent)
        self.points = points
        self.on_boundary_set = on_boundary_set
        self.boundary: List[Tuple[float, float]] = []
        self.holes: List[List[Tuple[float, float]]] = []       # 已完成的挖空区
        self.drawing_hole: List[Tuple[float, float]] = []      # 正在绘制的挖空区
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
        # 绘制对象：计算边界，或挖空区（建筑物、水池等不计方量的区域）
        target_row = ttk.Frame(mode_frame)
        target_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(target_row, text="绘制：").pack(side=tk.LEFT)
        self.target_var = tk.StringVar(value="boundary")
        for value, label in (("boundary", "计算边界"), ("hole", "挖空区")):
            ttk.Radiobutton(target_row, text=label, value=value, variable=self.target_var,
                            command=self._on_target_change).pack(side=tk.LEFT, padx=(0, 8))
        hole_row = ttk.Frame(mode_frame)
        hole_row.pack(fill=tk.X, pady=(4, 0))
        self.hole_status_var = tk.StringVar(value="挖空区：无")
        ttk.Label(hole_row, textvariable=self.hole_status_var, style="Muted.TLabel").pack(side=tk.LEFT)
        hole_buttons = ttk.Frame(mode_frame)
        hole_buttons.pack(fill=tk.X, pady=(2, 0))
        hole_buttons.grid_columnconfigure((0, 1, 2), weight=1, uniform="hole_buttons")
        for column, (text, command) in enumerate((("完成挖空区", self._finish_hole),
                                                   ("导入挖空区", self._import_holes),
                                                   ("清空挖空区", self._clear_holes))):
            ttk.Button(hole_buttons, text=text, command=command).grid(
                row=0, column=column, sticky="ew", padx=(0 if column == 0 else 2, 0 if column == 2 else 2))

        # 边界点列表
        self.list_title_var = tk.StringVar(value="边界点坐标:")
        ttk.Label(ctrl_frame, textvariable=self.list_title_var, style="Title.TLabel").grid(
            row=1, column=0, sticky="w", pady=(10, 0))

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

        self.coord_var = tk.StringVar(value="X: --  Y: --")
        ttk.Label(ctrl_frame, textvariable=self.coord_var, style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(10, 2)
        )
        self.boundary_status_var = tk.StringVar(value="待添加边界点")
        ttk.Label(ctrl_frame, textvariable=self.boundary_status_var, style="Accent.TLabel").grid(
            row=5, column=0, sticky="w"
        )
        self._update_boundary_status()

        # 信息提示
        info_text = ("操作说明:\n"
                    "1. 不知道边界时，先点“自动生成数据范围边界”\n"
                    "2. 手工边界按顺时针或逆时针逐点左键点击\n"
                    "3. 点会自动吸附到附近测量点；右键或“撤销”可回退\n"
                    "4. 至少 3 个点，系统会自动闭合；确认前检查状态提示\n"
                    "5. 也可导入 DXF 多段线或 CSV/TXT（前两列为 X、Y）\n"
                    "6. 建筑物、水池等不计方量的区域：选“挖空区”逐个画，\n"
                    "   每画完一个点“完成挖空区”")
        self.info_label = ttk.Label(ctrl_frame, text=info_text, justify=tk.LEFT, style="Hint.TLabel")
        self.info_label.grid(row=6, column=0, sticky="w", pady=(10, 0))
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
            
        target = self._target()
        if event.button == 1:  # 左键添加点
            if event.xdata is not None and event.ydata is not None:
                x, y = self._snap_to_point(event.xdata, event.ydata)
                target.append((x, y))
                self.current_point = None
                self._refresh_plot()
                self._update_boundary_list()
        elif event.button == 3 and target:  # 右键删除最后一点
            target.pop()
            self.current_point = None
            self._refresh_plot()
            self._update_boundary_list()
                
    def _on_canvas_motion(self, event):
        if event.inaxes == self.plotter.ax and event.xdata is not None and event.ydata is not None:
            self.coord_var.set(f"X: {event.xdata:.3f}  Y: {event.ydata:.3f}")
            if self.edit_mode and self._target():
                self.current_point = (event.xdata, event.ydata)
                self.plotter.update_boundary_preview(self._target(), self.current_point)

    def _snap_to_point(self, x: float, y: float, threshold: float = 5.0) -> Tuple[float, float]:
        """吸附到最近的测量点。"""
        if not self.points:
            return x, y
        if self._point_xy is None or len(self._point_xy) != len(self.points):
            self._point_xy = np.array([(point.x, point.y) for point in self.points], dtype=float)
        distances = np.sum((self._point_xy - (x, y)) ** 2, axis=1)
        point_index = int(np.argmin(distances))
        if distances[point_index] < threshold ** 2:
            return tuple(self._point_xy[point_index])
        return x, y
        
    def _target(self) -> List[Tuple[float, float]]:
        """鼠标点击、撤销、删除点所作用的多边形：计算边界或正在绘制的挖空区。"""
        return self.drawing_hole if self.target_var.get() == "hole" else self.boundary

    def _on_target_change(self):
        self.current_point = None
        self.list_title_var.set("挖空区顶点坐标:" if self.target_var.get() == "hole" else "边界点坐标:")
        self._refresh_plot()
        self._update_boundary_list()

    def _refresh_plot(self):
        self.plotter.plot_boundary_edit(self.points, self.boundary, self.current_point,
                                        holes=self.holes, drawing_hole=self.drawing_hole)
        self._update_boundary_status()
        self._update_hole_status()

    def _update_hole_status(self):
        if not hasattr(self, "hole_status_var"):
            return
        text = f"挖空区：{len(self.holes)} 个" if self.holes else "挖空区：无"
        if self.drawing_hole:
            text += f"；正在绘制的有 {len(self.drawing_hole)} 个点"
        self.hole_status_var.set(text)

    def _finish_hole(self) -> bool:
        """把正在绘制的挖空区加入列表；无效时提示并保留，便于修改。"""
        if len(self.drawing_hole) < 3:
            messagebox.showwarning("提示", "挖空区至少需要 3 个点。先在“绘制”中选“挖空区”，再在图上逐点点击。")
            return False
        try:
            check_holes(self.boundary, self.holes + [list(self.drawing_hole)])
        except ValueError as error:
            messagebox.showwarning("挖空区无效", str(error).replace(f"第 {len(self.holes) + 1} 个挖空区", "这个挖空区"))
            return False
        self.holes.append(list(self.drawing_hole))
        self.drawing_hole = []
        self.current_point = None
        self._refresh_plot()
        self._update_boundary_list()
        return True

    def _import_holes(self):
        filepath = filedialog.askopenfilename(
            title="导入挖空区",
            filetypes=[("DXF图形", "*.dxf"), ("CSV文件", "*.csv"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if filepath:
            self.load_holes_file(filepath)

    def load_holes_file(self, filepath: str) -> bool:
        """DXF 取边界以内的全部闭合多段线；CSV/TXT 为一个挖空区（前两列为 X、Y）。"""
        try:
            if filepath.lower().endswith(".dxf"):
                from utils.dxf_io import import_holes_from_dxf
                holes = import_holes_from_dxf(filepath, self.boundary or None)
            else:
                holes = [read_xy_file(filepath)]
            check_holes(self.boundary, self.holes + holes)
        except Exception as error:
            messagebox.showerror("导入失败", str(error))
            return False
        self.holes += holes
        self._refresh_plot()
        messagebox.showinfo("成功", f"导入挖空区 {len(holes)} 个")
        return True

    def _clear_holes(self):
        if (self.holes or self.drawing_hole) and not messagebox.askyesno("清空挖空区", "删除全部挖空区？"):
            return
        self.holes = []
        self.drawing_hole = []
        self.current_point = None
        self._refresh_plot()
        self._update_boundary_list()

    def set_holes(self, holes: List[List[Tuple[float, float]]]):
        """外部设置挖空区（打开工程）。"""
        self.holes = [list(hole) for hole in holes or []]
        self.drawing_hole = []
        self._refresh_plot()

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
        """对边界和挖空区每个顶点做同一变换（平移、X/Y 互换）。"""
        self.boundary = [func(x, y) for x, y in self.boundary]
        self.holes = [[func(x, y) for x, y in hole] for hole in self.holes]
        self.drawing_hole = [func(x, y) for x, y in self.drawing_hole]
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
        target = self._target()
        if target:
            target.pop()
            self.current_point = None
            self._refresh_plot()
            self._update_boundary_list()
        
    def _update_boundary_list(self):
        self.tree_boundary.delete(*self.tree_boundary.get_children())
        for i, (x, y) in enumerate(self._target(), 1):
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
                self.boundary = read_xy_file(filepath)
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
        self._target().clear()
        self.current_point = None
        self._refresh_plot()
        self._update_boundary_list()
        
    def _delete_selected(self):
        selection = self.tree_boundary.selection()
        if selection:
            idx = int(self.tree_boundary.item(selection[0])['values'][0]) - 1
            target = self._target()
            if 0 <= idx < len(target):
                target.pop(idx)
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
        if self.drawing_hole and not self._finish_hole():
            return
        try:
            check_holes(self.boundary, self.holes)
        except ValueError as error:
            messagebox.showwarning("挖空区无效", f"{error}\n\n可“清空挖空区”后重新绘制。")
            return
        self._update_boundary_list()
        self.on_boundary_set(list(self.boundary), [list(hole) for hole in self.holes])
        holes = f"，挖空区 {len(self.holes)} 个" if self.holes else ""
        messagebox.showinfo("成功", f"边界已设置，共 {len(self.boundary)} 个点{holes}")

    def set_boundary(self, boundary: List[Tuple[float, float]]):
        """外部设置边界"""
        self.boundary = list(boundary)
        self._refresh_plot()
        self._update_boundary_list()

def read_xy_file(filepath: str) -> List[Tuple[float, float]]:
    """读取 CSV/TXT 中的多边形顶点（前两列为 X、Y，表头和说明行自动跳过）。"""
    import pandas as pd
    df = pd.read_csv(filepath, sep=None, engine="python", header=None)
    if df.shape[1] < 2:
        raise ValueError("文件列数不足，需要至少X、Y两列")
    points = []
    for _, row in df.iterrows():
        try:
            points.append((float(row[0]), float(row[1])))
        except (TypeError, ValueError):
            continue  # 表头或说明行
    return points
