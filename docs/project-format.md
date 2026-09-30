# Compositor project format, versions 1–7

A `.comp` file is a document package containing `manifest.json` and an `images/` directory of `<layer UUID>.png` assets. On Linux the package is an ordinary directory ending in `.comp`; transfer the whole directory between machines.

The manifest identifies `com.compositor.project`, version `7` for new saves (versions `1`–`6` remain readable), and the sRGB working space. It stores document UUID, pixel dimensions, active layer UUID, and layers in bottom-to-top order. Each layer stores its UUID, name, visibility, transform (origin, size, clockwise rotation, flips, sampling), and optional image filename. Blank layers have no image asset. CGPoint origins and CGSize dimensions encode as `[x, y]` arrays; UUID asset names use uppercase canonical UUIDs.

Embedded PNGs preserve source pixels and transparency; transforms remain separate. Projects survive moving or deleting imported source photos. Saving uses a coordinated atomic package replacement. Unsupported versions, invalid metadata, missing assets, unsafe paths, and oversized data are rejected before replacing the live document.

Limits: 30,000 pixels per canvas/image side, 100 million total source pixels, 10,000 layers, 4 MiB manifest, 512 MiB per encoded asset. See `ProjectStore.swift` for validation.

Undo history, selection, and viewport are session-only. Opening fits the canvas, restores the active layer, and starts with clean history. Future editable features must extend the schema and round-trip tests. PNG export is a flattened derivative and does not mark project edits saved.

Image Size adds optional `resolution` (pixels/inch, 1–9600). Older manifests without it default to 72. This additive field retains version 1 compatibility. Both PNG and JPEG exports include document resolution metadata. Resampling stores the new layer pixels and bounds; undo retains the prior sources only during the current session.

Version 2 adds optional `parentID` and `isGroup` on layer records. A group has no image file. Root nodes have no parent; children refer to an existing group. Array order defines bottom-to-top sibling order; renderers traverse each group as a contiguous subtree. Visibility is inherited without changing child flags. Cycles, missing/non-group parents, image-bearing groups, and nesting beyond 64 ancestor levels are rejected. Group ancestors permit room for leaf nodes at the deepest level. Group metadata survives image/canvas resizing and cropping. Older app builds reject version 2 rather than misrender grouped documents. Collapse state is not serialized.

Version 3 adds optional per-layer `opacity` (finite 0–1) and `blendMode` (Normal, Multiply, Screen, Overlay, Darken, Lighten, Difference, Color Dodge, Color Burn). Missing fields default to full opacity and Normal. Group records currently require those defaults; their children can have independent effects. Effects are applied during compositing and retained as metadata when resizing sources. Files declaring older versions cannot contain non-default appearance values.

Version 4 adds optional `maskFile` and `maskEnabled` fields to individual layers. Mask filenames must be `<layer UUID>.mask.png` under `images/`; enabled defaults to true when a mask exists. Records without masks omit both fields. Groups cannot carry masks in this version. Files declaring versions 1–3 cannot contain mask metadata.

Masks store 8-bit grayscale coverage without alpha (white reveals, black hides). Their normalized extent matches the image’s local rectangle, so the same layer transform applies to both. A uniform 1×1 mask is valid and avoids allocating full-resolution pixels before painting. Nonuniform mask pixels and a thumbnail are immutable assets shared by history. Image Size resamples them with the image transform; Canvas Size and Crop preserve their pixels. Up to 100 million mask pixels may be stored in addition to the existing 100 million image pixels; per-side and per-file limits also apply to masks. Disabled masks remain embedded and editable but do not affect compositing. Image-versus-mask target selection is session-only and reopens on image pixels.

Version 5 adds optional `maskSourceID`: the UUID of a non-group layer supplying live alpha in document coordinates. It multiplies the target’s alpha alongside its enabled raster mask. Source pixels, transform, opacity, raster mask and upstream live masks contribute coverage; visibility and RGB color do not. Sources remain independent layers. Missing references, self-links, cycles, group endpoints and chains over 256 nodes are rejected. Deletion can bake the live coverage into dependent image pixels (retaining their raster masks) or remove the links, as one undoable operation. Links survive image/canvas resize and crop. Older versions default to no live mask; older app builds reject v5.

UI terminology: these alpha links are clipping masks. Option-click assigns the lower sibling’s base or releases the connection. Multiple clipped layers share one base, show indented above it, and release when moved outside the contiguous stack. The underlying `maskSourceID` representation is unchanged.

Version 6 allows `maskFile` and `maskEnabled` on group records. A folder has no image, so its mask covers the folder's own transform rectangle (the canvas size when the folder was created); Image Size resamples it through that transform, and Canvas Size and Crop preserve its pixels, exactly as for layer masks. Groups are pass-through, so an enabled folder mask multiplies the coverage of every descendant layer, together with that layer's own mask and any enclosing folders' masks; clipping-mask coverage is unaffected. Files declaring versions 1–5 cannot give a group a mask, and older app builds reject v6.

Version 7 adds adjustment-layer metadata and editable shape metadata. `adjustment.kind`
is Hue/Saturation, Levels, Curves, Exposure, Gradient Map, or Grain. The Swift Codable
record requires legacy `hue`, `saturation`, `lightness`, `colorize`, `levels` and `curves`
fields even when inactive. Levels stores four ranges (RGB, Red, Green, Blue); Curves
stores four point arrays in the same order. Newer settings are optional nested
`hsvSettings`, `exposureSettings`, `gradientMapSettings`, and `grainSettings` records.
HSV dictionaries keyed by the Swift ColorRange enum encode as alternating key/value
arrays, for example `["Reds", {"hue": 15, "saturation": 0, "lightness": 0}]`.
`shape` stores Rectangle or Ellipse, normalized RGB values, and `cornerRadius`.
Groups cannot carry adjustment or shape content.

Optional `maskPlacement` uses the same transform record as a layer; `maskLinked`
defaults to true. A placed linked mask follows subsequent layer transforms; an
unlinked mask keeps its document placement. Uniform 1×1 masks supply constant
coverage. Displaced masks use their edge coverage to choose the outside background.
These optional placement fields are accepted on earlier mask-capable format versions.

Linux saves stage a complete sibling directory, fsync the assets and manifest, and
atomically exchange it with an existing valid project using Linux `renameat2`.
Filesystems without exchange support return an error and preserve the existing project.
Unknown optional manifest and layer fields are retained. This compatibility contract
is tested against source-derived fixtures; a real Mac-to-Linux-to-Mac corpus remains
necessary to certify end-to-end interchange and rendering parity.

## Linux editable text extension

Linux text layers have a normal `imageFile` containing their saved RGBA pixels,
plus optional `linuxText` metadata. The project still declares version 7. Text
cannot coexist with group, shape, or adjustment content. Readers without text
support can display the ordinary raster fallback. Swift Codable ignores the
unknown field; saving in the original Mac app can therefore discard editability.
This compatibility inference still needs a real Mac round-trip test.

`linuxText` version 1 has `text` (nonblank plain text, at most 32,768 characters),
`family` (font family, at most 256 characters), `size` (integer 1–2048 pixels),
`bold`, `italic`, `underline` (booleans), `alignment` (Left, Center, Right), and
`color` (three integer RGB channels, 0–255). Native Qt font shaping runs only
when adding or editing text. Opening and compositing use the saved PNG, so a
missing font does not change the saved appearance. Subsequent text editing uses
the installed family or Qt fallback. Text is plain; HTML and resource URLs are
not interpreted. Rendered bounds must meet the normal raster limits.

Move, scale, rotate, flip, masks, and appearance settings retain text editability.
Image Size scales the placement while retaining the text source. Editing text
preserves its source corner anchor and scale, including linked-mask movement.
Pixel edits replace the immutable source and drop `linuxText`; Undo retains the
previous editable snapshot. Solid-color layers use existing version-7 Rectangle
shape metadata with an opaque full-canvas PNG, rather than a new format extension.
