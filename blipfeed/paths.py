"""Where things live.

Bundled content (code, CC-CEDICT, models) ships with the app and is read-only. User
data (the SQLite frequency store, logs, config, captured crops) must live under a
writable per-user dir — once installed under Program Files the install dir is not
writable.

Mirrors Blip's paths.py: during development a source checkout keeps everything
in-tree so you don't hunt for files; BLIPFEED_PORTABLE=1 forces that.
"""
import os
import sys
from pathlib import Path

APP_NAME = "Blipfeed"

# Root of the source checkout / install.
PKG_DIR = Path(__file__).resolve().parent
APP_DIR = PKG_DIR.parent


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def user_dir() -> Path:
    """Per-user writable data dir."""
    if os.environ.get("BLIPFEED_PORTABLE") == "1" or not _frozen():
        return APP_DIR                       # dev: keep everything in the checkout
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def data_dir() -> Path:
    """Bundled read-only data (CC-CEDICT, word lists)."""
    return APP_DIR / "data"


def icon_file() -> Path:
    """The app icon (.ico), bundled. Used by windows, the tray, and packaging."""
    return data_dir() / "icon.ico"


def cedict_file() -> Path:
    return data_dir() / "cedict" / "cedict_ts.u8"


def db_file() -> Path:
    """The SQLite frequency store — the source of truth."""
    return user_dir() / "blipfeed.sqlite"


def captures_dir() -> Path:
    """Optional saved snapshot crops (debug / re-OCR). Gitignored."""
    return user_dir() / "data" / "captures"


def config_file() -> Path:
    return user_dir() / "config.json"


def onboarded_marker() -> Path:
    """A sentinel written once the welcome completes. Kept OUT of config.json on
    purpose: config is rewritten by several code paths, and the welcome must never nag
    twice even if a write drops the flag. Its mere existence means 'onboarded'."""
    return user_dir() / ".onboarded"


def log_dir() -> Path:
    return user_dir() / "logs"


# --- Blip integration: locate the deck dir we write immersion cards into ---------
def blip_user_dir() -> Path | None:
    """Blip's per-user data dir, or None if we can't find it.

    Blip mirrors this same dev/frozen split: dev = its checkout, frozen =
    %APPDATA%\\Blip. We check the frozen location first (the shipped case), then a
    sibling checkout (the dev case on this machine), then an override env var.
    Kept here so the Blip coupling lives in one place (see CLAUDE.md deck seam).
    """
    override = os.environ.get("BLIP_USER_DIR")
    if override and Path(override).exists():
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        p = Path(appdata) / "Blip"
        if (p / "decks").exists() or p.exists():
            return p
    sibling = APP_DIR.parent / "Chinese Pop Learning"   # dev checkout on this box
    if (sibling / "decks").exists():
        return sibling
    return None


def blip_decks_dir() -> Path | None:
    base = blip_user_dir()
    return (base / "decks") if base else None


def ensure_dirs() -> None:
    for d in (user_dir(), captures_dir(), log_dir()):
        d.mkdir(parents=True, exist_ok=True)
