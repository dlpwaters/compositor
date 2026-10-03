# Upstream 1.4.5 Linux port map

Source comparison: `a19db9011282399785dc18efcfded904627bdcc2..upstream/main` (207 changed paths). Original Swift and C files remain the reference. This file maps every changed path below to its primary Linux feature family. Cross-cutting sources can inform more than one family. The Mac test paths are reference tests; their Linux counterparts are named by family.

Linux behavior is in the Python/Qt code, shared C kernels, packaging metadata, and Linux tests. The current qualification and accepted rendering limits are recorded in [STATUS.md](STATUS.md) and [linux-parity.md](linux-parity.md). Detailed local test receipts remain in the ignored `.runtime/upstream-port/` directory.

## Feature families

| ID | Upstream behavior | Linux application and test mapping |
| --- | --- | --- |
| F0 | Platform, CI, and shared documentation | Mac packaging, AppKit assets, Xcode, Info.plist, and Sparkle appcast have no Linux runtime counterpart. The Linux wheel/desktop flow is retained. Parent owns shared CI and README changes. Tests: Parent review; Linux packaging checks. |
| F1 | Project format and persistence | `model.py`, `store.py`, `text.py`: v1–11 manifests, optional fields, strict IDs/dimensions/version gates, UTF-16 rich text, effects/guides metadata, bounded asset reads, symlink checks, atomic save on Linux. Tests: `test_core.py`, `test_port_formats.py`, `test_import_port.py`. |
| F2 | Compositing and blend modes | `engine.py`: 24 Photoshop blend modes, alpha, clipping, adjustment order, pass-through folder opacity. Tests: `test_core.py`, `test_port_formats.py`. |
| F3 | Adjustments and shared kernels | `engine.py`, `kernels.py`: 12 adjustments, stable noise origins, shared C ABI for black and white, color balance, levels and color range; original imported C compiled by `setup.py`. Tests: `test_port_formats.py`, `test_kernel_portability.py`, `test_core.py`. |
| F4 | Layer effects | `effects.py`, `engine.py`, `dialogs.py`, `app.py`: six editable effects, visibility, local transform/mask scaling, reversible preview and undo. Tests: `test_port_formats.py`, `test_ui.py`. |
| F5 | Filters, dither, Camera Raw | `camera_raw.py`, `dither.py`, shared `Compositor/Rendering/DitherPixels.c`, `engine.py`, `dialogs.py`: Camera Raw setting families, finishing filters and all source dither modes. Original C math is used where portable; Core Image and Metal visuals use bounded CPU/Qt equivalents. Tests: `test_camera_raw_port.py`, `test_finishing_port.py`, `test_kernel_portability.py`, `test_ui.py`. |
| F6 | PSD, SVG, and camera RAW import | `psd.py`, `psd_text.py`, `svg.py`, `raw.py`, `app.py`: bounded 8-bit RGB PSD/PSB raw/PackBits layers and masks, first-style editable TySh, raster vector/fill fallbacks, conversion review, secure SVG, optional local `rawpy` decode. Tests: `test_psd_port.py`, `test_import_port.py`, `test_raw_port.py`, `test_ui.py`. |
| F7 | Canvas geometry, guides, and trim | `canvas.py`, `editing.py`, `app.py`, `dialogs.py`: rulers, persistent guides, grid and snap targets, transform/crop ratios, symmetry, shape, image trim and canvas/image resizing. Tests: `test_port_formats.py`, `test_ui.py`, `test_core.py`. |
| F8 | Layers, folders, and tabs | `model.py`, `editing.py`, `app.py`: folder opacity/ungroup, hierarchy-safe duplicate/copy/paste with fresh IDs and clipping bases, layer context menu, tab reordering/overflow. Tests: `test_port_formats.py`, `test_ui.py`, `test_core.py`. |
| F9 | Editable text | `text.py`, `model.py`, `dialogs.py`, `app.py`: Qt rich text runs, tracking/leading/wrap, modal and on-canvas editor (Shift-click or Layer menu), resize reflow, typing undo and commit/cancel. Tests: `test_ui.py`, `test_psd_port.py`, `test_port_formats.py`. |
| F10 | Selection, brushes, masks, and warp | `kernels.py`, `editing.py`, `canvas.py`, `warp.py`, `app.py`: color range, local U2Net object option, feather/smear/blur radius/liquify, mask painting across canvas, mask-only view and selection edits. Tests: `test_selection_port.py`, `test_brush_port.py`, `test_ui.py`, `test_core.py`. |
| F11 | Workspace and application controls | `app.py`, `model.py`, `tasks.py`, `dialogs.py`: recent projects, saved tool options, shortcuts, numeric scrubbing, presets, external digest polling/conflicts, immutable async save snapshots and dialog close handling. Tests: `test_ui.py`, `test_core.py`. |
| F12 | Budgets and responsiveness | `model.py`, `store.py`, `psd.py`, `svg.py`, `raw.py`, `tasks.py`, `editing.py`: finite side/pixel/input bounds; trim scans one-million-pixel strips with cancellation; long C operations run in a busy worker. Tests: `test_import_port.py`, `test_psd_port.py`, `test_port_formats.py`, `test_ui.py`. |

## Qualification and intentional differences

- Photoshop import accepts bounded 8-bit RGB PSD/PSB raw and PackBits data. Unsupported Photoshop adjustments without raster pixels, malformed text without a raster fallback, unsupported compression, and unsupported image modes fail explicitly. TySh keeps its first representable editable style. Vector/fill shapes with raster pixels remain raster and are named in the conversion review. Photoshop ZIP channels and full vector editing are not qualified.
- Camera RAW uses an optional, locally installed `rawpy`/LibRaw decoder; it never downloads a codec or model. A real camera sample was decoded in the Linux qualification environment; this does not establish every camera format or Apple's RAW appearance. Camera Raw filters use the imported C adjustment kernels where available; Qt/Pillow geometry and smooth curves may differ visually from Core Image. Bloom and Motion Blur are CPU approximations. Dither uses the shared upstream pixel math with serial C rows on Linux and retains Apple's dispatch path on macOS.
- Object selection is labeled as a local U2Net option. The existing explicit model installer remains opt-in; no Apple Vision or implicit network request is claimed.
- The inline Qt text editor and raster font shaping retain editability, styled runs and paragraph bounds. Font metrics, baseline, rotated editor overlay and glyph appearance can differ from AppKit. Photoshop text retains its original raster preview until re-edited.
- Document sides are limited to 30,000 pixels; raster assets and rendered regions to 100 million pixels. SVGs additionally cap input at 4 MiB and 16 million pixels; PSD/RAW input at 1 GiB. Trim scans at most roughly one million pixels per strip. Native C filters run in a worker and are not interruptible midway through a kernel call; trim can cancel between strips. Large end-to-end workflows still require Linux VM qualification.
- AppKit panels, Finder integration, Apple Metal/GPU performance, Core Image RAW appearance, Sparkle, appcast, Xcode schemes, DMG and notarization are macOS-only. The Linux installer and desktop launcher remain the Linux distribution path.

## Changed path inventory

Every row is one changed upstream path. `F0` is an explicit platform/shared-source exclusion; F1–F12 refer to the behavior and Linux tests in the table above. Status is implementation mapping, not an assertion that the Mac reference test ran in this checkout.

| Upstream changed path | Family | Disposition |
| --- | --- | --- |
| `.github/workflows/verify.yml` | F0 | Parent-owned shared instructions, README, or CI; no Linux implementation edit |
| `AGENTS.md` | F0 | Parent-owned shared instructions, README, or CI; no Linux implementation edit |
| `Compositor.xcodeproj/project.pbxproj` | F0 | Xcode-only project/scheme; no Linux runtime target |
| `Compositor.xcodeproj/xcshareddata/xcschemes/Compositor.xcscheme` | F0 | Xcode-only project/scheme; no Linux runtime target |
| `Compositor/Assets.xcassets/AppIcon.appiconset/app-icon-1024.png` | F0 | Mac app icon; Linux uses its existing launcher artwork |
| `Compositor/Assets.xcassets/AppIcon.appiconset/app-icon-128.png` | F0 | Mac app icon; Linux uses its existing launcher artwork |
| `Compositor/Assets.xcassets/AppIcon.appiconset/app-icon-16.png` | F0 | Mac app icon; Linux uses its existing launcher artwork |
| `Compositor/Assets.xcassets/AppIcon.appiconset/app-icon-256.png` | F0 | Mac app icon; Linux uses its existing launcher artwork |
| `Compositor/Assets.xcassets/AppIcon.appiconset/app-icon-32.png` | F0 | Mac app icon; Linux uses its existing launcher artwork |
| `Compositor/Assets.xcassets/AppIcon.appiconset/app-icon-512.png` | F0 | Mac app icon; Linux uses its existing launcher artwork |
| `Compositor/Assets.xcassets/AppIcon.appiconset/app-icon-64.png` | F0 | Mac app icon; Linux uses its existing launcher artwork |
| `Compositor/Compositor-Bridging-Header.h` | F3 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/CompositorApp.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/ContentView.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/Document/AdjustmentEditing.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/Document/BlurTool.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/BrushStroke.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/CameraRaw.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/Document/CameraRawColor.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/Document/CameraRawDetailOptics.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/Document/CameraRawGeometryCalibration.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/Document/CanvasSize.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Document/CloneStamp.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/ColorPalette.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/ColorRangeSelection.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/Crop.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Document/Distort.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/Dither.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/Document/DocumentHistory.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/Document/DocumentLimits.swift` | F12 | Linux Qt/CPU behavior and tests mapped in F12 |
| `Compositor/Document/EditorSession+Brush.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/EditorSession+Projects.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/Document/EditorSession.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/Document/Filters.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/Document/FloatingSelection.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/Gradient.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/Guides.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Document/HueSaturation.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/Document/ImageAdjustments.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/Document/ImageTrim.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Document/LayerAdjustment.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/Document/LayerAppearance.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/Document/LayerEffects.swift` | F4 | Linux Qt/CPU behavior and tests mapped in F4 |
| `Compositor/Document/LayerFlip.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/Document/LayerGroups.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/Document/LayerMask.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/LayerTransform.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Document/Levels.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/Document/LiveLayerMask.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/MagicWand.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/MaskTracing.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/ObjectSelection.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/ProjectWorkspace.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/Document/Selection.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/SelectionClipboard.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/SelectionEdits.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/ShapeTool.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Document/SmudgeLiquify.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/SubjectRemoval.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Document/ToolDefaults.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/Document/TypeTool.swift` | F9 | Linux Qt/CPU behavior and tests mapped in F9 |
| `Compositor/IO/CanvasResizer.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/IO/CompositorApplicationDelegate.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/IO/ImageExporter.swift` | F12 | Linux Qt/CPU behavior and tests mapped in F12 |
| `Compositor/IO/ImageFileDrop.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/ImageImporter.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/ImageResizer.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/IO/PSD/PSDChannelCoder.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/PSD/PSDDocumentBuilder.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/PSD/PSDReader.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/PSD/PSDText.swift` | F9 | Linux Qt/CPU behavior and tests mapped in F9 |
| `Compositor/IO/PSD/PSDTypes.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/PSD/PSDVector.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/ProjectController+ExternalChanges.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/IO/ProjectController.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/IO/ProjectDigest.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/IO/ProjectStore.swift` | F1 | Linux Qt/CPU behavior and tests mapped in F1 |
| `Compositor/IO/ProjectWatcher.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/IO/RawImporter.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/IO/RecentProjects.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/Rendering/AdjustPixels.c` | F3 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/AdjustPixels.h` | F3 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/AdjustmentSurface.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/Rendering/CanvasLinesOverlay.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Rendering/CanvasViewport.swift` | F12 | Linux Qt/CPU behavior and tests mapped in F12 |
| `Compositor/Rendering/DitherPixels.c` | F5 | Shared source compiled directly; guarded serial fallback on non-Apple platforms |
| `Compositor/Rendering/DitherPixels.h` | F5 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/DownsampleCache.swift` | F12 | Linux Qt/CPU behavior and tests mapped in F12 |
| `Compositor/Rendering/EditorCanvas.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Rendering/EffectsPreviewCache.swift` | F4 | Linux Qt/CPU behavior and tests mapped in F4 |
| `Compositor/Rendering/GPUCanvas.swift` | F12 | Apple GPU path excluded; CPU/Qt behavior and tests in F12 |
| `Compositor/Rendering/GPUNoise.swift` | F5 | Apple GPU path excluded; CPU/Qt behavior and tests in F5 |
| `Compositor/Rendering/InlineTextEditor.swift` | F9 | Linux Qt/CPU behavior and tests mapped in F9 |
| `Compositor/Rendering/LayerEffectsSurface.swift` | F4 | Linux Qt/CPU behavior and tests mapped in F4 |
| `Compositor/Rendering/LayerRenderer.swift` | F2 | Linux Qt/CPU behavior and tests mapped in F2 |
| `Compositor/Rendering/LevelsPixels.c` | F3 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/LevelsPixels.h` | F3 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/LiveMaskRenderer.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/Rendering/MetalLayerEffects.swift` | F4 | Apple GPU path excluded; CPU/Qt behavior and tests in F4 |
| `Compositor/Rendering/MetalWarp.swift` | F10 | Apple GPU path excluded; CPU/Qt behavior and tests in F10 |
| `Compositor/Rendering/NoisePixels.c` | F5 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/NoisePixels.h` | F5 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/RasterSnapshot.swift` | F12 | Linux Qt/CPU behavior and tests mapped in F12 |
| `Compositor/Rendering/SeparableBlend.swift` | F2 | Linux Qt/CPU behavior and tests mapped in F2 |
| `Compositor/Rendering/TiledLayerRenderer.swift` | F12 | Linux Qt/CPU behavior and tests mapped in F12 |
| `Compositor/Rendering/TransformOverlay.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/Rendering/WandPixels.c` | F10 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/Rendering/WandPixels.h` | F10 | Imported shared C kernel compiled by `setup.py` |
| `Compositor/UI/BlendModePicker.swift` | F2 | Linux Qt/CPU behavior and tests mapped in F2 |
| `Compositor/UI/BrushControls.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/UI/CameraRawColorControls.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/UI/CameraRawControls.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/UI/CameraRawDetailOpticsControls.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/UI/CameraRawGeometryCalibrationControls.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/UI/CameraRawSlider.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/UI/CanvasRulers.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/CanvasSizeSheet.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/CanvasThumbnail.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/UI/ColorPickerSheet.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/UI/ColorRangeSheet.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/UI/CropControls.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/EffectsSheet.swift` | F4 | Linux Qt/CPU behavior and tests mapped in F4 |
| `Compositor/UI/FilterSheet.swift` | F5 | Linux Qt/CPU behavior and tests mapped in F5 |
| `Compositor/UI/FloatingPanel.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/UI/GradientControls.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/UI/GridSettingsSheet.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/HeldModifiers.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/HueSaturationSheet.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/UI/ImageSizeSheet.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/IndicatorlessScrollView.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/UI/JPEGExportSheet.swift` | F12 | Linux Qt/CPU behavior and tests mapped in F12 |
| `Compositor/UI/KeyboardShortcuts.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/UI/LassoControls.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/UI/LayerAppearanceControls.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/UI/LayerMaskMenu.swift` | F10 | Linux Qt/CPU behavior and tests mapped in F10 |
| `Compositor/UI/LayersPanel.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/UI/LevelsSheet.swift` | F3 | Linux Qt/CPU behavior and tests mapped in F3 |
| `Compositor/UI/NativeLayerList.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/UI/NavigationToolHeader.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/NewCanvasSheet.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/UI/NumericScrub.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/UI/PSDConversionSheet.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/UI/ProjectTabLayout.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/UI/ProjectTabs.swift` | F8 | Linux Qt/CPU behavior and tests mapped in F8 |
| `Compositor/UI/RawDevelopSheet.swift` | F6 | Linux Qt/CPU behavior and tests mapped in F6 |
| `Compositor/UI/ShapeControls.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/ToolHeaderStyle.swift` | F11 | Linux Qt/CPU behavior and tests mapped in F11 |
| `Compositor/UI/TransformInspector.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/TrimSheet.swift` | F7 | Linux Qt/CPU behavior and tests mapped in F7 |
| `Compositor/UI/TypeControls.swift` | F9 | Linux Qt/CPU behavior and tests mapped in F9 |
| `CompositorTests/AdjustmentLayerTests.swift` | F3 | Mac reference regression; Linux tests mapped in F3 |
| `CompositorTests/BlendShortcutTests.swift` | F2 | Mac reference regression; Linux tests mapped in F2 |
| `CompositorTests/BlurBrushTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/BrushTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/CameraRawSliderTests.swift` | F5 | Mac reference regression; Linux tests mapped in F5 |
| `CompositorTests/CameraRawTests.swift` | F5 | Mac reference regression; Linux tests mapped in F5 |
| `CompositorTests/CanvasEntryTests.swift` | F7 | Mac reference regression; Linux tests mapped in F7 |
| `CompositorTests/CanvasThumbnailTests.swift` | F8 | Mac reference regression; Linux tests mapped in F8 |
| `CompositorTests/CompositorTests.swift` | F1 | Mac reference regression; Linux tests mapped in F1 |
| `CompositorTests/CropToCanvasImportTests.swift` | F6 | Mac reference regression; Linux tests mapped in F6 |
| `CompositorTests/CursorTests.swift` | F11 | Mac reference regression; Linux tests mapped in F11 |
| `CompositorTests/DistortTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/DitherTests.swift` | F5 | Mac reference regression; Linux tests mapped in F5 |
| `CompositorTests/ExternalChangeTests.swift` | F11 | Mac reference regression; Linux tests mapped in F11 |
| `CompositorTests/FilterTests.swift` | F5 | Mac reference regression; Linux tests mapped in F5 |
| `CompositorTests/FinishingFilterTests.swift` | F5 | Mac reference regression; Linux tests mapped in F5 |
| `CompositorTests/FloatingPanelTests.swift` | F11 | Mac reference regression; Linux tests mapped in F11 |
| `CompositorTests/GPUCanvasTests.swift` | F12 | Mac reference regression; Linux tests mapped in F12 |
| `CompositorTests/GroupTests.swift` | F8 | Mac reference regression; Linux tests mapped in F8 |
| `CompositorTests/GuideTests.swift` | F7 | Mac reference regression; Linux tests mapped in F7 |
| `CompositorTests/HueSaturationTests.swift` | F3 | Mac reference regression; Linux tests mapped in F3 |
| `CompositorTests/ImageAdjustmentTests.swift` | F3 | Mac reference regression; Linux tests mapped in F3 |
| `CompositorTests/ImageTrimTests.swift` | F7 | Mac reference regression; Linux tests mapped in F7 |
| `CompositorTests/InnerGlowTests.swift` | F4 | Mac reference regression; Linux tests mapped in F4 |
| `CompositorTests/LargeCanvasBrushTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/LayerAppearanceTests.swift` | F8 | Mac reference regression; Linux tests mapped in F8 |
| `CompositorTests/LayerMaskTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/LayerTests.swift` | F8 | Mac reference regression; Linux tests mapped in F8 |
| `CompositorTests/LevelsTests.swift` | F3 | Mac reference regression; Linux tests mapped in F3 |
| `CompositorTests/MaskAloneTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/MetalWarpTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/NativeResolutionPaintTests.swift` | F12 | Mac reference regression; Linux tests mapped in F12 |
| `CompositorTests/OuterGlowTests.swift` | F4 | Mac reference regression; Linux tests mapped in F4 |
| `CompositorTests/PSBImportTests.swift` | F6 | Mac reference regression; Linux tests mapped in F6 |
| `CompositorTests/PSDAdjustmentTests.swift` | F6 | Mac reference regression; Linux tests mapped in F6 |
| `CompositorTests/PSDFixture.swift` | F6 | Mac reference regression; Linux tests mapped in F6 |
| `CompositorTests/PSDRoundTripTests.swift` | F6 | Mac reference regression; Linux tests mapped in F6 |
| `CompositorTests/PSDVectorFixtures.swift` | F6 | Mac reference regression; Linux tests mapped in F6 |
| `CompositorTests/ProjectTabLayoutTests.swift` | F8 | Mac reference regression; Linux tests mapped in F8 |
| `CompositorTests/ProjectTests.swift` | F1 | Mac reference regression; Linux tests mapped in F1 |
| `CompositorTests/ProjectWorkspaceTests.swift` | F11 | Mac reference regression; Linux tests mapped in F11 |
| `CompositorTests/ResizeSnapTests.swift` | F7 | Mac reference regression; Linux tests mapped in F7 |
| `CompositorTests/SelectionEditTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/SelectionFeatherTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/SelectionTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/SliderSnapTests.swift` | F11 | Mac reference regression; Linux tests mapped in F11 |
| `CompositorTests/SmartEditTests.swift` | F10 | Mac reference regression; Linux tests mapped in F10 |
| `CompositorTests/TiledLayerTests.swift` | F12 | Mac reference regression; Linux tests mapped in F12 |
| `CompositorTests/TitleBarDragTests.swift` | F11 | Mac reference regression; Linux tests mapped in F11 |
| `CompositorTests/TransformPressTests.swift` | F7 | Mac reference regression; Linux tests mapped in F7 |
| `CompositorTests/TransformTests.swift` | F7 | Mac reference regression; Linux tests mapped in F7 |
| `CompositorTests/TypeToolTests.swift` | F9 | Mac reference regression; Linux tests mapped in F9 |
| `Config/Info.plist` | F0 | macOS bundle metadata; no Linux runtime target |
| `README.md` | F0 | Parent-owned shared instructions, README, or CI; no Linux implementation edit |
| `appcast.xml` | F0 | Sparkle macOS updates; no Linux runtime target |
| `docs/project-format.md` | F1 | Format/reference documentation mapped in F1 |
| `docs/writing-comp-files.md` | F1 | Format/reference documentation mapped in F1 |

Inventory total: **207 changed paths**.
