"""Enrichment round-trip tests.

The paste-back parser is the risky half: whatever comes back has been through a chat
model and a clipboard, so it may carry markdown, numbering, smart quotes or stray
commentary. It must recover what it can and *report* what it can't.
"""
from blipfeed import enrich, frequency, store
from blipfeed.ocr import OcrLine


def line(text, conf=0.9):
    return OcrLine(text, conf, (0, 0, 50, 20))


def seed(conn, term, context="师父该入万魂幡了"):
    frequency.ingest_line(conn, context, tokens=[term])
    return store.find_term(conn, term)


# ----------------------------------- export ----------------------------------
def test_export_includes_instructions_reading_and_context(conn):
    row = seed(conn, "万魂幡")
    text = enrich.export_text(conn, [row])
    assert "ONE LINE PER TERM" in text            # the model needs telling
    assert "万魂幡" in text
    assert "wàn hún fān" in text                  # pypinyin works on a CEDICT miss
    assert "师父该入万魂幡了" in text               # context is what makes it definable
    assert "seen 1×" in text


def test_export_can_omit_the_prompt(conn):
    row = seed(conn, "万魂幡")
    assert "ONE LINE PER TERM" not in enrich.export_text(conn, [row], prompt=False)


def test_export_accepts_queue_candidates(conn):
    """The UI passes Candidate objects, not raw rows."""
    seed(conn, "万魂幡")
    from blipfeed.config import Config
    queue = frequency.review_queue(conn, Config(), "needs_def")
    assert "万魂幡" in enrich.export_text(conn, queue)


def test_export_of_nothing_is_harmless(conn):
    assert enrich.export_text(conn, []).strip() != ""      # still shows the prompt


# ----------------------------------- parse -----------------------------------
def test_parses_the_requested_format():
    rows, skipped = enrich.parse_enriched(
        "万魂幡\ta soul-gathering banner\txianxia term\n"
        "婚姻危机\tmarital crisis\tcommon in drama titles\n")
    assert rows == [("万魂幡", "a soul-gathering banner", "xianxia term"),
                    ("婚姻危机", "marital crisis", "common in drama titles")]
    assert skipped == []


def test_parses_without_a_note():
    rows, _ = enrich.parse_enriched("万魂幡\ta soul banner")
    assert rows == [("万魂幡", "a soul banner", "")]


def test_strips_model_list_numbering_and_markdown():
    rows, _ = enrich.parse_enriched(
        "1. **万魂幡**\t`a soul banner`\txianxia\n"
        "- 婚姻危机\tmarital crisis\tdrama\n")
    assert rows == [("万魂幡", "a soul banner", "xianxia"),
                    ("婚姻危机", "marital crisis", "drama")]


def test_parses_a_markdown_table():
    rows, _ = enrich.parse_enriched("| 万魂幡 | a soul banner | xianxia |")
    assert rows == [("万魂幡", "a soul banner", "xianxia")]


def test_parses_aligned_columns_without_tabs():
    rows, _ = enrich.parse_enriched("万魂幡    a soul banner    xianxia term")
    assert rows == [("万魂幡", "a soul banner", "xianxia term")]


def test_unparseable_lines_are_reported_not_swallowed():
    """Silently dropping half a paste would be worse than refusing it."""
    rows, skipped = enrich.parse_enriched(
        "Sure! Here are the definitions:\n"
        "万魂幡\ta soul banner\txianxia\n"
        "Let me know if you want more.\n")
    assert rows == [("万魂幡", "a soul banner", "xianxia")]
    assert len(skipped) == 2
    assert "Sure!" in skipped[0]


def test_blank_input_parses_to_nothing():
    assert enrich.parse_enriched("") == ([], [])
    assert enrich.parse_enriched("   \n\n  ") == ([], [])


# ----------------------------------- apply -----------------------------------
def test_apply_fills_meaning_and_moves_bucket(conn):
    from blipfeed.config import Config
    row = seed(conn, "万魂幡")
    assert row["meaning"] is None
    applied, unmatched = enrich.apply_enriched(
        conn, [("万魂幡", "a soul-gathering banner", "xianxia term")])
    assert (applied, unmatched) == (1, [])
    updated = store.find_term(conn, "万魂幡")
    assert updated["meaning"] == "a soul-gathering banner"
    assert updated["note"] == "xianxia term"
    assert updated["enriched_at"]
    # now cardable: it left needs-a-definition for ready
    assert [c.term for c in frequency.review_queue(conn, Config(), "ready")] \
        == ["万魂幡"]
    assert frequency.review_queue(conn, Config(), "needs_def") == []


def test_apply_reports_terms_we_never_mined(conn):
    """A hallucinated term must not create a row."""
    seed(conn, "万魂幡")
    applied, unmatched = enrich.apply_enriched(
        conn, [("不存在的词", "made up", "")])
    assert applied == 0 and unmatched == ["不存在的词"]
    assert store.find_term(conn, "不存在的词") is None


def test_apply_matches_either_script(conn, monkeypatch):
    from blipfeed import hanzi
    monkeypatch.setattr(hanzi, "_MAP", {"學": "学", "習": "习"})
    frequency.ingest_line(conn, "我喜欢学习", tokens=["学习"])
    applied, _ = enrich.apply_enriched(conn, [("學習", "to study", "")])
    assert applied == 1                                   # traditional reply matched
    assert store.find_term(conn, "学习")["meaning"] == "to study"


def test_round_trip(conn):
    """Export, 'answer' it, paste back — the whole loop."""
    seed(conn, "万魂幡")
    exported = enrich.export_text(conn, [store.find_term(conn, "万魂幡")])
    assert "万魂幡" in exported
    reply = "万魂幡\ta soul-gathering banner\tfrom xianxia fiction"
    rows, skipped = enrich.parse_enriched(reply)
    applied, unmatched = enrich.apply_enriched(conn, rows)
    assert (applied, unmatched, skipped) == (1, [], [])
    assert store.find_term(conn, "万魂幡")["meaning"] == "a soul-gathering banner"
