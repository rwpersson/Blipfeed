"""The mining-queue review window — a dumb shell over ReviewController.

All behaviour lives in review.py (tested, no Tk); this only draws it and forwards
clicks. An ordinary focus-taking window — the user opened it deliberately (CLAUDE.md
Decisions #6), so no Win32 gymnastics.

Layout: three tabs — **Needs a definition** (loudest, opens first), **Ready to card**,
**Carded** (for pulling leaked UI back out). Each row shows the term, reading, count,
meaning, and mined context; the secondary (ambient / 1-char / …) tier is collapsed
behind a toggle, never hidden. The enrichment loop is two buttons on the needs-def
tab: **Copy for AI** and **Paste enriched**.
"""
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from ..logging_setup import get  # noqa: E402

log = get("ui.review")

_CJK_FONT = ("Microsoft YaHei", 11)
_SMALL = ("Segoe UI", 9)


class _Row(ttk.Frame):
    """One candidate: term + reading + count, meaning/context, and its actions."""

    def __init__(self, master, cand, actions, show_meaning=True):
        super().__init__(master, padding=(8, 6))
        self.columnconfigure(1, weight=1)

        head = f"{cand.term}"
        if cand.alt and cand.alt != cand.term:
            head += f" · {cand.alt}"
        ttk.Label(self, text=head, font=_CJK_FONT).grid(row=0, column=0, sticky="w")
        sub = cand.reading or ""
        sub += f"    seen {cand.count}×"
        ttk.Label(self, text=sub, foreground="#7a8291", font=_SMALL).grid(
            row=0, column=1, sticky="w", padx=(10, 0))

        rownum = 1
        if show_meaning and cand.meaning:
            ttk.Label(self, text=cand.meaning, wraplength=520,
                      foreground="#d0d0d0").grid(row=rownum, column=0, columnspan=2,
                                                 sticky="w", pady=(2, 0))
            rownum += 1
        if cand.reasons:
            ttk.Label(self, text="· " + "; ".join(cand.reasons), font=_SMALL,
                      foreground="#8a6d3b").grid(row=rownum, column=0, columnspan=2,
                                                 sticky="w")
            rownum += 1
        for ctx in cand.contexts[:2]:
            ttk.Label(self, text=f"“{ctx}”", font=_SMALL, foreground="#6f7784",
                      wraplength=520).grid(row=rownum, column=0, columnspan=2,
                                           sticky="w")
            rownum += 1

        bar = ttk.Frame(self)
        bar.grid(row=0, column=2, rowspan=max(rownum, 1), sticky="e")
        for label, fn in actions:
            ttk.Button(bar, text=label, width=8,
                       command=fn).pack(side="left", padx=2)


class _Bucket(ttk.Frame):
    """A scrollable list with a collapsed secondary section."""

    def __init__(self, master, window, kind):
        super().__init__(master)
        self.window = window
        self.kind = kind
        self._show_secondary = tk.BooleanVar(value=False)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.body, anchor="nw", width=760)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # wheel sc. bind only while the pointer is over this canvas
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all(
            "<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def render(self, cands, actions_for):
        for w in self.body.winfo_children():
            w.destroy()
        primary = [c for c in cands if c.tier == "primary"]
        secondary = [c for c in cands if c.tier == "secondary"]

        if not primary:
            ttk.Label(self.body, text="Nothing here right now.", foreground="#7a8291",
                      padding=16).pack(anchor="w")
        for cand in primary:
            _Row(self.body, cand, actions_for(cand)).pack(fill="x")
            ttk.Separator(self.body, orient="horizontal").pack(fill="x")

        if secondary:
            toggle = ttk.Checkbutton(
                self.body,
                text=f"  {len(secondary)} filtered out (ambient, single-character…) "
                     f"— show and act on them",
                variable=self._show_secondary,
                command=lambda: self.render(cands, actions_for))
            toggle.pack(anchor="w", pady=(8, 4))
            if self._show_secondary.get():
                for cand in secondary:
                    _Row(self.body, cand, actions_for(cand)).pack(fill="x")
                    ttk.Separator(self.body, orient="horizontal").pack(fill="x")


class ReviewWindow:
    def __init__(self, root, controller):
        self.root = root
        self.c = controller
        root.title("Blipfeed — review")
        root.geometry("820x640")

        self.status = ttk.Label(root, text="", padding=(10, 6), foreground="#7a8291")
        self.status.pack(side="bottom", fill="x")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)
        self.needs = _Bucket(nb, self, "needs_def")
        self.ready = _Bucket(nb, self, "ready")
        self.carded = _Bucket(nb, self, "carded")
        nb.add(self.needs, text="Needs a definition")
        nb.add(self.ready, text="Ready to card")
        nb.add(self.carded, text="Carded")

        bar = ttk.Frame(self.needs)
        bar.pack(side="bottom", fill="x", pady=4)
        ttk.Button(bar, text="Copy for AI", command=self._copy_for_ai).pack(
            side="left", padx=4)
        ttk.Button(bar, text="Paste enriched…", command=self._paste_enriched).pack(
            side="left", padx=4)

        self.refresh()

    # ------------------------------------------------------------- rendering
    def refresh(self):
        self.needs.render(self.c.needs_definition(), self._needs_actions)
        self.ready.render(self.c.ready(), self._ready_actions)
        self.carded.render(self.c.carded(), self._carded_actions)
        counts = self.c.counts()
        nd, rd = counts["needs_def"], counts["ready"]
        self.status.config(
            text=f"Needs a definition: {nd[0]}   ·   Ready: {rd[0]}   ·   "
                 f"Carded: {counts['carded']}   ·   Known: {counts['known']}")

    def _do(self, fn, term_id):
        try:
            fn(term_id)
        except Exception as exc:
            log.exception("review action failed")
            messagebox.showerror("Blipfeed", str(exc))
        self.refresh()

    def _needs_actions(self, cand):
        return [("Define", lambda c=cand: self._define(c)),
                ("Known", lambda c=cand: self._do(self.c.mark_known, c.id)),
                ("Ignore", lambda c=cand: self._do(self.c.ignore, c.id))]

    def _ready_actions(self, cand):
        return [("Card it", lambda c=cand: self._do(self.c.accept, c.id)),
                ("Known", lambda c=cand: self._do(self.c.mark_known, c.id)),
                ("Ignore", lambda c=cand: self._do(self.c.ignore, c.id))]

    def _carded_actions(self, cand):
        return [("Un-card", lambda c=cand: self._do(self.c.uncard, c.id))]

    # ------------------------------------------------------------- dialogs
    def _define(self, cand):
        _DefineDialog(self.root, cand, self._save_definition)

    def _save_definition(self, term_id, meaning, note):
        if not meaning.strip():
            return
        self._do(lambda tid: self.c.set_meaning(tid, meaning, note), term_id)

    def _copy_for_ai(self):
        text = self.c.export_for_ai()
        if not text:
            messagebox.showinfo("Blipfeed", "Nothing needs a definition right now.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        n = text.count("— seen ")
        messagebox.showinfo(
            "Blipfeed",
            f"Copied {n} term(s) to the clipboard.\n\nPaste them into any AI chat, "
            "then bring the reply back with “Paste enriched…”.")

    def _paste_enriched(self):
        _PasteDialog(self.root, self._apply_enriched)

    def _apply_enriched(self, text):
        updated, unmatched = self.c.import_enriched(text)
        self.refresh()
        msg = f"Filled in {updated} definition(s)."
        if unmatched:
            msg += (f"\n\n{len(unmatched)} term(s) didn't match anything in the "
                    f"queue:\n{', '.join(unmatched[:12])}")
        messagebox.showinfo("Blipfeed", msg)


class _DefineDialog(tk.Toplevel):
    def __init__(self, master, cand, on_save):
        super().__init__(master)
        self.title(f"Define {cand.term}")
        self.on_save = on_save
        self.term_id = cand.id
        self.transient(master)
        self.resizable(False, False)

        head = cand.term + (f"  ({cand.reading})" if cand.reading else "")
        ttk.Label(self, text=head, font=_CJK_FONT, padding=(12, 10)).grid(
            row=0, column=0, columnspan=2, sticky="w")
        for ctx in cand.contexts[:3]:
            ttk.Label(self, text=f"“{ctx}”", foreground="#6f7784", font=_SMALL,
                      wraplength=380, padding=(12, 0)).grid(
                columnspan=2, sticky="w")

        ttk.Label(self, text="Meaning", padding=(12, 8, 4, 0)).grid(sticky="w")
        self.meaning = ttk.Entry(self, width=48)
        self.meaning.grid(row=98, column=0, columnspan=2, padx=12, sticky="ew")
        ttk.Label(self, text="Note (optional)", padding=(12, 8, 4, 0)).grid(sticky="w")
        self.note = ttk.Entry(self, width=48)
        self.note.grid(row=99, column=0, columnspan=2, padx=12, sticky="ew")

        bar = ttk.Frame(self, padding=12)
        bar.grid(columnspan=2, sticky="e")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(bar, text="Save", command=self._save).pack(side="left")
        self.meaning.focus_set()
        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def _save(self):
        self.on_save(self.term_id, self.meaning.get(), self.note.get())
        self.destroy()


class _PasteDialog(tk.Toplevel):
    def __init__(self, master, on_apply):
        super().__init__(master)
        self.title("Paste enriched")
        self.on_apply = on_apply
        self.transient(master)
        ttk.Label(self, text="Paste the AI's reply (term ⇥ meaning ⇥ note per line):",
                  padding=(10, 8)).pack(anchor="w")
        self.text = scrolledtext.ScrolledText(self, width=70, height=18,
                                              font=("Consolas", 10))
        self.text.pack(fill="both", expand=True, padx=10)
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Apply", command=self._apply).pack(side="right", padx=6)
        self.text.focus_set()
        self.grab_set()

    def _apply(self):
        text = self.text.get("1.0", "end")
        self.destroy()
        self.on_apply(text)


def run(controller):
    """Blocking: open the review window on its own Tk root."""
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    ReviewWindow(root, controller)
    root.mainloop()


if __name__ == "__main__":
    from .. import review

    sys.exit(review.main())
