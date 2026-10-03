"""Finishing filter behavior from FinishingFilterTests.swift."""

from compositor_linux import engine
from PIL import Image


def test_vignette_colors_edges_preserving_center_and_alpha():
    source = Image.new("RGBA", (21, 21), (100, 100, 100, 128))
    result = engine.filtered(
        source,
        "Vignette",
        dict(
            vignetteAmount=100,
            vignetteColor=[255, 0, 0],
            vignetteMidpoint=50,
            vignetteRoundness=100,
            vignetteFeather=60,
            vignetteHighlights=0,
        ),
    )
    assert result.getpixel((0, 0))[0] > result.getpixel((0, 0))[1]
    assert result.getpixel((10, 10))[:3] == (100, 100, 100)
    assert result.getchannel("A").getextrema() == (128, 128)


def test_bloom_spreads_light_beyond_source_alpha():
    source = Image.new("RGBA", (31, 31))
    source.putpixel((15, 15), (255, 255, 255, 255))
    result = engine.filtered(source, "Bloom / Glow", dict(bloomAmount=100, bloomRadius=5))
    assert result.getpixel((18, 15))[3] > 0


def test_tonal_contrast_changes_midtone_detail_without_alpha_change():
    source = Image.new("RGBA", (32, 32), (100, 100, 100, 180))
    for y in range(12, 20):
        for x in range(12, 20):
            source.putpixel((x, y), (145, 145, 145, 180))
    result = engine.filtered(
        source,
        "Tonal Contrast",
        dict(tonalAmount=100, tonalRadius=6, tonalShadows=0, tonalMidtones=100, tonalHighlights=0),
    )
    assert result.getpixel((15, 15))[0] > source.getpixel((15, 15))[0]
    assert result.getchannel("A").tobytes() == source.getchannel("A").tobytes()
