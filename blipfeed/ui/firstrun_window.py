"""First-run onboarding — a three-step welcome shown once.

What a new user can't guess: what the app does, that it's local-only (the trust
question for anything that reads your screen), and the two setup choices — the capture
region and their level, so a beginner isn't handed words they already know.

Runs as its own process before the dashboard (see __main__), sets `onboarded` on the
way out so it never nags twice.
"""
import sys
import tkinter as tk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from . import theme as t  # noqa: E402
from .. import config as config_mod, paths  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.firstrun")


class FirstRun:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.step = 0
        self.region = cfg.region()
        self.known_note = ""
        root.title("Welcome to Blipfeed")
        root.configure(bg=t.BG)
        root.geometry("560x480")
        root.minsize(520, 440)
        t.apply_icon(root)
        root.protocol("WM_DELETE_WINDOW", self._finish)   # closing = done, no nag

        self.body = tk.Frame(root, bg=t.BG)
        self.body.pack(fill="both", expand=True)

        nav = tk.Frame(root, bg=t.SURFACE)
        nav.pack(fill="x", side="bottom")
        self.dots = tk.Label(nav, text="", bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL)
        self.dots.pack(side="left", padx=16)
        self.next_btn = t.button(nav, "Next  →", self._next, kind="primary")
        self.next_btn.pack(side="right", padx=(4, 16), pady=10)
        self.back_btn = t.button(nav, "←  Back", self._back, kind="ghost")
        self.back_btn.pack(side="right", padx=4, pady=10)

        self._render()

    # ------------------------------------------------------------- steps
    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        (self._welcome, self._privacy, self._setup)[self.step]()
        self.dots.config(text=f"Step {self.step + 1} of 3")
        self.back_btn.config(state="normal" if self.step else "disabled")
        self.next_btn.config(text="Finish  ✓" if self.step == 2 else "Next  →")

    def _title(self, text, sub):
        tk.Label(self.body, text=text, bg=t.BG, fg=t.INK, font=t.FONT_TITLE,
                 justify="left", anchor="w").pack(fill="x", padx=32, pady=(36, 8))
        tk.Label(self.body, text=sub, bg=t.BG, fg=t.MUTED, font=("Segoe UI", 11),
                 justify="left", anchor="w", wraplength=480).pack(
            fill="x", padx=32, pady=(0, 8))

    def _welcome(self):
        self._title("Welcome to Blipfeed",
                    "It watches a corner of your screen while you immerse — a video, "
                    "Douyin, an article — reads the words you see, and turns the ones "
                    "that keep coming up into flashcards in Blip.\n\nYou just immerse. "
                    "The words you actually saw show up to review, with the sentence "
                    "they came from attached.")

    def _privacy(self):
        self._title("Your screen stays yours",
                    "Everything runs on this machine — nothing is ever uploaded, ever.\n\n"
                    "• You choose exactly which region is watched.\n"
                    "• You can always see when capture is on.\n"
                    "• One click stops it.\n\n"
                    "It only reads the rectangle you point it at — nothing else on "
                    "your screen.")

    def _setup(self):
        self._title("Two quick things", "")
        card = t.card(self.body, "1 · The area to watch")
        row = tk.Frame(card, bg=t.SURFACE)
        row.pack(fill="x", padx=14, pady=(4, 12))
        self.region_lbl = tk.Label(
            row, text=self.region.label() if self.region else "not set yet",
            bg=t.SURFACE, fg=t.INK if self.region else t.MUTED, font=t.FONT,
            anchor="w")
        self.region_lbl.pack(side="left", fill="x", expand=True)
        t.button(row, "Pick region", self._pick_region, kind="primary").pack(
            side="right")

        card2 = t.card(self.body, "2 · Your level")
        tk.Label(card2, text="So we don't hand you words you already know.",
                 bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL, anchor="w").pack(
            fill="x", padx=14, pady=(2, 4))
        row2 = tk.Frame(card2, bg=t.SURFACE)
        row2.pack(fill="x", padx=14, pady=(0, 12))
        t.button(row2, "Sync what I know from Blip", self._sync_known,
                 kind="ghost").pack(side="left")
        tk.Label(row2, text="  or just start fresh — a beginner sees everything.",
                 bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL).pack(side="left")
        self.known_lbl = tk.Label(card2, text=self.known_note, bg=t.SURFACE,
                                  fg=t.GOOD, font=t.FONT_SMALL, anchor="w")
        self.known_lbl.pack(fill="x", padx=14, pady=(0, 8))

    # ------------------------------------------------------------- actions
    def _pick_region(self):
        from .region import pick_region
        region = pick_region(master=self.root, current=self.region)
        if region is not None:
            self.region = region
            self.cfg.set_region(region).save()
            self.region_lbl.config(text=region.label(), fg=t.INK)

    def _sync_known(self):
        from .. import known, store
        try:
            conn = store.connect()
            result = known.sync_all(conn, self.cfg.language,
                                    self.cfg.get("blip_graduate_at", 9))
            conn.close()
            self.known_note = (f"Marked {result['total_known']} words known "
                               f"from your Blip decks.")
        except Exception as exc:
            log.exception("known sync during onboarding failed")
            self.known_note = f"Couldn't sync: {exc}"
        self.known_lbl.config(text=self.known_note)

    def _next(self):
        if self.step < 2:
            self.step += 1
            self._render()
        else:
            self._finish()

    def _back(self):
        if self.step:
            self.step -= 1
            self._render()

    def _finish(self):
        self.cfg.set("onboarded", True).save()
        # Also drop the sentinel — the authoritative 'already onboarded' signal, immune
        # to any later config rewrite (see paths.onboarded_marker).
        try:
            marker = paths.onboarded_marker()
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError:
            log.exception("couldn't write the onboarded marker")
        self.root.destroy()


def run(cfg=None):
    root = tk.Tk()
    FirstRun(root, cfg or config_mod.load())
    root.mainloop()


if __name__ == "__main__":
    from ..capture import set_dpi_aware

    set_dpi_aware()
    run()
