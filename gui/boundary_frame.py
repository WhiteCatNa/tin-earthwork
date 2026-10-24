"""
边界设置页面
"""
import math
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from typing import Dict, List, Tuple, Callable, Optional
from utils.plotter import EarthworkPlotter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from core.breaklines import Breakline
from core.calculator import DUPLICATE_TOLERANCE, SurveyPoint, check_holes
from core.geometry import ring_self_intersects, signed_area
import numpy as np

XY = Tuple[float, float]

TARGET_BOUNDARY = "boundary"
TARGET_BREAKLINE = "breakline"
TARGET_HOLE = "hole"
BOUNDARY_TITLE = "边界编辑 - 单击加点，点边插入，拖顶点，右键删点"
BREAKLINE_TITLE = "特征线绘制 - 左键点测点添加顶点，右键结束这一条"
HOLE_TITLE = "挖空区绘制 - 左键添加顶点，右键回退，完成后点“完成挖空区”"

# 鼠标操作的手感，单位都是屏幕像素（按系统显示缩放折算），与图的缩放无关
SNAP_PIXELS = 12          # 光标离测点（或底图顶点）这么近时吸附上去
VERTEX_PICK_PIXELS = 9    # 离边界顶点这么近算点中顶点
EDGE_PICK_PIXELS = 6      # 离边界线这么近算点在边上
BACKDROP_PICK_PIXELS = 8  # 选底图线时，离线这么近算点中
DRAG_PIXELS = 4           # 按下后移动超过这个距离算拖动，否则算单击
ZOOM_STEP = 1.25          # 滚轮每格的缩放倍数
MAX_SCROLL_STEPS = 3.0    # 一次滚轮事件最多算几格
# macOS 的 Tk 每格滚轮的 delta 是 1（Windows 是 120），触控板还会连续发来很多小事件，
# 所以每个单位只算 0.35 格，缩放才不会一下子冲过头
MAC_SCROLL_UNIT = 0.35
# 图还没画出来、算不出像素比例时的吸附距离（米）
FALLBACK_SNAP_DISTANCE = 5.0
HISTORY_LIMIT = 100
# 边界内测点覆盖不到这个比例时，图上标出无测点区域、状态里写明
FULL_COVERAGE = 0.9995
SHORTCUT_MODIFIER = "Command" if sys.platform == "darwin" else "Control"


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

    def __init__(self, parent, points: List[SurveyPoint], on_boundary_set: Callable[[List[XY], List[List[XY]]], None],
                 notify: Optional[Callable[[str], None]] = None,
                 on_breaklines_changed: Optional[Callable[[List[Breakline]], None]] = None,
                 on_holes_changed: Optional[Callable[[List[List[XY]]], None]] = None):
        super().__init__(parent)
        self.points = points
        self.on_boundary_set = on_boundary_set
        self.on_breaklines_changed = on_breaklines_changed
        self.on_holes_changed = on_holes_changed
        self.notify = notify or (lambda message: messagebox.showinfo("提示", message))
        self.boundary: List[Tuple[float, float]] = []
        self.breaklines: List[Breakline] = []
        self.drawing: List[XY] = []  # 正在绘制的特征线顶点
        self.holes: List[List[XY]] = []
        self.drawing_hole: List[XY] = []
        self._hole_history: List[List[XY]] = []
        self._hole_redo: List[List[XY]] = []
        self.edit_mode = True
        self.current_point: Optional[Tuple[float, float]] = None
        self._point_xy = np.empty((0, 2), dtype=float)
        self._point_ids: List[str] = []
        self._point_id_at: Dict[XY, str] = {}    # 坐标 -> 点号，列表里显示边界点对应的测点
        self._history: List[List[XY]] = []       # 撤销用：每次修改前的边界
        self._redo: List[List[XY]] = []          # 重做用：撤销掉的边界
        self._selected: Optional[int] = None     # 选中的边界顶点
        self._view = None                        # 用户缩放/平移后的视野 (xlim, ylim)；None 为全图
        self._press: Optional[dict] = None       # 鼠标按下到松开之间的状态
        self._auto_edge_text = ""                # 程序自动填的“最长边”，测点变了要重算
        self._hull: Optional[List[XY]] = None    # 测点覆盖范围（凸包），测点变了要重算
        self._hull_ready = False
        self._problems = ([], [], 0.0)           # 当前边界的（交叉的边, 重合的点, 无测点面积）
        # CAD 底图：线（点列）、全部顶点（吸附用）、所有线段及其所属的线（点选用）
        self._backdrop: List[np.ndarray] = []
        self._backdrop_layers: List[str] = []
        self._backdrop_xy = np.empty((0, 2), dtype=float)
        self._backdrop_segments = np.empty((0, 2, 2), dtype=float)
        self._backdrop_owner = np.empty(0, dtype=int)
        self._pick_mode = False

        self._create_widgets()
        self._refresh_plot()

    def _create_widgets(self):
        # 左侧：绘图区
        plot_frame = ttk.LabelFrame(self, text="边界编辑", padding=8)
        plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 6), pady=10)

        view_bar = ttk.Frame(plot_frame)
        view_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Button(view_bar, text="全图", width=6, command=self._reset_view).pack(side=tk.LEFT)
        ttk.Button(view_bar, text="导入底图", command=self._import_backdrop).pack(side=tk.LEFT, padx=(6, 0))
        self.pick_button = ttk.Button(view_bar, text="选底图线", command=self._toggle_pick_mode)
        self.pick_button.pack(side=tk.LEFT, padx=(6, 0))
        self.backdrop_var = tk.BooleanVar(value=True)
        self.backdrop_check = ttk.Checkbutton(view_bar, text="底图", variable=self.backdrop_var,
                                              command=self._refresh_plot, state="disabled")
        self.backdrop_check.pack(side=tk.LEFT, padx=(8, 0))
        self.coord_var = tk.StringVar(value="X: --  Y: --")
        ttk.Label(view_bar, textvariable=self.coord_var, style="Muted.TLabel").pack(side=tk.RIGHT)

        # 第二行：编辑选项；操作提示在窗口偏窄时被截短
        option_bar = ttk.Frame(plot_frame)
        option_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        self.edit_var = tk.BooleanVar(value=True)
        self.snap_var = tk.BooleanVar(value=True)
        self.show_ids_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(option_bar, text="启用编辑", variable=self.edit_var,
                       command=self._toggle_edit_mode).pack(side=tk.LEFT)
        ttk.Checkbutton(option_bar, text="吸附测点", variable=self.snap_var).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Checkbutton(option_bar, text="显示点号", variable=self.show_ids_var,
                       command=self._refresh_point_labels).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(
            option_bar, style="Hint.TLabel",
            text="滚轮缩放 · 拖空白处平移 · 单击加点 · 点边插入 · 拖顶点 · 右键删点",
        ).pack(side=tk.LEFT, padx=(12, 0))

        # 边界状态放在图的下方，按绘图区宽度折行：交叉位置、无测点面积等说明较长，
        # 放在右侧面板里会挤掉坐标列表（Windows 字体更大时尤其明显）
        self.boundary_status_var = tk.StringVar(value="待添加边界点")
        self.status_label = ttk.Label(plot_frame, textvariable=self.boundary_status_var, style="Accent.TLabel",
                                      justify=tk.LEFT, wraplength=600)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        plot_frame.bind("<Configure>", lambda event: self.status_label.configure(
            wraplength=max(200, event.width - 30)))

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
        target_row = ttk.Frame(mode_frame)
        target_row.pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(target_row, text="左键绘制：").pack(side=tk.LEFT)
        self.target_var = tk.StringVar(value=TARGET_BOUNDARY)
        for value, text in ((TARGET_BOUNDARY, "计算边界"), (TARGET_BREAKLINE, "特征线"), (TARGET_HOLE, "挖空区")):
            ttk.Radiobutton(target_row, text=text, value=value, variable=self.target_var,
                            command=self._on_target_change).pack(side=tk.LEFT, padx=(0, 8))

        hole_frame = ttk.LabelFrame(ctrl_frame, text="挖空区（不计方量）", padding=6)
        hole_frame.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        hole_row = ttk.Frame(hole_frame)
        hole_row.pack(fill=tk.X, pady=(4, 0))
        self.hole_status_var = tk.StringVar(value="挖空区：无")
        ttk.Label(hole_row, textvariable=self.hole_status_var, style="Muted.TLabel").pack(side=tk.LEFT)
        hole_buttons = ttk.Frame(hole_frame)
        hole_buttons.pack(fill=tk.X, pady=(2, 0))
        hole_buttons.grid_columnconfigure((0, 1, 2), weight=1, uniform="hole_buttons")
        for column, (text, command) in enumerate((("完成挖空区", self._finish_hole),
                                                   ("导入挖空区", self._import_holes),
                                                   ("清空挖空区", self._clear_holes))):
            ttk.Button(hole_buttons, text=text, command=command).grid(
                row=0, column=column, sticky="ew", padx=(0 if column == 0 else 2, 0 if column == 2 else 2))

        hole_edit = ttk.Frame(hole_frame)
        hole_edit.pack(fill=tk.X, pady=(4, 0))
        for text, command in (("撤销顶点", self._undo_last_point), ("重做顶点", self._redo_last),
                              ("删除选中顶点", self._delete_selected), ("清空当前轮廓", self._clear_boundary)):
            ttk.Button(hole_edit, text=text, command=command, width=0).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # 边界点列表
        self.list_title_var = tk.StringVar(value="边界点坐标:")
        self.list_title_label = ttk.Label(ctrl_frame, textvariable=self.list_title_var, style="Title.TLabel")
        self.list_title_label.grid(row=1, column=0, sticky="w", pady=(10, 0))

        # 边界点列表（双击改坐标）
        list_frame = ttk.Frame(ctrl_frame)
        list_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 4))

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
        self.tree_boundary.bind("<Double-1>", self._edit_vertex)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree_boundary.yview)
        self.tree_boundary.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # 生成、录入与编辑按钮：每行一个框，按钮按文字宽度分配，整行铺满
        btn_frame = ttk.Frame(ctrl_frame)
        btn_frame.grid(row=3, column=0, sticky="ew", pady=4)

        def row():
            frame = ttk.Frame(btn_frame)
            frame.pack(side=tk.TOP, fill=tk.X, pady=1)
            return frame

        def buttons(frame, specs):
            # width=0：按文字宽度，不用主题默认的“至少 11 个字宽”，一行放得下四五个按钮
            for index, (text, command) in enumerate(specs):
                ttk.Button(frame, text=text, command=command, width=0).pack(
                    side=tk.LEFT, fill=tk.X, expand=True, padx=(0 if index == 0 else 2, 0))

        # 生成：数据范围矩形，或沿测点外轮廓（最长边留空时按测点疏密自动取值）
        generate = row()
        buttons(generate, [("数据范围矩形", self._create_auto_boundary),
                           ("沿测点外轮廓", self._create_outline_boundary)])
        ttk.Label(generate, text="最长边").pack(side=tk.LEFT, padx=(6, 0))
        self.outline_edge_var = tk.StringVar()
        edge_entry = ttk.Entry(generate, textvariable=self.outline_edge_var, width=5)
        edge_entry.pack(side=tk.LEFT, padx=2)
        edge_entry.bind("<Return>", lambda _event: self._create_outline_boundary())
        ttk.Label(generate, text="m").pack(side=tk.LEFT)

        # 按点号连线：现场沿边界测的点，输入点号按顺序连成边界
        by_ids = row()
        ttk.Label(by_ids, text="点号").pack(side=tk.LEFT)
        self.id_sequence_var = tk.StringVar()
        id_entry = ttk.Entry(by_ids, textvariable=self.id_sequence_var, width=10)
        id_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        id_entry.bind("<Return>", lambda _event: self._connect_by_ids())
        ttk.Button(by_ids, text="按点号连线", command=self._connect_by_ids, width=0).pack(side=tk.LEFT)

        buttons(row(), [("导入边界文件", self._import_boundary), ("粘贴坐标", self._paste_coordinates),
                        ("导出边界", self._export_boundary)])
        buttons(row(), [("撤销", self._undo_last_point), ("重做", self._redo_last),
                        ("理顺顺序", self._untangle), ("删除选中点", self._delete_selected),
                        ("清空边界", self._clear_boundary)])

        confirm_frame = ttk.Frame(ctrl_frame)
        confirm_frame.grid(row=5, column=0, sticky="ew", pady=4)
        ttk.Separator(confirm_frame, orient='horizontal').pack(side=tk.TOP, fill=tk.X, pady=2)
        ttk.Button(confirm_frame, text="确认边界", command=self._confirm_boundary,
                  style='Accent.TButton').pack(side=tk.TOP, fill=tk.X, pady=2)

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

        self.boundary_tools_frame = btn_frame
        self.breakline_tools_frame = line_frame
        self.hole_tools_frame = hole_frame
        self._sync_target_tools()
        self._update_boundary_status()

        # 信息提示
        info_text = ("操作说明:\n"
                    "1. 现场测了边界点：输入点号（如 5,6,7,12-18 或 B*）连线，\n"
                    "   或导入/粘贴边界点坐标（可带点号、表头）\n"
                    "2. 没有边界点：点“沿测点外轮廓”，或在图上逐点单击\n"
                    "3. 有 CAD 图：“导入底图”后沿图描，或“选底图线”直接点一条\n"
                    "4. 连线交叉时标红，可点“理顺顺序”；双击列表可改坐标\n"
                    "5. 斜线区域不在测点范围内，不计方量；确认前看状态提示\n"
                    "6. 有沟、坎、坡时选“特征线”，点测点画线；右键结束一条\n"
                    "7. 建筑物、水池等不计方量的区域：选“挖空区”逐个画，\n"
                    "   每画完一个点“完成挖空区”")
        self.info_label = ttk.Label(ctrl_frame, text=info_text, justify=tk.LEFT, style="Hint.TLabel")
        self.info_label.grid(row=6, column=0, sticky="w", pady=(10, 0))
        self.ctrl_frame, self.list_frame = ctrl_frame, list_frame
        ctrl_frame.bind("<Configure>", self._adapt_hint)

        # 快捷键：撤销、重做、删除选中点、Esc 取消选线/选中
        for widget in (self.canvas.get_tk_widget(), self.tree_boundary):
            for sequence, handler in (
                (f"<{SHORTCUT_MODIFIER}-z>", self._undo_last_point),
                (f"<{SHORTCUT_MODIFIER}-Z>", self._redo_last),          # Shift+Z
                (f"<{SHORTCUT_MODIFIER}-y>", self._redo_last),
                ("<Delete>", self._delete_selected),
                ("<BackSpace>", self._delete_selected),
                ("<Escape>", self._escape),
            ):
                widget.bind(sequence, lambda _event, handler=handler: handler() or "break")

    # 坐标列表至少保留的可见行数
    LIST_MIN_ROWS = 4

    def _adapt_hint(self, _event=None):
        """面板偏矮时先隐藏操作说明、再隐藏列表标题，把高度留给坐标列表；变高后再显示。

        表头和行高随平台字体不同（Windows 上明显更高），所以按坐标列表的实际行高计算。
        """
        tree = self.tree_boundary
        row_height = tree.winfo_reqheight() // max(int(tree.cget("height")), 1)
        try:
            row_height = int(ttk.Style(tree).lookup("Treeview", "rowheight")) or row_height
        except (tk.TclError, ValueError):
            pass
        list_min = tree.winfo_reqheight() - (int(tree.cget("height")) - self.LIST_MIN_ROWS) * row_height + 2
        # 按保留优先级排列：标题比操作说明重要；两者 grid 时都是 pady=(10, 0)
        optional = [(self.list_title_label, self.list_title_label.winfo_reqheight() + 10),
                    (self.info_label, self.info_label.winfo_reqheight() + 10)]
        base = self.ctrl_frame.winfo_reqheight() - self.list_frame.winfo_reqheight()
        base -= sum(height for widget, height in optional if widget.winfo_manager())
        budget = self.ctrl_frame.winfo_height() - base - list_min
        for widget, height in optional:
            show = budget >= height
            budget = budget - height if show else -1   # 前一项放不下，后面的也不显示
            if show and not widget.winfo_manager():
                widget.grid()
            elif not show and widget.winfo_manager():
                widget.grid_remove()

    def _toggle_edit_mode(self):
        self._press = None
        self.edit_mode = self.edit_var.get()
        self.current_point = None
        self._refresh_plot()

    # ------------------------------------------------------------------ 鼠标操作

    def _on_canvas_press(self, event):
        if event.inaxes != self.plotter.ax:
            return
        if event.button == 3:
            if self._pick_mode:
                self._set_pick_mode(False)
                return
            if self.edit_mode and self.target_var.get() == TARGET_BREAKLINE:
                self._finish_drawing()
                return
            if self.edit_mode and self.target_var.get() == TARGET_HOLE:
                if self.drawing_hole:
                    self._apply_hole(self.drawing_hole[:-1])
                return
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
            "vertex": (self._vertex_at(event)
                       if event.button == 1 and self.edit_mode and not self._pick_mode
                       and self.target_var.get() == TARGET_BOUNDARY else None),
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
        if press["button"] == 1 and self._pick_mode:
            self._pick_backdrop(event)
        elif press["button"] == 1 and self.edit_mode:
            self._click(event)

    def _click(self, event):
        """左键单击：点中顶点是选中它，点在边上是插入，其余是接着往后加点。"""
        if event.inaxes != self.plotter.ax or event.xdata is None or event.ydata is None:
            return
        if self.target_var.get() == TARGET_BREAKLINE:
            self.add_drawing_vertex(event.xdata, event.ydata)
            return
        if self.target_var.get() == TARGET_HOLE:
            position, _ = self._snap(event.xdata, event.ydata)
            if position not in self.drawing_hole:
                self._apply_hole(self.drawing_hole + [position])
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

    def _on_canvas_click(self, event):
        """单击入口；真实鼠标通过按下/松开处理，程序调用也可添加/回退顶点。"""
        if not self.edit_mode or event.inaxes != self.plotter.ax:
            return
        if event.button == 1:
            self._click(event)
        elif event.button == 3:
            self._on_canvas_press(event)

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
        if self._pick_mode:
            line = self._backdrop_line_at(event)
            self.plotter.highlight_backdrop(None if line is None else self._backdrop[line])
            self.coord_var.set("单击高亮的线作为边界（右键或 Esc 取消）" if line is not None else "把光标移到底图的线上")
            return
        if self.target_var.get() == TARGET_BREAKLINE:
            point_index = self._nearest_point(event.xdata, event.ydata, FALLBACK_SNAP_DISTANCE)
            position = ((float(self._point_xy[point_index, 0]), float(self._point_xy[point_index, 1]))
                        if point_index is not None else (event.xdata, event.ydata))
            self._show_coordinates(position, point_index)
            self.current_point = position if self.edit_mode and point_index is not None else None
            self.plotter.update_boundary_preview(self.drawing, self.current_point)
            return
        position, point_index = self._snap(event.xdata, event.ydata)
        self._show_coordinates(position, point_index)
        if self.target_var.get() == TARGET_HOLE:
            self.current_point = position if self.edit_mode else None
            self.plotter.update_boundary_preview(self.drawing_hole, self.current_point)
            return
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

    @staticmethod
    def _segment_distances(starts: np.ndarray, ends: np.ndarray, cursor: np.ndarray) -> np.ndarray:
        direction = ends - starts
        length_sq = np.maximum((direction ** 2).sum(axis=1), 1e-12)
        along = np.clip(((cursor - starts) * direction).sum(axis=1) / length_sq, 0.0, 1.0)
        return np.hypot(*(starts + direction * along[:, None] - cursor).T)

    def _edge_at(self, event) -> Optional[int]:
        """光标下的边的序号 i（连接第 i 点和下一点；最后一条是回到起点的闭合边）。"""
        count = len(self.boundary)
        if count < 2:
            return None
        starts = self._boundary_pixels()
        ends = np.roll(starts, -1, axis=0)
        if count == 2:
            starts, ends = starts[:1], ends[:1]   # 两个点之间只有一条边
        distances = self._segment_distances(starts, ends, np.array([event.x, event.y], dtype=float))
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
        return point_index if distances[point_index] <= threshold ** 2 else None

    def _snap_to_point(self, x: float, y: float, threshold: float = FALLBACK_SNAP_DISTANCE) -> Tuple[float, float]:
        """吸附到最近的测量点。"""
        point_index = self._nearest_point(x, y, threshold)
        if point_index is None:
            return x, y
        return float(self._point_xy[point_index, 0]), float(self._point_xy[point_index, 1])

    def _snap(self, x: float, y: float) -> Tuple[XY, Optional[int]]:
        """按当前缩放把光标位置吸附到附近测点或底图顶点，返回（位置, 吸附到的测点序号）。"""
        scale = self.plotter.data_per_pixel()
        threshold = self._pixels(SNAP_PIXELS) * scale if scale > 0 else FALLBACK_SNAP_DISTANCE
        point_index = self._nearest_point(x, y, threshold)
        if not self.snap_var.get():
            return (float(x), float(y)), point_index   # 不吸附时仍返回附近测点，用来显示点号
        best, best_distance = None, threshold
        if point_index is not None:
            best = (float(self._point_xy[point_index, 0]), float(self._point_xy[point_index, 1]))
            best_distance = math.hypot(best[0] - x, best[1] - y)
        if self._backdrop_shown() and len(self._backdrop_xy):
            distances = np.hypot(self._backdrop_xy[:, 0] - x, self._backdrop_xy[:, 1] - y)
            index = int(np.argmin(distances))
            if distances[index] < best_distance:
                return (float(self._backdrop_xy[index, 0]), float(self._backdrop_xy[index, 1])), None
        if best is None:
            return (float(x), float(y)), None
        return best, point_index

    # ------------------------------------------------------------------ 显示

    def _backdrop_shown(self) -> bool:
        return bool(self._backdrop) and self.backdrop_var.get()

    def _refresh_plot(self):
        self._problems = self._find_problems()
        crossings, duplicates, uncovered = self._problems
        self.plotter.plot_boundary_edit(
            self.points, self.boundary, self.current_point, view=self._view, selected=self._selected,
            backdrop=self._backdrop if self._backdrop_shown() else None,
            crossings=crossings, duplicates=duplicates,
            hull=self._hull if uncovered > 0 else None,
            breaklines=self.breaklines, drawing=self.drawing,
            holes=self.holes, drawing_hole=self.drawing_hole,
            title={TARGET_BREAKLINE: BREAKLINE_TITLE, TARGET_HOLE: HOLE_TITLE}.get(
                self.target_var.get(), BOUNDARY_TITLE),
        )
        self._refresh_point_labels()
        self._update_boundary_status()
        self._update_breakline_status()
        self._update_hole_status()

    # ------------------------------------------------------------------
    # 特征线
    # ------------------------------------------------------------------

    def _target(self) -> List[XY]:
        return self.drawing_hole if self.target_var.get() == TARGET_HOLE else self.boundary

    def _sync_target_tools(self):
        """只显示当前绘制对象的工具，把空间留给坐标列表；确认按钮始终可见。"""
        target = self.target_var.get()
        for value, frame in ((TARGET_BOUNDARY, self.boundary_tools_frame),
                             (TARGET_BREAKLINE, self.breakline_tools_frame),
                             (TARGET_HOLE, self.hole_tools_frame)):
            if target == value:
                frame.grid()
            else:
                frame.grid_remove()

    def _on_target_change(self):
        self._sync_target_tools()
        self._press = None
        self._selected = None
        self.list_title_var.set("挖空区顶点坐标:" if self.target_var.get() == TARGET_HOLE else "边界点坐标:")
        self.current_point = None
        if self._pick_mode:
            self._set_pick_mode(False)
        self._refresh_plot()
        self._update_boundary_list()

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
        point_index = self._nearest_point(x, y, FALLBACK_SNAP_DISTANCE)
        if point_index is None:
            self._update_breakline_status("特征线顶点须点在测点上：附近 5 m 内没有测点")
            return False
        snapped = tuple(float(value) for value in self._point_xy[point_index])
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
        self.current_point = None
        self._press = None
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
        self._hole_history.clear()
        self._hole_redo.clear()
        self.current_point = None
        self._selected = None
        self._notify_holes()
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
        self._notify_holes()
        self._refresh_plot()
        messagebox.showinfo("成功", f"导入挖空区 {len(holes)} 个")
        return True

    def _clear_holes(self):
        if (self.holes or self.drawing_hole) and not messagebox.askyesno("清空挖空区", "删除全部挖空区？"):
            return
        self.holes = []
        self.drawing_hole = []
        self._hole_history.clear()
        self._hole_redo.clear()
        self.current_point = None
        self._selected = None
        self._notify_holes()
        self._refresh_plot()
        self._update_boundary_list()

    def set_holes(self, holes: List[List[Tuple[float, float]]]):
        """外部设置挖空区（打开工程）。"""
        self.holes = [list(hole) for hole in holes or []]
        self.drawing_hole = []
        self._hole_history.clear()
        self._hole_redo.clear()
        self.current_point = None
        self._press = None
        self._selected = None
        self._refresh_plot()
        self._update_boundary_list()

    def _notify_holes(self):
        if self.on_holes_changed is not None:
            self.on_holes_changed([list(hole) for hole in self.holes])

    def _apply_hole(self, ring, remember: bool = True):
        """修改正在绘制的挖空区；不改动计算边界或已完成的挖空区。"""
        ring = [(float(x), float(y)) for x, y in ring]
        if remember and ring != self.drawing_hole:
            self._hole_history.append(list(self.drawing_hole))
            del self._hole_history[:-HISTORY_LIMIT]
            self._hole_redo.clear()
        self.drawing_hole = ring
        self.current_point = None
        if self._selected is not None and self._selected >= len(ring):
            self._selected = None
        self._refresh_plot()
        self._update_boundary_list()

    def _find_problems(self):
        """当前边界的问题：交叉的边、重合的点、落在测点范围以外的面积。"""
        from core.boundary_check import covered_area, duplicate_vertices, find_crossings

        if len(self.boundary) < 3:
            return [], [], 0.0
        duplicates = duplicate_vertices(self.boundary)
        crossings = find_crossings(self.boundary)
        uncovered = 0.0
        hull = self._points_hull()
        if hull and not duplicates and not crossings:
            area = abs(signed_area(np.asarray(self.boundary, dtype=float)))
            covered = covered_area(self.boundary, hull)
            if area > 0 and covered < area * FULL_COVERAGE:
                uncovered = area - covered
        return crossings, duplicates, uncovered

    def _points_hull(self) -> Optional[List[XY]]:
        if not self._hull_ready:
            from core.boundary_check import convex_hull

            self._hull = convex_hull([(point.x, point.y) for point in self.points]) if self.points else None
            self._hull_ready = True
        return self._hull

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
        crossings, duplicates, uncovered = self._problems
        if count < 3:
            self.boundary_status_var.set(f"当前 {count} 个点；至少还需 {3 - count} 个点")
        elif duplicates:
            index = duplicates[0]
            first = self.boundary.index(self.boundary[index])
            self.boundary_status_var.set(
                f"边界无效：第 {index + 1} 点与第 {first + 1} 点重合（图中 ×），请删掉一个")
        elif crossings:
            i, j = crossings[0]
            more = f"等 {len(crossings)} 处" if len(crossings) > 1 else ""
            self.boundary_status_var.set(
                f"边界无效：第 {i + 1}→{(i + 1) % count + 1} 点与第 {j + 1}→{(j + 1) % count + 1} 点的连线交叉"
                f"{more}（图中红线）；可点“理顺顺序”或拖动顶点")
        else:
            area = abs(signed_area(np.asarray(self.boundary, dtype=float)))
            text = f"当前 {count} 个点，面积 {area:.1f} m²；边界有效，确认后可计算"
            if uncovered > 0:
                text += (f"\n其中 {uncovered:.1f} m²（{uncovered / area * 100:.1f}%）不在测点范围内"
                         f"（图中斜线），这部分不计方量")
            self.boundary_status_var.set(text)

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
        for i, (x, y) in enumerate(self._target(), 1):
            self.tree_boundary.insert('', 'end', iid=str(i - 1),
                                      values=(i, self._point_id_at.get((x, y), ""), f"{x:.3f}", f"{y:.3f}"))
        if self._selected is not None and self._selected < len(self._target()):
            self.tree_boundary.selection_set(str(self._selected))
            self.tree_boundary.see(str(self._selected))

    def _select_vertex(self, index: Optional[int]):
        """选中一个边界顶点：图上圈出，列表里同步选中。"""
        self._selected = index
        if self.target_var.get() != TARGET_HOLE:
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
            if self.target_var.get() != TARGET_HOLE:
                self.plotter.update_boundary_line(self.boundary, index)

    def _escape(self):
        """Esc：退出选底图线，取消选中和“下一点”预览。"""
        self._press = None
        if self._pick_mode:
            self._set_pick_mode(False)
        self.current_point = None
        self.plotter.update_boundary_preview(self.boundary, None)
        self._select_vertex(None)

    # ------------------------------------------------------------------ 修改边界

    def _apply_boundary(self, boundary, fit_view: bool = False, message: Optional[str] = None):
        """所有修改边界的操作都走这里：记入撤销历史，刷新图、列表和状态。"""
        new_boundary = [(float(x), float(y)) for x, y in boundary]
        if new_boundary != self.boundary:
            self._history.append(list(self.boundary))
            del self._history[:-HISTORY_LIMIT]
            self._redo.clear()
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

    def _restore(self, boundary: List[XY]):
        self.boundary = boundary
        self.current_point = None
        self._selected = None
        self._refresh_plot()
        self._update_boundary_list()

    def _remove_vertices(self, indices):
        doomed = set(indices)
        self._selected = None
        ring = [point for index, point in enumerate(self._target()) if index not in doomed]
        if self.target_var.get() == TARGET_HOLE:
            self._apply_hole(ring)
        else:
            self._apply_boundary(ring)

    def set_points(self, points: List[SurveyPoint]):
        """更新测点（使吸附坐标缓存失效）并重绘。"""
        self.points = points
        self._point_xy = None
        self._hull_ready = False
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
        """对边界、特征线及边界撤销历史做同一变换（平移、X/Y 互换）。"""
        self.boundary = [func(x, y) for x, y in self.boundary]
        self._history = [[func(x, y) for x, y in previous] for previous in self._history]
        self._redo = [[func(x, y) for x, y in previous] for previous in self._redo]
        self.holes = [[func(x, y) for x, y in hole] for hole in self.holes]
        self.drawing_hole = [func(x, y) for x, y in self.drawing_hole]
        self._hole_history = [[func(x, y) for x, y in ring] for ring in self._hole_history]
        self._hole_redo = [[func(x, y) for x, y in ring] for ring in self._hole_redo]
        if self.holes:
            self._notify_holes()
        self.drawing = [func(x, y) for x, y in self.drawing]
        if self.breaklines:
            self.breaklines = [Breakline([(*func(x, y), z) for x, y, z in line.vertices], name=line.name)
                               for line in self.breaklines]
            self._notify_breaklines()
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
        if self.target_var.get() == TARGET_HOLE:
            if self._hole_history:
                self._hole_redo.append(list(self.drawing_hole))
                self._apply_hole(self._hole_history.pop(), remember=False)
            elif self.drawing_hole:
                self._apply_hole(self.drawing_hole[:-1])
            return
        if not self._history:
            return
        self._redo.append(list(self.boundary))
        self._restore(self._history.pop())

    def _redo_last(self):
        """重做刚撤销的那一步。"""
        if self.target_var.get() == TARGET_HOLE:
            if self._hole_redo:
                self._hole_history.append(list(self.drawing_hole))
                self._apply_hole(self._hole_redo.pop(), remember=False)
            return
        if not self._redo:
            return
        self._history.append(list(self.boundary))
        self._restore(self._redo.pop())

    def _untangle(self):
        """连线交叉时调整连线顺序（点不增不减），直到没有交叉。"""
        from core.boundary_check import untangle

        if len(self.boundary) < 4 or not self._problems[0]:
            self.notify("连线没有交叉，不需要理顺")
            return
        try:
            ordered = untangle(self.boundary)
        except ValueError as error:
            messagebox.showwarning("理顺顺序", str(error))
            return
        self._selected = None
        self._apply_boundary(ordered, message="已理顺连线顺序（点没有增减）；不满意可以撤销")

    def _edit_vertex(self, event=None):
        """双击列表的一行：输入新坐标“X, Y”，或输入一个测点点号改用它的坐标。"""
        from utils.boundary_io import find_point

        if event is not None:
            row = self.tree_boundary.identify_row(event.y)   # 双击在表头或空白处：不改
        else:
            selection = self.tree_boundary.selection()
            row = selection[0] if selection else ""
        if not row:
            return
        index = int(row)
        target = self._target()
        x, y = target[index]
        answer = simpledialog.askstring(
            "修改边界点", f"第 {index + 1} 点的新坐标（X, Y），或填一个测点点号：",
            initialvalue=f"{x:.3f}, {y:.3f}", parent=self,
        )
        if answer is None or not answer.strip():
            return
        numbers = [part for part in answer.replace("，", ",").replace(",", " ").split() if part]
        try:
            if len(numbers) == 2:
                position = (float(numbers[0]), float(numbers[1]))
                if not all(math.isfinite(value) for value in position):
                    raise ValueError
            else:
                point = find_point(answer, self.points)
                if point is None:
                    raise ValueError
                position = (float(point.x), float(point.y))
        except ValueError:
            messagebox.showwarning("修改边界点", f"没看懂“{answer}”：请填“X, Y”两个数，或一个测点点号")
            return
        if position in target and target.index(position) != index:
            messagebox.showwarning("修改边界点", "这个位置已经有边界点了")
            return
        boundary = list(target)
        boundary[index] = position
        self._selected = index
        if self.target_var.get() == TARGET_HOLE:
            self._apply_hole(boundary)
        else:
            self._apply_boundary(boundary)

    def _paste_coordinates(self):
        """把剪贴板里的坐标（如从 Excel 复制的点号、X、Y 几列）作为边界。"""
        from utils.boundary_io import read_boundary_text

        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if not text.strip():
            messagebox.showwarning("粘贴坐标", "剪贴板里没有文字。\n先在 Excel 等处选中点号、X、Y 几列复制，再点“粘贴坐标”。")
            return
        try:
            result = read_boundary_text(text)
        except ValueError as error:
            messagebox.showwarning("粘贴坐标", str(error))
            return
        self._selected = None
        self._apply_boundary(
            result.points, fit_view=True,
            message=f"已粘贴边界点 {len(result.points)} 个（按 {result.layout} 读取），检查无误后点“确认边界”",
        )
        self._warn_skipped(result.skipped)

    def _export_boundary(self):
        """当前边界导出成 CSV 坐标表或 DXF，下次可用“导入边界文件”读回。"""
        if len(self.boundary) < 3:
            messagebox.showwarning("导出边界", "边界至少需要 3 个点")
            return
        filepath = filedialog.asksaveasfilename(
            title="导出边界", defaultextension=".csv", initialfile="计算边界",
            filetypes=[("CSV 坐标表", "*.csv"), ("DXF图形", "*.dxf")],
        )
        if not filepath:
            return
        try:
            if filepath.lower().endswith(".dxf"):
                from utils.data_handler import DataExporter
                DataExporter().export_boundary_only_dxf(self.boundary, filepath, self.points)
            else:
                from utils.boundary_io import write_boundary_csv
                write_boundary_csv(filepath, self.boundary, self.points)
        except OSError as error:
            messagebox.showerror("导出边界", f"导出失败: {error}")
            return
        self.notify(f"边界已导出：{filepath}")

    def _warn_skipped(self, skipped: List[str]):
        if skipped:
            shown = "\n".join(skipped[:10]) + (f"\n……共 {len(skipped)} 行" if len(skipped) > 10 else "")
            messagebox.showwarning("部分行未导入", f"以下行没有读出坐标，已跳过：\n{shown}")

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
        backdrop = []
        try:
            if filepath.lower().endswith(".dxf"):
                from utils.dxf_io import read_boundary_from_dxf, read_dxf_backdrop
                boundary, arc_count = read_boundary_from_dxf(filepath)
                detail = f"（含 {arc_count} 段圆弧，已按圆弧折线化）" if arc_count else ""
                backdrop = read_dxf_backdrop(filepath)
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
        message = f"已导入边界点 {len(boundary)} 个{detail}，检查无误后点“确认边界”"
        if len(backdrop) > 1:
            # 图里不止一条线：整张图作为底图，自动选的不对可以点选别的
            self._set_backdrop(backdrop)
            message = (f"DXF 里有 {len(backdrop)} 条线，已自动选了一条作边界{detail}；"
                       f"不对的话点“选底图线”，在图上点要用的那条")
        self._selected = None
        self._apply_boundary(boundary, fit_view=True, message=message)
        self._warn_skipped(skipped)

    # ------------------------------------------------------------------ CAD 底图

    def _import_backdrop(self):
        filepath = filedialog.askopenfilename(title="导入底图（DXF）", filetypes=[("DXF图形", "*.dxf"), ("所有文件", "*.*")])
        if not filepath:
            return
        from utils.dxf_io import read_dxf_backdrop

        try:
            backdrop = read_dxf_backdrop(filepath)
        except Exception as error:
            messagebox.showerror("导入底图", f"读取失败: {error}")
            return
        if not backdrop:
            messagebox.showwarning(
                "导入底图", "DXF 里没有找到直线或多段线。\n如果图形在图块里，请先在 CAD 里把图块炸开（EXPLODE）再存一份。")
            return
        self._set_backdrop(backdrop)
        self._refresh_plot()
        message = f"已载入底图：{len(backdrop)} 条线。可沿图单击描边界（会吸附到图上的顶点），或点“选底图线”直接选一条"
        if self.points and not self._backdrop_overlaps_points():
            messagebox.showwarning(
                "底图与测点不重叠",
                "底图和测点的范围完全不重叠，可能是坐标系不同，或测点的 X/Y 与图纸方向相反"
                "（可在数据导入页点“X/Y 互换”）。")
        self.notify(message)

    def _set_backdrop(self, polylines):
        """载入底图：记下每条线的点列、所有顶点和线段，供显示、吸附和点选。"""
        lines, layers = [], []
        for item in polylines:
            points = np.asarray(item.points, dtype=float).reshape(-1, 2)
            if item.closed and len(points) >= 3 and not np.array_equal(points[0], points[-1]):
                points = np.vstack([points, points[:1]])
            if len(points) >= 2:
                lines.append(points)
                layers.append(item.layer)
        self._backdrop, self._backdrop_layers = lines, layers
        self._backdrop_xy = np.unique(np.vstack(lines), axis=0) if lines else np.empty((0, 2), dtype=float)
        if lines:
            self._backdrop_segments = np.concatenate([np.stack([line[:-1], line[1:]], axis=1) for line in lines])
            self._backdrop_owner = np.concatenate([np.full(len(line) - 1, index) for index, line in enumerate(lines)])
        else:
            self._backdrop_segments = np.empty((0, 2, 2), dtype=float)
            self._backdrop_owner = np.empty(0, dtype=int)
        self.backdrop_var.set(True)
        self.backdrop_check.configure(state="normal" if lines else "disabled")

    def _backdrop_overlaps_points(self) -> bool:
        low, high = self._backdrop_xy.min(axis=0), self._backdrop_xy.max(axis=0)
        self._ensure_point_cache()
        plow, phigh = self._point_xy.min(axis=0), self._point_xy.max(axis=0)
        return bool(np.all(low <= phigh) and np.all(plow <= high))

    def _toggle_pick_mode(self):
        self._set_pick_mode(not self._pick_mode)

    def _set_pick_mode(self, active: bool):
        if active and not self._backdrop:
            messagebox.showwarning("选底图线", "还没有底图。先点“导入底图”选一个 DXF 文件。")
            return
        if active and not self.backdrop_var.get():
            self.backdrop_var.set(True)
            self._refresh_plot()
        self._pick_mode = active
        self.pick_button.configure(text="取消选线" if active else "选底图线")
        self.plotter.highlight_backdrop(None)
        if active:
            self.current_point = None
            self.plotter.update_boundary_preview(self.boundary, None)
            self.notify("选底图线：把光标移到要用的线上（会高亮），单击选用；右键或 Esc 取消")

    def _backdrop_line_at(self, event) -> Optional[int]:
        """光标下的底图线的序号。"""
        if not len(self._backdrop_segments):
            return None
        transform = self.plotter.ax.transData.transform
        starts = transform(self._backdrop_segments[:, 0])
        ends = transform(self._backdrop_segments[:, 1])
        distances = self._segment_distances(starts, ends, np.array([event.x, event.y], dtype=float))
        index = int(np.argmin(distances))
        return int(self._backdrop_owner[index]) if distances[index] <= self._pixels(BACKDROP_PICK_PIXELS) else None

    def _pick_backdrop(self, event):
        line = self._backdrop_line_at(event)
        if line is None:
            return
        points = [(float(x), float(y)) for x, y in self._backdrop[line]]
        if len(points) >= 2 and points[0] == points[-1]:
            points = points[:-1]
        if len(points) < 3:
            messagebox.showwarning("选底图线", f"这条线只有 {len(points)} 个点，围不成边界；请选闭合的多段线，或沿图单击描出边界")
            return
        self._set_pick_mode(False)
        self._selected = None
        self._apply_boundary(points, message=f"已选用底图图层“{self._backdrop_layers[line]}”上的一条线作边界，共 {len(points)} 个点")

    def _clear_boundary(self):
        self._selected = None
        if self.target_var.get() == TARGET_HOLE:
            self._apply_hole([])
        else:
            self._apply_boundary([])

    def _delete_selected(self):
        selection = self.tree_boundary.selection()
        if not selection:
            self.notify("先在列表里或图上选中要删除的边界点")
            return
        self._remove_vertices([int(item) for item in selection])

    def _confirm_boundary(self):
        if not self._boundary_is_valid():
            messagebox.showwarning("提示", "边界至少需要 3 个不重复点，且边线不能交叉。可使用“撤销”或“理顺顺序”修正。")
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

    def set_boundary(self, boundary: List[Tuple[float, float]]):
        """外部设置边界"""
        self.current_point = None
        self._press = None
        self.boundary = list(boundary)
        self._history = []
        self._redo = []
        self._selected = None
        self._view = None
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


def read_xy_file(filepath: str) -> List[XY]:
    """读取 CSV/TXT 中的多边形顶点，兼容表头、点号与说明行。"""
    from utils.boundary_io import read_boundary_points
    return read_boundary_points(filepath).points
