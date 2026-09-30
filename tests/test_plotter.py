import io
import warnings

import matplotlib

matplotlib.use("Agg")

from core.calculator import SurveyPoint
from matplotlib.figure import Figure
from utils.plotter import EarthworkPlotter, setup_chinese_font


def test_titles_with_superscripts_do_not_warn_missing_glyphs():
    setup_chinese_font()
    fig = Figure()
    ax = fig.add_subplot(111)
    ax.set_title("挖方 1.0m³ 面积 1.0m²")
    buf = io.BytesIO()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fig.savefig(buf, format="png")
    glyph_warnings = [item for item in caught if "Glyph" in str(item.message)]
    assert glyph_warnings == []


def test_plotter_uses_shared_theme_cut_fill_colors():
    from gui.theme import PLOT_COLORS

    plotter = EarthworkPlotter()

    assert plotter.colors["cut"] == PLOT_COLORS["cut"]
    assert plotter.colors["fill"] == PLOT_COLORS["fill"]
    assert plotter.colors["zero"] == PLOT_COLORS["zero"]
    assert plotter.colors["boundary"] == PLOT_COLORS["boundary"]


def test_boundary_edit_without_data_does_not_create_empty_legend():
    plotter = EarthworkPlotter()

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        plotter.plot_boundary_edit([], [])

    assert plotter.ax.get_legend() is None


def test_boundary_edit_with_boundary_keeps_boundary_legend():
    plotter = EarthworkPlotter()
    points = [SurveyPoint("A", 0, 0, 1)]

    plotter.plot_boundary_edit(points, [(0, 0), (1, 0), (1, 1)])

    legend = plotter.ax.get_legend()
    assert legend is not None
    assert [text.get_text() for text in legend.get_texts()] == ["边界"]


def _mixed_result():
    from core.calculator import TINEarthworkCalculator

    points = [SurveyPoint("A", 0, 0, 1), SurveyPoint("B", 10, 0, -1), SurveyPoint("C", 0, 10, -1),
              SurveyPoint("D", 10, 10, 1)]
    calculator = TINEarthworkCalculator(0)
    calculator.add_points(points)
    calculator.set_boundary([(1, 1), (9, 1), (9, 9), (1, 9)])
    return calculator.run_full_calculation(), points


def test_only_one_triangle_stays_highlighted():
    plotter = EarthworkPlotter()
    result, points = _mixed_result()
    plotter.plot_result(result, points)
    first, second = [tri for tri in result.triangles if not tri.is_boundary][:2]
    plotter.highlight_triangle(first)
    plotter.highlight_triangle(second)
    highlights = [patch for patch in plotter.ax.patches if patch.get_linewidth() == 3]
    assert len(highlights) == 1


def test_click_picks_triangle_under_cursor():
    plotter = EarthworkPlotter()
    result, points = _mixed_result()
    plotter.plot_result(result, points)
    tri = plotter._find_triangle_at(2, 3)
    assert tri is not None and not tri.is_boundary
    assert plotter._find_triangle_at(50, 50) is None


def test_standalone_figure_does_not_leak_pyplot_figures():
    import matplotlib.pyplot as plt
    from utils.plotter import create_standalone_figure

    result, points = _mixed_result()
    before = plt.get_fignums()
    figure = create_standalone_figure(result, points, result.boundary_points, "试验段")
    buf = io.BytesIO()
    figure.savefig(buf, format="png")
    assert plt.get_fignums() == before
    assert "试验段" in figure.axes[0].get_title()


def test_result_legend_sits_below_axis_label_even_on_short_figure():
    """回归：图例用固定偏移放在图下方时，图较矮会和 X 轴标题叠在一起。"""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    plotter = EarthworkPlotter(figsize=(9, 3.2))
    FigureCanvasAgg(plotter.fig)
    result, points = _mixed_result()
    plotter.plot_result(result, points)
    plotter.fig.canvas.draw()
    renderer = plotter.fig.canvas.get_renderer()
    legend_box = plotter._result_legend.get_window_extent(renderer)
    label_box = plotter.ax.xaxis.label.get_window_extent(renderer)
    assert legend_box.y1 <= label_box.y0 + 1
    assert legend_box.y0 >= 0

    # 切回测点图时图例要清掉，再画结果时不能叠出两个
    plotter.plot_points_only(points)
    assert plotter._result_legend is None and plotter.fig.legends == []
    plotter.plot_result(result, points)
    assert len(plotter.fig.legends) == 1
