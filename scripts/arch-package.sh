#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${COMPOSITOR_PYTHON:-"$project_root/linux/.venv/bin/python"}
if [[ ! -x "$python_bin" && -z "${COMPOSITOR_PYTHON:-}" ]]; then
  python_bin=$(command -v python)
fi
"$python_bin" -m build --sdist --no-isolation --outdir "$project_root/packaging/arch" "$project_root"
cd -- "$project_root/packaging/arch"
exec makepkg "$@"
