# Compositor Linux status

Linux build: **0.1.1**, early release. The base port is merged in
[PR #1](https://github.com/dlpwaters/compositor/pull/1). This milestone adds a
fresh-machine source installer, a detailed user guide, a native workspace 5
screen recording, refreshed screenshots, and an editable synthetic poster.
The [0.1.1 preview](https://github.com/dlpwaters/compositor/releases/tag/linux-v0.1.1)
is the intended installation target for this guide.
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

- **137 tests** pass. Coverage includes strict project validation, atomic saves,
  rendering, masks/clipping, geometry, adjustments, painting, undo/redo, pending
  edits, text create/edit/round-trip/rasterization, transformed text anchors,
  cancellation, closing during a preview, solid fills and double-click recoloring,
  read-only installer preflight, contradictory options, path quoting, reinstall,
  removal, unrelated launcher preservation and symlink preservation.
  Ruff, formatting, shell syntax and Desktop Entry validation pass.
- Native Wayland checks exercise the actual text and New Layer dialogs, canvas
  Text tool events, preview/commit, text editing/undo, cancellation, export, and
  save/reopen with identical rendered pixels. Published screenshots show only
  the synthetic sample project in the application's Fusion theme. The 0.1.1
  finite native demo passes all five checks, including actual text typing,
  editing/undo, solid-layer recoloring/undo, project reopening and PNG export.
  The 37-second H.264 video and animated preview were recorded on workspace 5
  and visually reviewed; no personal content or audio is included.
- The installed release launcher passes all 14 broad native Wayland smoke checks:
  brush strokes, gradient/transform/crop Apply/Cancel,
  Hue/Saturation targeting, original-pixel Levels sampling, distortion, and
  clipboard Copy Merged/Paste with an independent `wl-paste` reader. The later
  broad smoke rerun after the icon update stopped at its window-focus prerequisite;
  the final release run passes that prerequisite and all checks. These tests use
  Qt event delivery and do not establish physical keyboard delivery.
- Wheel and source builds succeed. An independently installed wheel outside the
  checkout renders all 28 SVGs and edits/renders saved text layers. The original
  MIT notice is present in the wheel and distributions.
- The 0.1.1 Arch package creation with development dependencies passes 137 tests and
  includes both commands, all 28 SVGs, the text renderer, license, notices and
  screenshots, footage and the editable sample. Its entrypoint uses `/usr/bin/python`; Qt SVG and Wayland are
  explicit dependencies. Source archives exclude prior Arch build directories.
  Build dependency resolution was skipped with
  `makepkg --nodeps`; system installation was not performed.
- The 0.1.1 per-user installer passes a fresh **official Arch x86_64 container**
  check with system dependencies resolved by pacman, a new ordinary user and no
  existing Python environment/cache. Installation, reinstall, the actual command,
  SVG/text rendering, native kernels, save/reopen/export, desktop validation and
  launcher removal all pass. The model is not downloaded implicitly. This is
  headless qualification, separate from the native Wayland demo on this host.
  The optional graphical polkit/pacman authorization was not exercised in the
  container. A clean system **package** installation and aarch64 remain pending.
- The updated installer and desktop entry work on the Omarchy host and preserve
  unrelated launcher files. Actual local U2NETP inference and its pinned checksum were
  verified earlier; model weights are excluded from the repository/packages.
- `pip-audit` found no known advisories in 21 installed dependencies (the editable
  application itself is excluded from the registry lookup). This covers known
  dependency advisories, not an independent application security audit.

[Linux CI](https://github.com/dlpwaters/compositor/actions/workflows/linux.yml)
runs Python 3.12/3.14 tests, lint/format, Desktop Entry validation and isolated
wheel/source builds. A new pinned Arch container job exercises the per-user
installer and explicitly verifies root-install refusal. Consult the workflow
for the result at a specific commit.
The [merged-main run](https://github.com/dlpwaters/compositor/actions/runs/36745781801)
passes both jobs at `a95b24d`. A fresh public clone at that commit also builds
the kernels and passes all 129 tests. Uploaded release digests match local artifacts.

This is **not a certified full 1:1 replacement**. The
[parity audit](linux-parity.md) separates implemented behavior from qualification.
Mac-generated reference projects and cross-platform interaction tests remain
necessary. CPU/Core Image sampling and blur, Qt appearance, Apple Vision
substitution, selected-pixel transform controls, large-photo performance,
additional inter-app drops, aarch64 and clean system package installation need further
qualification. Linux text uses a saved PNG fallback; Mac resaving can discard
editability.

Next engineering step: collect the Mac reference corpus, compare with
`scripts/linux-compare.py`, and qualify the remaining interactions and package
installation on a clean Arch system.
