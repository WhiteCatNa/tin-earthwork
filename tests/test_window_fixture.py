"""公共窗口夹具的重试逻辑（针对 Windows 上 Tcl 初始化的偶发错误）。"""
import tkinter as tk

import pytest

import conftest


class _FakeWindow:
    withdrawn = False

    def withdraw(self):
        self.withdrawn = True


def test_create_main_window_retries_tcl_init_flake(monkeypatch):
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise tk.TclError("Can't find a usable tk.tcl in the following directories: ...")
        return _FakeWindow()

    monkeypatch.setattr(conftest.app_main, "MainApplication", flaky)
    window = conftest.create_main_window(delay=0)
    assert len(attempts) == 3
    assert window.withdrawn


def test_create_main_window_gives_up_after_repeated_flakes(monkeypatch):
    attempts = []

    def always_flaky():
        attempts.append(1)
        raise tk.TclError("Can't find a usable init.tcl in the following directories: ...")

    monkeypatch.setattr(conftest.app_main, "MainApplication", always_flaky)
    with pytest.raises(tk.TclError, match="usable init.tcl"):
        conftest.create_main_window(attempts=3, delay=0)
    assert len(attempts) == 3


def test_create_main_window_does_not_retry_other_errors(monkeypatch):
    attempts = []

    def broken():
        attempts.append(1)
        raise tk.TclError('invalid command name ".!frame"')

    monkeypatch.setattr(conftest.app_main, "MainApplication", broken)
    with pytest.raises(tk.TclError, match="invalid command name"):
        conftest.create_main_window(delay=0)
    assert len(attempts) == 1
