"""Blip deck export tests — the seam into Blip.

The rules Blip imposes are the ones worth pinning: a card must have a meaning, ids
must be unique within the deck, and re-running must not duplicate cards.
"""
import json

from blipfeed import blip_link, frequency, store
from blipfeed.config import Config
from blipfeed.dictionary import Entry
from blipfeed.ocr import OcrLine

XUEXI = Entry("学习", "學習", "xue2 xi2", ("to learn", "to study"))
IDX = {"学习": XUEXI, "學習": XUEXI}


def line(text, conf=0.9):
    return OcrLine(text, conf, (0, 0, 50, 20))


def test_new_deck_has_blip_schema(tmp_path):
    path = tmp_path / "immersion.json"
    deck = blip_link.load_deck(path)
    assert deck["schema"] == 1 and deck["words"] == []


def test_export_writes_a_valid_blip_word(conn, tmp_path):
    path = tmp_path / "immersion.json"
    for _ in range(2):
        frequency.ingest(conn, [line("我喜欢学习")], idx=IDX)
    rows = frequency.select_for_promotion(conn, Config())
    _p, added = blip_link.export(rows, path=path,
                                 contexts_by_term={"学习": ["我喜欢学习"]})
    assert len(added) == 1
    word = added[0]
    assert word["term"] == "学习"
    assert word["meaning"] == "to learn; to study"
    assert word["reading"] == "xué xí"
    assert word["tones"] == "2-2"
    assert word["alt"] == "學習"                    # both scripts on the card
    assert word["note"] == "Seen in: 我喜欢学习"     # mined example sentence
    assert word["cat"] == "common"

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema"] == 1 and saved["language"] == "zh-CN"
    assert saved["words"][0]["term"] == "学习"


def test_a_term_without_meaning_is_never_written(conn, tmp_path):
    """Blip's validation rejects a word with no meaning — don't hand it one."""
    frequency.ingest_line(conn, "师父该入万魂幡了", tokens=["万魂幡"], idx=IDX)
    rows = [store.find_term(conn, "万魂幡")]
    _p, added = blip_link.export(rows, path=tmp_path / "immersion.json")
    assert added == []


def test_ids_are_unique_within_the_deck(conn, tmp_path):
    path = tmp_path / "immersion.json"
    idx = {**IDX, "中文": Entry("中文", "中文", "zhong1 wen2", ("Chinese language",))}
    for term in ("学习", "中文"):
        for _ in range(2):
            frequency.ingest(conn, [line(term)], idx=idx)
    rows = frequency.select_for_promotion(conn, Config(
        {"max_promotions_per_snapshot": 5}))
    _p, added = blip_link.export(rows, path=path)
    ids = [w["id"] for w in added]
    assert len(ids) == len(set(ids)) == 2


def test_reexport_does_not_duplicate(conn, tmp_path):
    path = tmp_path / "immersion.json"
    for _ in range(2):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    rows = frequency.select_for_promotion(conn, Config())
    blip_link.export(rows, path=path)
    _p, again = blip_link.export(rows, path=path)          # same terms again
    assert again == []
    assert len(json.loads(path.read_text(encoding="utf-8"))["words"]) == 1


def test_appends_to_an_existing_deck(conn, tmp_path):
    path = tmp_path / "immersion.json"
    path.write_text(json.dumps({
        "schema": 1, "name": "Immersion", "language": "zh-CN", "voice": None,
        "words": [{"id": "1", "term": "旧词", "meaning": "old word", "cat": "common"}],
    }, ensure_ascii=False), encoding="utf-8")
    for _ in range(2):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    rows = frequency.select_for_promotion(conn, Config())
    blip_link.export(rows, path=path)
    words = json.loads(path.read_text(encoding="utf-8"))["words"]
    assert [w["term"] for w in words] == ["旧词", "学习"]
    assert words[1]["id"] == "2"                            # no id collision


def test_corrupt_deck_does_not_lose_mined_words(conn, tmp_path):
    """Refusing to write because a deck got corrupted would lose the user's words."""
    path = tmp_path / "immersion.json"
    path.write_text("{ not json", encoding="utf-8")
    for _ in range(2):
        frequency.ingest(conn, [line("学习")], idx=IDX)
    rows = frequency.select_for_promotion(conn, Config())
    _p, added = blip_link.export(rows, path=path)
    assert len(added) == 1                                   # started a fresh deck
    assert json.loads(path.read_text(encoding="utf-8"))["words"][0]["term"] == "学习"


def test_promote_and_export_end_to_end(conn, tmp_path):
    """The whole loop: seen twice -> gated -> written as a Blip card -> carded."""
    path = tmp_path / "immersion.json"
    cfg = Config()
    for _ in range(2):
        frequency.ingest(conn, [line("我喜欢学习")], idx=IDX)
    promoted = frequency.promote(conn, cfg,
                                 deck_writer=blip_link.exporter(conn, cfg, path))
    assert [r["term"] for r in promoted] == ["学习"]
    assert store.find_term(conn, "学习")["status"] == "carded"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["words"][0]["note"] == "Seen in: 我喜欢学习"
