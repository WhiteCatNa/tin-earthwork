"""
边界设置页面
"""
import math
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Tuple, Callable, Optional
from utils.plotter import EarthworkPlotter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from core.calculator import SurveyPoint
from core.geometry import ring_self_intersects, signed_area
import numpy as np

XY = Tuple[float, float]

# 鼠标操作的手感，单位都是屏幕像素（按系统显示缩放折算），与图的缩放无关
SNAP_PIXELS = 12          # 光标离测点这么近时吸附上去
VERTEX_PICK_PIXELS = 9    # 离边界顶点这么近算点中顶点
EDGE_PICK_PIXELS = 6      # 离边界线这么近算点在边上
DRAG_PIXELS = 4           # 按下后移动超过这个距离算拖动，否则算单击
ZOOM_STEP = 1.25          # 滚轮每格的缩放倍数
MAX_SCROLL_STEPS = 3.0    # 一次滚轮事件最多算几格
# macOS 的 Tk 每格滚轮的 delta 是 1（Windows 是 120），触控板还会连续发来很多小事件，
# 所以每个单位只算 0.35 格，缩放才不会一下子冲过头
MAC_SCROLL_UNIT = 0.35
# 图还没画出来、算不出像素比例时的吸附距离（米）
FALLBACK_SNAP_DISTANCE = 5.0
HISTORY_LIMIT = 100


def scroll_steps(event) -> float:
    """这次滚轮事件相当于滚了几格（向上为正）。

    matplotlib 在 Windows 和 macOS 上都把 Tk 的 delta 除以 120 作为 step：
    Windows 每格正好是 1；macOS 每格只有 1/120，要换算回来。
    """
    step = float(getattr(event, "step", 0) or 0)
    if step == 0:
        step = {"up": 1.0, "down": -1.0}.get(getattr(event, "button", None), 0.0)
    elif sys.platform == "darwin" and abs(step) < 0.5:
        step = step * 120 * MAC_SCROLL_UNIT
    return max(-MAX_SCROLL_STEPS, min(MAX_SCROLL_STEPS, step))


class BoundaryFrame(ttk.Frame):
    """计算边界设置页面"""

    def __init__(self, parent, points: List[SurveyPoint], on_boundary_set: Callable[[List[Tuple[float, float]]], None],
                 notify: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self.points = points
        self.on_boundary_set = on_boundary_set
        self.notify = notify or (lambda message: messagebox.showinfo("提示", message))
        self.boundary: List[Tuple[float, float]] = []
        self.edit_mode = True
        self.current_point: Optional[Tuple[float, float]] = None
        self._point_xy = np.empty((0, 2), dtype=float)
        self._point_ids: List[str] = []
        self._point_id_at: Dict[XY, str] = {}    # 坐标 -> 点号，列表里显示边界点对应的测点
        self._history: List[List[XY]] = []       # 撤销用：每次修改前的边界
        self._selected: Optional[int] = None     # 选中的边界顶点
        self._view = None                        # 用户缩放/平移后的视野 (xlim, ylim)；None 为全图
        self._press: Optional[dict] = None       # 鼠标按下到松开之间的状态
        self._auto_edge_text = ""                # 程序自动填的“最长边”，测点变了要重算

        self._create_widgets()
        self._refresh_plot()

    def _create_widgets(self):
        # 左侧：绘图区
        plot_frame = ttk.LabelFrame(self, text="边界编辑", padding=8)
        plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 6), pady=10)

        view_bar = ttk.Frame(plot_frame)
        view_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Button(view_bar, text="全图", width=6, command=self._reset_view).pack(side=tk.LEFT)
        # 光标坐标靠右先占位，窗口偏窄时被截短的是中间的操作提示
        self.coord_var = tk.StringVar(value="X: --  Y: --")
        ttk.Label(view_bar, textvariable=self.coord_var, style="Muted.TLabel").pack(side=tk.RIGHT)
        ttk.Label(
            view_bar, style="Hint.TLabel",
            text="滚轮缩放 · 拖动空白处平移 · 单击加点 · 点在边上插入 · 拖动顶点 · 右键删点",
        ).pack(side=tk.LEFT, padx=(8, 8))

        self.plotter = EarthworkPlotter(figsize=(8, 6))
        self.canvas = FigureCanvasTkAgg(self.plotter.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.plotter.bind_canvas(self.canvas, enable_hover=False)

        # 绑定鼠标事件
        self._canvas_connection_ids = [
            self.canvas.mpl_connect('button_press_event', self._on_canvas_press),
            self.canvas.mpl_connect('button_release_event', self._on_canvas_release),
            self.canvas.mpl_connect('motion_notify_event', self._on_canvas_motion),
            self.canvas.mpl_connect('scroll_event', self._on_canvas_scroll),
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
        self.snap_var = tk.BooleanVar(value=True)
        self.show_ids_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode_frame, text="编辑边界", variable=self.edit_var,
                       command=self._toggle_edit_mode).pack(side=tk.LEFT)
        ttk.Checkbutton(mode_frame, text="吸附测点", variable=self.snap_var).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Checkbutton(mode_frame, text="显示点号", variable=self.show_ids_var,
                       command=self._refresh_point_labels).pack(side=tk.LEFT, padx=(10, 0))

        # 边界点列表
        list_frame = ttk.Frame(ctrl_frame)
        list_frame.grid(row=2, column=0, sticky="nsew", pady=(4, 8))

        self.tree_boundary = ttk.Treeview(list_frame, columns=('idx', 'id', 'x', 'y'), show='headings', height=8,
                                          selectmode='extended')
        self.tree_boundary.heading('idx', text='序号')
        self.tree_boundary.heading('id', text='测点')
        self.tree_boundary.heading('x', text='X坐标')
        self.tree_boundary.heading('y', text='Y坐标')
        self.tree_boundary.column('idx', width=44, anchor='center')
        self.tree_boundary.column('id', width=60, anchor='center')
        self.tree_boundary.column('x', width=98, anchor='center')
        self.tree_boundary.column('y', width=98, anchor='center')
        self.tree_boundary.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree_boundary.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree_boundary.bind("<Delete>", lambda _event: self._delete_selected())

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree_boundary.yview)
        self.tree_boundary.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # 生成与编辑按钮
        btn_frame = ttk.Frame(ctrl_frame)
        btn_frame.grid(row=3, column=0, sticky="ew", pady=4)
        btn_frame.grid_columnconfigure(tuple(range(6)), weight=1, uniform="boundary_buttons")

        def place(widget, row, column, span):
            widget.grid(row=row, column=column, columnspan=span, sticky="ew",
                        padx=(0 if column == 0 else 2, 0 if column + span == 6 else 2), pady=2)

        place(ttk.Button(btn_frame, text="自动生成数据范围边界", command=self._create_auto_boundary), 0, 0, 3)
        place(ttk.Button(btn_frame, text="导入边界文件", command=self._import_boundary), 0, 3, 3)

        # 沿测点外轮廓生成：最长边留空时按测点疏密自动取值
        place(ttk.Button(btn_frame, text="沿测点外轮廓生成", command=self._create_outline_boundary), 1, 0, 3)
        edge_frame = ttk.Frame(btn_frame)
        place(edge_frame, 1, 3, 3)
        ttk.Label(edge_frame, text="最长边").pack(side=tk.LEFT)
        self.outline_edge_var = tk.StringVar()
        edge_entry = ttk.Entry(edge_frame, textvariable=self.outline_edge_var, width=6)
        edge_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        edge_entry.bind("<Return>", lambda _event: self._create_outline_boundary())
        ttk.Label(edge_frame, text="m").pack(side=tk.LEFT)

        # 按点号连线：现场沿边界测的点，输入点号按顺序连成边界
        id_frame = ttk.Frame(btn_frame)
        place(id_frame, 2, 0, 6)
        ttk.Label(id_frame, text="点号").pack(side=tk.LEFT)
        self.id_sequence_var = tk.StringVar()
        id_entry = ttk.Entry(id_frame, textvariable=self.id_sequence_var, width=10)
        id_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        id_entry.bind("<Return>", lambda _event: self._connect_by_ids())
        ttk.Button(id_frame, text="按点号连线", command=self._connect_by_ids).pack(side=tk.LEFT)

        place(ttk.Button(btn_frame, text="撤销上一步", command=self._undo_last_point), 3, 0, 2)
        place(ttk.Button(btn_frame, text="删除选中点", command=self._delete_selected), 3, 2, 2)
        place(ttk.Button(btn_frame, text="清空边界", command=self._clear_boundary), 3, 4, 2)

        ttk.Separator(btn_frame, orient='horizontal').grid(row=4, column=0, columnspan=6, sticky="ew", pady=8)
        ttk.Button(btn_frame, text="确认边界", command=self._confirm_boundary,
                  style='Accent.TButton').grid(row=5, column=0, columnspan=6, sticky="ew", pady=2)

        self.boundary_status_var = tk.StringVar(value="待添加边界点")
        ttk.Label(ctrl_frame, textvariable=self.boundary_status_var, style="Accent.TLabel").grid(
            row=5, column=0, sticky="w", pady=(6, 0)
        )
        self._update_boundary_status()

        # 信息提示
        info_text = ("操作说明:\n"
                    "1. 现场测了边界点：输入点号（如 5,6,7,12-18 或 B*）连线，\n"
                    "   或导入边界点文件（Excel/CSV/TXT/CASS .dat，可带点号）\n"
                    "2. 没有边界点：点“沿测点外轮廓生成”，或在图上逐点单击\n"
                    "3. 放大后显示测点点号；单击自动吸附到附近测点\n"
                    "4. 至少 3 个点，系统会自动闭合；确认前检查状态提示\n"
                    "5. 图纸边界可导入 DXF 多段线（圆弧按圆弧计）")
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
        self.current_point = None
        self._refresh_plot()

    # ------------------------------------------------------------------ 鼠标操作

    def _on_canvas_press(self, event):
        if event.inaxes != self.plotter.ax:
            return
        if event.button == 3:
            # 右键：点在顶点上删这个顶点，否则删最后一点
            if self.edit_mode and self.boundary:
                index = self._vertex_at(event)
                self._remove_vertices([len(self.boundary) - 1 if index is None else index])
            return
        if event.button not in (1, 2):
            return
        self._press = {
            "button": event.button,
            "x": event.x,
            "y": event.y,
            "view": self.plotter.get_view(),
            "scale": self.plotter.data_per_pixel(),
            # 左键按在顶点上是拖动这个顶点；按在别处（或中键）是平移
            "vertex": self._vertex_at(event) if event.button == 1 and self.edit_mode else None,
            "dragging": False,
            "preview": None,
        }

    def _on_canvas_motion(self, event):
        press = self._press
        if press is None:
            self._hover(event)
            return
        dx, dy = event.x - press["x"], event.y - press["y"]
        if not press["dragging"] and math.hypot(dx, dy) < self._pixels(DRAG_PIXELS):
            return
        press["dragging"] = True
        if press["vertex"] is None:
            (x0, x1), (y0, y1) = press["view"]
            shift_x, shift_y = dx * press["scale"], dy * press["scale"]
            self.plotter.set_view((x0 - shift_x, x1 - shift_x), (y0 - shift_y, y1 - shift_y))
        elif event.inaxes == self.plotter.ax and event.xdata is not None and event.ydata is not None:
            position, point_index = self._snap(event.xdata, event.ydata)
            preview = list(self.boundary)
            preview[press["vertex"]] = position
            press["preview"] = preview
            self.plotter.update_boundary_preview(self.boundary, None)
            self.plotter.update_boundary_line(preview, press["vertex"])
            self._show_coordinates(position, point_index)

    def _on_canvas_release(self, event):
        press, self._press = self._press, None
        if press is None or event.button != press["button"]:
            return
        if press["dragging"]:
            if press["vertex"] is None:
                self._view = self.plotter.get_view()
                self._refresh_point_labels()
                return
            preview = press["preview"]
            moved = preview is not None and preview != self.boundary
            if moved and len(set(preview)) == len(preview):
                self._selected = press["vertex"]
                self._apply_boundary(preview)
            else:
                if moved:
                    self.notify("这个位置已经有边界点了，顶点没有移动")
                self._refresh_plot()
            return
        if press["button"] == 1 and self.edit_mode:
            self._click(event)

    def _click(self, event):
        """左键单击：点中顶点是选中它，点在边上是插入，其余是接着往后加点。"""
        if event.inaxes != self.plotter.ax or event.xdata is None or event.ydata is None:
            return
        vertex = self._vertex_at(event)
        if vertex is not None:
            self._select_vertex(vertex)
            return
        position, _ = self._snap(event.xdata, event.ydata)
        if position in self.boundary:
            self._select_vertex(self.boundary.index(position))   # 吸附到的测点已经是边界点
            return
        boundary = list(self.boundary)
        edge = self._edge_at(event)
        if edge is None or edge == len(boundary) - 1:
            boundary.append(position)
            self._selected = None
        else:
            boundary.insert(edge + 1, position)
            self._selected = edge + 1
        self._apply_boundary(boundary)

    def _on_canvas_scroll(self, event):
        if event.inaxes != self.plotter.ax or event.xdata is None or event.ydata is None:
            return
        step = scroll_steps(event)
        if step == 0:
            return
        (x0, x1), _ = self.plotter.get_view()
        span = abs(x1 - x0) / ZOOM_STEP ** step
        if not 1e-3 <= span <= 1e9:
            return
        self.plotter.zoom_view(event.xdata, event.ydata, ZOOM_STEP ** step)
        self._view = self.plotter.get_view()
        self._refresh_point_labels()

    def _hover(self, event):
        """没按键时移动鼠标：显示坐标和附近的测点点号，预览下一点会落在哪。"""
        if event.inaxes != self.plotter.ax or event.xdata is None or event.ydata is None:
            return
        position, point_index = self._snap(event.xdata, event.ydata)
        self._show_coordinates(position, point_index)
        if not self.edit_mode:
            return
        if self._vertex_at(event) is not None:
            # 光标在顶点上：下一步是选中或拖动它，不画“下一点”的预览
            self.current_point = None
            self.plotter.update_boundary_preview(self.boundary, None)
            return
        self.current_point = position
        anchors = None
        edge = self._edge_at(event)
        if edge is not None and edge < len(self.boundary) - 1:
            anchors = (self.boundary[edge], self.boundary[edge + 1])
        self.plotter.update_boundary_preview(self.boundary, position, anchors)

    def _show_coordinates(self, position: XY, point_index: Optional[int]):
        text = f"X: {position[0]:.3f}  Y: {position[1]:.3f}"
        if point_index is not None:
            text += f"  测点 {self._point_ids[point_index]}"
        self.coord_var.set(text)

    def _pixels(self, pixels: float) -> float:
        """Windows 高分屏上画布按显示缩放放大了，像素阈值跟着放大，手感才一致。"""
        return pixels * float(getattr(self.canvas, "device_pixel_ratio", 1.0) or 1.0)

    def _boundary_pixels(self) -> np.ndarray:
        return self.plotter.ax.transData.transform(np.asarray(self.boundary, dtype=float).reshape(-1, 2))

    def _vertex_at(self, event) -> Optional[int]:
        """光标下的边界顶点序号；没有点中返回 None。"""
        if not self.boundary:
            return None
        pixels = self._boundary_pixels()
        distances = np.hypot(pixels[:, 0] - event.x, pixels[:, 1] - event.y)
        index = int(np.argmin(distances))
        return index if distances[index] <= self._pixels(VERTEX_PICK_PIXELS) else None

    def _edge_at(self, event) -> Optional[int]:
        """光标下的边的序号 i（连接第 i 点和下一点；最后一条是回到起点的闭合边）。"""
        count = len(self.boundary)
        if count < 2:
            return None
        starts = self._boundary_pixels()
        ends = np.roll(starts, -1, axis=0)
        if count == 2:
            starts, ends = starts[:1], ends[:1]   # 两个点之间只有一条边
        direction = ends - starts
        length_sq = np.maximum((direction ** 2).sum(axis=1), 1e-12)
        cursor = np.array([event.x, event.y], dtype=float)
        along = np.clip(((cursor - starts) * direction).sum(axis=1) / length_sq, 0.0, 1.0)
        nearest = starts + direction * along[:, None]
        distances = np.hypot(*(nearest - cursor).T)
        index = int(np.argmin(distances))
        return index if distances[index] <= self._pixels(EDGE_PICK_PIXELS) else None

    def _ensure_point_cache(self):
        if self._point_xy is None or len(self._point_xy) != len(self.points):
            self._point_xy = np.array([(point.x, point.y) for point in self.points], dtype=float).reshape(-1, 2)
            self._point_ids = [str(point.id) for point in self.points]
            self._point_id_at = {}
            for point in self.points:
                self._point_id_at.setdefault((float(point.x), float(point.y)), str(point.id))

    def _nearest_point(self, x: float, y: float, threshold: float) -> Optional[int]:
        """距离 (x, y) 不超过 threshold 的最近测点的序号。"""
        if not self.points:
            return None
        self._ensure_point_cache()
        distances = np.sum((self._point_xy - (x, y)) ** 2, axis=1)
        point_index = int(np.argmin(distances))
        return point_index if distances[point_index] < threshold ** 2 else None

    def _snap_to_point(self, x: float, y: float, threshold: float = FALLBACK_SNAP_DISTANCE) -> Tuple[float, float]:
        """吸附到最近的测量点。"""
        point_index = self._nearest_point(x, y, threshold)
        if point_index is None:
            return x, y
        return float(self._point_xy[point_index, 0]), float(self._point_xy[point_index, 1])

    def _snap(self, x: float, y: float) -> Tuple[XY, Optional[int]]:
        """按当前缩放把光标位置吸附到附近测点，返回（位置, 吸附到的测点序号）。"""
        scale = self.plotter.data_per_pixel()
        threshold = self._pixels(SNAP_PIXELS) * scale if scale > 0 else FALLBACK_SNAP_DISTANCE
        point_index = self._nearest_point(x, y, threshold)
        if point_index is None or not self.snap_var.get():
            return (float(x), float(y)), point_index   # 不吸附时仍返回附近测点，用来显示点号
        return (float(self._point_xy[point_index, 0]), float(self._point_xy[point_index, 1])), point_index

    # ------------------------------------------------------------------ 显示

    def _refresh_plot(self):
        self.plotter.plot_boundary_edit(
            self.points, self.boundary, self.current_point, view=self._view, selected=self._selected
        )
        self._refresh_point_labels()
        self._update_boundary_status()

    def _refresh_point_labels(self):
        self._ensure_point_cache()
        self.plotter.update_point_labels(self._point_xy, self._point_ids, self.show_ids_var.get())

    def _reset_view(self):
        self._view = None
        self._refresh_plot()

    def _update_boundary_status(self):
        if not hasattr(self, 'boundary_status_var'):
            return
        count = len(self.boundary)
        if count < 3:
            self.boundary_status_var.set(f"当前 {count} 个点；至少还需 {3 - count} 个点")
        elif self._boundary_is_valid():
            area = abs(signed_area(np.asarray(self.boundary, dtype=float)))
            self.boundary_status_var.set(f"当前 {count} 个点，面积 {area:.1f} m²；边界有效，确认后可计算")
        elif len(set(self.boundary)) != count:
            self.boundary_status_var.set("边界无效：有重复的点，请删除")
        else:
            self.boundary_status_var.set("边界无效：连线有交叉，请调整顺序或删点")

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

    def _update_boundary_list(self):
        self._ensure_point_cache()
        self.tree_boundary.delete(*self.tree_boundary.get_children())
        for i, (x, y) in enumerate(self.boundary, 1):
            self.tree_boundary.insert('', 'end', iid=str(i - 1),
                                      values=(i, self._point_id_at.get((x, y), ""), f"{x:.3f}", f"{y:.3f}"))
        if self._selected is not None and self._selected < len(self.boundary):
            self.tree_boundary.selection_set(str(self._selected))
            self.tree_boundary.see(str(self._selected))

    def _select_vertex(self, index: Optional[int]):
        """选中一个边界顶点：图上圈出，列表里同步选中。"""
        self._selected = index
        self.plotter.update_boundary_line(self.boundary, index)
        if index is None:
            self.tree_boundary.selection_remove(*self.tree_boundary.selection())
        else:
            self.tree_boundary.selection_set(str(index))
            self.tree_boundary.see(str(index))

    def _on_tree_select(self, _event=None):
        selection = self.tree_boundary.selection()
        index = int(selection[0]) if selection else None
        if index != self._selected:
            self._selected = index
            self.plotter.update_boundary_line(self.boundary, index)

    # ------------------------------------------------------------------ 修改边界

    def _apply_boundary(self, boundary, fit_view: bool = False, message: Optional[str] = None):
        """所有修改边界的操作都走这里：记入撤销历史，刷新图、列表和状态。"""
        new_boundary = [(float(x), float(y)) for x, y in boundary]
        if new_boundary != self.boundary:
            self._history.append(list(self.boundary))
            del self._history[:-HISTORY_LIMIT]
        self.boundary = new_boundary
        self.current_point = None
        if self._selected is not None and self._selected >= len(new_boundary):
            self._selected = None
        if fit_view:
            self._view = None
        self._refresh_plot()
        self._update_boundary_list()
        if message:
            self.notify(message)

    def _remove_vertices(self, indices):
        doomed = set(indices)
        self._selected = None
        self._apply_boundary([point for index, point in enumerate(self.boundary) if index not in doomed])

    def set_points(self, points: List[SurveyPoint]):
        """更新测点（使吸附坐标缓存失效）并重绘。"""
        self.points = points
        self._point_xy = None
        self._view = None
        if self.outline_edge_var.get() == self._auto_edge_text:
            self.outline_edge_var.set("")   # 自动取的“最长边”随测点重算；用户自己填的保留
        self._auto_edge_text = ""
        try:
            self._refresh_plot()
            self._update_boundary_list()
        except tk.TclError:
            pass

    def transform_boundary(self, func: Callable[[float, float], Tuple[float, float]]):
        """对边界每个顶点做同一变换（平移、X/Y 互换）。"""
        self.boundary = [func(x, y) for x, y in self.boundary]
        self._history = [[func(x, y) for x, y in previous] for previous in self._history]
        self.current_point = None
        self._view = None
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
        self._selected = None
        self._apply_boundary(
            [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))], fit_view=True
        )

    def _create_outline_boundary(self):
        """沿测点外轮廓生成边界；“最长边”留空时按测点疏密自动取值并填回输入框。"""
        from core.outline import outline_with_edge

        text = self.outline_edge_var.get().strip()
        try:
            max_edge = float(text) if text else None
        except ValueError:
            messagebox.showwarning("沿测点外轮廓生成", "“最长边”请填数字（米）。\n留空由程序按测点疏密取值，填 0 不收缩（凸包）。")
            return
        try:
            ring, used_edge = outline_with_edge([(point.x, point.y) for point in self.points], max_edge)
        except ValueError as error:
            messagebox.showwarning("沿测点外轮廓生成", str(error))
            return
        if max_edge is None:
            self._auto_edge_text = f"{used_edge:g}"
            self.outline_edge_var.set(self._auto_edge_text)
        self._selected = None
        how = "凸包，未收缩" if used_edge <= 0 else f"相邻两点最长连 {used_edge:g} m"
        self._apply_boundary(
            ring, fit_view=True,
            message=f"已沿测点外轮廓生成边界，共 {len(ring)} 个点（{how}）；太松或太紧可改“最长边”再生成",
        )

    def _connect_by_ids(self):
        """按输入的点号顺序把测点连成边界。"""
        from utils.boundary_io import parse_point_sequence

        try:
            chosen = parse_point_sequence(self.id_sequence_var.get(), self.points)
        except ValueError as error:
            messagebox.showwarning("按点号连线", str(error))
            return
        self._selected = None
        self._apply_boundary(
            [(point.x, point.y) for point in chosen], fit_view=True,
            message=f"已按点号连成边界，共 {len(chosen)} 个点；检查无误后点“确认边界”",
        )

    def _undo_last_point(self):
        """撤销上一步修改（加点、移动、删点、导入、生成都算一步）。"""
        if not self._history:
            return
        self.boundary = self._history.pop()
        self.current_point = None
        self._selected = None
        self._refresh_plot()
        self._update_boundary_list()

    def _import_boundary(self):
        filepath = filedialog.askopenfilename(
            title="导入边界文件",
            filetypes=[
                ("边界文件", "*.dxf *.xlsx *.xls *.csv *.txt *.dat"),
                ("DXF图形", "*.dxf"),
                ("Excel文件", "*.xlsx *.xls"),
                ("CSV/文本/CASS", "*.csv *.txt *.dat"),
                ("所有文件", "*.*"),
            ],
        )
        if not filepath:
            return

        skipped: List[str] = []
        try:
            if filepath.lower().endswith(".dxf"):
                from utils.dxf_io import read_boundary_from_dxf
                boundary, arc_count = read_boundary_from_dxf(filepath)
                detail = f"（含 {arc_count} 段圆弧，已按圆弧折线化）" if arc_count else ""
            else:
                from utils.boundary_io import read_boundary_points
                result = read_boundary_points(filepath)
                boundary, skipped = result.points, result.skipped
                detail = f"（按 {result.layout} 读取）"
            if len(boundary) < 3:
                messagebox.showwarning("提示", "边界至少需要 3 个点")
                return
        except Exception as error:
            messagebox.showerror("错误", f"导入失败: {error}")
            return
        self._selected = None
        self._apply_boundary(
            boundary, fit_view=True,
            message=f"已导入边界点 {len(boundary)} 个{detail}，检查无误后点“确认边界”",
        )
        if skipped:
            shown = "\n".join(skipped[:10]) + (f"\n……共 {len(skipped)} 行" if len(skipped) > 10 else "")
            messagebox.showwarning("部分行未导入", f"以下行没有读出坐标，已跳过：\n{shown}")

    def _clear_boundary(self):
        self._selected = None
        self._apply_boundary([])

    def _delete_selected(self):
        selection = self.tree_boundary.selection()
        if not selection:
            self.notify("先在列表里或图上选中要删除的边界点")
            return
        self._remove_vertices([int(item) for item in selection])

    def _confirm_boundary(self):
        if not self._boundary_is_valid():
            messagebox.showwarning("提示", "边界至少需要 3 个不重复点，且边线不能交叉。可使用“撤销上一步”修正。")
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

    def set_boundary(self, boundary: List[Tuple[float, float]]):
        """外部设置边界"""
        self.boundary = list(boundary)
        self._history = []
        self._selected = None
        self._view = None
        self._refresh_plot()
        self._update_boundary_list()
