"""Bounded Photoshop TySh point and paragraph text import.

The descriptor layout and representability checks follow PSDText.swift. EngineData
is parsed as data; its PostScript-like text is never evaluated.
"""

from __future__ import annotations

import math
import re
import struct

from .model import linux_to_mac_text, validate_text


class Reader:
    def __init__(self, data):
        self.data = memoryview(data)
        self.at = 0

    def take(self, length):
        if length < 0 or self.at + length > len(self.data):
            raise ValueError("Truncated Photoshop text descriptor.")
        result = self.data[self.at : self.at + length]
        self.at += length
        return bytes(result)

    def number(self, length):
        return int.from_bytes(self.take(length), "big")

    def double(self):
        return struct.unpack(">d", self.take(8))[0]

    def ident(self):
        length = self.number(4)
        return self.take(4 if length == 0 else min(length, 10_001)).decode("ascii")

    def unicode(self):
        length = self.number(4)
        if length > 100_000:
            raise ValueError("Photoshop text is too long.")
        return self.take(length * 2).decode("utf-16-be")

    def descriptor(self, versioned=True, depth=0):
        if depth > 8 or versioned and self.number(4) != 16:
            raise ValueError("Unsupported Photoshop descriptor version.")
        self.unicode()
        self.ident()
        count = self.number(4)
        if count > 10_000:
            raise ValueError("Photoshop descriptor has too many entries.")
        entries = {}
        for _ in range(count):
            key, kind = self.ident(), self.take(4)
            if kind == b"TEXT":
                value = self.unicode()
            elif kind == b"enum":
                self.ident()
                value = self.ident()
            elif kind == b"tdta":
                length = self.number(4)
                if length > 8_000_000:
                    raise ValueError("Photoshop engine data is too large.")
                value = self.take(length)
            elif kind == b"Objc" or kind == b"GlbO":
                value = self.descriptor(False, depth + 1)
            elif kind == b"doub":
                value = self.double()
            elif kind == b"UntF":
                self.take(4)
                value = self.double()
            elif kind == b"long":
                value = int.from_bytes(self.take(4), "big", signed=True)
            elif kind == b"bool":
                value = bool(self.number(1))
            else:
                raise ValueError("Unsupported Photoshop text descriptor value.")
            entries[key] = value
        return entries


_FLOAT = rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def _number(data, key, default):
    found = re.search(rb"/" + key.encode("ascii") + rb"\s+(" + _FLOAT + rb")(?=\s|\]|>|$)", data)
    return float(found.group(1)) if found else default


def _boolean(data, key):
    found = re.search(rb"/" + key.encode("ascii") + rb"\s+(true|false)(?=\s|>|$)", data)
    return found is not None and found.group(1) == b"true"


def _engine_style(data, scale, content):
    if len(data) > 8_000_000 or b"<<" not in data:
        raise ValueError("Unsupported Photoshop text engine data.")
    first = data.find(b"/StyleSheetData")
    style = data[first : data.find(b"/StyleSheetData", first + 1)] if first >= 0 else data
    if not style:
        style = data[first:]
    size = _number(style, "FontSize", 12) * scale
    if not math.isfinite(size) or not 1 <= size <= 2000:
        raise ValueError("Invalid Photoshop font size.")
    names = re.findall(rb"/Name\s+\(([^()] {0,255}|[^()]*)\)", data)
    index = round(_number(style, "Font", 0))
    family = names[index].decode("utf-8", "replace") if 0 <= index < len(names) else "Sans Serif"
    colors = re.search(rb"/FillColor\s*<<.*?/Values\s*\[([^]]+)\]", style, re.S)
    values = [float(part) for part in re.findall(_FLOAT, colors.group(1))] if colors else []
    if len(values) >= 4:
        values = values[1:4]
    elif len(values) == 1:
        values *= 3
    elif len(values) != 3:
        values = [0, 0, 0]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Invalid Photoshop text color.")
    rgb = [round(max(0, min(1, value / 255 if value > 1 else value)) * 255) for value in values]
    alignment = {0: "Left", 1: "Right", 2: "Center"}.get(
        round(_number(data[data.find(b"/ParagraphRun") :], "Justification", 0)), "Left"
    )
    tracking = _number(style, "Tracking", 0) * size / 1000
    leading = 0 if _boolean(style, "AutoLeading") else _number(style, "Leading", 0) * scale
    mac = {
        "content": content,
        "fontName": family,
        "fontSize": size,
        "alignment": alignment,
        "red": rgb[0] / 255,
        "green": rgb[1] / 255,
        "blue": rgb[2] / 255,
        "tracking": tracking,
        "leading": leading,
    }
    settings = {
        "version": 1,
        "text": content,
        "family": family,
        "size": round(size),
        "bold": False,
        "italic": False,
        "underline": False,
        "alignment": alignment,
        "color": rgb,
        "macText": mac,
    }
    notes = []
    if data.count(b"/StyleSheetData") > 1:
        notes.append("Only the first Photoshop text style was kept")
    if _boolean(style, "FauxBold") or _boolean(style, "FauxItalic"):
        notes.append("Faux bold or faux italic was omitted")
    return settings, notes


def parse(data):
    """Return (editable settings, angle, flipY, anchor, notes), or None."""
    if not data or len(data) > 8_000_000:
        return None
    try:
        reader = Reader(data)
        if reader.number(2) != 1:
            return None
        xx, xy, yx, yy, tx, ty = (reader.double() for _ in range(6))
        if not all(math.isfinite(n) for n in (xx, xy, yx, yy, tx, ty)):
            return None
        scale = math.hypot(xx, yx)
        if scale < 1e-6:
            return None
        cos_r, sin_r = xx / scale, yx / scale
        local_x, local_y = cos_r * xy + sin_r * yy, -sin_r * xy + cos_r * yy
        if abs(local_x) > 0.02 * max(scale, abs(local_y)) or abs(scale - abs(local_y)) > 0.02 * max(
            scale, abs(local_y)
        ):
            return None
        if reader.number(2) != 50:
            return None
        descriptor = reader.descriptor()
        if descriptor.get("Ornt") == "Vrtc":
            return None
        content = descriptor.get("Txt ", descriptor.get("Txt"))
        if not isinstance(content, str):
            return None
        content = content.lstrip("\ufeff\0").rstrip("\0").replace("\r\n", "\n").replace("\r", "\n")
        if not content.strip() or len(content.encode("utf-16-be")) // 2 > 100_000:
            return None
        engine = descriptor.get("EngineData", b"")
        settings, notes = _engine_style(engine, scale, content)
        if len(data) - reader.at >= 2 and reader.number(2) == 1:
            warp = reader.descriptor()
            if warp.get("warpStyle") not in (None, "warpNone", "none"):
                notes.append("Photoshop text warp was omitted")
        bounds, glyphs = descriptor.get("bounds"), descriptor.get("boundingBox")
        if isinstance(bounds, dict) and isinstance(glyphs, dict):

            def rect(value):
                return (
                    value.get("Rght", 0) - value.get("Left", 0),
                    value.get("Btom", 0) - value.get("Top ", 0),
                )

            bw, bh = rect(bounds)
            gw, gh = rect(glyphs)
            if bw > gw + 4 and bh > gh + 4:
                size = [round(bw * scale + 24), round(bh * scale + 24)]
                if min(size) < 16 or max(size) > 30_000 or size[0] * size[1] > 100_000_000:
                    return None
                settings["macText"]["boxSize"] = size
        validate_text(settings)
        linux_to_mac_text(settings)
        return settings, math.degrees(math.atan2(sin_r, cos_r)), local_y < 0, (tx, ty), notes
    except (UnicodeError, ValueError, OverflowError, IndexError, TypeError, struct.error):
        return None
