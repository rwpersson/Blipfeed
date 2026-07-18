"""Segmentation tests — LOAD-BEARING, because a bad split corrupts every frequency
count with no error to point at."""
from blipfeed import segment


def test_splits_a_sentence_into_words():
    toks = segment.segment("我喜欢学习中文")
    assert "喜欢" in toks and "学习" in toks and "中文" in toks


def test_particles_and_pronouns_are_dropped():
    toks = segment.segment("我喜欢学习中文")
    assert "我" not in toks                      # pronoun: never a useful card
    assert "的" not in segment.segment("这是我的书")


def test_punctuation_latin_and_digits_are_dropped():
    for text in ("你好，世界！", "hello 世界", "2026年 abc 世界"):
        for tok in segment.segment(text):
            assert any("一" <= c <= "鿿" for c in tok), tok
    assert segment.segment("hello world 123!") == []


def test_keep_predicate():
    assert segment.keep("学习")
    assert not segment.keep("的")               # stopword
    assert not segment.keep("hello")            # no CJK
    assert not segment.keep("123")
    assert not segment.keep("，")
    assert not segment.keep("  ")


def test_tokenize_is_unfiltered_for_manual_mode():
    """Manual mode shows every chip and lets the user decide."""
    raw = segment.tokenize("我喜欢学习")
    assert "我" in raw                           # present in raw...
    assert "我" not in segment.segment("我喜欢学习")   # ...but not recorded in auto


def test_empty_input():
    assert segment.segment("") == []
    assert segment.tokenize("") == []


def test_oov_slang_fragments_without_a_user_dict():
    """Documents the real-frame finding: 万魂幡 splits, because jieba doesn't know it.
    This is why manual merge + the user dictionary exist."""
    toks = segment.segment("师父该入万魂幡了")
    assert "万魂幡" not in toks                   # the limitation, pinned
    assert any("万魂" in t or "幡" in t for t in toks)


def test_user_dict_makes_a_slang_term_one_token(tmp_path, monkeypatch):
    """Teaching the segmenter a merged slang word makes it stick."""
    monkeypatch.setattr(segment.paths, "user_dir", lambda: tmp_path)
    monkeypatch.setattr(segment, "_USERDICT_LOADED", False)
    assert segment.add_user_word("万魂幡")
    assert "万魂幡" in segment.segment("师父该入万魂幡了")
    assert "万魂幡" in (tmp_path / "userdict.txt").read_text(encoding="utf-8")


def test_add_user_word_rejects_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(segment.paths, "user_dir", lambda: tmp_path)
    assert not segment.add_user_word("   ")
