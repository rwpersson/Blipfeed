"""Ingestion + promotion tests — the product's brain.

The gate is the thing most likely to need tuning, so it's tested directly rather than
only through the pipeline.
"""
from blipfeed import frequency, store
from blipfeed.config import Config
from blipfeed.dictionary import Entry
from blipfeed.ocr import OcrLine

XUEXI = Entry("学习", "學習", "xue2 xi2", ("to learn", "to study"))
ZHONGWEN = Entry("中文", "中文", "zhong1 wen2", ("Chinese language",))
IDX = {"学习": XUEXI, "學習": XUEXI, "中文": ZHONGWEN}


def line(text, conf=0.9, x=0, y=0):
    return OcrLine(text, conf, (x, y, x + 50, y + 20))


def cfg(**over):
    return Config(over)


# --------------------------------- ingestion ---------------------------------
def test_ingest_records_tokens_with_context(conn):
    frequency.ingest(conn, [line("我喜欢学习中文")], idx=IDX)
    t = store.find_term(conn, "学习")
    assert t["count"] == 1
    assert store.contexts_for(conn, t["id"]) == ["我喜欢学习中文"]
    assert store.find_term(conn, "我") is None        # particle filtered out


def test_ingest_enriches_a_dictionary_hit(conn):
    frequency.ingest(conn, [line("学习")], idx=IDX)
    t = store.find_term(conn, "学习")
    assert t["meaning"] == "to learn; to study"
    assert t["alt"] == "學習"           # both scripts, for the card
    assert t["dict_ok"] == 1
    assert t["reading"] == "xué xí"     # pypinyin, independent of the dictionary


def test_ingest_keeps_a_dictionary_miss_with_a_reading(conn):
    """A miss is not garbage: recorded, readable, awaiting a definition.

    Tokens are explicit because jieba fragments 万魂幡 (see test_segment) — this test
    is about the gate, not segmentation.
    """
    frequency.ingest_line(conn, "师父该入万魂幡了", tokens=["万魂幡"], idx=IDX)
    t = store.find_term(conn, "万魂幡")
    assert t is not None
    assert t["meaning"] is None         # -> needs-a-definition bucket
    assert t["reading"] == "wàn hún fān"
    assert [r["term"] for r in store.bucket(conn, "needs_def", min_count=1)] \
        == ["万魂幡"]


def test_ingest_accumulates_frequency(conn):
    for _ in range(3):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    assert store.find_term(conn, "学习")["count"] == 3


def test_ingest_explicit_tokens_for_manual_mode(conn):
    """Manual mode records exactly the chips the user picked, including merges."""
    frequency.ingest_line(conn, "师父该入万魂幡了", tokens=["万魂幡"], idx=IDX)
    assert store.find_term(conn, "万魂幡")["count"] == 1
    assert store.find_term(conn, "师父") is None


# --------------------------------- the gate ----------------------------------
def test_promotion_requires_min_count(conn):
    frequency.ingest(conn, [line("学习")], idx=IDX)
    assert frequency.select_for_promotion(conn, cfg()) == []      # count 1 < N=2
    frequency.ingest(conn, [line("学习")], idx=IDX)
    assert [r["term"] for r in frequency.select_for_promotion(conn, cfg())] == ["学习"]


def test_promotion_requires_a_meaning(conn):
    for _ in range(5):
        frequency.ingest_line(conn, "师父该入万魂幡了", tokens=["万魂幡"], idx=IDX)
    assert frequency.select_for_promotion(conn, cfg()) == []      # no meaning yet
    store.set_meaning(conn, store.find_term(conn, "万魂幡")["id"], "soul banner")
    assert [r["term"] for r in frequency.select_for_promotion(conn, cfg())] == ["万魂幡"]


def test_promotion_skips_single_char_basics(conn):
    """The spike finding: 很/好/太 pass CEDICT but are too basic to auto-card."""
    idx = {"好": Entry("好", "好", "hao3", ("good",))}
    for _ in range(5):
        frequency.ingest_line(conn, "好", tokens=["好"], idx=idx)
    assert frequency.select_for_promotion(conn, cfg()) == []
    # ...but it is still recorded and visible for manual review
    assert store.find_term(conn, "好")["count"] == 5


def test_promotion_respects_confidence_floor(conn):
    for _ in range(3):
        frequency.ingest(conn, [line("学习", conf=0.2)], idx=IDX)
    assert frequency.select_for_promotion(conn, cfg(min_confidence=0.5)) == []
    assert frequency.select_for_promotion(conn, cfg(min_confidence=0.1)) != []


def test_promotion_is_capped_at_k(conn):
    for term in ("学习", "中文"):
        for _ in range(3):
            frequency.ingest(conn, [line(term)], idx=IDX)
    assert len(frequency.select_for_promotion(conn, cfg(max_promotions_per_snapshot=1))) == 1
    assert len(frequency.select_for_promotion(conn, cfg(max_promotions_per_snapshot=5))) == 2


def test_promotion_orders_by_frequency(conn):
    for _ in range(2):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    for _ in range(6):
        frequency.ingest(conn, [line("中文")], idx=IDX)
    picked = frequency.select_for_promotion(conn, cfg(max_promotions_per_snapshot=5))
    assert [r["term"] for r in picked] == ["中文", "学习"]     # most-seen first


def test_auto_card_off_promotes_nothing(conn):
    for _ in range(5):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    assert frequency.select_for_promotion(conn, cfg(auto_card=False)) == []


def test_promote_cards_once_and_leaves_the_queue(conn):
    for _ in range(3):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    promoted = frequency.promote(conn, cfg())
    assert [r["term"] for r in promoted] == ["学习"]
    assert store.find_term(conn, "学习")["status"] == "carded"
    assert frequency.select_for_promotion(conn, cfg()) == []     # not carded twice
    frequency.ingest(conn, [line("学习")], idx=IDX)              # still counted
    assert store.find_term(conn, "学习")["count"] == 4


def test_promote_writes_before_carding(conn):
    """The deck write runs first, so a failed write can't leave a term marked carded
    with no card to show for it."""
    seen = {}

    def writer(rows):
        seen["status_at_write"] = store.find_term(conn, "学习")["status"]

    for _ in range(3):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    frequency.promote(conn, cfg(), deck_writer=writer)
    assert seen["status_at_write"] != "carded"


# ------------------------------ the ubiquity ceiling -------------------------
def _snapshots(conn, n, session_id=None):
    """Fake n snapshots so ubiquity has a denominator."""
    sid = session_id or store.start_session(conn, "s")
    for _ in range(n):
        store.add_snapshot(conn, sid, source="r")
    return sid


def test_ubiquitous_term_is_not_auto_carded(conn):
    """The real-session bug, stated as a rule: a word present in most snapshots is
    ambient (UI, or something you already know) and must not auto-card."""
    sid = _snapshots(conn, 20)
    for _ in range(18):                     # 18/20 snapshots = 90% ubiquity
        frequency.ingest(conn, [line("打开")], session_id=sid,
                         idx={"打开": Entry("打开", "打開", "da3 kai1", ("to open",))})
    assert frequency.select_for_promotion(conn, cfg()) == []
    # ...but it is NOT discarded: still recorded, still visible, still cardable by hand
    assert store.find_term(conn, "打开")["count"] == 18
    assert [r["term"] for r in store.bucket(conn, "ready", min_count=2)] == ["打开"]


def test_normal_term_is_carded_alongside_a_ubiquitous_one(conn):
    """Your case: skip the ambient word, pick a different one from the same screen."""
    idx = {"打开": Entry("打开", "打開", "da3 kai1", ("to open",)), **IDX}
    sid = _snapshots(conn, 20)
    for _ in range(18):
        frequency.ingest(conn, [line("打开")], session_id=sid, idx=idx)
    for _ in range(3):
        frequency.ingest(conn, [line("学习")], session_id=sid, idx=idx)
    picked = [r["term"] for r in frequency.select_for_promotion(conn, cfg())]
    assert picked == ["学习"]               # ambient skipped, real word chosen


def test_ubiquity_is_a_ratio_not_a_raw_count(conn):
    """A long session must not block a genuinely useful word just for accumulating."""
    idx = {"电影": Entry("电影", "電影", "dian4 ying3", ("movie",))}
    sid = _snapshots(conn, 400)             # a long sitting
    for _ in range(30):                     # 30 sightings, but only 7.5% ubiquity
        frequency.ingest(conn, [line("电影")], session_id=sid, idx=idx)
    assert [r["term"] for r in frequency.select_for_promotion(conn, cfg())] == ["电影"]


def test_nothing_cards_until_there_are_enough_snapshots_to_judge(conn):
    """Early on everything looks ubiquitous, so nothing qualifies — the warm-up is
    emergent from the ratio, not a special case. An earlier version exempted the first
    N snapshots and chrome sailed straight through that window and got carded."""
    sid = _snapshots(conn, 2)
    for _ in range(2):
        frequency.ingest(conn, [line("学习")], session_id=sid, idx=IDX)
    assert frequency.select_for_promotion(conn, cfg()) == []      # 2/2 = 100%

    for _ in range(10):                     # more snapshots -> the ratio drops
        store.add_snapshot(conn, sid, source="r")
    assert [r["term"] for r in frequency.select_for_promotion(conn, cfg())] == ["学习"]


def test_ubiquity_threshold_is_configurable(conn):
    idx = {"东西": Entry("东西", "東西", "dong1 xi5", ("thing",))}
    sid = _snapshots(conn, 20)
    for _ in range(8):                      # 40% ubiquity
        frequency.ingest(conn, [line("东西")], session_id=sid, idx=idx)
    assert frequency.select_for_promotion(conn, cfg(max_ubiquity=0.35)) == []
    assert frequency.select_for_promotion(conn, cfg(max_ubiquity=0.90)) != []
