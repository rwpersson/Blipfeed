"""Blipfeed entry point — run an immersion capture session.

    python -m blipfeed                  # AUTO: headless capture on a timer
    python -m blipfeed --manual         # MANUAL: hotkey grabs a frame, you pick chips
    python -m blipfeed --region         # redraw the capture region first
    python -m blipfeed --once           # a single tick, then exit (diagnostics)
    python -m blipfeed --status         # what's in the store; capture nothing

AUTO mode draws nothing and says nothing on screen (Decisions #6) — the console
readout is for when you're watching, and the hotkeys are how you drive it while a game
has focus. Cards land in Blip's deck; **Blip picks them up on its next launch**, not
live.
"""
import sys
import threading

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import blipfeed  # noqa: F401

    __package__ = "blipfeed"

from . import capture, config as config_mod, hotkeys, session as session_mod, store  # noqa: E402
from .logging_setup import get  # noqa: E402

log = get("main")


def _fmt_tick(result):
    if result.error:
        return f"  ! tick failed: {result.error}"
    if result.skipped:
        return "  · unchanged — skipped"
    bits = [f"{result.lines} lines"]
    if result.suppressed:
        bits.append(f"{result.suppressed} chrome")
    bits.append(f"{result.observations} words")
    line = "  · " + ", ".join(bits)
    if result.promoted:
        line += f"   ★ carded: {', '.join(result.promoted)}"
    return line


def _resolve_region(cfg, capturer, want_picker):
    """The region to capture, picking one if asked or if none is saved."""
    region = cfg.region()
    if want_picker or region is None:
        if region is None and not want_picker:
            print("No capture region saved yet — pick one.")
        from .ui.region import pick_region
        picked = pick_region(current=region)
        if picked is not None:
            region = picked
            cfg.set_region(region).save()
            print(f"region saved: {region.label()}")
        elif region is None:
            region = capturer.bottom_third(cfg.monitor)
            print(f"cancelled — using the bottom third: {region.label()}")
    return region


def _show_status(conn, cfg):
    rows = list(conn.execute(
        "SELECT status, COUNT(*) n, SUM(count) seen FROM terms GROUP BY status"))
    if not rows:
        print("store is empty — nothing mined yet.")
        return 0
    print("terms by status:")
    for r in rows:
        print(f"  {r['status']:<10} {r['n']:>5} terms  ({r['seen']} sightings)")
    for which, label in (("needs_def", "Needs a definition"), ("ready", "Ready")):
        top = store.bucket(conn, which, min_count=cfg.min_count, limit=8)
        shown = ", ".join(f"{r['term']}({r['count']})" for r in top) or "—"
        print(f"\n{label} (top): {shown}")
    return 0


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    capture.set_dpi_aware()                       # before any window or capture

    cfg = config_mod.load()
    for problem in cfg.validate():
        print(f"config problem: {problem}")
    if "--manual" in argv:
        cfg.set("mode", "manual")

    conn = store.connect()
    if "--status" in argv:
        return _show_status(conn, cfg)

    capturer = capture.Capturer()
    region = _resolve_region(cfg, capturer, want_picker="--region" in argv)

    sess = session_mod.CaptureSession(conn, cfg, region, capturer=capturer,
                                      label=f"session {region.label()}")
    stop = threading.Event()

    # Only one picker window at a time: each creates its own Tk root, and a second
    # live interpreter in one process is the bug Blip documents (gotcha #13).
    ui_lock = threading.Lock()

    def _in_ui_thread(fn, what):
        """Run a window off the hotkey thread (the pump must never block), and never
        two at once."""
        def wrapper():
            if not ui_lock.acquire(blocking=False):
                print(f"\n  ({what} ignored — a window is already open)", flush=True)
                return
            try:
                fn()
            except Exception:
                log.exception("%s failed", what)
            finally:
                ui_lock.release()
        threading.Thread(target=wrapper, daemon=True).start()

    def do_manual():
        _in_ui_thread(lambda: _run_manual(sess), "manual capture")

    def do_toggle():
        print("\nstopping…")
        stop.set()

    def do_region():
        def redraw():
            from .ui.region import pick_region
            picked = pick_region(current=sess.region)
            if picked is None:
                return
            sess.region = picked
            sess.detector.reset(sess.source)   # new region, old frame hash is moot
            sess.filter.reset()                # and its chrome is different chrome
            cfg.set_region(picked).save()
            print(f"\n  region is now {picked.label()}", flush=True)
        _in_ui_thread(redraw, "redraw region")

    listener = hotkeys.HotkeyListener({
        cfg.hotkey_toggle: do_toggle,
        cfg.hotkey_manual: do_manual,
        cfg.hotkey_region: do_region,
    })
    listener.start()
    listener.wait_ready()
    for combo, why in listener.failed.items():
        print(f"hotkey {combo} unavailable: {why}")

    print(f"Blipfeed — {cfg.mode.upper()} mode")
    print(f"  region   {region.label()}")
    print(f"  every    {cfg.interval_seconds}s   (N={cfg.min_count}, "
          f"K={cfg.max_promotions_per_snapshot}, "
          f"auto_card={'on' if cfg.auto_card else 'off'})")
    print(f"  hotkeys  {cfg.hotkey_toggle} stop · {cfg.hotkey_manual} capture now")
    print("  cards go to Blip; Blip shows them on its NEXT launch.\n")

    try:
        if "--once" in argv:
            print(_fmt_tick(sess.tick()))
        elif cfg.mode == "manual":
            print(f"Manual mode: press {cfg.hotkey_manual} to capture. Ctrl+C to quit.")
            while not stop.is_set():
                stop.wait(0.5)
        else:
            sess.run(stop_event=stop, on_tick=lambda r: print(_fmt_tick(r), flush=True))
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        listener.stop()
        summary = sess.summary()
        sess.close()
        conn.close()
        print(f"\nsession {summary['session_id']}: {summary['ticks']} ticks, "
              f"{summary['observations']} words seen, "
              f"{len(summary['promoted'])} carded"
              + (f" ({', '.join(summary['promoted'])})" if summary["promoted"] else ""))
        print("Review what was mined:  python -m blipfeed --status")
    return 0


def _run_manual(sess):
    from .ui.pick import pick_tokens
    try:
        img, lines = sess.capture_lines()
        picked = pick_tokens(img, lines)
        if picked:
            sess.record_manual(picked["tokens"], context=picked.get("context", ""))
            print(f"  + recorded: {', '.join(picked['tokens'])}", flush=True)
    except Exception:
        log.exception("manual capture failed")


if __name__ == "__main__":
    sys.exit(main())
