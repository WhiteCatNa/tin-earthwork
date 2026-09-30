"""
通用界面部件。
"""
import tkinter as tk
from tkinter import ttk

from gui.theme import COLORS


def fixed_request_box(parent, width: int, height: int) -> ttk.Frame:
    """放表格的容器：请求尺寸固定为 width×height，内部表格照样随可用空间伸缩。

    Treeview 的列被拉伸铺满后，拉伸后的列宽会成为它新的请求宽度，外层面板据此变宽、
    越撑越宽，最终把并排的另一个面板挤没。容器不向外传递表格的尺寸，就不会出现这种情况。
    """
    box = ttk.Frame(parent, width=width, height=height)
    box.grid_propagate(False)
    box.pack_propagate(False)
    return box


class WrappingButtonRow(ttk.Frame):
    """一排按钮：放得下时排成一行（左对齐或右对齐），宽度不够时自动折成多行等宽排列。

    用 pack 排一行按钮时，窗口一窄最后几个按钮会被挤成 0 宽、直接消失。
    """

    def __init__(self, parent, align: str = "left", gap: int = 6, **kwargs):
        super().__init__(parent, **kwargs)
        self.align = align
        self.gap = gap
        self.buttons = []
        self._layout = None
        self.bind("<Configure>", self._relayout)

    def add(self, text: str, command, **kwargs) -> ttk.Button:
        button = ttk.Button(self, text=text, command=command, **kwargs)
        self.buttons.append(button)
        self._layout = None
        self._relayout()
        return button

    def _natural_width(self) -> int:
        return sum(b.winfo_reqwidth() for b in self.buttons) + self.gap * max(len(self.buttons) - 1, 0)

    def _relayout(self, _event=None):
        if not self.buttons:
            return
        width = self.winfo_width()
        if width <= 1 or width >= self._natural_width():
            layout = ("row",)
        else:
            cell = max(b.winfo_reqwidth() for b in self.buttons) + self.gap
            layout = ("grid", max(1, min(len(self.buttons), (width + self.gap) // cell)))
        if layout == self._layout:
            return
        self._layout = layout
        for column in range(len(self.buttons) + 1):
            self.grid_columnconfigure(column, weight=0, uniform="")
        if layout[0] == "row":
            # 空白列放在对齐方向的另一侧
            offset = 1 if self.align == "right" else 0
            self.grid_columnconfigure(0 if self.align == "right" else len(self.buttons), weight=1)
            for index, button in enumerate(self.buttons):
                button.grid(row=0, column=index + offset, sticky="ew",
                            padx=(0 if index == 0 else self.gap, 0), pady=0)
            return
        columns = layout[1]
        for column in range(columns):
            self.grid_columnconfigure(column, weight=1, uniform="buttons")
        order = self.buttons[::-1] if self.align == "right" else self.buttons
        for index, button in enumerate(order):
            row, column = divmod(index, columns)
            if self.align == "right":
                column = columns - 1 - column
            button.grid(row=row, column=column, sticky="ew",
                        padx=(0 if column == 0 else self.gap, 0), pady=(0 if row == 0 else self.gap, 0))


class ScrollableFrame(ttk.Frame):
    """竖向可滚动的容器，内容放在 .body 里。

    空间足够时与普通 Frame 一样、不显示滚动条；窗口偏矮时出现滚动条，
    鼠标在区域内时滚轮可滚动。宽度始终跟随内容，避免内容被横向截断。
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=COLORS["bg"])
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(sequence, self._on_wheel, add="+")

    @property
    def scrollable(self) -> bool:
        return self.body.winfo_reqheight() > self.canvas.winfo_height() + 1

    def _on_body_configure(self, _event=None):
        width, height = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, width, height), width=width, height=height)
        self._update_scrollbar()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=max(event.width, self.body.winfo_reqwidth()))
        self._update_scrollbar()

    def _update_scrollbar(self):
        if self.scrollable:
            if not self.scrollbar.winfo_ismapped():
                self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y, before=self.canvas)
        elif self.scrollbar.winfo_ismapped():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def _contains_pointer(self, event) -> bool:
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return False
        while widget is not None:
            if widget is self:
                return True
            widget = widget.master
        return False

    def _on_wheel(self, event):
        if not self.winfo_exists() or not self.scrollable or not self._contains_pointer(event):
            return
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")
