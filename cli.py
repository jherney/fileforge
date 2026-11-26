"""FileForge on the command line: convert files without the server or a browser.

    python3 cli.py convert FILE_OR_FOLDER [...] --to FMT [--out DIR] [-r] [--notify]
    python3 cli.py targets FILE

The converted file is written next to the original (or into --out) and never
overwrites anything: a taken name becomes name-1.fmt. A folder stands for the files
in it that can become FMT (others, and hidden/temporary files, are skipped); -r descends
into subfolders. Exit status: 0 everything converted, 1 a conversion failed, 2 bad
request (missing file, no way to reach FMT, folder with nothing convertible).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from watcher import ignored
from converters import REGISTRY, chained_pairs, convert_path, find_route, save_output


def ext_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def cmd_targets(args) -> int:
    src = ext_of(args.file)
    direct = sorted(t for (s, t) in REGISTRY if s == src)
    chained = {t: route for (s, t), route in chained_pairs().items() if s == src}
    if not direct and not chained:
        print(f"{args.file.name}: FileForge cannot convert .{src or '(no extension)'} files", file=sys.stderr)
        return 2
    for t in direct:
        print(t)
    for t in sorted(chained):
        print(f"{t} via {', '.join(dst for _, dst in chained[t][:-1])}")
    return 0


def expand(paths: list[Path], fmt: str, recursive: bool):
    """Yield (path, explicit): files as given, folders replaced by their convertible files."""
    for p in paths:
        if not p.is_dir():
            yield p, True
            continue
        members = sorted(p.rglob("*") if recursive else p.iterdir())
        found = [m for m in members
                 if m.is_file() and not ignored(m) and find_route(ext_of(m), fmt)]
        if not found:
            print(f"{p}: no files that can become {fmt}", file=sys.stderr)
            yield p, None
        yield from ((m, False) for m in found)


def cmd_convert(args) -> int:
    fmt = args.to.lower().lstrip(".")
    status = 0
    converted = failed = 0
    for path, explicit in expand(args.files, fmt, args.recursive):
        if explicit is None:
            status = max(status, 2)
            continue
        if not path.is_file():
            print(f"{path}: no such file", file=sys.stderr)
            status = max(status, 2)
            failed += 1
            continue
        if find_route(ext_of(path), fmt) is None:
            print(f"{path.name}: cannot convert .{ext_of(path) or '(no extension)'} to {fmt}", file=sys.stderr)
            status = max(status, 2)
            failed += 1
            continue
        try:
            data, _ = convert_path(path, fmt)
            out = save_output(args.out or path.parent, path.stem, fmt, data)
        except Exception as exc:  # one bad file must not stop the rest
            print(f"{path.name}: {exc}", file=sys.stderr)
            status = max(status, 1)
            failed += 1
            continue
        print(f"{path.name} -> {out}")
        converted += 1
    if args.notify:
        from watcher import notify
        body = f"{converted} converted" + (f", {failed} failed" if failed else "")
        notify("FileForge", body)
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cli.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("convert", help="convert files and folders")
    c.add_argument("files", metavar="FILE_OR_FOLDER", nargs="+", type=Path)
    c.add_argument("--to", required=True, metavar="FMT", help="target format, e.g. pdf, png, jpg")
    c.add_argument("--out", type=Path, help="folder for the results (default: beside each file)")
    c.add_argument("-r", "--recursive", action="store_true", help="include files in subfolders of any folder given")
    c.add_argument("--notify", action="store_true", help="show one desktop notification when done")
    c.set_defaults(func=cmd_convert)
    t = sub.add_parser("targets", help="list what a file can be converted to")
    t.add_argument("file", type=Path)
    t.set_defaults(func=cmd_targets)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
