"""
TIN 土方自动算量系统 - 主程序入口
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sys
import os
import matplotlib

matplotlib.use('TkAgg')

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.data_import_frame import DataImportFrame
from gui.boundary_frame import BoundaryFrame
from gui.calculation_frame import DEFAULT_PROJECT_NAME, CalculationFrame
from gui.theme import COLORS, apply_theme, font
from core.calculator import SurveyPoint
from utils.data_handler import load_project, save_project
from utils.recent_projects import forget_recent, load_recent, remember_recent
from version import APP_NAME, COMPANY, __version__

PROJECT_SUFFIXES = (".tinproj.json", ".json")
# 工程文件中属于计算页的设置，与 CalculationFrame.apply_design_settings 的参数一一对应
DESIGN_SETTING_KEYS = (
    "design_elevation", "use_partition", "partition", "design_mode", "design_plane",
    "compare_points", "compare_source", "grid_enabled", "grid_spacing",
)
DEFAULT_WINDOW_SIZE = (1400, 900)
MIN_WINDOW_SIZE = (1200, 800)


def window_size_for_screen(screen_width: int, screen_height: int) -> tuple[int, int]:
    """默认窗口尺寸，留出标题栏和任务栏的位置后不超过屏幕。"""
    return (
        min(DEFAULT_WINDOW_SIZE[0], screen_width - 40),
        min(DEFAULT_WINDOW_SIZE[1], screen_height - 100),
    )


class MainApplication(tk.Tk):
    """主应用程序窗口"""
    
    def __init__(self):
        super().__init__()
        
        self.title(f"{APP_NAME} v{__version__} - {COMPANY}")
        self._fit_to_screen()

        apply_theme(self)
        
        # 数据状态
        self.points: list[SurveyPoint] = []
        self.boundary: list[tuple[float, float]] = []
        self.project_name_var = tk.StringVar(value=DEFAULT_PROJECT_NAME)
        self.project_path = None
        self._pending_design_settings = None  # 打开工程时读到、等计算页创建后再应用的设置
        self._closing = False
        
        # 创建界面
        self._create_widgets()
        
        # 绑定关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        
    def _create_widgets(self):
        # 页头一行：左侧名称与单位，右侧工程操作（原先三行，小屏上占掉太多高度）
        header = tk.Frame(self, bg=COLORS["accent"])
        header.pack(fill=tk.X)
        row = tk.Frame(header, bg=COLORS["accent"])
        row.pack(fill=tk.X, padx=16, pady=8)
        titles = tk.Frame(row, bg=COLORS["accent"])
        titles.pack(side=tk.LEFT)
        tk.Label(
            titles,
            text=APP_NAME,
            bg=COLORS["accent"],
            fg=COLORS["white"],
            font=font(15, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X)
        tk.Label(
            titles,
            text=COMPANY,
            bg=COLORS["accent"],
            fg=COLORS["header_sub"],
            font=font(9),
            anchor=tk.W,
        ).pack(fill=tk.X)
        actions = tk.Frame(row, bg=COLORS["accent"])
        actions.pack(side=tk.RIGHT)
        ttk.Button(actions, text="打开工程", command=self._open_project).pack(side=tk.LEFT)
        ttk.Button(actions, text="保存工程", command=self._save_project).pack(side=tk.LEFT, padx=8)
        self.recent_btn = ttk.Menubutton(actions, text="最近工程")
        self.recent_menu = tk.Menu(self.recent_btn, tearoff=0)
        self.recent_btn["menu"] = self.recent_menu
        self.recent_btn.pack(side=tk.LEFT)
        self._rebuild_recent_menu()
        tk.Label(
            actions, text="项目名称", bg=COLORS["accent"], fg=COLORS["white"], font=font(10)
        ).pack(side=tk.LEFT, padx=(20, 6))
        ttk.Entry(actions, textvariable=self.project_name_var, width=24).pack(side=tk.LEFT)

        stepper = tk.Frame(self, bg=COLORS["surface"])
        stepper.pack(fill=tk.X)
        inner = tk.Frame(stepper, bg=COLORS["surface"])
        inner.pack(fill=tk.X, padx=16, pady=5)

        self.progress_steps = []
        step_names = ["① 数据导入", "② 边界设置", "③ 计算结果"]
        for i, name in enumerate(step_names):
            lbl = tk.Label(
                inner,
                text=name,
                bg=COLORS["surface"],
                fg=COLORS["dim"],
                font=font(11),
            )
            lbl.pack(side=tk.LEFT)
            self.progress_steps.append(lbl)
            if i < len(step_names) - 1:
                tk.Label(
                    inner,
                    text="  ›  ",
                    bg=COLORS["surface"],
                    fg=COLORS["rule"],
                    font=font(11),
                ).pack(side=tk.LEFT)

        self.project_label_var = tk.StringVar(value="项目: 未加载")
        self.project_name_var.trace_add("write", lambda *_: self._update_project_label())
        tk.Label(
            inner,
            textvariable=self.project_label_var,
            bg=COLORS["surface"],
            fg=COLORS["dim"],
            font=font(9),
        ).pack(side=tk.RIGHT)
        self.step_var = tk.StringVar(value="步骤 1/3: 数据导入")
        tk.Label(
            inner,
            textvariable=self.step_var,
            bg=COLORS["surface"],
            fg=COLORS["accent"],
            font=font(10),
        ).pack(side=tk.RIGHT, padx=16)
        self._update_step_indicator(0)

        tk.Frame(self, bg=COLORS["rule"], height=1).pack(fill=tk.X)

        # 底部状态栏：先于主内容区 pack，窗口偏矮时由主内容区收缩，状态栏不被挤掉
        self.status_var = tk.StringVar(value="就绪 - 请导入测量数据文件")
        status = self.status_bar = tk.Frame(self, bg=COLORS["status_bg"])
        status.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Frame(status, bg=COLORS["rule"], height=1).pack(fill=tk.X)
        tk.Label(
            status,
            textvariable=self.status_var,
            bg=COLORS["status_bg"],
            fg=COLORS["dim"],
            font=font(10),
            anchor=tk.W,
            padx=16,
            pady=6,
        ).pack(fill=tk.X)

        # 主内容区 - 使用 Notebook 作为向导式界面
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 页面 1: 数据导入
        self.import_frame = DataImportFrame(
            self.notebook, self._on_points_loaded, on_points_mutated=self._on_points_mutated
        )
        self.notebook.add(self.import_frame, text="  ① 数据导入与检查  ")
        
        # 页面 2: 边界设置
        self.boundary_frame = BoundaryFrame(self.notebook, [], self._on_boundary_set)
        self.notebook.add(self.boundary_frame, text="  ② 计算边界设置  ")
        self.notebook.tab(1, state='disabled')  # 初始禁用
        
        # 页面 3: 计算结果
        self.calc_frame = None  # 延迟创建

    def _fit_to_screen(self):
        """默认 1400x900；屏幕放不下时（如 1366x768 笔记本）缩到屏幕以内，避免按钮落在屏幕外。"""
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = window_size_for_screen(screen_w, screen_h)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 30)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(MIN_WINDOW_SIZE[0], width), min(MIN_WINDOW_SIZE[1], height))

    @property
    def project_name(self) -> str:
        return self.project_name_var.get().strip() or DEFAULT_PROJECT_NAME

    @project_name.setter
    def project_name(self, value: str) -> None:
        self.project_name_var.set(value or DEFAULT_PROJECT_NAME)

    def _update_project_label(self):
        if self.points:
            self.project_label_var.set(f"项目: {self.project_name}")

    def _update_step_indicator(self, current_step: int):
        """更新步骤指示器颜色"""
        for i, lbl in enumerate(self.progress_steps):
            if i < current_step:
                lbl.config(fg=COLORS["fill"], font=font(11, "bold"))
            elif i == current_step:
                lbl.config(fg=COLORS["accent"], font=font(11, "bold"))
            else:
                lbl.config(fg=COLORS["dim"], font=font(11))
                
    def _on_points_loaded(self, points: list[SurveyPoint]):
        """数据导入完成回调"""
        self.points = points
        self.status_var.set(f"已导入 {len(points)} 个测量点 - 请设置计算边界")
        self.step_var.set("步骤 2/3: 边界设置")
        self._update_step_indicator(1)
        
        self.boundary_frame.set_points(points)
        self._update_project_label()
        if self.calc_frame is not None:
            # 测点变了，旧结果作废；须重新确认边界后才能计算和导出
            self.calc_frame.update_inputs(points, self.boundary)
            self.notebook.tab(2, state='disabled')

        # 启用边界页面
        self.notebook.tab(1, state='normal')
        self.notebook.select(1)

    def _on_points_mutated(self, kind: str = "edit", dx: float = 0.0, dy: float = 0.0, dz: float = 0.0):
        """导入表改点、坐标平移或 X/Y 互换后，同步边界页与计算页。"""
        self.points = self.import_frame.points
        transform = None
        if kind == "offset" and (dx or dy):
            transform = lambda x, y: (x + dx, y + dy)
        elif kind == "swap" and (self.boundary or self.boundary_frame.boundary) and messagebox.askyesno(
            "X/Y 互换",
            "已设置的计算边界是否也交换 X/Y？\n\n"
            "边界是在本程序里对着测点画的，选“是”；\n"
            "边界是从 CAD 图纸导入的（已是图纸方向），选“否”。",
        ):
            transform = lambda x, y: (y, x)
        if transform is not None:
            self.boundary = [transform(x, y) for x, y in self.boundary]
            self.boundary_frame.transform_boundary(transform)
        self.boundary_frame.set_points(self.points)
        if self.calc_frame is not None:
            try:
                self.calc_frame.update_inputs(self.points, self.boundary)
            except tk.TclError:
                pass
        self.status_var.set(f"测点已更新，共 {len(self.points)} 个")

    def _on_boundary_set(self, boundary: list[tuple[float, float]]):
        """边界设置完成回调"""
        self.boundary = boundary
        self.status_var.set(f"边界已设置 ({len(boundary)} 个点) - 请进行计算")
        self.step_var.set("步骤 3/3: 计算结果")
        self._update_step_indicator(2)
        
        # 创建或更新计算页面
        if self.calc_frame is None:
            self.calc_frame = CalculationFrame(
                self.notebook, self.points, self.boundary, project_name_getter=lambda: self.project_name
            )
            self.notebook.add(self.calc_frame, text="  ③ 计算设置与结果  ")
        else:
            self.calc_frame.update_inputs(self.points, self.boundary)
        if self._pending_design_settings is not None:
            settings, self._pending_design_settings = self._pending_design_settings, None
            self.calc_frame.apply_design_settings(**settings)
            
        # 启用计算页面
        self.notebook.tab(2, state='normal')
        self.notebook.select(2)

    def _collect_design_settings(self) -> dict:
        """计算页的比较面与方格网设置；打开工程后尚未应用到计算页时，沿用工程里读到的设置。"""
        if self._pending_design_settings is not None:
            return dict(self._pending_design_settings)
        if self.calc_frame is not None:
            return self.calc_frame.design_settings()
        settings = {"design_elevation": 0.0, "use_partition": False, "partition": {}}
        if any(point.has_design_z for point in self.points):
            designs = [point.design_z for point in self.points if point.has_design_z]
            unique = {round(value, 6) for value in designs}
            if len(unique) == 1:
                settings["design_elevation"] = designs[0]
            else:
                settings["use_partition"] = True
                settings["partition"] = {point.id: point.design_z for point in self.points if point.has_design_z}
        return settings

    def _collect_project_state(self) -> dict:
        return {
            "points": self.points,
            "boundary": list(self.boundary) or list(self.boundary_frame.boundary),
            "project_name": self.project_name,
            **self._collect_design_settings(),
        }

    def save_project_to(self, filepath: str) -> None:
        if not self.points:
            raise ValueError("没有可保存的测量点")
        state = self._collect_project_state()
        save_project(filepath, state.pop("points"), state.pop("boundary"), **state)
        self.project_path = filepath
        self._update_project_label()
        remember_recent(filepath)
        self._rebuild_recent_menu()

    def apply_project(self, data: dict) -> None:
        points = data["points"]
        if not points:
            raise ValueError("工程文件中没有测量点")
        self.project_name = data.get("project_name") or DEFAULT_PROJECT_NAME
        self.boundary = []
        self.boundary_frame.set_boundary([])
        self.import_frame.load_points(points)
        self._on_points_loaded(points)
        settings = {key: data[key] for key in DESIGN_SETTING_KEYS if key in data}
        settings.setdefault("design_elevation", 0.0)
        settings["design_elevation"] = settings["design_elevation"] or 0.0
        # 计算页在确认边界后才创建；先记下设置，创建时再应用
        self._pending_design_settings = settings
        boundary = data.get("boundary") or []
        if boundary:
            self.boundary_frame.set_boundary(boundary)
            self._on_boundary_set(list(boundary))
        self._update_project_label()

    def load_project_from(self, filepath: str) -> None:
        data = load_project(filepath)
        self.apply_project(data)
        self.project_path = filepath
        remember_recent(filepath)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.delete(0, tk.END)
        items = load_recent()
        if not items:
            self.recent_menu.add_command(label="（无最近工程）", state=tk.DISABLED)
            return
        for path in items:
            self.recent_menu.add_command(
                label=path,
                command=lambda recent=path: self._open_recent_clicked(recent),
            )

    def _open_recent_clicked(self, filepath: str):
        try:
            self.open_recent_project(filepath)
            messagebox.showinfo("成功", f"已打开工程，共 {len(self.points)} 个测量点")
        except Exception as error:
            messagebox.showerror("错误", f"打开失败: {error}")

    def open_recent_project(self, filepath: str) -> None:
        if not os.path.exists(filepath):
            forget_recent(filepath)
            self._rebuild_recent_menu()
            raise FileNotFoundError(f"最近工程不存在: {filepath}")
        self.load_project_from(filepath)

    def _save_project(self):
        if not self.points:
            messagebox.showwarning("提示", "请先导入测量点再保存工程")
            return
        filepath = filedialog.asksaveasfilename(
            title="保存工程",
            defaultextension=".tinproj.json",
            filetypes=[("TIN工程", "*.tinproj.json"), ("JSON", "*.json")],
            initialdir=os.path.dirname(self.project_path) if self.project_path else None,
            initialfile=os.path.basename(self.project_path) if self.project_path else f"{self.project_name}.tinproj.json",
        )
        if not filepath:
            return
        if self.project_name == DEFAULT_PROJECT_NAME:
            # 未起名时用文件名作为项目名，报告抬头才有意义
            self.project_name = project_name_from_path(filepath)
        try:
            self.save_project_to(filepath)
            messagebox.showinfo("成功", "工程已保存，下次可用“打开工程”继续")
        except Exception as error:
            messagebox.showerror("错误", f"保存失败: {error}")

    def _open_project(self):
        filepath = filedialog.askopenfilename(
            title="打开工程",
            filetypes=[("TIN工程", "*.tinproj.json *.json"), ("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not filepath:
            return
        try:
            self.load_project_from(filepath)
            messagebox.showinfo("成功", f"已打开工程，共 {len(self.points)} 个测量点")
        except Exception as error:
            messagebox.showerror("错误", f"打开失败: {error}")
        
    def _on_closing(self):
        """窗口关闭事件"""
        if self._closing:
            return
        if messagebox.askokcancel("退出", "确定要退出程序吗？"):
            self._closing = True
            if self.calc_frame is not None:
                self.calc_frame.shutdown()
            self.boundary_frame.shutdown()
            self.destroy()


def project_name_from_path(filepath: str) -> str:
    name = os.path.basename(filepath)
    for suffix in PROJECT_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)] or DEFAULT_PROJECT_NAME
    return os.path.splitext(name)[0] or DEFAULT_PROJECT_NAME


def main():
    """主函数"""
    
    # 高 DPI 支持 (Windows)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass
    
    app = MainApplication()
    app.mainloop()


if __name__ == "__main__":
    main()
