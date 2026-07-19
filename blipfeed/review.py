"""Shortcut so the review window matches the documented command.

    python -m blipfeed.review

The window itself lives in `blipfeed/ui/review.py`; this is only the entry point, the
same way Blip exposes its editor.
"""
import sys

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import blipfeed  # noqa: F401

    __package__ = "blipfeed"

from .ui.review import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
