"""FileForge — a local web app for converting documents.

The web layer only: upload handling, safety checks and the static UI. The
converters themselves live in converters.py.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from converters import MAX_BYTES, REGISTRY, chained_pairs, content_type_for, find_route, run_chain

log = logging.getLogger("fileforge")
PORT = 8420
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
ALLOWED_ORIGINS = {f"http://{h}:{PORT}" for h in LOCAL_HOSTS}
TOO_LARGE = f"File too large ({MAX_BYTES >> 20} MB limit)"
CONVERSION_SLOTS = asyncio.Semaphore(2)  # each conversion can use a lot of memory or a whole LibreOffice

app = FastAPI(title="FileForge")
# Only answer requests addressed to this machine (blocks DNS rebinding), and refuse
# state-changing requests that a web page on another site sent (blocks cross-site
# uploads). Requests with no Origin header (curl, the watcher, scripts) pass.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)


@app.middleware("http")
async def refuse_oversize_early(request, call_next):
    # Stop a huge upload from its Content-Length header, before the body is parsed. A POST
    # without one (chunked) would be spooled to disk in full first, so it is refused.
    if request.method == "POST":
        declared = request.headers.get("content-length", "")
        if not declared.isdigit():
            return JSONResponse({"detail": "Content-Length is required"}, status_code=411)
        if int(declared) > MAX_BYTES + 1024 * 1024:
            return JSONResponse({"detail": TOO_LARGE}, status_code=413)
    return await call_next(request)


# The UI is one page with inline CSS, its own script, and a data: arrow icon. Nothing else.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                               "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


@app.middleware("http")
async def refuse_cross_site_writes(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin is not None:
        if origin not in ALLOWED_ORIGINS:  # another local dev server (localhost:3000) is cross-site too
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    return await call_next(request)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
TEMP_PATH = re.compile(r"(?:file://)?/(?:tmp|var|home|run)/\S*")


def _public_message(exc: Exception) -> str:
    """The error text without server paths, and short: converter stderr can be long."""
    msg = TEMP_PATH.sub("<path>", str(exc)).strip() or "unknown error"
    return msg if len(msg) <= 300 else msg[:300] + "…"


def _safe_stem(filename: str) -> str:
    stem = Path(filename or "document").stem
    stem = SAFE_NAME.sub("_", stem).strip(" .") or "document"
    return stem[:80]


@app.get("/api/formats")
def formats():
    pairs = [{"from": s, "to": t} for (s, t) in sorted(REGISTRY)]
    for (s, t), route in sorted(chained_pairs().items()):
        pairs.append({"from": s, "to": t, "via": [dst for _, dst in route[:-1]]})
    return JSONResponse({"pairs": pairs})


@app.post("/api/convert")
async def convert(file: UploadFile, fmt_from: str = "pdf", fmt_to: str = "docx"):
    key = (fmt_from.lower().strip("."), fmt_to.lower().strip("."))
    route = find_route(*key)  # one hop if a direct converter exists, else a chain
    if route is None:
        raise HTTPException(400, f"Unsupported conversion: {key[0]} -> {key[1]}")

    data = await file.read(MAX_BYTES + 1)  # never hold more than the limit in memory
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, TOO_LARGE)

    try:
        async with CONVERSION_SLOTS:
            out = await run_in_threadpool(run_chain, data, route)
    except Exception as exc:  # converter failure -> 422 with a readable message
        log.warning("conversion %s -> %s failed: %s", *key, exc)
        raise HTTPException(422, f"Conversion failed: {_public_message(exc)}") from exc

    name = f"{_safe_stem(file.filename)}.{key[1]}"
    return _download(out, name, content_type_for(route))


def _download(data: bytes, name: str, content_type: str):
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
    uvicorn.run(app, host="127.0.0.1", port=PORT)
