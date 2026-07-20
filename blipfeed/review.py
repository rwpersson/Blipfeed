"""The mining-queue review — logic, and the standalone entry point.

Two jobs the automation can't finish on its own:

- **Enrichment.** The *Needs a definition* bucket is the slang/name list CC-CEDICT
  can't gloss (无畏, 二发, 捷风 from a real session). They have a reading and mined
  context already; what they lack is a meaning. The manual-AI loop supplies it —
  `export_for_ai()` hands you a batch to paste into any chat model, `import_enriched()`
  reads the reply back — the only AI path, by product decision (CLAUDE.md Decisions #5).
- **Cleanup.** Auto-card is deliberately on, so a little transient UI slips through
  (设置, 识别). Un-carding pulls a term back out of the Blip deck and the queue.

The **controller holds all the logic and imports no Tk**, so it's tested directly
(Blip's editor pattern); `ui/review_window.py` is the dumb shell. `python -m
blipfeed.review` launches the window.

All actions are one term flipping `status`/`meaning`, exactly the store operations the
gate already speaks — the queue is a view, not a second source of truth.
"""
import re
import sys

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import blipfeed  # noqa: F401

    __package__ = "blipfeed"

from . import blip_link, frequency, store  # noqa: E402
from .logging_setup import get  # noqa: E402

log = get("review")

# The paste-back grammar: term<TAB>meaning<TAB>note. Forgiving on purpose — the reply
# comes from an arbitrary chat model, so tolerate extra spaces, list bullets, and a
# missing note.
_BULLET = re.compile(r"^\s*(?:[-*·•]|\d+[.)])\s*")


class Candidate:
    """A queue row prepared for display: the store row + its derived context."""

    __slots__ = ("row", "tier", "reasons", "contexts")

    def __init__(self, row, tier, reasons, contexts):
        self.row = row
        self.tier = tier
        self.reasons = reasons
        self.contexts = contexts

    # convenience passthroughs the window reads
    id = property(lambda self: self.row["id"])
    term = property(lambda self: self.row["term"])
    alt = property(lambda self: self.row["alt"])
    reading = property(lambda self: self.row["reading"])
    count = property(lambda self: self.row["count"])
    meaning = property(lambda self: self.row["meaning"])
    note = property(lambda self: self.row["note"])


class ReviewController:
    """Everything the review window can do, without any Tk."""

    def __init__(self, conn, cfg):
        self.conn = conn
        self.cfg = cfg

    # ------------------------------------------------------------ read side
    def _candidates(self, which, context_limit=3):
        out = []
        for c in frequency.review_queue(self.conn, self.cfg, which):
            contexts = store.contexts_for(self.conn, c.row["id"], limit=context_limit)
            out.append(Candidate(c.row, c.tier, c.reasons, contexts))
        return out

    def needs_definition(self):
        """The loud bucket: real words with no meaning yet."""
        return self._candidates("needs_def")

    def ready(self):
        """Have a meaning; one click from a card (many auto-card on their own)."""
        return self._candidates("ready")

    def carded(self):
        """Already in the Blip deck — shown so UI stragglers can be pulled back out."""
        rows = store.carded_terms(self.conn, self.cfg.language)
        return [Candidate(r, "primary", (), store.contexts_for(self.conn, r["id"], 1))
                for r in rows]

    def counts(self):
        def split(cands):
            p = sum(1 for c in cands if c.tier == "primary")
            return p, len(cands) - p
        nd_p, nd_s = split(self.needs_definition())
        rd_p, rd_s = split(self.ready())
        return {"needs_def": (nd_p, nd_s), "ready": (rd_p, rd_s),
                "carded": len(store.carded_terms(self.conn, self.cfg.language)),
                "known": store.known_count(self.conn, self.cfg.language)}

    # ------------------------------------------------------------ write side
    def accept(self, term_id):
        """Card a term now (used for Ready terms not yet auto-promoted). Writes the
        Blip deck first, then marks carded — same order as the auto gate, so a failed
        write can't strand a term as carded with no card."""
        row = store.get_term(self.conn, term_id)
        if row is None:
            raise KeyError(term_id)
        if not row["meaning"]:
            raise ValueError("can't card a term with no meaning — enrich it first")
        contexts = {row["term"]: store.contexts_for(self.conn, term_id, 1)}
        blip_link.export([row], deck_id=self.cfg.blip_deck_id,
                         language=self.cfg.language, contexts_by_term=contexts)
        store.mark_carded(self.conn, term_id, self.cfg.blip_deck_id, str(term_id))
        self.conn.commit()

    def ignore(self, term_id):
        store.set_status(self.conn, term_id, "ignored")
        self.conn.commit()

    def mark_known(self, term_id):
        store.set_status(self.conn, term_id, "known")
        self.conn.commit()

    def set_meaning(self, term_id, meaning, note=None):
        """Supply/edit a definition. This is what moves a term from *needs a
        definition* to *ready* and makes it cardable."""
        store.set_meaning(self.conn, term_id, meaning.strip(),
                          (note or "").strip() or None)
        self.conn.commit()

    def uncard(self, term_id):
        """Pull a term back out of the Blip deck and the queue (a leaked UI word)."""
        row = store.get_term(self.conn, term_id)
        if row is None:
            raise KeyError(term_id)
        path = blip_link.deck_path(self.cfg.blip_deck_id)
        deck = blip_link.load_deck(path, self.cfg.blip_deck_id,
                                   language=self.cfg.language)
        if blip_link.remove_terms(deck, [row["term"]]):
            blip_link.write_deck(deck, path)
        store.uncard(self.conn, term_id)
        self.conn.commit()

    # ------------------------------------------------- the manual-AI loop
    def export_for_ai(self, limit=40):
        """A batch of undefined terms as text to paste into any chat model.

        Everything a model needs to gloss slang *in context*: the term, its reading,
        how often it was seen, and a few real sentences it appeared in — plus an
        instruction to reply in the format `import_enriched` reads back.
        """
        cands = [c for c in self.needs_definition() if c.tier == "primary"][:limit]
        if not cands:
            return ""
        lines = [
            "I'm learning Chinese from immersion (video/games), so many of these are "
            "slang, names, or game terms. For each, give a concise English meaning "
            "and a short usage/nuance note.",
            "",
            "Reply with ONE line per term, exactly:  term<TAB>meaning<TAB>note",
            "(a real tab between fields; leave note blank if there's nothing to add)",
            "",
            "Terms:",
        ]
        for c in cands:
            reading = f"  ({c.reading})" if c.reading else ""
            lines.append(f"{c.term}{reading}  — seen {c.count}×")
            for sentence in c.contexts:
                lines.append(f"    · {sentence}")
        return "\n".join(lines)

    def import_enriched(self, text):
        """Parse a model's reply and fill in meanings. Returns (updated, unmatched).

        Forgiving: skips headers/blank lines, strips list bullets, accepts a tab or
        (failing that) two-or-more spaces between fields, and matches the term by
        either script. A line whose term isn't in the store is reported, not fatal.
        """
        updated, unmatched = 0, []
        for raw in text.splitlines():
            line = _BULLET.sub("", raw.strip())
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                parts = re.split(r"\s{2,}", line)
            if len(parts) < 2:
                continue
            term = parts[0].strip()
            meaning = parts[1].strip()
            note = parts[2].strip() if len(parts) > 2 else None
            if not term or not meaning:
                continue
            row = store.find_term(self.conn, term, self.cfg.language)
            if row is None:
                unmatched.append(term)
                continue
            store.set_meaning(self.conn, row["id"], meaning, note or None)
            updated += 1
        self.conn.commit()
        if unmatched:
            log.info("paste-enriched: %d matched, %d unmatched (%s)",
                     updated, len(unmatched), ", ".join(unmatched[:8]))
        return updated, unmatched


def main():
    from . import capture, config as config_mod

    sys.stdout.reconfigure(encoding="utf-8")
    capture.set_dpi_aware()
    cfg = config_mod.load()
    conn = store.connect()
    try:
        from .ui.review_window import run
        run(ReviewController(conn, cfg))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
