"""窗口排版回归测试：默认窗口和 1366x768 笔记本窗口下，各页主要按钮和状态栏不能被挤出窗口。

曾经的问题：按钮排在可伸展的表格/图形之后 pack，窗口高度不够时
“确认无误，进入下一步”“确认边界”和整排导出按钮都被挤到 0 高度，看不见也点不到。
"""
import tkinter as tk

import pytest

import main as app_main
from run_full_test import generate_test_data


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TIN_EARTHWORK_RECENT", str(tmp_path / "recent.json"))
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda *a, **k: "ok")
    window = app_main.MainApplication()
    window.minsize(1, 1)
    window.geometry("{}x{}".format(*app_main.DEFAULT_WINDOW_SIZE))
    yield window
    window.destroy()


LAPTOP_WINDOW = app_main.window_size_for_screen(1366, 768)


def _find(widget, text):
    for child in widget.winfo_children():
        try:
            if child.cget("text") == text:
                return child
        except tk.TclError:
            pass
        found = _find(child, text)
        if found is not None:
            return found
    return None


def _assert_fully_inside_window(app, widget, label):
    app.update_idletasks()
    x = y = 0
    current = widget
    while current is not app:
        x += current.winfo_x()
        y += current.winfo_y()
        current = current.master
    assert widget.winfo_height() >= widget.winfo_reqheight() - 2, f"{label} 被压扁"
    assert y + widget.winfo_height() <= app.winfo_height(), f"{label} 超出窗口底部"
    assert x + widget.winfo_width() <= app.winfo_width(), f"{label} 超出窗口右侧"


def test_default_window_size_fits_common_screens():
    assert app_main.window_size_for_screen(1920, 1080) == (1400, 900)
    width, height = app_main.window_size_for_screen(1366, 768)
    assert width <= 1366 and height <= 768 - 80


@pytest.mark.parametrize("size", [app_main.DEFAULT_WINDOW_SIZE, LAPTOP_WINDOW], ids=["1400x900", "laptop"])
def test_primary_buttons_visible(app, size):
    app.geometry("{}x{}".format(*size))
    if size == LAPTOP_WINDOW:
        # 小屏默认收起列映射，把高度让给测点表
        app.import_frame.set_mapping_expanded(False)
    points = generate_test_data()
    app.import_frame.load_points(points)
    app.update()
    for text in ("确认无误，进入下一步", "导出检查报告", "X/Y 互换", "应用平移", "添加测点"):
        _assert_fully_inside_window(app, _find(app.import_frame, text), text)
    _assert_fully_inside_window(app, app.status_bar, "状态栏")

    app.import_frame._confirm_points()
    app.boundary_frame._create_auto_boundary()
    app.notebook.select(1)
    app.update()
    for text in ("确认边界", "自动生成数据范围边界", "清空边界"):
        _assert_fully_inside_window(app, _find(app.boundary_frame, text), text)

    app.boundary_frame._confirm_boundary()
    app.update()
    cf = app.calc_frame
    _assert_fully_inside_window(app, cf.calculate_button, "开始计算")
    _assert_fully_inside_window(app, cf.progress, "进度条")
    assert len(cf._export_buttons) == 6
    for button in cf._export_buttons:
        _assert_fully_inside_window(app, button, button.cget("text"))


def test_partition_editor_reappears_right_below_its_checkbox(app):
    app.import_frame.load_points(generate_test_data()[:50])
    app.import_frame._confirm_points()
    app.boundary_frame._create_auto_boundary()
    app.boundary_frame._confirm_boundary()
    cf = app.calc_frame
    cf.use_partition_var.set(True)
    cf._toggle_partition()
    slaves = cf.partition_check.master.pack_slaves()
    assert slaves.index(cf.partition_frame) == slaves.index(cf.partition_check) + 1


def test_calc_settings_scroll_instead_of_hiding_on_small_window(app):
    app.geometry("{}x{}".format(*LAPTOP_WINDOW))
    app.import_frame.load_points(generate_test_data()[:50])
    app.import_frame._confirm_points()
    app.boundary_frame._create_auto_boundary()
    app.boundary_frame._confirm_boundary()
    cf = app.calc_frame
    cf.design_mode_var.set("plane")
    cf._on_mode_change()
    cf.use_partition_var.set(True)
    cf._toggle_partition()
    app.update()
    # 设置项超出可见高度时出现滚动条，“开始计算”仍固定可见
    assert cf.settings.scrollable
    assert cf.settings.scrollbar.winfo_ismapped()
    _assert_fully_inside_window(app, cf.calculate_button, "开始计算")


def test_boundary_hint_hides_on_short_panel_and_returns_when_tall(app):
    app.import_frame.load_points(generate_test_data()[:50])
    app.import_frame._confirm_points()
    app.notebook.select(1)
    frame = app.boundary_frame
    app.geometry("{}x{}".format(*LAPTOP_WINDOW))
    app.update()
    assert not frame.info_label.winfo_manager()
    app.geometry("1400x1000")
    app.update()
    assert frame.info_label.winfo_manager() == "grid"


def test_mapping_collapses_to_summary_and_expands_when_incomplete(app):
    import pandas as pd

    frame = app.import_frame
    frame.set_mapping_expanded(False)
    frame.raw_df = pd.DataFrame({"点号": ["A"], "X": [0.0], "Y": [0.0], "高程": [1.0]})
    frame._update_mapping_display()
    assert not frame.mapping_body.winfo_manager()
    assert "X←X" in frame.mapping_summary_var.get()
    frame.raw_df = pd.DataFrame({"名称": ["A"], "东": [0.0]})
    frame._update_mapping_display()
    assert frame.mapping_body.winfo_manager() == "pack"
    assert "未识别" in frame.mapping_summary_var.get()
