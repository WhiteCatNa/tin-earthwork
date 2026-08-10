import warnings

import matplotlib

matplotlib.use("Agg")

from core.calculator import SurveyPoint
from utils.plotter import EarthworkPlotter


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
