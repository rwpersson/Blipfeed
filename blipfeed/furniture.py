"""Static-text suppression — strips UI chrome before anything gets counted.

**Why this is load-bearing.** UI chrome (browser bar, Douyin's nav, the taskbar,
watermarks) is on screen *constantly*, so it accumulates the highest frequency of
anything — which would invert the whole "frequency = worth learning" premise. Proven
on real frames: a Douyin homepage OCR'd to 114 tokens whose top ranks were pure
chrome (抖音×7, 精选×5) while the actual target word sat at 2.

**Why not a stoplist.** A hardcoded nav-word list would filter real words a user
might want and can't know every app's UI. Chrome isn't identifiable by *which words*
it contains — it's identifiable by being **static**. So we key each OCR line on
`(text, rounded position)` and suppress a key once it has been seen before:

  same text + same place, again      -> furniture, suppress
  same place + different text        -> content (a subtitle band cycling) -> keep
  same text + different place        -> content -> keep
  new text                           -> content -> keep

That auto-calibrates to any app with no configuration, and it never drops a *content*
word: if 游戏 appears as dialogue in the subtitle band it has a different position
(and usually different neighbouring text) from 游戏 in the nav bar, so it is kept.

**It also gives consecutive-text dedup for free.** A subtitle that persists across
two snapshots is the same text in the same box, so the second sighting is suppressed
— which is exactly the "don't count a held subtitle twice" rule.

**The leak invariant (keep this true).** A key is only suppressed from its
`threshold`-th sighting, so chrome leaks at most `threshold - 1` observations before
being silenced. Promotion requires `count >= N` (default 2). So as long as

    threshold - 1  <  N        i.e.   threshold <= N

chrome can **never** reach a card, structurally. Defaults threshold=2, N=2 give a
1-observation leak against a 2-observation gate. Raising `threshold` past `N` breaks
that guarantee — don't, without raising `N` too.

State is per session and **in memory** — persisting it would be storing what we are
about to discard (CLAUDE.md data model).
"""
from .logging_setup import get

log = get("furniture")

DEFAULT_THRESHOLD = 2      # suppress from the 2nd identical sighting
DEFAULT_GRID = 8           # px bucket, so OCR box jitter still keys the same


class FurnitureFilter:
    """Per-session static-text suppressor. Call `filter()` once per snapshot."""

    def __init__(self, threshold=DEFAULT_THRESHOLD, grid=DEFAULT_GRID):
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.threshold = threshold
        self.grid = grid
        self._seen = {}                     # key -> snapshots it appeared in (capped)

    def _key(self, line):
        x, y = line.position
        # floor-division buckets adjacent pixels together, so a box that jitters a
        # few px between OCR runs still resolves to the same key.
        return (line.text, x // self.grid, y // self.grid)

    def is_furniture(self, line):
        return self._seen.get(self._key(line), 0) >= self.threshold - 1

    def filter(self, lines):
        """Return the non-furniture lines, updating state. One call per snapshot."""
        kept, kept_keys = [], set()
        for line in lines:
            key = self._key(line)
            if self._seen.get(key, 0) >= self.threshold - 1:
                continue                    # furniture: seen enough times already
            kept.append(line)
            kept_keys.add(key)
        # count each key at most once per snapshot; suppressed keys stay parked at
        # threshold-1 so the tally stays bounded.
        for key in kept_keys:
            self._seen[key] = self._seen.get(key, 0) + 1
        if len(kept) < len(lines):
            log.debug("furniture: suppressed %d/%d lines",
                      len(lines) - len(kept), len(lines))
        return kept

    def reset(self):
        self._seen.clear()

    @property
    def tracked(self):
        """How many distinct (text, position) keys we're tracking."""
        return len(self._seen)
