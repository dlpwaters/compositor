# Compositor Linux

[![Linux checks](https://github.com/dlpwaters/compositor/actions/workflows/linux.yml/badge.svg)](https://github.com/dlpwaters/compositor/actions/workflows/linux.yml)

A native layered image editor for **Arch Linux and Omarchy**, built with Qt and
Wayland. Compose images, paint and retouch, edit text, work with masks and
adjustment layers, and keep the editable project alongside your exports.

This fork ports [Compositor](https://github.com/robbietilton/Compositor), created
by **Robbie Tilton / Wonder Assembly LLC**. It reuses the original eight C pixel
kernels and preserves the macOS sources. The Linux interface, packaging, and
editable text support are maintained in this fork. This is an independent port;
it is not an official Omarchy or upstream Compositor release.

**Early release:** the editor works on native Wayland, but exact Mac rendering
and interaction parity are still being qualified. Rendering uses the CPU, and
local background removal uses U2NETP rather than Apple Vision. See the
[feature and parity audit](docs/linux-parity.md) for specific limits.

[Download the 0.1.0 preview](https://github.com/dlpwaters/compositor/releases/tag/linux-v0.1.0)
for source, a Python 3.14 x86_64 wheel, an experimental Arch package, and checksums.

![Compositor Linux editing a sample poster with text and color layers](docs/screenshots/editor.png)

## Install on Arch / Omarchy

Requires Python 3.11+, `uv`, a C compiler, and Qt Wayland support. Install the
system prerequisites with your package manager (`base-devel`, `python`, `uv`,
`qt6-wayland`, and `qt6-svg`), then:

```sh
git clone https://github.com/dlpwaters/compositor.git
cd compositor
scripts/linux-install.sh
compositor-linux
```

The installer creates a project-local Python environment and a per-user
**Compositor Linux** launcher. It needs no root privileges and installs no
services or global shortcuts. Keep the checkout in place; rerun the installer
after moving it. The `compositor` command remains a compatibility alias.

Optional background removal runs locally. Download the small pinned model
explicitly:

```sh
linux/.venv/bin/compositor-install-model
```

For a system package, build the local source archive using
`scripts/arch-package.sh`. The [Arch recipe](packaging/arch/PKGBUILD) lists its
dependencies. x86_64 package creation has been exercised with development
dependencies; clean system installation and aarch64 are not yet qualified.
There is no AUR package maintained by this fork yet.

[Full installation, removal, shortcuts, and development instructions](linux/README.md).

## What you can make

- **Layers:** folders, nesting, blend modes, opacity, raster and folder masks,
  clipping masks, duplicate/reorder/merge, and multiple project tabs.
- **Text:** press **T**, click the canvas, and choose text, font, size, styles,
  alignment, and color. Double-click the text layer or use **Edit Layer Content**
  to edit it again. Previews commit as one undo step.
- **Color layers:** the layer **+** button or **Ctrl+Shift+N** offers transparent
  or solid-color fill. Double-click a solid layer to change its color.
- **Transform and select:** move, scale, rotate, flip, distort, crop, snapping,
  marquee, lasso, wand, and selection refinement.
- **Paint and retouch:** brush, eraser, healing, clone, blur, liquify/smudge,
  gradients, shapes, and eyedropper. Tools use scalable monochrome SVG icons.
- **Adjust and export:** six editable adjustment types, filters, optional local
  background removal, JPEG/PNG/HEIC/TIFF import, PNG/JPEG export, and clipboard.

| Editable text | Solid-color layers |
| --- | --- |
| ![Text controls with font, styles, color and preview](docs/screenshots/text-layer.png) | ![New layer dialog with solid color fill](docs/screenshots/solid-layer.png) |

## Projects and compatibility

`.comp` projects are directories containing a manifest and embedded PNG assets.
Transfer the whole directory. The Linux editor reads versions 1–7 and writes
version 7; imported photo paths are not needed to reopen a saved project.

Text is a Linux extension stored with a PNG fallback. Other readers can display
the baked pixels; saving in the original Mac app can drop text editability.
Pixel edits rasterize text, and Undo restores it. Reopening a project preserves
its saved appearance even when the original font is unavailable; editing text
requires that font or uses Qt's fallback. Real Mac round-trip validation remains
pending. Details are in the [project format reference](docs/project-format.md).

## Local processing and security

Image processing stays on the machine. There is no cloud upload, telemetry,
account requirement, or automatic model download. The optional model installer
checks a pinned size and SHA-256. Source installation accesses package servers;
the optional model download accesses GitHub.

The project reader validates paths, types, dimensions, hierarchy, and asset
budgets. Saves stage and atomically replace a project; text is rendered as plain
text. These protections and their tests are described in
[SECURITY.md](SECURITY.md). This is an early release, not an independent security
certification. Keep dependencies updated and back up original projects.

## Build and verify

```sh
uv venv linux/.venv
uv pip install --python linux/.venv/bin/python -e '.[test,background]' build
linux/.venv/bin/ruff check linux scripts/linux-desktop.py scripts/linux-compare.py setup.py
linux/.venv/bin/ruff format --check linux scripts/linux-desktop.py scripts/linux-compare.py setup.py
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME= linux/.venv/bin/python -m pytest -q
linux/.venv/bin/python -m build
desktop-file-validate packaging/linux/compositor.desktop
```

Linux CI tests Python 3.12 and 3.14 and builds wheel/source distributions.
[Current validation and outstanding work](docs/STATUS.md) includes native Wayland
checks. macOS development remains in `Compositor.xcodeproj`; consult the
[original project](https://github.com/robbietilton/Compositor) for Mac instructions.

## Credit and licensing

Compositor Linux and the original Compositor sources are **MIT licensed**.
The original `Copyright (c) 2026 Wonder Assembly LLC` notice and full permission
notice are retained in [LICENSE](LICENSE), including in wheel, source, and Arch
packages. Preserve that notice when redistributing the source or substantial
portions of it. The upstream artwork and C kernels retain their attribution.

Dependencies keep their own licenses. Qt/PySide6 are dynamically loaded and
installed separately; they are not relicensed under MIT. Model weights are
downloaded separately. See [third-party notices](THIRD_PARTY_NOTICES.md) for
sources and licensing references. All screenshots show a synthetic project
created for this Linux build.
