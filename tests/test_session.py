"""Capture-loop tests.

Everything the session depends on is injectable, so the whole tick cycle runs headless
— no screen, no timer, no OCR stack.
"""
import numpy as np

from blipfeed import store
from blipfeed.capture import ChangeDetector, Region
from blipfeed.config import Config
from blipfeed.dictionary import Entry
from blipfeed.furniture import FurnitureFilter
from blipfeed.ocr import OcrLine
from blipfeed.session import CaptureSession

XUEXI = Entry("学习", "學習", "xue2 xi2", ("to learn", "to study"))
REGION = Region(0, 0, 100, 40)


class FakeCapturer:
    """Yields a preset frame per grab; repeats the last one when exhausted."""

    def __init__(self, frames=None):
        self.frames = list(frames or [np.zeros((4, 4, 3), dtype=np.uint8)])
        self.grabs = 0

    def grab(self, region):
        frame = self.frames[min(self.grabs, len(self.frames) - 1)]
        self.grabs += 1
        return frame

    def close(self):
        pass


class FakeProvider:
    """Yields a preset list of OCR lines per call."""
    name = "fake"

    def __init__(self, per_call):
        self.per_call = list(per_call)
        self.calls = 0

    def recognize(self, image):
        out = self.per_call[min(self.calls, len(self.per_call) - 1)]
        self.calls += 1
        return out


def line(text, conf=0.9, x=0, y=0):
    return OcrLine(text, conf, (x, y, x + 50, y + 20))


def frame(value):
    return np.full((4, 4, 3), value, dtype=np.uint8)


def make(conn, *, frames=None, per_call, cfg=None, writer=lambda rows: None):
    cfg = cfg or Config()
    return CaptureSession(
        conn, cfg, REGION, capturer=FakeCapturer(frames),
        provider=FakeProvider(per_call), detector=ChangeDetector(),
        filt=FurnitureFilter(threshold=cfg.furniture_threshold),
        deck_writer=writer)


def test_tick_records_what_it_sees(conn):
    """Ingestion is lossless: what's on screen is recorded on the tick it's seen."""
    sess = make(conn, per_call=[[line("我喜欢学习")]])
    result = sess.tick()
    assert result.lines == 1 and result.observations > 0
    assert store.find_term(conn, "学习")["count"] == 1


def test_unchanged_frame_skips_ocr(conn):
    """The cheap win: a paused screen costs nothing."""
    sess = make(conn, frames=[frame(1), frame(1)], per_call=[[line("我喜欢学习")]])
    assert not sess.tick().skipped
    second = sess.tick()
    assert second.skipped
    assert sess.provider.calls == 1          # OCR never ran the second time


def test_changed_frame_runs_ocr_again(conn):
    sess = make(conn, frames=[frame(1), frame(2)],
                per_call=[[line("我喜欢学习")], [line("今天天气很好")]])
    sess.tick()
    assert not sess.tick().skipped
    assert sess.provider.calls == 2


def test_chrome_is_recorded_as_data_but_never_carded(conn, monkeypatch):
    """Chrome IS counted — "seen 20x" is true data — but the promotion gate's
    ubiquity ceiling keeps it out of the deck. Recording and carding are separate
    decisions; that separation is the whole architecture."""
    from blipfeed import dictionary
    monkeypatch.setattr(dictionary, "_INDEX", {
        "全部": Entry("全部", "全部", "quan2 bu4", ("whole; all",)),
        "学习": XUEXI, "學習": XUEXI})
    nav = line("全部", x=10, y=10)
    # 学习 appears in only 3 of 10 subtitles, so it is content, not ambient.
    subtitles = ["我喜欢学习", "今天天气很好", "他正在看书", "外面下雨了",
                 "学习中文", "我们去公园", "这个很有趣", "开始学习",
                 "明天见面吧", "音乐真好听"]
    sess = make(conn, frames=[frame(i) for i in range(1, 12)],
                per_call=[[nav, line(t, y=900)] for t in subtitles])
    for _ in range(len(subtitles)):
        sess.tick()

    chrome = store.find_term(conn, "全部")
    assert chrome["count"] == len(subtitles)      # recorded, not discarded
    assert chrome["status"] != "carded"           # but never auto-carded
    assert "全部" not in sess.summary()["promoted"]


def test_tick_promotes_when_the_threshold_is_crossed(conn, monkeypatch):
    from blipfeed import dictionary
    monkeypatch.setattr(dictionary, "_INDEX", {"学习": XUEXI, "學習": XUEXI})
    written = []
    # 学习 in 2 of 8 snapshots: over min_count, well under the ubiquity ceiling.
    subtitles = ["我喜欢学习", "今天天气很好", "外面下雨了", "他正在看书",
                 "学习中文", "我们去公园", "明天见面吧", "音乐真好听"]
    sess = make(conn, frames=[frame(i) for i in range(1, 9)],
                per_call=[[line(t)] for t in subtitles], writer=written.append)
    promoted = []
    for _ in range(len(subtitles)):
        promoted += sess.tick().promoted
    assert "学习" in promoted
    assert store.find_term(conn, "学习")["status"] == "carded"
    assert written                            # the deck writer ran


def test_tick_survives_a_failure(conn):
    """A capture/OCR hiccup must not end the sitting."""
    class Boom:
        name = "boom"

        def recognize(self, image):
            raise RuntimeError("ocr exploded")

    sess = make(conn, per_call=[[]])
    sess.provider = Boom()
    result = sess.tick()
    assert result.error and "exploded" in result.error
    assert sess.tick() is not None            # still usable afterwards


def test_episodes_mode_records_on_disappearance_and_flushes_at_close(conn):
    """The opt-in alternative: count encounters, not screen time."""
    cfg = Config({"count_mode": "episodes"})
    sess = make(conn, frames=[frame(1), frame(2)],
                per_call=[[line("我喜欢学习")], []], cfg=cfg)
    sess.tick()
    assert store.find_term(conn, "学习") is None      # episode hasn't ended yet
    sess.tick()                                       # gone -> recorded once
    assert store.find_term(conn, "学习")["count"] == 1

    held = make(conn, per_call=[[line("他正在看电影")]], cfg=cfg)
    held.tick()
    assert store.find_term(conn, "电影") is None
    held.close()                                      # flush, or it would be lost
    assert store.find_term(conn, "电影")["count"] == 1


def test_run_stops_at_max_ticks(conn):
    cfg = Config({"interval_seconds": 5})
    sess = make(conn, per_call=[[line("我喜欢学习")]], cfg=cfg)
    summary = sess.run(max_ticks=3)
    assert summary["ticks"] == 3


def test_run_stops_immediately_when_the_event_is_set(conn):
    import threading
    stop = threading.Event()
    stop.set()
    sess = make(conn, per_call=[[line("我喜欢学习")]],
                cfg=Config({"interval_seconds": 3600}))
    summary = sess.run(stop_event=stop)       # must not wait out the interval
    assert summary["ticks"] == 0


def test_capture_lines_does_not_ingest(conn):
    """Manual mode: the user decides what counts, so nothing is recorded yet."""
    sess = make(conn, per_call=[[line("我喜欢学习")]])
    _img, lines = sess.capture_lines()
    assert len(lines) == 1
    assert store.find_term(conn, "学习") is None


def test_record_manual_records_exactly_the_picked_tokens(conn):
    sess = make(conn, per_call=[[line("师父该入万魂幡了")]])
    sess.record_manual(["万魂幡"], context="师父该入万魂幡了")
    assert store.find_term(conn, "万魂幡")["count"] == 1
    assert store.find_term(conn, "师父") is None
    assert store.contexts_for(conn, store.find_term(conn, "万魂幡")["id"]) \
        == ["师父该入万魂幡了"]


def test_session_counters(conn):
    sess = make(conn, frames=[frame(1), frame(2)],
                per_call=[[line("我喜欢学习")], [line("今天天气很好")]])
    sess.tick()
    sess.tick()
    assert sess.summary()["ticks"] == 2


def test_manual_recording_from_another_thread(conn):
    """Regression: manual capture runs on a worker (the hotkey pump must not block),
    and sqlite3's thread check turned that into a crash that LOST the user's picked
    words. The session lock + check_same_thread=False is what makes it safe."""
    import threading

    sess = make(conn, per_call=[[line("我喜欢学习")]])
    errors = []

    def worker():
        try:
            for _ in range(10):
                sess.record_manual(["万魂幡"], context="师父该入万魂幡了")
        except Exception as exc:                # pragma: no cover - the bug
            errors.append(repr(exc))

    def ticker():
        try:
            for _ in range(10):
                sess.tick()
        except Exception as exc:                # pragma: no cover - the bug
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker), threading.Thread(target=ticker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert errors == []
    assert store.find_term(conn, "万魂幡")["count"] == 10
