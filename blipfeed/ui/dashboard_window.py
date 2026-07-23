"""The dashboard window — Blipfeed's home. A dumb shell over DashboardController.

Logic lives in dashboard.py (tested, no Tk); this draws it with the shared theme and
forwards clicks. One Tk root per process (Blip gotcha #13), so Review launches as a
separate process while the region/chip pickers are Toplevels that return inline.
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

from . import theme as t  # noqa: E402
from .. import hotkeys as hotkeys_mod  # noqa: E402
from ..dashboard import NoRegion  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.dashboard")


class DashboardWindow:
    def __init__(self, root, controller):
        self.root = root
        self.c = controller
        self._busy = threading.Lock()
        self._pulse_on = False
        self._toast_job = None
        root.title("Blipfeed")
        root.configure(bg=t.BG)
        root.minsize(600, 320)
        t.apply_icon(root)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # scrollable body so content is never clipped; sized to content afterwards
        outer = tk.Frame(root, bg=t.BG)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=t.BG, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=t.BG)
        self._body_win = self.canvas.create_window((0, 0), window=self.body,
                                                   anchor="nw")
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._body_win, width=e.width))
        # wheel from anywhere in the window — bind on the root (every descendant's
        # bindtags include it), not a canvas <Enter>/<Leave> bind_all that dies the
        # moment the pointer moves onto a child widget
        root.bind("<MouseWheel>", self._wheel)

        self._build_header()
        self._build_primary()
        self._build_mode_region()
        self._build_stats()
        self._build_hint()
        self._build_actions()
        self._build_footer()

        root.update_idletasks()
        want_h = self.body.winfo_reqheight() + 4
        root.geometry(f"640x{min(want_h, int(root.winfo_screenheight() * 0.85))}")

        self._hinted_tray = False
        self._last_running = None
        self._children = []                  # Review/Stats run as separate processes
        self._register_hotkeys()
        self._setup_tray()
        self._pulse()
        self._refresh()

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ------------------------------------------------------------- building
    def _build_header(self):
        head = tk.Frame(self.body, bg=t.BG)
        head.pack(fill="x", padx=20, pady=(14, 4))
        tk.Label(head, text="Blipfeed", bg=t.BG, fg=t.INK, font=t.FONT_TITLE).pack(
            side="left")
        self.dot = tk.Label(head, text="○ Idle", bg=t.BG, fg=t.MUTED, font=t.FONT_H)
        self.dot.pack(side="right")

    def _build_primary(self):
        wrap = tk.Frame(self.body, bg=t.BG)
        wrap.pack(fill="x", padx=20, pady=(2, 6))
        self.primary = t.button(wrap, "▶  Start", self._primary_click,
                                kind="primary", big=True, pady=11)
        self.primary.pack(fill="x")

    def _build_mode_region(self):
        # side by side (uses the wider window) so the dashboard isn't so tall
        wrap = tk.Frame(self.body, bg=t.BG)
        wrap.pack(fill="x", padx=20, pady=6)
        left = tk.Frame(wrap, bg=t.SURFACE)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(wrap, bg=t.SURFACE)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self._build_toggles(left)
        self._build_region(right)

    def _build_toggles(self, card):
        t.section_label(card, "Mode").pack(anchor="w", padx=14, pady=(10, 0))
        row = tk.Frame(card, bg=t.SURFACE)
        row.pack(fill="x", padx=14, pady=(6, 12))
        self.mode_auto = self._seg(row, "Auto", lambda: self._set_mode("auto"))
        self.mode_manual = self._seg(row, "Manual", lambda: self._set_mode("manual"))
        self.mode_auto.pack(side="left")
        self.mode_manual.pack(side="left", padx=(6, 0))
        self.autocard_var = tk.BooleanVar()
        self.autocard = tk.Checkbutton(
            row, text="Auto-card", variable=self.autocard_var,
            command=self._toggle_autocard, bg=t.SURFACE, fg=t.INK,
            selectcolor=t.SURFACE_2, activebackground=t.SURFACE,
            activeforeground=t.INK, font=t.FONT, highlightthickness=0, bd=0,
            cursor="hand2")
        self.autocard.pack(side="right")

    def _seg(self, master, text, cmd):
        return tk.Button(master, text=text, command=cmd, font=t.FONT, relief="flat",
                         bd=0, padx=14, pady=6, cursor="hand2", activeforeground=t.INK)

    def _build_region(self, card):
        t.section_label(card, "Capture region").pack(anchor="w", padx=14, pady=(10, 0))
        row = tk.Frame(card, bg=t.SURFACE)
        row.pack(fill="x", padx=14, pady=(6, 12))
        self.region_lbl = tk.Label(row, text="—", bg=t.SURFACE, fg=t.INK, font=t.FONT,
                                   anchor="w")
        self.region_lbl.pack(side="left", fill="x", expand=True)
        t.button(row, "Change", self._change_region, kind="ghost", padx=12,
                 pady=5).pack(side="right")

    def _build_stats(self):
        card = t.card(self.body, "")
        grid = tk.Frame(card, bg=t.SURFACE)
        grid.pack(fill="x", padx=14, pady=(8, 10))
        self.stat = {}
        num_font = ("Segoe UI Semibold", 16)
        # "This session" spans the top; "To review" and "Library" share the next row so
        # the block stays short and uses the width.
        groups = [("This session", ["snapshots", "words", "cards"], 0, 0, 3),
                  ("To review", ["needs a definition", "ready"], 2, 0, 2),
                  ("Library", ["known", "carded"], 2, 3, 2)]
        for label, keys, base_row, base_col, span in groups:
            t.section_label(grid, label, bg=t.SURFACE).grid(
                row=base_row, column=base_col, columnspan=span, sticky="w", pady=(8, 0))
            for i, key in enumerate(keys):
                cell = tk.Frame(grid, bg=t.SURFACE)
                cell.grid(row=base_row + 1, column=base_col + i, sticky="w",
                          padx=(0, 18))
                num = tk.Label(cell, text="0", bg=t.SURFACE, fg=t.INK, font=num_font)
                num.pack(anchor="w")
                tk.Label(cell, text=key, bg=t.SURFACE, fg=t.MUTED,
                         font=t.FONT_SMALL).pack(anchor="w")
                self.stat[key] = num

    def _build_hint(self):
        # empty-state / contextual guidance line
        self.hint = tk.Label(self.body, text="", bg=t.BG, fg=t.MUTED, font=t.FONT,
                             wraplength=420, justify="left")
        self.hint.pack(fill="x", padx=22, pady=(2, 2))

    def _build_actions(self):
        # Review is the primary follow-up, full width; the utilities sit in their own
        # evenly-spread, labelled row below (icons alone read as cramped/awkward).
        self.review_btn = t.button(self.body, "Review", self._open_review,
                                   kind="ghost", big=True)
        self.review_btn.pack(fill="x", padx=20, pady=(8, 6))
        row = tk.Frame(self.body, bg=t.BG)
        row.pack(fill="x", padx=20, pady=(0, 6))
        for text, cmd in (("Sync known", self._sync_known),
                          ("🗂  Decks", self._open_decks),
                          ("📊  Stats", self._open_stats),
                          ("⚙  Settings", self._open_settings)):
            t.button(row, text, cmd, kind="ghost").pack(
                side="left", fill="x", expand=True, padx=3)

    def _build_footer(self):
        self.toast = tk.Label(self.body, text="", bg=t.BG, fg=t.MUTED,
                              font=t.FONT_SMALL, wraplength=430)
        self.toast.pack(pady=(6, 2))
        tk.Label(self.body, text="New cards appear in Blip on its next launch.",
                 bg=t.BG, fg=t.FAINT, font=t.FONT_SMALL).pack(pady=(0, 8))

    # --------------------------------------------------------------- actions
    def _primary_click(self):
        if self.c.cfg.mode == "manual":
            self._capture_now()
            return
        if self.c.running():
            self.c.stop()
            self._refresh()
            return
        self._start_with_region_check()

    def _start_with_region_check(self):
        """Starting AUTO **always** confirms the capture region first. Every time.

        A wrong region is uniquely brutal: capture runs happily, mines nothing, and you
        only discover it after the session. A region silently reset to a 100x40 box ate a
        full hour of immersion. Confirming costs one keypress — the picker opens with the
        current region drawn, Esc keeps it, a drag replaces it — and it makes a wrong
        region impossible to not notice. Never make this conditional on "we already have
        one": having one is exactly the case that failed.
        """
        if not self._confirm_region():
            self._refresh()
            return
        try:
            self.c.start()
        except NoRegion:
            self._notify("Pick a capture region to start.", "danger")
        self._refresh()

    def _confirm_region(self):
        """Picker with the current region drawn: drag replaces it, Esc keeps it.
        Returns False only when there is still no region at all."""
        from .region import pick_region
        current = self.c.cfg.region()
        region = pick_region(master=self.root, current=current)
        if region is not None:
            self.c.set_region(region)
            self._notify(f"Region set to {region.label()}.", "good")
            return True
        if current is None:
            self._notify("No capture region — nothing would be mined.", "danger")
            return False
        return True

    def _capture_now(self):
        if not self._busy.acquire(blocking=False):
            return
        try:
            from .pick import pick_tokens
            self._toast_now("Capturing…", t.ACCENT)
            self.root.update_idletasks()
            tokens = self.c.capture_manual(
                lambda img, lines: pick_tokens(img, lines, master=self.root))
            if tokens:
                self._notify(f"Recorded {len(tokens)}: " + " ".join(tokens[:6]), "good")
            else:
                self._notify("Nothing recorded.")
        except NoRegion:
            self._change_region()
        except Exception as exc:
            log.exception("manual capture failed")
            self._notify(str(exc), "danger")
        finally:
            self._busy.release()
            self._refresh()

    def _set_mode(self, mode):
        self.c.set_mode(mode)
        self._refresh()

    def _toggle_autocard(self):
        self.c.set_auto_card(self.autocard_var.get())
        self._toast(f"Auto-card {'on' if self.autocard_var.get() else 'off'}.")

    def _change_region(self):
        from .region import pick_region
        region = pick_region(master=self.root, current=self.c.cfg.region())
        if region is not None:
            was = self.c.running()
            self.c.stop()
            self.c.set_region(region)
            if was:
                self.c.start()
            self._toast(f"Region set to {region.label()}.")
        self._refresh()

    def _spawn(self, module):
        """Open a sibling window as its own process (one Tk root per process), tracked
        so Quit can close it too — the user expects Quit to close *everything* Blipfeed,
        not just this window."""
        self._children = [p for p in self._children if p.poll() is None]   # prune dead
        self._children.append(subprocess.Popen([sys.executable, "-m", module]))

    def _open_review(self):
        self._spawn("blipfeed.review")

    def _open_stats(self):
        self._spawn("blipfeed.ui.stats_window")

    def _open_settings(self):
        # in-process (Toplevel), so a Save mutates the config we hold and takes effect
        # live — not a separate process editing a copy we wouldn't see
        from .settings_window import open_toplevel
        open_toplevel(self.root, self.c.cfg, on_saved=lambda _c: self._toast(
            "Settings saved.", "good"))

    def _open_decks(self):
        # in-process so graduating/restoring can refresh the dashboard; the active-deck
        # choice itself is derived from the deck files, not shared cfg state
        from .decks_window import open_toplevel
        open_toplevel(self.root, self.c.cfg, on_change=self._refresh)

    def _sync_known(self):
        r = self.c.sync_known()
        self._toast(f"Known words: {t.commas(r['total_known'])} "
                    f"(from {t.commas(r['deck_total'])} Blip cards).", "good")
        self._refresh()

    # --------------------------------------------------------------- feedback
    def _notify(self, text, kind="info"):
        """Report a hotkey-triggered outcome. Toasts in the window, and *also* through
        the tray when the window is hidden — in AUTO mode it usually is, which is exactly
        how "capture-now silently does nothing" stayed invisible. This is a response to
        something the user pressed, so it isn't the unsolicited interruption AUTO mode
        is meant to avoid (CLAUDE.md Decisions #6)."""
        self._toast(text, kind)
        try:
            if getattr(self, "tray_active", False) and self.root.state() != "normal":
                self.tray.notify(text)
        except Exception:
            log.exception("tray notification")

    def _toast(self, text, kind="info"):
        colour = {"good": t.GOOD, "danger": t.DANGER, "info": t.MUTED,
                  t.ACCENT: t.ACCENT}.get(kind, t.MUTED)
        self._toast_now(text, colour)
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(5000, self._restore_toast)

    def _toast_now(self, text, colour):
        self.toast.config(text=text, fg=colour)

    def _restore_toast(self):
        self._toast_job = None
        self.toast.config(text="")

    # --------------------------------------------------------------- pulse
    def _pulse(self):
        if self.c.running():
            self._pulse_on = not self._pulse_on
            self.dot.config(text="●  Capturing",
                            fg=t.GOOD if self._pulse_on else t.GOOD_DIM)
        else:
            self.dot.config(text="○  Idle", fg=t.MUTED)
        self.root.after(700, self._pulse)

    # --------------------------------------------------------------- refresh
    def _refresh(self):
        s = self.c.stats()
        running = s["running"]

        if s["mode"] == "manual":
            t.style_button(self.primary, "primary")
            self.primary.config(text="◉  Capture now")
        elif running:
            t.style_button(self.primary, "danger")
            self.primary.config(text="■  Stop")
        else:
            t.style_button(self.primary, "primary")
            self.primary.config(text="▶  Start")

        self._paint_seg(self.mode_auto, s["mode"] == "auto")
        self._paint_seg(self.mode_manual, s["mode"] == "manual")
        self.autocard_var.set(s["auto_card"])
        self.region_lbl.config(text=s["region"].label() if s["region"]
                               else "not set — click Change")

        sess = s["session"] or {}
        self.stat["snapshots"].config(text=t.commas(sess.get("ticks", 0)))
        self.stat["words"].config(text=t.commas(sess.get("observations", 0)))
        self.stat["cards"].config(text=t.commas(len(sess.get("promoted", []))))
        self.stat["needs a definition"].config(text=t.commas(s["needs_def"]))
        self.stat["ready"].config(text=t.commas(s["ready"]))
        self.stat["known"].config(text=t.commas(s["known"]))
        self.stat["carded"].config(text=t.commas(s["carded"]))
        n_review = s["needs_def"] + s["ready"]
        self.review_btn.config(text=f"Review  ({t.commas(n_review)})" if n_review
                               else "Review")

        # the guidance line always reflects state; the toast is only for transient
        # messages (sync done, recorded, errors) and clears itself
        self.hint.config(text=self._hint(s),
                         fg=t.ACCENT if s["region"] is None else t.MUTED)

        # keep the tray's label/tooltip in step when capture starts or stops
        if getattr(self, "tray_active", False) and running != self._last_running:
            self._last_running = running
            self.tray.refresh()

        self.root.after(1000 if running else 2500, self._refresh)

    def _hint(self, s):
        if s["region"] is None:
            return "Pick a capture region to begin — click Change."
        if s["mode"] == "manual":
            return "Manual mode: press Capture now (or Ctrl+Alt+N) over the words."
        if not s["running"] and s["seen_terms"] == 0:
            return "Press Start and immerse — words you see will collect here."
        if (s["needs_def"] + s["ready"]) == 0:
            return "Nothing to review yet — keep immersing."
        return f"{s['needs_def']} to define, {s['ready']} ready — open Review."

    def _paint_seg(self, btn, on):
        btn.config(bg=t.ACCENT if on else t.SURFACE_2,
                   fg=t.ACCENT_INK if on else t.INK,
                   activebackground=t.ACCENT_HOVER if on else t.HOVER)

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

    # --------------------------------------------------------------- tray
    def _setup_tray(self):
        from .tray import Tray

        def marshal(fn):
            return lambda: self.root.after(0, fn)

        self.tray = Tray(running=self.c.running,
                         on_toggle=marshal(self._tray_toggle),
                         on_show=marshal(self._show_window),
                         on_review=marshal(self._open_review),
                         on_quit=marshal(self._quit))
        self.tray_active = self.tray.start()

    def _tray_toggle(self):
        # same rule from the tray as from the window: never start without confirming
        # the region (this is the path used while gaming, so it matters most here)
        if self.c.running():
            self.c.stop()
            self._refresh()
        else:
            self._show_window()          # the picker needs a visible owner
            self._start_with_region_check()

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self):
        # close every Blipfeed window we opened, not just this one
        for proc in self._children:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    log.exception("closing child window")
        try:
            self.listener.stop()
        except Exception:
            log.exception("stopping hotkeys")
        if getattr(self, "tray", None):
            self.tray.stop()
        self.c.close()
        self.root.destroy()

    def _on_close(self):
        """The window's X hides to the tray (capture keeps running); Quit is on the
        tray menu. Without a tray, X quits."""
        if getattr(self, "tray_active", False):
            self.root.withdraw()
            if not self._hinted_tray:
                self._hinted_tray = True
                self.tray.notify("Still running here — right-click to stop or quit.")
        else:
            self._quit()


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
