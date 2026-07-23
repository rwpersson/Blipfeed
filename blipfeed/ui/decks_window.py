"""The deck manager — graduate a finished batch, or bring one back.

Opens as a Toplevel of the dashboard so it can refresh the dashboard when a deck's state
changes. Graduating just flips the shared `archived` flag in the deck file; where new
cards go is re-derived from the deck files on the next promote (`blip_link.active_deck_id`),
so the running capture loop follows along without any shared in-memory state.

Two operations, mirrored by Blip through the shared `archived` flag
(docs/DECK_ARCHIVE_CONTRACT.md):

- **Graduate** — you've learned this batch; retire it from Blip's review. It stays in
  your Carded list here. If it was the active deck, new cards roll to a fresh one.
- **Restore** — put an archived deck back into Blip's review.

Auto-cap (max_deck_cards) rolls the active deck to a fresh file on its own so nothing
balloons; graduating is the deliberate "move on" that this window is for.
"""
import sys
import tkinter as tk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from . import theme as t  # noqa: E402
from .. import blip_link  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.decks")


class DecksManager:
    """Builds the manager into `container`; `on_change()` lets the dashboard refresh."""

    def __init__(self, container, cfg, on_change=None, on_close=None):
        self.cfg = cfg
        self.on_change = on_change
        self.on_close = on_close
        container.configure(bg=t.BG)

        # bottom bar (reserved first — Blip gotcha #16) with a status line + Close
        bar = tk.Frame(container, bg=t.SURFACE)
        bar.pack(fill="x", side="bottom")
        self.msg = tk.Label(bar, text="", bg=t.SURFACE, fg=t.MUTED, anchor="w",
                            font=t.FONT_SMALL, justify="left", wraplength=380)
        self.msg.pack(side="left", fill="x", expand=True, padx=14, pady=10)
        if on_close:
            t.button(bar, "Close", on_close, kind="ghost").pack(
                side="right", padx=(4, 14), pady=8)

        tk.Label(container, text="Decks", bg=t.BG, fg=t.INK,
                 font=t.FONT_TITLE).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(container, text="Graduate a batch you've learned to retire it from "
                 "Blip's review — it stays in your Carded list. Restore brings one "
                 "back. The active deck auto-splits at "
                 f"{cfg.get('max_deck_cards', 150)} cards.",
                 bg=t.BG, fg=t.MUTED, font=t.FONT_SMALL, justify="left",
                 wraplength=420).pack(anchor="w", padx=20, pady=(0, 8))

        # scrollable list (decks accumulate over time)
        outer = tk.Frame(container, bg=t.BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=t.BG, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(canvas, bg=t.BG)
        win = canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(
                int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        self._render()

    # --------------------------------------------------------------- rendering
    def _render(self):
        for w in self.list.winfo_children():
            w.destroy()
        decks = blip_link.list_decks(self.cfg)
        if not decks:
            tk.Label(self.list, text="No immersion decks yet — card some words first.",
                     bg=t.BG, fg=t.FAINT, font=t.FONT).pack(anchor="w", padx=20,
                                                            pady=16)
            return
        for d in decks:
            self._row(d)

    def _row(self, d):
        card = tk.Frame(self.list, bg=t.SURFACE)
        card.pack(fill="x", padx=20, pady=4)
        inner = tk.Frame(card, bg=t.SURFACE)
        inner.pack(fill="x", padx=14, pady=10)

        left = tk.Frame(inner, bg=t.SURFACE)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=d["name"], bg=t.SURFACE, fg=t.INK, font=t.FONT_H,
                 anchor="w").pack(anchor="w")
        meta = f"{t.commas(d['count'])} card{'s' if d['count'] != 1 else ''}"
        if d["active"]:
            meta += "  ·  active — new cards land here"
        elif d["archived"]:
            meta += "  ·  archived — hidden from Blip's review"
        else:
            meta += "  ·  in Blip's review"
        tk.Label(left, text=meta, bg=t.SURFACE,
                 fg=t.GOOD if d["active"] else (t.MUTED if d["archived"] else t.INK),
                 font=t.FONT_SMALL, anchor="w").pack(anchor="w")

        if d["archived"]:
            t.button(inner, "Restore", lambda: self._restore(d["id"]),
                     kind="ghost").pack(side="right")
        else:
            t.button(inner, "Graduate", lambda: self._graduate(d["id"]),
                     kind="primary").pack(side="right")

    # --------------------------------------------------------------- actions
    def _graduate(self, deck_id):
        try:
            if blip_link.graduate(deck_id):
                self._done(f"Graduated “{deck_id}”. It stays in your Carded list; "
                           "Blip drops it from review on its next launch.")
            else:
                self._done(f"Couldn't find deck “{deck_id}”.", ok=False)
        except Exception as exc:
            log.exception("graduate failed")
            self._done(str(exc), ok=False)

    def _restore(self, deck_id):
        try:
            if blip_link.restore(deck_id):
                self._done(f"Restored “{deck_id}” — back in Blip's review next launch.")
            else:
                self._done(f"Couldn't find deck “{deck_id}”.", ok=False)
        except Exception as exc:
            log.exception("restore failed")
            self._done(str(exc), ok=False)

    def _done(self, msg, ok=True):
        self.msg.config(text=msg, fg=t.MUTED if ok else t.DANGER)
        self._render()
        if ok and self.on_change:
            self.on_change()


def open_toplevel(master, cfg, on_change=None):
    """Open the deck manager as a child of the dashboard root."""
    top = tk.Toplevel(master)
    top.title("Blipfeed — decks")
    top.geometry("480x560")
    top.configure(bg=t.BG)
    t.apply_icon(top)
    DecksManager(top, cfg, on_change=on_change, on_close=top.destroy)
    top.transient(master)
    return top


def run(cfg=None):
    from .. import config as config_mod

    root = tk.Tk()
    root.title("Blipfeed — decks")
    root.geometry("480x560")
    root.configure(bg=t.BG)
    t.apply_icon(root)
    DecksManager(root, cfg or config_mod.load(), on_close=root.destroy)
    root.mainloop()


if __name__ == "__main__":
    from ..capture import set_dpi_aware

    set_dpi_aware()
    run()
