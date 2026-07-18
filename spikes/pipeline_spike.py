"""Spike: run the REAL modules (ocr.py + furniture.py) over real frames.

Unlike ocr_spike.py (throwaway code), this exercises the production pipeline to show
what furniture detection actually suppresses on real screens.

    python spikes/pipeline_spike.py spikes/frames/real/*.png
"""
import glob
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root

from blipfeed import ocr as ocr_mod                              # noqa: E402
from blipfeed.furniture import FurnitureFilter                   # noqa: E402


def run(paths, label):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    provider = ocr_mod.get_provider()
    furn = FurnitureFilter()
    for i, p in enumerate(paths, 1):
        lines = provider.recognize(p)
        kept = furn.filter(lines)
        kept_set = {id(ln) for ln in kept}
        dropped = [ln for ln in lines if id(ln) not in kept_set]
        print(f"\n--- snapshot {i}: {p.split(chr(92))[-1]} ---")
        print(f"  OCR lines: {len(lines)}   kept: {len(kept)}   "
              f"suppressed as furniture: {len(dropped)}")
        if dropped:
            print("  SUPPRESSED (static chrome):")
            for ln in dropped[:14]:
                print(f"    ✗ {ln.text[:44]!r} @ {ln.position}")
            if len(dropped) > 14:
                print(f"    … and {len(dropped) - 14} more")
        print("  KEPT (content):")
        for ln in kept[:10]:
            print(f"    ✓ {ln.text[:44]!r} @ {ln.position}")
        if len(kept) > 10:
            print(f"    … and {len(kept) - 10} more")


if __name__ == "__main__":
    args = sys.argv[1:] or ["spikes/frames/real/*.png"]
    paths = sorted(p for a in args for p in glob.glob(a))
    if not paths:
        print("no images found")
        sys.exit(1)
    # 1) the same frame twice — a static screen must go fully silent
    run([paths[0], paths[0]], "SAME FRAME TWICE (static screen -> total suppression)")
    # 2) two different real frames — shared chrome must be stripped from the 2nd
    if len(paths) > 1:
        run(paths[:2], "TWO REAL FRAMES (shared chrome stripped from snapshot 2)")
