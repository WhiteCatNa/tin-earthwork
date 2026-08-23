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
