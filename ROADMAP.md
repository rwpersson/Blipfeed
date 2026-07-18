# Roadmap: Blipfeed (screen → mined words → Blip cards)

Status legend: ☐ todo · ◐ in progress · ☑ done

The goal: you immerse (hardsubbed video, Douyin, an article, a game), and the words
you actually saw become Blip flashcards — ranked by how often they appeared, with the
real sentence they came from attached — **without you pausing to snapshot anything.**

Ordered so each phase unblocks the next. The spine is **Phases 2–4**: store →
capture → the frequency/promotion policy. Everything before is scaffolding;
everything after is quality and polish. See CLAUDE.md for the locked decisions and the
data model this roadmap builds.

---

## Phase 0 — Foundations ☑ DONE

Nothing glamorous; everything after is harder without it.

- ☑ `git init` + `.gitignore` (`*.sqlite*`, `__pycache__/`, `dist/`, `build/`,
  model caches, captured images, and `spikes/frames/real/` — real captures contain the
  user's desktop)
- ☑ `requirements.txt` — `rapidocr-onnxruntime`, `jieba`, `pypinyin`, `mss`,
  `Pillow`, `numpy` (paddle dropped — see Phase 1)
- ☑ `LICENSE` (MIT, matching Blip) + `ATTRIBUTION.md` for **CC-CEDICT** (CC BY-SA)
  and the libraries
- ☑ `CLAUDE.md` context doc
- ☑ `ROADMAP.md` (this file)
- ☑ `paths.py` — Blipfeed's data (dev in-tree, frozen `%APPDATA%\Blipfeed\`), plus
  `blip_user_dir()` so the Blip coupling lives in one place
- ☑ `logging_setup.py` — rotating file + console, UTF-8 console fix (cp1252 crashes
  on hanzi), no bare `except: pass`

## Phase 1 — Spike the risky unknowns ☑ DONE

Prove the things that could sink the design before building around them. Throwaway
scripts (`spikes/`), durable conclusions (`spikes/FINDINGS.md`).

- ☑ **OCR on real hardsubs** — ran on synthetic frames *and* two real full-screen
  Douyin captures. RapidOCR read the stylized 万魂幡 hardsub correctly at conf
  0.78–0.82; ~1.8s cold init, ~1s/frame cropped. 30s ticks are comfortable.
- ☑ **Engine decision reversed: RapidOCR, not paddlepaddle** — paddlepaddle has no
  Python 3.14 wheel, and RapidOCR runs the same PP-OCR models on onnxruntime (same
  accuracy, far lighter to bundle). Folded into Decisions #3.
- ☑ **jieba on OCR output** — good on ordinary text; found that **OOV slang
  fragments** (万魂幡 → 万魂/幡), which is why manual chip-merge + a growable user
  dictionary are in Phases 4/6.
- ☑ **CC-CEDICT lookup** — 120,964 entries, 0.3s load, 83–90% hit rate. Crucially it
  showed `dict_ok` filters OCR *garbage* but is **powerless against real-word chrome**
  (游戏/音乐/影视 are all valid entries) — which is what made furniture detection
  non-negotiable rather than optional.
- ☑ Decision checkpoint resolved: Decisions #3 (engine) reversed, #2b (modes) and the
  furniture gotcha added, all *before* Phase 2 — as intended.

## Phase 2 — The store ☑ DONE ★

The data foundation the whole app queries. Get the schema right before anything writes
to it.

- ☑ `store.py` — SQLite schema: `sessions`, `snapshots`, `observations`, `terms` +
  `meta` (schema version, migrations). WAL + foreign_keys on (capture & review may be
  separate processes). **terms use a surrogate `id`** + UNIQUE(language, term), and an
  **`alt` column** carries the traditional form so a card shows both scripts.
- ☑ `observe(term, session, snapshot, context, confidence, …)` — upserts the term,
  appends the observation, bumps the **denormalized `count` / `ocr_conf_max` /
  `last_seen`**. Create-fields apply only on first sight (never clobber user edits).
- ☑ Term state: `new → queued → carded | known | ignored`. **Card-once AND
  cardable⇔has-meaning both enforced in `mark_carded`**, not the UI. `set_meaning`
  moves a term from *needs-def* to *ready*; `seed_term` imports known words.
- ☑ Buckets as views: `bucket('needs_def')` / `bucket('ready')`, frequency-ranked,
  plus a partial index for the loud needs-def query. `contexts_for` mines best
  distinct sentences. `recount` rebuilds the caches.
- ☑ Indices: `observations(term_id)`, `observations(session_id, ts)`,
  `terms(status, count DESC)`, partial `terms` index for needs-def.
- ☑ Tests: **15 passing** — counts, per-language uniqueness, both-forms, buckets,
  card-once, cardable-invariant, known-seeding, context mining, recount.

## Phase 3 — Capture ☑ DONE

Get pixels into text, cheaply and only when they change.

- ☑ `capture.py` — `Capturer` grabs a screen **region** (mss) as BGR; monitor helpers
  + a `bottom_third()` default (subtitles live low). **AUTO mode is headless** — no
  window at all, so nothing can steal focus and it can run while gaming (Decisions
  #6). `set_dpi_aware()` verified live: reports the true 1920×1200 where an
  unaware process would see 1536×960 on this 125%-scaled machine.
- ☑ `ui/region.py` — **visual drag-select region picker**, an ordinary focus-taking
  fullscreen window spanning the virtual desktop, with a live size readout and Esc to
  cancel. `python -m blipfeed.ui.region` runs it standalone.
- ☑ **Change-detection** — `frame_hash` (exact) + `ChangeDetector`: identical frame ⇒
  skip OCR. Kills paused-screen inflation. Note it only saves *OCR cost*; a subtitle
  held while video moves behind it needs the text-level dedup that `furniture.py`
  provides — the two compose.
- ☑ `ocr.py` — provider interface + `RapidOcrProvider` (lazy, load-once, ~1.8s init),
  returning `OcrLine(text, confidence, bbox)`. `NullProvider` lets the pipeline be
  exercised without the OCR stack.
- ☑ `furniture.py` — **static-text suppression** (the chrome fix, NOT a stoplist).
  Keys each line on `(text, rounded position)`; a repeat is furniture and is dropped
  *before counting*. Content in a fixed box with changing text is kept, and the same
  word appearing as content elsewhere is never lost. **Leak invariant**: chrome leaks
  at most `threshold-1` observations against the `N` promotion gate, so with
  `threshold ≤ N` chrome can never reach a card.
  *Verified on the real frames*: a static screen goes 73 lines → 0 kept on snapshot 2;
  across two real frames exactly 8 lines were suppressed, all genuine chrome (tab
  title, Incognito, taskbar), zero content false-positives (spikes/FINDINGS.md).
- ☑ Tests: **40 green** (+9 furniture, +9 capture). The picker's drag interaction is
  the one part not automatically tested — it needs a human to drag.
- ☑ DPI / multi-monitor correctness — `set_dpi_aware()` + capture in real pixels;
  monitors enumerated via mss, picker spans the virtual desktop. Verified live.
- ☐ Window-capture as an alternative source (capture a specific window handle).

## Phase 4 — Segmentation + frequency + promotion ★ ☐

The heart. Turns recognized text into ranked, gated cards.

- ☐ `segment.py` — jieba segmentation + token filter (drop punctuation, latin,
  digits, single-char particles via a stoplist). **Load-bearing and unit-tested**
  against known sentences — bad splits corrupt every count silently. Support a
  **growable jieba user dictionary** (`jieba.load_userdict`) so a slang term added
  once (万魂幡) stays merged thereafter — the real-frame test showed OOV slang is the
  thing jieba fragments (see spikes/FINDINGS.md).
- ☐ `dict.py` — CC-CEDICT load + lookup: `meaning`, `dict_ok`, optional `hsk_level`.
- ☐ `lang.py` — reading/tones via pypinyin (ported from Blip; the only Chinese-only
  logic besides segment/dict).
- ☐ `frequency.py` — **ingestion**: recognized line → tokens → observations (with
  context sentence). Pure over `store.py`, tested.
- ☐ `frequency.py` — **promotion policy** (the gate): eligible = `status ∈
  {new,queued}` ∧ `count ≥ N` ∧ `dict_ok` ∧ confidence-ok ∧ not-known; promote up to
  `K` per snapshot, highest count first; overflow stays queued. Defaults **N=2,
  K=2**. Pure and seedable — test it directly, it's the product's brain.
  - **Phase 1 spike lesson:** `dict_ok` is necessary but not sufficient. Add a
    **commonness/difficulty filter** — 很/好/去/太 pass CEDICT but are too basic to
    card. And treat a CEDICT *miss* as "low-priority, keep in queue" not "discard":
    compositional words like 很多 aren't headwords but are real (see
    spikes/FINDINGS.md).
- ☐ `blip_link.py` — locate Blip's user dir; write/merge the `immersion` deck in
  Blip's schema-1 format. Enrich at promotion (CC-CEDICT meaning + pypinyin +
  best context sentence). **Never write a card without a meaning** (Blip rejects it).

**End of Phase 4 = the app works end to end**: run it over a video, get an immersion
deck of the words you saw, review them in Blip. Everything after is quality.

## Phase 5 — The mining queue + enrichment (review UI) ★ ☐

Where the user tunes what the automation did — and where the high-value slang words
live. A set of *views* over `terms`, not a new store. This is arguably the primary
surface, not an afterthought: on real content the words worth learning are
disproportionately the CEDICT-misses (万魂幡, 婚姻危机, 凡人歌 — verified), and this is
where they surface.

- ☐ `ui/review.py` (`python -m blipfeed.review`) — two prominent buckets + a
  collapsed tail (see CLAUDE.md data model):
  - **Needs a definition** (loudest): `meaning IS NULL ∧ count≥N` — the slang/nuance
    list, ranked by frequency, each row already carrying reading (pypinyin) + mined
    context sentences. These are NEVER discarded and NEVER silently auto-carded.
  - **Ready to card**: `meaning IS NOT NULL ∧ count≥N` — CEDICT filled the meaning;
    one-click accept (or auto-carded if `auto_card` is on).
  - **Long tail**: `count<N`, hidden by default (one-offs + OCR noise).
- ☐ Actions: **accept → card**, **ignore**, **mark known**, **edit** meaning/note.
  Each just sets `status` / fields. A term becomes cardable the moment it has a
  `meaning`.
- ☐ **The manual-AI enrichment loop** (CLAUDE.md Enrichment; the only AI path, free):
  - **"Copy for AI"** — export the *Needs a definition* batch as text, each row
    `term · reading · freq · 1–3 context sentences` — everything a chat model needs
    to define slang *in context*.
  - **"Paste enriched"** — forgiving `term<TAB>meaning<TAB>note` re-import that fills
    `meaning`/`note` and moves the term to *Ready to card*.
- ☐ Knob controls: N (eligibility threshold), K (per-snapshot cap), and `auto_card`
  on/off (off = nothing cards without you; everything routes to review) — persisted.
- ☐ A **known-words import** so you don't get carded for words you already know:
  seed `status=known` from an HSK level, a word list, or Blip's graduated words
  (`state.json`).

## Phase 6 — Control surface + sessions ☐

Auto mode is headless, so its control surface is a **hotkey + tray**, not a window
(CLAUDE.md Decisions #6). Everything the user deliberately opens is an ordinary
focus-taking window.

- ☐ `hotkeys.py` — **global hotkey** to start/stop a session and to trigger a manual
  capture, so auto mode is controllable without alt-tabbing out of a game. Port
  Blip's `RegisterHotKey` approach (register + message-pump on the *same* thread) —
  the one Win32 piece worth borrowing.
- ☐ **Tray icon** — start/stop, mode toggle, open review, quit. In headless auto mode
  the tray effectively *is* the UI, so this matters more here than it did in Blip.
  Needs an .ico (shared with packaging, Phase 8).
- ☐ **Auto mode stays silent** — no toasts when a word is carded; that's the
  interruption we're avoiding. Words accumulate, the user reviews after.
- ☐ `ui/panel.py` — start/stop capture, pick region, name the session, **AUTO/MANUAL
  mode toggle**, live counters (snapshots taken, words seen, cards made). **Capture
  state always visible, one click to stop** (CLAUDE.md privacy decision #7).
- ☐ `ui/pick.py` — **MANUAL-mode chip picker**: freeze the snapshot, overlay every
  detected word as a clickable chip (furniture-filtered so it's not cluttered), user
  taps the ones to keep → they observe into the store. The precision path for dense
  frames (CLAUDE.md Decisions #2b). **Adjacent-chip merge** (万魂 + 幡 → 万魂幡) fixes
  the OOV-slang fragmentation the real-frame test found, and can feed the jieba user
  dictionary so the merge sticks next time.
- ☐ Session boundaries — a session is a sitting (a video, a scroll); review "words
  from *that* video."
- ☐ Pause/resume; auto-pause when the capture source window isn't foreground
  (optional).

## Phase 7 — Learning-quality touches ☐

- ☐ Better example-sentence selection (prefer short, complete, distinct sentences
  that actually contain the target).
- ☐ Word-difficulty / usefulness signal (HSK level, corpus rarity) to break
  frequency ties in promotion.
- ☐ Roll the immersion deck by date (`immersion-<yyyy-mm-dd>`) so no single Blip deck
  grows without bound.
- ☐ pkuseg as an alternative segmenter if jieba proves too coarse on real subs.
- ☐ Stats: words seen vs carded vs known over time; per-session, per-source.

## Phase 8 — Packaging & release ☐

- ☐ App icon (.ico) — PyInstaller + tray both need it.
- ☐ Tray icon — start/stop capture, open review, quit.
- ☐ PyInstaller build — **the hard one**: PaddleOCR + models are large and native;
  expect a big artifact and antivirus/SmartScreen friction (same unsigned-binary
  caveat Blip documents).
- ☐ First-run: explain the region pick, the privacy stance, and that cards appear in
  Blip on *its* next launch (no hot-reload).
- ☐ Ship CC-CEDICT + model files, or fetch-on-first-run with a progress UI if the
  artifact is too big to bundle.

---

## Language expansion (post-v1, the Chinese-first seams pay off)

Adding Japanese = a new segmenter (MeCab/SudachiPy) in `segment.py`, a new dictionary
(JMdict) in `dict.py`, and a reading provider in `lang.py`. The store, capture,
frequency/promotion policy, review UI, and Blip-deck seam are all language-neutral and
don't change. Prove this by keeping Chinese assumptions out of everything except those
three files (CLAUDE.md Decisions #4).

## Known risks

- **PaddleOCR accuracy on stylized hardsubs** — the thing that can actually make
  frequency data too noisy to trust. Phase 1 spike de-risks it; OCR confidence gating
  + the `count ≥ N` filter are the mitigations.
- **Segmentation quality** — silently corrupts frequency. Mitigated by direct tests
  and, if needed, a better segmenter (Phase 7).
- **PaddleOCR packaging size** — large, native, AV-flag-prone. May force
  fetch-on-first-run over bundling.
- **Blip user-dir coupling** — dev-vs-frozen path divergence; contained to
  `blip_link.py`, but verify against a real frozen Blip install.
- **Privacy perception** — an always-watching screen reader must be, and must look,
  entirely local. Any network egress of captured content would be a betrayal of the
  premise; there is none by design.
