"""Renderer regressions against source behavior and Mac generated exports."""

from pathlib import Path

import numpy as np
import pytest
from compositor_linux import engine, store
from compositor_linux.model import Document, Layer
from PIL import Image

REFERENCE = Path(__file__).resolve().parent / "fixtures/mac-reference"


def test_hard_mix_uses_strict_sum_threshold_at_opaque_boundary():
    backdrop = Image.new("RGBA", (1, 1), (204, 204, 204, 255))
    foreground = Image.new("RGBA", (1, 1), (51, 51, 51, 255))
    assert engine.blend(backdrop, foreground, "Hard Mix").getpixel((0, 0)) == (0, 0, 0, 255)


def test_hard_mix_matches_opaque_mac_export():
    name = "opaque-blend-hard-mix"
    package, png = REFERENCE / f"{name}.comp", REFERENCE / f"{name}.png"
    actual = np.asarray(engine.render(store.load(package)))
    expected = np.asarray(Image.open(png).convert("RGBA"))
    assert np.array_equal(actual, expected)


def test_soft_alpha_hard_mix_avoids_threshold_flips_against_mac_export():
    name = "blend-hard-mix"
    package, png = REFERENCE / f"{name}.comp", REFERENCE / f"{name}.png"
    actual = np.asarray(engine.render(store.load(package)), dtype=np.int16)
    expected = np.asarray(Image.open(png).convert("RGBA"), dtype=np.int16)
    assert np.array_equal(actual, expected)


def test_pass_through_folder_hard_mix_uses_single_effective_opacity():
    # A real Mac PSD/PSB reference pixel: drawing with combined opacity yields green 145;
    # quantizing folder and child opacity separately flips Hard Mix to green 211.
    backdrop = Layer.blank(1, 1, name="Backdrop")
    backdrop.image = Image.new("RGBA", (1, 1), (248, 248, 47, 156))
    folder = Layer.blank(1, 1, name="Folder")
    folder.group = True
    folder.opacity = 128 / 255
    child = Layer.blank(1, 1, name="Foreground")
    child.parent = folder.id
    child.blend = "Hard Mix"
    child.opacity = 186 / 255
    child.image = Image.new("RGBA", (1, 1), (251, 5, 5, 213))
    actual = engine.render(Document(1, 1, layers=[backdrop, folder, child])).getpixel((0, 0))
    assert isinstance(actual, tuple)
    assert all(abs(a - b) <= 1 for a, b in zip(actual, (251, 145, 29, 186)))


@pytest.mark.parametrize(
    "backdrop,foreground,expected",
    [
        ((227, 137, 95, 171), (235, 88, 157, 162), (234, 103, 82, 190)),
        ((144, 144, 159, 152), (143, 174, 97, 169), (167, 171, 117, 177)),
        ((136, 248, 184, 240), (231, 229, 74, 97), (153, 249, 193, 242)),
        ((77, 115, 179, 80), (235, 67, 77, 249), (183, 72, 99, 142)),
    ],
)
def test_hard_mix_matches_mac_quantized_opacity_surfaces(backdrop, foreground, expected):
    # Direct RGBA8 Core Graphics + CIHardMixBlendMode observations, not fitted values.
    actual = engine.blend(
        Image.new("RGBA", (1, 1), backdrop),
        Image.new("RGBA", (1, 1), foreground),
        "Hard Mix",
        opacity=(186 / 255) * (128 / 255),
    ).getpixel((0, 0))
    assert isinstance(actual, tuple)
    assert all(abs(a - b) <= 1 for a, b in zip(actual, expected)), (actual, expected)


@pytest.mark.parametrize("angle", [0, 27])
def test_motion_blur_edge_matches_cropped_transparent_canvas(angle):
    near_edge = Image.new("RGBA", (9, 9))
    near_edge.putpixel((0, 4), (255, 0, 0, 255))
    padded = Image.new("RGBA", (33, 33))
    padded.putpixel((12, 16), (255, 0, 0, 255))
    settings = {"angle": angle, "distance": 7}
    actual = engine.filtered(near_edge, "Motion Blur", settings)
    expected = engine.filtered(padded, "Motion Blur", settings).crop((12, 12, 21, 21))
    assert actual.tobytes() == expected.tobytes()


def test_motion_blur_positive_angle_runs_upward_to_right():
    image = Image.new("RGBA", (33, 33))
    image.putpixel((16, 16), (255, 0, 0, 255))
    result = np.asarray(engine.filtered(image, "Motion Blur", {"angle": 27, "distance": 7}))
    assert result[14, 20, 3] > result[18, 20, 3]
    assert result[16, 16, 3] < 255


def test_motion_blur_mac_reference_corner_alpha():
    name = "adjustment-motion-blur"
    package, png = REFERENCE / f"{name}.comp", REFERENCE / f"{name}.png"
    actual = np.asarray(engine.render(store.load(package)), dtype=np.int16)
    expected = np.asarray(Image.open(png).convert("RGBA"), dtype=np.int16)
    assert actual[0, 0, 3] == expected[0, 0, 3]
    # Accepted platform difference, not pixel parity: keep the measured error from growing.
    difference = np.abs(actual - expected)
    assert int(difference.max()) <= 51
    assert float(difference.mean()) <= 4
