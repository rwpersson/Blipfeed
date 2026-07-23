"""Blipfeed entry point.

    python -m blipfeed                  # open the dashboard (the home window)
    python -m blipfeed --status [--all] # what's in the store; capture nothing
    python -m blipfeed --clean [--yes]  # re-judge past cards against today's gate
    python -m blipfeed --known-sync     # learn what you already know, from Blip
    python -m blipfeed --known-import F # bulk-import a level list (HSK, frequency…)
    python -m blipfeed --known-forget W # put a word back in play

With no arguments this opens the **dashboard** (`ui/dashboard_window.py`), which is the
home for everything: Start/Stop, mode + auto-card toggles, the capture region, live
stats, and Review. AUTO capture runs headless on a background thread behind it
(Decisions #6). The commands above stay for the terminal — no window, capture nothing.
"""
import sys

if __package__ in (None, ""):                       # run-as-a-script guard
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import blipfeed  # noqa: F401

    __package__ = "blipfeed"

from . import capture, config as config_mod, paths, store  # noqa: E402
from .logging_setup import get  # noqa: E402

log = get("main")


def _clean(conn, cfg, argv):
    """Re-judge every carded term against the current gate; un-card the ambient ones.

    The gate has been corrected twice, and a deck minted under an older, wronger
    version keeps its mistakes. This re-runs today's rules over past decisions and
    removes what would no longer qualify — from the store *and* the Blip deck.
    Dry-run by default: it prints what it would do unless you pass --yes.
    """
    from . import blip_link, frequency

    doomed = []
    for row in store.carded_terms(conn, cfg.language):
        df, window = store.term_snapshot_stats(conn, row["id"])
        if not window:
            continue
        if df < frequency.required_snapshots(row, cfg):
            doomed.append((row, f"only {df} snapshot(s)"))
        elif frequency.ubiquity(df, window) > cfg.max_ubiquity:
            doomed.append((row, f"ambient — in {frequency.ubiquity(df, window):.0%} "
                                f"of its {window} snapshots"))
    if not doomed:
        print("nothing to clean — every carded term still passes the gate.")
        return 0

    print(f"{len(doomed)} carded term(s) would not qualify under the current gate:")
    for row, why in doomed:
        print(f"  {row['term']:<8} {why}")
    if "--yes" not in argv:
        print("\ndry run — re-run with --yes to un-card these and remove them "
              "from the Blip deck.")
        return 0

    path = blip_link.deck_path(cfg.blip_deck_id)
    deck = blip_link.load_deck(path, cfg.blip_deck_id, language=cfg.language)
    removed = blip_link.remove_terms(deck, [r["term"] for r, _ in doomed])
    blip_link.write_deck(deck, path)
    for row, _why in doomed:
        store.uncard(conn, row["id"])
    conn.commit()
    print(f"\nun-carded {len(doomed)}; removed {len(removed)} card(s) from {path}")
    print("Blip will reflect this on its next launch.")
    return 0


def _known(conn, cfg, argv):
    """Manage the known-words set — what the user already has.

    No list at all is the beginner default (everything surfaces), so this only ever
    *adds* evidence; it never assumes a level on the user's behalf.
    """
    from pathlib import Path

    from . import known

    if "--known-import" in argv:
        path = Path(argv[argv.index("--known-import") + 1])
        if not path.exists():
            print(f"no such file: {path}")
            return 1
        created, updated = known.from_file(conn, path, cfg.language)
        print(f"imported {path.name}: {created} new, {updated} marked known")
    elif "--known-forget" in argv:
        term = argv[argv.index("--known-forget") + 1]
        ok = known.forget(conn, term, cfg.language)
        print(f"{term}: back in play" if ok else f"{term}: not in the store")
    else:                                    # --known-sync
        result = known.sync_all(conn, cfg.language, cfg.get("blip_graduate_at", 9))
        d_new, d_upd = result["blip_decks"]
        g_new, g_upd = result["blip_graduated"]
        # Report set size AND newly-marked separately: the graduated words are a
        # subset of the deck words, so "newly marked: 0" is normal and must not be
        # printed in a way that reads as "you've learned nothing".
        print(f"Blip decks      : {result['deck_total']} term(s) you already have "
              f"cards for  (+{d_new + d_upd} newly marked)")
        print(f"Blip graduated  : {result['graduated_total']} term(s) Blip considers "
              f"learned  (+{g_new + g_upd} newly marked)")
        print(f"known total     : {result['total_known']}")
    return 0


def _show_status(conn, cfg, argv=()):
    rows = list(conn.execute(
        "SELECT status, COUNT(*) n, SUM(count) seen FROM terms GROUP BY status"))
    if not rows:
        print("store is empty — nothing mined yet.")
        return 0
    print("terms by status:")
    for r in rows:
        print(f"  {r['status']:<10} {r['n']:>5} terms  ({r['seen']} sightings)")
    n_known = store.known_count(conn, cfg.language)
    if not n_known:
        print("\nknown words: none yet — assuming beginner, so everything surfaces."
              "\n  run --known-sync to import what you already have in Blip.")
    else:
        print(f"\nknown words: {n_known} (these never surface or card)")
    from . import frequency

    verbose = "--all" in argv
    for which, label in (("needs_def", "Needs a definition"), ("ready", "Ready")):
        queue = frequency.review_queue(conn, cfg, which)
        primary = [c for c in queue if c.tier == "primary"]
        secondary = [c for c in queue if c.tier == "secondary"]

        print(f"\n{label} — {len(primary)} worth a look:")
        for c in primary[:10]:
            gloss = (c.meaning or "")[:38]
            print(f"   {c.term:<8} seen {c.count:<4} {gloss}")
        if not primary:
            print("   —")

        # Collapsed, never hidden: 'ambient' and 'single character' are judgements
        # the user is entitled to overrule, so they stay listable and actionable.
        if secondary:
            print(f"   … plus {len(secondary)} filtered out "
                  f"({'--all to list' if not verbose else 'shown below'})")
            if verbose:
                for c in secondary[:20]:
                    print(f"     {c.term:<8} seen {c.count:<4} "
                          f"— {'; '.join(c.reasons)}")
    return 0


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    capture.set_dpi_aware()                       # before any window or capture

    cfg = config_mod.load()
    for problem in cfg.validate():
        print(f"config problem: {problem}")

    # Terminal commands: do their thing and exit, no window.
    if any(a in argv for a in ("--status", "--clean")) or             any(a.startswith("--known") for a in argv):
        conn = store.connect()
        try:
            if "--status" in argv:
                return _show_status(conn, cfg, argv)
            if "--clean" in argv:
                return _clean(conn, cfg, argv)
            return _known(conn, cfg, argv)
        finally:
            conn.close()

    # First launch: onboarding runs first, in its own process so the dashboard opens
    # on a clean root afterwards (one Tk root per process). "Already onboarded" is the
    # sentinel file OR the config flag — the sentinel is authoritative because config
    # gets rewritten by several paths and could drop the flag; the config flag is kept
    # as a fallback and self-heals anyone who onboarded before the sentinel existed.
    marker = paths.onboarded_marker()
    if not (marker.exists() or cfg.get("onboarded")):
        import subprocess
        subprocess.run([sys.executable, "-m", "blipfeed.ui.firstrun_window"])
        cfg = config_mod.load()                   # pick up region/known/onboarded
    elif not marker.exists():                     # onboarded before the sentinel — heal
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError:
            log.exception("couldn't write the onboarded marker")

    # Default: the dashboard is home. AUTO capture runs headless behind it.
    from .dashboard import DashboardController
    from .ui.dashboard_window import run
    run(DashboardController(cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
