"""场地放坡窗口：设置平台与坡比，计算平台 + 边坡土方，显示坡顶线、坡脚线，求平台平衡高程，导出 DXF。"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional, Sequence, Tuple

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.figure import Figure

from core.calculator import SurveyPoint
from core.slope import Platform, balance_platforms, export_slope_dxf, run_slope, suggest_spacing
from gui.theme import COLORS, PLOT_COLORS
from gui.view3d_window import cancel_idle_draw
from utils.dxf_io import import_holes_from_dxf
import utils.plotter  # noqa: F401  导入时配置中文字体

XY = Tuple[float, float]


class SlopeWindow(tk.Toplevel):
    def __init__(self, parent, points: Sequence[SurveyPoint], boundary: Sequence[XY],
                 platforms: Optional[List[Platform]] = None, spacing: Optional[float] = None,
                 compact: float = 1.0, on_change: Optional[Callable[[List[Platform], float], None]] = None,
                 project_name: str = ""):
        super().__init__(parent)
        self.points = list(points)
        self.boundary = list(boundary)
        self.platforms: List[Platform] = list(platforms or [])
        self.compact = compact
        self.on_change = on_change
        self.result = None
        self._events: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._poll_id = None
        self.title(f"场地放坡 - {project_name}" if project_name else "场地放坡")
        self.geometry("1100x720")
        self.configure(bg=COLORS["bg"])
        self.transient(parent.winfo_toplevel())

        left = ttk.Frame(self, padding=10)
        left.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(left, text="设计平台", style="Title.TLabel").pack(anchor=tk.W)
        self.tree = ttk.Treeview(left, columns=("name", "h", "cut", "fill", "n"), show="headings", height=8)
        for key, text, width in (("name", "名称", 80), ("h", "高程 (m)", 80), ("cut", "挖坡 1:m", 70),
                                 ("fill", "填坡 1:n", 70), ("n", "顶点", 50)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=tk.CENTER)
        self.tree.pack(fill=tk.X)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._load_selected())
        row = ttk.Frame(left)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(row, text="导入 DXF 平台…", command=self._import_dxf).pack(side=tk.LEFT)
        ttk.Button(row, text="用计算边界", command=self._use_boundary).pack(side=tk.LEFT, padx=6)
        ttk.Button(row, text="删除", command=self._delete).pack(side=tk.LEFT)

        edit = ttk.LabelFrame(left, text="选中平台", padding=8)
        edit.pack(fill=tk.X, pady=(10, 0))
        self.vars = {key: tk.StringVar() for key in ("name", "elevation", "cut_ratio", "fill_ratio")}
        for r, (key, label) in enumerate((("name", "名称"), ("elevation", "平台高程 (m)"),
                                          ("cut_ratio", "挖方坡比 1 :"), ("fill_ratio", "填方坡比 1 :"))):
            ttk.Label(edit, text=label).grid(row=r, column=0, sticky=tk.W, pady=2)
            ttk.Entry(edit, textvariable=self.vars[key], width=12).grid(row=r, column=1, sticky=tk.W, padx=6)
        ttk.Button(edit, text="应用", command=self._apply_edit).grid(row=4, column=1, sticky=tk.W, pady=(4, 0))

        settings = ttk.Frame(left)
        settings.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(settings, text="取样间距 (m)").pack(side=tk.LEFT)
        self.spacing_var = tk.StringVar(value=f"{spacing or suggest_spacing(self.points):g}")
        ttk.Entry(settings, textvariable=self.spacing_var, width=8).pack(side=tk.LEFT, padx=6)
        ttk.Label(left, style="Muted.TLabel", wraplength=330, justify=tk.LEFT,
                  text="平台外按最近平台放坡，直到与原地面相交。取样间距越小，转角处锥面越精确、计算越慢。"
                  ).pack(anchor=tk.W, pady=(4, 0))

        actions = ttk.Frame(left)
        actions.pack(fill=tk.X, pady=(10, 0))
        self.run_button = ttk.Button(actions, text="计算放坡土方", style="Accent.TButton", command=self.compute)
        self.run_button.pack(side=tk.LEFT)
        self.balance_button = ttk.Button(actions, text=f"求平衡高程（Ks'={compact:g}）", command=self.balance)
        self.balance_button.pack(side=tk.LEFT, padx=6)
        self.export_button = ttk.Button(left, text="导出 DXF（平台、坡顶线、坡脚线）…", command=self._export,
                                        state=tk.DISABLED)
        self.export_button.pack(anchor=tk.W, pady=(8, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.status_var, wraplength=330, justify=tk.LEFT).pack(anchor=tk.W, pady=(10, 0))

        self.figure = Figure(figsize=(7, 6), dpi=100, constrained_layout=True)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 8), pady=8)
        self._refresh_tree()
        self._draw()

    # ---------------------------------------------------------------- 平台列表
    def _refresh_tree(self, select: Optional[int] = None):
        self.tree.delete(*self.tree.get_children())
        for index, p in enumerate(self.platforms):
            self.tree.insert("", tk.END, iid=str(index),
                             values=(p.name, f"{p.elevation:.3f}", f"{p.cut_ratio:g}", f"{p.fill_ratio:g}", len(p.ring)))
        if select is not None and 0 <= select < len(self.platforms):
            self.tree.selection_set(str(select))
            self._load_selected()

    def _selected(self) -> Optional[int]:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _load_selected(self):
        index = self._selected()
        if index is None:
            return
        p = self.platforms[index]
        for key in self.vars:
            value = getattr(p, key)
            self.vars[key].set(value if key == "name" else f"{value:g}")

    def _default_elevation(self) -> float:
        zs = sorted(p.z for p in self.points)
        return round(zs[len(zs) // 2], 2) if zs else 0.0

    def add_platform(self, ring: Sequence[XY], name: Optional[str] = None):
        platform = Platform(name or f"平台{len(self.platforms) + 1}", list(ring), self._default_elevation())
        self.platforms.append(platform)
        self._changed()
        self._refresh_tree(select=len(self.platforms) - 1)

    def _import_dxf(self):
        path = filedialog.askopenfilename(parent=self, title="导入平台（闭合多段线）",
                                          filetypes=[("DXF 文件", "*.dxf")])
        if not path:
            return
        try:
            rings = import_holes_from_dxf(path)
        except Exception as error:
            messagebox.showerror("导入失败", str(error), parent=self)
            return
        for ring in rings:
            self.add_platform(ring)

    def _use_boundary(self):
        if len(self.boundary) < 3:
            messagebox.showinfo("提示", "还没有计算边界", parent=self)
            return
        self.add_platform(self.boundary)

    def _delete(self):
        index = self._selected()
        if index is None:
            return
        del self.platforms[index]
        self._changed()
        self._refresh_tree()

    def _apply_edit(self) -> bool:
        index = self._selected()
        if index is None:
            messagebox.showinfo("提示", "请先在列表中选中平台", parent=self)
            return False
        try:
            values = {key: float(self.vars[key].get()) for key in ("elevation", "cut_ratio", "fill_ratio")}
        except ValueError:
            messagebox.showerror("输入错误", "高程和坡比必须是数字", parent=self)
            return False
        candidate = Platform(self.vars["name"].get().strip() or f"平台{index + 1}", self.platforms[index].ring,
                             **values)
        try:
            candidate.validate()
        except ValueError as error:
            messagebox.showerror("输入错误", str(error), parent=self)
            return False
        self.platforms[index] = candidate
        self._changed()
        self._refresh_tree(select=index)
        return True

    def _changed(self):
        self.result = None
        self.export_button.config(state=tk.DISABLED)
        if self.on_change is not None:
            self.on_change(list(self.platforms), self._spacing_or_none())
        self._draw()

    def _spacing_or_none(self) -> Optional[float]:
        try:
            value = float(self.spacing_var.get())
        except ValueError:
            return None
        return value if value > 0 else None

    # ---------------------------------------------------------------- 计算
    def _start(self, label: str, work: Callable, done: Callable):
        if self._thread is not None:
            return
        spacing = self._spacing_or_none()
        if spacing is None:
            messagebox.showerror("输入错误", "取样间距必须是大于 0 的数字", parent=self)
            return
        if self.on_change is not None:
            self.on_change(list(self.platforms), spacing)
        self.status_var.set(label)
        self.run_button.config(state=tk.DISABLED)
        self.balance_button.config(state=tk.DISABLED)
        platforms = list(self.platforms)

        def worker():
            try:
                self._events.put(("done", work(platforms, spacing)))
            except Exception as error:
                self._events.put(("error", error))

        self._done = done
        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        self._poll()

    def _poll(self):
        try:
            kind, payload = self._events.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(50, self._poll)
            return
        self._poll_id = None
        self._thread = None
        self.run_button.config(state=tk.NORMAL)
        self.balance_button.config(state=tk.NORMAL)
        if kind == "error":
            self.status_var.set("计算失败")
            messagebox.showerror("场地放坡", str(payload), parent=self)
            return
        self._done(payload)

    def compute(self):
        self._start("正在计算放坡土方…", lambda platforms, spacing: run_slope(self.points, platforms, spacing),
                    self._show_result)

    def balance(self):
        def done(payload):
            delta, result = payload
            for platform in self.platforms:
                platform.elevation = round(platform.elevation + delta, 3)
            self._changed()
            self._refresh_tree(select=0 if self.platforms else None)
            self._show_result(result, note=f"各平台已升降 {delta:+.3f} m，放坡后挖填平衡。\n")

        self._start("正在求平衡高程（需要反复计算，请稍候）…",
                    lambda platforms, spacing: balance_platforms(self.points, platforms, self.compact, spacing), done)

    def _show_result(self, result, note: str = ""):
        self.result = result
        lines = [f"{label}：挖方 {cut:.2f} m³，填方 {fill:.2f} m³" for label, cut, fill in result.rows()]
        text = note + "\n".join(lines)
        if result.warnings:
            text += "\n\n" + "\n".join(f"· {w}" for w in result.warnings)
        self.status_var.set(text)
        self.export_button.config(state=tk.NORMAL)
        self._draw()

    def _draw(self):
        ax = self.ax
        ax.clear()
        if self.points:
            ax.scatter([p.x for p in self.points], [p.y for p in self.points], s=2, color=PLOT_COLORS["tin"])
        if self.result is not None:
            for polygons, key in ((self.result.cut_polygons, "cut"), (self.result.fill_polygons, "fill")):
                if polygons:
                    ax.add_collection(PolyCollection(polygons, facecolor=PLOT_COLORS[key], alpha=0.45,
                                                     edgecolor="none"))
            for segments, color, label in ((self.result.cut_line, PLOT_COLORS["cut"], "坡顶线"),
                                           (self.result.fill_line, PLOT_COLORS["fill"], "坡脚线")):
                if segments:
                    ax.add_collection(LineCollection(segments, colors=color, linewidths=1.6, label=label))
        for p in self.platforms:
            ring = list(p.ring) + [p.ring[0]]
            ax.plot(*zip(*ring), color=PLOT_COLORS["boundary"], linewidth=1.8)
            cx = sum(x for x, _ in p.ring) / len(p.ring)
            cy = sum(y for _, y in p.ring) / len(p.ring)
            ax.text(cx, cy, f"{p.name}\n{p.elevation:.2f}", ha="center", va="center", fontsize=8)
        if self.result is not None and (self.result.cut_line or self.result.fill_line):
            ax.legend(loc="upper right", fontsize=8)
        ax.set_aspect("equal", adjustable="datalim")
        ax.autoscale_view()
        ax.ticklabel_format(useOffset=False, style="plain")
        ax.set_title("平台、放坡范围（红挖蓝填）与开口线", fontsize=10)
        self.canvas.draw_idle()

    def _export(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(parent=self, title="导出放坡 DXF", defaultextension=".dxf",
                                            filetypes=[("DXF 文件", "*.dxf")])
        if not path:
            return
        try:
            export_slope_dxf(self.result, path)
        except Exception as error:
            messagebox.showerror("导出失败", str(error), parent=self)
            return
        messagebox.showinfo("导出完成", f"已保存：{os.path.basename(path)}", parent=self)

    def destroy(self):
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        cancel_idle_draw(self.canvas)
        self.figure.clear()
        super().destroy()
