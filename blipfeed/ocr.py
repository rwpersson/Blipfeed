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
import threading
from dataclasses import dataclass

from .logging_setup import get

log = get("ocr")


@dataclass(frozen=True)
class OcrLine:
    """One recognized line.

    `box` is the axis-aligned bbox (x0, y0, x1, y1) in captured-image pixels — what
    furniture keying and positioning use. `quad` keeps the OCR's four corner points
    ((x, y) × 4, clockwise from top-left) so the *angle* survives — text on screen is
    often slanted, and the manual picker draws its word borders along it. `quad` is
    None for lines built without corner data (tests, non-RapidOCR providers).
    """
    text: str
    confidence: float
    box: tuple
    quad: tuple = None

    @property
    def position(self):
        return self.box[0], self.box[1]


def _bbox(points):
    """RapidOCR gives 4 corner points; reduce to an axis-aligned bbox."""
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


def _quad(points):
    """The four corner points as floats, or None if not a quadrilateral."""
    if not points or len(points) != 4:
        return None
    return tuple((float(p[0]), float(p[1])) for p in points)


class OcrProvider:
    name = "abstract"

    def available(self):
        return True

    def configure(self, **params):
        """Set detection sensitivity, etc. No-op for providers that don't tune."""

    def recognize(self, image):
        """image: a path, or an HxWx3 BGR numpy array. -> list[OcrLine]"""
        raise NotImplementedError


class RapidOcrProvider(OcrProvider):
    """PP-OCR models on ONNXRuntime. Local, offline, no per-use cost."""
    name = "rapidocr"

    def __init__(self):
        self._engine = None
        self._params = {}
        # Providers are module-level singletons (see _PROVIDERS), and a hotkey-triggered
        # manual capture on the main thread can now run while the AUTO loop ticks on the
        # capture thread — so two threads reach this one engine. RapidOCR wraps stateful
        # det/cls/rec models, so serialize inference rather than assume it's reentrant.
        # The lock also makes the lazy load happen exactly once.
        self._lock = threading.Lock()

    def available(self):
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False

    def configure(self, **params):
        """Call-time detection tuning (RapidOCR accepts these per recognize):

        - box_thresh (default 0.5) — lower detects fainter/smaller text
        - text_score (default 0.5) — lower keeps lower-confidence recognitions
        - unclip_ratio (default 1.6) — larger grows each detected box

        Lowering the first two catches words the defaults drop (small text on a
        full-screen grab, stylized fonts). Safe here because ingestion is lossless and
        promotion has its own confidence gate — recording more never over-cards.
        """
        with self._lock:
            self._params = {k: v for k, v in params.items() if v is not None}

    def _ensure(self):
        """Load the engine. Caller must hold `_lock`."""
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            log.info("loading RapidOCR (one-time, ~2s)")
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image):
        # Inference under the lock; the cheap result-shaping below runs outside it so a
        # concurrent capture waits only for the actual OCR (~1s), not the parsing.
        with self._lock:
            engine = self._ensure()
            result, _elapse = engine(image, **self._params)
        if not result:
            return []
        lines = []
        for points, text, conf in result:
            text = (text or "").strip()
            if text:
                lines.append(OcrLine(text, float(conf), _bbox(points),
                                     quad=_quad(points)))
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
