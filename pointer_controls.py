"""Tk modifier meanings differ between Windows and X11."""
import sys


def alt_pressed(state):
    # Windows Tk: Mod1 (0x8) is Num Lock; real Alt is ALT_MASK (0x20000).
    # X11 uses Mod1 for Alt. Never combine the two on Windows.
    return bool(state & (0x20000 if sys.platform == "win32" else 0x8))
