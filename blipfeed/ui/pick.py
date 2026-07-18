"""MANUAL mode — freeze the frame, click the words you want.

The precision path (CLAUDE.md Decisions #2b). AUTO mode records everything and lets
frequency sort it out; here *you* are the filter, which is what makes a dense frame (a
Douyin homepage) workable and lets you grab one specific term and move on.

**Merge is the point, not a bonus.** The real-frame test found jieba splits OOV slang:
万魂幡 -> 万魂 / 幡. No heuristic can fix that safely (幡 alone is a valid word), so the
human does it: select the adjacent chips, hit Merge, and the fused term is recorded
*and* taught to the segmenter permanently (`segment.add_user_word`), so every future
snapshot keeps it whole.

An ordinary focus-taking window — you asked for this frame, so taking focus is correct
(Decisions #6). Chip positions are estimated by distributing each OCR line's box across
its characters, which is a good approximation for CJK (near-uniform width) and only
needs to be close enough to click.
"""
import sys
import tkinter as tk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from .. import segment  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.pick")

_MAX_W_FRACTION = 0.92          # scale the frame down to fit the screen
_MAX_H_FRACTION = 0.78


class _Chip:
    """One clickable token, positioned over the frozen frame."""

    __slots__ = ("text", "line_index", "order", "x0", "y0", "x1", "y1",
                 "rect", "label", "selected")

    def __init__(self, text, line_index, order, box):
        self.text, self.line_index, self.order = text, line_index, order
        self.x0, self.y0, self.x1, self.y1 = box
        self.rect = self.label = None
        self.selected = False


def _chips_for(lines, scale):
    """Estimate a box per token by splitting each line's box across its characters."""
    chips = []
    for li, line in enumerate(lines):
        tokens = segment.tokenize(line.text)
        if not tokens:
            continue
        x0, y0, x1, y1 = (c * scale for c in line.box)
        total = sum(len(t) for t in tokens) or 1
        cursor = 0
        for order, tok in enumerate(tokens):
            a = x0 + (cursor / total) * (x1 - x0)
            cursor += len(tok)
            b = x0 + (cursor / total) * (x1 - x0)
            if segment.keep(tok):        # punctuation/particles aren't clickable
                chips.append(_Chip(tok, li, order, (a, y0, b, y1)))
    return chips


class _Picker:
    def __init__(self, root, image, lines, scale):
        self.root = root
        self.result = None
        self.lines = lines
        self.chips = _chips_for(lines, scale)

        w = image.width() if image else 800
        h = image.height() if image else 200
        self.canvas = tk.Canvas(root, width=w, height=h, highlightthickness=0,
                                bg="#111")
        self.canvas.pack()
        if image:
            self.canvas.create_image(0, 0, anchor="nw", image=image)
            self.canvas.image = image        # keep a reference or Tk drops it

        for chip in self.chips:
            chip.rect = self.canvas.create_rectangle(
                chip.x0, chip.y0, chip.x1, chip.y1,
                outline="#4da3ff", width=1, fill="#0b2a4a", stipple="gray50")
            chip.label = self.canvas.create_text(
                (chip.x0 + chip.x1) / 2, (chip.y0 + chip.y1) / 2,
                text=chip.text, fill="#eaf2ff", font=("Microsoft YaHei", 11))
            for item in (chip.rect, chip.label):
                self.canvas.tag_bind(item, "<Button-1>",
                                     lambda _e, c=chip: self.toggle(c))

        bar = tk.Frame(root, bg="#1b1b1b")
        bar.pack(fill="x")
        self.status = tk.Label(bar, text=self._status_text(), anchor="w",
                               bg="#1b1b1b", fg="#cfd6e4",
                               font=("Segoe UI", 10), padx=10, pady=8)
        self.status.pack(side="left", fill="x", expand=True)
        self.merge_btn = tk.Button(bar, text="Merge selected", state="disabled",
                                   command=self.merge)
        self.merge_btn.pack(side="left", padx=4, pady=6)
        tk.Button(bar, text="Cancel", command=self.cancel).pack(side="left", padx=4)
        tk.Button(bar, text="Record selected", command=self.confirm,
                  default="active").pack(side="left", padx=(4, 10))

        root.bind("<Escape>", lambda _e: self.cancel())
        root.bind("<Return>", lambda _e: self.confirm())

    # ------------------------------------------------------------- selection
    def selected(self):
        return [c for c in self.chips if c.selected]

    def _status_text(self):
        picked = self.selected()
        if not picked:
            return ("Click the words you want   ·   select adjacent pieces and Merge "
                    "to fix a split term   ·   Enter to record, Esc to cancel")
        return f"{len(picked)} selected: " + "  ".join(c.text for c in picked)

    def toggle(self, chip):
        chip.selected = not chip.selected
        self.canvas.itemconfig(
            chip.rect, fill="#1f6feb" if chip.selected else "#0b2a4a",
            outline="#8ec5ff" if chip.selected else "#4da3ff",
            width=2 if chip.selected else 1)
        self.refresh()

    def refresh(self):
        self.status.config(text=self._status_text())
        self.merge_btn.config(
            state="normal" if self._mergeable() else "disabled")

    def _mergeable(self):
        """Two or more chips that are adjacent, in order, on the same line."""
        picked = self.selected()
        if len(picked) < 2:
            return False
        if len({c.line_index for c in picked}) != 1:
            return False
        orders = sorted(c.order for c in picked)
        return orders == list(range(orders[0], orders[0] + len(orders)))

    # ---------------------------------------------------------------- actions
    def merge(self):
        """Fuse adjacent chips into one term and teach it to the segmenter."""
        picked = sorted(self.selected(), key=lambda c: c.order)
        if not self._mergeable():
            return
        merged = "".join(c.text for c in picked)
        segment.add_user_word(merged)          # sticks for every future snapshot
        keep, drop = picked[0], picked[1:]
        keep.text = merged
        keep.x1 = picked[-1].x1
        self.canvas.coords(keep.rect, keep.x0, keep.y0, keep.x1, keep.y1)
        self.canvas.itemconfig(keep.label, text=merged)
        self.canvas.coords(keep.label, (keep.x0 + keep.x1) / 2,
                           (keep.y0 + keep.y1) / 2)
        for chip in drop:
            self.canvas.delete(chip.rect)
            self.canvas.delete(chip.label)
            self.chips.remove(chip)
        log.info("merged %s", merged)
        self.refresh()

    def confirm(self):
        picked = self.selected()
        if not picked:
            return self.cancel()
        by_line = {}
        for chip in picked:
            by_line.setdefault(chip.line_index, []).append(chip)
        context = " ".join(self.lines[i].text for i in sorted(by_line)
                           if i < len(self.lines))
        self.result = {"tokens": [c.text for c in picked], "context": context}
        self.root.destroy()

    def cancel(self):
        self.result = None
        self.root.destroy()


def _to_photo(img, root):
    """BGR numpy -> Tk image, scaled to fit. Returns (photo, scale)."""
    try:
        from PIL import Image, ImageTk
    except ImportError:
        log.warning("Pillow not available — showing chips without the frame")
        return None, 1.0
    try:
        rgb = img[:, :, ::-1]                       # BGR -> RGB
        pil = Image.fromarray(rgb)
        max_w = int(root.winfo_screenwidth() * _MAX_W_FRACTION)
        max_h = int(root.winfo_screenheight() * _MAX_H_FRACTION)
        scale = min(max_w / pil.width, max_h / pil.height, 1.0)
        if scale < 1.0:
            pil = pil.resize((max(int(pil.width * scale), 1),
                              max(int(pil.height * scale), 1)))
        return ImageTk.PhotoImage(pil), scale
    except Exception:
        log.exception("could not render the captured frame")
        return None, 1.0


def pick_tokens(img, lines, master=None):
    """Show the frozen frame; return {"tokens": [...], "context": str} or None."""
    if not lines:
        log.info("nothing recognized — no chips to pick")
        return None
    owns_root = master is None
    root = tk.Tk() if owns_root else tk.Toplevel(master)
    root.title("Blipfeed — pick words")
    root.configure(bg="#111")

    photo, scale = _to_photo(img, root) if img is not None else (None, 1.0)
    picker = _Picker(root, photo, lines, scale)
    root.attributes("-topmost", True)
    root.focus_force()
    if owns_root:
        root.mainloop()
    else:
        root.grab_set()
        root.wait_window()
    return picker.result
