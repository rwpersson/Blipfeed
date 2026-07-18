"""hanzi (trad→simp) tests — pure logic, with an injected map so they don't need
the gitignored CC-CEDICT file."""
from blipfeed import hanzi

# 學習 / 中國 -> 学习 / 中国
MAP = {"學": "学", "習": "习", "國": "国", "萬": "万", "電": "电", "影": "影"}


def test_to_simplified_converts_traditional():
    assert hanzi.to_simplified("學習", MAP) == "学习"
    assert hanzi.to_simplified("中國", MAP) == "中国"


def test_to_simplified_leaves_simplified_and_unknown():
    assert hanzi.to_simplified("学习", MAP) == "学习"      # already simplified
    assert hanzi.to_simplified("hello", MAP) == "hello"   # non-hanzi untouched


def test_canonicalize_traditional_sets_alt():
    canon, alt = hanzi.canonicalize("學習", "zh-CN", MAP)
    assert canon == "学习" and alt == "學習"


def test_canonicalize_simplified_has_no_alt():
    canon, alt = hanzi.canonicalize("学习", "zh-CN", MAP)
    assert canon == "学习" and alt is None


def test_canonicalize_non_chinese_is_identity():
    assert hanzi.canonicalize("study", "en", MAP) == ("study", None)


def test_empty_map_is_identity():
    assert hanzi.to_simplified("學習", {}) == "學習"       # graceful: no dict, no dedup
