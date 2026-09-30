"""
Survey-drawing visual tokens and ttk theme for the TIN earthwork GUI.

Colors follow 运行流程图.html (paper surface, ink, cut/fill/zero).
"""
from __future__ import annotations

import platform
import tkinter as tk
from tkinter import ttk
from typing import Tuple

# Tokens from 运行流程图.html
COLORS = {
    "bg": "#F4F6F7",
    "surface": "#FFFFFF",
    "sheet": "#EEF1F2",
    "ink": "#172029",
    "dim": "#5C6B78",
    "rule": "#D6DDE1",
    "accent": "#2C3E50",
    "accent_hover": "#3D5568",
    "accent_pressed": "#1A2833",
    "cut": "#C6453C",
    "fill": "#1F8C82",
    "zero": "#A8770D",
    "tab": "#E2E7EA",
    "header_sub": "#B8C4CE",
    "status_bg": "#E8EEF1",
    "white": "#FFFFFF",
    "disabled": "#8C9BA5",
    "flash": "#FBEFD2",      # 状态栏操作反馈的短暂高亮
    "cut_bg": "#FBECEA",     # 结果卡片底色
    "fill_bg": "#E6F4F2",
}

PLOT_COLORS = {
    "cut": COLORS["cut"],
    "fill": COLORS["fill"],
    "zero": COLORS["zero"],
    "boundary": COLORS["accent"],
    "tin": "#B4BFC6",
    "points": COLORS["ink"],
    "selected": COLORS["cut"],
    "text_bg": COLORS["sheet"],
}

_FONT_FAMILY = None


def resolve_font_family(root: tk.Misc) -> str:
    """Pick a CJK-capable UI font for the current platform."""
    global _FONT_FAMILY
    if _FONT_FAMILY:
        return _FONT_FAMILY

    import tkinter.font as tkfont

    available = set(tkfont.families(root))
    system = platform.system()
    if system == "Darwin":
        candidates = ("PingFang SC", "Hiragino Sans GB", "Heiti SC", "Songti SC")
    elif system == "Windows":
        candidates = ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei")
    else:
        candidates = ("Noto Sans CJK SC", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Droid Sans Fallback")

    for name in candidates:
        if name in available:
            _FONT_FAMILY = name
            return name

    _FONT_FAMILY = tkfont.nametofont("TkDefaultFont").actual()["family"]
    return _FONT_FAMILY


def font(size: int = 11, weight: str = "normal") -> Tuple:
    family = _FONT_FAMILY or "TkDefaultFont"
    if weight == "bold":
        return (family, size, "bold")
    return (family, size)


def mono_font(size: int = 10) -> Tuple:
    system = platform.system()
    if system == "Darwin":
        return ("Menlo", size)
    if system == "Windows":
        return ("Consolas", size)
    return ("DejaVu Sans Mono", size)


def style_text_widget(widget: tk.Text) -> None:
    widget.configure(
        background=COLORS["surface"],
        foreground=COLORS["ink"],
        insertbackground=COLORS["ink"],
        selectbackground=COLORS["accent"],
        selectforeground=COLORS["white"],
        highlightthickness=1,
        highlightbackground=COLORS["rule"],
        highlightcolor=COLORS["accent"],
        borderwidth=0,
        relief="flat",
        font=mono_font(10),
    )


def apply_theme(root: tk.Tk) -> ttk.Style:
    """Configure the clam ttk theme to the survey-drawing palette."""
    family = resolve_font_family(root)
    root.configure(bg=COLORS["bg"])
    # 改具名字体而不是 option_add("*Font")：后者会作为每个 ttk 控件自身的 font 选项，
    # 盖掉样式里的字号和粗体（标题、主按钮、结果卡片都会变成 11 号常规字）
    import tkinter.font as tkfont
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
        tkfont.nametofont(name, root).configure(family=family, size=11)

    style = ttk.Style(root)
    style.theme_use("clam")

    body = font(11)
    body_bold = font(11, "bold")
    small = font(10)
    heading = font(10, "bold")

    style.configure(
        ".",
        font=body,
        background=COLORS["bg"],
        foreground=COLORS["ink"],
        bordercolor=COLORS["rule"],
        darkcolor=COLORS["rule"],
        lightcolor=COLORS["bg"],
        troughcolor=COLORS["sheet"],
        focuscolor=COLORS["bg"],
    )

    style.configure("TFrame", background=COLORS["bg"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["ink"], font=body)
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["dim"], font=small)
    style.configure("Accent.TLabel", background=COLORS["bg"], foreground=COLORS["accent"], font=body)
    style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["ink"], font=body_bold)
    style.configure("Hint.TLabel", background=COLORS["bg"], foreground=COLORS["dim"], font=font(9))

    style.configure(
        "TLabelframe",
        background=COLORS["bg"],
        bordercolor=COLORS["rule"],
        relief="solid",
        borderwidth=1,
        padding=10,
    )
    style.configure(
        "TLabelframe.Label",
        background=COLORS["bg"],
        foreground=COLORS["ink"],
        font=heading,
    )

    style.configure(
        "TButton",
        background=COLORS["surface"],
        foreground=COLORS["ink"],
        bordercolor=COLORS["rule"],
        lightcolor=COLORS["surface"],
        darkcolor=COLORS["rule"],
        font=body,
        padding=(10, 5),
    )
    style.map(
        "TButton",
        background=[("active", COLORS["tab"]), ("pressed", COLORS["rule"]), ("disabled", COLORS["sheet"])],
        foreground=[("disabled", COLORS["disabled"])],
        bordercolor=[("disabled", COLORS["rule"])],
    )

    style.configure(
        "Accent.TButton",
        background=COLORS["accent"],
        foreground=COLORS["white"],
        bordercolor=COLORS["accent"],
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"],
        focuscolor=COLORS["accent"],
        font=body_bold,
        padding=(12, 6),
    )
    style.map(
        "Accent.TButton",
        background=[
            ("active", COLORS["accent_hover"]),
            ("pressed", COLORS["accent_pressed"]),
            ("disabled", COLORS["disabled"]),
        ],
        foreground=[("disabled", COLORS["header_sub"])],
        bordercolor=[
            ("active", COLORS["accent_hover"]),
            ("pressed", COLORS["accent_pressed"]),
            ("disabled", COLORS["disabled"]),
        ],
        lightcolor=[
            ("active", COLORS["accent_hover"]),
            ("pressed", COLORS["accent_pressed"]),
            ("disabled", COLORS["disabled"]),
        ],
        darkcolor=[
            ("active", COLORS["accent_hover"]),
            ("pressed", COLORS["accent_pressed"]),
            ("disabled", COLORS["disabled"]),
        ],
    )

    style.configure(
        "TCheckbutton",
        background=COLORS["bg"],
        foreground=COLORS["ink"],
        focuscolor=COLORS["bg"],
        font=body,
    )
    style.map(
        "TCheckbutton",
        background=[("active", COLORS["bg"])],
        indicatorcolor=[("selected", COLORS["accent"]), ("!selected", COLORS["surface"])],
    )

    style.configure(
        "TEntry",
        fieldbackground=COLORS["surface"],
        foreground=COLORS["ink"],
        bordercolor=COLORS["rule"],
        lightcolor=COLORS["rule"],
        darkcolor=COLORS["rule"],
        insertcolor=COLORS["ink"],
        padding=4,
        font=body,
    )

    style.configure(
        "TNotebook",
        background=COLORS["bg"],
        bordercolor=COLORS["rule"],
        tabmargins=(4, 6, 4, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=COLORS["tab"],
        foreground=COLORS["ink"],
        bordercolor=COLORS["rule"],
        lightcolor=COLORS["tab"],
        padding=(14, 7),
        font=body,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS["surface"]), ("disabled", COLORS["sheet"])],
        foreground=[("selected", COLORS["accent"]), ("disabled", COLORS["disabled"])],
        lightcolor=[("selected", COLORS["surface"])],
    )

    style.configure(
        "Treeview",
        background=COLORS["surface"],
        foreground=COLORS["ink"],
        fieldbackground=COLORS["surface"],
        bordercolor=COLORS["rule"],
        lightcolor=COLORS["rule"],
        darkcolor=COLORS["rule"],
        rowheight=26,
        font=body,
    )
    style.configure(
        "Treeview.Heading",
        background=COLORS["tab"],
        foreground=COLORS["ink"],
        bordercolor=COLORS["rule"],
        relief="flat",
        font=heading,
        padding=(6, 5),
    )
    style.map(
        "Treeview",
        background=[("selected", COLORS["accent"])],
        foreground=[("selected", COLORS["white"])],
    )
    style.map(
        "Treeview.Heading",
        background=[("active", COLORS["rule"])],
    )

    style.configure(
        "TProgressbar",
        background=COLORS["fill"],
        troughcolor=COLORS["tab"],
        bordercolor=COLORS["rule"],
        lightcolor=COLORS["fill"],
        darkcolor=COLORS["fill"],
    )

    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=COLORS["tab"],
            troughcolor=COLORS["sheet"],
            bordercolor=COLORS["rule"],
            arrowcolor=COLORS["ink"],
            darkcolor=COLORS["tab"],
            lightcolor=COLORS["tab"],
        )

    style.configure("TSeparator", background=COLORS["rule"])
    style.configure("Card.TFrame", background=COLORS["surface"], relief="solid", borderwidth=1)
    for name, fg, bg in (
        ("Cut", COLORS["cut"], COLORS["cut_bg"]),
        ("Fill", COLORS["fill"], COLORS["fill_bg"]),
        ("Net", COLORS["ink"], COLORS["surface"]),
    ):
        style.configure(f"{name}Card.TFrame", background=bg)
        style.configure(f"{name}CardTitle.TLabel", background=bg, foreground=COLORS["dim"], font=small)
        style.configure(f"{name}CardValue.TLabel", background=bg, foreground=fg, font=font(15, "bold"))
    style.configure("Status.TLabel", background=COLORS["status_bg"], foreground=COLORS["dim"], font=small)

    return style
