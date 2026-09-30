"""Install or remove only Compositor's per-user launcher files."""

import argparse
import os
import shlex
import shutil
import subprocess
from pathlib import Path

MARKER = "# Managed by Compositor's user installer"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "remove"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    launcher = Path.home() / ".local/bin/compositor-linux"
    legacy_launcher = launcher.with_name("compositor")
    desktop = data / "applications/compositor.desktop"
    icon = data / "icons/hicolor/256x256/apps/compositor.png"
    if args.action == "remove":
        for path in (launcher, legacy_launcher, desktop):
            if not path.is_symlink() and path.is_file() and MARKER in path.read_text():
                path.unlink()
        if (
            not icon.is_symlink()
            and icon.is_file()
            and icon.read_bytes()
            == (root / "linux/compositor_linux/assets/compositor.png").read_bytes()
        ):
            icon.unlink()
    else:
        for path in (launcher, desktop):
            if (
                path.is_symlink()
                or path.exists()
                and (not path.is_file() or MARKER not in path.read_text())
            ):
                raise SystemExit(f"Preserving existing file: {path}")
        legacy_available = not legacy_launcher.is_symlink() and (
            not legacy_launcher.exists()
            or legacy_launcher.is_file()
            and MARKER in legacy_launcher.read_text()
        )
        if (
            icon.is_symlink()
            or icon.exists()
            and (
                icon.read_bytes()
                != (root / "linux/compositor_linux/assets/compositor.png").read_bytes()
            )
        ):
            raise SystemExit(f"Preserving existing icon: {icon}")
        for path in (launcher, legacy_launcher, desktop, icon):
            path.parent.mkdir(parents=True, exist_ok=True)
        executable = root / "linux/.venv/bin/compositor-linux"
        if not executable.is_file():
            raise SystemExit("Install the Python package into linux/.venv first.")
        launcher.write_text(
            f'#!/usr/bin/env bash\n{MARKER}\nexec {shlex.quote(str(executable))} "$@"\n'
        )
        launcher.chmod(0o755)
        if legacy_available:
            legacy_launcher.write_text(launcher.read_text())
            legacy_launcher.chmod(0o755)
        else:
            print(f"Preserving unrelated compatibility command: {legacy_launcher}")
        # Desktop Entry quoting is different from shell quoting.
        escaped = (
            str(launcher)
            .replace("\\", "\\\\\\\\")
            .replace('"', '\\\\"')
            .replace("`", "\\\\`")
            .replace("$", "\\\\$")
            .replace("%", "%%")
        )
        entry = (
            (root / "packaging/linux/compositor.desktop")
            .read_text()
            .replace("Exec=compositor-linux %F", f'Exec="{escaped}" %F')
        )
        desktop.write_text(entry + MARKER + "\n")
        shutil.copyfile(root / "linux/compositor_linux/assets/compositor.png", icon)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(desktop.parent)], check=True)
    print(f"Compositor Linux desktop files: {args.action} complete")


if __name__ == "__main__":
    main()
