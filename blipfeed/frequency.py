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
from dataclasses import dataclass

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
                bbox=None, language="zh-CN", idx=None, tokens=None, manual=False):
    """Record every kept token in one recognized line. Returns the term ids.

    `tokens` overrides segmentation — that's how manual mode records exactly the
    chips the user picked (including merged ones). `manual=True` flags those terms.
    """
    out = []
    for token in (tokens if tokens is not None else segment.segment(text)):
        canonical, _alt = hanzi.canonicalize(token, language)
        term_id = store.observe(
            conn, term=canonical, language=language, session_id=session_id,
            snapshot_id=snapshot_id, context=text, confidence=confidence,
            bbox=bbox, create_fields=enrich_fields(canonical, language, idx),
            manual=manual)
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
def ubiquity(df, window):
    """Fraction of the term's lifetime snapshots it appeared in. 0 == can't judge."""
    return (df / window) if window else 0.0


def is_verified(row):
    """Do we have independent evidence this is a real word?

    A CC-CEDICT headword, or a term the user personally supplied a meaning for — the
    latter counts for more than any dictionary, since they vouched for it by hand.
    """
    return bool(row["dict_ok"]) or bool(row["enriched_at"])


def required_snapshots(row, cfg):
    """How many distinct snapshots this term needs before it may card.

    Requiring two of *everything* was blocking real vocabulary: at 30s sampling we see
    a small fraction of the lines in a video, so a genuine word rarely gets caught
    twice — while chrome is in every single frame and always clears the bar. The
    corroboration a second sighting provides is only really needed for terms nothing
    else vouches for; a dictionary hit already has that evidence.
    """
    return (cfg.get("min_count_verified", 1) if is_verified(row)
            else cfg.min_count)


def is_promotable(conn, row, cfg):
    """The per-term half of the gate (the bucket handles status/meaning/count).

    **Frequency is a band, not a floor.** Too rare (< N) is probably OCR noise; too
    *ubiquitous* is either UI chrome or a word so common the learner already knows it
    (的/是/好) — neither makes a card worth reviewing. One signal covers both cases.

    Ubiquity is a ratio against total snapshots, not a raw count, so it doesn't
    mis-fire as a session gets long: a genuinely useful word that reaches 30 sightings
    over three hours is nothing like a nav item present in every frame. Nothing is
    lost either way — an over-ubiquitous term stays in the queue, visible and
    manually cardable.
    """
    if len(row["term"]) < cfg.min_promote_length:
        return False
    conf = row["ocr_conf_max"]
    if conf is not None and conf < cfg.min_confidence:
        return False

    df, window = store.term_snapshot_stats(conn, row["id"])
    if not window:
        return True                      # no snapshot data (direct ingestion) — can't judge

    # "Seen more than once" must mean more than one *snapshot*. Counting raw
    # observations let a word appearing twice on a single screen (a toast and its
    # button) clear the gate from one frame and get carded on tick 1.
    # How many snapshots are needed depends on whether anything vouches for the term.
    if df < required_snapshots(row, cfg):
        return False

    # No warm-up exemption. An earlier version skipped this check until N snapshots
    # existed, and chrome — in every frame, so it qualifies immediately — sailed
    # straight through that window. The warm-up is emergent instead: a term's first
    # snapshot gives 1/1 = 100%, so nothing can card until its sightings have thinned
    # out relative to how long it has been around.
    if ubiquity(df, window) > cfg.get("max_ubiquity", 1.0):
        return False
    return True


def select_for_promotion(conn, cfg):
    """The terms that would be carded now, best first. Pure — no writes.

    Separated from `promote()` so the policy can be tested directly; it is the piece
    most likely to need tuning.

    Note the ordering interacts with the gate: candidates arrive most-seen-first, so
    without the ubiquity ceiling this would preferentially card the *most ambient*
    words. That is exactly how a real session ended up carding Douyin's UI. Filter
    first, then take the most-seen of what remains.
    """
    if not cfg.auto_card:
        return []
    # bucket() is a cheap index-backed pre-filter on the denormalized raw count; the
    # precise per-snapshot gate below is exact but costs a query, so evaluate lazily
    # and stop as soon as we have K.
    #
    # The pre-filter must use the LOWEST bar any term could face, or it silently
    # excludes rows the real gate would accept — passing cfg.min_count here dropped
    # every dictionary-verified single sighting before it was ever judged.
    floor = min(cfg.min_count, cfg.get("min_count_verified", cfg.min_count))
    candidates = store.bucket(conn, "ready", language=cfg.language,
                              min_count=floor)
    chosen = []
    for row in candidates:
        if is_promotable(conn, row, cfg):
            chosen.append(row)
            if len(chosen) >= cfg.max_promotions_per_snapshot:
                break
    return chosen


# ------------------------------- the review queue ----------------------------
@dataclass(frozen=True)
class Candidate:
    """One row of the review queue, classified so a UI can lead with what matters.

    `tier` is 'primary' when the term could actually be carded, and 'secondary'
    otherwise — with `reasons` saying why, in words fit to show the user. Secondary
    candidates are **collapsed, never hidden**: they stay fully interactive, because
    "ambient" and "single character" are judgements the user is entitled to overrule.
    """
    row: object
    df: int
    window: int
    ubiquity: float
    tier: str
    reasons: tuple

    @property
    def term(self):
        return self.row["term"]

    @property
    def count(self):
        return self.row["count"]

    @property
    def meaning(self):
        return self.row["meaning"]

    @property
    def reading(self):
        return self.row["reading"]


def classify(row, cfg, df=0, window=0):
    """(tier, reasons) for a candidate — the queue's view of the promotion gate.

    Deliberately mirrors `is_promotable` check for check. If they drift, the review
    list stops predicting what will card, which is the one thing it's for.
    """
    reasons = []
    if len(row["term"]) < cfg.min_promote_length:
        reasons.append("single character" if cfg.min_promote_length == 2
                       else f"shorter than {cfg.min_promote_length} characters")
    conf = row["ocr_conf_max"]
    if conf is not None and conf < cfg.min_confidence:
        reasons.append(f"low OCR confidence ({conf:.0%})")
    if window:
        need = required_snapshots(row, cfg)
        if df < need:
            why = ("" if is_verified(row)
                   else " — not in the dictionary, so it needs to recur")
            reasons.append(f"seen in only {df} snapshot"
                           f"{'s' if df != 1 else ''} (needs {need}){why}")
        elif ubiquity(df, window) > cfg.get("max_ubiquity", 1.0):
            reasons.append(f"ambient — in {ubiquity(df, window):.0%} of the "
                           f"{window} snapshots since it first appeared")
    return ("secondary" if reasons else "primary"), tuple(reasons)


def review_queue(conn, cfg, which="ready", limit=None, tier=None):
    """Candidates for review, **useful ones first**.

    The raw `store.bucket()` ranks by count alone, which on real content puts the
    ambient junk (集/讲/第/日/月) at the very top and buries anything worth learning.
    This orders by tier first, then frequency, so the queue opens on words that can
    actually become cards.

    `tier='primary'`/`'secondary'` fetches one group; omit it for everything.
    """
    rows = store.bucket(conn, which, language=cfg.language, min_count=1)
    stats = store.term_snapshot_stats_bulk(conn, [r["id"] for r in rows])
    out = []
    for row in rows:
        df, window = stats.get(row["id"], (0, 0))
        kind, reasons = classify(row, cfg, df, window)
        out.append(Candidate(row, df, window, ubiquity(df, window), kind, reasons))
    out.sort(key=lambda c: (c.tier != "primary", -c.count, c.term))
    if tier:
        out = [c for c in out if c.tier == tier]
    return out[:limit] if limit else out


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
