"""Word segmentation — where "words" actually come from. LOAD-BEARING.

Chinese has no spaces, so OCR hands us 我喜欢学习中文 and this module turns it into
我 / 喜欢 / 学习 / 中文. **Every frequency count in the product depends on these
splits**, and a bad split corrupts them silently — no error, just wrong data. Hence
direct tests.

## What gets filtered here, and what deliberately doesn't

Filtered: anything with no Chinese character (punctuation, latin, digits, emoji — one
rule catches them all) and a small set of **grammatical particles/pronouns** (的了是我
你…) that could never be a useful card.

*Not* filtered: ordinary words that merely happen to be easy. "Too basic for me" is a
promotion concern, not a segmentation one — it depends on the user's level and their
known-words list, and the store is meant to be lossless (ingest everything, promote
selectively). Single-character basics like 很/好/去/太 are handled by the promotion
gate's `min_promote_length`, so they're still recorded and still visible in review.

## OOV slang fragments — the known limitation

The real-frame test found jieba splits 万魂幡 into 万魂 / 幡, because the slang term
isn't in its dictionary either. The words the user most wants are exactly the ones
off-the-shelf segmenters mishandle. Two fixes, both keeping a human in the loop:

- **manual-mode chip merge** (the user fuses adjacent chips), and
- **a growable user dictionary** — `add_user_word("万魂幡")` makes the merge stick for
  every future snapshot.

Do NOT try to auto-merge adjacent unknown tokens: 幡 alone is a valid word, so
heuristics overreach.
"""
import re

from . import paths
from .logging_setup import get

log = get("segment")

_HAS_CJK = re.compile(r"[一-鿿]")

# Grammatical particles, pronouns, and bare measure words: never a useful card.
# Content words are NOT listed here however common — that's the promotion gate's job.
STOPWORDS = frozenset(
    "的 了 着 过 是 在 有 和 与 或 而 但 就 都 也 不 没 很 我 你 您 他 她 它 们 咱 "
    "这 那 哪 之 吗 呢 吧 啊 呀 哦 嗯 把 被 给 让 对 从 向 往 到 为 以 于 及 等 "
    "个 位 只 条 张 些 里 上 下 中 大 小 多 少 会 能 要 说 去 来 做 太 更 最"
    .split())

_USERDICT_LOADED = False


def userdict_path():
    """Growable slang dictionary — one term per line."""
    return paths.user_dir() / "userdict.txt"


def _ensure_jieba():
    """Import jieba and load the user dictionary once."""
    global _USERDICT_LOADED
    import jieba
    if not _USERDICT_LOADED:
        p = userdict_path()
        if p.exists():
            try:
                jieba.load_userdict(str(p))
                log.info("loaded user dictionary: %s", p)
            except Exception:
                log.exception("could not load user dictionary %s", p)
        _USERDICT_LOADED = True
    return jieba


def add_user_word(word, freq=None):
    """Teach the segmenter a term (e.g. a merged slang word) permanently.

    Applies immediately *and* persists, so 万魂幡 stays one token from now on.
    """
    word = (word or "").strip()
    if not word:
        return False
    jieba = _ensure_jieba()
    try:
        jieba.add_word(word, freq=freq)
    except Exception:
        log.exception("jieba.add_word failed for %r", word)
        return False
    p = userdict_path()
    try:
        existing = set()
        if p.exists():
            existing = {ln.split()[0] for ln in
                        p.read_text(encoding="utf-8").splitlines() if ln.strip()}
        if word not in existing:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{word}\n")
    except OSError:
        log.exception("could not persist user word %r", word)
        return False
    return True


def tokenize(text):
    """Every raw token, unfiltered — what manual mode shows as chips."""
    if not text:
        return []
    return [t for t in _ensure_jieba().lcut(text) if t.strip()]


def keep(token):
    """Is this token worth recording?

    One rule removes punctuation, latin, digits and emoji: a real Chinese word must
    contain a Chinese character.
    """
    if not token or not token.strip():
        return False
    if not _HAS_CJK.search(token):
        return False
    return token not in STOPWORDS


def segment(text):
    """The tokens we actually record for a line of recognized text."""
    return [t for t in tokenize(text) if keep(t)]
