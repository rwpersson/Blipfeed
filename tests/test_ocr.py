"""OCR provider — the bits that aren't the model itself.

The provider instances in `ocr._PROVIDERS` are module-level singletons, and since the
capture-now hotkey can fire a manual capture (main thread) while the AUTO loop ticks
(capture thread), two threads reach one engine. That makes the serialization here
load-bearing, not decoration.
"""
import threading

from blipfeed import ocr


class _SpyEngine:
    """Stands in for RapidOCR; records whether two calls ever overlap."""

    def __init__(self, hold=0.02):
        self.hold = hold
        self.concurrent = 0
        self.max_concurrent = 0
        self.calls = 0
        self._guard = threading.Lock()

    def __call__(self, image, **params):
        with self._guard:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.calls += 1
        import time
        time.sleep(self.hold)               # a real recognize takes ~1s
        with self._guard:
            self.concurrent -= 1
        return [([[0, 0], [10, 0], [10, 5], [0, 5]], "学习", 0.9)], 1.0


def test_recognize_is_serialized_across_threads():
    p = ocr.RapidOcrProvider()
    engine = _SpyEngine()
    p._engine = engine                      # skip the real model load

    threads = [threading.Thread(target=p.recognize, args=(object(),))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert engine.calls == 6
    assert engine.max_concurrent == 1       # never two inferences at once


def test_recognize_returns_parsed_lines():
    p = ocr.RapidOcrProvider()
    p._engine = _SpyEngine(hold=0)
    lines = p.recognize(object())
    assert [line.text for line in lines] == ["学习"]
    assert lines[0].confidence == 0.9


def test_configure_only_keeps_supplied_params():
    p = ocr.RapidOcrProvider()
    p.configure(box_thresh=0.3, text_score=None)
    assert p._params == {"box_thresh": 0.3}


def test_providers_are_shared_singletons():
    """Both sessions must get the SAME engine — two RapidOCR instances would double a
    ~100MB model for no reason."""
    assert ocr.get_provider("rapidocr") is ocr.get_provider("rapidocr")
