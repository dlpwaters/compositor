"""Bounded 8-bit RGB PSD/PSB importer, following PSDReader.swift.

Only raw and PackBits channel compression are decoded. Unsupported Photoshop
features are listed in ``conversions`` for review before the document is opened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import psd_text
from .model import (
    MAX_PIXELS,
    Document,
    Layer,
    TextContent,
    Transform,
    dimensions,
    linux_to_mac_text,
    new_id,
)

BLENDS = dict(
    zip(
        (
            "norm",
            "mul ",
            "scrn",
            "over",
            "sLit",
            "dark",
            "lite",
            "diff",
            "div ",
            "idiv",
            "hue ",
            "sat ",
            "colr",
            "lum ",
            "lbrn",
            "lddg",
            "hLit",
            "vLit",
            "lLit",
            "pLit",
            "hMix",
            "smud",
            "fsub",
            "fdiv",
        ),
        (
            "Normal",
            "Multiply",
            "Screen",
            "Overlay",
            "Soft Light",
            "Darken",
            "Lighten",
            "Difference",
            "Color Dodge",
            "Color Burn",
            "Hue",
            "Saturation",
            "Color",
            "Luminosity",
            "Linear Burn",
            "Linear Dodge (Add)",
            "Hard Light",
            "Vivid Light",
            "Linear Light",
            "Pin Light",
            "Hard Mix",
            "Exclusion",
            "Subtract",
            "Divide",
        ),
    )
)
LARGE_KEYS = {
    "LMsk",
    "Lr16",
    "Lr32",
    "Layr",
    "Mt16",
    "Mt32",
    "Mtrn",
    "Alph",
    "FMsk",
    "lnk2",
    "FEid",
    "FXid",
    "PxSD",
}
ADJUSTMENT_KEYS = {
    "levl",
    "curv",
    "hue2",
    "hue ",
    "expA",
    "grdm",
    "brit",
    "blnc",
    "nvrt",
    "thrs",
    "post",
    "mixr",
    "selc",
    "blwh",
    "phfl",
    "vibA",
}


class Cursor:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError("The Photoshop file is damaged or incomplete.")
        result = self.data[self.pos : self.pos + count]
        self.pos += count
        return result

    def number(self, size, signed=False):
        return int.from_bytes(self.take(size), "big", signed=signed)

    def text(self, size):
        return bytes(self.take(size)).decode("latin-1")

    def seek(self, end):
        if end < self.pos or end > len(self.data):
            raise ValueError("The Photoshop file is damaged or incomplete.")
        self.pos = end


@dataclass
class RawLayer:
    name: str
    bounds: tuple[int, int, int, int]
    channels: list[tuple[int, int]]
    blend: str
    opacity: int
    clipping: bool
    hidden: bool
    fill: int = 255
    section: int = 0
    extra_keys: set[str] = field(default_factory=set)
    extra_data: dict[str, bytes] = field(default_factory=dict)
    mask_bounds: tuple[int, int, int, int] | None = None
    mask_default: int = 255
    mask_enabled: bool = True
    mask_linked: bool = True
    image: Image.Image | None = None
    mask: Image.Image | None = None
    cropped: bool = False


@dataclass
class ImportResult:
    document: Document
    conversions: list[str]


def _packbits(data, width, height, large, crop):
    cursor = Cursor(data)
    lengths = [cursor.number(4 if large else 2) for _ in range(height)]
    x, y, out_w, out_h = crop
    output = bytearray(out_w * out_h)
    for row, length in enumerate(lengths):
        end = cursor.pos + length
        if end > len(data):
            raise ValueError("The Photoshop channel is truncated.")
        if not y <= row < y + out_h:
            cursor.seek(end)
            continue
        unpacked = bytearray()
        while len(unpacked) < width:
            if cursor.pos >= end:
                raise ValueError("The Photoshop PackBits row is truncated.")
            n = cursor.number(1, signed=True)
            if n >= 0:
                unpacked.extend(cursor.take(n + 1))
            elif n != -128:
                unpacked.extend(bytes(cursor.take(1)) * (1 - n))
            if cursor.pos > end or len(unpacked) > width:
                raise ValueError("The Photoshop PackBits row is invalid.")
        output[(row - y) * out_w : (row - y + 1) * out_w] = unpacked[x : x + out_w]
        cursor.seek(end)
    return bytes(output)


def _plane(data, width, height, large, crop):
    cursor = Cursor(data)
    compression = cursor.number(2)
    payload = cursor.take(len(data) - 2)
    x, y, out_w, out_h = crop
    if compression == 0:
        if len(payload) < width * height:
            raise ValueError("The Photoshop channel is truncated.")
        return b"".join(
            bytes(payload[row * width + x : row * width + x + out_w]) for row in range(y, y + out_h)
        )
    if compression == 1:
        return _packbits(payload, width, height, large, crop)
    raise ValueError("This Photoshop layer compression method is unsupported.")


def _record(cursor, large):
    top, left, bottom, right = (cursor.number(4, signed=True) for _ in range(4))
    count = cursor.number(2)
    if count > 56 or bottom < top or right < left:
        raise ValueError("Invalid Photoshop layer bounds or channel count.")
    channels = [
        (cursor.number(2, signed=True), cursor.number(8 if large else 4)) for _ in range(count)
    ]
    if cursor.text(4) != "8BIM":
        raise ValueError("Invalid Photoshop layer signature.")
    blend = cursor.text(4)
    opacity, clipping, flags = cursor.number(1), cursor.number(1), cursor.number(1)
    cursor.take(1)
    extra_length = cursor.number(4)
    end = cursor.pos + extra_length
    mask_len = cursor.number(4)
    mask_end = cursor.pos + mask_len
    mask_bounds = None
    mask_default, mask_enabled, mask_linked = 255, True, True
    if mask_len >= 20:
        mask_bounds = tuple(cursor.number(4, signed=True) for _ in range(4))
        mask_default = cursor.number(1)
        mask_flags = cursor.number(1)
        mask_enabled = not bool(mask_flags & 2)
        mask_linked = not bool(mask_flags & 1)
    cursor.seek(mask_end)
    cursor.take(cursor.number(4))
    name_length = cursor.number(1)
    name = bytes(cursor.take(name_length)).decode("mac_roman", errors="replace")
    cursor.take(-(name_length + 1) % 4)
    layer = RawLayer(
        name or "Layer",
        (top, left, bottom, right),
        channels,
        blend,
        opacity,
        bool(clipping),
        bool(flags & 2),
        mask_bounds=mask_bounds,
        mask_default=mask_default,
        mask_enabled=mask_enabled,
        mask_linked=mask_linked,
    )
    while cursor.pos + 12 <= end:
        signature, key = cursor.text(4), cursor.text(4)
        if signature not in ("8BIM", "8B64"):
            break
        size = cursor.number(8 if signature == "8B64" or large and key in LARGE_KEYS else 4)
        payload = cursor.take(size)
        if size % 2:
            cursor.take(1)
        layer.extra_keys.add(key)
        if key in ("TySh", "tySh") and size <= 8_000_000:
            layer.extra_data[key] = bytes(payload)
        if key == "luni" and size >= 4:
            units = int.from_bytes(payload[:4], "big")
            if 0 < units <= 8192 and size >= 4 + units * 2:
                layer.name = (
                    bytes(payload[4 : 4 + units * 2])
                    .decode("utf-16-be", errors="replace")
                    .rstrip("\0")
                )
        elif key == "iOpa" and size:
            layer.fill = payload[0]
        elif key in ("lsct", "lsdk") and size >= 4:
            layer.section = int.from_bytes(payload[:4], "big")
    cursor.seek(end)
    return layer


def _crop(bounds, canvas):
    top, left, bottom, right = bounds
    width, height = canvas
    x0, y0 = min(width, max(0, left)), min(height, max(0, top))
    x1, y1 = max(x0, min(width, right)), max(y0, min(height, bottom))
    return x0 - left, y0 - top, x1 - x0, y1 - y0


def _decode_layers(cursor, layers, large, canvas):
    used = 0
    for layer in layers:
        top, left, bottom, right = layer.bounds
        width, height = right - left, bottom - top
        if width > 30_000 or height > 30_000 or used + width * height > MAX_PIXELS:
            image_crop = _crop(layer.bounds, canvas)
            layer.cropped = True
        else:
            image_crop = (0, 0, width, height)
        x, y, cropped_w, cropped_h = image_crop
        if used + cropped_w * cropped_h > MAX_PIXELS:
            raise ValueError("Photoshop layers exceed the 100-megapixel raster budget.")
        mask_crop = None
        if layer.mask_bounds:
            mt, ml, mb, mr = layer.mask_bounds
            mask_crop = _crop(layer.mask_bounds, canvas)
            if mask_crop[2] != mr - ml or mask_crop[3] != mb - mt:
                layer.cropped = True
        planes = {}
        for id, length in layer.channels:
            data = cursor.take(length)
            if id not in (-2, -1, 0, 1, 2) or length < 2:
                continue
            source_w, source_h = (mr - ml, mb - mt) if id == -2 and mask_crop else (width, height)
            crop = mask_crop if id == -2 and mask_crop else image_crop
            if crop[2] and crop[3]:
                if source_w <= 0 or source_h <= 0 or crop[2] * crop[3] > MAX_PIXELS:
                    raise ValueError("Invalid Photoshop channel size.")
                planes[id] = _plane(data, source_w, source_h, large, crop)
        if cropped_w and cropped_h and any(id in planes for id in (0, 1, 2)):
            count = cropped_w * cropped_h
            image = Image.merge(
                "RGBA",
                tuple(
                    Image.frombytes(
                        "L", (cropped_w, cropped_h), planes.get(id, bytes([default]) * count)
                    )
                    for id, default in ((0, 0), (1, 0), (2, 0), (-1, 255))
                ),
            )
            layer.image = image
            layer.bounds = (top + y, left + x, top + y + cropped_h, left + x + cropped_w)
            used += count
        if mask_crop and -2 in planes:
            mx, my, mw, mh = mask_crop
            if used + mw * mh > MAX_PIXELS:
                raise ValueError("Photoshop masks exceed the raster budget.")
            layer.mask = Image.frombytes("L", (mw, mh), planes[-2])
            layer.mask_bounds = (mt + my, ml + mx, mt + my + mh, ml + mx + mw)
            used += mw * mh


def _merged(cursor, canvas, channels, large):
    width, height = canvas
    compression = cursor.number(2)
    count = width * height
    used = min(channels, 4)  # RGB plus optional alpha; Photoshop may contain many unused channels.
    if compression == 0:
        planes = [bytes(cursor.take(count)) for _ in range(used)]
        cursor.seek(cursor.pos + (channels - used) * count)
    elif compression == 1:
        size = 4 if large else 2
        row_lengths = cursor.take(channels * height * size)
        planes = []
        for index in range(channels):
            rows = row_lengths[index * height * size : (index + 1) * height * size]
            encoded_size = sum(
                int.from_bytes(rows[row * size : (row + 1) * size], "big") for row in range(height)
            )
            if index < used:
                payload = bytes(rows) + bytes(cursor.take(encoded_size))
                planes.append(_packbits(payload, width, height, large, (0, 0, width, height)))
            else:
                cursor.seek(cursor.pos + encoded_size)
    else:
        raise ValueError("Unsupported Photoshop merged image compression.")
    rgba = [Image.frombytes("L", canvas, planes[index]) for index in range(3)]
    rgba.append(Image.frombytes("L", canvas, planes[3] if channels >= 4 else bytes([255]) * count))
    return Image.merge("RGBA", rgba)


def read(data):
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) > 1024 * 1024 * 1024:
        raise ValueError("Photoshop file exceeds the 1 GiB input limit.")
    cursor = Cursor(data)
    if cursor.text(4) != "8BPS":
        raise ValueError("This is not a Photoshop file.")
    version = cursor.number(2)
    if version not in (1, 2):
        raise ValueError("Unsupported Photoshop format version.")
    large = version == 2
    cursor.take(6)
    channels = cursor.number(2)
    height, width = cursor.number(4), cursor.number(4)
    depth, mode = cursor.number(2), cursor.number(2)
    if depth != 8 or mode != 3 or not 3 <= channels <= 56:
        raise ValueError("Only 8-bit RGB Photoshop files are supported.")
    dimensions(width, height, raster=True)
    cursor.take(cursor.number(4))
    resources_length = cursor.number(4)
    resources_end = cursor.pos + resources_length
    resolution = 72
    while cursor.pos + 12 <= resources_end:
        if cursor.text(4) != "8BIM":
            break
        resource_id = cursor.number(2)
        name_length = cursor.number(1)
        cursor.take(name_length + (name_length + 1) % 2)
        size = cursor.number(4)
        payload = cursor.take(size)
        if resource_id == 1005 and size >= 4:
            resolution = min(9600, max(1, int.from_bytes(payload[:4], "big") / 65536))
        if size % 2:
            cursor.take(1)
    cursor.seek(resources_end)
    section_length = cursor.number(8 if large else 4)
    section_end = cursor.pos + section_length
    records = []
    if section_length >= (8 if large else 4):
        info_length = cursor.number(8 if large else 4)
        info_end = cursor.pos + info_length
        if info_length >= 2:
            count = abs(cursor.number(2, signed=True))
            if count > 10_000:
                raise ValueError("Photoshop layer count exceeds 10,000.")
            records = [_record(cursor, large) for _ in range(count)]
            _decode_layers(cursor, records, large, (width, height))
            if cursor.pos > info_end:
                raise ValueError("Invalid Photoshop layer section length.")
    cursor.seek(section_end)
    document = Document(width, height, resolution=resolution)
    conversions = []
    groups = []
    bases = {}
    for raw in records:
        if raw.section == 3:
            groups.append(new_id())
            continue
        group = raw.section in (1, 2)
        id = groups.pop() if group and groups else new_id()
        if group and raw.blend not in ("norm", "pass"):
            conversions.append(f"{raw.name}: folder blend {raw.blend!r} converted to pass-through")
        elif not group and raw.blend not in BLENDS:
            conversions.append(f"{raw.name}: blend {raw.blend!r} converted to Normal")
        top, left, bottom, right = raw.bounds
        layer = Layer(
            raw.name,
            Transform(0, 0, width, height)
            if group
            else Transform(left, top, max(1, right - left), max(1, bottom - top)),
            id=id,
            image=None if group else raw.image,
            visible=not raw.hidden,
            parent=groups[-1] if groups else None,
            group=group,
            opacity=raw.opacity / 255 * raw.fill / 255,
            blend="Normal" if group else BLENDS.get(raw.blend, "Normal"),
        )
        if raw.cropped:
            conversions.append(f"{raw.name}: cropped to canvas to fit memory budget")
        if raw.extra_keys & ADJUSTMENT_KEYS:
            if raw.image is None:
                raise ValueError(f"{raw.name}: Photoshop adjustment has no raster fallback.")
            conversions.append(
                f"{raw.name}: unsupported Photoshop adjustment; raster pixels retained when present"
            )
        if raw.extra_keys & {"TySh", "tySh", "txt2"}:
            parsed = psd_text.parse(raw.extra_data.get("TySh") or raw.extra_data.get("tySh"))
            if raw.image is None:
                raise ValueError(f"{raw.name}: Photoshop text has no raster fallback.")
            if parsed:
                settings, angle, flip_y, anchor, notes = parsed
                layer.text = TextContent(settings, layer.image)
                layer.extras["text"] = linux_to_mac_text(settings)
                layer.transform.rotation = angle
                layer.transform.flip_y = flip_y
                conversions.extend(f"{raw.name}: {note}" for note in notes)
            else:
                conversions.append(f"{raw.name}: Photoshop text rasterized; remains a pixel layer")
        if raw.extra_keys & {"vmsk", "vsms", "vogk"}:
            if raw.image is None:
                raise ValueError(f"{raw.name}: Photoshop vector layer has no raster fallback.")
            conversions.append(f"{raw.name}: vector shape rasterized; remains a pixel layer")
        if raw.extra_keys & {"SoCo", "GdFl", "PtFl"}:
            if raw.image is None:
                raise ValueError(f"{raw.name}: Photoshop fill layer has no raster fallback.")
            conversions.append(f"{raw.name}: Photoshop fill rasterized; remains a pixel layer")
        if raw.extra_keys & {"SoLd", "SoLE"}:
            conversions.append(
                f"{raw.name}: smart object rasterized; linked contents are unavailable"
            )
        if raw.extra_keys & {"lfx2", "lrFX", "lmfx"}:
            conversions.append(f"{raw.name}: Photoshop layer effects were discarded")
        if raw.mask is not None:
            mt, ml, mb, mr = raw.mask_bounds
            mw, mh = (width, height) if group else (max(1, right - left), max(1, bottom - top))
            if mw * mh > MAX_PIXELS:
                raise ValueError("Layer mask grid exceeds the raster budget.")
            mask = Image.new("L", (mw, mh), raw.mask_default)
            mask.paste(raw.mask, (ml if group else ml - left, mt if group else mt - top))
            layer.mask = mask
            layer.mask_enabled, layer.mask_linked = raw.mask_enabled, raw.mask_linked
        if raw.clipping:
            base = bases.get(layer.parent)
            if base:
                layer.mask_source = base
            else:
                conversions.append(f"{raw.name}: clipping base unavailable; clipping skipped")
        elif not group:
            bases[layer.parent] = layer.id
        else:
            bases[layer.parent] = None
        document.layers.append(layer)
    if groups:
        raise ValueError("Unclosed Photoshop folder section.")
    if not records:
        image = _merged(cursor, (width, height), channels, large)
        document.layers.append(Layer("Merged Image", Transform(0, 0, width, height), image=image))
        conversions.append("Merged image only: no editable Photoshop layers were present")
    document.active = document.layers[-1].id
    document.validate()
    return ImportResult(document, conversions)


def load(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError("Photoshop file exceeds the 1 GiB input limit.")
    return read(path.read_bytes())
