"""MANUAL mode — freeze the frame, click the words you want.

The precision path (CLAUDE.md Decisions #2b). AUTO mode records everything and lets
frequency sort it out; here *you* are the filter, which makes a dense frame (a Douyin
homepage) workable and lets you grab one specific term and move on.

**Merge is the point, not a bonus.** The real-frame test found jieba splits OOV slang:
万魂幡 -> 万魂 / 幡. No heuristic can fix that safely (幡 alone is a valid word), so the
human does it: select the adjacent chips, hit Merge, and the fused term is recorded
*and* taught to the segmenter permanently (`segment.add_user_word`).

## Layout — legibility first

The words used to be drawn *onto* the screenshot at the source text's own scale, so on
a busy frame they were tiny and unreadable. Instead: the frozen frame is a **dimmed
context strip** at the top (so you still see what you captured and where), and the
words are a **readable word bank** below — bold pills, grouped in reading order,
wrapping and scrolling, with real click targets. The two are decoupled, so chip
legibility no longer depends on how small the on-screen text was.

An ordinary focus-taking window — you asked for this frame (Decisions #6).
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
_BG = "#14171c"          # window
_PANEL = "#1b2027"       # bars
_INK = "#e8eef6"         # primary text
_DIM = "#8b95a3"         # secondary text
_CHIP_BG = "#2a313b"
_CHIP_FG = "#e8eef6"
_CHIP_BORDER = "#3b4552"
_CHIP_HOVER = "#59657a"
_SEL_BG = "#2f6fed"      # selected
_SEL_FG = "#ffffff"
_SEL_BORDER = "#6b9bff"
_ACCENT = "#2f6fed"

_CHIP_FONT = ("Microsoft YaHei", 15, "bold")     # big + bold, the whole point
_UI_FONT = ("Segoe UI", 10)
_HEAD_FONT = ("Segoe UI Semibold", 13)

_CONTEXT_MAX_H = 200     # the dimmed screenshot strip
_WIN_W_FRAC, _WIN_H_FRAC = 0.62, 0.86


class _Chip:
    __slots__ = ("text", "line_index", "order", "selected", "widget")

    def __init__(self, text, line_index, order):
        self.text = text
        self.line_index = line_index
        self.order = order
        self.selected = False
        self.widget = None


def _chips_for(lines):
    """Clickable tokens in reading order. Position no longer matters (the bank is a
    list, not an overlay), only line + order for merge adjacency."""
    chips = []
    for li, line in enumerate(lines):
        for order, tok in enumerate(segment.tokenize(line.text)):
            if segment.keep(tok):        # punctuation/particles aren't clickable
                chips.append(_Chip(tok, li, order))
    return chips


class _Picker:
    def __init__(self, root, photo, lines):
        self.root = root
        self.result = None
        self.lines = lines
        self.chips = _chips_for(lines)
        root.configure(bg=_BG)

        # --- context strip: the dimmed frame you captured --------------------
        if photo is not None:
            head = tk.Frame(root, bg=_BG)
            head.pack(fill="x")
            tk.Label(head, image=photo, bg=_BG, bd=0).pack()
            head.image = photo

        # --- header ----------------------------------------------------------
        top = tk.Frame(root, bg=_BG)
        top.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(top, text="Pick the words you want", font=_HEAD_FONT, bg=_BG,
                 fg=_INK).pack(side="left")
        tk.Label(top, text=f"{len(self.chips)} detected", font=_UI_FONT, bg=_BG,
                 fg=_DIM).pack(side="right")
        tk.Label(root, text="Tip: select two adjacent pieces of a split word "
                            "(万魂 + 幡) and Merge to fix it — it stays merged next time.",
                 font=_UI_FONT, bg=_BG, fg=_DIM, anchor="w").pack(
            fill="x", padx=18, pady=(0, 8))

        # --- action bar ------------------------------------------------------
        # Packed BEFORE the expanding word bank on purpose: pack allocates space in
        # order, so a bar packed after an expand=True widget is the first thing
        # squeezed off-screen (Blip's gotcha #16). Reserve the bottom first.
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

        # --- the word bank ---------------------------------------------------
        wrap = tk.Frame(root, bg=_BG)
        wrap.pack(fill="both", expand=True, padx=12)
        self.bank = tk.Text(wrap, wrap="word", bg=_BG, relief="flat", bd=0,
                            padx=8, pady=6, spacing3=10, cursor="arrow",
                            highlightthickness=0)
        vsb = tk.Scrollbar(wrap, command=self.bank.yview)
        self.bank.configure(yscrollcommand=vsb.set)
        self.bank.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._fill_bank()

        root.bind("<Escape>", lambda _e: self.cancel())
        root.bind("<Return>", lambda _e: self.confirm())
        self._refresh()

    # --------------------------------------------------------------- widgets
    def _button(self, master, text, command, kind="ghost"):
        if kind == "accent":
            bg, fg, active = _ACCENT, "#ffffff", "#4880f5"
        else:
            bg, fg, active = "#2b323c", _INK, "#39424e"
        b = tk.Button(master, text=text, command=command, font=("Segoe UI", 10),
                      bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                      relief="flat", bd=0, padx=16, pady=7, cursor="hand2")
        return b

    def _make_chip(self, chip):
        lbl = tk.Label(self.bank, text=chip.text, font=_CHIP_FONT, padx=12, pady=5,
                       bg=_CHIP_BG, fg=_CHIP_FG, cursor="hand2",
                       highlightthickness=1, highlightbackground=_CHIP_BORDER,
                       highlightcolor=_CHIP_BORDER)
        lbl.bind("<Button-1>", lambda _e, c=chip: self.toggle(c))
        lbl.bind("<Enter>", lambda _e, c=chip: self._hover(c, True))
        lbl.bind("<Leave>", lambda _e, c=chip: self._hover(c, False))
        chip.widget = lbl
        self._paint(chip)
        return lbl

    def _fill_bank(self):
        self.bank.config(state="normal")
        self.bank.delete("1.0", "end")
        last_line = None
        for chip in self.chips:
            if last_line is not None and chip.line_index != last_line:
                self.bank.insert("end", "\n")
            self.bank.window_create("end", window=self._make_chip(chip),
                                    padx=4, pady=4)
            last_line = chip.line_index
        self.bank.config(state="disabled")

    # --------------------------------------------------------------- painting
    def _paint(self, chip):
        if chip.selected:
            chip.widget.config(bg=_SEL_BG, fg=_SEL_FG, highlightbackground=_SEL_BORDER,
                               highlightcolor=_SEL_BORDER)
        else:
            chip.widget.config(bg=_CHIP_BG, fg=_CHIP_FG,
                               highlightbackground=_CHIP_BORDER,
                               highlightcolor=_CHIP_BORDER)

    def _hover(self, chip, entering):
        if chip.selected:
            return
        chip.widget.config(highlightbackground=_CHIP_HOVER if entering else _CHIP_BORDER,
                           highlightcolor=_CHIP_HOVER if entering else _CHIP_BORDER)

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
            words = "  ".join(c.text for c in picked)
            self.status.config(text=f"{len(picked)} selected:   {words}")
        self.merge_btn.config(state="normal" if self._mergeable() else "disabled")

    def _mergeable(self):
        """Two or more chips, adjacent and in order on the same line."""
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
        for chip in drop:
            self.chips.remove(chip)
        keep.selected = True
        self._fill_bank()                      # re-flow: widths changed
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


def _to_photo(img, root):
    """BGR numpy -> a dimmed, fitted Tk image for the context strip. (photo | None)."""
    try:
        from PIL import Image, ImageEnhance, ImageTk
    except ImportError:
        log.warning("Pillow not available — showing the word bank without the frame")
        return None
    try:
        pil = Image.fromarray(img[:, :, ::-1])          # BGR -> RGB
        max_w = int(root.winfo_screenwidth() * _WIN_W_FRAC)
        scale = min(max_w / pil.width, _CONTEXT_MAX_H / pil.height, 1.0)
        pil = pil.resize((max(int(pil.width * scale), 1),
                          max(int(pil.height * scale), 1)))
        pil = ImageEnhance.Brightness(pil).enhance(0.45)   # dim: context, not focus
        return ImageTk.PhotoImage(pil)
    except Exception:
        log.exception("could not render the captured frame")
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

    w = int(root.winfo_screenwidth() * _WIN_W_FRAC)
    h = int(root.winfo_screenheight() * _WIN_H_FRAC)
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"{w}x{h}+{x}+{y}")

    photo = _to_photo(img, root) if img is not None else None
    picker = _Picker(root, photo, lines)
    root.attributes("-topmost", True)
    root.focus_force()
    if owns_root:
        root.mainloop()
    else:
        root.grab_set()
        root.wait_window()
    return picker.result
