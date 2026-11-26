# Design: chained conversions

Status: IMPLEMENTED (converters.py: `find_route`, `chained_pairs`, `run_chain`)

## Problem
Some conversions have no direct converter (Word to PNG, PDF to JPG). A second
engine in between makes them possible: Word to PDF, then PDF to PNG.

## Rules
- A direct converter always wins. A route is only looked up when `(src, dst)` is not in `REGISTRY`.
- Fewest steps first, at most 3 (`MAX_HOPS`). No format is visited twice.
- These formats are never a stop on the way, because they drop layout or frames:
  `txt, csv, html, rtf, zip` (`LOSSY_INTERMEDIATES`). They can still be the start or end.
- Ties between routes of the same length go to the better stops, in this order:
  `pdf, png, docx, jpg` (`PREFERRED_INTERMEDIATES`), then alphabetically, so results are stable.
- Each step runs on the previous step's output, in memory. A step's output over 50 MB
  (`MAX_BYTES`) stops the chain. A failure names the step: `png->jpg failed (step 2 of 2): ...`.

## Where it shows up
- `GET /api/formats`: direct pairs as before, chained pairs with `"via": ["pdf"]`.
- `POST /api/convert`: falls back to the route; unreachable pairs are still a 400.
- Watcher: a file with no direct converter in `to-<fmt>/` goes through the route; the log row has `route`
  (`["docx", "pdf", "png"]`) and `--history` shows `(via pdf)`.
- UI: chained targets are in a "Converted in steps" group in the To list, labelled `(via PDF)`.

## Also added
Image to PDF (png, jpg, jpeg, webp, gif, bmp, tiff, heic, heif): one page sized to the image
(longest side 842 pt), transparency flattened to white, first frame only for GIF and TIFF.

## Known limits
- Each LibreOffice step starts its own process, so a two-step Office chain takes about twice as long.
- Quality is whatever the steps give: PDF to DOCX is layout reconstruction, JPG in the middle is a
  re-encode. Chains exist to be possible, not to be the best path; a direct pair is always better.
- 192 chained pairs exist today. Some are unlikely choices (spreadsheet to Word); they are grouped
  under "Converted in steps" so the direct choices stay on top.
