"""
可视化模块：matplotlib 绘图嵌入 Tkinter
"""
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Patch
from matplotlib.collections import PolyCollection, LineCollection
import numpy as np
from typing import List, Tuple, Optional
from core.calculator import SurveyPoint, Triangle, CalculationResult, extract_zero_contour_segments
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
        self._highlight_artists: list = []
        self._pick_xy: Optional[np.ndarray] = None
        self._pick_tris: List[Triangle] = []

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
        if x is None or y is None or self._current_result is None:
            return
        tri = self._find_triangle_at(x, y)
        if tri:
            self.selected_triangle = tri
            self.highlight_triangle(tri)
            if self.canvas:
                self.canvas.draw_idle()
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

    def _find_triangle_at(self, x: float, y: float) -> Optional[Triangle]:
        """向量化查找包含点 (x, y) 的三角形。"""
        if self._pick_xy is None or not len(self._pick_xy):
            return None
        p0, p1, p2 = self._pick_xy[:, 0], self._pick_xy[:, 1], self._pick_xy[:, 2]
        denom = (p1[:, 1] - p2[:, 1]) * (p0[:, 0] - p2[:, 0]) + (p2[:, 0] - p1[:, 0]) * (p0[:, 1] - p2[:, 1])
        with np.errstate(divide="ignore", invalid="ignore"):
            a = ((p1[:, 1] - p2[:, 1]) * (x - p2[:, 0]) + (p2[:, 0] - p1[:, 0]) * (y - p2[:, 1])) / denom
            b = ((p2[:, 1] - p0[:, 1]) * (x - p2[:, 0]) + (p0[:, 0] - p2[:, 0]) * (y - p2[:, 1])) / denom
        c = 1 - a - b
        hits = np.nonzero((np.abs(denom) > 1e-12) & (a >= 0) & (b >= 0) & (c >= 0))[0]
        return self._pick_tris[hits[0]] if len(hits) else None

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
        self._highlight_artists = []
        self._pick_xy = None
        self._pick_tris = []

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
        self._invalidate_result_cache()
        self._current_result = result
        self._cached_points_id = id(points)

        artists = draw_result(self.ax, result, points, result.boundary_points, self.colors, fine=False)
        self._artist_cut = artists["cut"]
        self._artist_fill = artists["fill"]
        self._artist_tin = artists["tin"]
        self._artist_contour_lines = [artists["zero"]] if artists["zero"] is not None else []
        self._artist_scatter = artists["scatter"]
        self._artist_boundary = artists["boundary"]
        self._pick_tris = artists["pick_tris"]
        self._pick_xy = artists["pick_xy"]

        if self._artist_tin is not None:
            self._artist_tin.set_visible(show_tin)
        for line in self._artist_contour_lines:
            line.set_visible(show_contour)
        if self._artist_scatter is not None:
            self._artist_scatter.set_visible(show_points)
            self._colorbar = self.fig.colorbar(self._artist_scatter, ax=self.ax, shrink=0.8, label='实测高程 (m)')
            self._colorbar.ax.set_visible(show_points)
        if self._artist_boundary is not None:
            self._artist_boundary.set_visible(show_boundary)

        if self.selected_triangle is not None and self.selected_triangle in self._pick_tris:
            self.highlight_triangle(self.selected_triangle)
        else:
            self.selected_triangle = None

        if show_legend and artists["legend"]:
            # 图例放在图下方一行，不遮挡边界角上的挖填区域
            self.ax.legend(handles=artists["legend"], loc='upper center', bbox_to_anchor=(0.5, -0.09),
                           ncol=len(artists["legend"]), fontsize=9, frameon=False)
        self.ax.set_title(
            f'土方计算结果 - 挖方:{result.total_cut:.1f}m³  填方:{result.total_fill:.1f}m³  净:{result.net_volume:.1f}m³',
            fontsize=11, pad=10)

        if self.canvas:
            self.canvas.draw_idle()

    def highlight_triangle(self, tri: Triangle):
        """高亮显示三角形（只保留一个高亮）"""
        for artist in self._highlight_artists:
            try:
                artist.remove()
            except (ValueError, NotImplementedError):
                pass
        self._highlight_artists = []
        if not tri.vertex_points:
            return
        verts = np.array([[p.x, p.y] for p in tri.vertex_points])
        poly = Polygon(verts, closed=True, facecolor='none',
                       edgecolor=self.colors['selected'], linewidth=3, zorder=10)
        self.ax.add_patch(poly)

        cx = np.mean(verts[:, 0])
        cy = np.mean(verts[:, 1])
        info = f"Δ{tri.id}\n面积:{tri.area:.1f}m²\n挖:{tri.cut_volume:.1f} 填:{tri.fill_volume:.1f}"
        note = self.ax.annotate(info, (cx, cy), fontsize=8,
                                bbox=dict(boxstyle='round,pad=0.3', facecolor=COLORS["zero"],
                                          alpha=0.9, edgecolor=self.colors['selected']),
                                ha='center', va='center', zorder=11, color=COLORS["white"])
        self._highlight_artists = [poly, note]

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


RASTERIZE_TRIANGLES = 5000


def _result_geometry(result: CalculationResult):
    """整理绘图用的几何数据：挖方/填方多边形、TIN 三角形、零填挖线。"""
    cut, fill, mesh, tris = [], [], [], []
    for tri in result.triangles:
        if tri.is_boundary:
            continue
        verts = [(p.x, p.y) for p in tri.vertex_points]
        mesh.append(verts)
        tris.append(tri)
        if tri.cut_polygon or tri.fill_polygon:
            if len(tri.cut_polygon) >= 3:
                cut.append(tri.cut_polygon)
            if len(tri.fill_polygon) >= 3:
                fill.append(tri.fill_polygon)
        elif tri.volume > 0:
            cut.append(verts)
        else:
            fill.append(verts)
    mesh_xy = np.array(mesh, dtype=float).reshape(-1, 3, 2)
    return cut, fill, mesh_xy, tris, extract_zero_contour_segments(result)


def draw_result(ax, result: CalculationResult, points: List[SurveyPoint],
                boundary: List[Tuple[float, float]], colors: dict, fine: bool) -> dict:
    """在坐标轴上绘制计算结果；界面图与导出图共用。fine=True 为导出用的细线条样式。"""
    cut, fill, mesh_xy, tris, zero = _result_geometry(result)
    edge_width = 0.3 if fine else 0.5
    # 导出 PDF/SVG 时，大量三角形按栅格嵌入，文字、边界和零线仍为矢量
    rasterize = fine and len(mesh_xy) > RASTERIZE_TRIANGLES
    artists = {"cut": None, "fill": None, "tin": None, "zero": None, "scatter": None,
               "boundary": None, "legend": [], "pick_tris": tris, "pick_xy": mesh_xy}

    clip_patch = None
    if boundary:
        clip_patch = Polygon(np.asarray(boundary, dtype=float), closed=True, transform=ax.transData)

    for key, polygons, label in (("cut", cut, "挖方区"), ("fill", fill, "填方区")):
        if not polygons:
            continue
        collection = PolyCollection(polygons, facecolor=colors[key], edgecolor=colors['tin'],
                                    alpha=0.7, linewidth=edge_width)
        collection.set_rasterized(rasterize)
        ax.add_collection(collection)
        artists[key] = collection
        artists["legend"].append(Patch(facecolor=colors[key], edgecolor=colors['tin'], label=label))

    if len(mesh_xy):
        segments = np.concatenate([mesh_xy[:, [0, 1]], mesh_xy[:, [1, 2]], mesh_xy[:, [2, 0]]])
        tin = LineCollection(segments, colors=colors['tin'], linewidths=0.2 if fine else 0.3,
                             alpha=0.4 if fine else 0.5)
        tin.set_rasterized(rasterize)
        ax.add_collection(tin)
        if clip_patch is not None:
            tin.set_clip_path(clip_patch)
        artists["tin"] = tin

    if zero:
        artists["zero"] = LineCollection(zero, colors=colors['zero'], linewidths=1.5 if fine else 2, zorder=6)
        ax.add_collection(artists["zero"])
        artists["legend"].append(Line2D([], [], color=colors['zero'], linewidth=2, label='零填挖线'))

    if points:
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        zs = [p.z for p in points]
        artists["scatter"] = ax.scatter(xs, ys, c=zs, cmap='terrain', s=15 if fine else 20,
                                        edgecolors='white', linewidth=0.3 if fine else 0.5, zorder=5,
                                        rasterized=rasterize)

    if boundary:
        bx = [p[0] for p in boundary] + [boundary[0][0]]
        by = [p[1] for p in boundary] + [boundary[0][1]]
        artists["boundary"], = ax.plot(bx, by, color=colors['boundary'], linewidth=3 if fine else 2.5,
                                       linestyle='--', label='计算边界', zorder=10)
        artists["legend"].append(artists["boundary"])

    ax.set_aspect('equal')
    ax.autoscale_view()
    ax.set_xlabel('X 坐标 (m)', fontsize=12 if fine else 10)
    ax.set_ylabel('Y 坐标 (m)', fontsize=12 if fine else 10)
    ax.grid(True, linestyle=':', alpha=0.3)
    return artists


def create_standalone_figure(result: CalculationResult,
                             points: List[SurveyPoint],
                             boundary: List[Tuple[float, float]],
                             project_name: str) -> Figure:
    """创建独立图形用于导出（不经过 pyplot，可在后台线程使用）"""
    setup_chinese_font()
    fig = Figure(figsize=(12, 10), dpi=150, facecolor=COLORS["sheet"])
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(COLORS["sheet"])
    artists = draw_result(ax, result, points, boundary, dict(PLOT_COLORS), fine=True)
    if artists["scatter"] is not None:
        fig.colorbar(artists["scatter"], ax=ax, shrink=0.8, label='实测高程 (m)')
    ax.set_title(
        f'{project_name}\n挖方:{result.total_cut:.1f}m³  填方:{result.total_fill:.1f}m³  '
        f'净:{result.net_volume:.1f}m³  设计高程:{result.design_text}',
        fontsize=13, pad=15)
    if artists["legend"]:
        ax.legend(handles=artists["legend"], loc='upper right', fontsize=11, framealpha=0.95)
    fig.tight_layout()
    return fig
