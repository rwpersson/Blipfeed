"""Deck lifecycle — the auto-cap roll and the graduate/restore seam.

Which deck is "active" (where new cards land) is **derived from the deck files**, not
cached in config: Blipfeed runs several processes and a cached value got clobbered,
leaving `active_deck_id` pointing at a graduated deck. These tests pin the file-derived
behaviour, especially the rule that it can *never* return an archived deck.

The `archived` flag is the seam Blip reads/writes — docs/DECK_ARCHIVE_CONTRACT.md.
"""
import os

from blipfeed import blip_link, store
from blipfeed.config import Config
from blipfeed.review import ReviewController


def cfg_at(tmp_path, **over):
    return Config(over, path=tmp_path / "config.json")


def make_deck(deck_id, n, *, archived=False, mtime=None):
    words = [{"id": str(i), "term": f"词{i:03d}", "meaning": "m"} for i in range(n)]
    deck = {"name": deck_id, "words": words}
    if archived:
        deck["archived"] = True
    p = blip_link.write_deck(deck, blip_link.deck_path(deck_id))
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


# ------------------------------- the archived flag ---------------------------
def test_archived_flag_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    p = blip_link.deck_path("immersion")
    blip_link.write_deck({"name": "x", "words": [], "archived": True}, p)
    assert blip_link._read_deck(p)["archived"] is True
    blip_link.write_deck({"name": "x", "words": []}, p)     # active writes no flag
    assert "archived" not in blip_link._read_deck(p)


def test_default_name_is_distinct_per_deck():
    assert blip_link._default_name("immersion") == "Immersion"
    assert blip_link._default_name("immersion-2026-07-22-2") == "Immersion · 2026-07-22-2"


# ------------------------------ active deck (derived) ------------------------
def test_active_deck_defaults_to_base_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    assert blip_link.active_deck_id(cfg_at(tmp_path)) == "immersion"


def test_active_deck_is_the_live_deck_under_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion", 2)
    assert blip_link.active_deck_id(cfg_at(tmp_path, max_deck_cards=3)) == "immersion"


def test_active_deck_rolls_past_a_full_deck(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion", 3)                              # at the cap
    active = blip_link.active_deck_id(cfg_at(tmp_path, max_deck_cards=3))
    assert active != "immersion"                          # a fresh deck takes new cards


def test_active_deck_NEVER_returns_an_archived_deck(tmp_path, monkeypatch):
    """The bug this whole rewrite fixes: a graduated deck must never be the write
    target, and must never be flagged the active one."""
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion", 196, archived=True)            # graduated
    cfg = cfg_at(tmp_path)
    assert blip_link.active_deck_id(cfg) != "immersion"   # -> a fresh, live deck
    # and with a live sibling present, that live one is chosen
    make_deck("immersion-2026-07-22", 3)
    assert blip_link.active_deck_id(cfg) == "immersion-2026-07-22"


def test_active_deck_prefers_the_most_recently_written_live_deck(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion-2026-07-22", 3, mtime=1000)
    make_deck("immersion-2026-07-22-2", 1, mtime=2000)    # newer
    assert blip_link.active_deck_id(cfg_at(tmp_path)) == "immersion-2026-07-22-2"


def test_live_deck_ids_excludes_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion", 5, archived=True)
    make_deck("immersion-2026-07-22", 3)
    make_deck("immersion-2026-07-22-2", 1)
    assert blip_link.live_deck_ids() == {"immersion-2026-07-22", "immersion-2026-07-22-2"}


# ------------------------------ graduate / restore ---------------------------
def test_graduate_archives_and_active_moves_on(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion", 5)
    cfg = cfg_at(tmp_path)
    assert blip_link.active_deck_id(cfg) == "immersion"
    assert blip_link.graduate("immersion") is True
    assert blip_link._read_deck(blip_link.deck_path("immersion"))["archived"] is True
    assert "immersion" in blip_link.archived_deck_ids()
    assert blip_link.active_deck_id(cfg) != "immersion"   # new cards go elsewhere now


def test_restore_puts_a_deck_back_into_review(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion-2026-01-01", 5, archived=True)
    assert blip_link.restore("immersion-2026-01-01") is True
    assert "immersion-2026-01-01" not in blip_link.archived_deck_ids()


def test_graduate_a_nonexistent_deck_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    assert blip_link.graduate("nope") is False


# --------------------------------- list_decks --------------------------------
def test_list_decks_reports_family_with_correct_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    make_deck("immersion", 196, archived=True)            # graduated
    make_deck("immersion-2026-07-22", 33)                 # live
    make_deck("hsk1", 2)                                  # hand-made Blip deck — not ours
    decks = {d["id"]: d for d in blip_link.list_decks(cfg_at(tmp_path))}
    assert set(decks) == {"immersion", "immersion-2026-07-22"}
    assert decks["immersion"]["archived"] is True
    assert decks["immersion"]["active"] is False          # graduated -> never active
    assert decks["immersion-2026-07-22"]["active"] is True   # the live write-target
    assert decks["immersion-2026-07-22"]["name"] == "Immersion · 2026-07-22"


# ---------------------------- promotion / carding seam -----------------------
def test_exporter_writes_into_the_active_live_deck(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    conn = store.connect(":memory:")
    try:
        cfg = cfg_at(tmp_path, max_deck_cards=3)
        make_deck("immersion", 3, archived=True)          # full AND graduated
        tid = store.observe(conn, term="学习",
                            create_fields={"meaning": "to study"})
        conn.commit()
        active = blip_link.active_deck_id(cfg)
        assert active != "immersion"                      # not the graduated/full deck
        blip_link.exporter(conn, cfg)([store.get_term(conn, tid)])
        deck = blip_link._read_deck(blip_link.deck_path(active))
        assert any(w["term"] == "学习" for w in deck["words"])
    finally:
        conn.close()


def test_accept_then_uncard_target_the_deck_it_landed_in(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path)                                 # empty dir -> active = immersion
    tid = store.observe(conn, term="学习", create_fields={"meaning": "to study"})
    conn.commit()
    c = ReviewController(conn, cfg)
    c.accept(tid)
    row = store.get_term(conn, tid)
    assert row["card_deck"] == "immersion"
    c.uncard(tid)
    deck = blip_link._read_deck(blip_link.deck_path("immersion"))
    assert all(w["term"] != "学习" for w in deck["words"])
    assert store.get_term(conn, tid)["status"] == "ignored"
