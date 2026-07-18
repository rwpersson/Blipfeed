"""Visual drag-select region picker.

An ordinary focus-taking fullscreen window: picking a region is a deliberate setup
step, so focus is fine and no Win32 gymnastics are needed (CLAUDE.md Decisions #6).
Beats typing coordinates by a mile.

    python -m blipfeed.ui.region        # standalone: pick, print the region

Spans the **virtual desktop** (the union of all monitors), so a region can be picked
on any screen. `set_dpi_aware()` runs before the window exists, or coordinates come
back scaled and the capture lands in the wrong place (Blip's documented trap).
"""
import sys
import tkinter as tk

from .. import capture
from ..logging_setup import get

log = get("ui.region")

_MIN_SIZE = 8          # ignore an accidental click (a 2px "drag" isn't a region)


def _virtual_desktop():
    """(left, top, width, height) covering every monitor."""
    cap = capture.Capturer()
    try:
        m = cap.monitors()[0]          # mss index 0 is the all-screens union
        return m["left"], m["top"], m["width"], m["height"]
    finally:
        cap.close()


class _Picker:
    def __init__(self, win, origin):
        self.win = win
        self.ox, self.oy = origin          # virtual-desktop origin, for screen coords
        self.start = None
        self.result = None
        self.rect = None
        self.canvas = tk.Canvas(win, cursor="cross", bg="black",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.hint = self.canvas.create_text(
            win.winfo_screenwidth() // 2, 40,
            text="Drag to select the capture region   ·   Esc to cancel",
            fill="white", font=("Segoe UI", 16))
        self.size_label = None
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        win.bind("<Escape>", self.on_cancel)

    def on_press(self, e):
        self.start = (e.x, e.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                 outline="#4da3ff", width=2)

    def on_drag(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        self.canvas.coords(self.rect, x0, y0, e.x, e.y)
        w, h = abs(e.x - x0), abs(e.y - y0)
        if self.size_label:
            self.canvas.delete(self.size_label)
        self.size_label = self.canvas.create_text(
            min(x0, e.x) + w // 2, max(min(y0, e.y) - 14, 10),
            text=f"{w} × {h}", fill="#4da3ff", font=("Segoe UI", 12, "bold"))

    def on_release(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        left, top = min(x0, e.x), min(y0, e.y)
        w, h = abs(e.x - x0), abs(e.y - y0)
        if w < _MIN_SIZE or h < _MIN_SIZE:
            log.info("region too small (%dx%d) — ignoring", w, h)
            self.start = None
            return
        # canvas coords -> real screen coords
        self.result = capture.Region(self.ox + left, self.oy + top, w, h)
        self.win.destroy()

    def on_cancel(self, _e=None):
        self.result = None
        self.win.destroy()


def pick_region(master=None):
    """Show the picker; return a `capture.Region`, or None if cancelled.

    Pass `master` when the caller already owns a Tk root (one root per process).
    """
    capture.set_dpi_aware()
    left, top, width, height = _virtual_desktop()

    owns_root = master is None
    win = tk.Tk() if owns_root else tk.Toplevel(master)
    win.title("Blipfeed — pick a capture region")
    win.overrideredirect(True)
    win.geometry(f"{width}x{height}+{left}+{top}")
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.30)

    picker = _Picker(win, (left, top))
    win.focus_force()                    # deliberate window: taking focus is correct
    if owns_root:
        win.mainloop()
    else:
        win.grab_set()
        win.wait_window()
    return picker.result


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    region = pick_region()
    if region is None:
        print("cancelled")
        return 1
    print(f"region: {region.label()}")
    print(f"  left={region.left} top={region.top} "
          f"width={region.width} height={region.height}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
