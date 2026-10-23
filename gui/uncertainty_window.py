"""不确定度窗口：内插误差分布图 / 变化显著性图、推荐补测点、按精度目标规划补测、评定结果表，
可导出补测点（CSV / DXF）。"""
import csv
from types import SimpleNamespace
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import PolyCollection
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from core.calculator import CalculationResult, TINEarthworkCalculator
from core.survey_plan import SurveyPlan, plan_survey
from core.uncertainty import UncertaintyResult, extend_recommendations
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
    def __init__(self, parent, result: CalculationResult, project_name: str = "",
                 calculator: TINEarthworkCalculator = None):
        super().__init__(parent)
        self.result = result
        self.calculator = calculator
        self.uncertainty: UncertaintyResult = result.uncertainty
        self.project_name = project_name
        self.plan: SurveyPlan = None
        self.title(f"不确定度与补测点 - {project_name}" if project_name else "不确定度与补测点")
        self.geometry("1050x700")
        self.configure(bg=COLORS["bg"])
        self.transient(parent.winfo_toplevel())

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill=tk.X)
        self.summary_var = tk.StringVar(value=self.uncertainty.summary_text())
        ttk.Label(bar, textvariable=self.summary_var, wraplength=760).pack(side=tk.LEFT, fill=tk.X, expand=True)
        state = tk.NORMAL if self.uncertainty.recommendations else tk.DISABLED
        self.export_buttons = [ttk.Button(bar, text="补测点 DXF…", command=self._export_dxf, state=state),
                               ttk.Button(bar, text="补测点 CSV…", command=self._export_csv, state=state)]
        self.export_buttons[0].pack(side=tk.RIGHT)
        self.export_buttons[1].pack(side=tk.RIGHT, padx=6)

        options = ttk.Frame(self, padding=(8, 0, 8, 6))
        options.pack(fill=tk.X)
        self.view_var = tk.StringVar(value="error")
        ttk.Radiobutton(options, text="内插误差分布", value="error", variable=self.view_var,
                        command=self._draw).pack(side=tk.LEFT)
        significance = ttk.Radiobutton(options, text="变化显著性", value="significance", variable=self.view_var,
                                       command=self._draw)
        significance.pack(side=tk.LEFT, padx=(8, 0))
        if self.uncertainty.significance is None:
            significance.state(["disabled"])
        ttk.Separator(options, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)
        ttk.Label(options, text="精度目标：挖方、填方 95% 区间 ±").pack(side=tk.LEFT)
        self.target_var = tk.StringVar(value="2")
        ttk.Entry(options, textvariable=self.target_var, width=5).pack(side=tk.LEFT)
        ttk.Label(options, text="%").pack(side=tk.LEFT)
        self.plan_button = ttk.Button(options, text="规划补测", command=self.make_plan)
        self.plan_button.pack(side=tk.LEFT, padx=6)
        if calculator is None:
            self.plan_button.state(["disabled"])
        self.plan_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.plan_var, wraplength=1000, padding=(8, 0, 8, 6)).pack(fill=tk.X)

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
        if self.uncertainty.significance is not None:
            notes.append(self.uncertainty.significance.summary_text())
        ttk.Label(side, text="\n".join(f"· {item}" for item in notes), wraplength=380, style="Muted.TLabel",
                  padding=(0, 6)).pack(fill=tk.X)
        ttk.Label(side, text="推荐补测点（按收益从大到小，图中编号 R1、R2…）").pack(anchor=tk.W)
        self.point_tree = self._tree(side, POINT_HEADERS)
        self.point_tree.master.pack(fill=tk.BOTH, expand=True)
        self._fill_points()
        self._draw()

    @property
    def points_to_export(self):
        return self.plan.batch if self.plan is not None and self.plan.batch else self.uncertainty.recommendations

    def _fill_points(self):
        self.point_tree.delete(*self.point_tree.get_children())
        for k, item in enumerate(self.points_to_export, 1):
            self.point_tree.insert("", tk.END, values=(f"R{k}", f"{item.x:.3f}", f"{item.y:.3f}",
                                                       f"{item.reduction:.2f}", f"{item.remaining:.0%}"))
        for button in self.export_buttons:
            button.config(state=tk.NORMAL if self.points_to_export else tk.DISABLED)

    def make_plan(self):
        """按精度目标规划：推荐点补到 1000 个（不足时），估计还需补测多少点，列出第一批。"""
        try:
            target = float(self.target_var.get()) / 100
            self.config(cursor="watch")
            self.update_idletasks()
            extend_recommendations(self.calculator, self.result, self.uncertainty,
                                   min(1000, max(len(self.calculator.points), 50)))
            self.plan = plan_survey(self.uncertainty, len(self.calculator.points), target)
        except ValueError as error:
            messagebox.showerror("规划补测", str(error) or "精度目标必须是数字", parent=self)
            return
        finally:
            self.config(cursor="")
        text = self.plan.summary_text()
        if self.plan.warnings:
            text += "。" + "；".join(self.plan.warnings)
        self.plan_var.set(text)
        self._fill_points()
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
        self.figure.clear()
        self.ax = ax = self.figure.add_subplot(111)
        uncertainty = self.uncertainty
        if self.view_var.get() == "significance" and uncertainty.significance is not None:
            sig = uncertainty.significance
            ny, nx = sig.classes.shape
            x0, y0 = sig.origin
            extent = (x0, x0 + nx * sig.cell, y0, y0 + ny * sig.cell)
            cmap = ListedColormap([PLOT_COLORS["fill"], "#D9D9D9", PLOT_COLORS["cut"]])
            ax.imshow(sig.classes, origin="lower", extent=extent, cmap=cmap, vmin=-1.5, vmax=1.5,
                      interpolation="nearest")
            ax.legend(handles=[Patch(color=PLOT_COLORS["cut"], label=f"显著挖方 {sig.significant_cut:,.0f} m³"),
                               Patch(color=PLOT_COLORS["fill"], label=f"显著填方 {sig.significant_fill:,.0f} m³"),
                               Patch(color="#D9D9D9", label=f"不显著 {sig.uncertain_ratio:.0%}")],
                      loc="upper right", fontsize=8, framealpha=0.9)
            title = f"变化显著性（{sig.confidence:.0%} 检测限随地面起伏与点距变化）"
        else:
            if uncertainty.triangle_sigma:
                polygons = [vertices for vertices, _ in uncertainty.triangle_sigma]
                values = [value * 100 for _, value in uncertainty.triangle_sigma]   # cm
                collection = PolyCollection(polygons, array=values, cmap="YlOrRd", edgecolor="none")
                ax.add_collection(collection)
                self.figure.colorbar(collection, ax=ax, shrink=0.8, label="三角形平均内插误差尺度 (cm)")
            title = "内插误差分布与推荐补测点（★）"
        boundary = self.result.boundary_points
        if boundary:
            xs, ys = zip(*(list(boundary) + [boundary[0]]))
            ax.plot(xs, ys, color=PLOT_COLORS["boundary"], linewidth=1.2)
        points = self.points_to_export
        for k, item in enumerate(points, 1):
            ax.plot(item.x, item.y, marker="*", markersize=11, color=PLOT_COLORS["zero"],
                    markeredgecolor="white", markeredgewidth=0.6, linestyle="none")
            if k <= 50:
                ax.annotate(f"R{k}", (item.x, item.y), xytext=(4, 3), textcoords="offset points", fontsize=7)
        if self.plan is not None and self.plan.batch:
            title += f"；★ 为第一批补测 {len(self.plan.batch)} 点"
        ax.autoscale_view()
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_title(title, fontsize=10)
        ax.ticklabel_format(useOffset=False, style="plain")
        self.canvas.draw_idle()

    def _save(self, title, extension, label, writer):
        path = filedialog.asksaveasfilename(parent=self, title=title, defaultextension=extension,
                                            filetypes=[(label, f"*{extension}")],
                                            initialfile=f"{self.project_name}_补测点{extension}")
        if not path:
            return
        try:
            writer(SimpleNamespace(recommendations=self.points_to_export), path)
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
