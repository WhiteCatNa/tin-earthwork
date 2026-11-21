"""土方调配窗口：设定调配区边长，求最小运输量的调配方案，图表显示并导出 Excel。"""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from core.calculator import TINEarthworkCalculator
from core.haul import (MOVE_HEADERS, ZONE_HEADERS, HaulResult, export_haul_excel, haul_rows, moves_between_zones,
                       run_haul, suggest_zone_size, summary_text)
from gui.theme import COLORS, PLOT_COLORS
from gui.view3d_window import cancel_idle_draw
import utils.plotter  # noqa: F401  导入时配置中文字体


class HaulWindow(tk.Toplevel):
    def __init__(self, parent, calculator: TINEarthworkCalculator, compact: float = 1.0, project_name: str = ""):
        super().__init__(parent)
        self.calculator = calculator
        self.compact = compact
        self.project_name = project_name
        self.result: HaulResult = None
        self._thread = None
        self._events = queue.Queue()
        self._poll_id = None
        self.title(f"土方调配 - {project_name}" if project_name else "土方调配")
        self.geometry("1000x700")
        self.configure(bg=COLORS["bg"])
        self.transient(parent.winfo_toplevel())

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="调配区边长 (m)").pack(side=tk.LEFT)
        self.size_var = tk.StringVar(value=f"{suggest_zone_size(calculator):g}")
        entry = ttk.Entry(bar, textvariable=self.size_var, width=8)
        entry.pack(side=tk.LEFT, padx=4)
        entry.bind("<Return>", lambda _e: self.compute())
        ttk.Label(bar, text=f"Ks' = {compact:g}（在计算设置中修改）", style="Muted.TLabel").pack(side=tk.LEFT, padx=8)
        self.compute_button = ttk.Button(bar, text="计算调配方案", style="Accent.TButton", command=self.compute)
        self.compute_button.pack(side=tk.LEFT, padx=6)
        self.export_button = ttk.Button(bar, text="导出 Excel…", command=self._export, state=tk.DISABLED)
        self.export_button.pack(side=tk.RIGHT)
        self.summary_var = tk.StringVar(value="按方格划分调配区，区内先平衡，余缺部分按运输量（方量×运距）最小调配")
        ttk.Label(self, textvariable=self.summary_var, wraplength=960, padding=(8, 0, 8, 6)).pack(fill=tk.X)

        pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.figure = Figure(figsize=(6, 5), dpi=100, constrained_layout=True)
        self.ax = self.figure.add_subplot(111)
        plot = ttk.Frame(pane)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        pane.add(plot, weight=3)
        tabs = ttk.Notebook(pane)
        pane.add(tabs, weight=2)
        self.move_tree = self._tree(tabs, MOVE_HEADERS, "调配方案")
        self.zone_tree = self._tree(tabs, ZONE_HEADERS, "调配区")
        self.compute()

    def _tree(self, tabs, headers, title):
        frame = ttk.Frame(tabs)
        tabs.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=list(range(len(headers))), show="headings")
        for index, text in enumerate(headers):
            tree.heading(index, text=text)
            tree.column(index, width=90, anchor=tk.CENTER)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def compute(self):
        """在后台求调配方案（测点多时线性规划和切分要几秒），完成后刷新表格和图。"""
        if self._thread is not None:
            return
        try:
            size = float(self.size_var.get())
        except ValueError:
            messagebox.showerror("土方调配", "调配区边长必须是数字", parent=self)
            return
        self.summary_var.set("正在计算调配方案…")
        self.compute_button.config(state=tk.DISABLED)
        calculator, compact = self.calculator, self.compact

        def worker():
            try:
                self._events.put(("done", run_haul(calculator, size, compact)))
            except Exception as error:      # noqa: BLE001  交给主线程提示
                self._events.put(("error", error))

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        self._poll()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _poll(self):
        try:
            kind, payload = self._events.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(50, self._poll)
            return
        self._poll_id = None
        self._thread = None
        self.compute_button.config(state=tk.NORMAL)
        if kind == "error":
            self.summary_var.set(summary_text(self.result) + "（均为自然方）" if self.result else "调配方案未算出")
            messagebox.showerror("土方调配", str(payload) or "调配失败", parent=self)
            return
        self.result = payload
        self.summary_var.set(summary_text(self.result) + "（均为自然方）")
        zones, moves = haul_rows(self.result)
        for tree, rows in ((self.move_tree, moves), (self.zone_tree, zones)):
            tree.delete(*tree.get_children())
            for row in rows:
                tree.insert("", tk.END, values=row)
        self.export_button.config(state=tk.NORMAL)
        self._draw()

    def _draw(self):
        ax = self.ax
        ax.clear()
        result = self.result
        for zone in result.zones:
            x0, y0, x1, y1 = zone.bounds
            net = zone.cut - zone.fill / result.compact
            color = PLOT_COLORS["cut"] if net > 0 else PLOT_COLORS["fill"]
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=color, alpha=0.25,
                                   edgecolor=PLOT_COLORS["tin"], linewidth=0.6))
            ax.text((x0 + x1) / 2, (y0 + y1) / 2, f"{zone.name}\n{net:+.0f}", ha="center", va="center", fontsize=7)
        arrows = moves_between_zones(result)
        largest = max((volume for _, _, volume in arrows), default=1.0)
        for start, end, volume in arrows:
            ax.annotate("", xy=end, xytext=start,
                        arrowprops=dict(arrowstyle="-|>", color=PLOT_COLORS["zero"],
                                        linewidth=0.8 + 3.2 * volume / largest, alpha=0.85))
        ax.autoscale_view()
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_title("调配区净方量（+ 余、− 缺，自然方 m³）与调运方向", fontsize=10)
        ax.ticklabel_format(useOffset=False, style="plain")
        self.canvas.draw_idle()

    def _export(self):
        path = filedialog.asksaveasfilename(parent=self, title="导出土方调配", defaultextension=".xlsx",
                                            filetypes=[("Excel 文件", "*.xlsx")],
                                            initialfile=f"{self.project_name}_土方调配.xlsx")
        if not path:
            return
        try:
            export_haul_excel(self.result, path, self.project_name)
        except Exception as error:
            messagebox.showerror("导出失败", str(error), parent=self)
            return
        messagebox.showinfo("导出完成", f"已保存：{path}", parent=self)

    def destroy(self):
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        cancel_idle_draw(self.canvas)
        self.figure.clear()
        super().destroy()
