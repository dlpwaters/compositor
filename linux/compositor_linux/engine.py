"""sRGB compositing, transform sampling, filters, and shared adjustment kernels."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps
from scipy.ndimage import gaussian_filter, map_coordinates, uniform_filter

from . import dither, effects, kernels
from .model import Document, dimensions


def byte_image(values):
    return Image.fromarray(np.clip(np.floor(values + 0.5), 0, 255).astype(np.uint8))


def place(image, transform, size, region=(0, 0), scale=1, fill=0):
    """Sample only the requested output rectangle; never allocate a scaled layer's extent."""
    cx, cy = transform.center
    a = math.radians(transform.rotation % 360)
    sx, sy = image.width / transform.width, image.height / transform.height
    if transform.flip_x:
        sx = -sx
    if transform.flip_y:
        sy = -sy
    coefficients = (
        math.cos(a) * sx / scale,
        math.sin(a) * sx / scale,
        (region[0] - cx) * math.cos(a) * sx + (region[1] - cy) * math.sin(a) * sx + image.width / 2,
        -math.sin(a) * sy / scale,
        math.cos(a) * sy / scale,
        -(region[0] - cx) * math.sin(a) * sy
        + (region[1] - cy) * math.cos(a) * sy
        + image.height / 2,
    )
    if (
        coefficients[0] == 1
        and coefficients[1] == 0
        and coefficients[3] == 0
        and coefficients[4] == 1
    ):
        left, top = coefficients[2], coefficients[5]
        if left.is_integer() and top.is_integer():
            left, top = int(left), int(top)
            result = image.crop((left, top, left + size[0], top + size[1]))
            if image.mode == "L" and fill:
                result = Image.new("L", size, fill)
                overlap = (
                    max(0, left),
                    max(0, top),
                    min(image.width, left + size[0]),
                    min(image.height, top + size[1]),
                )
                if overlap[2] > overlap[0] and overlap[3] > overlap[1]:
                    result.paste(image.crop(overlap), (overlap[0] - left, overlap[1] - top))
            return result
    if transform.sampling == "Nearest":
        resample = Image.Resampling.NEAREST
    elif transform.sampling == "Smooth":
        resample = Image.Resampling.BILINEAR
    else:
        resample = Image.Resampling.BICUBIC
        # An affine sampler doesn't prefilter reductions. Shrink first with Lanczos.
        reduction = min(abs(sx), abs(sy)) / scale
        if reduction > 2:
            factor = 2 ** int(math.log2(reduction))
            image = image.resize(
                (
                    max(1, math.ceil(image.width / factor)),
                    max(1, math.ceil(image.height / factor)),
                ),
                Image.Resampling.LANCZOS,
            )
            return place(image, transform, size, region, scale, fill)
    return image.transform(
        size,
        Image.Transform.AFFINE,
        coefficients,
        resample=resample,
        fillcolor=fill if image.mode == "L" else (0, 0, 0, 0),
    )


def _lum(c):
    return c[..., 0] * 0.3 + c[..., 1] * 0.59 + c[..., 2] * 0.11


def _set_lum(c, lum):
    c = c + (lum - _lum(c))[..., None]
    lo, hi, item = c.min(-1), c.max(-1), _lum(c)
    c = np.where(
        (lo < 0)[..., None],
        item[..., None] + (c - item[..., None]) * (item / np.maximum(item - lo, 1e-12))[..., None],
        c,
    )
    c = np.where(
        (hi > 1)[..., None],
        item[..., None]
        + (c - item[..., None]) * ((1 - item) / np.maximum(hi - item, 1e-12))[..., None],
        c,
    )
    return np.clip(c, 0, 1)


def _set_sat(c, sat):
    lo, hi = c.min(-1), c.max(-1)
    return (c - lo[..., None]) * (sat / np.maximum(hi - lo, 1e-12))[..., None]


def blend(backdrop, source, mode="Normal", opacity: float = 1):
    if mode == "Normal" and opacity == 1:
        return Image.alpha_composite(backdrop, source)
    b, s = (
        np.asarray(backdrop, dtype=np.float32) / 255,
        np.asarray(source, dtype=np.float32) / 255,
    )
    cb, cs, ab, ass = b[..., :3], s[..., :3], b[..., 3:4], s[..., 3:4] * opacity
    if mode == "Normal":
        value = cs
    elif mode == "Multiply":
        value = cb * cs
    elif mode == "Screen":
        value = cb + cs - cb * cs
    elif mode == "Overlay":
        value = np.where(cb <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    elif mode == "Darken":
        value = np.minimum(cb, cs)
    elif mode == "Lighten":
        value = np.maximum(cb, cs)
    elif mode == "Difference":
        value = np.abs(cb - cs)
    elif mode == "Color Dodge":
        value = np.where(
            cb == 0,
            0,
            np.where(cs >= 1, 1, np.minimum(1, cb / np.maximum(1 - cs, 1e-12))),
        )
    elif mode == "Color Burn":
        value = np.where(
            cb >= 1,
            1,
            np.where(cs == 0, 0, 1 - np.minimum(1, (1 - cb) / np.maximum(cs, 1e-12))),
        )
    elif mode == "Linear Burn":
        value = np.maximum(0, cb + cs - 1)
    elif mode == "Linear Dodge (Add)":
        value = np.minimum(1, cb + cs)
    elif mode == "Soft Light":
        d = np.where(cb <= 0.25, ((16 * cb - 12) * cb + 4) * cb, np.sqrt(cb))
        value = np.where(cs <= 0.5, cb - (1 - 2 * cs) * cb * (1 - cb), cb + (2 * cs - 1) * (d - cb))
    elif mode == "Hard Light":
        value = np.where(cs <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    elif mode == "Vivid Light":
        value = np.where(
            cs <= 0.5,
            1 - np.minimum(1, (1 - cb) / np.maximum(2 * cs, 1e-12)),
            np.minimum(1, cb / np.maximum(2 * (1 - cs), 1e-12)),
        )
        value = np.where((cs <= 0.5) & (cb >= 1), 1, value)
        value = np.where((cs > 0.5) & (cb <= 0), 0, value)
    elif mode == "Linear Light":
        value = np.clip(cb + 2 * cs - 1, 0, 1)
    elif mode == "Pin Light":
        value = np.where(cs <= 0.5, np.minimum(cb, 2 * cs), np.maximum(cb, 2 * cs - 1))
    elif mode == "Hard Mix":
        # Mac draws premultiplied RGBA8 with a separately quantized global alpha.
        # Keep both rounding stages: round(original premul) then multiply opacity.
        # A direct Core Graphics sweep verifies this integer surface operation.
        back = np.asarray(backdrop, dtype=np.int32)
        front = np.asarray(source, dtype=np.int32)
        factor = math.floor(opacity * 255 + 0.5)
        backdrop_alpha = back[..., 3:4]
        source_alpha = (front[..., 3:4] * factor + 127) // 255
        backdrop_premul = (back[..., :3] * backdrop_alpha + 127) // 255
        source_premul = (front[..., :3] * front[..., 3:4] + 127) // 255
        source_premul = (source_premul * factor + 127) // 255
        # Cross-multiplication preserves the strict equality boundary exactly.
        value = (
            source_premul * backdrop_alpha + backdrop_premul * source_alpha
            > source_alpha * backdrop_alpha
        )
        source_fraction, backdrop_fraction = source_alpha / 255, backdrop_alpha / 255
        alpha = np.floor(source_alpha + backdrop_alpha * (1 - source_fraction) + 0.5)
        premul = np.floor(
            source_premul * (1 - backdrop_fraction)
            + backdrop_premul * (1 - source_fraction)
            + value * source_fraction * backdrop_fraction * 255
            + 0.5
        )
        # Core Image returns another premultiplied RGBA8 surface before PNG export.
        rgb = premul * 255 / np.maximum(alpha, 1)
        return byte_image(np.concatenate((rgb, alpha), axis=-1))
    elif mode == "Exclusion":
        value = cb + cs - 2 * cb * cs
    elif mode == "Subtract":
        value = np.maximum(0, cb - cs)
    elif mode == "Divide":
        value = np.minimum(1, cb / np.maximum(cs, 1e-12))
    elif mode == "Hue":
        value = _set_lum(_set_sat(cs, cb.max(-1) - cb.min(-1)), _lum(cb))
    elif mode == "Saturation":
        value = _set_lum(_set_sat(cb, cs.max(-1) - cs.min(-1)), _lum(cb))
    elif mode == "Color":
        value = _set_lum(cs, _lum(cb))
    elif mode == "Luminosity":
        value = _set_lum(cb, _lum(cs))
    else:
        raise ValueError(f"Unknown blend mode: {mode}")
    alpha = ass + ab * (1 - ass)
    rgb = ass * ((1 - ab) * cs + ab * value) + (1 - ass) * ab * cb
    return byte_image(np.concatenate((rgb / np.maximum(alpha, 1e-12), alpha), axis=-1) * 255)


def render(document: Document, region=None, scale=1, only=None):
    if region is None:
        region = (0, 0, document.width, document.height)
    x, y, width, height = region
    size = max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))
    dimensions(*size, raster=True)
    output = Image.new("RGBA", size)
    by_id = {layer.id: layer for layer in document.layers}
    coverage_cache = {}

    def raster_mask(layer):
        if layer.mask is not None and layer.mask_enabled:
            if layer.mask.size == (1, 1):
                return Image.new("L", size, layer.mask.getpixel((0, 0)))
            background = 0
            if layer.mask_transform:
                array = np.asarray(layer.mask)
                edges = np.concatenate((array[0], array[-1], array[1:-1, 0], array[1:-1, -1]))
                background = 255 if edges.mean() >= 127.5 else 0
            return place(
                layer.mask,
                layer.mask_transform or layer.transform,
                size,
                (x, y),
                scale,
                background,
            )
        return Image.new("L", size, 255)

    def pixels(layer):
        if layer.image is None:
            return Image.new("RGBA", size)
        source = layer.image
        if layer.shape:
            target = (
                min(8192, max(1, round(layer.transform.width * scale))),
                min(8192, max(1, round(layer.transform.height * scale))),
            )
            source = shape_pixels(
                target,
                layer.shape,
                layer.shape.get("cornerRadius", 0) * target[0] / layer.transform.width,
                target[0] / layer.transform.width,
            )
        return place(source, layer.transform, size, (x, y), scale)

    def coverage(id):
        if id in coverage_cache:
            return coverage_cache[id]
        layer = by_id[id]
        alpha = pixels(layer).getchannel("A")
        alpha = ImageChops.multiply(alpha, raster_mask(layer))
        if layer.mask_source:
            alpha = ImageChops.multiply(alpha, coverage(layer.mask_source))
        if layer.opacity != 1:
            alpha = alpha.point(lambda n: round(n * layer.opacity))
        coverage_cache[id] = alpha
        return alpha

    included = document.descendants(only) if only else None
    entries = [
        (layer, ancestors)
        for layer, ancestors in document.entries()
        if not layer.group
        and layer.visible
        and all(a.visible for a in ancestors)
        and (included is None or layer.id in included)
    ]
    stacks, stacked = {}, set()
    for index, (base, ancestors) in enumerate(entries):
        if base.mask_source or base.adjustment:
            continue
        children = []
        for child, child_ancestors in entries[index + 1 :]:
            if child.mask_source != base.id or child.parent != base.parent:
                break
            children.append((child, child_ancestors))
        if children:
            stacks[base.id] = children
            stacked.update(child.id for child, _ in children)

    def mask_for(layer, ancestors, live=True, own=True, defer_ancestor_opacity=False):
        mask = raster_mask(layer) if own else Image.new("L", size, 255)
        for ancestor in ancestors:
            mask = ImageChops.multiply(mask, raster_mask(ancestor))
            if ancestor.opacity != 1 and not defer_ancestor_opacity:
                mask = mask.point(lambda n, amount=ancestor.opacity: round(n * amount))
        if live and layer.mask_source:
            mask = ImageChops.multiply(mask, coverage(layer.mask_source))
        return mask

    def own_image(layer, ancestors, live=True):
        # Mac draws Hard Mix with a single effective child-and-folder opacity.
        # Rounding folder alpha separately can flip a channel at the threshold.
        hard_mix = layer.blend == "Hard Mix"
        if layer.extras.get("effects"):
            source = layer.image.copy()
            if layer.shape:
                source = shape_pixels(source.size, layer.shape)
            if layer.mask is not None and layer.mask_enabled:
                if layer.mask.size == (1, 1):
                    local_mask = Image.new("L", source.size, layer.mask.getpixel((0, 0)))
                elif layer.mask_transform is None:
                    local_mask = layer.mask.resize(source.size, Image.Resampling.BILINEAR)
                else:
                    doc_mask = raster_mask(layer)
                    t = layer.transform
                    start = t.point(int(t.flip_x), int(t.flip_y))
                    end_x = t.point(int(not t.flip_x), int(t.flip_y))
                    end_y = t.point(int(t.flip_x), int(not t.flip_y))
                    coefficients = (
                        (end_x[0] - start[0]) * scale / source.width,
                        (end_y[0] - start[0]) * scale / source.height,
                        (start[0] - x) * scale,
                        (end_x[1] - start[1]) * scale / source.width,
                        (end_y[1] - start[1]) * scale / source.height,
                        (start[1] - y) * scale,
                    )
                    local_mask = doc_mask.transform(
                        source.size, Image.Transform.AFFINE, coefficients, Image.Resampling.BILINEAR
                    )
                source.putalpha(ImageChops.multiply(source.getchannel("A"), local_mask))
            settings = layer.extras["effects"]
            margin = 2
            for key, item in settings.items():
                if not item.get("enabled", True):
                    continue
                if key == "shadow":
                    margin = max(
                        margin, math.ceil(item.get("distance", 20) + item.get("blur", 20) * 3 + 2)
                    )
                elif key == "outerGlow":
                    margin = max(margin, math.ceil(item.get("size", 20) * 3 + 2))
                elif key == "stroke" and not item.get("inside", False):
                    margin = max(margin, math.ceil(item.get("size", 4) + 2))
            padded_size = source.width + 2 * margin, source.height + 2 * margin
            dimensions(*padded_size, raster=True)
            padded = Image.new("RGBA", padded_size)
            padded.paste(source, (margin, margin))
            rendered = effects.render(padded, settings)
            center_x, center_y = layer.transform.center
            expanded = replace(
                layer.transform,
                width=layer.transform.width * padded_size[0] / source.width,
                height=layer.transform.height * padded_size[1] / source.height,
            )
            expanded.x, expanded.y = (center_x - expanded.width / 2, center_y - expanded.height / 2)
            image = place(rendered, expanded, size, (x, y), scale)
            mask = mask_for(layer, ancestors, live, own=False, defer_ancestor_opacity=hard_mix)
        else:
            image = pixels(layer)
            mask = mask_for(layer, ancestors, live, defer_ancestor_opacity=hard_mix)
        alpha = ImageChops.multiply(image.getchannel("A"), mask)
        if layer.opacity != 1 and layer.blend != "Hard Mix":
            alpha = alpha.point(lambda n: round(n * layer.opacity))
        image.putalpha(alpha)
        return image

    def adjustment_surface(original, layer, ancestors, live=True):
        adjusted = adjust(original, layer.adjustment, origin=(x, y), units_per_pixel=1 / scale)
        if layer.blend != "Normal":
            base, top = original.copy(), adjusted.copy()
            base.putalpha(255)
            top.putalpha(255)
            adjusted = blend(base, top, layer.blend)
            adjusted.putalpha(original.getchannel("A"))
        mix = mask_for(layer, ancestors, live).point(lambda n: round(n * layer.opacity))
        return Image.composite(adjusted, original, mix)

    for layer, ancestors in entries:
        if layer.id in stacked:
            continue
        if layer.id in stacks:
            group = own_image(layer, ancestors, live=False)
            alpha = group.getchannel("A")
            group.putalpha(255)
            for child, child_ancestors in stacks[layer.id]:
                group = (
                    adjustment_surface(group, child, child_ancestors, live=False)
                    if child.adjustment
                    else blend(
                        group,
                        own_image(child, child_ancestors, live=False),
                        child.blend,
                        opacity=(child.opacity * math.prod(a.opacity for a in child_ancestors))
                        if child.blend == "Hard Mix"
                        else 1,
                    )
                )
            group.putalpha(alpha)
            output = blend(output, group, layer.blend)
        elif layer.adjustment:
            if layer.mask_source is None:
                output = adjustment_surface(output, layer, ancestors)
        else:
            output = blend(
                output,
                own_image(layer, ancestors),
                layer.blend,
                opacity=(layer.opacity * math.prod(a.opacity for a in ancestors))
                if layer.blend == "Hard Mix"
                else 1,
            )
    return output


def range_table(settings):
    def apply(values, r):
        black, white = r.get("black", 0), r.get("white", 255)
        output_black, output_white = r.get("outputBlack", 0), r.get("outputWhite", 255)
        return (
            output_black
            + np.clip((values * 255 - black) / max(1, white - black), 0, 1)
            ** (1 / r.get("gamma", 1))
            * (output_white - output_black)
        ) / 255

    ranges = settings.get("ranges", [{} for _ in range(4)])
    return np.array(
        [apply(apply(np.arange(256) / 255, ranges[c]), ranges[0]) for c in (1, 2, 3)],
        dtype=np.float32,
    )


def curve_table(points):
    x = np.array([p["x"] for p in points], float)
    y = np.array([p["y"] for p in points], float)
    d = np.diff(y) / np.diff(x)
    slopes = np.zeros(len(points))
    slopes[0], slopes[-1] = d[0], d[-1]
    for i in range(1, len(points) - 1):
        slopes[i] = 2 / (1 / d[i - 1] + 1 / d[i]) if d[i - 1] * d[i] > 0 else 0
    values = np.arange(256)
    i = np.clip(np.searchsorted(x, values, side="right") - 1, 0, len(points) - 2)
    h = x[i + 1] - x[i]
    t = np.clip((values - x[i]) / h, 0, 1)
    return (
        np.clip(
            (2 * t**3 - 3 * t**2 + 1) * y[i]
            + (t**3 - 2 * t**2 + t) * h * slopes[i]
            + (-2 * t**3 + 3 * t**2) * y[i + 1]
            + (t**3 - t**2) * h * slopes[i + 1],
            0,
            255,
        )
        / 255
    )


def dict_pairs(value):
    # Swift dictionaries with enum keys are encoded as alternating key/value arrays.
    return dict(zip(value[::2], value[1::2])) if isinstance(value, list) else value


def hue_saturation(image, settings):
    rgba = np.asarray(image, dtype=np.float32) / 255
    cube = hue_cube(json.dumps(settings, sort_keys=True, separators=(",", ":")))
    coordinates = (rgba[..., :3] * 32).transpose(2, 0, 1)
    rgba[..., :3] = np.stack(
        [
            map_coordinates(cube[..., channel], coordinates, order=1, mode="nearest")
            for channel in range(3)
        ],
        -1,
    )
    return byte_image(rgba * 255)


@lru_cache(maxsize=4)
def hue_cube(settings_json):
    values = np.linspace(0, 1, 33, dtype=np.float64)
    rgb = np.stack(np.meshgrid(values, values, values, indexing="ij"), -1)
    return hue_values(rgb, json.loads(settings_json)).astype(np.float32)


def hue_values(rgb, settings):
    lo, hi = rgb.min(-1), rgb.max(-1)
    delta, light = hi - lo, (hi + lo) / 2
    saturation = np.where(delta > 0, delta / np.maximum(1e-12, 1 - np.abs(2 * light - 1)), 0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = (
        np.where(
            hi == r,
            (g - b) / np.maximum(delta, 1e-12),
            np.where(
                hi == g,
                (b - r) / np.maximum(delta, 1e-12) + 2,
                (r - g) / np.maximum(delta, 1e-12) + 4,
            ),
        )
        * 60
    )
    hue = np.where(delta > 0, hue % 360, 0)
    adjustments = dict_pairs(settings.get("adjustments", {}))
    selected = settings.get("range", "Master")
    selected_adjustment = adjustments.get(selected, {})
    if settings.get("colorize", False):
        hue = np.full_like(hue, selected_adjustment.get("hue", 0) % 360)
        saturation = np.full_like(saturation, selected_adjustment.get("saturation", 25) / 100)
        light_amount = selected_adjustment.get("lightness", 0) / 100
    else:
        shift, sat_amount, light_amount = (
            np.zeros_like(hue),
            np.zeros_like(hue),
            np.zeros_like(hue),
        )
        bands = dict_pairs(settings.get("bands", {}))
        defaults = {
            "Reds": (315, 345, 15, 45),
            "Yellows": (15, 45, 75, 105),
            "Greens": (75, 105, 135, 165),
            "Cyans": (135, 165, 195, 225),
            "Blues": (195, 225, 255, 285),
            "Magentas": (255, 285, 315, 345),
        }
        for name, value in adjustments.items():
            weight = np.ones_like(hue)
            if name != "Master":
                band = bands.get(
                    name,
                    dict(
                        zip(
                            ("falloffStart", "rangeStart", "rangeEnd", "falloffEnd"),
                            defaults[name],
                        )
                    ),
                )
                start, end = band["falloffStart"], band["falloffEnd"]
                span, position = (
                    (end - start) % 360,
                    (np.floor(hue + 0.5) - start) % 360,
                )
                ramp_in, plateau = (
                    (band["rangeStart"] - start) % 360,
                    (band["rangeEnd"] - start) % 360,
                )
                if span > 0:
                    weight = np.where(
                        position < ramp_in,
                        position / max(ramp_in, 1e-12),
                        np.where(
                            position <= plateau,
                            1,
                            (span - position) / max(span - plateau, 1e-12),
                        ),
                    )
                    weight = np.where(position <= span, weight, 0)
                if settings.get("invertRange") and name == selected:
                    weight = 1 - weight
            shift += value.get("hue", 0) * weight
            sat_amount += value.get("saturation", 0) / 100 * weight
            light_amount += value.get("lightness", 0) / 100 * weight
        hue = (hue + shift) % 360
        saturation = np.clip(saturation * (1 + sat_amount), 0, 1)
    amount = np.clip(light_amount, -1, 1)
    light = np.where(amount >= 0, light + (1 - light) * amount, light * (1 + amount))
    chroma = (1 - np.abs(2 * light - 1)) * saturation
    sector = hue / 60
    second = chroma * (1 - np.abs(sector % 2 - 1))
    zero = np.zeros_like(chroma)
    sectors = [
        (chroma, second, zero),
        (second, chroma, zero),
        (zero, chroma, second),
        (zero, second, chroma),
        (second, zero, chroma),
        (chroma, zero, second),
    ]
    out = np.zeros_like(rgb)
    for i, channels in enumerate(sectors):
        out = np.where((sector.astype(int) == i)[..., None], np.stack(channels, -1), out)
    return out + (light - chroma / 2)[..., None]


def adjust(image, adjustment, origin=(0, 0), units_per_pixel=1):
    kind = adjustment["kind"]
    if kind == "Hue/Saturation":
        settings = adjustment.get("hsvSettings") or dict(
            range="Master",
            colorize=adjustment.get("colorize", False),
            adjustments={
                "Master": {k: adjustment.get(k, 0) for k in ("hue", "saturation", "lightness")}
            },
        )
        return hue_saturation(image, settings)
    if kind == "Levels":
        return kernels.mutate(image, "levels_apply", range_table(adjustment.get("levels", {})))
    if kind == "Curves":
        channels = adjustment.get("curves", {}).get(
            "channels", [[dict(x=0, y=0), dict(x=255, y=255)] for _ in range(4)]
        )
        composite = curve_table(channels[0])
        tables = [
            np.interp(curve_table(channels[i]) * 255, np.arange(256), composite) for i in (1, 2, 3)
        ]
        return kernels.mutate(image, "levels_apply", tables)
    if kind == "Exposure":
        settings = adjustment.get("exposureSettings") or {}
        v = np.arange(256) / 255
        linear = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        linear = np.maximum(
            0, linear * 2 ** settings.get("exposure", 0) + settings.get("offset", 0)
        ) ** (1 / settings.get("gamma", 1))
        output = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
        return kernels.mutate(image, "levels_apply", np.tile(np.clip(output, 0, 1), (3, 1)))
    if kind == "Gradient Map":
        settings = adjustment.get("gradientMapSettings") or {}
        colors = [
            np.array([settings.get(name, {}).get(k, default) for k in ("red", "green", "blue")])
            for name, default in (("shadows", 0), ("highlights", 1))
        ]
        if settings.get("reversed"):
            colors.reverse()
        t = np.arange(256)[:, None] / 255
        return kernels.mutate(
            image,
            "adjust_gradient_map",
            np.floor((colors[0] * (1 - t) + colors[1] * t) * 255 + 0.5).astype(np.uint8),
        )
    if kind == "Grain":
        settings = adjustment.get("grainSettings") or {}
        return kernels.mutate(
            image,
            "adjust_grain",
            settings.get("amount", 25),
            settings.get("size", 1.5),
            settings.get("roughness", 50),
            settings.get("seed", 0),
            *origin,
            units_per_pixel,
        )
    if kind == "Invert":
        return filtered(image, "Invert", {})
    if kind == "Black & White":
        settings = adjustment.get("blackWhiteSettings") or {}
        weights = [
            settings.get(key, default) / 100
            for key, default in (
                ("reds", 40),
                ("yellows", 60),
                ("greens", 40),
                ("cyans", 60),
                ("blues", 20),
                ("magentas", 80),
            )
        ]
        return kernels.mutate(
            image,
            "adjust_black_white",
            weights,
            int(settings.get("tint", False)),
            settings.get("tintHue", 40),
            settings.get("tintSaturation", 20) / 100,
        )
    if kind == "Color Balance":
        settings = adjustment.get("colorBalanceSettings") or {}
        channels = [
            [
                settings.get(band + axis, 0) / 100
                for axis in ("CyanRed", "MagentaGreen", "YellowBlue")
            ]
            for band in ("shadow", "mid", "highlight")
        ]
        return kernels.mutate(
            image, "adjust_color_balance", *channels, int(settings.get("preserveLuminosity", True))
        )
    if kind in ("Gaussian Blur", "Motion Blur"):
        settings = (
            {"radius": adjustment.get("blurRadius", 10)}
            if kind == "Gaussian Blur"
            else {
                "angle": adjustment.get("motionAngle", 0),
                "distance": adjustment.get("motionDistance", 10),
            }
        )
        return filtered(image, kind, settings)
    if kind == "Add Noise":
        return kernels.mutate(
            image,
            "noise_add_at",
            adjustment.get("noiseAmount", 10),
            int(adjustment.get("noiseGaussian", False)),
            int(adjustment.get("noiseMonochromatic", False)),
            adjustment.get("noiseSeed", 0),
            round(origin[0]),
            round(origin[1]),
        )
    raise ValueError(f"Unsupported adjustment: {kind}")


def filtered(image, kind, settings):
    if kind == "Camera Raw Filter":
        from .camera_raw import apply as camera_raw_apply

        return camera_raw_apply(image, settings)
    if kind == "Dither":
        return dither.apply(image, settings)
    if kind == "Vignette":
        color = settings.get("vignetteColor", [0, 0, 0])
        color = [v / 255 if v > 1 else v for v in color]
        return kernels.mutate(
            image,
            "adjust_colored_vignette",
            0,
            0,
            image.width,
            image.height,
            0,
            settings.get("vignetteAmount", 35),
            settings.get("vignetteMidpoint", 50),
            settings.get("vignetteRoundness", 100),
            settings.get("vignetteFeather", 60),
            settings.get("vignetteHighlights", 25),
            *color,
        )
    if kind == "Tonal Contrast":
        data = kernels.premultiply(image)
        blurred = np.clip(
            np.floor(
                gaussian_filter(
                    data.astype(np.float32),
                    sigma=(settings.get("tonalRadius", 16), settings.get("tonalRadius", 16), 0),
                    mode="nearest",
                )
                + 0.5
            ),
            0,
            255,
        ).astype(np.uint8)
        return kernels.mutate(
            image,
            "adjust_tonal_contrast",
            blurred,
            settings.get("tonalAmount", 50),
            settings.get("tonalShadows", 40),
            settings.get("tonalMidtones", 60),
            settings.get("tonalHighlights", 30),
        )
    if kind == "Bloom / Glow":
        data = kernels.premultiply(image).astype(np.float32)
        radius = settings.get("bloomRadius", 24)
        blurred = gaussian_filter(data, sigma=(radius, radius, 0), mode="constant")
        brightness = np.max(data[..., :3], axis=2, keepdims=True) / 255
        halo = gaussian_filter(
            data * np.clip((brightness - 0.5) * 2, 0, 1), sigma=(radius, radius, 0), mode="constant"
        )
        amount = settings.get("bloomAmount", 40) / 50
        result = np.clip(data + halo * amount + blurred * amount * 0.1, 0, 255)
        result[..., :3] = np.minimum(result[..., :3], result[..., 3:4])
        return kernels.straight(np.floor(result + 0.5).astype(np.uint8))
    if kind in (
        "Hue/Saturation",
        "Levels",
        "Curves",
        "Exposure",
        "Gradient Map",
        "Grain",
        "Black & White",
        "Color Balance",
    ):
        return adjust(image, dict(settings, kind=kind))
    if kind == "Invert":
        result = ImageOps.invert(image.convert("RGB")).convert("RGBA")
        result.putalpha(image.getchannel("A"))
        return result
    if kind == "Gaussian Blur":
        data = kernels.premultiply(image).astype(np.float32)
        data = gaussian_filter(
            data,
            sigma=(settings.get("radius", 1), settings.get("radius", 1), 0),
            mode="constant",
        )
        return kernels.straight(np.clip(np.floor(data + 0.5), 0, 255).astype(np.uint8))
    if kind == "Motion Blur":
        data = kernels.premultiply(image).astype(np.float32)
        result = np.zeros_like(data)
        distance, angle = (
            settings.get("distance", 10),
            math.radians(settings.get("angle", 0)),
        )
        # Core Image's motion kernel is Gaussian along the requested direction.
        sigma = distance / math.sqrt(12)
        steps = min(301, max(3, math.ceil(sigma * 6)))
        yy, xx = np.indices(data.shape[:2], dtype=float)
        offsets = np.linspace(-3 * sigma, 3 * sigma, steps)
        weights = np.exp(-0.5 * (offsets / max(0.001, sigma)) ** 2)
        weights /= weights.sum()
        for offset, weight in zip(offsets, weights):
            coords = [yy + offset * math.sin(angle), xx - offset * math.cos(angle)]
            # Core Image fades samples across the transparent image boundary.
            for channel in range(4):
                result[..., channel] += (
                    map_coordinates(data[..., channel], coords, order=1, mode="grid-constant")
                    * weight
                )
        return kernels.straight(np.clip(np.floor(result + 0.5), 0, 255).astype(np.uint8))
    if kind == "Add Noise":
        return kernels.mutate(
            image,
            "noise_add",
            settings.get("amount", 10),
            int(settings.get("gaussian", False)),
            int(settings.get("monochromatic", False)),
            settings.get("seed", 0),
        )
    if kind == "Lens Correction":
        return kernels.mutate(image, "lens_distort", settings.get("distortion", 0) / 100 * 0.35)
    raise ValueError(f"Unknown filter: {kind}")


def shape_pixels(size, style, radius=None, line_scale=1):
    factor = 4 if max(size) < 2048 else 1
    coverage = Image.new("L", (size[0] * factor, size[1] * factor))
    draw = ImageDraw.Draw(coverage)
    bounds = (0, 0, coverage.width - 1, coverage.height - 1)
    if style.get("kind") == "Line":
        width = max(1, round(style.get("lineWidth", 1) * line_scale * factor))
        inset_x = min(coverage.width, width) / 2
        inset_y = min(coverage.height, width) / 2
        start = style.get("start", [inset_x / coverage.width, inset_y / coverage.height])
        end = style.get("end", [1 - inset_x / coverage.width, 1 - inset_y / coverage.height])
        p = (start[0] * coverage.width, start[1] * coverage.height)
        q = (end[0] * coverage.width, end[1] * coverage.height)
        draw.line((*p, *q), fill=255, width=width)
        for cx, cy in (p, q):
            draw.ellipse((cx - width / 2, cy - width / 2, cx + width / 2, cy + width / 2), fill=255)
    elif style.get("kind") == "Ellipse":
        draw.ellipse(bounds, fill=255)
    else:
        draw.rounded_rectangle(
            bounds,
            radius=max(0, radius if radius is not None else style.get("cornerRadius", 0)) * factor,
            fill=255,
        )
    coverage = coverage.resize(size, Image.Resampling.LANCZOS)
    color = tuple(round(style.get(k, 0) * 255) for k in ("red", "green", "blue"))
    result = Image.new("RGBA", size, (*color, 255))
    result.putalpha(coverage)
    return result


def guided_matte(mask, image, radius=12, contrast=25, shift=0):
    p = np.asarray(mask, dtype=float) / 255
    guide = np.asarray(image.convert("L"), dtype=float) / 255
    if radius > 0:
        size = max(1, round(radius) * 2 + 1)
        mean_i, mean_p = uniform_filter(guide, size), uniform_filter(p, size)
        variance = uniform_filter(guide * guide, size) - mean_i * mean_i
        covariance = uniform_filter(guide * p, size) - mean_i * mean_p
        a = covariance / (variance + 0.001)
        b = mean_p - a * mean_i
        p = uniform_filter(a, size) * guide + uniform_filter(b, size)
    if shift:
        p = gaussian_filter(p, abs(shift) / 2)
        level = 0.75 if shift < 0 else 0.25
        p = np.clip((p - level) / 0.001, 0, 1)
    if contrast:
        slope = 1 / max(0.02, 1 - contrast / 100 * 0.98)
        p = p * slope + (1 - slope) / 2
    return byte_image(np.clip(p, 0, 1) * 255)


@lru_cache(maxsize=1)
def subject_session(model_path, modified):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    return ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])


def subject_mask(image, model_path):
    """Local U2NET inference. Model installation is explicit, never downloaded here."""

    model_path = Path(model_path)
    if not model_path.is_file():
        raise ValueError("Choose a local U2NET ONNX model in the Remove Background dialog.")
    session = subject_session(str(model_path.resolve()), model_path.stat().st_mtime_ns)
    rgb = image.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS)
    data = np.asarray(rgb, dtype=np.float32)
    data = data / max(1, float(data.max()))
    data = (data - np.array([0.485, 0.456, 0.406], np.float32)) / np.array(
        [0.229, 0.224, 0.225], np.float32
    )
    result = session.run(None, {session.get_inputs()[0].name: data.transpose(2, 0, 1)[None]})[0]
    mask = np.squeeze(result)
    if mask.ndim != 2 or not np.isfinite(mask).all():
        raise ValueError("The selected ONNX model does not produce a U2NET subject mask.")
    mask = (mask - mask.min()) / max(1e-8, float(mask.max() - mask.min()))
    return byte_image(mask * 255).resize(image.size, Image.Resampling.LANCZOS)
