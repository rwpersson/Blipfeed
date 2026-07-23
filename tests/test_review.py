"""Review-controller tests — all the logic, no Tk.

The window is a dumb shell over this, so this is where the review behaviour is
actually guaranteed: the buckets, the actions, and the manual-AI round trip.
"""
import json

from blipfeed import frequency, store
from blipfeed.config import Config
from blipfeed.dictionary import Entry
from blipfeed.ocr import OcrLine
from blipfeed.review import ReviewController

XUEXI = Entry("学习", "學習", "xue2 xi2", ("to learn", "to study"))
IDX = {"学习": XUEXI, "學習": XUEXI}


def line(text, conf=0.9):
    return OcrLine(text, conf, (0, 0, 50, 20))


def tick(conn, sid, texts, idx=None):
    snap = store.add_snapshot(conn, sid, source="r")
    frequency.ingest(conn, [line(t) for t in texts], session_id=sid,
                     snapshot_id=snap, idx=idx or IDX)
    return snap


def ctrl(conn, deck_path=None, **over):
    cfg = Config(over)
    if deck_path is not None:
        cfg.set("blip_deck_id", "immersion")
    c = ReviewController(conn, cfg)
    return c


def seed_slang(conn, term="无畏", context="无畏契约真好玩"):
    """A dictionary-miss that reaches the needs-def PRIMARY tier.

    Real content, so it must be a *minority* of snapshots (df>=2 to clear the
    unverified bar, but low ubiquity so it isn't judged ambient). Filler snapshots
    carry unrelated text and give the ratio a denominator.
    """
    sid = store.start_session(conn, "s")
    tick(conn, sid, [context], idx={})         # empty idx -> a dictionary miss
    tick(conn, sid, [context], idx={})         # seen in 2 snapshots
    for filler in ("天气好", "看书吧", "去公园", "真好听", "见面吧", "下雨了"):
        tick(conn, sid, [filler], idx={})
    return store.find_term(conn, term)


# --------------------------------- buckets -----------------------------------
def test_needs_definition_lists_undefined_words(conn):
    seed_slang(conn, "无畏", "无畏契约真好玩")
    c = ReviewController(conn, Config())
    terms = [x.term for x in c.needs_definition() if x.tier == "primary"]
    assert "无畏" in terms
    row = next(x for x in c.needs_definition() if x.term == "无畏")
    assert row.meaning is None
    # contexts are fetched lazily (per drawn row), not carried on every candidate
    assert c.contexts_for(row.id) == ["无畏契约真好玩"]


def test_ready_lists_defined_words(conn):
    sid = store.start_session(conn, "s")
    tick(conn, sid, ["我喜欢学习"], idx=IDX)
    for _ in range(4):
        tick(conn, sid, ["今天天气好"], idx=IDX)
    ready = [x.term for x in ReviewController(conn, Config()).ready()
             if x.tier == "primary"]
    assert "学习" in ready


def test_counts_summarize_the_queue(conn):
    seed_slang(conn, "无畏", "无畏契约真好玩")
    counts = ReviewController(conn, Config()).counts()
    assert counts["needs_def"][0] >= 1              # at least one primary
    assert counts["known"] == 0


# --------------------------------- actions -----------------------------------
def test_ignore_removes_from_the_queue(conn):
    row = seed_slang(conn, "无畏", "无畏契约真好玩")
    c = ReviewController(conn, Config())
    c.ignore(row["id"])
    assert store.get_term(conn, row["id"])["status"] == "ignored"
    assert "无畏" not in [x.term for x in c.needs_definition()]


def test_mark_known_removes_from_the_queue(conn):
    row = seed_slang(conn, "无畏", "无畏契约真好玩")
    c = ReviewController(conn, Config())
    c.mark_known(row["id"])
    assert store.get_term(conn, row["id"])["status"] == "known"


def test_set_meaning_moves_needs_def_to_ready(conn):
    row = seed_slang(conn, "无畏", "无畏契约真好玩")
    c = ReviewController(conn, Config())
    c.set_meaning(row["id"], "Valorant (the game)", note="a shooter")
    assert "无畏" not in [x.term for x in c.needs_definition()]
    ready = {x.term: x for x in c.ready()}
    assert ready["无畏"].meaning == "Valorant (the game)"
    assert ready["无畏"].note == "a shooter"


def test_accept_writes_a_card_and_marks_carded(conn, tmp_path, monkeypatch):
    from blipfeed import blip_link
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    sid = store.start_session(conn, "s")
    tick(conn, sid, ["我喜欢学习"], idx=IDX)
    row = store.find_term(conn, "学习")
    ReviewController(conn, Config()).accept(row["id"])
    assert store.get_term(conn, row["id"])["status"] == "carded"
    deck = json.loads((tmp_path / "immersion.json").read_text(encoding="utf-8"))
    assert deck["words"][0]["term"] == "学习"


def test_accept_refuses_without_a_meaning(conn):
    row = seed_slang(conn, "无畏", "无畏契约真好玩")
    try:
        ReviewController(conn, Config()).accept(row["id"])
    except ValueError:
        pass
    else:
        raise AssertionError("expected a refusal to card a meaningless term")


def test_uncard_removes_from_deck_and_requeues(conn, tmp_path, monkeypatch):
    from blipfeed import blip_link
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    sid = store.start_session(conn, "s")
    tick(conn, sid, ["我喜欢学习"], idx=IDX)
    row = store.find_term(conn, "学习")
    c = ReviewController(conn, Config())
    c.accept(row["id"])
    c.uncard(row["id"])
    assert store.get_term(conn, row["id"])["status"] == "ignored"
    deck = json.loads((tmp_path / "immersion.json").read_text(encoding="utf-8"))
    assert deck["words"] == []                       # pulled back out of Blip


# ------------------------------ the manual-AI loop ---------------------------
def test_export_for_ai_includes_term_reading_freq_context(conn):
    seed_slang(conn, "无畏", "无畏契约真好玩")
    text = ReviewController(conn, Config()).export_for_ai()
    assert "无畏" in text
    assert "wú wèi" in text                          # reading, from pypinyin
    assert "无畏契约真好玩" in text                    # a real context sentence
    assert "term" in text and "meaning" in text      # the reply-format instruction


def test_export_is_empty_when_nothing_needs_defining(conn):
    assert ReviewController(conn, Config()).export_for_ai() == ""


def test_import_enriched_fills_meanings(conn):
    row = seed_slang(conn, "无畏", "无畏契约真好玩")
    reply = "无畏\tValorant\ta tactical shooter\n"
    updated, unmatched = ReviewController(conn, Config()).import_enriched(reply)
    assert updated == 1 and unmatched == []
    t = store.get_term(conn, row["id"])
    assert t["meaning"] == "Valorant" and t["note"] == "a tactical shooter"


def test_import_enriched_is_forgiving(conn):
    seed_slang(conn, "无畏", "无畏契约真好玩")
    seed_slang(conn, "二发", "二发已经准备好")
    reply = (
        "Here are the definitions:\n"           # a header line -> skipped
        "\n"
        "- 无畏\tfearless\t\n"                    # bullet + empty note
        "二发     second shot     a game term\n"  # spaces instead of a tab
        "不存在\tnonexistent\t\n"                 # not in the store -> unmatched
    )
    updated, unmatched = ReviewController(conn, Config()).import_enriched(reply)
    assert updated == 2
    assert unmatched == ["不存在"]
    assert store.find_term(conn, "无畏")["meaning"] == "fearless"
    assert store.find_term(conn, "二发")["note"] == "a game term"


def test_import_enriched_matches_either_script(conn, monkeypatch):
    from blipfeed import hanzi
    monkeypatch.setattr(hanzi, "_MAP", {"學": "学", "習": "习"})
    sid = store.start_session(conn, "s")
    for _ in range(2):
        tick(conn, sid, ["学习"], idx={})           # stored simplified, no meaning
    reply = "學習\tto study\t\n"                     # traditional in the reply
    updated, _ = ReviewController(conn, Config()).import_enriched(reply)
    assert updated == 1
    assert store.find_term(conn, "学习")["meaning"] == "to study"


def test_enrich_then_accept_round_trip(conn, tmp_path, monkeypatch):
    """The whole slang path: mined -> defined by AI -> carded into Blip."""
    from blipfeed import blip_link
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    row = seed_slang(conn, "无畏", "无畏契约真好玩")
    c = ReviewController(conn, Config())
    c.import_enriched("无畏\tValorant\ttactical shooter\n")
    c.accept(row["id"])
    deck = json.loads((tmp_path / "immersion.json").read_text(encoding="utf-8"))
    w = deck["words"][0]
    assert w["term"] == "无畏" and w["meaning"] == "Valorant"
    assert w["reading"] == "wú wèi"                  # reading rode along


# --- the new tabs: session / manual / ignored / known / search / dates -------
def make_term(conn, term, *, meaning=None, status=None, manual=False, sid=None):
    snap = store.add_snapshot(conn, sid, source="r") if sid else None
    tid = store.observe(conn, term=term, session_id=sid, snapshot_id=snap,
                        create_fields={"meaning": meaning} if meaning else None,
                        manual=manual)
    if status:
        store.set_status(conn, tid, status)
        conn.commit()
    return tid


def test_ignored_and_known_tabs(conn):
    make_term(conn, "无畏", status="ignored")
    make_term(conn, "你好", meaning="hello", status="known")
    c = ReviewController(conn, Config())
    assert [x.term for x in c.ignored()] == ["无畏"]
    assert [x.term for x in c.known()] == ["你好"]
    assert c.ignored()[0].status == "ignored"


def test_revive_puts_word_back(conn):
    tid = make_term(conn, "无畏", status="ignored")
    c = ReviewController(conn, Config())
    c.revive(tid)
    assert store.get_term(conn, tid)["status"] == "new"
    assert c.ignored() == []


def test_card_from_ignored(conn, tmp_path, monkeypatch):
    from blipfeed import blip_link
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    tid = make_term(conn, "无畏", meaning="Valorant", status="ignored")
    ReviewController(conn, Config()).accept(tid)     # card straight from ignored
    assert store.get_term(conn, tid)["status"] == "carded"


def test_carded_tab_flags_and_leads_with_active_deck(conn):
    """Carded cards in Blipfeed's active deck are flagged and sorted ahead of cards in
    rolled/graduated decks — even ones seen more often."""
    a = make_term(conn, "修仙", meaning="cultivate immortality")
    b = make_term(conn, "灵石", meaning="spirit stone")
    for _ in range(5):
        store.observe(conn, term="灵石")            # the non-active card is seen MORE
    store.mark_carded(conn, a, "immersion", str(a))              # the active deck
    store.mark_carded(conn, b, "immersion-2026-01-01", str(b))   # a rolled/old deck
    conn.commit()

    cards = ReviewController(conn, Config()).carded()            # active_deck -> immersion
    by = {c.term: c for c in cards}
    assert by["修仙"].active is True
    assert by["灵石"].active is False
    assert cards[0].term == "修仙"                  # active leads despite lower count


def test_manual_tab(conn):
    make_term(conn, "万魂幡", manual=True)
    make_term(conn, "学习")                           # auto
    assert [x.term for x in ReviewController(conn, Config()).manual_terms()] == ["万魂幡"]


def test_latest_session_tab(conn):
    s1 = store.start_session(conn, "old")
    make_term(conn, "学习", sid=s1)
    s2 = store.start_session(conn, "new")
    make_term(conn, "中文", meaning="Chinese", sid=s2)
    make_term(conn, "电影", status="ignored", sid=s2)
    terms = {x.term for x in ReviewController(conn, Config()).latest_session()}
    assert terms == {"中文", "电影"}                   # every status from the latest sitting


def test_status_tabs_lead_with_content_not_chrome(conn):
    """The Session/status tabs must not open on ambient chrome. A real word sorts
    ahead of an ambient one and a single character, which drop to the secondary tail
    (the window's collapsed 'filtered out' section) — but nothing is discarded."""
    sid = store.start_session(conn, "s")
    snaps = [store.add_snapshot(conn, sid, source="r") for _ in range(8)]

    def obs(term, snap, **cf):
        store.observe(conn, term=term, session_id=sid, snapshot_id=snap,
                      create_fields=cf or None)

    for i, sn in enumerate(snaps):
        obs("抖音", sn, dict_ok=1, meaning="Douyin")     # ambient: in every snapshot
        if i < 3:
            obs("集", sn)                                 # single character
    obs("修仙", snaps[0], dict_ok=1, meaning="cultivate immortality")  # content, 1 snap
    conn.commit()

    sess = ReviewController(conn, Config()).latest_session()
    tier = {x.term: x.tier for x in sess}
    assert tier["修仙"] == "primary"
    assert tier["抖音"] == "secondary"                    # ambient -> collapsed tail
    assert tier["集"] == "secondary"                      # single character
    order = [x.term for x in sess]
    assert order.index("修仙") < order.index("抖音")       # content leads
    assert order.index("修仙") < order.index("集")
    assert {"修仙", "抖音", "集"} <= set(order)             # nothing dropped


def test_search_tab(conn):
    make_term(conn, "学习", meaning="to study")
    make_term(conn, "中文", meaning="Chinese")
    c = ReviewController(conn, Config())
    assert [x.term for x in c.search("学")] == ["学习"]
    assert c.search("") == []                          # empty -> nothing


def test_date_filter_across_tabs(conn):
    sid = store.start_session(conn, "s")
    snap = store.add_snapshot(conn, sid, source="r")
    store.observe(conn, term="无畏", session_id=sid, snapshot_id=snap)
    store.set_status(conn, store.find_term(conn, "无畏")["id"], "ignored")
    conn.commit()
    c = ReviewController(conn, Config())
    today = c.available_dates()[0]
    assert [x.term for x in c.ignored(dates=[today])] == ["无畏"]
    assert c.ignored(dates=["1999-01-01"]) == []       # a day with no data
