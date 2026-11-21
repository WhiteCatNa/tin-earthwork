"""2.0 界面流程：斜面设计面、两期对比、方格网校核、工程存取。"""
import time

import pytest

from core.calculator import SurveyPoint

BOUNDARY = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]


@pytest.fixture
def app(main_window, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda *a, **k: "ok")
    monkeypatch.setattr("tkinter.messagebox.askokcancel", lambda *a, **k: True)
    return main_window


def _wait(app, predicate, timeout=30.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待超时")


def _square(z, prefix="P"):
    return [SurveyPoint(f"{prefix}{i}", x, y, z) for i, (x, y) in
            enumerate([(0, 0), (20, 0), (20, 20), (0, 20), (10, 7)], 1)]


def _load(app, points=None):
    app.import_frame.load_points(points or _square(10.0))
    app.import_frame._confirm_points()
    app.boundary_frame.set_boundary(list(BOUNDARY))
    app.boundary_frame._confirm_boundary()
    return app.calc_frame


def _calculate(app, cf):
    cf.result = None
    cf._run_calculation()
    _wait(app, lambda: cf.result is not None)
    return cf.result


def test_plane_mode_from_gui(app):
    cf = _load(app)
    cf.design_mode_var.set("plane")
    cf._on_mode_change()
    # 首次切到斜面时自动填入场地中心与高程中位数
    assert cf.plane_vars["x0"].get() == "10.000" and cf.plane_vars["h0"].get() == "10.000"
    cf.plane_vars["h0"].set("9")
    cf.plane_vars["slope_x"].set("5")
    result = _calculate(app, cf)
    # 设计面 H = 9 + 0.05(x − 10)，在 0..20 正方形上平均 9 m，高差恒为 1 → 400 m³
    assert abs(result.net_volume - 400) < 1e-9
    assert "斜面" in result.design_text


def test_invalid_plane_input_shows_error(app, monkeypatch):
    errors = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda title, msg, **k: errors.append(msg))
    cf = _load(app)
    cf.design_mode_var.set("plane")
    cf._on_mode_change()
    cf.plane_vars["slope_y"].set("abc")
    cf._run_calculation()
    assert errors and "Y向坡度" in errors[0]
    assert cf.result is None


def test_compare_mode_imports_file_and_calculates_with_grid(app, tmp_path):
    cf = _load(app)
    after = tmp_path / "after.csv"
    after.write_text("点号,X,Y,高程\n" + "\n".join(
        f"Q{i},{x},{y},9.0" for i, (x, y) in enumerate([(0, 0), (20, 0), (20, 20), (0, 20), (6, 13)], 1)
    ), encoding="utf-8")
    assert cf.load_compare_file(str(after))
    assert "5 个后期测点" in cf.compare_label.cget("text")

    cf.design_mode_var.set("compare")
    cf._on_mode_change()
    assert str(cf.partition_check.cget("state")) == "disabled"
    cf.grid_enabled_var.set(True)
    cf.grid_spacing_var.set("5")
    result = _calculate(app, cf)
    assert result.is_compare and abs(result.total_cut - 400) < 1e-9
    assert result.grid_check is not None and abs(result.grid_check.total_cut - 400) < 1e-9
    summary = [cf.tree_summary.item(item, "values")[0] for item in cf.tree_summary.get_children()]
    assert "比较面" in summary and "方格网法挖方" in summary


def test_compare_mode_requires_second_survey(app, monkeypatch):
    warnings = []
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda title, msg, **k: warnings.append(msg))
    cf = _load(app)
    cf.design_mode_var.set("compare")
    cf._run_calculation()
    assert warnings and "后期" in warnings[0]


def test_project_roundtrip_keeps_v2_settings(app, tmp_path):
    cf = _load(app)
    cf.set_compare_points(_square(9.0, prefix="Q"), "after.xlsx")
    cf.design_mode_var.set("plane")
    cf._on_mode_change()
    cf.plane_vars["slope_y"].set("-0.5")
    cf.grid_enabled_var.set(True)
    cf.grid_spacing_var.set("4")
    path = tmp_path / "v2.tinproj.json"
    app.save_project_to(str(path))

    cf.design_mode_var.set("flat")
    cf.set_compare_points([], "")
    cf.grid_enabled_var.set(False)
    app.load_project_from(str(path))
    cf = app.calc_frame
    assert cf.design_mode_var.get() == "plane"
    assert cf.plane_value().slope_y == -0.5
    assert len(cf.compare_points) == 5 and cf.compare_source == "after.xlsx"
    assert cf.grid_enabled_var.get() and cf.grid_spacing_value() == 4.0


def test_settings_from_project_without_boundary_apply_when_calc_page_opens(app, tmp_path):
    from utils.data_handler import save_project

    path = tmp_path / "noboundary.tinproj.json"
    save_project(path, _square(10.0), [], design_elevation=8.0, design_mode="compare",
                 compare_points=_square(9.0, prefix="Q"), grid_enabled=True, grid_spacing=5.0)
    app.load_project_from(str(path))
    assert app.calc_frame is None
    # 未设边界时再次保存，不应丢掉工程里的设置
    again = tmp_path / "again.tinproj.json"
    app.save_project_to(str(again))
    from utils.data_handler import load_project
    assert load_project(again)["design_mode"] == "compare"

    app.boundary_frame.set_boundary(list(BOUNDARY))
    app.boundary_frame._confirm_boundary()
    cf = app.calc_frame
    assert cf.design_mode_var.get() == "compare" and len(cf.compare_points) == 5
    assert cf.grid_enabled_var.get()


def test_balance_only_offered_for_flat_design(app, monkeypatch):
    """挖填平衡只对统一高程有意义：斜面、两期对比时拒绝，并说明原因。"""
    warnings = []
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda title, msg, **k: warnings.append(msg))
    cf = _load(app)
    for mode in ("plane", "compare"):
        cf.design_mode_var.set(mode)
        cf._on_mode_change()
        cf._balance_elevation()
        assert cf._worker_thread is None and cf.result is None
    assert len(warnings) == 2 and all("统一高程" in item for item in warnings)


def test_balance_after_compare_run_ignores_second_survey(app):
    """回归：先做过两期对比再求挖填平衡，不能把后期测点带进平衡计算。"""
    cf = _load(app, [SurveyPoint(f"P{i}", x, y, z) for i, (x, y, z) in
                     enumerate([(0, 0, 9), (20, 0, 11), (20, 20, 11), (0, 20, 9), (10, 7, 10)], 1)])
    cf.set_compare_points(_square(5.0, prefix="Q"), "after.xlsx")
    cf.design_mode_var.set("compare")
    cf._on_mode_change()
    assert _calculate(app, cf).is_compare

    cf.design_mode_var.set("flat")
    cf._on_mode_change()
    cf.result = None
    cf._balance_elevation()
    _wait(app, lambda: cf.result is not None)
    assert not cf.result.is_compare
    assert abs(cf.result.net_volume) <= 0.0005 * cf.result.computed_area + 1e-9
    assert 9.5 < cf.design_elevation_value() < 10.5


def test_grid_check_result_does_not_pop_up_when_clean(app, monkeypatch):
    """没有需要注意的提示时，方格网校核的结果也走状态栏和汇总表，不弹窗。"""
    popups = []
    monkeypatch.setattr("tkinter.messagebox.showinfo", lambda *a, **k: popups.append(a))
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda *a, **k: popups.append(a))
    cf = _load(app)
    cf.design_elevation_var.set(9.0)
    cf.grid_enabled_var.set(True)
    cf.grid_spacing_var.set("5")
    result = _calculate(app, cf)
    assert result.grid_check is not None
    if not (result.warnings or result.grid_check.warnings):
        assert popups == []
        assert "方格网校核结果见汇总表" in app.status_var.get()


def test_v2_settings_change_marks_project_unsaved(app, tmp_path):
    cf = _load(app)
    app.save_project_to(str(tmp_path / "a.tinproj.json"))
    assert not app.title().startswith("*")
    cf.design_mode_var.set("plane")
    assert app.title().startswith("* ")
    app.save_project_to(str(tmp_path / "a.tinproj.json"))
    cf.grid_enabled_var.set(True)
    assert app.title().startswith("* ")
    app.load_project_from(str(tmp_path / "a.tinproj.json"))
    assert not app.title().startswith("*")
