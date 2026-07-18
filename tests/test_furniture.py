"""Furniture (static-text suppression) tests.

This is what protects the frequency signal from UI chrome, so it gets real coverage —
especially the guarantee that a *content* word is never dropped just because the same
word also appears as chrome elsewhere on screen.
"""
from blipfeed.furniture import FurnitureFilter
from blipfeed.ocr import OcrLine


def line(text, x=0, y=0, conf=0.9):
    return OcrLine(text, conf, (x, y, x + 50, y + 20))


def texts(lines):
    return [ln.text for ln in lines]


def test_static_chrome_is_suppressed_after_first_sighting():
    f = FurnitureFilter(threshold=2)
    nav = [line("全部", 10, 10), line("游戏", 80, 10)]
    assert texts(f.filter(nav)) == ["全部", "游戏"]   # 1st: not yet known as chrome
    assert f.filter(nav) == []                        # 2nd: recognized as furniture
    assert f.filter(nav) == []                        # and stays suppressed


def test_changing_text_in_a_fixed_box_is_kept():
    """The subtitle band: same position, new text every snapshot -> always content."""
    f = FurnitureFilter(threshold=2)
    for subtitle in ["师父该入万魂幡了", "你说什么", "我不明白"]:
        assert texts(f.filter([line(subtitle, 400, 900)])) == [subtitle]


def test_persisting_subtitle_is_deduped():
    """A held subtitle is the same text in the same box -> counted once, not twice."""
    f = FurnitureFilter(threshold=2)
    held = [line("师父该入万魂幡了", 400, 900)]
    assert len(f.filter(held)) == 1
    assert f.filter(held) == []


def test_same_word_as_chrome_and_as_content():
    """The property that makes a stoplist unnecessary: 游戏 in the nav bar gets
    suppressed, but 游戏 spoken in the subtitle is kept — different position."""
    f = FurnitureFilter(threshold=2)
    nav = line("游戏", 80, 10)
    f.filter([nav])
    f.filter([nav])
    assert f.filter([nav]) == []                       # nav copy is furniture now
    subtitle = line("游戏", 400, 900)                   # same word, content position
    assert texts(f.filter([subtitle])) == ["游戏"]      # never missed


def test_leak_is_bounded_by_threshold():
    """Chrome leaks at most threshold-1 observations — the invariant that keeps it
    below the promotion gate (N=2), so furniture can never reach a card."""
    for threshold in (1, 2, 3):
        f = FurnitureFilter(threshold=threshold)
        nav = [line("抖音", 10, 10)]
        kept = sum(len(f.filter(nav)) for _ in range(10))
        assert kept == threshold - 1


def test_box_jitter_within_grid_keys_the_same():
    f = FurnitureFilter(threshold=2, grid=8)
    assert len(f.filter([line("全部", 100, 40)])) == 1
    assert f.filter([line("全部", 103, 42)]) == []      # jittered a few px, same key


def test_position_beyond_grid_is_a_different_key():
    f = FurnitureFilter(threshold=2, grid=8)
    f.filter([line("全部", 100, 40)])
    assert len(f.filter([line("全部", 400, 40)])) == 1  # genuinely elsewhere -> content


def test_repeated_line_within_one_snapshot_counts_once():
    f = FurnitureFilter(threshold=2)
    # same key twice in a single snapshot must not self-suppress on the next one
    f.filter([line("全部", 10, 10), line("全部", 10, 10)])
    assert f.filter([line("全部", 10, 10)]) == []


def test_reset_clears_state():
    f = FurnitureFilter(threshold=2)
    nav = [line("全部", 10, 10)]
    f.filter(nav)
    assert f.filter(nav) == []
    f.reset()
    assert f.tracked == 0
    assert len(f.filter(nav)) == 1                     # new session, fresh slate
