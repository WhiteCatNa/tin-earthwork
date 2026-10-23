"""2.1 界面流程：边界页绘制、导入特征线，计算、显示、工程存取，两期对比的后期特征线。"""
import time

import pytest

import main as app_main
from core.breaklines import Breakline
from tests.test_breakline_io import _dxf, _lwpolyline, _polyline_3d
from tests.test_breaklines import EXACT_CUT, SITE, _edge_xy, _survey, ground

BOUNDARY = [(0.0, 0.0), (SITE[0], 0.0), (SITE[0], SITE[1]), (0.0, SITE[1])]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TIN_EARTHWORK_RECENT", str(tmp_path / "recent.json"))
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda *a, **k: "ok")
    monkeypatch.setattr("tkinter.messagebox.askokcancel", lambda *a, **k: True)
    window = app_main.MainApplication()
    window.withdraw()
    yield window
    window.destroy()


def _wait(app, predicate, timeout=30.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待超时")


def _load_points(app, points):
    app.import_frame.load_points(points)
    app.import_frame._confirm_points()


def _confirm_boundary(app):
    app.boundary_frame.set_boundary(list(BOUNDARY))
    app.boundary_frame._confirm_boundary()
    return app.calc_frame


def _calculate(app, cf, design=5.0):
    cf.design_elevation_var.set(design)
    cf.result = None
    cf._run_calculation()
    _wait(app, lambda: cf.result is not None)
    return cf.result


def _draw_edges(frame):
    frame.target_var.set("breakline")
    frame._on_target_change()
    for line in _edge_xy():
        for x, y in line:
            # 点在测点附近（不必正好点中），吸附到测点
            assert frame.add_drawing_vertex(x + 0.3, y - 0.2)
        frame._finish_drawing()


def test_draw_breaklines_and_calculate(app):
    points, _ = _survey()
    _load_points(app, points)
    frame = app.boundary_frame
    _draw_edges(frame)
    assert len(frame.breaklines) == 4
    assert "4 条特征线" in frame.breakline_status_var.get()

    cf = _confirm_boundary(app)
    result = _calculate(app, cf)
    assert result.total_cut == pytest.approx(EXACT_CUT, abs=1e-6)
    assert len(result.breaklines) == 4
    cut, fill = result.without_breaklines
    assert abs(cut - EXACT_CUT) > 20
    summary = [cf.tree_summary.item(item, "values")[0] for item in cf.tree_summary.get_children()]
    assert "特征线" in summary and "不加特征线挖方" in summary
    assert cf.plotter._artist_breaklines is not None
    cf.show_breaklines_var.set(False)
    cf._refresh_plot()
    assert not cf.plotter._artist_breaklines.get_visible()


def test_vertex_off_survey_point_is_refused(app):
    points, _ = _survey()
    _load_points(app, points)
    frame = app.boundary_frame
    frame.target_var.set("breakline")
    assert not frame.add_drawing_vertex(-50.0, -50.0)
    assert "须点在测点上" in frame.breakline_status_var.get()
    assert frame.drawing == []


def test_import_breaklines_from_dxf_and_save_project(app, tmp_path):
    points, lines = _survey(with_edge_points=False)
    _load_points(app, points)
    path = _dxf(tmp_path, *[
        _polyline_3d("坡线", [(x, y, float(ground(x, y))) for x, y in line]) for line in lines
    ], _lwpolyline("红线", BOUNDARY, closed=True))
    frame = app.boundary_frame
    assert frame.import_breaklines_dxf(str(path), layers=["坡线"])
    assert len(frame.breaklines) == 4 and "坡线" in frame.breaklines[0].name
    cf = _confirm_boundary(app)
    result = _calculate(app, cf)
    assert result.total_cut == pytest.approx(EXACT_CUT, abs=1e-6)

    project = tmp_path / "site.tinproj.json"
    app.save_project_to(str(project))
    frame._clear_breaklines()
    assert frame.breaklines == []
    app.load_project_from(str(project))
    assert [line.vertices for line in app.boundary_frame.breaklines] == [
        [(x, y, float(ground(x, y))) for x, y in line] for line in lines
    ]


def test_dxf_2d_vertices_off_points_warn(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda title, msg, **k: warnings.append(msg))
    points, _ = _survey(with_edge_points=False)
    _load_points(app, points)
    path = _dxf(tmp_path, _lwpolyline("坎", [(0.0, 20.0), (100.0, 20.0)]))
    assert app.boundary_frame.import_breaklines_dxf(str(path))
    assert warnings and "2 个不带高程的顶点不在测点上" in warnings[0]


def test_swap_xy_moves_breaklines(app, monkeypatch):
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
    points, _ = _survey()
    _load_points(app, points)
    frame = app.boundary_frame
    frame.set_breaklines([Breakline([(0.0, 20.0, None), (100.0, 20.0, None)])])
    app.import_frame.points = [type(p)(p.id, p.y, p.x, p.z) for p in points]
    app._on_points_mutated("swap")
    assert frame.breaklines[0].vertices == [(20.0, 0.0, None), (20.0, 100.0, None)]


def test_compare_with_after_breaklines(app, tmp_path):
    import numpy as np
    from tests.test_breaklines import _points, flat

    rng = np.random.default_rng(10)
    before = _points(np.vstack([rng.random((250, 2)) * SITE, BOUNDARY]), surface=flat, prefix="Q")
    after, lines = _survey(with_edge_points=False)
    _load_points(app, before)
    cf = _confirm_boundary(app)
    cf.set_compare_points(after, "after.csv")
    path = _dxf(tmp_path, *[
        _polyline_3d("后期坡线", [(x, y, float(ground(x, y))) for x, y in line]) for line in lines
    ])
    assert cf.import_compare_breaklines_dxf(str(path))
    assert "4 条" in cf.compare_breakline_label.cget("text")
    cf.design_mode_var.set("compare")
    cf._on_mode_change()
    result = _calculate(app, cf)
    assert result.total_cut == pytest.approx(12.0 * SITE[0], abs=1e-6)
    assert len(result.compare_breaklines) == 4

    project = tmp_path / "compare.tinproj.json"
    app.save_project_to(str(project))
    cf.set_compare_breaklines([])
    app.load_project_from(str(project))
    assert len(app.calc_frame.compare_breaklines) == 4
