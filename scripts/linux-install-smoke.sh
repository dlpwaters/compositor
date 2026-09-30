#!/usr/bin/env bash
# Run as an ordinary user in a disposable Arch environment with prerequisites installed.
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_root"
./install.sh --check
./install.sh
./install.sh
"$HOME/.local/bin/compositor-linux" --version
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME= linux/.venv/bin/python scripts/linux-check.py --background
[[ ! -e ${XDG_DATA_HOME:-"$HOME/.local/share"}/compositor/models/u2netp.onnx ]]
./install.sh --remove
[[ ! -e "$HOME/.local/bin/compositor-linux" ]]
[[ -x linux/.venv/bin/python ]]
printf 'Fresh Arch user installation, reinstall, renderer and launcher removal OK.\n'
