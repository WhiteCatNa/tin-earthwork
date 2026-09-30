"""测试公共夹具。"""
import sys
import time
import tkinter as tk

import pytest

import main as app_main

# Windows 上同一进程反复创建 Tk 根窗口时，Tcl 偶尔会报找不到 init.tcl / tk.tcl
# （文件其实存在），重试即可。只对这一种错误重试，别的 TclError 照常抛出。
TCL_INIT_FLAKE = "Can't find a usable"


def hide_window(window: tk.Tk) -> None:
    """让测试窗口不出现在屏幕上。

    macOS 的 Tk 有个问题：在被 withdraw 的、非第一个根窗口里创建 Canvas 内嵌控件
    （计算页的可滚动设置栏就是这样实现的），事件循环会停不下来。所以 macOS 上不用
    withdraw，而是把窗口设为全透明并移到屏幕外——窗口仍处于“已显示”状态。
    """
    if sys.platform == "darwin":
        window.attributes("-alpha", 0.0)
        window.geometry("+30000+30000")
    else:
        window.withdraw()


def create_main_window(attempts: int = 4, delay: float = 0.5, hidden: bool = True):
    """创建主窗口（默认不可见），遇到 Tcl 初始化的偶发错误时重试。

    排版测试需要窗口按真实尺寸显示才能量控件大小，传 hidden=False。
    """
    for attempt in range(1, attempts + 1):
        try:
            window = app_main.MainApplication()
        except tk.TclError as error:
            if TCL_INIT_FLAKE not in str(error) or attempt == attempts:
                raise
            time.sleep(delay)
            continue
        if hidden:
            hide_window(window)
        return window


@pytest.fixture
def main_window(tmp_path, monkeypatch):
    """不可见的主窗口；最近工程列表写到临时目录，不碰用户的真实记录。"""
    monkeypatch.setenv("TIN_EARTHWORK_RECENT", str(tmp_path / "recent.json"))
    window = create_main_window()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass  # 测试里已经关闭了窗口
