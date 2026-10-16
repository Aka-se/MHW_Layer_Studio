"""Tree columns can grow beyond the viewport instead of consuming adjacent columns."""
import tkinter.font as tkfont
from tkinter import ttk


def child_group_label(name, parent, siblings):
    """Keep automatic side labels legible even after the parent is renamed."""
    parts = [child.rsplit(" · ", 1) for child in siblings]
    if (len(parts) == 2 and all(len(p) == 2 for p in parts)
            and parts[0][0] == parts[1][0] and {p[1] for p in parts} == {"左侧", "右侧"}):
        return name.rsplit(" · ", 1)[1]
    return name.removeprefix(parent + " · ")


class ResizableTreeview(ttk.Treeview):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._column_drag = None
        self.bind("<ButtonPress-1>", self._resize_press)
        self.bind("<B1-Motion>", self._resize_motion)
        self.bind("<ButtonRelease-1>", self._resize_release)
        self.bind("<Double-1>", self._fit_separator)

    def _resize_press(self, event):
        if self.identify_region(event.x, event.y) == "separator":
            column = self.identify_column(event.x)
            self._column_drag = (column, event.x, int(self.column(column, "width")))
            return "break"

    def _resize_motion(self, event):
        if self._column_drag:
            column, x, width = self._column_drag
            self.column(column, width=max(int(self.column(column, "minwidth")), min(2400, width + event.x - x)), stretch=False)
            return "break"

    def _resize_release(self, event):
        if self._column_drag:
            self._column_drag = None
            return "break"

    def _fit_separator(self, event):
        if self.identify_region(event.x, event.y) == "separator":
            self.fit_column(self.identify_column(event.x))
            return "break"

    def fit_column(self, column):
        font = tkfont.nametofont("TkDefaultFont")
        width = font.measure(self.heading(column, "text")) + 24
        def visit(parent="", depth=0):
            nonlocal width
            for item in self.get_children(parent):
                value = self.item(item, "text") if column == "#0" else self.set(item, column)
                extra = 28 + depth * 20 if column == "#0" else 20
                width = max(width, font.measure(str(value)) + extra)
                visit(item, depth + 1)
        visit()
        self.column(column, width=max(int(self.column(column, "minwidth")), min(1600, width)), stretch=False)

    def fit_columns(self):
        for column in ("#0", *self.cget("columns")):
            self.fit_column(column)
