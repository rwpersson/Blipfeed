"""The settings window — a themed form generated from `settings.SPEC`.

Opens as a Toplevel of the dashboard (one root per process), so a Save mutates the
config the dashboard already holds and takes effect live. `run()` also makes it
standalone (`python -m blipfeed.ui.settings_window`).
"""
import sys
import tkinter as tk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from . import theme as t  # noqa: E402
from .. import settings as settings_mod  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.settings")


class SettingsForm:
    """Builds the form into `container`; calls `on_saved(cfg)` after a successful save."""

    def __init__(self, container, cfg, on_saved=None, on_close=None):
        self.container = container
        self.cfg = cfg
        self.on_saved = on_saved
        self.on_close = on_close
        self.vars = {}
        container.configure(bg=t.BG)

        # action bar reserved at the bottom first (Blip gotcha #16)
        bar = tk.Frame(container, bg=t.SURFACE)
        bar.pack(fill="x", side="bottom")
        self.msg = tk.Label(bar, text="", bg=t.SURFACE, fg=t.DANGER, anchor="w",
                            font=t.FONT_SMALL, justify="left", wraplength=360)
        self.msg.pack(side="left", fill="x", expand=True, padx=14, pady=10)
        t.button(bar, "Save", self._save, kind="primary").pack(
            side="right", padx=(4, 14), pady=8)
        t.button(bar, "Cancel", self._cancel, kind="ghost").pack(
            side="right", padx=4, pady=8)

        # scrollable body
        outer = tk.Frame(container, bg=t.BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=t.BG, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=t.BG)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(
                int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        tk.Label(body, text="Settings", bg=t.BG, fg=t.INK, font=t.FONT_TITLE).pack(
            anchor="w", padx=20, pady=(16, 4))
        for title, fields in settings_mod.SPEC:
            self._group(body, title, fields)
        tk.Label(body, text="Changes apply to the next capture you start.",
                 bg=t.BG, fg=t.FAINT, font=t.FONT_SMALL).pack(pady=(4, 14))

    # --------------------------------------------------------------- building
    def _group(self, body, title, fields):
        card = t.card(body, title)
        for field in fields:
            self._field(card, field)
        tk.Frame(card, bg=t.SURFACE, height=6).pack()

    def _field(self, card, field):
        row = tk.Frame(card, bg=t.SURFACE)
        row.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(row, text=field["label"], bg=t.SURFACE, fg=t.INK,
                 font=t.FONT, anchor="w").pack(side="left", fill="x", expand=True)
        widget = self._control(row, field)
        widget.pack(side="right")
        if field.get("suffix"):
            tk.Label(row, text=field["suffix"], bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT_SMALL).pack(side="right", padx=(0, 6))
        if field.get("help"):
            tk.Label(card, text=field["help"], bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT_SMALL, anchor="w", justify="left",
                     wraplength=380).pack(fill="x", padx=14, pady=(0, 2))

    def _control(self, row, field):
        key, kind = field["key"], field["kind"]
        cur = self.cfg.get(key)
        if kind == "bool":
            var = tk.BooleanVar(value=bool(cur))
            self.vars[key] = var
            return tk.Checkbutton(row, variable=var, bg=t.SURFACE,
                                  activebackground=t.SURFACE, selectcolor=t.SURFACE_2,
                                  highlightthickness=0, bd=0, cursor="hand2")
        if kind == "choice":
            var = tk.StringVar(value=str(cur))
            self.vars[key] = var
            om = tk.OptionMenu(row, var, *field["values"])
            om.config(bg=t.SURFACE_2, fg=t.INK, activebackground=t.HOVER,
                      activeforeground=t.INK, relief="flat", bd=0, highlightthickness=0,
                      font=t.FONT, cursor="hand2", width=10)
            om["menu"].config(bg=t.SURFACE_2, fg=t.INK, activebackground=t.ACCENT,
                              activeforeground=t.ACCENT_INK, bd=0)
            return om
        var = tk.StringVar(value=str(cur))
        self.vars[key] = var
        return tk.Entry(row, textvariable=var, bg=t.SURFACE_2, fg=t.INK,
                        insertbackground=t.INK, relief="flat", width=14,
                        font=t.FONT, justify="right")

    # --------------------------------------------------------------- actions
    def _save(self):
        values = {k: v.get() for k, v in self.vars.items()}
        _cfg, problems = settings_mod.apply(self.cfg, values)
        if problems:
            self.msg.config(text="  •  ".join(problems[:3]), fg=t.DANGER)
            return
        if self.on_saved:
            self.on_saved(self.cfg)
        self._cancel()

    def _cancel(self):
        if self.on_close:
            self.on_close()


def open_toplevel(master, cfg, on_saved=None):
    """Open settings as a modal child of an existing root (the dashboard)."""
    top = tk.Toplevel(master)
    top.title("Blipfeed — settings")
    top.geometry("460x640")
    t.apply_icon(top)
    SettingsForm(top, cfg, on_saved=on_saved, on_close=top.destroy)
    top.transient(master)
    top.grab_set()
    return top


def run(cfg=None):
    from .. import config as config_mod

    root = tk.Tk()
    root.title("Blipfeed — settings")
    root.geometry("460x640")
    t.apply_icon(root)
    SettingsForm(root, cfg or config_mod.load(), on_close=root.destroy)
    root.mainloop()


if __name__ == "__main__":
    from ..capture import set_dpi_aware

    set_dpi_aware()
    run()
