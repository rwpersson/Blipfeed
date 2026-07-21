"""Store tests — the source of truth gets real coverage (CLAUDE.md).

Every test uses the in-memory `conn` fixture; nothing touches the real DB.
"""
import pytest

from blipfeed import store
from blipfeed.store import AlreadyCarded


def test_migration_creates_schema(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sessions", "snapshots", "observations", "terms", "meta"} <= tables
    v = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert int(v) == store.SCHEMA_VERSION


def test_observe_creates_and_counts(conn):
    sid = store.start_session(conn, "test")
    tid = store.observe(conn, term="学习", session_id=sid, confidence=0.9,
                        context="我在学习",
                        create_fields={"reading": "xué xí", "dict_ok": 1})
    t = store.get_term(conn, tid)
    assert t["term"] == "学习"
    assert t["count"] == 1
    assert t["reading"] == "xué xí"
    assert t["first_seen"] and t["last_seen"]

    store.observe(conn, term="学习", session_id=sid, confidence=0.7)
    t = store.get_term(conn, tid)
    assert t["count"] == 2
    assert t["ocr_conf_max"] == 0.9          # max, not last


def test_confidence_none_leaves_max(conn):
    tid = store.observe(conn, term="A", confidence=0.8)
    store.observe(conn, term="A", confidence=None)
    assert store.get_term(conn, tid)["ocr_conf_max"] == 0.8   # unknown ≠ low


def test_create_fields_only_on_first(conn):
    tid = store.observe(conn, term="电影", create_fields={"meaning": "movie"})
    store.set_meaning(conn, tid, "film — edited")
    # a later sighting must NOT clobber a meaning the user may have edited
    store.observe(conn, term="电影", create_fields={"meaning": "movie"})
    assert store.get_term(conn, tid)["meaning"] == "film — edited"


def test_unique_per_language(conn):
    t_zh = store.observe(conn, term="中文", language="zh-CN")
    t_ja = store.observe(conn, term="中文", language="ja")
    assert t_zh != t_ja                      # same glyphs, different language
    assert store.observe(conn, term="中文", language="zh-CN") == t_zh


def test_trad_simp_dedup_for_frequency(conn, monkeypatch):
    # inject a small trad→simp map so the test doesn't need the CC-CEDICT file
    from blipfeed import hanzi
    monkeypatch.setattr(hanzi, "_MAP", {"學": "学", "習": "习"})

    t1 = store.observe(conn, term="学习", confidence=0.6)   # simplified
    t2 = store.observe(conn, term="學習", confidence=0.9)   # traditional -> same term
    assert t1 == t2                                         # one row, not two
    t = store.get_term(conn, t1)
    assert t["term"] == "学习"        # canonical (simplified) is the key
    assert t["count"] == 2            # frequency combined across scripts
    assert t["alt"] == "學習"         # traditional captured for the card
    assert store.find_term(conn, "學習")["id"] == t1        # lookup by either script


def test_both_script_forms_on_card(conn):
    # CEDICT hands us both forms; term = simplified (dedup key), alt = traditional.
    tid = store.observe(conn, term="学习",
                        create_fields={"alt": "學習", "meaning": "to study"})
    t = store.get_term(conn, tid)
    assert t["term"] == "学习" and t["alt"] == "學習"


def test_buckets_split_on_meaning(conn):
    store.observe(conn, term="万魂幡", confidence=0.8)              # no meaning
    store.observe(conn, term="学习", create_fields={"meaning": "to study"})
    store.observe(conn, term="学习")                               # count 2
    needs = [r["term"] for r in store.bucket(conn, "needs_def")]
    ready = [r["term"] for r in store.bucket(conn, "ready")]
    assert "万魂幡" in needs and "万魂幡" not in ready
    assert "学习" in ready and "学习" not in needs


def test_bucket_min_count_and_order(conn):
    for _ in range(3):
        store.observe(conn, term="A", create_fields={"meaning": "a"})
    store.observe(conn, term="B", create_fields={"meaning": "b"})
    ready = store.bucket(conn, "ready", min_count=2)
    assert [r["term"] for r in ready] == ["A"]        # B (count 1) filtered out
    for _ in range(5):
        store.observe(conn, term="B")
    ready = store.bucket(conn, "ready", min_count=1)
    assert [r["term"] for r in ready][0] == "B"       # count 6 -> ranked first


def test_set_meaning_moves_bucket(conn):
    tid = store.observe(conn, term="婚姻危机", confidence=0.8)
    assert [r["term"] for r in store.bucket(conn, "needs_def")] == ["婚姻危机"]
    store.set_meaning(conn, tid, "marriage crisis", note="from AI")
    assert [r["term"] for r in store.bucket(conn, "ready")] == ["婚姻危机"]
    assert store.bucket(conn, "needs_def") == []
    t = store.get_term(conn, tid)
    assert t["note"] == "from AI" and t["enriched_at"]


def test_card_once(conn):
    tid = store.observe(conn, term="学习", create_fields={"meaning": "study"})
    store.mark_carded(conn, tid, "immersion", "1")
    assert store.get_term(conn, tid)["status"] == "carded"
    with pytest.raises(AlreadyCarded):
        store.mark_carded(conn, tid, "immersion", "2")
    # count still climbs after carding, but it leaves the queue
    store.observe(conn, term="学习")
    assert store.get_term(conn, tid)["count"] == 2
    assert store.bucket(conn, "ready") == []


def test_cannot_card_without_meaning(conn):
    tid = store.observe(conn, term="万魂幡")
    with pytest.raises(ValueError):
        store.mark_carded(conn, tid, "immersion", "1")


def test_seed_known_word_excluded_from_queue(conn):
    store.seed_term(conn, "你好", meaning="hello")     # status='known' by default
    store.observe(conn, term="你好")                    # even seen, stays known
    assert store.bucket(conn, "ready") == []
    assert store.bucket(conn, "needs_def") == []
    assert store.find_term(conn, "你好")["status"] == "known"


def test_contexts_best_first_and_distinct(conn):
    tid = store.observe(conn, term="喜欢", confidence=0.5, context="我喜欢你")
    store.observe(conn, term="喜欢", confidence=0.9, context="他喜欢猫")
    store.observe(conn, term="喜欢", confidence=0.7, context="我喜欢你")   # dup
    ctx = store.contexts_for(conn, tid, limit=3)
    assert ctx[0] == "他喜欢猫"                 # highest confidence first
    assert ctx.count("我喜欢你") == 1           # distinct
    assert len(ctx) == 2


def test_recount_rebuilds_cache(conn):
    tid = store.observe(conn, term="学习", confidence=0.8)
    store.observe(conn, term="学习", confidence=0.9)
    conn.execute("UPDATE terms SET count=99, ocr_conf_max=0.1 WHERE id=?", (tid,))
    store.recount(conn)
    t = store.get_term(conn, tid)
    assert t["count"] == 2 and t["ocr_conf_max"] == 0.9


def test_last_snapshot_hash(conn):
    sid = store.start_session(conn, "vid")
    store.add_snapshot(conn, sid, source="r1", image_hash="aaa")
    store.add_snapshot(conn, sid, source="r1", image_hash="bbb")
    assert store.last_snapshot_hash(conn, sid, "r1") == "bbb"


# --- history / stats aggregates + carded_at migration (v2) -------------------
def test_carded_at_set_and_cleared(conn):
    tid = store.observe(conn, term="学习", create_fields={"meaning": "study"})
    store.mark_carded(conn, tid, "immersion", "1")
    assert store.get_term(conn, tid)["carded_at"] is not None
    store.uncard(conn, tid)
    assert store.get_term(conn, tid)["carded_at"] is None


def test_summary_counts(conn):
    store.observe(conn, term="学习", create_fields={"meaning": "study"})
    store.seed_known_bulk(conn, ["你好", "谢谢"])
    s = store.summary(conn)
    assert s["known"] if False else s["by_status"]["known"] == 2
    assert s["terms"] == 3
    assert s["observations"] == 1
    assert s["sessions"] == 0


def test_daily_activity_covers_every_day(conn):
    sid = store.start_session(conn, "s")
    snap = store.add_snapshot(conn, sid, source="r")
    store.observe(conn, term="学习", session_id=sid, snapshot_id=snap,
                  create_fields={"meaning": "study"})
    days = store.daily_activity(conn, days=7)
    assert len(days) == 7                              # continuous range, gaps = 0
    assert days[-1]["seen"] >= 1                       # today has the sighting + new
    assert days[-1]["new"] >= 1
    assert all(set(d) == {"date", "seen", "new", "cards"} for d in days)


def test_daily_activity_counts_cards_today(conn):
    tid = store.observe(conn, term="学习", create_fields={"meaning": "study"})
    store.mark_carded(conn, tid, "immersion", "1")
    assert store.daily_activity(conn, days=3)[-1]["cards"] == 1


def test_sessions_history(conn):
    sid = store.start_session(conn, "video A")
    for _ in range(3):
        snap = store.add_snapshot(conn, sid, source="r")
        store.observe(conn, term="学习", session_id=sid, snapshot_id=snap)
    rows = store.sessions_history(conn)
    assert rows[0]["source_label"] == "video A"
    assert rows[0]["snaps"] == 3 and rows[0]["words"] == 3


def test_top_terms_ranked(conn):
    for _ in range(5):
        store.observe(conn, term="中文")
    for _ in range(2):
        store.observe(conn, term="学习")
    top = store.top_terms(conn, limit=5)
    assert top[0]["term"] == "中文" and top[0]["count"] == 5


# --- flexible listing (drives the review tabs) + manual flag (schema v3) -----
def test_manual_flag_set_on_manual_observe(conn):
    store.observe(conn, term="学习")                       # auto
    store.observe(conn, term="万魂幡", manual=True)         # by hand
    assert store.find_term(conn, "学习")["manual"] == 0
    assert store.find_term(conn, "万魂幡")["manual"] == 1


def test_list_terms_by_status(conn):
    a = store.observe(conn, term="学习", create_fields={"meaning": "study"})
    store.mark_carded(conn, a, "immersion", "1")
    store.seed_known_bulk(conn, ["你好"])
    store.observe(conn, term="万魂幡")                      # new, no meaning
    assert [r["term"] for r in store.list_terms(conn, statuses=("carded",))] == ["学习"]
    assert [r["term"] for r in store.list_terms(conn, statuses=("known",))] == ["你好"]
    assert "万魂幡" in [r["term"] for r in store.list_terms(conn, statuses=("new",))]


def test_list_terms_has_meaning_and_manual(conn):
    store.observe(conn, term="学习", create_fields={"meaning": "study"})
    store.observe(conn, term="万魂幡", manual=True)
    assert [r["term"] for r in store.list_terms(conn, has_meaning=True)] == ["学习"]
    assert [r["term"] for r in store.list_terms(conn, has_meaning=False)] == ["万魂幡"]
    assert [r["term"] for r in store.list_terms(conn, manual=True)] == ["万魂幡"]


def test_list_terms_by_session(conn):
    s1 = store.start_session(conn, "one")
    snap1 = store.add_snapshot(conn, s1, source="r")
    store.observe(conn, term="学习", session_id=s1, snapshot_id=snap1)
    s2 = store.start_session(conn, "two")
    snap2 = store.add_snapshot(conn, s2, source="r")
    store.observe(conn, term="中文", session_id=s2, snapshot_id=snap2)
    assert store.latest_session_id(conn) == s2
    assert [r["term"] for r in store.list_terms(conn, session_id=s2)] == ["中文"]


def test_list_terms_search(conn):
    store.observe(conn, term="学习", create_fields={"meaning": "to study"})
    store.observe(conn, term="中文", create_fields={"meaning": "Chinese"})
    assert [r["term"] for r in store.list_terms(conn, search="学")] == ["学习"]
    # matches meaning too
    assert "中文" in [r["term"] for r in store.list_terms(conn, search="Chinese")]


def test_list_terms_by_dates(conn, monkeypatch):
    sid = store.start_session(conn, "s")
    snap = store.add_snapshot(conn, sid, source="r")
    store.observe(conn, term="学习", session_id=sid, snapshot_id=snap)
    dates = store.observation_dates(conn)
    assert len(dates) == 1
    today = dates[0]
    assert [r["term"] for r in store.list_terms(conn, dates=[today])] == ["学习"]
    assert store.list_terms(conn, dates=["1999-01-01"]) == []
    assert store.term_ids_on_dates(conn, [today]) == {store.find_term(conn, "学习")["id"]}
