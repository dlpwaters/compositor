import json
from dataclasses import replace

import numpy as np
import pytest
from compositor_linux import editing, engine, kernels, store
from compositor_linux.model import BLENDS, Document, History, Layer, TextContent, Transform
from PIL import Image


def project(width=16, height=16, color=(200, 80, 30, 255)):
    layer = Layer.blank(width, height)
    layer.image = Image.new("RGBA", (width, height), color)
    return Document(width, height, layers=[layer], active=layer.id)


def test_identity_transform_and_render():
    d = project()
    assert engine.render(d).tobytes() == d.layers[0].image.tobytes()


def text_settings():
    return dict(
        version=1,
        text="Hello Ω",
        family="DejaVu Sans",
        size=24,
        bold=False,
        italic=False,
        underline=False,
        alignment="Left",
        color=[20, 80, 160],
    )


def test_solid_color_layer_roundtrip_undo_and_group_parent(tmp_path):
    history = History(project())
    folder = editing.new_layer(history, group=True)
    id = editing.new_layer(history, name="Backdrop", color=(12, 34, 56))
    layer = history.document.layer()
    assert layer.id == id and layer.parent == folder and layer.name == "Backdrop"
    assert engine.render(history.document).getpixel((8, 8)) == (12, 34, 56, 255)
    store.save(history.document, tmp_path / "color.comp")
    loaded = store.load(tmp_path / "color.comp")
    assert loaded.layer().shape == layer.shape
    assert engine.render(loaded).tobytes() == engine.render(history.document).tobytes()
    history.undo()
    assert history.document.layer(id) is None
    history.redo()
    assert history.document.layer(id).shape == layer.shape


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", True),
        ("text", " "),
        ("text", "x" * 32769),
        ("family", ""),
        ("size", 0),
        ("size", 2049),
        ("size", True),
        ("bold", 1),
        ("alignment", "invalid"),
        ("color", [256, 0, 0]),
        ("color", [0, True, 0]),
    ],
)
def test_text_metadata_validation(key, value):
    document = project()
    settings = dict(text_settings(), **{key: value})
    document.layer().text = TextContent(settings, document.layer().image)
    with pytest.raises(ValueError, match="text"):
        document.validate()


def test_pixel_edits_rasterize_text_and_undo_restores_editability():
    history = History(project())
    layer = history.document.layer()
    layer.text = TextContent(text_settings(), layer.image)
    with history.edit("Invert") as document:
        editing.apply_filter(document, "Invert", {})
    assert history.document.layer().text is None
    history.undo()
    assert history.document.layer().live_text.settings == text_settings()
    history.redo()
    assert history.document.layer().live_text is None


def test_text_retains_source_on_image_resize_and_mask_changes():
    history = History(project())
    layer = history.document.layer()
    layer.text = TextContent(text_settings(), layer.image)
    image = layer.image
    editing.add_mask(history)
    editing.resize_image(history, 32, 48)
    layer = history.document.layer()
    assert layer.live_text is not None and layer.image is image
    assert (layer.transform.width, layer.transform.height) == (32, 48)
    assert layer.mask is not None


@pytest.mark.parametrize("mode", BLENDS)
def test_blends_preserve_soft_alpha(mode):
    bottom = Image.new("RGBA", (1, 1), (100, 150, 200, 255))
    transparent = Image.new("RGBA", (1, 1), (250, 30, 50, 0))
    assert engine.blend(bottom, transparent, mode).getpixel((0, 0)) == bottom.getpixel((0, 0))
    soft = Image.new("RGBA", (1, 1), (250, 30, 50, 64))
    assert engine.blend(bottom, soft, mode).getpixel((0, 0))[3] == 255


def test_multiply_known_value():
    b = Image.new("RGBA", (1, 1), (128, 128, 128, 255))
    assert engine.blend(b, b, "Multiply").getpixel((0, 0)) == (64, 64, 64, 255)


def test_folder_mask_and_hidden_source_clipping():
    d = project()
    base = d.layers[0]
    base.visible = False
    base.mask = Image.new("L", (1, 1), 128)
    upper = Layer.blank(16, 16)
    upper.image = Image.new("RGBA", (16, 16), (30, 200, 80, 255))
    upper.mask_source = base.id
    folder = Layer.blank(16, 16, "Folder")
    folder.group = True
    folder.mask = Image.new("L", (1, 1), 128)
    upper.parent = folder.id
    d.layers.extend([folder, upper])
    assert 63 <= engine.render(d).getpixel((8, 8))[3] <= 65


def test_store_v7_roundtrip_and_atomic_replacement(tmp_path):
    d = project()
    d.layers[0].mask = Image.new("L", (1, 1), 123)
    d.layers[0].mask_transform = Transform(2, 3, 16, 16)
    d.layers[0].mask_linked = False
    d.extras = {"futureField": {"preserve": True}}
    d.layers[0].extras = {"futureLayerFlag": 12}
    path = tmp_path / "test.comp"
    store.save(d, path)
    restored = store.load(path)
    assert store.manifest(restored) == store.manifest(d)
    assert restored.layers[0].image.tobytes() == d.layers[0].image.tobytes()
    d.layers[0].name = "Changed"
    store.save(d, path)
    assert store.load(path).layers[0].name == "Changed"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["test.comp"]


@pytest.mark.parametrize("version", range(1, 8))
def test_load_supported_versions(tmp_path, version):
    d = project()
    path = tmp_path / "test.comp"
    store.save(d, path)
    m = store.manifest(d)
    m["version"] = version
    (path / "manifest.json").write_text(json.dumps(m))
    assert store.load(path).width == d.width


@pytest.mark.parametrize(
    "attack",
    ["traversal", "symlink", "cycle", "nan", "version", "duplicate", "non_mask"],
)
def test_reject_damaged_packages(tmp_path, attack):
    d = project()
    path = tmp_path / "test.comp"
    store.save(d, path)
    m = store.manifest(d)
    record = m["layers"][0]
    if attack == "traversal":
        record["imageFile"] = "../../outside.png"
    elif attack == "symlink":
        asset = path / "images" / record["imageFile"]
        asset.unlink()
        asset.symlink_to(tmp_path / "outside.png")
    elif attack == "cycle":
        record["maskSourceID"] = record["id"]
    elif attack == "nan":
        record["opacity"] = float("nan")
    elif attack == "version":
        m["version"] = 12
    elif attack == "duplicate":
        m["layers"].append(record)
    elif attack == "non_mask":
        record["maskFile"] = f"{record['id']}.mask.png"
        Image.new("RGBA", (2, 2)).save(path / "images" / record["maskFile"])
    (path / "manifest.json").write_text(json.dumps(m))
    with pytest.raises((ValueError, FileNotFoundError)):
        store.load(path)


def test_save_never_replaces_unrelated_folder(tmp_path):
    path = tmp_path / "unrelated.comp"
    path.mkdir()
    (path / "important.txt").write_text("Keep me")
    with pytest.raises(FileNotFoundError):
        store.save(project(), path)
    assert (path / "important.txt").read_text() == "Keep me"


def test_undo_redo_saved_revision_and_rollback():
    h = History(project())
    h.saved("sample.comp")
    original = h.document.layers[0].image
    with h.edit("Move") as d:
        d.layers[0].transform.x = 3
    assert h.dirty and h.document.layers[0].image is original
    assert h.undo() and not h.dirty and h.document.layers[0].transform.x == 0
    assert h.redo() and h.dirty and h.document.layers[0].transform.x == 3
    with pytest.raises(ValueError):
        with h.edit("Invalid") as d:
            d.layers[0].transform.width = float("nan")
    assert h.document.layers[0].transform.width == 16


def test_stroke_opacity_caps_overlapping_dabs():
    d = project(32, 32, (0, 0, 0, 0))
    stroke = editing.Stroke(d, diameter=10, opacity=0.5, color=(255, 0, 0, 255))
    for _ in range(3):
        stroke.append((16, 16))
    stroke.finish()
    assert 127 <= d.layers[0].image.getpixel((16, 16))[3] <= 128


def test_selection_restricts_paint():
    d = project(32, 32, (0, 0, 0, 0))
    d.selection = Image.new("L", (32, 32))
    d.selection.paste(255, (0, 0, 16, 32))
    stroke = editing.Stroke(d, diameter=16, color=(255, 0, 0, 255))
    stroke.append((16, 16))
    assert d.layers[0].image.getpixel((12, 16))[3] == 255
    assert d.layers[0].image.getpixel((20, 16))[3] == 0


def test_wand_contiguous_and_all_matches():
    image = Image.new("RGBA", (9, 3), (255, 0, 0, 255))
    image.paste((0, 0, 255, 255), (3, 0, 6, 3))
    assert np.count_nonzero(np.asarray(kernels.wand(image, 1, 1, tolerance=0))) == 9
    assert (
        np.count_nonzero(np.asarray(kernels.wand(image, 1, 1, tolerance=0, contiguous=False))) == 18
    )


@pytest.mark.parametrize("kind", ["Levels", "Curves", "Exposure"])
def test_identity_adjustments_preserve_alpha(kind):
    d = project(color=(128, 200, 64, 128))
    adjusted = engine.adjust(d.layers[0].image, {"kind": kind})
    a, b = np.asarray(adjusted, dtype=int), np.asarray(d.layers[0].image, dtype=int)
    assert np.max(np.abs(a - b)) <= 1
    assert adjusted.getchannel("A").tobytes() == d.layers[0].image.getchannel("A").tobytes()


def test_hue_rotation_and_gray_neutrality():
    image = Image.new("RGBA", (2, 1))
    image.putdata([(255, 0, 0, 255), (128, 128, 128, 128)])
    result = engine.adjust(image, {"kind": "Hue/Saturation", "hue": 120, "saturation": 100})
    assert result.getpixel((0, 0)) == (0, 255, 0, 255)
    assert result.getpixel((1, 0)) == (128, 128, 128, 128)


def test_noise_and_grain_are_deterministic():
    image = project().layers[0].image
    a = engine.filtered(image, "Add Noise", {"seed": 123})
    b = engine.filtered(image, "Add Noise", {"seed": 123})
    assert a.tobytes() == b.tobytes() and a.tobytes() != image.tobytes()
    a = engine.adjust(image, {"kind": "Grain", "grainSettings": {"seed": 123}})
    b = engine.adjust(image, {"kind": "Grain", "grainSettings": {"seed": 123}})
    assert a.tobytes() == b.tobytes()


def test_clone_group_maps_internal_links():
    d = project()
    group = Layer.blank(16, 16, "Folder")
    group.group = True
    d.layers[0].parent = group.id
    d.layers.insert(0, group)
    h = History(d)
    copies = editing.duplicate(h, {group.id})
    child = next(item for item in h.document.layers if item.id in copies and not item.group)
    assert child.parent in copies
    h.document.validate()


def test_canvas_resize_preserves_sources_and_crop_is_undoable():
    h = History(project())
    image = h.document.layers[0].image
    editing.resize_canvas(h, 32, 32)
    assert h.document.layers[0].transform.x == 8 and h.document.layers[0].image is image
    editing.crop(h, (4, 4, 8, 8))
    assert h.document.width == 8 and h.document.layers[0].transform.x == 4
    h.undo()
    assert h.document.width == 32


def test_export_dpi_and_jpeg_white_background(tmp_path):
    d = project(color=(0, 0, 0, 0))
    d.resolution = 300
    store.export_image(d, tmp_path / "sample.png")
    with Image.open(tmp_path / "sample.png") as image:
        assert abs(image.info["dpi"][0] - 300) < 0.1
    store.export_image(d, tmp_path / "sample.jpg", format="JPEG")
    with Image.open(tmp_path / "sample.jpg") as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_content_fill_and_all_heal_modes():
    image = Image.new("RGBA", (48, 48), (100, 120, 140, 255))
    image.paste((255, 0, 0, 255), (22, 22, 26, 26))
    mask = Image.new("L", image.size)
    # The brush covers the blemish and a surrounding margin, like the upstream fixture.
    mask.paste(255, (18, 18, 30, 30))
    result = kernels.fill(image, mask)
    assert result.getpixel((24, 24)) == (100, 120, 140, 255)
    for mode in range(3):
        result = kernels.heal(image, mask, mode=mode)
        assert result.getpixel((24, 24))[3] == 255
        assert abs(result.getpixel((24, 24))[0] - 100) < 20
        assert result.getpixel((2, 2)) == image.getpixel((2, 2))


@pytest.mark.parametrize("duplicate", [False, True])
def test_move_selected_pixels_preserves_other_source_pixels(duplicate):
    d = project(16, 16)
    original = d.layer().image.copy()
    d.layer().image.paste((255, 0, 0, 255), (2, 2, 4, 4))
    d.selection = Image.new("L", (16, 16))
    d.selection.paste(255, (2, 2, 4, 4))
    h = History(d)
    editing.move_pixels(h, 5, 0, duplicate)
    result = engine.render(h.document)
    assert result.getpixel((7, 2)) == (255, 0, 0, 255)
    assert result.getpixel((10, 10)) == original.getpixel((10, 10))
    assert result.getpixel((2, 2))[3] == (255 if duplicate else 0)
    assert h.document.selection.getbbox() == (7, 2, 9, 4)
    assert h.undo()
    assert engine.render(h.document).getpixel((2, 2)) == (255, 0, 0, 255)


def test_brush_can_extend_beyond_imported_layer_bounds():
    d = Document(64, 64)
    layer = Layer(
        "Small",
        Transform(20, 20, 8, 8),
        image=Image.new("RGBA", (8, 8), (0, 0, 255, 255)),
    )
    d.layers, d.active = [layer], layer.id
    stroke = editing.Stroke(d, diameter=8, color=(255, 0, 0, 255))
    stroke.append((40, 24))
    stroke.finish()
    result = engine.render(d)
    assert result.getpixel((40, 24)) == (255, 0, 0, 255)
    assert result.getpixel((22, 22)) == (0, 0, 255, 255)
    assert layer.transform.width > 8 and layer.image.width > 8


def test_adjustment_wire_format_contains_swift_required_fields(tmp_path):
    d = project()
    for kind in (
        "Levels",
        "Hue/Saturation",
        "Curves",
        "Exposure",
        "Gradient Map",
        "Grain",
    ):
        layer = Layer.blank(16, 16, kind)
        layer.adjustment = {"kind": kind}
        d.layers.append(layer)
    store.save(d, tmp_path / "adjustments.comp")
    records = json.loads((tmp_path / "adjustments.comp" / "manifest.json").read_text())["layers"]
    for record in records[1:]:
        adjustment = record["adjustment"]
        assert {
            "kind",
            "hue",
            "saturation",
            "lightness",
            "colorize",
            "levels",
            "curves",
        } <= adjustment.keys()
        assert len(adjustment["levels"]["ranges"]) == 4
        assert "outputWhite" in adjustment["levels"]["ranges"][0]


def test_merge_inside_masked_folder_does_not_apply_mask_twice():
    d = project()
    folder = Layer.blank(16, 16, "Folder")
    folder.group = True
    folder.mask = Image.new("L", (1, 1), 128)
    d.layer().parent = folder.id
    second = Layer.blank(16, 16, "Second")
    second.parent = folder.id
    second.image = Image.new("RGBA", (16, 16), (20, 120, 220, 255))
    d.layers.extend([folder, second])
    h = History(d)
    editing.merge(h, {d.layers[0].id, second.id})
    # The folder mask still belongs to the parent, not to the baked child raster.
    assert engine.render(h.document).getpixel((8, 8))[3] == 128


def test_contiguous_clipping_stack_shares_base_alpha():
    d = project(color=(255, 0, 0, 128))
    base = d.layers[0]
    top = Layer.blank(16, 16, "Clipped")
    top.image = Image.new("RGBA", (16, 16), (0, 0, 255, 255))
    top.mask_source = base.id
    d.layers.append(top)
    assert engine.render(d).getpixel((8, 8)) == (0, 0, 255, 128)


def test_clipped_adjustment_changes_base_color_without_alpha_growth():
    d = project(color=(100, 100, 100, 128))
    top = Layer.blank(16, 16, "Exposure")
    top.adjustment = {
        "kind": "Exposure",
        "exposureSettings": {"exposure": 1, "offset": 0, "gamma": 1},
    }
    top.mask_source = d.layers[0].id
    d.layers.append(top)
    result = engine.render(d).getpixel((8, 8))
    assert result[0] > 100 and result[3] == 128


def test_placed_linked_mask_follows_affine_transform():
    d = project()
    layer = d.layer()
    layer.mask = Image.new("L", (8, 8), 255)
    layer.mask_transform = Transform(4, 4, 8, 8)
    history = History(d)
    editing.transform_layers(history, {layer.id}, Transform(10, 20, 32, 32))
    assert history.document.layer().mask_transform == Transform(18, 28, 16, 16)
    history.undo()
    layer = history.document.layer()
    layer.mask_linked = False
    editing.transform_layers(history, {layer.id}, Transform(10, 20, 32, 32))
    assert history.document.layer().mask_transform == Transform(4, 4, 8, 8)


def test_multi_layer_transform_uses_combined_bounds():
    d = project()
    d.layer().transform = Transform(10, 10, 10, 10)
    other = Layer("Second", Transform(30, 10, 10, 10), image=d.layer().image)
    d.layers.append(other)
    history = History(d)
    selected = {d.active, other.id}
    bounds = editing.selection_transform(d, selected)
    assert bounds == Transform(10, 10, 30, 10)
    editing.transform_layers(history, selected, replace(bounds, width=60, height=20))
    assert history.document.layer().transform == Transform(10, 10, 20, 20)
    assert history.document.layer(other.id).transform == Transform(50, 10, 20, 20)


def test_reorder_releases_disconnected_clipping_stack():
    d = project()
    clipped = Layer.blank(16, 16, "Clip")
    clipped.mask_source = d.active
    other = Layer.blank(16, 16, "Other")
    d.layers.extend([clipped, other])
    history = History(d)
    editing.move_layer(history, other.id, None, clipped.id)
    assert history.document.layer(clipped.id).mask_source is None


def test_blur_extends_source_and_preserves_mask_placement():
    d = project(width=32, height=32)
    layer = d.layer()
    layer.image = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    layer.transform = Transform(12, 12, 4, 4)
    layer.mask = Image.new("L", (4, 4), 255)
    editing.apply_filter(d, "Gaussian Blur", {"radius": 2})
    assert layer.image.size == (20, 20)
    assert layer.transform == Transform(4, 4, 20, 20)
    assert layer.mask_transform == Transform(12, 12, 4, 4)
    assert engine.render(d).getpixel((11, 13))[3] > 0


def test_apply_mask_keeps_layer_appearance_and_geometry():
    d = project()
    layer = d.layer()
    layer.opacity, layer.blend = 0.5, "Multiply"
    layer.mask = Image.new("L", (1, 1), 128)
    history = History(d)
    before = engine.render(d)
    editing.apply_mask(history)
    assert engine.render(history.document).tobytes() == before.tobytes()
    assert history.document.layer().opacity == 0.5
    assert history.document.layer().blend == "Multiply"
    assert history.document.layer().mask is None


@pytest.mark.parametrize("mode", ["Smudge", "Liquify"])
def test_document_space_warp_preserves_undo_and_placement(mode):
    from compositor_linux.warp import WarpStroke

    d = project(width=64, height=32, color=(0, 0, 255, 255))
    image = d.layer().image.copy()
    image.paste((255, 0, 0, 255), (0, 0, 32, 32))
    d.layer().image = image
    history = History(d)
    before = engine.render(d).tobytes()
    with history.edit(mode) as draft:
        stroke = WarpStroke(draft, 12, 1, 1, (0, 0, 0, 255), False, mode)
        stroke.append((24, 16))
        stroke.append((42, 16))
        stroke.finish()
    assert engine.render(history.document).tobytes() != before
    assert engine.render(history.document).getpixel((42, 16))[0] > 100
    assert history.document.layer().transform.rotation == 0
    assert history.undo()
    assert engine.render(history.document).tobytes() == before


def test_content_fill_can_expand_an_imported_source():
    d = project(width=32, height=32, color=(180, 120, 80, 255))
    d.layer().image = Image.new("RGBA", (16, 16), (180, 120, 80, 255))
    d.layer().transform = Transform(0, 0, 16, 16)
    d.selection = Image.new("L", (32, 32))
    d.selection.paste(255, (16, 0, 24, 16))
    editing.apply_filter(d, "Content-Aware Fill", {})
    assert d.layer().image.size == (24, 16)
    assert engine.render(d).getpixel((20, 8))[3] == 255


def test_edge_resize_and_centered_resize_preserve_correct_anchor():
    transform = Transform(10, 20, 40, 20)
    edge = editing.resized_transform(transform, 3, (50, 30), (70, 30), shift=True)
    assert edge == Transform(10, 20, 60, 20)
    centered = editing.resized_transform(transform, 3, (50, 30), (70, 30), option=True)
    assert centered.center == transform.center
    assert centered.width == 80 and centered.height == 40


@pytest.mark.parametrize("format", ["PNG", "JPEG", "TIFF", "HEIF"])
def test_supported_image_imports_are_self_contained_srgb(tmp_path, format):
    if format == "HEIF":
        from pillow_heif import register_heif_opener

        register_heif_opener()
    source = Image.new("RGB", (12, 8), (110, 170, 210))
    path = tmp_path / "image"
    source.save(path, format=format)
    actual = store.import_image(path)
    assert actual.mode == "RGBA" and actual.size == source.size
    assert all(abs(a - b) < 5 for a, b in zip(actual.getpixel((5, 4))[:3], source.getpixel((5, 4))))


def test_merge_folder_and_selected_child_keeps_valid_parent_and_trims_bounds():
    d = project(width=64, height=64)
    d.layer().image = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
    d.layer().transform = Transform(20, 20, 8, 8)
    folder = Layer.blank(64, 64, "Folder")
    folder.group = True
    d.layer().parent = folder.id
    d.layers.append(folder)
    before = engine.render(d).tobytes()
    history = History(d)
    editing.merge(history, {folder.id, d.active})
    assert history.document.layer().parent is None
    assert history.document.layer().image.size == (8, 8)
    assert engine.render(history.document).tobytes() == before


def test_merge_across_folders_is_rejected_without_changes():
    d = project()
    folder = Layer.blank(16, 16, "Folder")
    folder.group = True
    child = Layer.blank(16, 16, "Child")
    child.parent = folder.id
    d.layers.extend([folder, child])
    history = History(d)
    with pytest.raises(ValueError, match="same folder"):
        editing.merge(history, {d.active, child.id})
    assert len(history.document.layers) == 3 and not history.undo_stack


@pytest.mark.parametrize("value", [0, 50, 120, 180, 240, 330])
def test_hue_band_sampling_wraps_preserves_shoulders_and_rejects_crossed_handles(value):
    from compositor_linux import hue

    original = hue.band_for({}, "Reds")
    centered = hue.sample_band(original, value, "Sample")
    assert hue.weight(centered, value) == 1
    assert (centered["rangeStart"] - centered["falloffStart"]) % 360 == 30
    assert (centered["falloffEnd"] - centered["rangeEnd"]) % 360 == 30
    assert hue.weight(hue.sample_band(original, value, "Add"), value) == 1
    removed = hue.sample_band(centered, value, "Remove")
    assert hue.weight(removed, value) == 0
    assert hue.moved_handle(original, 1, 120) == original


def test_levels_auto_color_and_gray_sampling_remove_channel_casts():
    from compositor_linux import levels

    histogram = np.zeros((4, 256), float)
    for c, (low, high) in enumerate(((20, 200), (40, 160), (80, 220)), 1):
        histogram[c, low] = histogram[c, high] = 500
    shared = levels.automatic(histogram)
    assert shared["ranges"][0] == dict(black=20, white=220)
    separate = levels.automatic(histogram, "Color")
    assert [r.get("white") for r in separate["ranges"][1:]] == [200, 160, 220]
    calibrated = levels.sampled(
        dict(channel="RGB", ranges=[{} for _ in range(4)]), (64, 128, 192, 255), "Gray"
    )
    image = engine.adjust(
        Image.new("RGBA", (1, 1), (64, 128, 192, 255)), dict(kind="Levels", levels=calibrated)
    )
    assert max(image.getpixel((0, 0))[:3]) - min(image.getpixel((0, 0))[:3]) <= 1


def test_distortion_preserves_flipped_pixels_linked_mask_and_undo():
    from compositor_linux.model import Transform

    d = Document(8, 4)
    image = Image.new("RGBA", (8, 4), (255, 0, 0, 255))
    image.paste((0, 0, 255, 255), (4, 0, 8, 4))
    mask = Image.new("L", (8, 4), 255)
    mask.paste(0, (0, 0, 4, 4))
    layer = Layer(
        "Flipped",
        Transform(width=8, height=4, flip_x=True, sampling="Nearest"),
        image=image,
        mask=mask,
    )
    d.layers, d.active = [layer], layer.id
    h = History(d)
    before = engine.render(d).tobytes()
    editing.distort(h, [(0, 0), (8, 0), (8, 4), (0, 4)])
    assert engine.render(h.document).tobytes() == before
    assert not h.document.layer().transform.flip_x
    h.undo()
    assert h.document.layer().transform.flip_x and engine.render(h.document).tobytes() == before


def test_group_distortion_carries_all_layers_and_unlinked_masks_stay_in_place():
    from compositor_linux.model import Transform

    d = Document(32, 8)
    first = Layer(
        "Red",
        Transform(width=8, height=8, sampling="Nearest"),
        image=Image.new("RGBA", (8, 8), (255, 0, 0, 255)),
    )
    second = Layer(
        "Blue",
        Transform(x=8, width=8, height=8, sampling="Nearest"),
        image=Image.new("RGBA", (8, 8), (0, 0, 255, 255)),
    )
    first.mask = Image.new("L", (8, 8))
    first.mask.paste(255, (1, 1, 7, 7))
    first.mask_linked = False
    d.layers, d.active = [first, second], second.id
    h = History(d)
    editing.distort(h, [(0, 0), (32, 0), (32, 8), (0, 8)], selected={first.id, second.id})
    result = engine.render(h.document)
    assert result.getpixel((4, 4)) == (255, 0, 0, 255)
    assert result.getpixel((12, 4))[3] == 0
    assert result.getpixel((20, 4)) == (0, 0, 255, 255)
    assert h.document.layers[0].mask_transform.width == 8
    assert h.document.layers[1].transform.x == 16
    assert len(h.undo_stack) == 1


def test_distortion_rejects_crossed_quad_without_a_partial_edit():
    d = Document(8, 8)
    layer = Layer.blank(8, 8)
    layer.image = Image.new("RGBA", (8, 8), (10, 20, 30, 255))
    d.layers, d.active = [layer], layer.id
    h = History(d)
    with pytest.raises(ValueError, match="convex"):
        editing.distort(h, [(0, 0), (8, 8), (0, 8), (8, 0)])
    assert not h.undo_stack and engine.render(h.document).getpixel((4, 4)) == (10, 20, 30, 255)
