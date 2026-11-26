"""FileForge — a local web app for converting documents.

The web layer only: upload handling, safety checks and the static UI. The
converters themselves live in converters.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from converters import MAX_BYTES, REGISTRY

PORT = 8420
LOCAL_HOSTS = ["127.0.0.1", "localhost"]

app = FastAPI(title="FileForge")
# Only answer requests addressed to this machine (blocks DNS rebinding), and refuse
# state-changing requests that a web page on another site sent (blocks cross-site
# uploads). Requests with no Origin header (curl, the watcher, scripts) pass.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)


@app.middleware("http")
async def refuse_oversize_early(request, call_next):
    # Stop a huge upload from its Content-Length header, before the body is parsed.
    declared = request.headers.get("content-length", "")
    if request.method == "POST" and declared.isdigit() and int(declared) > MAX_BYTES + 1024 * 1024:
        return JSONResponse({"detail": "File too large (50 MB limit)"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def refuse_cross_site_writes(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin is not None:
        if urlparse(origin).hostname not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    return await call_next(request)


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

    data = await file.read(MAX_BYTES + 1)  # never hold more than the limit in memory
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (50 MB limit)")

    try:
        out = await run_in_threadpool(conv.run, data)
    except Exception as exc:  # converter failure -> 422 with a readable message
        raise HTTPException(422, f"Conversion failed: {exc}") from exc

    name = f"{_safe_stem(file.filename)}.{key[1]}"
    return _download(out, name, conv.content_type)


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
