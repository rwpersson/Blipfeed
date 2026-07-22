# Blipfeed

Blipfeed watches a region of your screen while you immerse in a language — a
hardsubbed video, Douyin, a Wikipedia article, a game — reads the words off it with
OCR, tracks how often each appears, and **auto-creates flashcards in
[Blip](../Chinese%20Pop%20Learning)**, the passive-overlay review app it feeds. You
just immerse; the words you actually saw show up to review, weighted by frequency,
with the real sentence they came from attached.

It's the **mining** half; Blip is the **review** half. Windows-first, Python. Chinese
is the flagship, with seams for other languages.

> New to the codebase? **CLAUDE.md** is the design doc (the locked decisions, the data
> model, the gotchas) and **ROADMAP.md** tracks what's built. Read those for the *why*.

## Requirements

- **Windows** + **Python 3.14** (what it's developed on)
- The Python packages in `requirements.txt` (RapidOCR, jieba, pypinyin, mss, Pillow,
  numpy, pystray)

## Install (fresh clone)

```bat
git clone https://github.com/rwpersson/Blipfeed
cd Blipfeed
pip install -r requirements.txt
```

Then fetch **CC-CEDICT** — the Chinese↔English dictionary. It's not in the repo (it's
CC BY-SA and ~10 MB), but the app needs it for meanings and its "real word" gate:

```bat
curl -L -o cedict.zip https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.zip
tar -xf cedict.zip -C data\cedict
```

That should leave `data/cedict/cedict_ts.u8`. (Without it, everything still runs — words
just all land in "Needs a definition" until you enrich them.)

## Run

```bat
python -m blipfeed
```

(or double-click **run.bat**). First launch shows a short welcome, then the
**dashboard** — the home for everything.

- **Start / Stop** capture (a region you drag once). Auto mode runs headless in the
  background, so you can immerse while it mines. Closing the window (**X**) hides it to
  the **tray**; **Quit** on the tray menu closes everything.
- **Review** — the mined words: *Needs a definition* (slang CC-CEDICT lacks — the AI
  copy/paste-back loop lives here), *Ready*, *Session*, *Manual*, *Ignored*, *Known*,
  *Carded*, *Search*, plus a date filter.
- **📊 Stats** — totals, a 30-day activity chart, top words, recent sessions.
- **⚙ Settings** — every knob (interval, the promotion gate, OCR sensitivity, hotkeys).

**Global hotkeys** (work while a game has focus): `Ctrl+Alt+B` start/stop ·
`Ctrl+Alt+N` capture-now (manual chip picker) · `Ctrl+Alt+R` redraw the region.

Terminal-only (no window): `--status [--all]`, `--clean [--yes]`,
`--known-sync | --known-import <file> | --known-forget <word>`.

Cards are written into Blip's user-deck dir; **Blip shows them on its next launch** (it
loads decks at startup, no hot-reload).

## Tests

```bat
python -m pytest tests -q
```

## What lives where vs. what doesn't

Committed: all the code, the app icon, CLAUDE.md / ROADMAP.md. **Not** committed (by
`.gitignore`, on purpose): CC-CEDICT (fetch above), your frequency store
(`blipfeed.sqlite`) and `config.json` (personal data), captured images, and logs. A
fresh clone rebuilds the app; your *mined words* live only in the SQLite file, so back
that up separately if you want to keep them.

## License

MIT (see `LICENSE`). Bundled data has its own terms — see `ATTRIBUTION.md`.
