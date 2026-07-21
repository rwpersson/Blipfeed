"""Settings apply/validate tests — the pure logic behind the form.

A settings form is where a typo'd key or a bad value silently does nothing or corrupts
the config, so the coercion + validation is tested directly (Blip's lesson).
"""
from blipfeed import settings
from blipfeed.config import Config


def cfg(tmp_path, **over):
    c = Config(over)
    c.path = tmp_path / "config.json"
    return c


def test_valid_values_coerce_and_save(tmp_path):
    c = cfg(tmp_path)
    new, problems = settings.apply(c, {
        "interval_seconds": "45", "min_count": "3", "max_ubiquity": "0.4",
        "auto_card": False, "hotkey_toggle": "ctrl+alt+g"})
    assert problems == []
    assert new.interval_seconds == 45 and isinstance(new.interval_seconds, int)
    assert new.min_count == 3
    assert new.max_ubiquity == 0.4
    assert new.auto_card is False
    # persisted
    from blipfeed import config as config_mod
    assert config_mod.load(c.path).interval_seconds == 45


def test_a_non_number_is_reported_not_saved(tmp_path):
    c = cfg(tmp_path)
    new, problems = settings.apply(c, {"interval_seconds": "soon"})
    assert new is None
    assert any("interval" in p.lower() for p in problems)
    assert not c.path.exists()                     # nothing written


def test_out_of_range_is_caught_by_validate(tmp_path):
    _new, problems = settings.apply(cfg(tmp_path), {"interval_seconds": "999"})
    assert any("interval_seconds" in p for p in problems)


def test_bad_hotkey_is_reported(tmp_path):
    _new, problems = settings.apply(cfg(tmp_path), {"hotkey_toggle": "justb"})
    assert any("hotkey_toggle" in p for p in problems)


def test_disabling_ubiquity_filter_is_flagged(tmp_path):
    """The 'max_ubiquity >= 1.0 lets chrome card' guard reaches the form too."""
    _new, problems = settings.apply(cfg(tmp_path), {"max_ubiquity": "1.0"})
    assert any("chrome" in p for p in problems)


def test_int_field_tolerates_a_float_string(tmp_path):
    new, problems = settings.apply(cfg(tmp_path), {"min_count": "3.0"})
    assert problems == [] and new.min_count == 3


def test_unknown_keys_in_the_form_are_ignored(tmp_path):
    new, problems = settings.apply(cfg(tmp_path), {"not_a_setting": "x",
                                                   "min_count": "2"})
    assert problems == []
    assert "not_a_setting" not in new.as_dict()


def test_every_spec_key_is_a_real_config_default():
    """A spec key with no matching default is a control that edits nothing."""
    from blipfeed.config import DEFAULTS
    for field in settings.fields():
        assert field["key"] in DEFAULTS, field["key"]
