"""
可视化模块：matplotlib 绘图嵌入 Tkinter
"""
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Polygon, Patch
from matplotlib.collections import PatchCollection, LineCollection
import numpy as np
from typing import List, Tuple, Optional, Callable
from core.calculator import SurveyPoint, Triangle, CalculationResult
from gui.theme import COLORS, PLOT_COLORS

# 设置中文字体
import matplotlib.font_manager as fm
import platform

def setup_chinese_font():
    """配置中文 UI 字体，并保留拉丁字体以渲染 m²/m³ 上标。"""
    plt.rcParams['axes.unicode_minus'] = False
    system = platform.system()
    if system == 'Darwin':
        preferred = ['PingFang SC', 'Hiragino Sans GB', 'Heiti SC', 'Songti SC']
    elif system == 'Windows':
        preferred = ['Microsoft YaHei', 'SimHei', 'SimSun']
    else:
        preferred = ['Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'Droid Sans Fallback']

    available = {font.name for font in fm.fontManager.ttflist}
    chosen = [name for name in preferred if name in available]
    for latin in ('Arial Unicode MS', 'DejaVu Sans', 'Arial'):
        if latin in available and latin not in chosen:
            chosen.append(latin)
            break
    if chosen:
        plt.rcParams['font.family'] = chosen

setup_chinese_font()


class EarthworkPlotter:
    """土方计算结果可视化"""
    
    def __init__(self, figsize=(10, 8), dpi=100):
        # constrained_layout 替代每次手动调用 tight_layout()，性能更好
        self.fig = Figure(figsize=figsize, dpi=dpi, constrained_layout=True, facecolor=COLORS["sheet"])
        self.ax = self.fig.add_subplot(111)
        self.canvas = None
        self.toolbar = None
        self.selected_triangle = None
        self.on_triangle_click = None  # 回调函数
        self._boundary_preview_artists = None
        self._colorbar = None
        self._connection_ids = []
        self._current_result = None

        # 结果图 artist 缓存：result 不变时只切换可见性，跳过全量重建
        self._cached_result_id: Optional[int] = None  # id(result)
        self._cached_points_id: Optional[int] = None  # id(points)
        self._artist_cut: Optional[object] = None
        self._artist_fill: Optional[object] = None
        self._artist_tin: Optional[object] = None
        self._artist_contour_lines: list = []
        self._artist_scatter: Optional[object] = None
        self._artist_boundary: Optional[object] = None

        # 颜色配置（与 GUI 主题共用挖填配色）
        self.colors = dict(PLOT_COLORS)
        self._style_axes()
        
    def bind_canvas(self, canvas: FigureCanvasTkAgg, toolbar_frame=None, enable_hover=True):
        """绑定画布和工具栏"""
        self.canvas = canvas
        self._last_hover_time: float = 0.0
        widget = canvas.get_tk_widget()
        widget.configure(background=COLORS["sheet"], highlightthickness=0)
        if toolbar_frame:
            self.toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
            self.toolbar.update()
        self._connection_ids.append(self.canvas.mpl_connect('button_press_event', self._on_click))
        if enable_hover:
            self._connection_ids.append(self.canvas.mpl_connect('motion_notify_event', self._on_hover))
        
    def _on_click(self, event):
        """鼠标点击事件"""
        if event.inaxes != self.ax or event.button != 1:
            return
            
        x, y = event.xdata, event.ydata
        if x is None or y is None:
            return
            
        # 查找最近的三角形
        if hasattr(self, '_current_result') and self._current_result:
            tri = self._find_triangle_at(x, y, self._current_result.triangles)
            if tri:
                self.selected_triangle = tri
                self.highlight_triangle(tri)
                if self.on_triangle_click:
                    self.on_triangle_click(tri)
                    
    def _on_hover(self, event):
        """鼠标悬停事件 - 显示坐标（限速：最多 30 fps，约 33 ms/帧）"""
        import time
        if event.inaxes != self.ax or event.xdata is None:
            return
        now = time.perf_counter()
        if now - self._last_hover_time < 0.033:   # 低于 33 ms 直接跳过
            return
        self._last_hover_time = now
        x, y = event.xdata, event.ydata
        self.ax.set_title(f"X: {x:.3f}, Y: {y:.3f}", fontsize=9, loc='right', color='gray')
        self.canvas.draw_idle()
            
    def _find_triangle_at(self, x: float, y: float, triangles: List[Triangle]) -> Optional[Triangle]:
        """查找包含点 (x,y) 的三角形。"""
        candidates = []
        for tri in triangles:
            if tri.is_boundary:
                continue
            p0, p1, p2 = tri.vertex_points
            if (min(p.x for p in (p0, p1, p2)) <= x <= max(p.x for p in (p0, p1, p2)) and
                    min(p.y for p in (p0, p1, p2)) <= y <= max(p.y for p in (p0, p1, p2))):
                candidates.append(tri)
        for tri in candidates:
            p0, p1, p2 = tri.vertex_points
            # 重心坐标法判断点在三角形内
            if self._point_in_triangle(x, y, p0, p1, p2):
                return tri
        return None
    
    def _point_in_triangle(self, x: float, y: float, 
                           p0: SurveyPoint, p1: SurveyPoint, p2: SurveyPoint) -> bool:
        """重心坐标法判断点在三角形内"""
        x1, y1 = p0.x, p0.y
        x2, y2 = p1.x, p1.y
        x3, y3 = p2.x, p2.y
        
        denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(denom) < 1e-10:
            return False
            
        a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / denom
        b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / denom
        c = 1 - a - b
        
        return 0 <= a <= 1 and 0 <= b <= 1 and 0 <= c <= 1

    def _style_axes(self):
        """Apply survey-drawing paper colors after ax.clear()."""
        self.fig.patch.set_facecolor(COLORS["sheet"])
        self.ax.set_facecolor(COLORS["sheet"])
        self.ax.tick_params(colors=COLORS["ink"], labelsize=9)
        self.ax.xaxis.label.set_color(COLORS["ink"])
        self.ax.yaxis.label.set_color(COLORS["ink"])
        self.ax.title.set_color(COLORS["ink"])
        for spine in self.ax.spines.values():
            spine.set_edgecolor(COLORS["rule"])
    
    def _remove_colorbar(self):
        """在重建坐标轴前移除旧颜色条，避免重复创建额外坐标轴。"""
        if hasattr(self, '_colorbar') and self._colorbar:
            self._colorbar.remove()
            self._colorbar = None

    def _invalidate_result_cache(self):
        """坐标轴被清空后，缓存的 artist 已失效，必须一并清除。

        否则 plot_result 的快速路径会去操作已经被 ax.clear() 销毁的 artist。
        """
        self._cached_result_id = None
        self._cached_points_id = None
        self._artist_cut = None
        self._artist_fill = None
        self._artist_tin = None
        self._artist_contour_lines = []
        self._artist_scatter = None
        self._artist_boundary = None

    def plot_result(self, result: CalculationResult,
                    points: List[SurveyPoint],
                    show_tin: bool = True,
                    show_contour: bool = True,
                    show_points: bool = True,
                    show_boundary: bool = True,
                    show_legend: bool = True):
        """绘制完整计算结果。

        result 未变时只切换 artist 可见性（毫秒级），跳过全量重建。
        result 变化时全量重建并缓存 artist 供后续复用。
        """
        # 用直接 is 比较，避免 id() 在 GC 后复用的误判；points 改变也需重建
        same_result = (
            self._current_result is result
            and self._cached_points_id == id(points)
        )

        if same_result:
            # -------- 快速路径：切换可见性 --------
            if self._artist_cut is not None:
                self._artist_cut.set_visible(True)
            if self._artist_fill is not None:
                self._artist_fill.set_visible(True)
            if self._artist_tin is not None:
                self._artist_tin.set_visible(show_tin)
            for line in self._artist_contour_lines:
                line.set_visible(show_contour)
            if self._artist_scatter is not None:
                self._artist_scatter.set_visible(show_points)
                if self._colorbar is not None:
                    self._colorbar.ax.set_visible(show_points)
            if self._artist_boundary is not None:
                self._artist_boundary.set_visible(show_boundary)
            # 图例同步
            legend = self.ax.get_legend()
            if legend:
                legend.set_visible(show_legend)
            if self.canvas:
                self.canvas.draw_idle()
            return

        # -------- 全量重建路径 --------
        self._remove_colorbar()
        self.ax.clear()
        self._style_axes()
        self._current_result = result
        self._cached_points_id = id(points)
        self._artist_cut = None
        self._artist_fill = None
        self._artist_tin = None
        self._artist_contour_lines = []
        self._artist_scatter = None
        self._artist_boundary = None

        # 1. 绘制三角形 (挖填分色)
        cut_patches = []
        fill_patches = []
        legend_handles = []

        for tri in result.triangles:
            if tri.is_boundary:
                continue
            verts = np.array([[p.x, p.y] for p in tri.vertex_points])
            poly = Polygon(verts, closed=True)
            if tri.is_mixed:
                for sub_tri in tri.sub_triangles:
                    if not sub_tri.vertex_points:
                        continue
                    sub_verts = np.array([[p.x, p.y] for p in sub_tri.vertex_points])
                    sub_poly = Polygon(sub_verts, closed=True)
                    avg_dz = np.mean([p.delta_z for p in sub_tri.vertex_points])
                    if avg_dz > 0:
                        cut_patches.append(sub_poly)
                    else:
                        fill_patches.append(sub_poly)
            else:
                if tri.volume > 0:
                    cut_patches.append(poly)
                else:
                    fill_patches.append(poly)

        if cut_patches:
            pc = PatchCollection(cut_patches, facecolor=self.colors['cut'],
                                 edgecolor=self.colors['tin'], alpha=0.7, linewidth=0.5)
            self.ax.add_collection(pc)
            self._artist_cut = pc
            legend_handles.append(Patch(facecolor=self.colors['cut'], edgecolor=self.colors['tin'], label='挖方区'))
        if fill_patches:
            pc = PatchCollection(fill_patches, facecolor=self.colors['fill'],
                                 edgecolor=self.colors['tin'], alpha=0.7, linewidth=0.5)
            self.ax.add_collection(pc)
            self._artist_fill = pc
            legend_handles.append(Patch(facecolor=self.colors['fill'], edgecolor=self.colors['tin'], label='填方区'))

        # 2. TIN 网格
        segments = []
        for tri in result.triangles:
            if tri.is_boundary:
                continue
            verts = np.array([[p.x, p.y] for p in tri.vertex_points])
            segments.extend(((verts[0], verts[1]), (verts[1], verts[2]), (verts[2], verts[0])))
        if segments:
            lc = LineCollection(segments, colors=self.colors['tin'], linewidths=0.3, alpha=0.5,
                                visible=show_tin)
            self.ax.add_collection(lc)
            self._artist_tin = lc

        # 3. 零填挖线 (混合三角形分割线) — 批量绘制为单一 LineCollection
        contour_segs = []
        for tri in result.triangles:
            if tri.is_mixed and tri.sub_triangles:
                for i, sub1 in enumerate(tri.sub_triangles):
                    for sub2 in tri.sub_triangles[i + 1:]:
                        pts1 = [(p.x, p.y) for p in sub1.vertex_points]
                        pts2 = [(p.x, p.y) for p in sub2.vertex_points]
                        shared = list(set(pts1) & set(pts2))
                        if len(shared) == 2:
                            contour_segs.append(shared)
        if contour_segs:
            clc = LineCollection(contour_segs, colors=self.colors['zero'], linewidths=2,
                                 zorder=6, visible=show_contour)
            self.ax.add_collection(clc)
            self._artist_contour_lines = [clc]

        # 4. 测量点
        if points:
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            zs = [p.z for p in points]
            scatter = self.ax.scatter(xs, ys, c=zs, cmap='terrain',
                                      s=20, edgecolors='white', linewidth=0.5,
                                      zorder=5, label='测量点', visible=show_points)
            self._artist_scatter = scatter
            self._colorbar = self.fig.colorbar(scatter, ax=self.ax, shrink=0.8, label='实测高程 (m)')
            if not show_points:
                self._colorbar.ax.set_visible(False)

        # 5. 边界
        if result.boundary_points:
            bx = [p[0] for p in result.boundary_points] + [result.boundary_points[0][0]]
            by = [p[1] for p in result.boundary_points] + [result.boundary_points[0][1]]
            line, = self.ax.plot(bx, by, color=self.colors['boundary'], linewidth=2.5,
                                 linestyle='--', label='计算边界', zorder=10,
                                 visible=show_boundary)
            self._artist_boundary = line

        # 6. 选中三角形高亮
        if self.selected_triangle:
            self.highlight_triangle(self.selected_triangle)

        # 图例 & 轴标签
        if show_legend:
            handles, labels = self.ax.get_legend_handles_labels()
            handles = legend_handles + handles
            if handles:
                self.ax.legend(handles=handles, loc='upper right', fontsize=9, framealpha=0.9)

        self.ax.set_aspect('equal')
        self.ax.set_xlabel('X 坐标 (m)', fontsize=10)
        self.ax.set_ylabel('Y 坐标 (m)', fontsize=10)
        self.ax.set_title(
            f'土方计算结果 - 挖方:{result.total_cut:.1f}m³  填方:{result.total_fill:.1f}m³  净:{result.net_volume:.1f}m³',
            fontsize=11, pad=10)
        self.ax.grid(True, linestyle=':', alpha=0.3)

        if self.canvas:
            self.canvas.draw_idle()

    def _plot_zero_contours(self, tri: Triangle):
        """绘制混合三角形的零填挖线"""
        # 简化：绘制子三角形之间的分割边
        for i, sub1 in enumerate(tri.sub_triangles):
            for sub2 in tri.sub_triangles[i+1:]:
                # 检查是否共享边 (有两个相同顶点)
                pts1 = [(p.x, p.y) for p in sub1.vertex_points]
                pts2 = [(p.x, p.y) for p in sub2.vertex_points]
                shared = set(pts1) & set(pts2)
                if len(shared) == 2:
                    pts = list(shared)
                    self.ax.plot([pts[0][0], pts[1][0]], [pts[0][1], pts[1][1]],
                               color=self.colors['zero'], linewidth=2, zorder=6)
                    
    def highlight_triangle(self, tri: Triangle):
        """高亮显示三角形"""
        if not tri.vertex_points:
            return
        verts = np.array([[p.x, p.y] for p in tri.vertex_points])
        poly = Polygon(verts, closed=True, facecolor='none', 
                      edgecolor=self.colors['selected'], linewidth=3, zorder=10)
        self.ax.add_patch(poly)
        
        # 显示三角形信息
        cx = np.mean(verts[:, 0])
        cy = np.mean(verts[:, 1])
        info = f"Δ{tri.id}\n面积:{tri.area:.1f}m²\n挖:{tri.cut_volume:.1f} 填:{tri.fill_volume:.1f}"
        self.ax.annotate(info, (cx, cy), fontsize=8, 
                        bbox=dict(boxstyle='round,pad=0.3', facecolor=COLORS["zero"],
                                  alpha=0.9, edgecolor=self.colors['selected']),
                        ha='center', va='center', zorder=11, color=COLORS["white"])
        
    def plot_points_only(self, points: List[SurveyPoint]):
        """仅绘制测量点 (用于数据导入预览)"""
        self._remove_colorbar()
        self.ax.clear()
        self._style_axes()
        self._invalidate_result_cache()
        if not points:
            self.ax.text(0.5, 0.5, '无数据', ha='center', va='center',
                         transform=self.ax.transAxes, color=COLORS["dim"])
            if self.canvas:
                self.canvas.draw_idle()
            return
            
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        zs = [p.z for p in points]
        
        scatter = self.ax.scatter(xs, ys, c=zs, cmap='terrain', s=30,
                                edgecolors='white', linewidth=0.5)
        self._colorbar = self.fig.colorbar(scatter, ax=self.ax, label='高程 (m)')
        
        # 标注点号
        for p in points[::max(1, len(points)//50)]:  # 避免太多标注
            self.ax.annotate(p.id, (p.x, p.y), fontsize=7, 
                           xytext=(2, 2), textcoords='offset points')
            
        self.ax.set_aspect('equal')
        self.ax.set_xlabel('X 坐标 (m)')
        self.ax.set_ylabel('Y 坐标 (m)')
        self.ax.set_title(f'测量点分布 - 共 {len(points)} 个点')
        self.ax.grid(True, linestyle=':', alpha=0.3)
        if self.canvas:
            self.canvas.draw_idle()

    def plot_boundary_edit(self, points: List[SurveyPoint], 
                           boundary: List[Tuple[float, float]],
                           current_point: Optional[Tuple[float, float]] = None):
        """边界编辑模式绘制"""
        self._remove_colorbar()
        self.ax.clear()
        self._style_axes()
        self._invalidate_result_cache()
        self._boundary_preview_artists = None
        if points:
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            self.ax.scatter(xs, ys, c=self.colors['points'], s=10, alpha=0.45)
            
        # 绘制边界
        if boundary:
            bx = [p[0] for p in boundary]
            by = [p[1] for p in boundary]
            self.ax.plot(
                bx, by,
                color=self.colors['boundary'],
                marker='o',
                linewidth=2,
                markersize=6,
                label='边界',
            )
            
            # 闭合预览线
            if len(boundary) >= 2:
                self.ax.plot(
                    [boundary[-1][0], boundary[0][0]],
                    [boundary[-1][1], boundary[0][1]],
                    color=self.colors['boundary'],
                    linestyle='--',
                    linewidth=1,
                    alpha=0.5,
                )
                           
        # 当前正在添加的点由可复用 artist 绘制，避免鼠标移动时不断创建新对象。
                           
        self.ax.set_aspect('equal')
        self.ax.set_xlabel('X 坐标 (m)')
        self.ax.set_ylabel('Y 坐标 (m)')
        self.ax.set_title('边界编辑模式 - 左键添加点，右键结束')
        handles, labels = self.ax.get_legend_handles_labels()
        if labels:
            self.ax.legend()
        self.ax.grid(True, linestyle=':', alpha=0.3)
        self._boundary_preview_artists = self._create_boundary_preview_artists()
        if current_point:
            self.update_boundary_preview(boundary, current_point)
        if self.canvas:
            self.canvas.draw_idle()

    def _create_boundary_preview_artists(self):
        marker, = self.ax.plot(
            [], [],
            color=self.colors['cut'],
            marker='o',
            markersize=8,
            linestyle='None',
            visible=False,
        )
        guide, = self.ax.plot(
            [], [],
            color=self.colors['cut'],
            linestyle='--',
            linewidth=1,
            visible=False,
        )
        return marker, guide

    def update_boundary_preview(self, boundary, current_point):
        """只更新临时预览 artist，避免鼠标移动时重绘整张图。"""
        if not self._boundary_preview_artists:
            return
        marker, guide = self._boundary_preview_artists
        if current_point:
            marker.set_data([current_point[0]], [current_point[1]])
            marker.set_visible(True)
            if boundary:
                guide.set_data([boundary[-1][0], current_point[0]], [boundary[-1][1], current_point[1]])
                guide.set_visible(True)
        else:
            marker.set_visible(False)
            guide.set_visible(False)
        if self.canvas:
            self.canvas.draw_idle()

    def close(self):
        """断开画布事件并释放嵌入式 Figure。"""
        if self.canvas:
            for connection_id in self._connection_ids:
                self.canvas.mpl_disconnect(connection_id)
        self._connection_ids.clear()
        self._remove_colorbar()
        self.fig.clear()
        self.canvas = None
        self.toolbar = None

    def clear(self):
        """清空绘图"""
        self._remove_colorbar()
        self.ax.clear()
        self._style_axes()
        self._invalidate_result_cache()
        self.selected_triangle = None
        self._boundary_preview_artists = None
        if self.canvas:
            self.canvas.draw_idle()


def create_standalone_figure(result: CalculationResult, 
                             points: List[SurveyPoint],
                             boundary: List[Tuple[float, float]],
                             design_elevation: float,
                             project_name: str) -> Figure:
    """创建独立的 matplotlib 图形用于导出高清图片"""
    fig, ax = plt.subplots(figsize=(12, 10), dpi=150)
    fig.patch.set_facecolor(COLORS["sheet"])
    ax.set_facecolor(COLORS["sheet"])
    
    colors = dict(PLOT_COLORS)
    
    # 绘制挖填分色
    cut_patches, fill_patches = [], []
    for tri in result.triangles:
        if tri.is_boundary:
            continue
        verts = np.array([[p.x, p.y] for p in tri.vertex_points])
        poly = Polygon(verts, closed=True)
        if tri.is_mixed:
            for sub_tri in tri.sub_triangles:
                if not sub_tri.vertex_points:
                    continue
                sub_verts = np.array([[p.x, p.y] for p in sub_tri.vertex_points])
                sub_poly = Polygon(sub_verts, closed=True)
                avg_dz = np.mean([p.delta_z for p in sub_tri.vertex_points])
                if avg_dz > 0:
                    cut_patches.append(sub_poly)
                else:
                    fill_patches.append(sub_poly)
        else:
            if tri.volume > 0:
                cut_patches.append(poly)
            else:
                fill_patches.append(poly)
                
    if cut_patches:
        pc = PatchCollection(cut_patches, facecolor=colors['cut'], edgecolor=colors['tin'], 
                           alpha=0.7, linewidth=0.3, label='挖方区')
        ax.add_collection(pc)
    if fill_patches:
        pc = PatchCollection(fill_patches, facecolor=colors['fill'], edgecolor=colors['tin'],
                           alpha=0.7, linewidth=0.3, label='填方区')
        ax.add_collection(pc)
        
    # TIN 网格
    segments = []
    for tri in result.triangles:
        if tri.is_boundary:
            continue
        verts = np.array([[p.x, p.y] for p in tri.vertex_points])
        segments.extend(((verts[0], verts[1]), (verts[1], verts[2]), (verts[2], verts[0])))
    if segments:
        ax.add_collection(LineCollection(
            segments, colors=colors['tin'], linewidths=0.2, alpha=0.4
        ))
              
    # 零填挖线
    for tri in result.triangles:
        if tri.is_mixed and tri.sub_triangles:
            for i, sub1 in enumerate(tri.sub_triangles):
                for sub2 in tri.sub_triangles[i+1:]:
                    pts1 = [(p.x, p.y) for p in sub1.vertex_points]
                    pts2 = [(p.x, p.y) for p in sub2.vertex_points]
                    shared = set(pts1) & set(pts2)
                    if len(shared) == 2:
                        pts = list(shared)
                        ax.plot([pts[0][0], pts[1][0]], [pts[0][1], pts[1][1]],
                              color=colors['zero'], linewidth=1.5, zorder=6)
                        
    # 测量点
    if points:
        xs, ys, zs = zip(*[(p.x, p.y, p.z) for p in points])
        ax.scatter(xs, ys, c=zs, cmap='terrain', s=15, edgecolors='white', linewidth=0.3, zorder=5)
        
    # 边界
    if boundary:
        bx = [p[0] for p in boundary] + [boundary[0][0]]
        by = [p[1] for p in boundary] + [boundary[0][1]]
        ax.plot(bx, by, color=colors['boundary'], linewidth=3, linestyle='--', label='计算边界', zorder=10)
        
    ax.set_aspect('equal')
    ax.set_xlabel('X 坐标 (m)', fontsize=12)
    ax.set_ylabel('Y 坐标 (m)', fontsize=12)
    ax.set_title(f'{project_name}\n挖方:{result.total_cut:.1f}m³  填方:{result.total_fill:.1f}m³  净:{result.net_volume:.1f}m³  设计高程:{design_elevation}m', 
                fontsize=13, pad=15)
    ax.legend(loc='upper right', fontsize=11, framealpha=0.95)
    ax.grid(True, linestyle=':', alpha=0.3)
    
    fig.tight_layout()
    return fig