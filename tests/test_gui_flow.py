"""界面流程回归测试：直接驱动真实窗口（隐藏），不弹出对话框。"""
import time

import pytest

from core.calculator import SurveyPoint
import main as app_main


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


def _square_points():
    return [
        SurveyPoint("A", 0, 0, 15.5),
        SurveyPoint("B", 10, 0, 15.2),
        SurveyPoint("C", 0, 10, 14.8),
        SurveyPoint("D", 10, 10, 15.1),
    ]


def _load(app, points, boundary=((0, 0), (10, 0), (10, 10), (0, 10))):
    app.import_frame.load_points(points)
    app.import_frame._confirm_points()
    app.boundary_frame.set_boundary(list(boundary))
    app.boundary_frame._confirm_boundary()
    return app.calc_frame


def test_partition_calculation_uses_ui_design_elevation_for_other_points(app):
    """回归：界面上只给部分点设分区高程时，其余点曾按 0 计算。"""
    cf = _load(app, _square_points())
    cf.design_elevation_var.set(15.0)
    cf.use_partition_var.set(True)
    cf.partition_data = {"A": 15.3}
    cf._run_calculation()
    _wait(app, lambda: cf.result is not None)
    assert {p.id: p.design_z for p in app.points} == {"A": 15.3, "B": 15.0, "C": 15.0, "D": 15.0}
    assert cf.result.total_cut < 20
    assert "3 个测点不在分区高程表中" in cf.result.warnings[0]


def test_invalid_design_elevation_shows_error_instead_of_crashing(app, monkeypatch):
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda title, msg, **k: errors.append(msg))
    cf = _load(app, _square_points())
    cf.design_elevation_var.set("abc")  # DoubleVar 读取会抛 TclError
    cf._run_calculation()
    assert errors and "数字" in errors[0]
    assert cf.result is None


def test_exports_use_project_name_and_run_in_background(app, tmp_path, monkeypatch):
    cf = _load(app, _square_points())
    app.project_name_var.set("三号地块")
    cf.design_elevation_var.set(15.0)
    cf._run_calculation()
    _wait(app, lambda: cf.result is not None)

    target = tmp_path / "report.txt"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda *a, **k: str(target))
    cf._export_text()
    _wait(app, lambda: cf._export_thread is None)
    text = target.read_text(encoding="utf-8")
    assert "三号地块 - 土方计算报告" in text
    assert "设计高程: 15.000 m" in text


def test_editing_boundary_after_confirm_does_not_change_confirmed_boundary(app):
    """回归：边界页、主窗口、计算页曾共用同一个列表，改边界页会悄悄改掉已确认的边界。"""
    cf = _load(app, _square_points())
    confirmed = list(app.boundary)
    app.boundary_frame.boundary.append((5, 12))
    assert app.boundary == confirmed
    assert cf.boundary == confirmed
    assert cf.calculator.boundary_polygon == confirmed


def test_reimporting_points_invalidates_old_result(app):
    cf = _load(app, _square_points())
    cf.design_elevation_var.set(15.0)
    cf._run_calculation()
    _wait(app, lambda: cf.result is not None)

    app.import_frame.load_points([SurveyPoint("N1", 0, 0, 1), SurveyPoint("N2", 5, 0, 1), SurveyPoint("N3", 0, 5, 1)])
    app.import_frame._confirm_points()
    assert cf.result is None
    assert app.notebook.tab(2, "state") == "disabled"


def test_swap_xy_updates_points_and_optionally_boundary(app, monkeypatch):
    points = [SurveyPoint("A", 0, 100, 1), SurveyPoint("B", 10, 100, 1), SurveyPoint("C", 0, 110, 1)]
    _load(app, points, boundary=((0, 100), (10, 100), (0, 110)))
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
    app.import_frame.swap_xy()
    assert (app.points[0].x, app.points[0].y) == (100, 0)
    assert app.boundary == [(100, 0), (100, 10), (110, 0)]
    assert app.calc_frame.result is None


def test_column_mapping_can_be_changed_and_applied(app):
    import pandas as pd

    frame = app.import_frame
    frame.raw_df = pd.DataFrame({"编号": ["A", "B", "C"], "北": [0, 0, 1], "东": [0, 1, 0], "H": [10, 11, 12]})
    frame._update_mapping_display({"id": "编号", "x": "北", "y": "东", "z": "H"})
    frame.set_mapping_source("x", "东")
    frame.set_mapping_source("y", "北")
    frame._apply_mapping()
    assert [(p.id, p.x, p.y) for p in frame.points] == [("A", 0.0, 0.0), ("B", 1.0, 0.0), ("C", 0.0, 1.0)]
    assert frame.tree_map.set("x", "source") == "东"


def test_save_project_names_project_after_file(app, tmp_path, monkeypatch):
    app.import_frame.load_points(_square_points())
    app.import_frame._confirm_points()
    target = tmp_path / "五号地块.tinproj.json"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda *a, **k: str(target))
    app._save_project()
    assert app.project_name == "五号地块"
    assert target.exists()
