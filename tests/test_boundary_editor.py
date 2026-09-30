"""边界页的现场采集功能：鼠标画线（缩放、平移、插点、拖点、删点）、按点号连线、
沿测点外轮廓生成、导入带点号的边界点文件。在真实窗口里用合成的鼠标事件驱动。"""
import math
from types import SimpleNamespace

import pytest

from core.calculator import SurveyPoint


def _grid_points(keep=lambda ix, iy: True, size=10, step=10.0):
    points = []
    for iy in range(size + 1):
        for ix in range(size + 1):
            if keep(ix, iy):
                points.append(SurveyPoint(str(len(points) + 1), ix * step, iy * step, 10.0 + 0.01 * ix))
    return points


@pytest.fixture
def frame(main_window, monkeypatch):
    """载入 11×11 测网（点距 10 m，点号 1~121，逐行编号）后的边界页。"""
    warnings = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda title, message=None, **k: warnings.append(message))
    main_window.import_frame.load_points(_grid_points())
    main_window.import_frame._confirm_points()
    main_window.update()
    boundary_frame = main_window.boundary_frame
    boundary_frame.warnings = warnings
    boundary_frame.canvas.draw()
    yield boundary_frame
    main_window.update()   # 让排队中的重绘在窗口销毁前跑完


def _event(frame, x, y, button=1, step=0):
    """数据坐标 (x, y) 处的鼠标事件。"""
    frame.canvas.draw()
    px, py = frame.plotter.ax.transData.transform((x, y))
    return SimpleNamespace(inaxes=frame.plotter.ax, x=px, y=py, xdata=x, ydata=y, button=button, step=step)


def _click(frame, x, y, button=1):
    event = _event(frame, x, y, button)
    frame._on_canvas_press(event)
    frame._on_canvas_release(event)


def _drag(frame, start, end, button=1):
    frame._on_canvas_press(_event(frame, *start, button))
    ax = frame.plotter.ax
    # 拖动过程中视野可能在变（平移），中间事件的像素位置都按按下时的坐标系算
    to_pixel = ax.transData.frozen().transform
    for fraction in (0.5, 1.0):
        x = start[0] + (end[0] - start[0]) * fraction
        y = start[1] + (end[1] - start[1]) * fraction
        px, py = to_pixel((x, y))
        frame._on_canvas_motion(SimpleNamespace(inaxes=ax, x=px, y=py, xdata=x, ydata=y, button=button, step=0))
    frame._on_canvas_release(SimpleNamespace(inaxes=ax, x=px, y=py, xdata=end[0], ydata=end[1], button=button, step=0))


def _area(ring):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def _rows(frame):
    return [frame.tree_boundary.item(item)["values"] for item in frame.tree_boundary.get_children()]


# ---------- 鼠标画线 ----------

def test_clicks_snap_to_survey_points_and_list_shows_their_numbers(frame):
    for x, y in [(0.8, -0.6), (99.5, 0.7), (100.4, 99.2), (-0.9, 100.6)]:
        _click(frame, x, y)
    assert frame.boundary == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    assert [str(row[1]) for row in _rows(frame)] == ["1", "11", "121", "111"]
    assert "面积 10000.0" in frame.boundary_status_var.get()


def test_click_on_an_edge_inserts_a_vertex_there(frame):
    frame.set_boundary([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)])
    frame.snap_var.set(False)
    _click(frame, 45.0, 0.3)       # 在第 1、2 点之间的边上
    assert frame.boundary[:3] == [(0.0, 0.0), (45.0, 0.3), (100.0, 0.0)]
    _click(frame, 0.2, 55.0)       # 在回到起点的闭合边上：等于接在最后
    assert frame.boundary[-1] == (0.2, 55.0) and len(frame.boundary) == 6


def test_dragging_a_vertex_moves_it_and_undo_restores(frame):
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    frame.set_boundary(list(square))
    _drag(frame, (100.0, 100.0), (88.7, 91.2))          # 松手处吸附到测点 (90, 90)
    assert frame.boundary == [(0.0, 0.0), (100.0, 0.0), (90.0, 90.0), (0.0, 100.0)]
    assert frame.tree_boundary.selection() == ("2",)
    frame._undo_last_point()
    assert frame.boundary == square

    _drag(frame, (100.0, 100.0), (100.3, 0.4))          # 拖到另一个边界点上：不允许
    assert frame.boundary == square


def test_right_click_deletes_the_vertex_under_the_cursor_or_the_last_one(frame):
    frame.set_boundary([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (50.0, 100.0), (0.0, 100.0)])
    _click(frame, 100.0, 0.0, button=3)
    assert frame.boundary == [(0.0, 0.0), (100.0, 100.0), (50.0, 100.0), (0.0, 100.0)]
    _click(frame, 55.0, 45.0, button=3)                  # 空白处：删最后一点
    assert frame.boundary == [(0.0, 0.0), (100.0, 100.0), (50.0, 100.0)]


def test_zoom_and_pan_keep_the_view_and_do_not_add_points(frame):
    full = frame.plotter.get_view()
    frame._on_canvas_scroll(_event(frame, 30.0, 30.0, step=3))
    zoomed = frame.plotter.get_view()
    assert zoomed[0][1] - zoomed[0][0] < (full[0][1] - full[0][0]) / 1.9
    frame.canvas.draw()
    px, py = frame.plotter.ax.transData.transform((30.0, 30.0))
    assert frame.plotter.ax.contains_point((px, py))     # 以光标为中心缩放，光标下的位置还在视野里

    _drag(frame, (35.0, 35.0), (25.0, 30.0))             # 拖动空白处：平移
    panned = frame.plotter.get_view()
    assert frame.boundary == []
    assert panned[0][0] == pytest.approx(zoomed[0][0] + 10.0, abs=0.5)
    assert panned[1][0] == pytest.approx(zoomed[1][0] + 5.0, abs=0.5)

    _click(frame, 30.2, 29.7)                            # 加点后视野不跳回全图
    assert frame.boundary == [(30.0, 30.0)]
    assert frame.plotter.get_view()[0] == pytest.approx(panned[0], abs=1e-6)

    frame._reset_view()
    assert frame.plotter.get_view()[0][1] - frame.plotter.get_view()[0][0] > 100.0


def test_point_numbers_appear_only_when_few_points_are_in_view(main_window, frame):
    assert len(frame.plotter._point_label_artists) == 121            # 121 个点，全图就标得下
    dense = _grid_points(size=20, step=5.0)                          # 441 个点
    main_window.import_frame.load_points(dense)
    main_window.import_frame._confirm_points()
    main_window.update()
    assert frame.plotter._point_label_artists == []
    frame._on_canvas_scroll(_event(frame, 50.0, 50.0, step=3))
    frame._on_canvas_scroll(_event(frame, 50.0, 50.0, step=3))
    labels = frame.plotter._point_label_artists
    assert 0 < len(labels) <= frame.plotter.MAX_POINT_LABELS
    frame.show_ids_var.set(False)
    frame._refresh_point_labels()
    assert frame.plotter._point_label_artists == []


def test_hover_reports_the_survey_point_under_the_cursor(frame):
    frame._on_canvas_motion(_event(frame, 20.4, 10.3))
    assert frame.coord_var.get().endswith("测点 14")     # 第 2 行第 3 个点
    assert frame.current_point == (20.0, 10.0)


def test_edit_mode_off_only_pans(frame):
    frame.edit_var.set(False)
    frame._toggle_edit_mode()
    _click(frame, 50.0, 50.0)
    assert frame.boundary == []


def test_snap_can_be_switched_off(frame):
    frame.snap_var.set(False)
    _click(frame, 20.4, 10.3)
    assert frame.boundary == [(20.4, 10.3)]


# ---------- 按点号连线 ----------

def test_connect_by_point_numbers(frame):
    frame.id_sequence_var.set("1, 11, 121-119, 111")
    frame._connect_by_ids()
    assert frame.boundary == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (90.0, 100.0), (80.0, 100.0), (0.0, 100.0)]
    assert frame._boundary_is_valid() and frame.warnings == []


def test_connect_by_point_numbers_reports_bad_input_and_keeps_boundary(frame):
    frame.set_boundary([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)])
    frame.id_sequence_var.set("1,2,999")
    frame._connect_by_ids()
    assert frame.boundary == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    assert "没有点号 999" in frame.warnings[-1]


def test_crossing_point_order_is_flagged_not_confirmed(main_window, frame):
    frame.id_sequence_var.set("1,121,11,111")            # 对角线交叉
    frame._connect_by_ids()
    assert "交叉" in frame.boundary_status_var.get()
    frame._confirm_boundary()
    assert main_window.boundary == []


# ---------- 沿测点外轮廓生成 ----------

def test_outline_follows_an_l_shaped_site(main_window, frame):
    l_shape = _grid_points(lambda ix, iy: not (ix > 5 and iy > 5))
    main_window.import_frame.load_points(l_shape)
    main_window.import_frame._confirm_points()
    frame._create_outline_boundary()
    assert frame.outline_edge_var.get() == "20"           # 自动取值并填回输入框
    assert _area(frame.boundary) == pytest.approx(7550.0)
    assert frame._boundary_is_valid()

    frame.outline_edge_var.set("12")                      # 收紧：完全贴合凹角
    frame._create_outline_boundary()
    assert _area(frame.boundary) == pytest.approx(7500.0)
    frame.outline_edge_var.set("0")                       # 凸包
    frame._create_outline_boundary()
    assert _area(frame.boundary) == pytest.approx(8750.0)

    frame._undo_last_point()
    assert _area(frame.boundary) == pytest.approx(7500.0)

    frame.outline_edge_var.set("二十")
    frame._create_outline_boundary()
    assert "请填数字" in frame.warnings[-1]


def test_auto_outline_edge_is_recomputed_for_new_points_but_user_value_is_kept(main_window, frame):
    frame._create_outline_boundary()
    assert frame.outline_edge_var.get() == "20"
    main_window.import_frame.load_points(_grid_points(size=20, step=5.0))
    main_window.import_frame._confirm_points()
    assert frame.outline_edge_var.get() == ""
    frame.outline_edge_var.set("33")
    main_window.import_frame._confirm_points()
    assert frame.outline_edge_var.get() == "33"


# ---------- 导入边界点文件 ----------

def test_import_collected_boundary_points_with_ids(frame, tmp_path, monkeypatch):
    path = tmp_path / "边界点.csv"
    path.write_text("1,10.0,10.0,5.2\n2,90.0,10.0,5.3\n3,,80.0,5.1\n4,90.0,80.0,5.1\n5,10.0,80.0,5.0\n", encoding="utf-8")
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", lambda *a, **k: str(path))
    frame._import_boundary()
    assert frame.boundary == [(10.0, 10.0), (90.0, 10.0), (90.0, 80.0), (10.0, 80.0)]
    assert "第 3 行" in frame.warnings[-1]                # 缺 X 的一行没有悄悄丢掉
    assert [str(row[1]) for row in _rows(frame)] == ["13", "21", "98", "90"]   # 与测点重合的显示测点号


def test_import_dxf_reports_arcs(frame, tmp_path, monkeypatch):
    messages = []
    frame.notify = messages.append
    bulge = math.tan(math.pi / 8)
    vertices = [(20, 0, 0), (80, 0, bulge), (100, 20, 0), (100, 80, bulge), (80, 100, 0), (20, 100, bulge),
                (0, 80, 0), (0, 20, bulge)]
    body = "".join(f"10\n{x}\n20\n{y}\n" + (f"42\n{b!r}\n" if b else "") for x, y, b in vertices)
    path = tmp_path / "rounded.dxf"
    path.write_text(f"0\nSECTION\n2\nENTITIES\n0\nLWPOLYLINE\n8\n红线\n90\n8\n70\n1\n{body}0\nENDSEC\n0\nEOF\n",
                    encoding="utf-8")
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", lambda *a, **k: str(path))
    frame._import_boundary()
    assert "含 4 段圆弧" in messages[-1]
    assert _area(frame.boundary) == pytest.approx(10000 - (4 - math.pi) * 400, rel=1e-9)


# ---------- 撤销、删除 ----------

def test_undo_walks_back_through_every_kind_of_change(frame):
    frame._create_auto_boundary()
    rectangle = list(frame.boundary)
    frame._clear_boundary()
    frame.id_sequence_var.set("1,11,121")
    frame._connect_by_ids()
    _click(frame, 0.0, 100.0)
    assert len(frame.boundary) == 4
    frame._undo_last_point()
    assert len(frame.boundary) == 3
    frame._undo_last_point()
    assert frame.boundary == []
    frame._undo_last_point()
    assert frame.boundary == rectangle
    frame._undo_last_point()
    frame._undo_last_point()                              # 没有可撤销的了：不报错
    assert frame.boundary == []


def test_delete_selected_rows(frame):
    frame.set_boundary([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (50.0, 100.0), (0.0, 100.0)])
    frame.tree_boundary.selection_set("1", "3")
    frame._delete_selected()
    assert frame.boundary == [(0.0, 0.0), (100.0, 100.0), (0.0, 100.0)]


def test_clicking_a_vertex_selects_it_in_the_list(frame):
    frame.set_boundary([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)])
    _click(frame, 100.2, 99.8)
    assert frame.boundary == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    assert frame.tree_boundary.selection() == ("2",)


# ---------- 真实的 Tk 鼠标事件 ----------

def _tk_position(frame, x, y):
    """数据坐标对应的 Tk 控件坐标（原点在左上角，单位是未缩放的屏幕像素）。"""
    frame.canvas.draw()
    ratio = getattr(frame.canvas, "device_pixel_ratio", 1.0)
    px, py = frame.plotter.ax.transData.transform((x, y))
    height = frame.canvas.get_width_height(physical=True)[1]
    return int(round(px / ratio)), int(round((height - py) / ratio))


def _tk_event(main_window, frame, sequence, x, y):
    tx, ty = _tk_position(frame, x, y)
    frame.canvas.get_tk_widget().event_generate(sequence, x=tx, y=ty)
    main_window.update()


def test_real_tk_events_click_drag_and_right_click(main_window, frame):
    """走一遍 Tk → matplotlib → 边界页的完整事件链：按键编号、按下/松开配对、坐标换算。"""
    for x, y in [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]:
        _tk_event(main_window, frame, "<ButtonPress-1>", x, y)
        _tk_event(main_window, frame, "<ButtonRelease-1>", x, y)
    assert frame.boundary == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]

    _tk_event(main_window, frame, "<ButtonPress-1>", 100.0, 100.0)      # 按住第 3 个顶点拖到测点 (80, 80)
    for x, y in [(95.0, 95.0), (88.0, 88.0), (80.5, 79.6)]:
        _tk_event(main_window, frame, "<Motion>", x, y)
    _tk_event(main_window, frame, "<ButtonRelease-1>", 80.5, 79.6)
    assert frame.boundary == [(0.0, 0.0), (100.0, 0.0), (80.0, 80.0), (0.0, 100.0)]

    right = "<ButtonPress-2>" if main_window.tk.call("tk", "windowingsystem") == "aqua" else "<ButtonPress-3>"
    _tk_event(main_window, frame, right, 100.0, 0.0)                    # 右键点在顶点上：删掉它
    assert frame.boundary == [(0.0, 0.0), (80.0, 80.0), (0.0, 100.0)]
