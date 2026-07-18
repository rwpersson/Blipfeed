"""Spike: the WHOLE loop, end to end, with the production modules.

    capture'd frames -> OCR -> furniture -> segment -> CC-CEDICT + pypinyin
                     -> store (frequency) -> promotion gate -> Blip deck JSON

Writes to spikes/out/ — it must never touch the user's real Blip deck.

    python spikes/full_pipeline_spike.py                    # synthetic subtitle run
    python spikes/full_pipeline_spike.py <images...>        # your own frames
"""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blipfeed import blip_link, frequency, ocr as ocr_mod, store  # noqa: E402
from blipfeed.config import Config                                # noqa: E402
from blipfeed.furniture import FurnitureFilter                    # noqa: E402

OUT = Path(__file__).resolve().parent / "out"


def main(paths):
    OUT.mkdir(exist_ok=True)
    deck_path = OUT / "immersion.json"
    if deck_path.exists():
        deck_path.unlink()

    cfg = Config()                      # shipped defaults: N=2, K=2, auto_card on
    conn = store.connect(":memory:")
    session = store.start_session(conn, "spike session")
    provider = ocr_mod.get_provider()
    furn = FurnitureFilter(threshold=cfg.furniture_threshold)

    print(f"config: N={cfg.min_count} K={cfg.max_promotions_per_snapshot} "
          f"min_len={cfg.min_promote_length} min_conf={cfg.min_confidence}\n")

    promoted_all = []
    for i, p in enumerate(paths, 1):
        lines = provider.recognize(p)
        kept = furn.filter(lines)
        snap = store.add_snapshot(conn, session, source="spike",
                                  raw_text=ocr_mod.joined_text(lines),
                                  ocr_engine=provider.name)
        ids = frequency.ingest(conn, kept, session_id=session, snapshot_id=snap)
        conn.commit()
        promoted = frequency.promote(conn, cfg,
                                     deck_writer=blip_link.exporter(conn, cfg,
                                                                    deck_path))
        promoted_all += [r["term"] for r in promoted]
        print(f"snapshot {i}: {len(lines):>3} lines | {len(kept):>3} after furniture "
              f"| {len(ids):>3} observations | promoted: "
              f"{', '.join(r['term'] for r in promoted) or '—'}")

    print("\n--- frequency store (top 12) ---")
    for r in conn.execute("SELECT term, count, meaning, status FROM terms "
                          "ORDER BY count DESC, term LIMIT 12"):
        flag = {"carded": "★ carded", "new": "", "queued": ""}.get(r["status"], r["status"])
        meaning = (r["meaning"] or "— needs a definition")[:34]
        print(f"  {r['count']:>2}  {r['term']:<6} {meaning:<36} {flag}")

    print("\n--- review buckets ---")
    for which, label in (("ready", "Ready to card"),
                         ("needs_def", "Needs a definition (the slang bucket)")):
        rows = store.bucket(conn, which, min_count=1)
        print(f"  {label}: {', '.join(f'{r[
            'term']}({r['count']})' for r in rows) or '—'}")

    print(f"\n--- Blip deck written to {deck_path} ---")
    if deck_path.exists():
        deck = json.loads(deck_path.read_text(encoding="utf-8"))
        print(f"  schema={deck['schema']} language={deck['language']} "
              f"words={len(deck['words'])}")
        for w in deck["words"]:
            print(json.dumps(w, ensure_ascii=False, indent=4))
    else:
        print("  (nothing promoted — no deck)")
    conn.close()


if __name__ == "__main__":
    args = sys.argv[1:] or ["spikes/frames/frame_*.png"]
    files = sorted(p for a in args for p in glob.glob(a))
    if not files:
        print("no images; run: python spikes/make_test_frame.py")
        sys.exit(1)
    main(files)
