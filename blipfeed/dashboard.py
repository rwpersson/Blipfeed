"""The dashboard — Blipfeed's home. Controller (no Tk), tested directly.

Until now Blipfeed was a pile of commands with no front door: no Start/Stop, no visible
state, knobs in a JSON file. This is the hub `python -m blipfeed` opens. The window
(`ui/dashboard_window.py`) is a dumb shell over this controller — Blip's editor/review
pattern — so the behaviour that matters is tested without a screen or a real capture.

## Run model (locked with the user)

The dashboard owns the Tk main thread. **AUTO capture runs headless on a background
thread**, so it keeps working while you game and the window is just the control surface
(CLAUDE.md Decisions #6). The capture thread has its **own** SQLite connection; the
dashboard reads stats on its own connection. That's safe because the store is WAL
(store.connect) — one writer + concurrent readers — so neither blocks the other and no
cross-thread connection sharing is needed.

MANUAL mode has no loop: a capture is a one-shot grab → pick → record, on the main
thread.

**A manual capture may happen while AUTO is running** (the capture-now hotkey — grabbing
a word you just saw is exactly what it's for, so demanding you stop mining first defeats
it). The two sessions coexist because nothing mutable is shared: each owns its `Capturer`
(mss is per-thread, and each instance is lazily created on the thread that uses it), its
own WAL connection, and its own furniture state; only the auto loop promotes. The one
real shared resource is the OCR engine singleton, which serializes inference internally.
"""
import threading

from . import session as session_mod, store
from .capture import Capturer
from .logging_setup import get

log = get("dashboard")


class NoRegion(Exception):
    """Start was requested with no capture region chosen yet."""


class DashboardController:
    def __init__(self, cfg, conn=None, make_session=None):
        self.cfg = cfg
        self.conn = conn or store.connect()          # dashboard's own read/write conn
        self._make_session = make_session or self._default_make_session
        self._thread = None
        self._stop = None
        self._session = None                          # the live capture session, if any
        self._manual_session = None
        self.last_error = None

    # ------------------------------------------------------------- lifecycle
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Begin AUTO capture on a background thread. Raises NoRegion if unset."""
        if self.running():
            return
        region = self.cfg.region()
        if region is None:
            raise NoRegion("pick a capture region first")
        # the warm manual session is deliberately kept: capture-now must stay instant
        # while AUTO runs (an explicit mode/region change still tears it down)
        self.last_error = None
        self._stop = threading.Event()
        self._session = self._make_session(self.cfg, region)
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()
        log.info("capture started on %s", region.label())

    def _run(self):
        try:
            self._session.run(stop_event=self._stop)
        except Exception as exc:                      # a crash must not kill the app
            log.exception("capture loop crashed")
            self.last_error = str(exc)
        finally:
            try:
                self._session.close()
            except Exception:
                log.exception("closing capture session")

    def stop(self, timeout=8):
        if self._stop is not None:
            self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        self._session = None
        log.info("capture stopped")

    def toggle(self):
        self.stop() if self.running() else self.start()

    # -------------------------------------------------------- manual capture
    def capture_manual(self, pick_fn):
        """One-shot manual grab → pick → record. `pick_fn(img, lines)` shows the chip
        picker (main thread) and returns {tokens, context} or None. Returns the tokens
        recorded (possibly []). Keeps a warm session between captures so OCR isn't
        re-initialised each time.

        **Works while AUTO capture is running** — grabbing a word you just saw is the
        whole point of the capture-now hotkey, and needing to stop mining first defeats
        it. The two coexist safely: separate `Capturer`s (mss is per-thread, and each is
        first used on its own thread), separate WAL connections, per-session furniture
        state, and only the auto loop promotes. The one genuinely shared thing is the
        OCR engine singleton, which serializes inference under its own lock.
        """
        sess = self._ensure_manual_session()
        if sess is None:
            return []
        img, lines = sess.capture_lines()
        picked = pick_fn(img, lines)
        if not picked or not picked.get("tokens"):
            return []
        sess.record_manual(picked["tokens"], picked.get("context", ""))
        return picked["tokens"]

    def _ensure_manual_session(self):
        if self._manual_session is None:
            region = self.cfg.region()
            if region is None:
                raise NoRegion("pick a capture region first")
            self._manual_session = self._make_session(self.cfg, region)
        return self._manual_session

    def _close_manual(self):
        if self._manual_session is not None:
            try:
                self._manual_session.close()
            except Exception:
                log.exception("closing manual session")
            self._manual_session = None

    # -------------------------------------------------------------- settings
    def set_auto_card(self, on):
        self.cfg.set("auto_card", bool(on)).save()

    def set_mode(self, mode):
        if mode not in ("auto", "manual"):
            raise ValueError(mode)
        self.stop()                                   # exclusive: tear down the other
        self._close_manual()
        self.cfg.set("mode", mode).save()

    def set_region(self, region):
        self.cfg.set_region(region).save()
        # a new region means a fresh manual session next time (different chrome)
        self._close_manual()

    def sync_known(self):
        from . import known
        return known.sync_all(self.conn, self.cfg.language,
                              self.cfg.get("blip_graduate_at", 9))

    # ---------------------------------------------------------------- stats
    def stats(self):
        """Everything the dashboard shows. Cheap enough to call on a ~1s timer:
        lifetime numbers are indexed COUNT queries; session numbers are read off the
        live session object."""
        from . import frequency
        by_status = {r["status"]: r["n"] for r in self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM terms GROUP BY status")}
        # the actionable (primary) counts — the same numbers the review window shows,
        # so the dashboard badge and the queue never disagree
        needs = sum(1 for c in frequency.review_queue(self.conn, self.cfg, "needs_def")
                    if c.tier == "primary")
        ready = sum(1 for c in frequency.review_queue(self.conn, self.cfg, "ready")
                    if c.tier == "primary")
        sess = self._session or self._manual_session
        session = sess.summary() if sess is not None else None
        return {
            "running": self.running(),
            "mode": self.cfg.mode,
            "auto_card": bool(self.cfg.auto_card),
            "region": self.cfg.region(),
            "needs_def": needs,
            "ready": ready,
            "known": by_status.get("known", 0),
            "carded": by_status.get("carded", 0),
            "seen_terms": sum(by_status.values()),
            "session": session,
            "error": self.last_error,
        }

    # --------------------------------------------------------------- factory
    def _default_make_session(self, cfg, region):
        """A real CaptureSession with its OWN connection (WAL lets it write while the
        dashboard reads)."""
        conn = store.connect()
        return session_mod.CaptureSession(
            conn, cfg, region, capturer=Capturer(),
            label=f"session {region.label()}")

    # --------------------------------------------------------------- cleanup
    def close(self):
        self.stop()
        self._close_manual()
