"""Reading + tones — the language-specific corner, isolated like Blip's lang.py.

For Chinese this is pypinyin: 学习 -> ("xué xí", "2-2"). It resolves polyphones from
context via a phrase dictionary (银行 yín háng, not xíng), which is why no BERT-based
guesser is needed.

**This works for every term, dictionary or not** — 万魂幡 -> "wàn hún fān". That's the
point: a CC-CEDICT miss is missing only its *meaning*, so a mined slang word still
reaches Blip with a tone-colored reading.

Auto-fill is a *suggestion*: pypinyin can disagree with a careful reading (it
neutralises 电子 to "diàn zi"), so the user must always be able to override.

Adding another language means adding a branch here, not touching the pipeline.
"""
from .logging_setup import get

log = get("lang")

NEUTRAL = "·"


def supports(language):
    return bool(language) and language.lower().startswith("zh")


def _tone_of(tone3_syllable):
    """'xi2' -> '2';  'de5' -> '·';  'flag' -> None (not a Chinese syllable)."""
    if not tone3_syllable:
        return None
    last = tone3_syllable[-1]
    if last in "1234":
        return last
    if last == "5":
        return NEUTRAL
    return None


def derive_reading(term, language="zh-CN"):
    """(reading, tones), e.g. ("wàn hún fān", "4-2-1").

    ("", "") when we can't help (non-Chinese deck, or pypinyin missing). The tone
    pattern is only claimed when it lines up with the reading — a mismatch would fail
    Blip's deck validation and lose the tone colors anyway.
    """
    if not term or not supports(language):
        return "", ""
    try:
        from pypinyin import Style, pinyin
    except ImportError:
        log.warning("pypinyin not installed — reading auto-fill unavailable")
        return "", ""
    try:
        marked = [s[0] for s in pinyin(term, style=Style.TONE)]
        numbered = [s[0] for s in pinyin(term, style=Style.TONE3,
                                         neutral_tone_with_five=True)]
    except Exception:
        log.exception("pypinyin failed for %r", term)
        return "", ""

    reading = " ".join(marked)
    tones = [t for t in (_tone_of(s) for s in numbered) if t is not None]
    if len(tones) != len(marked):
        return reading, ""
    return reading, "-".join(tones)


def fields_for(term, language="zh-CN"):
    """Card fields this module can supply: reading, tones (omitted when empty)."""
    reading, tones = derive_reading(term, language)
    out = {}
    if reading:
        out["reading"] = reading
    if tones:
        out["tones"] = tones
    return out
