"""窗口排版回归测试：默认窗口尺寸下，各页主要按钮和状态栏不能被挤出窗口。

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
    window.geometry("{}x{}".format(*app_main.DEFAULT_WINDOW_SIZE))
    yield window
    window.destroy()


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


def test_primary_buttons_visible_at_default_window_size(app):
    points = generate_test_data()
    app.import_frame.load_points(points)
    app.update()
    for text in ("确认无误，进入下一步", "导出检查报告", "X/Y 互换"):
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
