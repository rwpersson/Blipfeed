"""Stats & history — a read-only look at what you've mined.

Totals, a 30-day activity chart (words seen · new words · cards), your top words, and
recent sessions. Opens as its own process from the dashboard (read-only, its own
connection; WAL lets it read while capture writes). All aggregation is in `store`
(tested SQL); this only draws it.
"""
import sys
import tkinter as tk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from . import theme as t  # noqa: E402
from .. import store  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.stats")

_SERIES = [("seen", "#c98a2b", "words seen"),
           ("new", t.ACCENT, "new words"),
           ("cards", t.GOOD, "cards")]


class StatsWindow:
    def __init__(self, root, conn, language="zh-CN"):
        self.root = root
        self.conn = conn
        self.language = language
        root.title("Blipfeed — stats")
        root.configure(bg=t.BG)
        root.geometry("640x760")
        root.minsize(560, 520)
        t.apply_icon(root)

        outer = tk.Frame(root, bg=t.BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=t.BG, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(canvas, bg=t.BG)
        win = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(
                int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        tk.Label(self.body, text="Stats", bg=t.BG, fg=t.INK,
                 font=t.FONT_TITLE).pack(anchor="w", padx=20, pady=(16, 4))
        self._totals()
        self._chart()
        self._top_words()
        self._sessions()

    # ------------------------------------------------------------- sections
    def _totals(self):
        s = store.summary(self.conn, self.language)
        st = s["by_status"]
        card = t.card(self.body, "Library")
        grid = tk.Frame(card, bg=t.SURFACE)
        grid.pack(fill="x", padx=14, pady=12)
        cells = [("words seen", s["terms"]), ("carded", st.get("carded", 0)),
                 ("known", st.get("known", 0)), ("ignored", st.get("ignored", 0)),
                 ("sightings", s["observations"]), ("sessions", s["sessions"])]
        for i, (label, value) in enumerate(cells):
            cell = tk.Frame(grid, bg=t.SURFACE)
            cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 26), pady=6)
            tk.Label(cell, text=t.commas(value), bg=t.SURFACE, fg=t.INK,
                     font=t.FONT_NUM).pack(anchor="w")
            tk.Label(cell, text=label, bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT_SMALL).pack(anchor="w")

    def _chart(self):
        days = store.daily_activity(self.conn, days=30)
        card = t.card(self.body, "Last 30 days")
        # legend
        legend = tk.Frame(card, bg=t.SURFACE)
        legend.pack(anchor="w", padx=14, pady=(2, 0))
        for _key, colour, label in _SERIES:
            tk.Label(legend, text="■", fg=colour, bg=t.SURFACE,
                     font=t.FONT_SMALL).pack(side="left", padx=(0, 2))
            tk.Label(legend, text=label, fg=t.MUTED, bg=t.SURFACE,
                     font=t.FONT_SMALL).pack(side="left", padx=(0, 12))

        w, h, pad = 580, 160, 22
        cv = tk.Canvas(card, width=w, height=h, bg=t.SURFACE, highlightthickness=0)
        cv.pack(padx=14, pady=(6, 12))
        peak = max((max(d["seen"], d["new"], d["cards"]) for d in days), default=0)
        if peak == 0:
            cv.create_text(w // 2, h // 2, text="No activity yet — start a session.",
                           fill=t.MUTED, font=t.FONT)
            return
        base = h - pad
        cv.create_line(pad, base, w - 6, base, fill=t.BORDER)
        cv.create_text(pad - 4, pad - 8, text=t.commas(peak), fill=t.MUTED,
                       font=("Segoe UI", 8), anchor="w")
        slot = (w - pad - 8) / len(days)
        bw = max(slot / (len(_SERIES) + 1), 2)
        for i, d in enumerate(days):
            x0 = pad + i * slot
            for j, (key, colour, _l) in enumerate(_SERIES):
                val = d[key]
                if not val:
                    continue
                bh = (val / peak) * (base - pad)
                bx = x0 + j * bw
                cv.create_rectangle(bx, base - bh, bx + bw - 1, base, fill=colour,
                                    width=0)
            if i % 5 == 0:
                cv.create_text(x0, base + 8, text=d["date"][5:], fill=t.MUTED,
                               font=("Segoe UI", 7), anchor="n")

    def _top_words(self):
        card = t.card(self.body, "Most-seen words")
        rows = store.top_terms(self.conn, limit=15, language=self.language)
        if not rows:
            tk.Label(card, text="Nothing yet.", bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT).pack(anchor="w", padx=14, pady=(0, 10))
            return
        grid = tk.Frame(card, bg=t.SURFACE)
        grid.pack(fill="x", padx=14, pady=(2, 12))
        for i, r in enumerate(rows):
            tk.Label(grid, text=f"{r['count']}×", bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT_SMALL, width=5, anchor="e").grid(
                row=i, column=0, sticky="e", padx=(0, 8))
            tk.Label(grid, text=r["term"], bg=t.SURFACE, fg=t.INK,
                     font=("Microsoft YaHei", 11)).grid(row=i, column=1, sticky="w")
            gloss = (r["meaning"] or f"· {r['status']}")[:46]
            tk.Label(grid, text=gloss, bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT_SMALL, anchor="w").grid(row=i, column=2, sticky="w",
                                                         padx=(12, 0))

    def _sessions(self):
        card = t.card(self.body, "Recent sessions")
        rows = store.sessions_history(self.conn, limit=15)
        if not rows:
            tk.Label(card, text="No sessions yet.", bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT).pack(anchor="w", padx=14, pady=(0, 10))
            return
        grid = tk.Frame(card, bg=t.SURFACE)
        grid.pack(fill="x", padx=14, pady=(2, 12))
        for i, r in enumerate(rows):
            when = (r["started"] or "")[:16].replace("T", "  ")
            tk.Label(grid, text=when, bg=t.SURFACE, fg=t.MUTED,
                     font=t.FONT_SMALL).grid(row=i, column=0, sticky="w", padx=(0, 12))
            tk.Label(grid, text=(r["source_label"] or "session")[:26], bg=t.SURFACE,
                     fg=t.INK, font=t.FONT).grid(row=i, column=1, sticky="w")
            tk.Label(grid, text=f"{t.commas(r['snaps'])} snaps · "
                                f"{t.commas(r['words'])} words",
                     bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL).grid(
                row=i, column=2, sticky="e", padx=(12, 0))
            grid.columnconfigure(2, weight=1)


def run(language="zh-CN"):
    root = tk.Tk()
    StatsWindow(root, store.connect(), language)
    root.mainloop()


if __name__ == "__main__":
    from ..capture import set_dpi_aware

    set_dpi_aware()
    run()
