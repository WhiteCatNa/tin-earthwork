"""
界面主题：配色、字体与 ttk 样式。

所有窗口只引用这里的颜色和样式名，换主题只改本文件。控件的圆角、勾选框等用
Pillow 现画成图片元素（ttk "image" element），不依赖第三方主题库。

页面底色是 bg，LabelFrame 画成白色卡片（标题在卡片外）。卡片里的 Frame/Label 等
控件在显示时由 _on_map 自动换成 "Card.*" 样式，所以各窗口的布局代码不用关心自己
放在卡片里还是页面上。
"""
from __future__ import annotations

import os
import platform
import tkinter as tk
from tkinter import ttk
from typing import Dict, Tuple

# 每套主题的颜色。键名是各窗口与绘图共用的语义名。
PALETTES: Dict[str, Dict[str, str]] = {
    # 清爽白：白色页头，蓝色强调
    "light": {
        "bg": "#F2F4F7",
        "surface": "#FFFFFF",
        "sheet": "#FFFFFF",
        "ink": "#1D2939",
        "dim": "#667085",
        "rule": "#E4E7EC",
        "border": "#D0D5DD",
        "accent": "#2563EB",
        "accent_hover": "#1D4ED8",
        "accent_pressed": "#1E40AF",
        "accent_soft": "#EAF1FF",
        "on_accent": "#FFFFFF",
        "cut": "#D9534A",
        "fill": "#1D9A8A",
        "zero": "#D49B12",
        "tab": "#F7F8FA",
        "control": "#FFFFFF",
        "control_hover": "#F4F6F9",
        "control_pressed": "#E9EDF2",
        "field": "#FFFFFF",
        "select_bg": "#DCE7FE",
        "select_fg": "#1D2939",
        "header_bg": "#FFFFFF",
        "header_fg": "#1D2939",
        "header_sub": "#667085",
        "header_control": "#F2F4F7",
        "header_control_hover": "#E4E7EC",
        "nav_bg": "#FFFFFF",
        "status_bg": "#FFFFFF",
        "white": "#FFFFFF",
        "disabled": "#98A2B3",
        "tin": "#C3CBD5",
    },
    # 工程深蓝：深色页头，延续原来的藏青色
    "navy": {
        "bg": "#EEF1F4",
        "surface": "#FFFFFF",
        "sheet": "#FFFFFF",
        "ink": "#172029",
        "dim": "#5C6B78",
        "rule": "#DEE3E8",
        "border": "#C9D1D8",
        "accent": "#24405E",
        "accent_hover": "#2F5278",
        "accent_pressed": "#1A3048",
        "accent_soft": "#E6EDF5",
        "on_accent": "#FFFFFF",
        "cut": "#C6453C",
        "fill": "#1F8C82",
        "zero": "#B7830F",
        "tab": "#F5F7F9",
        "control": "#FFFFFF",
        "control_hover": "#F3F5F8",
        "control_pressed": "#E6EAEF",
        "field": "#FFFFFF",
        "select_bg": "#DCE6F1",
        "select_fg": "#172029",
        "header_bg": "#1B2B3C",
        "header_fg": "#FFFFFF",
        "header_sub": "#A9B7C6",
        "header_control": "#2A3D51",
        "header_control_hover": "#365069",
        "nav_bg": "#FFFFFF",
        "status_bg": "#FFFFFF",
        "white": "#FFFFFF",
        "disabled": "#8C9BA5",
        "tin": "#B9C3CB",
    },
    # 深色
    "dark": {
        "bg": "#14171C",
        "surface": "#1E232A",
        "sheet": "#1E232A",
        "ink": "#E4E8EE",
        "dim": "#98A2B0",
        "rule": "#2C333C",
        "border": "#3A434F",
        "accent": "#4C8DFF",
        "accent_hover": "#6A9FFF",
        "accent_pressed": "#3A78E6",
        "accent_soft": "#22324D",
        "on_accent": "#FFFFFF",
        "cut": "#E8675E",
        "fill": "#2FB3A3",
        "zero": "#E0B341",
        "tab": "#252B33",
        "control": "#2A3039",
        "control_hover": "#333A44",
        "control_pressed": "#3C4450",
        "field": "#161A1F",
        "select_bg": "#2B4470",
        "select_fg": "#FFFFFF",
        "header_bg": "#101317",
        "header_fg": "#E4E8EE",
        "header_sub": "#8A94A3",
        "header_control": "#232931",
        "header_control_hover": "#2E3540",
        "nav_bg": "#181C22",
        "status_bg": "#101317",
        "white": "#FFFFFF",
        "disabled": "#5D6673",
        "tin": "#4A5361",
    },
}
DEFAULT_THEME = "light"

# 当前主题的颜色；其它模块 import 的是同一个 dict，apply_theme 原地更新
COLORS: Dict[str, str] = dict(PALETTES[DEFAULT_THEME])
PLOT_COLORS = {
    "cut": COLORS["cut"],
    "fill": COLORS["fill"],
    "zero": COLORS["zero"],
    "boundary": COLORS["accent"],
    "tin": COLORS["tin"],
    "points": COLORS["ink"],
    "selected": COLORS["cut"],
    "text_bg": COLORS["sheet"],
}
_PLOT_KEYS = {key: value for key, value in (
    ("cut", "cut"), ("fill", "fill"), ("zero", "zero"), ("boundary", "accent"),
    ("tin", "tin"), ("points", "ink"), ("selected", "cut"), ("text_bg", "sheet"),
)}


def _sync_plot_colors() -> None:
    for key, token in _PLOT_KEYS.items():
        PLOT_COLORS[key] = COLORS[token]


def theme_name() -> str:
    name = os.environ.get("TIN_EARTHWORK_THEME", DEFAULT_THEME)
    return name if name in PALETTES else DEFAULT_THEME


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
        background=COLORS["field"],
        foreground=COLORS["ink"],
        insertbackground=COLORS["ink"],
        selectbackground=COLORS["select_bg"],
        selectforeground=COLORS["select_fg"],
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
        borderwidth=0,
        relief="flat",
        padx=8,
        pady=6,
        font=mono_font(10),
    )


# ---------------------------------------------------------------- 图片元素

_IMAGES: list = []  # PhotoImage 必须保持引用，否则会被回收
_SUPERSAMPLE = 4


def _scale(root: tk.Misc) -> float:
    """高 DPI 屏幕（Windows 150% 等）上按比例放大圆角和勾选框。"""
    try:
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def _image(root, draw, width: int, height: int):
    from PIL import Image, ImageDraw, ImageTk

    s = _SUPERSAMPLE
    img = Image.new("RGBA", (width * s, height * s), (0, 0, 0, 0))
    draw(ImageDraw.Draw(img), s)
    img = img.resize((width, height), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img, master=root)
    _IMAGES.append(photo)
    return photo


def _rounded(root, fill, outline=None, radius=6, size=None, bar=None, corners=None):
    """九宫格用的圆角矩形。bar=(颜色, 高度) 时在底部画一条强调线（选中的页签）。"""
    size = size or radius * 2 + 12  # 九宫格四边各 radius+2，中间留出可拉伸的部分

    def draw(d, s):
        w = size * s
        box = (0, 0, w - 1, w - 1)
        d.rounded_rectangle(box, radius=radius * s, fill=fill, outline=outline,
                            width=s if outline else 0, corners=corners)
        if bar:
            color, height = bar
            d.rounded_rectangle((radius * s // 2, w - height * s, w - 1 - radius * s // 2, w - 1),
                                radius=height * s // 2, fill=color)

    return _image(root, draw, size, size)


def _indicator(root, kind: str, state: str, px: int):
    def draw(d, s):
        w = px * s
        inset = s // 2
        box = (inset, inset, w - 1 - inset, w - 1 - inset)
        on = state == "on"
        disabled = state == "disabled"
        fill = COLORS["accent"] if on else COLORS["field"]
        outline = COLORS["accent"] if on else COLORS["border"]
        if disabled:
            fill, outline = COLORS["control_pressed"], COLORS["border"]
        if kind == "check":
            d.rounded_rectangle(box, radius=4 * s * px // 16, fill=fill, outline=outline, width=s)
            if on:
                d.line([(w * 0.27, w * 0.52), (w * 0.43, w * 0.68), (w * 0.74, w * 0.35)],
                       fill=COLORS["on_accent"], width=int(1.8 * s * px / 16), joint="curve")
        else:
            d.ellipse(box, fill=COLORS["field"] if not disabled else fill,
                      outline=outline, width=s if not on else int(1.6 * s))
            if on:
                c, r = w / 2, w * 0.22
                d.ellipse((c - r, c - r, c + r, c + r), fill=COLORS["accent"])

    return _image(root, draw, px, px)


def _create_elements(root: tk.Misc, style: ttk.Style) -> None:
    k = _scale(root)
    r = round(6 * k)
    card_r = round(8 * k)
    c = COLORS

    def button(name, normal, hover, pressed, disabled, outline_normal=None, outline_disabled=None):
        style.element_create(
            name, "image", _rounded(root, normal, outline_normal, r),
            ("disabled", _rounded(root, disabled, outline_disabled, r)),
            ("pressed", _rounded(root, pressed, outline_normal, r)),
            ("active", _rounded(root, hover, outline_normal, r)),
            border=r + 2, padding=2, sticky="nsew",
        )

    button("Plain.button", c["control"], c["control_hover"], c["control_pressed"], c["tab"],
           c["border"], c["rule"])
    button("Accent.button", c["accent"], c["accent_hover"], c["accent_pressed"], c["disabled"])
    button("Header.button", c["header_control"], c["header_control_hover"], c["header_control_hover"],
           c["header_control"])
    button("Ghost.button", c["surface"], c["control_hover"], c["control_pressed"], c["surface"])

    style.element_create(
        "Rounded.field", "image", _rounded(root, c["field"], c["border"], r),
        ("disabled", _rounded(root, c["tab"], c["rule"], r)),
        ("focus", _rounded(root, c["field"], c["accent"], r)),
        ("readonly", _rounded(root, c["control"], c["border"], r)),
        border=r + 2, padding=2, sticky="nsew",
    )
    style.element_create("Card.border", "image", _rounded(root, c["surface"], c["rule"], card_r),
                         border=card_r + 2, padding=2, sticky="nsew")
    style.element_create("Card.client", "image", _rounded(root, c["surface"], c["rule"], card_r),
                         border=card_r + 2, padding=2, sticky="nsew")

    # 页签：选中时底部一条强调线
    tab_r = round(4 * k)
    style.element_create(
        "Underline.tab", "image", _rounded(root, c["bg"], None, tab_r),
        ("selected", _rounded(root, c["bg"], None, tab_r, bar=(c["accent"], max(2, round(3 * k))))),
        ("active", _rounded(root, c["control_pressed"], None, tab_r)),
        border=tab_r + 2, padding=0, sticky="nsew",
    )

    px = round(16 * k)
    for kind, element in (("check", "Rounded.checkindicator"), ("radio", "Rounded.radioindicator")):
        style.element_create(
            element, "image", _indicator(root, kind, "off", px),
            ("disabled", _indicator(root, kind, "disabled", px)),
            ("selected", _indicator(root, kind, "on", px)),
            sticky="", padding=(0, 0, round(6 * k), 0),
        )

    thumb_r = round(4 * k)
    for orient in ("Vertical", "Horizontal"):
        style.element_create(
            f"Slim.{orient}.thumb", "image", _rounded(root, c["border"], None, thumb_r),
            ("pressed", _rounded(root, c["dim"], None, thumb_r)),
            ("active", _rounded(root, c["disabled"], None, thumb_r)),
            border=thumb_r + 1, padding=0, sticky="nsew",
        )


def _button_layout(element: str):
    return [(element, {"sticky": "nsew", "children": [
        ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]}),
    ]})]


# ---------------------------------------------------------------- 卡片内控件自动换样式

# 圆角控件四角是透明的，透出的是样式的 background，所以按钮、输入框也要换成卡片底色
_CARD_CLASSES = {"TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "TLabelframe", "TSeparator",
                 "TButton", "TEntry", "TMenubutton", "TCombobox"}
_card_styles: set = set()


def _inside_card(widget) -> bool:
    parent = widget.master
    while parent is not None:
        try:
            cls = parent.winfo_class()
        except tk.TclError:
            return False
        if cls == "TLabelframe":
            return True
        if cls == "TNotebook":
            # 结果区的子页签内容是白底卡片；主窗口的步骤页不是
            return str(parent.cget("style")) != "Main.TNotebook"
        if cls in ("Toplevel", "Tk"):
            return False
        parent = parent.master
    return False


def _card_style(style: ttk.Style, base: str) -> str:
    name = "Card." + base
    if name not in _card_styles:
        _card_styles.add(name)
        if base.endswith("TLabelframe"):
            style.layout(name, [("Inner.border", {"sticky": "nsew"})])
            style.configure(name, background=COLORS["surface"])
            style.configure(name + ".Label", background=COLORS["surface"])
        else:
            style.configure(name, background=COLORS["surface"])
            if base.endswith(("TCheckbutton", "TRadiobutton")):
                style.map(name, background=[("active", COLORS["surface"])])
    return name


def _on_map(event) -> None:
    widget = event.widget
    if isinstance(widget, str):
        return
    try:
        cls = widget.winfo_class()
    except tk.TclError:
        return
    if cls == "Canvas":
        # ScrollableFrame 等用页面底色的画布，放进卡片后跟随卡片底色
        try:
            if widget.cget("background") == COLORS["bg"] and _inside_card(widget):
                widget.configure(background=COLORS["surface"])
        except tk.TclError:
            pass
        return
    if cls not in _CARD_CLASSES:
        return
    try:
        current = str(widget.cget("style"))
    except tk.TclError:
        return
    if current.startswith(("Card.", "Page.", "Header.", "Nav.", "Status.")) or not _inside_card(widget):
        return
    widget.configure(style=_card_style(ttk.Style(widget), current or cls))


# ---------------------------------------------------------------- 主题

def apply_theme(root: tk.Tk, name: str | None = None) -> ttk.Style:
    """按 PALETTES[name] 配置 clam 主题。"""
    name = name or theme_name()
    COLORS.clear()
    COLORS.update(PALETTES[name])
    _sync_plot_colors()
    _card_styles.clear()
    c = COLORS

    resolve_font_family(root)
    root.configure(bg=c["bg"])
    root.option_add("*Font", font(11))
    root.option_add("*Toplevel.background", c["bg"])
    for option, value in (("background", c["surface"]), ("foreground", c["ink"]),
                          ("activeBackground", c["accent_soft"]), ("activeForeground", c["ink"]),
                          ("selectColor", c["accent"]), ("relief", "flat")):
        root.option_add(f"*Menu.{option}", value)
    root.option_add("*TCombobox*Listbox.background", c["field"])
    root.option_add("*TCombobox*Listbox.foreground", c["ink"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["select_bg"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["select_fg"])

    style = ttk.Style(root)
    style.theme_use("clam")
    _create_elements(root, style)
    k = _scale(root)

    body = font(11)
    body_bold = font(11, "bold")
    small = font(10)
    heading = font(10, "bold")

    style.configure(
        ".",
        font=body,
        background=c["bg"],
        foreground=c["ink"],
        bordercolor=c["border"],
        darkcolor=c["rule"],
        lightcolor=c["bg"],
        troughcolor=c["tab"],
        focuscolor=c["accent"],
        selectbackground=c["select_bg"],
        selectforeground=c["select_fg"],
        insertcolor=c["ink"],
        arrowcolor=c["dim"],
    )

    style.configure("TFrame", background=c["bg"])
    style.configure("Page.TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["ink"], font=body)
    style.configure("Muted.TLabel", foreground=c["dim"], font=small)
    style.configure("Accent.TLabel", foreground=c["accent"], font=body)
    style.configure("Title.TLabel", foreground=c["ink"], font=body_bold)
    style.configure("Hint.TLabel", foreground=c["dim"], font=font(9))

    # LabelFrame = 卡片，标题放在卡片上方
    style.layout("TLabelframe", [("Card.border", {"sticky": "nsew"})])
    style.configure("TLabelframe", background=c["bg"], labeloutside=True,
                    labelmargins=(2, 0, 0, round(6 * k)), padding=round(12 * k))
    style.configure("TLabelframe.Label", background=c["bg"], foreground=c["ink"], font=body_bold)
    # 卡片里再套的 LabelFrame：细边框分组
    style.element_create("Inner.border", "from", "clam", "border")
    style.configure("Card.TLabelframe", relief="solid", borderwidth=1, bordercolor=c["rule"],
                    lightcolor=c["surface"], darkcolor=c["surface"], labeloutside=True,
                    labelmargins=(0, round(4 * k), 0, round(4 * k)), padding=round(8 * k))
    style.configure("Card.TLabelframe.Label", foreground=c["dim"], font=heading)

    # 按钮
    pad = (round(14 * k), round(4 * k))
    style.layout("TButton", _button_layout("Plain.button"))
    style.configure("TButton", foreground=c["ink"], font=body, padding=pad, anchor="center")
    style.map("TButton", foreground=[("disabled", c["disabled"])])
    style.layout("Accent.TButton", _button_layout("Accent.button"))
    style.configure("Accent.TButton", foreground=c["on_accent"], font=body_bold, padding=pad)
    style.map("Accent.TButton", foreground=[("disabled", c["on_accent"])])
    style.layout("Header.TButton", _button_layout("Header.button"))
    style.configure("Header.TButton", background=c["header_bg"], foreground=c["header_fg"], font=body, padding=pad)
    style.layout("Ghost.TButton", _button_layout("Ghost.button"))
    style.configure("Ghost.TButton", foreground=c["accent"], font=body, padding=pad)

    menubutton = [("Plain.button", {"sticky": "nsew", "children": [
        ("Menubutton.padding", {"sticky": "nsew", "children": [
            ("Menubutton.indicator", {"side": "right", "sticky": ""}),
            ("Menubutton.label", {"side": "left", "sticky": ""}),
        ]}),
    ]})]
    style.layout("TMenubutton", menubutton)
    style.configure("TMenubutton", foreground=c["ink"], padding=pad, arrowcolor=c["dim"])
    style.layout("Header.TMenubutton", [("Header.button",) + menubutton[0][1:]])
    style.configure("Header.TMenubutton", background=c["header_bg"], foreground=c["header_fg"],
                    arrowcolor=c["header_sub"])

    # 输入框
    field_layout = [("Rounded.field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]}),
    ]})]
    style.layout("TEntry", field_layout)
    style.configure("TEntry", foreground=c["ink"], padding=(round(8 * k), round(3 * k)), font=body)
    style.map("TEntry", foreground=[("disabled", c["disabled"])])
    style.configure("Header.TEntry", background=c["header_bg"])
    style.layout("TCombobox", [("Rounded.field", {"sticky": "nsew", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]}),
    ]})])
    style.configure("TCombobox", foreground=c["ink"], padding=(round(8 * k), round(3 * k)),
                    arrowcolor=c["dim"], background=c["field"])
    style.map("TCombobox", fieldbackground=[("readonly", c["control"])],
              selectbackground=[("readonly", c["control"])], selectforeground=[("readonly", c["ink"])])

    # 勾选框、单选框
    for widget, element in (("TCheckbutton", "Rounded.checkindicator"), ("TRadiobutton", "Rounded.radioindicator")):
        style.layout(widget, [(f"{widget[1:]}.padding", {"sticky": "nsew", "children": [
            (element, {"side": "left", "sticky": ""}),
            (f"{widget[1:]}.label", {"side": "left", "sticky": "nsew"}),
        ]})])
        style.configure(widget, background=c["bg"], foreground=c["ink"], font=body, padding=(0, 2))
        style.map(widget, background=[("active", c["bg"])], foreground=[("disabled", c["disabled"])])

    # 页签：主窗口的步骤页不显示页签（由页头的步骤导航切换），结果区用下划线页签
    style.layout("Main.TNotebook.Tab", [])
    style.layout("Main.TNotebook", [("Notebook.client", {"sticky": "nsew"})])
    style.configure("Main.TNotebook", background=c["bg"], borderwidth=0, padding=0,
                    bordercolor=c["bg"], lightcolor=c["bg"], darkcolor=c["bg"])
    style.layout("TNotebook", [("Card.client", {"sticky": "nsew"})])
    style.configure("TNotebook", background=c["bg"], tabmargins=(0, 0, 0, round(6 * k)),
                    padding=round(1 * k))
    style.layout("TNotebook.Tab", [("Underline.tab", {"sticky": "nsew", "children": [
        ("Notebook.padding", {"sticky": "nsew", "children": [("Notebook.label", {"sticky": ""})]}),
    ]})])
    style.configure("TNotebook.Tab", foreground=c["dim"], padding=(round(14 * k), round(7 * k)), font=body)
    style.map("TNotebook.Tab", foreground=[("selected", c["accent"]), ("disabled", c["disabled"])],
              font=[("selected", body_bold)])

    # 表格
    style.configure(
        "Treeview",
        background=c["field"],
        foreground=c["ink"],
        fieldbackground=c["field"],
        bordercolor=c["rule"],
        lightcolor=c["rule"],
        darkcolor=c["rule"],
        rowheight=round(28 * k),
        font=body,
    )
    style.configure(
        "Treeview.Heading",
        background=c["tab"],
        foreground=c["dim"],
        bordercolor=c["rule"],
        lightcolor=c["tab"],
        darkcolor=c["rule"],
        relief="flat",
        font=heading,
        padding=(round(8 * k), round(6 * k)),
    )
    style.map("Treeview", background=[("selected", c["select_bg"])], foreground=[("selected", c["select_fg"])])
    style.map("Treeview.Heading", background=[("active", c["control_pressed"])])

    style.configure(
        "TProgressbar",
        background=c["accent"],
        troughcolor=c["tab"],
        bordercolor=c["rule"],
        lightcolor=c["accent"],
        darkcolor=c["accent"],
        thickness=round(6 * k),
    )

    # 细滚动条：去掉箭头，滑块圆角
    for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
        style.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {"sticky": sticky, "children": [
            (f"Slim.{orient}.thumb", {"expand": "1", "sticky": "nsew"}),
        ]})])
        style.configure(f"{orient}.TScrollbar", troughcolor=c["field"], bordercolor=c["field"],
                        lightcolor=c["field"], darkcolor=c["field"], arrowsize=round(10 * k),
                        width=round(10 * k), relief="flat")

    style.configure("TSeparator", background=c["rule"])
    style.configure("Status.TLabel", background=c["status_bg"], foreground=c["dim"], font=small)

    root.bind_all("<Map>", _on_map, add="+")
    return style
