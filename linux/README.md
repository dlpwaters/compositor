# Compositor for Arch Linux and Omarchy

Native Qt/Wayland image editing alongside the original macOS app, based on
upstream `a19db901` (Compositor 1.0.4). The Linux editor provides layers, folders,
masks, clipping, nondestructive transforms, selections, painting, retouching,
six editable adjustment types, filters, project tabs, and image import/export.
It builds the original eight C pixel kernels without changing their source.

This is a working Linux port with an explicit [parity audit](../docs/linux-parity.md).
Exact macOS pixel equivalence and Apple Vision equivalence are not certified.
The UI follows the same editing workflow using Linux-native controls and Ctrl
shortcuts. Rendering and filters currently use the CPU.

## Per-user installation on Omarchy

Requires Python 3.11+, a C compiler, and `uv`. Arch's `base-devel`, `python`,
`uv`, and `qt6-wayland` packages provide the system prerequisites. The installer
uses a project-local virtual environment and per-user launcher files; it needs
no root privileges, services, or Hyprland configuration changes.

From the repository root:

```sh
scripts/linux-install.sh
linux/.venv/bin/compositor-install-model
compositor
```

The model command explicitly downloads a pinned, checksummed 4.6 MB U2NETP model.
Images stay local. Without a model the editor still works; Remove Background
asks for a local U2NET-compatible ONNX file. The installer includes ONNX Runtime.
The default model is under `$XDG_DATA_HOME/compositor/models/` (normally
`~/.local/share/compositor/models/`). Its source and license are documented in
the parity audit. An existing different model file is preserved.

The application appears as **Compositor** in Omarchy's launcher. It also accepts
image paths or `.comp` directories on the command line. The installer preserves
unrelated launchers and refuses to replace files it does not own. Moving the
checkout requires rerunning the installer. Remove its launcher files with:

```sh
linux/.venv/bin/python scripts/linux-desktop.py remove
```

Removal keeps projects, preferences, the model, and the virtual environment.

## Arch package

[`packaging/arch/PKGBUILD`](../packaging/arch/PKGBUILD) builds a package from a
local source archive. Install the listed build and check dependencies first;
this repository does not automatically elevate or upgrade the machine.

```sh
scripts/arch-package.sh
# Install the resulting compositor-linux-*.pkg.tar.zst using your package manager.
```

Runtime packages are `pyside6`, `python-numpy`, `python-pillow`, `python-scipy`,
`python-pillow-heif`, `qt6-svg`, and `qt6-wayland`. Background removal additionally uses
`python-onnxruntime-cpu`. The PKGBUILD supports a local audited archive rather
than downloading an unpublished release. It declares x86_64 and aarch64;
only x86_64 has been exercised so far.

## Editing and shortcuts

Create a canvas or import an image, choose a tool on the left, and select layers
or their mask thumbnails on the right. Tools and common actions use bundled
monochrome SVG icons that scale with display density; hover for tool names and
shortcuts. The Brush icon switches to an eraser in Erase mode.
Most operations commit one undo step;
filter previews disappear when cancelled. Transforming an image preserves its
source raster until a pixel edit is applied. Ctrl+T opens a pending transform;
use Apply/Enter or Cancel/Escape. Crop and gradients also keep editable previews.
Switching tools commits pending transforms/gradients and cancels a crop frame.
Double-click an adjustment layer
to edit it. Open `.comp` projects by selecting the whole package directory.

| Action | Linux shortcut |
| --- | --- |
| New / open / save | Ctrl+N / Ctrl+O / Ctrl+S |
| Import images / Save As | Ctrl+Shift+O / Ctrl+Shift+S |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z |
| Cut / copy / paste / Copy Merged | Ctrl+X / Ctrl+C / Ctrl+V / Ctrl+Shift+C |
| Export PNG / JPEG | Ctrl+Shift+E / Ctrl+Alt+Shift+S |
| Move, marquee, lasso, wand, crop | V, M, L, W, C |
| Brush, eraser, healing, clone, blur/liquify | B, E, J, S, R |
| Gradient, shape, eyedropper, hand, zoom | G, U, I, H, Z |
| Transform / duplicate / merge / group | Ctrl+T / Ctrl+J / Ctrl+E / Ctrl+G |
| Levels / Hue-Saturation / Curves / Invert | Ctrl+L / Ctrl+U / Ctrl+M / Ctrl+I |
| Select all / deselect / inverse | Ctrl+A / Ctrl+D / Ctrl+Shift+I |
| Content-aware fill | Shift+Backspace |
| Foreground / background fill | Alt+Backspace / Ctrl+Backspace |

Space temporarily pans. Shift constrains drags and draws straight brush lines.
Alt-click sets the Clone Stamp source; Alt-drag duplicates a layer. Ctrl-drag a
transform corner distorts pixels. Ctrl-drag a selection moves its pixels;
adding Alt duplicates them. X swaps colors and D resets them.
Window-manager bindings can consume a shortcut before the app receives it.
Every operation is also available through menus; no global bindings are installed.

## Project compatibility and limits

Reads format versions 1–7 and writes version 7. Embedded assets, masks, shapes,
adjustments, hierarchy and clipping references round-trip independently of
original photo paths. See [project format](../docs/project-format.md).
Unknown optional manifest/layer fields are retained. PNG and JPEG exports use
sRGB and document resolution; JPEG previews show the actual encoded bytes with
a selectable transparency background.

Canvas/source sides are limited to 30,000 pixels. Source and mask asset budgets
are each 100 megapixels. Flattened operations also require a raster within
100 megapixels. Project saves use atomic Linux directory exchange and fail
without replacing the existing project if a filesystem lacks that operation.
Undo retains at most 64 steps and approximately 512 MiB of unique raster assets.
Large images can exceed practical memory/latency limits before the file limits.

## Development and verification

```sh
uv venv linux/.venv
uv pip install --python linux/.venv/bin/python -e '.[test,background]' build
linux/.venv/bin/ruff check linux scripts/linux-desktop.py scripts/linux-compare.py setup.py
linux/.venv/bin/ruff format --check linux scripts/linux-desktop.py scripts/linux-compare.py setup.py
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME= linux/.venv/bin/python -m pytest -q
linux/.venv/bin/python -m build
desktop-file-validate packaging/linux/compositor.desktop
```

Rebuild C changes using `scripts/linux-build.sh`; Python edits need no rebuild.
A finite native GUI exercise is available for integration checks:

```sh
QT_QPA_PLATFORM=wayland QT_QPA_PLATFORMTHEME= compositor --smoke-test /tmp/compositor-qa
```

It paints through Qt mouse events, invokes undo/redo, saves and reopens a v7
project, checks pending transforms/crop/gradient, targeted Hue/Saturation and
original-pixel Levels sampling, exports PNG/JPEG, captures the editor, writes
`report.json`, and exits. It checks Copy Merged/Paste with an independent
`wl-paste` reader when installed and restores the previous clipboard.
It does not establish every feature's parity or replace human editing tests.

## License

MIT for this port and upstream Compositor. Dependencies retain their licenses;
PySide6 uses Qt's LGPL/GPL/commercial licensing. Model weights are downloaded
separately and are excluded from repository archives and wheels.
