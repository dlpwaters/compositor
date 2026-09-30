# Linux port status

Branch: `port/arch-linux`. Baseline: Compositor 1.0.4 (`a19db901`).

The native Qt/Wayland editor is implemented under `linux/`. It reuses the original
eight C pixel kernels and reads `.comp` versions 1–7, saving version 7. The original
Mac application is preserved. Linux setup, shortcuts, per-user installation,
model installation and Arch packaging are in [linux/README.md](../linux/README.md).

Verified locally on Arch/Omarchy x86_64 with Python 3.14:

- 106 unit and native Qt event tests pass, covering project validation/round trips,
  masks/clipping, blend alpha, adjustments, geometry, painting, warp, undo/redo,
  cancellation, finite workers, JPEG preview encoding, empty selections, original
  Levels sampling/Auto, hue-band editing, persistent transforms, crop and gradient.
- The tool rail and common action buttons use 27 original monochrome SVG icons.
  All render at normal and high DPI sizes and are present in both wheel and source
  archive. Wayland screenshots and AT-SPI Brush selection verify the palette;
  Qt key events verify the Erase glyph, and layer/mask/folder buttons work. The
  tab-close regression verifies the correct project closes after tab indices change.
- The pre-icon-update Wayland smoke exercise paints through Qt mouse events,
  invokes menu undo/redo and project save, reopens the package, compares rendering, exports
  PNG/JPEG, captures the editor and exits. Copy Merged/Paste matched the composite
  pixel-for-pixel through the actual Wayland clipboard, including an independent
  `wl-paste` reader; prior contents were restored.
- Pending gradient cancellation, persistent transform Apply/undo, crop handles
  and Apply/undo, Hue/Saturation targeted dragging, Levels original-pixel sampling,
  and distortion preview/Apply/undo pass in the real Wayland editor. The finite
  check allows Fcitx's asynchronous key forwarding through the session bus.
- The per-user desktop entry validates and the installed launcher opens a native
  `compositor` Hyprland window. Host AT-SPI observation and semantic tool selection
  verify the Brush option panel; the native screenshot was inspected.
- The full Wayland smoke rerun after the icon update stopped at its window-focus
  prerequisite. Current native screenshots and AT-SPI tool selection passed;
  mode switching and layer action checks passed through Qt's event path. The
  Wayland dropdown driver did not commit an Erase selection, so its row-selection
  acknowledgement was not treated as verification.
- Actual local U2NETP inference returns a nonuniform full-size mask. The default
  model's size and SHA-256 were verified. Model weights are outside the repository.
- Ruff, shell syntax and Desktop Entry validation pass. A local Arch package
  rebuild includes all 27 SVGs, passes all 106 tests and contains the expected
  `/usr` paths and interpreter.
  Build dependencies were supplied by the development environment with makepkg
  dependency resolution skipped; system package installation was not performed.
  The package explicitly depends on `qt6-svg` for the bundled icon renderer;
  Arch lists that support as optional for PySide6.
- A built wheel installed into a separate dependency environment outside the
  checkout loads its packaged module, native kernel library and icon, and reads
  and renders a saved project. Installation used the local wheel and cached
  dependencies; no editable source mapping was used in that environment.
- One synthetic four-layer 1600×1000 render took 0.282 seconds and peaked at
  320.5 MiB process RSS. This is a basic CPU/memory sanity check, not a photo benchmark.

Initial implementation commit: `3e5a488`. A clean archive of the committed tracked
files also builds a wheel. The icon update is `a9dad31`; its wheel also renders
every SVG from a separate installed environment. The branch is pushed to the
public fork, with [draft PR #1](https://github.com/dlpwaters/compositor/pull/1).
GitHub's [push workflow](https://github.com/dlpwaters/compositor/actions/runs/36717461320)
and [PR workflow](https://github.com/dlpwaters/compositor/actions/runs/36717586703)
both pass on Python 3.12 and 3.14 at `a9dad31`: 106 tests, lint/format checks,
Desktop Entry validation and wheel/source builds. Subsequent documentation and
package dependency updates do not change the tested runtime. Nothing has been
merged or released.

This is not a certified full 1:1 replacement. [The parity audit](linux-parity.md)
lists implementation and verification separately. Mac-generated reference files
and cross-platform interaction tests are the next acceptance step. Remaining
platform differences include CPU/Core Image sampling and blur, Qt appearance,
Apple Vision substitution, and some interaction details. Selected-pixel transforms
use a numeric Apply/Cancel dialog rather than the Mac's floating canvas controls.
Large-photo performance, additional inter-app drop behavior, aarch64, and clean
system package installation still need qualification.

Next: collect the Mac reference corpus and complete the remaining interactions.
The PR remains a draft until the remaining parity qualification is complete.
For exact output comparisons, use `scripts/linux-compare.py` on original Mac
`.comp` projects and PNG exports.
