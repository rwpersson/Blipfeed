"""User configuration — one JSON file, typed defaults, validated invariants.

Blip's convention, kept: **add new knobs to `DEFAULTS`, never as constants scattered
through the code.** Anything tunable lives here so there is exactly one place to look
and one place for a settings UI to drive.

The file is written to `paths.config_file()`. A missing file is not an error — the
defaults *are* the configuration until the user changes something.

## How chrome is kept out of your deck

Not by refusing to record it. **Ingestion is lossless** — "打开 seen 150×" is true
data, and that word really is ubiquitous. Chrome is excluded at the *promotion* gate:

    max_ubiquity — a term appearing in more than this fraction of ALL snapshots is
                   ambient (UI chrome, or a word so common you already know it) and
                   is not auto-carded. It stays queued and manually cardable.

A ratio rather than a raw count, so it holds for a 5-minute session and a 3-hour one.
It also supplies the warm-up for free: with `min_count=2` and `max_ubiquity=0.35`
nothing can card until ~6 snapshots exist, because before that everything looks
ubiquitous. An earlier version had an explicit warm-up exemption instead, and chrome —
which reaches count 2 on the *second* snapshot, being in every frame — sailed through
that window and got carded. Don't reintroduce one.

`furniture_threshold` now only affects MANUAL mode, where the chip picker must
de-clutter the frame immediately and can't wait for a ratio to settle.
"""
import json
from pathlib import Path

from . import paths
from .logging_setup import get

log = get("config")

DEFAULTS = {
    # what we're learning
    "language": "zh-CN",

    # capture
    "mode": "auto",                     # 'auto' (headless) | 'manual' (chip picker)
    "interval_seconds": 30,             # snapshot cadence; NOT continuous
    "monitor": 1,                       # mss monitor index (0 = virtual union)
    "region": None,                     # {left, top, width, height} or None
    "ocr_provider": "rapidocr",

    # counting: 'sightings' records every appearance (lossless — chrome included, so
    # "seen 150x" stays true data); 'episodes' records one appearance-episode.
    "count_mode": "sightings",
    "furniture_threshold": 2,           # MANUAL mode: de-clutter chips immediately
    "episode_max_ticks": 3,             # only used by count_mode='episodes'
    "min_confidence": 0.50,             # OCR confidence floor for promotion

    # promotion policy (the gate)
    "min_count": 2,                     # N: distinct snapshots before an UNVERIFIED
                                        # term is eligible (guards OCR garbage)
    "min_count_verified": 1,            # ...but a CC-CEDICT hit, or a term you
                                        # defined yourself, is very unlikely to be
                                        # garbage, so one clear sighting is enough.
                                        # At 30s sampling a real word rarely recurs
                                        # across snapshots while chrome always does,
                                        # so requiring 2 of everything mostly blocked
                                        # real vocabulary. Ubiquity still applies.
    "max_ubiquity": 0.35,               # skip terms appearing in >35% of ALL snapshots
                                        # — ambient UI, or a word you already know.
                                        # A ratio, not a count, so it survives long
                                        # sessions. Still queued & manually cardable.
                                        # Also supplies the warm-up: nothing can card
                                        # until ~6 snapshots exist.
    "max_promotions_per_snapshot": 2,   # K: cap so a dense frame can't flood
    "min_promote_length": 2,            # skip 1-char basics (很/好/去/太) when
                                        # AUTO-carding; they stay visible in review
    "auto_card": True,                  # False = nothing cards without the user

    # global hotkeys — AUTO mode is headless, so these are its control surface
    # (CLAUDE.md Decisions #6). They must work while a game has focus.
    "hotkey_toggle": "ctrl+alt+b",       # start/stop capture
    "hotkey_manual": "ctrl+alt+n",       # capture now -> manual chip picker
    "hotkey_region": "ctrl+alt+r",       # redraw the capture region

    # output
    "blip_deck_id": "immersion",
}

# Bounds used by validate(); also what a settings UI would drive.
_RANGES = {
    "interval_seconds": (5, 120),
    "furniture_threshold": (1, 20),
    "min_count": (1, 50),
    "min_count_verified": (1, 50),
    "max_promotions_per_snapshot": (1, 50),
    "min_promote_length": (1, 10),
    "min_confidence": (0.0, 1.0),
    "max_ubiquity": (0.01, 1.0),
}


class Config:
    """Attribute access over a plain dict, so `cfg.min_count` reads naturally."""

    def __init__(self, data=None, path=None):
        self._data = {**DEFAULTS, **(data or {})}
        self.path = path

    def __getattr__(self, name):
        # only reached when normal attribute lookup fails
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        return self

    def as_dict(self):
        return dict(self._data)

    # --- region convenience -------------------------------------------------
    def region(self):
        """The saved capture region as a `capture.Region`, or None if unset."""
        raw = self._data.get("region")
        if not raw:
            return None
        from .capture import Region
        try:
            return Region(int(raw["left"]), int(raw["top"]),
                          int(raw["width"]), int(raw["height"]))
        except (KeyError, TypeError, ValueError):
            log.warning("ignoring malformed region in config: %r", raw)
            return None

    def set_region(self, region):
        self._data["region"] = None if region is None else {
            "left": region.left, "top": region.top,
            "width": region.width, "height": region.height}
        return self

    # --- validation ---------------------------------------------------------
    def validate(self):
        """Human-readable problems. Empty == fine."""
        problems = []
        for key, (lo, hi) in _RANGES.items():
            v = self._data.get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                problems.append(f"{key}: expected a number, got {v!r}")
            elif not (lo <= v <= hi):
                problems.append(f"{key}: {v} is outside {lo}..{hi}")

        if self._data.get("mode") not in ("auto", "manual"):
            problems.append(f"mode: expected 'auto' or 'manual', "
                            f"got {self._data.get('mode')!r}")

        if self._data.get("count_mode") not in ("sightings", "episodes"):
            problems.append(f"count_mode: expected 'sightings' or 'episodes', "
                            f"got {self._data.get('count_mode')!r}")

        # A ubiquity ceiling at/above 1.0 disables the only thing keeping UI chrome
        # out of the deck — worth saying out loud rather than leaving to discovery.
        if self._data.get("max_ubiquity", 0) >= 1.0:
            problems.append(
                "max_ubiquity >= 1.0 disables the ambient-word filter: UI chrome and "
                "words you already know will be auto-carded.")
        return problems

    # --- persistence --------------------------------------------------------
    def save(self, path=None):
        path = Path(path) if path else (self.path or paths.config_file())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        self.path = path
        return path


def load(path=None):
    """Load config, falling back to defaults. Unknown keys are preserved so a file
    written by a newer version survives a round-trip through an older one."""
    path = Path(path) if path else paths.config_file()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("config must be a JSON object")
        except Exception:
            log.exception("unreadable config at %s — using defaults", path)
            data = {}
    cfg = Config(data, path)
    for problem in cfg.validate():
        log.warning("config problem: %s", problem)
    return cfg
