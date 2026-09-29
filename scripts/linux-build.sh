#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_root"
python_bin=${COMPOSITOR_PYTHON:-"$project_root/linux/.venv/bin/python"}
if [[ ! -x "$python_bin" ]]; then
  printf 'Create the environment first: uv venv linux/.venv && uv pip install --python linux/.venv/bin/python -e ".[test]"\n' >&2
  exit 1
fi
"$python_bin" setup.py build_ext --inplace
