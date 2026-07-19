"""Config tests — defaults, persistence, and the invariants validate() must catch.

The important one is `furniture_threshold <= min_count`: exceed it and UI chrome
becomes cardable, silently breaking the product. A documented invariant nothing
checks is folklore, so it's tested here.
"""
from blipfeed import config as config_mod
from blipfeed.capture import Region
from blipfeed.config import Config

DEFAULT_N = config_mod.DEFAULTS["min_count"]


def test_defaults_are_present_and_valid():
    cfg = Config()
    assert cfg.min_count == 2
    assert cfg.max_promotions_per_snapshot == 2
    assert cfg.interval_seconds == 30
    assert cfg.mode == "auto"
    assert cfg.validate() == []


def test_attribute_and_item_access():
    cfg = Config({"min_count": 5})
    assert cfg.min_count == 5 and cfg["min_count"] == 5
    assert "min_count" in cfg
    assert cfg.get("nope", "fallback") == "fallback"


def test_unknown_attribute_raises():
    cfg = Config()
    try:
        cfg.definitely_not_a_key
    except AttributeError:
        pass
    else:
        raise AssertionError("expected AttributeError")


def test_missing_file_falls_back_to_defaults(tmp_path):
    cfg = config_mod.load(tmp_path / "nope.json")
    assert cfg.min_count == DEFAULT_N
    assert cfg.validate() == []


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    Config({"min_count": 4, "auto_card": False}).save(p)
    cfg = config_mod.load(p)
    assert cfg.min_count == 4 and cfg.auto_card is False


def test_unknown_keys_are_preserved(tmp_path):
    """A file written by a newer version must survive a round-trip."""
    p = tmp_path / "config.json"
    Config({"future_knob": "keep me"}).save(p)
    assert config_mod.load(p).get("future_knob") == "keep me"


def test_unreadable_file_falls_back(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    assert config_mod.load(p).min_count == DEFAULT_N     # logged, not crashed


def test_non_object_json_falls_back(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert config_mod.load(p).min_count == DEFAULT_N


def test_region_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    Config().set_region(Region(10, 20, 300, 400)).save(p)
    r = config_mod.load(p).region()
    assert (r.left, r.top, r.width, r.height) == (10, 20, 300, 400)


def test_region_unset_is_none():
    assert Config().region() is None


def test_malformed_region_is_ignored_not_fatal():
    assert Config({"region": {"left": 1}}).region() is None


def test_validate_catches_a_disabled_ubiquity_filter():
    """max_ubiquity is now the only thing keeping UI chrome out of the deck, so
    switching it off should say so rather than be discovered later in your deck."""
    problems = Config({"max_ubiquity": 1.0}).validate()
    assert any("max_ubiquity" in p and "chrome" in p for p in problems)


def test_validate_catches_bad_count_mode():
    assert any("count_mode" in p for p in Config({"count_mode": "wat"}).validate())


def test_furniture_threshold_no_longer_gates_auto_carding():
    """It only affects MANUAL mode's chip de-cluttering now, so a high value is not
    a problem the way it was when it (wrongly) guarded promotion."""
    assert Config({"furniture_threshold": 5, "min_count": 2}).validate() == []


def test_validate_catches_out_of_range():
    assert any("interval_seconds" in p
               for p in Config({"interval_seconds": 999}).validate())
    assert any("min_confidence" in p
               for p in Config({"min_confidence": 1.5}).validate())


def test_validate_catches_bad_mode():
    assert any("mode" in p for p in Config({"mode": "sideways"}).validate())


def test_validate_rejects_bool_as_number():
    """True == 1 in Python; a bool in a numeric knob is a config error, not a 1."""
    assert any("min_count" in p for p in Config({"min_count": True}).validate())
