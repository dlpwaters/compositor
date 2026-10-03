"""Regression checks for upstream document metadata and compositing."""

import pytest
from compositor_linux import editing
from compositor_linux.engine import blend, filtered, render
from compositor_linux.model import BLENDS, Document, History, Layer, Transform
from compositor_linux.store import load, save
from PIL import Image


def test_rotated_shadow_follows_layer_local_axes():
    image = Image.new("RGBA", (5, 5))
    image.putpixel((2, 2), (255, 255, 255, 255))
    layer = Layer("Turned", Transform(25, 25, 5, 5, rotation=90), image=image)
    layer.extras["effects"] = {
        "shadow": dict(enabled=True, angle=0, distance=5, blur=0, red=0, green=0, blue=0, opacity=1)
    }
    result = render(Document(60, 60, layers=[layer]))
    assert result.getpixel((27, 22))[3] > 0
    assert result.getpixel((22, 27))[3] == 0


def test_folder_opacity_multiplies_children_without_flattening(tmp_path):
    folder = Layer("Folder", Transform(width=2, height=2), group=True, opacity=0.5)
    child = Layer(
        "Ink",
        Transform(width=2, height=2),
        parent=folder.id,
        image=Image.new("RGBA", (2, 2), (255, 0, 0, 255)),
    )
    document = Document(2, 2, layers=[folder, child])
    assert render(document).getpixel((0, 0))[3] == 128
    path = tmp_path / "group.comp"
    save(document, path)
    assert load(path).layers[0].opacity == 0.5


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("Linear Burn", 51),
        ("Linear Dodge (Add)", 255),
        ("Exclusion", 143),
        ("Subtract", 0),
        ("Divide", 128),
    ],
)
def test_new_blends_are_executable(mode, expected):
    back = Image.new("RGBA", (1, 1), (102, 102, 102, 255))
    top = Image.new("RGBA", (1, 1), (204, 204, 204, 255))
    assert abs(blend(back, top, mode).getpixel((0, 0))[0] - expected) <= 1


@pytest.mark.parametrize("mode", BLENDS)
def test_all_24_blends_preserve_transparent_source_and_backdrop(mode):
    color = Image.new("RGBA", (1, 1), (73, 146, 219, 255))
    clear = Image.new("RGBA", (1, 1))
    assert blend(color, clear, mode).getpixel((0, 0)) == color.getpixel((0, 0))
    assert blend(clear, color, mode).getpixel((0, 0)) == color.getpixel((0, 0))


def test_guides_and_modern_text_are_preserved(tmp_path):
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
    layer = Layer("Title", Transform(width=4, height=4), image=image)
    layer.extras["text"] = {
        "content": "A😀B",
        "fontName": "Helvetica",
        "fontSize": 20,
        "red": 1,
        "green": 0,
        "blue": 0,
        "alignment": "Left",
        "tracking": 0,
        "leading": 0,
        "colorRuns": [{"location": 1, "length": 2, "red": 0, "green": 1, "blue": 0}],
        "fontRuns": [{"location": 3, "length": 1, "fontName": "Helvetica-Bold"}],
    }
    document = Document(
        4,
        4,
        layers=[layer],
        extras={
            "guides": [
                {"id": "C14196E3-290F-4092-BEFA-25419D3DA46F", "axis": "vertical", "position": 2}
            ]
        },
    )
    path = tmp_path / "modern.comp"
    save(document, path)
    opened = load(path)
    assert opened.extras["guides"] == document.extras["guides"]
    assert opened.layers[0].extras["text"] == layer.extras["text"]
    assert opened.layers[0].live_text is not None
    assert opened.layers[0].live_text.settings["text"] == "A😀B"


@pytest.mark.parametrize(
    "kind,settings",
    [
        ("Invert", {}),
        ("Black & White", {"blackWhiteSettings": {"reds": 80}}),
        ("Color Balance", {"colorBalanceSettings": {"midCyanRed": 40}}),
        ("Gaussian Blur", {"blurRadius": 2}),
        ("Motion Blur", {"motionAngle": 0, "motionDistance": 3}),
        ("Add Noise", {"noiseAmount": 10, "noiseSeed": 7}),
    ],
)
def test_new_adjustments_render_and_roundtrip(tmp_path, kind, settings):
    base = Layer(
        "Base", Transform(width=3, height=3), image=Image.new("RGBA", (3, 3), (80, 120, 160, 255))
    )
    adjustment = Layer(kind, Transform(width=3, height=3), adjustment={"kind": kind, **settings})
    document = Document(3, 3, layers=[base, adjustment])
    path = tmp_path / "adjust.comp"
    save(document, path)
    opened = load(path)
    assert opened.layers[1].adjustment["kind"] == kind
    assert render(opened).size == (3, 3)


@pytest.mark.parametrize(
    "kind,settings,point",
    [
        ("stroke", {"size": 1, "red": 0, "green": 1, "blue": 0}, (1, 2)),
        ("shadow", {"distance": 1, "blur": 0, "angle": 90}, (2, 3)),
        ("colorOverlay", {"red": 0, "green": 1, "blue": 0}, (2, 2)),
        ("innerShadow", {"distance": 1, "blur": 0, "angle": 90}, (2, 2)),
        ("outerGlow", {"size": 2, "red": 0, "green": 1, "blue": 0}, (1, 2)),
        ("innerGlow", {"size": 2, "red": 0, "green": 1, "blue": 0}, (2, 2)),
    ],
)
def test_effect_visibility_is_reversible(kind, settings, point):
    image = Image.new("RGBA", (5, 5))
    image.putpixel((2, 2), (255, 0, 0, 255))
    layer = Layer("Mark", Transform(width=5, height=5), image=image)
    layer.extras["effects"] = {kind: settings}
    document = Document(5, 5, layers=[layer])
    visible = render(document).getpixel(point)
    layer.extras["effects"][kind]["enabled"] = False
    hidden = render(document).getpixel(point)
    assert visible != hidden


def test_line_shape_uses_persisted_endpoints_after_resize():
    image = Image.new("RGBA", (5, 5))
    layer = Layer(
        "Line",
        Transform(width=9, height=9),
        image=image,
        shape={
            "kind": "Line",
            "red": 1,
            "green": 0,
            "blue": 0,
            "cornerRadius": 0,
            "lineWidth": 1,
            "start": [0, 0],
            "end": [1, 1],
        },
    )
    result = render(Document(9, 9, layers=[layer]))
    assert result.getpixel((0, 0))[3] > 0
    assert result.getpixel((4, 4))[3] > 0
    assert result.getpixel((8, 0))[3] == 0


def test_guides_follow_crop_and_image_size():
    vertical = {"id": "C14196E3-290F-4092-BEFA-25419D3DA46F", "axis": "vertical", "position": 15}
    horizontal = {
        "id": "19A40E61-3B24-4854-997E-178A9E9FE45C",
        "axis": "horizontal",
        "position": 20,
    }
    history = History(Document(50, 50, extras={"guides": [vertical, horizontal]}))
    editing.crop(history, (5, 10, 30, 30))
    assert [g["position"] for g in history.document.extras["guides"]] == [10, 10]
    editing.resize_image(history, 60, 90)
    assert [g["position"] for g in history.document.extras["guides"]] == [20, 30]
    history.undo()
    assert [g["position"] for g in history.document.extras["guides"]] == [10, 10]


@pytest.mark.parametrize(
    "based_on", ["Transparent Pixels", "Top Left Pixel Color", "Bottom Right Pixel Color"]
)
def test_trim_finds_content_bounds_and_is_undoable(based_on):
    image = Image.new("RGBA", (20, 20), (0, 0, 255, 255))
    if based_on == "Transparent Pixels":
        image = Image.new("RGBA", (20, 20))
    image.paste((255, 0, 0, 255), (2, 4, 17, 15))
    if based_on == "Bottom Right Pixel Color":
        image.paste((0, 0, 255, 255), (0, 0, 20, 4))
    layer = Layer("Subject", Transform(width=20, height=20), image=image)
    history = History(Document(20, 20, layers=[layer], active=layer.id))
    box = editing.trim_bounds(history.document, based_on)
    assert box == (2, 4, 15, 11)
    editing.trim(history, based_on)
    assert (history.document.width, history.document.height) == (15, 11)
    assert history.document.layer().transform.x == -2
    history.undo()
    assert history.document.width == 20


def test_trim_sides_tolerance_empty_and_cancellation():
    image = Image.new("RGBA", (16, 16), (0, 0, 255, 255))
    image.paste((255, 255, 0, 255), (3, 3, 13, 13))
    doc = Document(16, 16, layers=[Layer("Pixels", Transform(width=16, height=16), image=image)])
    assert editing.trim_bounds(doc, "Top Left Pixel Color", sides=(True, False, False, False)) == (
        0,
        3,
        16,
        13,
    )
    assert editing.trim_bounds(doc, "Top Left Pixel Color", tolerance=255) is None
    with pytest.raises(ValueError, match="cancel"):
        editing.trim_bounds(doc, "Top Left Pixel Color", cancelled=lambda: True)


def test_ungroup_splices_children_at_folder_position():
    first = Layer.blank(2, 2, "Below")
    folder = Layer("Folder", Transform(width=2, height=2), group=True)
    above = Layer.blank(2, 2, "Above")
    child = Layer.blank(2, 2, "Child")
    child.parent = folder.id
    history = History(Document(2, 2, layers=[first, folder, above, child], active=folder.id))
    editing.ungroup(history, {folder.id})
    assert [layer.name for layer in history.document.layers] == ["Below", "Child", "Above"]
    assert child.parent is None


def test_copy_layer_hierarchy_between_documents_clones_ids_masks_and_clipping():
    folder = Layer("Folder", Transform(width=8, height=8), group=True)
    base = Layer(
        "Base",
        Transform(width=8, height=8),
        parent=folder.id,
        image=Image.new("RGBA", (8, 8), (255, 0, 0, 255)),
        mask=Image.new("L", (8, 8), 127),
    )
    clipped = Layer(
        "Clipped",
        Transform(width=8, height=8),
        parent=folder.id,
        image=Image.new("RGBA", (8, 8), (0, 0, 255, 255)),
        mask_source=base.id,
    )
    source = Document(8, 8, layers=[folder, base, clipped], active=folder.id)
    copied = editing.copy_layers(source, {folder.id})
    target = History(Document(8, 8, layers=[Layer.blank(8, 8)]))
    ids = editing.paste_layers(target, copied)
    assert len(ids) == 3
    pasted = {layer.name: layer for layer in target.document.layers if layer.id in ids}
    assert pasted["Base"].parent == pasted["Folder"].id
    assert pasted["Clipped"].mask_source == pasted["Base"].id
    assert pasted["Base"].mask.getpixel((0, 0)) == 127
    assert ids.isdisjoint({folder.id, base.id, clipped.id})
    copied[1].mask.paste(0, (0, 0, 8, 8))
    assert pasted["Base"].mask.getpixel((0, 0)) == 127
    target.undo()
    assert len(target.document.layers) == 1


def test_copy_single_clipped_layer_includes_its_source():
    base = Layer("Base", Transform(width=2, height=2), image=Image.new("RGBA", (2, 2)))
    top = Layer(
        "Top", Transform(width=2, height=2), image=Image.new("RGBA", (2, 2)), mask_source=base.id
    )
    copied = editing.copy_layers(Document(2, 2, layers=[base, top]), {top.id})
    assert [layer.name for layer in copied] == ["Base", "Top"]


@pytest.mark.parametrize(
    "style", ["Atkinson (Classic Mac)", "Bayer 4 × 4", "Halftone Dots", "ASCII", "Scanlines (CRT)"]
)
def test_dither_source_modes_preserve_alpha_and_draw(style):
    image = Image.new("RGBA", (32, 24), (120, 150, 180, 255))
    image.paste((0, 0, 0, 0), (0, 0, 2, 2))
    result = filtered(
        image, "Dither", {"style": style, "pixelSize": 2, "pixelShape": "Dot", "characters": " .#@"}
    )
    assert result.size == image.size
    assert result.getpixel((0, 0))[3] == 0
    assert result.tobytes() != image.tobytes()
