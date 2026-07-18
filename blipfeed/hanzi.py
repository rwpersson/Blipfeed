"""Traditional ↔ simplified handling — the only place that knows the two scripts.

Frequency dedup needs 學習 and 学习 to be *one* term. We canonicalize to simplified
and use that as the store's key; the other form rides along as `alt` so a Blip card
can show both (the user asked for both, to memorize each).

The trad→simp map is derived from **CC-CEDICT itself** — every entry carries the
traditional and simplified forms of equal length, so zipping them character-by-
character across the whole dictionary yields a comprehensive char map with no extra
dependency (no OpenCC). Trad→simp is overwhelmingly many-to-one, so the per-character
majority mapping is unambiguous in practice.

Degrades gracefully: if CC-CEDICT isn't present (a fresh clone — it's gitignored),
the map is empty and canonicalize() is the identity, so nothing breaks; dedup across
scripts just doesn't happen until the dictionary is there.
"""
from collections import Counter, defaultdict

from . import paths
from .logging_setup import get

log = get("hanzi")

_MAP = None                          # trad_char -> simp_char (only chars that differ)


def _load_map(path=None):
    path = path or paths.cedict_file()
    if not path.exists():
        log.warning("CC-CEDICT not found at %s — trad→simp dedup disabled", path)
        return {}
    votes = defaultdict(Counter)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or " " not in line:
                    continue
                trad, _, rest = line.partition(" ")
                simp, _, _ = rest.partition(" ")
                if len(trad) != len(simp):
                    continue
                for t, s in zip(trad, simp):
                    if t != s:
                        votes[t][s] += 1
    except OSError:
        log.exception("failed reading CC-CEDICT for trad→simp map")
        return {}
    m = {t: c.most_common(1)[0][0] for t, c in votes.items()}
    log.info("trad→simp map built: %d characters", len(m))
    return m


def _map():
    global _MAP
    if _MAP is None:
        _MAP = _load_map()
    return _MAP


def to_simplified(text, mapping=None):
    m = mapping if mapping is not None else _map()
    if not m:
        return text
    return "".join(m.get(c, c) for c in text)


def canonicalize(term, language="zh-CN", mapping=None):
    """(canonical, alt) for a term.

    For Chinese, canonical = the simplified form (the dedup key); alt = the input
    itself when it was traditional (differs from canonical), else None — the
    authoritative traditional form for a word first seen simplified is filled later
    from CC-CEDICT at enrichment. For non-Chinese, identity with no alt.
    """
    if not term or not language or not language.lower().startswith("zh"):
        return term, None
    simp = to_simplified(term, mapping)
    alt = term if simp != term else None
    return simp, alt
