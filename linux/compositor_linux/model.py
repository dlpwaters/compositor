"""Document values, shared immutable raster assets, and bounded undo history."""

from __future__ import annotations

import math
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PIL import Image

MAX_SIDE = 30_000
MAX_PIXELS = 100_000_000
BLENDS = (
    "Normal",
    "Multiply",
    "Screen",
    "Overlay",
    "Darken",
    "Lighten",
    "Difference",
    "Color Dodge",
    "Color Burn",
    "Hue",
    "Saturation",
    "Color",
    "Luminosity",
)
ADJUSTMENTS = (
    "Hue/Saturation",
    "Levels",
    "Curves",
    "Exposure",
    "Gradient Map",
    "Grain",
)


def new_id():
    return str(uuid.uuid4()).upper()


def finite(value, low=-math.inf, high=math.inf):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and low <= value <= high
    )


def dimensions(width, height, raster=False):
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= MAX_SIDE
        or not 1 <= height <= MAX_SIDE
        or raster
        and width * height > MAX_PIXELS
    ):
        raise ValueError("Dimensions exceed the 30,000-pixel side or 100-megapixel raster limit.")


@dataclass
class Transform:
    x: float = 0
    y: float = 0
    width: float = 1
    height: float = 1
    rotation: float = 0
    flip_x: bool = False
    flip_y: bool = False
    sampling: str = "High quality"

    def validate(self):
        if (
            not finite(self.x, -1_000_000, 1_000_000)
            or not finite(self.y, -1_000_000, 1_000_000)
            or not finite(self.width, 1, 300_000)
            or not finite(self.height, 1, 300_000)
            or not finite(self.rotation)
            or type(self.flip_x) is not bool
            or type(self.flip_y) is not bool
            or self.sampling not in ("Nearest", "Smooth", "High quality")
        ):
            raise ValueError("Invalid layer transform.")

    @property
    def center(self):
        return self.x + self.width / 2, self.y + self.height / 2

    def point(self, u, v):
        angle = math.radians(self.rotation % 360)
        x, y = (u - 0.5) * self.width, (v - 0.5) * self.height
        cx, cy = self.center
        return cx + x * math.cos(angle) - y * math.sin(angle), cy + x * math.sin(
            angle
        ) + y * math.cos(angle)

    def local(self, x, y, pixel_size):
        cx, cy = self.center
        a = math.radians(self.rotation % 360)
        dx, dy = x - cx, y - cy
        u = (dx * math.cos(a) + dy * math.sin(a)) / self.width + 0.5
        v = (-dx * math.sin(a) + dy * math.cos(a)) / self.height + 0.5
        return (
            (1 - u if self.flip_x else u) * pixel_size[0],
            (1 - v if self.flip_y else v) * pixel_size[1],
        )

    def matrix(self):
        points = [
            self.point(1 - u if self.flip_x else u, 1 - v if self.flip_y else v)
            for u, v in ((0, 0), (1, 0), (0, 1))
        ]
        p, px, py = np.asarray(points)
        return np.array(
            [[px[0] - p[0], py[0] - p[0], p[0]], [px[1] - p[1], py[1] - p[1], p[1]], [0, 0, 1]]
        )

    def following(self, old, new):
        if (old.width, old.height, old.rotation, old.flip_x, old.flip_y) == (
            new.width,
            new.height,
            new.rotation,
            new.flip_x,
            new.flip_y,
        ):
            return replace(self, x=self.x + new.x - old.x, y=self.y + new.y - old.y)
        mapping = new.matrix() @ np.linalg.inv(old.matrix()) @ self.matrix()
        sign = -1 if self.flip_x else 1
        angle = math.atan2(mapping[1, 0] * sign, mapping[0, 0] * sign)
        along = -mapping[0, 1] * math.sin(angle) + mapping[1, 1] * math.cos(angle)
        width, height = float(np.linalg.norm(mapping[:2, 0])), abs(float(along))
        center = mapping @ np.array([0.5, 0.5, 1])
        degrees = math.degrees(angle)
        return replace(
            self,
            x=float(center[0]) - width / 2,
            y=float(center[1]) - height / 2,
            width=width,
            height=height,
            flip_y=bool(along < 0),
            rotation=degrees + round((self.rotation - degrees) / 360) * 360,
        )

    def record(self):
        # CGPoint/CGSize use unkeyed pairs in Swift's Foundation Codable implementation.
        return dict(
            origin=[self.x, self.y],
            size=[self.width, self.height],
            rotation=self.rotation,
            flipX=self.flip_x,
            flipY=self.flip_y,
            sampling=self.sampling,
        )

    @classmethod
    def from_record(cls, value):
        try:
            origin, size = value["origin"], value["size"]
            if (
                not isinstance(origin, list)
                or not isinstance(size, list)
                or len(origin) != 2
                or len(size) != 2
            ):
                raise ValueError("Invalid point or size encoding.")
            result = cls(
                *origin,
                *size,
                value["rotation"],
                value["flipX"],
                value["flipY"],
                value["sampling"],
            )
            result.validate()
            return result
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError("Invalid transform metadata.") from exc


@dataclass
class Layer:
    name: str
    transform: Transform
    id: str = field(default_factory=new_id)
    image: Image.Image | None = None
    visible: bool = True
    parent: str | None = None
    group: bool = False
    opacity: float = 1
    blend: str = "Normal"
    mask: Image.Image | None = None
    mask_enabled: bool = True
    mask_source: str | None = None
    mask_transform: Transform | None = None
    mask_linked: bool = True
    adjustment: dict | None = None
    shape: dict | None = None
    extras: dict = field(default_factory=dict)

    def clone(self):
        return replace(
            self,
            transform=replace(self.transform),
            mask_transform=replace(self.mask_transform) if self.mask_transform else None,
            adjustment=deepcopy(self.adjustment),
            shape=deepcopy(self.shape),
            extras=deepcopy(self.extras),
        )

    @classmethod
    def blank(cls, width, height, name="Layer"):
        return cls(name, Transform(width=width, height=height))


@dataclass
class Document:
    width: int
    height: int
    id: str = field(default_factory=new_id)
    resolution: float = 72
    layers: list[Layer] = field(default_factory=list)
    active: str | None = None
    selection: Image.Image | None = None
    extras: dict = field(default_factory=dict)

    def clone(self):
        return replace(
            self,
            layers=[layer.clone() for layer in self.layers],
            extras=deepcopy(self.extras),
        )

    def layer(self, id=None):
        return next((layer for layer in self.layers if layer.id == (id or self.active)), None)

    def descendants(self, ids):
        result = set(ids)
        for _ in range(65):
            following = result | {layer.id for layer in self.layers if layer.parent in result}
            if following == result:
                return result
            result = following
        raise ValueError("Folder nesting exceeds 64 levels.")

    def entries(self, top_first=False):
        children = {}
        for layer in self.layers:
            children.setdefault(layer.parent, []).append(layer)

        def visit(parent, ancestors):
            for layer in (
                reversed(children.get(parent, [])) if top_first else children.get(parent, [])
            ):
                yield layer, ancestors
                if layer.group:
                    yield from visit(layer.id, ancestors + [layer])

        return visit(None, [])

    def validate(self):
        dimensions(self.width, self.height)
        if not finite(self.resolution, 1, 9600) or len(self.layers) > 10_000:
            raise ValueError("Invalid resolution or layer count.")
        uuid.UUID(self.id)
        by_id = {}
        used, mask_used = 0, 0
        for layer in self.layers:
            uuid.UUID(layer.id)
            if layer.id in by_id:
                raise ValueError("Duplicate layer UUID.")
            by_id[layer.id] = layer
            layer.transform.validate()
            if (
                not isinstance(layer.name, str)
                or not layer.name.strip()
                or len(layer.name.encode()) > 16_384
                or type(layer.visible) is not bool
                or type(layer.group) is not bool
                or not finite(layer.opacity, 0, 1)
                or layer.blend not in BLENDS
            ):
                raise ValueError("Invalid layer metadata.")
            if layer.group and (
                layer.image is not None
                or layer.adjustment is not None
                or layer.opacity != 1
                or layer.blend != "Normal"
            ):
                raise ValueError("Folders must be pass-through and cannot contain raster pixels.")
            if layer.adjustment:
                if layer.image is not None or layer.group:
                    raise ValueError("Adjustment layers cannot contain pixels.")
                validate_adjustment(layer.adjustment)
            if layer.shape:
                if (
                    not isinstance(layer.shape, dict)
                    or layer.shape.get("kind") not in ("Rectangle", "Ellipse")
                    or any(not finite(layer.shape.get(k), 0, 1) for k in ("red", "green", "blue"))
                    or not finite(layer.shape.get("cornerRadius"), 0, 300_000)
                    or layer.image is None
                    or layer.group
                    or layer.adjustment
                ):
                    raise ValueError("Invalid live shape metadata.")
            if layer.mask_transform:
                layer.mask_transform.validate()
                if layer.mask is None:
                    raise ValueError("Mask placement without a mask.")
            for is_mask, asset in ((False, layer.image), (True, layer.mask)):
                if asset is None:
                    continue
                dimensions(*asset.size, raster=True)
                if is_mask:
                    if asset.mode != "L":
                        raise ValueError("Masks must be 8-bit grayscale without alpha.")
                    mask_used += asset.width * asset.height
                else:
                    used += asset.width * asset.height
            if used > MAX_PIXELS or mask_used > MAX_PIXELS:
                raise ValueError("Project exceeds the 100-megapixel source or mask budget.")
        if self.active is not None and self.active not in by_id:
            raise ValueError("Active layer does not exist.")
        for layer in self.layers:
            seen = {layer.id}
            parent = layer.parent
            while parent is not None:
                if (
                    parent in seen
                    or parent not in by_id
                    or not by_id[parent].group
                    or len(seen) > 64
                ):
                    raise ValueError("Invalid folder hierarchy.")
                seen.add(parent)
                parent = by_id[parent].parent
            if layer.group and len(seen) > 64:
                raise ValueError("Folder nesting exceeds 64 levels.")
            seen = {layer.id}
            source = layer.mask_source
            while source is not None:
                if (
                    layer.group
                    or source in seen
                    or source not in by_id
                    or by_id[source].group
                    or len(seen) >= 256
                ):
                    raise ValueError("Invalid clipping mask graph.")
                seen.add(source)
                source = by_id[source].mask_source


def validate_adjustment(a):
    if not isinstance(a, dict) or a.get("kind") not in ADJUSTMENTS:
        raise ValueError("Unknown adjustment kind.")
    for key, limit in (("hue", 360), ("saturation", 100), ("lightness", 100)):
        if not finite(a.get(key, 0), -limit, limit):
            raise ValueError("Invalid hue/saturation settings.")
    levels = a.get("levels", {})
    ranges = levels.get("ranges", [{} for _ in range(4)])
    if levels.get("channel", "RGB") not in ("RGB", "Red", "Green", "Blue") or len(ranges) != 4:
        raise ValueError("Invalid levels channels.")
    for r in ranges:
        for key, lo, hi in (
            ("black", 0, 254),
            ("white", 1, 255),
            ("gamma", 0.1, 9.99),
            ("outputBlack", 0, 255),
            ("outputWhite", 0, 255),
        ):
            default = {"white": 255, "gamma": 1, "outputWhite": 255}.get(key, 0)
            if not finite(r.get(key, default), lo, hi):
                raise ValueError("Invalid levels range.")
        if r.get("white", 255) <= r.get("black", 0):
            raise ValueError("Levels white point must exceed black point.")
    curves = a.get("curves", {}).get(
        "channels", [[dict(x=0, y=0), dict(x=255, y=255)] for _ in range(4)]
    )
    if len(curves) != 4:
        raise ValueError("Invalid curves channels.")
    for points in curves:
        if (
            not 2 <= len(points) <= 32
            or points[0].get("x") != 0
            or points[-1].get("x") != 255
            or any(not finite(p.get("x"), 0, 255) or not finite(p.get("y"), 0, 255) for p in points)
            or any(p["x"] >= q["x"] for p, q in zip(points, points[1:]))
        ):
            raise ValueError("Invalid curve points.")
    for field_name, specifications in (
        (
            "exposureSettings",
            (
                ("exposure", -20, 20, 0),
                ("offset", -0.5, 0.5, 0),
                ("gamma", 0.01, 9.99, 1),
            ),
        ),
        (
            "grainSettings",
            (("amount", 0, 100, 25), ("size", 0.5, 20, 1.5), ("roughness", 0, 100, 50)),
        ),
    ):
        settings = a.get(field_name) or {}
        for key, lo, hi, default in specifications:
            if not finite(settings.get(key, default), lo, hi):
                raise ValueError("Invalid adjustment settings.")
    gradient = a.get("gradientMapSettings") or {}
    for name, default in (("shadows", 0), ("highlights", 1)):
        if any(
            not finite(gradient.get(name, {}).get(k, default), 0, 1)
            for k in ("red", "green", "blue")
        ):
            raise ValueError("Invalid gradient-map color.")
    hsv = a.get("hsvSettings") or {}
    for field_name in ("adjustments", "bands"):
        values = hsv.get(field_name, {})
        if isinstance(values, list):
            if len(values) % 2:
                raise ValueError("Invalid hue range dictionary.")
            values = dict(zip(values[::2], values[1::2]))
        if not isinstance(values, dict) or any(
            k not in ("Master", "Reds", "Yellows", "Greens", "Cyans", "Blues", "Magentas")
            for k in values
        ):
            raise ValueError("Invalid hue range.")
        for settings in values.values():
            if not isinstance(settings, dict):
                raise ValueError("Invalid hue settings.")
            if field_name == "adjustments":
                for key, limit in (
                    ("hue", 360),
                    ("saturation", 100),
                    ("lightness", 100),
                ):
                    if not finite(settings.get(key, 0), -limit, limit):
                        raise ValueError("Invalid hue range adjustment.")
            elif any(
                not finite(settings.get(key))
                for key in ("falloffStart", "rangeStart", "rangeEnd", "falloffEnd")
            ):
                raise ValueError("Invalid hue band.")


def adjustment_record(value):
    """Fill nonoptional Swift Codable fields, including fields for inactive adjustments."""
    result = deepcopy(value)
    for key, default in (
        ("hue", 0),
        ("saturation", 0),
        ("lightness", 0),
        ("colorize", False),
    ):
        result.setdefault(key, default)
    levels = result.setdefault("levels", {})
    levels.setdefault("channel", "RGB")
    ranges = levels.setdefault("ranges", [{} for _ in range(4)])
    for r in ranges:
        for key, default in (
            ("black", 0),
            ("gamma", 1),
            ("white", 255),
            ("outputBlack", 0),
            ("outputWhite", 255),
        ):
            r.setdefault(key, default)
    curves = result.setdefault("curves", {})
    curves.setdefault("channel", "RGB")
    curves.setdefault("channels", [[dict(x=0, y=0), dict(x=255, y=255)] for _ in range(4)])
    hsv = result.get("hsvSettings")
    if hsv:
        for key, default in (
            ("range", "Master"),
            ("colorize", False),
            ("invertRange", False),
            ("adjustments", []),
            ("bands", []),
        ):
            hsv.setdefault(key, default)
        for key in ("adjustments", "bands"):
            if isinstance(hsv[key], dict):
                hsv[key] = [item for pair in hsv[key].items() for item in pair]
    return result


@dataclass
class History:
    document: Document
    path: Path | None = None
    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)
    revision: str = field(default_factory=new_id)
    saved_revision: str | None = None
    max_steps: int = 64

    @property
    def dirty(self):
        return self.revision != self.saved_revision

    @contextmanager
    def edit(self, name):
        before = self.document.clone()
        revision = self.revision
        try:
            yield self.document
            self.document.validate()
        except BaseException:
            self.document = before
            raise
        self.undo_stack.append((name, before, revision))
        self.undo_stack = self.undo_stack[-self.max_steps :]
        self.redo_stack.clear()
        self.revision = new_id()
        self._trim_rasters()

    def _trim_rasters(self):
        # Immutable images are shared between snapshots. Bound unique retained raster bytes.
        seen, total = set(), 0
        keep = len(self.undo_stack)
        for i in range(len(self.undo_stack) - 1, -1, -1):
            for layer in self.undo_stack[i][1].layers:
                for image in (layer.image, layer.mask):
                    if image is not None and id(image) not in seen:
                        seen.add(id(image))
                        total += image.width * image.height * len(image.getbands())
            if total > 512 * 1024 * 1024:
                keep = len(self.undo_stack) - i - 1
                break
        if keep < len(self.undo_stack):
            self.undo_stack = self.undo_stack[-max(1, keep) :]

    def undo(self):
        if not self.undo_stack:
            return False
        name, document, revision = self.undo_stack.pop()
        self.redo_stack.append((name, self.document, self.revision))
        self.document, self.revision = document, revision
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        name, document, revision = self.redo_stack.pop()
        self.undo_stack.append((name, self.document, self.revision))
        self.document, self.revision = document, revision
        return True

    def saved(self, path):
        self.path, self.saved_revision = Path(path), self.revision
