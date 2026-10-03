"""Portable Photoshop style dithering through the source-derived C pixel routine."""

import ctypes as C
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter

from . import kernels
from .model import dimensions, finite

STYLES = (
    "Atkinson (Classic Mac)",
    "Floyd–Steinberg",
    "Bayer 2 × 2",
    "Bayer 4 × 4",
    "Bayer 8 × 8",
    "Halftone Dots",
    "Halftone Lines",
    "Halftone Diamonds",
    "Mac Patterns",
    "ASCII",
    "Scanlines (CRT)",
)


def _glyphs(characters, size):
    if (
        not isinstance(characters, str)
        or not characters
        or len(characters) > 64
        or "\n" in characters
    ):
        raise ValueError("ASCII characters must be 1–64 printable characters.")
    width, height = max(1, round(size * 0.6)), round(size)
    font = ImageFont.load_default(size=round(size))
    maps = []
    for char in characters:
        image = Image.new("L", (width, height))
        draw = ImageDraw.Draw(image)
        bounds = draw.textbbox((0, 0), char, font=font)
        draw.text(
            (
                (width - (bounds[2] - bounds[0])) / 2 - bounds[0],
                (height - (bounds[3] - bounds[1])) / 2 - bounds[1],
            ),
            char,
            fill=255,
            font=font,
        )
        array = np.asarray(image, dtype=np.uint8).copy()
        maps.append((float(array.mean()) / 255, array))
    maps.sort(key=lambda item: item[0])
    glyphs = np.ascontiguousarray(np.stack([item[1] for item in maps]))
    coverage = np.ascontiguousarray([item[0] for item in maps], dtype=np.float32)
    return glyphs, coverage, width, height


def apply(image, settings):
    style = settings.get("style", STYLES[0])
    if style not in STYLES or settings.get("pixelShape", "Square") not in ("Square", "Dot"):
        raise ValueError("Unknown dither style or pixel shape.")
    for key, low, high, default in (
        ("pixelSize", 1, 32, 2),
        ("cellSize", 4, 64, 8),
        ("textSize", 6, 64, 14),
        ("lineSpacing", 2, 32, 4),
        ("glow", 0, 100, 35),
        ("dots", 0, 100, 0),
        ("wobble", 0, 64, 0),
        ("angle", -90, 90, 45),
        ("levels", 2, 8, 2),
        ("diffusion", 0, 100, 100),
        ("density", -100, 100, 0),
        ("contrast", -100, 100, 0),
    ):
        if not finite(settings.get(key, default), low, high):
            raise ValueError(f"Invalid dither {key}.")
    block = round(settings.get("pixelSize", 2)) if style not in ("ASCII", "Scanlines (CRT)") else 1
    small = (
        image.resize(
            ((image.width + block - 1) // block, (image.height + block - 1) // block),
            Image.Resampling.BOX,
        )
        if block > 1
        else image
    )
    data = kernels.premultiply(small)
    color_mode = settings.get("colors", "Black & White")
    if color_mode not in ("Black & White", "Two Colors", "Original"):
        raise ValueError("Invalid dither color mode.")

    def rgb(key, default):
        value = settings.get(key, default)
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 3
            or any(type(channel) is not int or not 0 <= channel <= 255 for channel in value)
        ):
            raise ValueError("Invalid dither color.")
        return (C.c_uint8 * 3)(*value)

    dark = rgb("dark", [0, 0, 0])
    light = rgb("light", [255, 255, 255])
    glyphs = coverage = None
    width = height = 0
    if style == "ASCII":
        glyphs, coverage, width, height = _glyphs(
            settings.get("characters", " .:-=+*#%@"), settings.get("textSize", 14)
        )
    params = kernels.DitherParams(
        STYLES.index(style),
        round(settings.get("levels", 2)),
        settings.get("diffusion", 100) / 100,
        settings.get("density", 0) / 100,
        settings.get("contrast", 0) / 100,
        round(
            settings.get("lineSpacing", 4)
            if style == "Scanlines (CRT)"
            else settings.get("cellSize", 8)
        ),
        math.radians(settings.get("angle", 45)),
        int(settings.get("lightOnDark", True)),
        int(color_mode == "Original"),
        dark,
        light,
        width,
        height,
        glyphs.ctypes.data if glyphs is not None else None,
        coverage.ctypes.data if coverage is not None else None,
        len(coverage) if coverage is not None else 0,
        settings.get("dots", 0) / 100,
        settings.get("wobble", 0),
    )
    if not kernels.library().dither_apply(
        data.ctypes.data, data.shape[1], data.shape[0], data.shape[1] * 4, C.byref(params)
    ):
        raise MemoryError("Dither could not allocate its working buffers.")
    if style == "Scanlines (CRT)" and settings.get("glow", 35):
        bloom = np.clip(
            gaussian_filter(
                data.astype(np.float32),
                (settings.get("lineSpacing", 4) * 3 + 3,) * 2 + (0,),
                mode="nearest",
            ),
            0,
            255,
        ).astype(np.uint8)
        kernels.library().dither_glow(
            data.ctypes.data,
            bloom.ctypes.data,
            data.shape[1],
            data.shape[0],
            data.shape[1] * 4,
            settings.get("glow", 35) / 100 * 2.5,
        )
    if block > 1:
        data = np.ascontiguousarray(
            np.asarray(
                Image.fromarray(data).resize((image.width, image.height), Image.Resampling.NEAREST)
            )
        )
        if settings.get("pixelShape", "Square") == "Dot":
            kernels.library().dither_dots(
                data.ctypes.data,
                image.width,
                image.height,
                image.width * 4,
                block,
                C.cast(dark, C.c_void_p),
            )
    dimensions(*image.size, raster=True)
    return kernels.straight(data)
