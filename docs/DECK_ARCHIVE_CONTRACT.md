# Deck archive contract — the Blipfeed ⇄ Blip seam

**Audience: whoever implements the Blip side (the Blip codebase / its own agent).**
This is the agreed interface for archiving decks. It is deliberately tiny: one boolean
in the deck JSON, which both apps read and write. There is no IPC, no shared database,
no new file — the deck file *is* the seam, so the two apps stay in sync by construction.

Blipfeed's side of this is already implemented (`blipfeed/blip_link.py`). This document
is the spec Blip implements against.

---

## The mechanism: one field, `archived`

A deck gains one optional top-level boolean, `archived`:

```json
{
  "schema": 1,
  "name": "Immersion · 2026-07-21",
  "language": "zh-CN",
  "voice": null,
  "archived": true,
  "words": [ { "id": "1", "term": "…", "reading": "…", "tones": "…",
              "cat": "common", "meaning": "…", "note": "…", "alt": "…" } ]
}
```

Semantics:

| State of the field | Meaning |
|---|---|
| `"archived": true` | **Archived** — retired from active review, but still a real, listed deck. |
| `"archived": false` **or absent** | **Active** — a normal deck, reviewed as today. |

- **Readers must treat absent as `false`.** Both `false` and a missing key mean active.
- **Writers should omit the key when not archived** (Blipfeed does this, to keep active
  decks clean) — but never rely on the other app doing so; always tolerate an explicit
  `false`.
- Archiving is **only the flag.** No words are added or removed; `words`, `voice`,
  `name`, `schema`, ids, and audio are all untouched. Un-archiving is the exact inverse.

---

## What each app must do

### Blipfeed (done — for reference)

- **Graduate** a deck → set `archived: true`. If it was the active deck, new cards roll
  to a fresh deck so nothing lands in the retired one.
- **Restore** → remove the flag (back to active).
- Reads the flag to list decks (active vs archived) in its deck manager; graduated decks
  stay in Blipfeed's own "Carded" list regardless.
- Blipfeed only ever archives decks in the `immersion*` family it authored. It never
  touches decks you made by hand in Blip.
- Auto-cap: Blipfeed splits the active deck into a fresh file at 150 cards, so expect
  **multiple `immersion-<date>.json` files** to appear over time (Blip already globs the
  deck dir, so this needs no change — just be aware more files will show up).

### Blip (to implement)

1. **Deck loader** — read `archived` (default `false`; tolerate `true`/`false`/absent).
   Keep loading archived decks so they remain listable; just carry the flag.
2. **Review / scheduler** — **exclude cards from archived decks** from the active review
   rotation. That is the whole point: a graduated deck is present but not drilled.
3. **Deck manager UI** — show every deck including archived ones, visibly marked, with
   an **archive / un-archive toggle**. Toggling writes the flag (see #4).
4. **Writing the flag** — set/remove `archived` and rewrite the deck file. **Preserve
   every other field**, including any keys this contract doesn't mention (forward-compat)
   and the full `words` array. Do not regenerate ids or audio; do not drop `note`/`alt`.
5. **state.json** — a deck's per-word progress in `state.json` may stay as-is when it's
   archived; excluding it from review (#2) is sufficient. Don't delete progress —
   restoring a deck should bring its history back intact.

---

## Coordination model (important, and simple)

- **The file is the single source of truth.** Each app reads it on its own schedule:
  Blip at startup (it has no hot-reload — consistent with how new decks already appear
  only on next launch), Blipfeed whenever it reads or writes decks.
- **No live sync, and none is needed.** If Blip un-archives a deck, the flag is now
  `false` in the file; Blipfeed sees that the next time it reads. If Blipfeed graduates a
  deck, Blip sees `true` on its next launch. They cannot disagree about a deck for longer
  than one read cycle because they read the same byte.
- **Conflicts are benign.** The only shared state is one boolean per deck; last write
  wins, and the loser is at worst a stale archived/active state corrected on the next
  read. Nothing destructive can happen — no words are involved.
- **Rewrite rule (both apps):** when you rewrite a deck for any reason, carry through
  fields you didn't intend to change — most importantly `archived`. Blipfeed already
  preserves it; Blip must too, so a routine deck rewrite on Blip's side never silently
  un-archives something.

---

## Acceptance checklist for the Blip side

- [ ] A deck with `"archived": true` loads without error and appears in the deck list.
- [ ] Cards from an archived deck **never** appear in a review session.
- [ ] Cards from a non-archived deck (flag absent or `false`) review exactly as before.
- [ ] The deck manager can toggle a deck archived → active → archived; each toggle
      persists to the JSON and survives a restart.
- [ ] Rewriting a deck (via the manager or any other path) preserves `archived`, `words`,
      `voice`, `name`, `schema`, and any unknown keys.
- [ ] Restoring an archived deck brings its `state.json` progress back (it was never
      deleted).

That's the whole contract. If anything here is ambiguous when you implement Blip's side,
the safe default is: **treat `archived` as a display/scheduling flag only, change nothing
else about the deck, and preserve every field you didn't explicitly edit.**
