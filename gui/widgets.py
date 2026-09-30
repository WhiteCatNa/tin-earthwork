"""
通用界面部件。
"""
import tkinter as tk
from tkinter import ttk

from gui.theme import COLORS


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
