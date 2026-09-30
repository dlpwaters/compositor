#!/usr/bin/env bash
# Per-user source install. Only the explicit dependency option uses elevation.
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
system_packages=(base-devel python uv qt6-wayland qt6-svg desktop-file-utils ttf-liberation)
install_deps=false
install_model=false
background=true
check_only=false
remove=false
stage='checking prerequisites'

usage() {
  cat <<'EOF'
Usage: ./install.sh [OPTIONS]
  --check                 Check prerequisites without changing files
  --install-system-deps   Install missing Arch packages through graphical pkexec
  --install-model         Download the pinned local background-removal model
  --without-background    Omit optional ONNX Runtime (no background removal)
  --remove                Remove owned launchers; keep projects, settings and models
  --system-packages       Print the Arch prerequisite package names and exit
  -h, --help              Show this help

Run as your normal desktop user from a checkout you intend to keep.
The dependency option never refreshes package databases or upgrades the system.
EOF
}
fail() { printf 'Compositor Linux: %s\n' "$*" >&2; exit 1; }
trap 'printf "Compositor Linux: failed while %s. See README.md troubleshooting.\n" "$stage" >&2' ERR

for arg in "$@"; do
  case "$arg" in
    --check) check_only=true ;;
    --install-system-deps) install_deps=true ;;
    --install-model) install_model=true ;;
    --without-background) background=false ;;
    --remove) remove=true ;;
    --system-packages) printf '%s\n' "${system_packages[@]}"; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "Unknown option: $arg" ;;
  esac
done
if "$remove" && { "$install_deps" || "$install_model" || "$check_only"; }; then
  fail '--remove cannot be combined with install or check options.'
fi
if "$install_model" && ! "$background"; then
  fail '--install-model requires background removal support.'
fi
[[ $(uname -s) == Linux ]] || fail 'This installer requires Linux.'
if [[ $EUID == 0 ]] && ! "$check_only"; then
  fail 'Run as your normal user, not root. Only missing system packages need administrator authorization.'
fi
if "$remove"; then
  command -v python3 >/dev/null || fail 'Python 3 is required to remove the launchers.'
  exec python3 "$project_root/scripts/linux-desktop.py" remove
fi

missing=()
if command -v pacman >/dev/null; then
  for package in "${system_packages[@]}"; do
    # A user-installed uv is already sufficient; do not replace it with pacman.
    if [[ $package == uv ]] && command -v uv >/dev/null; then continue; fi
    if ! pacman -Q "$package" >/dev/null 2>&1; then missing+=("$package"); fi
  done
elif "$install_deps" && ! "$check_only"; then
  fail '--install-system-deps requires Arch Linux / pacman. See the manual prerequisites in README.md.'
fi
if ((${#missing[@]})); then
  printf 'Missing Arch prerequisites: %s\n' "${missing[*]}"
  if "$install_deps" && ! "$check_only"; then
    command -v pkexec >/dev/null || fail 'pkexec is unavailable. Install these packages using your package manager, then rerun.'
    [[ -x /usr/bin/pacman ]] || fail 'Expected the Arch package manager at /usr/bin/pacman.'
    stage='installing system prerequisites'
    printf 'A graphical administrator prompt will authorize pacman -S --needed for the packages above.\n'
    pkexec /usr/bin/pacman -S --needed "${missing[@]}"
  else
    printf 'Install with your package manager, or rerun ./install.sh --install-system-deps.\n' >&2
    exit 1
  fi
fi
for tool in python3 uv cc desktop-file-validate; do
  command -v "$tool" >/dev/null || fail "Missing command: $tool. Install the prerequisites listed in README.md."
done
python_bin=$(command -v python3)
"$python_bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || fail 'Python 3.11 or newer is required.'
venv_python="$project_root/linux/.venv/bin/python"
if [[ -e "$project_root/linux/.venv" ]]; then
  [[ -x "$venv_python" ]] || fail 'linux/.venv is incomplete. Rename it as a backup, then rerun the installer.'
  system_version=$("$python_bin" -c 'import sys; print(sys.version_info[:2])')
  venv_version=$("$venv_python" -c 'import sys; print(sys.version_info[:2])') || fail 'linux/.venv is broken. Rename it as a backup, then rerun the installer.'
  [[ $system_version == "$venv_version" ]] || fail 'Python changed since installation. Rename linux/.venv as a backup, then rerun; your projects and models are separate.'
fi
printf 'Prerequisites OK (%s).\n' "$("$python_bin" --version)"
if "$check_only"; then exit 0; fi

stage='creating the local Python environment'
if [[ ! -x "$venv_python" ]]; then
  uv venv --python "$python_bin" "$project_root/linux/.venv"
fi
stage='installing Python dependencies and compiling the original pixel kernels'
requirement="$project_root"
if "$background"; then requirement+='[background]'; fi
uv pip install --python "$venv_python" --upgrade -e "$requirement"
stage='checking the installed renderer'
health_options=()
if "$background"; then health_options+=(--background); fi
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME= "$venv_python" "$project_root/scripts/linux-check.py" "${health_options[@]}"
stage='installing per-user desktop launchers'
"$venv_python" "$project_root/scripts/linux-desktop.py" install
desktop_data=${XDG_DATA_HOME:-"$HOME/.local/share"}
desktop-file-validate "$desktop_data/applications/compositor.desktop"
if "$install_model"; then
  stage='downloading the optional checksummed model'
  "$project_root/linux/.venv/bin/compositor-install-model"
fi
printf '\nInstalled. Open Compositor Linux in your application launcher, or run:\n  %s/.local/bin/compositor-linux\n' "$HOME"
printf 'Keep this checkout in place. Rerun ./install.sh to update; use --remove to remove launchers.\n'
