"""
主窗口和数据导入页面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
from typing import List, Optional, Callable
from core.calculator import SurveyPoint
from utils.data_handler import DataImporter, DataValidator


class DataImportFrame(ttk.Frame):
    """数据导入与检查页面"""
    
    def __init__(self, parent, on_points_loaded: Callable[[List[SurveyPoint]], None]):
        super().__init__(parent)
        self.on_points_loaded = on_points_loaded
        self.points: List[SurveyPoint] = []
        self.raw_df: Optional[pd.DataFrame] = None
        self.column_mapping = {}
        
        self._create_widgets()
        
    def _create_widgets(self):
        # 左侧：文件操作和列映射
        left_frame = ttk.LabelFrame(self, text="数据导入", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 文件选择
        self.file_frame = ttk.Frame(left_frame)
        self.file_frame.pack(fill=tk.X, pady=5)

        ttk.Label(self.file_frame, text="数据文件:").pack(side=tk.LEFT)
        self.file_var = tk.StringVar()
        ttk.Entry(self.file_frame, textvariable=self.file_var, width=40).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(self.file_frame, text="浏览...", command=self._browse_file).pack(side=tk.LEFT)
        self.import_btn = ttk.Button(self.file_frame, text="导入", command=self._import_file)
        self.import_btn.pack(side=tk.LEFT, padx=5)
        # 进度条占位（不创建，仅在导入时动态添加）
        self.progress_bar: Optional[ttk.Progressbar] = None
        
        # 列映射
        map_frame = ttk.LabelFrame(left_frame, text="列映射 (自动识别，可手动调整)", padding=5)
        map_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.tree_map = ttk.Treeview(map_frame, columns=('source', 'target'), show='headings', height=8)
        self.tree_map.heading('source', text='源列名')
        self.tree_map.heading('target', text='目标字段')
        self.tree_map.column('source', width=150)
        self.tree_map.column('target', width=150)
        self.tree_map.pack(fill=tk.BOTH, expand=True)
        
        # 映射编辑按钮
        map_btn_frame = ttk.Frame(map_frame)
        map_btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(map_btn_frame, text="应用映射", command=self._apply_mapping).pack(side=tk.LEFT)
        ttk.Button(map_btn_frame, text="重置自动识别", command=self._auto_detect_mapping).pack(side=tk.LEFT, padx=5)
        
        # 数据预览
        preview_frame = ttk.LabelFrame(left_frame, text="数据预览 (前20行)", padding=5)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.tree_preview = ttk.Treeview(preview_frame, show='headings', height=10)
        vsb = ttk.Scrollbar(preview_frame, orient="vertical", command=self.tree_preview.yview)
        hsb = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.tree_preview.xview)
        self.tree_preview.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree_preview.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)
        
        # 右侧：数据检查结果
        right_frame = ttk.LabelFrame(self, text="数据质量检查", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 检查结果列表
        self.tree_issues = ttk.Treeview(right_frame, columns=('type', 'detail'), show='headings', height=20)
        self.tree_issues.heading('type', text='检查项')
        self.tree_issues.heading('detail', text='详情')
        self.tree_issues.column('type', width=120)
        self.tree_issues.column('detail', width=300)
        self.tree_issues.pack(fill=tk.BOTH, expand=True)
        
        # 统计信息
        stats_frame = ttk.Frame(right_frame)
        stats_frame.pack(fill=tk.X, pady=5)
        self.stats_var = tk.StringVar(value="等待导入数据...")
        ttk.Label(stats_frame, textvariable=self.stats_var, font=('', 10, 'bold')).pack()
        
        # 底部按钮
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="确认无误，进入下一步", command=self._confirm_points).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="导出检查报告", command=self._export_issues).pack(side=tk.RIGHT, padx=5)
        
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
            
    def _update_mapping_display(self):
        """更新列映射显示"""
        self.tree_map.delete(*self.tree_map.get_children())
        if self.raw_df is not None:
            from utils.data_handler import DataValidator
            self.column_mapping = DataValidator.auto_detect_columns(self.raw_df)
            # 如果自动识别不全，补充前4列
            cols = self.raw_df.columns.tolist()
            defaults = {'id': 0, 'x': 1, 'y': 2, 'z': 3}
            for key, idx in defaults.items():
                if key not in self.column_mapping and idx < len(cols):
                    self.column_mapping[key] = cols[idx]
                    
            for target, source in self.column_mapping.items():
                self.tree_map.insert('', 'end', values=(source, target), tags=(target,))
                
    def _auto_detect_mapping(self):
        self._update_mapping_display()
        
    def _apply_mapping(self):
        """应用当前映射重新解析数据"""
        if self.raw_df is None:
            return
            
        # 从 tree 获取当前映射
        mapping = {}
        for item in self.tree_map.get_children():
            vals = self.tree_map.item(item)['values']
            if len(vals) == 2:
                mapping[vals[1]] = vals[0]
        self.column_mapping = mapping
        
        # 重新解析
        try:
            self.points = []
            for idx, row in self.raw_df.iterrows():
                try:
                    pt = SurveyPoint(
                        id=str(row[mapping.get('id', self.raw_df.columns[0])]) if 'id' in mapping else f"P{idx+1}",
                        x=float(row[mapping['x']]) if 'x' in mapping else 0.0,
                        y=float(row[mapping['y']]) if 'y' in mapping else 0.0,
                        z=float(row[mapping['z']]) if 'z' in mapping else 0.0
                    )
                    self.points.append(pt)
                except (ValueError, KeyError):
                    continue
                    
            issues = DataValidator.validate_points(self.points)
            self._update_preview()
            self._update_issues(issues)
            self._update_stats()
            messagebox.showinfo("成功", f"映射应用完成，共 {len(self.points)} 个有效点")
        except Exception as e:
            messagebox.showerror("错误", f"映射应用失败: {e}")
            
    def _update_preview(self):
        """更新数据预览（限制 500 行）"""
        self.tree_preview.delete(*self.tree_preview.get_children())
        if self.points:
            cols = ['点号', 'X坐标', 'Y坐标', '实测高程']
            self.tree_preview['columns'] = cols
            for c in cols:
                self.tree_preview.heading(c, text=c)
                self.tree_preview.column(c, width=100, anchor='center')

            # 只显示前 500 行
            preview_count = min(500, len(self.points))
            for pt in self.points[:preview_count]:
                self.tree_preview.insert('', 'end', values=(pt.id, f"{pt.x:.3f}", f"{pt.y:.3f}", f"{pt.z:.3f}"))

            # 如果数据超过 500 行，在表格下方显示提示
            if len(self.points) > preview_count:
                # 添加一个占位行显示"..."
                self.tree_preview.insert('', 'end', values=('...', f'（共 {len(self.points)} 行，仅显示前 {preview_count} 行）', '', ''), tags=('info',))
                self.tree_preview.tag_configure('info', foreground='gray')
                
    def _update_issues(self, issues: dict):
        """更新检查结果"""
        self.tree_issues.delete(*self.tree_issues.get_children())
        type_names = {
            'missing_values': '缺失值',
            'duplicate_coords': '重复坐标',
            'duplicate_ids': '重复点号',
            'coord_outliers': '坐标异常',
            'elevation_outliers': '高程异常',
            'format_errors': '格式错误'
        }
        total = 0
        for key, items in issues.items():
            if items:
                for item in items:
                    self.tree_issues.insert('', 'end', values=(type_names.get(key, key), item))
                total += len(items)
                
        if total == 0:
            self.tree_issues.insert('', 'end', values=('✓ 通过', '未发现异常数据'), tags=('ok',))
        self.tree_issues.tag_configure('ok', foreground='green')
        
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
                
    def get_points(self) -> List[SurveyPoint]:
        return self.points