import io
import warnings

import numpy as np

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


def _navigable_plotter():
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    plotter = EarthworkPlotter()
    canvas = FigureCanvasAgg(plotter.fig)
    plotter.bind_canvas(canvas, enable_hover=False, enable_navigation=True)
    result, points = _mixed_result()
    plotter.plot_result(result, points)
    plotter._plotted_points = points
    canvas.draw()
    return plotter, canvas


def _fire(canvas, ax, name, x, y, button=None, step=0):
    from matplotlib.backend_bases import MouseEvent

    px, py = ax.transData.transform((x, y))
    event = MouseEvent(name, canvas, px, py, button=button, step=step)
    canvas.callbacks.process(name, event)


def _width(lim):
    return lim[1] - lim[0]


def test_scroll_zooms_around_cursor_and_reset_restores_view():
    plotter, canvas = _navigable_plotter()
    ax = plotter.ax
    home_x, home_y = ax.get_xlim(), ax.get_ylim()

    _fire(canvas, ax, "scroll_event", 3, 4, step=1)
    zoomed_x, zoomed_y = ax.get_xlim(), ax.get_ylim()
    assert _width(zoomed_x) < _width(home_x)
    assert _width(zoomed_y) < _width(home_y)
    # 鼠标所在点在缩放前后保持同一相对位置
    assert np.isclose((3 - home_x[0]) / _width(home_x), (3 - zoomed_x[0]) / _width(zoomed_x))
    assert np.isclose((4 - home_y[0]) / _width(home_y), (4 - zoomed_y[0]) / _width(zoomed_y))

    _fire(canvas, ax, "scroll_event", 3, 4, step=-2)
    assert _width(ax.get_xlim()) > _width(home_x)

    plotter.reset_view()
    assert np.allclose(ax.get_xlim(), home_x)
    assert np.allclose(ax.get_ylim(), home_y)


def test_left_drag_pans_without_selecting_triangle():
    from matplotlib.backend_bases import MouseButton

    plotter, canvas = _navigable_plotter()
    ax = plotter.ax
    home_x, home_y = ax.get_xlim(), ax.get_ylim()
    clicked = []
    plotter.on_triangle_click = clicked.append

    _fire(canvas, ax, "button_press_event", 3, 4, MouseButton.LEFT)
    _fire(canvas, ax, "motion_notify_event", 5, 5)
    _fire(canvas, ax, "button_release_event", 5, 5, MouseButton.LEFT)

    new_x, new_y = ax.get_xlim(), ax.get_ylim()
    assert np.allclose(np.subtract(new_x, home_x), -2, atol=0.05)
    assert np.allclose(np.subtract(new_y, home_y), -1, atol=0.05)
    assert np.isclose(_width(new_x), _width(home_x))
    assert clicked == []


def test_left_click_without_drag_still_selects_triangle():
    from matplotlib.backend_bases import MouseButton

    plotter, canvas = _navigable_plotter()
    ax = plotter.ax
    home_x = ax.get_xlim()
    clicked = []
    plotter.on_triangle_click = clicked.append

    _fire(canvas, ax, "button_press_event", 2, 3, MouseButton.LEFT)
    _fire(canvas, ax, "button_release_event", 2, 3, MouseButton.LEFT)

    assert len(clicked) == 1 and not clicked[0].is_boundary
    assert np.allclose(ax.get_xlim(), home_x)


def test_toggling_layers_keeps_zoomed_view():
    plotter, canvas = _navigable_plotter()
    ax = plotter.ax
    result, points = plotter._current_result, plotter._plotted_points
    plotter.zoom_at(5, 5, 0.5)
    zoomed_x = ax.get_xlim()

    plotter.plot_result(result, points, show_tin=False)
    assert np.allclose(ax.get_xlim(), zoomed_x)


def test_double_click_resets_view():
    from matplotlib.backend_bases import MouseButton, MouseEvent

    plotter, canvas = _navigable_plotter()
    ax = plotter.ax
    home_x = ax.get_xlim()
    plotter.zoom_at(5, 5, 0.5)

    px, py = ax.transData.transform((5, 5))
    event = MouseEvent("button_press_event", canvas, px, py, button=MouseButton.LEFT, dblclick=True)
    canvas.callbacks.process("button_press_event", event)
    assert np.allclose(ax.get_xlim(), home_x)
