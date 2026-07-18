"""Logging — one place, so nothing swallows a crash.

Blip's rule, kept here: no bare `except: pass`; use log.exception(). You can't debug
a stranger's crash otherwise. Logs go to a rotating file under the user dir and to
the console.

Windows console gotcha (inherited from Blip): the console is cp1252 and *crashes*
printing Chinese. We reconfigure stdout/stderr to UTF-8 so a log line with hanzi
never aborts a run.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths

_CONFIGURED = False


def _fix_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass                              # already utf-8, or not reconfigurable


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _fix_console_encoding()
    paths.log_dir().mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("blipfeed")
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    fh = RotatingFileHandler(
        paths.log_dir() / "blipfeed.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    root.addHandler(fh)
    root.addHandler(ch)
    root.propagate = False
    _CONFIGURED = True


def get(name: str) -> logging.Logger:
    """A namespaced logger, e.g. get("ocr") -> 'blipfeed.ocr'."""
    _configure()
    return logging.getLogger(f"blipfeed.{name}")
