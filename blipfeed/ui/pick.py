"""MANUAL mode — freeze the frame, click the words you want.

The precision path (CLAUDE.md Decisions #2b). AUTO mode records everything and lets
frequency sort it out; here *you* are the filter, which makes a dense frame (a Douyin
homepage) workable and lets you grab one specific term and move on.

**Merge is the point, not a bonus.** The real-frame test found jieba splits OOV slang:
万魂幡 -> 万魂 / 幡. No heuristic can fix that safely (幡 alone is a valid word), so the
human does it: select the adjacent chips, hit Merge, and the fused term is recorded
*and* taught to the segmenter permanently (`segment.add_user_word`).

## Two ways to pick, one selection

- **On the screen** (top): the frozen frame, dimmed, with the words drawn as bold pills
  *at the position they appeared*. This keeps spatial context — is 游戏 the nav bar or
  the subtitle? The old version drew tiny faint text at the source scale; these are
  fixed-size, bold, high-contrast pills, so they're actually readable.
- **In the word bank** (below): the same words as a flowing, wrapping list — easier
  for dense frames where pills would overlap.

A word is one `_Chip` with two representations; clicking either toggles the same
selection, and `_paint` keeps both in sync. Ordinary focus-taking window (Decisions #6).
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

# --- palette: a purpose-built dark capture tool -------------------------------
_BG = "#14171c"
_PANEL = "#1b2027"
_INK = "#e8eef6"
_DIM = "#8b95a3"
_CHIP_BG = "#2a313b"
_CHIP_FG = "#e8eef6"
_CHIP_BORDER = "#3b4552"
_CHIP_HOVER = "#59657a"
_SEL_BG = "#2f6fed"
_SEL_FG = "#ffffff"
_SEL_BORDER = "#6b9bff"
_ACCENT = "#2f6fed"

_CHIP_FONT = ("Microsoft YaHei", 15, "bold")     # word bank
_CANVAS_FONT = ("Microsoft YaHei", 13, "bold")   # on-image pills
_UI_FONT = ("Segoe UI", 10)
_HEAD_FONT = ("Segoe UI Semibold", 13)

_WIN_W_FRAC, _WIN_H_FRAC = 0.80, 0.90
_IMAGE_H_FRAC = 0.52             # share of the window given to the screen view
_DIM_BRIGHTNESS = 0.5            # how far the screenshot recedes behind the pills


class _Chip:
    __slots__ = ("text", "line_index", "order", "box", "selected",
                 "bank_widget", "tag")

    def __init__(self, text, line_index, order, box):
        self.text = text
        self.line_index = line_index
        self.order = order
        self.box = box               # (x0,y0,x1,y1) in displayed-image px, or None
        self.selected = False
        self.bank_widget = None
        self.tag = None              # canvas tag for this chip's items


def _chips_for(lines, scale):
    """Clickable tokens in reading order, each with a displayed-image box (for the
    overlay) plus line + order (for merge adjacency)."""
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
            if segment.keep(tok):
                chips.append(_Chip(tok, li, order, (a, y0, b, y1)))
    return chips


class _Picker:
    def __init__(self, root, photo, lines, scale):
        self.root = root
        self.result = None
        self.lines = lines
        self.chips = _chips_for(lines, scale)
        root.configure(bg=_BG)

        # --- header ----------------------------------------------------------
        top = tk.Frame(root, bg=_BG)
        top.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(top, text="Pick the words you want", font=_HEAD_FONT, bg=_BG,
                 fg=_INK).pack(side="left")
        tk.Label(top, text=f"{len(self.chips)} detected", font=_UI_FONT, bg=_BG,
                 fg=_DIM).pack(side="right")
        tk.Label(root, text="Click a word on the screen above or in the list below — "
                            "either works. Select two adjacent pieces of a split word "
                            "(万魂 + 幡) and Merge to fix it.",
                 font=_UI_FONT, bg=_BG, fg=_DIM, anchor="w", justify="left").pack(
            fill="x", padx=18, pady=(0, 6))

        # --- action bar (reserved before the expanding bank, Blip gotcha #16) --
        bar = tk.Frame(root, bg=_PANEL)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="", anchor="w", bg=_PANEL, fg=_INK,
                               font=("Segoe UI", 11), padx=16, pady=12)
        self.status.pack(side="left", fill="x", expand=True)
        self._button(bar, "Record  ✓", self.confirm, kind="accent").pack(
            side="right", padx=(4, 14), pady=8)
        self._button(bar, "Cancel", self.cancel, kind="ghost").pack(
            side="right", padx=4, pady=8)
        self.merge_btn = self._button(bar, "Merge", self.merge, kind="ghost")
        self.merge_btn.pack(side="right", padx=4, pady=8)

        # --- the screen view (overlay pills) ---------------------------------
        self.canvas = None
        if photo is not None:
            frame = tk.Frame(root, bg=_BG)
            frame.pack(fill="x", padx=12, pady=(0, 6))
            self.canvas = tk.Canvas(frame, width=photo.width(),
                                    height=photo.height(), bg=_BG,
                                    highlightthickness=1,
                                    highlightbackground="#2b323c")
            self.canvas.pack()
            self.canvas.create_image(0, 0, anchor="nw", image=photo, tags="bg")
            self.canvas.image = photo
            self._fill_canvas()

        # --- the word bank ---------------------------------------------------
        wrap = tk.Frame(root, bg=_BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        tk.Label(wrap, text="ALL WORDS", font=("Segoe UI", 8, "bold"), bg=_BG,
                 fg=_DIM, anchor="w").pack(fill="x", padx=6)
        inner = tk.Frame(wrap, bg=_BG)
        inner.pack(fill="both", expand=True)
        self.bank = tk.Text(inner, wrap="word", bg=_BG, relief="flat", bd=0,
                            padx=8, pady=6, spacing3=10, cursor="arrow",
                            highlightthickness=0)
        vsb = tk.Scrollbar(inner, command=self.bank.yview)
        self.bank.configure(yscrollcommand=vsb.set)
        self.bank.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._fill_bank()

        root.bind("<Escape>", lambda _e: self.cancel())
        root.bind("<Return>", lambda _e: self.confirm())
        self._refresh()

    # --------------------------------------------------------------- widgets
    def _button(self, master, text, command, kind="ghost"):
        bg, fg, active = ((_ACCENT, "#ffffff", "#4880f5") if kind == "accent"
                          else ("#2b323c", _INK, "#39424e"))
        return tk.Button(master, text=text, command=command, font=("Segoe UI", 10),
                         bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                         relief="flat", bd=0, padx=16, pady=7, cursor="hand2")

    # ---- screen view ----
    def _fill_canvas(self):
        self.canvas.delete("chip")
        for chip in self.chips:
            if chip.box is None:
                continue
            x0, y0, x1, y1 = chip.box
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            tag = f"c{id(chip)}"
            chip.tag = tag
            t = self.canvas.create_text(cx, cy, text=chip.text, font=_CANVAS_FONT,
                                        fill=_CHIP_FG, tags=("chip", tag))
            bb = self.canvas.bbox(t)
            pad = 5
            r = self.canvas.create_rectangle(bb[0] - pad, bb[1] - pad, bb[2] + pad,
                                             bb[3] + pad, fill=_CHIP_BG,
                                             outline=_CHIP_BORDER, width=1,
                                             tags=("chip", tag))
            self.canvas.tag_raise(t, r)
            self.canvas.tag_bind(tag, "<Button-1>",
                                 lambda _e, c=chip: self.toggle(c))
            self.canvas.tag_bind(tag, "<Enter>",
                                 lambda _e, c=chip: self._hover(c, True))
            self.canvas.tag_bind(tag, "<Leave>",
                                 lambda _e, c=chip: self._hover(c, False))
            self._paint_canvas(chip)

    def _paint_canvas(self, chip):
        if chip.tag is None:
            return
        rects = [i for i in self.canvas.find_withtag(chip.tag)
                 if self.canvas.type(i) == "rectangle"]
        texts = [i for i in self.canvas.find_withtag(chip.tag)
                 if self.canvas.type(i) == "text"]
        if chip.selected:
            for r in rects:
                self.canvas.itemconfig(r, fill=_SEL_BG, outline=_SEL_BORDER, width=2)
            for t in texts:
                self.canvas.itemconfig(t, fill=_SEL_FG)
            self.canvas.tag_raise(chip.tag)          # selected pills on top
        else:
            for r in rects:
                self.canvas.itemconfig(r, fill=_CHIP_BG, outline=_CHIP_BORDER, width=1)
            for t in texts:
                self.canvas.itemconfig(t, fill=_CHIP_FG)

    # ---- word bank ----
    def _make_bank_chip(self, chip):
        lbl = tk.Label(self.bank, text=chip.text, font=_CHIP_FONT, padx=12, pady=5,
                       bg=_CHIP_BG, fg=_CHIP_FG, cursor="hand2", highlightthickness=1,
                       highlightbackground=_CHIP_BORDER, highlightcolor=_CHIP_BORDER)
        lbl.bind("<Button-1>", lambda _e, c=chip: self.toggle(c))
        lbl.bind("<Enter>", lambda _e, c=chip: self._hover(c, True))
        lbl.bind("<Leave>", lambda _e, c=chip: self._hover(c, False))
        chip.bank_widget = lbl
        self._paint_bank(chip)
        return lbl

    def _fill_bank(self):
        self.bank.config(state="normal")
        self.bank.delete("1.0", "end")
        last_line = None
        for chip in self.chips:
            if last_line is not None and chip.line_index != last_line:
                self.bank.insert("end", "\n")
            self.bank.window_create("end", window=self._make_bank_chip(chip),
                                    padx=4, pady=4)
            last_line = chip.line_index
        self.bank.config(state="disabled")

    def _paint_bank(self, chip):
        if chip.bank_widget is None:
            return
        if chip.selected:
            chip.bank_widget.config(bg=_SEL_BG, fg=_SEL_FG,
                                    highlightbackground=_SEL_BORDER,
                                    highlightcolor=_SEL_BORDER)
        else:
            chip.bank_widget.config(bg=_CHIP_BG, fg=_CHIP_FG,
                                    highlightbackground=_CHIP_BORDER,
                                    highlightcolor=_CHIP_BORDER)

    # ---- both ----
    def _paint(self, chip):
        self._paint_canvas(chip)
        self._paint_bank(chip)

    def _hover(self, chip, entering):
        if chip.selected:
            return
        colour = _CHIP_HOVER if entering else _CHIP_BORDER
        if chip.bank_widget is not None:
            chip.bank_widget.config(highlightbackground=colour, highlightcolor=colour)
        if chip.tag is not None:
            for r in self.canvas.find_withtag(chip.tag):
                if self.canvas.type(r) == "rectangle":
                    self.canvas.itemconfig(r, outline=colour)

    # -------------------------------------------------------------- selection
    def selected(self):
        return [c for c in self.chips if c.selected]

    def toggle(self, chip):
        chip.selected = not chip.selected
        self._paint(chip)
        self._refresh()

    def _refresh(self):
        picked = self.selected()
        if not picked:
            self.status.config(text="Click the words you want   ·   Enter to record"
                                    "   ·   Esc to cancel")
        else:
            self.status.config(text=f"{len(picked)} selected:   "
                                    + "  ".join(c.text for c in picked))
        self.merge_btn.config(state="normal" if self._mergeable() else "disabled")

    def _mergeable(self):
        picked = self.selected()
        if len(picked) < 2 or len({c.line_index for c in picked}) != 1:
            return False
        orders = sorted(c.order for c in picked)
        return orders == list(range(orders[0], orders[0] + len(orders)))

    # ---------------------------------------------------------------- actions
    def merge(self):
        picked = sorted(self.selected(), key=lambda c: c.order)
        if not self._mergeable():
            return
        merged = "".join(c.text for c in picked)
        segment.add_user_word(merged)          # sticks for every future snapshot
        keep, drop = picked[0], picked[1:]
        keep.text = merged
        if keep.box and all(c.box for c in picked):
            xs = [c.box[0] for c in picked] + [c.box[2] for c in picked]
            ys = [c.box[1] for c in picked] + [c.box[3] for c in picked]
            keep.box = (min(xs), min(ys), max(xs), max(ys))
        for chip in drop:
            self.chips.remove(chip)
        keep.selected = True
        if self.canvas is not None:
            self._fill_canvas()
        self._fill_bank()
        self._refresh()
        log.info("merged %s", merged)

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


def _to_photo(img, root, area_w, area_h):
    """BGR numpy -> (dimmed PhotoImage fitted to area, scale). (None, 1.0) if no PIL."""
    try:
        from PIL import Image, ImageEnhance, ImageTk
    except ImportError:
        log.warning("Pillow not available — showing the word bank without the frame")
        return None, 1.0
    try:
        pil = Image.fromarray(img[:, :, ::-1])          # BGR -> RGB
        scale = min(area_w / pil.width, area_h / pil.height, 1.0)
        disp = pil.resize((max(int(pil.width * scale), 1),
                           max(int(pil.height * scale), 1)))
        disp = ImageEnhance.Brightness(disp).enhance(_DIM_BRIGHTNESS)
        return ImageTk.PhotoImage(disp), scale
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
    root.configure(bg=_BG)

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h = int(sw * _WIN_W_FRAC), int(sh * _WIN_H_FRAC)
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

    photo, scale = ((None, 1.0) if img is None
                    else _to_photo(img, root, w - 48, int(h * _IMAGE_H_FRAC)))
    picker = _Picker(root, photo, lines, scale)
    root.attributes("-topmost", True)
    root.focus_force()
    if owns_root:
        root.mainloop()
    else:
        root.grab_set()
        root.wait_window()
    return picker.result
