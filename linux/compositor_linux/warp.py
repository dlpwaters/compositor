"""Document-space Smudge and Liquify, translated from SmudgeLiquify.swift."""

import math
from dataclasses import replace

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

from . import editing, engine, kernels
from .model import Transform, dimensions


class WarpStroke:
    def __init__(self, document, diameter, hardness, opacity, color, mask, mode, source=None):
        if mask:
            raise ValueError("Smudge and Liquify work on image pixels, not a mask.")
        self.document = document
        layer, image, transform = editing.raster_target(document)
        self.layer, self.original = layer, layer.clone()
        self.diameter = max(2, diameter)
        self.hardness, self.strength = min(0.98, max(0, hardness)), min(1, max(0.01, opacity))
        self.mode, self.last, self.points = mode, None, []
        dimensions(document.width, document.height, raster=True)
        self.pixels = kernels.premultiply(
            engine.place(image, transform, (document.width, document.height))
        )
        self.carried = None

    def append(self, point):
        if self.last is None:
            self.last = point
            if self.mode == "Smudge":
                self.pick_up(point)
            return
        distance = math.dist(point, self.last)
        spacing = max(1, self.diameter * (0.005 if self.mode == "Smudge" else 0.025))
        if distance < spacing:
            return
        previous = self.last
        steps = math.ceil(distance / spacing)
        for step in range(1, steps + 1):
            next_point = tuple(a + (b - a) * step / steps for a, b in zip(self.last, point))
            self.dab(previous, next_point)
            self.points.append(next_point)
            previous = next_point
        self.last = point
        self.layer.image = kernels.straight(self.pixels)
        self.layer.shape = None
        self.layer.transform = Transform(width=self.document.width, height=self.document.height)
        if self.layer.mask is not None and self.layer.mask_transform is None:
            self.layer.mask_transform = replace(self.original.transform)

    def center(self, point):
        return tuple(math.floor(value + 0.5) for value in point)

    def pick_up(self, point):
        radius = math.ceil(self.diameter / 2)
        side = radius * 2 + 1
        self.carried = np.zeros((side, side, 4), np.float32)
        cx, cy = self.center(point)
        left, top = max(0, cx - radius), max(0, cy - radius)
        right, bottom = (
            min(self.document.width, cx + radius + 1),
            min(self.document.height, cy + radius + 1),
        )
        if left < right and top < bottom:
            self.carried[
                top - cy + radius : bottom - cy + radius, left - cx + radius : right - cx + radius
            ] = self.pixels[top:bottom, left:right]

    def dab(self, previous, point):
        radius = math.ceil(self.diameter / 2)
        cx, cy = self.center(point)
        left, top = max(0, cx - radius), max(0, cy - radius)
        right, bottom = (
            min(self.document.width, cx + radius + 1),
            min(self.document.height, cy + radius + 1),
        )
        if left >= right or top >= bottom:
            return
        yy, xx = np.mgrid[top:bottom, left:right]
        distance = np.hypot(xx - cx, yy - cy) / (self.diameter / 2)
        fade = np.clip((1 - distance) / (1 - self.hardness), 0, 1)
        weight = fade * fade * (3 - 2 * fade)
        if self.mode == "Smudge":
            carried = self.carried[
                top - cy + radius : bottom - cy + radius, left - cx + radius : right - cx + radius
            ]
            under = self.pixels[top:bottom, left:right].astype(np.float32)
            painted = under + (carried - under) * weight[..., None] * self.strength
            self.pixels[top:bottom, left:right] = np.clip(np.floor(painted + 0.5), 0, 255).astype(
                np.uint8
            )
            carried[:] = painted
        else:
            dx, dy = (
                (point[0] - previous[0]) * self.strength,
                (point[1] - previous[1]) * self.strength,
            )
            margin = math.ceil(max(abs(dx), abs(dy))) + 2
            x0, y0 = max(0, left - margin), max(0, top - margin)
            x1, y1 = (
                min(self.document.width, right + margin),
                min(self.document.height, bottom + margin),
            )
            scratch = self.pixels[y0:y1, x0:x1].astype(np.float32)
            coords = [
                np.clip(yy - y0 - dy * weight, 0, scratch.shape[0] - 1),
                np.clip(xx - x0 - dx * weight, 0, scratch.shape[1] - 1),
            ]
            moved = np.stack(
                [
                    map_coordinates(scratch[..., channel], coords, order=3, mode="nearest")
                    for channel in range(4)
                ],
                -1,
            )
            self.pixels[top:bottom, left:right] = np.clip(np.floor(moved + 0.5), 0, 255).astype(
                np.uint8
            )

    def finish(self):
        original = self.original.clone()
        index = self.document.layers.index(self.layer)
        self.document.layers[index] = original
        if not self.points:
            return original.image
        _, image, transform = editing.raster_target(self.document)
        radius = (self.diameter + 4) / 2
        corners = np.array(
            [
                [x + dx, y + dy, 1]
                for x, y in self.points
                for dx, dy in (
                    (-radius, -radius),
                    (radius, -radius),
                    (radius, radius),
                    (-radius, radius),
                )
            ]
        )
        local = (np.linalg.inv(editing.pixel_matrix(transform, image.size)) @ corners.T).T[:, :2]
        left, top = np.minimum(0, np.floor(local.min(0)) - 2).astype(int)
        right, bottom = np.maximum(image.size, np.ceil(local.max(0)) + 2).astype(int)
        size = int(right - left), int(bottom - top)
        dimensions(*size, raster=True)
        expanded = Image.new("RGBA", size)
        expanded.paste(image, (-int(left), -int(top)))
        if original.mask is not None and original.mask_transform is None:
            original.mask_transform = replace(transform)
        original.image = expanded
        original.transform = editing.extended_transform(
            transform, image.size, int(left), int(top), size
        )
        mapping = editing.pixel_matrix(original.transform, size)
        source = kernels.straight(self.pixels).transform(
            size, Image.Transform.AFFINE, tuple(mapping[:2].ravel()), Image.Resampling.BICUBIC
        )
        stroke = editing.Stroke(
            self.document, self.diameter + 4, 1, 1, mode="Replace", source=source
        )
        for point in self.points:
            stroke.append(point)
        return stroke.finish()
