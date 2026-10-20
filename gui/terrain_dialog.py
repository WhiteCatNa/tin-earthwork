"""地形图 DXF、LAS 点云导入前的选项对话框。"""
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional, Sequence, Tuple

from gui.theme import COLORS
from utils.terrain_import import (THIN_CENTER, THIN_LOWEST, LasCloud, TerrainSource, default_selection,
                                  suggest_cell)


class _OptionsDialog(tk.Toplevel):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=COLORS["bg"])
        self.transient(parent.winfo_toplevel())
        self.result = None
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill=tk.BOTH, expand=True)

    def _buttons(self):
        row = ttk.Frame(self, padding=(12, 0, 12, 12))
        row.pack(fill=tk.X)
        ttk.Button(row, text="取消", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(row, text="导入", style="Accent.TButton", command=self.ok).pack(side=tk.RIGHT, padx=6)
        self.bind("<Return>", lambda _e: self.ok())
        self.bind("<Escape>", lambda _e: self.destroy())

    def show(self):
        self.grab_set()
        self.wait_window()
        return self.result


class DxfTerrainDialog(_OptionsDialog):
    """按图层选择高程点、高程注记、等高线，设定等高线取点间距。"""

    def __init__(self, parent, sources: Sequence[TerrainSource], spacing: float = 5.0):
        super().__init__(parent, "从地形图取点")
        self.sources = list(sources)
        ttk.Label(self.body, text="选择参与构网的数据（按图层分组）：").pack(anchor=tk.W)
        self.vars: List[tk.BooleanVar] = []
        for source, selected in zip(self.sources, default_selection(self.sources)):
            var = tk.BooleanVar(value=selected)
            self.vars.append(var)
            ttk.Checkbutton(self.body, variable=var,
                            text=f"[{source.kind}] 图层 {source.layer}：{source.description}").pack(anchor=tk.W)
        row = ttk.Frame(self.body)
        row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(row, text="等高线取点间距 (m)").pack(side=tk.LEFT)
        self.spacing_var = tk.StringVar(value=f"{spacing:g}")
        ttk.Entry(row, textvariable=self.spacing_var, width=8).pack(side=tk.LEFT, padx=6)
        ttk.Label(self.body, style="Muted.TLabel", wraplength=420, justify=tk.LEFT,
                  text="高程注记常与高程点重复，有高程点时默认不选。等高线顶点全部保留，长于间距的线段加密插点。"
                  ).pack(anchor=tk.W, pady=(8, 0))
        self._buttons()

    def ok(self):
        selected = [var.get() for var in self.vars]
        if not any(selected):
            messagebox.showwarning("提示", "请至少选择一组数据", parent=self)
            return
        try:
            spacing = float(self.spacing_var.get())
            if not spacing > 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "取点间距必须是大于 0 的数字", parent=self)
            return
        self.result = (selected, spacing)
        self.destroy()


class LasDialog(_OptionsDialog):
    """点云抽稀：地面点筛选、格网边长、每格取点方式。"""

    def __init__(self, parent, cloud: LasCloud):
        super().__init__(parent, "点云抽稀")
        self.cloud = cloud
        ground = int((cloud.classification == 2).sum())
        info = f"LAS {cloud.version}，点格式 {cloud.point_format}，共 {cloud.count:,} 个点"
        if ground:
            info += f"，其中地面点（分类 2）{ground:,} 个"
        ttk.Label(self.body, text=info).pack(anchor=tk.W)
        self.ground_var = tk.BooleanVar(value=bool(ground))
        check = ttk.Checkbutton(self.body, text="只用地面点（分类 2）", variable=self.ground_var)
        check.pack(anchor=tk.W, pady=(8, 0))
        if not ground:
            check.state(["disabled"])
        row = ttk.Frame(self.body)
        row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(row, text="抽稀格网边长 (m)").pack(side=tk.LEFT)
        self.cell_var = tk.StringVar(value=f"{suggest_cell(cloud.xyz):g}")
        ttk.Entry(row, textvariable=self.cell_var, width=8).pack(side=tk.LEFT, padx=6)
        self.method_var = tk.StringVar(value=THIN_LOWEST)
        for value, text in ((THIN_LOWEST, "每格取最低点（去掉植被、建筑等高出地面的点）"),
                            (THIN_CENTER, "每格取最接近格网中心的点")):
            ttk.Radiobutton(self.body, text=text, value=value, variable=self.method_var).pack(anchor=tk.W)
        self._buttons()

    def ok(self):
        try:
            cell = float(self.cell_var.get())
            if not cell > 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "格网边长必须是大于 0 的数字", parent=self)
            return
        self.result = (cell, self.method_var.get(), bool(self.ground_var.get()))
        self.destroy()


def ask_dxf_options(parent, sources) -> Optional[Tuple[List[bool], float]]:
    return DxfTerrainDialog(parent, sources).show()


def ask_las_options(parent, cloud) -> Optional[Tuple[float, str, bool]]:
    return LasDialog(parent, cloud).show()
