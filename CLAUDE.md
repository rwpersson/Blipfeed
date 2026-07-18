# Blipfeed — project context

Blipfeed watches your screen while you immerse in a language (hardsubbed video,
Douyin, a Wikipedia article, a game), reads the words off it with OCR, tracks how
often each word appears, and **auto-creates flashcards in [Blip](../Chinese%20Pop%20Learning)**
— the passive-overlay flashcard app this feeds. You immerse; the words you actually
saw show up as cards to review, weighted by how often you saw them, with the real
sentence they came from attached. Windows-first, Python.

It is the *mining* half; **Blip is the review half**. Blipfeed emits Blip decks; it
does not do flashcard review itself.

**This is a separate app from Blip on purpose** (see Decisions #1). It writes deck
JSON into Blip's user-deck dir and lets Blip pick it up. It never imports Blip's
package or touches Blip's Win32 overlay code.

## The one idea that drives everything: ingest ≠ promote

Two different jobs. Conflating them is what makes the "Douyin problem" (dozens of
words on screen every frame) unmanageable. Keep them separate:

- **Ingestion — lossless, cheap, every snapshot.** Every OCR'd + segmented word is
  recorded: increment its count, save the sentence it appeared in, timestamp,
  source. We never discard signal. This is the frequency data; it scales to millions
  of rows.
- **Promotion — sparse, gated, the auto-carding.** *Separately*, at most a couple
  words per snapshot become an actual Blip card. This is where "don't card
  everything" and "don't card a term twice" live.

Ingestion answers *what did I see and how often*. Promotion answers *what deserves a
card right now*. **Never make the capture loop decide what to card** — it records;
promotion decides.

## The pipeline

```
every ~30s (configurable; NOT continuous — see Decisions #2):
  grab region  →  perceptual/exact hash
  →  unchanged since last snapshot? SKIP  (kills paused-screen frequency inflation)
  →  RapidOCR (PP-OCR/onnx)  (lines + per-line confidence + boxes)
  →  jieba segmentation       (Chinese has no spaces — this is where "words" happen)
  →  filter: drop punctuation / latin / digits / single-char particles (的了是…)
  →  for each kept token: write an OBSERVATION (term + its context sentence)

separately, promotion pass (after each snapshot):
  eligible = terms where status ∈ {new, queued}
             AND count ≥ N            (default 2 — seen >once ⇒ probably real)
             AND in CC-CEDICT         (a real word, not OCR garbage)
             AND ocr confidence ok
             AND not already known
  promote up to K (default 2), highest count first
  → enrich (CC-CEDICT meaning, pypinyin reading/tones, best context sentence)
  → status = carded; write into the Blip immersion deck
  overflow stays queued for a later pass or manual review
```

## Data model — SQLite, not JSON

Blip uses JSON because its decks are tiny and hand-authored. Blipfeed is the
opposite: unbounded, append-only, needs ranking/filter/dedup queries. **SQLite
(stdlib `sqlite3`, zero dependency)** is the store. One file under the user dir.

| Table | One row per | Key columns |
|---|---|---|
| `sessions` | immersion sitting (a video, a scroll session) | `id, started, ended, source_label, language` |
| `snapshots` | capture tick | `id, session_id, ts, source, region, image_hash, raw_text, ocr_engine` |
| `observations` | a single sighting of a term | `id, term, snapshot_id, session_id, context_sentence, confidence, bbox, ts` |
| `terms` | a distinct word (the queryable state) | `term (PK), language, reading, tones, meaning, note, cat, hsk_level, dict_ok, ocr_conf_max, count, status, first_seen, last_seen, card_deck, card_id, enriched_at` |

- `terms.count` is **denormalized** (bumped on every observation insert) so ranking
  by frequency is O(1), never `COUNT(*)` over `observations`.
- `observations` is the **source of truth** — append-only, indexed on `(term)` and
  `(session_id, ts)`. You can always recompute `terms` from it. Never delete
  observations to "clean up"; that's the frequency history.
- `context_sentence` on every observation is the free **example-sentence** mine —
  the best distinct one(s) get attached to the card at promotion. (Blip's own
  roadmap wanted example sentences and never built them; here they fall out for
  free.)
- **`terms.status` is the whole lifecycle**: `new → queued → carded`, or `known` /
  `ignored`. Card-once, frequency gating, and the mining queue are all just this
  column + `count`:
  - **card-once**: promotion only touches `new`/`queued`; once `carded`, count keeps
    climbing (you'll see "seen 47×") but it's never carded again.
  - **mining queue** = a *view*, not a separate structure: `SELECT … FROM terms
    WHERE status IN ('new','queued') ORDER BY count DESC`. Accept / ignore /
    mark-known / edit just set `status`.

## Frequency + dedup rules — do not regress

1. **Change-detection is the primary dedup.** A paused video would otherwise count
   the same subtitle every 30s forever. Hash the captured region; identical hash to
   the previous snapshot for that source ⇒ skip OCR entirely, record no
   observations. This is cheaper *and* correct.
2. A word genuinely **recurring across different sentences counts each time** — that
   is real exposure and the signal we want. Only *identical consecutive* frames are
   deduped (rule 1), not repeats across the session.
3. **Frequency does double duty**: eligibility gate (`count ≥ N`) *and* promotion
   priority (highest count first). It is the app's whole quality filter — a real
   word recurs, an OCR misread appears once. Lean on it; don't add a heavier
   classifier before this proves insufficient.
4. **Segmentation quality is upstream of everything.** For Chinese the OCR is the
   easy half; turning `我喜欢学中文` into 我/喜欢/学/中文 (jieba) is where the "words"
   come from. A bad segmentation silently corrupts every frequency count. Treat
   `segment.py` as load-bearing and test it directly.

## Enrichment — offline, no paid API (Decisions #4)

- **Meaning + word-validity: CC-CEDICT**, bundled, offline, free. Also the
  `dict_ok` gate — a token not in CEDICT is probably OCR garbage or a name, so it
  stays low-priority and is not auto-carded.
- **Reading + tones: pypinyin** — reuse Blip's approach (`lang.py`), the same
  polyphone-aware fill. Blipfeed carries its own small copy of that logic (it does
  not import Blip).
- **Context/note: the mined sentence.** The best distinct `context_sentence`
  becomes the card's example.
- **AI enrichment is optional and MANUAL.** No built-in API calls, no per-use cost
  to anyone. The review UI offers a "copy candidates" button so the user can paste a
  batch into any chat model themselves and paste enriched notes back. **Never add an
  automated LLM call to the pipeline** — that was an explicit product decision.

## Integration with Blip — the deck seam

- Blipfeed writes a deck (default id `immersion` or `immersion-<yyyy-mm-dd>`) into
  **Blip's user-deck dir**, in Blip's schema-1 format
  (`{schema, name, language, voice, words:[{id, term, reading, tones, cat, meaning,
  note}]}`). Blip's `decks.load_all()` picks it up.
- **Every Blip card needs a `meaning`** or Blip's validation rejects it — that is
  why CC-CEDICT is a hard requirement, not a nice-to-have. Do not write a card
  without a meaning; hold it in the queue until it has one.
- **Blip loads decks at startup and does not hot-reload.** New cards appear on Blip's
  next launch, not live. Say so in any "carded!" message; don't imply an instant
  switch.
- **Blip's user dir**: dev = the checkout, frozen = `%APPDATA%\Blip\`. Blipfeed must
  locate it the same way Blip's `paths.py` does (`%APPDATA%\Blip` if it exists, else
  ask / configure). Keep this in one module (`blipfeed/blip_link.py`) so the
  coupling is contained. Audio is Blip's job — Blipfeed writes JSON only; Blip
  synthesizes on next launch (content-addressed, so it just works).

## Layout (planned — keep this updated as it lands)

```
blipfeed/
  __main__.py       entry (python -m blipfeed)
  capture.py        screen/region/window grab (mss) + change-detection hash
  ocr.py            OCR provider interface; RapidOcrProvider (see Decisions #3)
  segment.py        jieba segmentation + token filtering (LOAD-BEARING, tested)
  dict.py           CC-CEDICT load + lookup (meaning, dict_ok, hsk optional)
  lang.py           reading/tones via pypinyin (ported from Blip, Chinese-only bit)
  store.py          SQLite: schema, upsert observation, term state (tested)
  frequency.py      ingestion + promotion policy (N, K, eligibility) — pure, tested
  blip_link.py      locate Blip's user dir; write/merge the immersion deck
  paths.py          where Blipfeed's own data lives
  logging_setup.py
  ui/               control panel, region picker, mining-queue review window
tests/              pytest; segment/store/frequency get real coverage
data/               bundled CC-CEDICT (+ optional HSK lists)
```

## Decisions (locked — don't re-litigate without a reason)

1. **Separate app, emits Blip decks.** Not a module inside Blip. Blip's whole
   architecture is isolated processes protecting focus/click-through invariants;
   the OCR stack (onnxruntime + opencv + models, ~100+ MB) must never be dragged into
   Blip's lean footprint. The boundary is the deck JSON. A future target (Anki) would
   slot in at the same seam.
2. **Snapshot-based (~30s default), NOT continuous.** Continuous full-frame OCR is
   wasteful and floods the store; the user explicitly does not want it. A timer tick
   + change-detection is cheaper and cleaner. Interval is configurable (5–120s).
3. **OCR = RapidOCR (ONNXRuntime), local.** Runs the *same* PP-OCR models as
   PaddleOCR but on onnxruntime instead of paddlepaddle. Chosen over paddlepaddle in
   the Phase 1 spike for two reasons: (a) paddlepaddle has **no wheel for Python
   3.14**, the interpreter here; (b) onnxruntime is far lighter than paddle and much
   friendlier to bundle (defuses a Phase 8 packaging risk), at equivalent accuracy.
   Offline, private, no per-use cost — correct for an always-watching screen reader.
   Behind an `ocr.py` provider interface so a cloud/Tesseract engine could slot in;
   RapidOCR is the default and the only one shipped. Spike numbers: cold init ~1.8s,
   ~1s/frame on CPU, conf 0.7–0.9 — comfortably inside a 30s tick. See
   spikes/FINDINGS.md.
4. **Chinese-first, seams for more.** jieba + CC-CEDICT + pypinyin nail Chinese, but
   segmentation and dictionary sit behind interfaces so Japanese/others slot in later
   — mirroring Blip's language-agnostic deck model. Don't hardcode Chinese outside
   `segment.py` / `dict.py` / `lang.py`.
5. **Enrichment is offline (CC-CEDICT + pypinyin); AI is manual and optional.** No
   paid API in the pipeline, ever (see Enrichment above).
6. **Privacy: local-only, explicit region, visibly toggled.** An app that reads "any
   screen" is reading everything — bank, email, DMs. Nothing leaves the machine;
   capture is an explicit region/window the user chooses; the capture state is always
   visible and one click to stop. This is both an ethics requirement and what makes
   the tool trustworthy. Do not add any network egress of captured content.

## Anticipated gotchas (fill in as they cost real debugging)

- **Segmentation is the silent failure mode.** Wrong splits corrupt frequency with
  no error. Unit-test `segment.py` against known sentences.
- **OCR confidence must reach promotion.** A low-confidence line that happens to
  segment into a real CEDICT word is a trap; carry `ocr_conf_max` on the term and
  gate on it, don't card on text alone.
- **OCR init is slow-ish and heavy** (~1.8s model load; first frame ~1.7s, then
  ~1s). Load the engine once, lazily, off the UI thread; never re-init per snapshot.
- **Region capture across DPI / multi-monitor** — same trap Blip documents. Capture
  in real pixels; a region picked at 125% scaling must map to real coordinates.
- **The immersion deck can grow without bound.** Cap it / roll by date
  (`immersion-<date>`) so Blip's deck list and audio-gen stay sane; Blip already
  scrolls its deck list but don't hand it a 5,000-card single deck.

## Run / test (fill in as commands land)

```
python -m blipfeed              # the capture app + control panel
python -m blipfeed.review       # mining-queue review window (standalone)
python -m pytest tests -q       # keep green; segment/store/frequency are the core
```