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
| PDF (dedicated engines) | pdf | docx, txt, png |
| Documents (LibreOffice) | doc, docx, odt, rtf, txt, html | pdf + each other |
| Spreadsheets (LibreOffice) | xls, xlsx, ods, csv | pdf + each other |
| Presentations (LibreOffice) | ppt, pptx, odp | pdf + each other |
| Images (Pillow) | png, jpg/jpeg, webp, gif, bmp, tiff | each other |

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

Register a function in `app.py`:

```python
@register("src", "dst", "mime/type")
def my_converter(data: bytes) -> bytes:
    ...
    return out_bytes
```

The UI picks it up automatically from `/api/formats` — no frontend changes.

## Limits

- 50 MB per file (`MAX_BYTES` in `app.py`)
- LibreOffice conversions time out after 180 s
- Listens on 127.0.0.1 only; it is a local tool, not a public service
