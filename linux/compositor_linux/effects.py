"""Portable layer effects following the upstream source-over order and coverage rules."""

import math

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, maximum_filter, minimum_filter, shift


def render(image, settings, scale=1):
    """Apply enabled effects to a placed, already raster-masked RGBA layer."""
    if not settings or not any(item.get("enabled", True) for item in settings.values()):
        return image
    alpha = np.asarray(image.getchannel("A"), dtype=np.float32) / 255
    result = Image.new("RGBA", image.size)

    def effect(kind):
        item = settings.get(kind)
        return item if item and item.get("enabled", True) else None

    def soft(mask, radius):
        return (
            gaussian_filter(mask, max(0, radius) * scale / 2, mode="constant") if radius else mask
        )

    def paint(mask, item):
        nonlocal result
        opacity = item.get(
            "opacity",
            0.5 if item is settings.get("shadow") or item is settings.get("innerShadow") else 1,
        )
        coverage = np.clip(np.floor(mask * opacity * 255 + 0.5), 0, 255).astype(np.uint8)
        color = tuple(round(item.get(key, 0) * 255) for key in ("red", "green", "blue"))
        surface = Image.new("RGBA", image.size, (*color, 0))
        surface.putalpha(Image.fromarray(coverage))
        result = Image.alpha_composite(result, surface)

    def moved(item):
        angle = math.radians(item.get("angle", 90))
        distance = item.get("distance", 20) * scale
        return shift(
            alpha,
            (math.sin(angle) * distance, -math.cos(angle) * distance),
            order=1,
            mode="constant",
            cval=0,
        )

    shadow = effect("shadow")
    if shadow:
        paint(soft(moved(shadow), shadow.get("blur", 20)), shadow)
    outer = effect("outerGlow")
    if outer:
        paint(np.clip(soft(alpha, outer.get("size", 20)) * (1 - alpha), 0, 1), outer)
    stroke = effect("stroke")
    if stroke and not stroke.get("inside", False):
        reach = max(1, round(stroke.get("size", 4) * scale))
        grown = maximum_filter(alpha, size=2 * reach + 1, mode="constant", cval=0)
        paint(np.maximum(0, grown - alpha), stroke)
    result = Image.alpha_composite(result, image)
    overlay = effect("colorOverlay")
    if overlay:
        paint(alpha, overlay)
    glow = effect("innerGlow")
    if glow:
        paint(np.clip(alpha * (1 - soft(alpha, glow.get("size", 10))), 0, 1), glow)
    inner = effect("innerShadow")
    if inner:
        paint(np.clip(alpha * (1 - soft(moved(inner), inner.get("blur", 10))), 0, 1), inner)
    if stroke and stroke.get("inside", False):
        reach = max(1, round(stroke.get("size", 4) * scale))
        shrunk = minimum_filter(alpha, size=2 * reach + 1, mode="constant", cval=0)
        paint(np.maximum(0, alpha - shrunk), stroke)
    return result
