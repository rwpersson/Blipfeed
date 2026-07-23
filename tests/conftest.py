import pytest

from blipfeed import paths, store


@pytest.fixture(autouse=True)
def _isolate_user_files(tmp_path, monkeypatch):
    """No test may touch the real user's files. **This fixture is load-bearing.**

    `Config.save()` falls back to `paths.config_file()` when the Config has no explicit
    path, and tests build path-less Configs — so a single test calling `set_mode`,
    `set_auto_card` or `set_region` rewrote the developer's REAL config.json with test
    values: a 100x40 test region and onboarded=False. That silently destroyed a real
    capture region, and the next hour of immersion mined nothing because the app was
    reading a 100x40 box. It also explained two other "mysteries" (the welcome nagging
    every launch, active_deck reverting to null) — one cause, three symptoms.

    Redirecting the user dir (config, store, logs, onboarding marker) AND Blip's deck dir
    for every test makes that whole class of accident impossible: a test that writes user
    state now writes it into its own tmp_path. Tests that need a specific deck dir still
    monkeypatch `blip_link.deck_dir` themselves; this is the floor, not a ceiling.
    """
    user = tmp_path / "userdir"
    user.mkdir()
    blip = tmp_path / "blip"
    (blip / "decks").mkdir(parents=True)
    monkeypatch.setattr(paths, "user_dir", lambda: user)
    monkeypatch.setattr(paths, "blip_user_dir", lambda: blip)
    return user


@pytest.fixture
def conn():
    """A fresh in-memory store per test. Never touches the real DB."""
    c = store.connect(":memory:")
    yield c
    c.close()
