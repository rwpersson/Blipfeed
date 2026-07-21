"""Settings — the spec that drives the GUI, and the pure apply/validate (tested).

Config knobs used to require hand-editing config.json. This puts them behind a form.
The **spec is the single source** of what's editable and how — the window is generated
from it, so adding a setting is one entry here (Blip's spec-driven pattern; a typo'd
key yields a control that changes nothing, which is why `apply` is tested).

`apply` is pure: given the current config and a dict of raw form values, it coerces,
validates (reusing `Config.validate` plus a hotkey-parse check), and either saves and
returns the new config or returns the problems for the form to show.
"""
from . import hotkeys
from .config import Config
from .logging_setup import get

log = get("settings")

# Each group is (title, [field, ...]). A field is a dict with a key, a human label,
# a kind, and kind-specific bounds/values.
SPEC = [
    ("Capture", [
        {"key": "interval_seconds", "label": "Snapshot interval",
         "kind": "int", "min": 5, "max": 120, "suffix": "seconds",
         "help": "How often the screen is read. Not continuous."},
        {"key": "count_mode", "label": "Counting",
         "kind": "choice", "values": ["sightings", "episodes"],
         "help": "'sightings' counts every snapshot a word is on screen; "
                 "'episodes' counts each time it appears and leaves."},
    ]),
    ("What becomes a card", [
        {"key": "min_count", "label": "Times seen before carding (N)",
         "kind": "int", "min": 1, "max": 50},
        {"key": "min_count_verified", "label": "…if the word is in the dictionary",
         "kind": "int", "min": 1, "max": 50,
         "help": "Dictionary words are trustworthy on fewer sightings."},
        {"key": "max_promotions_per_snapshot", "label": "Max new cards per snapshot (K)",
         "kind": "int", "min": 1, "max": 50},
        {"key": "min_promote_length", "label": "Skip words shorter than",
         "kind": "int", "min": 1, "max": 10, "suffix": "characters"},
        {"key": "max_ubiquity", "label": "Skip words seen on more than",
         "kind": "float", "min": 0.05, "max": 1.0, "suffix": "of all screens",
         "help": "A word on nearly every screen is UI chrome or already-known."},
        {"key": "min_confidence", "label": "Minimum OCR confidence to card",
         "kind": "float", "min": 0.0, "max": 1.0},
        {"key": "auto_card", "label": "Auto-card while capturing",
         "kind": "bool",
         "help": "Off = nothing cards on its own; you approve everything in Review."},
    ]),
    ("OCR sensitivity", [
        {"key": "ocr_box_thresh", "label": "Detection threshold",
         "kind": "float", "min": 0.1, "max": 0.9,
         "help": "Lower catches more (smaller/fainter text), with more noise."},
        {"key": "ocr_text_score", "label": "Recognition threshold",
         "kind": "float", "min": 0.1, "max": 0.9},
    ]),
    ("Hotkeys", [
        {"key": "hotkey_toggle", "label": "Start / stop", "kind": "hotkey"},
        {"key": "hotkey_manual", "label": "Capture now", "kind": "hotkey"},
        {"key": "hotkey_region", "label": "Redraw region", "kind": "hotkey"},
    ]),
    ("Blip", [
        {"key": "blip_deck_id", "label": "Immersion deck name", "kind": "text"},
        {"key": "blip_graduate_at", "label": "Count a Blip word known after",
         "kind": "int", "min": 1, "max": 50, "suffix": "reviews"},
    ]),
]

_HOTKEY_KEYS = ("hotkey_toggle", "hotkey_manual", "hotkey_region")


def fields():
    for _group, items in SPEC:
        yield from items


def coerce(field, raw):
    """Raw form value -> the config type. Raises ValueError on a bad number."""
    kind = field["kind"]
    if kind == "int":
        return int(float(raw))
    if kind == "float":
        return round(float(raw), 4)
    if kind == "bool":
        return bool(raw)
    return str(raw).strip()


def apply(cfg, values):
    """Coerce + validate `values`, then commit into `cfg` **in place** and save.

    Returns (cfg, []) on success — mutating the passed config so live consumers (the
    running dashboard/session that hold this same object) see the change, not a stale
    copy. On any problem returns (None, [msgs]) with `cfg` untouched and nothing
    written, so a bad form never corrupts the file.
    """
    coerced, problems = {}, []
    for field in fields():
        key = field["key"]
        if key not in values:
            continue
        try:
            coerced[key] = coerce(field, values[key])
        except (ValueError, TypeError):
            problems.append(f"{field['label']}: not a valid number")

    trial = Config({**cfg.as_dict(), **coerced}, cfg.path)   # validate before committing
    problems.extend(trial.validate())
    for key in _HOTKEY_KEYS:
        try:
            hotkeys.parse(trial.get(key, ""))
        except ValueError as exc:
            problems.append(f"{key}: {exc}")

    if problems:
        return None, problems
    for key, value in coerced.items():
        cfg.set(key, value)
    cfg.save()
    log.info("settings saved to %s", cfg.path)
    return cfg, []
