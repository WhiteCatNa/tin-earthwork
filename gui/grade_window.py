"""设计面费用优化窗口：输入单价和坡度范围，后台求总费用最小的斜面，与挖填平衡水平面、最小二乘斜面对照，
可把选中的方案填回计算设置。"""
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from core.balance import SoilFactors
from core.calculator import TINEarthworkCalculator
from core.grade_opt import HEADERS, MAX_EVALUATIONS, CostRates, GradeResult, optimize_grade, summary_text
from core.haul import suggest_zone_size
from core.surface import PlaneDesign
from gui.theme import COLORS

RATE_FIELDS = [("cut", "挖方 元/m³"), ("fill", "填方 元/m³"), ("haul", "运输 元/(m³·km)"),
               ("waste", "余土外运 元/m³"), ("borrow", "借土 元/m³")]


class GradeWindow(tk.Toplevel):
    def __init__(self, parent, calculator: TINEarthworkCalculator, factors: SoilFactors,
                 on_apply: Callable[[PlaneDesign], None], project_name: str = ""):
        super().__init__(parent)
        self.calculator = calculator
        self.factors = factors
        self.on_apply = on_apply
        self.result: GradeResult = None
        self._thread = None
        self._events = queue.Queue()
        self._poll_id = None
        self.title(f"设计面费用优化 - {project_name}" if project_name else "设计面费用优化")
        self.geometry("980x380")
        self.configure(bg=COLORS["bg"])
        self.transient(parent.winfo_toplevel())

        form = ttk.Frame(self, padding=(8, 8))
        form.pack(fill=tk.X)
        defaults = CostRates().to_dict()
        self.rate_vars = {}
        for column, (key, label) in enumerate(RATE_FIELDS):
            ttk.Label(form, text=label).grid(row=0, column=column * 2, sticky="w")
            self.rate_vars[key] = tk.StringVar(value=f"{defaults[key]:g}")
            ttk.Entry(form, textvariable=self.rate_vars[key], width=7).grid(row=0, column=column * 2 + 1,
                                                                             padx=(4, 10), pady=2)
        self.range_vars = {}
        for column, (key, label, value) in enumerate((("x_low", "X 坡度 % 从", "-2"), ("x_high", "到", "2"),
                                                     ("y_low", "Y 坡度 % 从", "-2"), ("y_high", "到", "2"))):
            ttk.Label(form, text=label).grid(row=1, column=column * 2, sticky="w")
            self.range_vars[key] = tk.StringVar(value=value)
            ttk.Entry(form, textvariable=self.range_vars[key], width=7).grid(row=1, column=column * 2 + 1,
                                                                              padx=(4, 10), pady=2)
        ttk.Label(form, text="调配区边长 m").grid(row=1, column=8, sticky="w")
        self.zone_var = tk.StringVar(value=f"{suggest_zone_size(calculator):g}")
        ttk.Entry(form, textvariable=self.zone_var, width=7).grid(row=1, column=9, padx=(4, 10), pady=2)

        bar = ttk.Frame(self, padding=(8, 0, 8, 6))
        bar.pack(fill=tk.X)
        self.compute_button = ttk.Button(bar, text="求费用最小斜面", style="Accent.TButton", command=self.compute)
        self.compute_button.pack(side=tk.LEFT)
        self.apply_button = ttk.Button(bar, text="采用选中方案", command=self.apply, state=tk.DISABLED)
        self.apply_button.pack(side=tk.LEFT, padx=6)
        self.summary_var = tk.StringVar(
            value="费用 = 挖、填单价 × 方量 + 运输单价 × 场内调配运输量 + 余土外运 + 借土；"
                  "设计面的高低和坡向同时影响方量、运距和余缺")
        ttk.Label(self, textvariable=self.summary_var, wraplength=940, padding=(8, 0, 8, 6)).pack(fill=tk.X)

        frame = ttk.Frame(self, padding=(8, 0, 8, 8))
        frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(frame, columns=list(range(len(HEADERS))), show="headings", height=4,
                                 selectmode="browse")
        for index, text in enumerate(HEADERS):
            self.tree.heading(index, text=text)
            self.tree.column(index, width=170 if index == 0 else 84, anchor=tk.W if index == 0 else tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.apply())

    def _inputs(self):
        rates = CostRates(**{key: float(var.get()) for key, var in self.rate_vars.items()})
        r = {key: float(var.get()) for key, var in self.range_vars.items()}
        zone = float(self.zone_var.get())
        if zone <= 0:
            raise ValueError("调配区边长必须大于 0")
        return rates, (r["x_low"], r["x_high"]), (r["y_low"], r["y_high"]), zone

    def compute(self):
        if self._thread is not None:
            return
        try:
            rates, slope_x, slope_y, zone = self._inputs()
        except ValueError as error:
            messagebox.showerror("设计面费用优化", str(error) if "could not" not in str(error) else "请输入数字",
                                 parent=self)
            return
        self.compute_button.config(state=tk.DISABLED)
        self.apply_button.config(state=tk.DISABLED)
        self.summary_var.set("正在搜索（每试一个设计面都要重算方量和调配，约需十几秒到一分钟）…")
        calculator, factors, events = self.calculator, self.factors, self._events
        total = MAX_EVALUATIONS + 3

        def worker():
            try:
                events.put(("done", optimize_grade(
                    calculator, rates, slope_x, slope_y, zone, factors,
                    progress=lambda k: events.put(("progress", min(k, total) * 100 // total)))))
            except Exception as error:      # noqa: BLE001  交给主线程提示
                events.put(("error", error))

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        self._poll()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _poll(self):
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                self._poll_id = self.after(50, self._poll)
                return
            if kind == "progress":
                self.summary_var.set(f"正在搜索费用最小的设计面… {payload}%")
                continue
            break
        self._poll_id = None
        self._thread = None
        self.compute_button.config(state=tk.NORMAL)
        if kind == "error":
            self.summary_var.set("优化未完成")
            messagebox.showerror("设计面费用优化", str(payload) or "优化失败", parent=self)
            return
        self.result = payload
        self.summary_var.set(summary_text(payload) + f"。共试算 {payload.evaluations} 个设计面")
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(payload.rows()):
            self.tree.insert("", tk.END, iid=str(index), values=row)
        self.tree.selection_set("0")
        self.apply_button.config(state=tk.NORMAL)

    def apply(self):
        if self.result is None or not self.tree.selection():
            return
        options = [self.result.best] + self.result.baselines
        self.on_apply(options[int(self.tree.selection()[0])].plane)
        self.summary_var.set(self.summary_var.get().split("。已填入")[0] + "。已填入计算设置（斜面），可点“开始计算”")

    def destroy(self):
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        super().destroy()
