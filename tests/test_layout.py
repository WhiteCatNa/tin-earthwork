"""窗口排版回归测试：默认窗口、1366x768 和 1024x768 屏幕的窗口下，各页按钮和状态栏不能被挤出窗口。

曾经的问题：按钮排在可伸展的表格/图形之后 pack，窗口高度不够时
“确认无误，进入下一步”“确认边界”和整排导出按钮都被挤到 0 高度，看不见也点不到；
窗口较窄时一行按钮的最后几个被挤成 0 宽。

注意：Windows 上窗口不能大于屏幕（CI 的屏幕只有 1024x768），所以断言都以窗口的实际尺寸为准。
"""
import tkinter as tk

import pytest

import main as app_main
from conftest import create_main_window
from run_full_test import generate_test_data


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TIN_EARTHWORK_RECENT", str(tmp_path / "recent.json"))
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda *a, **k: "ok")
    window = create_main_window(hidden=False)
    window.minsize(1, 1)
    window.geometry("{}x{}".format(*app_main.DEFAULT_WINDOW_SIZE))
    yield window
    window.destroy()


LAPTOP_WINDOW = app_main.window_size_for_screen(1366, 768)
SMALL_WINDOW = app_main.window_size_for_screen(1024, 768)
WINDOW_SIZES = [app_main.DEFAULT_WINDOW_SIZE, LAPTOP_WINDOW, SMALL_WINDOW]
INTERACTIVE = {"TButton", "TCheckbutton", "TRadiobutton", "TEntry", "TMenubutton", "TCombobox"}


def _squeezed_controls(root):
    """已布局（其父控件可见）却被挤得比所需尺寸小、或被挤到隐藏的按钮、输入框等。"""
    found = []

    def walk(widget):
        for child in widget.winfo_children():
            if not child.winfo_manager() or not widget.winfo_ismapped():
                continue
            if child.winfo_class() in INTERACTIVE and (
                not child.winfo_ismapped()
                or child.winfo_width() < child.winfo_reqwidth() - 2
                or child.winfo_height() < child.winfo_reqheight() - 2
            ):
                try:
                    label = child.cget("text")
                except tk.TclError:
                    label = str(child)
                found.append(f"{label}（{child.winfo_width()}x{child.winfo_height()}，"
                             f"需要 {child.winfo_reqwidth()}x{child.winfo_reqheight()}）")
            walk(child)

    root.update_idletasks()
    walk(root)
    return found


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
    assert widget.winfo_ismapped(), f"{label} 被挤到隐藏"
    assert widget.winfo_height() >= widget.winfo_reqheight() - 2, f"{label} 被压扁"
    assert widget.winfo_width() >= widget.winfo_reqwidth() - 2, f"{label} 被挤窄"
    assert y + widget.winfo_height() <= app.winfo_height(), f"{label} 超出窗口底部"
    assert x + widget.winfo_width() <= app.winfo_width(), f"{label} 超出窗口右侧"


def test_default_window_size_fits_common_screens():
    assert app_main.window_size_for_screen(1920, 1080) == (1400, 900)
    width, height = app_main.window_size_for_screen(1366, 768)
    assert width <= 1366 and height <= 768 - 80


@pytest.mark.parametrize("size", WINDOW_SIZES, ids=["default", "1366x768", "1024x768"])
def test_primary_buttons_visible(app, size):
    app.geometry("{}x{}".format(*size))
    if size != app_main.DEFAULT_WINDOW_SIZE:
        # 小屏默认收起列映射，把高度让给测点表
        app.import_frame.set_mapping_expanded(False)
    points = generate_test_data()
    app.import_frame.load_points(points)
    app.update()
    for text in ("确认无误，进入下一步", "导出检查报告", "X/Y 互换", "应用平移", "添加测点"):
        _assert_fully_inside_window(app, _find(app.import_frame, text), text)
    _assert_fully_inside_window(app, app.status_bar, "状态栏")
    assert _squeezed_controls(app.import_frame) == []

    app.import_frame._confirm_points()
    app.boundary_frame._create_auto_boundary()
    app.notebook.select(1)
    app.update()
    for text in ("确认边界", "自动生成数据范围边界", "清空边界"):
        _assert_fully_inside_window(app, _find(app.boundary_frame, text), text)
    assert _squeezed_controls(app.boundary_frame) == []

    app.boundary_frame._confirm_boundary()
    app.update()
    cf = app.calc_frame
    _assert_fully_inside_window(app, cf.calculate_button, "开始计算")
    _assert_fully_inside_window(app, cf.progress, "进度条")
    _assert_fully_inside_window(app, cf.balance_button, "挖填平衡")
    assert len(cf._export_buttons) == 6
    for button in cf._export_buttons:
        _assert_fully_inside_window(app, button, button.cget("text"))
    for mode in ("flat", "plane", "compare"):
        cf.design_mode_var.set(mode)
        cf._on_mode_change()
        assert _squeezed_controls(cf) == [], mode


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
    # 步骤条并入状态栏后，1366x768 屏幕的窗口已能完整放下斜面 + 分区的设置项；
    # 用再矮 100 px 的窗口验证“放不下时滚动、开始计算仍可见”
    app.geometry("{}x{}".format(LAPTOP_WINDOW[0], LAPTOP_WINDOW[1] - 100))
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
    """用 place 直接给边界页指定尺寸，不受屏幕大小限制（Windows 上窗口不能大于屏幕）。"""
    from gui.boundary_frame import BoundaryFrame

    host = tk.Toplevel(app)
    frame = BoundaryFrame(host, generate_test_data()[:50], lambda boundary: None)
    try:
        frame.place(x=0, y=0, width=1400, height=450)
        app.update()
        assert not frame.info_label.winfo_manager()
        frame.place_configure(height=1000)
        app.update()
        assert frame.info_label.winfo_manager() == "grid"
    finally:
        frame.shutdown()
        host.destroy()


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


def test_tables_do_not_grow_and_squeeze_neighbour_panel(app):
    """回归：表格列被拉伸后，拉伸后的列宽成了新的请求宽度，左栏越撑越宽，右栏按钮被挤没。"""
    app.geometry("{}x{}".format(*SMALL_WINDOW))
    frame = app.import_frame
    frame.set_mapping_expanded(False)   # 1024x768 屏幕上的默认状态
    app.update()
    frame.points = generate_test_data()
    frame._update_preview()
    frame._update_stats()
    for _ in range(3):
        app.update()
    assert _squeezed_controls(frame) == []
    _assert_fully_inside_window(app, _find(frame, "确认无误，进入下一步"), "确认无误，进入下一步")
