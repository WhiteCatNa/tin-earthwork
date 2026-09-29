"""
计算设置与结果页面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Callable, Optional, Dict, Tuple
from core.calculator import SurveyPoint, CalculationResult, TINEarthworkCalculator, format_id_list
from utils.plotter import EarthworkPlotter, create_standalone_figure
from utils.data_handler import DataExporter
from gui.theme import COLORS, style_text_widget
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import queue

DEFAULT_PROJECT_NAME = "TIN土方计算项目"


class CalculationFrame(ttk.Frame):
    """计算设置与结果页面"""
    
    def __init__(self, parent, points: List[SurveyPoint], boundary: List[Tuple[float, float]],
                 project_name_getter: Optional[Callable[[], str]] = None):
        super().__init__(parent)
        self.project_name_getter = project_name_getter
        self.points = points
        self.boundary = boundary
        self.calculator = TINEarthworkCalculator()
        self.calculator.add_points(points)
        self.calculator.set_boundary(boundary)
        self.result: Optional[CalculationResult] = None
        self.design_elevation_var = tk.DoubleVar(value=self.calculator.design_elevation)
        imported_design = [point for point in points if point.has_design_z]
        unique_design = {round(point.design_z, 6) for point in imported_design}
        self.use_partition_var = tk.BooleanVar(value=len(unique_design) > 1)
        self.partition_data: Dict[str, float] = (
            {point.id: point.design_z for point in imported_design} if len(unique_design) > 1 else {}
        )
        self._detail_rows = []
        self._detail_load_after_id = None
        self._detail_batch_size = 200
        self._detail_load_token = 0
        self._worker_events = queue.Queue()
        self._worker_thread = None
        self._worker_poll_after_id = None
        self._export_events = queue.Queue()
        self._export_thread = None
        self._export_poll_after_id = None
        self._export_buttons: List[ttk.Button] = []
        self._closing = False

        self._create_widgets()

    def _project_name(self) -> str:
        name = self.project_name_getter() if self.project_name_getter else ""
        return (name or "").strip() or DEFAULT_PROJECT_NAME

    def design_elevation_value(self) -> float:
        """读取统一设计高程；输入框不是数字时给出明确提示。"""
        try:
            return float(self.design_elevation_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("统一设计高程必须是数字") from None

    def update_inputs(self, points: List[SurveyPoint], boundary: List[Tuple[float, float]]):
        """测点或边界变化后调用：同步计算器并作废旧结果。"""
        self.points = points
        self.boundary = boundary
        self.calculator.add_points(points)
        self.calculator.set_boundary(boundary)
        self.result = None
        self._refresh_plot()

    def apply_design_settings(self, design_elevation: float, use_partition: bool, partition: Dict[str, float]):
        """恢复工程文件中的设计高程设置。"""
        self.design_elevation_var.set(design_elevation)
        self.use_partition_var.set(bool(use_partition))
        self.partition_data = dict(partition or {})
        self._toggle_partition()
        if self.use_partition_var.get() and self.partition_data:
            self.calculator.set_design_elevations(self.partition_data, default=design_elevation)
            self.partition_label.config(
                text=f"已设置 {len(self.partition_data)} 个分区高程", foreground=COLORS["ink"]
            )
        else:
            self.calculator.set_design_elevation(design_elevation)
        self.result = None
        self._refresh_plot()
        
    def _create_widgets(self):
        # 左侧：设置面板
        left_frame = ttk.LabelFrame(self, text="计算设置", padding=12)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 6), pady=10)
        
        # 统一设计高程
        ttk.Label(left_frame, text="统一设计高程 (m):", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 6))
        elev_frame = ttk.Frame(left_frame)
        elev_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Entry(elev_frame, textvariable=self.design_elevation_var, width=15).pack(side=tk.LEFT)
        ttk.Button(elev_frame, text="从数据估算", command=self._estimate_elevation).pack(side=tk.LEFT, padx=8)
        
        # 分区设计高程
        ttk.Separator(left_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Checkbutton(left_frame, text="启用分区设计高程", variable=self.use_partition_var,
                       command=self._toggle_partition).pack(anchor=tk.W)
        
        self.partition_frame = ttk.Frame(left_frame)
        self.partition_frame.pack(fill=tk.X, pady=6)
        ttk.Button(self.partition_frame, text="编辑分区高程", command=self._edit_partition).pack(fill=tk.X)
        self.partition_label = ttk.Label(self.partition_frame, text="未设置", style="Muted.TLabel")
        self.partition_label.pack(anchor=tk.W, pady=6)
        self._toggle_partition()
        if self.partition_data:
            self.partition_label.config(
                text=f"已从文件导入 {len(self.partition_data)} 个分区高程",
                foreground=COLORS["ink"],
            )
        
        # 计算选项
        ttk.Separator(left_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(left_frame, text="计算选项:", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 4))
        
        self.show_tin_var = tk.BooleanVar(value=True)
        self.show_contour_var = tk.BooleanVar(value=True)
        self.show_points_var = tk.BooleanVar(value=True)
        self.show_boundary_var = tk.BooleanVar(value=True)
        
        ttk.Checkbutton(left_frame, text="显示TIN网格", variable=self.show_tin_var,
                       command=self._refresh_plot).pack(anchor=tk.W)
        ttk.Checkbutton(left_frame, text="显示零填挖线", variable=self.show_contour_var,
                       command=self._refresh_plot).pack(anchor=tk.W)
        ttk.Checkbutton(left_frame, text="显示测量点", variable=self.show_points_var,
                       command=self._refresh_plot).pack(anchor=tk.W)
        ttk.Checkbutton(left_frame, text="显示计算边界", variable=self.show_boundary_var,
                       command=self._refresh_plot).pack(anchor=tk.W)
        
        # 计算按钮
        ttk.Separator(left_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        self.calculate_button = ttk.Button(
            left_frame,
            text="开始计算",
            command=self._run_calculation,
            style='Accent.TButton'
        )
        self.calculate_button.pack(fill=tk.X, ipady=5)
        
        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(left_frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=8)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(left_frame, textvariable=self.status_var, style="Muted.TLabel").pack(anchor=tk.W)
        
        # 右侧：结果显示
        right_frame = ttk.Frame(self)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 10), pady=10)
        
        # 结果选项卡
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # 选项卡1：图形显示
        self.plot_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.plot_frame, text="计算结果图")
        
        self.plotter = EarthworkPlotter(figsize=(10, 7))
        self.canvas = FigureCanvasTkAgg(self.plotter.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.plotter.bind_canvas(self.canvas)
        self.plotter.on_triangle_click = self._on_triangle_selected
        
        # 选项卡2：汇总表
        self.summary_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.summary_frame, text="汇总表")
        self._create_summary_table()
        
        # 选项卡3：明细表
        self.detail_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.detail_frame, text="计算明细")
        self._create_detail_table()
        
        # 选项卡4：三角形信息 (点击三角形后显示)
        self.info_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.info_frame, text="三角形详情")
        self._create_triangle_info()
        
        # 底部导出按钮
        export_frame = ttk.Frame(right_frame)
        export_frame.pack(fill=tk.X, pady=(8, 0))
        for index, (text, command) in enumerate((
            ("导出 Excel 报告", self._export_excel),
            ("导出 CSV 明细", self._export_csv),
            ("导出 DXF", self._export_dxf),
            ("导出 PDF 计算书", self._export_pdf),
            ("导出高清图片", self._export_image),
            ("生成文本报告", self._export_text),
        )):
            button = ttk.Button(export_frame, text=text, command=command)
            button.pack(side=tk.LEFT, padx=(0, 6) if index == 0 else 6)
            self._export_buttons.append(button)
        
    def _create_summary_table(self):
        """创建汇总表"""
        columns = ('项目', '数值', '单位', '说明')
        self.tree_summary = ttk.Treeview(self.summary_frame, columns=columns, show='headings', height=10)
        for col in columns:
            self.tree_summary.heading(col, text=col)
            self.tree_summary.column(col, width=150, anchor='center')
        self.tree_summary.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        
    def _create_detail_table(self):
        """创建明细表"""
        columns = ('三角形', '顶点1', '顶点2', '顶点3', '边界内面积(m²)', '平均高差(m)',
                  '挖方量(m³)', '填方量(m³)', '净体积(m³)', '类型')
        self.tree_detail = ttk.Treeview(self.detail_frame, columns=columns, show='headings', height=20)
        for col in columns:
            self.tree_detail.heading(col, text=col)
            self.tree_detail.column(col, width=100, anchor='center')
            
        vsb = ttk.Scrollbar(self.detail_frame, orient="vertical", command=self.tree_detail.yview)
        hsb = ttk.Scrollbar(self.detail_frame, orient="horizontal", command=self.tree_detail.xview)
        self.tree_detail.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree_detail.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        self.detail_frame.grid_rowconfigure(0, weight=1)
        self.detail_frame.grid_columnconfigure(0, weight=1)
        
    def _create_triangle_info(self):
        """创建三角形详情面板"""
        self.info_text = tk.Text(self.info_frame, wrap=tk.WORD)
        style_text_widget(self.info_text)
        self.info_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.info_text.insert('1.0', '点击左侧图形中的三角形查看详细信息...')
        self.info_text.config(state=tk.DISABLED)
        
    def _estimate_elevation(self):
        """从数据估算设计高程 (中位数)"""
        import numpy as np
        zs = [p.z for p in self.points]
        median_z = np.median(zs)
        self.design_elevation_var.set(round(median_z, 3))
        messagebox.showinfo("估算结果", f"建议设计高程 (中位数): {median_z:.3f} m")
        
    def _toggle_partition(self):
        """切换分区高程编辑状态"""
        if self.use_partition_var.get():
            self.partition_frame.pack(fill=tk.X, pady=5)
        else:
            self.partition_frame.pack_forget()
            
    def _edit_partition(self):
        """编辑分区设计高程对话框"""
        dialog = tk.Toplevel(self)
        dialog.title("分区设计高程设置")
        dialog.geometry("500x400")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        
        ttk.Label(dialog, text="为选定点号设置不同的设计高程 (点号,高程 每行一条):").pack(anchor=tk.W, padx=12, pady=8)
        
        text_frame = ttk.Frame(dialog)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        
        text = tk.Text(text_frame)
        style_text_widget(text)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 加载现有数据
        for pid, elev in self.partition_data.items():
            text.insert('end', f"{pid},{elev}\n")
            
        def save():
            data, bad_lines = parse_partition_text(text.get('1.0', 'end-1c'))
            known_ids = {point.id for point in self.points}
            unknown = [pid for pid in data if pid not in known_ids]
            problems = []
            if bad_lines:
                problems.append("以下行格式不对（应为 点号,高程），将被忽略：\n" + "\n".join(bad_lines[:10]))
            if unknown:
                problems.append("以下点号在测点表中不存在：" + "、".join(unknown[:10])
                                + (f" 等共 {len(unknown)} 个" if len(unknown) > 10 else ""))
            if problems and not messagebox.askokcancel(
                "请核对分区高程", "\n\n".join(problems) + "\n\n仍要保存吗？", parent=dialog
            ):
                return
            self.partition_data = data
            self.partition_label.config(text=f"已设置 {len(self.partition_data)} 个分区高程", foreground=COLORS["ink"])
            dialog.destroy()
            
        ttk.Button(dialog, text="保存", command=save, style="Accent.TButton").pack(pady=10)
        
    def _run_calculation(self):
        """运行计算 (在后台线程)"""
        if not self.points:
            messagebox.showwarning("提示", "没有可计算的测量点")
            return

        # Tkinter 控件只能由主线程访问；在线程启动前读取所需配置。
        use_partition = self.use_partition_var.get()
        partition_data = self.partition_data.copy()
        try:
            design_elevation = self.design_elevation_value()
        except ValueError as error:
            messagebox.showerror("输入错误", str(error))
            return

        self.status_var.set("正在计算...")
        self.progress_var.set(0)
        self.calculate_button.config(state=tk.DISABLED)
        self.update_idletasks()

        def calc_thread():
            try:
                if use_partition and partition_data:
                    # 未列入分区的点使用界面上的统一设计高程
                    self.calculator.set_design_elevations(partition_data, default=design_elevation)
                else:
                    self.calculator.set_design_elevation(design_elevation)

                self._worker_events.put(("progress", 30, "构建 TIN 三角网..."))
                result = self.calculator.run_full_calculation()
                if use_partition and partition_data:
                    unlisted = [str(p.id) for p in self.calculator.points if p.id not in partition_data]
                    if unlisted:
                        result.warnings.insert(0, (
                            f"有 {len(unlisted)} 个测点不在分区高程表中，按统一设计高程 "
                            f"{design_elevation:.3f} m 计算：{format_id_list(unlisted)}"
                        ))
                self._worker_events.put(("progress", 90, "更新显示..."))
                self._worker_events.put(("done", result))
            except Exception as error:
                self._worker_events.put(("error", error))

        self._worker_events = queue.Queue()
        self._worker_thread = threading.Thread(target=calc_thread, daemon=True)
        self._worker_thread.start()
        self._schedule_worker_poll()

    def _schedule_worker_poll(self):
        """在 Tk 主线程轮询后台计算事件。"""
        if self._closing or self._worker_poll_after_id is not None:
            return
        self._worker_poll_after_id = self.after(20, self._poll_worker_events)

    def _poll_worker_events(self):
        """处理后台线程生成的纯 Python 事件。"""
        self._worker_poll_after_id = None
        if self._closing:
            return

        terminal_event = False
        while True:
            try:
                event = self._worker_events.get_nowait()
            except queue.Empty:
                break

            if event[0] == "progress":
                self._update_calculation_progress(event[1], event[2])
            elif event[0] == "done":
                self._worker_thread = None
                self.result = event[1]
                self._on_calculation_done()
                terminal_event = True
            elif event[0] == "error":
                self._worker_thread = None
                self._on_calculation_error(event[1])
                terminal_event = True

        if not terminal_event and self._worker_thread is not None:
            self._schedule_worker_poll()

    def _update_calculation_progress(self, progress: float, status: str):
        """在主线程更新计算进度。"""
        self.progress_var.set(progress)
        self.status_var.set(status)
        
    def _on_calculation_done(self):
        self.progress_var.set(100)
        self.status_var.set("计算完成")
        self.calculate_button.config(state=tk.NORMAL)
        self._refresh_plot()
        self._update_summary_table()
        self._start_detail_table_load()
        summary = (
            f"总挖方量: {self.result.total_cut:.2f} m³\n"
            f"总填方量: {self.result.total_fill:.2f} m³\n"
            f"挖填差值: {self.result.net_volume:.2f} m³\n"
            f"计算面积: {self.result.computed_area:.2f} m²\n"
            f"三角形数: {self.result.triangle_count}\n"
            f"混合三角形: {self.result.mixed_triangle_count}"
        )
        if self.result.warnings:
            self.status_var.set("计算完成（有提示，请查看）")
            messagebox.showwarning(
                "计算完成，请注意",
                summary + "\n\n" + "\n\n".join(f"· {item}" for item in self.result.warnings),
            )
        else:
            messagebox.showinfo("计算完成", summary)
            
    def _on_calculation_error(self, error):
        self.status_var.set("计算失败")
        self.progress_var.set(0)
        self.calculate_button.config(state=tk.NORMAL)
        messagebox.showerror("计算错误", str(error))
        
    def _refresh_plot(self):
        if self.result:
            self.plotter.plot_result(
                self.result, self.points,
                show_tin=self.show_tin_var.get(),
                show_contour=self.show_contour_var.get(),
                show_points=self.show_points_var.get(),
                show_boundary=self.show_boundary_var.get()
            )
        else:
            self.plotter.plot_points_only(self.points)
            
    def _update_summary_table(self):
        """立即更新体积汇总；大明细表由后台事件循环分批填充。"""
        if not self.result:
            return

        self.tree_summary.delete(*self.tree_summary.get_children())
        for row in DataExporter.summary_rows(self.result):
            self.tree_summary.insert('', 'end', values=row)
        for item in self.result.warnings:
            self.tree_summary.insert('', 'end', values=('提示', '', '', item), tags=('warn',))
        self.tree_summary.tag_configure('warn', foreground=COLORS["cut"])

    def _start_detail_table_load(self):
        """分批写入明细，避免大量 Treeview 行阻塞主界面。"""
        self._detail_load_token += 1
        if self._detail_load_after_id is not None:
            self.after_cancel(self._detail_load_after_id)
            self._detail_load_after_id = None
        self.tree_detail.delete(*self.tree_detail.get_children())
        if not self.result:
            self._detail_rows = []
            return
        self._detail_rows = [tri for tri in self.result.triangles if not tri.is_boundary]
        self._load_detail_batch(0, self._detail_load_token)

    def _load_detail_batch(self, start: int, token: int):
        """写入一批明细行，并把其余工作让回 Tk 事件循环。"""
        if token != self._detail_load_token:
            return
        end = min(start + self._detail_batch_size, len(self._detail_rows))
        for tri in self._detail_rows[start:end]:
            p0, p1, p2 = tri.vertex_points
            tri_type = "混合" if tri.is_mixed else ("挖方" if tri.volume > 0 else "填方")
            if tri.is_clipped:
                tri_type += "（边界裁剪）"
            self.tree_detail.insert('', 'end', values=(
                tri.id,
                f"{p0.id}({p0.delta_z:+.3f})",
                f"{p1.id}({p1.delta_z:+.3f})",
                f"{p2.id}({p2.delta_z:+.3f})",
                f"{tri.area:.2f}",
                f"{tri.avg_delta_z:.3f}" if not tri.is_mixed else "分割计算",
                f"{tri.cut_volume:.3f}",
                f"{tri.fill_volume:.3f}",
                f"{tri.volume:.3f}",
                tri_type
            ))
        if end < len(self._detail_rows):
            self._detail_load_after_id = self.after(1, self._load_detail_batch, end, token)
        else:
            self._detail_load_after_id = None

    def _on_triangle_selected(self, tri):
        """三角形被点击时显示详情"""
        self.notebook.select(self.info_frame)
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete('1.0', 'end')
        
        p0, p1, p2 = tri.vertex_points
        info = f"""三角形详细信息
{'='*50}
三角形编号: {tri.id}
是否被边界裁剪: {'是（只计边界内部分）' if tri.is_clipped else '否'}
是否混合挖填: {'是' if tri.is_mixed else '否'}
边界内水平面积: {tri.area:.3f} m²

顶点信息:
  顶点1: {p0.id}  X={p0.x:.3f}  Y={p0.y:.3f}  实测={p0.z:.3f}  设计={p0.design_z:.3f}  高差={p0.delta_z:+.3f}
  顶点2: {p1.id}  X={p1.x:.3f}  Y={p1.y:.3f}  实测={p1.z:.3f}  设计={p1.design_z:.3f}  高差={p1.delta_z:+.3f}
  顶点3: {p2.id}  X={p2.x:.3f}  Y={p2.y:.3f}  实测={p2.z:.3f}  设计={p2.design_z:.3f}  高差={p2.delta_z:+.3f}

计算结果:
  平均高差: {tri.avg_delta_z:.3f} m
  挖方量: {tri.cut_volume:.3f} m³
  填方量: {tri.fill_volume:.3f} m³
  净体积: {tri.volume:.3f} m³
"""
        if tri.is_mixed:
            info += (
                "\n按零填挖线分割:\n"
                f"  挖方部分: 面积={tri.cut_area:.3f} m²  体积={tri.cut_volume:.3f} m³\n"
                f"  填方部分: 面积={tri.fill_area:.3f} m²  体积={tri.fill_volume:.3f} m³\n"
            )

        self.info_text.insert('1.0', info)
        self.info_text.config(state=tk.DISABLED)
        
    def shutdown(self):
        """停止 Tk 回调并释放嵌入式绘图资源。"""
        if self._closing:
            return
        self._closing = True
        self._detail_load_token += 1

        for attr_name in ("_worker_poll_after_id", "_detail_load_after_id", "_export_poll_after_id"):
            after_id = getattr(self, attr_name, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr_name, None)

        if hasattr(self, "plotter"):
            self.plotter.close()
        if hasattr(self, "canvas"):
            try:
                self.canvas.get_tk_widget().destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # 导出（后台线程执行，避免大数据量时界面无响应）
    # ------------------------------------------------------------------

    def _ask_export_path(self, title: str, extension: str, filetypes) -> Optional[str]:
        if not self.result:
            messagebox.showwarning("提示", "请先进行计算")
            return None
        if self._export_thread is not None:
            messagebox.showinfo("提示", "上一个导出任务还在进行，请稍候")
            return None
        return filedialog.asksaveasfilename(
            title=title, defaultextension=extension, filetypes=filetypes,
            initialfile=f"{self._project_name()}{extension}",
        ) or None

    def _run_export(self, label: str, work: Callable[[], None], success: str):
        """在后台线程执行导出，完成后在主线程提示结果。"""
        for button in self._export_buttons:
            button.config(state=tk.DISABLED)
        self.status_var.set(f"正在{label}...")
        events = queue.Queue()
        self._export_events = events

        def worker():
            try:
                work()
                events.put(("done", success))
            except Exception as error:  # 把具体原因带回界面
                hint = "\n文件可能正被 Excel 等程序打开，请关闭后重试。" if isinstance(error, PermissionError) else ""
                events.put(("error", f"{label}失败: {error}{hint}"))

        self._export_thread = threading.Thread(target=worker, daemon=True)
        self._export_thread.start()
        self._schedule_export_poll()

    def _schedule_export_poll(self):
        if self._closing or self._export_poll_after_id is not None:
            return
        self._export_poll_after_id = self.after(50, self._poll_export_events)

    def _poll_export_events(self):
        self._export_poll_after_id = None
        if self._closing:
            return
        try:
            kind, message = self._export_events.get_nowait()
        except queue.Empty:
            self._schedule_export_poll()
            return
        self._export_thread = None
        for button in self._export_buttons:
            button.config(state=tk.NORMAL)
        if kind == "done":
            self.status_var.set(message)
            messagebox.showinfo("成功", message)
        else:
            self.status_var.set("导出失败")
            messagebox.showerror("错误", message)

    def _export_excel(self):
        filepath = self._ask_export_path("导出 Excel 报告", ".xlsx", [("Excel文件", "*.xlsx")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result, points = self.result, list(self.points)
        self._run_export(
            "导出 Excel 报告",
            lambda: exporter.export_summary_excel(result, filepath, points),
            "Excel 报告导出成功",
        )

    def _export_csv(self):
        filepath = self._ask_export_path("导出 CSV 明细", ".csv", [("CSV文件", "*.csv")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result = self.result
        self._run_export("导出 CSV 明细", lambda: exporter.export_triangles_csv(result, filepath), "CSV 明细导出成功")

    def _export_dxf(self):
        filepath = self._ask_export_path("导出 DXF", ".dxf", [("DXF图形", "*.dxf")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result = self.result
        self._run_export(
            "导出 DXF",
            lambda: exporter.export_boundary_dxf(result, filepath),
            "DXF 已导出（BOUNDARY 边界 / ZERO_CONTOUR 零填挖线）",
        )

    def _export_pdf(self):
        filepath = self._ask_export_path("导出 PDF 计算书", ".pdf", [("PDF文件", "*.pdf")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        result, points = self.result, list(self.points)
        self._run_export(
            "导出 PDF 计算书",
            lambda: exporter.export_pdf_report(result, len(points), points, result.boundary_points, filepath),
            "PDF 计算书已导出（汇总页 + 成果图）",
        )

    def _export_image(self):
        filepath = self._ask_export_path(
            "导出高清图片", ".png",
            [("PNG图片", "*.png"), ("PDF文件", "*.pdf"), ("SVG矢量图", "*.svg")],
        )
        if not filepath:
            return
        result, points, name = self.result, list(self.points), self._project_name()

        def work():
            figure = create_standalone_figure(result, points, result.boundary_points, name)
            figure.savefig(filepath, dpi=300, bbox_inches='tight')

        self._run_export("导出高清图片", work, "高清图片导出成功")

    def _export_text(self):
        filepath = self._ask_export_path("生成文本报告", ".txt", [("文本文件", "*.txt")])
        if not filepath:
            return
        exporter = DataExporter(self._project_name())
        report = exporter.generate_report_text(self.result, len(self.points))

        def work():
            with open(filepath, 'w', encoding='utf-8') as handle:
                handle.write(report)

        self._run_export("生成文本报告", work, "文本报告生成成功")


def parse_partition_text(content: str) -> Tuple[Dict[str, float], List[str]]:
    """解析“点号,高程”文本（逗号、中文逗号、制表符或空格分隔），返回 (数据, 无法解析的行)。"""
    data: Dict[str, float] = {}
    bad_lines: List[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [part for part in line.replace("，", ",").replace("\t", ",").replace(" ", ",").split(",") if part]
        try:
            if len(parts) != 2:
                raise ValueError
            data[parts[0].strip()] = float(parts[1])
        except ValueError:
            bad_lines.append(line)
    return data, bad_lines
