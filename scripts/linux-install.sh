#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
command -v uv >/dev/null || { printf 'Install uv first (Arch: pacman -S uv).\n' >&2; exit 1; }
if [[ ! -x "$project_root/linux/.venv/bin/python" ]]; then
  uv venv "$project_root/linux/.venv"
fi
uv pip install --python "$project_root/linux/.venv/bin/python" -e "$project_root[background]"
"$project_root/linux/.venv/bin/python" "$project_root/scripts/linux-desktop.py" install
printf 'Run compositor or open Compositor from the application launcher.\n'
