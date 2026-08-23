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
from gui.calculation_frame import CalculationFrame
from gui.theme import COLORS, apply_theme, font
from core.calculator import SurveyPoint
from utils.data_handler import load_project, save_project
from utils.recent_projects import forget_recent, load_recent, remember_recent


class MainApplication(tk.Tk):
    """主应用程序窗口"""
    
    def __init__(self):
        super().__init__()
        
        self.title("TIN 土方自动算量系统 v1.3 - 上海上铁建筑工程（集团）有限公司")
        self.geometry("1400x900")
        self.minsize(1200, 800)
        
        apply_theme(self)
        
        # 数据状态
        self.points: list[SurveyPoint] = []
        self.boundary: list[tuple[float, float]] = []
        self.project_name = "TIN土方计算项目"
        self.project_path = None
        self._closing = False
        
        # 创建界面
        self._create_widgets()
        
        # 绑定关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        
    def _create_widgets(self):
        header = tk.Frame(self, bg=COLORS["accent"])
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="TIN 土方自动算量系统",
            bg=COLORS["accent"],
            fg=COLORS["white"],
            font=font(16, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X, padx=16, pady=(12, 0))
        tk.Label(
            header,
            text="上海上铁建筑工程（集团）有限公司",
            bg=COLORS["accent"],
            fg=COLORS["header_sub"],
            font=font(10),
            anchor=tk.W,
        ).pack(fill=tk.X, padx=16, pady=(2, 12))
        actions = tk.Frame(header, bg=COLORS["accent"])
        actions.pack(fill=tk.X, padx=16, pady=(0, 10))
        ttk.Button(actions, text="打开工程", command=self._open_project).pack(side=tk.LEFT)
        ttk.Button(actions, text="保存工程", command=self._save_project).pack(side=tk.LEFT, padx=8)
        self.recent_btn = ttk.Menubutton(actions, text="最近工程")
        self.recent_menu = tk.Menu(self.recent_btn, tearoff=0)
        self.recent_btn["menu"] = self.recent_menu
        self.recent_btn.pack(side=tk.LEFT)
        self._rebuild_recent_menu()

        stepper = tk.Frame(self, bg=COLORS["surface"])
        stepper.pack(fill=tk.X)
        inner = tk.Frame(stepper, bg=COLORS["surface"])
        inner.pack(fill=tk.X, padx=16, pady=8)

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
        
        # 底部状态栏
        self.status_var = tk.StringVar(value="就绪 - 请导入测量数据文件")
        status = tk.Frame(self, bg=COLORS["status_bg"])
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
        
        # 更新边界页面的测量点，并使吸附坐标缓存失效。
        self.boundary_frame.points = points
        self.boundary_frame._point_xy = None
        self.boundary_frame._refresh_plot()
        
        # 启用边界页面
        self.notebook.tab(1, state='normal')
        self.notebook.select(1)

    def _on_points_mutated(self, kind: str = "edit", dx: float = 0.0, dy: float = 0.0, dz: float = 0.0):
        """导入表改点或坐标平移后，同步边界页与计算页。"""
        self.points = self.import_frame.points
        if kind == "offset" and (dx or dy):
            self.boundary = [(x + dx, y + dy) for x, y in self.boundary]
            self.boundary_frame.boundary = [(x + dx, y + dy) for x, y in self.boundary_frame.boundary]
            self.boundary_frame._update_boundary_list()
        self.boundary_frame.points = self.points
        self.boundary_frame._point_xy = None
        try:
            self.boundary_frame._refresh_plot()
        except tk.TclError:
            pass
        if self.calc_frame is not None:
            self.calc_frame.points = self.points
            self.calc_frame.boundary = self.boundary
            self.calc_frame.calculator.add_points(self.points)
            self.calc_frame.calculator.set_boundary(self.boundary)
            self.calc_frame.result = None
            try:
                self.calc_frame._refresh_plot()
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
            self.calc_frame = CalculationFrame(self.notebook, self.points, self.boundary)
            self.notebook.add(self.calc_frame, text="  ③ 计算设置与结果  ")
        else:
            self.calc_frame.points = self.points
            self.calc_frame.boundary = boundary
            self.calc_frame.calculator.add_points(self.points)
            self.calc_frame.calculator.set_boundary(boundary)
            self.calc_frame.result = None
            self.calc_frame._refresh_plot()
            
        # 启用计算页面
        self.notebook.tab(2, state='normal')
        self.notebook.select(2)

    def _collect_project_state(self) -> dict:
        boundary = list(self.boundary) or list(self.boundary_frame.boundary)
        design_elevation = 0.0
        use_partition = False
        partition = {}
        if self.calc_frame is not None:
            design_elevation = float(self.calc_frame.design_elevation_var.get())
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
        self.project_label_var.set(f"项目: {self.project_name}")
        remember_recent(filepath)
        self._rebuild_recent_menu()

    def apply_project(self, data: dict) -> None:
        points = data["points"]
        if not points:
            raise ValueError("工程文件中没有测量点")
        self.project_name = data.get("project_name") or "TIN土方计算项目"
        self.import_frame.points = points
        self.import_frame._update_preview()
        self.import_frame._update_stats()
        self._on_points_loaded(points)
        boundary = data.get("boundary") or []
        if boundary:
            self.boundary_frame.set_boundary(boundary)
            self._on_boundary_set(boundary)
        if self.calc_frame is not None:
            self.calc_frame.design_elevation_var.set(data.get("design_elevation") or 0.0)
            self.calc_frame.use_partition_var.set(bool(data.get("use_partition")))
            self.calc_frame.partition_data = dict(data.get("partition") or {})
            self.calc_frame._toggle_partition()
            if self.calc_frame.use_partition_var.get() and self.calc_frame.partition_data:
                self.calc_frame.calculator.set_design_elevations(self.calc_frame.partition_data)
                self.calc_frame.partition_label.config(
                    text=f"已设置 {len(self.calc_frame.partition_data)} 个分区高程",
                    foreground=COLORS["ink"],
                )
            else:
                self.calc_frame.calculator.set_design_elevation(self.calc_frame.design_elevation_var.get())
            self.calc_frame.result = None
            self.calc_frame._refresh_plot()
        self.project_label_var.set(f"项目: {self.project_name}")

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
            initialfile=self.project_path or "",
        )
        if not filepath:
            return
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


def main():
    """主函数"""
    
    # 高 DPI 支持 (Windows)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    
    app = MainApplication()
    app.mainloop()


if __name__ == "__main__":
    main()
