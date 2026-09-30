# Compositor Linux status

Linux build: **0.1.0**, early release. Implementation is tracked in
[PR #1](https://github.com/dlpwaters/compositor/pull/1).
Baseline: Compositor 1.0.4 (`a19db9011282399785dc18efcfded904627bdcc2`).
The original Swift sources and eight C kernels remain unchanged.

The native Qt/Wayland editor implements layered compositing, hierarchy, masks
and clipping, transforms and distortion, selections, painting and retouching,
six editable adjustments, filters, local background removal, tabs, import/export,
and clipboard. The tool rail and common actions use **28 original monochrome
SVGs**. Linux adds editable text and a New Layer transparent/solid fill chooser.
The app and desktop launcher are named **Compositor Linux**; `compositor-linux`
is the primary command and `compositor` remains an alias.

Verified on Arch/Omarchy x86_64 with Python 3.14:

- **129 tests** pass. Coverage includes strict project validation, atomic saves,
  rendering, masks/clipping, geometry, adjustments, painting, undo/redo, pending
  edits, text create/edit/round-trip/rasterization, transformed text anchors,
  cancellation, closing during a preview, solid fills and double-click recoloring.
  Ruff, formatting, shell syntax and Desktop Entry validation pass.
- Native Wayland checks exercise the actual text and New Layer dialogs, canvas
  Text tool events, preview/commit, text editing/undo, cancellation, export, and
  save/reopen with identical rendered pixels. Published screenshots show only
  the synthetic sample project in the application's Fusion theme.
- Earlier native checks cover brush strokes, gradient/transform/crop Apply/Cancel,
  Hue/Saturation targeting, original-pixel Levels sampling, distortion, and
  clipboard Copy Merged/Paste with an independent `wl-paste` reader. The later
  broad smoke rerun stopped at its window-focus prerequisite. The current text
  checks do not establish physical keyboard delivery or replace that broad run.
- Wheel and source builds succeed. An independently installed wheel outside the
  checkout renders all 28 SVGs and edits/renders saved text layers. The original
  MIT notice is present in the wheel and distributions.
- Arch package creation with development dependencies passes 129 tests and
  includes both commands, all 28 SVGs, the text renderer, license, notices and
  screenshots. Its entrypoint uses `/usr/bin/python`; Qt SVG and Wayland are
  explicit dependencies. Build dependency resolution was skipped with
  `makepkg --nodeps`; system installation was not performed.
- The updated per-user installer and desktop entry work and preserve unrelated
  launcher files. Actual local U2NETP inference and its pinned checksum were
  verified earlier; model weights are excluded from the repository/packages.
- `pip-audit` found no known advisories in 21 installed dependencies (the editable
  application itself is excluded from the registry lookup). This covers known
  dependency advisories, not an independent application security audit.

[Linux CI](https://github.com/dlpwaters/compositor/actions/workflows/linux.yml)
runs Python 3.12/3.14 tests, lint/format, Desktop Entry validation and isolated
wheel/source builds. Consult the workflow for the result at a specific commit.

This is **not a certified full 1:1 replacement**. The
[parity audit](linux-parity.md) separates implemented behavior from qualification.
Mac-generated reference projects and cross-platform interaction tests remain
necessary. CPU/Core Image sampling and blur, Qt appearance, Apple Vision
substitution, selected-pixel transform controls, large-photo performance,
additional inter-app drops, aarch64 and clean system installation need further
qualification. Linux text uses a saved PNG fallback; Mac resaving can discard
editability.

Next engineering step: collect the Mac reference corpus, compare with
`scripts/linux-compare.py`, and qualify the remaining interactions and package
installation on a clean Arch system.
