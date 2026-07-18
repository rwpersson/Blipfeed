# Attribution

Blipfeed itself is MIT (see LICENSE). It relies on bundled data and libraries with
their own terms — recorded here so a release honours them.

## Bundled data

- **CC-CEDICT** — the Chinese→English dictionary used for word meanings and the
  `dict_ok` validity gate. Licensed **CC BY-SA 4.0**
  (https://cc-cedict.org/). Redistributing the dictionary file requires attribution
  and share-alike; keep this notice with any build that ships it.
- **HSK word lists** (optional, for known-word seeding / difficulty) — attribute the
  specific list used when one is added.

## Key libraries

- **PaddleOCR** / **PaddlePaddle** — Apache-2.0.
- **jieba** — MIT.
- **pypinyin** — MIT.
- **mss** — MIT.

## Relationship to Blip

Blipfeed emits decks in [Blip](../Chinese%20Pop%20Learning)'s JSON format and writes
them into Blip's user-deck dir. It does not import or redistribute Blip's code; Blip
is a separate MIT project.
