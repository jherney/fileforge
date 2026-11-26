"""FileForge magic folder: drop a file in ~/Convert/to-<format>/ and the
converted file appears in its out/ subfolder. No browser needed.

    python3 watcher.py                 # watch ~/Convert, create to-pdf/ if missing
    python3 watcher.py --root /path    # watch another folder

Folders named to-<fmt> (to-pdf, to-docx, to-png, ...) are picked up as you
create them. Originals are never moved, changed or deleted. A failed
conversion writes failed/<name>.err and the watcher keeps going. Converters
come straight from REGISTRY in app.py and run one at a time, so two drops
never collide inside LibreOffice.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app import MAX_BYTES, REGISTRY

TARGETS = {t for (_, t) in REGISTRY}
SKIP_SUFFIXES = {".part", ".crdownload", ".tmp", ".partial", ".download"}
LOG_NAME = "watcher.jsonl"


def ignored(p: Path) -> bool:
    n = p.name
    return n.startswith(".") or n.endswith("~") or p.suffix.lower() in SKIP_SUFFIXES


def free_name(folder: Path, stem: str, ext: str) -> Path:
    out = folder / f"{stem}.{ext}"
    i = 1
    while out.exists():
        out = folder / f"{stem}-{i}.{ext}"
        i += 1
    return out


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "FileForge", title, body], check=False)


class Watcher:
    def __init__(self, root: Path):
        self.root = root
        self.seen: dict[Path, tuple[int, int]] = {}   # path -> (size, mtime_ns) at last poll
        self.done: set[tuple[str, int, int]] = set()  # (path, size, mtime_ns) already handled
        self.log_path = root / LOG_NAME
        self._load_log()

    def _load_log(self) -> None:
        if not self.log_path.exists():
            return
        for line in self.log_path.read_text().splitlines():
            try:
                r = json.loads(line)
                self.done.add((r["path"], r["size"], r["mtime_ns"]))
            except (ValueError, KeyError):
                continue

    def _log(self, path: Path, st, status: str, **extra) -> None:
        self.done.add((str(path), st.st_size, st.st_mtime_ns))
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "path": str(path),
               "size": st.st_size, "mtime_ns": st.st_mtime_ns, "status": status, **extra}
        with self.log_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def target_dirs(self) -> list[tuple[Path, str]]:
        found = []
        for d in sorted(self.root.glob("to-*")):
            fmt = d.name[3:].lower()
            if d.is_dir() and fmt in TARGETS:
                found.append((d, fmt))
        return found

    def poll(self) -> int:
        """One scan. Converts files whose size and mtime did not change since the last scan."""
        converted = 0
        current: dict[Path, tuple[int, int]] = {}
        for folder, fmt in self.target_dirs():
            for p in sorted(folder.iterdir()):
                if not p.is_file() or ignored(p):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                sig = (st.st_size, st.st_mtime_ns)
                current[p] = sig
                if self.seen.get(p) != sig:
                    continue  # new or still changing: wait one more poll
                if (str(p), *sig) in self.done:
                    continue
                converted += self.convert(p, fmt, st)
        self.seen = current
        return converted

    def convert(self, p: Path, fmt: str, st) -> int:
        src = p.suffix.lower().lstrip(".")
        conv = REGISTRY.get((src, fmt))
        try:
            if conv is None:
                raise ValueError(f"cannot convert {src or 'this file type'} to {fmt}")
            if st.st_size == 0:
                raise ValueError("file is empty")
            if st.st_size > MAX_BYTES:
                raise ValueError("file is over the 50 MB limit")
            data = conv.run(p.read_bytes())
        except Exception as exc:  # any converter failure must not stop the watcher
            failed = p.parent / "failed"
            failed.mkdir(exist_ok=True)
            (failed / f"{p.name}.err").write_text(f"{exc}\n")
            self._log(p, st, "failed", error=str(exc))
            notify(f"Could not convert {p.name}", str(exc))
            print(f"FAILED {p.name}: {exc}", flush=True)
            return 0
        out_dir = p.parent / "out"
        out_dir.mkdir(exist_ok=True)
        out = free_name(out_dir, p.stem, fmt)
        tmp = out.with_name(f".{out.name}.tmp")
        tmp.write_bytes(data)
        tmp.rename(out)
        self._log(p, st, "ok", output=str(out))
        notify(f"Converted {p.name}", f"Saved {out.name}")
        print(f"OK {p.name} -> {out}", flush=True)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path.home() / "Convert")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between scans")
    args = ap.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    if not any(args.root.glob("to-*")):
        (args.root / "to-pdf").mkdir()
    w = Watcher(args.root)
    print(f"Watching {args.root}: {', '.join(d.name for d, _ in w.target_dirs())}", flush=True)
    try:
        while True:
            w.poll()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
