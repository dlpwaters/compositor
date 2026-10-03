"""Bounded local-only SVG raster import through QtSvg."""

import math
import re
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from .model import dimensions

MAX_SVG_BYTES = 4 * 1024 * 1024
MAX_SVG_PIXELS = 16_000_000
MAX_SVG_NODES = 10_000
MAX_SVG_DEPTH = 32
FORBIDDEN = {
    "script",
    "foreignObject",
    "image",
    "use",
    "iframe",
    "object",
    "animate",
    "animateMotion",
    "animateTransform",
    "set",
    "feImage",
}


def load(path):
    path = Path(path)
    # Bound the read itself: a file can grow or be replaced after a separate stat().
    with path.open("rb") as source:
        data = source.read(MAX_SVG_BYTES + 1)
    if len(data) > MAX_SVG_BYTES:
        raise ValueError("SVG exceeds the 4 MiB import limit.")
    if b"<!" in data or b"\x00" in data or re.search(rb"@import\b|url\s*\(", data, re.I):
        raise ValueError("SVG declarations, entities and embedded resources are unsupported.")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid SVG XML.") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("Image is not an SVG document.")
    pending = [(root, 0)]
    nodes = 0
    while pending:
        element, depth = pending.pop()
        nodes += 1
        if depth > MAX_SVG_DEPTH or nodes + len(element) > MAX_SVG_NODES:
            raise ValueError("SVG exceeds the scene complexity limit.")
        pending.extend((child, depth + 1) for child in element)
        if element.tag.rsplit("}", 1)[-1] in FORBIDDEN:
            raise ValueError("SVG scripts, animation and external resources are unsupported.")
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name in ("href", "src") or re.search(r"url\s*\(", value, re.I):
                raise ValueError("SVG linked resources are unsupported.")
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        raise ValueError("SVG could not be rendered.")
    size = renderer.defaultSize()
    width, height = size.width(), size.height()
    if not all(math.isfinite(value) for value in (width, height)):
        raise ValueError("SVG dimensions are not finite.")
    dimensions(width, height, raster=True)
    if width * height > MAX_SVG_PIXELS:
        raise ValueError("SVG exceeds the 16-megapixel render budget.")
    output = QImage(width, height, QImage.Format.Format_RGBA8888)
    if output.isNull():
        raise MemoryError("SVG raster could not be allocated.")
    output.fill(0)
    painter = QPainter(output)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return Image.frombytes("RGBA", (width, height), bytes(output.constBits()))
