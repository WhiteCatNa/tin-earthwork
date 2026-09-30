"""测试公共夹具。"""
import time
import tkinter as tk

import pytest

import main as app_main

# Windows 上同一进程反复创建 Tk 根窗口时，Tcl 偶尔会报找不到 init.tcl / tk.tcl
# （文件其实存在），重试即可。只对这一种错误重试，别的 TclError 照常抛出。
TCL_INIT_FLAKE = "Can't find a usable"


def create_main_window(attempts: int = 4, delay: float = 0.5):
    """创建隐藏的主窗口，遇到 Tcl 初始化的偶发错误时重试。"""
    for attempt in range(1, attempts + 1):
        try:
            window = app_main.MainApplication()
        except tk.TclError as error:
            if TCL_INIT_FLAKE not in str(error) or attempt == attempts:
                raise
            time.sleep(delay)
            continue
        window.withdraw()
        return window


@pytest.fixture
def main_window(tmp_path, monkeypatch):
    """隐藏的主窗口；最近工程列表写到临时目录，不碰用户的真实记录。"""
    monkeypatch.setenv("TIN_EARTHWORK_RECENT", str(tmp_path / "recent.json"))
    window = create_main_window()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass  # 测试里已经关闭了窗口
