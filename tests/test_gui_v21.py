"""2.1 界面流程：挖空区、挖填平衡标高、最优斜面、可松性系数、三角网清理、工程存取。"""
import time

import pytest

import main as app_main
from core.calculator import SurveyPoint

BOUNDARY = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
HOLE = [(5.0, 5.0), (10.0, 5.0), (10.0, 10.0), (5.0, 10.0)]


@pytest.fixture
def app(main_window, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda *a, **k: "ok")
    monkeypatch.setattr("tkinter.messagebox.askokcancel", lambda *a, **k: True)
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
    return main_window


def _wait(app, predicate, timeout=30.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待超时")


def _sloped(step=2.5):
    """z = 10 + 0.1·x 的规则格网测点（TIN 对平面精确）。"""
    points = []
    n = int(20 / step) + 1
    for i in range(n):
        for j in range(n):
            x, y = i * step, j * step
            points.append(SurveyPoint(f"P{i}-{j}", x, y, 10 + 0.1 * x))
    return points


class _Click:
    def __init__(self, ax, x, y, button=1):
        self.inaxes, self.xdata, self.ydata, self.button = ax, x, y, button


def _load(app, draw_hole=True):
    app.import_frame.load_points(_sloped())
    app.import_frame._confirm_points()
    bf = app.boundary_frame
    bf.set_boundary(list(BOUNDARY))
    if draw_hole:
        bf.target_var.set("hole")
        bf._on_target_change()
        for x, y in HOLE:
            bf._on_canvas_click(_Click(bf.plotter.ax, x + 0.3, y + 0.3))   # 吸附到测点
        assert bf.drawing_hole == HOLE
        assert bf.boundary == BOUNDARY   # 画挖空区不影响边界
    bf._confirm_boundary()
    return app.calc_frame


def _calculate(app, cf):
    cf.result = None
    cf._run_calculation()
    _wait(app, lambda: cf.result is not None)
    return cf.result


def test_drawn_hole_is_excluded(app):
    cf = _load(app)
    assert app.boundary_frame.holes == [HOLE] and app.boundary_frame.drawing_hole == []
    cf.design_elevation_var.set(10.0)
    result = _calculate(app, cf)
    # ∫0.1x over 20×20 = 400，挖空区 5×5、形心 x = 7.5 → 18.75
    assert result.total_cut == pytest.approx(400 - 18.75, abs=1e-9)
    assert result.holes == [HOLE]
    assert any(row[0] == "挖空区" for row in (cf.tree_summary.item(i)["values"]
                                              for i in cf.tree_summary.get_children()))


def test_invalid_hole_is_kept_for_editing(app, monkeypatch):
    warnings = []
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda title, msg, **k: warnings.append(msg))
    app.import_frame.load_points(_sloped())
    app.import_frame._confirm_points()
    bf = app.boundary_frame
    bf.set_boundary(list(BOUNDARY))
    bf.target_var.set("hole")
    bf._on_target_change()
    bf.drawing_hole = [(15.0, 15.0), (25.0, 15.0), (25.0, 25.0)]
    assert not bf._finish_hole()
    assert "超出了计算边界" in warnings[-1]
    assert bf.drawing_hole and not bf.holes


def test_balance_buttons_fill_inputs(app):
    cf = _load(app, draw_hole=False)
    cf.loose_var.set("1.2")
    cf.compact_var.set("1.0")
    cf._balance_flat()
    _wait(app, lambda: cf._balance_thread is None)
    # 平均高程 = 10 + 0.1·10 = 11
    assert float(cf.design_elevation_var.get()) == pytest.approx(11, abs=1e-3)

    cf.design_mode_var.set("plane")
    cf._on_mode_change()
    cf._best_fit_plane()
    _wait(app, lambda: cf._balance_thread is None)
    assert float(cf.plane_vars["slope_x"].get()) == pytest.approx(10, abs=1e-3)
    assert float(cf.plane_vars["slope_y"].get()) == pytest.approx(0, abs=1e-3)
    result = _calculate(app, cf)
    assert result.total_cut == pytest.approx(0, abs=0.05) and result.total_fill == pytest.approx(0, abs=0.05)

    cf.plane_vars["slope_x"].set("5")
    cf._balance_plane()
    _wait(app, lambda: cf._balance_thread is None)
    result = _calculate(app, cf)
    assert result.total_cut == pytest.approx(result.total_fill, abs=0.05)
    assert result.soil_balance.factors.loose == 1.2


def test_settings_saved_and_restored(app, tmp_path):
    cf = _load(app)
    cf.loose_var.set("1.25")
    cf.compact_var.set("1.05")
    cf.trim_enabled_var.set(True)
    cf.max_edge_var.set("30")
    path = tmp_path / "v21.tinproj.json"
    app.save_project_to(str(path))

    app.load_project_from(str(path))
    cf = app.calc_frame
    assert app.holes == [HOLE] and app.boundary_frame.holes == [HOLE]
    assert cf.loose_var.get() == "1.25" and cf.compact_var.get() == "1.05"
    assert cf.trim_enabled_var.get() and cf.max_edge_var.get() == "30"
    result = _calculate(app, cf)
    assert result.holes == [HOLE]
    assert result.soil_balance.factors.compact == 1.05


def test_bad_factor_shows_error(app, monkeypatch):
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda title, msg, **k: errors.append(msg))
    cf = _load(app, draw_hole=False)
    cf.compact_var.set("0")
    cf._run_calculation()
    assert errors and "Ks'" in errors[0]
