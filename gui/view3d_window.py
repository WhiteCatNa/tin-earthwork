"""三维视图窗口：拖动旋转，可切换地面/设计面、调整高程夸张、另存图片。"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Tuple

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from core.calculator import CalculationResult
from gui.theme import COLORS
from utils.view3d import MAX_FACES, draw_3d, exaggeration_value, face_count
import utils.plotter  # noqa: F401  导入时配置中文字体


def cancel_idle_draw(canvas: FigureCanvasTkAgg):
    """取消画布尚未执行的 draw_idle 并销毁画布控件，避免关闭后 Tk 报 invalid command name。"""
    widget = canvas.get_tk_widget()
    idle = getattr(canvas, "_idle_draw_id", None)
    try:
        if idle:
            widget.after_cancel(idle)
            canvas._idle_draw_id = None
        widget.destroy()
    except tk.TclError:
        pass


class View3DWindow(tk.Toplevel):
    def __init__(self, parent, result: CalculationResult, natural: List[np.ndarray], design: List[np.ndarray],
                 origin: Tuple[float, float], project_name: str = ""):
        super().__init__(parent)
        self.result = result
        self.natural = natural
        self.design = design
        self.origin = origin
        self.title(f"三维视图 - {project_name}" if project_name else "三维视图")
        self.geometry("900x680")
        self.configure(bg=COLORS["bg"])
        self.transient(parent.winfo_toplevel())

        second = "后期面" if result.is_compare and not result.is_design_tin else "设计面"
        first = "前期地面" if result.is_compare and not result.is_design_tin else "原地面"
        self.show_natural_var = tk.BooleanVar(value=True)
        self.show_design_var = tk.BooleanVar(value=True)
        self.exaggeration_var = tk.StringVar(value=f"{self._suggested_exaggeration():g}")

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill=tk.X)
        ttk.Checkbutton(bar, text=first, variable=self.show_natural_var, command=self.redraw).pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text=second, variable=self.show_design_var, command=self.redraw).pack(
            side=tk.LEFT, padx=(10, 0))
        ttk.Label(bar, text="高程夸张").pack(side=tk.LEFT, padx=(18, 4))
        spin = ttk.Spinbox(bar, from_=0.1, to=100, increment=0.5, width=6, textvariable=self.exaggeration_var,
                           command=self.redraw)
        spin.pack(side=tk.LEFT)
        spin.bind("<Return>", lambda _e: self.redraw())
        spin.bind("<FocusOut>", lambda _e: self.redraw())
        ttk.Label(bar, text="倍").pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(bar, text="俯视", command=lambda: self._view(90, -90)).pack(side=tk.LEFT, padx=(18, 0))
        ttk.Button(bar, text="斜视", command=lambda: self._view(30, -60)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="保存图片…", command=self._save).pack(side=tk.RIGHT)
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", padding=(8, 0, 8, 6)).pack(
            side=tk.BOTTOM, anchor=tk.W)

        self.figure = Figure(figsize=(9, 6.5), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.ax = self.figure.add_axes([0.0, 0.02, 0.86, 0.96], projection="3d")   # 画布已建立，Axes3D 自动绑定鼠标旋转
        self.cax = self.figure.add_axes([0.88, 0.2, 0.02, 0.6])
        self.ax.view_init(30, -60)
        self.redraw()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _suggested_exaggeration(self) -> float:
        """高差相对场地尺寸很小时自动夸张，使起伏看得出来。"""
        faces = self.natural or self.design
        if not faces:
            return 1.0
        xyz = np.concatenate(faces)
        span = float(np.ptp(xyz[:, :2], axis=0).max())
        relief = float(np.ptp(xyz[:, 2]))
        if relief <= 0 or span <= 0:
            return 1.0
        ratio = 0.25 * span / relief
        for nice in (1, 2, 5, 10, 20, 50, 100):
            if ratio <= nice:
                return float(nice)
        return 100.0

    def _view(self, elev: float, azim: float):
        self.ax.view_init(elev, azim)
        self.canvas.draw_idle()

    def redraw(self):
        value = exaggeration_value(self.exaggeration_var.get())
        if value is None:
            self.status_var.set("高程夸张须为 0.1~100 的数字")
            return
        elev, azim = self.ax.elev, self.ax.azim
        mappable = draw_3d(self.ax, self.natural, self.design, self.result, value,
                           self.show_natural_var.get(), self.show_design_var.get(), self.origin)
        self.ax.view_init(elev, azim)
        self.cax.clear()
        self.cax.set_visible(mappable is not None)
        if mappable is not None:
            self.figure.colorbar(mappable, cax=self.cax, label="地面高程 (m)")
        count = face_count(self.natural, self.design)
        note = "，面数较多，旋转可能较慢" if count > MAX_FACES else ""
        self.status_var.set(f"鼠标左键拖动旋转，右键拖动缩放。高程放大 {value:g} 倍显示，刻度为真实高程{note}")
        self.canvas.draw_idle()

    def _save(self):
        path = filedialog.asksaveasfilename(parent=self, title="保存三维视图", defaultextension=".png",
                                            filetypes=[("PNG 图片", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")])
        if not path:
            return
        try:
            self.figure.savefig(path, dpi=200, bbox_inches="tight")
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=self)

    def destroy(self):
        cancel_idle_draw(self.canvas)
        self.figure.clear()
        super().destroy()
