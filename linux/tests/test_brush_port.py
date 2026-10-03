"""BlurTool.swift and SmudgeLiquify.swift regression cases."""

from compositor_linux import editing, engine
from compositor_linux.model import Document, Layer, Transform
from compositor_linux.warp import WarpStroke
from PIL import Image


def _step():
    image = Image.new("RGBA", (64, 32), (0, 0, 0, 255))
    image.paste((255, 255, 255, 255), (32, 0, 64, 32))
    layer = Layer("Step", Transform(0, 0, 64, 32), image=image)
    return Document(64, 32, layers=[layer], active=layer.id)


def test_blur_radius_is_independent_of_diameter():
    narrow, wide = _step(), _step()
    for document, radius in ((narrow, 2), (wide, 12)):
        stroke = editing.Stroke(
            document, diameter=20, hardness=1, opacity=1, mode="Blur", blur_radius=radius
        )
        stroke.append((32, 16))
        stroke.finish()
    assert wide.layer().image.getpixel((25, 16))[0] > narrow.layer().image.getpixel((25, 16))[0]


def test_smudge_strength_changes_paint_and_does_not_repeat_source_stamp():
    weak, strong = _step(), _step()
    for document, strength in ((weak, 0.2), (strong, 1)):
        stroke = WarpStroke(document, 12, 1, strength, (0, 0, 0, 255), False, "Smudge")
        stroke.append((35, 16))
        stroke.append((25, 16))
        stroke.finish()
    assert strong.layer().image.getpixel((27, 16))[0] > weak.layer().image.getpixel((27, 16))[0]


def test_mask_brush_can_paint_beyond_layer_bounds():
    layer = Layer(
        "Small",
        Transform(20, 20, 10, 10),
        image=Image.new("RGBA", (10, 10), (255, 0, 0, 255)),
        mask=Image.new("L", (1, 1), 255),
    )
    d = Document(64, 64, layers=[layer], active=layer.id)
    stroke = editing.Stroke(d, diameter=6, hardness=1, opacity=1, color=(0, 0, 0, 255), mask=True)
    stroke.append((15, 25))
    stroke.finish()
    assert layer.mask_transform is not None
    placed = engine.place(layer.mask, layer.mask_transform, (64, 64), fill=255)
    assert placed.getpixel((15, 25)) < 255
