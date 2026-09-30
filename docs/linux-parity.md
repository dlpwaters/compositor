# Linux parity audit

Baseline: Compositor 1.0.4, upstream commit `a19db9011282399785dc18efcfded904627bdcc2`.
The original Swift app and its C kernels remain intact. The Linux implementation
uses Qt Widgets/PySide6 for the native interface, Pillow/NumPy/SciPy for raster
rendering, the original C routines for portable pixel operations, and an optional
local ONNX segmentation model.

**This does not yet establish a full 1:1 port.** The feature implementation is
substantial and usable, but a macOS reference corpus, exhaustive interaction
comparison, and performance qualification are still required. Source-derived
tests can prove Linux behavior without proving byte-equivalent Mac output.

## Feature coverage

| Upstream feature | Linux implementation | Verification / remaining difference |
| --- | --- | --- |
| Blank/imported layers and project tabs | Native layer tree and tabs; separate documents and viewports | Document/tab tests and live Wayland editor |
| Folders, nesting, reorder, visibility, rename, duplicates | Pass-through hierarchy, native drag/drop and menus | Hierarchy, group duplicate, undo and clipping-reorder tests; exhaustive drag/drop UX comparison pending |
| Opacity and 13 blend modes | Premultiplied compositing, separable/nonseparable blend formulas | Soft-alpha tests for every mode; Core Graphics golden comparison pending |
| Raster, folder, clipping masks | Uniform/placed masks, linked/unlinked affine movement, contiguous clipping stacks | Mask placement, hidden source, folder mask and shared-alpha tests |
| Merge layer/down/group | Rasterizes selected subtree against transparency and retains parent | Masked-folder merge regression; Mac fixtures pending |
| Move, scale, rotation, flips, multiple layers/folders | Source-preserving transforms, inspector, eight handles, ratio lock, auto-select, pending Apply/Cancel | Combined bounds, linked-mask and selection-transform tests; selected-pixel transform uses a numeric dialog rather than floating canvas controls |
| Distort | Perspective raster transform through Ctrl-drag corner/edge handles; pending corners and Apply/Cancel | Convexity validation, source-preserving repeated previews, group mapping, flips and linked/unlinked mask tests; live Wayland preview/Apply/undo |
| Snapping / guides | Canvas and sibling edge/center snapping; visible move guides; crop edge snapping | Implemented; golden geometry/interaction comparison pending |
| Marquee, ellipse, freehand/polygon lasso, wand | Raster selections with add/subtract, move, feather, expand/contract | Wand kernel, selected-pixel move/duplicate, empty-selection clipping and Qt canvas tests; rasterized outline differs from Mac vector path |
| Brush / eraser | Size, hardness, stroke opacity cap, Shift-line, canvas clipping, source expansion | Native Qt pointer stroke, mask painting, coverage and expansion tests |
| Healing | Original heal kernel, content-aware / proximity / create texture | Synthetic native-kernel tests; photo corpus pending |
| Clone Stamp | Aligned/nonaligned sampling of active or all layers, Alt source picking | Implemented; real editing session and cross-platform sample comparison pending |
| Blur / Liquify / Smudge | Local blur; document-space forward warp and carried-color smudge translated from Swift | Source-translated warp and undo tests; sampling/brush-boundary golden comparisons pending; no Metal path |
| Gradient and shapes | Linear/radial gradient, direction/reverse/colors, pending preview/Apply/Cancel, live rectangle/ellipse shape metadata | Gradient options regenerate from the original raster; tool switches resolve it; cancellation and one-step undo tests plus live Wayland checks |
| Eyedropper and color picker | Layer/composite pixel sampling, native QColorDialog, foreground/background | Native workflow; Mac color-picker UI is replaced |
| Hue/Saturation | Seven editable ranges, four-handle spectrum, Sample/Add/Remove, targeted saturation/hue dragging, invert/colorize/reset and 33³ cube | Source-translated circular bands, HSL/cube and wire-format tests; native Wayland targeting/cancellation; exhaustive Mac gesture comparison pending |
| Levels / Curves | RGB and per-channel controls, original-input histogram/handles, three Auto modes, black/gray/white sampling, editable curve graph | Original Levels C kernel, source-translated Auto/calibration, Hermite curves, selection/undo/serialization tests; original-pixel sampler checked live |
| Exposure / Gradient Map / Grain / Invert | Destructive or live adjustment layers; alpha/mask/opacity integration | Original portable kernels where applicable; color output still needs Core Image reference comparisons |
| Gaussian / Motion Blur | Premultiplied CPU filters with expanded source grid | Expansion/mask regression; Core Image blur kernels are approximated |
| Noise / Lens Correction | Original C implementations | Native-kernel tests |
| Content-Aware Fill and outpainting | Original C fill with selection and source-grid expansion | Synthetic fill tests; quality on real photographs not qualified |
| Remove Background | Local U2NETP + optional guided matte refinement | Actual pinned-model inference succeeded; output differs from Apple Vision |
| Canvas/Image Size, crop, flips | Metadata/source resampling, anchored canvas changes, crop frame/handles/ratios and Apply/Cancel | Geometry, pending-edit tests, real Wayland crop Apply/undo; detailed Mac interaction comparison pending |
| JPEG/PNG/HEIC/TIFF import, drops | Pillow, libheif binding, EXIF orientation, ICC conversion to sRGB | Synthetic import checks cover all four formats; broader inter-app drops remain pending |
| PNG export / JPEG preview / Copy Merged | sRGB+DPI PNG; actual encoded JPEG preview with quality and matte; native clipboard | Export and matte/DPI tests; real Wayland Copy Merged/Paste compared pixel-for-pixel and prior clipboard restored |
| `.comp` v1–7 | Strict validated read/write; preserves optional metadata and immutable embedded assets | Source-derived version fixtures, save/reopen and atomic replacement tests; real Mac round-trip corpus pending |
| Undo/redo | Shared immutable assets, revision-based dirty state, bounded snapshots | Rollback/undo/redo tests and live menu exercise |
| macOS system integration and updates | Linux desktop entry, per-user installer, Arch package recipe | Native Wayland launcher/window observed; fresh Arch user install/reinstall/removal and headless renderer verified in a container; clean system package install and AUR publication pending |

## Platform substitutions

Editable text and the New Layer fill chooser are Linux additions, not features
in the baseline Swift text schema. Text uses optional `linuxText` metadata and
a baked PNG fallback; solid layers use the existing Rectangle shape schema.
Text creation/editing, transformed placement, rasterization/undo, cancellation,
and save/reopen have regression tests and native Wayland checks. Mac resaving
can discard text editability; actual Mac round-trip testing remains pending.

- SwiftUI/AppKit/SF Symbols become native Qt controls. Layout, dialogs, icons for
  tools, and focus behavior are therefore not pixel-identical to macOS.
- Core Graphics and Core Image become a CPU renderer. Downsampling, antialiasing,
  filter edges and color rounding can differ. Large layered photos need profiling
  before claiming Mac-like performance. Brush edits retain immutable source assets;
  flattened operations allocate document-sized rasters.
- Metal brush/render pipelines are not ported. Heavy committed filters, JPEG
  encoding and subject inference run in finite workers while Qt processes events.
  Some previews, brush paths, project I/O and view compositing remain synchronous.
- Apple Vision is unavailable on Linux. The optional model comes from the
  [rembg model release](https://github.com/danielgatis/rembg/releases/tag/v0.0.0),
  using [U²-Net](https://github.com/xuebinqin/U-2-Net) (Apache-2.0 upstream code).
  The pinned model is 4,574,861 bytes, SHA-256
  `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8`.
  Weights are fetched explicitly, verified, stored outside the repo, and inferred
  locally. This is a different segmentation model, not a reproduction of Vision.
- Sparkle/DMG/notarization remain specific to the Mac build. Linux updates use the
  package/environment workflow; no updater daemon or remote image-processing
  service is installed.

## Certifying exact parity

On a Mac, open representative projects in the baseline app and save both the
`.comp` package and flattened PNG output. Include transparent edges, all blend
modes, rotated/flipped pixels, clipped adjustments, nested folder masks, displaced
linked/unlinked masks, editable shapes, color adjustments and blur boundaries.
Also save operation-specific before/after examples for selection/painting tools.

Transfer whole packages and their PNGs to Linux, then run:

```sh
linux/.venv/bin/python scripts/linux-compare.py sample.comp sample-mac.png --render /tmp/sample-linux.png
```

The comparator reports maximum/mean channel error and differing pixel count,
returns a failure for differences above the requested `--tolerance` (default 0),
and can save the Linux render. Open a Linux-resaved copy back on the Mac and
confirm both metadata and rendering. An identical Linux round-trip only validates
the Linux reader/writer pair.

Finish the missing interaction details and collect CPU/latency/memory measurements
on real editing documents before treating the Linux port as a certified 1:1
replacement. The current verification state lives in [STATUS.md](STATUS.md).
