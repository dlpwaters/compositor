"""Checked ctypes bindings to the unchanged upstream C pixel kernels."""

import ctypes as C
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


@lru_cache(maxsize=1)
def library():
    paths = list(Path(__file__).parent.glob("_pixels*.so"))
    if not paths:
        raise RuntimeError("Native kernels are missing. Run scripts/linux-build.sh first.")
    lib = C.CDLL(str(paths[0]))
    signatures = {
        "content_fill": (
            [C.c_void_p, C.c_size_t, C.c_void_p, C.c_size_t, C.c_int, C.c_int],
            C.c_int,
        ),
        "spot_heal": (
            [
                C.c_void_p,
                C.c_void_p,
                C.c_size_t,
                C.c_size_t,
                C.c_size_t,
                C.c_float,
                C.c_int,
                C.c_uint32,
            ],
            C.c_int,
        ),
        "wand_mask": (
            [
                C.c_void_p,
                C.c_size_t,
                C.c_size_t,
                C.c_size_t,
                C.c_size_t,
                C.c_size_t,
                C.c_size_t,
                C.c_int,
                C.c_int,
                C.c_void_p,
            ],
            C.c_long,
        ),
        "levels_apply": ([C.c_void_p, C.c_size_t, C.c_void_p], None),
        "levels_histogram": ([C.c_void_p, C.c_void_p, C.c_size_t, C.c_void_p], None),
        "noise_add": (
            [
                C.c_void_p,
                C.c_size_t,
                C.c_size_t,
                C.c_size_t,
                C.c_float,
                C.c_int,
                C.c_int,
                C.c_uint32,
            ],
            None,
        ),
        "adjust_gradient_map": (
            [C.c_void_p, C.c_size_t, C.c_size_t, C.c_size_t, C.c_void_p],
            None,
        ),
        "adjust_grain": (
            [
                C.c_void_p,
                C.c_size_t,
                C.c_size_t,
                C.c_size_t,
                C.c_double,
                C.c_double,
                C.c_double,
                C.c_uint32,
                C.c_double,
                C.c_double,
                C.c_double,
            ],
            None,
        ),
        "lens_distort": (
            [C.c_void_p, C.c_void_p, C.c_size_t, C.c_size_t, C.c_size_t, C.c_double],
            None,
        ),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    return lib


def premultiply(image):
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    pixels[..., :3] = (pixels[..., :3].astype(np.uint16) * pixels[..., 3:4] + 127) // 255
    return np.ascontiguousarray(pixels)


def straight(pixels):
    result = pixels.copy()
    alpha = result[..., 3:4].astype(np.uint32)
    colors = result[..., :3].astype(np.uint32)
    result[..., :3] = np.where(
        alpha > 0,
        np.minimum(255, (colors * 255 + alpha // 2) // np.maximum(1, alpha)),
        0,
    )
    return Image.fromarray(result)


def mutate(image, operation, *arguments):
    data = premultiply(image)
    height, width = data.shape[:2]
    if operation == "levels_apply":
        table = np.ascontiguousarray(arguments[0], dtype=np.float32).reshape(3, 256)
        library().levels_apply(data.ctypes.data, width * height, table.ctypes.data)
    elif operation == "adjust_gradient_map":
        table = np.ascontiguousarray(arguments[0], dtype=np.uint8).reshape(256, 3)
        library().adjust_gradient_map(data.ctypes.data, width, height, width * 4, table.ctypes.data)
    elif operation == "lens_distort":
        output = np.zeros_like(data)
        library().lens_distort(
            data.ctypes.data, output.ctypes.data, width, height, width * 4, *arguments
        )
        data = output
    else:
        getattr(library(), operation)(data.ctypes.data, width, height, width * 4, *arguments)
    return straight(data)


def fill(image, coverage):
    data = premultiply(image)
    mask = np.ascontiguousarray(coverage.convert("L"), dtype=np.uint8)
    if mask.shape != data.shape[:2]:
        raise ValueError("Fill coverage must match the raster.")
    height, width = data.shape[:2]
    result = library().content_fill(
        data.ctypes.data, width * 4, mask.ctypes.data, width, width, height
    )
    if result < 0:
        raise MemoryError("Content-aware fill could not allocate its working buffers.")
    if result == 0:
        raise ValueError("No source patch is available outside this selection.")
    return straight(data)


def heal(image, coverage, opacity=1, mode=0, seed=0):
    data = premultiply(image)
    mask = np.ascontiguousarray(coverage.convert("L"), dtype=np.uint8)
    if mask.shape != data.shape[:2] or mode not in (0, 1, 2):
        raise ValueError("Invalid healing coverage or mode.")
    h, w = data.shape[:2]
    if (
        library().spot_heal(data.ctypes.data, mask.ctypes.data, w, h, w * 4, opacity, mode, seed)
        < 0
    ):
        raise MemoryError("Spot healing could not allocate its working buffers.")
    return straight(data)


def wand(image, x, y, tolerance=32, contiguous=True, radius=0):
    data = premultiply(image)
    h, w = data.shape[:2]
    result = np.zeros((h, w), dtype=np.uint8)
    if not 0 <= x < w or not 0 <= y < h:
        return Image.fromarray(result)
    count = library().wand_mask(
        data.ctypes.data,
        w,
        h,
        w * 4,
        int(x),
        int(y),
        max(0, min(16, int(radius))),
        max(0, min(255, int(tolerance))),
        int(contiguous),
        result.ctypes.data,
    )
    if count < 0:
        raise MemoryError("Magic Wand could not allocate its working buffers.")
    return Image.fromarray(result)


def histogram(image, coverage=None):
    data = premultiply(image)
    bins = np.zeros((4, 256), dtype=np.float64)
    mask = np.ascontiguousarray(coverage, dtype=np.uint8) if coverage is not None else None
    if mask is not None and mask.shape != data.shape[:2]:
        raise ValueError("Histogram coverage must match the raster.")
    library().levels_histogram(
        data.ctypes.data,
        mask.ctypes.data if mask is not None else None,
        data.shape[0] * data.shape[1],
        bins.ctypes.data,
    )
    return bins
