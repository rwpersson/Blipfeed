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
def tick(conn, sid, texts, idx=None):
    """One realistic snapshot: lines recorded against a real snapshot id, which is
    what document frequency is measured over."""
    snap = store.add_snapshot(conn, sid, source="r")
    frequency.ingest(conn, [line(t) for t in texts], session_id=sid,
                     snapshot_id=snap, idx=idx or IDX)
    return snap


OPEN = Entry("打开", "打開", "da3 kai1", ("to open",))
MOVIE = Entry("电影", "電影", "dian4 ying3", ("movie",))
IDX2 = {**IDX, "打开": OPEN, "电影": MOVIE}


def test_a_word_twice_on_one_screen_is_not_seen_twice(conn):
    """The tick-1 bug: 当前为静音 + its 打开声音 button put the word on screen twice,
    which cleared min_count from a SINGLE snapshot and carded chrome immediately."""
    sid = store.start_session(conn, "s")
    tick(conn, sid, ["请打开声音", "打开"], idx=IDX2)      # count 2, but one snapshot
    assert store.find_term(conn, "打开")["count"] == 2
    assert frequency.select_for_promotion(conn, cfg()) == []


def test_chrome_in_every_snapshot_is_never_carded(conn):
    """Ubiquity is measured since the term first appeared, so chrome sits at 100%."""
    sid = store.start_session(conn, "s")
    for sub in ["我喜欢学习", "今天天气好", "外面下雨了", "他正在看书",
                "明天见面吧", "音乐真好听", "我们去公园", "这个很有趣"]:
        tick(conn, sid, ["请打开声音", sub], idx=IDX2)     # chrome + varying content
    chrome = store.find_term(conn, "打开")
    df, window = store.term_snapshot_stats(conn, chrome["id"])
    assert df == window                                    # in every snapshot: 100%
    assert "打开" not in [r["term"] for r in frequency.select_for_promotion(conn, cfg())]


def test_content_is_carded_alongside_chrome(conn):
    """Your case: skip the ambient word, pick a real one from the same screen."""
    sid = store.start_session(conn, "s")
    subs = ["我喜欢学习", "今天天气好", "外面下雨了", "他正在看书",
            "明天见面吧", "音乐真好听", "学习中文", "这个很有趣"]
    for sub in subs:
        tick(conn, sid, ["请打开声音", sub], idx=IDX2)
    picked = [r["term"] for r in frequency.select_for_promotion(conn, cfg())]
    assert "学习" in picked and "打开" not in picked


def test_a_new_term_cannot_card_on_its_first_snapshot(conn):
    """1 snapshot out of 1 since first seen = 100% — nothing qualifies immediately,
    however much older history exists in the store."""
    sid = store.start_session(conn, "s")
    for _ in range(20):                       # plenty of unrelated history
        store.add_snapshot(conn, sid, source="r")
    tick(conn, sid, ["请打开声音", "打开"], idx=IDX2)
    assert frequency.select_for_promotion(conn, cfg()) == []


def test_ubiquity_is_a_ratio_so_long_sessions_still_card(conn):
    """A useful word must not be blocked just for accumulating over a long sitting."""
    sid = store.start_session(conn, "s")
    for i in range(40):
        texts = ["请打开声音"] + (["他在看电影"] if i % 8 == 0 else ["今天天气好"])
        tick(conn, sid, texts, idx=IDX2)
    picked = [r["term"] for r in frequency.select_for_promotion(conn, cfg())]
    assert "电影" in picked                    # 5 sightings, but only ~12% ubiquity


def test_ubiquity_threshold_is_configurable(conn):
    sid = store.start_session(conn, "s")
    for i in range(10):
        tick(conn, sid, ["他在看电影"] if i % 2 == 0 else ["今天天气好"], idx=IDX2)
    assert "电影" not in [r["term"] for r in
                          frequency.select_for_promotion(conn, cfg(max_ubiquity=0.35))]
    assert "电影" in [r["term"] for r in
                      frequency.select_for_promotion(conn, cfg(max_ubiquity=0.90))]
