# Phase 1 spike — findings

Throwaway spike (`spikes/`), but the conclusions shape Phases 2–4. Run:

```
python spikes/make_test_frame.py     # synth subtitle frames
python spikes/ocr_spike.py           # OCR -> segment -> CC-CEDICT, + frequency
```

Tested on Python 3.14.4, CPU-only, five synthetic white-outlined subtitle frames
over busy backgrounds (the common hardsub look). **Real Douyin/YouTube frames still
need testing** — synthetic text is cleaner than the wild. But the pipeline is proven
and the filtering thesis held up.

## Decisions made

- **OCR engine = RapidOCR (ONNXRuntime), not paddlepaddle.** paddlepaddle has *no
  wheel for Python 3.14*; RapidOCR runs the same PP-OCR models on onnxruntime, which
  installs clean on 3.14 and is much lighter to bundle. Folded into CLAUDE.md
  Decisions #3 + requirements.txt.
- **30s tick is comfortable.** Cold init ~1.8s; first frame ~1.7s; subsequent frames
  ~1.0s on CPU. Even continuous would be feasible, but 30s stands (product choice).

## Measurements

| Frame | OCR conf | OCR correct? | dict hit rate |
|---|---|---|---|
| 我喜欢学习中文 | 0.71 | ✅ | 3/3 |
| 今天天气很好我们去公园吧 | 0.87 | ✅ | 4/5 |
| 他正在看一部很有意思的电影 | 0.75 | ❌ dropped 一, added garbage 一音 | 4/6 |
| 学习中文需要很多的练习和耐心 | 0.86 | ✅ | 5/6 |
| 这个视频真的太好笑了哈哈哈 | 0.92 | ✅ | 5/6 |

## The core thesis held: dict_ok + frequency filters the noise

Every piece of OCR/segmentation garbage was **correctly rejected by the CC-CEDICT
gate**: `一音` (OCR hallucination), `看部` (mis-split after OCR dropped a char),
`哈哈哈` (onomatopoeia), `今天天气` (jieba over-merge). Real words all hit. This is
the whole design in miniature — *a real word is in the dictionary and recurs; noise
isn't*. Confidence 0.71–0.92, and the one genuine OCR error produced only count-1
tokens, which `count ≥ 2` would drop anyway. The two filters are complementary.

## Two refinements this surfaced (feed into Phase 4)

1. **`dict_ok` is necessary but NOT sufficient — also need a commonness/difficulty
   filter.** Single-char ultra-common words (很, 好, 去, 太) pass the CEDICT gate and
   got flagged "eligible", but they're exactly the too-basic words you don't want to
   auto-card. So promotion needs a **stoplist / HSK-basics / min-usefulness filter**
   on top of dict validity. (This is why ROADMAP Phase 5's known-words seeding and
   Phase 7's difficulty signal exist — the spike confirms they're not optional.)

2. **`dict_ok` has false-negatives on compositional words.** 很多 ("many") is real
   but not a CC-CEDICT headword (it's 很+多), so the gate rejected it. Mitigation:
   frequency still catches such words (seen often), so surface them in the review
   queue even when `dict_ok` is false — don't let a missing headword silently bury a
   word the user clearly keeps seeing. `dict.py` should treat CEDICT-miss as
   "low-priority, not auto-card" rather than "discard".

## jieba segmentation quality

Good overall (喜欢/学习/中文/有意思/电影/需要/练习/耐心 all correct). Two issues:
over-merge (今天天气 should be 今天/天气) and a split corrupted by an upstream OCR
error (看部). Both were caught by the dict gate, so they didn't reach a card. If real
frames show more of this, pkuseg is the Phase 7 fallback — but jieba looks good
enough to build on.
