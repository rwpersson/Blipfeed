"""User configuration — one JSON file, typed defaults, validated invariants.

Blip's convention, kept: **add new knobs to `DEFAULTS`, never as constants scattered
through the code.** Anything tunable lives here so there is exactly one place to look
and one place for a settings UI to drive.

The file is written to `paths.config_file()`. A missing file is not an error — the
defaults *are* the configuration until the user changes something.

`validate()` enforces the invariants the design depends on, most importantly:

    furniture_threshold <= min_count

Chrome leaks at most `furniture_threshold - 1` observations before suppression kicks
in (see furniture.py), and promotion needs `min_count`. If a user raises the
threshold past `min_count`, UI chrome becomes cardable — the one knob combination
that silently breaks the product. Documented invariants that nothing checks are just
folklore, so this one is checked.
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

    # noise control
    "furniture_threshold": 2,           # suppress from the Nth identical sighting
    "min_confidence": 0.50,             # OCR confidence floor for promotion

    # promotion policy (the gate)
    "min_count": 2,                     # N: sightings before a term is eligible
    "max_promotions_per_snapshot": 2,   # K: cap so a dense frame can't flood
    "auto_card": True,                  # False = nothing cards without the user

    # output
    "blip_deck_id": "immersion",
}

# Bounds used by validate(); also what a settings UI would drive.
_RANGES = {
    "interval_seconds": (5, 120),
    "furniture_threshold": (1, 20),
    "min_count": (1, 50),
    "max_promotions_per_snapshot": (1, 50),
    "min_confidence": (0.0, 1.0),
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

        # THE invariant: chrome leaks (threshold-1) observations; promotion needs
        # min_count. threshold > min_count makes UI chrome cardable.
        thr, n = self._data.get("furniture_threshold"), self._data.get("min_count")
        if isinstance(thr, int) and isinstance(n, int) and thr > n:
            problems.append(
                f"furniture_threshold ({thr}) > min_count ({n}): UI chrome could "
                f"reach a card. Keep furniture_threshold <= min_count.")
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
