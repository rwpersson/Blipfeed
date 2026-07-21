"""The capture loop — one immersion sitting, tick by tick.

This is where every previous phase meets:

    grab region -> changed? -> OCR -> furniture -> ingest -> promote -> Blip deck

**AUTO mode is headless** (CLAUDE.md Decisions #6): nothing here creates a window or
prints to the screen, so it can run while the user games. It is also **silent** — no
toast when a word is carded, because that is exactly the interruption we're avoiding.
Words accumulate; the user reviews later.

`tick()` is one full cycle and returns what happened, so the loop can be driven and
asserted in tests without a screen, a timer, or the OCR stack — everything it depends
on (capturer, OCR provider, furniture filter, clock) is injectable.

Order inside a tick matters:

1. **change detection first** — an unchanged frame skips OCR entirely, which is the
   cheap win on a paused video or a static article.
2. **furniture before ingest** — chrome must never be counted (it would dominate the
   frequency ranking).
3. **promote after ingest** — a term can cross the threshold on the same tick that
   pushed it over.
"""
import threading
from dataclasses import dataclass, field

from . import blip_link, capture, frequency, furniture, ocr as ocr_mod, store
from .logging_setup import get

log = get("session")


@dataclass
class TickResult:
    """What one snapshot cycle did. Counts are for the panel/console readout."""
    skipped: bool = False               # frame unchanged -> no OCR
    lines: int = 0                      # OCR lines recognized this snapshot
    chrome: int = 0                     # on-screen lines judged persistent furniture
    recorded: int = 0                   # appearance episodes that ENDED and were kept
    observations: int = 0               # tokens recorded
    promoted: list = field(default_factory=list)   # terms carded this tick
    error: str = None


class CaptureSession:
    """One immersion sitting. Owns the per-session furniture state and counters."""

    def __init__(self, conn, cfg, region, *, capturer=None, provider=None,
                 detector=None, filt=None, tracker=None, deck_writer=None,
                 source="region", session_id=None, label=None):
        self.conn = conn
        # The capture loop ticks on the main thread while a hotkey-triggered manual
        # capture records from a worker. sqlite3 is opened with check_same_thread=
        # False, so serializing here is what keeps that safe rather than corrupt.
        self.lock = threading.RLock()
        self.cfg = cfg
        self.region = region
        self.source = source
        self.capturer = capturer if capturer is not None else capture.Capturer()
        self.provider = (provider if provider is not None
                         else ocr_mod.get_provider(cfg.get("ocr_provider")))
        self.provider.configure(box_thresh=cfg.get("ocr_box_thresh"),
                                text_score=cfg.get("ocr_text_score"),
                                unclip_ratio=cfg.get("ocr_unclip_ratio"))
        self.detector = detector if detector is not None else capture.ChangeDetector()
        # Furniture state is per session and in memory, by design. AUTO ingestion
        # uses the EpisodeTracker (records a line when its appearance ends, so
        # persistent chrome is never recorded at all); MANUAL mode keeps the
        # immediate FurnitureFilter, because a chip picker can't wait for an episode
        # to finish — it has to de-clutter the frame right now.
        self.count_mode = cfg.get("count_mode", "sightings")
        self.tracker = tracker if tracker is not None else furniture.EpisodeTracker(
            max_ticks=cfg.get("episode_max_ticks", 3))
        self.filter = filt if filt is not None else furniture.FurnitureFilter(
            threshold=cfg.furniture_threshold)
        self.deck_writer = (deck_writer if deck_writer is not None
                            else blip_link.exporter(conn, cfg))
        self.session_id = (session_id if session_id is not None
                           else store.start_session(conn, label,
                                                    language=cfg.language,
                                                    mode=cfg.mode))
        self.ticks = 0
        self.total_observations = 0
        self.total_promoted = []

    # ------------------------------------------------------------------ tick
    def grab(self):
        """Capture the region and say whether it changed since last time."""
        img = self.capturer.grab(self.region)
        changed, digest = self.detector.changed(self.source, img)
        return img, changed, digest

    def _record(self, lines, digest=None):
        """Store a batch of ended episodes and run the promotion gate."""
        if not lines:
            return 0, []
        snapshot_id = store.add_snapshot(
            self.conn, self.session_id, source=self.source,
            region=self.region.label(), image_hash=digest,
            raw_text=ocr_mod.joined_text(lines), ocr_engine=self.provider.name)
        ids = frequency.ingest(self.conn, lines, session_id=self.session_id,
                               snapshot_id=snapshot_id, language=self.cfg.language)
        self.conn.commit()
        promoted = frequency.promote(self.conn, self.cfg,
                                     deck_writer=self.deck_writer)
        self.total_observations += len(ids)
        self.total_promoted += [r["term"] for r in promoted]
        return len(ids), [r["term"] for r in promoted]

    def tick(self):
        """One full cycle. Never raises — a bad tick must not kill the session.

        Ingestion is **lossless by default** (`count_mode='sightings'`): every
        recognized line is recorded, chrome included. "打开 seen 150×" is true data —
        that word really is ubiquitous — and discarding it would be solving a
        *promotion* problem at the *ingestion* layer, which is exactly the split this
        app is built around. Chrome is kept out of your deck by the promotion gate's
        ubiquity ceiling (`frequency.is_promotable`), not by refusing to look at it.

        `count_mode='episodes'` is the alternative: record one appearance-episode per
        line instead of one per snapshot, which counts encounters rather than screen
        time. Off by default.
        """
        self.ticks += 1
        try:
            with self.lock:
                img, changed, digest = self.grab()
                if not changed:
                    # Nothing moved, so no episode can have ended either.
                    log.debug("tick %d: unchanged, skipping OCR", self.ticks)
                    return TickResult(skipped=True)

                lines = self.provider.recognize(img)
                # The tracker runs either way: in 'episodes' mode it decides what to
                # record; in the default 'sightings' mode it only supplies the
                # "how much of this screen is ambient" readout.
                ended = self.tracker.update(lines)
                to_record = ended if self.count_mode == "episodes" else lines
                n_obs, promoted = self._record(to_record, digest)
                return TickResult(lines=len(lines), chrome=self.tracker.persistent,
                                  recorded=len(to_record), observations=n_obs,
                                  promoted=promoted)
        except Exception as exc:
            # A capture/OCR hiccup must not end the sitting; log it and tick on.
            log.exception("tick %d failed", self.ticks)
            return TickResult(error=str(exc))

    # ------------------------------------------------------- manual capture
    def capture_lines(self):
        """One frame's recognized lines, for MANUAL mode's chip picker.

        Deliberately does NOT ingest: in manual mode the user decides what counts.
        Skips change detection too — an explicit request should always produce a
        frame, even if the screen hasn't moved. Uses the immediate FurnitureFilter
        rather than episodes, because the picker must de-clutter the frame *now*.
        """
        with self.lock:
            img = self.capturer.grab(self.region)
            lines = self.provider.recognize(img)
            return img, self.filter.filter(lines)

    def record_manual(self, tokens, context=""):
        """Record exactly the chips the user picked (already merged if they merged).

        Runs on a worker thread (the hotkey pump must not block), hence the lock —
        without it sqlite3 raised a thread-affinity error and the user's picked words
        were lost.
        """
        if not tokens:
            return []
        with self.lock:
            snapshot_id = store.add_snapshot(
                self.conn, self.session_id, source=self.source,
                region=self.region.label(), raw_text=context,
                ocr_engine=self.provider.name)
            ids = frequency.ingest_line(
                self.conn, context or "".join(tokens), tokens=tokens,
                session_id=self.session_id, snapshot_id=snapshot_id,
                language=self.cfg.language, manual=True)
            self.conn.commit()
            self.total_observations += len(ids)
            return ids

    # ------------------------------------------------------------------ loop
    def run(self, stop_event=None, on_tick=None, max_ticks=None):
        """Tick every `interval_seconds` until stopped.

        `stop_event.wait(interval)` rather than sleep(), so stopping is immediate
        instead of waiting out the interval.
        """
        stop_event = stop_event or threading.Event()
        interval = self.cfg.interval_seconds
        log.info("session %s started: %s every %ss (mode=%s)",
                 self.session_id, self.region.label(), interval, self.cfg.mode)
        while not stop_event.is_set():
            result = self.tick()
            if on_tick is not None:
                on_tick(result)
            if max_ticks is not None and self.ticks >= max_ticks:
                break
            if stop_event.wait(interval):
                break
        return self.summary()

    def close(self):
        """End the sitting.

        In 'episodes' mode the last thing on screen never got to end, so it is flushed
        or it would be lost. In the default 'sightings' mode every line was already
        recorded as it was seen, so there is nothing to flush.
        """
        with self.lock:
            try:
                if self.count_mode == "episodes":
                    leftovers = self.tracker.flush()
                    if leftovers:
                        self._record(leftovers)
            except Exception:
                log.exception("flushing the final episodes")
            store.end_session(self.conn, self.session_id)
        try:
            self.capturer.close()
        except Exception:
            log.exception("closing capturer")

    def summary(self):
        return {"session_id": self.session_id, "ticks": self.ticks,
                "observations": self.total_observations,
                "promoted": list(self.total_promoted)}
