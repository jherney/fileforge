# FileForge

A local web app for converting files between formats. Everything runs on your
machine — files are never uploaded anywhere.

## Run it

```bash
pip install -r requirements.txt
python3 app.py            # serves http://127.0.0.1:8420
```

Open http://127.0.0.1:8420, drop a file in, pick a target format, convert.

## Supported conversions

| Group | Sources | Targets |
|---|---|---|
| PDF (dedicated engines) | pdf | docx, txt, png (first page), zip (every page as a PNG) |
| Documents (LibreOffice) | doc, docx, odt, rtf, txt, html | pdf + each other |
| Spreadsheets (LibreOffice) | xls, xlsx, ods, csv | pdf + each other |
| Presentations (LibreOffice) | ppt, pptx, odp | pdf + each other |
| Images (Pillow) | png, jpg/jpeg, webp, gif, bmp, tiff | each other |
| Phone photos (pillow-heif) | heic, heif | jpg, png, webp, gif, bmp, tiff |

PDF → Word uses [pdf2docx](https://github.com/dothinking/pdf2docx), which
preserves layout, tables, and fonts far better than a generic converter.
The live, authoritative list is always `GET /api/formats`.

## Engines

- **pdf2docx** — PDF → DOCX (layout-preserving)
- **PyMuPDF** — PDF → text, PDF → PNG (150 dpi first page)
- **LibreOffice headless** — the office-document matrix and PDF export. Each
  request runs in its own temp profile so conversions are isolated and safe to
  run concurrently. Requires `soffice` on PATH.
- **Pillow** — raster image formats

## Adding a format pair

Register a function in `converters.py`:

```python
@register("src", "dst", "mime/type")
def my_converter(data: bytes) -> bytes:
    ...
    return out_bytes
```

The UI picks it up automatically from `/api/formats` — no frontend changes.

## Limits

- 50 MB per file (`MAX_BYTES` in `converters.py`)
- LibreOffice conversions time out after 180 s
- Listens on 127.0.0.1 only; it is a local tool, not a public service

## Magic folder

`watcher.py` converts files without the browser. Drop a file into
`~/Convert/to-<format>/` (for example `to-pdf/`) and the result appears in that
folder's `out/` subfolder. Originals are never touched; failures write
`failed/<name>.err`. Any `to-<format>` folder you create is picked up.

```bash
python3 watcher.py                 # run it by hand
python3 watcher.py --history -n 20  # recent conversions and failures
python3 watcher.py --retry-failed   # try files that failed before again
```

To retry one failed file, delete its `failed/<name>.err`; the watcher picks it up
again on the next scan.

To start it on login (assumes the repo is at `~/fileforge`):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/fileforge-watcher.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fileforge-watcher.service
```

## Tests

```bash
pip install -r requirements-dev.txt        # httpx, for the API tests
python3 -m unittest tests.test_convert     # every registered pair, content checks, HTTP API (~1 min, needs soffice)
python3 -m unittest tests.test_watcher     # magic folder (~1 s)
```

A new `register(...)` pair is covered automatically by the matrix test once its
source format has a sample in `tests/test_convert.py`.
