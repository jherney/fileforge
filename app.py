"""FileForge — a local web app for converting documents.

Conversion registry: new format pairs are added by registering small
converter functions. Engines used:
  * pdf2docx  — PDF -> Word (layout preserving)
  * LibreOffice headless — document/spreadsheet/presentation matrix + PDF export
  * PyMuPDF   — PDF -> text / PNG
  * Pillow    — raster image conversions (HEIC/HEIF read via pillow-heif)
"""
from __future__ import annotations

import functools
import io
import mimetypes
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

MAX_BYTES = 50 * 1024 * 1024  # 50 MB

app = FastAPI(title="FileForge")


@dataclass
class Conversion:
    source: str          # source extension, lowercase, no dot
    target: str          # target extension, lowercase, no dot
    content_type: str    # MIME type of the result
    run: object          # fn(data: bytes) -> bytes


REGISTRY: dict[tuple[str, str], Conversion] = {}


def register(source: str, target: str, content_type: str):
    def deco(fn):
        REGISTRY[(source, target)] = Conversion(source, target, content_type, fn)
        return fn
    return deco


# --------------------------------------------------------------------------
# Converters
# --------------------------------------------------------------------------

@register("pdf", "docx",
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
def pdf_to_docx(data: bytes) -> bytes:
    from pdf2docx import Converter

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "input.pdf"
        dst = Path(td) / "output.docx"
        src.write_bytes(data)
        cv = Converter(str(src))
        try:
            cv.convert(str(dst))
        finally:
            cv.close()
        return dst.read_bytes()


@register("pdf", "txt", "text/plain")
def pdf_to_txt(data: bytes) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return "\n\n".join(page.get_text() for page in doc).encode("utf-8")
    finally:
        doc.close()


@register("pdf", "png", "image/png")
def pdf_to_png(data: bytes) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        pix = doc[0].get_pixmap(dpi=150)
        return pix.tobytes("png")
    finally:
        doc.close()


# --- LibreOffice headless engine -------------------------------------------

LO_TIMEOUT = 180


def soffice_convert(data: bytes, src_ext: str, dst_ext: str) -> bytes:
    """Run one headless LibreOffice conversion in an isolated temp profile."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"input.{src_ext}"
        src.write_bytes(data)
        profile = Path(td) / "lo_profile"
        cmd = [
            "soffice", "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation=file://{profile}",
            *(["--infilter=HTML (StarWriter)"] if src_ext == "html" else []),
            "--convert-to", dst_ext, "--outdir", td, str(src),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=LO_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError("LibreOffice conversion timed out")
        out = Path(td) / f"input.{dst_ext}"
        if not out.exists():
            err = (proc.stdout + proc.stderr).decode(errors="replace").strip()
            raise RuntimeError(err or "LibreOffice produced no output")
        return out.read_bytes()


# source extension -> sensible export targets
OFFICE_SOURCES = {
    "doc": ["pdf", "docx", "odt", "rtf", "txt", "html"],
    "docx": ["pdf", "odt", "rtf", "txt", "html"],
    "odt": ["pdf", "docx", "rtf", "txt", "html"],
    "rtf": ["pdf", "docx", "odt", "txt", "html"],
    "txt": ["pdf", "docx", "odt", "html"],
    "html": ["pdf", "docx", "odt", "txt"],
    "xls": ["pdf", "xlsx", "ods", "csv"],
    "xlsx": ["pdf", "ods", "csv"],
    "ods": ["pdf", "xlsx", "csv"],
    "csv": ["pdf", "xlsx", "ods"],
    "ppt": ["pdf", "pptx", "odp"],
    "pptx": ["pdf", "odp"],
    "odp": ["pdf", "pptx"],
}

for _src, _targets in OFFICE_SOURCES.items():
    for _dst in _targets:
        _ct = mimetypes.guess_type(f"x.{_dst}")[0] or "application/octet-stream"
        register(_src, _dst, _ct)(
            functools.partial(soffice_convert, src_ext=_src, dst_ext=_dst))


# --- Pillow image engine -----------------------------------------------------

IMAGE_SOURCES = {
    "png": ["jpg", "webp", "gif", "bmp", "tiff"],
    "jpg": ["png", "webp", "gif", "bmp", "tiff"],
    "jpeg": ["png", "webp", "gif", "bmp", "tiff"],
    "webp": ["png", "jpg", "gif", "bmp", "tiff"],
    "gif": ["png", "jpg", "webp", "bmp"],
    "bmp": ["png", "jpg", "webp", "gif", "tiff"],
    "tiff": ["png", "jpg", "webp", "gif", "bmp"],
    "heic": ["jpg", "png", "webp", "gif", "bmp", "tiff"],  # phone photos
    "heif": ["jpg", "png", "webp", "gif", "bmp", "tiff"],
}


def image_convert(data: bytes, src_ext: str, dst_ext: str) -> bytes:
    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()  # lets Image.open read .heic/.heif (idempotent)
    Image.MAX_IMAGE_PIXELS = 200_000_000  # decompression-bomb guard
    im = Image.open(io.BytesIO(data))
    if dst_ext == "jpg":
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")  # flatten alpha/palette for JPEG
    elif dst_ext == "webp" and im.mode == "P":
        im = im.convert("RGBA")
    buf = io.BytesIO()
    save_kw = {"jpg": {"quality": 92}}.get(dst_ext, {})
    im.save(buf, {"jpg": "JPEG", "tiff": "TIFF"}.get(dst_ext, dst_ext.upper()),
            **save_kw)
    return buf.getvalue()


for _src, _targets in IMAGE_SOURCES.items():
    for _dst in _targets:
        register(_src, _dst, f"image/{'jpeg' if _dst == 'jpg' else _dst}")(
            functools.partial(image_convert, src_ext=_src, dst_ext=_dst))


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _safe_stem(filename: str) -> str:
    stem = Path(filename or "document").stem
    stem = SAFE_NAME.sub("_", stem).strip(" .") or "document"
    return stem[:80]


@app.get("/api/formats")
def formats():
    pairs = [{"from": s, "to": t} for (s, t) in sorted(REGISTRY)]
    return JSONResponse({"pairs": pairs})


@app.post("/api/convert")
async def convert(file: UploadFile, fmt_from: str = "pdf", fmt_to: str = "docx"):
    key = (fmt_from.lower().strip("."), fmt_to.lower().strip("."))
    conv = REGISTRY.get(key)
    if conv is None:
        raise HTTPException(400, f"Unsupported conversion: {key[0]} -> {key[1]}")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (50 MB limit)")

    try:
        out = await run_in_threadpool(conv.run, data)
    except HTTPException:
        raise
    except Exception as exc:  # converter failure -> 422 with a readable message
        raise HTTPException(422, f"Conversion failed: {exc}") from exc

    name = f"{_safe_stem(file.filename)}.{key[1]}"
    return _download(out, name, conv.content_type)


def _download(data: bytes, name: str, content_type: str):
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "X-Download-Filename": name},
    )


# Static UI (mounted last so /api wins)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420)
