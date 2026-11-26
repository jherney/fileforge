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
import importlib.util
import io
import itertools
import math
import mimetypes
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Vercel functions refuse request bodies over about 4.5 MB, so the hosted limit is lower.
MAX_BYTES = int(os.environ.get("FILEFORGE_MAX_MB") or (4 if os.environ.get("VERCEL") else 50)) * 1024 * 1024
MAX_PIXELS = 100_000_000      # largest image we decode (about 400 MB as RGBA)
MAX_RENDER_PIXELS = 50_000_000  # largest PDF page we rasterise (dpi is lowered to fit)
MAX_PDF_PAGES = 200           # pages turned into PNGs in one zip
LO_TIMEOUT = 180              # seconds for one LibreOffice conversion
PDF_TIMEOUT = 120             # seconds for PyMuPDF / pdf2docx work in the worker process


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


def pdf_to_txt(data: bytes) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return "\n\n".join(page.get_text() for page in doc).encode("utf-8")
    finally:
        doc.close()


def _render_page(page) -> bytes:
    """Page as PNG at 150 dpi, lowered for huge pages so the bitmap stays under MAX_RENDER_PIXELS."""
    dpi = 150
    area = page.rect.width * page.rect.height * (dpi / 72) ** 2
    if area > MAX_RENDER_PIXELS:
        dpi = max(10, int(dpi * math.sqrt(MAX_RENDER_PIXELS / area)))
    return page.get_pixmap(dpi=dpi).tobytes("png")


def pdf_to_png(data: bytes) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return _render_page(doc[0])
    finally:
        doc.close()


def pdf_to_zip(data: bytes) -> bytes:
    """Every page as page-001.png, page-002.png, ... (150 dpi) in one zip."""
    import zipfile

    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if len(doc) > MAX_PDF_PAGES:
            raise ValueError(f"the PDF has {len(doc)} pages; the limit is {MAX_PDF_PAGES}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # PNGs are already compressed
            for i, page in enumerate(doc, start=1):
                z.writestr(f"page-{i:03d}.png", _render_page(page))
                if buf.tell() > MAX_BYTES:
                    raise ValueError(f"the zip would be over the {MAX_BYTES >> 20} MB limit (stopped at page {i} of {len(doc)})")
        return buf.getvalue()
    finally:
        doc.close()


def _communicate_or_kill(proc: subprocess.Popen, data: bytes | None, timeout: float) -> tuple[bytes, bytes] | None:
    """proc.communicate(), but on timeout kill its whole process group and return None.

    proc must have been started with start_new_session=True.
    """
    try:
        return proc.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None


# --- Worker process for PDF work ---------------------------------------------------
# PyMuPDF and pdf2docx run untrusted PDFs in native code with no time limit of their own.
# Each call runs in a child process that is killed on timeout (or dies alone on a crash).

_WORKER_CODE = "import sys; sys.path.insert(0, sys.argv[1]); import converters; converters._worker_main(sys.argv[2])"
_ERR_MARK = "FFERR:"


def _worker_main(name: str) -> None:
    """Child side: bytes on stdin -> converter `name` -> bytes on stdout."""
    data = sys.stdin.buffer.read()
    result_fd = os.dup(1)
    os.dup2(2, 1)  # library chatter on stdout must not mix into the result
    try:
        out = globals()[name](data)
    except Exception as exc:
        kind = "value" if isinstance(exc, ValueError) else "other"
        text = str(exc)
        print(f"{_ERR_MARK}{kind}:{text.splitlines()[0] if text else type(exc).__name__}", file=sys.stderr)
        sys.exit(3)
    with os.fdopen(result_fd, "wb") as f:
        f.write(out)


def run_isolated(name: str, data: bytes, timeout: float | None = None) -> bytes:
    timeout = PDF_TIMEOUT if timeout is None else timeout
    proc = subprocess.Popen(
        [sys.executable, "-c", _WORKER_CODE, str(Path(__file__).resolve().parent), name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        # the worker must find the same packages as this process (hosts may install them off the default path)
        env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)})
    result = _communicate_or_kill(proc, data, timeout)
    if result is None:
        raise RuntimeError(f"the PDF conversion took longer than {timeout:g} s and was stopped")
    out, err = result
    if proc.returncode == 0:
        return out
    lines = [l for l in err.decode(errors="replace").splitlines() if l.startswith(_ERR_MARK)]
    if lines:
        kind, _, msg = lines[-1][len(_ERR_MARK):].partition(":")
        raise (ValueError if kind == "value" else RuntimeError)(msg)
    raise RuntimeError("the PDF converter crashed (out of memory or a damaged file)")


# Only pairs this machine can actually run are registered, so the UI never offers the rest.
HAVE_PDF2DOCX = importlib.util.find_spec("pdf2docx") is not None
HAVE_SOFFICE = shutil.which("soffice") is not None

for _name, _dst, _ct in [
        ("pdf_to_docx", "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf_to_txt", "txt", "text/plain"),
        ("pdf_to_png", "png", "image/png"),
        ("pdf_to_zip", "zip", "application/zip")]:
    if _name != "pdf_to_docx" or HAVE_PDF2DOCX:
        register("pdf", _dst, _ct)(functools.partial(run_isolated, _name))


# --- LibreOffice headless engine -------------------------------------------

# LibreOffice follows links in documents (HTML stylesheets, linked images). Run it in a new
# network namespace so a crafted file cannot make it call localhost or the LAN.
def _network_isolation() -> list[str]:
    if not shutil.which("unshare"):
        return []
    cmd = ["unshare", "--map-root-user", "--net"]
    try:  # some systems disallow unprivileged namespaces; then run without
        ok = subprocess.run([*cmd, "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return cmd if ok else []


NO_NETWORK = _network_isolation() if HAVE_SOFFICE else []


def soffice_convert(data: bytes, src_ext: str, dst_ext: str) -> bytes:
    """Run one headless LibreOffice conversion in an isolated temp profile."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"input.{src_ext}"
        src.write_bytes(data)
        profile = Path(td) / "lo_profile"
        cmd = [
            *NO_NETWORK, "soffice", "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation=file://{profile}",
            *(["--infilter=HTML (StarWriter)"] if src_ext == "html" else []),
            "--convert-to", dst_ext, "--outdir", td, str(src),
        ]
        # own process group, so a timeout can kill soffice.bin and not just the launcher
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        result = _communicate_or_kill(proc, None, LO_TIMEOUT)
        if result is None:
            raise RuntimeError("LibreOffice conversion timed out")
        stdout, stderr = result
        out = Path(td) / f"input.{dst_ext}"
        if not out.exists():
            err = (stdout + stderr).decode(errors="replace").strip()
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

for _src, _targets in (OFFICE_SOURCES.items() if HAVE_SOFFICE else ()):
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
    "tif": ["png", "jpg", "webp", "gif", "bmp"],  # same format, the common short extension
    "heic": ["jpg", "png", "webp", "gif", "bmp", "tiff"],  # phone photos
    "heif": ["jpg", "png", "webp", "gif", "bmp", "tiff"],
}


def _open_image(data: bytes):
    from PIL import Image, ImageOps, UnidentifiedImageError
    from pillow_heif import register_heif_opener

    register_heif_opener()  # lets Image.open read .heic/.heif (idempotent)
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(data))  # lazy: reads the header only
    except UnidentifiedImageError:
        raise ValueError("cannot read this image") from None
    except Image.DecompressionBombError:  # Pillow's own refusal, above twice the limit
        raise ValueError(f"the image is too large; the limit is {MAX_PIXELS // 1_000_000} megapixels") from None
    # Pillow only raises above twice MAX_IMAGE_PIXELS, so check the size ourselves
    if im.width * im.height > MAX_PIXELS:
        raise ValueError(f"the image is {im.width}x{im.height}; the limit is {MAX_PIXELS // 1_000_000} megapixels")
    return ImageOps.exif_transpose(im)  # phone photos store "rotate me" in EXIF


def image_convert(data: bytes, src_ext: str, dst_ext: str) -> bytes:
    im = _open_image(data)
    if im.mode in ("I", "I;16", "I;16L", "I;16B", "F"):
        if dst_ext not in ("png", "tiff"):  # 8-bit formats: scale 16-bit down, not clip
            im = im.convert("I").point(lambda v: v / 256).convert("L") if im.mode != "F" else im.convert("L")
    elif im.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        im = im.convert("RGB")  # CMYK, YCbCr, LAB, HSV...: no target but TIFF/JPEG holds them
    if im.mode == "LA" and dst_ext in ("bmp", "gif"):
        im = im.convert("RGBA")  # these cannot store gray+alpha
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

TEXT_TARGETS = {"txt", "csv", "html", "rtf"}  # whitespace-only output counts as empty

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
            if here != src and here in LOSSY_INTERMEDIATES:
                continue  # may not continue from a lossy stop
            walk(path + [nxt])

    walk([src])
    # An image only ever becomes another image (or a PDF, directly): routes through
    # PDF to text, HTML, DOCX... would be empty or meaningless.
    return {dst: route for dst, (_, route) in best.items()
            if src not in IMAGE_SOURCES or dst in IMAGE_SOURCES}


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
                raise ValueError(f"intermediate result is over the {MAX_BYTES >> 20} MB limit")
        except Exception as exc:
            if len(route) == 1:
                raise
            raise RuntimeError(f"{src}->{dst} failed (step {step} of {len(route)}): {exc}") from exc
    if len(data) > MAX_BYTES:
        raise ValueError(f"the result is over the {MAX_BYTES >> 20} MB limit")
    text_like = route[-1][1] in TEXT_TARGETS
    if not (data.strip() if text_like else data):
        raise ValueError("the result is empty (the source has nothing to convert to this format)")
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
        raise ValueError(f"file is over the {MAX_BYTES >> 20} MB limit")
    return run_chain(path.read_bytes(), route), route


def save_output(folder: Path, stem: str, fmt: str, data: bytes) -> Path:
    """Write folder/stem.fmt without overwriting anything (stem-1.fmt if taken).

    The data goes to a private temp file first, then a hard link claims the final
    name: it appears complete or not at all, and a name taken meanwhile is skipped.
    """
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".fileforge-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        for i in itertools.count():
            out = folder / (f"{stem}.{fmt}" if i == 0 else f"{stem}-{i}.{fmt}")
            try:
                os.link(tmp, out)
                return out
            except FileExistsError:
                continue
            except OSError:  # file system without hard links (FAT, some network shares)
                if not out.exists():
                    os.rename(tmp, out)
                    return out
    finally:
        Path(tmp).unlink(missing_ok=True)
