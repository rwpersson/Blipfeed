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


def deck_files():
    """Every deck JSON in Blip's deck dir (ours included — a term we already carded
    shouldn't be mined again either)."""
    d = deck_dir()
    return sorted(d.glob("*.json")) if d.exists() else []


def _read_deck(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("words"), list):
            return data
    except Exception:
        log.exception("skipping unreadable Blip deck %s", path)
    return None


def _term_of(word):
    """Blip's field accessors tolerate the pre-rename names, so ours must too."""
    return word.get("term") or word.get("hanzi") or ""


def deck_terms():
    """Every term the user already has a Blip card for.

    The single most valuable known-words source available: these words are already
    being studied, so re-mining them would just mint duplicates.
    """
    out = set()
    for path in deck_files():
        deck = _read_deck(path)
        if not deck:
            continue
        out.update(t for t in (_term_of(w) for w in deck["words"]) if t)
    return out


def graduated_terms(graduate_at=9):
    """Terms Blip considers learned — `e` (exposures) at or past its GRADUATE_AT.

    `state.json` keys words by their *deck-local* id, so the deck files are needed to
    resolve those ids back to terms. Mirrors Blip's constant rather than importing it;
    Blipfeed never imports Blip's package (Decisions #1).
    """
    base = paths.blip_user_dir()
    if not base:
        return set()
    state_path = base / "state.json"
    if not state_path.exists():
        return set()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("unreadable Blip state at %s", state_path)
        return set()

    by_deck = {}
    for path in deck_files():
        deck = _read_deck(path)
        if deck:
            by_deck[path.stem] = {str(w.get("id")): _term_of(w)
                                  for w in deck["words"]}
    out = set()
    for deck_id, entry in (state.get("decks") or {}).items():
        ids = by_deck.get(deck_id, {})
        for word_id, progress in (entry.get("words") or {}).items():
            try:
                exposures = int((progress or {}).get("e", 0))
            except (TypeError, ValueError):
                continue
            if exposures >= graduate_at:
                term = ids.get(str(word_id))
                if term:
                    out.add(term)
    return out


def remove_terms(deck, terms):
    """Drop words from a deck by term. Returns the terms actually removed."""
    wanted = set(terms)
    before = {w.get("term") for w in deck["words"]}
    deck["words"] = [w for w in deck["words"] if w.get("term") not in wanted]
    return sorted(before & wanted)


def write_deck(deck, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, "name": deck.get("name", "Immersion"),
               "language": deck.get("language", "zh-CN"),
               "voice": deck.get("voice"), "words": deck["words"]}
    # `archived` is the shared seam with Blip (see docs/DECK_ARCHIVE_CONTRACT.md):
    # present-and-true means "retired from review, still listed". We only ever write it
    # when true, so an active deck stays clean and its absence unambiguously means live.
    if deck.get("archived"):
        payload["archived"] = True
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
    """A `deck_writer` for `frequency.promote()` that attaches mined sentences.

    Writes into the *active* deck, rolling to a fresh one first if it's full — so the
    id `frequency.promote()` then records with `mark_carded` (via `active_deck_id`)
    matches the deck actually written. An explicit `path` (tests) skips the roll.
    """
    from . import store

    def write(rows):
        contexts = {r["term"]: store.contexts_for(conn, r["id"], limit=1)
                    for r in rows}
        deck_id = active_deck_id(cfg) if path else roll_if_full(cfg)
        export(rows, deck_id=deck_id, language=cfg.language,
               contexts_by_term=contexts, path=path)
    return write


# --------------------------- deck lifecycle: cap, roll, graduate --------------
# Two distinct operations, easy to conflate:
#   • ROLL (auto-cap)  — the active deck hit max_deck_cards, so new cards start a fresh
#     deck. The full deck stays *active* (Blip keeps reviewing it). Purely to stop any
#     one file from ballooning.
#   • GRADUATE (manual) — you've learned a batch, so retire it from Blip's review by
#     setting the shared `archived` flag. It stays in Blipfeed's carded list. If the
#     graduated deck was the active one, new cards roll to a fresh deck too.
# The `archived` flag is the seam Blip reads/writes — see docs/DECK_ARCHIVE_CONTRACT.md.

def deck_card_count(path):
    deck = _read_deck(path)
    return len(deck["words"]) if deck else 0


def _fresh_deck_id(base):
    """A new, unused deck id in the `base` family, dated for a human-readable name."""
    from datetime import date

    stem = f"{base}-{date.today().isoformat()}"
    if not deck_path(stem).exists():
        return stem
    i = 2
    while deck_path(f"{stem}-{i}").exists():
        i += 1
    return f"{stem}-{i}"


def active_deck_id(cfg):
    """The deck new cards are written to. **Pure** (no file IO, no roll) so it's safe
    to call more than once in a single promote and always agree with itself."""
    return cfg.get("active_deck") or cfg.blip_deck_id


def roll_if_full(cfg):
    """Return the deck to write to now, starting a fresh one first if the active deck
    is at/over the cap. Records the new active deck in config so a later
    `active_deck_id()` (e.g. `mark_carded`) sees the same id. The rolled-off deck is
    left active — auto-cap only splits files; retiring is `graduate()`."""
    active = active_deck_id(cfg)
    cap = cfg.get("max_deck_cards", 150)
    if cap and deck_card_count(deck_path(active)) >= cap:
        active = _fresh_deck_id(cfg.blip_deck_id)
        cfg.set("active_deck", active)
        cfg.save()
        log.info("deck full (cap %d) — new cards now roll into %s", cap, active)
    return active


def _set_archived(deck_id, value):
    path = deck_path(deck_id)
    deck = _read_deck(path)
    if deck is None:
        log.warning("can't archive %s — deck not found", deck_id)
        return False
    deck["archived"] = bool(value)
    write_deck(deck, path)
    log.info("%s %s", "archived" if value else "restored", deck_id)
    return True


def graduate(cfg, deck_id):
    """Retire a deck from Blip's review (set `archived`), keeping it in the carded
    list. If it was the active deck, new cards roll to a fresh one."""
    ok = _set_archived(deck_id, True)
    if ok and active_deck_id(cfg) == deck_id:
        cfg.set("active_deck", _fresh_deck_id(cfg.blip_deck_id))
        cfg.save()
    return ok


def restore(cfg, deck_id):
    """Un-archive a deck — put it back into Blip's active review."""
    return _set_archived(deck_id, False)


def archived_deck_ids():
    """Ids of every deck currently flagged archived."""
    return {p.stem for p in deck_files() if (_read_deck(p) or {}).get("archived")}


def _owned(deck_id, base):
    """A deck Blipfeed authored — the base id or a dated roll of it. Guards us from
    ever archiving a deck the user hand-made in Blip."""
    return deck_id == base or deck_id.startswith(base + "-")


def list_decks(cfg):
    """The immersion deck family, for the deck manager: id, name, card count, and
    whether each is archived / the active one. Sorted active first, then by id."""
    base = cfg.blip_deck_id
    active = active_deck_id(cfg)
    out = []
    for path in deck_files():
        if not _owned(path.stem, base):
            continue
        deck = _read_deck(path)
        if deck is None:
            continue
        out.append({"id": path.stem, "name": deck.get("name", path.stem),
                    "count": len(deck["words"]), "archived": bool(deck.get("archived")),
                    "active": path.stem == active})
    out.sort(key=lambda d: (d["archived"], not d["active"], d["id"]))
    return out
