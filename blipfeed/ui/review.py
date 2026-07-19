"""The mining queue — review what was found, decide what becomes a card.

    python -m blipfeed.review

An ordinary focus-taking window: you opened it deliberately (CLAUDE.md Decisions #6).
All the logic lives in `frequency` / `enrich` / `store`, so this file stays dumb and
those stay testable — Blip's editor split, kept.

## What it's for

**The *Needs a definition* bucket is the point, not the leftovers.** On real content
the words worth learning skew heavily to what CC-CEDICT lacks — slang, xianxia terms,
show titles. Those arrive with a reading and real mined sentences and need only a
meaning, which is what the Copy-for-AI → Paste-enriched loop supplies. So that bucket
opens first and is never buried under the ordinary dictionary hits.

## Layout

A list on top, a detail pane below. Selecting a row shows its mined sentences and an
editable meaning/note; the actions apply to the selection. A Treeview (rather than a
widget per row) keeps it responsive at hundreds of terms and gives keyboard
navigation for free — you review a queue by holding a key, not by aiming a mouse.

`secondary` candidates (ambient, single-character, too-few-snapshots) are **collapsed,
not hidden**: one toggle reveals them, each labelled with why it was filtered, and
they remain fully actionable. Those are the app's judgements and the user is entitled
to overrule them.
"""
import sys
import tkinter as tk
from tkinter import messagebox, ttk

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import blipfeed.ui  # noqa: F401

    __package__ = "blipfeed.ui"

from .. import config as config_mod, enrich, frequency, store  # noqa: E402
from ..logging_setup import get  # noqa: E402

log = get("ui.review")

BUCKETS = (("needs_def", "Needs a definition"), ("ready", "Ready to card"))


class ReviewWindow:
    def __init__(self, root, conn, cfg):
        self.root = root
        self.conn = conn
        self.cfg = cfg
        self.which = "needs_def"          # the high-value bucket opens first
        self.show_secondary = tk.BooleanVar(value=False)
        self.candidates = []
        self._by_iid = {}

        root.title("Blipfeed — review")
        root.geometry("980x680")
        self._build_header()
        self._build_list()
        self._build_detail()
        self._build_footer()
        self._bind_keys()
        self.refresh()

    # ------------------------------------------------------------- widgets
    def _build_header(self):
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        self.bucket_var = tk.StringVar(value=self.which)
        self.bucket_buttons = {}
        for key, label in BUCKETS:
            btn = ttk.Radiobutton(bar, text=label, value=key,
                                  variable=self.bucket_var, command=self.on_bucket,
                                  style="Toolbutton")
            btn.pack(side="left", padx=(0, 6))
            self.bucket_buttons[key] = btn
        ttk.Checkbutton(bar, text="show filtered", variable=self.show_secondary,
                        command=self.refresh).pack(side="right")
        self.summary = ttk.Label(bar, text="")
        self.summary.pack(side="right", padx=12)

    def _build_list(self):
        wrap = ttk.Frame(self.root, padding=(10, 0))
        wrap.pack(fill="both", expand=True)
        cols = ("term", "reading", "seen", "meaning", "why")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=14)
        for col, title, width in (("term", "Term", 110), ("reading", "Reading", 150),
                                  ("seen", "Seen", 55), ("meaning", "Meaning", 340),
                                  ("why", "Filtered because", 250)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width,
                             anchor="center" if col == "seen" else "w")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("secondary", foreground="#8a8a8a")
        self.tree.tag_configure("undefined", foreground="#0a58ca")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())

    def _build_detail(self):
        box = ttk.LabelFrame(self.root, text="Selected", padding=10)
        box.pack(fill="x", padx=10, pady=8)

        self.contexts = tk.Text(box, height=3, wrap="word", relief="flat",
                                background="#f4f4f4", font=("Microsoft YaHei", 10))
        self.contexts.pack(fill="x")
        self.contexts.configure(state="disabled")

        form = ttk.Frame(box)
        form.pack(fill="x", pady=(8, 0))
        ttk.Label(form, text="Meaning").grid(row=0, column=0, sticky="w")
        self.meaning_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.meaning_var).grid(
            row=0, column=1, sticky="ew", padx=6)
        ttk.Label(form, text="Note").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.note_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.note_var).grid(
            row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        form.columnconfigure(1, weight=1)

        actions = ttk.Frame(box)
        actions.pack(fill="x", pady=(10, 0))
        for text, cmd in (("Save meaning  (s)", self.on_save),
                          ("Make card  (c)", self.on_card),
                          ("I know it  (k)", self.on_know),
                          ("Ignore  (i)", self.on_ignore)):
            ttk.Button(actions, text=text, command=cmd).pack(side="left", padx=(0, 6))

    def _build_footer(self):
        bar = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="Copy for AI", command=self.on_copy).pack(side="left")
        ttk.Button(bar, text="Paste enriched",
                   command=self.on_paste).pack(side="left", padx=6)
        self.status = ttk.Label(bar, text="")
        self.status.pack(side="left", padx=12)
        ttk.Button(bar, text="Close", command=self.root.destroy).pack(side="right")

    def _bind_keys(self):
        # You review a queue by holding a key, not by aiming a mouse.
        for key, cmd in (("c", self.on_card), ("k", self.on_know),
                         ("i", self.on_ignore), ("s", self.on_save)):
            self.root.bind(f"<KeyPress-{key}>", lambda _e, c=cmd: self._unless_typing(c))
        self.root.bind("<Escape>", lambda _e: self.root.destroy())

    def _unless_typing(self, cmd):
        """Single-key actions must not fire while the user is editing a field."""
        if isinstance(self.root.focus_get(), (ttk.Entry, tk.Entry, tk.Text)):
            return
        cmd()

    # -------------------------------------------------------------- data
    def refresh(self, keep=None):
        queue = frequency.review_queue(self.conn, self.cfg, self.which)
        primary = [c for c in queue if c.tier == "primary"]
        secondary = [c for c in queue if c.tier == "secondary"]
        self.candidates = primary + (secondary if self.show_secondary.get() else [])

        self.tree.delete(*self.tree.get_children())
        self._by_iid.clear()
        for cand in self.candidates:
            meaning = cand.meaning or "— needs a definition —"
            iid = self.tree.insert(
                "", "end",
                values=(cand.term, cand.reading or "", cand.count, meaning,
                        "; ".join(cand.reasons)),
                tags=("secondary",) if cand.tier == "secondary"
                else (("undefined",) if not cand.meaning else ()))
            self._by_iid[iid] = cand

        for key, label in BUCKETS:
            n = len([c for c in frequency.review_queue(self.conn, self.cfg, key)
                     if c.tier == "primary"])
            self.bucket_buttons[key].configure(text=f"{label}  ({n})")
        hidden = 0 if self.show_secondary.get() else len(secondary)
        self.summary.configure(
            text=f"{len(primary)} worth a look"
                 + (f"  ·  {hidden} filtered" if hidden else ""))

        children = self.tree.get_children()
        if children:
            target = keep if keep in children else children[0]
            self.tree.selection_set(target)
            self.tree.focus(target)
        self.on_select()

    def selected(self):
        sel = self.tree.selection()
        return self._by_iid.get(sel[0]) if sel else None

    def on_bucket(self):
        self.which = self.bucket_var.get()
        self.refresh()

    def on_select(self):
        cand = self.selected()
        self.contexts.configure(state="normal")
        self.contexts.delete("1.0", "end")
        if cand is not None:
            sentences = store.contexts_for(self.conn, cand.row["id"], limit=3)
            self.contexts.insert(
                "1.0", "\n".join(f"· {s}" for s in sentences)
                or "(no context sentences recorded)")
            self.meaning_var.set(cand.meaning or "")
            self.note_var.set(cand.row["note"] or "")
        else:
            self.meaning_var.set("")
            self.note_var.set("")
        self.contexts.configure(state="disabled")

    # ------------------------------------------------------------ actions
    def _after(self, message):
        sel = self.tree.selection()
        nxt = self.tree.next(sel[0]) if sel else None
        self.status.configure(text=message)
        self.refresh(keep=nxt)

    def on_save(self):
        cand = self.selected()
        if cand is None:
            return
        meaning = self.meaning_var.get().strip()
        if not meaning:
            messagebox.showinfo("Blipfeed", "A card needs a meaning.")
            return
        store.set_meaning(self.conn, cand.row["id"], meaning,
                          self.note_var.get().strip() or None)
        self.conn.commit()
        self._after(f"saved {cand.term}")

    def on_card(self):
        cand = self.selected()
        if cand is None:
            return
        meaning = self.meaning_var.get().strip()
        if not meaning:
            messagebox.showinfo(
                "Blipfeed",
                f"{cand.term} has no meaning yet.\n\nBlip rejects a card without one — "
                "add a definition, or use Copy for AI to get one.")
            return
        if meaning != (cand.meaning or ""):
            store.set_meaning(self.conn, cand.row["id"], meaning,
                              self.note_var.get().strip() or None)
        from .. import blip_link
        row = store.get_term(self.conn, cand.row["id"])
        try:
            contexts = {row["term"]: store.contexts_for(self.conn, row["id"], limit=1)}
            blip_link.export([row], deck_id=self.cfg.blip_deck_id,
                             language=self.cfg.language, contexts_by_term=contexts)
            store.mark_carded(self.conn, row["id"], self.cfg.blip_deck_id,
                              str(row["id"]))
            self.conn.commit()
        except store.AlreadyCarded:
            self._after(f"{cand.term} was already carded")
            return
        except Exception as exc:
            log.exception("carding %s failed", cand.term)
            messagebox.showerror("Blipfeed", f"Could not card {cand.term}:\n{exc}")
            return
        self._after(f"carded {cand.term} — Blip shows it on its next launch")

    def on_know(self):
        cand = self.selected()
        if cand is None:
            return
        store.set_status(self.conn, cand.row["id"], "known")
        self.conn.commit()
        self._after(f"{cand.term} marked known")

    def on_ignore(self):
        cand = self.selected()
        if cand is None:
            return
        store.set_status(self.conn, cand.row["id"], "ignored")
        self.conn.commit()
        self._after(f"{cand.term} ignored")

    # ---------------------------------------------------- the AI loop
    def on_copy(self):
        batch = [c for c in self.candidates if not c.meaning] or self.candidates
        if not batch:
            self.status.configure(text="nothing to copy")
            return
        text = enrich.export_text(self.conn, batch)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.configure(
            text=f"copied {len(batch)} term(s) — paste into any AI, then Paste enriched")

    def on_paste(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Blipfeed", "The clipboard is empty.")
            return
        rows, skipped = enrich.parse_enriched(text)
        if not rows:
            messagebox.showinfo(
                "Blipfeed",
                "Nothing usable on the clipboard.\n\nExpected one line per term:\n"
                "term<TAB>meaning<TAB>note")
            return
        applied, unmatched = enrich.apply_enriched(self.conn, rows, self.cfg.language)
        parts = [f"filled {applied} definition(s)"]
        if unmatched:
            parts.append(f"{len(unmatched)} term(s) not in the store "
                         f"({', '.join(unmatched[:3])}…)")
        if skipped:
            parts.append(f"{len(skipped)} line(s) ignored")
        self.status.configure(text=" · ".join(parts))
        self.refresh()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = config_mod.load()
    conn = store.connect()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    ReviewWindow(root, conn, cfg)
    root.mainloop()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
