"""FileForge on the command line: convert files without the server or a browser.

    python3 cli.py convert FILE [FILE ...] --to FMT [--out DIR] [--notify]
    python3 cli.py targets FILE

The converted file is written next to the original (or into --out) and never
overwrites anything: a taken name becomes name-1.fmt. Exit status: 0 everything
converted, 1 a conversion failed, 2 bad request (missing file, no way to reach FMT).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def cmd_convert(args) -> int:
    fmt = args.to.lower().lstrip(".")
    status = 0
    converted = failed = 0
    for path in args.files:
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
    c = sub.add_parser("convert", help="convert one or more files")
    c.add_argument("files", nargs="+", type=Path)
    c.add_argument("--to", required=True, metavar="FMT", help="target format, e.g. pdf, png, jpg")
    c.add_argument("--out", type=Path, help="folder for the results (default: beside each file)")
    c.add_argument("--notify", action="store_true", help="show one desktop notification when done")
    c.set_defaults(func=cmd_convert)
    t = sub.add_parser("targets", help="list what a file can be converted to")
    t.add_argument("file", type=Path)
    t.set_defaults(func=cmd_targets)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
