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

from . import theme as _t  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.review")

_CJK_FONT = ("Microsoft YaHei", 11)
_SMALL = ("Segoe UI", 9)

# From the shared theme (dark). ttk widgets are styled globally in run().
_TEXT = _t.INK           # the meaning — primary readable text
_MUTED = _t.MUTED        # reading + count
_WARN = _t.WARNING       # why a candidate was filtered
_CTX = _t.FAINT          # mined context sentence
_WRAP = 440              # keeps text clear of the reserved button column
_PAGE = 25               # rows drawn at once (~8 ms/row of ttk widget-building is the
                         # whole cost). You act on the top of a frequency-ranked list,
                         # and the list refreshes as you do; "…and N more" notes the tail.


class _Row(ttk.Frame):
    """One candidate: term + reading + count, meaning/context, and its actions.

    Laid out with pack, not grid: the button bar is reserved on the right *first*, so
    it always gets its space and can never clip a button (an earlier grid layout let
    the expanding middle column squeeze the right-aligned bar, cutting off "Define").
    """

    def __init__(self, master, cand, actions, contexts=(), show_meaning=True):
        super().__init__(master, padding=(10, 8))
        self._contexts = contexts

        bar = ttk.Frame(self)
        bar.pack(side="right", anchor="n")              # reserved before content
        for label, fn in actions:
            ttk.Button(bar, text=label, width=8, command=fn).pack(side="left", padx=2)

        content = ttk.Frame(self)
        content.pack(side="left", fill="x", expand=True)

        head_row = ttk.Frame(content)
        head_row.pack(fill="x", anchor="w")
        head = cand.term
        if cand.alt and cand.alt != cand.term:
            head += f"  ·  {cand.alt}"
        ttk.Label(head_row, text=head, font=_CJK_FONT).pack(side="left")
        sub = (cand.reading + "    ") if cand.reading else ""
        sub += f"seen {cand.count}×"
        ttk.Label(head_row, text=sub, foreground=_MUTED, font=_SMALL).pack(
            side="left", padx=(10, 0))

        if show_meaning and cand.meaning:
            ttk.Label(content, text=cand.meaning, wraplength=_WRAP, justify="left",
                      foreground=_TEXT).pack(anchor="w", pady=(2, 0))
        if cand.reasons:
            ttk.Label(content, text="· " + "; ".join(cand.reasons), font=_SMALL,
                      foreground=_WARN, wraplength=_WRAP, justify="left").pack(
                anchor="w")
        for ctx in self._contexts[:2]:
            ttk.Label(content, text=f"“{ctx}”", font=_SMALL, foreground=_CTX,
                      wraplength=_WRAP, justify="left").pack(anchor="w")


class _Bucket(ttk.Frame):
    """A scrollable list with a collapsed secondary section."""

    def __init__(self, master, window, kind):
        super().__init__(master)
        self.window = window
        self.kind = kind
        self._show_secondary = tk.BooleanVar(value=False)
        self._pending = None            # (cands, actions_for) awaiting a render
        self._dirty = True              # needs (re)drawing before it's shown
        self.stale = False              # data changed; refetch before showing
        self._limits = {"primary": _PAGE, "secondary": _PAGE}
        self._rows = {}                 # term_id -> [row_widget, separator]

        self.canvas = tk.Canvas(self, highlightthickness=0, bg=_t.BG)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # NB: the wheel is bound once at the window level (ReviewWindow), not per
        # canvas — a per-canvas <Enter>/<Leave> binding broke, because moving onto a
        # _Row (which sits on the canvas) fires the canvas <Leave> and unbinds it, so
        # the wheel only worked over empty gaps.

    def scroll(self, delta):
        self.canvas.yview_scroll(int(-delta / 120), "units")

    def remove(self, term_id):
        """Surgically drop one term's row (after an action) instead of rebuilding the
        whole tab — the rebuild is ~28 ms/row, so removing one widget is instant."""
        entry = self._rows.pop(term_id, None)
        if entry:
            for widget in entry:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
        if self._pending:                       # keep the backing list in sync
            cands, actions = self._pending
            self._pending = ([c for c in cands if c.id != term_id], actions)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def set_data(self, cands, actions_for):
        """Stash the rows to draw and mark dirty — but don't build widgets yet.

        Rendering is deferred to when the tab is actually shown (`ensure_rendered`),
        so acting on one tab doesn't rebuild the two you can't see. That's what made
        every click cost ~4s: all three tabs, all ~350 rows, rebuilt each time.
        """
        self._pending = (cands, actions_for)
        self._dirty = True
        self._limits = {"primary": _PAGE, "secondary": _PAGE}   # reset on new data

    def ensure_rendered(self):
        if self._dirty and self._pending is not None:
            self._render(*self._pending)
            self._dirty = False

    def _draw_rows(self, cands, actions_for, which):
        """Draw up to this list's current limit, then a 'Show more' button the user
        controls — revealing a page at a time keeps a 200-row bucket responsive while
        still letting them reach every last word."""
        limit = self._limits[which]
        for cand in cands[:limit]:
            contexts = self.window.c.contexts_for(cand.id)     # lazy, per drawn row
            row = _Row(self.body, cand, actions_for(cand), contexts=contexts)
            row.pack(fill="x")
            sep = ttk.Separator(self.body, orient="horizontal")
            sep.pack(fill="x")
            self._rows[cand.id] = [row, sep]
        hidden = len(cands) - limit
        if hidden > 0:
            more = min(_PAGE, hidden)
            bar = ttk.Frame(self.body)
            bar.pack(fill="x", pady=(6, 4))
            ttk.Button(bar, text=f"Show {more} more",
                       command=lambda: self._show_more(which)).pack(side="left",
                                                                    padx=10)
            if hidden > more:
                ttk.Button(bar, text=f"Show all {len(cands)}",
                           command=lambda: self._show_all(which, len(cands))).pack(
                    side="left")
            ttk.Label(bar, text=f"{hidden} more", foreground=_MUTED).pack(
                side="left", padx=10)

    def _show_more(self, which):
        self._limits[which] += _PAGE
        self._render(*self._pending)

    def _show_all(self, which, total):
        self._limits[which] = total
        self._render(*self._pending)

    def _render(self, cands, actions_for):
        for w in self.body.winfo_children():
            w.destroy()
        self._rows = {}
        primary = [c for c in cands if c.tier == "primary"]
        secondary = [c for c in cands if c.tier == "secondary"]

        if not primary:
            ttk.Label(self.body, text="Nothing here right now.", foreground=_MUTED,
                      padding=16).pack(anchor="w")
        self._draw_rows(primary, actions_for, "primary")

        if secondary:
            ttk.Checkbutton(
                self.body,
                text=f"  {len(secondary)} filtered out (ambient, single-character…) "
                     f"— show and act on them",
                variable=self._show_secondary,
                command=lambda: self._render(cands, actions_for)).pack(
                anchor="w", pady=(8, 4))
            if self._show_secondary.get():
                self._draw_rows(secondary, actions_for, "secondary")


class ReviewWindow:
    def __init__(self, root, controller):
        self.root = root
        self.c = controller
        root.title("Blipfeed — review")
        root.geometry("820x640")

        self.status = ttk.Label(root, text="", padding=(10, 6), foreground=_t.MUTED)
        self.status.pack(side="bottom", fill="x")

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True)
        self.needs = _Bucket(self.nb, self, "needs_def")
        self.ready = _Bucket(self.nb, self, "ready")
        self.carded = _Bucket(self.nb, self, "carded")
        self.nb.add(self.needs, text="Needs a definition")
        self.nb.add(self.ready, text="Ready to card")
        self.nb.add(self.carded, text="Carded")
        # Draw a tab only when it's shown, so acting on one tab never rebuilds the
        # others (the whole reason clicks were slow).
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._show_current())
        # One wheel binding for the whole window -> the visible tab (the per-canvas
        # binding was unreliable; see _Bucket).
        root.bind_all("<MouseWheel>", lambda e: self._current_bucket().scroll(e.delta))
        # how each bucket's data is fetched, for the lazy stale-refresh after an action
        self._fetch = {self.needs: (self.c.needs_definition, self._needs_actions),
                       self.ready: (self.c.ready, self._ready_actions),
                       self.carded: (self.c.carded, self._carded_actions)}

        bar = ttk.Frame(self.needs)
        bar.pack(side="bottom", fill="x", pady=4)
        ttk.Button(bar, text="Copy for AI", command=self._copy_for_ai).pack(
            side="left", padx=4)
        ttk.Button(bar, text="Paste enriched…", command=self._paste_enriched).pack(
            side="left", padx=4)

        self.refresh()

    # ------------------------------------------------------------- rendering
    def _current_bucket(self):
        selected = self.nb.select()          # widget path of the visible tab
        for bucket in (self.needs, self.ready, self.carded):
            if str(bucket) == selected:
                return bucket
        return self.needs

    def _show_current(self):
        bucket = self._current_bucket()
        if bucket.stale:                         # data changed since last shown
            fetch, actions = self._fetch[bucket]
            bucket.set_data(fetch(), actions)
            bucket.stale = False
        bucket.ensure_rendered()

    def refresh(self):
        """Full refresh — recompute every tab's data, draw only the visible one. Used
        on open and after a bulk change (paste-enriched); single actions use `_do`."""
        needs = self.c.needs_definition()
        ready = self.c.ready()
        self.needs.set_data(needs, self._needs_actions)
        self.ready.set_data(ready, self._ready_actions)
        self.carded.set_data(self.c.carded(), self._carded_actions)
        self._status(needs, ready)
        self._show_current()

    def _status(self, needs=None, ready=None):
        counts = self.c.counts(needs=needs, ready=ready)
        nd, rd = counts["needs_def"], counts["ready"]
        self.status.config(
            text=f"Needs a definition: {nd[0]}   ·   Ready: {rd[0]}   ·   "
                 f"Carded: {counts['carded']}   ·   Known: {counts['known']}")

    def _do(self, fn, term_id):
        """A single-term action: apply it, then **surgically remove that one row** from
        the visible tab and mark the others stale (they refetch when next shown). This
        keeps a click near-instant instead of rebuilding ~25 rows (~700 ms)."""
        try:
            fn(term_id)
        except Exception as exc:
            log.exception("review action failed")
            messagebox.showerror("Blipfeed", str(exc))
            return
        current = self._current_bucket()
        current.remove(term_id)
        for bucket in (self.needs, self.ready, self.carded):
            if bucket is not current:
                bucket.stale = True
        self._status()

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
    _t.style_ttk(root)                    # dark amber ttk styling
    _t.apply_icon(root)
    ReviewWindow(root, controller)
    root.mainloop()


if __name__ == "__main__":
    from .. import review

    sys.exit(review.main())
