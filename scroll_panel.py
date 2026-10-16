"""Keep expanded tool sections reachable in small editor windows."""
import tkinter as tk
from tkinter import ttk


class ScrollPanel(ttk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        background = ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0"
        self.canvas = tk.Canvas(self, width=1, height=1, background=background, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", self._resize)
        self.content.bind("<Configure>", self._layout)

    def _resize(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        self._layout()

    def _layout(self, _event=None):
        bounds = self.canvas.bbox(self._window)
        if not bounds:
            return
        self.canvas.configure(scrollregion=bounds)
        if bounds[3] > self.canvas.winfo_height():
            if not self.scroll.winfo_ismapped():
                self.scroll.pack(side="right", fill="y")
        else:
            self.scroll.pack_forget()
            self.canvas.yview_moveto(0)

    def bind_wheel(self):
        def bind(widget):
            if not isinstance(widget, (ttk.Entry, ttk.Combobox, tk.Text, ttk.Scrollbar)) and not getattr(widget, "_panel_wheel_bound", False):
                widget.bind("<MouseWheel>", self._wheel, add="+")
                widget._panel_wheel_bound = True
            for child in widget.winfo_children():
                bind(child)
        bind(self.content)
        self.canvas.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event):
        bounds = self.canvas.bbox(self._window)
        if bounds and bounds[3] > self.canvas.winfo_height() and event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
