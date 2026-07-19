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
  →  furniture filter         (drop lines static across snapshots — UI chrome/nav/taskbar)
  →  jieba segmentation       (Chinese has no spaces — this is where "words" happen)
  →  filter: drop punctuation / latin / digits / single-char particles (的了是…)
  →  AUTO mode:   for each kept token: write an OBSERVATION (term + context sentence)
     MANUAL mode: freeze the frame, highlight tokens as chips, user picks → observe

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
| `terms` | a distinct word (the queryable state) | `id (PK) + UNIQUE(language, term), alt, reading, tones, meaning, note, cat, hsk_level, dict_ok, ocr_conf_max, count, status, first_seen, last_seen, enriched_at, card_deck, card_id` |

- `terms` has a **surrogate `id`** (not `term` as PK) + `UNIQUE(language, term)`:
  observations reference a compact int, and (language, term) uniqueness is the
  multi-language seam (Decisions #4) with no later migration.
- **Both script forms, and trad→simp dedup — DONE (`hanzi.py`).** `term` holds the
  canonical (simplified) form — the frequency dedup key — and `alt` holds the other
  (traditional), mapped straight onto Blip's `alt` field so a card shows both. The
  store canonicalizes on the way in (`upsert_term` → `hanzi.canonicalize`), so 學習
  and 学习 share one row and their counts combine; whichever script is seen first is
  backfilled as `alt`. The trad→simp map is derived from **CC-CEDICT itself** (zip
  each entry's two forms) — no OpenCC dependency — and degrades to identity if the
  dictionary is absent. CEDICT lookup at enrichment (Phase 4) fills the authoritative
  traditional form for a word first seen simplified.
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
  column + `count` + whether the term has a `meaning` yet:
  - **card-once**: promotion only touches `new`/`queued`; once `carded`, count keeps
    climbing (you'll see "seen 47×") but it's never carded again.
  - **cardable ⇔ has a `meaning`.** A Blip card must have a meaning (Blip rejects it
    otherwise). CC-CEDICT fills it for known words; for a dictionary-miss the meaning
    is NULL until enrichment supplies one. So "needs a definition" is not a separate
    status — it's `meaning IS NULL`, and it's what the review UI surfaces loudest.
  - **mining queue** = *views*, not a separate structure. Two buckets the UI derives
    (see Enrichment), plus a collapsed long tail:
    - *Ready to card*: `status∈('new','queued') ∧ meaning IS NOT NULL ∧ count≥N`
    - *Needs a definition*: `status∈('new','queued') ∧ meaning IS NULL ∧ count≥N`
    - *Long tail*: `count<N` (one-offs + OCR noise), hidden by default
  - Accept / ignore / mark-known / edit-meaning just set `status` / `meaning`.

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

## Enrichment — offline baseline + a first-class slang path (Decisions #4/#5)

**The CEDICT-miss words are the point, not the leftovers.** On real Douyin/YouTube
the words worth learning are disproportionately the ones CC-CEDICT does *not* have —
slang, xianxia/fantasy terms, show titles, fresh compounds. Verified on the sample
frames: 万魂幡 (the on-screen hardsub), 婚姻危机, 凡人歌 all miss CEDICT, while the
ordinary 娶妻/婚姻/二次元 hit. So a dictionary-miss must be **surfaced prominently and
never discarded** — it is a candidate for AI enrichment, not garbage.

What we can supply for *every* term, dictionary or not:

- **Reading + tones: pypinyin** (`lang.py`, ported from Blip, polyphone-aware).
  Works on any hanzi — 万魂幡 → wàn hún fān — so even a slang word arrives in Blip
  with a tone-colored reading. A miss is only ever missing its *meaning*.
- **Context sentences: the mined lines.** Every observation stores the sentence the
  word appeared in. These are what make the miss enrichable — 万魂幡 alone is opaque;
  "师父该入万魂幡了" lets an AI (or the user) define it. Keep the best few per term.

What differs by dictionary hit:

- **Hit → `meaning` auto-filled from CC-CEDICT.** Eligible for the *Ready to card*
  bucket and (if `auto_card` is on) auto-promotion.
- **Miss → `meaning` stays NULL → the *Needs a definition* bucket.** The high-value
  slang list, ranked by frequency, with reading + context already attached. It is
  NOT auto-carded (no meaning yet, and Blip would reject it) and NOT discarded — it
  sits in the loudest part of the review UI waiting for a definition.

**Frequency is what separates real slang from OCR noise here** (there is no
dictionary to lean on): a miss seen once is probably an OCR hallucination (`一音`); a
miss seen ≥N times across a session is a real word the content keeps using (万魂幡).
So the *Needs a definition* bucket is gated on `count ≥ N`, same as everything.

**AI enrichment is optional and MANUAL** — no built-in API calls, no per-use cost to
anyone (Decisions #5). The loop:

1. Review UI **"Copy for AI"** exports a batch (default: the *Needs a definition*
   bucket, highest frequency first) as text — each row `term · reading · freq ·
   1–3 context sentences`. That is everything a chat model needs to define slang in
   context.
2. The user pastes it into any AI themselves, gets back `meaning` + nuance `note`.
3. **"Paste enriched"** re-imports it (a simple, forgiving `term<TAB>meaning<TAB>note`
   parse), filling `meaning`/`note`. The term becomes cardable and moves to *Ready*.

**Never add an automated LLM call to the pipeline.** The export/paste-back seam is
the only AI path, by explicit product decision.

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
  furniture.py      static-text suppression: drop lines stable across snapshots (tested)
  segment.py        jieba segmentation + token filtering + growable user dict
                    (LOAD-BEARING, tested)
  dictionary.py     CC-CEDICT load + lookup (meaning, dict_ok, both scripts).
                    Named `dictionary`, not `dict`, to never shadow the builtin.
  lang.py           reading/tones via pypinyin (ported from Blip, Chinese-only bit)
  hanzi.py          trad↔simp: canonicalize for dedup, CEDICT-derived map (tested)
  store.py          SQLite: schema, upsert observation, term state (tested)
  frequency.py      ingestion + promotion policy (N, K, eligibility) — pure, tested
  blip_link.py      locate Blip's user dir; write/merge the immersion deck
  paths.py          where Blipfeed's own data lives
  logging_setup.py
  ui/               control panel, region picker, manual-mode chip picker,
                    mining-queue review window (Ready / Needs-a-definition buckets)
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
2b. **Two capture modes: AUTO and MANUAL.**
   - **AUTO** — no interaction while immersing. Furniture detection strips static UI,
     frequency ranks the rest, slang collects in *Needs a definition*. The "just
     immerse" promise.
   - **MANUAL / assist** — the app freezes the snapshot and highlights every detected
     word as a clickable chip; the user taps the ones to keep. Total precision for
     dense frames (a Douyin homepage) or grabbing one specific slang term. YomiNinja-
     like, but feeding the same frequency store + queue.

   Both share the pipeline; they differ only at the last step (observe-all vs
   user-picks). **UI-chrome is handled by furniture detection, never a stoplist** — a
   hardcoded nav-word list would filter real words a user wants and can't know every
   app (see the furniture gotcha).
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
6. **Focus model: Blipfeed is NOT Blip — most windows may take focus.** Blip's
   never-steal-focus invariant exists because it draws a card over your live game.
   Blipfeed is a mining tool that inherently asks for a little more work, so
   focus-taking is fine for anything the user *deliberately opens*: the manual-mode
   chip picker, the region picker, the control panel, the review queue — all ordinary
   Tk windows, no Win32 gymnastics. **Do not port Blip's `prevent_focus_steal`
   machinery wholesale.** The split:
   - **AUTO mode is headless** — `timer → grab → OCR → furniture → segment → store`
     draws *nothing*. `mss` is a passive pixel read, so it takes no focus by
     construction (stronger than NOACTIVATE, and far less code). It can run while the
     user games. Its control surface is a **global hotkey** (`RegisterHotKey` —
     the one piece worth borrowing from Blip's `hotkeys.py`) plus a **tray icon**;
     in a headless mode the tray effectively *is* the UI.
   - **AUTO mode stays silent** — no toasts/popups when a word is carded. That would
     be exactly the interruption we're avoiding. Words accumulate; the user reviews
     after.
   - **MANUAL mode and every deliberate window may take focus**, normally.

   Caveat (not focus-related): `mss` captures the desktop, so a genuinely *exclusive*
   fullscreen game may not be capturable. Borderless/windowed is fine; the primary
   browser use case is unaffected.
7. **Privacy: local-only, explicit region, visibly toggled.** An app that reads "any
   screen" is reading everything — bank, email, DMs. Nothing leaves the machine;
   capture is an explicit region/window the user chooses; the capture state is always
   visible and one click to stop. This is both an ethics requirement and what makes
   the tool trustworthy. Do not add any network egress of captured content.

## Conventions

- **Every module with a `__main__` needs the run-as-a-script guard.** Relative
  imports (`from .. import capture`) fail with *"attempted relative import with no
  known parent package"* when the file is executed directly — which is exactly what an
  IDE's Run button does. Requiring `python -m blipfeed.ui.region` is a footgun, so
  establish a package context *before* the relative imports (see `ui/region.py`):

  ```python
  if __package__ in (None, ""):
      from pathlib import Path
      sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
      import blipfeed.ui  # noqa: F401
      __package__ = "blipfeed.ui"
  ```

  (`parents[2]` from `blipfeed/ui/x.py`; use `parents[1]` from `blipfeed/x.py`.)
  Verify without opening a window: `runpy.run_path(file, run_name="smoketest")`
  executes the body — and the guard — while skipping `main()`.
- Log, don't swallow: no bare `except: pass` — use `log.exception(...)` (Blip's rule).

## Anticipated gotchas (fill in as they cost real debugging)

- **Segmentation is the silent failure mode.** Wrong splits corrupt frequency with
  no error. Unit-test `segment.py` against known sentences.
- **OOV slang fragments — the words you most want are the ones jieba splits.**
  Proven on the real frames: 万魂幡 → 万魂 / 幡 (the slang term isn't in jieba's dict).
  So the exact target doesn't survive as one token. Don't try to auto-merge
  cleverly (幡 alone is a valid word — heuristics will overreach). The fixes are
  **manual-mode chip merge** (user fuses 万魂 + 幡 → 万魂幡; this is why manual mode is
  the real *slang-mining* path) and a **growable jieba user dictionary** (add it once,
  it stays merged). This is the same class of word as the CEDICT-misses — high value,
  poorly served by off-the-shelf tools, so keep the human in the loop for them.
- **OCR confidence must reach promotion.** A low-confidence line that happens to
  segment into a real CEDICT word is a trap; carry `ocr_conf_max` on the term and
  gate on it, don't card on text alone.
- **OCR init is slow-ish and heavy** (~1.8s model load; first frame ~1.7s, then
  ~1s). Load the engine once, lazily, off the UI thread; never re-init per snapshot.
- **UI chrome would poison the frequency signal — strip it with furniture
  detection, NOT a stoplist.** The sample Douyin frames are full of text that is not
  the content: browser chrome/URL, Douyin's nav (全部/游戏/音乐/影视…), thumbnails,
  toasts, the taskbar. It is *stable across every snapshot*, so it would rack up the
  highest frequency of anything and dominate the queue — which breaks the whole
  "frequency = worth learning" premise. A hardcoded chrome/nav stoplist is the wrong
  fix: it filters real words a user might want and can't know every app's UI. The
  right fix is **static-text suppression** — RapidOCR gives a box per line, so key
  each line on `(text + rounded position)` and track persistence across snapshots:
  - same text + same box across many snapshots → **furniture**, suppress
  - same box + changing text → **content** (the subtitle band cycling) → keep
  - new/transient text → content → keep

  This auto-calibrates to any app with no stoplist and no config, and never drops a
  *content* word (if 游戏 appears as dialogue it's transient/in the subtitle box, so
  it's kept). Strip furniture **before counting**, so frequency only ever ranks
  content. (The linguistic particle filter for 的/了/是 is separate — that's grammar,
  not chrome.) A "static for ≥ M snapshots" threshold keeps a briefly-held title from
  being mistaken for furniture.

  **CORRECTION (from a real session): the per-sighting filter above is NOT enough for
  AUTO ingestion.** The claimed invariant — "chrome leaks at most `threshold-1`
  observations, safely under the `N` gate" — was wrong. The leak is per
  `(text, position)` **key, not per word**. A word appearing in two different chrome
  lines (Douyin's `是否要打开声音？` prompt *and* its `打开声音` button) leaked one
  observation each, hit `count=2`, and got carded. Half a real session's cards were UI.
  The fix is `furniture.EpisodeTracker`, used for AUTO ingestion: **record a line when
  its appearance ENDS, not when it starts.** Chrome persists, so it never ends and is
  never recorded — a true zero leak. A held subtitle is recorded exactly once when it
  goes. Cost: a word lands in the store one tick after you saw it. `FurnitureFilter`
  stays for MANUAL mode, which must de-clutter the frame immediately and can't wait
  for an episode to finish. Remaining leak: a *transient* toast is indistinguishable
  from content by persistence, so keep the region on the subtitle band.

  **`dict_ok` is NOT a chrome filter — furniture detection is the only one.** Proven
  on the real frames: a Douyin homepage OCR'd to 114 tokens with an 83–90% CEDICT hit
  rate, yet the top of the ranking was pure chrome (抖音×7, 精选×5, 三角洲×3) — because
  *chrome is made of real dictionary words* (游戏/音乐/影视/全部 all hit CEDICT). The
  dictionary gate filters OCR *garbage*; it does nothing against real-word chrome.
  Only static-position detection removes it.
- **The text you want isn't always in the same place.** On these frames it's the
  burned-in hardsub (bottom-center: 万魂幡？) on one and the caption (bottom-left:
  第4集：当中年人遇到…) on the other. Region is drawn per source; a generous
  bottom-third default + optional multiple regions beats assuming one fixed band.
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