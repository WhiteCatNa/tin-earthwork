"""
主窗口和数据导入页面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
from typing import List, Optional, Callable
from core.calculator import SurveyPoint
from gui.theme import COLORS
from utils.data_handler import ISSUE_NAMES, DataImporter, DataValidator
from utils.survey_edit import (
    add_survey_point as append_survey_point,
    delete_survey_point_at as remove_survey_point_at,
    offset_survey_data,
    swap_survey_xy,
    update_survey_point as mutate_survey_point,
)

PREVIEW_LIMIT = 2000
# 列映射的目标字段及显示名称
MAPPING_TARGETS = [
    ("id", "点号（可不选，自动编号）"),
    ("x", "X 坐标"),
    ("y", "Y 坐标"),
    ("z", "实测高程"),
    ("design_z", "设计高程（可不选）"),
]
NOT_USED = "（不使用）"


class DataImportFrame(ttk.Frame):
    """数据导入与检查页面"""
    
    def __init__(self, parent, on_points_loaded: Callable[[List[SurveyPoint]], None],
                 on_points_mutated: Optional[Callable] = None):
        super().__init__(parent)
        self.on_points_loaded = on_points_loaded
        self.on_points_mutated = on_points_mutated
        self.points: List[SurveyPoint] = []
        self.raw_df: Optional[pd.DataFrame] = None
        self.column_mapping = {}
        self._edit_entry = None
        self._mapping_editor = None
        
        self._create_widgets()
        
    def _create_widgets(self):
        # 左侧：文件操作和列映射
        left_frame = ttk.LabelFrame(self, text="数据导入", padding=12)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 6), pady=10)
        
        # 文件选择
        self.file_frame = ttk.Frame(left_frame)
        self.file_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(self.file_frame, text="数据文件:").pack(side=tk.LEFT)
        self.file_var = tk.StringVar()
        ttk.Entry(self.file_frame, textvariable=self.file_var, width=40).pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        ttk.Button(self.file_frame, text="浏览...", command=self._browse_file).pack(side=tk.LEFT)
        self.import_btn = ttk.Button(self.file_frame, text="导入", command=self._import_file, style="Accent.TButton")
        self.import_btn.pack(side=tk.LEFT, padx=(8, 0))
        # 进度条占位（不创建，仅在导入时动态添加）
        self.progress_bar: Optional[ttk.Progressbar] = None
        
        # 列映射只有 5 行，不参与伸缩；多出的高度留给下面的测点表。
        # 可收起为一行摘要：小屏上把高度让给测点表，识别不全时自动展开
        map_frame = ttk.LabelFrame(left_frame, text="列映射（自动识别；双击“源列名”可改选）", padding=8)
        map_frame.pack(fill=tk.X, pady=(0, 8))
        map_header = ttk.Frame(map_frame)
        map_header.pack(fill=tk.X)
        self.mapping_toggle = ttk.Button(map_header, text="收起", width=6, command=self._toggle_mapping)
        self.mapping_toggle.pack(side=tk.RIGHT)
        self.mapping_summary_var = tk.StringVar(value="尚未导入数据")
        ttk.Label(map_header, textvariable=self.mapping_summary_var, style="Muted.TLabel").pack(side=tk.LEFT)

        self.mapping_body = ttk.Frame(map_frame)
        # 映射编辑按钮放在表格右侧，省出一行高度
        map_btn_frame = ttk.Frame(self.mapping_body)
        map_btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        ttk.Button(map_btn_frame, text="应用映射", command=self._apply_mapping).pack(fill=tk.X)
        ttk.Button(map_btn_frame, text="重置自动识别", command=self._auto_detect_mapping).pack(fill=tk.X, pady=(8, 0))

        self.tree_map = ttk.Treeview(self.mapping_body, columns=('target', 'source'), show='headings', height=5)
        self.tree_map.heading('target', text='目标字段')
        self.tree_map.heading('source', text='源列名')
        self.tree_map.column('target', width=170)
        self.tree_map.column('source', width=150)
        self.tree_map.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.tree_map.bind("<Double-1>", self._on_mapping_double_click)
        self.set_mapping_expanded(self.winfo_screenheight() >= 900)

        # 数据预览（可编辑）
        preview_frame = ttk.LabelFrame(left_frame, text="测点表（双击单元格修改）", padding=8)
        preview_frame.pack(fill=tk.BOTH, expand=True)
        
        self.tree_preview = ttk.Treeview(preview_frame, show='headings', height=10)
        vsb = ttk.Scrollbar(preview_frame, orient="vertical", command=self.tree_preview.yview)
        hsb = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.tree_preview.xview)
        self.tree_preview.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree_preview.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)
        self.tree_preview.bind("<Double-1>", self._on_preview_double_click)

        edit_bar = ttk.Frame(preview_frame)
        edit_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(edit_bar, text="添加测点", command=self._add_point_dialog).pack(side=tk.LEFT)
        ttk.Button(edit_bar, text="删除选中", command=self._delete_selected).pack(side=tk.LEFT, padx=6)
        ttk.Label(edit_bar, text="定位点号").pack(side=tk.LEFT, padx=(12, 4))
        self.find_id_var = tk.StringVar()
        ttk.Entry(edit_bar, textvariable=self.find_id_var, width=10).pack(side=tk.LEFT)
        ttk.Button(edit_bar, text="定位", command=self._focus_point_id).pack(side=tk.LEFT, padx=4)

        offset_bar = ttk.Frame(preview_frame)
        offset_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(offset_bar, text="坐标平移").pack(side=tk.LEFT)
        self.dx_var = tk.StringVar(value="0")
        self.dy_var = tk.StringVar(value="0")
        self.dz_var = tk.StringVar(value="0")
        ttk.Label(offset_bar, text="ΔX").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(offset_bar, textvariable=self.dx_var, width=8).pack(side=tk.LEFT)
        ttk.Label(offset_bar, text="ΔY").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(offset_bar, textvariable=self.dy_var, width=8).pack(side=tk.LEFT)
        ttk.Label(offset_bar, text="ΔZ").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(offset_bar, textvariable=self.dz_var, width=8).pack(side=tk.LEFT)
        ttk.Button(offset_bar, text="应用平移", command=self._apply_offset_clicked).pack(side=tk.LEFT, padx=8)
        ttk.Button(offset_bar, text="X/Y 互换", command=self._swap_xy_clicked).pack(side=tk.LEFT)
        
        # 右侧：数据检查结果
        right_frame = ttk.LabelFrame(self, text="数据质量检查", padding=12)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 10), pady=10)
        
        # 底部按钮和统计信息先 pack（side=BOTTOM），窗口偏矮时由检查列表收缩，按钮不被挤掉
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        ttk.Button(
            btn_frame,
            text="确认无误，进入下一步",
            command=self._confirm_points,
            style="Accent.TButton",
        ).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="导出检查报告", command=self._export_issues).pack(side=tk.RIGHT, padx=8)

        # 统计信息（大地坐标时一行放不下，按宽度自动换行）
        stats_frame = ttk.Frame(right_frame)
        stats_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=8)
        self.stats_var = tk.StringVar(value="等待导入数据...")
        stats_label = ttk.Label(stats_frame, textvariable=self.stats_var, style="Title.TLabel", justify=tk.LEFT)
        stats_label.pack(anchor=tk.W, fill=tk.X)
        stats_label.bind("<Configure>", lambda event: stats_label.configure(wraplength=max(event.width, 100)))

        # 检查结果列表
        self.tree_issues = ttk.Treeview(right_frame, columns=('type', 'detail'), show='headings', height=20)
        self.tree_issues.heading('type', text='检查项')
        self.tree_issues.heading('detail', text='详情')
        self.tree_issues.column('type', width=120)
        self.tree_issues.column('detail', width=300)
        self.tree_issues.pack(fill=tk.BOTH, expand=True)

    def _browse_file(self):
        filepath = filedialog.askopenfilename(
            title="选择测量数据文件",
            filetypes=[("所有支持格式", "*.xlsx *.xls *.csv *.txt *.dat"), 
                      ("Excel文件", "*.xlsx *.xls"),
                      ("CSV文件", "*.csv"),
                      ("文本文件", "*.txt *.dat")]
        )
        if filepath:
            self.file_var.set(filepath)
            
    def _import_file(self):
        filepath = self.file_var.get()
        if not filepath:
            messagebox.showwarning("提示", "请先选择文件")
            return

        # 禁用按钮，显示进度条
        self.import_btn.config(state='disabled', text="导入中...")
        if self.progress_bar is None:
            self.progress_bar = ttk.Progressbar(self.file_frame, mode='indeterminate', length=120)
            self.progress_bar.pack(side=tk.LEFT, padx=5)
        self.progress_bar.start(10)

        def import_worker():
            try:
                points, issues, raw_df = DataImporter.import_file(filepath)
                self.after(0, lambda: self._on_import_complete(points, issues, raw_df))
            except Exception as e:
                self.after(0, lambda: self._on_import_error(str(e)))

        import threading
        threading.Thread(target=import_worker, daemon=True).start()

    def _on_import_complete(self, points, issues, raw_df):
        """导入完成，回主线程更新 UI"""
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.import_btn.config(state='normal', text="导入")

        self.points = points
        self.raw_df = raw_df
        self._update_mapping_display()
        self._update_preview()
        self._update_issues(issues)
        self._update_stats()
        messagebox.showinfo("成功", f"导入完成，共 {len(self.points)} 个测量点")

    def _on_import_error(self, error_msg):
        """导入失败，回主线程显示错误"""
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.import_btn.config(state='normal', text="导入")
        messagebox.showerror("错误", f"导入失败: {error_msg}")
            
    def _update_mapping_display(self, mapping: Optional[dict] = None):
        """显示列映射；不传 mapping 时重新自动识别。"""
        self._destroy_mapping_editor()
        self.tree_map.delete(*self.tree_map.get_children())
        if self.raw_df is None:
            return
        if mapping is None:
            mapping = DataImporter.complete_column_mapping(
                self.raw_df, DataValidator.auto_detect_columns(self.raw_df)
            )
        self.column_mapping = dict(mapping)
        for target, label in MAPPING_TARGETS:
            source = self.column_mapping.get(target)
            self.tree_map.insert('', 'end', iid=target, values=(label, NOT_USED if source is None else str(source)))
        names = {"id": "点号", "x": "X", "y": "Y", "z": "高程", "design_z": "设计高程"}
        parts = [f"{names[key]}←{self.column_mapping[key]}" for key in names if key in self.column_mapping]
        missing = [names[key] for key in ("x", "y", "z") if key not in self.column_mapping]
        summary = "，".join(parts)
        if missing:
            summary += f"（未识别：{'、'.join(missing)}）"
            self.set_mapping_expanded(True)
        self.mapping_summary_var.set(summary or "未识别到任何列")

    def set_mapping_expanded(self, expanded: bool) -> None:
        if expanded:
            self.mapping_body.pack(fill=tk.X, pady=(6, 0))
        else:
            self._destroy_mapping_editor()
            self.mapping_body.pack_forget()
        self.mapping_toggle.config(text="收起" if expanded else "展开")

    def _toggle_mapping(self):
        self.set_mapping_expanded(not self.mapping_body.winfo_manager())

    def _auto_detect_mapping(self):
        self._update_mapping_display()

    def _destroy_mapping_editor(self):
        if self._mapping_editor is not None:
            try:
                self._mapping_editor.destroy()
            except tk.TclError:
                pass
            self._mapping_editor = None

    def _on_mapping_double_click(self, event):
        """双击“源列名”弹出下拉框，从原始表格的列中选择。"""
        if self.raw_df is None:
            return
        target = self.tree_map.identify_row(event.y)
        if not target or self.tree_map.identify_column(event.x) != "#2":
            return
        bbox = self.tree_map.bbox(target, "source")
        if not bbox:
            return
        self._destroy_mapping_editor()
        columns = list(self.raw_df.columns)
        labels = [NOT_USED] + [str(column) for column in columns]
        editor = ttk.Combobox(self.tree_map, values=labels, state="readonly")
        x, y, width, height = bbox
        editor.place(x=x, y=y, width=width, height=height)
        current = self.column_mapping.get(target)
        editor.set(NOT_USED if current is None else str(current))
        editor.focus()
        self._mapping_editor = editor

        def commit(_event=None):
            if self._mapping_editor is not editor:
                return
            index = labels.index(editor.get()) if editor.get() in labels else 0
            self.set_mapping_source(target, None if index == 0 else columns[index - 1])

        editor.bind("<<ComboboxSelected>>", commit)
        editor.bind("<FocusOut>", lambda _event: self._destroy_mapping_editor())
        editor.bind("<Escape>", lambda _event: self._destroy_mapping_editor())

    def set_mapping_source(self, target: str, source) -> None:
        """设置某个目标字段对应的源列（None 表示不使用），随后需点“应用映射”。"""
        mapping = dict(self.column_mapping)
        if source is None:
            mapping.pop(target, None)
        else:
            mapping[target] = source
        self._update_mapping_display(mapping)

    def _apply_mapping(self):
        """应用当前映射重新解析数据"""
        if self.raw_df is None:
            return
        mapping = dict(self.column_mapping)
        missing = [label for target, label in MAPPING_TARGETS if target in ("x", "y", "z") and target not in mapping]
        if missing:
            messagebox.showwarning("提示", "请先为以下字段选择源列：" + "、".join(missing))
            return
        chosen = [mapping[target] for target in ("x", "y", "z")]
        if len(set(chosen)) < 3:
            messagebox.showwarning("提示", "X、Y、实测高程不能选同一列")
            return

        self.points, format_errors = DataImporter.points_from_dataframe(self.raw_df, mapping)
        issues = DataValidator.validate_points(self.points)
        issues["format_errors"].extend(format_errors)
        self._update_preview()
        self._update_issues(issues)
        self._update_stats()
        messagebox.showinfo("成功", f"映射应用完成，共 {len(self.points)} 个有效点")

    def load_points(self, points: List[SurveyPoint]) -> None:
        """直接载入测点（打开工程时使用），刷新表格、检查结果和统计。"""
        self.points = points
        self.raw_df = None
        self._update_mapping_display()
        self._update_preview()
        self._update_issues(DataValidator.validate_points(points))
        self._update_stats()

    def _update_preview(self):
        """刷新测点表。"""
        self._destroy_edit_entry()
        self.tree_preview.delete(*self.tree_preview.get_children())
        if self.points:
            cols = ['点号', 'X坐标', 'Y坐标', '实测高程']
            if any(pt.has_design_z for pt in self.points):
                cols.append('设计高程')
            self.tree_preview['columns'] = cols
            for c in cols:
                self.tree_preview.heading(c, text=c)
                self.tree_preview.column(c, width=100, anchor='center')

            preview_count = min(PREVIEW_LIMIT, len(self.points))
            for index, pt in enumerate(self.points[:preview_count]):
                values = [pt.id, f"{pt.x:.3f}", f"{pt.y:.3f}", f"{pt.z:.3f}"]
                if '设计高程' in cols:
                    values.append(f"{pt.design_z:.3f}" if pt.has_design_z else "")
                self.tree_preview.insert('', 'end', iid=str(index), values=values)

            if len(self.points) > preview_count:
                placeholder = ['...', f'（共 {len(self.points)} 行，仅显示前 {preview_count} 行，可用定位点号）', '', '']
                if '设计高程' in cols:
                    placeholder.append('')
                self.tree_preview.insert('', 'end', iid="info", values=placeholder, tags=('info',))
                self.tree_preview.tag_configure('info', foreground=COLORS['dim'])

    def _refresh_after_edit(self, kind: str = "edit", dx: float = 0.0, dy: float = 0.0, dz: float = 0.0):
        issues = DataValidator.validate_points(self.points) if self.points else {
            "missing_values": [], "duplicate_coords": [], "duplicate_ids": [],
            "coord_outliers": [], "elevation_outliers": [], "format_errors": [],
        }
        self._update_preview()
        self._update_issues(issues)
        self._update_stats()
        if self.on_points_mutated:
            self.on_points_mutated(kind, dx, dy, dz)

    def add_survey_point(self, point: SurveyPoint) -> SurveyPoint:
        append_survey_point(self.points, point)
        self._refresh_after_edit("edit")
        return point

    def delete_survey_point_at(self, index: int) -> SurveyPoint:
        removed = remove_survey_point_at(self.points, index)
        self._refresh_after_edit("edit")
        return removed

    def update_survey_point_at(self, index: int, **changes) -> SurveyPoint:
        updated = mutate_survey_point(self.points, index, **changes)
        self._refresh_after_edit("edit")
        return updated

    def apply_coordinate_offset(self, dx: float, dy: float, dz: float) -> None:
        if not self.points:
            raise ValueError("没有可平移的测量点")
        offset_survey_data(self.points, dx, dy, dz)
        self._refresh_after_edit("offset", dx, dy, dz)

    def _destroy_edit_entry(self):
        if self._edit_entry is not None:
            try:
                self._edit_entry.destroy()
            except tk.TclError:
                pass
            self._edit_entry = None

    def _on_preview_double_click(self, event):
        if self.tree_preview.identify("region", event.x, event.y) != "cell":
            return
        item = self.tree_preview.identify_row(event.y)
        column = self.tree_preview.identify_column(event.x)
        if not item or item == "info" or "info" in self.tree_preview.item(item, "tags"):
            return
        bbox = self.tree_preview.bbox(item, column)
        if not bbox:
            return
        col_index = int(column.replace("#", "")) - 1
        columns = list(self.tree_preview["columns"])
        field = columns[col_index]
        self._destroy_edit_entry()
        x, y, width, height = bbox
        entry = ttk.Entry(self.tree_preview)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, self.tree_preview.set(item, field))
        entry.select_range(0, tk.END)
        entry.focus()
        self._edit_entry = entry

        def commit(_event=None):
            if self._edit_entry is not entry:
                return
            text = entry.get().strip()
            self._destroy_edit_entry()
            try:
                self._commit_cell_edit(int(item), field, text)
            except (ValueError, IndexError) as error:
                messagebox.showerror("错误", str(error))

        def cancel(_event=None):
            self._destroy_edit_entry()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    def _commit_cell_edit(self, index: int, field: str, text: str):
        if field == "点号":
            self.update_survey_point_at(index, point_id=text)
        elif field == "X坐标":
            self.update_survey_point_at(index, x=float(text))
        elif field == "Y坐标":
            self.update_survey_point_at(index, y=float(text))
        elif field == "实测高程":
            self.update_survey_point_at(index, z=float(text))
        elif field == "设计高程":
            if text == "":
                self.update_survey_point_at(index, clear_design_z=True)
            else:
                self.update_survey_point_at(index, design_z=float(text))

    def _add_point_dialog(self):
        if self.points:
            last = self.points[-1]
            default = SurveyPoint(id=f"P{len(self.points) + 1}", x=last.x, y=last.y, z=last.z)
        else:
            default = SurveyPoint(id="P1", x=0.0, y=0.0, z=0.0)
        dialog = tk.Toplevel(self)
        dialog.title("添加测点")
        dialog.transient(self.winfo_toplevel())
        values = {
            "点号": tk.StringVar(value=default.id),
            "X": tk.StringVar(value=str(default.x)),
            "Y": tk.StringVar(value=str(default.y)),
            "Z": tk.StringVar(value=str(default.z)),
        }
        for row, (label, var) in enumerate(values.items()):
            ttk.Label(dialog, text=label).grid(row=row, column=0, padx=8, pady=4, sticky=tk.W)
            ttk.Entry(dialog, textvariable=var, width=16).grid(row=row, column=1, padx=8, pady=4)

        def save():
            try:
                self.add_survey_point(SurveyPoint(
                    id=values["点号"].get().strip(),
                    x=float(values["X"].get()),
                    y=float(values["Y"].get()),
                    z=float(values["Z"].get()),
                ))
                dialog.destroy()
            except ValueError as error:
                messagebox.showerror("错误", f"无法添加测点: {error}")

        ttk.Button(dialog, text="确定", command=save, style="Accent.TButton").grid(
            row=len(values), column=0, columnspan=2, pady=8
        )

    def _delete_selected(self):
        selection = self.tree_preview.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选中要删除的测点")
            return
        indexes = sorted(
            (int(item) for item in selection if item != "info" and item.isdigit()),
            reverse=True,
        )
        if not indexes:
            return
        if not messagebox.askokcancel("确认删除", f"确定删除选中的 {len(indexes)} 个测点吗？"):
            return
        for index in indexes:
            remove_survey_point_at(self.points, index)
        self._refresh_after_edit("edit")

    def _focus_point_id(self):
        target = self.find_id_var.get().strip()
        if not target:
            return
        for index, point in enumerate(self.points):
            if point.id == target:
                iid = str(index)
                if self.tree_preview.exists(iid):
                    self.tree_preview.see(iid)
                    self.tree_preview.selection_set(iid)
                    self.tree_preview.focus(iid)
                else:
                    messagebox.showinfo(
                        "提示",
                        f"已找到点 {target}（第 {index + 1} 行）。表格只显示前 {PREVIEW_LIMIT} 行，"
                        "这个点无法在表中直接修改，可在原始文件中修改后重新导入。",
                    )
                return
        messagebox.showwarning("提示", f"没有点号 {target}")

    def _apply_offset_clicked(self):
        try:
            dx = float(self.dx_var.get() or 0)
            dy = float(self.dy_var.get() or 0)
            dz = float(self.dz_var.get() or 0)
        except ValueError:
            messagebox.showerror("错误", "平移量必须是数字")
            return
        if not self.points:
            messagebox.showwarning("提示", "请先导入测量点")
            return
        self.apply_coordinate_offset(dx, dy, dz)
        messagebox.showinfo("成功", f"已平移 ΔX={dx}  ΔY={dy}  ΔZ={dz}")

    def swap_xy(self) -> None:
        if not self.points:
            raise ValueError("没有可互换的测量点")
        swap_survey_xy(self.points)
        self._refresh_after_edit("swap")

    def _swap_xy_clicked(self):
        if not self.points:
            messagebox.showwarning("提示", "请先导入测量点")
            return
        self.swap_xy()
        messagebox.showinfo("成功", "已交换所有测点的 X、Y 坐标")
                
    def _update_issues(self, issues: dict):
        """更新检查结果"""
        self.tree_issues.delete(*self.tree_issues.get_children())
        total = 0
        for key, items in issues.items():
            if items:
                for item in items:
                    self.tree_issues.insert('', 'end', values=(ISSUE_NAMES.get(key, key), item))
                total += len(items)
                
        if total == 0:
            self.tree_issues.insert('', 'end', values=('✓ 通过', '未发现异常数据'), tags=('ok',))
        self.tree_issues.tag_configure('ok', foreground=COLORS['fill'])
        
    def _update_stats(self):
        if self.points:
            xs = [p.x for p in self.points]
            ys = [p.y for p in self.points]
            zs = [p.z for p in self.points]
            self.stats_var.set(
                f"点数: {len(self.points)}  |  "
                f"X范围: {min(xs):.2f}~{max(xs):.2f}  |  "
                f"Y范围: {min(ys):.2f}~{max(ys):.2f}  |  "
                f"高程: {min(zs):.2f}~{max(zs):.2f}"
                + (
                    f"  |  设计高程: {min(designs):.2f}~{max(designs):.2f}"
                    if (designs := [p.design_z for p in self.points if p.has_design_z])
                    else ""
                )
            )
        else:
            self.stats_var.set("无数据")
            
    def _confirm_points(self):
        if not self.points:
            messagebox.showwarning("提示", "没有有效数据")
            return
        self.on_points_loaded(self.points)
        
    def _export_issues(self):
        filepath = filedialog.asksaveasfilename(
            title="导出检查报告",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("CSV文件", "*.csv")]
        )
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write("数据质量检查报告\n")
                    f.write("="*50 + "\n\n")
                    for item in self.tree_issues.get_children():
                        vals = self.tree_issues.item(item)['values']
                        f.write(f"[{vals[0]}] {vals[1]}\n")
                messagebox.showinfo("成功", "报告已导出")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")
