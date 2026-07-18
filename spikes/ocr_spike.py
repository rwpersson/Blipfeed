"""Spike: prove OCR -> segment -> CC-CEDICT on real image frames (ROADMAP Phase 1).

Measures the three de-risking questions:
  1. RapidOCR accuracy + confidence + cold-init / per-frame latency
  2. jieba segmentation quality on OCR output
  3. CC-CEDICT hit rate (real words found, garbage rejected) -> the dict_ok gate

Usage:
    python spikes/ocr_spike.py spikes/frames/*.png
    python spikes/ocr_spike.py path/to/a/real/douyin_frame.png   # the real test
"""
import glob
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# --- CC-CEDICT: minimal loader (the real dict.py will formalize this) ------------
CEDICT = Path(__file__).resolve().parent.parent / "data" / "cedict" / "cedict_ts.u8"


def load_cedict():
    """simplified -> first english gloss. ~120k entries, loads in well under a sec."""
    d = {}
    with open(CEDICT, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            # trad simp [pinyin] /g1/g2/...
            try:
                rest = line.split(" ", 2)[2]
                simp = line.split(" ", 2)[1]
                gloss = rest.split("/")[1]
            except IndexError:
                continue
            d.setdefault(simp, gloss)
    return d


# --- particle / stopword filter (segment.py will own the real list) --------------
STOP = set("的了是我你他她它们这那有和在也不都就要会着过吧呢啊吗把被给让"
           "。，、！？；：""''（）《》…—·.,!?;:\"'()")


def keep(tok):
    if not tok.strip():
        return False
    if all(c in STOP for c in tok):
        return False
    if tok.isascii():                          # latin/digits handled elsewhere
        return False
    return True


def main(paths):
    import jieba
    from rapidocr_onnxruntime import RapidOCR

    print("loading CC-CEDICT...")
    t = time.perf_counter()
    cedict = load_cedict()
    print(f"  {len(cedict):,} entries in {time.perf_counter()-t:.2f}s\n")

    print("init RapidOCR (cold)...")
    t = time.perf_counter()
    ocr = RapidOCR()
    jieba.initialize()
    print(f"  ready in {time.perf_counter()-t:.2f}s\n")

    freq = {}
    for p in paths:
        print(f"=== {p} ===")
        t = time.perf_counter()
        result, _ = ocr(p)
        dt = time.perf_counter() - t
        if not result:
            print(f"  (no text)  [{dt*1000:.0f} ms]\n")
            continue
        lines = [(box_txt, float(conf)) for _, box_txt, conf in result]
        text = "".join(t for t, _ in lines)
        avg_conf = sum(c for _, c in lines) / len(lines)
        print(f"  OCR [{dt*1000:.0f} ms, conf {avg_conf:.2f}]: {text}")

        toks = [t for t in jieba.lcut(text) if keep(t)]
        print(f"  segments: {' / '.join(toks)}")

        hits, misses = [], []
        for tok in toks:
            (hits if tok in cedict else misses).append(tok)
            freq[tok] = freq.get(tok, 0) + 1
        for tok in hits:
            print(f"    ✓ {tok}  →  {cedict[tok]}")
        if misses:
            print(f"    ✗ not in CEDICT (garbage/name/oversplit): {' '.join(misses)}")
        rate = len(hits) / len(toks) if toks else 0
        print(f"  dict hit rate: {len(hits)}/{len(toks)} = {rate:.0%}\n")

    print("=== frequency across all frames (the promotion signal) ===")
    for tok, n in sorted(freq.items(), key=lambda kv: -kv[1]):
        mark = "  ← eligible (≥2)" if n >= 2 and tok in cedict else ""
        print(f"  {n:2d}  {tok}{mark}")


if __name__ == "__main__":
    args = sys.argv[1:] or ["spikes/frames/*.png"]
    paths = [p for a in args for p in glob.glob(a)]
    if not paths:
        print("no images. run: python spikes/make_test_frame.py  first")
        sys.exit(1)
    main(paths)
