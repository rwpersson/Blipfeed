"""The dashboard window — Blipfeed's home. A dumb shell over DashboardController.

All logic lives in dashboard.py (tested, no Tk); this draws it and forwards clicks.
The one architectural rule it enforces: **one Tk root per process** (Blip gotcha #13),
so windows the user *browses* (Review) launch as a separate process, while the modal
bits of a capture flow (the region picker, the manual chip picker) are Toplevels of
this root and return a value inline.

Global hotkeys work while a game has focus; their callbacks marshal onto the Tk thread
with `after`, since the hotkey pump runs on its own thread.
"""
import subprocess
import sys
import threading
import tkinter as tk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from .. import hotkeys as hotkeys_mod  # noqa: E402
from ..dashboard import NoRegion  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.dashboard")

_BG = "#14171c"
_CARD = "#1b2027"
_INK = "#e8eef6"
_DIM = "#8b95a3"
_ACCENT = "#2f6fed"
_GOOD = "#37c26b"
_STOP = "#e0524d"
_ON = "#2f6fed"
_OFF = "#2b323c"

_F_TITLE = ("Segoe UI Semibold", 18)
_F_H = ("Segoe UI Semibold", 11)
_F = ("Segoe UI", 10)
_F_BIG = ("Segoe UI Semibold", 13)
_F_NUM = ("Segoe UI Semibold", 20)


class DashboardWindow:
    def __init__(self, root, controller):
        self.root = root
        self.c = controller
        self._busy = threading.Lock()
        root.title("Blipfeed")
        root.configure(bg=_BG)
        root.geometry("460x600")
        root.minsize(420, 560)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_primary()
        self._build_toggles()
        self._build_region()
        self._build_stats()
        self._build_actions()

        tk.Label(root, text="New cards appear in Blip on its next launch.",
                 bg=_BG, fg=_DIM, font=("Segoe UI", 9)).pack(side="bottom", pady=8)

        self._register_hotkeys()
        self._refresh()

    # ------------------------------------------------------------- building
    def _build_header(self):
        head = tk.Frame(self.root, bg=_BG)
        head.pack(fill="x", padx=20, pady=(18, 6))
        tk.Label(head, text="Blipfeed", bg=_BG, fg=_INK, font=_F_TITLE).pack(
            side="left")
        self.dot = tk.Label(head, text="○ Idle", bg=_BG, fg=_DIM, font=_F_H)
        self.dot.pack(side="right")

    def _build_primary(self):
        wrap = tk.Frame(self.root, bg=_BG)
        wrap.pack(fill="x", padx=20, pady=(6, 10))
        self.primary = tk.Button(wrap, text="▶  Start", command=self._primary_click,
                                 font=_F_BIG, bg=_ACCENT, fg="#fff", relief="flat",
                                 bd=0, pady=12, cursor="hand2",
                                 activebackground="#4880f5", activeforeground="#fff")
        self.primary.pack(fill="x")

    def _card(self, title):
        card = tk.Frame(self.root, bg=_CARD)
        card.pack(fill="x", padx=20, pady=6)
        if title:
            tk.Label(card, text=title, bg=_CARD, fg=_DIM,
                     font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14,
                                                        pady=(10, 0))
        return card

    def _build_toggles(self):
        card = self._card("MODE")
        row = tk.Frame(card, bg=_CARD)
        row.pack(fill="x", padx=14, pady=(4, 12))
        self.mode_auto = self._seg(row, "Auto", lambda: self._set_mode("auto"))
        self.mode_manual = self._seg(row, "Manual", lambda: self._set_mode("manual"))
        self.mode_auto.pack(side="left")
        self.mode_manual.pack(side="left", padx=(6, 0))

        self.autocard_var = tk.BooleanVar()
        self.autocard = tk.Checkbutton(
            row, text="Auto-card", variable=self.autocard_var,
            command=self._toggle_autocard, bg=_CARD, fg=_INK, selectcolor=_OFF,
            activebackground=_CARD, activeforeground=_INK, font=_F,
            highlightthickness=0, bd=0, cursor="hand2")
        self.autocard.pack(side="right")

    def _seg(self, master, text, cmd):
        return tk.Button(master, text=text, command=cmd, font=_F, relief="flat",
                         bd=0, padx=16, pady=6, cursor="hand2",
                         activeforeground="#fff")

    def _build_region(self):
        card = self._card("CAPTURE REGION")
        row = tk.Frame(card, bg=_CARD)
        row.pack(fill="x", padx=14, pady=(4, 12))
        self.region_lbl = tk.Label(row, text="—", bg=_CARD, fg=_INK, font=_F,
                                   anchor="w")
        self.region_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Change", command=self._change_region, font=_F,
                  bg=_OFF, fg=_INK, relief="flat", bd=0, padx=12, pady=5,
                  cursor="hand2", activebackground="#39424e").pack(side="right")

    def _build_stats(self):
        card = self._card("")
        grid = tk.Frame(card, bg=_CARD)
        grid.pack(fill="x", padx=14, pady=12)
        self.stat = {}
        rows = [("This session", ["snapshots", "words", "cards"]),
                ("To review", ["needs a definition", "ready"]),
                ("Library", ["known", "carded"])]
        for r, (label, keys) in enumerate(rows):
            tk.Label(grid, text=label.upper(), bg=_CARD, fg=_DIM,
                     font=("Segoe UI", 8, "bold")).grid(row=r * 2, column=0,
                                                        columnspan=3, sticky="w",
                                                        pady=(6, 0))
            for col, key in enumerate(keys):
                cell = tk.Frame(grid, bg=_CARD)
                cell.grid(row=r * 2 + 1, column=col, sticky="w", padx=(0, 22))
                num = tk.Label(cell, text="0", bg=_CARD, fg=_INK, font=_F_NUM)
                num.pack(anchor="w")
                tk.Label(cell, text=key, bg=_CARD, fg=_DIM, font=("Segoe UI", 8)).pack(
                    anchor="w")
                self.stat[key] = num

    def _build_actions(self):
        row = tk.Frame(self.root, bg=_BG)
        row.pack(fill="x", padx=20, pady=(8, 4))
        self.review_btn = tk.Button(row, text="Review", command=self._open_review,
                                    font=_F_BIG, bg=_OFF, fg=_INK, relief="flat",
                                    bd=0, pady=9, cursor="hand2",
                                    activebackground="#39424e")
        self.review_btn.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Sync known", command=self._sync_known, font=_F,
                  bg=_OFF, fg=_INK, relief="flat", bd=0, padx=14, pady=9,
                  cursor="hand2", activebackground="#39424e").pack(
            side="left", padx=(8, 0))

    # --------------------------------------------------------------- actions
    def _primary_click(self):
        if self.c.cfg.mode == "manual":
            self._capture_now()
            return
        try:
            self.c.toggle()
        except NoRegion:
            self._change_region()
            if self.c.cfg.region() is not None:
                try:
                    self.c.start()
                except NoRegion:
                    pass
        self._refresh()

    def _capture_now(self):
        if not self._busy.acquire(blocking=False):
            return
        try:
            from .pick import pick_tokens
            self.dot.config(text="◐ Capturing…", fg=_ACCENT)
            self.root.update_idletasks()
            self.c.capture_manual(
                lambda img, lines: pick_tokens(img, lines, master=self.root))
        except NoRegion:
            self._change_region()
        except Exception as exc:
            log.exception("manual capture failed")
            self._toast(str(exc))
        finally:
            self._busy.release()
            self._refresh()

    def _set_mode(self, mode):
        self.c.set_mode(mode)
        self._refresh()

    def _toggle_autocard(self):
        self.c.set_auto_card(self.autocard_var.get())
        self._refresh()

    def _change_region(self):
        from .region import pick_region
        region = pick_region(master=self.root, current=self.c.cfg.region())
        if region is not None:
            was_running = self.c.running()
            self.c.stop()
            self.c.set_region(region)
            if was_running:
                self.c.start()
        self._refresh()

    def _open_review(self):
        # a browsing window -> its own process, so we never make a 2nd Tk root here
        subprocess.Popen([sys.executable, "-m", "blipfeed.review"])

    def _sync_known(self):
        result = self.c.sync_known()
        self._toast(f"Known words: {result['total_known']} "
                    f"(from {result['deck_total']} Blip cards)")
        self._refresh()

    # --------------------------------------------------------------- refresh
    def _refresh(self):
        s = self.c.stats()
        running = s["running"]
        if running:
            self.dot.config(text="● Capturing", fg=_GOOD)
        else:
            self.dot.config(text="○ Idle", fg=_DIM)

        if s["mode"] == "manual":
            self.primary.config(text="◉  Capture now", bg=_ACCENT)
        elif running:
            self.primary.config(text="■  Stop", bg=_STOP, activebackground="#ec6b66")
        else:
            self.primary.config(text="▶  Start", bg=_ACCENT, activebackground="#4880f5")

        self._paint_seg(self.mode_auto, s["mode"] == "auto")
        self._paint_seg(self.mode_manual, s["mode"] == "manual")
        self.autocard_var.set(s["auto_card"])
        self.region_lbl.config(text=s["region"].label() if s["region"]
                               else "not set — click Change")

        sess = s["session"] or {}
        self.stat["snapshots"].config(text=str(sess.get("ticks", 0)))
        self.stat["words"].config(text=str(sess.get("observations", 0)))
        self.stat["cards"].config(text=str(len(sess.get("promoted", []))))
        self.stat["needs a definition"].config(text=str(s["needs_def"]))
        self.stat["ready"].config(text=str(s["ready"]))
        self.stat["known"].config(text=str(s["known"]))
        self.stat["carded"].config(text=str(s["carded"]))
        self.review_btn.config(
            text=f"Review  ({s['needs_def'] + s['ready']})"
            if (s["needs_def"] + s["ready"]) else "Review")

        # re-poll while running (live counters); idle can poll slowly
        self.root.after(1000 if running else 2500, self._refresh)

    def _paint_seg(self, btn, on):
        btn.config(bg=_ON if on else _OFF, fg="#fff" if on else _INK,
                   activebackground=_ON if on else "#39424e")

    def _toast(self, text):
        self.dot_after = getattr(self, "dot_after", None)
        self.region_lbl.config(text=text)
        self.root.after(4000, self._refresh)

    # --------------------------------------------------------------- hotkeys
    def _register_hotkeys(self):
        cfg = self.c.cfg

        def marshal(fn):
            return lambda: self.root.after(0, fn)

        self.listener = hotkeys_mod.HotkeyListener({
            cfg.hotkey_toggle: marshal(self._primary_click),
            cfg.hotkey_manual: marshal(self._capture_now),
            cfg.hotkey_region: marshal(self._change_region),
        })
        self.listener.start()
        self.listener.wait_ready()
        for combo, why in self.listener.failed.items():
            log.warning("hotkey %s unavailable: %s", combo, why)

    def _on_close(self):
        try:
            self.listener.stop()
        except Exception:
            log.exception("stopping hotkeys")
        self.c.close()
        self.root.destroy()


def run(controller):
    root = tk.Tk()
    DashboardWindow(root, controller)
    root.mainloop()


if __name__ == "__main__":
    from .. import config as config_mod
    from ..capture import set_dpi_aware
    from ..dashboard import DashboardController

    set_dpi_aware()
    run(DashboardController(config_mod.load()))
