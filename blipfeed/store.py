"""The SQLite frequency store — Blipfeed's source of truth.

Four tables (see CLAUDE.md data model):
  sessions      one immersion sitting (a video, a scroll)
  snapshots     one capture tick; prunable (observations carry what they need)
  observations  one sighting of a term — the append-only frequency ledger
  terms         one distinct word — the queryable state

Design rules this module enforces (weighed in before Phase 2):
- **terms have a surrogate `id`** + UNIQUE(language, term). Observations reference the
  compact int, and (language, term) uniqueness gives the multi-language seam for free.
- **cardable ⇔ meaning IS NOT NULL.** `mark_carded` refuses a term with no meaning,
  so a Blip card is never written without one (Blip would reject it anyway).
- **card-once.** `mark_carded` refuses a term already `carded`. Count keeps climbing
  after carding; the card is minted once.
- **`count` and `ocr_conf_max` are denormalized** onto terms (bumped inside the same
  step as the observation insert) so ranking never scans observations. They're a
  cache — `recount()` rebuilds them from the ledger if they ever drift.
- **status is CHECKed**, so a typo can't create a dead state that silently does
  nothing (Blip's lesson).

Transactions: mutating functions do **not** auto-commit — the caller controls the
transaction (commit once per snapshot on the hot path). `connect()` and the migration
commit themselves. Furniture detection is NOT here: it runs before an observation is
written, as in-memory per-session state (persisting it would store what we discard).
"""
import sqlite3
from datetime import datetime, timezone

from . import paths
from .logging_setup import get

log = get("store")

SCHEMA_VERSION = 1
STATUSES = ("new", "queued", "carded", "known", "ignored")

# Fields a caller may set when a term is first created (whitelist — never format a
# column name from caller input into SQL).
_CREATE_FIELDS = ("alt", "reading", "tones", "meaning", "note", "cat", "hsk_level",
                  "dict_ok", "status")

_DDL_V1 = """
CREATE TABLE sessions (
  id           INTEGER PRIMARY KEY,
  started      TEXT NOT NULL,
  ended        TEXT,
  source_label TEXT,
  language     TEXT NOT NULL DEFAULT 'zh-CN',
  mode         TEXT NOT NULL DEFAULT 'auto' CHECK(mode IN ('auto','manual'))
);

CREATE TABLE snapshots (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES sessions(id),
  ts          TEXT NOT NULL,
  source      TEXT,
  region      TEXT,
  image_hash  TEXT,
  raw_text    TEXT,
  ocr_engine  TEXT
);
CREATE INDEX ix_snapshots_session ON snapshots(session_id, ts);

CREATE TABLE terms (
  id           INTEGER PRIMARY KEY,
  term         TEXT NOT NULL,              -- canonical form (simplified) — the dedup key
  alt          TEXT,                        -- the other script (traditional) -> Blip's `alt`
  language     TEXT NOT NULL DEFAULT 'zh-CN',
  reading      TEXT,
  tones        TEXT,
  meaning      TEXT,                       -- NULL => "needs a definition" bucket
  note         TEXT,
  cat          TEXT,
  hsk_level    INTEGER,
  dict_ok      INTEGER NOT NULL DEFAULT 0, -- was a CEDICT headword (informational)
  ocr_conf_max REAL,
  count        INTEGER NOT NULL DEFAULT 0, -- denormalized cache of observation count
  status       TEXT NOT NULL DEFAULT 'new'
               CHECK(status IN ('new','queued','carded','known','ignored')),
  first_seen   TEXT,
  last_seen    TEXT,
  enriched_at  TEXT,
  card_deck    TEXT,
  card_id      TEXT,
  UNIQUE(language, term)
);
CREATE INDEX ix_terms_status_count ON terms(status, count DESC);
-- the loud "needs a definition" bucket is the primary query -> partial index
CREATE INDEX ix_terms_needsdef ON terms(count DESC)
  WHERE meaning IS NULL AND status IN ('new','queued');

CREATE TABLE observations (
  id               INTEGER PRIMARY KEY,
  term_id          INTEGER NOT NULL REFERENCES terms(id),
  session_id       INTEGER REFERENCES sessions(id),
  snapshot_id      INTEGER,                -- soft ref; snapshots are prunable
  context_sentence TEXT,
  confidence       REAL,
  bbox             TEXT,
  ts               TEXT NOT NULL
);
CREATE INDEX ix_obs_term ON observations(term_id);
CREATE INDEX ix_obs_session ON observations(session_id, ts);
"""


class AlreadyCarded(Exception):
    """Raised when carding a term that was already carded (card-once)."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- connection + migration ---------------------------------------------------
def connect(path=None):
    """Open the store, set pragmas, run migrations. Returns a Row-factory conn.

    `check_same_thread=False` because the app is genuinely multi-threaded: the capture
    loop ticks on the main thread while a hotkey-triggered manual capture records from
    a worker (the hotkey pump must never block). sqlite3's default thread check made
    that a hard crash that *lost the user's picked words*.

    **Callers must serialize their own access** — `CaptureSession` holds a lock around
    every connection use. Turning the check off without that would trade a loud crash
    for silent corruption.
    """
    path = str(path or paths.db_file())
    if path != ":memory:":
        paths.user_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def _migrate(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    version = int(row[0]) if row else 0
    if version < 1:
        conn.executescript(_DDL_V1)
    # future: if version < 2: conn.executescript(_DDL_V2) ...
    if version < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),))
        conn.commit()
        log.info("migrated store %s -> schema v%d", version, SCHEMA_VERSION)


# --- sessions + snapshots -----------------------------------------------------
def start_session(conn, source_label=None, language="zh-CN", mode="auto"):
    cur = conn.execute(
        "INSERT INTO sessions(started, source_label, language, mode) VALUES(?,?,?,?)",
        (_now(), source_label, language, mode))
    conn.commit()
    return cur.lastrowid


def end_session(conn, session_id):
    conn.execute("UPDATE sessions SET ended=? WHERE id=?", (_now(), session_id))
    conn.commit()


def add_snapshot(conn, session_id, *, source=None, region=None, image_hash=None,
                 raw_text=None, ocr_engine=None):
    cur = conn.execute(
        "INSERT INTO snapshots(session_id, ts, source, region, image_hash, raw_text,"
        " ocr_engine) VALUES(?,?,?,?,?,?,?)",
        (session_id, _now(), source, region, image_hash, raw_text, ocr_engine))
    return cur.lastrowid


def term_snapshot_stats(conn, term_id):
    """(distinct snapshots the term appeared in, snapshots since it first appeared).

    Proper **document frequency**, and the two halves matter for different reasons:

    * The numerator is `COUNT(DISTINCT snapshot_id)`, not the raw observation count.
      A word appearing twice on one screen (Douyin's 当前为静音 toast *and* its button)
      must not read as "seen twice" — that let chrome clear `min_count` from a single
      snapshot and get carded on tick 1.
    * The denominator counts snapshots **since the term first appeared**, not all
      snapshots ever. Measured against all history a brand-new term is diluted by
      sessions it couldn't have appeared in, so ubiquitous chrome looked rare
      (抖音 scored 31% when it was really in every frame of its session).

    Returns (0, 0) when no observation carries a snapshot id, meaning "can't judge".
    """
    row = conn.execute(
        "SELECT COUNT(DISTINCT snapshot_id) AS df, MIN(snapshot_id) AS first_snap "
        "FROM observations WHERE term_id=? AND snapshot_id IS NOT NULL",
        (term_id,)).fetchone()
    if not row or not row["df"]:
        return 0, 0
    window = conn.execute("SELECT COUNT(*) FROM snapshots WHERE id >= ?",
                          (row["first_snap"],)).fetchone()[0]
    return row["df"], window


def term_snapshot_stats_bulk(conn, term_ids):
    """{term_id: (df, window)} for many terms in two queries instead of 2N.

    The review queue classifies every candidate, so the per-term version would mean
    hundreds of round-trips to draw one list. Snapshot ids are fetched once and
    bisected, which is exact even if snapshots are ever pruned (unlike arithmetic on
    id ranges).
    """
    ids = list(term_ids)
    if not ids:
        return {}
    from bisect import bisect_left

    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT term_id, COUNT(DISTINCT snapshot_id) AS df, "
        f"MIN(snapshot_id) AS first_snap FROM observations "
        f"WHERE snapshot_id IS NOT NULL AND term_id IN ({placeholders}) "
        f"GROUP BY term_id", ids).fetchall()
    snapshot_ids = [r[0] for r in
                    conn.execute("SELECT id FROM snapshots ORDER BY id")]
    total = len(snapshot_ids)
    out = {}
    for r in rows:
        if not r["df"]:
            continue
        window = total - bisect_left(snapshot_ids, r["first_snap"])
        out[r["term_id"]] = (r["df"], window)
    return out


def last_snapshot_hash(conn, session_id, source=None):
    """Most recent snapshot's image_hash for change-detection (Phase 3)."""
    sql = "SELECT image_hash FROM snapshots WHERE session_id=?"
    params = [session_id]
    if source is not None:
        sql += " AND source=?"
        params.append(source)
    sql += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


# --- terms + observations -----------------------------------------------------
def upsert_term(conn, term, language="zh-CN", fields=None, ts=None):
    """Ensure a term row exists. Returns (term_id, created).

    The term is canonicalized (trad→simp) so 學習 and 学习 dedup to one row; the
    other script rides along as `alt`. Fields are applied ONLY on creation — a later
    observation never overwrites a term the user may have edited — except `alt` is
    backfilled when still NULL (safe, and captures whichever script we see first).
    Counters are NOT touched here (see observe()).
    """
    from . import hanzi
    term, alt = hanzi.canonicalize(term, language)
    fields = dict(fields or {})
    if alt and not fields.get("alt"):
        fields["alt"] = alt

    row = conn.execute("SELECT id, alt FROM terms WHERE language=? AND term=?",
                       (language, term)).fetchone()
    if row:
        if alt and row["alt"] is None:
            conn.execute("UPDATE terms SET alt=? WHERE id=? AND alt IS NULL",
                         (alt, row["id"]))
        return row["id"], False
    ts = ts or _now()
    cols = {"term": term, "language": language, "first_seen": ts, "last_seen": ts}
    for k, v in fields.items():
        if k in _CREATE_FIELDS and v is not None:
            cols[k] = v
    keys = list(cols)
    conn.execute(f"INSERT INTO terms({','.join(keys)}) "
                 f"VALUES({','.join('?' * len(keys))})",
                 [cols[k] for k in keys])
    tid = conn.execute("SELECT id FROM terms WHERE language=? AND term=?",
                       (language, term)).fetchone()[0]
    return tid, True


def observe(conn, *, term, language="zh-CN", session_id=None, snapshot_id=None,
            context=None, confidence=None, bbox=None, ts=None, create_fields=None):
    """Record one sighting of a term. Creates the term on first sight.

    Bumps the denormalized count / ocr_conf_max / last_seen. The append-only
    observation is the ledger; count is the cache. Does NOT commit.
    """
    ts = ts or _now()
    term_id, _ = upsert_term(conn, term, language, create_fields, ts=ts)
    conn.execute(
        "INSERT INTO observations(term_id, session_id, snapshot_id, context_sentence,"
        " confidence, bbox, ts) VALUES(?,?,?,?,?,?,?)",
        (term_id, session_id, snapshot_id, context, confidence, bbox, ts))
    conn.execute(
        "UPDATE terms SET count = count + 1, last_seen = :ts,"
        " ocr_conf_max = CASE"
        "   WHEN :conf IS NULL THEN ocr_conf_max"          # unknown ≠ low: leave it
        "   WHEN ocr_conf_max IS NULL THEN :conf"
        "   ELSE MAX(ocr_conf_max, :conf) END "
        "WHERE id = :tid",
        {"ts": ts, "conf": confidence, "tid": term_id})
    return term_id


def seed_term(conn, term, language="zh-CN", *, status="known", **fields):
    """Create a term with no observation — e.g. importing a known-words list so
    those words never get carded (status='known'). Idempotent-ish: returns the id,
    updating status/fields only if it already existed as 'new'."""
    fields = {**fields, "status": status}
    term_id, created = upsert_term(conn, term, language, fields)
    if not created:
        conn.execute("UPDATE terms SET status=? WHERE id=? AND status='new'",
                     (status, term_id))
    return term_id


# --- term state ---------------------------------------------------------------
def get_term(conn, term_id):
    return conn.execute("SELECT * FROM terms WHERE id=?", (term_id,)).fetchone()


def find_term(conn, term, language="zh-CN"):
    """Look up by either script — the query term is canonicalized too."""
    from . import hanzi
    term, _ = hanzi.canonicalize(term, language)
    return conn.execute("SELECT * FROM terms WHERE language=? AND term=?",
                        (language, term)).fetchone()


def set_status(conn, term_id, status):
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    conn.execute("UPDATE terms SET status=? WHERE id=?", (status, term_id))


def set_meaning(conn, term_id, meaning, note=None, ts=None):
    """Supply a definition (from CEDICT or manual/AI enrichment). This is what moves
    a term out of the 'needs a definition' bucket and makes it cardable."""
    conn.execute(
        "UPDATE terms SET meaning=?, note=COALESCE(?, note), enriched_at=? WHERE id=?",
        (meaning, note, ts or _now(), term_id))


def uncard(conn, term_id, status="ignored"):
    """Undo a carding: clear the deck link and put the term back in the queue.

    `status='ignored'` by default so a word you removed as junk doesn't immediately
    re-card itself; pass 'queued' to genuinely reconsider it. Card-once is about not
    minting duplicates, not about being irreversible.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    conn.execute("UPDATE terms SET status=?, card_deck=NULL, card_id=NULL "
                 "WHERE id=?", (status, term_id))


def carded_terms(conn, language=None):
    sql = "SELECT * FROM terms WHERE status='carded'"
    params = []
    if language:
        sql += " AND language=?"
        params.append(language)
    return conn.execute(sql + " ORDER BY count DESC", params).fetchall()


def mark_carded(conn, term_id, deck, card_id):
    """Promote a term to a Blip card. Enforces the two invariants:
    card-once (refuse if already carded) and cardable⇔has-meaning."""
    row = conn.execute("SELECT status, meaning FROM terms WHERE id=?",
                       (term_id,)).fetchone()
    if row is None:
        raise KeyError(term_id)
    if row["status"] == "carded":
        raise AlreadyCarded(f"term {term_id} is already carded")
    if row["meaning"] is None:
        raise ValueError(f"term {term_id} has no meaning — cannot card it")
    conn.execute("UPDATE terms SET status='carded', card_deck=?, card_id=? WHERE id=?",
                 (deck, card_id, term_id))


# --- the mining queue: two buckets + the long tail ----------------------------
def bucket(conn, which, *, language=None, min_count=1, limit=None):
    """Rows for a review bucket, frequency-ranked. `which`:
      'needs_def' — meaning IS NULL   (the loud slang bucket)
      'ready'     — meaning IS NOT NULL (CEDICT/enriched, one-click cardable)
    Both are status ∈ (new, queued) and count ≥ min_count."""
    if which == "needs_def":
        meaning_clause = "meaning IS NULL"
    elif which == "ready":
        meaning_clause = "meaning IS NOT NULL"
    else:
        raise ValueError(f"unknown bucket {which!r}")
    sql = ("SELECT * FROM terms WHERE status IN ('new','queued') "
           f"AND {meaning_clause} AND count >= ?")
    params = [min_count]
    if language:
        sql += " AND language=?"
        params.append(language)
    sql += " ORDER BY count DESC, last_seen DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def contexts_for(conn, term_id, limit=3):
    """The best distinct context sentences for a term (highest OCR confidence) —
    the mined example sentences, and what makes a slang term enrichable."""
    rows = conn.execute(
        "SELECT context_sentence, MAX(confidence) c FROM observations "
        "WHERE term_id=? AND context_sentence IS NOT NULL AND context_sentence <> '' "
        "GROUP BY context_sentence ORDER BY c DESC, LENGTH(context_sentence) ASC "
        "LIMIT ?", (term_id, limit)).fetchall()
    return [r["context_sentence"] for r in rows]


# --- maintenance --------------------------------------------------------------
def recount(conn):
    """Rebuild the denormalized count / ocr_conf_max caches from the ledger."""
    conn.execute(
        "UPDATE terms SET "
        " count = (SELECT COUNT(*) FROM observations o WHERE o.term_id = terms.id),"
        " ocr_conf_max = (SELECT MAX(confidence) FROM observations o"
        "                 WHERE o.term_id = terms.id)")
    conn.commit()
