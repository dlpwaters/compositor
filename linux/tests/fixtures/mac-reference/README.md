# Real Mac renderer regression references

These synthetic 16×16 packages and PNGs were generated on macOS by
`CompositorTests/LinuxPortReferenceTests.swift` using the actual Compositor
1.4.5 `ProjectStore` and `ImageExporter`, not synthesized from Linux output.
They cover opaque Hard Mix, soft-alpha Hard Mix and nondefault Motion Blur.

The Linux regression tests load the whole `.comp` packages and compare against
the Mac PNGs. Hard Mix is byte-exact on the qualified build. Motion Blur's
remaining numerical difference is explicitly accepted and documented in
`docs/linux-parity.md`; its regression bound is not a claim of pixel parity.

The wider 68-case corpus and Mac PSD/PSB comparison were qualified separately.
The reference generator is committed so these cases can be regenerated on a
Mac. Source pixels are synthetic and contain no private document data.
