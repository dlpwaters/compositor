import AppKit
import Testing
@testable import Compositor

/// Generates a bounded, synthetic corpus using the actual Mac reader/writer and exporter.
/// Linux comparisons consume the package and flattened PNG together; Linux-only round trips
/// are not substitutes for this independent reference.
@MainActor
struct LinuxPortReferenceTests {
    private var root: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("CompositorLinuxPortReferences", isDirectory: true)
    }

    private func raster(foreground: Bool, opaque: Bool = false) throws -> CGImage {
        let side = 16
        let space = CGColorSpace(name: CGColorSpace.sRGB)!
        let bitmap = CGImageAlphaInfo.premultipliedLast.rawValue
        let context = try #require(CGContext(data: nil, width: side, height: side,
            bitsPerComponent: 8, bytesPerRow: side * 4, space: space, bitmapInfo: bitmap))
        let bytes = try #require(context.data).assumingMemoryBound(to: UInt8.self)
        for y in 0..<side {
            for x in 0..<side {
                let offset = (y * side + x) * 4
                let alpha = opaque ? 255 : (foreground ? 48 + ((x * 17 + y * 29) % 208) : 80 + ((x * 11 + y * 7) % 176))
                let colors = foreground ? [(x * 43 + y * 19) % 256, (x * 7 + y * 53) % 256, (x * 29 + y * 13) % 256]
                    : [(x * 31 + y * 5) % 256, (x * 13 + y * 23) % 256, (x * 3 + y * 37) % 256]
                for channel in 0..<3 { bytes[offset + channel] = UInt8((colors[channel] * alpha + 127) / 255) }
                bytes[offset + 3] = UInt8(alpha)
            }
        }
        return try #require(context.makeImage())
    }

    private func layer(_ name: String, image: CGImage, blend: LayerBlendMode = .normal,
                       opacity: Double = 1) -> (ProjectLayerRecord, ImportedImage) {
        let id = UUID()
        let record = ProjectLayerRecord(id: id, name: name, isVisible: true,
            transform: LayerTransform(origin: .zero, size: CGSize(width: 16, height: 16)),
            imageFile: "\(id.uuidString).png", opacity: opacity, blendMode: blend)
        return (record, ImportedImage(image: image, thumbnail: image, name: name))
    }

    private func write(_ name: String, records: [ProjectLayerRecord], images: [UUID: ImportedImage],
                       guides: [CanvasGuide]? = nil) async throws {
        let snapshot = ProjectSnapshot(manifest: ProjectManifest(resolution: 144,
            documentID: UUID(), width: 16, height: 16, activeLayerID: records.last?.id,
            layers: records, guides: guides), images: images)
        let package = root.appendingPathComponent("\(name).comp")
        try await ProjectStore.shared.save(snapshot, to: package)
        let restored = try await ProjectStore.shared.load(from: package)
        #expect(restored.manifest.layers.count == records.count)
        #expect(restored.manifest.version == 11)
        let png = try await ImageExporter.shared.pngData(restored)
        try png.write(to: root.appendingPathComponent("\(name).png"), options: .atomic)
    }

    @Test func generateMacProjectAndRenderingReferences() async throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let back = try raster(foreground: false), front = try raster(foreground: true)
        let opaqueBack = try raster(foreground: false, opaque: true), opaqueFront = try raster(foreground: true, opaque: true)
        var names: [String] = []
        for mode in LayerBlendMode.allCases {
            let name = "blend-" + mode.rawValue.lowercased().replacingOccurrences(of: " ", with: "-")
                .replacingOccurrences(of: "(", with: "").replacingOccurrences(of: ")", with: "")
            let a = layer("Backdrop", image: back), b = layer("Foreground", image: front, blend: mode, opacity: 0.73)
            try await write(name, records: [a.0, b.0], images: [a.0.id: a.1, b.0.id: b.1])
            names.append(name)
            let opaqueA = layer("Opaque backdrop", image: opaqueBack)
            let opaqueB = layer("Opaque foreground", image: opaqueFront, blend: mode)
            let opaqueName = "opaque-" + name
            try await write(opaqueName, records: [opaqueA.0, opaqueB.0], images: [opaqueA.0.id: opaqueA.1, opaqueB.0.id: opaqueB.1])
            names.append(opaqueName)
        }
        for kind in AdjustmentKind.allCases {
            let name = "adjustment-" + kind.rawValue.lowercased().replacingOccurrences(of: "/", with: "-")
                .replacingOccurrences(of: " ", with: "-").replacingOccurrences(of: "&", with: "and")
            let a = layer("Backdrop", image: back), id = UUID()
            var adjustment = LayerAdjustment(kind: kind)
            adjustment.noiseSeed = 12345
            switch kind {
            case .hsv:
                adjustment.hue = 35; adjustment.saturation = 25; adjustment.lightness = 5
            case .levels:
                adjustment.levels.ranges[0] = LevelRange(black: 10, gamma: 1.25, white: 235, outputBlack: 8, outputWhite: 240)
                adjustment.levels.ranges[1].gamma = 0.9
            case .curves:
                adjustment.curves.channels[0] = [CurvePoint(x: 0, y: 0), CurvePoint(x: 80, y: 60), CurvePoint(x: 180, y: 205), CurvePoint(x: 255, y: 255)]
            case .exposure:
                adjustment.exposure = ExposureSettings(exposure: 0.65, offset: 0.01, gamma: 1.1)
            case .gradientMap:
                adjustment.gradientMap = GradientMapSettings(shadows: AdjustmentColor(red: 0.1, green: 0.05, blue: 0.2),
                    highlights: AdjustmentColor(red: 0.9, green: 0.8, blue: 0.25))
            case .grain:
                adjustment.grain = GrainSettings(amount: 45, size: 2.25, roughness: 65, seed: 12345)
            case .blackWhite:
                adjustment.blackWhite = BlackWhiteSettings(reds: 95, yellows: 70, greens: 30, cyans: 55, blues: 20, magentas: 120,
                    tint: true, tintHue: 205, tintSaturation: 25)
            case .colorBalance:
                adjustment.colorBalance = ColorBalanceSettings(shadowCyanRed: -20, midMagentaGreen: 15, highlightYellowBlue: 22)
            case .gaussianBlur: adjustment.blurRadius = 2.75
            case .motionBlur: adjustment.motionAngle = 27; adjustment.motionDistance = 7
            case .addNoise: adjustment.noiseAmount = 25; adjustment.noiseGaussian = true
            case .invert: break
            }
            let b = ProjectLayerRecord(id: id, name: kind.rawValue, isVisible: true,
                transform: LayerTransform(origin: .zero, size: CGSize(width: 16, height: 16)),
                imageFile: nil, adjustment: adjustment)
            try await write(name, records: [a.0, b], images: [a.0.id: a.1])
            names.append(name)
        }
        let a = layer("Child", image: front), groupID = UUID()
        var child = a.0
        child.parentID = groupID
        let group = ProjectLayerRecord(id: groupID, name: "Half opacity folder", isVisible: true,
            transform: LayerTransform(origin: .zero, size: CGSize(width: 16, height: 16)),
            imageFile: nil, isGroup: true, opacity: 0.5, blendMode: .normal)
        try await write("group-guides", records: [group, child], images: [a.0.id: a.1], guides: [
            CanvasGuide(id: UUID(), axis: .horizontal, position: 3.5),
            CanvasGuide(id: UUID(), axis: .vertical, position: 8)
        ])
        names.append("group-guides")

        var text = LayerTextStyle()
        text.content = "A🌤BC"
        text.fontName = "Helvetica"
        text.fontSize = 12
        text.boxSize = CGSize(width: 16, height: 16)
        text.colorRuns = [LayerTextColorRun(location: 1, length: 2, red: 0.9, green: 0.2, blue: 0.1)]
        text.fontRuns = [LayerTextFontRun(location: 3, length: 2, fontName: "Helvetica-Bold")]
        var textLayer = layer("Text fallback", image: front).0
        textLayer.text = text
        textLayer.effects = LayerEffects(stroke: StrokeEffect(enabled: false, size: 2),
            shadow: ShadowEffect(enabled: false), colorOverlay: ColorOverlayEffect(enabled: false),
            innerShadow: InnerShadowEffect(enabled: false), outerGlow: OuterGlowEffect(enabled: false),
            innerGlow: InnerGlowEffect(enabled: false))
        try await write("rich-text-disabled-effects", records: [textLayer],
            images: [textLayer.id: ImportedImage(image: front, thumbnail: front, name: textLayer.name)])
        names.append("rich-text-disabled-effects")
        let effects: [(String, LayerEffects)] = [
            ("stroke", LayerEffects(stroke: StrokeEffect(size: 2, red: 0.1, green: 0.8, blue: 0.2, opacity: 0.8))),
            ("shadow", LayerEffects(shadow: ShadowEffect(angle: 135, distance: 2, blur: 1, opacity: 0.5))),
            ("overlay", LayerEffects(colorOverlay: ColorOverlayEffect(red: 0.2, green: 0.7, blue: 0.6, opacity: 0.4))),
            ("inner-shadow", LayerEffects(innerShadow: InnerShadowEffect(angle: 45, distance: 2, blur: 1, opacity: 0.7))),
            ("outer-glow", LayerEffects(outerGlow: OuterGlowEffect(size: 2, red: 0.2, green: 0.9, blue: 0.3, opacity: 0.8))),
            ("inner-glow", LayerEffects(innerGlow: InnerGlowEffect(size: 2)))
        ]
        for (effectName, effect) in effects {
            let name = "effect-" + effectName
            var record = layer("Effect sample", image: front).0
            record.effects = effect
            try await write(name, records: [record], images: [record.id: ImportedImage(image: front, thumbnail: front, name: record.name)])
            names.append(name)
        }
        let data = try JSONEncoder().encode(names.sorted())
        try data.write(to: root.appendingPathComponent("index.json"), options: .atomic)
        #expect(names.count == LayerBlendMode.allCases.count * 2 + AdjustmentKind.allCases.count + 2 + effects.count)
    }

    @Test func generatePhotoshopImportReferences() async throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let back = try raster(foreground: false), front = try raster(foreground: true)
        var base = PSDRecord(id: UUID(), name: "Backdrop")
        base.bounds = CGRect(x: 0, y: 0, width: 16, height: 16)
        base.image = back
        let groupID = UUID()
        var group = PSDRecord(id: groupID, name: "Half opacity folder")
        group.isGroup = true; group.blendKey = "pass"; group.opacity = 0.5
        var child = PSDRecord(id: UUID(), parentID: groupID, name: "Foreground")
        child.bounds = CGRect(x: 2, y: 1, width: 16, height: 16)
        child.image = front; child.blendKey = "hMix"; child.opacity = 0.73
        let document = PSDDocument(width: 16, height: 16, resolution: 144, layers: [base, group, child])
        for large in [false, true] {
            let name = large ? "mac-generated-psb" : "mac-generated-psd"
            let data = try PSDFixture.data(document, composite: back, largeDocument: large)
            try data.write(to: root.appendingPathComponent(name + (large ? ".psb" : ".psd")), options: .atomic)
            let imported = try PSDDocumentBuilder.makeImport(try PSDReader.read(data))
            #expect(imported.layers.map(\.name) == ["Backdrop", "Foreground", "Half opacity folder"])
            let importedGroup = try #require(imported.layers.first { $0.name == "Half opacity folder" })
            let importedChild = try #require(imported.layers.first { $0.name == "Foreground" })
            #expect(importedGroup.isGroup)
            #expect(abs(importedGroup.opacity - 0.5) < 0.01)
            #expect(importedChild.parentID == importedGroup.id)
            #expect(importedChild.blendMode == .hardMix)
            #expect(abs(importedChild.opacity - 0.73) < 0.01)
            let session = EditorSession()
            session.document = CanvasDocument(width: 16, height: 16, layers: imported.layers, resolution: imported.resolution)
            let snapshot = try #require(session.projectSnapshot())
            try await ProjectStore.shared.save(snapshot, to: root.appendingPathComponent(name + ".comp"))
            let png = try await ImageExporter.shared.pngData(snapshot)
            try png.write(to: root.appendingPathComponent(name + ".png"), options: .atomic)
        }
    }
}
