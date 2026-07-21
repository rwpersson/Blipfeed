"""Dashboard-controller tests — the app's home logic, no Tk, no real capture.

The capture loop is driven through a fake session so start/stop, the mode/auto-card
toggles, manual capture, and the stats aggregation are all tested headless.
"""
import threading
import time

import pytest

from blipfeed import frequency, store
from blipfeed.config import Config
from blipfeed.dashboard import DashboardController, NoRegion
from blipfeed.capture import Region
from blipfeed.dictionary import Entry
from blipfeed.ocr import OcrLine

REGION = {"left": 0, "top": 0, "width": 100, "height": 40}
IDX = {"学习": Entry("学习", "學習", "xue2 xi2", ("to learn",))}


class FakeSession:
    """Stands in for CaptureSession: run() loops until stopped; records counters."""

    def __init__(self):
        self.ticks = 0
        self.total_observations = 0
        self.total_promoted = []
        self.closed = False
        self.captured = 0
        self.recorded = None

    def run(self, stop_event=None, on_tick=None, max_ticks=None):
        while not stop_event.is_set():
            self.ticks += 1
            if stop_event.wait(0.005):
                break
        return self.summary()

    def summary(self):
        return {"session_id": 1, "ticks": self.ticks,
                "observations": self.total_observations,
                "promoted": list(self.total_promoted)}

    def capture_lines(self):
        self.captured += 1
        return None, [OcrLine("我喜欢学习", 0.9, (0, 0, 50, 20))]

    def record_manual(self, tokens, context=""):
        self.recorded = (tokens, context)

    def close(self):
        self.closed = True


def ctrl(conn, **over):
    cfg = Config({"region": REGION, **over})
    made = []

    def make(cfg_, region):
        s = FakeSession()
        made.append(s)
        return s

    c = DashboardController(cfg, conn=conn, make_session=make)
    c._made = made
    return c


# --------------------------------- lifecycle ---------------------------------
def test_starts_and_stops_auto_capture(conn):
    c = ctrl(conn)
    assert not c.running()
    c.start()
    assert c.running()
    time.sleep(0.03)
    assert c._made[0].ticks > 0                       # the loop actually ran
    c.stop()
    assert not c.running()
    assert c._made[0].closed                           # session torn down


def test_start_without_a_region_raises(conn):
    c = ctrl(conn)
    c.cfg.set_region(None)
    with pytest.raises(NoRegion):
        c.start()


def test_start_is_idempotent(conn):
    c = ctrl(conn)
    c.start()
    c.start()                                          # no second thread/session
    assert len(c._made) == 1
    c.stop()


def test_toggle_flips_running(conn):
    c = ctrl(conn)
    c.toggle()
    assert c.running()
    c.toggle()
    assert not c.running()


def test_stop_when_idle_is_safe(conn):
    ctrl(conn).stop()                                  # must not raise


# --------------------------------- settings ----------------------------------
def test_auto_card_toggle_persists(conn, tmp_path):
    c = ctrl(conn)
    c.cfg.path = tmp_path / "config.json"
    c.set_auto_card(False)
    assert c.cfg.auto_card is False
    from blipfeed import config as config_mod
    assert config_mod.load(c.cfg.path).auto_card is False


def test_set_mode_stops_a_running_session(conn):
    c = ctrl(conn)
    c.cfg.path = None
    c.start()
    c.set_mode("manual")
    assert not c.running()
    assert c.cfg.mode == "manual"


def test_set_mode_rejects_garbage(conn):
    with pytest.raises(ValueError):
        ctrl(conn).set_mode("sideways")


# ------------------------------- manual capture ------------------------------
def test_manual_capture_records_picked_tokens(conn):
    c = ctrl(conn, mode="manual")

    def pick(img, lines):
        return {"tokens": ["学习"], "context": "我喜欢学习"}

    tokens = c.capture_manual(pick)
    assert tokens == ["学习"]
    assert c._manual_session.recorded == (["学习"], "我喜欢学习")


def test_manual_capture_cancelled_records_nothing(conn):
    c = ctrl(conn, mode="manual")
    assert c.capture_manual(lambda img, lines: None) == []
    assert c._manual_session.recorded is None


def test_manual_capture_reuses_the_warm_session(conn):
    c = ctrl(conn, mode="manual")
    c.capture_manual(lambda i, l: {"tokens": ["学习"]})
    c.capture_manual(lambda i, l: {"tokens": ["学习"]})
    assert len(c._made) == 1                           # OCR not re-initialised


def test_manual_capture_refused_while_auto_running(conn):
    c = ctrl(conn)
    c.start()
    with pytest.raises(RuntimeError):
        c.capture_manual(lambda i, l: None)
    c.stop()


# ----------------------------------- stats -----------------------------------
def test_stats_report_queue_and_status_counts(conn):
    sid = store.start_session(conn, "s")

    def tick(txt):
        snap = store.add_snapshot(conn, sid, source="r")
        frequency.ingest(conn, [OcrLine(txt, 0.9, (0, 0, 50, 20))],
                         session_id=sid, snapshot_id=snap, idx=IDX)

    tick("我喜欢学习")                                  # 学习: dict hit, df=1 -> ready
    tick("无畏契约")                                    # slang miss: needs df>=2...
    tick("无畏契约")                                    # ...so seen in 2 snapshots
    for filler in ("今天天气好", "外面下雨", "他在看书", "明天见面", "音乐好听"):
        tick(filler)                                    # denominator -> low ubiquity
    store.seed_known_bulk(conn, ["你好", "谢谢"])

    s = ctrl(conn).stats()
    assert s["ready"] >= 1                              # 学习
    assert s["needs_def"] >= 1                          # 无畏 / 契约
    assert s["known"] == 2
    assert s["running"] is False
    assert s["region"].width == 100


def test_stats_include_the_live_session(conn):
    c = ctrl(conn)
    c.start()
    time.sleep(0.03)
    s = c.stats()
    assert s["running"] is True
    assert s["session"]["ticks"] > 0
    c.stop()
