"""不确定度窗口：内插误差分布图、推荐补测点、评定结果表，可导出补测点（CSV / DXF）。"""
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure

from core.calculator import CalculationResult
from core.uncertainty import UncertaintyResult
from gui.theme import COLORS, PLOT_COLORS
from gui.view3d_window import cancel_idle_draw
import utils.plotter  # noqa: F401  导入时配置中文字体

RESULT_HEADERS = ("项目", "TIN 法 (m³)", "修正后估计 (m³)", "95% 半宽 (m³)", "半宽占比")
POINT_HEADERS = ("序号", "X", "Y", "误差指标减少 (m³)", "剩余")


def export_recommendations_csv(uncertainty: UncertaintyResult, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["点号", "X", "Y"])
        for k, item in enumerate(uncertainty.recommendations, 1):
            writer.writerow([f"R{k}", f"{item.x:.3f}", f"{item.y:.3f}"])


def export_recommendations_dxf(uncertainty: UncertaintyResult, path: str) -> None:
    """补测点写成 POINT（图层 RECOMMEND）并标注点号，可直接导入全站仪/RTK 放样软件或 CAD。"""
    lines = ["0", "SECTION", "2", "ENTITIES"]
    for k, item in enumerate(uncertainty.recommendations, 1):
        lines += ["0", "POINT", "8", "RECOMMEND", "10", f"{item.x:.4f}", "20", f"{item.y:.4f}", "30", "0.0"]
        lines += ["0", "TEXT", "8", "RECOMMEND_ID", "10", f"{item.x:.4f}", "20", f"{item.y:.4f}", "30", "0.0",
                  "40", "1.0", "1", f"R{k}"]
    lines += ["0", "ENDSEC", "0", "EOF"]
    with open(path, "w", encoding="ascii") as handle:
        handle.write("\n".join(lines) + "\n")


class UncertaintyWindow(tk.Toplevel):
    def __init__(self, parent, result: CalculationResult, project_name: str = ""):
        super().__init__(parent)
        self.result = result
        self.uncertainty: UncertaintyResult = result.uncertainty
        self.project_name = project_name
        self.title(f"不确定度与补测点 - {project_name}" if project_name else "不确定度与补测点")
        self.geometry("1050x700")
        self.configure(bg=COLORS["bg"])
        self.transient(parent.winfo_toplevel())

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill=tk.X)
        self.summary_var = tk.StringVar(value=self.uncertainty.summary_text())
        ttk.Label(bar, textvariable=self.summary_var, wraplength=760).pack(side=tk.LEFT, fill=tk.X, expand=True)
        state = tk.NORMAL if self.uncertainty.recommendations else tk.DISABLED
        ttk.Button(bar, text="补测点 DXF…", command=self._export_dxf, state=state).pack(side=tk.RIGHT)
        ttk.Button(bar, text="补测点 CSV…", command=self._export_csv, state=state).pack(side=tk.RIGHT, padx=6)

        pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.figure = Figure(figsize=(6, 5), dpi=100, constrained_layout=True)
        self.ax = self.figure.add_subplot(111)
        plot = ttk.Frame(pane)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        pane.add(plot, weight=3)
        side = ttk.Frame(pane)
        pane.add(side, weight=2)
        self.result_tree = self._tree(side, RESULT_HEADERS, height=3)
        self.result_tree.master.pack(fill=tk.X)
        for label, tin, estimate, half, pct in self.uncertainty.rows():
            self.result_tree.insert("", tk.END, values=(label, f"{tin:,.2f}", f"{estimate:,.2f}", f"{half:,.2f}",
                                                        "—" if pct is None else f"{pct:.1f}%"))
        notes = list(self.uncertainty.warnings) or ["修正后估计 = TIN 法方量 − 抽稀外推估计的内插偏差"]
        ttk.Label(side, text="\n".join(f"· {item}" for item in notes), wraplength=380, style="Muted.TLabel",
                  padding=(0, 6)).pack(fill=tk.X)
        ttk.Label(side, text="推荐补测点（按收益从大到小，图中编号 R1、R2…）").pack(anchor=tk.W)
        self.point_tree = self._tree(side, POINT_HEADERS)
        self.point_tree.master.pack(fill=tk.BOTH, expand=True)
        for k, item in enumerate(self.uncertainty.recommendations, 1):
            self.point_tree.insert("", tk.END, values=(f"R{k}", f"{item.x:.3f}", f"{item.y:.3f}",
                                                       f"{item.reduction:.2f}", f"{item.remaining:.0%}"))
        self._draw()

    def _tree(self, parent, headers, height=10):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=list(range(len(headers))), show="headings", height=height)
        for index, text in enumerate(headers):
            tree.heading(index, text=text)
            tree.column(index, width=80, anchor=tk.CENTER)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _draw(self):
        ax = self.ax
        ax.clear()
        uncertainty = self.uncertainty
        if uncertainty.triangle_sigma:
            polygons = [vertices for vertices, _ in uncertainty.triangle_sigma]
            values = [value * 100 for _, value in uncertainty.triangle_sigma]   # cm
            collection = PolyCollection(polygons, array=values, cmap="YlOrRd", edgecolor="none")
            ax.add_collection(collection)
            self.figure.colorbar(collection, ax=ax, shrink=0.8, label="三角形平均内插误差尺度 (cm)")
        boundary = self.result.boundary_points
        if boundary:
            xs, ys = zip(*(list(boundary) + [boundary[0]]))
            ax.plot(xs, ys, color=PLOT_COLORS["boundary"], linewidth=1.2)
        for k, item in enumerate(uncertainty.recommendations, 1):
            ax.plot(item.x, item.y, marker="*", markersize=11, color=PLOT_COLORS["zero"],
                    markeredgecolor="white", markeredgewidth=0.6, linestyle="none")
            if k <= 50:
                ax.annotate(f"R{k}", (item.x, item.y), xytext=(4, 3), textcoords="offset points", fontsize=7)
        ax.autoscale_view()
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_title("内插误差分布与推荐补测点（★）", fontsize=10)
        ax.ticklabel_format(useOffset=False, style="plain")
        self.canvas.draw_idle()

    def _save(self, title, extension, label, writer):
        path = filedialog.asksaveasfilename(parent=self, title=title, defaultextension=extension,
                                            filetypes=[(label, f"*{extension}")],
                                            initialfile=f"{self.project_name}_补测点{extension}")
        if not path:
            return
        try:
            writer(self.uncertainty, path)
        except Exception as error:
            messagebox.showerror("导出失败", str(error), parent=self)
            return
        messagebox.showinfo("导出完成", f"已保存：{path}", parent=self)

    def _export_csv(self):
        self._save("导出补测点", ".csv", "CSV 文件", export_recommendations_csv)

    def _export_dxf(self):
        self._save("导出补测点", ".dxf", "DXF 文件", export_recommendations_dxf)

    def destroy(self):
        cancel_idle_draw(self.canvas)
        self.figure.clear()
        super().destroy()
