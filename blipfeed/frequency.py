"""Ingestion and the promotion gate — the product's brain.

This is where the "ingest ≠ promote" split (CLAUDE.md) becomes code:

- **`ingest()`** is lossless and cheap. Every furniture-filtered line is segmented and
  every kept token is recorded with the sentence it appeared in. It decides nothing
  about cards; it only remembers what was seen. Enrichment we can do offline
  (CC-CEDICT meaning + both scripts, pypinyin reading/tones) is attached on first
  sight, which is what decides whether a term lands in *Ready to card* or
  *Needs a definition*.

- **`promote()`** is sparse and gated. It picks at most K terms per snapshot from the
  eligible pool, highest frequency first, and cards them once.

Keeping them separate is what makes a dense frame (a Douyin homepage: 114 tokens)
survivable — everything is *recorded*, almost nothing is *carded*.

## The gate

A term is promotable when all of these hold:

    status ∈ (new, queued)      not already carded / known / ignored
    meaning IS NOT NULL         cardable ⇔ has a meaning (Blip rejects otherwise)
    count >= min_count (N)      seen more than once ⇒ real, not an OCR fluke
    ocr_conf_max >= min_conf    we read it clearly at least once
    len(term) >= min_promote_length   1-char basics (很/好/去/太) aren't auto-carded

The first three come from `store.bucket('ready')`; the rest are applied here.
Everything rejected stays in the queue — visible, never discarded.

`auto_card=False` disables promotion entirely: nothing cards without the user, and
the review queue becomes the only path. That's a legitimate way to run the app.
"""
from . import dictionary, hanzi, lang, segment, store
from .logging_setup import get

log = get("frequency")


# --------------------------------- ingestion ---------------------------------
def enrich_fields(term, language="zh-CN", idx=None):
    """Everything we can fill in offline for a newly seen term.

    A CC-CEDICT hit supplies meaning + the other script form (making it cardable);
    pypinyin supplies reading/tones for *any* term, hit or miss — so a slang word
    still arrives with a tone-colored reading and only needs a definition.
    """
    fields = {"cat": "common"}
    fields.update(dictionary.fields_for(term, idx))
    fields.update(lang.fields_for(term, language))
    return fields


def ingest_line(conn, text, *, session_id=None, snapshot_id=None, confidence=None,
                bbox=None, language="zh-CN", idx=None, tokens=None):
    """Record every kept token in one recognized line. Returns the term ids.

    `tokens` overrides segmentation — that's how manual mode records exactly the
    chips the user picked (including merged ones).
    """
    out = []
    for token in (tokens if tokens is not None else segment.segment(text)):
        canonical, _alt = hanzi.canonicalize(token, language)
        term_id = store.observe(
            conn, term=canonical, language=language, session_id=session_id,
            snapshot_id=snapshot_id, context=text, confidence=confidence,
            bbox=bbox, create_fields=enrich_fields(canonical, language, idx))
        out.append(term_id)
    return out


def ingest(conn, lines, *, session_id=None, snapshot_id=None, language="zh-CN",
           idx=None):
    """Record a whole snapshot's worth of (furniture-filtered) OCR lines."""
    term_ids = []
    for line in lines:
        term_ids.extend(ingest_line(
            conn, line.text, session_id=session_id, snapshot_id=snapshot_id,
            confidence=line.confidence, bbox=repr(line.box), language=language,
            idx=idx))
    log.debug("ingested %d observations from %d lines", len(term_ids), len(lines))
    return term_ids


# --------------------------------- promotion ---------------------------------
def is_promotable(row, cfg):
    """The per-term half of the gate (the bucket handles status/meaning/count)."""
    if len(row["term"]) < cfg.min_promote_length:
        return False
    conf = row["ocr_conf_max"]
    if conf is not None and conf < cfg.min_confidence:
        return False
    return True


def select_for_promotion(conn, cfg):
    """The terms that would be carded now, best first. Pure — no writes.

    Separated from `promote()` so the policy can be tested directly; it is the piece
    most likely to need tuning.
    """
    if not cfg.auto_card:
        return []
    candidates = store.bucket(conn, "ready", language=cfg.language,
                              min_count=cfg.min_count)
    eligible = [r for r in candidates if is_promotable(r, cfg)]
    return eligible[:cfg.max_promotions_per_snapshot]


def promote(conn, cfg, deck_writer=None):
    """Card up to K eligible terms. Returns the rows promoted.

    `deck_writer(rows) -> None` receives the terms to write out (blip_link.export in
    production); it runs *before* carding so a failed write doesn't leave terms marked
    carded with no card to show for it.
    """
    chosen = select_for_promotion(conn, cfg)
    if not chosen:
        return []
    if deck_writer is not None:
        deck_writer(chosen)
    for row in chosen:
        try:
            store.mark_carded(conn, row["id"], cfg.blip_deck_id, str(row["id"]))
        except store.AlreadyCarded:
            log.warning("term %s was already carded — skipping", row["term"])
        except ValueError:
            log.exception("refusing to card %s", row["term"])
    conn.commit()
    log.info("promoted %d terms: %s", len(chosen),
             ", ".join(r["term"] for r in chosen))
    return chosen
