"""Install the FileForge magic-folder watcher as a systemd user service.

    python3 deploy/install_watcher_service.py             # write the unit and daemon-reload
    python3 deploy/install_watcher_service.py --uninstall # stop, disable and remove it

Fills in this checkout's path and the current python3 in deploy/fileforge-watcher.service,
so it works wherever the repo is cloned. It does NOT enable or start the service; the
command to do that is printed. Use --name to install under another unit name.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "fileforge-watcher.service"
UNIT_DIR = Path.home() / ".config/systemd/user"


def render(python: str = sys.executable, root: Path = ROOT) -> str:
    text = TEMPLATE.read_text()
    text = text.replace("/usr/bin/python3", shlex.quote(python))
    # systemd needs literal spaces escaped as \x20 in paths; %h-relative defaults become absolute
    return text.replace("%h/fileforge", str(root).replace(" ", "\\x20"))


def systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--name", default="fileforge-watcher")
    args = ap.parse_args()
    target = UNIT_DIR / f"{args.name}.service"
    if args.uninstall:
        systemctl("disable", "--now", f"{args.name}.service")
        target.unlink(missing_ok=True)
        systemctl("daemon-reload")
        print(f"removed {target}")
        return 0
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(render())
    systemctl("daemon-reload")
    print(f"installed {target}\nstart it with: systemctl --user enable --now {args.name}.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
