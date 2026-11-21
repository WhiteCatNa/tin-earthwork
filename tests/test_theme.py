"""界面主题：卡片里的控件换成卡片底色，步骤导航可切换页面。"""
import tkinter as tk
from tkinter import ttk

import pytest

import main as app_main
from conftest import create_main_window
from gui.theme import COLORS
from run_full_test import generate_test_data


@pytest.fixture
def app(tmp_path, monkeypatch):
    """卡片样式在控件显示（<Map>）时才换上，Windows 上被 withdraw 的窗口不发这个事件，所以窗口要真的显示。"""
    monkeypatch.setenv("TIN_EARTHWORK_RECENT", str(tmp_path / "recent.json"))
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda *a, **k: "ok")
    window = create_main_window(hidden=False)
    window.update()
    yield window
    window.destroy()


def _background(widget):
    style = str(widget.cget("style")) or widget.winfo_class()
    return ttk.Style(widget).lookup(style, "background")


def test_widgets_inside_cards_use_card_background(app):
    frame = app.import_frame
    card_label = next(w for w in frame.file_frame.winfo_children() if w.winfo_class() == "TLabel")
    assert str(card_label.cget("style")).startswith("Card.")
    assert _background(card_label) == COLORS["surface"]
    # 页面本身仍是页面底色
    assert _background(frame) == COLORS["bg"]


def test_card_restyle_keeps_custom_style(app):
    label = ttk.Label(app.import_frame.file_frame, text="x", style="Muted.TLabel")
    label.pack()
    app.update()
    assert str(label.cget("style")) == "Card.Muted.TLabel"
    assert ttk.Style(app).lookup("Card.Muted.TLabel", "foreground") == COLORS["dim"]


def test_step_nav_switches_only_enabled_pages(app):
    app._goto_step(1)
    assert app.notebook.index("current") == 0  # 边界页尚未启用

    app.import_frame.points = generate_test_data()[:200]
    app.import_frame._confirm_points()
    app.update()
    assert app.notebook.index("current") == 1
    app._goto_step(0)
    app.update()
    assert app.notebook.index("current") == 0
    assert app.progress_steps[0].cget("bg") == COLORS["accent_soft"]
    assert app.progress_steps[0].cget("text").startswith("✓")
