"""OCR, behind a provider interface.

The engine is **RapidOCR** (PP-OCR models on ONNXRuntime) — chosen in the Phase 1
spike over paddlepaddle, which has no Python 3.14 wheel and is far heavier to bundle
(CLAUDE.md Decisions #3). Nothing outside this module knows which engine is in use,
so a cloud/Tesseract provider could slot in later.

Recognition returns `OcrLine`s carrying **text + confidence + bounding box**. The box
is not decoration: furniture detection keys on (text, position) to strip static UI
chrome, and manual mode needs positions to draw clickable chips.

Engine init is heavy (~1.8s) — the provider loads it **lazily and once**, never per
snapshot.
"""
from dataclasses import dataclass

from .logging_setup import get

log = get("ocr")


@dataclass(frozen=True)
class OcrLine:
    """One recognized line. `box` is (x0, y0, x1, y1) in captured-image pixels."""
    text: str
    confidence: float
    box: tuple

    @property
    def position(self):
        return self.box[0], self.box[1]


def _bbox(points):
    """RapidOCR gives 4 corner points; reduce to an axis-aligned bbox."""
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


class OcrProvider:
    name = "abstract"

    def available(self):
        return True

    def recognize(self, image):
        """image: a path, or an HxWx3 BGR numpy array. -> list[OcrLine]"""
        raise NotImplementedError


class RapidOcrProvider(OcrProvider):
    """PP-OCR models on ONNXRuntime. Local, offline, no per-use cost."""
    name = "rapidocr"

    def __init__(self):
        self._engine = None

    def available(self):
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            log.info("loading RapidOCR (one-time, ~2s)")
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image):
        engine = self._ensure()
        result, _elapse = engine(image)
        if not result:
            return []
        lines = []
        for points, text, conf in result:
            text = (text or "").strip()
            if text:
                lines.append(OcrLine(text, float(conf), _bbox(points)))
        return lines


class NullProvider(OcrProvider):
    """Recognizes nothing — lets the pipeline be exercised without the OCR stack."""
    name = "null"

    def recognize(self, image):
        return []


_PROVIDERS = {p.name: p for p in (RapidOcrProvider(), NullProvider())}
DEFAULT = "rapidocr"


def get_provider(name=None):
    p = _PROVIDERS.get(name or DEFAULT)
    if p is None:
        log.warning("unknown ocr provider %r, falling back to %s", name, DEFAULT)
        p = _PROVIDERS[DEFAULT]
    return p


def joined_text(lines):
    """All recognized text as one string (stored on the snapshot for debug/re-parse)."""
    return "".join(line.text for line in lines)
