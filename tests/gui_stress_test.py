#!/usr/bin/env python3
"""
GUI 自动化点击压力测试：模拟真实鼠标点击/移动，捕获异常并测量每步耗时。

用法:
    python tests/gui_stress_test.py [--points 1000]
"""
import argparse
import os
import sys
import time
import traceback

# 测试脚本位于 tests/ 下，需将项目根目录加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use('TkAgg')

import tkinter as tk
from tkinter import messagebox
from matplotlib.backend_bases import MouseEvent, MouseButton

import main as app_main
from core.calculator import SurveyPoint
from run_full_test import generate_test_data

ERRORS = []
TIMINGS = []


def _record_error(where, exc):
    ERRORS.append((where, "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))))
    print(f"  !! 异常 [{where}]: {type(exc).__name__}: {exc}")


class timed:
    """测量一段交互耗时；超过阈值标记为慢。"""

    def __init__(self, label, threshold=0.30):
        self.label = label
        self.threshold = threshold

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter() - self.t0
        TIMINGS.append((self.label, elapsed, self.threshold))
        flag = "  <== 慢" if elapsed > self.threshold else ""
        print(f"  {self.label:<38} {elapsed * 1000:8.1f} ms{flag}")
        if exc is not None:
            _record_error(self.label, exc)
            return True  # 继续跑完其余步骤
        return False


def silence_dialogs():
    """自动应答所有模态对话框，避免测试阻塞。"""
    messagebox.showinfo = lambda *a, **k: "ok"
    messagebox.showwarning = lambda *a, **k: "ok"
    messagebox.showerror = lambda *a, **k: "ok"
    messagebox.askokcancel = lambda *a, **k: True
    messagebox.askyesnocancel = lambda *a, **k: False  # 退出时不保存


def data_to_pixel(ax, x, y):
    return ax.transData.transform((x, y))


def fire_pixel(canvas, name, px, py, button=None, step=0):
    """在画布像素位置 (px, py) 处合成一个 matplotlib 鼠标事件。"""
    event = MouseEvent(name, canvas, px, py, button=button, step=step)
    canvas.callbacks.process(name, event)


def fire(canvas, ax, name, x, y, button=None, step=0):
    """在数据坐标 (x, y) 处合成一个 matplotlib 鼠标事件。"""
    fire_pixel(canvas, name, *data_to_pixel(ax, x, y), button=button, step=step)


def click(canvas, ax, x, y, button=MouseButton.LEFT):
    """单击 = 按下 + 松开（边界页按住拖动是平移或移动顶点，松开时没动才算单击）。"""
    fire(canvas, ax, 'button_press_event', x, y, button)
    fire(canvas, ax, 'button_release_event', x, y, button)


def drag(canvas, ax, start, end, steps=8):
    """按住左键从 start 拖到 end（数据坐标）。像素位置按起拖时的视野算，拖动中视野可能在变。"""
    x0, y0 = data_to_pixel(ax, *start)
    x1, y1 = data_to_pixel(ax, *end)
    fire_pixel(canvas, 'button_press_event', x0, y0, MouseButton.LEFT)
    for i in range(1, steps + 1):
        fire_pixel(canvas, 'motion_notify_event', x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    fire_pixel(canvas, 'button_release_event', x1, y1, MouseButton.LEFT)


def pump(root, seconds=0.05):
    """驱动 Tk 事件循环，让 after 回调与重绘真实发生。"""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.001)


def wait_for(root, predicate, timeout=60.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        root.update()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def run(point_count):
    silence_dialogs()
    print("=" * 70)
    print("GUI 自动化点击测试")
    print("=" * 70)

    import tempfile
    os.environ["TIN_EARTHWORK_RECENT"] = os.path.join(tempfile.mkdtemp(prefix="tin_recent_"), "recent.json")

    points = generate_test_data()[:point_count]
    print(f"\n测试数据: {len(points)} 点\n")

    app = app_main.MainApplication()
    app.report_callback_exception = lambda exc, val, tb: _record_error(
        "tk_callback", val if isinstance(val, BaseException) else Exception(str(val))
    )
    app.update()

    print("--- 阶段 1: 数据导入页 ---")
    with timed("导入 1000 点 (回调)", 2.0):
        app.import_frame.points = points
        app.import_frame._update_preview()
        app.import_frame._update_stats()
        app.import_frame._confirm_points()
        pump(app, 0.2)

    print("\n--- 阶段 2: 边界编辑页 (鼠标点击) ---")
    bf = app.boundary_frame
    bcanvas, bax = bf.canvas, bf.plotter.ax

    with timed("自动生成边界按钮", 1.0):
        bf._create_auto_boundary()
        pump(app, 0.1)

    with timed("沿测点外轮廓生成", 2.0):
        bf._create_outline_boundary()
        pump(app, 0.1)
    if not bf._boundary_is_valid():
        ERRORS.append(("outline", f"外轮廓边界无效: {len(bf.boundary)} 点"))

    with timed("按点号连线", 1.0):
        corners = [(10, 10), (180, 12), (185, 140), (15, 138)]
        nearest = [min(points, key=lambda p: (p.x - x) ** 2 + (p.y - y) ** 2) for x, y in corners]
        bf.id_sequence_var.set(",".join(str(p.id) for p in nearest))
        bf._connect_by_ids()
        pump(app, 0.1)
    if bf.boundary != [(p.x, p.y) for p in nearest]:
        ERRORS.append(("connect_by_ids", f"按点号连线结果不对: {bf.boundary}"))

    with timed("理顺交叉的连线", 1.0):
        bf.id_sequence_var.set(",".join(str(p.id) for p in (nearest[0], nearest[2], nearest[1], nearest[3])))
        bf._connect_by_ids()
        bf._untangle()
        pump(app, 0.1)
    if not bf._boundary_is_valid():
        ERRORS.append(("untangle", f"理顺后边界仍无效: {bf.boundary}"))

    with timed("粘贴坐标", 1.0):
        bf.clipboard_get = lambda: "点号\tX\tY\nJ1\t20\t20\nJ2\t160\t20\nJ3\t160\t120\nJ4\t20\t120\n"
        bf._paste_coordinates()
        del bf.clipboard_get
        pump(app, 0.1)
    if bf.boundary != [(20.0, 20.0), (160.0, 20.0), (160.0, 120.0), (20.0, 120.0)]:
        ERRORS.append(("paste", f"粘贴坐标结果不对: {bf.boundary}"))

    with timed("导出边界 CSV + DXF", 1.0):
        from tkinter import filedialog
        export_dir = tempfile.mkdtemp(prefix="tin_boundary_")
        for name in ("边界.csv", "边界.dxf"):
            filedialog.asksaveasfilename = lambda *a, _name=name, **k: os.path.join(export_dir, _name)
            bf._export_boundary()
        pump(app, 0.05)
    if sorted(os.listdir(export_dir)) != ["边界.csv", "边界.dxf"]:
        ERRORS.append(("export_boundary", f"导出的文件: {os.listdir(export_dir)}"))

    with timed("导入底图 + 选底图线", 2.0):
        backdrop_path = os.path.join(export_dir, "底图.dxf")
        with open(backdrop_path, "w", encoding="utf-8") as handle:
            handle.write("0\nSECTION\n2\nENTITIES\n0\nLWPOLYLINE\n8\n红线\n90\n4\n70\n1\n"
                         "10\n30\n20\n30\n10\n150\n20\n30\n10\n150\n20\n110\n10\n30\n20\n110\n"
                         "0\nLINE\n8\n道路\n10\n0\n20\n140\n11\n200\n21\n140\n0\nENDSEC\n0\nEOF\n")
        filedialog.askopenfilename = lambda *a, **k: backdrop_path
        bf._import_backdrop()
        bf._set_pick_mode(True)
        click(bcanvas, bax, 90, 30)
        pump(app, 0.1)
    if bf.boundary != [(30.0, 30.0), (150.0, 30.0), (150.0, 110.0), (30.0, 110.0)]:
        ERRORS.append(("pick_backdrop", f"选底图线结果不对: {bf.boundary}"))

    with timed("清空边界按钮", 0.5):
        bf._clear_boundary()
        pump(app, 0.1)

    # 逐点左键点击画边界
    click_pts = [(10, 10), (180, 12), (185, 140), (15, 138), (100, 145)]
    for i, (x, y) in enumerate(click_pts, 1):
        with timed(f"左键添加边界点 {i}", 0.5):
            click(bcanvas, bax, x, y)
            pump(app, 0.05)
    if len(bf.boundary) != len(click_pts):
        ERRORS.append(("click_add", f"单击 {len(click_pts)} 次后边界有 {len(bf.boundary)} 个点"))

    # 鼠标移动 (橡皮筋预览) —— 高频事件
    with timed("鼠标移动 x60 (边界预览)", 1.5):
        for i in range(60):
            fire(bcanvas, bax, 'motion_notify_event', 20 + i * 2, 60 + i)
        pump(app, 0.1)

    with timed("滚轮放大 x4 再缩小 x4", 1.5):
        for step in (1, 1, 1, 1, -1, -1, -1, -1):
            fire(bcanvas, bax, 'scroll_event', 90, 70, button='up' if step > 0 else 'down', step=step)
            pump(app, 0.02)

    with timed("拖动空白处平移", 1.0):
        drag(bcanvas, bax, (90, 70), (60, 50))
        pump(app, 0.05)
    if len(bf.boundary) != len(click_pts):
        ERRORS.append(("pan", f"平移后边界点数变了: {len(bf.boundary)}"))

    with timed("全图按钮", 0.5):
        bf._reset_view()
        pump(app, 0.05)

    with timed("拖动顶点", 1.0):
        before = bf.boundary[4]
        drag(bcanvas, bax, before, (before[0] + 12, before[1] - 25))
        pump(app, 0.05)
    if bf.boundary[4] == before or len(bf.boundary) != len(click_pts):
        ERRORS.append(("drag_vertex", f"拖动顶点没有生效: {bf.boundary}"))

    with timed("点在边上插入顶点", 0.5):
        (ax0, ay0), (ax1, ay1) = bf.boundary[0], bf.boundary[1]
        click(bcanvas, bax, (ax0 + ax1) / 2, (ay0 + ay1) / 2)
        pump(app, 0.05)
    if len(bf.boundary) != len(click_pts) + 1:
        ERRORS.append(("insert_vertex", f"点在边上后边界有 {len(bf.boundary)} 个点"))

    with timed("右键删除光标下的顶点", 0.5):
        fire(bcanvas, bax, 'button_press_event', *bf.boundary[1], MouseButton.RIGHT)
        pump(app, 0.05)

    with timed("右键删除最后一点", 0.5):
        fire(bcanvas, bax, 'button_press_event', 100, 70, MouseButton.RIGHT)
        pump(app, 0.05)
    if len(bf.boundary) != 4:
        ERRORS.append(("right_click", f"两次右键删除后边界有 {len(bf.boundary)} 个点"))

    with timed("撤销上一步按钮", 0.5):
        bf._undo_last_point()
        pump(app, 0.05)
    if len(bf.boundary) != 5:
        ERRORS.append(("undo", f"撤销后边界有 {len(bf.boundary)} 个点"))

    with timed("再删最后一点 + 确认边界", 1.0):
        fire(bcanvas, bax, 'button_press_event', 100, 70, MouseButton.RIGHT)
        pump(app, 0.05)
        bf._confirm_boundary()
        pump(app, 0.3)

    if app.calc_frame is None:
        print("  !! 边界确认后计算页未创建，后续阶段跳过")
        return finish(app)

    print("\n--- 阶段 3: 计算页 ---")
    cf = app.calc_frame

    with timed("从数据估算高程按钮", 0.5):
        cf._estimate_elevation()
        pump(app, 0.05)

    with timed("开始计算按钮 -> 完成", 30.0):
        cf._run_calculation()
        ok = wait_for(app, lambda: cf.result is not None, timeout=60)
        if not ok:
            raise RuntimeError("计算超时未返回结果")
        pump(app, 0.5)

    print(f"  三角形数: {cf.result.triangle_count}, 混合: {cf.result.mixed_triangle_count}")

    with timed("等待明细表分批加载完成", 60.0):
        wait_for(app, lambda: cf._detail_load_after_id is None, timeout=90)

    # 复选框切换 —— 每次都会整图重绘
    for label, var in (
        ("显示TIN网格", cf.show_tin_var),
        ("显示零填挖线", cf.show_contour_var),
        ("显示测量点", cf.show_points_var),
        ("显示计算边界", cf.show_boundary_var),
    ):
        with timed(f"取消勾选 {label}", 0.5):
            var.set(False)
            cf._refresh_plot()
            pump(app, 0.05)
        with timed(f"重新勾选 {label}", 0.5):
            var.set(True)
            cf._refresh_plot()
            pump(app, 0.05)

    print("\n--- 阶段 4: 结果图鼠标交互 ---")
    ccanvas, cax = cf.canvas, cf.plotter.ax

    with timed("结果图鼠标移动 x40 (hover)", 1.0):
        for i in range(40):
            fire(ccanvas, cax, 'motion_notify_event', 30 + i * 3, 40 + i)
        pump(app, 0.1)

    for i, (x, y) in enumerate([(50, 50), (100, 75), (150, 100), (60, 120)], 1):
        with timed(f"点击三角形 {i}", 0.8):
            fire(ccanvas, cax, 'button_press_event', x, y, MouseButton.LEFT)
            pump(app, 0.1)

    with timed("连续点击同一位置 x10", 3.0):
        for _ in range(10):
            fire(ccanvas, cax, 'button_press_event', 100, 75, MouseButton.LEFT)
        pump(app, 0.2)

    print("\n--- 阶段 5: 选项卡切换 ---")
    for idx, name in enumerate(["计算结果图", "汇总表", "计算明细", "三角形详情"]):
        with timed(f"切换到 {name}", 0.6):
            cf.notebook.select(idx)
            pump(app, 0.08)

    print("\n--- 阶段 6: 重复计算 (第二次) ---")
    with timed("第二次开始计算 -> 完成", 30.0):
        cf.result = None
        cf._run_calculation()
        if not wait_for(app, lambda: cf.result is not None, timeout=60):
            raise RuntimeError("第二次计算超时")
        pump(app, 0.5)

    print("\n--- 阶段 7: 返回边界页改边界后重新确认 ---")
    with timed("修改边界并再次确认", 2.0):
        app.notebook.select(1)
        # 直接设置边界点（绕过 fire() 的 inaxes 限制，模拟真实用户完成画点的效果）
        bf.boundary = [(20.0, 20.0), (150.0, 20.0), (150.0, 120.0), (20.0, 120.0)]
        bf._update_boundary_list()
        bf._confirm_boundary()
        pump(app, 0.3)
    new_boundary = list(app.calc_frame.boundary)
    expected = [(20.0, 20.0), (150.0, 20.0), (150.0, 120.0), (20.0, 120.0)]
    new_boundary_applied = new_boundary == expected
    print(f"  计算页边界是否同步: {new_boundary_applied}  {new_boundary}")
    if not new_boundary_applied:
        ERRORS.append(("boundary_sync", f"重新确认边界后，计算页仍使用旧边界: {new_boundary}"))

    print("\n--- 阶段 8: 导出 (写入临时目录) ---")
    # 第7阶段改了边界，cf.result 已被清空；重新计算后才能导出
    with timed("导出前重新计算", 30.0):
        cf._run_calculation()
        if not wait_for(app, lambda: cf.result is not None, timeout=60):
            raise RuntimeError("导出前重新计算超时")
        pump(app, 0.3)
    import tempfile
    from tkinter import filedialog
    tmpdir = tempfile.mkdtemp(prefix="tin_gui_")

    for label, method, fname in (
        ("导出 Excel 报告", cf._export_excel, "r.xlsx"),
        ("导出 CSV 明细", cf._export_csv, "r.csv"),
        ("导出 DXF", cf._export_dxf, "r.dxf"),
        ("导出 PDF 计算书", cf._export_pdf, "r.pdf"),
        ("导出高清图片", cf._export_image, "r.png"),
        ("生成文本报告", cf._export_text, "r.txt"),
    ):
        target = os.path.join(tmpdir, fname)
        filedialog.asksaveasfilename = lambda *a, **k: target
        with timed(label, 20.0):
            method()
            wait_for(app, lambda: cf._export_thread is None, timeout=60)
            pump(app, 0.05)
        if not os.path.exists(target):
            ERRORS.append((label, f"导出后文件不存在: {target}"))

    print("\n--- 阶段 9: 关闭窗口 ---")
    with timed("关闭应用", 3.0):
        app._on_closing()
        for _ in range(20):
            try:
                app.update()
            except tk.TclError:
                break
            time.sleep(0.01)

    return finish(app, already_closed=True)


def finish(app, already_closed=False):
    if not already_closed:
        try:
            app._on_closing()
        except Exception:
            pass

    print("\n" + "=" * 70)
    slow = [t for t in TIMINGS if t[1] > t[2]]
    print(f"耗时统计: {len(TIMINGS)} 步, 超阈值 {len(slow)} 步")
    for label, elapsed, threshold in slow:
        print(f"  慢: {label} = {elapsed * 1000:.0f} ms (阈值 {threshold * 1000:.0f} ms)")

    print(f"\n错误数: {len(ERRORS)}")
    for where, tb in ERRORS:
        print(f"\n--- {where} ---")
        print(tb)
    print("=" * 70)
    return 0 if not ERRORS and not slow else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--points", type=int, default=1000)
    args = parser.parse_args()
    sys.exit(run(args.points))
