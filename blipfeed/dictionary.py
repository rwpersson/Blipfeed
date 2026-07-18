"""CC-CEDICT lookup — meanings, the `dict_ok` signal, and both script forms.

Named `dictionary` rather than `dict` so it never shadows the builtin at a call site.

Three jobs:

1. **`meaning`** — a Blip card is invalid without one (Blip's validation rejects it),
   so this is what makes a mined term cardable at all.
2. **`dict_ok`** — a headword hit means "a real word", which filters OCR *garbage*
   (一音, 看部). It emphatically does **not** filter UI chrome: 游戏/音乐/影视 are all
   valid entries, which is why furniture detection exists (spikes/FINDINGS.md).
3. **Both script forms** — every entry carries traditional and simplified, so a hit
   fills the card's `alt` field for free (the user wants to memorize both).

A **miss is not garbage**. On real content the words worth learning skew heavily to
the ones CC-CEDICT lacks — slang, xianxia terms, titles, fresh compounds (万魂幡,
婚姻危机, 凡人歌 all miss). A miss means "no offline meaning yet", which routes the
term to the *Needs a definition* bucket for enrichment — never to the bin.

Load is lazy and cached (~121k entries, ~0.3s). Absent dictionary degrades to "no
hits", which is survivable: everything routes to needs-a-definition.
"""
import re
from dataclasses import dataclass

from . import paths
from .logging_setup import get

log = get("dictionary")

# trad simp [pin1 yin1] /gloss/gloss/
_LINE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.+)/\s*$")

# Glosses that are poor card definitions when a better one exists.
_WEAK = ("variant of", "old variant", "surname ", "see ", "abbr. for",
         "used in", "(same as")

_INDEX = None


@dataclass(frozen=True)
class Entry:
    simplified: str
    traditional: str
    pinyin: str                  # numbered, as CC-CEDICT stores it ("xue2 xi2")
    glosses: tuple

    @property
    def alt(self):
        """The other script form, or None when both are identical."""
        return self.traditional if self.traditional != self.simplified else None

    def meaning(self, max_glosses=2, max_len=90):
        """A short English gloss suitable for a flashcard."""
        strong = [g for g in self.glosses
                  if not any(g.lower().startswith(w) for w in _WEAK)]
        picked = (strong or list(self.glosses))[:max_glosses]
        out = "; ".join(picked)
        if len(out) > max_len:
            out = out[:max_len].rsplit(";", 1)[0].rstrip(" ;") or out[:max_len]
        return out


def _load(path=None):
    """simplified/traditional -> Entry. First entry per headword wins.

    Homographs (行 xing2 'to go' vs hang2 'row') collapse to the first entry rather
    than merging senses, which would produce a confusing mixed definition. The
    reading comes from pypinyin (context-aware) anyway, and the user can edit.
    """
    path = path or paths.cedict_file()
    index = {}
    if not path.exists():
        log.warning("CC-CEDICT not found at %s — every term will need a definition",
                    path)
        return index
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                m = _LINE.match(line.rstrip("\n"))
                if not m:
                    continue
                trad, simp, pinyin, glosses = m.groups()
                entry = Entry(simp, trad, pinyin,
                              tuple(g for g in glosses.split("/") if g))
                index.setdefault(simp, entry)
                index.setdefault(trad, entry)
    except OSError:
        log.exception("failed reading CC-CEDICT")
        return {}
    log.info("CC-CEDICT loaded: %d headwords", len(index))
    return index


def index():
    global _INDEX
    if _INDEX is None:
        _INDEX = _load()
    return _INDEX


def lookup(term, idx=None):
    """The Entry for a term (either script), or None. `idx` overrides for tests."""
    if not term:
        return None
    return (index() if idx is None else idx).get(term)


def has(term, idx=None):
    return lookup(term, idx) is not None


def fields_for(term, idx=None):
    """Card fields CC-CEDICT can supply: meaning, alt, dict_ok.

    A miss returns dict_ok=0 and no meaning — the term still gets stored and ranked,
    it just lands in the *Needs a definition* bucket.
    """
    entry = lookup(term, idx)
    if entry is None:
        return {"dict_ok": 0}
    out = {"dict_ok": 1, "meaning": entry.meaning()}
    if entry.alt:
        out["alt"] = entry.alt
    return out
