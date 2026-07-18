"""Hotkey parsing tests.

Registration itself needs a real message pump and a free combo, so it isn't unit
tested — but parsing is where typos in a config file turn into a key that silently
does nothing, so that half is covered here.
"""
import pytest

from blipfeed import hotkeys
from blipfeed.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse


def test_parses_a_letter_combo():
    mods, vk = parse("ctrl+alt+b")
    assert mods == MOD_CONTROL | MOD_ALT
    assert vk == ord("B")


def test_is_case_and_space_insensitive():
    assert parse("Ctrl + Alt + B") == parse("ctrl+alt+b")


def test_accepts_every_modifier_name():
    assert parse("control+shift+win+k")[0] == MOD_CONTROL | MOD_SHIFT | MOD_WIN


def test_parses_named_keys():
    assert parse("ctrl+alt+f5")[1] == 0x74
    assert parse("ctrl+space")[1] == 0x20
    assert parse("ctrl+alt+1")[1] == ord("1")


def test_rejects_a_bare_key():
    """A modifier-less hotkey would swallow that key system-wide."""
    with pytest.raises(ValueError):
        parse("b")


def test_rejects_unknown_names():
    with pytest.raises(ValueError):
        parse("hyper+b")
    with pytest.raises(ValueError):
        parse("ctrl+alt+nonsense")


def test_rejects_empty():
    for bad in ("", "   ", "+"):
        with pytest.raises(ValueError):
            parse(bad)


def test_bad_binding_is_reported_not_raised():
    """A dead hotkey must be reportable by name — the user needs to know *which*
    one does nothing, rather than pressing keys that silently fail."""
    listener = hotkeys.HotkeyListener({"nonsense+++": lambda: None})
    listener.start()
    listener.wait_ready()
    listener.stop()
    listener.join(timeout=2)
    assert "nonsense+++" in listener.failed
    assert listener.registered == []
