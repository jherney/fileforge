# FileForge roadmap

Plan only. Nothing here is implemented. File and line references were read from the repo on 2026-11-26.
Effort = `human team / AI-assisted` (focused hours, including tests and review, not calendar time). These are estimates, not measurements.
Agent types are from the allowed list; `debugger` is unused (no open bug found).

## Findings that shape the plan

- `REGISTRY`, the converters and `MAX_BYTES` live in `app.py`, so `watcher.py` (line 23) imports FastAPI just to get converters. A CLI and a history page both need a FastAPI-free core (task C0).
- REGISTRY has no image-to-PDF pair: `IMAGE_SOURCES` (app.py:147) has no `pdf` target and `OFFICE_SOURCES` only starts from documents. "HEIC -> JPG -> PDF" cannot work until an image->pdf converter exists (task B2). Chaining alone is not enough.
- `tests/test_convert.py:168` builds `TestClient(A.app)`, whose Host header is `testserver`. A Host check (R3) will break every `Api` test unless the client uses `base_url="http://127.0.0.1:8420"`.
- `tests/test_convert.py:198` patches `A.MAX_BYTES`. `convert()` must keep reading `MAX_BYTES` from the `app` module namespace after any refactor.
- The From default is BMP because `fillFrom()` (static/app.js:38) sorts keys and BMP is alphabetically first.
- The watcher records failures in `done` (`_load_log`, watcher.py:57, and `_log`, line 68), so a failed file is never retried unless its size or mtime changes.
- There is no JavaScript test harness; UI checks are manual unless a task adds one.

## Section 1: remaining improvements

| ID | Task | Agent type | Depends on | Acceptance check | Effort |
|---|---|---|---|---|---|
| R4 | Pin `requirements.txt` (and `httpx` in `requirements-dev.txt`) to compatible-release (`~=`) versions taken from the working environment's `pip freeze` | python-pro | none | Fresh venv + `pip install -r requirements.txt -r requirements-dev.txt`, then both unittest files pass; README notes the LibreOffice version used | 0.5h / 0.25h |
| R3 | Add Origin/Host middleware in `app.py`: allow Host `127.0.0.1:8420` and `localhost:8420` (derive from the port in `__main__`), reject POSTs whose `Origin` is present and not allowed; no Origin (curl, CLI) is still accepted. Update `Api` tests to the 127.0.0.1 base_url | security-engineer | none | New tests: POST with `Origin: https://evil.example` returns 403; `Host: evil.example` returns 403 (DNS-rebinding case); no-Origin POST still works; all existing tests pass | 2h / 0.75h |
| R2 | Reject oversize uploads early in `convert()` (app.py:205): check `Content-Length` before reading, then read `file` in 1 MB chunks and stop at `MAX_BYTES + 1` | backend-developer | R3 (same file) | Test: `Content-Length` over limit returns 413 and the converter mock is never called; chunked body over limit returns 413; `mock.patch.object(A, "MAX_BYTES", 10)` test still passes | 1.5h / 0.5h |
| R1 | PDF to PNG all pages: keep `("pdf","png")` as page 1 (existing test at test_convert.py:132) and register `("pdf","zip")` returning `page-001.png...` at 150 dpi, with a page cap (suggest 200) and a clear error above it | python-pro | R2 (same file) | 3-page PDF gives a zip with 3 valid PNGs; `looks_valid` and the sample table in `tests/test_convert.py` cover `zip`; watcher `to-zip/` works because `TARGETS` derives from REGISTRY | 2h / 0.75h |
| R5 | Watcher retry and minimal history: track failed signatures separately from `done`; a failed file is retried when its `failed/<name>.err` is deleted, or with `--retry-failed`; add `--history [-n N]` printing the tail of `watcher.jsonl` | backend-developer | none | Test with a converter that fails once then succeeds: delete the `.err`, next `poll()` produces `out/`, log has `failed` then `ok`; restart still does not reconvert `ok` files; `--history` prints last N rows | 3h / 1h |
| R6 | From dropdown starts on a disabled "Choose a format..." placeholder in `fillFrom()`; `fillTo()` yields an empty To list until a file or format is chosen | frontend-developer | none | Page load: From shows the placeholder, Convert disabled; choosing a file sets From and enables Convert; picking a mismatched From still clears the file (app.js:191 handler) | 1h / 0.5h |
| QA1 | Regression pass for R1-R6: run `python3 -m unittest` for both test files, curl cross-origin POST, manual browser walkthrough of the UI | qa-expert | R1-R6 | Written pass/fail list; all unittest green; UI checklist done in a browser | 2h / 1h |

## Section 2: bigger ideas

### Shared groundwork

| ID | Task | Agent type | Depends on | Acceptance check | Effort |
|---|---|---|---|---|---|
| C0 | Extract `converters.py` (Conversion, `register`, REGISTRY, engines, `MAX_BYTES`, `free_name`); `app.py` and `watcher.py` import from it; `app.py` keeps the `MAX_BYTES` name in its own namespace | backend-developer | R1, R2, R5 | Existing tests pass with only import-path edits; `python3 -c "import watcher"` no longer imports FastAPI | 3h / 1h |
| T0 | Characterization tests for the extracted core before more features land | test-automator | C0 | Tests assert REGISTRY keys and every converter's output signature; run in CI-free local `unittest` | 2h / 0.75h |

### Idea A: chained conversions (HEIC -> JPG -> PDF)

| ID | Task | Agent type | Depends on | Acceptance check | Effort |
|---|---|---|---|---|---|
| B1 | Design routing rules: BFS over REGISTRY, fewest hops, direct edge always wins, ban lossy intermediates (`txt`, `csv`, `zip`), max 3 hops, response shape for `/api/formats` and `/api/route` | api-designer | C0 | One page in `docs/designs/` listing rules, edge cases and API shape | 2h / 0.5h |
| B2 | Add image->pdf converters (Pillow or PyMuPDF) for png, jpg, jpeg, webp, gif, bmp, tiff, heic, heif | python-pro | C0 | Each new pair passes `RegistryMatrix` (valid PDF output); multi-frame GIF/TIFF behavior documented | 3h / 1h |
| B3 | `find_route(src, dst)` and `run_chain(data, route)` in `converters.py`; per-hop error naming the failing hop | backend-developer | B1, B2 | `find_route("heic","pdf")` is `[heic->jpg, jpg->pdf]`; direct pairs return 1 hop; unreachable returns `None`; no cycles; every hop exists in REGISTRY | 4h / 1.5h |
| B3t | Route tests including an all-pairs property check | test-automator | B3 | Every reachable pair returns a valid route; end-to-end HEIC drop gives a valid PDF | 2h / 0.75h |
| B4 | Watcher uses routes when no direct pair exists (`Watcher.convert`, watcher.py:104); log record gains `route`; intermediates stay in memory and respect `MAX_BYTES` | backend-developer | B3 | Test: `.heic` in `to-pdf/` produces `out/*.pdf`; failure `.err` names the failing hop | 2h / 0.75h |
| B5 | `/api/formats` lists chained pairs with `via`; `/api/convert` falls back to the route | backend-developer | B3, R3 | `POST png->pdf`-style chained request returns the file with correct Content-Type; unreachable still 400 | 2h / 0.75h |
| B6 | UI shows chained targets, labelled "via JPG", in `loadFormats()` and `renderAvailable()` (app.js:113-111) | frontend-developer | B5, R6 | Selecting HEIC offers PDF with a "via" label and downloads a valid PDF | 2h / 1h |

Risks: conversion time doubles per hop (LibreOffice starts a fresh profile each call, app.py:103); larger From/To lists get noisy; hop quality loss (JPG re-encode) needs the label. Effort total: about 17h human / 6.25h AI-assisted.

### Idea B: KDE right-click "Convert to..."

| ID | Task | Agent type | Depends on | Acceptance check | Effort |
|---|---|---|---|---|---|
| K1 | `cli.py`: `convert FILE --to FMT [--out DIR]` and `targets FILE`; output beside the source via `free_name`; exit 0/1/2, error text on stderr; uses `find_route` when present | backend-developer | C0 (B3 optional) | `cli.py convert a.heic --to jpg` writes `a.jpg`, original untouched; bad file exits non-zero with a message; no server needed | 3h / 1h |
| K2 | `deploy/kde/` service menu `.desktop` (and a generator script) calling `cli.py`, with `notify-send` result | backend-developer (weak fit: no KDE specialist in the list) | K1 | Right-click a PNG in Dolphin shows "Convert to..." and the converted file appears; manual test on this machine's Plasma version | 3h / 1.5h |

Risks: Plasma 5 vs 6 servicemenu paths and the executable-bit/trust rules differ, so treat K2 as needing hands-on verification; CLI start-up cost from importing Pillow and PyMuPDF. Total: about 6h / 2.5h.

### Idea C: results/history page with re-run

| ID | Task | Agent type | Depends on | Acceptance check | Effort |
|---|---|---|---|---|---|
| H1 | Design the data model and API: watcher history comes from `watcher.jsonl`; web conversions need their own metadata log (no files stored by default). Decide re-run scope | api-designer | C0, R5 | Design page listing endpoints (`GET /api/history`, `POST /api/history/{id}/rerun`), record fields, retention, and the privacy decision | 2h / 0.5h |
| H2 | Implement history endpoints and a shared `convert_path()` extracted from `Watcher.convert`; re-run looks up the entry by id, never takes a path from the client | backend-developer | H1, R3 | Tests: list returns watcher and web rows; rerun of an existing source file produces a new `out/` file; a missing source returns 404 | 4h / 1.5h |
| H3 | Mock layout for a History view in the two-panel UI (`static/index.html`) | ui-designer | H1 | Annotated layout covering empty, failed, long-name, and many-rows states | 2h / 0.75h |
| H4 | Build the history view and re-run button in `static/` | frontend-developer | H2, H3 | Page lists rows, filters failed, re-run updates the row; web rows prefill From/To instead of re-uploading | 4h / 1.5h |
| H5 | Security review of re-run and history (path handling, log injection, CSRF via R3) | security-engineer | H2 | Written findings; no client-supplied path reaches the filesystem | 2h / 0.75h |

Risks: web uploads are not stored, so "re-run" for them is limited unless a keep-uploads option is added (privacy and disk cost, needs your decision); server and watcher both append to one log. Total: about 14h / 5h.

### Cross-cutting

| ID | Task | Agent type | Depends on | Acceptance check | Effort |
|---|---|---|---|---|---|
| P1 | Measure and cap memory/time for 200-page PDF to zip and for two-hop chains | performance-engineer | R1, B3 | Numbers recorded in a short doc; caps chosen from measurement | 3h / 1.5h |
| D1 | Update README, `docs/designs/magic-folder.md` (retry, chains), CLI and KDE install steps | documentation-engineer | B4, K2, H4 | Every new flag, folder (`to-zip/`) and install step appears in README | 2h / 0.75h |

## Recommended execution order

1. Parallel start (disjoint files): R4 (requirements), R3 (app.py), R6 (static/app.js), R5 (watcher.py).
2. R2, then R1 in `app.py` after R3 lands. Then QA1.
3. C0, then T0.
4. Parallel tracks after C0: chain track B1 -> B2 -> B3 -> B3t -> B4 and B5 -> B6; CLI track K1 -> K2; history track H1 -> H2 and H3 -> H4 -> H5.
5. P1 once R1 and B3 exist; D1 last.

Suggested order of ideas if time is short: C0, chains (the watcher gains the most), CLI + KDE (reuses C0 and B3), history last (biggest, needs a privacy decision).

## Open questions

- PDF to PNG: is a zip of all pages enough, or do you want a page picker (needs a new request parameter and UI field)?
- History: should web uploads ever be kept on disk so they can be re-run?
- Which Plasma version is installed? That fixes the K2 target path.
- `__pycache__` shows `cpython-315`; confirm pinned wheels (R4) exist for that Python before choosing versions.
