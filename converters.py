"""FileForge converters: the registry of (source, target) -> converter functions.

No web code here, so the web app (app.py), the magic-folder watcher (watcher.py)
and the tests can all use it. Engines:
  * pdf2docx  — PDF -> Word (layout preserving)
  * LibreOffice headless — document/spreadsheet/presentation matrix + PDF export
  * PyMuPDF   — PDF -> text / PNG / zip of PNGs
  * Pillow    — raster image conversions (HEIC/HEIF read via pillow-heif)

Add a format pair by registering a function:

    @register("src", "dst", "mime/type")
    def my_converter(data: bytes) -> bytes: ...
"""
from __future__ import annotations

import functools
import io
import mimetypes
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024  # 50 MB


def free_name(folder: Path, stem: str, ext: str) -> Path:
    """folder/stem.ext, or stem-1.ext, stem-2.ext ... if that name is taken."""
    out = folder / f"{stem}.{ext}"
    i = 1
    while out.exists():
        out = folder / f"{stem}-{i}.{ext}"
        i += 1
    return out


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


@register("pdf", "zip", "application/zip")
def pdf_to_zip(data: bytes) -> bytes:
    """Every page as page-001.png, page-002.png, ... (150 dpi) in one zip."""
    import zipfile

    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # PNGs are already compressed
            for i, page in enumerate(doc, start=1):
                z.writestr(f"page-{i:03d}.png", page.get_pixmap(dpi=150).tobytes("png"))
        return buf.getvalue()
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


def _open_image(data: bytes):
    from PIL import Image, UnidentifiedImageError
    from pillow_heif import register_heif_opener

    register_heif_opener()  # lets Image.open read .heic/.heif (idempotent)
    Image.MAX_IMAGE_PIXELS = 200_000_000  # decompression-bomb guard
    try:
        return Image.open(io.BytesIO(data))
    except UnidentifiedImageError:
        raise ValueError("cannot read this image") from None


def image_convert(data: bytes, src_ext: str, dst_ext: str) -> bytes:
    im = _open_image(data)
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


def image_to_pdf(data: bytes, src_ext: str) -> bytes:
    """One page sized to the image (longest side 842 pt, like A4), first frame only."""
    import pymupdf
    from PIL import Image

    im = _open_image(data)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        flat = Image.new("RGB", im.size, "white")  # transparent areas become white, not black
        flat.paste(im, mask=im.getchannel("A"))
        im = flat
    else:
        im = im.convert("RGB")
    jpeg = io.BytesIO()
    im.save(jpeg, "JPEG", quality=92)
    scale = 842 / max(im.size)
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=max(im.width * scale, 1), height=max(im.height * scale, 1))
        page.insert_image(page.rect, stream=jpeg.getvalue())
        return doc.tobytes()
    finally:
        doc.close()


for _src, _targets in IMAGE_SOURCES.items():
    for _dst in _targets:
        register(_src, _dst, f"image/{'jpeg' if _dst == 'jpg' else _dst}")(
            functools.partial(image_convert, src_ext=_src, dst_ext=_dst))
    register(_src, "pdf", "application/pdf")(functools.partial(image_to_pdf, src_ext=_src))


# --- Chains: convert in more than one step ---------------------------------------
# When no converter exists for a pair, find the shortest route through REGISTRY, e.g.
# docx -> pdf -> png. A direct converter always wins over any route.

MAX_HOPS = 3
# Formats that throw away layout or frames: never used as a stop on the way.
LOSSY_INTERMEDIATES = {"txt", "csv", "html", "rtf", "zip"}
# Preferred stops, best first, to choose between routes of the same length.
PREFERRED_INTERMEDIATES = ["pdf", "png", "docx", "jpg"]

Route = list[tuple[str, str]]  # [(src, dst), ...], every pair is in REGISTRY


def _rank(fmt: str) -> int:
    return PREFERRED_INTERMEDIATES.index(fmt) if fmt in PREFERRED_INTERMEDIATES else len(PREFERRED_INTERMEDIATES)


def _best_routes_from(src: str) -> dict[str, Route]:
    """Best route from src to every format reachable in at most MAX_HOPS steps."""
    edges: dict[str, list[str]] = {}
    for (a, b) in REGISTRY:
        edges.setdefault(a, []).append(b)
    best: dict[str, tuple[tuple, Route]] = {}

    def walk(path: list[str]):
        here = path[-1]
        if len(path) > 1:
            stops = path[1:-1]
            score = (len(path), tuple(_rank(x) for x in stops), tuple(path))
            if here not in best or score < best[here][0]:
                best[here] = (score, list(zip(path, path[1:])))
        if len(path) - 1 >= MAX_HOPS:
            return
        for nxt in sorted(edges.get(here, [])):
            if nxt in path:
                continue  # no loops
            if len(path) >= 1 and here != src and here in LOSSY_INTERMEDIATES:
                continue  # may not continue from a lossy stop
            walk(path + [nxt])

    walk([src])
    return {dst: route for dst, (_, route) in best.items()}


def find_route(src: str, dst: str) -> Route | None:
    """[(src, dst)] if a direct converter exists, else the shortest multi-step route, else None."""
    if (src, dst) in REGISTRY:
        return [(src, dst)]
    return _best_routes_from(src).get(dst)


def chained_pairs() -> dict[tuple[str, str], Route]:
    """Every (src, dst) that has no direct converter but can be reached by a route."""
    out = {}
    for src in sorted({a for (a, _) in REGISTRY}):
        for dst, route in _best_routes_from(src).items():
            if (src, dst) not in REGISTRY and dst != src:
                out[(src, dst)] = route
    return out


def run_chain(data: bytes, route: Route) -> bytes:
    """Run each hop on the previous hop's output; a failure names the hop that broke."""
    for step, (src, dst) in enumerate(route, start=1):
        try:
            data = REGISTRY[(src, dst)].run(data)
            if step < len(route) and len(data) > MAX_BYTES:
                raise ValueError("intermediate result is over the 50 MB limit")
        except Exception as exc:
            if len(route) == 1:
                raise
            raise RuntimeError(f"{src}->{dst} failed (step {step} of {len(route)}): {exc}") from exc
    return data


def content_type_for(route: Route) -> str:
    return REGISTRY[route[-1]].content_type


# --- Files: shared by the watcher and the command line ---------------------------

def convert_path(path: Path, fmt: str) -> tuple[bytes, Route]:
    """Convert a file on disk to fmt. Returns (output bytes, route used)."""
    src = path.suffix.lower().lstrip(".")
    route = find_route(src, fmt)
    if route is None:
        raise ValueError(f"cannot convert {src or 'this file type'} to {fmt}")
    size = path.stat().st_size
    if size == 0:
        raise ValueError("file is empty")
    if size > MAX_BYTES:
        raise ValueError("file is over the 50 MB limit")
    return run_chain(path.read_bytes(), route), route


def save_output(folder: Path, stem: str, fmt: str, data: bytes) -> Path:
    """Write folder/stem.fmt without overwriting anything (stem-1.fmt if taken)."""
    folder.mkdir(parents=True, exist_ok=True)
    out = free_name(folder, stem, fmt)
    tmp = out.with_name(f".{out.name}.tmp")
    tmp.write_bytes(data)
    tmp.rename(out)  # appears complete or not at all
    return out
