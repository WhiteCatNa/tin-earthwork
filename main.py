"""
TIN 土方自动算量系统 - 主程序入口
"""
import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os
import matplotlib

matplotlib.use('TkAgg')

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.data_import_frame import DataImportFrame
from gui.boundary_frame import BoundaryFrame
from gui.calculation_frame import CalculationFrame
from core.calculator import SurveyPoint


class MainApplication(tk.Tk):
    """主应用程序窗口"""
    
    def __init__(self):
        super().__init__()
        
        self.title("TIN 土方自动算量系统 v1.0 - 上海上铁建筑工程（集团）有限公司")
        self.geometry("1400x900")
        self.minsize(1200, 800)
        
        # 设置样式
        self._setup_styles()
        
        # 数据状态
        self.points: list[SurveyPoint] = []
        self.boundary: list[tuple[float, float]] = []
        self._closing = False
        
        # 创建界面
        self._create_widgets()
        
        # 绑定关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        
    def _setup_styles(self):
        """配置 ttk 样式"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # 强调按钮样式
        style.configure('Accent.TButton', 
                       font=('', 10, 'bold'),
                       foreground='white',
                       background='#4472C4')
        style.map('Accent.TButton',
                 background=[('active', '#315A9B'), ('pressed', '#284880')])
        
        # 标签框样式
        style.configure('TLabelframe', font=('', 10, 'bold'))
        style.configure('TLabelframe.Label', font=('', 10, 'bold'))
        
    def _create_widgets(self):
        # 顶部工具栏
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(toolbar, text="工作流程:", font=('', 10, 'bold')).pack(side=tk.LEFT, padx=5)
        
        # 步骤指示器
        self.step_var = tk.StringVar(value="步骤 1/3: 数据导入")
        ttk.Label(toolbar, textvariable=self.step_var, font=('', 10), foreground='#4472C4').pack(side=tk.LEFT, padx=10)
        
        ttk.Separator(toolbar, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=10)
        
        # 进度指示
        self.progress_steps = []
        step_names = ["① 数据导入", "② 边界设置", "③ 计算结果"]
        for i, name in enumerate(step_names):
            lbl = ttk.Label(toolbar, text=name, font=('', 9), foreground='gray')
            lbl.pack(side=tk.LEFT, padx=5)
            self.progress_steps.append(lbl)
        self._update_step_indicator(0)
        
        ttk.Separator(toolbar, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=10)
        
        # 项目信息
        ttk.Label(toolbar, text="项目: 未加载", font=('', 9)).pack(side=tk.RIGHT, padx=10)
        
        # 主内容区 - 使用 Notebook 作为向导式界面
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 页面 1: 数据导入
        self.import_frame = DataImportFrame(self.notebook, self._on_points_loaded)
        self.notebook.add(self.import_frame, text="  ① 数据导入与检查  ")
        
        # 页面 2: 边界设置
        self.boundary_frame = BoundaryFrame(self.notebook, [], self._on_boundary_set)
        self.notebook.add(self.boundary_frame, text="  ② 计算边界设置  ")
        self.notebook.tab(1, state='disabled')  # 初始禁用
        
        # 页面 3: 计算结果
        self.calc_frame = None  # 延迟创建
        
        # 底部状态栏
        self.status_var = tk.StringVar(value="就绪 - 请导入测量数据文件")
        status_bar = ttk.Label(self, textvariable=self.status_var, relief='sunken', anchor=tk.W, padding=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=5)
        
    def _update_step_indicator(self, current_step: int):
        """更新步骤指示器颜色"""
        for i, lbl in enumerate(self.progress_steps):
            if i < current_step:
                lbl.config(foreground='green', font=('', 9, 'bold'))
            elif i == current_step:
                lbl.config(foreground='#4472C4', font=('', 9, 'bold'))
            else:
                lbl.config(foreground='gray', font=('', 9))
                
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
            # 边界变更：同步到计算页，清除旧结果
            self.calc_frame.boundary = boundary
            self.calc_frame.calculator.set_boundary(boundary)
            self.calc_frame.result = None
            self.calc_frame._refresh_plot()
            
        # 启用计算页面
        self.notebook.tab(2, state='normal')
        self.notebook.select(2)
        
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