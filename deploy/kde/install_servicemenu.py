"""Install a Dolphin right-click "Convert to..." menu that calls cli.py (Plasma 6).

    python3 deploy/kde/install_servicemenu.py [--uninstall]

Writes ~/.local/share/kio/servicemenus/fileforge.desktop, marked executable
(Plasma ignores service menus that are not). The menu appears on every file type
FileForge can read and offers every format it can write; a pair with no route fails
with a desktop notification instead of being hidden.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from converters import REGISTRY, chained_pairs  # noqa: E402

TARGET = Path.home() / ".local/share/kio/servicemenus/fileforge.desktop"
EXTRA_MIMES = {"heic": "image/heic", "heif": "image/heif"}


def source_mimes() -> list[str]:
    mimes = set()
    for ext in {s for s, _ in REGISTRY} | {s for s, _ in chained_pairs()}:
        m = EXTRA_MIMES.get(ext) or mimetypes.guess_type(f"x.{ext}")[0]
        if m:
            mimes.add(m)
    return sorted(mimes)


def build_desktop() -> str:
    targets = sorted({t for _, t in REGISTRY} | {t for _, t in chained_pairs()})
    cli = f"{sys.executable} {ROOT / 'cli.py'}"
    lines = [
        "[Desktop Entry]",
        "Type=Service",
        "ServiceTypes=KonqPopupMenu/Plugin",
        f"MimeType={';'.join(source_mimes())};",
        f"Actions={';'.join(f'to-{t}' for t in targets)};",
        "X-KDE-Submenu=Convert to...",
        "X-KDE-Priority=TopLevel",
        "",
    ]
    for t in targets:
        lines += [
            f"[Desktop Action to-{t}]",
            f"Name={t.upper()}",
            "Icon=document-export",
            f"Exec={cli} convert %F --to {t} --notify",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--uninstall", action="store_true")
    if ap.parse_args().uninstall:
        TARGET.unlink(missing_ok=True)
        print(f"removed {TARGET}")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(build_desktop())
    os.chmod(TARGET, 0o755)
    print(f"installed {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
