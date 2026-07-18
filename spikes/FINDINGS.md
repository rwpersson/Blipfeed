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

---

## Real-frame test — two full-screen Douyin captures (the decisive run)

Ran the spike on two *un-cropped* real screenshots (a Douyin homepage feed and a
playing video). This is the run that settled the architecture.

**OCR works on the wild.** RapidOCR read the stylized hardsub **万魂幡？** correctly
(all three chars), plus 婚姻危机 / 娶妻 / 凡人歌 from the caption, at conf 0.78–0.82.
Full-screen latency 4.6s (dense homepage) / 2.7s (video) — a cropped region will be a
fraction of that. Real stylized hardsubs are readable. ✅

**Furniture detection is proven essential — and dict_ok can't substitute for it.**
The homepage produced **114 tokens, almost all UI/other-video titles**, and the
frequency ranking was pure chrome:

```
7 抖音   5 精选   4 日   3 月   3 三角洲   3 行动   3 歌   ...   2 万魂   2 幡
```

The word actually worth learning (万魂幡) is buried at count 2 under chrome at
counts 3–7. Critically, the dict hit rate was **83–90%** — high and *useless as a
quality signal*, because **chrome is made of real dictionary words** (游戏/音乐/影视/
全部 all hit CEDICT). So:

- `dict_ok` filters OCR *garbage*, but is **powerless against real-word chrome**.
- Only **static-position furniture detection** removes chrome. 抖音×7 / 精选×5 —
  identical text in identical positions across both frames — is exactly its signal.
- Chrome must be stripped **before** counting, or the frequency premise inverts.

This empirically confirms CLAUDE.md Decisions #2b and the furniture gotcha.

**New finding — OOV slang fragments in segmentation.** jieba split **万魂幡 → 万魂 /
幡** (the slang term isn't in jieba's dict; 幡 alone *is* in CEDICT, 万魂 isn't). So
the exact word the user wants doesn't survive as one token. Fixes:

- **Manual-mode merge** — let the user fuse adjacent chips (万魂 + 幡 → 万魂幡). The
  reliable path, and a strong reason manual mode is the *slang-mining* mode.
- A jieba **user dictionary** the user can grow (add 万魂幡 once, it stays merged).
- Auto "merge adjacent OOV tokens" heuristics are unreliable (幡 is a valid word) —
  don't try to be clever here; let the user merge.

### Furniture detection verified on the real frames (Phase 3)

`spikes/pipeline_spike.py` runs the **production** modules (`ocr.py` + `furniture.py`)
over the real screenshots. Two scenarios:

**1. Same frame twice (a static screen):** 73 OCR lines → 73 kept on snapshot 1,
**0 kept / 73 suppressed** on snapshot 2. A motionless screen goes completely silent,
exactly as intended.

**2. Two different real frames:** snapshot 2 had 30 lines, **8 suppressed — and all 8
were genuine cross-page chrome**, with zero false positives on content:

```
✗ 抖音精选电脑版-抖音旗下优质 @ (89,17)   browser tab title
✗ Incognito (2)              @ (1755,70)  browser chrome
✗ 89°F · Partly sunny · 4:00 PM · 7/18/2026 · Search · ENG   the Windows taskbar
```

It correctly did **not** suppress the URL (different text — a modal_id was appended),
the search box content 凡人歌, or any video content. The Douyin nav bar wasn't
suppressed either, because the second frame is a video modal that doesn't show it —
it had only been seen once. All correct.

Combined with the leak invariant (`threshold - 1 = 1` observation vs the `N = 2`
promotion gate), chrome is structurally incapable of reaching a card.

**Homepage is the worst case, not the real case.** Frame 1 (browse feed, dozens of
thumbnails) is maximally noisy; frame 2 (a playing video: one caption + one hardsub)
was 90% content and clean. Capture sessions happen on a playing video, where region
+ furniture + the subtitle genuinely recurring over time all work in our favor.
Region targeting stays the cheap first cut; furniture handles the rest.
