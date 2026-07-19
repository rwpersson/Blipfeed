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


class EpisodeTracker:
    """Records a line once, when its on-screen appearance **ends**.

    `FurnitureFilter` (above) counts a line on first sight and suppresses repeats.
    A real session proved that leaks: the leak is per `(text, position)` key, **not
    per word**, so a word appearing in two different chrome lines (Douyin's
    "是否要打开声音？" prompt *and* its 打开声音 button) leaked one observation each,
    reached count 2, and got carded. The invariant as originally stated was wrong.

    This fixes it with a better discriminator: **chrome persists, content comes and
    goes.** Track what is on screen; when a line disappears, that appearance episode
    is over and gets recorded *once*. Consequences:

    - Persistent chrome (nav, sidebar, watermark, "更新至第9集") never disappears while
      you're on the page, so it is **never recorded at all** — a true zero leak, not a
      bounded one.
    - A subtitle shown for one snapshot is recorded once when it goes.
    - A subtitle *held* across several snapshots is still recorded **once**, not once
      per snapshot — the held-subtitle dedup, for free and correct.
    - A line that outlives `max_ticks` snapshots is treated as furniture and dropped
      even if it eventually disappears (you navigated away).

    Known remaining leak: a *transient* overlay (a toast that shows once) is
    indistinguishable from content by persistence alone, so it can still be recorded
    once. Region targeting is the defence there — keep the capture box on the subtitle
    band, not the whole window.

    State is per session and in memory, by design.
    """

    def __init__(self, grid=DEFAULT_GRID, max_ticks=3):
        self.grid = grid
        self.max_ticks = max_ticks          # longer than this on screen == furniture
        self._onscreen = {}                 # key -> [line, snapshots_seen]

    def _key(self, line):
        x, y = line.position
        return (line.text, x // self.grid, y // self.grid)

    def update(self, lines):
        """Feed one snapshot's lines; return the lines whose episode just ended."""
        current = {self._key(ln): ln for ln in lines}
        ended = [entry[0] for key, entry in self._onscreen.items()
                 if key not in current and entry[1] <= self.max_ticks]
        merged = {}
        for key, line in current.items():
            prior = self._onscreen.get(key)
            merged[key] = [line, (prior[1] if prior else 0) + 1]
        self._onscreen = merged
        return ended

    def flush(self):
        """End-of-session: record what's still on screen, if it looks like content."""
        out = [entry[0] for entry in self._onscreen.values()
               if entry[1] <= self.max_ticks]
        self._onscreen.clear()
        return out

    @property
    def persistent(self):
        """On-screen lines that have outlived `max_ticks` — i.e. chrome."""
        return sum(1 for entry in self._onscreen.values()
                   if entry[1] > self.max_ticks)

    @property
    def onscreen(self):
        return len(self._onscreen)

    def reset(self):
        self._onscreen.clear()
