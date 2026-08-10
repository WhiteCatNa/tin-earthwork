"""
边界设置页面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Tuple, Callable, Optional
from utils.plotter import EarthworkPlotter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from core.calculator import SurveyPoint
import numpy as np


class BoundaryFrame(ttk.Frame):
    """计算边界设置页面"""
    
    def __init__(self, parent, points: List[SurveyPoint], on_boundary_set: Callable[[List[Tuple[float, float]]], None]):
        super().__init__(parent)
        self.points = points
        self.on_boundary_set = on_boundary_set
        self.boundary: List[Tuple[float, float]] = []
        self.edit_mode = True
        self.current_point: Optional[Tuple[float, float]] = None
        self._point_xy = np.empty((0, 2), dtype=float)
        
        self._create_widgets()
        self._refresh_plot()
        
    def _create_widgets(self):
        # 左侧：绘图区
        plot_frame = ttk.LabelFrame(self, text="边界编辑 (左键添加点，右键结束/删除最后一点)", padding=5)
        plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.plotter = EarthworkPlotter(figsize=(8, 6))
        self.canvas = FigureCanvasTkAgg(self.plotter.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.plotter.bind_canvas(self.canvas, enable_hover=False)

        # 绑定鼠标事件
        self._canvas_connection_ids = [
            self.canvas.mpl_connect('button_press_event', self._on_canvas_click),
            self.canvas.mpl_connect('motion_notify_event', self._on_canvas_motion),
        ]
        
        # 右侧：控制面板
        ctrl_frame = ttk.LabelFrame(self, text="边界控制", padding=10)
        ctrl_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        
        # 模式选择
        mode_frame = ttk.Frame(ctrl_frame)
        mode_frame.pack(fill=tk.X, pady=5)
        self.edit_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode_frame, text="编辑模式（左键添加）", variable=self.edit_var,
                       command=self._toggle_edit_mode).pack(anchor=tk.W)
        
        # 边界点列表
        ttk.Label(ctrl_frame, text="边界点坐标:").pack(anchor=tk.W, pady=(10, 0))
        
        list_frame = ttk.Frame(ctrl_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.tree_boundary = ttk.Treeview(list_frame, columns=('idx', 'x', 'y'), show='headings', height=15)
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
        
        # 编辑按钮
        btn_frame = ttk.Frame(ctrl_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="自动生成数据范围边界", command=self._create_auto_boundary).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="导入边界文件", command=self._import_boundary).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="撤销上一点", command=self._undo_last_point).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="清空边界", command=self._clear_boundary).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="删除选中点", command=self._delete_selected).pack(fill=tk.X, pady=2)
        ttk.Separator(btn_frame, orient='horizontal').pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="确认边界", command=self._confirm_boundary, 
                  style='Accent.TButton').pack(fill=tk.X, pady=2)
        
        self.coord_var = tk.StringVar(value="X: --  Y: --")
        ttk.Label(ctrl_frame, textvariable=self.coord_var, font=('', 10)).pack(anchor=tk.W, pady=(10, 2))
        self.boundary_status_var = tk.StringVar(value="待添加边界点")
        ttk.Label(ctrl_frame, textvariable=self.boundary_status_var, foreground='#4472C4').pack(anchor=tk.W)
        self._update_boundary_status()

        # 信息提示
        info_text = ("操作说明:\n"
                    "1. 不知道边界时，先点“自动生成数据范围边界”\n"
                    "2. 手工边界按顺时针或逆时针逐点左键点击\n"
                    "3. 点会自动吸附到附近测量点；右键或“撤销”可回退\n"
                    "4. 至少 3 个点，系统会自动闭合；确认前检查状态提示\n"
                    "5. 也可导入 CSV/TXT（前两列为 X、Y 坐标）")
        ttk.Label(ctrl_frame, text=info_text, justify=tk.LEFT, 
                 foreground='gray', font=('', 9)).pack(anchor=tk.W, pady=10)
        
    def _toggle_edit_mode(self):
        self.edit_mode = self.edit_var.get()
        self._refresh_plot()
        
    def _on_canvas_click(self, event):
        if not self.edit_mode or event.inaxes != self.plotter.ax:
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
            if self.edit_mode and self.boundary:
                self.current_point = (event.xdata, event.ydata)
                self.plotter.update_boundary_preview(self.boundary, self.current_point)

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
        
    def _refresh_plot(self):
        self.plotter.plot_boundary_edit(self.points, self.boundary, self.current_point)
        self._update_boundary_status()

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
        return not self._has_self_intersection()

    def _has_self_intersection(self) -> bool:
        segments = list(zip(self.boundary, self.boundary[1:] + self.boundary[:1]))
        for index, (start_a, end_a) in enumerate(segments):
            for other_index, (start_b, end_b) in enumerate(segments[index + 1:], index + 1):
                if abs(index - other_index) <= 1 or {index, other_index} == {0, len(segments) - 1}:
                    continue
                if self._segments_intersect(start_a, end_a, start_b, end_b):
                    return True
        return False

    @staticmethod
    def _segments_intersect(a, b, c, d) -> bool:
        def orientation(p, q, r):
            value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
            # 显式转 int，避免 numpy float64/bool_ 做减法时抛 TypeError
            return int(value > 0) - int(value < 0)
        return orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(c, d, a) * orientation(c, d, b) < 0

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
            title="导入边界坐标文件",
            filetypes=[("CSV文件", "*.csv"), ("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if not filepath:
            return
            
        try:
            # 尝试读取，支持逗号、空格、制表符分隔
            import pandas as pd
            df = pd.read_csv(filepath, sep=None, engine='python', header=None)
            if df.shape[1] >= 2:
                self.boundary = [(float(row[0]), float(row[1])) for _, row in df.iterrows()]
                self._refresh_plot()
                self._update_boundary_list()
                messagebox.showinfo("成功", f"导入边界点 {len(self.boundary)} 个")
            else:
                messagebox.showwarning("提示", "文件列数不足，需要至少X、Y两列")
        except Exception as e:
            messagebox.showerror("错误", f"导入失败: {e}")
            
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
        self._update_boundary_list()
        self.on_boundary_set(self.boundary)
        messagebox.showinfo("成功", f"边界已设置，共 {len(self.boundary)} 个点")
        
    def set_boundary(self, boundary: List[Tuple[float, float]]):
        """外部设置边界"""
        self.boundary = boundary
        self._refresh_plot()
        self._update_boundary_list()