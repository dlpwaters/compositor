"""Native font shaping for editable text, with a portable raster fallback."""

import math

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)

from .model import dimensions, validate_text


def render_text(settings):
    validate_text(settings)
    font = QFont(settings["family"])
    font.setPixelSize(settings["size"])
    font.setBold(settings["bold"])
    font.setItalic(settings["italic"])
    font.setUnderline(settings["underline"])
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(max(2, math.ceil(settings["size"] / 3)))
    option = document.defaultTextOption()
    option.setWrapMode(option.WrapMode.NoWrap)
    option.setAlignment(
        {
            "Left": Qt.AlignmentFlag.AlignLeft,
            "Center": Qt.AlignmentFlag.AlignHCenter,
            "Right": Qt.AlignmentFlag.AlignRight,
        }[settings["alignment"]]
    )
    document.setDefaultTextOption(option)
    document.setPlainText(settings["text"])
    document.setTextWidth(-1)
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    color = QTextCharFormat()
    color.setForeground(QColor(*settings["color"]))
    cursor.mergeCharFormat(color)
    size = document.size()
    width, height = max(1, math.ceil(size.width())), max(1, math.ceil(size.height()))
    dimensions(width, height, raster=True)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise ValueError("Unable to allocate the text bitmap.")
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        document.drawContents(painter)
    finally:
        painter.end()
    pixels = Image.frombytes("RGBA", (width, height), bytes(image.constBits()))
    bounds = pixels.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("Enter visible text.")
    return pixels.crop(bounds)
