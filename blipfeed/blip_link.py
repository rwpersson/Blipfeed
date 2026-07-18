"""The Blip seam — writes mined terms out as a Blip deck.

**All coupling to Blip lives here.** Blipfeed never imports Blip's package; the
boundary is the deck JSON (CLAUDE.md Decisions #1), which is also why an Anki target
could slot in at this same seam later.

Blip's schema 1:

    {"schema": 1, "name": …, "language": "zh-CN", "voice": null,
     "words": [{"id", "term", "reading", "tones", "cat", "meaning", "note", "alt"}]}

Rules Blip imposes that this module must respect:

- **A word without `meaning` fails Blip's validation** — so a term with no definition
  is never written. `store.mark_carded` refuses those too; this is the second guard.
- **Word ids only need to be unique within the deck**, because Blip's audio is
  content-addressed. We assign sequential ids and never reuse one.
- **Blip loads decks at startup and does not hot-reload**, so new cards appear on its
  *next* launch. Say that in any UI message — don't imply an instant switch.
- Audio is Blip's job: it synthesizes on next launch from the deck JSON. We write no
  media (nothing to redistribute, one consistent voice).

Merging is by `term`, so re-running never duplicates a card.

If Blip can't be found, we write to Blipfeed's own `decks/` instead of failing — the
deck still exists and can be copied in. Losing mined words because a path moved would
be the worst possible failure here.
"""
import json

from . import paths
from .logging_setup import get

log = get("blip_link")

SCHEMA = 1
_WORD_FIELDS = ("id", "term", "reading", "tones", "cat", "meaning", "note", "alt")


def deck_dir():
    """Blip's user deck dir, or our own fallback if Blip isn't found."""
    d = paths.blip_decks_dir()
    if d is not None:
        return d
    fallback = paths.user_dir() / "decks"
    log.warning("Blip not found — writing decks to %s instead", fallback)
    return fallback


def deck_path(deck_id="immersion"):
    return deck_dir() / f"{deck_id}.json"


def load_deck(path, deck_id="immersion", name=None, language="zh-CN"):
    """Read an existing deck, or start a fresh one. Never raises on a bad file —
    it returns a new deck and logs, because refusing to mine because a deck got
    corrupted would lose the user's words."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("words"), list):
                data.setdefault("schema", SCHEMA)
                data.setdefault("language", language)
                return data
            raise ValueError("deck is not a Blip deck object")
        except Exception:
            log.exception("unreadable deck at %s — starting a new one", path)
    return {"schema": SCHEMA, "name": name or "Immersion", "language": language,
            "voice": None, "words": []}


def _next_id(deck):
    nums = [int(w["id"]) for w in deck["words"] if str(w.get("id", "")).isdigit()]
    return str(max(nums) + 1) if nums else "1"


def make_word(deck, row, contexts=()):
    """Build one Blip word from a store `terms` row.

    The mined sentence becomes the card's `note` — real example sentences, which is
    the feature that falls out of this pipeline for free.
    """
    word = {"id": _next_id(deck), "term": row["term"], "cat": row["cat"] or "common",
            "meaning": row["meaning"]}
    for field in ("reading", "tones", "alt"):
        if row[field]:
            word[field] = row[field]
    note = row["note"] or ""
    if not note and contexts:
        note = f"Seen in: {contexts[0]}"
    if note:
        word["note"] = note
    return {k: v for k, v in word.items() if k in _WORD_FIELDS and v}


def add_cards(deck, rows, contexts_by_term=None):
    """Merge rows into the deck, skipping duplicates and meaningless terms.

    Returns the words actually added.
    """
    have = {w.get("term") for w in deck["words"]}
    added = []
    for row in rows:
        if not row["meaning"]:
            log.warning("skipping %s — no meaning (Blip would reject it)",
                        row["term"])
            continue
        if row["term"] in have:
            continue
        contexts = (contexts_by_term or {}).get(row["term"], ())
        word = make_word(deck, row, contexts)
        deck["words"].append(word)
        have.add(row["term"])
        added.append(word)
    return added


def write_deck(deck, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, "name": deck.get("name", "Immersion"),
               "language": deck.get("language", "zh-CN"),
               "voice": deck.get("voice"), "words": deck["words"]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def export(rows, *, deck_id="immersion", name="Immersion", language="zh-CN",
           contexts_by_term=None, path=None):
    """Write rows into the Blip deck. Returns (path, words_added)."""
    path = path or deck_path(deck_id)
    deck = load_deck(path, deck_id, name, language)
    added = add_cards(deck, rows, contexts_by_term)
    if added:
        write_deck(deck, path)
        log.info("wrote %d card(s) to %s", len(added), path)
    return path, added


def exporter(conn, cfg, path=None):
    """A `deck_writer` for `frequency.promote()` that attaches mined sentences."""
    from . import store

    def write(rows):
        contexts = {r["term"]: store.contexts_for(conn, r["id"], limit=1)
                    for r in rows}
        export(rows, deck_id=cfg.blip_deck_id, language=cfg.language,
               contexts_by_term=contexts, path=path)
    return write
