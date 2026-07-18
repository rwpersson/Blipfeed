"""Capture tests — the pure parts (region math, hashing, change detection).

Actually grabbing the screen needs a desktop session, so that stays an integration
check; everything here runs headless.
"""
import numpy as np

from blipfeed.capture import ChangeDetector, Region, frame_hash


def frame(value, shape=(4, 4, 3)):
    return np.full(shape, value, dtype=np.uint8)


def test_region_box_is_what_mss_wants():
    r = Region(10, 20, 300, 400)
    assert r.box == {"left": 10, "top": 20, "width": 300, "height": 400}
    assert r.label() == "300x400+10+20"


def test_frame_hash_is_deterministic():
    assert frame_hash(frame(7)) == frame_hash(frame(7))


def test_frame_hash_differs_on_change():
    assert frame_hash(frame(7)) != frame_hash(frame(8))


def test_frame_hash_differs_on_shape():
    assert frame_hash(frame(7, (4, 4, 3))) != frame_hash(frame(7, (8, 4, 3)))


def test_change_detector_first_frame_is_a_change():
    cd = ChangeDetector()
    changed, h = cd.changed("r1", frame(1))
    assert changed is True and h


def test_change_detector_identical_frame_is_not():
    cd = ChangeDetector()
    cd.changed("r1", frame(1))
    changed, _ = cd.changed("r1", frame(1))
    assert changed is False          # a paused screen -> skip OCR entirely


def test_change_detector_tracks_sources_independently():
    cd = ChangeDetector()
    cd.changed("r1", frame(1))
    changed, _ = cd.changed("r2", frame(1))
    assert changed is True           # same pixels, different source -> still new


def test_change_detector_sees_a_real_change():
    cd = ChangeDetector()
    cd.changed("r1", frame(1))
    changed, _ = cd.changed("r1", frame(2))
    assert changed is True


def test_change_detector_reset():
    cd = ChangeDetector()
    cd.changed("r1", frame(1))
    cd.reset("r1")
    changed, _ = cd.changed("r1", frame(1))
    assert changed is True           # forgotten -> counts as new again
