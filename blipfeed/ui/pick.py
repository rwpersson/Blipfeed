"""MANUAL mode — freeze the frame, click the words you want.

The precision path (CLAUDE.md Decisions #2b). AUTO mode records everything and lets
frequency sort it out; here *you* are the filter, which makes a dense frame workable
and lets you grab one specific term and move on.

**Merge** fuses adjacent split pieces (万魂 + 幡 -> 万魂幡) and teaches the segmenter
(`segment.add_user_word`). **Type a word** is the escape hatch for anything OCR can't
read — small text, or a stylized font the recognition model misses; you can see it on
the frozen frame, so you type it and it's recorded like any other.

## Two ways to pick, one selection, and zoom

- **On the screen** (top): the frozen frame with a **border around each recognized
  word at the line's true angle** (the OCR quad, not an axis-aligned box), so the real
  text stays visible. Hover brightens a border; click selects. **Zoom** (Ctrl+wheel,
  the +/- buttons) + pan (scroll, middle-drag) so small words on a full-screen grab
  are legible.
- **In the word bank** (below): the same words as a flowing list of pills.

A word is one `_Chip`; clicking either representation toggles the same selection and
`_paint` keeps both in sync. Chip quads are in ORIGINAL capture pixels and multiplied
by the current display scale when drawn, so zoom is just a redraw. Ordinary
focus-taking window (Decisions #6).
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

# --- palette (from the shared theme) ------------------------------------------
from . import theme as _t  # noqa: E402

_BG = _t.BG
_PANEL = _t.SURFACE
_INK = _t.INK
_DIM = _t.MUTED
_CHIP_BG = _t.SURFACE_2
_CHIP_FG = _t.INK
_CHIP_BORDER = _t.BORDER
_CHIP_HOVER = _t.MUTED
_SEL_BG = _t.ACCENT
_SEL_FG = _t.ACCENT_INK
_SEL_BORDER = _t.ACCENT_HOVER
_ACCENT = _t.ACCENT
_EDGE = "#c7a86a"                # a word's border, at rest (warm, reads on any frame)
_EDGE_FILL = "#2a2213"           # faint stipple tint -> the whole word is clickable

_CHIP_FONT = ("Microsoft YaHei", 15, "bold")
_UI_FONT = ("Segoe UI", 10)
_HEAD_FONT = ("Segoe UI Semibold", 13)

_WIN_W_FRAC, _WIN_H_FRAC = 0.80, 0.90
_IMAGE_H_FRAC = 0.52
_DIM_BRIGHTNESS = 0.82
_ZOOM_STEP = 1.25
_ZOOM_MAX_DISPLAY = 2.5          # cap display scale at 2.5x native (readable, not huge)
_TYPED_LINE = 10 ** 9            # bank line-index bucket for hand-typed words


def _lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


class _Chip:
    __slots__ = ("text", "line_index", "order", "quad", "selected",
                 "bank_widget", "tag")

    def __init__(self, text, line_index, order, quad):
        self.text = text
        self.line_index = line_index         # None for hand-typed words
        self.order = order
        self.quad = quad                     # 4 (x,y) in ORIGINAL capture px, or None
        self.selected = False
        self.bank_widget = None
        self.tag = None


def _line_corners(line):
    """(TL, TR, BR, BL) in original capture px — the OCR quad if present (angle kept),
    else the axis-aligned box."""
    if line.quad is not None:
        p = [(float(x), float(y)) for x, y in line.quad]
        return p[0], p[1], p[2], p[3]
    x0, y0, x1, y1 = line.box
    return (x0, y0), (x1, y0), (x1, y1), (x0, y1)


def _chips_for(lines):
    chips = []
    for li, line in enumerate(lines):
        tokens = segment.tokenize(line.text)
        if not tokens:
            continue
        tl, tr, br, bl = _line_corners(line)
        total = sum(len(t) for t in tokens) or 1
        cursor = 0
        for order, tok in enumerate(tokens):
            f0 = cursor / total
            cursor += len(tok)
            f1 = cursor / total
            if segment.keep(tok):
                quad = (_lerp(tl, tr, f0), _lerp(tl, tr, f1),
                        _lerp(bl, br, f1), _lerp(bl, br, f0))
                chips.append(_Chip(tok, li, order, quad))
    return chips


class _Picker:
    def __init__(self, root, pil_image, fit_scale, lines):
        self.root = root
        self.result = None
        self.lines = lines
        self.chips = _chips_for(lines)
        self.pil = pil_image                 # original RGB PIL, or None
        self.fit_scale = fit_scale
        self.display_scale = fit_scale
        self._photo = None
        self._typed = 0
        root.configure(bg=_BG)

        # --- header ----------------------------------------------------------
        top = tk.Frame(root, bg=_BG)
        top.pack(fill="x", padx=18, pady=(12, 2))
        tk.Label(top, text="Pick the words you want", font=_HEAD_FONT, bg=_BG,
                 fg=_INK).pack(side="left")
        self.count_lbl = tk.Label(top, text="", font=_UI_FONT, bg=_BG, fg=_DIM)
        self.count_lbl.pack(side="right")

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

        # --- screen view + zoom toolbar --------------------------------------
        self.canvas = None
        if self.pil is not None:
            tools = tk.Frame(root, bg=_BG)
            tools.pack(fill="x", padx=18, pady=(4, 2))
            tk.Label(tools, text="Screen — click a word’s border", font=_UI_FONT,
                     bg=_BG, fg=_DIM).pack(side="left")
            self._button(tools, "＋ Type a word", self._add_typed_dialog,
                         kind="ghost").pack(side="right", padx=(8, 0), pady=2)
            self.zoom_lbl = tk.Label(tools, text="", font=_UI_FONT, bg=_BG, fg=_DIM,
                                     width=6)
            self._button(tools, "＋", lambda: self._zoom(_ZOOM_STEP),
                         kind="ghost").pack(side="right", padx=2, pady=2)
            self.zoom_lbl.pack(side="right")
            self._button(tools, "－", lambda: self._zoom(1 / _ZOOM_STEP),
                         kind="ghost").pack(side="right", padx=2, pady=2)
            self._button(tools, "Fit", self._zoom_fit, kind="ghost").pack(
                side="right", padx=(2, 8), pady=2)

            frame = tk.Frame(root, bg=_BG)
            frame.pack(fill="x", padx=12)
            vw = int(self.pil.width * fit_scale)
            vh = int(self.pil.height * fit_scale)
            self.canvas = tk.Canvas(frame, width=vw, height=vh, bg="#0c0e12",
                                    highlightthickness=1,
                                    highlightbackground=_t.BORDER)
            hsb = tk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
            vsb = tk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
            self.canvas.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            frame.columnconfigure(0, weight=1)
            self.canvas.bind("<MouseWheel>", self._on_wheel)
            self.canvas.bind("<ButtonPress-2>",
                             lambda e: self.canvas.scan_mark(e.x, e.y))
            self.canvas.bind("<B2-Motion>",
                             lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
            self._render_image()

        # --- word bank -------------------------------------------------------
        wrap = tk.Frame(root, bg=_BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=(6, 4))
        tk.Label(wrap, text="ALL WORDS", font=("Segoe UI", 8, "bold"), bg=_BG,
                 fg=_DIM, anchor="w").pack(fill="x", padx=6)
        inner = tk.Frame(wrap, bg=_BG)
        inner.pack(fill="both", expand=True)
        self.bank = tk.Text(inner, wrap="word", bg=_BG, relief="flat", bd=0,
                            padx=8, pady=6, spacing3=10, cursor="arrow",
                            highlightthickness=0)
        bvsb = tk.Scrollbar(inner, command=self.bank.yview)
        self.bank.configure(yscrollcommand=bvsb.set)
        self.bank.pack(side="left", fill="both", expand=True)
        bvsb.pack(side="right", fill="y")
        self._fill_bank()

        root.bind("<Escape>", lambda _e: self.cancel())
        root.bind("<Return>", lambda _e: self.confirm())
        root.bind("<Control-plus>", lambda _e: self._zoom(_ZOOM_STEP))
        root.bind("<Control-minus>", lambda _e: self._zoom(1 / _ZOOM_STEP))
        self._refresh()

    # --------------------------------------------------------------- widgets
    def _button(self, master, text, command, kind="ghost"):
        bg, fg, active = ((_ACCENT, _t.ACCENT_INK, _t.ACCENT_HOVER) if kind == "accent"
                          else (_t.SURFACE_2, _INK, _t.HOVER))
        return tk.Button(master, text=text, command=command, font=("Segoe UI", 10),
                         bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                         relief="flat", bd=0, padx=14, pady=6, cursor="hand2")

    # ---- screen view: image + angled borders ----
    def _render_image(self):
        from PIL import Image, ImageEnhance, ImageTk
        ds = self.display_scale
        w, h = max(int(self.pil.width * ds), 1), max(int(self.pil.height * ds), 1)
        disp = self.pil.resize((w, h), Image.BILINEAR)
        disp = ImageEnhance.Brightness(disp).enhance(_DIM_BRIGHTNESS)
        self._photo = ImageTk.PhotoImage(disp)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo, tags="bg")
        self.canvas.config(scrollregion=(0, 0, w, h))
        self._fill_canvas()
        if hasattr(self, "zoom_lbl"):
            self.zoom_lbl.config(text=f"{ds * 100:.0f}%")

    def _fill_canvas(self):
        self.canvas.delete("chip")
        ds = self.display_scale
        for chip in self.chips:
            if chip.quad is None:
                continue
            tag = f"c{id(chip)}"
            chip.tag = tag
            pts = [c * ds for point in chip.quad for c in point]
            self.canvas.create_polygon(*pts, fill=_EDGE_FILL, stipple="gray12",
                                       outline=_EDGE, width=2, joinstyle="round",
                                       tags=("chip", tag))
            self.canvas.tag_bind(tag, "<Button-1>",
                                 lambda _e, c=chip: self.toggle(c))
            self.canvas.tag_bind(tag, "<Enter>",
                                 lambda _e, c=chip: self._hover(c, True))
            self.canvas.tag_bind(tag, "<Leave>",
                                 lambda _e, c=chip: self._hover(c, False))
            self._paint_canvas(chip)

    def _paint_canvas(self, chip):
        if chip.tag is None or self.canvas is None:
            return
        for it in self.canvas.find_withtag(chip.tag):
            if chip.selected:
                self.canvas.itemconfig(it, outline=_SEL_BORDER, width=3,
                                       fill=_SEL_BG, stipple="gray25")
            else:
                self.canvas.itemconfig(it, outline=_EDGE, width=2,
                                       fill=_EDGE_FILL, stipple="gray12")
        if chip.selected:
            self.canvas.tag_raise(chip.tag)

    # ---- zoom / pan ----
    def _zoom(self, factor):
        lo, hi = self.fit_scale, _ZOOM_MAX_DISPLAY
        self.display_scale = max(lo, min(hi, self.display_scale * factor))
        self._render_image()

    def _zoom_fit(self):
        self.display_scale = self.fit_scale
        self._render_image()

    def _on_wheel(self, event):
        if event.state & 0x0004:             # Ctrl -> zoom
            self._zoom(_ZOOM_STEP if event.delta > 0 else 1 / _ZOOM_STEP)
        elif event.state & 0x0001:           # Shift -> horizontal
            self.canvas.xview_scroll(int(-event.delta / 120), "units")
        else:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ---- word bank ----
    def _make_bank_chip(self, chip):
        text = chip.text + ("  ⌨" if chip.line_index is None else "")
        lbl = tk.Label(self.bank, text=text, font=_CHIP_FONT, padx=12, pady=5,
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
        last = None
        for chip in self.chips:
            key = chip.line_index if chip.line_index is not None else _TYPED_LINE
            if last is not None and key != last:
                self.bank.insert("end", "\n")
            self.bank.window_create("end", window=self._make_bank_chip(chip),
                                    padx=4, pady=4)
            last = key
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

    def _paint(self, chip):
        self._paint_canvas(chip)
        self._paint_bank(chip)

    def _hover(self, chip, entering):
        if chip.selected:
            return
        if chip.bank_widget is not None:
            colour = _CHIP_HOVER if entering else _CHIP_BORDER
            chip.bank_widget.config(highlightbackground=colour, highlightcolor=colour)
        if chip.tag is not None and self.canvas is not None:
            for it in self.canvas.find_withtag(chip.tag):
                self.canvas.itemconfig(
                    it, outline=_ACCENT if entering else _EDGE,
                    width=3 if entering else 2,
                    stipple="gray25" if entering else "gray12")

    # -------------------------------------------------------------- selection
    def selected(self):
        return [c for c in self.chips if c.selected]

    def toggle(self, chip):
        chip.selected = not chip.selected
        self._paint(chip)
        self._refresh()

    def _refresh(self):
        picked = self.selected()
        self.count_lbl.config(text=f"{len(self.chips)} words")
        if not picked:
            self.status.config(text="Click the words you want   ·   Enter to record"
                                    "   ·   Esc to cancel")
        else:
            self.status.config(text=f"{len(picked)} selected:   "
                                    + "  ".join(c.text for c in picked))
        self.merge_btn.config(state="normal" if self._mergeable() else "disabled")

    def _mergeable(self):
        picked = self.selected()
        if len(picked) < 2 or any(c.line_index is None for c in picked):
            return False
        if len({c.line_index for c in picked}) != 1:
            return False
        orders = sorted(c.order for c in picked)
        return orders == list(range(orders[0], orders[0] + len(orders)))

    # ---------------------------------------------------------------- actions
    def _add_typed_dialog(self):
        _TypeDialog(self.root, self._add_typed)

    def _add_typed(self, text):
        text = (text or "").strip()
        if not text:
            return
        self._typed += 1
        chip = _Chip(text, None, self._typed, None)
        chip.selected = True                 # you typed it because you want it
        self.chips.append(chip)
        self._fill_bank()
        self._refresh()
        log.info("typed word added: %s", text)

    def merge(self):
        picked = sorted(self.selected(), key=lambda c: c.order)
        if not self._mergeable():
            return
        merged = "".join(c.text for c in picked)
        segment.add_user_word(merged)
        keep, drop = picked[0], picked[1:]
        keep.text = merged
        if all(c.quad for c in picked):
            first, last = picked[0].quad, picked[-1].quad
            keep.quad = (first[0], last[1], last[2], first[3])
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
            if chip.line_index is not None:
                by_line.setdefault(chip.line_index, []).append(chip)
        context = " ".join(self.lines[i].text for i in sorted(by_line)
                           if i < len(self.lines))
        self.result = {"tokens": [c.text for c in picked], "context": context}
        self.root.destroy()

    def cancel(self):
        self.result = None
        self.root.destroy()


class _TypeDialog(tk.Toplevel):
    """Type a word OCR couldn't read — a stylized font, or text too small to detect."""

    def __init__(self, master, on_add):
        super().__init__(master)
        self.on_add = on_add
        self.title("Type a word")
        self.configure(bg=_BG)
        self.transient(master)
        tk.Label(self, text="A word OCR missed (fancy font, too small):",
                 bg=_BG, fg=_INK, font=_UI_FONT, padx=14, pady=(12, 4)).pack(
            anchor="w")
        self.entry = tk.Entry(self, font=("Microsoft YaHei", 14), width=22,
                              bg=_t.SURFACE_2, fg=_INK, insertbackground=_INK,
                              relief="flat")
        self.entry.pack(padx=14, pady=(0, 10), ipady=4)
        row = tk.Frame(self, bg=_BG)
        row.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(row, text="Add", command=self._ok, bg=_ACCENT, fg=_t.ACCENT_INK,
                  relief="flat", padx=14, pady=5, cursor="hand2").pack(side="right")
        tk.Button(row, text="Cancel", command=self.destroy, bg=_t.SURFACE_2, fg=_INK,
                  relief="flat", padx=14, pady=5, cursor="hand2").pack(
            side="right", padx=6)
        self.entry.focus_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def _ok(self):
        self.on_add(self.entry.get())
        self.destroy()


def _to_pil(img):
    """BGR numpy -> RGB PIL, or None if Pillow is missing."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not available — showing the word bank without the frame")
        return None
    try:
        return Image.fromarray(img[:, :, ::-1])
    except Exception:
        log.exception("could not read the captured frame")
        return None


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

    pil = _to_pil(img) if img is not None else None
    if pil is not None:
        area_w, area_h = w - 48, int(h * _IMAGE_H_FRAC)
        fit_scale = min(area_w / pil.width, area_h / pil.height, 1.0)
    else:
        fit_scale = 1.0
    picker = _Picker(root, pil, fit_scale, lines)

    root.attributes("-topmost", True)
    root.focus_force()
    if owns_root:
        root.mainloop()
    else:
        root.grab_set()
        root.wait_window()
    return picker.result
