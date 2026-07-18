"""Screen capture — headless, so AUTO mode can run while the user games.

`mss` is a passive pixel read: it draws nothing and takes no focus, which is why
AUTO mode needs no window at all (CLAUDE.md Decisions #6). Nothing here creates a
window; the region *picker* is separate UI.

Change detection lives here too: hash the captured region and skip OCR when it is
byte-identical to the previous grab. That kills paused-screen frequency inflation and
saves the expensive step.

**A pixel hash is not the whole dedup story.** When a subtitle stays on screen while
the video moves behind it, the pixels change but the *text* doesn't — so the hash
says "changed" and we would count that subtitle twice. The text-level dedup is
`furniture.py`'s job (identical text at the same position is suppressed), and the two
compose: this saves OCR cost, that prevents double counting.

DPI: `set_dpi_aware()` must run **before** any capture or window, or Windows reports
scaled coordinates and the region lands in the wrong place on a scaled display
(Blip's documented trap — this machine runs at 125%).
"""
import ctypes
import hashlib
from dataclasses import dataclass

from .logging_setup import get

log = get("capture")

_DPI_SET = False


def set_dpi_aware():
    """Per-monitor DPI awareness. Idempotent; call once at process start."""
    global _DPI_SET
    if _DPI_SET:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
        _DPI_SET = True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()        # older fallback
            _DPI_SET = True
        except Exception:
            log.exception("could not set DPI awareness — regions may be misplaced")


@dataclass(frozen=True)
class Region:
    """A capture rectangle in **real screen pixels** (post-DPI-awareness)."""
    left: int
    top: int
    width: int
    height: int

    @property
    def box(self):
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}

    def label(self):
        return f"{self.width}x{self.height}+{self.left}+{self.top}"


def frame_hash(img):
    """Stable hash of a captured frame, for change detection.

    Exact (not perceptual): we only need "is this byte-identical to last time",
    which is what a paused screen or a static article looks like. Perceptual hashing
    would tolerate video noise but can't be trusted to say *unchanged*.
    """
    try:
        import numpy as np
        data = np.ascontiguousarray(img).tobytes()
    except Exception:
        data = bytes(img)
    return hashlib.blake2b(data, digest_size=16).hexdigest()


class Capturer:
    """Holds one mss instance. **Not thread-safe** — mss requires per-thread
    instances, so create one per capture thread."""

    def __init__(self):
        self._sct = None

    def _ensure(self):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    def monitors(self):
        """Real monitors (mss index 0 is the virtual 'all screens' union)."""
        return self._ensure().monitors

    def monitor_region(self, index=1):
        mons = self.monitors()
        if index >= len(mons):
            log.warning("monitor %d not present; falling back to primary", index)
            index = 1 if len(mons) > 1 else 0
        m = mons[index]
        return Region(m["left"], m["top"], m["width"], m["height"])

    def bottom_third(self, index=1):
        """A sane default region: subtitles and captions live in the lower band."""
        r = self.monitor_region(index)
        h = r.height // 3
        return Region(r.left, r.top + r.height - h, r.width, h)

    def grab(self, region):
        """Capture a region as an HxWx3 BGR array (what the OCR provider wants)."""
        import numpy as np
        shot = self._ensure().grab(region.box)
        return np.asarray(shot)[:, :, :3]          # BGRA -> BGR

    def close(self):
        if self._sct is not None:
            self._sct.close()
            self._sct = None


class ChangeDetector:
    """Remembers the last frame hash per source so unchanged frames skip OCR."""

    def __init__(self):
        self._last = {}

    def changed(self, source, img):
        """True if this frame differs from the previous one for `source`."""
        h = frame_hash(img)
        prev = self._last.get(source)
        self._last[source] = h
        return prev != h, h

    def reset(self, source=None):
        if source is None:
            self._last.clear()
        else:
            self._last.pop(source, None)
