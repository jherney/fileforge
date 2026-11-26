# FileForge

A local web app for converting files between formats. Everything runs on your
machine — files are never uploaded anywhere.

## Run it

```bash
pip install -r requirements.txt     # or: pip install --require-hashes -r requirements.lock
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
Where there is no direct converter, FileForge converts in steps, for example Word
to PNG by way of PDF, or PDF to JPG by way of PNG. See `docs/designs/chains.md`.
Images also convert to PDF (one page per file, transparency flattened to white).
The live, authoritative list, including `via` for chained pairs, is always `GET /api/formats`.

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
- Images: at most 100 megapixels. PDF pages are rasterised at 150 dpi, lower for huge pages (50 megapixels at most); PDF to zip stops at 200 pages or 50 MB
- Every result must be under 50 MB and not empty
- LibreOffice conversions time out after 180 s and run with no network access (`unshare`), so a document cannot make it fetch linked content. PDF conversions run in a worker process that is stopped after 120 s
- At most 2 conversions run at once in the web app
- Symlinks in watched folders and in folders given to the CLI are skipped
- Uploads must send `Content-Length`; the page is served with a strict Content-Security-Policy
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

## Command line

Convert without the server or a browser:

```bash
python3 cli.py convert photo.heic --to jpg          # writes photo.jpg beside the original
python3 cli.py convert a.docx b.docx --to pdf --out ~/pdfs --notify
python3 cli.py convert ~/Scans --to pdf -r            # every convertible file in a folder (-r: subfolders too)
python3 cli.py targets report.docx                   # what can this file become?
```

### Dolphin right-click menu (KDE Plasma 6)

```bash
python3 deploy/kde/install_servicemenu.py            # adds "Convert to..." to Dolphin's context menu
python3 deploy/kde/install_servicemenu.py --uninstall
```

It calls `cli.py convert ... --notify`, so results appear beside the originals with a desktop
notification. The menu lists every format for every readable file type; a pair with no route
ends in a "failed" notification. Re-run the installer if you move the repo.

Nothing is overwritten (a taken name becomes `photo-1.jpg`). Exit status: 0 all
converted, 1 a conversion failed, 2 bad request (missing file, no way to reach the format).

## Tests

```bash
pip install -r requirements-dev.txt        # httpx, for the API tests
python3 -m unittest tests.test_convert     # every registered pair, content checks, HTTP API (~1 min, needs soffice)
python3 -m unittest tests.test_watcher     # magic folder (~1 s)
python3 -m unittest tests.test_cli         # command line (~1 s)
python3 -m pytest tests                    # everything, including tests/test_hardening.py (~1 min)
```

`requirements.lock` pins every dependency with hashes (`uv pip compile requirements.txt --generate-hashes -o requirements.lock`); `uvx pip-audit -r requirements.lock --no-deps --disable-pip` checks it for known vulnerabilities.

A new `register(...)` pair is covered automatically by the matrix test once its
source format has a sample in `tests/test_convert.py`.
