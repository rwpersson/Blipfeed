"""Deck lifecycle — the auto-cap roll and the graduate/restore seam.

Two operations that are easy to conflate and are tested apart here:

- **roll** (auto-cap): the active deck fills, so new cards start a fresh deck; the full
  one stays *active* (Blip keeps reviewing it). Only stops a single file ballooning.
- **graduate** (manual): retire a learned batch from Blip by setting the shared
  `archived` flag; it stays in Blipfeed's carded list.

The `archived` flag is the seam Blip reads/writes — docs/DECK_ARCHIVE_CONTRACT.md.
"""
from blipfeed import blip_link, store
from blipfeed.config import Config
from blipfeed.review import ReviewController


def cfg_at(tmp_path, **over):
    """A config whose save() lands in tmp_path, not the real config file."""
    return Config(over, path=tmp_path / "config.json")


def make_deck(deck_id, n, archived=False):
    words = [{"id": str(i), "term": f"词{i:03d}", "meaning": "m"} for i in range(n)]
    deck = {"name": deck_id, "words": words}
    if archived:
        deck["archived"] = True
    blip_link.write_deck(deck, blip_link.deck_path(deck_id))


# ------------------------------- the archived flag ---------------------------
def test_archived_flag_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    p = blip_link.deck_path("immersion")
    blip_link.write_deck({"name": "x", "words": [], "archived": True}, p)
    assert blip_link._read_deck(p)["archived"] is True
    # an active deck writes no archived key at all — absence means live
    blip_link.write_deck({"name": "x", "words": []}, p)
    assert "archived" not in blip_link._read_deck(p)


# ------------------------------ active deck + roll ---------------------------
def test_active_deck_defaults_to_base_then_follows_config(tmp_path):
    cfg = cfg_at(tmp_path)
    assert blip_link.active_deck_id(cfg) == "immersion"
    cfg.set("active_deck", "immersion-2026-07-22")
    assert blip_link.active_deck_id(cfg) == "immersion-2026-07-22"


def test_roll_starts_a_fresh_deck_when_full(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path, max_deck_cards=3)
    make_deck("immersion", 3)                        # already at the cap
    new_id = blip_link.roll_if_full(cfg)
    assert new_id != "immersion"
    assert cfg.get("active_deck") == new_id
    # the full deck is left ACTIVE — auto-cap only splits files, never archives
    assert not (blip_link._read_deck(blip_link.deck_path("immersion")) or {}).get(
        "archived")


def test_roll_is_a_noop_under_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path, max_deck_cards=3)
    make_deck("immersion", 2)
    assert blip_link.roll_if_full(cfg) == "immersion"
    assert cfg.get("active_deck") is None            # nothing rolled, config untouched


# ------------------------------ graduate / restore ---------------------------
def test_graduate_archives_and_rolls_active_away(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path)
    make_deck("immersion", 5)
    assert blip_link.graduate(cfg, "immersion") is True
    assert blip_link._read_deck(blip_link.deck_path("immersion"))["archived"] is True
    assert "immersion" in blip_link.archived_deck_ids()
    # new cards must not fall into the deck we just retired
    active = cfg.get("active_deck")
    assert active and active != "immersion"


def test_restore_puts_a_deck_back_into_review(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path)
    make_deck("immersion-2026-01-01", 5, archived=True)
    assert blip_link.restore(cfg, "immersion-2026-01-01") is True
    assert "immersion-2026-01-01" not in blip_link.archived_deck_ids()


def test_graduate_a_nonexistent_deck_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path)
    assert blip_link.graduate(cfg, "nope") is False


# --------------------------------- list_decks --------------------------------
def test_list_decks_reports_the_immersion_family_only(tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path)
    make_deck("immersion", 3)
    make_deck("immersion-2026-01-01", 5, archived=True)
    make_deck("hsk1", 2)                              # a hand-made Blip deck — not ours
    decks = {d["id"]: d for d in blip_link.list_decks(cfg)}
    assert set(decks) == {"immersion", "immersion-2026-01-01"}
    assert decks["immersion"] == {"id": "immersion", "name": "immersion", "count": 3,
                                  "archived": False, "active": True}
    assert decks["immersion-2026-01-01"]["archived"] is True
    assert decks["immersion-2026-01-01"]["active"] is False


# ---------------------------- promotion / carding seam -----------------------
def test_exporter_rolls_and_active_id_agrees(tmp_path, monkeypatch):
    """When the active deck is full, the exporter writes into a fresh deck and
    active_deck_id() returns that same id — the one promote's mark_carded records."""
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)

    def make_conn():
        return store.connect(":memory:")

    conn = make_conn()
    try:
        cfg = cfg_at(tmp_path, max_deck_cards=1)
        make_deck("immersion", 1)                    # active deck already at cap
        tid = store.observe(conn, term="学习",
                            create_fields={"meaning": "to study"})
        conn.commit()
        blip_link.exporter(conn, cfg)([store.get_term(conn, tid)])
        new_id = cfg.get("active_deck")
        assert new_id and new_id != "immersion"
        assert blip_link.active_deck_id(cfg) == new_id      # what mark_carded uses
        deck = blip_link._read_deck(blip_link.deck_path(new_id))
        assert any(w["term"] == "学习" for w in deck["words"])
        # the pre-roll deck was left untouched (still just its one filler card)
        assert blip_link.deck_card_count(blip_link.deck_path("immersion")) == 1
    finally:
        conn.close()


def test_accept_then_uncard_targets_the_rolled_deck(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(blip_link, "deck_dir", lambda: tmp_path)
    cfg = cfg_at(tmp_path, max_deck_cards=1)
    make_deck("immersion", 1)                         # force accept() to roll
    tid = store.observe(conn, term="学习", create_fields={"meaning": "to study"})
    conn.commit()
    c = ReviewController(conn, cfg)
    c.accept(tid)
    row = store.get_term(conn, tid)
    assert row["card_deck"] != "immersion"            # carded into the rolled deck
    c.uncard(tid)                                     # must pull from THAT deck
    deck = blip_link._read_deck(blip_link.deck_path(row["card_deck"]))
    assert all(w["term"] != "学习" for w in deck["words"])
    assert store.get_term(conn, tid)["status"] == "ignored"
