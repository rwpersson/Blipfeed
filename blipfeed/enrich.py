"""The manual AI-enrichment loop — export a batch, paste it back.

**This is the only AI path in Blipfeed, and it is deliberately manual** (CLAUDE.md
Decisions #5): no built-in API calls, no key to configure, no per-use cost to the user
or the author. The user pastes into whatever chat model they already have.

It exists because the dictionary-miss words are the *point*, not the leftovers. On real
content the words worth learning skew heavily to what CC-CEDICT lacks — slang, xianxia
terms, show titles, fresh compounds (万魂幡, 婚姻危机, 凡人歌 all miss). Those arrive
with a reading (pypinyin works on any hanzi) and real context sentences; the only thing
missing is a meaning, and that is exactly what a chat model can supply *given the
context we mined*.

Two halves, both pure so they're tested without a window:

- `export_text()` — a ready-to-paste prompt: instructions the model can follow, then
  each term with its reading, frequency and mined sentences.
- `parse_enriched()` — a **forgiving** parser. Whatever comes back has been through a
  chat model and a clipboard, so it may carry markdown, numbering, smart quotes, or
  stray commentary. Anything recoverable is recovered; anything else is reported rather
  than silently dropped.
"""
import re

from . import store
from .logging_setup import get

log = get("enrich")

PROMPT = (
    "Define each Chinese term below as it is used in its context sentences.\n"
    "Reply with ONE LINE PER TERM, tab-separated, and nothing else:\n"
    "term<TAB>English meaning<TAB>brief usage note\n"
    "Keep the meaning short enough for a flashcard. If a term is a name, a show "
    "title, or internet slang, say so in the note.\n"
)

# Leading list markers a model likes to add: "1. ", "- ", "* ", "1) "
_LEADING = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
# Markdown emphasis / code ticks around a field
_DECOR = re.compile(r"^[\s`*_\"'“”‘’]+|[\s`*_\"'“”‘’]+$")


def export_text(conn, candidates, contexts=3, prompt=True):
    """A paste-ready batch for a chat model.

    Includes the instructions so the user doesn't have to write them, and the mined
    sentences so the model defines the word *in context* rather than guessing at an
    isolated string — which is the whole reason a slang term is enrichable at all.
    """
    lines = [PROMPT, ""] if prompt else []
    for cand in candidates:
        row = getattr(cand, "row", cand)
        reading = row["reading"] or ""
        head = f"{row['term']}"
        bits = [b for b in (reading, f"seen {row['count']}×") if b]
        if bits:
            head += f" ({', '.join(bits)})"
        lines.append(head)
        for sentence in store.contexts_for(conn, row["id"], limit=contexts):
            lines.append(f"  · {sentence}")
    return "\n".join(lines).rstrip() + "\n"


def _clean_field(text):
    return _DECOR.sub("", (text or "").strip())


def _split_row(line):
    """Best-effort split of one reply line into (term, meaning, note)."""
    line = _LEADING.sub("", line.rstrip())
    if not line.strip():
        return None
    for sep in ("\t", "|", "  "):          # tab, markdown table, or aligned columns
        if sep in line:
            parts = [_clean_field(p) for p in line.split(sep)]
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                return parts[0], parts[1], (parts[2] if len(parts) > 2 else "")
    return None


def parse_enriched(text):
    """(rows, skipped) from a model's reply.

    `rows` are (term, meaning, note); `skipped` are lines we couldn't make sense of,
    returned rather than swallowed so the UI can say what it ignored — silently
    dropping half a paste would be worse than refusing it.
    """
    rows, skipped = [], []
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        parsed = _split_row(raw)
        if parsed is None:
            skipped.append(raw.strip())
            continue
        term, meaning, note = parsed
        if not term or not meaning:
            skipped.append(raw.strip())
            continue
        rows.append((term, meaning, note))
    return rows, skipped


def apply_enriched(conn, rows, language="zh-CN"):
    """Write meanings back. Returns (applied, unmatched_terms).

    A term we've never seen is reported, not created: this imports definitions for
    words you mined, and inventing rows from a model's reply would let a hallucinated
    term into the store.
    """
    applied, unmatched = 0, []
    for term, meaning, note in rows:
        row = store.find_term(conn, term, language)
        if row is None:
            unmatched.append(term)
            continue
        store.set_meaning(conn, row["id"], meaning, note or None)
        applied += 1
    conn.commit()
    log.info("enrichment applied to %d term(s); %d unmatched", applied,
             len(unmatched))
    return applied, unmatched
