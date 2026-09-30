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
SHORTCUT_MODIFIER = "Command" if sys.platform == "darwin" else "Control"
SHORTCUT_LABEL = "⌘" if sys.platform == "darwin" else "Ctrl"


class MainApplication(tk.Tk):
    """主应用程序窗口"""
    
    def __init__(self):
        super().__init__()
        
        self.geometry("1400x900")
        self.minsize(1200, 800)
        
        apply_theme(self)
        
        # 数据状态
        self.points: list[SurveyPoint] = []
        self.boundary: list[tuple[float, float]] = []
        self.project_name_var = tk.StringVar(value=DEFAULT_PROJECT_NAME)
        self.project_path = None
        self._closing = False
        self._dirty = False
        self._flash_after_id = None
        
        # 创建界面
        self._create_widgets()
        
        # 绑定关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        
    def _create_widgets(self):
        # 顶栏只占一行：左侧名称，右侧工程操作，给图表留出高度
        header = tk.Frame(self, bg=COLORS["accent"])
        header.pack(fill=tk.X)
        tk.Label(
            header, text=APP_NAME, bg=COLORS["accent"], fg=COLORS["white"], font=font(15, "bold")
        ).pack(side=tk.LEFT, padx=(16, 10), pady=10)
        tk.Label(
            header, text=f"v{__version__}  ·  {COMPANY}", bg=COLORS["accent"], fg=COLORS["header_sub"], font=font(10)
        ).pack(side=tk.LEFT, pady=(4, 0))

        actions = tk.Frame(header, bg=COLORS["accent"])
        actions.pack(side=tk.RIGHT, padx=16)
        tk.Label(
            actions, text="项目名称", bg=COLORS["accent"], fg=COLORS["white"], font=font(10)
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(actions, textvariable=self.project_name_var, width=24).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Button(actions, text="打开工程", command=self._open_project).pack(side=tk.LEFT)
        ttk.Button(actions, text="保存工程", command=self._save_project).pack(side=tk.LEFT, padx=6)
        self.recent_btn = ttk.Menubutton(actions, text="最近工程")
        self.recent_menu = tk.Menu(self.recent_btn, tearoff=0)
        self.recent_btn["menu"] = self.recent_menu
        self.recent_btn.pack(side=tk.LEFT)
        self._rebuild_recent_menu()

        # 底部状态栏：左侧操作反馈，右侧当前步骤和项目
        self.status_var = tk.StringVar(value=f"就绪 - 请导入测量数据文件（{SHORTCUT_LABEL}+O 打开工程）")
        self.step_var = tk.StringVar(value="步骤 1/3：数据导入")
        self.project_label_var = tk.StringVar(value="项目: 未加载")
        self.project_name_var.trace_add("write", lambda *_: self._on_project_name_changed())
        status = tk.Frame(self, bg=COLORS["status_bg"])
        status.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Frame(status, bg=COLORS["rule"], height=1).pack(fill=tk.X)
        self.status_label = tk.Label(
            status,
            textvariable=self.status_var,
            bg=COLORS["status_bg"],
            fg=COLORS["dim"],
            font=font(10),
            anchor=tk.W,
            padx=16,
            pady=6,
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            status, textvariable=self.project_label_var, bg=COLORS["status_bg"], fg=COLORS["dim"], font=font(10)
        ).pack(side=tk.RIGHT, padx=16)
        tk.Label(
            status, textvariable=self.step_var, bg=COLORS["status_bg"], fg=COLORS["accent"], font=font(10, "bold")
        ).pack(side=tk.RIGHT)

        # 主内容区 - 使用 Notebook 作为向导式界面，选项卡本身就是步骤指示
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(8, 10))
        
        # 页面 1: 数据导入
        self.import_frame = DataImportFrame(
            self.notebook, self._on_points_loaded, on_points_mutated=self._on_points_mutated, notify=self.notify
        )
        self.notebook.add(self.import_frame, text="  ① 数据导入与检查  ")
        
        # 页面 2: 边界设置
        self.boundary_frame = BoundaryFrame(self.notebook, [], self._on_boundary_set, notify=self.notify)
        self.notebook.add(self.boundary_frame, text="  ② 计算边界设置  ")
        self.notebook.tab(1, state='disabled')  # 初始禁用
        
        # 页面 3: 计算结果
        self.calc_frame = None  # 延迟创建

        self._bind_shortcuts()
        self._update_title()

    def _bind_shortcuts(self):
        for key, handler in (("o", self._open_project), ("s", self._save_project)):
            for sequence in (f"<{SHORTCUT_MODIFIER}-{key}>", f"<{SHORTCUT_MODIFIER}-{key.upper()}>"):
                self.bind_all(sequence, lambda _event, handler=handler: handler())
        self.bind_all("<F5>", lambda _event: self._calculate_shortcut())

    def _calculate_shortcut(self):
        if self.calc_frame is not None and str(self.notebook.select()) == str(self.calc_frame):
            self.calc_frame.start_calculation()

    def notify(self, message: str) -> None:
        """操作成功的反馈显示在状态栏（短暂高亮），不再弹窗打断操作。"""
        self.status_var.set(message)
        if self._flash_after_id is not None:
            self.after_cancel(self._flash_after_id)
        self.status_label.config(bg=COLORS["flash"], fg=COLORS["ink"])

        def restore():
            self._flash_after_id = None
            self.status_label.config(bg=COLORS["status_bg"], fg=COLORS["dim"])

        self._flash_after_id = self.after(1800, restore)

    def mark_dirty(self, *_args) -> None:
        if self.points and not self._dirty:
            self._dirty = True
            self._update_title()

    def _mark_clean(self) -> None:
        self._dirty = False
        self._update_title()

    def _update_title(self):
        prefix = "* " if self._dirty else ""
        name = f"{self.project_name} - " if self.points else ""
        self.title(f"{prefix}{name}{APP_NAME} v{__version__} - {COMPANY}")

    def _on_project_name_changed(self):
        self._update_project_label()
        self.mark_dirty()
        self._update_title()

    @property
    def project_name(self) -> str:
        return self.project_name_var.get().strip() or DEFAULT_PROJECT_NAME

    @project_name.setter
    def project_name(self, value: str) -> None:
        self.project_name_var.set(value or DEFAULT_PROJECT_NAME)

    def _update_project_label(self):
        if self.points:
            self.project_label_var.set(f"项目: {self.project_name}")

    def _on_points_loaded(self, points: list[SurveyPoint]):
        """数据导入完成回调"""
        self.points = points
        self.notify(f"已导入 {len(points)} 个测量点 - 请设置计算边界")
        self.step_var.set("步骤 2/3：边界设置")

        self.boundary_frame.set_points(points)
        self._update_project_label()
        self.mark_dirty()
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
        self.notify(f"测点已更新，共 {len(self.points)} 个")
        self.mark_dirty()

    def _on_boundary_set(self, boundary: list[tuple[float, float]]):
        """边界设置完成回调"""
        self.boundary = boundary
        self.notify(f"边界已设置（{len(boundary)} 个点）- 请输入设计高程后计算（F5）")
        self.step_var.set("步骤 3/3：计算结果")
        self.mark_dirty()

        # 创建或更新计算页面
        if self.calc_frame is None:
            self.calc_frame = CalculationFrame(
                self.notebook, self.points, self.boundary,
                project_name_getter=lambda: self.project_name,
                notify=self.notify,
                on_settings_changed=self.mark_dirty,
            )
            self.notebook.add(self.calc_frame, text="  ③ 计算设置与结果  ")
        else:
            self.calc_frame.update_inputs(self.points, self.boundary)
            
        # 启用计算页面
        self.notebook.tab(2, state='normal')
        self.notebook.select(2)

    def _collect_project_state(self) -> dict:
        boundary = list(self.boundary) or list(self.boundary_frame.boundary)
        design_elevation = 0.0
        use_partition = False
        partition = {}
        if self.calc_frame is not None:
            design_elevation = self.calc_frame.design_elevation_value()
            use_partition = bool(self.calc_frame.use_partition_var.get())
            partition = dict(self.calc_frame.partition_data)
        elif any(point.has_design_z for point in self.points):
            designs = [point.design_z for point in self.points if point.has_design_z]
            unique = {round(value, 6) for value in designs}
            if len(unique) == 1:
                design_elevation = designs[0]
            else:
                use_partition = True
                partition = {point.id: point.design_z for point in self.points if point.has_design_z}
        return {
            "points": self.points,
            "boundary": boundary,
            "design_elevation": design_elevation,
            "use_partition": use_partition,
            "partition": partition,
            "project_name": self.project_name,
        }

    def save_project_to(self, filepath: str) -> None:
        if not self.points:
            raise ValueError("没有可保存的测量点")
        state = self._collect_project_state()
        save_project(
            filepath,
            state["points"],
            state["boundary"],
            design_elevation=state["design_elevation"],
            use_partition=state["use_partition"],
            partition=state["partition"],
            project_name=state["project_name"],
        )
        self.project_path = filepath
        self._update_project_label()
        self._mark_clean()
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
        boundary = data.get("boundary") or []
        if boundary:
            self.boundary_frame.set_boundary(boundary)
            self._on_boundary_set(list(boundary))
        if self.calc_frame is not None:
            self.calc_frame.apply_design_settings(
                data.get("design_elevation") or 0.0,
                bool(data.get("use_partition")),
                data.get("partition") or {},
            )
        self._update_project_label()

    def load_project_from(self, filepath: str) -> None:
        data = load_project(filepath)
        self.apply_project(data)
        self.project_path = filepath
        self._mark_clean()
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
        if not self._confirm_discard_changes():
            return
        try:
            self.open_recent_project(filepath)
            self.notify(f"已打开工程 {os.path.basename(filepath)}，共 {len(self.points)} 个测量点")
        except Exception as error:
            messagebox.showerror("错误", f"打开失败: {error}")

    def open_recent_project(self, filepath: str) -> None:
        if not os.path.exists(filepath):
            forget_recent(filepath)
            self._rebuild_recent_menu()
            raise FileNotFoundError(f"最近工程不存在: {filepath}")
        self.load_project_from(filepath)

    def _save_project(self) -> bool:
        """保存工程；返回是否已保存（取消或失败为 False）。"""
        if not self.points:
            messagebox.showwarning("提示", "请先导入测量点再保存工程")
            return False
        filepath = filedialog.asksaveasfilename(
            title="保存工程",
            defaultextension=".tinproj.json",
            filetypes=[("TIN工程", "*.tinproj.json"), ("JSON", "*.json")],
            initialdir=os.path.dirname(self.project_path) if self.project_path else None,
            initialfile=os.path.basename(self.project_path) if self.project_path else f"{self.project_name}.tinproj.json",
        )
        if not filepath:
            return False
        if self.project_name == DEFAULT_PROJECT_NAME:
            # 未起名时用文件名作为项目名，报告抬头才有意义
            self.project_name = project_name_from_path(filepath)
        try:
            self.save_project_to(filepath)
        except Exception as error:
            messagebox.showerror("错误", f"保存失败: {error}")
            return False
        self.notify(f"工程已保存：{filepath}")
        return True

    def _confirm_discard_changes(self) -> bool:
        """有未保存的修改时询问是否先保存；返回 False 表示用户取消了当前操作。"""
        if not self._dirty:
            return True
        answer = messagebox.askyesnocancel("未保存的修改", "当前工程有未保存的修改，是否先保存？")
        if answer is None:
            return False
        if answer:
            return self._save_project()
        return True

    def _open_project(self):
        if not self._confirm_discard_changes():
            return
        filepath = filedialog.askopenfilename(
            title="打开工程",
            filetypes=[("TIN工程", "*.tinproj.json *.json"), ("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not filepath:
            return
        try:
            self.load_project_from(filepath)
            self.notify(f"已打开工程 {os.path.basename(filepath)}，共 {len(self.points)} 个测量点")
        except Exception as error:
            messagebox.showerror("错误", f"打开失败: {error}")
        
    def _on_closing(self):
        """窗口关闭事件"""
        if self._closing:
            return
        if self._dirty:
            proceed = self._confirm_discard_changes()
        else:
            proceed = messagebox.askokcancel("退出", "确定要退出程序吗？")
        if proceed:
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
