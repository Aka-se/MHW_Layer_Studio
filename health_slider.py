"""An integer health track with aligned reference ticks and absolute pointer input."""
import tkinter as tk
from tkinter import ttk


class HealthSlider(tk.Canvas):
    def __init__(self, parent, variable, command):
        background = ttk.Style(parent).lookup("TFrame", "background") or "#f0f0f0"
        super().__init__(parent, height=48, width=1, background=background,
                         highlightthickness=0, takefocus=True)
        self.variable, self.command = variable, command
        self.ticks = (0, 25, 50, 75, 100)
        self._trace = variable.trace_add("write", self._draw)
        self.bind("<Configure>", self._draw)
        self.bind("<ButtonPress-1>", self._pointer)
        self.bind("<B1-Motion>", self._pointer)
        self.bind("<ButtonRelease-1>", self._pointer)
        for key, delta in (("Left", -1), ("Down", -1), ("Right", 1), ("Up", 1)):
            self.bind(f"<KeyPress-{key}>", lambda _e, d=delta: self._key(self.get() + d))
        self.bind("<Home>", lambda _e: self._key(0))
        self.bind("<End>", lambda _e: self._key(100))
        self.bind("<Destroy>", self._cleanup)

    def coordinates(self, value):
        return 20 + (max(41, self.winfo_width()) - 40) * value / 100

    def get(self):
        return int(float(self.variable.get()))

    def set_ticks(self, values):
        ticks = tuple(sorted({0, 100, *values}))
        if ticks != self.ticks:
            self.ticks = ticks
            self._draw()

    def set(self, value):
        value = max(0, min(100, int(round(float(value)))))
        self.variable.set(value)
        self.command(str(value))

    def _key(self, value):
        self.set(value)
        return "break"

    def _pointer(self, event):
        self.focus_set()
        span = self.coordinates(100) - self.coordinates(0)
        self.set((event.x - self.coordinates(0)) * 100 / span)
        return "break"

    def _draw(self, *_args):
        if self.winfo_width() < 41:
            return
        self.delete("all")
        start, end, y = self.coordinates(0), self.coordinates(100), 14
        self.create_line(start, y - 1, end, y - 1, width=3, fill="#8b8b8b")
        self.create_line(start, y + 2, end, y + 2, fill="white")
        self.create_line(start, y, self.coordinates(self.get()), y, width=2, fill="#737373")
        last_label = -100
        for value in self.ticks:
            x = self.coordinates(value)
            self.create_line(x, y - 6, x, y + 6, fill="#576675", width=1)
            if value in (0, 100) or (x - last_label >= 42 and end - x >= 42):
                self.create_text(x, 37, text=str(value), fill="#576675", font="TkDefaultFont", tags=f"tick-{value}")
                last_label = x
        x = self.coordinates(self.get())
        self.create_rectangle(x - 6, y - 10, x + 6, y + 10, fill="#d9d9d9", outline="#5e5e5e")
        self.create_line(x - 5, y + 9, x - 5, y - 9, x + 5, y - 9, fill="white")
        self.create_line(x + 5, y - 8, x + 5, y + 9, x - 4, y + 9, fill="#8a8a8a")

    def _cleanup(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self._trace)
