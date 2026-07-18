"""CC-CEDICT tests. Parsing/selection logic runs against a small inline index so it
doesn't depend on the gitignored dictionary file; a couple of tests use the real one
when present."""
import pytest

from blipfeed import dictionary
from blipfeed.dictionary import Entry


def idx(*entries):
    out = {}
    for e in entries:
        out.setdefault(e.simplified, e)
        out.setdefault(e.traditional, e)
    return out


XUEXI = Entry("学习", "學習", "xue2 xi2", ("to learn", "to study"))
ZHONGWEN = Entry("中文", "中文", "zhong1 wen2", ("Chinese language",))
VARIANT = Entry("罗", "羅", "luo2", ("variant of 羅|罗", "a net for catching birds"))


def test_lookup_by_either_script():
    i = idx(XUEXI)
    assert dictionary.lookup("学习", i) is XUEXI
    assert dictionary.lookup("學習", i) is XUEXI


def test_lookup_miss_returns_none():
    assert dictionary.lookup("万魂幡", idx(XUEXI)) is None
    assert dictionary.lookup("", idx(XUEXI)) is None


def test_alt_is_the_other_script_or_none():
    assert XUEXI.alt == "學習"
    assert ZHONGWEN.alt is None          # identical in both scripts


def test_meaning_joins_glosses():
    assert XUEXI.meaning() == "to learn; to study"


def test_meaning_prefers_a_real_gloss_over_variant_of():
    assert VARIANT.meaning().startswith("a net")


def test_meaning_is_truncated():
    long = Entry("x", "x", "x", ("a" * 200,))
    assert len(long.meaning(max_len=90)) <= 90


def test_fields_for_hit_supplies_meaning_and_alt():
    f = dictionary.fields_for("学习", idx(XUEXI))
    assert f["dict_ok"] == 1
    assert f["meaning"] == "to learn; to study"
    assert f["alt"] == "學習"


def test_fields_for_miss_is_not_garbage():
    """A miss means 'needs a definition', not 'discard'."""
    f = dictionary.fields_for("万魂幡", idx(XUEXI))
    assert f == {"dict_ok": 0}
    assert "meaning" not in f            # -> lands in the needs-def bucket


def test_fields_for_hit_without_alt_omits_it():
    assert "alt" not in dictionary.fields_for("中文", idx(ZHONGWEN))


# --- against the real dictionary, when it's present ---------------------------
real = pytest.mark.skipif(not dictionary.paths.cedict_file().exists(),
                          reason="CC-CEDICT not present")


@real
def test_real_dictionary_hits_ordinary_words():
    for term in ("学习", "电影", "婚姻"):
        assert dictionary.has(term), term


@real
def test_real_dictionary_misses_the_slang_we_expect():
    """The finding that shaped the design: the words worth learning often miss."""
    for term in ("万魂幡", "婚姻危机", "凡人歌"):
        assert not dictionary.has(term), term


@real
def test_real_dictionary_supplies_both_scripts():
    assert dictionary.fields_for("学习")["alt"] == "學習"
