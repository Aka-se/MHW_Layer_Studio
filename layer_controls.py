"""Native button borders with two independently styled text lines."""
import os
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk
from PIL import Image, ImageDraw, ImageFont, ImageTk


class LayerButton(ttk.Button):
    def __init__(self, parent, command):
        super().__init__(parent, command=command, style="Layer.TButton", compound="none")
        self._text, self._disabled, self._pressed = "", False, False
        self._photo = None
        base = tkfont.nametofont("TkDefaultFont")
        size = base.cget("size")
        pixels = abs(size) if size < 0 else round(self.winfo_fpixels(f"{size}p"))
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        self._main_font = ImageFont.truetype(str(font_path), max(12, pixels))
        self._hint_font = ImageFont.truetype(str(font_path), max(11, pixels - 2))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        for key in ("<space>", "<Return>"):
            self.bind(key, lambda _e: self._keyboard())

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        changed = False
        if "text" in kwargs:
            value = kwargs["text"]
            changed = value != self._text
            self._text = value
        if "state" in kwargs:
            disabled = kwargs["state"] == "disabled"
            changed = changed or disabled != self._disabled
            self._disabled = disabled
        if changed:
            self._render_text()
            kwargs["image"] = self._photo
        return super().configure(cnf, **kwargs)

    config = configure

    def _render_text(self):
        title, _, hint = self._text.partition("\n")
        fonts = (self._main_font, self._hint_font)
        texts = (title, hint)
        widths = [int(font.getlength(text)) for font, text in zip(fonts, texts)]
        heights = [sum(font.getmetrics()) for font in fonts]
        width, height = max(1, max(widths) + 2), sum(heights) + 2
        image = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(image)
        y = 0
        for i, (text, font) in enumerate(zip(texts, fonts)):
            color = ("#606060" if i == 0 else "#737373") if self._disabled else "#101010" if i == 0 else "#6c6c6c"
            draw.text((width / 2, y), text, font=font, fill=color, anchor="mt")
            y += heights[i] + 2
        self._photo = ImageTk.PhotoImage(image, master=self)

    def _press(self, _event):
        if not self.instate(["disabled"]):
            self._pressed = True
            self.focus_set()
            self.state(["pressed"])
        return "break"

    def _release(self, event):
        self.state(["!pressed"])
        pressed, self._pressed = self._pressed, False
        if (pressed and 0 <= event.x_root - self.winfo_rootx() < self.winfo_width()
                and 0 <= event.y_root - self.winfo_rooty() < self.winfo_height()):
            self.invoke()
        return "break"

    def _keyboard(self):
        self.invoke()
        return "break"
