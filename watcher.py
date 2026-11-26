"""FileForge magic folder: drop a file in ~/Convert/to-<format>/ and the
converted file appears in its out/ subfolder. No browser needed.

    python3 watcher.py                 # watch ~/Convert, create to-pdf/ if missing
    python3 watcher.py --root /path    # watch another folder

Folders named to-<fmt> (to-pdf, to-docx, to-png, ...) are picked up as you
create them. Originals are never moved, changed or deleted. A failed
conversion writes failed/<name>.err and the watcher keeps going; delete that
.err file (or start with --retry-failed) to try the file again. Converters
come straight from REGISTRY in converters.py (a file with no direct converter, say
a .docx in to-png/, goes through the shortest chain, here docx -> pdf -> png) and
run one at a time, so two drops never collide inside LibreOffice.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from converters import REGISTRY, convert_path, save_output

TARGETS = {t for (_, t) in REGISTRY}
SKIP_SUFFIXES = {".part", ".crdownload", ".tmp", ".partial", ".download"}
LOG_NAME = "watcher.jsonl"


def ignored(p: Path) -> bool:
    n = p.name
    return n.startswith(".") or n.endswith("~") or p.suffix.lower() in SKIP_SUFFIXES


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "FileForge", title, body], check=False)


def read_log(log_path: Path) -> list[dict]:
    rows = []
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            try:
                r = json.loads(line)
                r["path"], r["size"], r["mtime_ns"]  # reject rows missing the key fields
                rows.append(r)
            except (ValueError, KeyError, TypeError):
                continue
    return rows


def history_lines(log_path: Path, n: int) -> list[str]:
    """The last n log rows, one readable line each."""
    out = []
    for r in read_log(log_path)[-n:]:
        detail = r.get("output") or r.get("error") or ""
        if r.get("route"):
            detail += f" (via {', '.join(r['route'][1:-1])})"
        out.append(f"{r.get('ts', '')}  {r.get('status', ''):6}  {Path(r['path']).name}  {detail}".rstrip())
    return out


class Watcher:
    def __init__(self, root: Path, retry_failed: bool = False):
        self.root = root
        self.seen: dict[Path, tuple[int, int]] = {}   # path -> (size, mtime_ns) at last poll
        self.done: set[tuple[str, int, int]] = set()  # converted: never redone
        self.failed: set[tuple[str, int, int]] = set()  # failed: retried when its .err is deleted
        self.log_path = root / LOG_NAME
        self._load_log(retry_failed)

    def _load_log(self, retry_failed: bool) -> None:
        for r in read_log(self.log_path):
            key = (r["path"], r["size"], r["mtime_ns"])
            if r.get("status") == "ok":
                self.done.add(key)
            elif not retry_failed:
                self.failed.add(key)

    def _log(self, path: Path, st, status: str, **extra) -> None:
        (self.done if status == "ok" else self.failed).add((str(path), st.st_size, st.st_mtime_ns))
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
                key = (str(p), *sig)
                if key in self.done:
                    continue
                if key in self.failed:
                    if (p.parent / "failed" / f"{p.name}.err").exists():
                        continue
                    self.failed.discard(key)  # its .err was deleted: try again
                converted += self.convert(p, fmt, st)
        self.seen = current
        return converted

    def convert(self, p: Path, fmt: str, st) -> int:
        try:
            data, route = convert_path(p, fmt)
        except Exception as exc:  # any converter failure must not stop the watcher
            failed = p.parent / "failed"
            failed.mkdir(exist_ok=True)
            (failed / f"{p.name}.err").write_text(f"{exc}\n")
            self._log(p, st, "failed", error=str(exc))
            notify(f"Could not convert {p.name}", str(exc))
            print(f"FAILED {p.name}: {exc}", flush=True)
            return 0
        out = save_output(p.parent / "out", p.stem, fmt, data)
        steps = [route[0][0]] + [dst for _, dst in route]  # e.g. ["docx", "pdf", "png"]
        self._log(p, st, "ok", output=str(out), **({"route": steps} if len(route) > 1 else {}))
        notify(f"Converted {p.name}", f"Saved {out.name}")
        print(f"OK {p.name} -> {out}" + (f" (via {', '.join(steps[1:-1])})" if len(route) > 1 else ""), flush=True)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path.home() / "Convert")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between scans")
    ap.add_argument("--retry-failed", action="store_true", help="try files that failed before again")
    ap.add_argument("--history", action="store_true", help="print recent conversions and exit")
    ap.add_argument("-n", type=int, default=20, help="rows to show with --history")
    args = ap.parse_args()
    if args.history:
        print("\n".join(history_lines(args.root / LOG_NAME, args.n)) or "No conversions logged yet.")
        return 0
    args.root.mkdir(parents=True, exist_ok=True)
    if not any(args.root.glob("to-*")):
        (args.root / "to-pdf").mkdir()
    w = Watcher(args.root, retry_failed=args.retry_failed)
    print(f"Watching {args.root}: {', '.join(d.name for d, _ in w.target_dirs())}", flush=True)
    try:
        while True:
            w.poll()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
