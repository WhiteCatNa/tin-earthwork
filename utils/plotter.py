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
import logging
import matplotlib.font_manager as fm
import platform


class _ExpectedAspectAdjustment(logging.Filter):
    """边界页缩放后视野是固定的，窗口大小一变，matplotlib 会微调显示范围来保持 1:1 比例，
    并为此记一条日志。这正是想要的行为，不必每次都打印。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return "to fulfill fixed data aspect" not in record.getMessage()


logging.getLogger("matplotlib.axes._base").addFilter(_ExpectedAspectAdjustment())

def setup_chinese_font():
    """配置中文 UI 字体，并保留拉丁字体以渲染 m²/m³ 上标。"""
    plt.rcParams['axes.unicode_minus'] = False
    system = platform.system()
    if system == 'Darwin':
        preferred = ['PingFang SC', 'Hiragino Sans GB', 'Heiti SC', 'Songti SC']
    elif system == 'Windows':
        preferred = ['Microsoft YaHei', 'SimHei', 'SimSun']
    else:
        preferred = ['Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'WenQuanYi Zen Hei', 'Droid Sans Fallback']

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
        self._boundary_artists = None      # 边界线、闭合线、起点、选中标记、顶点序号
        self._point_label_artists: list = []
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
        self._result_legend = None   # 结果图的图例（图级，放在坐标轴下方）
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
        if self._result_legend is not None:
            self._result_legend.remove()
            self._result_legend = None

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
            if self._result_legend is not None:
                self._result_legend.set_visible(show_legend)
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
            self._colorbar = self.fig.colorbar(self._artist_scatter, ax=self.ax, shrink=0.8,
                                               label=f'{natural_label(result)} (m)')
            self._colorbar.ax.set_visible(show_points)
        if self._artist_boundary is not None:
            self._artist_boundary.set_visible(show_boundary)

        if self.selected_triangle is not None and self.selected_triangle in self._pick_tris:
            self.highlight_triangle(self.selected_triangle)
        else:
            self.selected_triangle = None

        if show_legend and artists["legend"]:
            # 图例放在坐标轴下方一行，不遮挡边界角上的挖填区域。用图级图例的 outside 位置，
            # 由约束布局给它留出高度；固定偏移在图较矮时会和 X 轴标题叠在一起
            self._result_legend = self.fig.legend(
                handles=artists["legend"], loc='outside lower center',
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

    # 边界顶点多于这个数时（如圆弧折线化后的边界）缩小顶点标记、不再标序号
    BOUNDARY_MARKER_LIMIT = 80
    BOUNDARY_NUMBER_LIMIT = 40
    # 视野内的测点不超过这个数时才标点号，避免全图挤成一片
    MAX_POINT_LABELS = 150

    def plot_boundary_edit(self, points: List[SurveyPoint],
                           boundary: List[Tuple[float, float]],
                           current_point: Optional[Tuple[float, float]] = None,
                           view: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None,
                           selected: Optional[int] = None):
        """边界编辑模式绘制。view 为 (xlim, ylim) 时保持这个视野，否则显示全图。"""
        self._remove_colorbar()
        self.ax.clear()
        self._style_axes()
        self._invalidate_result_cache()
        self._boundary_preview_artists = None
        self._boundary_artists = None
        self._point_label_artists = []
        if points:
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            self.ax.scatter(xs, ys, c=self.colors['points'], s=10, alpha=0.45)

        # 绘制边界
        if boundary:
            bx = [p[0] for p in boundary]
            by = [p[1] for p in boundary]
            line, = self.ax.plot(
                bx, by,
                color=self.colors['boundary'],
                marker='o',
                linewidth=2,
                markersize=6 if len(boundary) <= self.BOUNDARY_MARKER_LIMIT else 3,
                label='边界',
            )
            # 闭合预览线
            closing, = self.ax.plot(
                [], [],
                color=self.colors['boundary'],
                linestyle='--',
                linewidth=1,
                alpha=0.5,
            )
            # 起点用方形标出，便于看出连线方向
            start, = self.ax.plot(
                [], [],
                color=self.colors['boundary'],
                marker='s',
                markersize=9,
                linestyle='None',
            )
            chosen, = self.ax.plot(
                [], [],
                marker='o',
                markersize=13,
                markerfacecolor='none',
                markeredgecolor=self.colors['selected'],
                markeredgewidth=2,
                linestyle='None',
            )
            numbers = []
            if len(boundary) <= self.BOUNDARY_NUMBER_LIMIT:
                numbers = [
                    self.ax.annotate(
                        str(index), point, xytext=(6, 6), textcoords='offset points',
                        fontsize=8, color=self.colors['boundary'], fontweight='bold',
                    )
                    for index, point in enumerate(boundary, 1)
                ]
            self._boundary_artists = (line, closing, start, chosen, numbers)
            self.update_boundary_line(boundary, selected, redraw=False)

        # 当前正在添加的点由可复用 artist 绘制，避免鼠标移动时不断创建新对象。

        # datalim：坐标轴铺满绘图区，比例靠调整显示范围保持，缩放、平移时不留白边
        self.ax.set_aspect('equal', adjustable='datalim')
        self.ax.set_xlabel('X 坐标 (m)')
        self.ax.set_ylabel('Y 坐标 (m)')
        handles, labels = self.ax.get_legend_handles_labels()
        if labels:
            self.ax.legend()
        self.ax.grid(True, linestyle=':', alpha=0.3)
        self._boundary_preview_artists = self._create_boundary_preview_artists()
        if view is not None:
            self.ax.set_xlim(view[0])
            self.ax.set_ylim(view[1])
        if current_point:
            self.update_boundary_preview(boundary, current_point)
        if self.canvas:
            self.canvas.draw_idle()

    def update_boundary_line(self, boundary, selected: Optional[int] = None, redraw: bool = True):
        """只更新边界线、起点、序号和选中标记（拖动顶点时用），不重绘测点。"""
        if not self._boundary_artists or not boundary:
            return
        line, closing, start, chosen, numbers = self._boundary_artists
        line.set_data([p[0] for p in boundary], [p[1] for p in boundary])
        if len(boundary) >= 2:
            closing.set_data([boundary[-1][0], boundary[0][0]], [boundary[-1][1], boundary[0][1]])
        else:
            closing.set_data([], [])
        start.set_data([boundary[0][0]], [boundary[0][1]])
        if selected is not None and 0 <= selected < len(boundary):
            chosen.set_data([boundary[selected][0]], [boundary[selected][1]])
        else:
            chosen.set_data([], [])
        for label, point in zip(numbers, boundary):
            label.xy = point
        if redraw and self.canvas:
            self.canvas.draw_idle()

    def update_point_labels(self, xy: np.ndarray, ids, enabled: bool = True):
        """视野内测点不多时标出点号（放大后才出现），供“按点号连线”对照。"""
        for artist in self._point_label_artists:
            artist.remove()
        self._point_label_artists = []
        if enabled and xy is not None and len(xy):
            x0, x1 = sorted(self.ax.get_xlim())
            y0, y1 = sorted(self.ax.get_ylim())
            visible = np.nonzero((xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1))[0]
            if 0 < len(visible) <= self.MAX_POINT_LABELS:
                self._point_label_artists = [
                    self.ax.annotate(
                        str(ids[index]), (xy[index, 0], xy[index, 1]), xytext=(4, -9), textcoords='offset points',
                        fontsize=7, color=COLORS["dim"],
                    )
                    for index in visible
                ]
        if self.canvas:
            self.canvas.draw_idle()

    def get_view(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        return tuple(self.ax.get_xlim()), tuple(self.ax.get_ylim())

    def set_view(self, xlim, ylim):
        self.ax.set_xlim(xlim)
        self.ax.set_ylim(ylim)
        if self.canvas:
            self.canvas.draw_idle()

    def zoom_view(self, x: float, y: float, factor: float):
        """以 (x, y) 为中心缩放，factor > 1 为放大。"""
        (x0, x1), (y0, y1) = self.get_view()
        self.set_view((x - (x - x0) / factor, x + (x1 - x) / factor),
                      (y - (y - y0) / factor, y + (y1 - y) / factor))

    def data_per_pixel(self) -> float:
        """当前视野下一个屏幕像素对应的图上距离（米）；还没画出来时返回 0。"""
        try:
            inverse = self.ax.transData.inverted()
            (ax_, ay), (bx_, by) = inverse.transform([(0.0, 0.0), (1.0, 0.0)])
        except (ValueError, np.linalg.LinAlgError):
            return 0.0
        scale = float(np.hypot(bx_ - ax_, by - ay))
        return scale if np.isfinite(scale) else 0.0

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

    def update_boundary_preview(self, boundary, current_point, anchors=None):
        """只更新临时预览 artist，避免鼠标移动时重绘整张图。

        默认从最后一个边界点连到光标（接着往后加点）；anchors 给出两个点时，
        画成“前一点 → 光标 → 后一点”，表示在这条边上插入。
        """
        if not self._boundary_preview_artists:
            return
        marker, guide = self._boundary_preview_artists
        if current_point:
            marker.set_data([current_point[0]], [current_point[1]])
            marker.set_visible(True)
            if anchors:
                guide.set_data([anchors[0][0], current_point[0], anchors[1][0]],
                               [anchors[0][1], current_point[1], anchors[1][1]])
                guide.set_visible(True)
            elif boundary:
                guide.set_data([boundary[-1][0], current_point[0]], [boundary[-1][1], current_point[1]])
                guide.set_visible(True)
            else:
                guide.set_visible(False)
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


def natural_label(result: CalculationResult) -> str:
    """测点散点的含义：与设计面比较时是实测高程，两期对比时是前期高程。"""
    return "前期高程" if result.is_compare else "实测高程"


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
        fig.colorbar(artists["scatter"], ax=ax, shrink=0.8, label=f'{natural_label(result)} (m)')
    ax.set_title(
        f'{project_name}\n挖方:{result.total_cut:.1f}m³  填方:{result.total_fill:.1f}m³  '
        f'净:{result.net_volume:.1f}m³\n{"比较面" if result.is_compare else "设计高程"}: {result.design_text}',
        fontsize=13, pad=15)
    if artists["legend"]:
        ax.legend(handles=artists["legend"], loc='upper right', fontsize=11, framealpha=0.95)
    fig.tight_layout()
    return fig
