"""Known words — the model of what the user already has.

## Beginner is the default, and it needs no machinery

With no data, the known-set is **empty**, so every word surfaces. That is exactly
right for a beginner: 因为 / 之后 / 内容 *are* the words they need. "Beginner" isn't a
mode to implement, it's the honest starting state — we assume nothing about someone we
know nothing about, and the set grows from real evidence.

That matters for anyone but the author: a tool that presumes an intermediate learner
is useless to the person opening it on day one.

## Sources, strongest first

1. **The user says so** — marking a word known in review. Nothing beats it.
2. **Blip already has a card for it** (`from_blip_decks`). The most valuable bulk
   source available, because the two apps share a user: re-mining a word they're
   already studying just mints a duplicate.
3. **Blip says they learned it** (`from_blip_graduated`) — exposures past Blip's
   graduation bar. This closes the loop: mine → drill → graduate → stop resurfacing.
4. **A level list** (`from_file`) — an HSK level, a frequency band, any one-word-per-
   line file. Bulk, so it must scale to thousands.

All of them converge on the same thing: `status='known'` in the store. Known terms are
still *ingested* (ingestion stays lossless — "seen 40×" remains true), they just never
card and never clutter the review queue.
"""
import csv

from . import blip_link, store
from .logging_setup import get

log = get("known")


def _apply(conn, terms, language, label):
    created, updated = store.seed_known_bulk(conn, terms, language)
    log.info("%s: %d term(s) -> %d new, %d promoted from 'new'",
             label, len(set(terms)), created, updated)
    return created, updated


def from_blip_decks(conn, language="zh-CN"):
    """Mark every term the user already has a Blip card for."""
    return _apply(conn, blip_link.deck_terms(), language, "blip decks")


def from_blip_graduated(conn, language="zh-CN", graduate_at=9):
    """Mark the terms Blip considers learned."""
    return _apply(conn, blip_link.graduated_terms(graduate_at), language,
                  "blip graduated")


def read_word_list(path):
    """Terms from a word list: one per line, or the first column of a CSV/TSV.

    Deliberately forgiving — these files come from anywhere (an HSK list, a frequency
    dump, a hand-written file), so anything with a term at the start of a line works.
    `#` comments and blank lines are skipped.
    """
    terms = []
    text = path.read_text(encoding="utf-8-sig")
    delimiter = None
    for candidate in ("\t", ","):
        if any(candidate in line for line in text.splitlines()[:20]):
            delimiter = candidate
            break
    if delimiter:
        for row in csv.reader(text.splitlines(), delimiter=delimiter):
            if row and row[0].strip() and not row[0].lstrip().startswith("#"):
                terms.append(row[0].strip())
    else:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line.split()[0])
    return terms


def from_file(conn, path, language="zh-CN"):
    """Bulk-import a level list (HSK, frequency band, custom)."""
    terms = read_word_list(path)
    return _apply(conn, terms, language, f"list {path.name}")


def forget(conn, term, language="zh-CN", status="new"):
    """Un-know a term, putting it back in play. Returns True if it existed."""
    row = store.find_term(conn, term, language)
    if row is None:
        return False
    store.set_status(conn, row["id"], status)
    conn.commit()
    return True


def sync_all(conn, language="zh-CN", graduate_at=9):
    """Every automatic source at once — what `--known-sync` runs.

    Reports each source's **set size** as well as what it newly marked. Those two
    numbers differ a lot, and conflating them misleads: the graduated words are a
    subset of the deck words, so the second source legitimately marks nothing new
    while being far from empty. Printing "0" there reads as "you've learned nothing".
    """
    deck_set = blip_link.deck_terms()
    grad_set = blip_link.graduated_terms(graduate_at)
    decks = _apply(conn, deck_set, language, "blip decks")
    grad = _apply(conn, grad_set, language, "blip graduated")
    return {"blip_decks": decks, "blip_graduated": grad,
            "deck_total": len(deck_set), "graduated_total": len(grad_set),
            "total_known": store.known_count(conn, language)}
