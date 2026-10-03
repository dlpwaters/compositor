"""ColorRangeSelection.swift and local object component regressions."""

from compositor_linux import kernels
from PIL import Image


def test_color_range_selects_disjoint_matches_and_excludes_samples():
    image = Image.new("RGBA", (4, 1))
    image.putdata([(255, 0, 0, 255), (0, 0, 255, 255), (254, 1, 1, 255), (255, 0, 0, 0)])
    mask = kernels.color_range(image, [(255, 0, 0)], fuzziness=3)
    assert list(mask.tobytes()) == [255, 0, 255, 0]
    excluded = kernels.color_range(image, [(255, 0, 0)], [(254, 1, 1)], fuzziness=0)
    assert list(excluded.tobytes()) == [255, 0, 0, 0]
