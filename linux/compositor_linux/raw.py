"""Opt-in local camera RAW development with LibRaw via rawpy."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

from .model import dimensions, finite

EXTENSIONS = {
    ".dng",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".raf",
    ".orf",
    ".rw2",
    ".pef",
    ".srw",
    ".3fr",
    ".iiq",
    ".k25",
    ".mos",
}
MAX_BYTES = 1024 * 1024 * 1024


def validate(settings):
    defaults = dict(exposure=0, temperature=5000, tint=0, boost=1)
    if not isinstance(settings, dict) or set(settings) - set(defaults):
        raise ValueError("Unsupported RAW development setting.")
    values = defaults | settings
    if (
        not finite(values["exposure"], -5, 5)
        or not finite(values["temperature"], 2000, 50_000)
        or not finite(values["tint"], -100, 100)
        or not finite(values["boost"], 0, 1)
    ):
        raise ValueError("Invalid RAW development setting.")
    return values


def develop(path, settings=None, longest=None):
    values = validate(settings or {})
    path = Path(path)
    if (
        path.suffix.lower() not in EXTENSIONS
        or not path.is_file()
        or path.stat().st_size > MAX_BYTES
    ):
        raise ValueError("Unsupported RAW file or input exceeds 1 GiB.")
    try:
        import rawpy
    except ImportError as exc:
        raise ValueError(
            "RAW decoding requires the optional rawpy dependency (install the raw extra)."
        ) from exc
    try:
        with rawpy.imread(str(path)) as frame:
            dimensions(int(frame.sizes.width), int(frame.sizes.height), raster=True)
            camera = tuple(float(v) for v in frame.camera_whitebalance)
            if len(camera) < 4 or any(not math.isfinite(v) or v <= 0 for v in camera[:4]):
                raise ValueError("Camera white balance metadata is invalid.")
            warm = values["temperature"] / 5000
            magenta = values["tint"] / 100
            white_balance = [
                camera[0] * warm * (1 + 0.15 * magenta),
                camera[1] * (1 - 0.30 * magenta),
                camera[2] / warm * (1 + 0.15 * magenta),
                camera[3] * (1 - 0.30 * magenta),
            ]
            developed = frame.postprocess(
                output_bps=8,
                output_color=rawpy.ColorSpace.sRGB,
                use_camera_wb=False,
                user_wb=white_balance,
                no_auto_bright=True,
                exp_shift=2 ** values["exposure"],
                bright=max(0.01, values["boost"]),
                half_size=bool(
                    longest and max(frame.sizes.width, frame.sizes.height) > longest * 2
                ),
            )
    except (OSError, RuntimeError) as exc:
        raise ValueError("Camera RAW file could not be decoded.") from exc
    if (
        not isinstance(developed, np.ndarray)
        or developed.dtype != np.uint8
        or (developed.ndim != 3 or developed.shape[2] != 3)
    ):
        raise ValueError("RAW decoder returned an unsupported pixel format.")
    dimensions(developed.shape[1], developed.shape[0], raster=True)
    image = Image.fromarray(developed, "RGB").convert("RGBA")
    if longest and max(image.size) > longest:
        image.thumbnail((longest, longest), Image.Resampling.BILINEAR)
    return image
