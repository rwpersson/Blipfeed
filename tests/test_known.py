"""Known-words tests.

The default matters as much as the machinery: with no data the known-set is empty and
everything surfaces, which is the honest state for a beginner. These tests pin both
that default and the sources that grow the set.
"""
import json

from blipfeed import blip_link, frequency, known, store
from blipfeed.config import Config
from blipfeed.dictionary import Entry
from blipfeed.ocr import OcrLine

XUEXI = Entry("学习", "學習", "xue2 xi2", ("to learn", "to study"))
IDX = {"学习": XUEXI, "學習": XUEXI,
       "电影": Entry("电影", "電影", "dian4 ying3", ("movie",))}


def cfg(**over):
    return Config(over)


def line(text, conf=0.9):
    return OcrLine(text, conf, (0, 0, 50, 20))


def write_deck(dirpath, name, words, language="zh-CN"):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{name}.json").write_text(json.dumps({
        "schema": 1, "name": name, "language": language, "voice": None,
        "words": words}, ensure_ascii=False), encoding="utf-8")


# --------------------------------- the default -------------------------------
def test_beginner_default_knows_nothing(conn):
    """No data == beginner == everything surfaces. A tool that presumes an
    intermediate learner is useless on day one."""
    assert store.known_count(conn) == 0


def test_a_beginner_still_gets_common_words_offered(conn):
    """因为/之后 are noise to an advanced learner and exactly the point for a
    beginner, so they must NOT be filtered by default."""
    sid = store.start_session(conn, "s")
    snap = store.add_snapshot(conn, sid, source="r")
    frequency.ingest(conn, [line("我喜欢学习")], session_id=sid, snapshot_id=snap,
                     idx=IDX)
    for _ in range(4):
        store.add_snapshot(conn, sid, source="r")
    assert "学习" in [c.term for c in
                      frequency.review_queue(conn, cfg(), "ready", tier="primary")]


# --------------------------------- bulk seeding ------------------------------
def test_bulk_seed_marks_terms_known(conn):
    created, _updated = store.seed_known_bulk(conn, ["学习", "电影", "你好"])
    assert created == 3
    assert store.known_count(conn) == 3
    assert store.find_term(conn, "学习")["status"] == "known"


def test_bulk_seed_is_idempotent(conn):
    store.seed_known_bulk(conn, ["学习"])
    created, _ = store.seed_known_bulk(conn, ["学习"])
    assert created == 0
    assert store.known_count(conn) == 1


def test_bulk_seed_promotes_an_existing_new_term(conn):
    frequency.ingest(conn, [line("我喜欢学习")], idx=IDX)
    assert store.find_term(conn, "学习")["status"] == "new"
    store.seed_known_bulk(conn, ["学习"])
    assert store.find_term(conn, "学习")["status"] == "known"


def test_bulk_seed_never_overwrites_a_decision(conn):
    """A bulk import must not quietly undo an individual choice."""
    frequency.ingest(conn, [line("学习")], idx=IDX)
    row = store.find_term(conn, "学习")
    store.mark_carded(conn, row["id"], "immersion", "1")
    store.seed_known_bulk(conn, ["学习"])
    assert store.find_term(conn, "学习")["status"] == "carded"   # untouched


def test_bulk_seed_canonicalizes_scripts(conn, monkeypatch):
    from blipfeed import hanzi
    monkeypatch.setattr(hanzi, "_MAP", {"學": "学", "習": "习"})
    store.seed_known_bulk(conn, ["學習"])                        # traditional in
    assert store.find_term(conn, "学习")["status"] == "known"    # simplified stored


def test_known_terms_never_reach_the_queue(conn):
    sid = store.start_session(conn, "s")
    store.seed_known_bulk(conn, ["学习"])
    for _ in range(5):
        snap = store.add_snapshot(conn, sid, source="r")
        frequency.ingest(conn, [line("我喜欢学习")], session_id=sid,
                         snapshot_id=snap, idx=IDX)
    assert store.find_term(conn, "学习")["count"] == 5     # still counted…
    assert frequency.review_queue(conn, cfg(), "ready") == []   # …never offered


# --------------------------------- Blip sources ------------------------------
def test_from_blip_decks_marks_cards_you_already_have(conn, tmp_path, monkeypatch):
    """The most valuable bulk source: re-mining a word you're already studying just
    mints a duplicate."""
    decks = tmp_path / "decks"
    write_deck(decks, "starter", [{"id": "s01", "term": "你好", "meaning": "hello"},
                                  {"id": "s02", "term": "谢谢", "meaning": "thanks"}])
    monkeypatch.setattr(blip_link, "deck_dir", lambda: decks)
    created, _ = known.from_blip_decks(conn)
    assert created == 2
    assert store.find_term(conn, "你好")["status"] == "known"


def test_deck_terms_tolerates_blips_legacy_field_names(conn, tmp_path, monkeypatch):
    """Blip's own accessors still read the pre-rename 'hanzi', so ours must too."""
    decks = tmp_path / "decks"
    write_deck(decks, "old", [{"id": "1", "hanzi": "朋友", "meaning": "friend"}])
    monkeypatch.setattr(blip_link, "deck_dir", lambda: decks)
    assert "朋友" in blip_link.deck_terms()


def test_from_blip_graduated_uses_exposure_counts(conn, tmp_path, monkeypatch):
    """Closes the loop: mine -> drill -> graduate -> stop resurfacing."""
    decks = tmp_path / "decks"
    write_deck(decks, "week-1", [{"id": "1", "term": "学习", "meaning": "study"},
                                 {"id": "2", "term": "电影", "meaning": "movie"}])
    (tmp_path / "state.json").write_text(json.dumps({
        "schema": 3, "decks": {"week-1": {"words": {
            "1": {"e": 9, "intro": True},      # graduated
            "2": {"e": 3, "intro": True}}}}}), encoding="utf-8")
    monkeypatch.setattr(blip_link, "deck_dir", lambda: decks)
    monkeypatch.setattr(blip_link.paths, "blip_user_dir", lambda: tmp_path)

    graduated = blip_link.graduated_terms(graduate_at=9)
    assert graduated == {"学习"}                       # 电影 is still being learned


def test_graduated_is_empty_without_blip_state(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link.paths, "blip_user_dir", lambda: tmp_path)
    assert blip_link.graduated_terms() == set()


def test_corrupt_blip_state_does_not_crash(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(blip_link.paths, "blip_user_dir", lambda: tmp_path)
    assert blip_link.graduated_terms() == set()


# --------------------------------- list import -------------------------------
def test_import_a_plain_word_list(conn, tmp_path):
    p = tmp_path / "hsk1.txt"
    p.write_text("# HSK 1\n你好\n谢谢\n\n再见\n", encoding="utf-8")
    created, _ = known.from_file(conn, p)
    assert created == 3
    assert store.known_count(conn) == 3


def test_import_a_csv_uses_the_first_column(conn, tmp_path):
    p = tmp_path / "list.csv"
    p.write_text("你好,hello,greeting\n谢谢,thanks,polite\n", encoding="utf-8")
    known.from_file(conn, p)
    assert store.find_term(conn, "你好")["status"] == "known"


def test_import_handles_a_bom(conn, tmp_path):
    p = tmp_path / "bom.txt"
    p.write_bytes("﻿你好\n".encode("utf-8"))
    known.from_file(conn, p)
    assert store.find_term(conn, "你好") is not None


def test_import_scales(conn, tmp_path):
    """A level list is thousands of words — this must be one transaction, not N."""
    terms = [f"词{i}" for i in range(3000)]
    p = tmp_path / "big.txt"
    p.write_text("\n".join(terms), encoding="utf-8")
    created, _ = known.from_file(conn, p)
    assert created == 3000


# --------------------------------- undo --------------------------------------
def test_forget_puts_a_word_back_in_play(conn):
    store.seed_known_bulk(conn, ["学习"])
    assert known.forget(conn, "学习")
    assert store.find_term(conn, "学习")["status"] == "new"


def test_forget_an_unknown_term_is_not_an_error(conn):
    assert known.forget(conn, "没有这个词") is False
