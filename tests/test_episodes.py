"""EpisodeTracker tests — the fix for chrome reaching a card.

A real session carded 打开/声音/合集/更新 (all Douyin UI). The old per-sighting filter
leaked one observation *per (text, position) key*, so a word appearing in two chrome
lines reached the N=2 gate. These tests pin the stronger rule: chrome persists, so it
is never recorded at all.
"""
from blipfeed.furniture import EpisodeTracker
from blipfeed.ocr import OcrLine


def line(text, x=0, y=0, conf=0.9):
    return OcrLine(text, conf, (x, y, x + 50, y + 20))


def texts(lines):
    return sorted(ln.text for ln in lines)


def test_a_line_is_recorded_when_it_disappears():
    t = EpisodeTracker()
    assert t.update([line("你好")]) == []          # still on screen -> not yet
    assert texts(t.update([])) == ["你好"]         # gone -> episode ended, record it


def test_persistent_chrome_is_never_recorded():
    """The bug that carded 打开/声音: chrome stays on screen, so it never ends."""
    t = EpisodeTracker(max_ticks=3)
    nav = [line("打开声音", 10, 10)]
    for _ in range(10):
        assert t.update(nav) == []
    assert t.persistent == 1


def test_a_word_in_two_chrome_lines_still_leaks_nothing():
    """The exact shape of the real failure: 打开 appeared in a prompt AND a button,
    leaking one observation each and reaching count 2."""
    t = EpisodeTracker(max_ticks=3)
    chrome = [line("是否要打开声音", 100, 900), line("打开声音", 400, 950)]
    for _ in range(8):
        assert t.update(chrome) == []           # neither line is ever recorded


def test_a_held_subtitle_is_recorded_once_not_per_tick():
    t = EpisodeTracker(max_ticks=3)
    held = [line("师父该入万魂幡了", 400, 900)]
    t.update(held)
    t.update(held)                               # still there
    assert texts(t.update([])) == ["师父该入万魂幡了"]   # exactly one record


def test_changing_subtitles_each_get_recorded():
    t = EpisodeTracker()
    got = []
    for subtitle in ["第一句", "第二句", "第三句"]:
        got += t.update([line(subtitle, 400, 900)])
    got += t.update([])
    assert texts(got) == ["第一句", "第三句", "第二句"]


def test_a_line_outliving_max_ticks_is_dropped_even_when_it_ends():
    """You navigated away — but it was chrome while it lasted."""
    t = EpisodeTracker(max_ticks=2)
    nav = [line("全部", 10, 10)]
    for _ in range(5):
        t.update(nav)
    assert t.update([]) == []                    # not resurrected as content


def test_same_text_in_a_different_place_is_a_separate_episode():
    t = EpisodeTracker()
    t.update([line("游戏", 10, 10)])              # nav
    ended = t.update([line("游戏", 400, 900)])    # subtitle: nav's episode ended
    assert texts(ended) == ["游戏"]


def test_flush_records_short_lived_leftovers():
    """Session end: what you were reading shouldn't be lost."""
    t = EpisodeTracker(max_ticks=3)
    t.update([line("最后一句", 400, 900)])
    assert texts(t.flush()) == ["最后一句"]
    assert t.onscreen == 0


def test_flush_excludes_chrome():
    t = EpisodeTracker(max_ticks=2)
    nav = [line("全部", 10, 10)]
    for _ in range(6):
        t.update(nav)
    assert t.flush() == []


def test_box_jitter_keeps_one_episode():
    t = EpisodeTracker(grid=8)
    t.update([line("你好", 100, 40)])
    assert t.update([line("你好", 103, 42)]) == []    # same episode, not a new one


def test_reset_clears_state():
    t = EpisodeTracker()
    t.update([line("你好")])
    t.reset()
    assert t.onscreen == 0
    assert t.update([]) == []
